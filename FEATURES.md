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
  - Supports time range filtering (all time, past year, month, week, day) — applied across all search sources, not just Reddit API
  - Collects thread metadata: title, subreddit, score, comment count, author, date, URL

### 3. Web Search Thread Discovery
- **Description**: Supplements Reddit API search by finding threads via DuckDuckGo `site:reddit.com` queries
- **Location**: `services/web_search_service.py` - `search_reddit_threads()`
- **Details**:
  - Uses the `ddgs` package to search DuckDuckGo with LLM-generated keyword query variants
  - Respects time range filter via DuckDuckGo's `timelimit` parameter (d/w/m/y)
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
- **Description**: Collects comments from each relevant thread with conversation context
- **Location**: `services/reddit_service.py` - `collect_comments()`
- **Details**:
  - Collects up to configurable max per thread (25–200, default 100)
  - Filters out deleted and removed comments
  - Uses `replace_more(limit=0)` to skip expanding collapsed sub-threads (performance optimization)
  - Hard cap of 750 total comments across all threads (keeps highest-scored if exceeded)
  - Collects: author, body text, score (upvotes), date, depth in thread, permalink
  - **Comment context**: Each comment gets a concise `context` field populated during collection:
    - Top-level comments (depth=0): `"Thread: {title}"`
    - Reply comments (depth>0): `"Thread: {title} | Replying to @{parent_author}: {parent_snippet}"`
    - Parent lookup uses PRAW's in-memory `_comments_by_id` dict (zero API cost after `comments.list()`)
    - Parent snippet capped at 200 chars, thread title at 120 chars
    - Gracefully handles deleted parents and collapsed chains (falls back to thread-only context)
  - Context is also populated for HN comments (parent text threaded through recursion), web article quotes (`"Article: {title}"`), and Product Hunt comments (parent body for replies)

### 6. AI Comment Relevancy Scoring
- **Description**: Uses GPT-4o-mini to score each comment's relevancy to the research question on a 1–10 scale, with thread and parent context
- **Location**: `services/scoring_service.py` - `score_comments()`
- **Details**:
  - Batches 20 comments per LLM call for cost efficiency
  - Uses OpenAI structured outputs (Pydantic models) for reliable parsing
  - Returns a score (1–10) and brief reasoning for each comment
  - **Comment context in prompts**: When a comment has context (thread title, parent comment snippet), it appears as a `Context:` line between the comment header and body, helping the LLM understand what a reply is responding to (e.g., knowing "this product" refers to Databricks)
  - Scoring criteria: 1–2 irrelevant, 3–4 tangential, 5–6 mentions topic but little actionable value, 7–8 relevant and useful, 9 directly addresses with specific information, 10 reserved for comments that completely answer the question with concrete actionable advice
  - Score 10 is intentionally rare — the prompt includes few-shot examples calibrating the LLM to only assign 10 to comments a researcher would call out as "exactly what I was looking for"
  - Named-entity floor rule: if the question asks about a specific product/company/tool, any comment that explicitly names and discusses it scores at least 5; first-hand user experience with it ("we use it", "I tried it") scores at least 7 — prevents niche products from being under-scored due to lack of Reddit coverage
  - Two sets of few-shot examples: one for a mainstream topic (Databricks cost optimization) and one for a niche product (Keebo AI) to calibrate both scenarios
  - Truncates comment bodies to 500 chars in prompts to control costs
  - If the LLM call fails for a batch, all comments in that batch are saved with `relevancy_score = null` and reasoning "Not scored — API timeout or error"

