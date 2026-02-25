# Features

This document tracks all features and their functionality. Update this file whenever features are added or modified.

## Thread Discovery Features

### 1. AI Subreddit Discovery
- **Description**: Before searching, uses GPT-4o-mini to identify the most relevant subreddits for the research question
- **Location**: `services/scoring_service.py` - `suggest_subreddits()`
- **Details**:
  - Returns 4–8 subreddit names specifically likely to contain useful discussions
  - Also returns 2–4 keyword-focused search query variants (e.g. "Databricks cost optimization") for web search
  - Validated against Reddit via PRAW before use — non-existent subreddits are silently dropped
  - Falls back to searching r/all if no subreddits are validated
  - Validated subreddits are stored in `settings_json` and displayed on the results page

### 2. Reddit Thread Search
- **Description**: Searches Reddit for threads matching the research question within the discovered subreddits
- **Location**: `services/reddit_service.py` - `search_threads()`
- **Details**:
  - Uses PRAW to search within validated subreddits joined with `+` (e.g. `r/databricks+r/dataengineering`), or r/all as fallback
  - Supports configurable max threads (5–25, default 15)
  - Supports time range filtering (all time, past year, month, week, day)
  - Collects thread metadata: title, subreddit, score, comment count, author, date, URL

### 3. Web Search Thread Discovery
- **Description**: Supplements Reddit API search by finding threads via DuckDuckGo `site:reddit.com` queries
- **Location**: `services/web_search_service.py` - `search_reddit_threads()`
- **Details**:
  - Uses the `ddgs` package to search DuckDuckGo with LLM-generated keyword query variants
  - Runs two passes: broad `site:reddit.com` search and per-subreddit `site:reddit.com/r/<sub>` searches
  - Extracts Reddit thread IDs from result URLs via regex, fetches full thread data via PRAW
  - Results are deduplicated by thread ID and merged with Reddit API results before scoring
  - Non-blocking: if web search fails for any reason, the pipeline continues with Reddit API results only
  - Finds highly relevant threads that Reddit's own search API often misses

### 4. AI Thread Filtering
- **Description**: Uses GPT-4o-mini to score each discovered thread for relevancy before collecting comments
- **Location**: `services/scoring_service.py` - `score_threads()`
- **Details**:
  - Runs a single LLM call covering all candidate threads (titles are short)
  - Scoring criteria: 1–3 unrelated, 4–5 tangential, 6–7 related, 8–10 directly relevant
  - Only threads scoring 6+ proceed to comment collection
  - Fallback: if the LLM call fails or no threads pass, all threads are kept
  - Prevents wasting comment collection and scoring budget on irrelevant threads

## Comment Collection & Scoring Features

### 5. Reddit Comment Collection
- **Description**: Collects comments from each relevant thread
- **Location**: `services/reddit_service.py` - `collect_comments()`
- **Details**:
  - Collects up to configurable max per thread (25–200, default 100)
  - Filters out deleted and removed comments
  - Uses `replace_more(limit=0)` to skip expanding collapsed sub-threads (performance optimization)
  - Hard cap of 750 total comments across all threads (keeps highest-scored if exceeded)
  - Collects: author, body text, score (upvotes), date, depth in thread, permalink

### 6. AI Comment Relevancy Scoring
- **Description**: Uses GPT-4o-mini to score each comment's relevancy to the research question on a 1–10 scale
- **Location**: `services/scoring_service.py` - `score_comments()`
- **Details**:
  - Batches 20 comments per LLM call for cost efficiency
  - Uses OpenAI structured outputs (Pydantic models) for reliable parsing
  - Returns a score (1–10) and brief reasoning for each comment
  - Scoring criteria: 1–2 irrelevant, 3–4 tangential, 5–6 mentions topic but little actionable value, 7–8 relevant and useful, 9 directly addresses with specific information, 10 reserved for comments that completely answer the question with concrete actionable advice
  - Score 10 is intentionally rare — the prompt includes few-shot examples calibrating the LLM to only assign 10 to comments a researcher would call out as "exactly what I was looking for"
  - Named-entity floor rule: if the question asks about a specific product/company/tool, any comment that explicitly names and discusses it scores at least 5; first-hand user experience with it ("we use it", "I tried it") scores at least 7 — prevents niche products from being under-scored due to lack of Reddit coverage
  - Two sets of few-shot examples: one for a mainstream topic (Databricks cost optimization) and one for a niche product (Keebo AI) to calibrate both scenarios
  - Truncates comment bodies to 500 chars in prompts to control costs
  - If the LLM call fails for a batch, all comments in that batch are saved with `relevancy_score = null` and reasoning "Not scored — API timeout or error"

