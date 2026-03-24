# Architecture

This document describes the engineering design of the Research Assistant application.

## System Overview

Research Assistant is a Python Flask web application that searches Reddit, Hacker News, and the web for comments and article excerpts relevant to a research question, scores them for relevancy using an LLM, and presents results in an interactive UI.

```
+------------------+       +------------------+       +------------------+
|                  |       |                  |       |                  |
|     Browser      | <---> |   Flask Server   | <---> |   Reddit API     |
|   (HTML/JS)      |       |   (Python)       |       |   (via PRAW)     |
|                  |       |                  |       |                  |
+------------------+       +--------+---------+       +------------------+
                                    |
                    +------+--------+--------+------+
                    |      |        |        |      |
            +-------v--+ +-v------+ +--v---+ +--v--------+ +--v--------+
            |          | |        | |      | |           | |           |
            | OpenAI   | | SQLite | | DDG  | | HN Algolia| |trafilatura|
            | API      | | DB     | | Web  | | API       | | + LLM    |
            |          | |        | |Search| |           | | (articles)|
            +----------+ +-------+ +------+ +-----------+ +-----------+
```

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Web Framework | Flask 3.1 | HTTP routing, template rendering |
| Templates | Jinja2 | Server-side HTML rendering |
| Frontend | Vanilla JS | Client-side interactivity |
| Reddit API | PRAW 7.8 | Reddit search and comment collection |
| Web Search | ddgs | DuckDuckGo search for supplementary thread and article discovery |
| Hacker News | requests + HN Algolia API | Story search and comment collection from Hacker News |
| Article Extraction | trafilatura | Download and extract article text from web URLs |
| LLM | OpenAI SDK (GPT-4o-mini) | Subreddit suggestion, thread filtering, comment scoring, article quote extraction, summarization |
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
│   ├── hn_service.py         # Hacker News Algolia API wrapper (story search, comment collection)
│   ├── article_service.py    # Web article extraction (trafilatura) + LLM quote extraction
│   ├── web_search_service.py # DuckDuckGo web search for thread and article discovery
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
       │   └─► For each URL: detect source (Reddit, HN, or web article)
       │   └─► Reddit: fetch via PRAW; HN: fetch via Algolia API; Web: trafilatura extract + LLM quotes
       │   └─► Save threads to SQLite
       │   └─► SSE: "Loaded N thread(s) — collecting comments..."
       │   └─► ─── jump to Stage 3 ───►
       │
       ├─► [Normal path] Stage 1: SUBREDDIT DISCOVERY
       │   └─► LLM: suggest 4-8 relevant subreddits + 2-4 search query variants
       │   └─► PRAW: validate each subreddit exists (if Reddit source enabled)
       │   └─► Store validated subreddits in settings_json
       │   └─► SSE: "Searching in r/sub1, r/sub2, ..."
       │
       ├─► [Normal path] Stage 2: THREAD DISCOVERY (multi-source, conditional)
       │   ├─► [if reddit] Source A: PRAW search + DuckDuckGo site:reddit.com search
       │   ├─► [if hackernews] Source B: HN Algolia story search using keyword queries
       │   └─► [if web] Source C: DuckDuckGo article search (non-Reddit, non-HN)
       │       └─► For each URL: trafilatura extract + LLM quote extraction (cached)
       │   └─► Merge all sources and deduplicate by thread ID
       │   └─► SSE: "Found N threads total"
       │
       ├─► [Normal path] Stage 2a: THREAD FILTERING
       │   └─► LLM: score each thread title/description 1-10 (source-aware)
       │   └─► Keep only threads scoring >= 6
       │   └─► Save relevant threads to SQLite
       │   └─► SSE: "N of M threads are relevant"
       │
       ├─► Stage 3: COMMENT COLLECTION (dispatched by source)
       │   └─► Reddit threads: PRAW submission.comments.list()
       │   └─► HN threads: Algolia items API → flatten comment tree
       │   └─► Web threads: retrieve cached LLM-extracted quotes
       │   └─► Filter deleted/removed; sort all collected comments by score desc
       │   └─► Apply per-thread cap (default 100), keeping highest-scored comments
       │   └─► Apply total cap (750 across all threads), keeping highest-scored
       │   └─► SSE: "Collecting comments from thread N/M"
       │
       ├─► Stage 3a: SAVE RAW COMMENTS
       │   └─► All collected comments saved to SQLite with relevancy_score = NULL
       │   └─► Uses upsert — safe to re-save; preserves user_relevancy_score and starred
       │   └─► Ensures comments survive even if scoring is interrupted
       │
       ├─► Stage 4: COMMENT SCORING
       │   └─► Batch comments (20/batch)
       │   └─► For each batch: OpenAI structured output → relevancy scores 1-10
       │   └─► Each scored batch saved to SQLite immediately (not buffered until end)
       │   └─► Failed batches (timeout/error): comments retain relevancy_score = null
       │   └─► SSE: "Scoring batch N/M" (includes scored comments for live table updates)
       │
       └─► Stage 5: FINALIZE
           └─► Update research status to "complete"
           └─► Export CSV file
           └─► SSE: "complete"
           Note: browser redirects to /results/{id} at first "scoring" event
           (early redirect — user sees results while scoring continues in background)