### 7. AI Summary Generation
- **Description**: Generates a comprehensive summary of research findings
- **Location**: `services/summary_service.py`, `static/js/app.js` - `renderSummary()`
- **Details**:
  - Triggered by user clicking "Summarize Comments" button
  - Filters to comments with relevancy score >= 4
  - Splits comments into community (Reddit + HN) and web pools; reserves 20% of slots for web quotes to prevent upvote-weighted sorting from excluding them
  - Community pool weighted by `relevancy_score * max(upvotes, 1)`; web pool by relevancy only
  - Sends top N comments (default 50, configurable 25–200 via Customize panel) to GPT-4o-mini for summarization
  - **Comment context in prompts**: Each comment includes its `Context:` line (thread title, parent comment) so the LLM understands reply chains and what "this product" or "I agree" refers to
  - Summary includes: Key Takeaways, thematic sections directly answering the question, Conclusion
  - LLM embeds `[#comment_id]` citation markers inline throughout the summary text
  - **Numbered citations**: `renderSummary()` resolves `[#id]` markers into numbered superscript links `[1]`, `[2]` etc. (in appearance order), each linking to the original source
  - **Sources section**: A "Sources" panel is appended below the summary listing every cited comment with its number, source badge (Reddit/HN/Web), author, 150-char snippet, and permalink link
  - Summary is saved and persists across sessions; citations re-resolve from live `allComments` data on every render
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
  - Sortable by clicking column headers; active sort column highlighted with blue background, accent border, and directional arrow (▲/▼)
  - Live thread count shown above the table ("N threads collected · Click a thread to filter comments and see the full post")
  - **Post body panel**: Clicking a thread row expands a panel below the table showing the full post body/selftext, author, and a link to the original — allows reading the original post without leaving the app
  - Toggle off filter by clicking the same thread again (panel collapses)
  - External "View" links open the thread on Reddit/HN/web
  - "Remove" button per row to delete the thread and its comments (see Feature 20)

### 14. Comments Table
- **Description**: Sortable, paginated table of scored comments
- **Location**: `templates/results.html`, `static/js/tables.js`
- **Details**:
  - Columns: Relevancy Score (color-coded badge), Comment (expandable), Author, Score, Thread, Date, Link
  - Color coding: green (8–10), yellow (5–7), red (1–4), gray dash "—" for unscored (null)
  - Default sort: relevancy score descending; active sort column highlighted with blue background and directional arrow
  - Sortable columns: relevancy, author, score, date
  - Expandable rows: click to show full comment text and AI scoring reasoning
  - Pagination: 50 comments per page with Previous/Next controls
  - Thread filtering: shows filter banner with clear option when filtering
  - "Not scored" filter: meta row below table header shows count of unscored comments as a clickable link to filter to only those; "Show all" link clears it
  - Thread filter and not-scored filter compose — both can be active simultaneously

### 15. Find More Comments & Articles
- **Description**: Expands an existing research by searching all three sources simultaneously — new Reddit threads, Hacker News stories, and web articles — in a single click
- **Location**: `app.py` - `expand_research()`, `run_expand_pipeline()`, `expand_status()`, `static/js/app.js` - `handleFindMore()`, `checkExpandStatus()`, `templates/results.html`, `static/css/style.css`
- **Details**:
  - **One click = all sources**: Each click searches Reddit (next unused sort), HN, and Web simultaneously — mirrors the main research flow
  - **Configure button**: Gear ⚙ icon next to the button opens a dropdown to select which sources to include; defaults to all enabled sources
  - **Source exhaustion**: Reddit has 4 sorts (top → new → controversial → hot); HN and Web each run once. Exhausted sources are automatically unchecked and disabled in the configure panel
  - **Source awareness**: Sources not enabled in the original research are disabled in the configure panel from the start
  - **Button disabled**: Only when all sources in the original research are exhausted (or all are unchecked)
  - Backend `expand_research()` accepts `sources` list in POST body; builds a `tasks` list (Reddit sort + HN + Web) filtered to requested and non-exhausted sources
  - `run_expand_pipeline()` takes `sorts: list`, runs each task's discovery in sequence, merges all candidates, then runs the unified dedup → score → collect → save pipeline once
  - `expand_status()` returns `research_sources`, `reddit_exhausted`, `hn_exhausted`, `web_exhausted` for the frontend to configure checkboxes
  - Deduplicates against all already-collected thread IDs; new threads go through AI thread scoring and comment scoring
  - Shows inline activity feed with per-source progress messages
  - New comments are inserted live into the results tables

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

## User-Managed Comment Features

