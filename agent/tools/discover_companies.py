"""
discover_companies tool — find company career pages via web search.

Uses DuckDuckGo site-specific searches to discover company slugs on
Greenhouse, Lever, and Ashby that aren't in the bundled lists.
"""

import re
from typing import Callable
from urllib.parse import urlparse

from agent.tools import AgentEvent, ServiceContainer

_ATS_SEARCH_PATTERNS = {
    "greenhouse": {
        "site": "boards.greenhouse.io",
        "slug_pattern": r"boards\.greenhouse\.io/([a-zA-Z0-9_-]+)",
    },
    "lever": {
        "site": "jobs.lever.co",
        "slug_pattern": r"jobs\.lever\.co/([a-zA-Z0-9_-]+)",
    },
    "ashby": {
        "site": "jobs.ashbyhq.com",
        "slug_pattern": r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)",
    },
}

_DEFAULT_PLATFORMS = ["greenhouse", "lever", "ashby"]


def _emit(emit_fn, content: str, progress: int):
    if emit_fn:
        emit_fn(AgentEvent("tool_progress", content, {"progress": progress}))


def discover_companies(
    query: str,
    ats_platforms: list = None,
    max_results: int = 20,
    save_to_lists: bool = False,
    emit: Callable = None,
    services: ServiceContainer = None,
) -> dict:
    """
    Discover company career pages on ATS platforms using web search.

    Searches DuckDuckGo for company job boards on Greenhouse, Lever, and
    Ashby that match a query. Useful for finding companies not in the
    bundled company lists.

    Args:
        query: Search terms to find companies (e.g., "AI startups",
               "fintech NYC", "developer tools").
        ats_platforms: Platforms to search — "greenhouse", "lever", "ashby".
                       Defaults to all three.
        max_results: Maximum company slugs to discover per platform (default 20).
        save_to_lists: If true, append new slugs to the bundled company
                       lists for future searches.
    """
    if not services or not services.job_search_svc:
        return {"error": "Job search service is not configured."}

    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return {"error": "duckduckgo_search package is not installed."}

    platforms = ats_platforms or _DEFAULT_PLATFORMS
    ddgs = DDGS()
    results_by_ats = {}
    total_new = 0

    for i, ats in enumerate(platforms):
        pattern_info = _ATS_SEARCH_PATTERNS.get(ats)
        if not pattern_info:
            continue

        pct = int(80 * (i / len(platforms)))
        _emit(emit, f"Searching for {ats.title()} company boards...", pct)

        site = pattern_info["site"]
        slug_re = pattern_info["slug_pattern"]

        # Run DDG search with site: filter
        search_query = f'{query} site:{site}'
        discovered = set()

        try:
            ddg_results = ddgs.text(search_query, max_results=max_results * 2)
            for r in ddg_results:
                href = r.get("href", "")
                match = re.search(slug_re, href)
                if match:
                    slug = match.group(1).lower()
                    # Skip generic pages
                    if slug not in ("embed", "api", "docs", "help", "about"):
                        discovered.add(slug)
                if len(discovered) >= max_results:
                    break
        except Exception:
            pass

        # Identify which are new (not already in bundled lists)
        existing = set(services.job_search_svc.companies.get(ats, []))
        new_slugs = [s for s in discovered if s not in existing]
        all_slugs = list(discovered)

        results_by_ats[ats] = {
            "discovered": all_slugs,
            "new_slugs": new_slugs,
            "already_known": len(all_slugs) - len(new_slugs),
        }

        # Optionally save new slugs to the bundled lists
        if save_to_lists and new_slugs:
            added = services.job_search_svc.add_companies(ats, new_slugs)
            results_by_ats[ats]["added_to_list"] = added
            total_new += added
        else:
            total_new += len(new_slugs)

    _emit(emit, "Discovery complete!", 100)

    return {
        "query": query,
        "results": results_by_ats,
        "total_new_companies": total_new,
        "saved_to_lists": save_to_lists,
    }
