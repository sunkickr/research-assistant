import praw
from typing import List, Optional, Generator
from models.data_models import RedditThread, RedditComment


class RedditService:
    """Handles all Reddit API interactions via PRAW."""

    def __init__(self, client_id: str, client_secret: str, user_agent: str):
        self.reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
        )

    def validate_subreddits(self, names: List[str]) -> List[str]:
        """
        Check which subreddit names actually exist and are accessible.
        Returns the validated subset.
        """
        valid = []
        for name in names:
            try:
                sub = self.reddit.subreddit(name)
                # Accessing .id forces a fetch; raises if subreddit doesn't exist
                _ = sub.id
                valid.append(name)
            except Exception:
                pass
        return valid

    def search_threads(
        self,
        query: str,
        max_threads: int = 15,
        time_filter: str = "all",
        sort: str = "relevance",
        subreddits: Optional[List[str]] = None,
    ) -> Generator[RedditThread, None, None]:
        """
        Search Reddit for threads matching the query.
        If subreddits is provided, searches within those subreddits using
        PRAW's multi-subreddit join (e.g. "databricks+dataengineering").
        Falls back to r/all if subreddits is empty or None.
        """
        if subreddits:
            target = self.reddit.subreddit("+".join(subreddits))
        else:
            target = self.reddit.subreddit("all")

        for submission in target.search(
            query, sort=sort, time_filter=time_filter, limit=max_threads
        ):
            yield RedditThread(
                id=submission.id,
                title=submission.title,
                subreddit=str(submission.subreddit),
                score=submission.score,
                num_comments=submission.num_comments,
                url=submission.url,
                permalink=f"https://reddit.com{submission.permalink}",
                selftext=(submission.selftext or "")[:2000],
                created_utc=submission.created_utc,
                author=str(submission.author) if submission.author else "[deleted]",
            )

    def collect_comments(
        self, thread_id: str, max_comments: int = 100, thread_title: str = ""
    ) -> List[RedditComment]:
        """
        Collect comments from a single thread.
        Uses replace_more(limit=0) to skip expanding collapsed comment chains.
        Collects all available comments, then returns the top max_comments by
        Reddit score so we always score the highest-quality comments.
        """
        submission = self.reddit.submission(id=thread_id)
        submission.comments.replace_more(limit=0)
        title = thread_title or submission.title or ""
        comments = []
        for comment in submission.comments.list():
            if (
                hasattr(comment, "body")
                and comment.body
                and comment.body not in ("[deleted]", "[removed]")
            ):
                context = _build_reddit_context(comment, title)
                comments.append(
                    RedditComment(
                        id=comment.id,
                        thread_id=thread_id,
                        author=str(comment.author)
                        if comment.author
                        else "[deleted]",
                        body=comment.body,
                        score=comment.score,
                        created_utc=comment.created_utc,
                        depth=comment.depth,
                        permalink=f"https://reddit.com{comment.permalink}",
                        context=context,
                    )
                )
        comments.sort(key=lambda c: c.score, reverse=True)
        return comments[:max_comments]


_CONTEXT_PARENT_MAX = 200


def _build_reddit_context(comment, thread_title: str) -> str:
    """Build concise context: thread title + parent comment snippet for replies."""
    title_part = f"Thread: {thread_title[:120]}" if thread_title else ""

    if comment.is_root:
        return title_part

    # For replies, try to get parent comment text (zero-cost dict lookup after .list())
    parent_author = ""
    parent_body = ""
    try:
        parent = comment.parent()
        if hasattr(parent, "author") and parent.author:
            parent_author = str(parent.author)
        if (
            hasattr(parent, "body")
            and parent.body
            and parent.body not in ("[deleted]", "[removed]")
        ):
            parent_body = parent.body.strip()[:_CONTEXT_PARENT_MAX]
    except Exception:
        pass

    parts = [title_part] if title_part else []
    if parent_author and parent_body:
        parts.append(f"Replying to @{parent_author}: {parent_body}")
    elif parent_body:
        parts.append(f"Replying to: {parent_body}")
    return " | ".join(parts)
