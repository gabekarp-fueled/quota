"""Tool registry for Claude agent loops.

Each tool has a name, description, JSON input schema, and an async handler function.
The registry generates tool definitions for the Claude API and dispatches tool calls.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    """A single tool that Claude can call."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Coroutine[Any, Any, Any]]


class ToolRegistry:
    """Registry of tools available to a Claude agent."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[..., Coroutine[Any, Any, Any]],
    ) -> None:
        """Register a tool with its schema and handler."""
        self._tools[name] = Tool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
        )

    def get_schemas(self) -> list[dict[str, Any]]:
        """Return tool definitions in Claude API format."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    async def execute(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        """Execute a tool by name and return the result as a string."""
        tool = self._tools.get(tool_name)
        if not tool:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        try:
            result = await tool.handler(**tool_input)
            if isinstance(result, str):
                return result
            return json.dumps(result, default=str)
        except Exception as e:
            logger.error("Tool %s failed: %s", tool_name, e, exc_info=True)
            return json.dumps({"error": f"Tool {tool_name} failed: {str(e)}"})

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())
