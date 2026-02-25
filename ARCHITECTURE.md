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
                    +---------------+---------------+
                    |               |               |
            +-------v----+  +------v-------+  +----v--------+
            |            |  |              |  |             |
            |  OpenAI    |  |   SQLite     |  | DuckDuckGo  |
            |  API       |  |   Database   |  | Web Search  |
            |            |  |              |  |  (ddgs)     |
            +------------+  +--------------+  +-------------+
```

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Web Framework | Flask 3.1 | HTTP routing, template rendering |
| Templates | Jinja2 | Server-side HTML rendering |
| Frontend | Vanilla JS | Client-side interactivity |
| Reddit API | PRAW 7.8 | Reddit search and comment collection |
| Web Search | ddgs | DuckDuckGo search for supplementary thread discovery |
| LLM | OpenAI SDK (GPT-4o-mini) | Subreddit suggestion, thread filtering, comment scoring, summarization |
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
│   ├── reddit_service.py     # Reddit API wrapper (search, collect, validate subreddits)
│   ├── web_search_service.py # DuckDuckGo web search for supplementary thread discovery
│   ├── llm_provider.py       # Abstract LLM interface + OpenAI implementation
│   ├── scoring_service.py    # Subreddit suggestion, thread scoring, comment scoring
│   ├── summary_service.py    # Research summarization
│   └── storage_service.py    # SQLite + CSV persistence
├── templates/
│   ├── base.html             # Shared layout (nav, sidebar)
│   ├── index.html            # Landing page
│   └── results.html          # Results page
├── static/
│   ├── css/style.css         # All styles
│   └── js/
│       ├── app.js            # Form, SSE, summarize, find-more, add-thread logic
│       └── tables.js         # Table rendering, sorting, filtering, pagination
└── data/                     # Runtime data (auto-created, git-ignored)
    ├── research.db           # SQLite database
    └── exports/              # CSV files
```

## Data Flow

### Research Pipeline

The core data flow when a user submits a research question:

```
1. User submits question (+ optional seed_urls)
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
       ├─► [If seed_urls provided] SEED THREAD PATH:
       │   └─► Fetch each URL's thread directly via PRAW (skip stages 1–2)
       │   └─► Save threads to SQLite
       │   └─► SSE: "Loaded N thread(s) — collecting comments..."
       │   └─► ─── jump to Stage 3 ───►
       │
       ├─► [Normal path] Stage 1: SUBREDDIT DISCOVERY
       │   └─► LLM: suggest 4-8 relevant subreddits + 2-4 search query variants
       │   └─► PRAW: validate each subreddit exists
       │   └─► Store validated subreddits in settings_json
       │   └─► SSE: "Searching in r/sub1, r/sub2, ..."
       │
       ├─► [Normal path] Stage 2: THREAD DISCOVERY (two sources, merged)
       │   ├─► Source A: PRAW search within validated subreddits (or r/all)
       │   └─► Source B: DuckDuckGo web search using LLM-generated keyword queries
       │       └─► Broad site:reddit.com search + per-subreddit searches
       │       └─► Extract thread IDs from URLs, fetch via PRAW
       │   └─► Merge and deduplicate by thread ID
       │   └─► SSE: "Found N threads total (M from web search)"
       │
       ├─► [Normal path] Stage 2a: THREAD FILTERING
       │   └─► LLM: score each thread title/description 1-10
       │   └─► Keep only threads scoring >= 6
       │   └─► Save relevant threads to SQLite
       │   └─► SSE: "N of M threads are relevant"
       │
       ├─► Stage 3: COMMENT COLLECTION
       │   └─► For each relevant thread: PRAW submission.comments.list()
       │   └─► Filter deleted/removed; sort all collected comments by Reddit score desc
       │   └─► Apply per-thread cap (default 100), keeping highest-scored comments
       │   └─► Apply total cap (750 across all threads), keeping highest-scored
       │   └─► SSE: "Collecting comments from thread N/M"
       │
       ├─► Stage 4: COMMENT SCORING
       │   └─► Batch comments (20/batch)
       │   └─► For each batch: OpenAI structured output → relevancy scores 1-10
       │   └─► Failed batches (timeout/error): comments saved with relevancy_score = null
       │   └─► Save scored comments to SQLite
       │   └─► SSE: "Scoring batch N/M"
       │
       └─► Stage 5: FINALIZE
           └─► Update research status to "complete"
           └─► Export CSV file
           └─► SSE: "complete" → browser redirects to /results/{id}
```

