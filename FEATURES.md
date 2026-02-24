# Features

This document tracks all features and their functionality. Update this file whenever features are added or modified.

## Core Features

### 1. Reddit Thread Search
- **Description**: Searches Reddit (all subreddits) for threads matching a user's question or topic
- **Location**: `services/reddit_service.py` - `search_threads()`
- **Details**:
  - Uses PRAW to search `subreddit("all")` via Reddit's search API
  - Supports configurable max threads (5-25, default 15)
  - Supports time range filtering (all time, past year, month, week, day)
  - Collects thread metadata: title, subreddit, score, comment count, author, date, URL

### 2. Reddit Comment Collection
- **Description**: Collects comments from each discovered thread
- **Location**: `services/reddit_service.py` - `collect_comments()`
- **Details**:
  - Collects up to configurable max per thread (25-200, default 100)
  - Filters out deleted and removed comments
  - Uses `replace_more(limit=0)` to skip expanding collapsed threads (performance optimization)
  - Hard cap of 750 total comments across all threads (keeps highest-scored if exceeded)
  - Collects: author, body text, score (upvotes), date, depth in thread, permalink

### 3. AI Relevancy Scoring
- **Description**: Uses GPT-4o-mini to score each comment's relevancy to the research question on a 1-10 scale
- **Location**: `services/scoring_service.py`
- **Details**:
  - Batches 20 comments per LLM call for cost efficiency
  - Uses OpenAI structured outputs (Pydantic models) for reliable parsing
  - Returns a score (1-10) and brief reasoning for each comment
  - Scoring criteria: 1-2 irrelevant, 3-4 tangential, 5-6 somewhat relevant, 7-8 relevant, 9-10 highly relevant
  - Truncates comment bodies to 500 chars in prompts to control costs
  - Falls back to score of 5 if LLM fails for a specific comment

### 4. AI Summary Generation
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

### 5. LLM Provider Abstraction
- **Description**: Abstract interface allowing the AI provider to be swapped without changing application code
- **Location**: `services/llm_provider.py`
- **Details**:
  - `LLMProvider` abstract base class with two methods: `complete()` (structured output) and `complete_text()` (free text)
  - `OpenAIProvider` implementation using the OpenAI SDK
  - To swap providers: create a new class implementing `LLMProvider` and update instantiation in `app.py`

## Data Features

### 6. SQLite Persistence
- **Description**: All research data persists in a local SQLite database
- **Location**: `services/storage_service.py`
- **Details**:
  - Database at `data/research.db` (auto-created on first run)
  - Tables: `researches`, `threads`, `comments`
  - Stores complete research history including threads, scored comments, and summaries
  - Indexed for fast lookups

### 7. CSV Export
- **Description**: Research results are exported as CSV files for easy use in spreadsheets
- **Location**: `services/storage_service.py` - `export_csv()`
- **Details**:
  - Comments CSV: id, thread_id, author, body, score, relevancy_score, reasoning, permalink, depth, created_utc
  - Threads CSV: id, title, subreddit, score, num_comments, url, permalink, author, created_utc
  - Auto-generated on research completion
  - Also downloadable via "Export CSV" button on results page
  - Saved to `data/exports/` with research ID and question in filename

## UI Features

### 8. Landing Page
- **Description**: Home page with search form and research settings
- **Location**: `templates/index.html`
- **Details**:
  - Question input textarea
  - Collapsible settings panel: max threads (slider), max comments per thread (slider), time range (dropdown)
  - Displays collection limits to set user expectations
  - Red "Research" submit button

### 9. Real-Time Progress Display
- **Description**: Shows live progress while research is running
- **Location**: `templates/index.html`, `static/js/app.js`
- **Details**:
  - Uses Server-Sent Events (SSE) for real-time updates from the backend
  - Animated progress bar with percentage
  - Stage-by-stage status messages (searching, collecting, scoring)
  - Error handling with retry option
  - Redirects to results page on completion

### 10. Threads Table
- **Description**: Sortable table displaying all discovered threads
- **Location**: `templates/results.html`, `static/js/tables.js`
- **Details**:
  - Columns: Title, Subreddit, Score, Comments, Date, Link
  - Sortable by clicking column headers (with sort direction indicators)
  - Clickable rows filter the Comments table to show only that thread's comments
  - Toggle off filter by clicking the same thread again
  - External "View" links open the thread on Reddit

### 11. Comments Table
- **Description**: Sortable, paginated table of scored comments
- **Location**: `templates/results.html`, `static/js/tables.js`
- **Details**:
  - Columns: Relevancy Score (color-coded badge), Comment (expandable), Author, Score, Thread, Date, Link
  - Color coding: green (8-10), yellow (5-7), red (1-4)
  - Default sort: relevancy score descending
  - Sortable columns: relevancy, author, score, date
  - Expandable rows: click to show full comment text and AI scoring reasoning
  - Pagination: 50 comments per page with Previous/Next controls
  - Thread filtering: shows filter banner with clear option when filtering

### 12. History Sidebar
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

### 13. Background Processing Pipeline
- **Description**: Research runs in a background thread to avoid blocking the web server
- **Location**: `app.py` - `run_research_pipeline()`
- **Details**:
  - `threading.Thread` for the research pipeline
  - `queue.Queue` per research for SSE progress events
  - Pipeline stages: search -> collect -> score -> save -> export CSV
  - Error handling with status tracking (pending, processing, complete, error)
  - Timeout handling on SSE stream (120 seconds)
