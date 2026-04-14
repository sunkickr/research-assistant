import re
import requests
from typing import List
from models.data_models import RedditThread, RedditComment


HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
HN_ITEM_URL = "https://hn.algolia.com/api/v1/items/{}"


class HNService:
    """Discovers Hacker News stories and collects comments via the Algolia API."""

    def search_stories(
        self, queries: List[str], max_results: int = 10, page: int = 0
    ) -> List[RedditThread]:
        """
        Search HN stories via Algolia for each query variant.
        Returns deduplicated RedditThread objects with source='hackernews'.
        Use page parameter for pagination (0-indexed).
        """
        seen_ids = set()
        threads = []

        for query in queries:
            try:
                resp = requests.get(
                    HN_SEARCH_URL,
                    params={
                        "query": query,
                        "tags": "story",
                        "hitsPerPage": max_results,
                        "page": page,
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                hits = resp.json().get("hits", [])
            except Exception:
                continue

            for hit in hits:
                story_id = hit.get("objectID", "")
                if not story_id or story_id in seen_ids:
                    continue
                seen_ids.add(story_id)

                threads.append(
                    RedditThread(
                        id=f"hn_{story_id}",
                        title=hit.get("title", ""),
                        subreddit="Hacker News",
                        score=hit.get("points") or 0,
                        num_comments=hit.get("num_comments") or 0,
                        url=hit.get("url") or f"https://news.ycombinator.com/item?id={story_id}",
                        permalink=f"https://news.ycombinator.com/item?id={story_id}",
                        selftext=(hit.get("story_text") or "")[:2000],
                        created_utc=0,  # Will be set below if available
                        author=hit.get("author") or "",
                        source="hackernews",
                    )
                )

                # Parse created_at timestamp if available
                created_at = hit.get("created_at_i")
                if created_at:
                    threads[-1].created_utc = float(created_at)

            if len(threads) >= max_results:
                break

        return threads[:max_results]

    def collect_comments(
        self, thread_id: str, max_comments: int = 100, thread_title: str = ""
    ) -> List[RedditComment]:
        """
        Fetch the full comment tree for an HN story.
        thread_id should be 'hn_{objectID}' format.
        Returns a flat list of RedditComment objects sorted by points desc.
        """
        # Strip the hn_ prefix to get the numeric ID
        numeric_id = thread_id.replace("hn_", "")

        try:
            resp = requests.get(
                HN_ITEM_URL.format(numeric_id),
                timeout=15,
            )
            resp.raise_for_status()
            item = resp.json()
        except Exception:
            return []

        title = thread_title or item.get("title") or ""
        comments = self._flatten_comments(
            item.get("children", []), thread_id, depth=0,
            thread_title=title, parent_text="", parent_author="",
        )

        # Sort by points descending, then cap
        comments.sort(key=lambda c: c.score, reverse=True)
        return comments[:max_comments]

    def _flatten_comments(
        self, children: list, thread_id: str, depth: int = 0,
        thread_title: str = "", parent_text: str = "", parent_author: str = "",
    ) -> List[RedditComment]:
        """Recursively flatten the HN comment tree into a flat list."""
        comments = []
        for child in children:
            if child.get("type") != "comment":
                continue
            text = child.get("text") or ""
            if not text or text == "[deleted]":
                continue

            comment_id = child.get("id", "")
            created_at = child.get("created_at_i") or 0
            child_author = child.get("author") or "[deleted]"

            context = _build_hn_context(depth, thread_title, parent_author, parent_text)

            comments.append(
                RedditComment(
                    id=f"hn_{comment_id}",
                    thread_id=thread_id,
                    author=child_author,
                    body=text,
                    score=child.get("points") or 0,
                    created_utc=float(created_at),
                    depth=depth,
                    permalink=f"https://news.ycombinator.com/item?id={comment_id}",
                    source="hackernews",
                    context=context,
                )
            )

            # Recurse into child comments, passing current comment as parent
            clean_text = _strip_html(text)[:200]
            comments.extend(
                self._flatten_comments(
                    child.get("children", []), thread_id, depth + 1,
                    thread_title=thread_title,
                    parent_text=clean_text,
                    parent_author=child_author,
                )
            )

        return comments


def _strip_html(text: str) -> str:
    """Strip basic HTML tags from HN comment text."""
    return re.sub(r"<[^>]+>", "", text).strip()


def _build_hn_context(
    depth: int, thread_title: str, parent_author: str, parent_text: str,
) -> str:
    """Build concise context for an HN comment."""
    title_part = f"Thread: {thread_title[:120]}" if thread_title else ""
    parts = [title_part] if title_part else []
    if depth > 0 and parent_text:
        if parent_author and parent_author != "[deleted]":
            parts.append(f"Replying to @{parent_author}: {parent_text}")
        else:
            parts.append(f"Replying to: {parent_text}")
    return " | ".join(parts)