### Find More Comments Flow

```
User clicks "Find More Comments"
   │
   ├─► POST /api/research/{id}/expand
   │   └─► Picks next unused sort strategy (top → new → controversial → hot)
   │   └─► Spawns background thread
   │
   ├─► Browser opens SSE: GET /api/research/{id}/expand/stream
   │
   └─► Expand Pipeline:
       ├─► Reddit API search with next sort strategy
       ├─► DuckDuckGo web search with "{question} {sort}"
       ├─► Merge, deduplicate against already-collected thread IDs
       ├─► LLM thread scoring → keep relevant new threads
       ├─► Collect + score comments from new threads
       ├─► Save to SQLite, recalculate counts, export CSV
       └─► SSE: "complete" → browser reloads tables
```

### Add Thread Manually Flow

```
User pastes a Reddit URL and clicks "Add Thread"
   │
   ├─► POST /api/research/{id}/add-thread
   │   └─► Parse thread ID from URL (supports reddit.com and redd.it links)
   │   └─► Check if thread already exists in this research
   │       └─► If exists: return {already_exists: true, message: "..."}
   │   └─► Spawns background thread
   │
   ├─► Browser opens SSE: GET /api/research/{id}/add-thread/stream
   │
   └─► Add-Thread Pipeline:
       ├─► Fetch full thread details via PRAW
       ├─► Save thread to SQLite
       ├─► Collect comments (up to max_comments_per_thread)
       ├─► Score comments via LLM
       ├─► Save to SQLite, recalculate counts, export CSV
       └─► SSE: "complete" → browser reloads tables
```

### Remove Thread Flow

```
User clicks "Remove" on a thread row
   │
   ├─► browser confirm() dialog — warns thread + all comments will be deleted
   │   └─► If cancelled: no action
   │
   └─► DELETE /api/research/{id}/threads/{thread_id}
       ├─► DELETE FROM threads WHERE id=? AND research_id=?
       ├─► DELETE FROM comments WHERE thread_id=? AND research_id=?
       ├─► recalculate_counts() — updates num_threads / num_comments on research record
       └─► Browser clears active thread filter (if it was the deleted thread)
       └─► loadResults() — reloads both tables from the API
```

### Summarization Flow

```
User clicks "Summarize Comments"
   │
   └─► POST /api/research/{id}/summarize
       ├─► Load scored comments from SQLite
       ├─► Filter: relevancy_score >= 4
       ├─► Sort: relevancy_score * max(upvotes, 1) descending
       ├─► Take top 50 comments
       ├─► Send to OpenAI for summarization
       ├─► Save summary to SQLite
       └─► Return summary text to browser
```

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Landing page |
| GET | `/results/<id>` | Results page |
| POST | `/api/research` | Start a new research pipeline |
| GET | `/api/research/<id>/stream` | SSE: research progress |
| GET | `/api/research/<id>` | Get threads + comments JSON |
| POST | `/api/research/<id>/summarize` | Generate AI summary |
| GET | `/api/research/<id>/export` | Download CSV |
| GET | `/api/history` | List past researches |
| POST | `/api/research/<id>/expand` | Start "Find More Comments" expansion |
| GET | `/api/research/<id>/expand/stream` | SSE: expand progress |
| GET | `/api/research/<id>/expand/status` | Check if expansion is still possible |
| POST | `/api/research/<id>/add-thread` | Add a specific thread by URL |
| GET | `/api/research/<id>/add-thread/stream` | SSE: add-thread progress |
| DELETE | `/api/research/<id>/threads/<thread_id>` | Remove a thread and its comments |

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

### Scoring Service Responsibilities

`ScoringService` handles four distinct LLM operations:

| Method | Input | Output | LLM calls |
|--------|-------|--------|-----------|
| `suggest_subreddits()` | question | subreddit names + search query variants | 1 |
| `score_threads()` | question + thread list | filtered thread list (score ≥ 6) | 1 |
| `score_comments()` | question + comment list | scored comment list | N/20 batches |

### Web Search Strategy

Two-pass DuckDuckGo search for maximum coverage:
1. **Broad pass**: `"{query}" site:reddit.com` — finds threads across all subreddits
2. **Per-subreddit pass**: `"{query}" site:reddit.com/r/{sub}` — targeted search within each known relevant subreddit

