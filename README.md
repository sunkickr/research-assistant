# Research Assistant

A web application that helps you research topics by collecting comments and articles from Reddit, Hacker News, and the web, scoring them for relevancy using AI, displaying results in sortable tables, and generating cited summaries weighted by community sentiment.

## Prerequisites

- Python 3.9+
- A Reddit API application (for API credentials)
- An OpenAI API key (for comment scoring and summarization)

## Setup

### 1. Clone and install dependencies

```bash
cd research-assistant
pip3 install -r requirements.txt
```

### 2. Get Reddit API credentials

1. Go to https://www.reddit.com/prefs/apps
2. Click "create another app..."
3. Fill in the form:
   - **name**: ResearchAssistant (or anything you like)
   - **type**: Select "script"
   - **redirect uri**: http://localhost:8080
4. Click "create app"
5. Note your **client_id** (the string under the app name) and **client_secret**

### 3. Get an OpenAI API key

1. Go to https://platform.openai.com/api-keys
2. Create a new API key
3. Ensure you have billing set up at https://platform.openai.com/settings/organization/billing/overview

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in your credentials:

```
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret
REDDIT_USER_AGENT=ResearchAssistant/1.0
OPENAI_API_KEY=sk-your-key-here
```

### 5. Run the application

```bash
python3 app.py
```

Open http://localhost:5000 in your browser.

## Usage

1. Enter a question or topic in the search box
2. Optionally adjust settings (sources: Reddit / Hacker News / Web Articles, max threads, max comments, time range)
3. Click "Research" and watch the live activity feed as threads and comments are collected and scored
4. Browse the Threads and Comments tables — click a thread to filter comments and see the full post body
5. Star (⭐) interesting comments, set your own relevancy scores, filter by source tab (All / Reddit / HN / Web)
6. Click **Find More Comments & Articles** to search for additional content across all three sources in one click; use the ⚙ gear button to choose which sources to include
7. Click **Summarize Comments** for an AI-generated summary with numbered citations; a Sources section lists every cited comment with a link
8. Click **Export CSV** to download the data
9. Use the sidebar to view or archive past research sessions

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `REDDIT_CLIENT_ID` | (required) | Reddit app client ID |
| `REDDIT_CLIENT_SECRET` | (required) | Reddit app client secret |
| `REDDIT_USER_AGENT` | `ResearchAssistant/1.0` | User agent for Reddit API |
| `OPENAI_API_KEY` | (required) | OpenAI API key |
| `LLM_MODEL` | `gpt-4o-mini` | OpenAI model to use |
| `FLASK_DEBUG` | `true` | Enable Flask debug mode |
| `PORT` | `5000` | Port to run the app on |

## Cost

The app uses GPT-4o-mini for scoring and summarization. Typical cost per research query is $0.02-0.05 depending on the number of comments and sources collected.

## Data

Research data is stored in `data/research.db` (SQLite) and CSV exports are saved to `data/exports/`. The `data/` directory is created automatically on first run and is excluded from git.
