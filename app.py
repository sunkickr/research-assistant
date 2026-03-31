import json
import os
import re
import uuid
import threading
import queue
from datetime import datetime

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    Response,
    send_file,
    send_from_directory,
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
from services.producthunt_service import ProductHuntService
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
alt_llm = OpenAIProvider(config.OPENAI_API_KEY, config.ALT_SUMMARY_MODEL)
scoring_svc = ScoringService(llm, batch_size=config.LLM_BATCH_SIZE)
summary_svc = SummaryService(llm)
alt_summary_svc = SummaryService(alt_llm)
storage_svc = StorageService(config.DB_PATH, config.EXPORT_DIR)
web_search_svc = WebSearchService(reddit_svc.reddit)
hn_svc = HNService()
article_svc = ArticleService(llm)
ph_svc = ProductHuntService(config.PRODUCT_HUNT_API_TOKEN)

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
    settings = json.loads(research.get("settings_json") or "{}")
    research_type = settings.get("research_type", "general")
    template = "product_results.html" if research_type == "product" else "results.html"
    return render_template(
        template, research_id=research_id, research=research, history=history
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
    elif thread.source in ("web", "reviews"):
        return article_svc.get_cached_quotes(thread.id)
    elif thread.source == "producthunt":
        return ph_svc.collect_comments(thread.id, max_comments=max_comments)
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
                        title, body, article_date = result
                        thread = article_svc.make_thread(url, title, body, created_utc=article_date)
                        article_svc.extract_quotes(thread.id, url, title, body, question, created_utc=article_date)
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
                        title, body, article_date = result
                        thread = article_svc.make_thread(url, title, body, created_utc=article_date)
                        if thread.id not in seen_ids:
                            # Extract quotes now and cache them
                            article_svc.extract_quotes(thread.id, url, title, body, question, created_utc=article_date)
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


@app.route("/api/models")
def get_models():
    """Return available summary model names for the UI toggle."""
    return jsonify(
        default_model=config.LLM_MODEL,
        alt_model=config.ALT_SUMMARY_MODEL,
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

    use_alt = data.get("use_alt_model", False)
    svc = alt_summary_svc if use_alt else summary_svc

    comments_data = storage_svc.get_comments(research_id)
    scored_fields = {f for f in ScoredComment.__dataclass_fields__}
    comments = [ScoredComment(**{k: v for k, v in c.items() if k in scored_fields}) for c in comments_data]
    threads_data = storage_svc.get_threads(research_id)
    summary = svc.summarize(research["question"], comments, user_feedback=user_feedback, threads=threads_data, max_comments=max_comments)
    storage_svc.save_summary(research_id, summary)
    return jsonify(summary=summary)


@app.route("/api/research/<research_id>/export")
def export(research_id):
    filepath = storage_svc.export_csv(research_id)
    return send_file(filepath, as_attachment=True)


PUBLISH_DIR = os.path.join(os.path.dirname(__file__), "published")

PUBLISH_SOURCE_QUOTAS = {
    "reddit": 0.60,
    "web": 0.15,
    "hackernews": 0.15,
    "producthunt": 0.10,
}

PUBLISH_SECTION_ORDER = [
    ("general", "General Information"),
    ("issues", "Top Issues"),
    ("feature_requests", "Feature Requests"),
    ("benefits", "Benefits & Strengths"),
    ("competitors", "Competitors"),
    ("alternatives", "Churn Analysis"),
]

PUBLISH_SOURCE_LABELS = {
    "reddit": "Reddit",
    "hackernews": "Hacker News",
    "web": "Web",
    "reviews": "Reviews",
    "producthunt": "Product Hunt",
}

PUBLISH_CATEGORY_LABELS = {
    "general": "General",
    "issues": "Issues",
    "feature_requests": "Features",
    "benefits": "Benefits",
    "competitors": "Competitors",
    "alternatives": "Alternatives",
}


def _select_publish_comments(comments: list, total: int = 50) -> list:
    """Select top comments with source diversity quotas."""
    # Sort all by effective relevancy descending
    def _eff(c):
        ur = c.get("user_relevancy_score")
        ar = c.get("relevancy_score") or 0
        return (ur + 0.5 if ur is not None else ar)

    by_source = {}
    for c in sorted(comments, key=_eff, reverse=True):
        src = c.get("source", "reddit") or "reddit"
        by_source.setdefault(src, []).append(c)

    selected = []
    selected_ids = set()

    # Reserve slots per source
    for source, quota in PUBLISH_SOURCE_QUOTAS.items():
        slots = int(total * quota)
        pool = by_source.get(source, [])
        for c in pool[:slots]:
            if c["id"] not in selected_ids:
                selected.append(c)
                selected_ids.add(c["id"])

    # Reserve slots for issues category
    issues_min = int(total * 0.10)
    issues_count = sum(1 for c in selected if c.get("category") == "issues")
    if issues_count < issues_min:
        for c in sorted(comments, key=_eff, reverse=True):
            if c["id"] not in selected_ids and c.get("category") == "issues":
                selected.append(c)
                selected_ids.add(c["id"])
                issues_count += 1
                if issues_count >= issues_min:
                    break

    # Fill remaining slots from best across all sources
    remaining = total - len(selected)
    if remaining > 0:
        all_sorted = sorted(comments, key=_eff, reverse=True)
        for c in all_sorted:
            if c["id"] not in selected_ids:
                selected.append(c)
                selected_ids.add(c["id"])
                if len(selected) >= total:
                    break

    # Final sort by relevancy desc, then date desc
    selected.sort(key=lambda c: (_eff(c), c.get("created_utc") or 0), reverse=True)
    return selected


def _md_to_html(text: str, comment_ids: set = None, return_citations: bool = False):
    """Convert markdown summary text to HTML with citation anchors.

    If return_citations is True, returns (html, cited_ids_list) tuple.
    """
    if not text:
        return ("", []) if return_citations else ""
    import re as _re

    # Build citation ordering
    citation_order = []
    citation_index = {}
    if comment_ids is None:
        comment_ids = set()

    for m in _re.finditer(r'\[#([^\]]+)\]', text):
        cid = m.group(1)
        if cid not in citation_index:
            citation_index[cid] = len(citation_order) + 1
            citation_order.append(cid)

    lines = text.split('\n')
    html_parts = []
    in_ul = False
    in_ol = False
    in_li = False  # track whether we're inside a <li> with sub-content

    def close_li():
        nonlocal in_li
        s = ''
        if in_li:
            s += '</li>'
            in_li = False
        return s

    def close_list():
        nonlocal in_ul, in_ol
        s = close_li()
        if in_ul:
            s += '</ul>'
            in_ul = False
        if in_ol:
            s += '</ol>'
            in_ol = False
        return s

    def inline(line):
        # Bold
        line = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line)
        # Citations [#id] → superscript anchor links
        def replace_citation(m):
            cid = m.group(1)
            num = citation_index.get(cid)
            if not num:
                return ''
            return f'<a href="#comment-{cid}" class="citation-ref">[{num}]</a>'
        line = _re.sub(r'\[#([^\]]+)\]', replace_citation, line)
        # Markdown links [text](url)
        line = _re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)', r'<a href="\2" target="_blank">\1</a>', line)
        return line

    for raw_line in lines:
        trimmed = raw_line.strip()
        if not trimmed:
            continue

        if trimmed.startswith('#### '):
            html_parts.append(close_list() + f'<h4>{inline(trimmed[5:])}</h4>')
        elif trimmed.startswith('### '):
            html_parts.append(close_list() + f'<h4>{inline(trimmed[4:])}</h4>')
        elif trimmed.startswith('## '):
            html_parts.append(close_list() + f'<h3>{inline(trimmed[3:])}</h3>')
        elif trimmed.startswith('# '):
            html_parts.append(close_list() + f'<h2>{inline(trimmed[2:])}</h2>')
        elif trimmed.startswith('> '):
            if in_ol or in_ul:
                # Keep blockquote inside the current list item
                html_parts.append(f'<blockquote>{inline(trimmed[2:])}</blockquote>')
            else:
                html_parts.append(f'<blockquote>{inline(trimmed[2:])}</blockquote>')
        elif _re.match(r'^\d+\.\s', trimmed):
            content = _re.sub(r'^\d+\.\s', '', trimmed)
            if not in_ol:
                html_parts.append(close_list())
                in_ol = True
                html_parts.append(f'<ol>{close_li()}<li>{inline(content)}')
                in_li = True
            else:
                html_parts.append(f'{close_li()}<li>{inline(content)}')
                in_li = True
        elif trimmed.startswith('- ') or trimmed.startswith('* '):
            if not in_ul:
                html_parts.append(close_list())
                in_ul = True
                html_parts.append(f'<ul>{close_li()}<li>{inline(trimmed[2:])}')
                in_li = True
            else:
                html_parts.append(f'{close_li()}<li>{inline(trimmed[2:])}')
                in_li = True
        else:
            if in_ol or in_ul:
                # Continuation text stays inside the current list item
                html_parts.append(f'<p>{inline(trimmed)}</p>')
            else:
                html_parts.append(close_list() + f'<p>{inline(trimmed)}</p>')

    html_parts.append(close_list())
    html = '\n'.join(html_parts)
    if return_citations:
        return html, citation_order
    return html


