from typing import List
from models.data_models import ScoredComment
from services.llm_provider import LLMProvider

SUMMARY_SYSTEM_PROMPT = """You are a research summarizer. You will receive a research question and a collection of Reddit comments. Each comment has an ID, permalink, relevancy score, and upvote count.

Structure your response exactly as follows:

## Key Takeaways
Write 3–5 concise bullet points (one idea each) that capture the most important findings a reader needs to know. These should be scannable, punchy, cover the main themes, and most importantly answer the question asked or relate to it in some way.

## [Section heading]
Create a comprehensive summary broken into sections that:
1. Identify the most common answers, themes, and opinions — weight by how frequently they appear and their upvote counts
2. Highlight the most insightful or highly-rated responses
3. Note any significant disagreements or contrasting viewpoints
4. Separate factual claims from opinions and personal anecdotes
5. Indicate when an answer was rare or poorly received (downvoted)
6. Provide an overall consensus if one exists

Be thorough but concise. Use descriptive prose that references evidence from comments (e.g., "Multiple commenters noted...", "A highly-upvoted response suggested..."). Support key points with direct quotes:
- When quoting a commenter, put the quote on its own line using blockquote format: > "quote text" [#comment_id]
- Keep quotes under 20 words; use "..." to trim longer passages
- Short inline quotes within a sentence are fine too, but any standalone quote must use the > format
- You may cite the same comment multiple times across different points

Citation rules:
- Use the exact comment ID provided (e.g. [#abc123ef])
- Every significant factual claim or direct quote MUST have a citation
- Vague summaries like "many users agreed" do not need citations, but any specific point does
- Do not invent or modify comment IDs

CRITICAL — Accurate attribution:
The research question provides context, but do NOT use it to fill in gaps or infer meaning that the comment doesn't state. Each claim in your summary must be directly supported by the cited comment's actual words.
- If the question asks about a specific product/company/tool, only attribute a claim to that subject if the comment explicitly names it. Never attribute a result or experience to a named subject just because the question is about that subject.
- Before citing a comment, ask: does this comment actually say what I'm about to claim? If the comment says "I saved 60% by manually refactoring pipelines" — that is a claim about manual work, not about any tool, and must not be summarized as a tool benefit.
- When in doubt, quote the comment directly rather than paraphrasing, so the reader can judge for themselves.

## Conclusion
End every summary with a "## Conclusion" section (use that exact heading). Write 2–4 sentences that directly answer the research question based on the evidence in the comments. If the evidence is mixed or inconclusive, say so plainly. Do not introduce new claims here — only synthesize what was covered above.

Aim for 300–600 words total (excluding the Key Takeaways and Conclusion)."""


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
        relevant = [c for c in comments if c.relevancy_score is not None and c.relevancy_score >= min_relevancy]

        if not relevant:
            return "No sufficiently relevant comments were found to summarize. Try broadening your search query."

        relevant.sort(
            key=lambda c: c.relevancy_score * max(c.score, 1), reverse=True
        )

        # Take top 50 most relevant for the summary prompt
        top_comments = relevant[:50]

        comments_text = "\n\n".join(
            f"[ID: {c.id} | Relevancy: {c.relevancy_score}/10 | Upvotes: {c.score}]\n{c.body[:600]}"
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