### 7. AI Summary Generation
- **Description**: Generates a comprehensive summary of research findings
- **Location**: `services/summary_service.py`
- **Details**:
  - Triggered by user clicking "Summarize Comments" button
  - Filters to comments with relevancy score >= 4
  - Weights by `relevancy_score * max(upvotes, 1)` to surface best content
  - Sends top 50 comments to GPT-4o-mini for summarization
  - Summary includes: common themes, key insights, disagreements, fact vs opinion separation, consensus
  - Summary is saved and persists across sessions
  - Can be regenerated with "Regenerate Summary" button

## Data Features

### 8. SQLite Persistence
- **Description**: All research data persists in a local SQLite database
- **Location**: `services/storage_service.py`
- **Details**:
  - Database at `data/research.db` (auto-created on first run)
  - Tables: `researches`, `threads`, `comments`
  - Stores complete research history including threads, scored comments, and summaries
  - Indexed for fast lookups

### 9. CSV Export
- **Description**: Research results are exported as CSV files for easy use in spreadsheets
- **Location**: `services/storage_service.py` - `export_csv()`
- **Details**:
  - Comments CSV: id, thread_id, author, body, score, relevancy_score, reasoning, permalink, depth, created_utc
  - Auto-generated on research completion and after each expand/add-thread operation
  - Also downloadable via "Export CSV" button on results page
  - Saved to `data/exports/` with research ID and question in filename

### 10. LLM Provider Abstraction
- **Description**: Abstract interface allowing the AI provider to be swapped without changing application code
- **Location**: `services/llm_provider.py`
- **Details**:
  - `LLMProvider` abstract base class with two methods: `complete()` (structured output) and `complete_text()` (free text)
  - `OpenAIProvider` implementation using the OpenAI SDK
  - To swap providers: create a new class implementing `LLMProvider` and update instantiation in `app.py`

## UI Features

### 11. Landing Page
- **Description**: Home page with search form and research settings
- **Location**: `templates/index.html`
- **Details**:
  - Question input textarea
  - Collapsible settings panel: max threads (slider), max comments per thread (slider), time range (dropdown)
  - Collapsible "Add specific threads (optional)" panel for seed URLs (see Feature 19)
  - Displays collection limits to set user expectations
  - Red "Research" submit button

### 12. Real-Time Progress Display
- **Description**: Shows live progress while research is running
- **Location**: `templates/index.html`, `static/js/app.js`
- **Details**:
  - Uses Server-Sent Events (SSE) for real-time updates from the backend
  - Animated progress bar with percentage
  - Stage-by-stage status messages (finding subreddits, searching Reddit, web searching, filtering, collecting, scoring)
  - Error handling with retry option
  - Redirects to results page on completion

### 13. Threads Table
- **Description**: Sortable table displaying all discovered threads
- **Location**: `templates/results.html`, `static/js/tables.js`
- **Details**:
  - Columns: Title, Subreddit, Score, Comments, Date, Link, Remove
  - Sortable by clicking column headers (with sort direction indicators)
  - Live thread count shown above the table ("N threads collected · Click a thread to filter comments")
  - Clickable rows filter the Comments table to show only that thread's comments
  - Toggle off filter by clicking the same thread again
  - External "View" links open the thread on Reddit
  - "Remove" button per row to delete the thread and its comments (see Feature 20)

### 14. Comments Table
- **Description**: Sortable, paginated table of scored comments
- **Location**: `templates/results.html`, `static/js/tables.js`
- **Details**:
  - Columns: Relevancy Score (color-coded badge), Comment (expandable), Author, Score, Thread, Date, Link
  - Color coding: green (8–10), yellow (5–7), red (1–4), gray dash "—" for unscored (null)
  - Default sort: relevancy score descending
  - Sortable columns: relevancy, author, score, date
  - Expandable rows: click to show full comment text and AI scoring reasoning
  - Pagination: 50 comments per page with Previous/Next controls
  - Thread filtering: shows filter banner with clear option when filtering
  - "Not scored" filter: meta row below table header shows count of unscored comments as a clickable link to filter to only those; "Show all" link clears it
  - Thread filter and not-scored filter compose — both can be active simultaneously