Multiple LLM-generated keyword query variants are used for each pass (e.g. for "How to save money on Databricks?", queries include "Databricks cost optimization", "reduce Databricks costs", "Databricks cost savings"). This overcomes the semantic mismatch where Reddit thread titles use different wording than the user's question.

A `max_total` cap (equal to `max_threads` for the main pipeline, `MAX_THREADS_LIMIT` for expand) limits how many thread IDs are collected before PRAW fetches begin. The loop breaks early once the cap is reached, keeping response time predictable even if DuckDuckGo returns many results.

The subreddit suggestion prompt instructs the LLM to drop URL-style suffixes (`.ai`, `.io`, `.com`) from product names in search queries, since users rarely write those on Reddit. It also instructs pairing bare product names with domain context words (e.g. "Keebo snowflake" rather than "Keebo.ai reviews") to reduce ambiguity with unrelated terms.

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
└── settings_json TEXT               -- JSON: {max_threads, max_comments, time_filter,
                                     --        subreddits, sorts_tried}

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
├── relevancy_score INTEGER          -- AI score 1-10; NULL if scoring failed (batch timeout/error)
├── reasoning TEXT                   -- AI explanation; "Not scored — API timeout or error" if NULL
└── PRIMARY KEY (id, research_id)
```

Composite primary keys `(id, research_id)` allow the same Reddit thread/comment to appear in multiple research sessions.

## Background Processing

Each user-triggered operation (research, expand, add-thread) runs in a `threading.Thread` with a dedicated `queue.Queue` for SSE progress events:

```
Background Thread                    SSE Endpoint
       │                                  │
       ├─► q.put({stage, message, pct}) ──┤
       ├─► q.put({stage, message, pct}) ──┤──► yield SSE event to browser
       ├─► q.put({stage, message, pct}) ──┤
       └─► q.put(None) ──────────────────►│──► close SSE stream
```

Queue dicts keyed by `research_id`:
- `progress_queues` — main research pipeline (SSE timeout: 120s)
- `expand_queues` — Find More Comments (SSE timeout: 300s — processes more threads)
- `add_thread_queues` — Add Thread manually (SSE timeout: 120s)

The OpenAI client is initialized with `timeout=60.0` so any single LLM call fails after 60 seconds rather than hanging. Failed scoring batches fall back to `null` relevancy scores rather than blocking the pipeline.

This is appropriate for a single-user local application. For production multi-user deployment, replace with Celery + Redis or similar task queue.

## Rate Limits and Costs

### Reddit API
- 60 requests/minute with OAuth
- Typical research: 1 search + up to 25 thread fetches (web search) + up to 25 comment fetches ≈ 50 requests
- Well within limits for single queries

### OpenAI API
- GPT-4o-mini: $0.15/1M input tokens, $0.60/1M output tokens
- Per research: 1 subreddit suggestion + 1 thread scoring + ~38 comment scoring batches + (optionally) 1 summary
- Estimated cost: $0.02–0.05 per research query

### DuckDuckGo
- No API key required, no rate limit tier for reasonable use
- Runs up to `len(queries) × (1 + len(subreddits))` search requests per pipeline execution

## Frontend Architecture

The frontend uses server-rendered Jinja2 templates with vanilla JavaScript for interactivity:

```
base.html (shared layout)
├── Navigation bar
├── Main content area ({% block content %})
└── History sidebar

index.html (landing page)
├── Search form with settings
│   ├── Collapsible settings panel (max threads, max comments, time range)
│   └── Collapsible seed threads panel (optional URLs to bypass discovery)
├── Progress display (hidden, shown during research)
└── Error display (hidden, shown on error)

results.html (results page)
├── Question header + metadata
├── Action buttons (Summarize, Find More Comments, Export CSV)
├── Expand progress bar (hidden, shown during expansion)
├── Add Thread input + progress bar
├── Summary section (hidden until generated)
├── Threads table (with Remove button per row)
└── Comments table with pagination
```

JavaScript is split into two files:
- `app.js`: Form submission, SSE progress, summarize, find-more, and add-thread handlers
- `tables.js`: Table rendering, sorting, filtering, pagination, comment expansion

All table data is loaded once via `GET /api/research/{id}` and manipulated client-side for sorting, filtering, and pagination (no server round-trips for table interactions).
