"""
analyze_research tool — synthesize collected research with targeted LLM analysis.

Unlike summarize (which produces a broad overview), analyze answers a specific
analytical question about the data: themes, evidence, gaps, or an overview.
No new data is collected — works entirely from what's already in the database.
"""

from typing import Literal

from agent.tools import ServiceContainer


_ANALYSIS_PROMPTS = {
    "overview": """You are a research analyst. Given a research question and a set of scored community comments,
write a concise synthesis (300-500 words) that directly answers the question.
Lead with the most important finding. Use specific evidence from the comments.
Do not repeat the question back. Be direct and concrete.""",

    "themes": """You are a research analyst. Given a research question and a set of scored community comments,
identify the 3-7 most prominent recurring themes in the data.
For each theme:
- Give it a clear 2-5 word label
- Write 1-2 sentences describing what people say about it
- Cite 1-2 specific comments that exemplify it (use author name or snippet)
Order themes by how frequently they appear across comments.""",

    "evidence": """You are a research analyst. Given a research question and a set of scored community comments,
extract the strongest pieces of evidence — comments that most directly, specifically, and credibly
address the question. For each piece of evidence:
- Quote or closely paraphrase the key insight (1-3 sentences)
- Note the author and relevancy score
- Explain in one sentence why this comment is significant
List up to 8 pieces of evidence, strongest first.""",

    "gaps": """You are a research analyst. Given a research question and a set of scored community comments,
identify what aspects of the question are NOT well-covered by the collected data.
For each gap:
- Name the missing angle or sub-question
- Estimate how significant this gap is (high/medium/low)
- Suggest what additional research would fill it (e.g., specific subreddit, source type, refined query)
Also note any methodological limitations in the current data set.""",
}


def analyze_research(
    research_id: str,
    analysis_type: Literal["overview", "themes", "evidence", "gaps"],
    question: str = "",
    services: ServiceContainer = None,
) -> dict:
    """
    Synthesize collected research data using targeted LLM analysis.

    Args:
        research_id: The research session to analyze.
        analysis_type: The type of analysis — "overview" for a direct answer to
                       the research question, "themes" to cluster recurring topics,
                       "evidence" to surface the strongest supporting comments,
                       "gaps" to identify what the current data doesn't cover.
        question: Optional specific question to focus the analysis on. If empty,
                  uses the original research question.
    """
    record = services.storage_svc.get_research(research_id)
    if not record:
        return {"error": f"No research found with id '{research_id}'"}

    focus_question = question.strip() or record["question"]

    # Load top comments (relevancy >= 6) for a focused, high-signal prompt
    comments_raw = services.storage_svc.get_comments(research_id)
    top_comments = [
        c for c in comments_raw
        if (c.get("user_relevancy_score") or c.get("relevancy_score") or 0) >= 6
    ][:60]

    if not top_comments:
        return {
            "error": "No high-relevancy comments found (score >= 6). "
                     "Run collect_research and score_comments first, or lower your expectations."
        }

    # Format comments into a compact prompt-friendly block
    lines = [f"Research Question: {focus_question}\n\n--- Comments ({len(top_comments)}) ---\n"]
    for c in top_comments:
        relevancy = c.get("user_relevancy_score") or c.get("relevancy_score") or "?"
        body_snippet = (c.get("body") or "")[:300]
        author = c.get("author", "unknown")
        source = c.get("source", "reddit")
        lines.append(f"[score:{relevancy} | {source} | @{author}]\n{body_snippet}\n")

    user_prompt = "\n".join(lines)
    system_prompt = _ANALYSIS_PROMPTS[analysis_type]

    analysis = services.llm.complete_text(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.3,
        max_tokens=1500,
    )

    return {
        "research_id": research_id,
        "analysis_type": analysis_type,
        "focus_question": focus_question,
        "comments_analyzed": len(top_comments),
        "analysis": analysis,
    }