```

### Find More Comments & Articles Flow

```
User clicks "Find More Comments & Articles" (optionally configures sources via ⚙ gear dropdown)
   │
   ├─► POST /api/research/{id}/expand  { sources: ["reddit", "hackernews", "web"] }
   │   └─► Builds task list for this click:
   │       ├─► reddit: next unused sort (top → new → controversial → hot), if not exhausted
   │       ├─► hackernews: added if "hn" not in sorts_tried
   │       └─► web: added if "web" not in sorts_tried
   │   └─► Filters tasks to sources in research_sources AND in requested sources
   │   └─► Returns 400 if task list is empty (all selected sources exhausted)
   │   └─► Spawns background thread
   │
   ├─► Browser opens SSE: GET /api/research/{id}/expand/stream
   │
   └─► Expand Pipeline (runs ALL tasks in one pipeline execution):
       ├─► For each task in sorts list:
       │   ├─► Reddit (top/new/controversial/hot): PRAW search + DDG site:reddit.com search
       │   ├─► "hn": HN Algolia story search using keyword queries
       │   └─► "web": DDG article search → trafilatura extract → LLM quotes
       ├─► Merge candidates from all tasks; deduplicate against existing thread IDs
       ├─► LLM thread scoring → keep relevant new threads
       ├─► Collect + score comments from new threads (dispatched by source)
       ├─► Save to SQLite, recalculate counts, export CSV
       ├─► Append all tasks to sorts_tried in settings_json
       └─► SSE: "complete" → browser reloads tables

GET /api/research/{id}/expand/status returns:
   can_expand, next_sort, sorts_tried,
   research_sources, reddit_exhausted, hn_exhausted, web_exhausted
```

### Add Thread Manually Flow

```
User pastes a URL (Reddit, HN, or web article) and clicks "Add Thread"
   │
   ├─► POST /api/research/{id}/add-thread
   │   └─► Detect source from URL (Reddit, HN, or web article fallback)
   │   └─► Check if thread already exists in this research
   │       └─► If exists: return {already_exists: true, message: "..."}
   │   └─► Spawns background thread
   │
   ├─► Browser opens SSE: GET /api/research/{id}/add-thread/stream
   │
   └─► Add-Thread Pipeline (dispatched by source):
       ├─► Reddit: fetch via PRAW, collect comments
       ├─► HN: fetch via Algolia items API, flatten comment tree
       ├─► Web: trafilatura extract, LLM quote extraction
       ├─► Save thread to SQLite
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
User clicks "Summarize Comments" (optionally via Customize panel)
   │
   └─► POST /api/research/{id}/summarize
       ├─► Load ALL comments from SQLite
       │     (Reddit comments + HN comments + web article quotes,
       │      all stored uniformly with a source field)
       ├─► Filter: effective_relevancy >= 4
       │     (user_relevancy_score + 0.5 boost if set, else AI relevancy_score)
       ├─► Split into community (Reddit + HN) and web pools
       ├─► Community: sort by effective_relevancy * max(upvotes, 1) descending
       ├─► Web: sort by effective_relevancy descending (no upvotes to weight)
       ├─► Reserve 20% of slots for web quotes (at least 1); fill rest with community
       │     (unused web slots go to community comments)
       ├─► Take top N comments total (default 50, configurable 25–200)
       ├─► Load ALL threads from SQLite
       ├─► Build post-body preamble: threads with non-empty selftext (up to 10)
       │     (Reddit self-posts, HN Ask HN posts, web article bodies up to 1500 chars)
       │     → prepended to prompt as "primary source material"
       │     Note: web articles appear here AND as extracted quote comments above —
       │     the article body goes to preamble; LLM-extracted quotes go to the
       │     scored comment pool (score=0, ranked purely by relevancy)
       ├─► Send to OpenAI: [post-body preamble + top-N comments]
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
| GET | `/api/research/<id>/expand/status` | Check expansion status + per-source exhaustion |
| POST | `/api/research/<id>/add-thread` | Add a specific thread by URL |
| GET | `/api/research/<id>/add-thread/stream` | SSE: add-thread progress |
| DELETE | `/api/research/<id>/threads/<thread_id>` | Remove a thread and its comments |
| POST | `/api/research/<id>/comments/<comment_id>/star` | Toggle starred status |
| POST | `/api/research/<id>/comments/<comment_id>/relevancy` | Set user relevancy score |
| DELETE | `/api/research/<id>/comments/<comment_id>/relevancy` | Clear user relevancy score |
| POST | `/api/research/<id>/archive` | Archive research |
| POST | `/api/research/<id>/unarchive` | Restore archived research |
| POST | `/api/research/<id>/rescore` | Rescore comments with null relevancy scores |
| GET | `/api/research/<id>/rescore/stream` | SSE: rescore progress |
| GET | `/api/research/<id>/unscored-count` | Count of comments with null relevancy scores |
| DELETE | `/api/research/<id>` | Permanently delete research |

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
                                     --        subreddits, sorts_tried, sources}