### 21. User Relevancy Scores
- **Description**: Allows users to override AI-assigned relevancy scores with their own 1–10 rating
- **Location**: `static/js/tables.js` - `setUserRelevancy()`, `clearUserRelevancy()`, `app.py` - `set_user_relevancy()`, `services/storage_service.py` - `set_user_relevancy()`
- **Details**:
  - "Your Score" dropdown column in the comments table, next to the AI Relevancy column
  - User scores supersede AI scores for sorting using a +0.5 boost: user_relevancy=10 → effective 10.5 (ranks above AI=10)
  - AI relevancy still displays for reference in its own column
  - Small × button clears the user score, falling back to AI relevancy
  - User scores are validated to be 1–10 only
  - Effective relevancy is used consistently in: table sorting, summary service ordering, and DB query ordering
  - User scores persist across sessions (stored in SQLite)
  - Protected from being overwritten by "Find More" or "Add Thread" operations via `ON CONFLICT ... DO UPDATE` that skips user columns

### 22. Starred Comments
- **Description**: Allows users to bookmark interesting comments for easy retrieval
- **Location**: `static/js/tables.js` - `toggleStar()`, `app.py` - `toggle_star()`, `services/storage_service.py` - `toggle_star()`
- **Details**:
  - Star icon (☆/★) in the last column of each comment row
  - Clicking toggles between starred (filled gold ★) and unstarred (outline ☆)
  - Starred rows are highlighted with a light yellow background
  - Starring does not affect relevancy or sort order — it is purely for bookmarking
  - Filter link in the comments meta area to show only starred comments
  - Starred filter composes with existing thread and unscored filters (all are AND-composed)
  - Stars persist across sessions (stored in SQLite)

### 23. Archive Research
- **Description**: Allows users to archive old research questions to declutter the sidebar
- **Location**: `templates/base.html`, `static/js/app.js` - `archiveResearch()`, `openArchivedPopup()`, `restoreResearch()`, `permanentlyDelete()`, `app.py` - archive/unarchive/delete endpoints, `services/storage_service.py` - archive/unarchive/delete methods
- **Details**:
  - Archive button (📥) appears on hover for each sidebar history item
  - Archiving requires confirmation via browser `confirm()` dialog
  - Archived research is removed from the sidebar but not deleted — direct URLs still work
  - "Archived Research" button at the bottom of the sidebar opens a modal popup
  - Popup lists all archived research with Restore and Delete buttons for each
  - Restore returns the research to the sidebar (requires confirmation)
  - Delete permanently removes the research from the database (requires confirmation) but preserves CSV export files
  - Sidebar refreshes dynamically after archive/restore operations without a full page reload
  - If the user archives the research they are currently viewing, they are redirected to the home page

### 24. Summary Customize Panel
- **Description**: Allows users to customize summary generation with optional feedback/instructions and configurable comment count
- **Location**: `templates/results.html`, `static/js/app.js`, `app.py`, `services/summary_service.py`
- **Details**:
  - Split button UI: "Summarize Comments | Customize" appears as a unified button with two distinct halves
  - Clicking "Customize" toggles a panel with a feedback textarea, comment count input, submit button, and cancel button
  - **Comment count setting**: Number input to control how many top comments are included in the summary (25–200, default 50)
  - **Feedback textarea**: Optional instructions guiding what the AI focuses on (capped at 500 chars server-side)
  - Both settings are sent as JSON in the POST body to `/api/research/{id}/summarize`
  - The summary service appends user feedback to the LLM prompt, clearly labeled
  - Prompt injection guard: system prompt instructs the LLM to ignore feedback that asks for content unrelated to summarizing comments
  - The normal "Summarize Comments" button continues to work with defaults (no feedback, 50 comments)

