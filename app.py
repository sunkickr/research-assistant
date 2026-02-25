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

# Active research streams: research_id -> queue.Queue
progress_queues: dict[str, queue.Queue] = {}
# Active expand streams: research_id -> queue.Queue
expand_queues: dict[str, queue.Queue] = {}
# Active add-thread streams: research_id -> queue.Queue
add_thread_queues: dict[str, queue.Queue] = {}

# Sort order cycle for successive "Find More" expansions
EXPAND_SORTS = ["top", "new", "controversial", "hot"]


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

    research_id = uuid.uuid4().hex[:12]
    settings = {
        "max_threads": max_threads,
        "max_comments_per_thread": max_comments,
        "time_filter": time_filter,
    }
    storage_svc.create_research(research_id, question, settings)

    # Create progress queue and start background task
    q: queue.Queue = queue.Queue()
    progress_queues[research_id] = q
    t = threading.Thread(
        target=run_research_pipeline,
        args=(research_id, question, max_threads, max_comments, time_filter, q, seed_urls),
        daemon=True,
    )
    t.start()

    return jsonify(research_id=research_id)


def run_research_pipeline(
    research_id: str,
    question: str,
    max_threads: int,
    max_comments: int,
    time_filter: str,
    q: queue.Queue,
    seed_urls: list = None,
):
    """Background task that runs the full research pipeline."""
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
                match = re.search(r"reddit\.com/r/\w+/comments/(\w+)", url)
                if not match:
                    match = re.search(r"redd\.it/(\w+)", url)
                if not match:
                    continue
                thread_id = match.group(1)
                try:
                    sub = reddit_svc.reddit.submission(id=thread_id)
                    thread = RedditThread(
                        id=sub.id,
                        title=sub.title,
                        subreddit=str(sub.subreddit),
                        score=sub.score,
                        num_comments=sub.num_comments,
                        url=sub.url,
                        permalink=f"https://reddit.com{sub.permalink}",
                        selftext=(sub.selftext or "")[:500],
                        created_utc=sub.created_utc,
                        author=str(sub.author) if sub.author else "[deleted]",
                    )
                    relevant_threads.append(thread)
                except Exception:
                    continue

            if not relevant_threads:
                storage_svc.update_research_status(research_id, "complete", 0, 0)
                q.put({
                    "stage": "complete",
                    "message": "None of the provided URLs are valid Reddit threads.",
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
            # --- Normal flow: discover subreddits, search Reddit + web ---
            # Stage 1: Discover relevant subreddits
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
                })
            else:
                q.put({
                    "stage": "searching",
                    "message": "Searching across all of Reddit...",
                    "progress": 8,
                })

            # Store validated subreddits in settings for display on results page
            storage_svc.update_research_subreddits(research_id, validated)

            # Stage 2: Search threads within those subreddits
            q.put({
                "stage": "searching",
                "message": "Searching for relevant threads...",
                "progress": 10,
            })
            threads = list(
                reddit_svc.search_threads(
                    question,
                    max_threads=max_threads,
                    time_filter=time_filter,
                    subreddits=validated or None,
                )
            )

            # Stage 2a: Web search for additional threads via DuckDuckGo
            q.put({
                "stage": "searching",
                "message": f"Found {len(threads)} threads via Reddit — searching the web for more...",
                "progress": 14,
            })
            web_queries = search_queries if search_queries else [question]
            web_threads = web_search_svc.search_reddit_threads(web_queries, max_results=15, subreddits=validated or None, max_total=max_threads)
            seen_ids = {t.id for t in threads}
            web_added = 0
            for wt in web_threads:
                if wt.id not in seen_ids:
                    threads.append(wt)
                    seen_ids.add(wt.id)
                    web_added += 1

            q.put({
                "stage": "searching",
                "message": f"Found {len(threads)} threads total ({web_added} from web search) — filtering for relevancy...",
                "progress": 18,
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
            })
            storage_svc.save_threads(research_id, relevant_threads)

        # --- Shared: collect comments ---
        all_comments = []
        for i, thread in enumerate(relevant_threads):
            q.put({
                "stage": "collecting",
                "message": f"Collecting comments from thread {i + 1}/{len(relevant_threads)}: {thread.title[:60]}...",
                "progress": 20 + int(40 * (i / len(relevant_threads))),
            })
            comments = reddit_svc.collect_comments(
                thread.id, max_comments=max_comments
            )
            all_comments.extend(comments)

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

        def on_batch_progress(batch_num, total_batches):
            pct = 62 + int(33 * (batch_num / total_batches))
            q.put({
                "stage": "scoring",
                "message": f"Scoring batch {batch_num}/{total_batches}...",
                "progress": pct,
            })

        scored_comments = scoring_svc.score_comments(
            question, all_comments, progress_callback=on_batch_progress
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
        q.put(None)  # Signal stream end


@app.route("/api/research/<research_id>/stream")
def research_stream(research_id):
    """SSE endpoint for real-time progress updates."""

    def generate():
        q = progress_queues.get(research_id)
        if not q:
            yield f"data: {json.dumps({'stage': 'error', 'message': 'Research not found'})}\n\n"
            return
        while True:
            try:
                msg = q.get(timeout=120)
            except queue.Empty:
                yield f"data: {json.dumps({'stage': 'error', 'message': 'Research timed out'})}\n\n"
                break
            if msg is None:
                break
            yield f"data: {json.dumps(msg)}\n\n"
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
    comments_data = storage_svc.get_comments(research_id)
    scored_fields = {f for f in ScoredComment.__dataclass_fields__}
    comments = [ScoredComment(**{k: v for k, v in c.items() if k in scored_fields}) for c in comments_data]
    summary = summary_svc.summarize(research["question"], comments)
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


@app.route("/api/research/<research_id>/expand", methods=["POST"])
def expand_research(research_id):
    """Start a 'Find More Comments' expansion using the next sort order."""
    research = storage_svc.get_research(research_id)
    if not research:
        return jsonify(error="Not found"), 404

    settings = storage_svc.get_settings(research_id)
    subreddits = settings.get("subreddits") or None
    sorts_tried = settings.get("sorts_tried", [])
    time_filter = settings.get("time_filter", "all")
    max_comments = settings.get("max_comments_per_thread", config.DEFAULT_MAX_COMMENTS_PER_THREAD)

    # Pick the next unused sort order
    next_sort = next((s for s in EXPAND_SORTS if s not in sorts_tried), None)
    if next_sort is None:
        return jsonify(error="All search strategies have been tried for this query."), 400

    q: queue.Queue = queue.Queue()
    expand_queues[research_id] = q

    t = threading.Thread(
        target=run_expand_pipeline,
        args=(research_id, research["question"], subreddits, next_sort, time_filter, max_comments, q),
        daemon=True,
    )
    t.start()

    return jsonify(sort_used=next_sort)


def run_expand_pipeline(
    research_id: str,
    question: str,
    subreddits,
    sort: str,
    time_filter: str,
    max_comments: int,
    q: queue.Queue,
):
    """Background task that finds more threads and merges results."""
    try:
        existing_thread_ids = storage_svc.get_existing_thread_ids(research_id)

        subreddit_label = (
            ", ".join(f"r/{s}" for s in subreddits) if subreddits else "all of Reddit"
        )
        q.put({
            "stage": "searching",
            "message": f"Searching {subreddit_label} sorted by {sort}...",
            "progress": 5,
        })

        # Fetch more threads than usual to account for duplicates
        candidates = list(
            reddit_svc.search_threads(
                question,
                max_threads=config.MAX_THREADS_LIMIT,
                time_filter=time_filter,
                sort=sort,
                subreddits=subreddits,
            )
        )

        # Also search the web for additional threads
        q.put({
            "stage": "searching",
            "message": f"Searching the web for more Reddit threads...",
            "progress": 12,
        })
        web_threads = web_search_svc.search_reddit_threads(
            [f"{question} {sort}"], max_results=10, subreddits=subreddits, max_total=config.MAX_THREADS_LIMIT
        )
        seen_ids = {t.id for t in candidates}
        for wt in web_threads:
            if wt.id not in seen_ids:
                candidates.append(wt)
                seen_ids.add(wt.id)

        # Remove threads already collected
        new_threads = [t for t in candidates if t.id not in existing_thread_ids]
        q.put({
            "stage": "searching",
            "message": f"Found {len(new_threads)} new threads — filtering for relevancy...",
            "progress": 20,
        })

        if not new_threads:
            storage_svc.update_settings(research_id, {
                "sorts_tried": storage_svc.get_settings(research_id).get("sorts_tried", []) + [sort]
            })
            q.put({"stage": "complete", "message": "No new threads found with this strategy.", "progress": 100})
            return

        # Score threads for relevancy
        relevant_threads = scoring_svc.score_threads(question, new_threads)
        q.put({
            "stage": "searching",
            "message": f"{len(relevant_threads)} of {len(new_threads)} new threads are relevant",
            "progress": 30,
        })
        # Note: threads are saved after scoring completes so they only appear
        # in the UI once their comments are also ready.

        # Collect comments
        all_comments = []
        for i, thread in enumerate(relevant_threads):
            q.put({
                "stage": "collecting",
                "message": f"Collecting comments from thread {i + 1}/{len(relevant_threads)}: {thread.title[:60]}...",
                "progress": 30 + int(35 * (i / len(relevant_threads))),
            })
            comments = reddit_svc.collect_comments(thread.id, max_comments=max_comments)
            all_comments.extend(comments)

        # Apply total cap to keep scoring time predictable
        if len(all_comments) > config.TOTAL_COMMENTS_CAP:
            all_comments.sort(key=lambda c: c.score, reverse=True)
            all_comments = all_comments[: config.TOTAL_COMMENTS_CAP]

        q.put({
            "stage": "collecting",
            "message": f"Collected {len(all_comments)} new comments",
            "progress": 65,
        })

        if not all_comments:
            storage_svc.update_settings(research_id, {
                "sorts_tried": storage_svc.get_settings(research_id).get("sorts_tried", []) + [sort]
            })
            q.put({"stage": "complete", "message": "No new comments found.", "progress": 100})
            return

        # Score comments
        def on_batch(batch_num, total_batches):
            pct = 65 + int(30 * (batch_num / total_batches))
            q.put({"stage": "scoring", "message": f"Scoring batch {batch_num}/{total_batches}...", "progress": pct})

        scored = scoring_svc.score_comments(question, all_comments, progress_callback=on_batch)

        # Save threads and comments together so they always appear as a pair
        storage_svc.save_threads(research_id, relevant_threads)
        storage_svc.save_scored_comments(research_id, scored)

        # Update counts and mark this sort as tried
        storage_svc.recalculate_counts(research_id)
        settings = storage_svc.get_settings(research_id)
        sorts_tried = settings.get("sorts_tried", []) + [sort]
        storage_svc.update_settings(research_id, {"sorts_tried": sorts_tried})
        storage_svc.export_csv(research_id)

        q.put({"stage": "complete", "message": f"Added {len(scored)} new comments!", "progress": 100})

    except Exception as e:
        q.put({"stage": "error", "message": str(e), "progress": 0})
    finally:
        q.put(None)


@app.route("/api/research/<research_id>/expand/stream")
def expand_stream(research_id):
    """SSE endpoint for expand progress updates."""
    def generate():
        q = expand_queues.get(research_id)
        if not q:
            yield f"data: {json.dumps({'stage': 'error', 'message': 'Expand task not found'})}\n\n"
            return
        while True:
            try:
                msg = q.get(timeout=300)
            except queue.Empty:
                yield f"data: {json.dumps({'stage': 'error', 'message': 'Expand timed out'})}\n\n"
                break
            if msg is None:
                break
            yield f"data: {json.dumps(msg)}\n\n"
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
    remaining = [s for s in EXPAND_SORTS if s not in sorts_tried]
    return jsonify(
        can_expand=len(remaining) > 0,
        next_sort=remaining[0] if remaining else None,
        sorts_tried=sorts_tried,
    )


@app.route("/api/research/<research_id>/add-thread", methods=["POST"])
def add_thread(research_id):
    """Add a specific Reddit thread URL to an existing research."""
    research = storage_svc.get_research(research_id)
    if not research:
        return jsonify(error="Not found"), 404

    data = request.get_json()
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify(error="URL is required"), 400

    # Extract thread ID from full or short Reddit URLs
    match = re.search(r"reddit\.com/r/\w+/comments/(\w+)", url)
    if not match:
        match = re.search(r"redd\.it/(\w+)", url)
    if not match:
        return jsonify(error="Invalid Reddit thread URL. Paste a full reddit.com link."), 400

    thread_id = match.group(1)

    # Check if thread has already been collected for this research
    existing_ids = storage_svc.get_existing_thread_ids(research_id)
    if thread_id in existing_ids:
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
        args=(research_id, research["question"], thread_id, max_comments, q),
        daemon=True,
    )
    t.start()
    return jsonify(thread_id=thread_id)


