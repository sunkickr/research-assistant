import json
import re
import uuid
import threading
import queue

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    Response,
    send_file,
    redirect,
    url_for,
)
from config import Config
from services.reddit_service import RedditService
from services.llm_provider import OpenAIProvider
from services.scoring_service import ScoringService
from services.summary_service import SummaryService
from services.storage_service import StorageService
from services.web_search_service import WebSearchService
from services.hn_service import HNService
from services.article_service import ArticleService
from models.data_models import ScoredComment, RedditThread

app = Flask(__name__)
config = Config()

# Custom Jinja2 filter to parse JSON in templates
@app.template_filter("fromjson")
def fromjson_filter(value):
    try:
        return json.loads(value)
    except Exception:
        return {}

# Initialize services
reddit_svc = RedditService(
    config.REDDIT_CLIENT_ID, config.REDDIT_CLIENT_SECRET, config.REDDIT_USER_AGENT
)
llm = OpenAIProvider(config.OPENAI_API_KEY, config.LLM_MODEL)
scoring_svc = ScoringService(llm, batch_size=config.LLM_BATCH_SIZE)
summary_svc = SummaryService(llm)
storage_svc = StorageService(config.DB_PATH, config.EXPORT_DIR)
web_search_svc = WebSearchService(reddit_svc.reddit)
hn_svc = HNService()
article_svc = ArticleService(llm)

# Active research streams: research_id -> queue.Queue
progress_queues: dict[str, queue.Queue] = {}
# Active expand streams: research_id -> queue.Queue
expand_queues: dict[str, queue.Queue] = {}
# Active add-thread streams: research_id -> queue.Queue
add_thread_queues: dict[str, queue.Queue] = {}
# Active rescore streams: research_id -> queue.Queue
rescore_queues: dict[str, queue.Queue] = {}

# Reddit sort order cycle for successive "Find More" expansions
REDDIT_EXPAND_SORTS = ["top", "new", "controversial", "hot"]


# ----- Page Routes -----


@app.route("/")
def index():
    history = storage_svc.get_history()
    return render_template(
        "index.html",
        history=history,
        config={
            "default_max_threads": config.DEFAULT_MAX_THREADS,
            "max_threads_limit": config.MAX_THREADS_LIMIT,
            "default_max_comments": config.DEFAULT_MAX_COMMENTS_PER_THREAD,
            "max_comments_limit": config.MAX_COMMENTS_PER_THREAD_LIMIT,
        },
    )


@app.route("/results/<research_id>")
def results(research_id):
    history = storage_svc.get_history()
    research = storage_svc.get_research(research_id)
    if not research:
        return redirect(url_for("index"))
    return render_template(
        "results.html", research_id=research_id, research=research, history=history
    )


# ----- API Routes -----


@app.route("/api/research", methods=["POST"])
def start_research():
    data = request.get_json()
    question = data.get("question", "").strip()
    if not question:
        return jsonify(error="Question is required"), 400

    max_threads = min(
        int(data.get("max_threads", config.DEFAULT_MAX_THREADS)),
        config.MAX_THREADS_LIMIT,
    )
    max_comments = min(
        int(data.get("max_comments_per_thread", config.DEFAULT_MAX_COMMENTS_PER_THREAD)),
        config.MAX_COMMENTS_PER_THREAD_LIMIT,
    )
    time_filter = data.get("time_filter", "all")
    if time_filter not in ("hour", "day", "week", "month", "year", "all"):
        time_filter = "all"

    seed_urls = data.get("seed_urls", [])
    if not isinstance(seed_urls, list):
        seed_urls = []

    sources = data.get("sources", ["reddit", "hackernews", "web"])
    if not isinstance(sources, list) or not sources:
        sources = ["reddit", "hackernews", "web"]

    research_id = uuid.uuid4().hex[:12]
    settings = {
        "max_threads": max_threads,
        "max_comments_per_thread": max_comments,
        "time_filter": time_filter,
        "sources": sources,
    }
    storage_svc.create_research(research_id, question, settings)

    # Create progress queue and start background task
    q: queue.Queue = queue.Queue()
    progress_queues[research_id] = q
    t = threading.Thread(
        target=run_research_pipeline,
        args=(research_id, question, max_threads, max_comments, time_filter, q, seed_urls, sources),
        daemon=True,
    )
    t.start()

    return jsonify(research_id=research_id)


def _comments_for_sse(scored_comments):
    """Serialize scored comments for SSE live preview (minimal fields)."""
    return [
        {
            "id": c.id,
            "body": c.body[:150],
            "score": c.score,
            "relevancy_score": c.relevancy_score,
            "permalink": c.permalink,
            "thread_id": c.thread_id,
            "source": c.source,
        }
        for c in scored_comments
    ]


def _collect_comments_for_thread(thread, max_comments, reddit_svc, hn_svc, article_svc):
    """Dispatch comment collection based on thread source."""
    if thread.source == "hackernews":
        return hn_svc.collect_comments(thread.id, max_comments=max_comments)
    elif thread.source == "web":
        return article_svc.get_cached_quotes(thread.id)
    else:
        return reddit_svc.collect_comments(thread.id, max_comments=max_comments)


