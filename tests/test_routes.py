"""
Flask route integration tests.
All external services are mocked via the `client` fixture from conftest.py.
"""

import json
import pytest
from unittest.mock import MagicMock


# ── POST /api/research ────────────────────────────────────────────────────────

class TestStartResearch:

    def test_missing_question_returns_400(self, client):
        resp = client.post("/api/research", json={})
        assert resp.status_code == 400
        assert "Question is required" in resp.get_data(as_text=True)

    def test_empty_question_returns_400(self, client):
        resp = client.post("/api/research", json={"question": "   "})
        assert resp.status_code == 400

    def test_returns_research_id(self, client):
        resp = client.post("/api/research", json={"question": "What is Python?"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "research_id" in data
        assert len(data["research_id"]) > 0

    def test_max_threads_clamped_to_limit(self, client, mock_storage, monkeypatch):
        import app as app_module
        from config import Config
        monkeypatch.setattr(app_module.config, "MAX_THREADS_LIMIT", 25)
        monkeypatch.setattr(app_module.config, "DEFAULT_MAX_THREADS", 15)

        client.post("/api/research", json={
            "question": "test",
            "max_threads": 999,
        })

        call_args = mock_storage.create_research.call_args
        settings = call_args[0][2]
        assert settings["max_threads"] <= 25

    def test_max_comments_clamped_to_limit(self, client, mock_storage, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module.config, "MAX_COMMENTS_PER_THREAD_LIMIT", 200)

        client.post("/api/research", json={
            "question": "test",
            "max_comments_per_thread": 9999,
        })

        call_args = mock_storage.create_research.call_args
        settings = call_args[0][2]
        assert settings["max_comments_per_thread"] <= 200

    def test_invalid_time_filter_defaults_to_all(self, client, mock_storage):
        client.post("/api/research", json={"question": "test", "time_filter": "badvalue"})
        call_args = mock_storage.create_research.call_args
        settings = call_args[0][2]
        assert settings["time_filter"] == "all"

    def test_valid_time_filter_accepted(self, client, mock_storage):
        for tf in ("hour", "day", "week", "month", "year", "all"):
            mock_storage.create_research.reset_mock()
            client.post("/api/research", json={"question": "test", "time_filter": tf})
            settings = mock_storage.create_research.call_args[0][2]
            assert settings["time_filter"] == tf

    def test_invalid_sources_defaults_to_all_three(self, client, mock_storage):
        client.post("/api/research", json={"question": "test", "sources": "notalist"})
        settings = mock_storage.create_research.call_args[0][2]
        assert set(settings["sources"]) == {"reddit", "hackernews", "web"}

    def test_empty_sources_defaults_to_all_three(self, client, mock_storage):
        client.post("/api/research", json={"question": "test", "sources": []})
        settings = mock_storage.create_research.call_args[0][2]
        assert set(settings["sources"]) == {"reddit", "hackernews", "web"}

    def test_seed_urls_non_list_normalized(self, client, mock_storage):
        """Non-list seed_urls should be silently normalized to []."""
        resp = client.post("/api/research", json={"question": "test", "seed_urls": "not-a-list"})
        assert resp.status_code == 200


# ── GET /api/research/<id> ────────────────────────────────────────────────────

class TestGetResearch:

    def test_not_found_returns_404(self, client, mock_storage):
        mock_storage.get_research.return_value = None
        resp = client.get("/api/research/missing123")
        assert resp.status_code == 404

    def test_returns_threads_and_comments(self, client, mock_storage):
        mock_storage.get_threads.return_value = [{"id": "t1", "title": "Thread"}]
        mock_storage.get_comments.return_value = [{"id": "c1", "body": "Comment"}]
        resp = client.get("/api/research/res123")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "threads" in data
        assert "comments" in data
        assert len(data["threads"]) == 1
        assert len(data["comments"]) == 1


# ── POST /api/research/<id>/summarize ────────────────────────────────────────

class TestSummarize:

    def test_not_found_returns_404(self, client, mock_storage):
        mock_storage.get_research.return_value = None
        resp = client.post("/api/research/missing/summarize", json={})
        assert resp.status_code == 404

    def test_returns_summary_field(self, client, monkeypatch):
        import app as app_module
        mock_summary_svc = MagicMock()
        mock_summary_svc.summarize.return_value = "This is the summary."
        monkeypatch.setattr(app_module, "summary_svc", mock_summary_svc)

        resp = client.post("/api/research/res123/summarize", json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["summary"] == "This is the summary."

    def test_feedback_truncated_at_500_chars(self, client, monkeypatch):
        import app as app_module
        mock_summary_svc = MagicMock()
        mock_summary_svc.summarize.return_value = "summary"
        monkeypatch.setattr(app_module, "summary_svc", mock_summary_svc)

        long_feedback = "x" * 600
        client.post("/api/research/res123/summarize", json={"feedback": long_feedback})

        call_kwargs = mock_summary_svc.summarize.call_args[1]
        assert len(call_kwargs["user_feedback"]) == 500

    def test_empty_feedback_passed_as_none(self, client, monkeypatch):
        import app as app_module
        mock_summary_svc = MagicMock()
        mock_summary_svc.summarize.return_value = "summary"
        monkeypatch.setattr(app_module, "summary_svc", mock_summary_svc)

        client.post("/api/research/res123/summarize", json={"feedback": "   "})

        call_kwargs = mock_summary_svc.summarize.call_args[1]
        assert call_kwargs["user_feedback"] is None

    def test_no_feedback_field_passes_none(self, client, monkeypatch):
        import app as app_module
        mock_summary_svc = MagicMock()
        mock_summary_svc.summarize.return_value = "summary"
        monkeypatch.setattr(app_module, "summary_svc", mock_summary_svc)

        client.post("/api/research/res123/summarize", json={})
        call_kwargs = mock_summary_svc.summarize.call_args[1]
        assert call_kwargs["user_feedback"] is None

    def test_max_comments_passed_to_service(self, client, monkeypatch):
        import app as app_module
        mock_summary_svc = MagicMock()
        mock_summary_svc.summarize.return_value = "summary"
        monkeypatch.setattr(app_module, "summary_svc", mock_summary_svc)

        client.post("/api/research/res123/summarize", json={"max_comments": 100})
        call_kwargs = mock_summary_svc.summarize.call_args[1]
        assert call_kwargs["max_comments"] == 100

    def test_max_comments_clamped_below_min(self, client, monkeypatch):
        import app as app_module
        mock_summary_svc = MagicMock()
        mock_summary_svc.summarize.return_value = "summary"
        monkeypatch.setattr(app_module, "summary_svc", mock_summary_svc)

        client.post("/api/research/res123/summarize", json={"max_comments": 5})
        call_kwargs = mock_summary_svc.summarize.call_args[1]
        assert call_kwargs["max_comments"] == 25

    def test_max_comments_clamped_above_max(self, client, monkeypatch):
        import app as app_module
        mock_summary_svc = MagicMock()
        mock_summary_svc.summarize.return_value = "summary"
        monkeypatch.setattr(app_module, "summary_svc", mock_summary_svc)

        client.post("/api/research/res123/summarize", json={"max_comments": 999})
        call_kwargs = mock_summary_svc.summarize.call_args[1]
        assert call_kwargs["max_comments"] == 200

    def test_max_comments_defaults_to_50(self, client, monkeypatch):
        import app as app_module
        mock_summary_svc = MagicMock()
        mock_summary_svc.summarize.return_value = "summary"
        monkeypatch.setattr(app_module, "summary_svc", mock_summary_svc)

        client.post("/api/research/res123/summarize", json={})
        call_kwargs = mock_summary_svc.summarize.call_args[1]
        assert call_kwargs["max_comments"] == 50


# ── POST /api/research/<id>/expand ────────────────────────────────────────────

class TestExpand:

    def _expand(self, client, research_id="res123", sources=None, sorts_tried=None,
                research_sources=None):
        """Helper to call expand with custom settings."""
        # Use mock_storage from the client fixture via separate setup
        import app as app_module
        settings = {
            "sorts_tried": sorts_tried or [],
            "sources": research_sources or ["reddit", "hackernews", "web"],
            "time_filter": "all",
            "max_comments_per_thread": 100,
        }
        app_module.storage_svc.get_settings.return_value = settings

        body = {}
        if sources is not None:
            body["sources"] = sources
        return client.post(f"/api/research/{research_id}/expand", json=body)

    def test_new_research_tasks_include_reddit_hn_web(self, client):
        resp = self._expand(client)
        assert resp.status_code == 200
        data = resp.get_json()
        sorts_used = data["sorts_used"]
        assert "top" in sorts_used  # first reddit sort
        assert any(s.startswith("hn_") for s in sorts_used)
        assert any(s.startswith("web_") for s in sorts_used)

    def test_exhausted_reddit_sorts_not_included(self, client):
        resp = self._expand(client, sorts_tried=["top", "new", "controversial", "hot"])
        data = resp.get_json()
        assert all(s not in data["sorts_used"] for s in ["top", "new", "controversial", "hot"])

    def test_reddit_sort_progression(self, client):
        """Second click should pick 'new' after 'top' was tried."""
        resp = self._expand(client, sorts_tried=["top"])
        data = resp.get_json()
        assert "new" in data["sorts_used"]
        assert "top" not in data["sorts_used"]

    def test_hn_pagination_increments_page(self, client):
        """After hn_0, the next task should be hn_1."""
        resp = self._expand(client, sorts_tried=["hn_0"])
        data = resp.get_json()
        assert "hn_1" in data["sorts_used"]
        assert "hn_0" not in data["sorts_used"]

    def test_hn_backward_compat_string_counts_as_page_0(self, client):
        """Old 'hn' entry should count as page 0 tried, so next is hn_1."""
        resp = self._expand(client, sorts_tried=["hn"])
        data = resp.get_json()
        assert "hn_1" in data["sorts_used"]

    def test_web_pagination_increments_page(self, client):
        resp = self._expand(client, sorts_tried=["web_0"])
        data = resp.get_json()
        assert "web_1" in data["sorts_used"]

    def test_web_backward_compat_string_counts_as_page_0(self, client):
        resp = self._expand(client, sorts_tried=["web"])
        data = resp.get_json()
        assert "web_1" in data["sorts_used"]

    def test_web_exhausted_after_max_pages(self, client, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module.config, "WEB_MAX_EXPAND_PAGES", 3)
        resp = self._expand(client, sorts_tried=["web_0", "web_1", "web_2"])
        data = resp.get_json()
        assert not any(s.startswith("web_") for s in data.get("sorts_used", []))

    def test_hn_exhausted_after_max_pages(self, client, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module.config, "HN_MAX_EXPAND_PAGES", 3)
        resp = self._expand(client, sorts_tried=["hn_0", "hn_1", "hn_2"])
        data = resp.get_json()
        assert not any(s.startswith("hn_") for s in data.get("sorts_used", []))

    def test_all_sources_exhausted_returns_400(self, client, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module.config, "HN_MAX_EXPAND_PAGES", 1)
        monkeypatch.setattr(app_module.config, "WEB_MAX_EXPAND_PAGES", 1)
        resp = self._expand(
            client,
            sorts_tried=["top", "new", "controversial", "hot", "hn_0", "web_0"],
        )
        assert resp.status_code == 400
        assert "tried" in resp.get_json()["error"].lower()

    def test_respects_requested_sources_reddit_only(self, client):
        resp = self._expand(client, sources=["reddit"])
        data = resp.get_json()
        sorts_used = data["sorts_used"]
        assert not any(s.startswith("hn_") for s in sorts_used)
        assert not any(s.startswith("web_") for s in sorts_used)
        assert "top" in sorts_used

    def test_respects_requested_sources_hn_only(self, client):
        resp = self._expand(client, sources=["hackernews"])
        data = resp.get_json()
        sorts_used = data["sorts_used"]
        assert any(s.startswith("hn_") for s in sorts_used)
        assert "top" not in sorts_used

    def test_source_not_in_research_sources_excluded(self, client):
        """HN should be excluded if research was reddit-only."""
        resp = self._expand(
            client,
            research_sources=["reddit"],
            sources=["reddit", "hackernews"],
        )
        data = resp.get_json()
        sorts_used = data["sorts_used"]
        assert not any(s.startswith("hn_") for s in sorts_used)

    def test_returns_sorts_used_field(self, client):
        resp = self._expand(client)
        assert "sorts_used" in resp.get_json()

    def test_not_found_returns_404(self, client, mock_storage):
        mock_storage.get_research.return_value = None
        resp = client.post("/api/research/missing/expand", json={})
        assert resp.status_code == 404


# ── GET /api/research/<id>/expand/status ─────────────────────────────────────

class TestExpandStatus:

    def _get_status(self, client, sorts_tried=None, sources=None):
        import app as app_module
        app_module.storage_svc.get_settings.return_value = {
            "sorts_tried": sorts_tried or [],
            "sources": sources or ["reddit", "hackernews", "web"],
        }
        return client.get("/api/research/res123/expand/status")

    def test_new_research_can_expand(self, client):
        resp = self._get_status(client)
        data = resp.get_json()
        assert data["can_expand"] is True
        assert data["reddit_exhausted"] is False
        assert data["hn_exhausted"] is False
        assert data["web_exhausted"] is False

    def test_reddit_exhausted_after_4_sorts(self, client, monkeypatch):
        resp = self._get_status(client, sorts_tried=["top", "new", "controversial", "hot"])
        data = resp.get_json()
        assert data["reddit_exhausted"] is True

    def test_hn_exhausted_after_max_pages(self, client, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module.config, "HN_MAX_EXPAND_PAGES", 3)
        resp = self._get_status(client, sorts_tried=["hn_0", "hn_1", "hn_2"])
        assert resp.get_json()["hn_exhausted"] is True

    def test_web_exhausted_after_max_pages(self, client, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module.config, "WEB_MAX_EXPAND_PAGES", 3)
        resp = self._get_status(client, sorts_tried=["web_0", "web_1", "web_2"])
        assert resp.get_json()["web_exhausted"] is True

    def test_can_expand_false_when_all_exhausted(self, client, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module.config, "HN_MAX_EXPAND_PAGES", 1)
        monkeypatch.setattr(app_module.config, "WEB_MAX_EXPAND_PAGES", 1)
        resp = self._get_status(
            client,
            sorts_tried=["top", "new", "controversial", "hot", "hn_0", "web_0"],
        )
        assert resp.get_json()["can_expand"] is False

    def test_research_sources_included_in_response(self, client):
        resp = self._get_status(client, sources=["reddit"])
        data = resp.get_json()
        assert "research_sources" in data
        assert data["research_sources"] == ["reddit"]

    def test_reddit_exhausted_if_not_in_research_sources(self, client):
        """reddit_exhausted should be True when reddit wasn't in the original research."""
        resp = self._get_status(client, sources=["hackernews", "web"])
        data = resp.get_json()
        assert data["reddit_exhausted"] is True

    def test_hn_exhausted_if_not_in_research_sources(self, client):
        resp = self._get_status(client, sources=["reddit"])
        assert resp.get_json()["hn_exhausted"] is True

    def test_hn_backward_compat_string(self, client):
        """Old 'hn' string in sorts_tried should count toward exhaustion."""
        import app as app_module
        # HN_MAX_EXPAND_PAGES=1: one 'hn' entry should exhaust it
        import app as app_module
        app_module.config.HN_MAX_EXPAND_PAGES = 1
        resp = self._get_status(client, sorts_tried=["hn"])
        assert resp.get_json()["hn_exhausted"] is True


# ── POST /api/research/<id>/add-thread ───────────────────────────────────────

class TestAddThread:

    def test_missing_url_returns_400(self, client):
        resp = client.post("/api/research/res123/add-thread", json={})
        assert resp.status_code == 400

    def test_not_found_returns_404(self, client, mock_storage):
        mock_storage.get_research.return_value = None
        resp = client.post("/api/research/missing/add-thread", json={"url": "https://example.com"})
        assert resp.status_code == 404

    def test_detects_reddit_full_url(self, client):
        resp = client.post("/api/research/res123/add-thread", json={
            "url": "https://www.reddit.com/r/python/comments/abc123/my_thread/"
        })
        data = resp.get_json()
        assert data["source"] == "reddit"
        assert data["thread_id"] == "abc123"

    def test_detects_reddit_shortlink(self, client):
        resp = client.post("/api/research/res123/add-thread", json={
            "url": "https://redd.it/xyz789"
        })
        data = resp.get_json()
        assert data["source"] == "reddit"
        assert data["thread_id"] == "xyz789"

    def test_detects_hn_url(self, client):
        resp = client.post("/api/research/res123/add-thread", json={
            "url": "https://news.ycombinator.com/item?id=12345678"
        })
        data = resp.get_json()
        assert data["source"] == "hackernews"
        assert data["thread_id"] == "hn_12345678"

    def test_detects_web_fallback(self, client):
        resp = client.post("/api/research/res123/add-thread", json={
            "url": "https://example.com/some-article"
        })
        data = resp.get_json()
        assert data["source"] == "web"
        assert data["thread_id"].startswith("web_")

    def test_already_exists_returns_flag(self, client, mock_storage):
        import hashlib
        url = "https://reddit.com/r/python/comments/dup123/thread/"
        mock_storage.get_existing_thread_ids.return_value = {"dup123"}
        resp = client.post("/api/research/res123/add-thread", json={"url": url})
        data = resp.get_json()
        assert data.get("already_exists") is True

    def test_web_already_exists(self, client, mock_storage):
        import hashlib
        url = "https://example.com/article"
        url_hash = hashlib.md5(url.encode()).hexdigest()[:10]
        mock_storage.get_existing_thread_ids.return_value = {f"web_{url_hash}"}
        resp = client.post("/api/research/res123/add-thread", json={"url": url})
        assert resp.get_json().get("already_exists") is True


# ── DELETE /api/research/<id>/threads/<thread_id> ────────────────────────────

class TestDeleteThread:

    def test_deletes_thread(self, client, mock_storage):
        resp = client.delete("/api/research/res123/threads/t1")
        assert resp.status_code == 200
        mock_storage.delete_thread.assert_called_once_with("res123", "t1")

    def test_not_found_returns_404(self, client, mock_storage):
        mock_storage.get_research.return_value = None
        resp = client.delete("/api/research/missing/threads/t1")
        assert resp.status_code == 404


# ── User relevancy routes ─────────────────────────────────────────────────────

class TestUserRelevancy:

    def test_set_valid_score(self, client, mock_storage):
        resp = client.put(
            "/api/research/res123/comments/c1/user-relevancy",
            json={"score": 7},
        )
        assert resp.status_code == 200
        mock_storage.set_user_relevancy.assert_called_once_with("res123", "c1", 7)

    def test_score_below_1_returns_400(self, client):
        resp = client.put(
            "/api/research/res123/comments/c1/user-relevancy",
            json={"score": 0},
        )
        assert resp.status_code == 400

    def test_score_above_10_returns_400(self, client):
        resp = client.put(
            "/api/research/res123/comments/c1/user-relevancy",
            json={"score": 11},
        )
        assert resp.status_code == 400

    def test_score_none_clears_relevancy(self, client, mock_storage):
        resp = client.put(
            "/api/research/res123/comments/c1/user-relevancy",
            json={"score": None},
        )
        assert resp.status_code == 200
        mock_storage.set_user_relevancy.assert_called_once_with("res123", "c1", None)


# ── Star route ────────────────────────────────────────────────────────────────

class TestStar:

    def test_toggle_star_returns_starred_state(self, client, mock_storage):
        mock_storage.toggle_star.return_value = 1
        resp = client.post("/api/research/res123/comments/c1/star")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["starred"] == 1
        mock_storage.toggle_star.assert_called_once_with("res123", "c1")


# ── Archive routes ────────────────────────────────────────────────────────────

class TestArchive:

    def test_archive_calls_storage(self, client, mock_storage):
        resp = client.post("/api/research/res123/archive")
        assert resp.status_code == 200
        mock_storage.archive_research.assert_called_once_with("res123")

    def test_archive_not_found_returns_404(self, client, mock_storage):
        mock_storage.get_research.return_value = None
        resp = client.post("/api/research/missing/archive")
        assert resp.status_code == 404

    def test_unarchive_calls_storage(self, client, mock_storage):
        resp = client.post("/api/research/res123/unarchive")
        assert resp.status_code == 200
        mock_storage.unarchive_research.assert_called_once_with("res123")

    def test_delete_research_calls_storage(self, client, mock_storage):
        resp = client.delete("/api/research/res123/delete")
        assert resp.status_code == 200
        mock_storage.delete_research.assert_called_once_with("res123")

    def test_get_archived_returns_list(self, client, mock_storage):
        mock_storage.get_archived.return_value = [
            {"id": "arc1", "question": "old research"}
        ]
        resp = client.get("/api/archived")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "archived" in data
        assert len(data["archived"]) == 1


# ── Rescore endpoints ────────────────────────────────────────────────────────

class TestRescore:

    def test_rescore_not_found_returns_404(self, client, mock_storage):
        mock_storage.get_research.return_value = None
        resp = client.post("/api/research/missing/rescore")
        assert resp.status_code == 404

    def test_rescore_no_unscored_returns_400(self, client, mock_storage, monkeypatch):
        import app as app_module
        mock_storage.get_unscored_comments.return_value = []
        resp = client.post("/api/research/res123/rescore")
        assert resp.status_code == 400

    def test_rescore_returns_count(self, client, mock_storage, monkeypatch):
        import app as app_module
        from models.data_models import RedditComment
        unscored = [
            RedditComment(id="c1", thread_id="t1", author="a", body="b",
                          score=1, created_utc=0, depth=0, permalink="p"),
            RedditComment(id="c2", thread_id="t1", author="a", body="b",
                          score=1, created_utc=0, depth=0, permalink="p"),
        ]
        mock_storage.get_unscored_comments.return_value = unscored
        resp = client.post("/api/research/res123/rescore")
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 2

    def test_unscored_count_endpoint(self, client, mock_storage):
        mock_storage.get_unscored_count.return_value = 5
        resp = client.get("/api/research/res123/unscored-count")
        assert resp.status_code == 200
        assert resp.get_json()["unscored_count"] == 5