### 15. Find More Comments
- **Description**: Expands an existing research by searching for additional threads not found in the original search
- **Location**: `app.py` - `run_expand_pipeline()`, `static/js/app.js` - `handleFindMore()`
- **Details**:
  - Cycles through four sort strategies: `top`, `new`, `controversial`, `hot`
  - Also runs DuckDuckGo web search to find threads missed by Reddit API
  - Deduplicates against already-collected threads by Reddit ID
  - New threads go through the same AI thread scoring and comment scoring pipeline
  - Shows inline progress bar with SSE updates
  - Button is automatically disabled when all strategies have been exhausted
  - New comments are merged into the existing results tables

### 16. Add Thread Manually
- **Description**: Allows users to add a specific Reddit thread by URL when they know a thread that wasn't automatically discovered
- **Location**: `app.py` - `run_add_thread_pipeline()`, `static/js/app.js` - `handleAddThread()`
- **Details**:
  - URL input field on the results page; accepts any full `reddit.com/r/.../comments/...` or `redd.it/` URL
  - Checks for duplicates before processing — shows "already processed" message if thread exists
  - Fetches thread details via PRAW, collects and scores comments using the same pipeline as automatic discovery
  - Shows inline progress bar with SSE updates while processing
  - Reloads the threads and comments tables automatically on completion
  - Pressing Enter in the URL field submits the form

### 17. History Sidebar
- **Description**: Right sidebar showing past research sessions
- **Location**: `templates/base.html`
- **Details**:
  - "New Research" button to return to landing page
  - List of past research questions with date and comment count
  - Currently active research is highlighted
  - Clickable to navigate to any past result
  - Present on all pages
  - Shows most recent 50 entries

## Architecture

### 18. Background Processing Pipeline
- **Description**: Research, expand, and add-thread operations all run in background threads to avoid blocking the web server
- **Location**: `app.py` - `run_research_pipeline()`, `run_expand_pipeline()`, `run_add_thread_pipeline()`
- **Details**:
  - `threading.Thread` for each background operation
  - `queue.Queue` per operation for SSE progress events
  - Separate queue dicts: `progress_queues`, `expand_queues`, `add_thread_queues`
  - Error handling with status tracking (pending, processing, complete, error)
  - SSE stream timeouts: 120 seconds for main research and add-thread streams; 300 seconds for the expand stream (which processes more threads)
  - OpenAI client is initialized with `timeout=60.0` to prevent any single LLM call from hanging indefinitely; failed batches fall back to null scores rather than blocking

### 19. Seed Thread URLs
- **Description**: Allows users to provide specific Reddit thread URLs on the homepage to bypass automatic thread discovery
- **Location**: `templates/index.html`, `static/js/app.js` - `handleResearchSubmit()`, `app.py` - `run_research_pipeline()`
- **Details**:
  - Collapsible "Add specific threads (optional)" panel on the landing page, below the settings panel
  - Textarea for pasting one Reddit URL per line (supports `reddit.com/r/.../comments/...` and `redd.it/` formats)
  - Explanatory copy: "For niche topics, Reddit's search can miss relevant threads you already know about"
  - When seed URLs are provided, `run_research_pipeline()` skips stages 1–2 (subreddit discovery, Reddit search, web search, LLM thread filtering) entirely
  - Instead fetches each thread directly via PRAW (same as the add-thread pipeline), then proceeds to comment collection and scoring as normal
  - Invalid or unparseable URLs are silently skipped; if none are valid, research completes with an explanatory message
  - When the textarea is left empty, the normal automatic discovery pipeline runs unchanged

### 20. Remove Thread
- **Description**: Allows users to delete a thread and all its associated comments from a research result
- **Location**: `static/js/tables.js` - `removeThread()`, `app.py` - `delete_thread()`, `services/storage_service.py` - `delete_thread()`
- **Details**:
  - "Remove" button in the last column of each thread row in the threads table
  - Clicking shows a browser `confirm()` dialog explaining the thread and all its comments will be permanently deleted
  - On confirmation, sends `DELETE /api/research/<id>/threads/<thread_id>` to the server
  - Server deletes from both `threads` and `comments` tables, then calls `recalculate_counts()` to keep `num_threads`/`num_comments` accurate
  - If the removed thread was the active filter in the comments table, the filter is automatically cleared
  - Both tables reload via `loadResults()` after deletion
  - Button uses `event.stopPropagation()` so clicking it does not trigger the row's thread-filter click handler
