from typing import List
from models.data_models import ScoredComment
from services.llm_provider import LLMProvider

SUMMARY_SYSTEM_PROMPT = """You are a research summarizer. You will receive a research question and a collection of Reddit comments that have been scored for relevancy. Each comment includes its upvote count and relevancy score.

Create a comprehensive summary that:
1. Identifies the most common answers, themes, and opinions (weighted by how frequently they appear and their upvote counts)
2. Highlights the most insightful or highly-rated responses
3. Notes any significant disagreements or contrasting viewpoints
4. Separates factual claims from opinions and personal anecdotes
5. Indicates when an answer was rare or poorly received (downvoted)
6. Provides an overall consensus if one exists

Format your response with clear headers and paragraphs. Be thorough but concise. Reference evidence from comments where helpful (e.g., "Multiple commenters noted..." or "A highly-upvoted response suggested..."). Aim for 300-600 words."""


class SummaryService:
    """Generates AI summaries of scored Reddit comments."""

    def __init__(self, llm: LLMProvider):
        self.llm = llm

    def summarize(
        self, question: str, comments: List[ScoredComment], min_relevancy: int = 4
    ) -> str:
        """
        Generate a summary of relevant comments.
        Filters to comments with relevancy >= min_relevancy,
        sorts by relevancy * upvotes to surface the best content.
        """
        relevant = [c for c in comments if c.relevancy_score >= min_relevancy]

        if not relevant:
            return "No sufficiently relevant comments were found to summarize. Try broadening your search query."

        relevant.sort(
            key=lambda c: c.relevancy_score * max(c.score, 1), reverse=True
        )

        # Take top 50 most relevant for the summary prompt
        top_comments = relevant[:50]

        comments_text = "\n\n".join(
            f"[Relevancy: {c.relevancy_score}/10, Upvotes: {c.score}]\n{c.body[:600]}"
            for c in top_comments
        )
        user_prompt = (
            f"Research Question: {question}\n\n"
            f"Total comments analyzed: {len(comments)}\n"
            f"Comments meeting relevancy threshold ({min_relevancy}+): {len(relevant)}\n\n"
            f"Top scored comments:\n{comments_text}"
        )

        return self.llm.complete_text(
            system_prompt=SUMMARY_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.5,
            max_tokens=2000,
        )
