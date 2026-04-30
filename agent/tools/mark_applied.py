"""
mark_applied tool — track job application status.

Marks a job as applied in the job search JSON file, with optional notes.
"""

import json
import os
from datetime import datetime, timezone

from agent.tools import ServiceContainer

_JOB_SEARCHES_DIR = "data/job_searches"


def mark_applied(
    search_id: str,
    job_id: str,
    notes: str = "",
    services: ServiceContainer = None,
) -> dict:
    """
    Mark a job as applied and optionally add notes.

    Args:
        search_id: The job search containing the job.
        job_id: The job to mark as applied.
        notes: Optional notes about the application (e.g., "Applied via
               website, referred by John").
    """
    path = os.path.join(_JOB_SEARCHES_DIR, f"{search_id}.json")
    if not os.path.exists(path):
        return {"error": f"No job search found with id '{search_id}'"}

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for job in data.get("jobs", []):
        if job.get("id") == job_id:
            job["applied"] = True
            job["applied_at"] = datetime.now(timezone.utc).isoformat()
            job["applied_notes"] = notes

            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            return {
                "status": "marked_applied",
                "job_id": job_id,
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "url": job.get("url", ""),
                "notes": notes,
            }

    return {"error": f"No job found with id '{job_id}' in search '{search_id}'"}