### 25. Multi-Source Research (Reddit, Hacker News, Web Articles)
- **Description**: Expands research beyond Reddit to include Hacker News discussions and quotes extracted from web articles
- **Location**: `app.py`, `services/hn_service.py`, `services/article_service.py`, `services/web_search_service.py`, `static/js/tables.js`, `static/js/app.js`, `templates/index.html`, `templates/results.html`
- **Details**:
  - **Source selection**: Three checkboxes in the search settings panel (Reddit, Hacker News, Web Articles), all checked by default
  - **Hacker News**: Uses the free HN Algolia API (no auth, 10k req/hr) to search stories and collect comment trees. IDs prefixed `hn_` to avoid PK collisions. Respects time range filter via `numericFilters=created_at_i>{cutoff}`
  - **Web articles**: DuckDuckGo search finds non-Reddit/non-HN articles, respecting time range filter via `timelimit`. `trafilatura` extracts article text. LLM extracts 3-8 relevant quotes as synthetic "comments" with `source="web"`. IDs prefixed `web_`
  - **Source tabs**: Both the threads and comments tables have source tabs (All | Reddit | HN | Web) for filtering. Tabs only appear when multiple sources have data
  - **Pipeline integration**: All three sources map into existing `RedditThread`/`RedditComment` data models with a `source` field (default `"reddit"` for backward compatibility). Scoring, storage, and summary pipelines work unchanged
  - **Seed URLs**: Support Reddit, HN (`news.ycombinator.com/item?id=...`), and arbitrary web article URLs
  - **Add Thread**: URL detection routes to the correct service (Reddit via PRAW, HN via Algolia, web via trafilatura + LLM)
  - **Find More**: Each click searches all selected sources simultaneously (Reddit next sort + HN + Web). Per-source exhaustion tracked independently. See Feature 15
  - **Existing search queries**: The LLM-generated keyword queries from `suggest_subreddits()` drive all three search sources — no additional discovery LLM call needed
  - **Backward compatible**: `source` defaults to `"reddit"` everywhere. Existing researches display exactly as before

### 26. Live Progress Feedback
- **Description**: Replaces static progress bars with a scrolling activity feed and live preview table during research, showing real-time pipeline progress
- **Location**: `app.py`, `services/scoring_service.py`, `templates/index.html`, `templates/results.html`, `static/js/app.js`, `static/js/tables.js`
- **Details**:
  - **Activity feed**: Dark terminal-styled scrolling feed shows events as they happen — subreddits discovered, threads found, comments collected per-thread, scoring progress
  - **Live preview table** (main research): During scoring, a preview table builds up showing scored comments sorted by relevancy with color-coded score badges (green 7+, yellow 4-6)
  - **Live table insertion** (expand/add-thread): Scored comments are inserted into the existing results table in real-time via `insertLiveComments()` as batches complete
  - **Mini feeds**: Find More and Add Thread flows show compact activity feeds in their progress areas
  - Scoring callback enhanced to pass batch results: `progress_callback(batch_num, total_batches, batch_results)`
  - SSE events enriched with optional data fields (subreddits, thread titles, comment counts, scored comment arrays) — backward compatible
  - Feed items show spinner animation for active items, green checkmark for completed
  - No additional API calls — all data was already flowing through the pipeline
  - Negligible performance impact (microseconds of queue overhead between existing API calls)

