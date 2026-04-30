"""
Agent tool registry, event types, and service container.

Tools are plain Python functions decorated with @registry.tool. The registry
auto-generates JSON schemas from type hints and docstrings, injects `services`
and `emit` at execution time (invisible to the LLM), and caps result strings
to keep conversation history from growing unbounded.
"""

import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Callable, get_type_hints

MAX_RESULT_CHARS = 3000  # cap tool output before appending to LLM history

# Internal sentinel to mark params excluded from the LLM-visible schema
_INTERNAL_PARAMS = frozenset({"emit", "services"})


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

@dataclass
class AgentEvent:
    """An event emitted by the agent harness or a tool during execution."""
    type: str    # "thinking" | "tool_call" | "tool_progress" | "tool_result" | "message" | "error"
    content: str
    data: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Service container
# ---------------------------------------------------------------------------

@dataclass
class ServiceContainer:
    """
    Holds all initialized service instances. Built once in cli.py (or the Flask
    route) and injected into the ToolRegistry so tools receive services without
    the LLM needing to know about them.
    """
    storage_svc: object
    scoring_svc: object
    summary_svc: object
    reddit_svc: object
    hn_svc: object
    article_svc: object
    web_search_svc: object
    ph_svc: object
    llm: object
    config: object
    job_search_svc: object = None


# ---------------------------------------------------------------------------
# Schema generation helpers
# ---------------------------------------------------------------------------

_PY_TO_JSON_TYPE = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
}


def _parse_docstring_params(doc: str) -> dict[str, str]:
    """Extract per-param descriptions from a Google-style docstring Args block."""
    if not doc:
        return {}
    descriptions: dict[str, str] = {}
    in_args = False
    current_param = None
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped.lower() in ("args:", "arguments:", "parameters:"):
            in_args = True
            continue
        if in_args:
            if stripped and not stripped.startswith(" ") and stripped.endswith(":") and " " not in stripped.rstrip(":"):
                # New top-level section (e.g. "Returns:", "Raises:") — stop
                in_args = False
                break
            # Param line: "    param_name (type): description"
            m = re.match(r"(\w+)\s*(?:\([^)]*\))?\s*:\s*(.*)", stripped)
            if m:
                current_param = m.group(1)
                descriptions[current_param] = m.group(2).strip()
            elif current_param and stripped:
                descriptions[current_param] += " " + stripped
    return descriptions


def _annotation_to_schema(annotation) -> dict:
    """Convert a Python type annotation to a JSON schema fragment."""
    import typing

    if annotation is inspect.Parameter.empty:
        return {"type": "string"}

    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", ())

    # Optional[X] (Union[X, None]) → unwrap to X
    if origin is typing.Union and len(args) == 2 and type(None) in args:
        inner = args[0] if args[1] is type(None) else args[1]
        return _annotation_to_schema(inner)

    # Literal["a", "b"] → enum
    if origin is not None:
        try:
            from typing import Literal
            if origin is Literal:
                return {"type": "string", "enum": list(args)}
        except ImportError:
            pass

    # list[str] etc.
    if origin is list:
        item_schema = _annotation_to_schema(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item_schema}

    # Bare list (no type parameter)
    if annotation is list:
        return {"type": "array", "items": {"type": "string"}}

    # Simple types by name
    name = getattr(annotation, "__name__", str(annotation))
    json_type = _PY_TO_JSON_TYPE.get(name, "string")
    return {"type": json_type}


def _build_schema(func: Callable) -> dict:
    """Build an OpenAI-format function schema from a function's signature and docstring."""
    hints = {}
    try:
        hints = get_type_hints(func)
    except Exception:
        pass

    sig = inspect.signature(func)
    doc = inspect.getdoc(func) or ""
    # First line of docstring is the tool description
    description = doc.splitlines()[0].strip() if doc else func.__name__
    param_docs = _parse_docstring_params(doc)

    properties: dict = {}
    required: list = []

    for param_name, param in sig.parameters.items():
        if param_name in _INTERNAL_PARAMS:
            continue  # invisible to LLM

        annotation = hints.get(param_name, inspect.Parameter.empty)
        schema_fragment = _annotation_to_schema(annotation)

        if param_name in param_docs:
            schema_fragment["description"] = param_docs[param_name]

        properties[param_name] = schema_fragment
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "function",
        "function": {
            "name": func.__name__,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """
    Registry of agent tools. Decorated functions are registered with auto-generated
    JSON schemas. The registry injects `services` and `emit` at call time so the
    LLM never sees them.
    """

    def __init__(self, container: ServiceContainer):
        self.container = container
        self._tools: dict[str, dict] = {}  # name → {func, schema}

    def tool(self, func: Callable) -> Callable:
        """Decorator: register a function as an agent tool."""
        schema = _build_schema(func)
        self._tools[func.__name__] = {"func": func, "schema": schema}
        return func

    def register(self, func: Callable) -> None:
        """Register a function as a tool (non-decorator form)."""
        schema = _build_schema(func)
        self._tools[func.__name__] = {"func": func, "schema": schema}

    def get_schemas(self) -> list[dict]:
        """Return OpenAI-format tool schemas for all registered tools."""
        return [entry["schema"] for entry in self._tools.values()]

    def execute(self, name: str, arguments: dict, emit: Callable[[AgentEvent], None]) -> str:
        """
        Execute a registered tool by name. Injects `emit` and `services` if the
        function declares those parameters. Returns a JSON string capped at
        MAX_RESULT_CHARS to protect conversation history size.
        """
        if name not in self._tools:
            return json.dumps({"error": f"Unknown tool: {name}"})

        func = self._tools[name]["func"]
        sig = inspect.signature(func)
        kwargs = dict(arguments)  # copy so we don't mutate the original

        if "emit" in sig.parameters:
            kwargs["emit"] = emit
        if "services" in sig.parameters:
            kwargs["services"] = self.container

        try:
            result = func(**kwargs)
        except Exception as exc:
            return json.dumps({"error": str(exc)})

        if isinstance(result, str):
            raw = result
        else:
            raw = json.dumps(result, default=str)

        if len(raw) > MAX_RESULT_CHARS:
            raw = raw[:MAX_RESULT_CHARS] + "... [truncated — use retrieve_research to see full data]"
        return raw