def _make_publish_filename(slug: str) -> str:
    """Generate unique filename in published/ directory."""
    os.makedirs(PUBLISH_DIR, exist_ok=True)
    base = f"{slug}-research"
    filename = f"{base}.html"
    if not os.path.exists(os.path.join(PUBLISH_DIR, filename)):
        return filename
    n = 2
    while os.path.exists(os.path.join(PUBLISH_DIR, f"{base}-{n}.html")):
        n += 1
    return f"{base}-{n}.html"


@app.route("/api/research/<research_id>/publish", methods=["POST"])
def publish_research(research_id):
    """Generate a self-contained HTML research report."""
    research = storage_svc.get_research(research_id)
    if not research:
        return jsonify(error="Not found"), 404

    settings = storage_svc.get_settings(research_id)
    is_product = settings.get("research_type") == "product"
    product_name = settings.get("product_name", "")
    title = product_name if is_product else research["question"]
    research_sources = settings.get("sources", ["reddit", "hackernews", "web"])

    # Check summaries exist
    if is_product:
        raw_summaries = research.get("product_summaries_json")
        if not raw_summaries:
            return jsonify(error="Generate summaries before publishing"), 400
        summaries = json.loads(raw_summaries)
    else:
        summary_text = research.get("summary")
        if not summary_text:
            return jsonify(error="Generate a summary before publishing"), 400
        summaries = None

    # Parse comment count from request body
    body = request.get_json(silent=True) or {}
    comment_count = min(max(int(body.get("comment_count", 50)), 25), 200)

    # Load comments and threads
    all_comments = storage_svc.get_comments(research_id)
    threads = storage_svc.get_threads(research_id)
    thread_map = {t["id"]: t for t in threads}

    # Select top comments with source quotas
    selected = _select_publish_comments(all_comments, total=comment_count)

    # Format comments for template
    comment_ids = {c["id"] for c in selected}

    formatted_comments = []
    for c in selected:
        source = c.get("source", "reddit") or "reddit"
        body = c.get("body", "")
        body_short = body[:300] if len(body) > 300 else body
        has_more = len(body) > 300
        created = c.get("created_utc")
        date_str = datetime.utcfromtimestamp(created).strftime("%b %d, %Y") if created else ""
        thread = thread_map.get(c.get("thread_id", ""))
        category = c.get("category")

        formatted_comments.append({
            "id": c["id"],
            "source": source,
            "source_label": PUBLISH_SOURCE_LABELS.get(source, source.title()),
            "category_label": PUBLISH_CATEGORY_LABELS.get(category) if category else None,
            "score": c.get("score", 0),
            "author": c.get("author", "anonymous"),
            "date": date_str,
            "body_short": body_short,
            "body_full": body if has_more else None,
            "has_more": has_more,
            "permalink": c.get("permalink", ""),
            "relevancy_score": c.get("relevancy_score"),
            "reasoning": c.get("reasoning", ""),
            "thread_title": thread["title"] if thread else None,
            "thread_url": thread.get("permalink") or thread.get("url") if thread else None,
        })

    # Build lookup of ALL comments by ID for citation sourcing
    all_comment_map = {}
    for c in all_comments:
        cid = c["id"]
        source = c.get("source", "reddit") or "reddit"
        body = c.get("body", "")
        created = c.get("created_utc")
        date_str = datetime.utcfromtimestamp(created).strftime("%b %d, %Y") if created else ""
        all_comment_map[cid] = {
            "id": cid,
            "source": source,
            "source_label": PUBLISH_SOURCE_LABELS.get(source, source.title()),
            "author": c.get("author", "anonymous"),
            "date": date_str,
            "body_short": body[:200] if len(body) > 200 else body,
            "permalink": c.get("permalink", ""),
            "score": c.get("score", 0),
        }

    # Build summary HTML
    if is_product:
        summary_sections = []
        for cat_key, cat_label in PUBLISH_SECTION_ORDER:
            section_text = summaries.get(cat_key, "")
            if section_text:
                html, cited_ids = _md_to_html(section_text, comment_ids, return_citations=True)
                # Collect cited comment details with their citation numbers
                cited_sources = []
                for i, cid in enumerate(cited_ids):
                    if cid in all_comment_map:
                        src = dict(all_comment_map[cid])
                        src["citation_num"] = i + 1
                        cited_sources.append(src)
                summary_sections.append({
                    "label": cat_label,
                    "html": html,
                    "cited_sources": cited_sources,
                })
        summary_html = None
    else:
        summary_sections = None
        summary_html = _md_to_html(summary_text, comment_ids)

    # Source label
    sources_label = ", ".join(
        PUBLISH_SOURCE_LABELS.get(s, s.title()) for s in research_sources
    )

    created_date = (research.get("created_at") or "")[:10]

    # Generate filename
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:50]
    filename = _make_publish_filename(slug)

    # Render template
    html_content = render_template(
        "published_research.html",
        title=title,
        is_product=is_product,
        num_threads=research.get("num_threads", 0),
        total_comments=research.get("num_comments", 0),
        created_date=created_date,
        sources_label=sources_label,
        summary_sections=summary_sections,
        summary_html=summary_html,
        comments=formatted_comments,
    )

    # Write file
    filepath = os.path.join(PUBLISH_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)

    return jsonify(filename=filename, path=f"published/{filename}", total_comments=len(all_comments))