def _make_scoring_progress_callback(q, base_pct, range_pct, research_id=None):
    """Return a scoring progress callback that emits SSE events and saves each batch."""
    def on_batch(batch_num, total_batches, batch_results):
        pct = base_pct + int(range_pct * (batch_num / total_batches))
        q.put({
            "stage": "scoring",
            "message": f"Scoring batch {batch_num}/{total_batches}...",
            "progress": pct,
            "comments": _comments_for_sse(batch_results),
        })
        if research_id:
            storage_svc.save_scored_comments(research_id, batch_results)
    return on_batch


def run_research_pipeline(
    research_id: str,
    question: str,
    max_threads: int,
    max_comments: int,
    time_filter: str,
    q: queue.Queue,
    seed_urls: list = None,
    sources: list = None,
):
    """Background task that runs the full research pipeline."""
    if sources is None:
        sources = ["reddit", "hackernews", "web"]
    try:
        if seed_urls:
            # --- Seed URL flow: skip discovery, fetch user-provided threads directly ---
            q.put({
                "stage": "searching",
                "message": f"Fetching {len(seed_urls)} user-provided thread(s)...",
                "progress": 10,
            })
            relevant_threads = []
            for url in seed_urls:
                try:
                    # Try Reddit URL
                    match = re.search(r"reddit\.com/r/\w+/comments/(\w+)", url)
                    if not match:
                        match = re.search(r"redd\.it/(\w+)", url)
                    if match:
                        thread_id = match.group(1)
                        sub = reddit_svc.reddit.submission(id=thread_id)
                        thread = RedditThread(
                            id=sub.id,
                            title=sub.title,
                            subreddit=str(sub.subreddit),
                            score=sub.score,
                            num_comments=sub.num_comments,
                            url=sub.url,
                            permalink=f"https://reddit.com{sub.permalink}",
                            selftext=(sub.selftext or "")[:2000],
                            created_utc=sub.created_utc,
                            author=str(sub.author) if sub.author else "[deleted]",
                        )
                        relevant_threads.append(thread)
                        continue

                    # Try Hacker News URL
                    if "news.ycombinator.com" in url:
                        hn_match = re.search(r"id=(\d+)", url)
                        if hn_match:
                            numeric_id = hn_match.group(1)
                            hn_thread_id = f"hn_{numeric_id}"
                            import requests as _requests
                            resp = _requests.get(
                                f"https://hn.algolia.com/api/v1/items/{numeric_id}",
                                timeout=15,
                            )
                            resp.raise_for_status()
                            item = resp.json()
                            thread = RedditThread(
                                id=hn_thread_id,
                                title=item.get("title") or "",
                                subreddit="Hacker News",
                                score=item.get("points") or 0,
                                num_comments=len(item.get("children", [])),
                                url=item.get("url") or f"https://news.ycombinator.com/item?id={numeric_id}",
                                permalink=f"https://news.ycombinator.com/item?id={numeric_id}",
                                selftext=(item.get("text") or "")[:2000],
                                created_utc=float(item.get("created_at_i") or 0),
                                author=item.get("author") or "",
                                source="hackernews",
                            )
                            relevant_threads.append(thread)
                            continue

                    # Fall back to web article
                    result = article_svc.fetch_article(url)
                    if result:
                        title, body = result
                        thread = article_svc.make_thread(url, title, body)
                        article_svc.extract_quotes(thread.id, url, title, body, question)
                        relevant_threads.append(thread)
                except Exception:
                    continue

            if not relevant_threads:
                storage_svc.update_research_status(research_id, "complete", 0, 0)
                q.put({
                    "stage": "complete",
                    "message": "None of the provided URLs could be fetched.",
                    "progress": 100,
                })
                return

            storage_svc.save_threads(research_id, relevant_threads)
            q.put({
                "stage": "searching",
                "message": f"Loaded {len(relevant_threads)} thread(s) — collecting comments...",
                "progress": 22,
            })

        else:
            # --- Normal flow: discover sources and search ---
            threads = []
            seen_ids = set()
            validated = []
            search_queries = []

            # Stage 1: Generate search queries (and subreddits if Reddit is enabled)
            if "reddit" in sources:
                q.put({
                    "stage": "searching",
                    "message": "Finding relevant subreddits...",
                    "progress": 3,
                })
                suggested, search_queries = scoring_svc.suggest_subreddits(question)
                validated = reddit_svc.validate_subreddits(suggested) if suggested else []

                if validated:
                    subreddit_display = ", ".join(f"r/{s}" for s in validated)
                    q.put({
                        "stage": "searching",
                        "message": f"Searching in: {subreddit_display}",
                        "progress": 8,
                        "subreddits": validated,
                    })
                else:
                    q.put({
                        "stage": "searching",
                        "message": "Searching across all of Reddit...",
                        "progress": 8,
                    })
                storage_svc.update_research_subreddits(research_id, validated)
            else:
                # Still need search queries for HN/web even without Reddit
                q.put({
                    "stage": "searching",
                    "message": "Generating search queries...",
                    "progress": 3,
                })
                _, search_queries = scoring_svc.suggest_subreddits(question)

            web_queries = search_queries if search_queries else [question]

            # Stage 2: Reddit search (if enabled)
            if "reddit" in sources:
                q.put({
                    "stage": "searching",
                    "message": "Searching Reddit for relevant threads...",
                    "progress": 10,
                })
                reddit_threads = list(
                    reddit_svc.search_threads(
                        question,
                        max_threads=max_threads,
                        time_filter=time_filter,
                        subreddits=validated or None,
                    )
                )
                for t in reddit_threads:
                    if t.id not in seen_ids:
                        threads.append(t)
                        seen_ids.add(t.id)

                # Stage 2a: Web search for additional Reddit threads
                q.put({
                    "stage": "searching",
                    "message": f"Found {len(threads)} Reddit threads — searching the web for more...",
                    "progress": 14,
                })
                web_threads = web_search_svc.search_reddit_threads(web_queries, max_results=15, subreddits=validated or None, max_total=max_threads)
                web_added = 0
                for wt in web_threads:
                    if wt.id not in seen_ids:
                        threads.append(wt)
                        seen_ids.add(wt.id)
                        web_added += 1

            # Stage 2b: Hacker News search (if enabled)
            if "hackernews" in sources:
                q.put({
                    "stage": "searching",
                    "message": "Searching Hacker News...",
                    "progress": 16,
                })
                hn_stories = hn_svc.search_stories(web_queries, max_results=config.HN_MAX_STORIES)
                hn_added = 0
                for story in hn_stories:
                    if story.id not in seen_ids:
                        threads.append(story)
                        seen_ids.add(story.id)
                        hn_added += 1
                q.put({
                    "stage": "searching",
                    "message": f"Found {hn_added} Hacker News discussions",
                    "progress": 17,
                })

            # Stage 2c: Web article search (if enabled)
            if "web" in sources:
                q.put({
                    "stage": "searching",
                    "message": "Searching the web for articles...",
                    "progress": 18,
                })
                article_urls = web_search_svc.search_web_articles(web_queries, max_results=config.WEB_MAX_ARTICLES)
                web_articles_added = 0
                for url in article_urls:
                    result = article_svc.fetch_article(url)
                    if result:
                        title, body = result
                        thread = article_svc.make_thread(url, title, body)
                        if thread.id not in seen_ids:
                            # Extract quotes now and cache them
                            article_svc.extract_quotes(thread.id, url, title, body, question)
                            threads.append(thread)
                            seen_ids.add(thread.id)
                            web_articles_added += 1
                q.put({
                    "stage": "searching",
                    "message": f"Found {web_articles_added} relevant web articles",
                    "progress": 19,
                })

            q.put({
                "stage": "searching",
                "message": f"Found {len(threads)} threads/articles total — filtering for relevancy...",
                "progress": 20,
            })

            if not threads:
                storage_svc.update_research_status(research_id, "complete", 0, 0)
                q.put({
                    "stage": "complete",
                    "message": "No threads found. Try a different query.",
                    "progress": 100,
                })
                return

            # Filter threads by relevancy before collecting comments
            relevant_threads = scoring_svc.score_threads(question, threads)
            q.put({
                "stage": "searching",
                "message": f"{len(relevant_threads)} of {len(threads)} threads are relevant",
                "progress": 22,
                "threads_relevant": len(relevant_threads),
                "threads_total": len(threads),
            })
            storage_svc.save_threads(research_id, relevant_threads)

        # --- Shared: collect comments (dispatch by source) ---
        all_comments = []
        for i, thread in enumerate(relevant_threads):
            q.put({
                "stage": "collecting",
                "message": f"Collecting comments from thread {i + 1}/{len(relevant_threads)}: {thread.title[:60]}...",
                "progress": 20 + int(40 * (i / len(relevant_threads))),
                "thread_title": thread.title[:60],
            })
            comments = _collect_comments_for_thread(thread, max_comments, reddit_svc, hn_svc, article_svc)
            all_comments.extend(comments)
            if comments:
                storage_svc.save_raw_comments(research_id, comments)
            q.put({
                "stage": "collecting",
                "message": f"Collected {len(comments)} comments from \"{thread.title[:40]}\"",
                "progress": 20 + int(40 * ((i + 1) / len(relevant_threads))),
                "thread_title": thread.title[:60],
                "thread_comments": len(comments),
            })

        # Apply total cap
        if len(all_comments) > config.TOTAL_COMMENTS_CAP:
            all_comments.sort(key=lambda c: c.score, reverse=True)
            all_comments = all_comments[: config.TOTAL_COMMENTS_CAP]

        q.put({
            "stage": "collecting",
            "message": f"Collected {len(all_comments)} comments total",
            "progress": 60,
        })

        if not all_comments:
            storage_svc.update_research_status(
                research_id, "complete", len(relevant_threads), 0
            )
            q.put({
                "stage": "complete",
                "message": "No comments found in the threads.",
                "progress": 100,
            })
            return

        # Stage 3: Score comments
        q.put({
            "stage": "scoring",
            "message": f"Scoring {len(all_comments)} comments for relevancy...",
            "progress": 62,
        })

        scored_comments = scoring_svc.score_comments(
            question, all_comments, progress_callback=_make_scoring_progress_callback(q, 62, 33, research_id=research_id)
        )
        storage_svc.save_scored_comments(research_id, scored_comments)
        q.put({"stage": "scoring", "message": "Scoring complete", "progress": 95})

        # Stage 4: Finalize
        storage_svc.update_research_status(
            research_id,
            "complete",
            num_threads=len(relevant_threads),
            num_comments=len(scored_comments),
        )
        storage_svc.export_csv(research_id)
        q.put({"stage": "complete", "message": "Research complete!", "progress": 100})

    except Exception as e:
        storage_svc.update_research_status(research_id, "error")
        q.put({"stage": "error", "message": str(e), "progress": 0})
    finally:
        article_svc.clear_cache()
        q.put(None)  # Signal stream end


