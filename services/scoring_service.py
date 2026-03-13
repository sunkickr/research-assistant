from pydantic import BaseModel, Field
from typing import List, Optional, Callable, Tuple
from models.data_models import RedditThread, RedditComment, ScoredComment
from services.llm_provider import LLMProvider


# ===== Subreddit Suggestion =====

class SubredditSuggestions(BaseModel):
    subreddits: List[str] = Field(
        description="List of relevant subreddit names (without r/ prefix)"
    )
    search_queries: List[str] = Field(
        description="2-4 keyword-focused search query variants for finding relevant threads (no question words like 'how', 'what', 'why')"
    )


SUBREDDIT_SYSTEM_PROMPT = """You are a Reddit expert. Given a research question, return two things:
1. subreddits: The most relevant subreddits to search for useful answers. Return only subreddit names that actually exist on Reddit (no r/ prefix). Aim for 4-8 subreddits, prioritizing specificity over breadth.
2. search_queries: 2-4 short keyword-focused search query variants that capture different phrasings of the topic. These will be used as web search queries to find Reddit threads, so:
   - Avoid question words (how, what, why)
   - Use concrete nouns/verbs
   - If the question mentions a product or company name with a URL-style suffix (.ai, .io, .com, .co, etc.), drop the suffix in the queries — people rarely write "Keebo.ai" in Reddit posts, they write "Keebo". Also pair the bare product name with its domain context (platform names, use cases) to avoid ambiguity with common words. For example, for "What do people think of Keebo.ai?", return ["Keebo snowflake", "Keebo cost optimization", "Keebo warehouse optimizer"] not ["Keebo.ai reviews"].
   - Example for "How to save money on Databricks?": ["Databricks cost optimization", "reduce Databricks costs", "Databricks cost savings", "Databricks spend reduction"]"""


# ===== Thread Scoring =====

class ThreadScore(BaseModel):
    thread_id: str = Field(description="The Reddit thread/submission ID")
    relevancy_score: int = Field(
        description="Relevancy score from 1-10, where 10 is highly relevant",
        ge=1,
        le=10,
    )


class ThreadBatchScoreResponse(BaseModel):
    scores: List[ThreadScore]


THREAD_SCORING_SYSTEM_PROMPT = """You are a relevancy scoring assistant. You will receive a research question and a list of discussion threads and articles from various sources (Reddit, Hacker News, web articles). Score each thread 1-10 based on how likely it is to contain useful content that answers the question.

- 1-3: Clearly unrelated topic (different technology, general career advice, off-topic)
- 4-5: Tangentially related or only mentions the topic in passing
- 6-7: Related topic, may contain useful information
- 8-10: Directly addresses the question or closely related topic

Be strict: only score 6+ if the thread is genuinely likely to have answers relevant to the specific question. You MUST return a score for every thread. Use the exact thread IDs provided."""


# ===== Comment Scoring =====

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


SCORING_SYSTEM_PROMPT = """You are a relevancy scoring assistant. You will receive a research question and a batch of comments and article excerpts from various sources (Reddit, Hacker News, web articles). For each item, assign a relevancy score from 1-10:

- 1-2: Completely irrelevant (off-topic, jokes, spam, social chit-chat with no substance)
- 3-4: Tangentially related but does not address the question (e.g. clarifying questions, meta-discussion, acknowledgements)
- 5-6: Mentions the topic but provides little actionable value (vague opinions, anecdotes without specifics, general encouragement)
- 7-8: Relevant and useful — provides context, perspective, or partial answers that help understand the topic
- 9: Directly addresses the question with specific, substantive information or clear personal experience relevant to the question
- 10: ONLY for comments that directly and completely answer the research question with concrete, actionable advice, detailed technical specifics, or highly valuable first-hand experience that someone could act on immediately

MANDATORY PRE-CHECK — Named subjects (products, companies, tools, people):
Before applying the 1–10 scale, check whether the research question names a specific entity (product, company, tool, service, or person). If it does, apply these hard floors FIRST — they override all other scoring judgments:
- The comment explicitly names AND says something about that subject → score at minimum 5. This applies even if the content is a feature description, marketing copy, or a general characterization. The comment is still about the subject.
- The comment includes first-hand use ("we use it", "I tried it", "switched to X") → score at minimum 7, even if brief.
These are hard minimums. You may NOT score below the floor because the comment lacks specifics, reads like marketing copy, or doesn't directly answer the question. A researcher investigating a niche product needs every mention of it.

Reserve 10 for comments that a researcher would call out as "this is exactly what I was looking for." A comment that is merely topically related, expresses an opinion, asks a question, or discusses adjacent issues should never score above 8 even if well-written.

--- EXAMPLES (question: "How to save money on Databricks?") ---

Comment: "Hey, no need to call me out like that lol"
Score: 1 — social chit-chat, completely irrelevant

Comment: "Can you expand on what you're saying people are missing out on?"
Score: 3 — just a clarifying question, contributes nothing to answering the question

Comment: "He has a financial interest in protecting the company so take his advice with a grain of salt."
Score: 4 — commentary about a person, not about how to save money

Comment: "Databricks is expensive but worth it for large teams in my experience."
Score: 5 — topically related but vague, no actionable specifics

Comment: "We moved off Databricks entirely to save costs. There are cheaper alternatives."
Score: 7 — relevant perspective, partial answer, but lacks specifics on how

Comment: "First thing I would check is any production jobs running on all-purpose compute — move those to job clusters immediately. All-purpose compute is ~3x the cost for the same DBUs."
Score: 10 — directly answers the question with a concrete, actionable step and explains why

Comment: "Move to serverless where possible, make aggressive use of job clusters instead of all-purpose compute, look at serving data out of SQL Endpoints rather than keeping clusters alive, and audit your photon usage — it adds cost but isn't always faster for your workload."
Score: 10 — comprehensive, actionable, directly answers the question with multiple specific techniques

--- EXAMPLES (question: "What do users think about Claude AI?") ---

Comment: "AI assistant tools are getting really competitive this year."
Score: 3 — generic industry observation, does not mention Claude at all

Comment: "Claude offers a comprehensive suite of AI-powered features designed to streamline your content production workflow."
Score: 5 — explicitly names and describes Claude; even though it reads like marketing copy and does not include a user opinion, the mandatory named-entity floor applies: it names and says something about the subject, so minimum 5

Comment: "I've been using Claude for about a month. The reasoning is excellent but context windows can feel limiting for large projects."
Score: 8 — first-hand experience with specific observations (reasoning quality, context window), directly relevant to what users think

--- EXAMPLES (question: "Is Keebo AI a good product?") ---

Comment: "What is Keebo?"
Score: 2 — no useful information, just a question

Comment: "AI tools in the data warehouse space are very hit or miss."
Score: 3 — generic opinion that does not mention Keebo at all

Comment: "We use Keebo. Saves a boatload on ad-hoc warehouse costs. It is less effective on fixed workloads, but can still help there too."
Score: 8 — direct first-hand user experience naming the product; answers whether it is good with a concrete strength (ad-hoc cost savings) and an honest limitation; highly valuable for a niche product with little Reddit coverage

Comment: "Tried Keebo for 3 months. Cut our Snowflake bill by 30% on query-heavy workloads. Support was responsive and onboarding was smooth, but pricing scales steeply."
Score: 9 — specific first-hand experience with a concrete metric, directly and completely answers whether the product is worth it

--- END EXAMPLES ---

You MUST return a score for every comment in the batch. Use the exact comment IDs provided."""


