"""
retrieve_jobs tool — query stored job search data.

Read-only. Lists job search profiles, retrieves found jobs with filters,
or shows full details for a specific job.
"""

import json
import os
from typing import Literal, Optional

from agent.tools import ServiceContainer

_JOB_SEARCHES_DIR = "data/job_searches"


def _load_search(search_id: str) -> Optional[dict]:
    path = os.path.join(_JOB_SEARCHES_DIR, f"{search_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _list_all_searches() -> list[dict]:
    """Scan the job_searches directory and return summary info for each."""
    if not os.path.isdir(_JOB_SEARCHES_DIR):
        return []
    results = []
    for filename in sorted(os.listdir(_JOB_SEARCHES_DIR)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(_JOB_SEARCHES_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            results.append({
                "id": data.get("id", filename.replace(".json", "")),
                "title": data.get("profile", {}).get("title", ""),
                "job_count": len(data.get("jobs", [])),
                "created_at": data.get("created_at", "")[:10],
                "last_search": (
                    data["search_history"][-1]["timestamp"][:10]
                    if data.get("search_history")
                    else "never"
                ),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return results


def retrieve_jobs(
    action: Literal["list_searches", "get_search", "jobs", "job_detail"],
    search_id: str = "",
    job_id: str = "",
    limit: int = 20,
    min_relevancy: int = 1,
    location_filter: str = "",
    search: str = "",
    services: ServiceContainer = None,
) -> dict:
    """
    Query stored job search data.

    Args:
        action: What to retrieve — "list_searches" for all job search profiles,
                "get_search" for a single profile's details, "jobs" for found
                jobs with filters, "job_detail" for full details of one job.
        search_id: Required for "get_search", "jobs", and "job_detail" actions.
        job_id: Required for "job_detail" action.
        limit: Max number of results to return (default 20).
        min_relevancy: Minimum AI relevancy score filter for jobs (1-10).
        location_filter: Filter jobs by location keyword (e.g., "remote", "NYC").
        search: Filter jobs by keyword across title, company, location, and
                description (e.g., "remote", "python", "AI startup").
    """
    if action == "list_searches":
        searches = _list_all_searches()
        return {"searches": searches, "count": len(searches)}

    if not search_id:
        return {"error": f"search_id is required for action '{action}'"}

    data = _load_search(search_id)
    if data is None:
        return {"error": f"No job search found with id '{search_id}'"}

    if action == "get_search":
        profile = data.get("profile", {})
        return {
            "id": data["id"],
            "title": profile.get("title", ""),
            "experience_level": profile.get("experience_level", ""),
            "skills": profile.get("skills", []),
            "locations": profile.get("locations", []),
            "has_resume": bool(profile.get("resume_text")),
            "exclude_companies": profile.get("exclude_companies", []),
            "job_count": len(data.get("jobs", [])),
            "search_history": data.get("search_history", []),
            "created_at": data.get("created_at", ""),
        }

    if action == "jobs":
        jobs = data.get("jobs", [])
        location_lower = location_filter.lower() if location_filter else ""
        search_lower = search.lower() if search else ""

        filtered = []
        for j in jobs:
            score = j.get("relevancy_score")
            if score is not None and score < min_relevancy:
                continue
            if location_lower and location_lower not in (j.get("location") or "").lower():
                continue
            if search_lower:
                searchable = " ".join([
                    j.get("title", ""),
                    j.get("company", ""),
                    j.get("location", ""),
                    j.get("description", ""),
                ]).lower()
                if search_lower not in searchable:
                    continue
            filtered.append({
                "id": j["id"],
                "title": j.get("title", ""),
                "company": j.get("company", ""),
                "ats": j.get("ats", ""),
                "location": j.get("location", ""),
                "url": j.get("url", ""),
                "relevancy_score": j.get("relevancy_score"),
                "reasoning": (j.get("reasoning") or "")[:200],
                "posted_date": j.get("posted_date", ""),
                "compensation": j.get("compensation"),
                "applied": j.get("applied", False),
            })

        # Sort by relevancy descending
        filtered.sort(key=lambda x: x.get("relevancy_score") or 0, reverse=True)
        filtered = filtered[:limit]

        return {
            "jobs": filtered,
            "count": len(filtered),
            "total_jobs": len(data.get("jobs", [])),
            "filtered_by": {
                "min_relevancy": min_relevancy,
                "location": location_filter or "all",
                "search": search or "all",
            },
        }

    if action == "job_detail":
        if not job_id:
            return {"error": "job_id is required for action 'job_detail'"}
        for j in data.get("jobs", []):
            if j.get("id") == job_id:
                return {
                    "id": j["id"],
                    "title": j.get("title", ""),
                    "company": j.get("company", ""),
                    "ats": j.get("ats", ""),
                    "location": j.get("location", ""),
                    "url": j.get("url", ""),
                    "posted_date": j.get("posted_date", ""),
                    "description": j.get("description", ""),
                    "compensation": j.get("compensation"),
                    "departments": j.get("departments", []),
                    "relevancy_score": j.get("relevancy_score"),
                    "reasoning": j.get("reasoning", ""),
                    "found_at": j.get("found_at", ""),
                    "applied": j.get("applied", False),
                    "applied_at": j.get("applied_at"),
                    "applied_notes": j.get("applied_notes", ""),
                }
        return {"error": f"No job found with id '{job_id}' in search '{search_id}'"}

    return {"error": f"Unknown action: '{action}'. Use list_searches, get_search, jobs, or job_detail."}
