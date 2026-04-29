"""
retrieve_research tool — query stored research data.

Read-only. Safe to call at any time to list past research, inspect threads,
or pull top comments for a given research session.
"""

from typing import Literal

from agent.tools import ServiceContainer


def retrieve_research(
    action: Literal["list", "get", "comments", "threads"],
    research_id: str = "",
    limit: int = 20,
    search: str = "",
    min_relevancy: int = 1,
    filter_starred: bool = False,
    category: str = "",
    services: ServiceContainer = None,
) -> dict:
    """
    Query stored research data from the database.

    Args:
        action: What to retrieve — "list" for all research history, "get" for a
                single research record, "comments" for scored comments, "threads"
                for collected threads.
        research_id: Required for actions "get", "comments", and "threads".
        limit: Max number of results to return (applies to list, comments, and threads).
        search: Filter research sessions by keyword in the question (applies to "list" action only).
                Use this when looking for a specific research topic.
        min_relevancy: Minimum AI relevancy score filter for comments (1-10).
        filter_starred: When True, return only starred comments.
        category: Filter comments or threads by category (e.g. "issues",
                  "feature_requests"). Leave empty to return all categories.
    """
    if action == "list":
        history = services.storage_svc.get_history()
        # Filter by search keyword if provided
        if search:
            search_lower = search.lower()
            history = [r for r in history if search_lower in r["question"].lower()]
        # Apply limit
        history = history[:limit]
        return {
            "research_sessions": [
                {
                    "id": r["id"],
                    "question": r["question"],
                    "status": r["status"],
                    "num_threads": r["num_threads"],
                    "num_comments": r["num_comments"],
                    "created_at": r["created_at"][:10],
                }
                for r in history
            ],
            "count": len(history),
            "search": search or None,
        }

    if not research_id:
        return {"error": f"research_id is required for action '{action}'"}

    if action == "get":
        record = services.storage_svc.get_research(research_id)
        if not record:
            return {"error": f"No research found with id '{research_id}'"}
        settings = services.storage_svc.get_settings(research_id)
        return {
            "id": record["id"],
            "question": record["question"],
            "status": record["status"],
            "research_type": record.get("research_type", "general"),
            "num_threads": record["num_threads"],
            "num_comments": record["num_comments"],
            "created_at": record["created_at"],
            "completed_at": record.get("completed_at"),
            "has_summary": bool(record.get("summary")),
            "sources": settings.get("sources", []),
            "subreddits": settings.get("subreddits", []),
        }

    if action == "threads":
        threads_raw = services.storage_svc.get_threads(research_id)
        if not threads_raw:
            return {"threads": [], "count": 0}
        result = []
        for t in threads_raw[:limit]:
            if category and t.get("category") != category:
                continue
            result.append({
                "id": t["id"],
                "title": t["title"],
                "source": t.get("source", "reddit"),
                "subreddit": t.get("subreddit", ""),
                "score": t.get("score", 0),
                "num_comments": t.get("num_comments", 0),
                "url": t.get("url", ""),
                "category": t.get("category"),
                "selftext_snippet": (t.get("selftext") or "")[:100],
            })
        return {"threads": result, "count": len(result)}

    if action == "comments":
        comments_raw = services.storage_svc.get_comments(research_id)
        if not comments_raw:
            return {"comments": [], "count": 0}
        result = []
        for c in comments_raw:
            relevancy = c.get("user_relevancy_score") or c.get("relevancy_score") or 0
            if relevancy < min_relevancy:
                continue
            if filter_starred and not c.get("starred"):
                continue
            if category and c.get("category") != category:
                continue
            result.append({
                "id": c["id"],
                "author": c.get("author", ""),
                "body": (c.get("body") or "")[:400],
                "relevancy_score": c.get("relevancy_score"),
                "user_relevancy_score": c.get("user_relevancy_score"),
                "reasoning": (c.get("reasoning") or "")[:200],
                "source": c.get("source", "reddit"),
                "category": c.get("category"),
                "score": c.get("score", 0),
                "permalink": c.get("permalink", ""),
                "starred": bool(c.get("starred")),
            })
            if len(result) >= limit:
                break
        return {"comments": result, "count": len(result), "filtered_by": {
            "min_relevancy": min_relevancy,
            "filter_starred": filter_starred,
            "category": category or "all",
        }}

    return {"error": f"Unknown action: '{action}'. Use list, get, comments, or threads."}
