"""
AgentHarness — transport-agnostic agent loop.

The harness holds conversation state and drives the LLM tool-calling loop.
It knows nothing about Rich, Flask, or queues. All output goes through the
`emit` callback, making it trivial to swap the transport:

    Terminal:      emit = terminal_emit (prints with Rich)
    Flask SSE:     emit = lambda e: q.put(e.__dict__)
    WebSocket:     emit = lambda e: ws.send(json.dumps(e.__dict__))

Usage:
    harness = AgentHarness(llm, registry, system_prompt)
    harness.chat("What research do I have?", terminal_emit)
"""

import json
from contextlib import nullcontext
from typing import Callable, Optional

from agent.tools import AgentEvent, ToolRegistry
from services.llm_provider import LLMProvider

_DEFAULT_MAX_ITERATIONS = 10

# Module-level tracer — set by the entry point (cli.py or app.py) if Phoenix is enabled
_tracer = None


def init_tracer(tracer) -> None:
    """Set the module-level tracer. Called by the entry point after Phoenix setup."""
    global _tracer
    _tracer = tracer


def _span(name, kind="CHAIN", **attrs):
    """Create an OpenTelemetry span if Phoenix is active, otherwise a no-op."""
    if _tracer:
        attrs["openinference.span.kind"] = kind
        return _tracer.start_as_current_span(name, attributes=attrs)
    return nullcontext()


class AgentHarness:
    """
    Transport-agnostic agent loop. Drives the LLM → tool call → result → LLM cycle.

    The `emit` callback receives every AgentEvent generated during a chat() call.
    Conversation history is maintained across calls so context persists within a
    session. Instantiate once per user session.
    """

    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
        system_prompt: str,
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
    ):
        self.llm = llm
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.conversation: list = []
        self.active_research_id: Optional[str] = None

    def chat(self, user_message: str, emit: Callable[[AgentEvent], None]) -> str:
        """
        Process one user message and return the assistant's final text response.

        The emit callback is called for every intermediate event (tool calls,
        progress, the final message). The returned string is the same content
        as the final `AgentEvent(type="message")` payload.
        """
        with _span("chat_turn", kind="CHAIN",
                    **{"input.value": user_message[:500]}):
            return self._chat_inner(user_message, emit)

    def _chat_inner(self, user_message: str, emit: Callable[[AgentEvent], None]) -> str:
        self.conversation.append({"role": "user", "content": user_message})
        tools = self.registry.get_schemas()

        for iteration in range(self.max_iterations):
            try:
                response = self.llm.complete_with_tools(
                    messages=self.conversation,
                    tools=tools,
                    system_prompt=self.system_prompt,
                )
            except Exception as exc:
                error_msg = f"LLM call failed: {exc}"
                emit(AgentEvent("error", error_msg))
                return error_msg

            if response.stop_reason == "tool_use":
                # Append the raw assistant message (with tool_calls) to history
                self.conversation.append(response.raw_message)

                for tc in response.tool_calls:
                    emit(AgentEvent(
                        "tool_call",
                        f"Using {tc.name}",
                        {"tool": tc.name, "args": tc.arguments},
                    ))

                    with _span(f"tool:{tc.name}", kind="TOOL",
                               **{"tool.name": tc.name}):
                        result_str = self.registry.execute(tc.name, tc.arguments, emit)

                    # Track the active research_id when a new collection completes
                    if tc.name == "collect_research":
                        try:
                            parsed = json.loads(result_str)
                            new_id = parsed.get("research_id")
                            if new_id:
                                self.active_research_id = new_id
                        except (json.JSONDecodeError, AttributeError):
                            pass

                    # Append the tool result back into the conversation
                    self.conversation.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_str,
                    })

                    emit(AgentEvent(
                        "tool_result",
                        f"{tc.name} complete",
                        {"summary": result_str[:200]},
                    ))

                # Continue the loop — let the LLM respond to the tool results
                continue

            # stop_reason == "end_turn": LLM has a final text response
            final_text = response.content or ""
            self.conversation.append({"role": "assistant", "content": final_text})
            emit(AgentEvent("message", final_text))
            return final_text

        # Reached iteration limit without a final text response
        limit_msg = (
            "I reached my iteration limit without a final answer. "
            "Please try rephrasing or breaking the request into smaller steps."
        )
        emit(AgentEvent("error", limit_msg))
        return limit_msg

    def reset(self) -> None:
        """Clear conversation history and active research context."""
        self.conversation = []
        self.active_research_id = None
