"""
collect_research tool — run the full research pipeline for a question or product.

Mirrors the run_research_pipeline and run_product_research_pipeline logic from
app.py, but emits progress through the AgentEvent callback instead of a
queue.Queue, and accepts a ServiceContainer instead of global service singletons.

app.py is NOT modified — this is a standalone re-implementation that calls the
same underlying service layer.
"""

import re
import time
import uuid
from typing import Callable, Literal

from agent.tools import AgentEvent, ServiceContainer
from models.data_models import RedditThread

# ---------------------------------------------------------------------------
# Constants copied from app.py (kept in sync manually)
# ---------------------------------------------------------------------------

PRODUCT_CATEGORIES = {
    "issues": ["{product} issues", "{product} problems"],
    "feature_requests": ["{product} feature request", "{product} missing feature"],
    "general": ["{product} review", "{product}", "{product} use cases"],
    "competitors": ["{product} competitors", "{product} vs"],
    "benefits": ["{product} benefits", "why use {product}"],
    "alternatives": ["{product} alternatives", "switching from {product}"],
}

REVIEW_SITES = ["g2.com", "capterra.com", "trustpilot.com", "quora.com"]

_TIME_FILTER_SECONDS = {
    "hour": 3600,
    "day": 86400,
    "week": 604800,
    "month": 2592000,
    "year": 31536000,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _emit(emit_fn, content: str, progress: int, data: dict = None):
    if emit_fn:
        emit_fn(AgentEvent("tool_progress", content, {"progress": progress, **(data or {})}))


def _filter_by_time_range(threads, time_filter):
    if time_filter == "all" or time_filter not in _TIME_FILTER_SECONDS:
        return threads
    cutoff = time.time() - _TIME_FILTER_SECONDS[time_filter]
    return [t for t in threads if t.created_utc == 0 or t.created_utc >= cutoff]


def _dispatch_comment_collection(thread, max_comments, svc: ServiceContainer):
    """Dispatch comment collection based on thread source."""
    title = thread.title or ""
    if thread.source == "hackernews":
        return svc.hn_svc.collect_comments(thread.id, max_comments=max_comments, thread_title=title)
    elif thread.source in ("web", "reviews"):
        return svc.article_svc.get_cached_quotes(thread.id)
    elif thread.source == "producthunt":
        return svc.ph_svc.collect_comments(thread.id, max_comments=max_comments, thread_title=title)
    else:
        return svc.reddit_svc.collect_comments(thread.id, max_comments=max_comments, thread_title=title)


def _collect_and_score(
    research_id: str,
    question: str,
    relevant_threads: list,
    max_comments: int,
    svc: ServiceContainer,
    emit_fn: Callable,
    score_with_category: bool = False,
    product_name: str = "",
    base_pct: int = 20,
):
    """Shared comment collection + scoring stage used by both general and product pipelines."""
    all_comments = []
    n = len(relevant_threads)

    for i, thread in enumerate(relevant_threads):
        _emit(emit_fn, f"Collecting from: {thread.title[:60]}", base_pct + int(20 * (i / max(n, 1))))
        comments = _dispatch_comment_collection(thread, max_comments, svc)
        all_comments.extend(comments)
        if comments:
            svc.storage_svc.save_raw_comments(research_id, comments)

    # Apply total cap
    total_cap = svc.config.TOTAL_COMMENTS_CAP
    if len(all_comments) > total_cap:
        all_comments.sort(key=lambda c: c.score, reverse=True)
        all_comments = all_comments[:total_cap]

    _emit(emit_fn, f"Collected {len(all_comments)} comments — scoring...", base_pct + 20)

    if not all_comments:
        return []

    def on_batch(batch_num, total_batches, batch_results):
        pct = (base_pct + 20) + int(35 * (batch_num / max(total_batches, 1)))
        _emit(emit_fn, f"Scoring batch {batch_num}/{total_batches}", pct)
        svc.storage_svc.save_scored_comments(research_id, batch_results)

    if score_with_category:
        scored = svc.scoring_svc.score_comments_with_category(
            product_name, all_comments, progress_callback=on_batch
        )
    else:
        scored = svc.scoring_svc.score_comments(
            question, all_comments, progress_callback=on_batch
        )

    svc.storage_svc.save_scored_comments(research_id, scored)
    return scored


def _fetch_seed_thread(url: str, svc: ServiceContainer, question: str):
    """Fetch a single thread from a seed URL (Reddit, HN, or web article)."""
    # Reddit
    match = re.search(r"reddit\.com/r/\w+/comments/(\w+)", url)
    if not match:
        match = re.search(r"redd\.it/(\w+)", url)
    if match:
        thread_id = match.group(1)
        sub = svc.reddit_svc.reddit.submission(id=thread_id)
        return RedditThread(
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

    # Hacker News
    if "news.ycombinator.com" in url:
        hn_match = re.search(r"id=(\d+)", url)
        if hn_match:
            import requests as _requests
            numeric_id = hn_match.group(1)
            resp = _requests.get(
                f"https://hn.algolia.com/api/v1/items/{numeric_id}", timeout=15
            )
            resp.raise_for_status()
            item = resp.json()
            return RedditThread(
                id=f"hn_{numeric_id}",
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

    # Web article
    result = svc.article_svc.fetch_article(url)
    if result:
        title, body, article_date = result
        thread = svc.article_svc.make_thread(url, title, body, created_utc=article_date)
        svc.article_svc.extract_quotes(thread.id, url, title, body, question, created_utc=article_date)
        return thread

    return None


# ---------------------------------------------------------------------------
# General research pipeline
# ---------------------------------------------------------------------------

def _collect_general(
    research_id: str,
    question: str,
    sources: list,
    max_threads: int,
    max_comments: int,
    time_filter: str,
    seed_urls: list,
    svc: ServiceContainer,
    emit_fn: Callable,
) -> dict:
    try:
        relevant_threads = []

        if seed_urls:
            _emit(emit_fn, f"Fetching {len(seed_urls)} seed thread(s)...", 10)
            for url in seed_urls:
                try:
                    thread = _fetch_seed_thread(url, svc, question)
                    if thread:
                        relevant_threads.append(thread)
                except Exception:
                    continue

            if not relevant_threads:
                svc.storage_svc.update_research_status(research_id, "complete", 0, 0)
                return {"research_id": research_id, "num_threads": 0, "num_comments": 0,
                        "status": "complete", "message": "None of the provided URLs could be fetched."}

            svc.storage_svc.save_threads(research_id, relevant_threads)
            _emit(emit_fn, f"Loaded {len(relevant_threads)} thread(s) — collecting comments...", 22)

        else:
            threads = []
            seen_ids: set = set()
            search_queries = []
            validated = []

            # Generate subreddits + search queries
            if "reddit" in sources:
                _emit(emit_fn, "Discovering relevant subreddits...", 3)
                suggested, search_queries = svc.scoring_svc.suggest_subreddits(question)
                validated = svc.reddit_svc.validate_subreddits(suggested) if suggested else []
                svc.storage_svc.update_research_subreddits(research_id, validated)
                sub_display = ", ".join(f"r/{s}" for s in validated) if validated else "all of Reddit"
                _emit(emit_fn, f"Searching in: {sub_display}", 8)
            else:
                _emit(emit_fn, "Generating search queries...", 3)
                _, search_queries = svc.scoring_svc.suggest_subreddits(question)

            web_queries = search_queries if search_queries else [question]

            # Reddit
            if "reddit" in sources:
                _emit(emit_fn, "Searching Reddit...", 10)
                for t in svc.reddit_svc.search_threads(
                    question, max_threads=max_threads, time_filter=time_filter,
                    subreddits=validated or None,
                ):
                    if t.id not in seen_ids:
                        threads.append(t)
                        seen_ids.add(t.id)

                web_reddit = svc.web_search_svc.search_reddit_threads(
                    web_queries, max_results=15, subreddits=validated or None,
                    max_total=max_threads, time_filter=time_filter,
                )
                for t in web_reddit:
                    if t.id not in seen_ids:
                        threads.append(t)
                        seen_ids.add(t.id)
                _emit(emit_fn, f"Found {len(threads)} Reddit threads so far...", 14)

            # Hacker News
            if "hackernews" in sources:
                _emit(emit_fn, "Searching Hacker News...", 16)
                for story in svc.hn_svc.search_stories(web_queries, max_results=svc.config.HN_MAX_STORIES, time_filter=time_filter):
                    if story.id not in seen_ids:
                        threads.append(story)
                        seen_ids.add(story.id)

            # Web articles
            if "web" in sources:
                _emit(emit_fn, "Searching for web articles...", 18)
                for url in svc.web_search_svc.search_web_articles(web_queries, max_results=svc.config.WEB_MAX_ARTICLES, time_filter=time_filter):
                    result = svc.article_svc.fetch_article(url)
                    if result:
                        title, body, article_date = result
                        thread = svc.article_svc.make_thread(url, title, body, created_utc=article_date)
                        if thread.id not in seen_ids:
                            svc.article_svc.extract_quotes(thread.id, url, title, body, question, created_utc=article_date)
                            threads.append(thread)
                            seen_ids.add(thread.id)

            threads = _filter_by_time_range(threads, time_filter)

            if not threads:
                svc.storage_svc.update_research_status(research_id, "complete", 0, 0)
                return {"research_id": research_id, "num_threads": 0, "num_comments": 0,
                        "status": "complete", "message": "No threads found. Try a different query."}

            _emit(emit_fn, f"Found {len(threads)} threads — filtering for relevancy...", 20)
            relevant_threads = svc.scoring_svc.score_threads(question, threads)
            _emit(emit_fn, f"{len(relevant_threads)} of {len(threads)} threads are relevant", 22)
            svc.storage_svc.save_threads(research_id, relevant_threads)

        if not relevant_threads:
            svc.storage_svc.update_research_status(research_id, "complete", 0, 0)
            return {"research_id": research_id, "num_threads": 0, "num_comments": 0,
                    "status": "complete", "message": "No relevant threads passed the filter."}

        scored = _collect_and_score(
            research_id, question, relevant_threads, max_comments, svc, emit_fn,
            score_with_category=False, base_pct=22,
        )

        svc.storage_svc.update_research_status(
            research_id, "complete",
            num_threads=len(relevant_threads),
            num_comments=len(scored),
        )
        svc.storage_svc.export_csv(research_id)
        _emit(emit_fn, "Research complete!", 100)

        return {
            "research_id": research_id,
            "num_threads": len(relevant_threads),
            "num_comments": len(scored),
            "status": "complete",
        }

    except Exception as e:
        svc.storage_svc.update_research_status(research_id, "error")
        raise
    finally:
        svc.article_svc.clear_cache()


# ---------------------------------------------------------------------------
# Product research pipeline
# ---------------------------------------------------------------------------

def _collect_product(
    research_id: str,
    product_name: str,
    sources: list,
    max_threads: int,
    max_comments: int,
    time_filter: str,
    svc: ServiceContainer,
    emit_fn: Callable,
) -> dict:
    try:
        threads = []
        seen_ids: set = set()
        category_list = list(PRODUCT_CATEGORIES.items())
        total_categories = len(category_list)

        for cat_idx, (category, query_templates) in enumerate(category_list):
            queries = [t.format(product=product_name) for t in query_templates]
            pct = int(40 * (cat_idx / total_categories))
            _emit(emit_fn, f"Searching {category.replace('_', ' ')}...", pct)

            if "reddit" in sources:
                try:
                    for t in svc.reddit_svc.search_threads(
                        queries[0], max_threads=max(3, max_threads // total_categories),
                        time_filter=time_filter,
                    ):
                        if t.id not in seen_ids:
                            t.category = category
                            threads.append(t)
                            seen_ids.add(t.id)
                except Exception:
                    pass

                try:
                    for t in svc.web_search_svc.search_reddit_threads(
                        queries, max_results=5, max_total=5, time_filter=time_filter,
                    ):
                        if t.id not in seen_ids:
                            t.category = category
                            threads.append(t)
                            seen_ids.add(t.id)
                except Exception:
                    pass

            if "hackernews" in sources:
                try:
                    for t in svc.hn_svc.search_stories(queries, max_results=3, time_filter=time_filter):
                        if t.id not in seen_ids:
                            t.category = category
                            threads.append(t)
                            seen_ids.add(t.id)
                except Exception:
                    pass

            if "web" in sources:
                try:
                    for url in svc.web_search_svc.search_web_articles(queries, max_results=3, time_filter=time_filter):
                        result = svc.article_svc.fetch_article(url)
                        if result:
                            title, body, article_date = result
                            thread = svc.article_svc.make_thread(url, title, body, created_utc=article_date)
                            if thread.id not in seen_ids:
                                svc.article_svc.extract_quotes(thread.id, url, title, body, product_name, created_utc=article_date)
                                thread.category = category
                                threads.append(thread)
                                seen_ids.add(thread.id)
                except Exception:
                    pass

            if "reviews" in sources:
                try:
                    for url in svc.web_search_svc.search_review_sites(
                        product_name, sites=REVIEW_SITES, max_per_site=2, time_filter=time_filter,
                    ):
                        result = svc.article_svc.fetch_article(url)
                        if result:
                            title, body, article_date = result
                            thread = svc.article_svc.make_thread(url, title, body, created_utc=article_date)
                            if thread.id not in seen_ids:
                                svc.article_svc.extract_quotes(thread.id, url, title, body, product_name, created_utc=article_date)
                                thread.source = "reviews"
                                thread.category = category
                                threads.append(thread)
                                seen_ids.add(thread.id)
                except Exception:
                    pass

            if "producthunt" in sources and cat_idx == 0:
                try:
                    ph_posts = svc.ph_svc.search_posts(product_name, max_results=svc.config.PH_MAX_POSTS)
                    for t in ph_posts:
                        if t.id not in seen_ids:
                            t.category = "general"
                            threads.append(t)
                            seen_ids.add(t.id)
                except Exception:
                    pass

        threads = _filter_by_time_range(threads, time_filter)

        if not threads:
            svc.storage_svc.update_research_status(research_id, "complete", 0, 0)
            return {"research_id": research_id, "num_threads": 0, "num_comments": 0,
                    "status": "complete", "message": "No threads found."}

        _emit(emit_fn, f"Found {len(threads)} threads — filtering for relevancy...", 40)
        relevant_threads = svc.scoring_svc.score_threads(
            f"Product research about {product_name}", threads
        )
        _emit(emit_fn, f"{len(relevant_threads)} relevant threads — collecting comments...", 45)
        svc.storage_svc.save_threads(research_id, relevant_threads)

        scored = _collect_and_score(
            research_id, product_name, relevant_threads, max_comments, svc, emit_fn,
            score_with_category=True, product_name=product_name, base_pct=45,
        )

        svc.storage_svc.update_research_status(
            research_id, "complete",
            num_threads=len(relevant_threads),
            num_comments=len(scored),
        )
        svc.storage_svc.export_csv(research_id)
        _emit(emit_fn, "Product research complete!", 100)

        return {
            "research_id": research_id,
            "num_threads": len(relevant_threads),
            "num_comments": len(scored),
            "status": "complete",
        }

    except Exception as e:
        svc.storage_svc.update_research_status(research_id, "error")
        raise
    finally:
        svc.article_svc.clear_cache()


# ---------------------------------------------------------------------------
# Public tool function
# ---------------------------------------------------------------------------

def collect_research(
    question: str,
    research_type: Literal["general", "product"] = "general",
    product_name: str = "",
    sources: list = None,
    max_threads: int = 15,
    max_comments: int = 100,
    time_filter: str = "all",
    seed_urls: list = None,
    emit: Callable = None,
    services: ServiceContainer = None,
) -> dict:
    """
    Collect and score community research for a question or product.

    Searches Reddit, Hacker News, and the web for relevant threads, collects
    comments, and scores them for relevancy using AI. This is a long-running
    operation (typically 2-5 minutes). Progress is streamed in real time.

    Args:
        question: The research question or topic to investigate.
        research_type: "general" for open-ended questions, "product" for
                       structured product analysis across 6 categories.
        product_name: Required when research_type is "product". The product
                      or company name to research (e.g. "Notion", "Linear").
        sources: Data sources to search. Options: "reddit", "hackernews",
                 "web", "reviews" (product only), "producthunt" (product only).
                 Defaults to ["reddit", "hackernews", "web"] for general,
                 ["reddit", "hackernews", "web", "reviews", "producthunt"] for product.
        max_threads: Maximum number of threads to collect (5-25, default 15).
        max_comments: Maximum comments per thread (25-200, default 100).
        time_filter: Time range — "all", "year", "month", "week", "day".
        seed_urls: Optional list of specific URLs to fetch instead of running
                   automatic discovery. Accepts Reddit, Hacker News, or article URLs.
    """
    cfg = services.config

    # Validate and cap parameters
    max_threads = max(5, min(int(max_threads), cfg.MAX_THREADS_LIMIT))
    max_comments = max(25, min(int(max_comments), cfg.MAX_COMMENTS_PER_THREAD_LIMIT))
    if time_filter not in ("hour", "day", "week", "month", "year", "all"):
        time_filter = "all"

    if research_type == "product":
        if not product_name:
            product_name = question
        if sources is None:
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
        services.storage_svc.create_research(
            research_id, f"Product research: {product_name}", settings
        )
        return _collect_product(
            research_id, product_name, sources, max_threads, max_comments,
            time_filter, services, emit,
        )
    else:
        if sources is None:
            sources = ["reddit", "hackernews", "web"]
        research_id = uuid.uuid4().hex[:12]
        settings = {
            "research_type": "general",
            "max_threads": max_threads,
            "max_comments_per_thread": max_comments,
            "time_filter": time_filter,
            "sources": sources,
        }
        services.storage_svc.create_research(research_id, question, settings)
        return _collect_general(
            research_id, question, sources, max_threads, max_comments,
            time_filter, seed_urls or [], services, emit,
        )
