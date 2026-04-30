"""
JobSearchService — ATS public API clients, job normalization, and LLM scoring.

Hits Greenhouse, Lever, and Ashby career page APIs to fetch published jobs,
normalizes them into a common schema, filters by recency, and scores them
against a user's job search profile using the LLM.

All three APIs are public, free, and require no authentication.
"""

import json
import logging
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable, List, Optional

import requests
from pydantic import BaseModel, Field

from services.llm_provider import LLMProvider

try:
    from openinference.instrumentation import using_tags
except ImportError:
    from contextlib import contextmanager

    @contextmanager
    def using_tags(tags):
        yield


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models for LLM structured scoring
# ---------------------------------------------------------------------------


class JobScore(BaseModel):
    job_id: str = Field(description="The job ID being scored")
    relevancy_score: int = Field(
        ge=1, le=10,
        description="How well this job matches the candidate profile (1=poor, 10=perfect)"
    )
    reasoning: str = Field(description="Brief explanation of the score")


class JobBatchScoreResponse(BaseModel):
    scores: List[JobScore]


# ---------------------------------------------------------------------------
# Scoring prompt
# ---------------------------------------------------------------------------

JOB_SCORING_SYSTEM_PROMPT = """You are a job matching assistant. You will receive a candidate profile and a batch of job postings. Score each job 1-10 based on how well it matches the candidate.

Scoring criteria:
- 1-3: Clearly wrong fit — different field, wrong seniority, unrelated skills
- 4-5: Tangentially related — some overlap but significant mismatches in level, skills, or domain
- 6-7: Reasonable match — relevant field and some skill overlap, worth considering
- 8-9: Strong match — aligns well on title, skills, seniority, and domain
- 10: Perfect match — exactly what the candidate is looking for

Consider: job title vs desired title, required skills vs candidate skills, seniority level,
location compatibility, and industry/domain alignment.

You MUST return a score for every job in the batch. Use the exact job IDs provided."""


# ---------------------------------------------------------------------------
# ATS API endpoints
# ---------------------------------------------------------------------------

_GREENHOUSE_JOBS_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
_LEVER_POSTINGS_URL = "https://api.lever.co/v0/postings/{slug}"
_ASHBY_JOB_BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

_REQUEST_TIMEOUT = 10  # seconds per company API call
_MAX_WORKERS = 5  # parallel fetch threads


