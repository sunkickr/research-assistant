"""
Tests for StorageService — SQLite persistence, CSV export, user features.
Uses a real StorageService backed by a tmp_path SQLite DB (no mocks).
"""

import os
import pytest
from models.data_models import RedditComment, RedditThread, ScoredComment


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_thread(tid="t1", research_id="r1", source="reddit"):
    return RedditThread(
        id=tid,
        title=f"Thread {tid}",
        subreddit="python",
        score=100,
        num_comments=50,
        url=f"https://reddit.com/{tid}",
        permalink=f"https://reddit.com/r/python/comments/{tid}/",
        selftext="Some post body",
        created_utc=1700000000.0,
        author="testuser",
        source=source,
    )


def make_comment(cid="c1", tid="t1", research_id="r1", relevancy=7, source="reddit"):
    return ScoredComment(
        id=cid,
        thread_id=tid,
        author="commenter",
        body="This is a comment body",
        score=42,
        created_utc=1700001000.0,
        depth=1,
        permalink=f"https://reddit.com/r/p/comments/{tid}//{cid}/",
        relevancy_score=relevancy,
        reasoning="Relevant because it discusses the topic",
        source=source,
    )


# ── Research CRUD ─────────────────────────────────────────────────────────────

def test_create_and_get_research(real_storage):
    real_storage.create_research("r1", "What is Python?", {"max_threads": 10})
    research = real_storage.get_research("r1")
    assert research is not None
    assert research["id"] == "r1"
    assert research["question"] == "What is Python?"
    assert research["status"] == "pending"


def test_get_research_missing(real_storage):
    assert real_storage.get_research("nonexistent") is None


def test_save_and_retrieve_summary(real_storage):
    real_storage.create_research("r1", "test", {})
    real_storage.save_summary("r1", "Here is the summary text.")
    research = real_storage.get_research("r1")
    assert research["summary"] == "Here is the summary text."


def test_update_research_status(real_storage):
    real_storage.create_research("r1", "test", {})
    real_storage.update_research_status("r1", "complete", num_threads=5, num_comments=50)
    research = real_storage.get_research("r1")
    assert research["status"] == "complete"
    assert research["num_threads"] == 5
    assert research["num_comments"] == 50
    assert research["completed_at"] is not None


# ── Threads ───────────────────────────────────────────────────────────────────

def test_save_and_get_threads(real_storage):
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread("t1"), make_thread("t2")])
    threads = real_storage.get_threads("r1")
    assert len(threads) == 2
    assert {t["id"] for t in threads} == {"t1", "t2"}


def test_thread_source_field_preserved(real_storage):
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread("hn_123", source="hackernews")])
    threads = real_storage.get_threads("r1")
    assert threads[0]["source"] == "hackernews"


def test_get_existing_thread_ids(real_storage):
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread("t1"), make_thread("t2")])
    ids = real_storage.get_existing_thread_ids("r1")
    assert ids == {"t1", "t2"}


# ── Comments ──────────────────────────────────────────────────────────────────

def test_save_and_get_comments(real_storage):
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread("t1")])
    real_storage.save_scored_comments("r1", [make_comment("c1", "t1")])
    comments = real_storage.get_comments("r1")
    assert len(comments) == 1
    c = comments[0]
    assert c["id"] == "c1"
    assert c["body"] == "This is a comment body"
    assert c["relevancy_score"] == 7
    assert c["source"] == "reddit"


def test_upsert_preserves_user_relevancy(real_storage):
    """ON CONFLICT DO UPDATE must not overwrite user_relevancy_score."""
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread("t1")])
    real_storage.save_scored_comments("r1", [make_comment("c1", "t1", relevancy=5)])

    # Set a user relevancy score
    real_storage.set_user_relevancy("r1", "c1", 9)

    # Re-save the same comment with a different AI score
    real_storage.save_scored_comments("r1", [make_comment("c1", "t1", relevancy=3)])

    comments = real_storage.get_comments("r1")
    assert comments[0]["user_relevancy_score"] == 9  # preserved
    assert comments[0]["relevancy_score"] == 3       # AI score updated


def test_upsert_preserves_starred(real_storage):
    """ON CONFLICT DO UPDATE must not overwrite starred status."""
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread("t1")])
    real_storage.save_scored_comments("r1", [make_comment("c1", "t1")])

    real_storage.toggle_star("r1", "c1")  # star it

    # Re-save should not clear the star
    real_storage.save_scored_comments("r1", [make_comment("c1", "t1")])
    comments = real_storage.get_comments("r1")
    assert comments[0]["starred"] == 1


