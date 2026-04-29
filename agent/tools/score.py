"""
score_comments tool — score any unscored comments for a research session.

Only needed when the user reports unscored comments or after a collection run
that was interrupted before scoring completed.
"""

from typing import Callable

from agent.tools import AgentEvent, ServiceContainer


def score_comments(
    research_id: str,
    emit: Callable = None,
    services: ServiceContainer = None,
) -> dict:
    """
    Score all unscored comments for a research session.

    Args:
        research_id: The research session to score comments for.
    """
    record = services.storage_svc.get_research(research_id)
    if not record:
        return {"error": f"No research found with id '{research_id}'"}

    unscored = services.storage_svc.get_unscored_comments(research_id)
    if not unscored:
        count = services.storage_svc.get_unscored_count(research_id)
        return {"message": "No unscored comments found.", "research_id": research_id, "unscored_count": count}

    total = len(unscored)
    if emit:
        emit(AgentEvent("tool_progress", f"Scoring {total} unscored comments...", {"progress": 5}))

    def progress_callback(batch_num, total_batches, batch_results):
        if emit:
            pct = int(10 + (batch_num / total_batches) * 85)
            emit(AgentEvent(
                "tool_progress",
                f"Scoring batch {batch_num}/{total_batches}",
                {"progress": pct},
            ))
        services.storage_svc.save_scored_comments(research_id, batch_results)

    scored = services.scoring_svc.score_comments(
        record["question"], unscored, progress_callback=progress_callback
    )

    services.storage_svc.recalculate_counts(research_id)

    if emit:
        emit(AgentEvent("tool_progress", "Scoring complete.", {"progress": 100}))

    return {
        "research_id": research_id,
        "scored_count": len(scored),
        "total_was_unscored": total,
    }