class ScoringService:
    """Scores Reddit threads and comments for relevancy using an LLM."""

    def __init__(self, llm: LLMProvider, batch_size: int = 20):
        self.llm = llm
        self.batch_size = batch_size

    def suggest_subreddits(self, question: str) -> Tuple[List[str], List[str]]:
        """
        Ask the LLM to suggest relevant subreddits and search query variants.
        Returns (subreddit_names, search_queries).
        """
        try:
            response: SubredditSuggestions = self.llm.complete(
                system_prompt=SUBREDDIT_SYSTEM_PROMPT,
                user_prompt=f"Research question: {question}",
                response_model=SubredditSuggestions,
                temperature=0.2,
            )
            # Normalize subreddit names: strip whitespace, leading r/
            names = []
            for name in response.subreddits:
                name = name.strip().lstrip("r/").strip()
                if name:
                    names.append(name)
            queries = [q.strip() for q in response.search_queries if q.strip()]
            return names, queries
        except Exception:
            return [], []

    def score_threads(
        self,
        question: str,
        threads: List[RedditThread],
        min_score: int = 6,
    ) -> List[RedditThread]:
        """
        Score thread titles and descriptions for relevancy to the question.
        Returns only threads with relevancy_score >= min_score.
        All threads are scored in a single LLM call (titles are short).
        """
        if not threads:
            return []

        threads_text = "\n\n".join(
            f"[Thread ID: {t.id}]\nSource: {t.source}\nTitle: {t.title}\nCommunity: {t.subreddit}"
            + (f"\nDescription: {t.selftext[:200]}" if t.selftext.strip() else "")
            for t in threads
        )
        user_prompt = (
            f"Research Question: {question}\n\n"
            f"Threads to score:\n{threads_text}"
        )

        try:
            response: ThreadBatchScoreResponse = self.llm.complete(
                system_prompt=THREAD_SCORING_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_model=ThreadBatchScoreResponse,
            )
            score_map = {s.thread_id: s.relevancy_score for s in response.scores}
        except Exception:
            return []

        return [t for t in threads if score_map.get(t.id, 0) >= min_score]

    def score_comments(
        self,
        question: str,
        comments: List[RedditComment],
        progress_callback: Optional[Callable] = None,
    ) -> List[ScoredComment]:
        """Score all comments in batches. Returns ScoredComment objects.
        progress_callback(batch_num, total_batches, batch_results) is called after each batch.
        """
        scored = []
        total_batches = (len(comments) + self.batch_size - 1) // self.batch_size

        for i in range(0, len(comments), self.batch_size):
            batch_num = i // self.batch_size + 1
            batch = comments[i : i + self.batch_size]

            batch_scored = self._score_batch(question, batch)
            scored.extend(batch_scored)

            if progress_callback:
                progress_callback(batch_num, total_batches, batch_scored)

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
                    relevancy_score=score_data.relevancy_score if score_data else None,
                    reasoning=score_data.reasoning
                    if score_data
                    else "Not scored — API timeout or error",
                    source=comment.source,
                )
            )
        return results
