"""
Shared fixtures for all test modules.

Route tests use a mocked storage + all-mocked external services.
Storage tests use a real StorageService backed by a tmp_path SQLite file.
"""

import threading
import pytest
from unittest.mock import MagicMock


# ── Default mock data helpers ──────────────────────────────────────────────────

def make_research(research_id="res123", question="test question", status="complete"):
    return {
        "id": research_id,
        "question": question,
        "status": status,
        "summary": None,
        "num_threads": 0,
        "num_comments": 0,
        "created_at": "2024-01-01T00:00:00+00:00",
        "completed_at": None,
        "settings_json": "{}",
        "archived": 0,
    }


def make_mock_storage(research_id="res123"):
    """Return a MagicMock storage_svc with sensible defaults."""
    m = MagicMock()
    m.get_research.return_value = make_research(research_id)
    m.get_comments.return_value = []
    m.get_threads.return_value = []
    m.get_settings.return_value = {
        "sorts_tried": [],
        "sources": ["reddit", "hackernews", "web"],
        "time_filter": "all",
        "max_comments_per_thread": 100,
    }
    m.get_existing_thread_ids.return_value = set()
    m.get_history.return_value = []
    m.get_archived.return_value = []
    m.toggle_star.return_value = 1
    m.export_csv.return_value = "/tmp/fake.csv"
    return m


# ── Flask test client fixture ──────────────────────────────────────────────────

@pytest.fixture
def mock_storage():
    return make_mock_storage()


@pytest.fixture
def client(monkeypatch, mock_storage):
    """
    Flask test client with all external service globals mocked.
    Background threads are suppressed so pipelines don't actually run.
    """
    import app as app_module

    monkeypatch.setattr(app_module, "storage_svc", mock_storage)
    monkeypatch.setattr(app_module, "scoring_svc", MagicMock())
    monkeypatch.setattr(app_module, "summary_svc", MagicMock())
    monkeypatch.setattr(app_module, "reddit_svc", MagicMock())
    monkeypatch.setattr(app_module, "web_search_svc", MagicMock())
    monkeypatch.setattr(app_module, "hn_svc", MagicMock())
    monkeypatch.setattr(app_module, "article_svc", MagicMock())

    # Prevent background threads from actually running pipelines
    mock_thread = MagicMock()
    monkeypatch.setattr(threading, "Thread", lambda **kwargs: mock_thread)

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ── Real StorageService fixture (no mocks) ────────────────────────────────────

@pytest.fixture
def real_storage(tmp_path):
    from services.storage_service import StorageService
    return StorageService(str(tmp_path / "test.db"), str(tmp_path / "exports"))
