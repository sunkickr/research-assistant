import re
from typing import List, Optional
from ddgs import DDGS
from models.data_models import RedditThread


class WebSearchService:
    """Discovers Reddit threads via DuckDuckGo web search (site:reddit.com)."""

    def __init__(self, reddit_instance):
        """Takes a praw.Reddit instance to fetch full thread details."""
        self.reddit = reddit_instance

    def search_reddit_threads(
        self,
        queries: List[str],
        max_results: int = 15,
        subreddits: Optional[List[str]] = None,
        max_total: int = 25,
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
            thread_ids.update(self._search_ids(ddgs, f"{query} site:reddit.com", max_results))
            # Per-subreddit search for each known relevant subreddit
            if subreddits:
                per_sub = max(5, max_results // len(subreddits))
                for sub in subreddits:
                    if len(thread_ids) >= max_total:
                        break
                    thread_ids.update(
                        self._search_ids(ddgs, f"{query} site:reddit.com/r/{sub}", per_sub)
                    )

        # Hard cap before PRAW fetches to keep response time predictable
        if len(thread_ids) > max_total:
            thread_ids = set(list(thread_ids)[:max_total])

        return self._fetch_threads(thread_ids)

    def _search_ids(self, ddgs: DDGS, query: str, max_results: int) -> set:
        """Run a single DuckDuckGo search and return the set of Reddit thread IDs found."""
        try:
            results = ddgs.text(query, max_results=max_results)
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
