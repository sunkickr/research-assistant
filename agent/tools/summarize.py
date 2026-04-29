"""
summarize tool — generate an AI summary of collected research.

Mirrors the /api/research/<id>/summarize and /api/research/<id>/summarize-product
route logic from app.py, calling the same summary_svc methods directly.
"""

from typing import Callable, Literal

from agent.tools import AgentEvent, ServiceContainer
from models.data_models import ScoredComment, RedditThread


def _comments_from_raw(comments_data: list) -> list[ScoredComment]:
    """Convert raw storage dicts to ScoredComment dataclass objects."""
    scored_fields = set(ScoredComment.__dataclass_fields__)
    return [
        ScoredComment(**{k: v for k, v in c.items() if k in scored_fields})
        for c in comments_data
    ]


def _threads_from_raw(threads_data: list) -> list[RedditThread]:
    """Convert raw storage dicts to RedditThread dataclass objects."""
    thread_fields = set(RedditThread.__dataclass_fields__)
    result = []
    for t in threads_data:
        kwargs = {k: v for k, v in t.items() if k in thread_fields}
        result.append(RedditThread(**kwargs))
    return result


def summarize(
    research_id: str,
    feedback: str = "",
    max_comments: int = 50,
    summary_type: Literal["general", "product"] = "general",
    emit: Callable = None,
    services: ServiceContainer = None,
) -> dict:
    """
    Generate an AI summary for a research session.

    Args:
        research_id: The research session to summarize.
        feedback: Optional instructions guiding the summary focus (max 500 chars).
        max_comments: How many top-scored comments to include (25-200, default 50).
        summary_type: "general" for open-ended research, "product" for product
                      research with per-category summaries.
    """
    record = services.storage_svc.get_research(research_id)
    if not record:
        return {"error": f"No research found with id '{research_id}'"}

    max_comments = max(25, min(int(max_comments), 200))
    user_feedback = (feedback or "").strip()[:500] or None

    if emit:
        emit(AgentEvent("tool_progress", "Loading comments and threads...", {"progress": 10}))

    comments_data = services.storage_svc.get_comments(research_id)
    threads_data = services.storage_svc.get_threads(research_id)
    comments = _comments_from_raw(comments_data)

    if not comments:
        return {"error": "No scored comments found. Run score_comments first."}

    if summary_type == "product":
        settings = services.storage_svc.get_settings(research_id)
        product_name = settings.get("product_name") or record["question"]

        if emit:
            emit(AgentEvent("tool_progress", f"Generating product summaries for '{product_name}'...", {"progress": 30}))

        summaries = services.summary_svc.summarize_product(
            product_name,
            comments,
            threads=threads_data,
            max_comments=max_comments,
            user_feedback=user_feedback,
        )
        services.storage_svc.save_product_summaries(research_id, summaries)

        if emit:
            emit(AgentEvent("tool_progress", "Product summaries saved.", {"progress": 100}))

        # Return abbreviated previews to keep history size manageable
        preview = {cat: text[:300] + "..." for cat, text in summaries.items()}
        return {
            "research_id": research_id,
            "summary_type": "product",
            "categories": list(summaries.keys()),
            "preview": preview,
            "message": "Full summaries saved to database.",
        }

    else:
        if emit:
            emit(AgentEvent("tool_progress", f"Generating summary from top {max_comments} comments...", {"progress": 30}))

        summary = services.summary_svc.summarize(
            record["question"],
            comments,
            user_feedback=user_feedback,
            threads=threads_data,
            max_comments=max_comments,
        )
        services.storage_svc.save_summary(research_id, summary)

        if emit:
            emit(AgentEvent("tool_progress", "Summary saved.", {"progress": 100}))

        return {
            "research_id": research_id,
            "summary_type": "general",
            "summary": summary[:800] + ("..." if len(summary) > 800 else ""),
            "message": "Full summary saved to database.",
        }
