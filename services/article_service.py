import hashlib
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from models.data_models import RedditComment, RedditThread
from services.llm_provider import LLMProvider


QUOTE_EXTRACTION_SYSTEM_PROMPT = """You are extracting research-relevant quotes from a web article. Given the article text and a research question, identify 3-8 distinct factual claims, opinions, or first-hand accounts from the article that are relevant to the research question.

Rules:
- Each quote should be a verbatim or lightly-edited excerpt from the article
- Do not invent content that is not present in the article
- Each quote should express a single distinct claim, opinion, or piece of information
- Skip generic filler text — focus on substantive, specific content
- If the article has fewer than 3 relevant quotes, return as many as you can find (minimum 1)
- Keep each quote concise (1-3 sentences)"""


class ExtractedQuote(BaseModel):
    text: str = Field(description="The verbatim or lightly-edited quote from the article")
    author: str = Field(
        description="The person or entity being quoted, or 'Article' if it's the author's own words"
    )


class QuoteExtractionResponse(BaseModel):
    quotes: List[ExtractedQuote] = Field(
        description="3-8 distinct relevant quotes from the article"
    )


class ArticleService:
    """Extracts content from web URLs and synthesizes comments from article text."""

    def __init__(self, llm: LLMProvider):
        self.llm = llm
        self._quote_cache: Dict[str, List[RedditComment]] = {}

    def fetch_article(self, url: str) -> Optional[Tuple[str, str]]:
        """
        Download and extract article text using trafilatura.
        Returns (title, body_text) or None if extraction fails.
        """
        try:
            import trafilatura

            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                return None

            text = trafilatura.extract(
                downloaded, include_comments=False, include_tables=True
            )
            if not text or len(text) < 100:
                return None

            # Try to extract title from metadata
            metadata = trafilatura.extract_metadata(downloaded)
            title = metadata.title if metadata and metadata.title else ""

            if not title:
                # Fallback: use first line of text as title
                first_line = text.split("\n")[0].strip()
                title = first_line[:120] if first_line else urlparse(url).netloc

            return title, text
        except Exception:
            return None

    def extract_quotes(
        self,
        thread_id: str,
        url: str,
        title: str,
        body: str,
        question: str,
    ) -> List[RedditComment]:
        """
        Use the LLM to extract relevant quotes from article text.
        Caches results by thread_id for later retrieval.
        """
        domain = urlparse(url).netloc.replace("www.", "")

        # Truncate body to avoid excessive token usage
        truncated_body = body[:4000]

        user_prompt = (
            f"Research Question: {question}\n\n"
            f"Article Title: {title}\n"
            f"Source: {domain}\n\n"
            f"Article Text:\n{truncated_body}"
        )

        try:
            response: QuoteExtractionResponse = self.llm.complete(
                system_prompt=QUOTE_EXTRACTION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_model=QuoteExtractionResponse,
                temperature=0.1,
            )
            quotes = response.quotes
        except Exception:
            quotes = []

        comments = []
        for i, quote in enumerate(quotes):
            comment_id = f"{thread_id}_q{i}"
            author = quote.author if quote.author and quote.author != "Article" else domain
            comments.append(
                RedditComment(
                    id=comment_id,
                    thread_id=thread_id,
                    author=author,
                    body=quote.text,
                    score=0,
                    created_utc=0,
                    depth=0,
                    permalink=url,
                    source="web",
                )
            )

        self._quote_cache[thread_id] = comments
        return comments

    def make_thread(self, url: str, title: str, body: str) -> RedditThread:
        """Create a RedditThread representing a web article."""
        domain = urlparse(url).netloc.replace("www.", "")
        url_hash = hashlib.md5(url.encode()).hexdigest()[:10]
        thread_id = f"web_{url_hash}"

        return RedditThread(
            id=thread_id,
            title=title,
            subreddit=domain,
            score=0,
            num_comments=0,
            url=url,
            permalink=url,
            selftext=body[:500],
            created_utc=0,
            author=domain,
            source="web",
        )

    def get_cached_quotes(self, thread_id: str) -> List[RedditComment]:
        """Retrieve previously extracted quotes from the cache."""
        return self._quote_cache.get(thread_id, [])

    def clear_cache(self):
        """Clear the quote cache after a pipeline run."""
        self._quote_cache.clear()
