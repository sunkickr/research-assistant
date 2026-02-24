from pydantic import BaseModel, Field
from typing import List, Optional, Callable
from models.data_models import RedditComment, ScoredComment
from services.llm_provider import LLMProvider


class CommentScore(BaseModel):
    """Structured LLM output for one comment's relevancy score."""

    comment_id: str = Field(description="The Reddit comment ID")
    relevancy_score: int = Field(
        description="Relevancy score from 1-10, where 10 is highly relevant",
        ge=1,
        le=10,
    )
    reasoning: str = Field(
        description="Brief explanation of why this score was assigned",
    )


class BatchScoreResponse(BaseModel):
    """Structured LLM output for a batch of comment scores."""

    scores: List[CommentScore]


SCORING_SYSTEM_PROMPT = """You are a relevancy scoring assistant. You will receive a research question and a batch of Reddit comments. For each comment, assign a relevancy score from 1-10:

- 1-2: Completely irrelevant (off-topic, jokes with no substance, spam)
- 3-4: Tangentially related but not useful for answering the question
- 5-6: Somewhat relevant, contains partial information or weak opinions
- 7-8: Relevant, provides useful information, experience, or perspective
- 9-10: Highly relevant, directly answers or deeply addresses the question

Consider: Does the comment provide factual information, personal experience, expert insight, or a well-reasoned opinion relevant to the question? Content quality matters more than upvotes.

You MUST return a score for every comment in the batch. Use the exact comment IDs provided."""


class ScoringService:
    """Scores Reddit comments for relevancy using an LLM."""

    def __init__(self, llm: LLMProvider, batch_size: int = 20):
        self.llm = llm
        self.batch_size = batch_size

    def score_comments(
        self,
        question: str,
        comments: List[RedditComment],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[ScoredComment]:
        """Score all comments in batches. Returns ScoredComment objects."""
        scored = []
        total_batches = (len(comments) + self.batch_size - 1) // self.batch_size

        for i in range(0, len(comments), self.batch_size):
            batch_num = i // self.batch_size + 1
            batch = comments[i : i + self.batch_size]

            if progress_callback:
                progress_callback(batch_num, total_batches)

            batch_scored = self._score_batch(question, batch)
            scored.extend(batch_scored)

        return scored

    def _score_batch(
        self, question: str, batch: List[RedditComment]
    ) -> List[ScoredComment]:
        """Score a single batch of comments via the LLM."""
        comments_text = "\n\n".join(
            f"[Comment ID: {c.id}] (score: {c.score})\n{c.body[:500]}"
            for c in batch
        )
        user_prompt = (
            f"Research Question: {question}\n\n"
            f"Comments to score:\n{comments_text}"
        )

        try:
            response: BatchScoreResponse = self.llm.complete(
                system_prompt=SCORING_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_model=BatchScoreResponse,
            )
            score_map = {s.comment_id: s for s in response.scores}
        except Exception:
            score_map = {}

        results = []
        for comment in batch:
            score_data = score_map.get(comment.id)
            results.append(
                ScoredComment(
                    id=comment.id,
                    thread_id=comment.thread_id,
                    author=comment.author,
                    body=comment.body,
                    score=comment.score,
                    created_utc=comment.created_utc,
                    depth=comment.depth,
                    permalink=comment.permalink,
                    relevancy_score=score_data.relevancy_score if score_data else 5,
                    reasoning=score_data.reasoning
                    if score_data
                    else "Score not returned by LLM",
                )
            )
        return results
