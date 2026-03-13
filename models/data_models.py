from dataclasses import dataclass
from typing import ClassVar, Optional


@dataclass
class RedditThread:
    id: str
    title: str
    subreddit: str
    score: int
    num_comments: int
    url: str
    permalink: str
    selftext: str
    created_utc: float
    author: str = ""
    source: str = "reddit"

    CSV_FIELDS: ClassVar[list[str]] = [
        "id", "title", "subreddit", "score", "num_comments",
        "url", "permalink", "author", "created_utc", "source",
    ]


@dataclass
class RedditComment:
    id: str
    thread_id: str
    author: str
    body: str
    score: int
    created_utc: float
    depth: int
    permalink: str
    source: str = "reddit"


@dataclass
class ScoredComment:
    id: str
    thread_id: str
    author: str
    body: str
    score: int
    created_utc: float
    depth: int
    permalink: str
    relevancy_score: Optional[int]
    reasoning: str
    user_relevancy_score: Optional[int] = None
    starred: int = 0
    source: str = "reddit"

    CSV_FIELDS: ClassVar[list[str]] = [
        "id", "thread_id", "author", "body", "score", "relevancy_score",
        "user_relevancy_score", "reasoning", "permalink", "depth", "created_utc",
        "starred", "source",
    ]


@dataclass
class Research:
    id: str
    question: str
    status: str
    summary: Optional[str]
    num_threads: int
    num_comments: int
    created_at: str
    completed_at: Optional[str]
    settings_json: Optional[str] = None
