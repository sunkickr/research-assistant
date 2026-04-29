from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Type
from pydantic import BaseModel


@dataclass
class ToolCall:
    """A single tool call returned by the LLM."""
    id: str
    name: str
    arguments: dict  # pre-parsed JSON


@dataclass
class LLMResponse:
    """Normalized response from complete_with_tools()."""
    content: Optional[str]           # text when stop_reason == "end_turn"
    tool_calls: List[ToolCall]       # populated when stop_reason == "tool_use"
    stop_reason: str                 # "end_turn" | "tool_use"
    raw_message: dict = field(default_factory=dict)  # provider-native message for history appending


class LLMProvider(ABC):
    """
    Abstract base class for LLM providers.
    Implement this interface to swap in a different provider (Anthropic, Gemini, etc.).
    """

    @abstractmethod
    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[BaseModel],
        temperature: float = 0.0,
    ) -> BaseModel:
        """Send a prompt and get a structured response parsed into response_model."""
        pass

    @abstractmethod
    def complete_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> str:
        """Send a prompt and get a plain text response."""
        pass

    @abstractmethod
    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        system_prompt: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """
        Send a conversation with tool schemas and get back either a text response
        or one or more tool calls.

        Args:
            messages: Conversation history in OpenAI format (role/content dicts).
                      Do NOT include the system message here — pass it via system_prompt.
            tools: List of tool schemas in OpenAI format:
                   [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}]
            system_prompt: System prompt prepended before the message history.
            temperature: Sampling temperature (0.0 for deterministic tool use).
            max_tokens: Maximum tokens in the response.

        Returns:
            LLMResponse with stop_reason "end_turn" (text) or "tool_use" (tool calls).
        """
        pass


# ---------------------------------------------------------------------------
# To add Anthropic support:
# 1. anthropic is already in requirements.txt
# 2. Implement AnthropicProvider(LLMProvider) with all three methods
# 3. For complete_with_tools(), use:
#      client.messages.create(..., tools=[{name, description, input_schema}])
# 4. Check stop_reason == "tool_use", parse content blocks of type "tool_use"
# 5. Tool results use role="user" with content=[{"type":"tool_result","tool_use_id":...}]
# 6. System prompt is passed as the system= kwarg (not in the messages list)
# 7. raw_message reconstruction: {"role": "assistant", "content": response.content}
#    where response.content is the list of content blocks from the Anthropic response
# ---------------------------------------------------------------------------


class OpenAIProvider(LLMProvider):
    """OpenAI implementation using GPT-4o-mini by default."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, timeout=60.0)
        self.model = model

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[BaseModel],
        temperature: float = 0.0,
    ) -> BaseModel:
        completion = self.client.beta.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=response_model,
            temperature=temperature,
        )
        return completion.choices[0].message.parsed

    def complete_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> str:
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return completion.choices[0].message.content

    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        system_prompt: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        import json as _json
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        response = self.client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            tools=tools,
            tool_choice="auto",
            temperature=temperature,
            max_tokens=max_tokens,
        )
        message = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        if finish_reason == "tool_calls" and message.tool_calls:
            parsed_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=_json.loads(tc.function.arguments),
                )
                for tc in message.tool_calls
            ]
            # Build raw_message in the format OpenAI expects back in history
            raw = {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ],
            }
            return LLMResponse(
                content=None,
                tool_calls=parsed_calls,
                stop_reason="tool_use",
                raw_message=raw,
            )

        return LLMResponse(
            content=message.content,
            tool_calls=[],
            stop_reason="end_turn",
            raw_message={"role": "assistant", "content": message.content},
        )
