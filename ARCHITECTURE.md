# Architecture

This document describes the engineering design of the Research Assistant application.

## System Overview

Research Assistant is a Python Flask web application that searches Reddit for comments relevant to a research question, scores them for relevancy using an LLM, and presents results in an interactive UI.

```
+------------------+       +------------------+       +------------------+
|                  |       |                  |       |                  |
|     Browser      | <---> |   Flask Server   | <---> |   Reddit API     |
|   (HTML/JS)      |       |   (Python)       |       |   (via PRAW)     |
|                  |       |                  |       |                  |
+------------------+       +--------+---------+       +------------------+
                                    |
                            +-------+-------+
                            |               |
                    +-------v----+   +------v-------+
                    |            |   |              |
                    |  OpenAI    |   |   SQLite     |
                    |  API       |   |   Database   |
                    |            |   |              |
                    +------------+   +--------------+
```

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Web Framework | Flask 3.1 | HTTP routing, template rendering |
| Templates | Jinja2 | Server-side HTML rendering |
| Frontend | Vanilla JS | Client-side interactivity |
| Reddit API | PRAW 7.8 | Reddit search and comment collection |
| LLM | OpenAI SDK (GPT-4o-mini) | Comment scoring and summarization |
| Database | SQLite3 (stdlib) | Persistent data storage |
| Config | python-dotenv | Environment variable management |
| Validation | Pydantic | LLM structured output schemas |

## Directory Structure

```
research-assistant/
├── app.py                    # Flask application, routes, pipeline orchestration
├── config.py                 # Configuration from environment variables
├── models/
│   └── data_models.py        # Python dataclasses for domain objects
├── services/
│   ├── reddit_service.py     # Reddit API wrapper
│   ├── llm_provider.py       # Abstract LLM interface + OpenAI implementation
│   ├── scoring_service.py    # Comment relevancy scoring
│   ├── summary_service.py    # Research summarization
│   └── storage_service.py    # SQLite + CSV persistence
├── templates/
│   ├── base.html             # Shared layout (nav, sidebar)
│   ├── index.html            # Landing page
│   └── results.html          # Results page
├── static/
│   ├── css/style.css         # All styles
│   └── js/
│       ├── app.js            # Form, SSE, summarize logic
│       └── tables.js         # Table rendering, sorting, filtering
└── data/                     # Runtime data (auto-created, git-ignored)
    ├── research.db           # SQLite database
    └── exports/              # CSV files
```

## Data Flow

### Research Pipeline

The core data flow when a user submits a research question:

```
1. User submits question
   │
   ├─► POST /api/research
   │   └─► Creates research record in SQLite (status: pending)
   │   └─► Spawns background thread
   │   └─► Returns research_id to browser
   │
   ├─► Browser opens SSE: GET /api/research/{id}/stream
   │
   └─► Background Thread Pipeline:
       │
       ├─► Stage 1: SEARCH
       │   └─► PRAW: subreddit("all").search(query)
       │   └─► Save threads to SQLite
       │   └─► SSE: "Found N threads"
       │
       ├─► Stage 2: COLLECT
       │   └─► For each thread: PRAW submission.comments.list()
       │   └─► Filter deleted/removed, apply per-thread cap
       │   └─► Apply total cap (750), keep highest-scored
       │   └─► SSE: "Collected N comments"
       │
       ├─► Stage 3: SCORE
       │   └─► Batch comments (20/batch)
       │   └─► For each batch: OpenAI structured output → relevancy scores
       │   └─► Save scored comments to SQLite
       │   └─► SSE: "Scoring batch N/M"
       │
       └─► Stage 4: FINALIZE
           └─► Update research status to "complete"
           └─► Export CSV files
           └─► SSE: "complete" → browser redirects to /results/{id}
```

### Summarization Flow

```
User clicks "Summarize Comments"
   │
   └─► POST /api/research/{id}/summarize
       │
       ├─► Load scored comments from SQLite
       ├─► Filter: relevancy_score >= 4
       ├─► Sort: relevancy_score * max(upvotes, 1) descending
       ├─► Take top 50 comments
       ├─► Send to OpenAI for summarization
       ├─► Save summary to SQLite
       └─► Return summary text to browser
```

## Service Layer Design

### LLM Provider Abstraction