def test_comment_source_field_preserved(real_storage):
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread("hn_1", source="hackernews")])
    real_storage.save_scored_comments("r1", [make_comment("hn_c1", "hn_1", source="hackernews")])
    comments = real_storage.get_comments("r1")
    assert comments[0]["source"] == "hackernews"


def test_comments_ordered_by_effective_relevancy(real_storage):
    """Comments should be sorted by effective_relevancy DESC."""
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread("t1")])
    real_storage.save_scored_comments("r1", [
        make_comment("c_low", "t1", relevancy=3),
        make_comment("c_high", "t1", relevancy=9),
        make_comment("c_mid", "t1", relevancy=6),
    ])
    comments = real_storage.get_comments("r1")
    scores = [c["relevancy_score"] for c in comments]
    assert scores == sorted(scores, reverse=True)


# ── Delete thread ─────────────────────────────────────────────────────────────

def test_delete_thread_cascades_to_comments(real_storage):
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread("t1"), make_thread("t2")])
    real_storage.save_scored_comments("r1", [
        make_comment("c1", "t1"),
        make_comment("c2", "t2"),
    ])

    real_storage.delete_thread("r1", "t1")

    threads = real_storage.get_threads("r1")
    comments = real_storage.get_comments("r1")
    assert len(threads) == 1
    assert threads[0]["id"] == "t2"
    assert len(comments) == 1
    assert comments[0]["id"] == "c2"


def test_recalculate_counts_after_delete(real_storage):
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread("t1"), make_thread("t2")])
    real_storage.save_scored_comments("r1", [make_comment("c1", "t1"), make_comment("c2", "t2")])

    real_storage.delete_thread("r1", "t1")

    research = real_storage.get_research("r1")
    assert research["num_threads"] == 1
    assert research["num_comments"] == 1


# ── User relevancy ────────────────────────────────────────────────────────────

def test_set_user_relevancy(real_storage):
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread("t1")])
    real_storage.save_scored_comments("r1", [make_comment("c1", "t1")])

    real_storage.set_user_relevancy("r1", "c1", 8)
    comments = real_storage.get_comments("r1")
    assert comments[0]["user_relevancy_score"] == 8


def test_clear_user_relevancy(real_storage):
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread("t1")])
    real_storage.save_scored_comments("r1", [make_comment("c1", "t1")])

    real_storage.set_user_relevancy("r1", "c1", 8)
    real_storage.set_user_relevancy("r1", "c1", None)
    comments = real_storage.get_comments("r1")
    assert comments[0]["user_relevancy_score"] is None


# ── Star ──────────────────────────────────────────────────────────────────────

def test_toggle_star_on_and_off(real_storage):
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread("t1")])
    real_storage.save_scored_comments("r1", [make_comment("c1", "t1")])

    result = real_storage.toggle_star("r1", "c1")
    assert result == 1
    result = real_storage.toggle_star("r1", "c1")
    assert result == 0


def test_toggle_star_missing_comment(real_storage):
    real_storage.create_research("r1", "test", {})
    result = real_storage.toggle_star("r1", "nonexistent")
    assert result == 0


# ── Archive / Restore / Delete ────────────────────────────────────────────────

def test_archive_removes_from_history(real_storage):
    real_storage.create_research("r1", "test", {})
    history_before = real_storage.get_history()
    assert any(r["id"] == "r1" for r in history_before)

    real_storage.archive_research("r1")
    history_after = real_storage.get_history()
    assert not any(r["id"] == "r1" for r in history_after)


def test_archived_appears_in_archived_list(real_storage):
    real_storage.create_research("r1", "test", {})
    real_storage.archive_research("r1")
    archived = real_storage.get_archived()
    assert any(r["id"] == "r1" for r in archived)


def test_unarchive_restores_to_history(real_storage):
    real_storage.create_research("r1", "test", {})
    real_storage.archive_research("r1")
    real_storage.unarchive_research("r1")

    history = real_storage.get_history()
    assert any(r["id"] == "r1" for r in history)
    archived = real_storage.get_archived()
    assert not any(r["id"] == "r1" for r in archived)