### 27. Product Research Mode
- **Description**: Dedicated research mode for product managers to investigate a product across 6 categories — issues, feature requests, general info, competitors, benefits, and alternatives — with structured per-category summaries
- **Location**: `app.py` - `product_research()`, `run_product_research_pipeline()`, `summarize_product()`, `templates/index.html`, `templates/product_results.html`, `static/js/app.js`, `static/js/tables.js`, `static/css/style.css`
- **Details**:
  - **Homepage toggle**: Two-button toggle switches between "General Research" and "Product Research" modes
  - **Product research form**: Single text input for product name, source checkboxes (Reddit, HN, Web, Review Sites, Product Hunt), max threads/comments sliders, time range
  - **Multi-category search**: Automatically searches 6 categories (issues, feature_requests, general, competitors, benefits, alternatives) using 2 query templates each across all enabled sources
  - **Category assignment**: LLM classifies each comment into a category during scoring (not inherited from search query), using extended `ProductCommentScore` Pydantic model with `category` field
  - **Review site search**: DuckDuckGo `site:` searches for G2, Capterra, Trustpilot, and Quora content. Results processed through the existing web article pipeline (trafilatura + LLM quote extraction), stored with `source="reviews"`
  - **Product Hunt integration**: Dedicated GraphQL v2 API service (`services/producthunt_service.py`). Searches posts by product name, collects comments with reply flattening. IDs prefixed `ph_`. Gracefully degrades if no API token configured
  - **Product results page**: Dedicated template with product research badge, "Generate Summaries" button, 6 scrollable summary cards in a 2-column grid (General Info, Issues, Feature Requests, Benefits, Competitors, Alternatives)
  - **Per-category summaries**: 6 separate LLM calls, each focused on one category. Same-category comments boosted to top of input (30), cross-category comments included for context (20). Citations use `[#comment_id]` format resolved to numbered superscript links
  - **Category filter tabs**: All | Issues | Feature Requests | General | Competitors | Benefits | Alternatives — filters both threads and comments tables by category
  - **Source tabs**: Extended with "Reviews" and "Product Hunt" tabs alongside Reddit, HN, Web
  - **Pipeline**: 4-stage background pipeline — search all categories (0-40%), collect comments (40-60%), score with category assignment (60-95%), finalize (95-100%). Uses same SSE progress system
  - **Data model**: `category` column on threads and comments tables, `research_type` and `product_summaries_json` columns on researches table. All migrations use `ALTER TABLE ADD COLUMN` in try/except for idempotency

### 28. Save Before Score & Rescore
- **Description**: Comments are saved to the database before scoring begins, ensuring no data is lost if scoring is interrupted. Unscored comments can be rescored later
- **Location**: `services/storage_service.py` - `save_raw_comments()`, `get_unscored_count()`, `get_unscored_comments()`, `app.py` - rescore endpoints, `static/js/app.js` - `checkUnscoredComments()`, `handleRescore()`
- **Details**:
  - All collected comments are saved with `relevancy_score = NULL` immediately after collection, before scoring begins
  - Uses the existing upsert mechanism — preserves `user_relevancy_score` and `starred` fields
  - Each scored batch is saved to SQLite immediately (not buffered until the end of scoring)
  - If scoring is interrupted (timeout, error, user closes browser), comments are preserved with null scores
  - "Rescore" button appears on the results page when unscored comments are detected
  - Rescore uses a dedicated SSE stream (`/api/research/<id>/rescore/stream`) to avoid conflicts with other operations
  - Applies to all three pipelines: research, expand, and add-thread

### 29. Early Redirect to Results
- **Description**: Users are redirected to the results page as soon as comment collection finishes, allowing them to browse results while scoring continues in the background
- **Location**: `app.py`, `static/js/app.js` - `listenToProgress()`, `listenToScoringProgress()`, `static/js/tables.js` - `insertLiveComments()`
- **Details**:
  - Browser redirects on the first `scoring` SSE event instead of waiting for `complete`
  - Results page reconnects to the SSE stream via `listenToScoringProgress()` and shows a scoring progress indicator
  - Scored comments are inserted live into the results table as each batch completes via `insertLiveComments()`
  - Existing raw comments (null scores) are updated in-place when scored versions arrive
  - Live renders are debounced (500ms) to prevent rapid DOM replacement from breaking sort header clicks
  - SSE generator catches `GeneratorExit` on client disconnect and keeps the queue alive for reconnection
  - On scoring completion: summary button is enabled, sidebar counts refresh, header metadata updates