```
              LLMProvider (ABC)
              ┌─────────────────┐
              │ complete()      │  → structured output (Pydantic model)
              │ complete_text() │  → plain text
              └────────┬────────┘
                       │
            ┌──────────┴──────────┐
            │                     │
    OpenAIProvider          (Future providers)
    ┌─────────────┐         AnthropicProvider
    │ GPT-4o-mini │         GeminiProvider
    │ or GPT-4o   │         LocalProvider
    └─────────────┘         etc.
```

To add a new LLM provider:
1. Create a class implementing `LLMProvider` in `services/llm_provider.py`
2. Implement `complete()` and `complete_text()` methods
3. Update `app.py` to instantiate the new provider based on config

### Scoring Strategy

Comments are scored in batches of 20 using OpenAI's structured output feature:

```python
# Request schema (sent to LLM):
class BatchScoreResponse(BaseModel):
    scores: List[CommentScore]

class CommentScore(BaseModel):
    comment_id: str
    relevancy_score: int   # 1-10
    reasoning: str
```

Why batch size 20:
- Small enough for reliable structured output parsing
- Large enough to minimize API call overhead
- 750 comments / 20 per batch = ~38 API calls = ~$0.02

## Database Schema

```sql
researches
├── id TEXT PRIMARY KEY
├── question TEXT NOT NULL
├── status TEXT DEFAULT 'pending'    -- pending, complete, error
├── summary TEXT
├── num_threads INTEGER
├── num_comments INTEGER
├── created_at TEXT
├── completed_at TEXT
└── settings_json TEXT               -- JSON: {max_threads, max_comments, time_filter}

threads
├── id TEXT                          -- Reddit submission ID
├── research_id TEXT (FK)
├── title TEXT
├── subreddit TEXT
├── score INTEGER                    -- Reddit score (net upvotes)
├── num_comments INTEGER
├── url TEXT
├── permalink TEXT
├── selftext TEXT                    -- Post body (truncated to 500 chars)
├── created_utc REAL
├── author TEXT
└── PRIMARY KEY (id, research_id)

comments
├── id TEXT                          -- Reddit comment ID
├── research_id TEXT (FK)
├── thread_id TEXT (FK)
├── author TEXT
├── body TEXT
├── score INTEGER                    -- Reddit score (net upvotes)
├── created_utc REAL
├── depth INTEGER                    -- Nesting depth in thread
├── permalink TEXT
├── relevancy_score INTEGER          -- AI score 1-10
├── reasoning TEXT                   -- AI explanation
└── PRIMARY KEY (id, research_id)
```

Composite primary keys `(id, research_id)` allow the same Reddit thread/comment to appear in multiple research sessions.

## Background Processing

The research pipeline runs in a Python `threading.Thread` to avoid blocking the Flask server. Communication between the background thread and the SSE endpoint uses `queue.Queue`:

```
Background Thread                    SSE Endpoint
       │                                  │
       ├─► q.put({stage, message, pct}) ──┤
       ├─► q.put({stage, message, pct}) ──┤──► yield SSE event to browser
       ├─► q.put({stage, message, pct}) ──┤
       └─► q.put(None) ──────────────────►│──► close SSE stream
```

This is appropriate for a single-user local application. For production multi-user deployment, replace with Celery + Redis or similar task queue.

## Rate Limits and Costs

### Reddit API
- 60 requests/minute with OAuth
- Typical research: ~16-26 requests (1 search + 15-25 comment fetches)
- Well within limits for single queries

### OpenAI API
- GPT-4o-mini: $0.15/1M input tokens, $0.60/1M output tokens
- Typical research: 38 scoring calls + 1 summary call
- Estimated cost: $0.01-0.03 per research query

## Frontend Architecture

The frontend uses server-rendered Jinja2 templates with vanilla JavaScript for interactivity:

```
base.html (shared layout)
├── Navigation bar
├── Main content area ({% block content %})
└── History sidebar

index.html (landing page)
├── Search form with settings
├── Progress display (hidden, shown during research)
└── Error display (hidden, shown on error)

results.html (results page)
├── Question header + metadata
├── Action buttons (Summarize, Export)
├── Summary section (expandable)
├── Threads table
└── Comments table with pagination
```

JavaScript is split into two files:
- `app.js`: Form submission, SSE progress handling, summarize button
- `tables.js`: Table rendering, sorting, filtering, pagination, comment expansion

All table data is loaded once via `GET /api/research/{id}` and manipulated client-side for sorting, filtering, and pagination (no server round-trips for table interactions).