def test_permanent_delete(real_storage):
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread("t1")])
    real_storage.save_scored_comments("r1", [make_comment("c1", "t1")])

    real_storage.delete_research("r1")

    assert real_storage.get_research("r1") is None
    assert real_storage.get_threads("r1") == []
    assert real_storage.get_comments("r1") == []


# ── Settings / sorts_tried persistence ───────────────────────────────────────

def test_sorts_tried_persistence(real_storage):
    real_storage.create_research("r1", "test", {"sources": ["reddit"]})
    real_storage.update_settings("r1", {"sorts_tried": ["top", "hn_0"]})
    settings = real_storage.get_settings("r1")
    assert settings["sorts_tried"] == ["top", "hn_0"]
    # Original settings preserved
    assert settings["sources"] == ["reddit"]


def test_update_settings_merges(real_storage):
    real_storage.create_research("r1", "test", {"max_threads": 10})
    real_storage.update_settings("r1", {"sorts_tried": ["top"]})
    settings = real_storage.get_settings("r1")
    assert settings["max_threads"] == 10
    assert settings["sorts_tried"] == ["top"]


def test_get_settings_empty_returns_dict(real_storage):
    real_storage.create_research("r1", "test", {})
    # Manually blank the settings
    import sqlite3, json
    with sqlite3.connect(real_storage.db_path) as conn:
        conn.execute("UPDATE researches SET settings_json=NULL WHERE id='r1'")
    settings = real_storage.get_settings("r1")
    assert settings == {}


# ── CSV export ────────────────────────────────────────────────────────────────

def test_export_csv_creates_file(real_storage):
    real_storage.create_research("r1", "What is Python?", {})
    real_storage.save_threads("r1", [make_thread("t1")])
    real_storage.save_scored_comments("r1", [make_comment("c1", "t1")])

    filepath = real_storage.export_csv("r1")
    assert os.path.exists(filepath)


def test_export_csv_contains_correct_headers(real_storage):
    real_storage.create_research("r1", "test", {})
    filepath = real_storage.export_csv("r1")

    with open(filepath) as f:
        header = f.readline()
    for col in ["id", "body", "relevancy_score", "source", "starred"]:
        assert col in header


# ── Raw comments & unscored ──────────────────────────────────────────────────

def _make_raw_comment(cid="c1", tid="t1"):
    return RedditComment(
        id=cid, thread_id=tid, author="author", body="body text",
        score=10, created_utc=1700000000.0, depth=0, permalink="http://example.com",
        source="reddit",
    )


def test_save_raw_comments(real_storage):
    """Raw comments should be stored with relevancy_score=NULL."""
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread()])
    real_storage.save_raw_comments("r1", [_make_raw_comment("c1"), _make_raw_comment("c2")])
    comments = real_storage.get_comments("r1")
    assert len(comments) == 2
    assert all(c["relevancy_score"] is None for c in comments)


def test_save_raw_then_scored_preserves_user_fields(real_storage):
    """Saving raw, setting user_relevancy, then re-saving scored should keep user_relevancy."""
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread()])
    real_storage.save_raw_comments("r1", [_make_raw_comment("c1")])

    # User sets relevancy
    real_storage.set_user_relevancy("r1", "c1", 8)

    # Now score arrives (upsert)
    real_storage.save_scored_comments("r1", [make_comment("c1", relevancy=6)])

    comments = real_storage.get_comments("r1")
    c = comments[0]
    assert c["relevancy_score"] == 6
    assert c["user_relevancy_score"] == 8  # preserved


def test_get_unscored_count(real_storage):
    """Should count only comments with relevancy_score IS NULL."""
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread()])
    real_storage.save_raw_comments("r1", [_make_raw_comment("c1"), _make_raw_comment("c2")])
    real_storage.save_scored_comments("r1", [make_comment("c1", relevancy=7)])
    assert real_storage.get_unscored_count("r1") == 1


def test_get_unscored_comments_returns_reddit_comment_objects(real_storage):
    """Should return RedditComment objects for re-scoring."""
    real_storage.create_research("r1", "test", {})
    real_storage.save_threads("r1", [make_thread()])
    real_storage.save_raw_comments("r1", [_make_raw_comment("c1")])
    unscored = real_storage.get_unscored_comments("r1")
    assert len(unscored) == 1
    assert isinstance(unscored[0], RedditComment)
    assert unscored[0].id == "c1"