class JobSearchService:
    """
    Fetches, normalizes, and scores job postings from ATS public APIs.

    Loaded once at startup via ServiceContainer. Company slug lists are
    read from JSON files in the configured directory.
    """

    def __init__(self, llm: LLMProvider, company_lists_dir: str = "data/company_lists"):
        self.llm = llm
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ResearchAssistant/1.0"})
        self.companies = self._load_company_lists(company_lists_dir)

    # ------------------------------------------------------------------
    # Company list management
    # ------------------------------------------------------------------

    def _load_company_lists(self, directory: str) -> dict[str, list[str]]:
        """Load {ats_name: [slugs]} from JSON files in directory."""
        result = {}
        for ats in ("greenhouse", "lever", "ashby"):
            path = os.path.join(directory, f"{ats}.json")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    slugs = json.load(f)
                # Deduplicate while preserving order
                seen = set()
                unique = []
                for s in slugs:
                    if s not in seen:
                        seen.add(s)
                        unique.append(s)
                result[ats] = unique
            else:
                result[ats] = []
        return result

    def get_company_count(self, ats: str) -> int:
        return len(self.companies.get(ats, []))

    def add_companies(self, ats: str, slugs: list[str], lists_dir: str = "data/company_lists") -> int:
        """Append new slugs to an ATS company list and persist to disk."""
        existing = set(self.companies.get(ats, []))
        new_slugs = [s for s in slugs if s not in existing]
        if not new_slugs:
            return 0
        self.companies.setdefault(ats, []).extend(new_slugs)
        path = os.path.join(lists_dir, f"{ats}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.companies[ats], f, indent=2)
        return len(new_slugs)

    # ------------------------------------------------------------------
    # ATS API fetchers
    # ------------------------------------------------------------------

    def fetch_greenhouse_jobs(self, slug: str) -> list[dict]:
        """Fetch all published jobs from a Greenhouse board."""
        try:
            url = _GREENHOUSE_JOBS_URL.format(slug=slug)
            resp = self.session.get(url, timeout=_REQUEST_TIMEOUT, params={"content": "true"})
            if resp.status_code != 200:
                return []
            data = resp.json()
            return data.get("jobs", [])
        except Exception:
            return []

    def fetch_lever_jobs(self, slug: str) -> list[dict]:
        """Fetch all published jobs from a Lever board."""
        try:
            url = _LEVER_POSTINGS_URL.format(slug=slug)
            resp = self.session.get(url, timeout=_REQUEST_TIMEOUT, params={"mode": "json"})
            if resp.status_code != 200:
                return []
            data = resp.json()
            if isinstance(data, list):
                return data
            return []
        except Exception:
            return []

    def fetch_ashby_jobs(self, slug: str) -> list[dict]:
        """Fetch all published jobs from an Ashby board."""
        try:
            url = _ASHBY_JOB_BOARD_URL.format(slug=slug)
            resp = self.session.get(
                url, timeout=_REQUEST_TIMEOUT,
                params={"includeCompensation": "true"},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            return data.get("jobs", [])
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Normalization — ATS-specific → common schema
    # ------------------------------------------------------------------

    def _strip_html(self, text: str) -> str:
        """Remove HTML tags from a string."""
        if not text:
            return ""
        return re.sub(r"<[^>]+>", "", text).strip()

    def normalize_greenhouse(self, raw: dict, company_slug: str) -> dict:
        location = raw.get("location", {})
        location_name = location.get("name", "") if isinstance(location, dict) else str(location)

        # Extract description text
        content = raw.get("content", "")
        description = self._strip_html(content)

        return {
            "id": f"gh_{company_slug}_{raw.get('id', '')}",
            "title": raw.get("title", ""),
            "company": company_slug,
            "ats": "greenhouse",
            "location": location_name,
            "url": raw.get("absolute_url", ""),
            "posted_date": raw.get("updated_at", ""),
            "description": description,
            "compensation": None,
            "departments": [d.get("name", "") for d in raw.get("departments", [])],
        }

    def normalize_lever(self, raw: dict, company_slug: str) -> dict:
        categories = raw.get("categories", {})
        created_ms = raw.get("createdAt", 0)
        posted_date = ""
        if created_ms:
            posted_date = datetime.fromtimestamp(
                created_ms / 1000, tz=timezone.utc
            ).isoformat()

        return {
            "id": f"lv_{company_slug}_{raw.get('id', '')}",
            "title": raw.get("text", ""),
            "company": company_slug,
            "ats": "lever",
            "location": categories.get("location", ""),
            "url": raw.get("hostedUrl", ""),
            "posted_date": posted_date,
            "description": raw.get("descriptionPlain", "") or self._strip_html(raw.get("description", "")),
            "compensation": None,
            "departments": [categories.get("team", "")] if categories.get("team") else [],
        }

    def normalize_ashby(self, raw: dict, company_slug: str) -> dict:
        location = raw.get("location", "")
        if isinstance(location, dict):
            location = location.get("name", "")

        compensation = raw.get("compensationTierSummary") or raw.get("compensation")

        return {
            "id": f"ab_{company_slug}_{raw.get('id', '')}",
            "title": raw.get("title", ""),
            "company": company_slug,
            "ats": "ashby",
            "location": location,
            "url": raw.get("jobUrl", ""),
            "posted_date": raw.get("publishedAt", ""),
            "description": self._strip_html(raw.get("descriptionHtml", "")),
            "compensation": compensation,
            "departments": [raw.get("department", "")] if raw.get("department") else [],
        }

    def normalize_job(self, raw: dict, ats: str, company_slug: str) -> dict:
        if ats == "greenhouse":
            return self.normalize_greenhouse(raw, company_slug)
        elif ats == "lever":
            return self.normalize_lever(raw, company_slug)
        elif ats == "ashby":
            return self.normalize_ashby(raw, company_slug)
        return {}

    # ------------------------------------------------------------------
    # Recency filtering
    # ------------------------------------------------------------------

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse an ISO 8601-ish date string into a datetime."""
        if not date_str:
            return None
        try:
            # Handle various ISO formats
            cleaned = date_str.replace("Z", "+00:00")
            return datetime.fromisoformat(cleaned)
        except (ValueError, TypeError):
            return None

    def filter_recent(self, jobs: list[dict], max_age_hours: int = 48) -> list[dict]:
        """Keep only jobs posted/updated within max_age_hours."""
        if max_age_hours <= 0:
            return jobs
        cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)
        result = []
        for job in jobs:
            dt = self._parse_date(job.get("posted_date", ""))
            if dt and dt.timestamp() >= cutoff:
                result.append(job)
        return result

    # ------------------------------------------------------------------
    # Parallel fetching
    # ------------------------------------------------------------------

    def fetch_all_jobs(
        self,
        ats: str,
        max_companies: int = 50,
        exclude_slugs: Optional[set] = None,
        include_slugs: Optional[list] = None,
        progress_callback: Optional[Callable] = None,
    ) -> list[dict]:
        """
        Fetch jobs from up to max_companies on a given ATS platform.

        Companies are shuffled so repeated calls with max_companies < total
        cover different companies over time. exclude_slugs removes companies
        from consideration; include_slugs adds extra companies beyond the
        bundled list.
        """
        slugs = list(self.companies.get(ats, []))

        # Remove excluded companies
        if exclude_slugs:
            slugs = [s for s in slugs if s.lower() not in exclude_slugs]

        random.shuffle(slugs)
        slugs = slugs[:max_companies]

        # Append any ad-hoc includes (deduplicated, not subject to max_companies cap)
        if include_slugs:
            existing = set(s.lower() for s in slugs)
            for s in include_slugs:
                if s.lower() not in existing and (not exclude_slugs or s.lower() not in exclude_slugs):
                    slugs.append(s)
                    existing.add(s.lower())

        if not slugs:
            return []

        fetch_fn = {
            "greenhouse": self.fetch_greenhouse_jobs,
            "lever": self.fetch_lever_jobs,
            "ashby": self.fetch_ashby_jobs,
        }.get(ats)

        if not fetch_fn:
            return []

        all_jobs = []
        completed = 0
        total = len(slugs)

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
            future_to_slug = {
                executor.submit(fetch_fn, slug): slug
                for slug in slugs
            }
            for future in as_completed(future_to_slug):
                slug = future_to_slug[future]
                completed += 1
                try:
                    raw_jobs = future.result()
                    for raw in raw_jobs:
                        normalized = self.normalize_job(raw, ats, slug)
                        if normalized and normalized.get("title"):
                            all_jobs.append(normalized)
                except Exception:
                    pass

                if progress_callback and completed % 5 == 0:
                    progress_callback(completed, total)

        return all_jobs

    # ------------------------------------------------------------------
    # LLM scoring
    # ------------------------------------------------------------------

    def score_jobs(
        self,
        jobs: list[dict],
        profile: dict,
        batch_size: int = 15,
        progress_callback: Optional[Callable] = None,
    ) -> list[dict]:
        """
        Score jobs for relevancy against a user's profile using the LLM.

        Returns the same job dicts with `relevancy_score` and `reasoning` added.
        """
        if not jobs:
            return []

        # Build the profile summary for the prompt
        profile_lines = []
        if profile.get("title"):
            profile_lines.append(f"Desired role: {profile['title']}")
        if profile.get("experience_level"):
            profile_lines.append(f"Experience level: {profile['experience_level']}")
        if profile.get("skills"):
            profile_lines.append(f"Key skills: {', '.join(profile['skills'])}")
        if profile.get("locations"):
            profile_lines.append(f"Preferred locations: {', '.join(profile['locations'])}")
        if profile.get("description"):
            profile_lines.append(f"Additional preferences: {profile['description']}")
        if profile.get("resume_text"):
            # Truncate resume to keep prompt manageable
            resume_snippet = profile["resume_text"][:2000]
            profile_lines.append(f"Resume summary:\n{resume_snippet}")

        profile_block = "\n".join(profile_lines)

        scored = []
        total_batches = (len(jobs) + batch_size - 1) // batch_size

        for batch_num in range(total_batches):
            start = batch_num * batch_size
            batch = jobs[start : start + batch_size]

            # Format jobs for the prompt
            job_lines = []
            for j in batch:
                desc_snippet = (j.get("description") or "")[:500]
                comp = f" | Compensation: {j['compensation']}" if j.get("compensation") else ""
                dept = f" | Dept: {', '.join(j['departments'])}" if j.get("departments") else ""
                job_lines.append(
                    f"[Job ID: {j['id']}] {j['title']} at {j['company']} ({j['ats']})\n"
                    f"Location: {j.get('location', 'Not specified')}{comp}{dept}\n"
                    f"Description: {desc_snippet}\n"
                )

            user_prompt = (
                f"=== Candidate Profile ===\n{profile_block}\n\n"
                f"=== Job Postings ({len(batch)}) ===\n\n"
                + "\n---\n".join(job_lines)
            )

            try:
                with using_tags(["agent:job_search", "task:job_scoring"]):
                    result: JobBatchScoreResponse = self.llm.complete(
                        system_prompt=JOB_SCORING_SYSTEM_PROMPT,
                        user_prompt=user_prompt,
                        response_model=JobBatchScoreResponse,
                        temperature=0.1,
                    )

                # Map scores back to jobs
                score_map = {s.job_id: s for s in result.scores}
                for j in batch:
                    s = score_map.get(j["id"])
                    if s:
                        j["relevancy_score"] = s.relevancy_score
                        j["reasoning"] = s.reasoning
                    else:
                        j["relevancy_score"] = None
                        j["reasoning"] = "Not scored — missing from LLM response"
                    scored.append(j)

            except Exception as exc:
                logger.warning(f"Scoring batch {batch_num + 1} failed: {exc}")
                for j in batch:
                    j["relevancy_score"] = None
                    j["reasoning"] = f"Not scored — API error: {str(exc)[:100]}"
                    scored.append(j)

            if progress_callback:
                progress_callback(batch_num + 1, total_batches)

        return scored