@app.route("/api/research/<research_id>/stream")
def research_stream(research_id):
    """SSE endpoint for real-time progress updates."""

    def generate():
        q = progress_queues.get(research_id)
        if not q:
            yield f"data: {json.dumps({'stage': 'error', 'message': 'Research not found'})}\n\n"
            return
        pipeline_done = False
        try:
            while True:
                try:
                    msg = q.get(timeout=120)
                except queue.Empty:
                    yield f"data: {json.dumps({'stage': 'error', 'message': 'Research timed out'})}\n\n"
                    pipeline_done = True
                    break
                if msg is None:
                    pipeline_done = True
                    break
                yield f"data: {json.dumps(msg)}\n\n"
        except GeneratorExit:
            pass  # Client disconnected; keep queue alive for reconnect
        finally:
            if pipeline_done:
                progress_queues.pop(research_id, None)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/research/<research_id>")
def get_research(research_id):
    research = storage_svc.get_research(research_id)
    if not research:
        return jsonify(error="Not found"), 404
    threads = storage_svc.get_threads(research_id)
    comments = storage_svc.get_comments(research_id)
    return jsonify(research=research, threads=threads, comments=comments)


@app.route("/api/research/<research_id>/summarize", methods=["POST"])
def summarize(research_id):
    research = storage_svc.get_research(research_id)
    if not research:
        return jsonify(error="Not found"), 404
    data = request.get_json(silent=True) or {}
    user_feedback = (data.get("feedback") or "").strip() or None
    if user_feedback and len(user_feedback) > 500:
        user_feedback = user_feedback[:500]

    max_comments = int(data.get("max_comments", 50))
    max_comments = max(25, min(max_comments, 200))

    comments_data = storage_svc.get_comments(research_id)
    scored_fields = {f for f in ScoredComment.__dataclass_fields__}
    comments = [ScoredComment(**{k: v for k, v in c.items() if k in scored_fields}) for c in comments_data]
    threads_data = storage_svc.get_threads(research_id)
    summary = summary_svc.summarize(research["question"], comments, user_feedback=user_feedback, threads=threads_data, max_comments=max_comments)
    storage_svc.save_summary(research_id, summary)
    return jsonify(summary=summary)


