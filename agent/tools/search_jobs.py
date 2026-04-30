"""
search_jobs tool — discover and score new job postings.

Long-running tool that queries ATS public APIs for recently-posted jobs,
filters by recency, and uses the LLM to score relevancy against the user's
job search profile.
"""

import json
import os
from datetime import datetime, timezone
from typing import Callable

from agent.tools import AgentEvent, ServiceContainer

_JOB_SEARCHES_DIR = "data/job_searches"

_DEFAULT_ATS_PLATFORMS = ["greenhouse", "lever", "ashby"]


def _emit(emit_fn, content: str, progress: int):
    if emit_fn:
        emit_fn(AgentEvent("tool_progress", content, {"progress": progress}))


def _load_search(search_id: str) -> tuple:
    path = os.path.join(_JOB_SEARCHES_DIR, f"{search_id}.json")
    if not os.path.exists(path):
        return None, path
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f), path


def search_jobs(
    search_id: str,
    max_age_hours: int = 48,
    ats_platforms: list = None,
    max_companies: int = 50,
    min_relevancy: int = 6,
    include_companies: list = None,
    exclude_companies: list = None,
    emit: Callable = None,
    services: ServiceContainer = None,
) -> dict:
    """
    Search for new job postings matching a job search profile.

    Queries Greenhouse, Lever, and Ashby public career page APIs for
    companies in the configured list, filters to recently-posted roles,
    and uses AI to score relevancy. Long-running (1-5 minutes).

    Args:
        search_id: The job search profile to match against.
        max_age_hours: Only return jobs posted within this many hours (default 48).
        ats_platforms: Platforms to search — "greenhouse", "lever", "ashby".
                       Defaults to all three.
        max_companies: Maximum companies to check per platform (default 50).
        min_relevancy: Minimum AI relevancy score to include (1-10, default 6).
        include_companies: Extra company slugs to search in addition to the
                           bundled list (e.g., ["stripe", "linear"]).
        exclude_companies: Company slugs to skip this search. Merged with
                           any exclusions saved in the profile.
    """
    if not services or not services.job_search_svc:
        return {"error": "Job search service is not configured."}

    data, path = _load_search(search_id)
    if data is None:
        return {"error": f"No job search found with id '{search_id}'"}

    profile = data.get("profile", {})
    if not profile.get("title"):
        return {"error": "Job search profile has no title. Update it first."}

    platforms = ats_platforms or _DEFAULT_ATS_PLATFORMS
    max_companies = max(5, min(int(max_companies), services.config.JOB_SEARCH_MAX_COMPANIES))
    max_age_hours = max(1, int(max_age_hours))

    svc = services.job_search_svc

    # Build exclusion set: profile exclusions + per-search exclusions
    profile_excludes = set(s.lower() for s in profile.get("exclude_companies", []))
    search_excludes = set(s.lower() for s in (exclude_companies or []))
    all_excludes = profile_excludes | search_excludes

    # Build inclusion list for ad-hoc companies
    extra_includes = [s.lower() for s in (include_companies or [])]

    # Collect existing job URLs for deduplication
    existing_urls = {j.get("url") for j in data.get("jobs", []) if j.get("url")}

    # Stage 1: Fetch jobs from all platforms
    all_candidates = []
    platform_pct = 60 // len(platforms)  # split 60% of progress across platforms

    for i, ats in enumerate(platforms):
        base_pct = i * platform_pct
        company_count = svc.get_company_count(ats)
        actual_max = min(max_companies, company_count)
        _emit(emit, f"Searching {ats.title()} ({actual_max} companies)...", base_pct + 2)

        def fetch_progress(completed, total, _ats=ats, _base=base_pct):
            pct = _base + int(platform_pct * (completed / max(total, 1)))
            _emit(emit, f"Checked {completed}/{total} {_ats.title()} companies...", pct)

        raw_jobs = svc.fetch_all_jobs(
            ats, max_companies=max_companies,
            exclude_slugs=all_excludes, include_slugs=extra_includes,
            progress_callback=fetch_progress,
        )
        _emit(emit, f"Found {len(raw_jobs)} total postings on {ats.title()}", base_pct + platform_pct)
        all_candidates.extend(raw_jobs)

    if not all_candidates:
        return {
            "search_id": search_id,
            "new_jobs_found": 0,
            "total_companies_checked": max_companies * len(platforms),
            "total_jobs_scanned": 0,
            "status": "complete",
            "message": "No job postings found across any platform.",
        }

    total_scanned = len(all_candidates)

    # Stage 2: Filter by recency
    _emit(emit, f"Filtering {len(all_candidates)} jobs by recency ({max_age_hours}h)...", 62)
    recent_jobs = svc.filter_recent(all_candidates, max_age_hours=max_age_hours)
    _emit(emit, f"{len(recent_jobs)} jobs posted in the last {max_age_hours} hours", 65)

    if not recent_jobs:
        # Record the search attempt even with no results
        _record_search_history(data, platforms, max_companies * len(platforms), total_scanned, 0)
        _save_search(data, path)
        return {
            "search_id": search_id,
            "new_jobs_found": 0,
            "total_companies_checked": max_companies * len(platforms),
            "total_jobs_scanned": total_scanned,
            "recent_jobs_found": 0,
            "status": "complete",
            "message": f"No jobs posted in the last {max_age_hours} hours. Try increasing max_age_hours.",
        }

    # Stage 3: Deduplicate against already-found jobs
    new_jobs = [j for j in recent_jobs if j.get("url") not in existing_urls]
    _emit(emit, f"{len(new_jobs)} new jobs after deduplication", 67)

    if not new_jobs:
        _record_search_history(data, platforms, max_companies * len(platforms), total_scanned, 0)
        _save_search(data, path)
        return {
            "search_id": search_id,
            "new_jobs_found": 0,
            "total_jobs_scanned": total_scanned,
            "recent_jobs_found": len(recent_jobs),
            "status": "complete",
            "message": "All recent jobs were already found in a previous search.",
        }

    # Stage 4: Score via LLM
    _emit(emit, f"Scoring {len(new_jobs)} jobs for relevancy...", 70)

    def score_progress(batch_num, total_batches):
        pct = 70 + int(25 * (batch_num / max(total_batches, 1)))
        _emit(emit, f"Scoring batch {batch_num}/{total_batches}", pct)

    scored_jobs = svc.score_jobs(new_jobs, profile, progress_callback=score_progress)

    # Stage 5: Filter by minimum relevancy
    matched = [j for j in scored_jobs if (j.get("relevancy_score") or 0) >= min_relevancy]
    matched.sort(key=lambda x: x.get("relevancy_score") or 0, reverse=True)

    # Add metadata and append to existing jobs
    now = datetime.now(timezone.utc).isoformat()
    for j in matched:
        j["found_at"] = now
        j["applied"] = False
        j["applied_at"] = None
        j["applied_notes"] = ""

    data["jobs"].extend(matched)
    _record_search_history(data, platforms, max_companies * len(platforms), total_scanned, len(matched))
    data["updated_at"] = now
    _save_search(data, path)

    _emit(emit, f"Found {len(matched)} relevant jobs!", 100)

    # Return summary + top 5 for the LLM to present
    top_5 = [
        {
            "title": j.get("title", ""),
            "company": j.get("company", ""),
            "ats": j.get("ats", ""),
            "location": j.get("location", ""),
            "url": j.get("url", ""),
            "relevancy_score": j.get("relevancy_score"),
            "reasoning": (j.get("reasoning") or "")[:150],
            "posted_date": j.get("posted_date", ""),
        }
        for j in matched[:5]
    ]

    return {
        "search_id": search_id,
        "new_jobs_found": len(matched),
        "total_companies_checked": max_companies * len(platforms),
        "total_jobs_scanned": total_scanned,
        "recent_jobs_found": len(recent_jobs),
        "scored_count": len(scored_jobs),
        "status": "complete",
        "top_jobs": top_5,
        "message": f"Found {len(matched)} jobs scoring {min_relevancy}+ out of {len(recent_jobs)} recent postings. Use retrieve_jobs to see all results.",
    }


def _record_search_history(data: dict, platforms: list, companies_checked: int, jobs_scanned: int, jobs_matched: int):
    data.setdefault("search_history", []).append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ats_platforms": platforms,
        "companies_checked": companies_checked,
        "jobs_scanned": jobs_scanned,
        "jobs_matched": jobs_matched,
    })


def _save_search(data: dict, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
