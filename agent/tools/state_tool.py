"""
update_state tool — persist notes, findings, and conclusions to a state file.

Each research question gets its own Markdown file in data/states/. The agent
writes to it after generating summaries or finding significant conclusions so
future sessions can pick up where the last one left off.
"""

import os
import re
from datetime import datetime
from typing import Literal

from agent.tools import ServiceContainer

_STATES_DIR = "data/states"
_VALID_SECTIONS = ("findings", "conclusions", "questions")


def _slug(question: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", question.lower().strip())[:40].strip("-")


def _state_path(question: str) -> str:
    os.makedirs(_STATES_DIR, exist_ok=True)
    return os.path.join(_STATES_DIR, f"{_slug(question)}-state.md")


def _build_initial_file(question: str, research_id: str, research_type: str, record: dict) -> str:
    num_threads = record.get("num_threads", 0)
    num_comments = record.get("num_comments", 0)
    return f"""# Research State: {question}
research_id: {research_id}
created: {datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
research_type: {research_type}

## Stats
- Threads: {num_threads}  |  Comments: {num_comments}

## Findings

## Conclusions

## Open Questions
"""


def _update_section(content: str, section_title: str, new_content: str) -> str:
    """Replace the body of a ## Section heading with new_content."""
    # Match the section header and everything until the next ## header or EOF
    pattern = rf"(## {re.escape(section_title)}\n)(.*?)(?=\n## |\Z)"
    replacement = rf"\g<1>{new_content.strip()}\n"
    updated = re.sub(pattern, replacement, content, flags=re.DOTALL)
    if updated == content:
        # Section not found — append it
        updated = content.rstrip() + f"\n\n## {section_title}\n{new_content.strip()}\n"
    return updated


def _update_stats(content: str, record: dict) -> str:
    """Refresh the Stats block with current counts."""
    num_threads = record.get("num_threads", 0)
    num_comments = record.get("num_comments", 0)
    new_stats = f"- Threads: {num_threads}  |  Comments: {num_comments}"
    return _update_section(content, "Stats", new_stats)


def update_state(
    research_id: str,
    section: Literal["findings", "conclusions", "questions"],
    content: str,
    services: ServiceContainer = None,
) -> dict:
    """
    Save a note, finding, or conclusion to the research state file.

    Args:
        research_id: The research session to update state for.
        section: Which section to write to — "findings" for key observations,
                 "conclusions" for confirmed takeaways, "questions" for open
                 follow-up questions.
        content: The text to write into that section (replaces existing content).
    """
    record = services.storage_svc.get_research(research_id)
    if not record:
        return {"error": f"No research found with id '{research_id}'"}

    question = record["question"]
    research_type = record.get("research_type", "general")
    path = _state_path(question)

    # Load or create state file
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            file_content = f.read()
    else:
        file_content = _build_initial_file(question, research_id, research_type, record)

    # Map section name to heading title
    section_title_map = {
        "findings": "Findings",
        "conclusions": "Conclusions",
        "questions": "Open Questions",
    }
    heading = section_title_map[section]
    file_content = _update_section(file_content, heading, content)
    file_content = _update_stats(file_content, record)

    with open(path, "w", encoding="utf-8") as f:
        f.write(file_content)

    return {"status": "saved", "file": path, "section": section}


def load_state(research_id: str, services: ServiceContainer = None) -> str:
    """
    Load the state file for a research session.

    Args:
        research_id: The research session to load state for.
    """
    record = services.storage_svc.get_research(research_id)
    if not record:
        return ""
    path = _state_path(record["question"])
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()