@app.route("/api/research/<research_id>/export")
def export(research_id):
    filepath = storage_svc.export_csv(research_id)
    return send_file(filepath, as_attachment=True)


@app.route("/api/history")
def history():
    return jsonify(history=storage_svc.get_history())


@app.route("/api/research/<research_id>/threads/<thread_id>", methods=["DELETE"])
def delete_thread(research_id, thread_id):
    """Remove a thread and all its comments from a research."""
    research = storage_svc.get_research(research_id)
    if not research:
        return jsonify(error="Not found"), 404
    storage_svc.delete_thread(research_id, thread_id)
    return jsonify(success=True)


@app.route("/api/research/<research_id>/comments/<comment_id>/user-relevancy", methods=["PUT"])
def set_user_relevancy(research_id, comment_id):
    """Set or clear user relevancy score for a comment."""
    data = request.get_json()
    score = data.get("score")
    if score is not None:
        score = int(score)
        if score < 1 or score > 10:
            return jsonify(error="Score must be 1-10"), 400
    storage_svc.set_user_relevancy(research_id, comment_id, score)
    return jsonify(success=True, user_relevancy_score=score)


@app.route("/api/research/<research_id>/comments/<comment_id>/star", methods=["POST"])
def toggle_star(research_id, comment_id):
    """Toggle starred status for a comment."""
    new_val = storage_svc.toggle_star(research_id, comment_id)
    return jsonify(success=True, starred=new_val)


@app.route("/api/research/<research_id>/archive", methods=["POST"])
def archive_research(research_id):
    """Archive a research (remove from sidebar)."""
    research = storage_svc.get_research(research_id)
    if not research:
        return jsonify(error="Not found"), 404
    storage_svc.archive_research(research_id)
    return jsonify(success=True)


@app.route("/api/research/<research_id>/unarchive", methods=["POST"])
def unarchive_research(research_id):
    """Restore an archived research to the sidebar."""
    storage_svc.unarchive_research(research_id)
    return jsonify(success=True)


@app.route("/api/research/<research_id>/delete", methods=["DELETE"])
def delete_research(research_id):
    """Permanently delete a research and all its data (not CSV files)."""
    storage_svc.delete_research(research_id)
    return jsonify(success=True)


@app.route("/api/archived")
def get_archived():
    """List all archived research entries."""
    return jsonify(archived=storage_svc.get_archived())