def run_add_thread_pipeline(
    research_id: str,
    question: str,
    thread_id: str,
    max_comments: int,
    q: queue.Queue,
):
    """Fetch, score, and store comments for a single manually-added thread."""
    try:
        q.put({"stage": "fetching", "message": "Fetching thread details...", "progress": 10})

        sub = reddit_svc.reddit.submission(id=thread_id)
        thread = RedditThread(
            id=sub.id,
            title=sub.title,
            subreddit=str(sub.subreddit),
            score=sub.score,
            num_comments=sub.num_comments,
            url=sub.url,
            permalink=f"https://reddit.com{sub.permalink}",
            selftext=(sub.selftext or "")[:500],
            created_utc=sub.created_utc,
            author=str(sub.author) if sub.author else "[deleted]",
        )
        storage_svc.save_threads(research_id, [thread])

        q.put({
            "stage": "collecting",
            "message": f"Collecting comments from: {thread.title[:60]}...",
            "progress": 30,
        })
        comments = reddit_svc.collect_comments(thread_id, max_comments=max_comments)

        if not comments:
            storage_svc.recalculate_counts(research_id)
            q.put({"stage": "complete", "message": "Thread added (no comments found).", "progress": 100})
            return

        q.put({
            "stage": "scoring",
            "message": f"Scoring {len(comments)} comments for relevancy...",
            "progress": 55,
        })

        def on_batch(batch_num, total_batches):
            pct = 55 + int(40 * (batch_num / total_batches))
            q.put({
                "stage": "scoring",
                "message": f"Scoring batch {batch_num}/{total_batches}...",
                "progress": pct,
            })

        scored = scoring_svc.score_comments(question, comments, progress_callback=on_batch)
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


if __name__ == "__main__":
    app.run(debug=config.DEBUG, port=config.PORT)