@app.route("/published/<path:filename>")
def serve_published(filename):
    """Serve published research HTML files."""
    return send_from_directory(PUBLISH_DIR, filename)


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
    research_type = settings.get("research_type", "general")
    is_product = research_type == "product"

    default_sources = ["reddit", "hackernews", "web", "reviews", "producthunt"] if is_product else ["reddit", "hackernews", "web"]
    research_sources = settings.get("sources", default_sources)

    # Determine which sources the user wants to search this click
    req_body = request.get_json(silent=True) or {}
    requested_sources = req_body.get("sources", default_sources)

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
    # Reviews: use next page (product mode only)
    if is_product and "reviews" in requested_sources and "reviews" in research_sources:
        reviews_tried_count = len([s for s in sorts_tried if s.startswith("reviews_")])
        if reviews_tried_count < config.REVIEWS_MAX_EXPAND_PAGES:
            tasks.append(f"reviews_{reviews_tried_count}")

    if not tasks:
        return jsonify(error="All search strategies have been tried for the selected sources."), 400

    q: queue.Queue = queue.Queue()
    expand_queues[research_id] = q

    if is_product:
        product_name = settings.get("product_name", research["question"])
        t = threading.Thread(
            target=run_product_expand_pipeline,
            args=(research_id, product_name, tasks, time_filter, max_comments, q),
            daemon=True,
        )
    else:
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
                        title, article_body, article_date = result
                        thd = article_svc.make_thread(url, title, article_body, created_utc=article_date)
                        article_svc.extract_quotes(thd.id, url, title, article_body, question, created_utc=article_date)
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