threads
├── id TEXT                          -- Thread ID (Reddit submission ID, hn_{id}, or web_{hash})
├── research_id TEXT (FK)
├── title TEXT
├── subreddit TEXT                   -- Subreddit name, "Hacker News", or domain name
├── score INTEGER                    -- Upvotes/points
├── num_comments INTEGER
├── url TEXT
├── permalink TEXT
├── selftext TEXT                    -- Post body (truncated to 500 chars)
├── created_utc REAL
├── author TEXT
├── source TEXT DEFAULT 'reddit'     -- "reddit" | "hackernews" | "web"
└── PRIMARY KEY (id, research_id)

comments
├── id TEXT                          -- Comment ID (Reddit ID, hn_{id}, or {thread_id}_q{n})
├── research_id TEXT (FK)
├── thread_id TEXT (FK)
├── author TEXT
├── body TEXT
├── score INTEGER                    -- Upvotes/points
├── created_utc REAL
├── depth INTEGER                    -- Nesting depth in thread
├── permalink TEXT
├── relevancy_score INTEGER          -- AI score 1-10; NULL if scoring failed (batch timeout/error)
├── reasoning TEXT                   -- AI explanation; "Not scored — API timeout or error" if NULL
├── source TEXT DEFAULT 'reddit'     -- "reddit" | "hackernews" | "web"
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
- `rescore_queues` — Rescore unscored comments (SSE timeout: 120s)

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

### Hacker News Algolia API
- No API key required, 10,000 requests/hour
- Typical research: 2-4 search queries + up to 10 item fetches for comment trees

### DuckDuckGo
- No API key required, no rate limit tier for reasonable use
- Runs up to `len(queries) × (1 + len(subreddits))` search requests per pipeline execution
- Also used for web article discovery (non-Reddit, non-HN URLs)

## Frontend Architecture

The frontend uses server-rendered Jinja2 templates with vanilla JavaScript for interactivity:

```
base.html (shared layout)
├── Navigation bar
├── Main content area ({% block content %})
└── History sidebar

index.html (landing page)
├── Search form with settings
│   ├── Collapsible settings panel (source checkboxes, max threads, max comments, time range)
│   └── Collapsible seed threads panel (optional URLs to bypass discovery)
├── Progress display (hidden, shown during research)
└── Error display (hidden, shown on error)

results.html (results page)
├── Question header + metadata
├── Action buttons (Summarize | Customize, Find More Comments & Articles + ⚙ configure, Export CSV)
├── Expand progress feed (hidden, shown during expansion)
├── Add Thread input + progress feed
├── Summary section (hidden until generated)
│   ├── Numbered citations [1][2]... inline linking to source comments
│   └── Sources panel listing cited comments with author, snippet, permalink
├── Threads table (with source tabs, post body panel on click, Remove button per row)
└── Comments table with source tabs, star column, user score column, and pagination
```

JavaScript is split into two files:
- `app.js`: Form submission, SSE progress, summarize, find-more, and add-thread handlers
- `tables.js`: Table rendering, sorting, filtering, pagination, comment expansion

All table data is loaded once via `GET /api/research/{id}` and manipulated client-side for sorting, filtering, and pagination (no server round-trips for table interactions).
