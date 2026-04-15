import re
from typing import List, Optional
from ddgs import DDGS
from models.data_models import RedditThread


DDG_TIME_MAP = {
    "hour": "d",   # DDG minimum is day
    "day": "d",
    "week": "w",
    "month": "m",
    "year": "y",
    "all": None,
}


EXCLUDED_DOMAINS = {
    "reddit.com", "www.reddit.com", "old.reddit.com",
    "news.ycombinator.com",
    "youtube.com", "www.youtube.com",
    "twitter.com", "x.com",
}


class WebSearchService:
    """Discovers Reddit threads via DuckDuckGo web search, and web articles."""

    def __init__(self, reddit_instance):
        """Takes a praw.Reddit instance to fetch full thread details."""
        self.reddit = reddit_instance

    def search_reddit_threads(
        self,
        queries: List[str],
        max_results: int = 15,
        subreddits: Optional[List[str]] = None,
        max_total: int = 25,
        time_filter: str = "all",
    ) -> List[RedditThread]:
        """
        Search DuckDuckGo for Reddit threads using multiple query variants.
        For each query: runs a broad site:reddit.com search and, if subreddits
        are provided, per-subreddit targeted searches for deeper coverage.
        Stops collecting once max_total unique thread IDs are found to avoid
        excessive PRAW fetches. Returns deduplicated RedditThread objects.
        Non-blocking on errors.
        """
        # verify=False works around TLS 1.3 requirement on Python 3.9 / older macOS
        ddgs = DDGS(verify=False)
        thread_ids: set = set()

        for query in queries:
            if len(thread_ids) >= max_total:
                break
            # Broad search
            thread_ids.update(self._search_ids(ddgs, f"{query} site:reddit.com", max_results, time_filter=time_filter))
            # Per-subreddit search for each known relevant subreddit
            if subreddits:
                per_sub = max(5, max_results // len(subreddits))
                for sub in subreddits:
                    if len(thread_ids) >= max_total:
                        break
                    thread_ids.update(
                        self._search_ids(ddgs, f"{query} site:reddit.com/r/{sub}", per_sub, time_filter=time_filter)
                    )

        # Hard cap before PRAW fetches to keep response time predictable
        if len(thread_ids) > max_total:
            thread_ids = set(list(thread_ids)[:max_total])

        return self._fetch_threads(thread_ids)

    def _search_ids(self, ddgs: DDGS, query: str, max_results: int, time_filter: str = "all") -> set:
        """Run a single DuckDuckGo search and return the set of Reddit thread IDs found."""
        try:
            results = ddgs.text(query, max_results=max_results, timelimit=DDG_TIME_MAP.get(time_filter))
        except Exception:
            return set()

        ids = set()
        for r in results:
            match = re.search(
                r"reddit\.com/r/\w+/comments/(\w+)", r.get("href", "")
            )
            if match:
                ids.add(match.group(1))
        return ids

    def _fetch_threads(self, thread_ids: set) -> List[RedditThread]:
        """Fetch RedditThread objects from PRAW for each thread ID."""
        threads = []
        for tid in thread_ids:
            try:
                sub = self.reddit.submission(id=tid)
                threads.append(
                    RedditThread(
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
                )
            except Exception:
                continue
        return threads

    def search_web_articles(
        self,
        queries: List[str],
        max_results: int = 8,
        page: int = 0,
        time_filter: str = "all",
    ) -> List[str]:
        """
        Search DuckDuckGo for web articles (non-Reddit, non-HN).
        Returns a deduplicated list of URLs for ArticleService to process.
        Use page parameter for pagination (0-indexed): requests more results
        from DDG and skips the first page*max_results to surface new URLs.
        """
        from urllib.parse import urlparse

        ddgs = DDGS(verify=False)
        seen_urls = set()
        urls = []
        # Request enough results to cover prior pages plus current page
        fetch_per_query = max_results * (page + 1)
        skip = page * max_results

        for query in queries:
            if len(urls) >= max_results:
                break
            try:
                results = ddgs.text(query, max_results=fetch_per_query, timelimit=DDG_TIME_MAP.get(time_filter))
            except Exception:
                continue

            # Skip results from earlier pages to surface new URLs
            results_slice = results[skip:] if len(results) > skip else []

            for r in results_slice:
                href = r.get("href", "")
                if not href:
                    continue
                domain = urlparse(href).netloc.lower()
                # Skip Reddit, HN, and social media — those have their own services
                if any(excl in domain for excl in EXCLUDED_DOMAINS):
                    continue
                if href in seen_urls:
                    continue
                seen_urls.add(href)
                urls.append(href)
                if len(urls) >= max_results:
                    break

        return urls

    def search_review_sites(
        self,
        product_name: str,
        sites: List[str] = None,
        max_per_site: int = 3,
        time_filter: str = "all",
    ) -> List[str]:
        """
        Search DuckDuckGo for product reviews on specific sites.
        Returns URLs for ArticleService to process.
        """
        if sites is None:
            sites = ["g2.com", "capterra.com", "trustpilot.com", "quora.com"]

        ddgs = DDGS(verify=False)
        seen_urls: set = set()
        urls: list = []

        for site in sites:
            try:
                results = ddgs.text(
                    f"{product_name} site:{site}",
                    max_results=max_per_site,
                    timelimit=DDG_TIME_MAP.get(time_filter),
                )
            except Exception:
                continue

            for r in results:
                href = r.get("href", "")
                if href and href not in seen_urls:
                    seen_urls.add(href)
                    urls.append(href)

        return urls
