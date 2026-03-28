"""
Tests for HNService, ArticleService, and WebSearchService.
External HTTP calls (requests, DDGS, trafilatura, PRAW) are mocked.
"""

import hashlib
import pytest
from unittest.mock import MagicMock, patch, call


# ═══════════════════════════════════════════════════════════════════════════════
# HNService
# ═══════════════════════════════════════════════════════════════════════════════

class TestHNService:

    @pytest.fixture
    def svc(self):
        from services.hn_service import HNService
        return HNService()

    def _make_hit(self, story_id, title="Test Story", points=50, num_comments=10):
        return {
            "objectID": story_id,
            "title": title,
            "points": points,
            "num_comments": num_comments,
            "url": f"https://example.com/{story_id}",
            "author": "user",
            "created_at_i": 1700000000,
            "story_text": "",
        }

    def test_search_passes_page_param_to_algolia(self, svc):
        with patch("services.hn_service.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"hits": []}
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            svc.search_stories(["python tips"], max_results=5, page=2)

            call_kwargs = mock_get.call_args[1]
            assert call_kwargs["params"]["page"] == 2

    def test_search_passes_query_to_algolia(self, svc):
        with patch("services.hn_service.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"hits": []}
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            svc.search_stories(["python tips"], max_results=5)
            call_kwargs = mock_get.call_args[1]
            assert call_kwargs["params"]["query"] == "python tips"

    def test_search_deduplicates_story_ids(self, svc):
        """Same story returned by multiple queries should appear once."""
        hit = self._make_hit("story1")
        with patch("services.hn_service.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"hits": [hit]}
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            results = svc.search_stories(["query1", "query2"], max_results=10)
            ids = [t.id for t in results]
            assert ids.count("hn_story1") == 1

    def test_thread_ids_prefixed_with_hn(self, svc):
        with patch("services.hn_service.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"hits": [self._make_hit("12345")]}
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            results = svc.search_stories(["test"])
            assert all(t.id.startswith("hn_") for t in results)

    def test_thread_source_is_hackernews(self, svc):
        with patch("services.hn_service.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"hits": [self._make_hit("999")]}
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            results = svc.search_stories(["test"])
            assert all(t.source == "hackernews" for t in results)

    def test_network_error_skips_query_gracefully(self, svc):
        with patch("services.hn_service.requests.get", side_effect=Exception("timeout")):
            results = svc.search_stories(["query1"])
            assert results == []

    def test_collect_comments_flattens_tree(self, svc):
        """Nested children should be recursively flattened."""
        item_data = {
            "children": [
                {
                    "id": 1001,
                    "type": "comment",
                    "text": "Top-level comment",
                    "author": "user1",
                    "points": 5,
                    "created_at_i": 1700001000,
                    "children": [
                        {
                            "id": 1002,
                            "type": "comment",
                            "text": "Nested reply",
                            "author": "user2",
                            "points": 2,
                            "created_at_i": 1700002000,
                            "children": [],
                        }
                    ],
                }
            ]
        }
        with patch("services.hn_service.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = item_data
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            comments = svc.collect_comments("hn_99999")
            assert len(comments) == 2

    def test_comment_ids_prefixed_with_hn(self, svc):
        item_data = {
            "children": [
                {
                    "id": 5555,
                    "type": "comment",
                    "text": "comment text",
                    "author": "user",
                    "points": 1,
                    "created_at_i": 0,
                    "children": [],
                }
            ]
        }
        with patch("services.hn_service.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = item_data
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            comments = svc.collect_comments("hn_99")
            assert all(c.id.startswith("hn_") for c in comments)

    def test_deleted_comments_skipped(self, svc):
        item_data = {
            "children": [
                {
                    "id": 7777,
                    "type": "comment",
                    "text": "[deleted]",
                    "author": "user",
                    "points": 1,
                    "created_at_i": 0,
                    "children": [],
                },
                {
                    "id": 7778,
                    "type": "comment",
                    "text": "Valid comment",
                    "author": "user",
                    "points": 5,
                    "created_at_i": 0,
                    "children": [],
                },
            ]
        }
        with patch("services.hn_service.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = item_data
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            comments = svc.collect_comments("hn_99")
            assert len(comments) == 1
            assert comments[0].body == "Valid comment"

    def test_collect_comments_network_error_returns_empty(self, svc):
        with patch("services.hn_service.requests.get", side_effect=Exception("timeout")):
            assert svc.collect_comments("hn_99") == []


# ═══════════════════════════════════════════════════════════════════════════════
# WebSearchService
# ═══════════════════════════════════════════════════════════════════════════════

class TestWebSearchService:

    @pytest.fixture
    def svc(self):
        from services.web_search_service import WebSearchService
        mock_reddit = MagicMock()
        return WebSearchService(mock_reddit)

    def _ddgs_result(self, href, body="snippet"):
        return {"href": href, "body": body, "title": "Title"}

    def test_excludes_reddit_domains(self, svc):
        with patch("services.web_search_service.DDGS") as mock_ddgs_cls:
            mock_ddgs = MagicMock()
            mock_ddgs_cls.return_value = mock_ddgs
            mock_ddgs.text.return_value = [
                self._ddgs_result("https://www.reddit.com/r/python/some_post/"),
                self._ddgs_result("https://techblog.example.com/article"),
            ]
            results = svc.search_web_articles(["python tips"])
            assert all("reddit.com" not in url for url in results)
            assert any("techblog.example.com" in url for url in results)

    def test_excludes_hn_domain(self, svc):
        with patch("services.web_search_service.DDGS") as mock_ddgs_cls:
            mock_ddgs = MagicMock()
            mock_ddgs_cls.return_value = mock_ddgs
            mock_ddgs.text.return_value = [
                self._ddgs_result("https://news.ycombinator.com/item?id=123"),
                self._ddgs_result("https://example.com/article"),
            ]
            results = svc.search_web_articles(["python"])
            assert all("ycombinator" not in url for url in results)

    def test_excludes_youtube(self, svc):
        with patch("services.web_search_service.DDGS") as mock_ddgs_cls:
            mock_ddgs = MagicMock()
            mock_ddgs_cls.return_value = mock_ddgs
            mock_ddgs.text.return_value = [
                self._ddgs_result("https://www.youtube.com/watch?v=abc"),
                self._ddgs_result("https://example.com/article"),
            ]
            results = svc.search_web_articles(["python"])
            assert all("youtube" not in url for url in results)

    def test_deduplicates_urls(self, svc):
        with patch("services.web_search_service.DDGS") as mock_ddgs_cls:
            mock_ddgs = MagicMock()
            mock_ddgs_cls.return_value = mock_ddgs
            url = "https://example.com/article"
            mock_ddgs.text.return_value = [
                self._ddgs_result(url),
                self._ddgs_result(url),
            ]
            results = svc.search_web_articles(["q1", "q2"])
            assert results.count(url) == 1

    def test_pagination_skips_earlier_results(self, svc):
        """page=1 should skip the first max_results results."""
        with patch("services.web_search_service.DDGS") as mock_ddgs_cls:
            mock_ddgs = MagicMock()
            mock_ddgs_cls.return_value = mock_ddgs
            # Return 6 results; with max_results=3 and page=1, first 3 should be skipped
            mock_ddgs.text.return_value = [
                self._ddgs_result(f"https://example.com/page{i}") for i in range(6)
            ]
            results = svc.search_web_articles(["query"], max_results=3, page=1)
            assert "https://example.com/page0" not in results
            assert "https://example.com/page1" not in results
            assert "https://example.com/page2" not in results

    def test_respects_max_results_cap(self, svc):
        with patch("services.web_search_service.DDGS") as mock_ddgs_cls:
            mock_ddgs = MagicMock()
            mock_ddgs_cls.return_value = mock_ddgs
            mock_ddgs.text.return_value = [
                self._ddgs_result(f"https://example.com/{i}") for i in range(20)
            ]
            results = svc.search_web_articles(["query"], max_results=5)
            assert len(results) <= 5

    def test_ddgs_error_skips_query_gracefully(self, svc):
        with patch("services.web_search_service.DDGS") as mock_ddgs_cls:
            mock_ddgs = MagicMock()
            mock_ddgs_cls.return_value = mock_ddgs
            mock_ddgs.text.side_effect = Exception("rate limited")
            results = svc.search_web_articles(["query"])
            assert results == []

    def test_search_reddit_threads_caps_at_max_total(self, svc):
        """Should not fetch more than max_total unique thread IDs via PRAW."""
        with patch("services.web_search_service.DDGS") as mock_ddgs_cls:
            mock_ddgs = MagicMock()
            mock_ddgs_cls.return_value = mock_ddgs
            # Return many different thread IDs
            mock_ddgs.text.return_value = [
                {"href": f"https://reddit.com/r/python/comments/id{i}/title/", "body": ""}
                for i in range(50)
            ]
            # PRAW submission fetch
            svc.reddit.submission.side_effect = Exception("skip")

            results = svc.search_reddit_threads(["python"], max_results=5, max_total=3)
            # PRAW should have been called at most 3 times (max_total)
            assert svc.reddit.submission.call_count <= 3


# ═══════════════════════════════════════════════════════════════════════════════
# ArticleService
# ═══════════════════════════════════════════════════════════════════════════════

class TestArticleService:

    @pytest.fixture
    def mock_llm(self):
        return MagicMock()

    @pytest.fixture
    def svc(self, mock_llm):
        from services.article_service import ArticleService
        return ArticleService(mock_llm)

    def test_fetch_article_returns_none_on_trafilatura_failure(self, svc):
        with patch("trafilatura.fetch_url", return_value=None):
            result = svc.fetch_article("https://example.com")
            assert result is None

    def test_fetch_article_returns_none_on_short_text(self, svc):
        with patch("trafilatura.fetch_url", return_value="raw html"), \
             patch("trafilatura.extract", return_value="short"), \
             patch("trafilatura.extract_metadata", return_value=None):
            result = svc.fetch_article("https://example.com")
            assert result is None

    def test_fetch_article_returns_title_and_body(self, svc):
        meta = MagicMock()
        meta.title = "My Article Title"
        meta.date = "2024-06-15"
        with patch("trafilatura.fetch_url", return_value="raw html"), \
             patch("trafilatura.extract", return_value="A" * 200), \
             patch("trafilatura.extract_metadata", return_value=meta):
            result = svc.fetch_article("https://example.com")
            assert result is not None
            title, body, created_utc = result
            assert title == "My Article Title"
            assert len(body) >= 100
            assert created_utc > 0  # date was parsed successfully

    def test_fetch_article_exception_returns_none(self, svc):
        with patch("trafilatura.fetch_url", side_effect=Exception("network error")):
            result = svc.fetch_article("https://example.com")
            assert result is None

    def test_extract_quotes_returns_comments_with_web_source(self, svc, mock_llm):
        from services.article_service import QuoteExtractionResponse, ExtractedQuote
        mock_llm.complete.return_value = QuoteExtractionResponse(quotes=[
            ExtractedQuote(text="Quote one", author="Author A"),
            ExtractedQuote(text="Quote two", author="Article"),
        ])
        comments = svc.extract_quotes(
            thread_id="web_abc123",
            url="https://example.com/article",
            title="Test Article",
            body="Long article body text here.",
            question="What is Python?",
        )
        assert all(c.source == "web" for c in comments)
        assert len(comments) == 2

    def test_extract_quotes_ids_follow_q_pattern(self, svc, mock_llm):
        from services.article_service import QuoteExtractionResponse, ExtractedQuote
        mock_llm.complete.return_value = QuoteExtractionResponse(quotes=[
            ExtractedQuote(text="Quote 1", author="A"),
            ExtractedQuote(text="Quote 2", author="B"),
        ])
        comments = svc.extract_quotes("web_tid1", "https://example.com", "Title", "body text " * 20, "q")
        assert comments[0].id == "web_tid1_q0"
        assert comments[1].id == "web_tid1_q1"

    def test_extract_quotes_caches_results(self, svc, mock_llm):
        from services.article_service import QuoteExtractionResponse, ExtractedQuote
        mock_llm.complete.return_value = QuoteExtractionResponse(quotes=[
            ExtractedQuote(text="Quote", author="A"),
        ])
        svc.extract_quotes("web_tid1", "https://example.com", "T", "body " * 20, "q")
        cached = svc.get_cached_quotes("web_tid1")
        assert len(cached) == 1

    def test_extract_quotes_llm_failure_returns_empty(self, svc, mock_llm):
        mock_llm.complete.side_effect = Exception("API error")
        comments = svc.extract_quotes("web_tid1", "https://example.com", "T", "body", "q")
        assert comments == []

    def test_make_thread_creates_correct_thread(self, svc):
        url = "https://techblog.example.com/python-tips"
        thread = svc.make_thread(url, "Python Tips", "Article body text")
        assert thread.source == "web"
        assert thread.id.startswith("web_")
        assert thread.url == url
        assert thread.subreddit == "techblog.example.com"

    def test_make_thread_id_is_url_hash(self, svc):
        url = "https://example.com/article"
        expected_hash = hashlib.md5(url.encode()).hexdigest()[:10]
        thread = svc.make_thread(url, "T", "body")
        assert thread.id == f"web_{expected_hash}"

    def test_article_author_uses_domain_when_article_author(self, svc, mock_llm):
        """'Article' author should be replaced with domain name."""
        from services.article_service import QuoteExtractionResponse, ExtractedQuote
        mock_llm.complete.return_value = QuoteExtractionResponse(quotes=[
            ExtractedQuote(text="Quote", author="Article"),
        ])
        comments = svc.extract_quotes(
            "web_1", "https://techblog.example.com/post", "T", "body " * 20, "q"
        )
        assert "techblog.example.com" in comments[0].author
