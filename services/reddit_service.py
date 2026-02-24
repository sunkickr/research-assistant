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
                selftext=(submission.selftext or "")[:500],
                created_utc=submission.created_utc,
                author=str(submission.author) if submission.author else "[deleted]",
            )

    def collect_comments(
        self, thread_id: str, max_comments: int = 100
    ) -> List[RedditComment]:
        """
        Collect comments from a single thread.
        Uses replace_more(limit=0) to skip expanding collapsed comment chains.
        Collects all available comments, then returns the top max_comments by
        Reddit score so we always score the highest-quality comments.
        """
        submission = self.reddit.submission(id=thread_id)
        submission.comments.replace_more(limit=0)
        comments = []
        for comment in submission.comments.list():
            if (
                hasattr(comment, "body")
                and comment.body
                and comment.body not in ("[deleted]", "[removed]")
            ):
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
                    )
                )
        comments.sort(key=lambda c: c.score, reverse=True)
        return comments[:max_comments]
