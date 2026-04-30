"""
create_job_search tool — create a new job search profile.

Entry point for users wanting to search for jobs. Creates a JSON file in
data/job_searches/ with the user's preferences and optional resume text.
"""

import json
import os
import uuid
from datetime import datetime, timezone

from agent.tools import ServiceContainer

_JOB_SEARCHES_DIR = "data/job_searches"


def create_job_search(
    title: str,
    description: str = "",
    experience_level: str = "",
    skills: list = None,
    locations: list = None,
    resume_text: str = "",
    resume_file: str = "",
    exclude_companies: list = None,
    services: ServiceContainer = None,
) -> dict:
    """
    Create a new job search profile describing the user's ideal role.

    Args:
        title: Desired job title or role (e.g., "Senior Backend Engineer").
        description: Free-text description of the ideal role, industry, or
                     company type the user is interested in.
        experience_level: Seniority level — "junior", "mid", "senior",
                          "staff", "principal", or "lead".
        skills: Key skills or technologies to match against
                (e.g., ["Python", "Kubernetes", "ML"]).
        locations: Preferred work locations or "remote"
                   (e.g., ["Remote", "San Francisco", "New York"]).
        resume_text: Optional plain-text resume. Provides richer context
                     for AI job matching.
        resume_file: Path to a text or markdown file containing the resume.
                     Use this instead of resume_text for multi-line resumes.
        exclude_companies: Company slugs to skip during searches
                           (e.g., ["mongodb", "meta"]).
    """
    os.makedirs(_JOB_SEARCHES_DIR, exist_ok=True)

    # Read resume from file if provided
    if resume_file and not resume_text:
        try:
            with open(os.path.expanduser(resume_file), "r", encoding="utf-8") as f:
                resume_text = f.read()
        except (OSError, IOError) as exc:
            return {"error": f"Could not read resume file: {exc}"}

    search_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()

    profile = {
        "title": title,
        "description": description,
        "experience_level": experience_level,
        "skills": skills or [],
        "locations": locations or [],
        "resume_text": resume_text,
        "exclude_companies": exclude_companies or [],
    }

    data = {
        "id": search_id,
        "created_at": now,
        "updated_at": now,
        "profile": profile,
        "search_history": [],
        "jobs": [],
    }

    path = os.path.join(_JOB_SEARCHES_DIR, f"{search_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return {
        "search_id": search_id,
        "title": title,
        "status": "created",
        "file": path,
        "has_resume": bool(resume_text),
    }
