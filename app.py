import json
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
from models.data_models import ScoredComment

app = Flask(__name__)
config = Config()

# Initialize services
reddit_svc = RedditService(
    config.REDDIT_CLIENT_ID, config.REDDIT_CLIENT_SECRET, config.REDDIT_USER_AGENT
)
llm = OpenAIProvider(config.OPENAI_API_KEY, config.LLM_MODEL)
scoring_svc = ScoringService(llm, batch_size=config.LLM_BATCH_SIZE)
summary_svc = SummaryService(llm)
storage_svc = StorageService(config.DB_PATH, config.EXPORT_DIR)

# Active research streams: research_id -> queue.Queue
progress_queues: dict[str, queue.Queue] = {}


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
        args=(research_id, question, max_threads, max_comments, time_filter, q),
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
):
    """Background task that runs the full research pipeline."""
    try:
        # Stage 1: Search threads
        q.put(
            {
                "stage": "searching",
                "message": "Searching Reddit for relevant threads...",
                "progress": 5,
            }
        )
        threads = list(
            reddit_svc.search_threads(
                question, max_threads=max_threads, time_filter=time_filter
            )
        )
        storage_svc.save_threads(research_id, threads)
        q.put(
            {
                "stage": "searching",
                "message": f"Found {len(threads)} threads",
                "progress": 15,
            }
        )

        if not threads:
            storage_svc.update_research_status(research_id, "complete", 0, 0)
            q.put(
                {
                    "stage": "complete",
                    "message": "No threads found. Try a different query.",
                    "progress": 100,
                }
            )
            return

        # Stage 2: Collect comments
        all_comments = []
        for i, thread in enumerate(threads):
            q.put(
                {
                    "stage": "collecting",
                    "message": f"Collecting comments from thread {i + 1}/{len(threads)}: {thread.title[:60]}...",
                    "progress": 15 + int(45 * (i / len(threads))),
                }
            )
            comments = reddit_svc.collect_comments(
                thread.id, max_comments=max_comments
            )
            all_comments.extend(comments)

        # Apply total cap
        if len(all_comments) > config.TOTAL_COMMENTS_CAP:
            all_comments.sort(key=lambda c: c.score, reverse=True)
            all_comments = all_comments[: config.TOTAL_COMMENTS_CAP]

        q.put(
            {
                "stage": "collecting",
                "message": f"Collected {len(all_comments)} comments total",
                "progress": 60,
            }
        )

        if not all_comments:
            storage_svc.update_research_status(
                research_id, "complete", len(threads), 0
            )
            q.put(
                {
                    "stage": "complete",
                    "message": "No comments found in the threads.",
                    "progress": 100,
                }
            )
            return

        # Stage 3: Score comments
        q.put(
            {
                "stage": "scoring",
                "message": f"Scoring {len(all_comments)} comments for relevancy...",
                "progress": 62,
            }
        )

        def on_batch_progress(batch_num, total_batches):
            pct = 62 + int(33 * (batch_num / total_batches))
            q.put(
                {
                    "stage": "scoring",
                    "message": f"Scoring batch {batch_num}/{total_batches}...",
                    "progress": pct,
                }
            )

        scored_comments = scoring_svc.score_comments(
            question, all_comments, progress_callback=on_batch_progress
        )
        storage_svc.save_scored_comments(research_id, scored_comments)
        q.put(
            {"stage": "scoring", "message": "Scoring complete", "progress": 95}
        )

        # Stage 4: Finalize
        storage_svc.update_research_status(
            research_id,
            "complete",
            num_threads=len(threads),
            num_comments=len(scored_comments),
        )
        storage_svc.export_csv(research_id)
        q.put(
            {"stage": "complete", "message": "Research complete!", "progress": 100}
        )

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
    comments = [ScoredComment(**c) for c in comments_data]
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


if __name__ == "__main__":
    app.run(debug=config.DEBUG, port=config.PORT)
