"""
Research Assistant Agent

Provides the AgentHarness and supporting types for building chat-based
research agents. The harness is transport-agnostic — wire it to a terminal
(agent/cli.py) or a Flask SSE route for the UI chat interface.

Quick start (terminal):
    python agent/cli.py

Programmatic usage:
    from agent import AgentHarness, AgentEvent, ServiceContainer, ToolRegistry
"""

from agent.harness import AgentHarness
from agent.tools import AgentEvent, ServiceContainer, ToolRegistry

__all__ = ["AgentHarness", "AgentEvent", "ServiceContainer", "ToolRegistry"]
