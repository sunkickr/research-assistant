"""
save_job_search tool — update an existing job search profile.

Merges only the fields the user provides, leaving everything else intact.
"""

import json
import os
from datetime import datetime, timezone

from agent.tools import ServiceContainer

_JOB_SEARCHES_DIR = "data/job_searches"


def _load_search(search_id: str) -> tuple:
    """Load a job search JSON file. Returns (data_dict, file_path) or (None, path)."""
    path = os.path.join(_JOB_SEARCHES_DIR, f"{search_id}.json")
    if not os.path.exists(path):
        return None, path
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f), path


def save_job_search(
    search_id: str,
    title: str = "",
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
    Update an existing job search profile with new preferences or resume text.

    Args:
        search_id: The job search to update.
        title: Updated desired job title (leave empty to keep current).
        description: Updated role description (leave empty to keep current).
        experience_level: Updated experience level (leave empty to keep current).
        skills: Updated skills list (leave empty to keep current).
        locations: Updated location preferences (leave empty to keep current).
        resume_text: Updated resume text (leave empty to keep current).
        resume_file: Path to a text or markdown file containing the resume.
                     Use this instead of resume_text for multi-line resumes.
        exclude_companies: Company slugs to exclude from future searches
                           (e.g., ["mongodb", "meta"]). Replaces the current list.
    """
    # Read resume from file if provided
    if resume_file and not resume_text:
        try:
            with open(os.path.expanduser(resume_file), "r", encoding="utf-8") as f:
                resume_text = f.read()
        except (OSError, IOError) as exc:
            return {"error": f"Could not read resume file: {exc}"}

    data, path = _load_search(search_id)
    if data is None:
        return {"error": f"No job search found with id '{search_id}'"}

    profile = data.get("profile", {})
    updated_fields = []

    if title:
        profile["title"] = title
        updated_fields.append("title")
    if description:
        profile["description"] = description
        updated_fields.append("description")
    if experience_level:
        profile["experience_level"] = experience_level
        updated_fields.append("experience_level")
    if skills is not None:
        profile["skills"] = skills
        updated_fields.append("skills")
    if locations is not None:
        profile["locations"] = locations
        updated_fields.append("locations")
    if resume_text:
        profile["resume_text"] = resume_text
        updated_fields.append("resume_text")
    if exclude_companies is not None:
        profile["exclude_companies"] = exclude_companies
        updated_fields.append("exclude_companies")

    if not updated_fields:
        return {"search_id": search_id, "status": "no_changes", "message": "No fields provided to update."}

    data["profile"] = profile
    data["updated_at"] = datetime.now(timezone.utc).isoformat()

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return {
        "search_id": search_id,
        "status": "updated",
        "fields_updated": updated_fields,
    }
