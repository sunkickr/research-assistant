"""
Tests for ScoringService — thread scoring, comment scoring, prompt content.
All LLM calls are mocked; no real API calls.
"""

import pytest
from unittest.mock import MagicMock, call
from models.data_models import RedditThread, RedditComment
from services.scoring_service import (
    ScoringService,
    ThreadBatchScoreResponse,
    ThreadScore,
    BatchScoreResponse,
    CommentScore,
    SCORING_SYSTEM_PROMPT,
    THREAD_SCORING_SYSTEM_PROMPT,
    SUBREDDIT_SYSTEM_PROMPT,
    SubredditSuggestions,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_llm():
    return MagicMock()


@pytest.fixture
def svc(mock_llm):
    return ScoringService(mock_llm, batch_size=20)


def make_thread(tid, title="Python discussion", source="reddit", selftext=""):
    return RedditThread(
        id=tid,
        title=title,
        subreddit="python",
        score=100,
        num_comments=20,
        url=f"https://reddit.com/{tid}",
        permalink=f"https://reddit.com/r/python/comments/{tid}/",
        selftext=selftext,
        created_utc=1700000000.0,
        source=source,
    )


def make_comment(cid, body="Some comment text", source="reddit"):
    return RedditComment(
        id=cid,
        thread_id="t1",
        author="user",
        body=body,
        score=10,
        created_utc=1700001000.0,
        depth=0,
        permalink=f"https://reddit.com/r/p/comments/t1//{cid}/",
        source=source,
    )


# ── score_threads ─────────────────────────────────────────────────────────────

def test_score_threads_filters_below_6(svc, mock_llm):
    mock_llm.complete.return_value = ThreadBatchScoreResponse(scores=[
        ThreadScore(thread_id="t1", relevancy_score=8),
        ThreadScore(thread_id="t2", relevancy_score=4),
        ThreadScore(thread_id="t3", relevancy_score=6),
    ])
    threads = [make_thread("t1"), make_thread("t2"), make_thread("t3")]
    result = svc.score_threads("test question", threads)
    ids = [t.id for t in result]
    assert "t1" in ids
    assert "t3" in ids
    assert "t2" not in ids


def test_score_threads_llm_failure_returns_empty(svc, mock_llm):
    """On LLM exception, return [] — not all threads (the old dangerous fallback)."""
    mock_llm.complete.side_effect = Exception("API error")
    threads = [make_thread("t1"), make_thread("t2")]
    result = svc.score_threads("test question", threads)
    assert result == []


def test_score_threads_none_relevant_returns_empty(svc, mock_llm):
    """If all threads score < 6, return [] — not all threads."""
    mock_llm.complete.return_value = ThreadBatchScoreResponse(scores=[
        ThreadScore(thread_id="t1", relevancy_score=3),
        ThreadScore(thread_id="t2", relevancy_score=2),
    ])
    threads = [make_thread("t1"), make_thread("t2")]
    result = svc.score_threads("test question", threads)
    assert result == []


def test_score_threads_empty_input(svc, mock_llm):
    result = svc.score_threads("test", [])
    assert result == []
    mock_llm.complete.assert_not_called()


def test_score_threads_prompt_includes_source_label(svc, mock_llm):
    """HN and web threads should have their source in the user_prompt."""
    mock_llm.complete.return_value = ThreadBatchScoreResponse(scores=[
        ThreadScore(thread_id="hn_1", relevancy_score=7),
        ThreadScore(thread_id="web_abc", relevancy_score=7),
    ])
    threads = [
        make_thread("hn_1", source="hackernews"),
        make_thread("web_abc", source="web"),
    ]
    svc.score_threads("test question", threads)

    call_kwargs = mock_llm.complete.call_args
    user_prompt = call_kwargs[1]["user_prompt"]
    assert "hackernews" in user_prompt or "Hacker News" in user_prompt.lower() or "hackernews" in user_prompt
    assert "web" in user_prompt


def test_score_threads_uses_correct_system_prompt(svc, mock_llm):
    mock_llm.complete.return_value = ThreadBatchScoreResponse(scores=[
        ThreadScore(thread_id="t1", relevancy_score=7),
    ])
    svc.score_threads("test", [make_thread("t1")])
    call_kwargs = mock_llm.complete.call_args[1]
    assert call_kwargs["system_prompt"] == THREAD_SCORING_SYSTEM_PROMPT


# ── score_comments (batching) ─────────────────────────────────────────────────

def test_score_comments_batches_in_groups_of_20(svc, mock_llm):
    """45 comments → 3 LLM calls (20 + 20 + 5)."""
    mock_llm.complete.return_value = BatchScoreResponse(scores=[])
    comments = [make_comment(f"c{i}") for i in range(45)]
    svc.score_comments("question", comments)
    assert mock_llm.complete.call_count == 3


def test_score_comments_custom_batch_size():
    mock_llm = MagicMock()
    mock_llm.complete.return_value = BatchScoreResponse(scores=[])
    svc = ScoringService(mock_llm, batch_size=10)
    comments = [make_comment(f"c{i}") for i in range(25)]
    svc.score_comments("question", comments)
    assert mock_llm.complete.call_count == 3  # 10 + 10 + 5


def test_score_comments_failed_batch_saves_null(svc, mock_llm):
    """On LLM exception, comments in that batch get relevancy_score=None."""
    mock_llm.complete.side_effect = Exception("timeout")
    comments = [make_comment("c1"), make_comment("c2")]
    result = svc.score_comments("question", comments)
    assert all(c.relevancy_score is None for c in result)
    assert all(c.reasoning == "Not scored — API timeout or error" for c in result)


def test_score_comments_preserves_all_fields(svc, mock_llm):
    """Scored comments must carry over the original comment's fields."""
    mock_llm.complete.return_value = BatchScoreResponse(scores=[
        CommentScore(comment_id="c1", relevancy_score=7, reasoning="Good comment"),
    ])
    comment = make_comment("c1", body="Specific body text", source="hackernews")
    result = svc.score_comments("question", [comment])
    assert len(result) == 1
    sc = result[0]
    assert sc.id == "c1"
    assert sc.body == "Specific body text"
    assert sc.source == "hackernews"
    assert sc.relevancy_score == 7
    assert sc.reasoning == "Good comment"


def test_score_comments_progress_callback(svc, mock_llm):
    """Progress callback should be called once per batch with batch results."""
    mock_llm.complete.return_value = BatchScoreResponse(scores=[])
    calls = []
    def cb(batch_num, total_batches, batch_results):
        calls.append((batch_num, total_batches))

    comments = [make_comment(f"c{i}") for i in range(25)]  # 2 batches
    svc.score_comments("question", comments, progress_callback=cb)
    assert len(calls) == 2
    assert calls[0] == (1, 2)
    assert calls[1] == (2, 2)


def test_score_comments_partial_batch_failure(svc, mock_llm):
    """First batch succeeds, second fails → only second batch gets null scores."""
    svc_small = ScoringService(mock_llm, batch_size=2)
    mock_llm.complete.side_effect = [
        BatchScoreResponse(scores=[
            CommentScore(comment_id="c0", relevancy_score=8, reasoning="good"),
            CommentScore(comment_id="c1", relevancy_score=5, reasoning="ok"),
        ]),
        Exception("timeout"),
    ]
    comments = [make_comment(f"c{i}") for i in range(4)]
    result = svc_small.score_comments("question", comments)
    assert result[0].relevancy_score == 8
    assert result[1].relevancy_score == 5
    assert result[2].relevancy_score is None
    assert result[3].relevancy_score is None


# ── Scoring prompt content (prompt-based rules) ───────────────────────────────

def test_scoring_prompt_has_named_entity_floor_rule():
    """Named entity floor must be present in the system prompt."""
    assert "MANDATORY PRE-CHECK" in SCORING_SYSTEM_PROMPT
    assert "minimum 5" in SCORING_SYSTEM_PROMPT or "at minimum 5" in SCORING_SYSTEM_PROMPT


def test_scoring_prompt_has_firsthand_experience_floor():
    assert "minimum 7" in SCORING_SYSTEM_PROMPT or "at minimum 7" in SCORING_SYSTEM_PROMPT


def test_scoring_prompt_has_score_10_calibration():
    """Score 10 should be labeled as reserved/rare."""
    assert "exactly what I was looking for" in SCORING_SYSTEM_PROMPT


def test_scoring_prompt_has_examples_for_niche_products():
    """Prompt should include Polsia or Keebo examples to calibrate niche scoring."""
    assert "Polsia" in SCORING_SYSTEM_PROMPT or "Keebo" in SCORING_SYSTEM_PROMPT


def test_thread_scoring_prompt_requires_score_per_thread():
    assert "return a score for every thread" in THREAD_SCORING_SYSTEM_PROMPT.lower() or \
           "score for every" in THREAD_SCORING_SYSTEM_PROMPT.lower()


# ── suggest_subreddits ────────────────────────────────────────────────────────

def test_suggest_subreddits_returns_names_and_queries(svc, mock_llm):
    mock_llm.complete.return_value = SubredditSuggestions(
        subreddits=["python", "learnpython"],
        search_queries=["Python best practices", "Python tips"],
    )
    names, queries = svc.suggest_subreddits("How do I learn Python?")
    assert names == ["python", "learnpython"]
    assert queries == ["Python best practices", "Python tips"]


def test_suggest_subreddits_strips_r_prefix(svc, mock_llm):
    mock_llm.complete.return_value = SubredditSuggestions(
        subreddits=["r/python", "r/learnpython"],
        search_queries=["python"],
    )
    names, _ = svc.suggest_subreddits("test")
    assert "python" in names
    assert not any(n.startswith("r/") for n in names)


def test_suggest_subreddits_llm_failure_returns_empty(svc, mock_llm):
    mock_llm.complete.side_effect = Exception("API error")
    names, queries = svc.suggest_subreddits("test")
    assert names == []
    assert queries == []
