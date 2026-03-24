"""
Tests for SummaryService — filtering, ordering, feedback guard, prompt content.
All LLM calls are mocked.
"""

import pytest
from unittest.mock import MagicMock
from models.data_models import ScoredComment
from services.summary_service import SummaryService, SUMMARY_SYSTEM_PROMPT


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_comment(cid, relevancy=7, user_relevancy=None, score=10, body="comment body", starred=0, source="reddit"):
    return ScoredComment(
        id=cid,
        thread_id="t1",
        author="user",
        body=body,
        score=score,
        created_utc=1700000000.0,
        depth=0,
        permalink=f"https://reddit.com/c/{cid}",
        relevancy_score=relevancy,
        reasoning="reason",
        user_relevancy_score=user_relevancy,
        starred=starred,
        source=source,
    )


@pytest.fixture
def mock_llm():
    m = MagicMock()
    m.complete_text.return_value = "## Key Takeaways\n- Finding 1\n\n## Conclusion\nSome conclusion."
    return m


@pytest.fixture
def svc(mock_llm):
    return SummaryService(mock_llm)


# ── Filtering ─────────────────────────────────────────────────────────────────

def test_filters_comments_below_min_relevancy(svc, mock_llm):
    comments = [
        make_comment("c_low", relevancy=3),
        make_comment("c_ok", relevancy=4),
        make_comment("c_good", relevancy=7),
    ]
    svc.summarize("test question", comments)
    user_prompt = mock_llm.complete_text.call_args[1]["user_prompt"]
    assert "c_low" not in user_prompt
    assert "c_ok" in user_prompt
    assert "c_good" in user_prompt


def test_user_relevancy_supersedes_ai_for_filtering(svc, mock_llm):
    """User relevancy=3 should exclude comment even if AI score=9."""
    comments = [
        make_comment("c_overridden", relevancy=9, user_relevancy=3),
        make_comment("c_kept", relevancy=4),
    ]
    svc.summarize("test question", comments)
    user_prompt = mock_llm.complete_text.call_args[1]["user_prompt"]
    assert "c_overridden" not in user_prompt
    assert "c_kept" in user_prompt


def test_no_relevant_comments_returns_fallback_without_llm_call(svc, mock_llm):
    """If no comments pass the threshold, return fallback string and skip LLM."""
    comments = [make_comment("c1", relevancy=2), make_comment("c2", relevancy=3)]
    result = svc.summarize("test question", comments)
    assert "No sufficiently relevant" in result
    mock_llm.complete_text.assert_not_called()


# ── Ordering ──────────────────────────────────────────────────────────────────

def test_sorts_by_relevancy_times_score(svc, mock_llm):
    """Top comments in prompt should be ordered by relevancy × score desc."""
    comments = [
        make_comment("c_best", relevancy=9, score=100),    # 900
        make_comment("c_ok", relevancy=5, score=50),       # 250
        make_comment("c_good", relevancy=7, score=80),     # 560
    ]
    svc.summarize("test question", comments)
    user_prompt = mock_llm.complete_text.call_args[1]["user_prompt"]
    pos_best = user_prompt.index("c_best")
    pos_good = user_prompt.index("c_good")
    pos_ok = user_prompt.index("c_ok")
    assert pos_best < pos_good < pos_ok


def test_user_relevancy_boost_affects_ordering(svc, mock_llm):
    """User score=5 → effective 5.5, should rank above AI score=5."""
    comments = [
        make_comment("c_user", relevancy=5, user_relevancy=5, score=10),   # effective 5.5 * 10 = 55
        make_comment("c_ai", relevancy=5, score=10),                       # effective 5 * 10 = 50
    ]
    svc.summarize("test question", comments)
    user_prompt = mock_llm.complete_text.call_args[1]["user_prompt"]
    assert user_prompt.index("c_user") < user_prompt.index("c_ai")


def test_top_50_cap(svc, mock_llm):
    """Only the top 50 comments should be sent to the LLM."""
    comments = [make_comment(f"c{i:04d}", relevancy=7, score=i) for i in range(60)]
    svc.summarize("test question", comments)
    user_prompt = mock_llm.complete_text.call_args[1]["user_prompt"]
    # IDs are zero-padded to 4 digits, so no substring collisions (c0001 vs c0010 are distinct)
    count = sum(1 for i in range(60) if f"c{i:04d}" in user_prompt)
    assert count == 50


def test_custom_comment_count_respected(svc, mock_llm):
    """max_comments=10 with 20 relevant comments should send only 10 to LLM."""
    comments = [make_comment(f"c{i:04d}", relevancy=7, score=i) for i in range(20)]
    svc.summarize("test question", comments, max_comments=10)
    user_prompt = mock_llm.complete_text.call_args[1]["user_prompt"]
    count = sum(1 for i in range(20) if f"c{i:04d}" in user_prompt)
    assert count == 10


# ── Web quote reserved slots ─────────────────────────────────────────────────

