import praw
from typing import List, Generator
from models.data_models import RedditThread, RedditComment


class RedditService:
    """Handles all Reddit API interactions via PRAW."""

    def __init__(self, client_id: str, client_secret: str, user_agent: str):
        self.reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
        )

    def search_threads(
        self,
        query: str,
        max_threads: int = 15,
        time_filter: str = "all",
        sort: str = "relevance",
    ) -> Generator[RedditThread, None, None]:
        """
        Search Reddit for threads matching the query.
        Yields RedditThread objects as they are found.
        """
        for submission in self.reddit.subreddit("all").search(
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
        """
        submission = self.reddit.submission(id=thread_id)
        submission.comments.replace_more(limit=0)
        comments = []
        for comment in submission.comments.list()[:max_comments]:
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
        return comments
