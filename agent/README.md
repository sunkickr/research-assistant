# Research Assistant Agent

A chat-based agent that gives you conversational access to the research assistant's full pipeline — collecting, scoring, summarizing, and analyzing community discussions from Reddit, Hacker News, and the web.

The agent decides which tools to call based on your conversation. Ask it a question, and it figures out whether to search for new data, pull up existing research, generate a summary, or run a targeted analysis.

## Quick Start

```bash
# Interactive chat
python agent/cli.py

# One-shot question (runs one turn and exits)
python agent/cli.py --question "What do people think of Linear?"
```

Requires `OPENAI_API_KEY` in your `.env` file. The agent uses the same database and services as the web app — any research collected through the agent is visible in the web UI and vice versa.

## What You Can Do

### List existing research

```
You: What research do I have?

⚙  Using retrieve_research
   {'action': 'list'}
✓ retrieve_research complete

╭─ Research Assistant ─╮
│ You have 14 research │
│ sessions:            │
│ 1. AI observability  │
│    88 threads,       │
│    858 comments      │
│ ...                  │
╰──────────────────────╯
```

### Collect new research

```
You: I want to research what people think about Cursor IDE. Search Reddit and
     Hacker News, max 10 threads.

⚙  Using collect_research
   {'question': 'What do people think about Cursor IDE', 'sources': ['reddit', 'hackernews'], 'max_threads': 10}
  [progress 3%] Discovering relevant subreddits...
  [progress 10%] Searching Reddit...
  [progress 16%] Searching Hacker News...
  [progress 22%] 15 of 20 threads are relevant
  [progress 40%] Collecting from: Cursor AI IDE review...
  [progress 70%] Scoring batch 3/8
  [progress 100%] Research complete!
✓ collect_research complete

╭─ Research Assistant ──────────────────╮
│ Research collected! Here's a summary: │
│ - 10 threads from Reddit and HN      │
│ - 287 comments scored for relevancy   │
│ Want me to summarize the findings?    │
╰───────────────────────────────────────╯
```

### Product research

```
You: Do a product research on Notion. Include review sites and Product Hunt.

⚙  Using collect_research
   {'question': 'Product research: Notion', 'research_type': 'product',
    'product_name': 'Notion', 'sources': ['reddit', 'hackernews', 'web', 'reviews', 'producthunt']}
  ...
```

Product research searches across 6 categories (issues, feature requests, general, competitors, benefits, alternatives) and assigns each comment to a category.

### Summarize research

```
You: Summarize the Cursor research

⚙  Using summarize
   {'research_id': 'a1b2c3d4e5f6', 'summary_type': 'general'}
✓ summarize complete

╭─ Research Assistant ─────────────────────────────────────────────╮
│ The community response to Cursor IDE is largely positive,       │
│ particularly around its AI-powered code completion. The two     │
│ key findings:                                                   │
│                                                                 │
│ 1. Users consistently praise the tab-completion as faster and   │
│    more context-aware than Copilot                              │
│ 2. The main criticism is pricing — several commenters switched  │
│    back to VS Code + Copilot after the trial ended              │
│                                                                 │
│ Full summary saved to database.                                 │
╰──────────────────────────────────────────────────────────────────╯
```

### Analyze specific aspects

```
You: What are the main themes in the Cursor research?

⚙  Using analyze_research
   {'research_id': 'a1b2c3d4e5f6', 'analysis_type': 'themes'}
```

Analysis types:
- `overview` — Direct answer to the research question
- `themes` — 3-7 recurring themes with supporting evidence
- `evidence` — Strongest specific quotes that address the question
- `gaps` — What aspects are not well-covered by current data

### View specific comments

```
You: Show me the top 5 comments about issues from the Notion research

⚙  Using retrieve_research
   {'action': 'comments', 'research_id': '...', 'limit': 5, 'category': 'issues'}
```

### Ask follow-up questions

The agent maintains conversation context, so you can ask follow-ups naturally:

```
You: What research do I have?
Agent: [lists sessions]

You: Tell me more about the Obsidian research
Agent: [calls retrieve_research with action="get"]

You: What are people saying about plugin support?
Agent: [calls analyze_research with a focused question]

You: Save that as a finding
Agent: [calls update_state with section="findings"]
```

## CLI Options

```
python agent/cli.py                    # Interactive REPL
python agent/cli.py -q "your question" # Single turn, non-interactive
```

In-session commands:
- `exit` / `quit` / `q` / `:q` — Exit the agent
- `reset` / `/reset` — Clear conversation history and start fresh

## Architecture

```
agent/
├── cli.py            # Terminal interface (Rich-based)
├── harness.py        # AgentHarness — transport-agnostic loop
├── system_prompt.md  # Agent personality and instructions
└── tools/
    ├── __init__.py   # ToolRegistry, AgentEvent, ServiceContainer
    ├── collect.py    # collect_research — full pipeline
    ├── score.py      # score_comments — batch scoring
    ├── summarize.py  # summarize — general + product
    ├── retrieve.py   # retrieve_research — query stored data
    ├── analyze.py    # analyze_research — LLM synthesis
    └── state_tool.py # update_state — persist notes
```

The `AgentHarness` is transport-agnostic. It accepts an `emit` callback for output events. The terminal CLI provides one adapter (Rich panels and progress bars); the same harness class will power the future web chat interface with a different adapter.

### Tools

| Tool | What it does | Duration |
|------|-------------|----------|
| `collect_research` | Full pipeline: discover threads, collect comments, score them | 2-5 min |
| `score_comments` | Score any unscored comments from a prior run | 30s-2 min |
| `summarize` | Generate AI summary (general or per-category product) | 10-30s |
| `analyze_research` | Targeted synthesis: themes, evidence, gaps, overview | 5-15s |
| `retrieve_research` | Query stored data: list sessions, get comments/threads | Instant |
| `update_state` | Save findings/conclusions to a state file | Instant |

### State Files

Each research session can have a state file at `data/states/<slug>-state.md`. The agent writes to these after generating summaries or making findings. State files persist across conversations so you can resume where you left off.

## Shared Database

The agent and the web app (`python app.py`) share the same SQLite database at `data/research.db`. Research collected through the agent appears in the web UI, and web UI research is accessible to the agent. They can run simultaneously for read operations, but avoid running long collection pipelines from both at the same time.
