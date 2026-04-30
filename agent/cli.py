"""
Research Assistant — Terminal interface.

Run from the project root:
    python agent/cli.py
    python agent/cli.py --question "What do people think of Notion?"

The CLI is the terminal transport adapter for AgentHarness. It initializes
all services, builds the ToolRegistry, and wires up a Rich-based emit callback
that renders tool calls, progress, and responses beautifully in the terminal.

The AgentHarness itself is transport-agnostic — the same class powers the
future Flask SSE chat interface. Only this file (the adapter) knows about Rich.
"""

import argparse
import json
import os
import pathlib
import sys
from typing import Optional

# Set CWD to project root so relative paths (data/research.db, etc.) resolve
# correctly regardless of where the script is invoked from.
os.chdir(pathlib.Path(__file__).parent.parent)

# Add project root to sys.path so imports work when run as a script
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# Phoenix observability (optional) — must run before OpenAI client is created
_tracer = None
if os.environ.get("PHOENIX_ENABLED", "").lower() == "true":
    try:
        from phoenix.otel import register
        from openinference.instrumentation.openai import OpenAIInstrumentor
        from opentelemetry import trace as _otel_trace
        phoenix_endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006")
        tracer_provider = register(
            project_name="research-assistant-agent",
            endpoint=f"{phoenix_endpoint}/v1/traces",
        )
        OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)
        _tracer = _otel_trace.get_tracer("research-assistant-agent")
    except ImportError:
        pass  # Phoenix not installed, skip

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.prompt import Prompt
from rich.rule import Rule
from rich.text import Text

from agent.harness import AgentHarness
from agent.tools import AgentEvent, ServiceContainer, ToolRegistry
from agent.tools.collect import collect_research
from agent.tools.score import score_comments
from agent.tools.summarize import summarize
from agent.tools.retrieve import retrieve_research
from agent.tools.analyze import analyze_research
from agent.tools.state_tool import update_state, load_state
from agent.tools.create_job_search import create_job_search
from agent.tools.save_job_search import save_job_search
from agent.tools.search_jobs import search_jobs
from agent.tools.retrieve_jobs import retrieve_jobs
from agent.tools.mark_applied import mark_applied
from agent.tools.discover_companies import discover_companies
from config import Config
from services.llm_provider import OpenAIProvider
from services.reddit_service import RedditService
from services.scoring_service import ScoringService
from services.summary_service import SummaryService
from services.storage_service import StorageService
from services.web_search_service import WebSearchService
from services.hn_service import HNService
from services.article_service import ArticleService
from services.producthunt_service import ProductHuntService
from services.job_search_service import JobSearchService

console = Console()

# ---------------------------------------------------------------------------
# Rich terminal transport (the "adapter" for AgentHarness)
# ---------------------------------------------------------------------------

_progress_instance = None  # type: Optional[Progress]
_progress_task_id = None
_current_tool: str = ""


def _get_or_create_progress() -> Progress:
    global _progress_instance
    if _progress_instance is None:
        _progress_instance = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=30),
            TaskProgressColumn(),
            console=console,
            transient=True,
        )
        _progress_instance.start()
    return _progress_instance


def _stop_progress() -> None:
    global _progress_instance, _progress_task_id
    if _progress_instance is not None:
        _progress_instance.stop()
        _progress_instance = None
        _progress_task_id = None


def terminal_emit(event: AgentEvent) -> None:
    global _progress_task_id, _current_tool

    if event.type == "tool_call":
        _current_tool = event.data.get("tool", "")
        console.print(f"\n[bold cyan]⚙  {event.content}[/bold cyan]")
        args = event.data.get("args", {})
        if args:
            # Show args but filter out noise (emit, services never appear here)
            args_str = str({k: v for k, v in args.items() if v not in (None, [], "")})
            if len(args_str) > 200:
                args_str = args_str[:200] + "..."
            console.print(f"   [dim]{args_str}[/dim]")

    elif event.type == "tool_progress":
        progress = event.data.get("progress", 0)
        description = event.content[:60]
        p = _get_or_create_progress()
        if _progress_task_id is None:
            _progress_task_id = p.add_task(description, total=100)
        p.update(_progress_task_id, completed=progress, description=description)

    elif event.type == "tool_result":
        _stop_progress()
        console.print(f"   [green]✓[/green] [dim]{event.content}[/dim]")

    elif event.type == "message":
        _stop_progress()
        console.print()
        console.print(Panel(
            Markdown(event.content),
            title="[bold]Research Assistant[/bold]",
            border_style="blue",
            padding=(1, 2),
        ))

    elif event.type == "error":
        _stop_progress()
        console.print()
        console.print(Panel(
            event.content,
            title="[bold red]Error[/bold red]",
            border_style="red",
        ))

    elif event.type == "thinking":
        console.print(f"[dim italic]  {event.content}[/dim italic]")