@app.route("/api/research/<research_id>/expand", methods=["POST"])
def expand_research(research_id):
    """Start a 'Find More Comments & Articles' expansion across selected sources."""
    research = storage_svc.get_research(research_id)
    if not research:
        return jsonify(error="Not found"), 404

    settings = storage_svc.get_settings(research_id)
    subreddits = settings.get("subreddits") or None
    sorts_tried = settings.get("sorts_tried", [])
    time_filter = settings.get("time_filter", "all")
    max_comments = settings.get("max_comments_per_thread", config.DEFAULT_MAX_COMMENTS_PER_THREAD)
    research_sources = settings.get("sources", ["reddit", "hackernews", "web"])

    # Determine which sources the user wants to search this click
    req_body = request.get_json(silent=True) or {}
    requested_sources = req_body.get("sources", ["reddit", "hackernews", "web"])

    # Build list of tasks to run for this click (multi-source)
    tasks = []
    # Reddit: pick next unused Reddit sort
    if "reddit" in requested_sources and "reddit" in research_sources:
        reddit_sorts = [s for s in ["top", "new", "controversial", "hot"] if s not in sorts_tried]
        if reddit_sorts:
            tasks.append(reddit_sorts[0])
    # HN: use next page (supports up to HN_MAX_EXPAND_PAGES pages)
    if "hackernews" in requested_sources and "hackernews" in research_sources:
        hn_tried_count = len([s for s in sorts_tried if s.startswith("hn_") or s == "hn"])
        if hn_tried_count < config.HN_MAX_EXPAND_PAGES:
            tasks.append(f"hn_{hn_tried_count}")
    # Web: use next page (supports up to WEB_MAX_EXPAND_PAGES pages)
    if "web" in requested_sources and "web" in research_sources:
        web_tried_count = len([s for s in sorts_tried if s.startswith("web_") or s == "web"])
        if web_tried_count < config.WEB_MAX_EXPAND_PAGES:
            tasks.append(f"web_{web_tried_count}")

    if not tasks:
        return jsonify(error="All search strategies have been tried for the selected sources."), 400

    q: queue.Queue = queue.Queue()
    expand_queues[research_id] = q

    t = threading.Thread(
        target=run_expand_pipeline,
        args=(research_id, research["question"], subreddits, tasks, time_filter, max_comments, q),
        daemon=True,
    )
    t.start()

    return jsonify(sorts_used=tasks)


