from dataclasses import dataclass
from typing import Optional


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