def run_product_expand_pipeline(
    research_id: str,
    product_name: str,
    sorts: list,
    time_filter: str,
    max_comments: int,
    q: queue.Queue,
):
    """Background task that finds more threads for product research across categories."""
    try:
        existing_thread_ids = storage_svc.get_existing_thread_ids(research_id)

        candidates = []
        seen_candidate_ids = set()
        category_list = list(PRODUCT_CATEGORIES.items())
        discovery_step = max(1, 13 // len(sorts))

        for task_idx, sort in enumerate(sorts):
            base_pct = 5 + task_idx * discovery_step

            if sort.startswith("hn_"):
                hn_page = int(sort.split("_")[1])
                page_label = f" (page {hn_page + 1})" if hn_page > 0 else ""
                q.put({"stage": "searching", "message": f"Searching Hacker News{page_label}...", "progress": base_pct})
                for category, query_templates in category_list:
                    queries = [t.format(product=product_name) for t in query_templates]
                    try:
                        for thd in hn_svc.search_stories(queries, max_results=3, page=hn_page):
                            if thd.id not in seen_candidate_ids:
                                thd.category = category
                                candidates.append(thd)
                                seen_candidate_ids.add(thd.id)
                    except Exception:
                        pass

            elif sort.startswith("web_"):
                web_page = int(sort.split("_")[1])
                page_label = f" (page {web_page + 1})" if web_page > 0 else ""
                q.put({"stage": "searching", "message": f"Searching the web for more articles{page_label}...", "progress": base_pct})
                for category, query_templates in category_list:
                    queries = [t.format(product=product_name) for t in query_templates]
                    try:
                        article_urls = web_search_svc.search_web_articles(queries, max_results=3, page=web_page)
                        for url in article_urls:
                            result = article_svc.fetch_article(url)
                            if result:
                                title, body, article_date = result
                                thd = article_svc.make_thread(url, title, body, created_utc=article_date)
                                if thd.id not in seen_candidate_ids:
                                    article_svc.extract_quotes(thd.id, url, title, body, product_name, created_utc=article_date)
                                    thd.category = category
                                    candidates.append(thd)
                                    seen_candidate_ids.add(thd.id)
                    except Exception:
                        pass

            elif sort.startswith("reviews_"):
                reviews_page = int(sort.split("_")[1])
                page_label = f" (page {reviews_page + 1})" if reviews_page > 0 else ""
                q.put({"stage": "searching", "message": f"Searching review sites{page_label}...", "progress": base_pct})
                try:
                    review_urls = web_search_svc.search_review_sites(
                        product_name, sites=REVIEW_SITES, max_per_site=2,
                    )
                    for url in review_urls:
                        result = article_svc.fetch_article(url)
                        if result:
                            title, body, article_date = result
                            thd = article_svc.make_thread(url, title, body, created_utc=article_date)
                            if thd.id not in seen_candidate_ids:
                                article_svc.extract_quotes(thd.id, url, title, body, product_name, created_utc=article_date)
                                thd.source = "reviews"
                                thd.category = "general"
                                candidates.append(thd)
                                seen_candidate_ids.add(thd.id)
                except Exception:
                    pass

            else:
                # Reddit sort — search across all product categories
                q.put({"stage": "searching", "message": f"Searching Reddit sorted by {sort}...", "progress": base_pct})
                for category, query_templates in category_list:
                    queries = [t.format(product=product_name) for t in query_templates]
                    try:
                        reddit_threads = list(reddit_svc.search_threads(
                            queries[0], max_threads=max(3, 15 // len(category_list)),
                            time_filter=time_filter, sort=sort,
                        ))
                        for thd in reddit_threads:
                            if thd.id not in seen_candidate_ids:
                                thd.category = category
                                candidates.append(thd)
                                seen_candidate_ids.add(thd.id)
                    except Exception:
                        pass
                    try:
                        web_reddit = web_search_svc.search_reddit_threads(queries, max_results=5, max_total=5)
                        for thd in web_reddit:
                            if thd.id not in seen_candidate_ids:
                                thd.category = category
                                candidates.append(thd)
                                seen_candidate_ids.add(thd.id)
                    except Exception:
                        pass

        # Remove threads already collected
        new_threads = [thd for thd in candidates if thd.id not in existing_thread_ids]
        q.put({"stage": "searching", "message": f"Found {len(new_threads)} new threads — filtering for relevancy...", "progress": 20})

        if not new_threads:
            current_tried = storage_svc.get_settings(research_id).get("sorts_tried", [])
            storage_svc.update_settings(research_id, {"sorts_tried": current_tried + sorts})
            q.put({"stage": "complete", "message": "No new threads found.", "progress": 100, "found_nothing": True})
            return

        # Score threads for relevancy
        relevant_threads = scoring_svc.score_threads(f"Product research about {product_name}", new_threads)
        q.put({
            "stage": "searching",
            "message": f"{len(relevant_threads)} of {len(new_threads)} new threads are relevant",
            "progress": 30, "threads_relevant": len(relevant_threads), "threads_total": len(new_threads),
        })

        # Collect comments
        all_comments = []
        for i, thread in enumerate(relevant_threads):
            q.put({
                "stage": "collecting",
                "message": f"Collecting comments from thread {i + 1}/{len(relevant_threads)}: {thread.title[:60]}...",
                "progress": 30 + int(35 * (i / len(relevant_threads))), "thread_title": thread.title[:60],
            })
            comments = _collect_comments_for_thread(thread, max_comments, reddit_svc, hn_svc, article_svc)
            all_comments.extend(comments)
            if comments:
                storage_svc.save_raw_comments(research_id, comments)
            q.put({
                "stage": "collecting",
                "message": f"Collected {len(comments)} comments from \"{thread.title[:40]}\"",
                "progress": 30 + int(35 * ((i + 1) / len(relevant_threads))),
                "thread_title": thread.title[:60], "thread_comments": len(comments),
            })

        # Apply total cap
        if len(all_comments) > config.TOTAL_COMMENTS_CAP:
            all_comments.sort(key=lambda c: c.score, reverse=True)
            all_comments = all_comments[: config.TOTAL_COMMENTS_CAP]

        q.put({"stage": "collecting", "message": f"Collected {len(all_comments)} new comments total", "progress": 65})

        if not all_comments:
            current_tried = storage_svc.get_settings(research_id).get("sorts_tried", [])
            storage_svc.update_settings(research_id, {"sorts_tried": current_tried + sorts})
            q.put({"stage": "complete", "message": "No relevant threads or articles found.", "progress": 100, "found_nothing": True})
            return

        # Score comments with category assignment
        scored = scoring_svc.score_comments_with_category(
            product_name, all_comments,
            progress_callback=_make_scoring_progress_callback(q, 65, 30, research_id=research_id),
        )

        # Save threads and comments
        storage_svc.save_threads(research_id, relevant_threads)
        storage_svc.save_scored_comments(research_id, scored)

        # Update counts and mark sorts as tried
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
    research_type = settings.get("research_type", "general")
    is_product = research_type == "product"
    default_sources = ["reddit", "hackernews", "web", "reviews", "producthunt"] if is_product else ["reddit", "hackernews", "web"]
    research_sources = settings.get("sources", default_sources)

    # Reddit: up to 4 sorts (top/new/controversial/hot)
    reddit_remaining = [s for s in REDDIT_EXPAND_SORTS if s not in sorts_tried and "reddit" in research_sources]
    reddit_exhausted = len(reddit_remaining) == 0

    # HN: up to HN_MAX_EXPAND_PAGES pages — backward compat: "hn" counts as page 0
    hn_tried_count = len([s for s in sorts_tried if s.startswith("hn_") or s == "hn"])
    hn_exhausted = hn_tried_count >= config.HN_MAX_EXPAND_PAGES or "hackernews" not in research_sources

    # Web: up to WEB_MAX_EXPAND_PAGES pages — backward compat: "web" counts as page 0
    web_tried_count = len([s for s in sorts_tried if s.startswith("web_") or s == "web"])
    web_exhausted = web_tried_count >= config.WEB_MAX_EXPAND_PAGES or "web" not in research_sources

    # Reviews: product mode only
    reviews_tried_count = len([s for s in sorts_tried if s.startswith("reviews_")])
    reviews_exhausted = reviews_tried_count >= config.REVIEWS_MAX_EXPAND_PAGES or "reviews" not in research_sources

    all_exhausted = reddit_exhausted and hn_exhausted and web_exhausted
    if is_product:
        all_exhausted = all_exhausted and reviews_exhausted

    can_expand = not all_exhausted
    return jsonify(
        can_expand=can_expand,
        next_sort=reddit_remaining[0] if reddit_remaining else None,
        sorts_tried=sorts_tried,
        research_sources=research_sources,
        research_type=research_type,
        reddit_exhausted=reddit_exhausted,
        hn_exhausted=hn_exhausted,
        web_exhausted=web_exhausted,
        reviews_exhausted=reviews_exhausted,
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
            title, body, article_date = result
            thread = article_svc.make_thread(url, title, body, created_utc=article_date)
            thread_id = thread.id
            storage_svc.save_threads(research_id, [thread])
            q.put({
                "stage": "collecting",
                "message": f"Extracting quotes from: {thread.title[:60]}...",
                "progress": 30,
                "thread_title": thread.title[:60],
            })
            comments = article_svc.extract_quotes(thread_id, url, title, body, question, created_utc=article_date)

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


# ── Product Research Mode ─────────────────────────────────────────────────────

PRODUCT_CATEGORIES = {
    "issues": ["{product} issues", "{product} problems"],
    "feature_requests": ["{product} feature request", "{product} missing feature"],
    "general": ["{product} review", "{product}", "{product} use cases"],
    "competitors": ["{product} competitors", "{product} vs"],
    "benefits": ["{product} benefits", "why use {product}"],
    "alternatives": ["{product} alternatives", "switching from {product}"],
}

REVIEW_SITES = ["g2.com", "capterra.com", "trustpilot.com", "quora.com"]


@app.route("/api/product-research", methods=["POST"])
def start_product_research():
    data = request.get_json()
    product_name = (data.get("product_name") or "").strip()
    if not product_name:
        return jsonify(error="Product name is required"), 400

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

    sources = data.get("sources", ["reddit", "hackernews", "web", "reviews", "producthunt"])
    if not isinstance(sources, list) or not sources:
        sources = ["reddit", "hackernews", "web", "reviews", "producthunt"]

    research_id = uuid.uuid4().hex[:12]
    settings = {
        "research_type": "product",
        "product_name": product_name,
        "max_threads": max_threads,
        "max_comments_per_thread": max_comments,
        "time_filter": time_filter,
        "sources": sources,
    }
    storage_svc.create_research(research_id, f"Product research: {product_name}", settings)

    q: queue.Queue = queue.Queue()
    progress_queues[research_id] = q
    t = threading.Thread(
        target=run_product_research_pipeline,
        args=(research_id, product_name, max_threads, max_comments, time_filter, q, sources),
        daemon=True,
    )
    t.start()
    return jsonify(research_id=research_id)


def run_product_research_pipeline(
    research_id: str,
    product_name: str,
    max_threads: int,
    max_comments: int,
    time_filter: str,
    q: queue.Queue,
    sources: list = None,
):
    """Background task for product research across multiple question categories."""
    if sources is None:
        sources = ["reddit", "hackernews", "web", "reviews", "producthunt"]
    try:
        threads = []
        seen_ids: set = set()
        category_list = list(PRODUCT_CATEGORIES.items())
        total_categories = len(category_list)

        # Stage 1: Search all categories across all sources
        for cat_idx, (category, query_templates) in enumerate(category_list):
            cat_pct_base = int(40 * (cat_idx / total_categories))
            cat_pct_range = int(40 / total_categories)
            queries = [t.format(product=product_name) for t in query_templates]

            q.put({
                "stage": "searching",
                "message": f"Searching for {category.replace('_', ' ')}...",
                "progress": cat_pct_base,
            })

            # Reddit
            if "reddit" in sources:
                try:
                    reddit_threads = list(reddit_svc.search_threads(
                        queries[0], max_threads=max(3, max_threads // total_categories),
                        time_filter=time_filter,
                    ))
                    for t in reddit_threads:
                        if t.id not in seen_ids:
                            t.category = category
                            threads.append(t)
                            seen_ids.add(t.id)
                except Exception:
                    pass

                # Web-augmented Reddit
                try:
                    web_reddit = web_search_svc.search_reddit_threads(
                        queries, max_results=5, max_total=5,
                    )
                    for t in web_reddit:
                        if t.id not in seen_ids:
                            t.category = category
                            threads.append(t)
                            seen_ids.add(t.id)
                except Exception:
                    pass

            # Hacker News
            if "hackernews" in sources:
                try:
                    hn_stories = hn_svc.search_stories(queries, max_results=3)
                    for t in hn_stories:
                        if t.id not in seen_ids:
                            t.category = category
                            threads.append(t)
                            seen_ids.add(t.id)
                except Exception:
                    pass

            # Web articles
            if "web" in sources:
                try:
                    article_urls = web_search_svc.search_web_articles(queries, max_results=3)
                    for url in article_urls:
                        result = article_svc.fetch_article(url)
                        if result:
                            title, body, article_date = result
                            thread = article_svc.make_thread(url, title, body, created_utc=article_date)
                            if thread.id not in seen_ids:
                                article_svc.extract_quotes(thread.id, url, title, body, product_name, created_utc=article_date)
                                thread.category = category
                                threads.append(thread)
                                seen_ids.add(thread.id)
                except Exception:
                    pass

            # Review sites
            if "reviews" in sources:
                try:
                    review_urls = web_search_svc.search_review_sites(
                        product_name, sites=REVIEW_SITES, max_per_site=2,
                    )
                    for url in review_urls:
                        result = article_svc.fetch_article(url)
                        if result:
                            title, body, article_date = result
                            thread = article_svc.make_thread(url, title, body, created_utc=article_date)
                            if thread.id not in seen_ids:
                                article_svc.extract_quotes(thread.id, url, title, body, product_name, created_utc=article_date)
                                thread.source = "reviews"
                                thread.category = category
                                threads.append(thread)
                                seen_ids.add(thread.id)
                except Exception:
                    pass

            # Product Hunt
            if "producthunt" in sources and cat_idx == 0:
                # Only search PH once (on first category) since it's name-based
                try:
                    ph_posts = ph_svc.search_posts(product_name, max_results=config.PH_MAX_POSTS)
                    for t in ph_posts:
                        if t.id not in seen_ids:
                            t.category = "general"
                            threads.append(t)
                            seen_ids.add(t.id)
                    if ph_posts:
                        q.put({
                            "stage": "searching",
                            "message": f"Found {len(ph_posts)} Product Hunt posts",
                            "progress": cat_pct_base + cat_pct_range,
                        })
                except Exception:
                    pass

            q.put({
                "stage": "searching",
                "message": f"Found {len(threads)} threads so far ({category.replace('_', ' ')} done)",
                "progress": cat_pct_base + cat_pct_range,
            })

        q.put({
            "stage": "searching",
            "message": f"Found {len(threads)} threads total — filtering for relevancy...",
            "progress": 40,
        })

        if not threads:
            storage_svc.update_research_status(research_id, "complete", 0, 0)
            q.put({"stage": "complete", "message": "No threads found.", "progress": 100})
            return

        # Score threads for relevancy
        relevant_threads = scoring_svc.score_threads(
            f"Product research about {product_name}", threads,
        )
        q.put({
            "stage": "searching",
            "message": f"{len(relevant_threads)} of {len(threads)} threads are relevant",
            "progress": 45,
            "threads_relevant": len(relevant_threads),
            "threads_total": len(threads),
        })
        storage_svc.save_threads(research_id, relevant_threads)

        # Stage 2: Collect comments
        all_comments = []
        for i, thread in enumerate(relevant_threads):
            q.put({
                "stage": "collecting",
                "message": f"Collecting comments from thread {i + 1}/{len(relevant_threads)}: {thread.title[:60]}...",
                "progress": 45 + int(15 * (i / len(relevant_threads))),
                "thread_title": thread.title[:60],
            })
            comments = _collect_comments_for_thread(thread, max_comments, reddit_svc, hn_svc, article_svc)
            all_comments.extend(comments)
            if comments:
                storage_svc.save_raw_comments(research_id, comments)
            q.put({
                "stage": "collecting",
                "message": f"Collected {len(comments)} comments from \"{thread.title[:40]}\"",
                "progress": 45 + int(15 * ((i + 1) / len(relevant_threads))),
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
                research_id, "complete", len(relevant_threads), 0,
            )
            q.put({"stage": "complete", "message": "No comments found.", "progress": 100})
            return

        # Stage 3: Score with category assignment
        q.put({
            "stage": "scoring",
            "message": f"Scoring {len(all_comments)} comments for relevancy...",
            "progress": 62,
        })

        scored_comments = scoring_svc.score_comments_with_category(
            product_name, all_comments,
            progress_callback=_make_scoring_progress_callback(q, 62, 33, research_id=research_id),
        )
        storage_svc.save_scored_comments(research_id, scored_comments)

        # Stage 4: Finalize
        storage_svc.update_research_status(
            research_id, "complete",
            num_threads=len(relevant_threads),
            num_comments=len(scored_comments),
        )
        storage_svc.export_csv(research_id)
        q.put({"stage": "complete", "message": "Product research complete!", "progress": 100})

    except Exception as e:
        storage_svc.update_research_status(research_id, "error")
        q.put({"stage": "error", "message": str(e), "progress": 0})
    finally:
        article_svc.clear_cache()
        q.put(None)


@app.route("/api/research/<research_id>/summarize-product", methods=["POST"])
def summarize_product(research_id):
    """Generate per-category product summaries."""
    research = storage_svc.get_research(research_id)
    if not research:
        return jsonify(error="Not found"), 404

    data = request.get_json(silent=True) or {}
    max_comments = int(data.get("max_comments", 50))
    max_comments = max(25, min(max_comments, 200))
    feedback = (data.get("feedback") or "")[:500].strip() or None
    use_alt = data.get("use_alt_model", False)
    svc = alt_summary_svc if use_alt else summary_svc

    comments_data = storage_svc.get_comments(research_id)
    scored_fields = {f for f in ScoredComment.__dataclass_fields__}
    comments = [ScoredComment(**{k: v for k, v in c.items() if k in scored_fields}) for c in comments_data]
    threads_data = storage_svc.get_threads(research_id)

    settings = json.loads(research.get("settings_json") or "{}")
    product_name = settings.get("product_name", research["question"])

    summaries = svc.summarize_product(
        product_name, comments, threads=threads_data,
        max_comments=max_comments, user_feedback=feedback,
    )
    storage_svc.save_product_summaries(research_id, summaries)
    return jsonify(summaries=summaries)


@app.route("/api/research/<research_id>/summarize-product-section", methods=["POST"])
def summarize_product_section(research_id):
    """Regenerate a single product summary section with optional feedback."""
    research = storage_svc.get_research(research_id)
    if not research:
        return jsonify(error="Not found"), 404

    data = request.get_json(silent=True) or {}
    category = data.get("category", "")
    if category not in SummaryService.PRODUCT_SECTION_PROMPTS:
        return jsonify(error=f"Invalid category: {category}"), 400

    feedback = (data.get("feedback") or "")[:500].strip() or None
    use_alt = data.get("use_alt_model", False)
    max_comments = min(int(data.get("max_comments", 50)), 100)
    svc = alt_summary_svc if use_alt else summary_svc

    comments_data = storage_svc.get_comments(research_id)
    scored_fields = {f for f in ScoredComment.__dataclass_fields__}
    comments = [ScoredComment(**{k: v for k, v in c.items() if k in scored_fields}) for c in comments_data]
    threads_data = storage_svc.get_threads(research_id)

    settings = json.loads(research.get("settings_json") or "{}")
    product_name = settings.get("product_name", research["question"])

    summary_text = svc.summarize_product_section(
        product_name, comments, category,
        threads=threads_data, user_feedback=feedback,
        max_comments=max_comments,
    )

    # Merge into existing summaries
    existing = storage_svc.get_product_summaries(research_id)
    existing[category] = summary_text
    storage_svc.save_product_summaries(research_id, existing)

    return jsonify(summary=summary_text, category=category)


if __name__ == "__main__":
    app.run(debug=config.DEBUG, port=config.PORT)