def test_web_quotes_get_reserved_slots(svc, mock_llm):
    """Web quotes should get reserved slots even when community comments have higher sort keys."""
    community = [make_comment(f"r{i:04d}", relevancy=7, score=50) for i in range(40)]
    web = [make_comment(f"w{i:04d}", relevancy=7, score=0, source="web") for i in range(10)]
    svc.summarize("test question", community + web, max_comments=50)
    user_prompt = mock_llm.complete_text.call_args[1]["user_prompt"]
    web_count = sum(1 for i in range(10) if f"w{i:04d}" in user_prompt)
    assert web_count >= 1, "At least some web quotes should appear in the summary"


def test_web_slots_fill_with_community_when_few_web(svc, mock_llm):
    """If fewer web quotes than reserved slots, unused slots go to community."""
    community = [make_comment(f"r{i:04d}", relevancy=7, score=50) for i in range(40)]
    web = [make_comment(f"w{i:04d}", relevancy=7, score=0, source="web") for i in range(2)]
    svc.summarize("test question", community + web, max_comments=50)
    user_prompt = mock_llm.complete_text.call_args[1]["user_prompt"]
    total = sum(1 for i in range(40) if f"r{i:04d}" in user_prompt)
    total += sum(1 for i in range(2) if f"w{i:04d}" in user_prompt)
    assert total == 42  # all 40 community + 2 web


def test_no_web_comments_uses_all_community(svc, mock_llm):
    """With zero web quotes, all slots go to community comments."""
    community = [make_comment(f"r{i:04d}", relevancy=7, score=50) for i in range(60)]
    svc.summarize("test question", community, max_comments=50)
    user_prompt = mock_llm.complete_text.call_args[1]["user_prompt"]
    count = sum(1 for i in range(60) if f"r{i:04d}" in user_prompt)
    assert count == 50


# ── Feedback ──────────────────────────────────────────────────────────────────

def test_feedback_appended_to_prompt(svc, mock_llm):
    comments = [make_comment("c1", relevancy=5)]
    svc.summarize("test question", comments, user_feedback="Focus on negatives")
    user_prompt = mock_llm.complete_text.call_args[1]["user_prompt"]
    assert "Focus on negatives" in user_prompt


def test_no_feedback_no_feedback_section(svc, mock_llm):
    comments = [make_comment("c1", relevancy=5)]
    svc.summarize("test question", comments, user_feedback=None)
    user_prompt = mock_llm.complete_text.call_args[1]["user_prompt"]
    assert "User feedback" not in user_prompt


# ── System prompt content ─────────────────────────────────────────────────────

def test_system_prompt_has_injection_guard():
    assert "USER FEEDBACK POLICY" in SUMMARY_SYSTEM_PROMPT


def test_system_prompt_requires_conclusion_section():
    assert "## Conclusion" in SUMMARY_SYSTEM_PROMPT


def test_system_prompt_requires_blockquote_format():
    """Quotes must use the > blockquote format."""
    assert "> " in SUMMARY_SYSTEM_PROMPT


def test_system_prompt_instructs_citation_format():
    """Must instruct LLM to use [#comment_id] citation markers."""
    assert "[#" in SUMMARY_SYSTEM_PROMPT


# ── Thread posts preamble ─────────────────────────────────────────────────────

def test_thread_selftext_included_in_preamble(svc, mock_llm):
    comments = [make_comment("c1", relevancy=5)]
    threads = [
        {"id": "t1", "title": "Great thread", "selftext": "This is the post body", "author": "poster"},
    ]
    svc.summarize("test question", comments, threads=threads)
    user_prompt = mock_llm.complete_text.call_args[1]["user_prompt"]
    assert "This is the post body" in user_prompt


def test_empty_selftext_threads_excluded_from_preamble(svc, mock_llm):
    comments = [make_comment("c1", relevancy=5)]
    threads = [
        {"id": "t1", "title": "Thread", "selftext": "", "author": "poster"},
    ]
    svc.summarize("test question", comments, threads=threads)
    user_prompt = mock_llm.complete_text.call_args[1]["user_prompt"]
    assert "Thread Post Bodies" not in user_prompt


# ── LLM call parameters ───────────────────────────────────────────────────────

def test_summarize_uses_correct_system_prompt(svc, mock_llm):
    comments = [make_comment("c1", relevancy=5)]
    svc.summarize("test", comments)
    call_kwargs = mock_llm.complete_text.call_args[1]
    assert call_kwargs["system_prompt"] == SUMMARY_SYSTEM_PROMPT


def test_summarize_uses_moderate_temperature(svc, mock_llm):
    """Temperature should be 0.5 for creative but consistent summaries."""
    comments = [make_comment("c1", relevancy=5)]
    svc.summarize("test", comments)
    call_kwargs = mock_llm.complete_text.call_args[1]
    assert call_kwargs["temperature"] == 0.5