def run_expand_pipeline(
    research_id: str,
    question: str,
    subreddits,
    sorts: list,
    time_filter: str,
    max_comments: int,
    q: queue.Queue,
):
    """Background task that finds more threads across multiple sources and merges results."""
    try:
        existing_thread_ids = storage_svc.get_existing_thread_ids(research_id)

        candidates = []
        seen_candidate_ids = set()
        discovery_step = max(1, 13 // len(sorts))

        for task_idx, sort in enumerate(sorts):
            base_pct = 5 + task_idx * discovery_step

            if sort.startswith("hn_"):
                hn_page = int(sort.split("_")[1])
                page_label = f" (page {hn_page + 1})" if hn_page > 0 else ""
                q.put({
                    "stage": "searching",
                    "message": f"Searching Hacker News for more discussions{page_label}...",
                    "progress": base_pct,
                })
                _, search_queries = scoring_svc.suggest_subreddits(question)
                web_queries = search_queries if search_queries else [question]
                for thd in hn_svc.search_stories(web_queries, max_results=config.HN_MAX_STORIES, page=hn_page):
                    if thd.id not in seen_candidate_ids:
                        candidates.append(thd)
                        seen_candidate_ids.add(thd.id)

            elif sort.startswith("web_"):
                web_page = int(sort.split("_")[1])
                page_label = f" (page {web_page + 1})" if web_page > 0 else ""
                q.put({
                    "stage": "searching",
                    "message": f"Searching the web for more articles{page_label}...",
                    "progress": base_pct,
                })
                _, search_queries = scoring_svc.suggest_subreddits(question)
                web_queries = search_queries if search_queries else [question]
                article_urls = web_search_svc.search_web_articles(web_queries, max_results=config.WEB_MAX_ARTICLES, page=web_page)
                for url in article_urls:
                    result = article_svc.fetch_article(url)
                    if result:
                        title, article_body = result
                        thd = article_svc.make_thread(url, title, article_body)
                        article_svc.extract_quotes(thd.id, url, title, article_body, question)
                        if thd.id not in seen_candidate_ids:
                            candidates.append(thd)
                            seen_candidate_ids.add(thd.id)

            else:
                # Reddit sort
                subreddit_label = (
                    ", ".join(f"r/{s}" for s in subreddits) if subreddits else "all of Reddit"
                )
                q.put({
                    "stage": "searching",
                    "message": f"Searching {subreddit_label} sorted by {sort}...",
                    "progress": base_pct,
                })
                reddit_threads = list(
                    reddit_svc.search_threads(
                        question,
                        max_threads=config.MAX_THREADS_LIMIT,
                        time_filter=time_filter,
                        sort=sort,
                        subreddits=subreddits,
                    )
                )
                q.put({
                    "stage": "searching",
                    "message": "Searching the web for more Reddit threads...",
                    "progress": base_pct + max(1, discovery_step // 2),
                })
                web_reddit_threads = web_search_svc.search_reddit_threads(
                    [question], max_results=10, subreddits=subreddits, max_total=config.MAX_THREADS_LIMIT
                )
                for thd in reddit_threads + web_reddit_threads:
                    if thd.id not in seen_candidate_ids:
                        candidates.append(thd)
                        seen_candidate_ids.add(thd.id)

        # Remove threads already collected
        new_threads = [thd for thd in candidates if thd.id not in existing_thread_ids]
        q.put({
            "stage": "searching",
            "message": f"Found {len(new_threads)} new threads — filtering for relevancy...",
            "progress": 20,
        })

        if not new_threads:
            current_tried = storage_svc.get_settings(research_id).get("sorts_tried", [])
            storage_svc.update_settings(research_id, {"sorts_tried": current_tried + sorts})
            q.put({"stage": "complete", "message": "No new threads found.", "progress": 100, "found_nothing": True})
            return

        # Score threads for relevancy
        relevant_threads = scoring_svc.score_threads(question, new_threads)
        q.put({
            "stage": "searching",
            "message": f"{len(relevant_threads)} of {len(new_threads)} new threads are relevant",
            "progress": 30,
            "threads_relevant": len(relevant_threads),
            "threads_total": len(new_threads),
        })

        # Collect comments (dispatch by source)
        all_comments = []
        for i, thread in enumerate(relevant_threads):
            q.put({
                "stage": "collecting",
                "message": f"Collecting comments from thread {i + 1}/{len(relevant_threads)}: {thread.title[:60]}...",
                "progress": 30 + int(35 * (i / len(relevant_threads))),
                "thread_title": thread.title[:60],
            })
            comments = _collect_comments_for_thread(thread, max_comments, reddit_svc, hn_svc, article_svc)
            all_comments.extend(comments)
            if comments:
                storage_svc.save_raw_comments(research_id, comments)
            q.put({
                "stage": "collecting",
                "message": f"Collected {len(comments)} comments from \"{thread.title[:40]}\"",
                "progress": 30 + int(35 * ((i + 1) / len(relevant_threads))),
                "thread_title": thread.title[:60],
                "thread_comments": len(comments),
            })

        # Apply total cap to keep scoring time predictable
        if len(all_comments) > config.TOTAL_COMMENTS_CAP:
            all_comments.sort(key=lambda c: c.score, reverse=True)
            all_comments = all_comments[: config.TOTAL_COMMENTS_CAP]

        q.put({
            "stage": "collecting",
            "message": f"Collected {len(all_comments)} new comments total",
            "progress": 65,
        })

        if not all_comments:
            current_tried = storage_svc.get_settings(research_id).get("sorts_tried", [])
            storage_svc.update_settings(research_id, {"sorts_tried": current_tried + sorts})
            q.put({"stage": "complete", "message": "No relevant threads or articles found.", "progress": 100, "found_nothing": True})
            return

        # Score comments
        scored = scoring_svc.score_comments(question, all_comments, progress_callback=_make_scoring_progress_callback(q, 65, 30, research_id=research_id))

        # Save threads and comments together so they always appear as a pair
        storage_svc.save_threads(research_id, relevant_threads)
        storage_svc.save_scored_comments(research_id, scored)

        # Update counts and mark all sorts as tried
        storage_svc.recalculate_counts(research_id)
        current_tried = storage_svc.get_settings(research_id).get("sorts_tried", [])
        storage_svc.update_settings(research_id, {"sorts_tried": current_tried + sorts})
        storage_svc.export_csv(research_id)

        q.put({"stage": "complete", "message": f"Added {len(scored)} new comments!", "progress": 100})

    except Exception as e:
        q.put({"stage": "error", "message": str(e), "progress": 0})
    finally:
        article_svc.clear_cache()
        q.put(None)


@app.route("/api/research/<research_id>/expand/stream")
def expand_stream(research_id):
    """SSE endpoint for expand progress updates."""
    def generate():
        q = expand_queues.get(research_id)
        if not q:
            yield f"data: {json.dumps({'stage': 'error', 'message': 'Expand task not found'})}\n\n"
            return
        deadline = 300  # total seconds before hard timeout
        elapsed = 0
        while elapsed < deadline:
            try:
                msg = q.get(timeout=15)
            except queue.Empty:
                elapsed += 15
                # Send a keepalive comment to prevent proxy/browser from dropping the connection
                yield ": keepalive\n\n"
                continue
            if msg is None:
                break
            yield f"data: {json.dumps(msg)}\n\n"
            elapsed = 0  # reset idle timer on activity
        else:
            yield f"data: {json.dumps({'stage': 'error', 'message': 'Expand timed out'})}\n\n"
        expand_queues.pop(research_id, None)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/research/<research_id>/expand/status")
def expand_status(research_id):
    """Return whether more expansions are possible."""
    settings = storage_svc.get_settings(research_id)
    sorts_tried = settings.get("sorts_tried", [])
    research_sources = settings.get("sources", ["reddit", "hackernews", "web"])

    # Reddit: up to 4 sorts (top/new/controversial/hot)
    reddit_remaining = [s for s in REDDIT_EXPAND_SORTS if s not in sorts_tried and "reddit" in research_sources]
    reddit_exhausted = len(reddit_remaining) == 0

    # HN: up to HN_MAX_EXPAND_PAGES pages — backward compat: "hn" counts as page 0
    hn_tried_count = len([s for s in sorts_tried if s.startswith("hn_") or s == "hn"])
    hn_exhausted = hn_tried_count >= config.HN_MAX_EXPAND_PAGES or "hackernews" not in research_sources

    # Web: up to WEB_MAX_EXPAND_PAGES pages — backward compat: "web" counts as page 0
    web_tried_count = len([s for s in sorts_tried if s.startswith("web_") or s == "web"])
    web_exhausted = web_tried_count >= config.WEB_MAX_EXPAND_PAGES or "web" not in research_sources

    can_expand = not (reddit_exhausted and hn_exhausted and web_exhausted)
    return jsonify(
        can_expand=can_expand,
        next_sort=reddit_remaining[0] if reddit_remaining else None,
        sorts_tried=sorts_tried,
        research_sources=research_sources,
        reddit_exhausted=reddit_exhausted,
        hn_exhausted=hn_exhausted,
        web_exhausted=web_exhausted,
    )


@app.route("/api/research/<research_id>/add-thread", methods=["POST"])
def add_thread(research_id):
    """Add a specific thread or article URL to an existing research."""
    research = storage_svc.get_research(research_id)
    if not research:
        return jsonify(error="Not found"), 404

    data = request.get_json()
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify(error="URL is required"), 400

    # Detect source from URL
    source = None
    thread_id = None

    # Try Reddit
    match = re.search(r"reddit\.com/r/\w+/comments/(\w+)", url)
    if not match:
        match = re.search(r"redd\.it/(\w+)", url)
    if match:
        source = "reddit"
        thread_id = match.group(1)

    # Try Hacker News
    if not source and "news.ycombinator.com" in url:
        hn_match = re.search(r"id=(\d+)", url)
        if hn_match:
            source = "hackernews"
            thread_id = f"hn_{hn_match.group(1)}"

    # Fall back to web article
    if not source:
        source = "web"
        # Pre-compute thread_id for dedup check
        import hashlib as _hashlib
        url_hash = _hashlib.md5(url.encode()).hexdigest()[:10]
        thread_id = f"web_{url_hash}"

    # Check if thread has already been collected for this research
    existing_ids = storage_svc.get_existing_thread_ids(research_id)
    if thread_id and thread_id in existing_ids:
        return jsonify(
            already_exists=True,
            message="This thread has already been processed for this research.",
        )

    settings = storage_svc.get_settings(research_id)
    max_comments = settings.get("max_comments_per_thread", config.DEFAULT_MAX_COMMENTS_PER_THREAD)

    q: queue.Queue = queue.Queue()
    add_thread_queues[research_id] = q
    t = threading.Thread(
        target=run_add_thread_pipeline,
        args=(research_id, research["question"], thread_id, max_comments, q, source, url),
        daemon=True,
    )
    t.start()
    return jsonify(thread_id=thread_id or "pending", source=source)


def run_add_thread_pipeline(
    research_id: str,
    question: str,
    thread_id: str,
    max_comments: int,
    q: queue.Queue,
    source: str = "reddit",
    url: str = "",
):
    """Fetch, score, and store comments for a single manually-added thread."""
    try:
        q.put({"stage": "fetching", "message": "Fetching thread details...", "progress": 10, "event": "fetch_start"})

        if source == "hackernews":
            # Fetch HN story details via Algolia item endpoint
            import requests as _requests
            numeric_id = thread_id.replace("hn_", "")
            resp = _requests.get(f"https://hn.algolia.com/api/v1/items/{numeric_id}", timeout=15)
            resp.raise_for_status()
            item = resp.json()
            thread = RedditThread(
                id=thread_id,
                title=item.get("title") or "",
                subreddit="Hacker News",
                score=item.get("points") or 0,
                num_comments=len(item.get("children", [])),
                url=item.get("url") or f"https://news.ycombinator.com/item?id={numeric_id}",
                permalink=f"https://news.ycombinator.com/item?id={numeric_id}",
                selftext=(item.get("text") or "")[:2000],
                created_utc=float(item.get("created_at_i") or 0),
                author=item.get("author") or "",
                source="hackernews",
            )
            storage_svc.save_threads(research_id, [thread])
            q.put({
                "stage": "collecting",
                "message": f"Collecting comments from: {thread.title[:60]}...",
                "progress": 30,
                "thread_title": thread.title[:60],
            })
            comments = hn_svc.collect_comments(thread_id, max_comments=max_comments)

        elif source == "web":
            # Fetch and extract web article
            result = article_svc.fetch_article(url)
            if not result:
                q.put({"stage": "error", "message": "Could not extract content from this URL.", "progress": 0})
                return
            title, body = result
            thread = article_svc.make_thread(url, title, body)
            thread_id = thread.id
            storage_svc.save_threads(research_id, [thread])
            q.put({
                "stage": "collecting",
                "message": f"Extracting quotes from: {thread.title[:60]}...",
                "progress": 30,
                "thread_title": thread.title[:60],
            })
            comments = article_svc.extract_quotes(thread_id, url, title, body, question)

        else:
            # Reddit (existing behavior)
            sub = reddit_svc.reddit.submission(id=thread_id)
            thread = RedditThread(
                id=sub.id,
                title=sub.title,
                subreddit=str(sub.subreddit),
                score=sub.score,
                num_comments=sub.num_comments,
                url=sub.url,
                permalink=f"https://reddit.com{sub.permalink}",
                selftext=(sub.selftext or "")[:2000],
                created_utc=sub.created_utc,
                author=str(sub.author) if sub.author else "[deleted]",
            )
            storage_svc.save_threads(research_id, [thread])
            q.put({
                "stage": "collecting",
                "message": f"Collecting comments from: {thread.title[:60]}...",
                "progress": 30,
                "thread_title": thread.title[:60],
            })
            comments = reddit_svc.collect_comments(thread_id, max_comments=max_comments)

        if not comments:
            storage_svc.recalculate_counts(research_id)
            q.put({"stage": "complete", "message": "Thread added (no comments found).", "progress": 100})
            return

        storage_svc.save_raw_comments(research_id, comments)

        q.put({
            "stage": "collecting",
            "message": f"Collected {len(comments)} comments",
            "progress": 50,
            "thread_title": thread.title[:60],
            "thread_comments": len(comments),
        })

        q.put({
            "stage": "scoring",
            "message": f"Scoring {len(comments)} comments for relevancy...",
            "progress": 55,
        })

        scored = scoring_svc.score_comments(
            question, comments,
            progress_callback=_make_scoring_progress_callback(q, 55, 40, research_id=research_id),
        )
        storage_svc.save_scored_comments(research_id, scored)
        storage_svc.recalculate_counts(research_id)
        storage_svc.export_csv(research_id)

        q.put({
            "stage": "complete",
            "message": f"Added \"{thread.title[:50]}\" with {len(scored)} comments!",
            "progress": 100,
        })

    except Exception as e:
        q.put({"stage": "error", "message": str(e), "progress": 0})
    finally:
        article_svc.clear_cache()
        q.put(None)


@app.route("/api/research/<research_id>/add-thread/stream")
def add_thread_stream(research_id):
    """SSE endpoint for add-thread progress updates."""
    def generate():
        q = add_thread_queues.get(research_id)
        if not q:
            yield f"data: {json.dumps({'stage': 'error', 'message': 'Task not found'})}\n\n"
            return
        while True:
            try:
                msg = q.get(timeout=120)
            except queue.Empty:
                yield f"data: {json.dumps({'stage': 'error', 'message': 'Timed out'})}\n\n"
                break
            if msg is None:
                break
            yield f"data: {json.dumps(msg)}\n\n"
        add_thread_queues.pop(research_id, None)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Rescore unscored comments ─────────────────────────────────────────────────

@app.route("/api/research/<research_id>/unscored-count")
def unscored_count(research_id):
    count = storage_svc.get_unscored_count(research_id)
    return jsonify(unscored_count=count)


@app.route("/api/research/<research_id>/rescore", methods=["POST"])
def rescore_comments(research_id):
    research = storage_svc.get_research(research_id)
    if not research:
        return jsonify(error="Not found"), 404

    unscored = storage_svc.get_unscored_comments(research_id)
    if not unscored:
        return jsonify(error="No unscored comments found"), 400

    q: queue.Queue = queue.Queue()
    rescore_queues[research_id] = q
    t = threading.Thread(
        target=run_rescore_pipeline,
        args=(research_id, research["question"], unscored, q),
        daemon=True,
    )
    t.start()
    return jsonify(count=len(unscored))


def run_rescore_pipeline(research_id, question, unscored_comments, q):
    """Background task to score previously unscored comments."""
    try:
        q.put({
            "stage": "scoring",
            "message": f"Scoring {len(unscored_comments)} unscored comments...",
            "progress": 10,
        })
        scored = scoring_svc.score_comments(
            question, unscored_comments,
            progress_callback=_make_scoring_progress_callback(q, 10, 85, research_id=research_id),
        )
        storage_svc.save_scored_comments(research_id, scored)
        storage_svc.recalculate_counts(research_id)
        storage_svc.export_csv(research_id)
        q.put({
            "stage": "complete",
            "message": f"Scored {len(scored)} comments!",
            "progress": 100,
        })
    except Exception as e:
        q.put({"stage": "error", "message": str(e), "progress": 0})
    finally:
        q.put(None)


@app.route("/api/research/<research_id>/rescore/stream")
def rescore_stream(research_id):
    """SSE endpoint for rescore progress updates."""
    def generate():
        q = rescore_queues.get(research_id)
        if not q:
            yield f"data: {json.dumps({'stage': 'error', 'message': 'Task not found'})}\n\n"
            return
        while True:
            try:
                msg = q.get(timeout=120)
            except queue.Empty:
                yield f"data: {json.dumps({'stage': 'error', 'message': 'Timed out'})}\n\n"
                break
            if msg is None:
                break
            yield f"data: {json.dumps(msg)}\n\n"
        rescore_queues.pop(research_id, None)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(debug=config.DEBUG, port=config.PORT)