### 30. Publish Research as Shareable HTML
- **Description**: Generates a self-contained HTML report that can be shared via GitHub Pages or any static hosting
- **Location**: `app.py` - `publish_research()`, `serve_published()`, `_md_to_html()`, `_select_publish_comments()`, `_make_publish_filename()`, `templates/published_research.html`, `static/js/app.js` - `handlePublishResearch()`, `togglePublishConfig()`
- **Details**:
  - "Publish Research" button on both general and product results pages (disabled until summaries exist)
  - Gear icon (⚙) next to the button opens a settings dropdown to configure comment count (25–200, step 25, default 50)
  - Generates a standalone HTML file with all CSS inlined — no external dependencies
  - **Table of contents**: Links to each summary section and the comments section; uses anchor IDs for smooth navigation
  - Product research: renders all 6 summary sections in order with per-section numbered Sources footer showing cited comments
  - General research: renders the single summary with citations
  - Selects top comments with source diversity quotas (Reddit 60%, Web 15%, HN 15%, Product Hunt 10%)
  - Issues category quota: at least 10% of selected comments must have `category == "issues"` (product research only); backfills from highest-relevancy issues comments if the source quota step didn't include enough
  - Comments sorted by AI relevancy score descending, then date descending as tiebreaker
  - Each comment shows: source badge, category badge, relevancy score, author, date, thread title (clickable link to original thread/article), truncated body with expandable full text, and permalink
  - Citation numbers in summaries (`[1]`, `[2]`, etc.) match numbered sources listed below each section
  - Files saved to `published/` directory with slug-based naming and auto-increment on collision
  - Button transforms to "View Published" link after successful generation
  - GitHub Actions workflow (`.github/workflows/deploy-pages.yml`) deploys `published/` as GitHub Pages root on push to main

### 31. Section Hide/Show Toggle
- **Description**: Allows users to collapse individual product summary cards to focus on sections of interest
- **Location**: `templates/product_results.html`, `static/js/app.js` - `toggleSectionVisibility()`, `static/css/style.css`
- **Details**:
  - ▲/▼ toggle button in each summary card header, next to the regenerate button
  - Clicking hides the card body while keeping the header visible
  - Click again to restore — purely client-side, no server round-trip
  - Both buttons grouped in a `.summary-card-actions` flex container for proper alignment

### 32. Per-Section Regeneration Settings
- **Description**: When regenerating a single product summary section, users can control the number of input comments and choose the enhanced LLM model
- **Location**: `templates/product_results.html` (section feedback modal), `static/js/app.js` - `handleRegenerateSection()`, `app.py` - `summarize_product_section()`
- **Details**:
  - Section regenerate modal now includes a "Comments to use" number input (10–100, default 50) and an alt model checkbox
  - Alt model checkbox displays the exact model name (e.g. "Use gpt-4.1-mini") fetched from `/api/models`
  - Both values sent to `POST /api/research/{id}/summarize-product-section` as `max_comments` and `use_alt_model`
  - Backend caps `max_comments` at 100 and routes to the alt summary service when `use_alt_model` is true

### 33. Phoenix Observability
- **Description**: Opt-in LLM call tracing via Arize Phoenix, providing visibility into prompts, responses, token usage, latency, and errors for all OpenAI calls
- **Location**: `app.py` (initialization + pipeline parent spans), `services/scoring_service.py`, `services/summary_service.py`, `services/article_service.py` (agent tags)
- **Details**:
  - Enabled via `PHOENIX_ENABLED=true` environment variable; zero overhead when disabled
  - Auto-instruments all OpenAI SDK calls (both `chat.completions.create` and `beta.chat.completions.parse`) via `openinference-instrumentation-openai`
  - **Agent tags**: Each LLM call is tagged with agent type (`agent:scoring`, `agent:summary`, `agent:collection`) and task type (`task:comment_scoring`, `task:general_summary`, `task:quote_extraction`, etc.) — filterable in Phoenix UI
  - **Pipeline parent spans**: Pipeline stages (`query-generation`, `thread-scoring`, `comment-scoring`, `summarize`, etc.) are wrapped in OpenTelemetry spans so auto-instrumented OpenAI calls appear as children in a trace hierarchy
  - `_span()` helper returns a no-op `nullcontext()` when Phoenix is disabled
  - `using_tags()` imports use `try/except ImportError` fallbacks so the app works without Phoenix installed
  - All traces appear under the "research-assistant" project in the Phoenix UI at http://localhost:6006
  - Future providers (Anthropic, Google) can be traced by installing the corresponding `openinference-instrumentation-*` package — no code changes needed