# ---------------------------------------------------------------------------
# Service initialization
# ---------------------------------------------------------------------------

def build_services(config: Config) -> ServiceContainer:
    """Initialize all services and return a ServiceContainer."""
    reddit_svc = RedditService(
        config.REDDIT_CLIENT_ID,
        config.REDDIT_CLIENT_SECRET,
        config.REDDIT_USER_AGENT,
    )
    llm = OpenAIProvider(config.OPENAI_API_KEY, config.LLM_MODEL)
    scoring_svc = ScoringService(llm, batch_size=config.LLM_BATCH_SIZE)
    summary_svc = SummaryService(llm)
    storage_svc = StorageService(config.DB_PATH, config.EXPORT_DIR)
    web_search_svc = WebSearchService(reddit_svc.reddit)
    hn_svc = HNService()
    article_svc = ArticleService(llm)
    ph_svc = ProductHuntService(config.PRODUCT_HUNT_API_TOKEN)
    job_search_svc = JobSearchService(llm, config.COMPANY_LISTS_DIR)

    return ServiceContainer(
        storage_svc=storage_svc,
        scoring_svc=scoring_svc,
        summary_svc=summary_svc,
        reddit_svc=reddit_svc,
        hn_svc=hn_svc,
        article_svc=article_svc,
        web_search_svc=web_search_svc,
        ph_svc=ph_svc,
        llm=llm,
        config=config,
        job_search_svc=job_search_svc,
    )


def build_registry(container: ServiceContainer) -> ToolRegistry:
    """Register all agent tools and return the registry."""
    registry = ToolRegistry(container=container)
    registry.register(collect_research)
    registry.register(score_comments)
    registry.register(summarize)
    registry.register(retrieve_research)
    registry.register(analyze_research)
    registry.register(update_state)
    registry.register(create_job_search)
    registry.register(save_job_search)
    registry.register(search_jobs)
    registry.register(retrieve_jobs)
    registry.register(mark_applied)
    registry.register(discover_companies)
    return registry


def load_system_prompt() -> str:
    prompt_path = pathlib.Path(__file__).parent / "system_prompt.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "You are a helpful research assistant."


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------

def print_welcome():
    console.print(Rule("[bold blue]Research Assistant[/bold blue]"))
    console.print(
        "[dim]Chat with your research data. Type [bold]exit[/bold] or press Ctrl+C to quit.[/dim]\n"
    )


def run_repl(harness: AgentHarness, initial_message=None):
    """Run the interactive REPL. If initial_message is provided, process it first."""
    print_welcome()

    if initial_message:
        # Non-interactive single-turn mode (--question flag)
        harness.chat(initial_message, terminal_emit)
        return

    while True:
        try:
            user_input = Prompt.ask("\n[bold]You[/bold]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q", ":q"):
            console.print("[dim]Goodbye.[/dim]")
            break
        if user_input.lower() in ("reset", "/reset"):
            harness.reset()
            console.print("[dim]Conversation reset.[/dim]")
            continue

        harness.chat(user_input, terminal_emit)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Research Assistant — chat-based research agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python agent/cli.py
  python agent/cli.py --question "What do people think of Linear?"
  python -m agent.cli
        """,
    )
    parser.add_argument(
        "--question", "-q",
        metavar="QUESTION",
        help="Run a single research question non-interactively and exit.",
    )
    args = parser.parse_args()

    config = Config()

    if not config.OPENAI_API_KEY:
        console.print("[bold red]Error:[/bold red] OPENAI_API_KEY is not set. Add it to your .env file.")
        sys.exit(1)

    # Pass tracer to the harness module if Phoenix was initialized
    if _tracer:
        from agent.harness import init_tracer
        init_tracer(_tracer)
        console.print("[dim]Phoenix observability enabled.[/dim]")

    try:
        container = build_services(config)
        registry = build_registry(container)
        system_prompt = load_system_prompt()
        harness = AgentHarness(llm=container.llm, registry=registry, system_prompt=system_prompt)
    except Exception as exc:
        console.print(f"[bold red]Failed to initialize:[/bold red] {exc}")
        sys.exit(1)

    run_repl(harness, initial_message=args.question)


if __name__ == "__main__":
    main()
