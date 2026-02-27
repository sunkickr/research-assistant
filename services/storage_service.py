import csv
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional
from models.data_models import RedditThread, ScoredComment


class StorageService:
    """Handles SQLite persistence and CSV export."""

    def __init__(self, db_path: str, export_dir: str):
        self.db_path = db_path
        self.export_dir = export_dir
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        os.makedirs(export_dir, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS researches (
                    id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    summary TEXT,
                    num_threads INTEGER DEFAULT 0,
                    num_comments INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    settings_json TEXT
                );
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT NOT NULL,
                    research_id TEXT NOT NULL,
                    title TEXT,
                    subreddit TEXT,
                    score INTEGER,
                    num_comments INTEGER,
                    url TEXT,
                    permalink TEXT,
                    selftext TEXT,
                    created_utc REAL,
                    author TEXT DEFAULT '',
                    PRIMARY KEY (id, research_id),
                    FOREIGN KEY (research_id) REFERENCES researches(id)
                );
                CREATE TABLE IF NOT EXISTS comments (
                    id TEXT NOT NULL,
                    research_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    author TEXT,
                    body TEXT,
                    score INTEGER,
                    created_utc REAL,
                    depth INTEGER,
                    permalink TEXT,
                    relevancy_score INTEGER,
                    reasoning TEXT,
                    PRIMARY KEY (id, research_id),
                    FOREIGN KEY (research_id) REFERENCES researches(id)
                );
                CREATE INDEX IF NOT EXISTS idx_comments_research
                    ON comments(research_id);
                CREATE INDEX IF NOT EXISTS idx_threads_research
                    ON threads(research_id);
                CREATE INDEX IF NOT EXISTS idx_researches_created
                    ON researches(created_at DESC);
            """
            )
            # Migrations for new columns (idempotent)
            for stmt in [
                "ALTER TABLE comments ADD COLUMN user_relevancy_score INTEGER",
                "ALTER TABLE comments ADD COLUMN starred INTEGER DEFAULT 0",
                "ALTER TABLE researches ADD COLUMN archived INTEGER DEFAULT 0",
            ]:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # Column already exists

    def create_research(self, research_id: str, question: str, settings: dict):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO researches (id, question, created_at, settings_json) VALUES (?, ?, ?, ?)",
                (
                    research_id,
                    question,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(settings),
                ),
            )

    def save_threads(self, research_id: str, threads: List[RedditThread]):
        with self._get_conn() as conn:
            for t in threads:
                conn.execute(
                    """INSERT OR REPLACE INTO threads
                    (id, research_id, title, subreddit, score, num_comments, url, permalink, selftext, created_utc, author)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        t.id,
                        research_id,
                        t.title,
                        t.subreddit,
                        t.score,
                        t.num_comments,
                        t.url,
                        t.permalink,
                        t.selftext,
                        t.created_utc,
                        t.author,
                    ),
                )

    def save_scored_comments(
        self, research_id: str, comments: List[ScoredComment]
    ):
        with self._get_conn() as conn:
            for c in comments:
                conn.execute(
                    """INSERT INTO comments
                    (id, research_id, thread_id, author, body, score, created_utc, depth, permalink, relevancy_score, reasoning)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id, research_id) DO UPDATE SET
                        thread_id=excluded.thread_id, author=excluded.author, body=excluded.body,
                        score=excluded.score, created_utc=excluded.created_utc, depth=excluded.depth,
                        permalink=excluded.permalink, relevancy_score=excluded.relevancy_score,
                        reasoning=excluded.reasoning""",
                    (
                        c.id,
                        research_id,
                        c.thread_id,
                        c.author,
                        c.body,
                        c.score,
                        c.created_utc,
                        c.depth,
                        c.permalink,
                        c.relevancy_score,
                        c.reasoning,
                    ),
                )

    def get_existing_thread_ids(self, research_id: str) -> set:
        """Return the set of thread IDs already collected for this research."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id FROM threads WHERE research_id=?", (research_id,)
            ).fetchall()
            return {row["id"] for row in rows}

    def get_settings(self, research_id: str) -> dict:
        """Return the parsed settings_json for a research."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT settings_json FROM researches WHERE id=?", (research_id,)
            ).fetchone()
            if row and row["settings_json"]:
                return json.loads(row["settings_json"])
            return {}

    def update_settings(self, research_id: str, updates: dict):
        """Merge updates into settings_json."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT settings_json FROM researches WHERE id=?", (research_id,)
            ).fetchone()
            settings = json.loads(row["settings_json"] or "{}") if row else {}
            settings.update(updates)
            conn.execute(
                "UPDATE researches SET settings_json=? WHERE id=?",
                (json.dumps(settings), research_id),
            )

    def delete_thread(self, research_id: str, thread_id: str):
        """Delete a thread and all its comments from a research, then recalculate counts."""
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM threads WHERE id=? AND research_id=?",
                (thread_id, research_id),
            )
            conn.execute(
                "DELETE FROM comments WHERE thread_id=? AND research_id=?",
                (thread_id, research_id),
            )
        self.recalculate_counts(research_id)

    def recalculate_counts(self, research_id: str):
        """Recount threads and comments and update the research record."""
        with self._get_conn() as conn:
            t_count = conn.execute(
                "SELECT COUNT(*) FROM threads WHERE research_id=?", (research_id,)
            ).fetchone()[0]
            c_count = conn.execute(
                "SELECT COUNT(*) FROM comments WHERE research_id=?", (research_id,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE researches SET num_threads=?, num_comments=? WHERE id=?",
                (t_count, c_count, research_id),
            )

    def update_research_subreddits(self, research_id: str, subreddits: List[str]):
        """Store the validated subreddits used for this research in settings_json."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT settings_json FROM researches WHERE id=?", (research_id,)
            ).fetchone()
            if row:
                settings = json.loads(row["settings_json"] or "{}")
                settings["subreddits"] = subreddits
                conn.execute(
                    "UPDATE researches SET settings_json=? WHERE id=?",
                    (json.dumps(settings), research_id),
                )

    def update_research_status(
        self,
        research_id: str,
        status: str,
        num_threads: int = 0,
        num_comments: int = 0,
    ):
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE researches SET status=?, num_threads=?, num_comments=?,
                   completed_at=? WHERE id=?""",
                (
                    status,
                    num_threads,
                    num_comments,
                    datetime.now(timezone.utc).isoformat()
                    if status in ("complete", "error")
                    else None,
                    research_id,
                ),
            )

    def save_summary(self, research_id: str, summary: str):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE researches SET summary=? WHERE id=?",
                (summary, research_id),
            )

    def get_research(self, research_id: str) -> Optional[dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM researches WHERE id=?", (research_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_threads(self, research_id: str) -> List[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM threads WHERE research_id=? ORDER BY score DESC",
                (research_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def set_user_relevancy(
        self, research_id: str, comment_id: str, score: Optional[int]
    ):
        """Set or clear user relevancy score for a comment."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE comments SET user_relevancy_score=? WHERE id=? AND research_id=?",
                (score, comment_id, research_id),
            )

    def toggle_star(self, research_id: str, comment_id: str) -> int:
        """Toggle starred status. Returns new value (0 or 1)."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT starred FROM comments WHERE id=? AND research_id=?",
                (comment_id, research_id),
            ).fetchone()
            if not row:
                return 0
            new_val = 0 if row["starred"] else 1
            conn.execute(
                "UPDATE comments SET starred=? WHERE id=? AND research_id=?",
                (new_val, comment_id, research_id),
            )
            return new_val

    def get_comments(self, research_id: str) -> List[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT *,
                    CASE WHEN user_relevancy_score IS NOT NULL
                         THEN user_relevancy_score + 0.5
                         ELSE COALESCE(relevancy_score, 0)
                    END AS effective_relevancy
                   FROM comments WHERE research_id=?
                   ORDER BY effective_relevancy DESC, score DESC""",
                (research_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_history(self) -> List[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT id, question, status, num_threads, num_comments, created_at
                   FROM researches
                   WHERE (archived IS NULL OR archived = 0)
                   ORDER BY created_at DESC LIMIT 50"""
            ).fetchall()
            return [dict(r) for r in rows]

    def get_archived(self) -> List[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT id, question, status, num_threads, num_comments, created_at
                   FROM researches WHERE archived = 1
                   ORDER BY created_at DESC"""
            ).fetchall()
            return [dict(r) for r in rows]

    def archive_research(self, research_id: str):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE researches SET archived=1 WHERE id=?", (research_id,)
            )

    def unarchive_research(self, research_id: str):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE researches SET archived=0 WHERE id=?", (research_id,)
            )

    def delete_research(self, research_id: str):
        """Permanently delete a research and all its data (not CSV files)."""
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM comments WHERE research_id=?", (research_id,)
            )
            conn.execute(
                "DELETE FROM threads WHERE research_id=?", (research_id,)
            )
            conn.execute(
                "DELETE FROM researches WHERE id=?", (research_id,)
            )

    def export_csv(self, research_id: str) -> str:
        """Export scored comments as CSV. Returns file path."""
        comments = self.get_comments(research_id)
        research = self.get_research(research_id)

        # Create a safe filename from the question
        safe_question = "".join(
            c if c.isalnum() or c in " -_" else "" for c in (research or {}).get("question", "")
        )[:50].strip()
        filename = f"research_{research_id}_{safe_question}.csv"
        filepath = os.path.join(self.export_dir, filename)

        fieldnames = [
            "id",
            "thread_id",
            "author",
            "body",
            "score",
            "relevancy_score",
            "user_relevancy_score",
            "reasoning",
            "permalink",
            "depth",
            "created_utc",
            "starred",
        ]

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for c in comments:
                writer.writerow({k: c.get(k, "") for k in fieldnames})

        # Also export threads CSV
        threads = self.get_threads(research_id)
        threads_filename = f"threads_{research_id}_{safe_question}.csv"
        threads_filepath = os.path.join(self.export_dir, threads_filename)
        thread_fieldnames = [
            "id",
            "title",
            "subreddit",
            "score",
            "num_comments",
            "url",
            "permalink",
            "author",
            "created_utc",
        ]
        with open(threads_filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=thread_fieldnames)
            writer.writeheader()
            for t in threads:
                writer.writerow({k: t.get(k, "") for k in thread_fieldnames})

        return filepath
