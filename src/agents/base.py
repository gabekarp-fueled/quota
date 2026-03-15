from abc import ABC, abstractmethod

import anthropic

from src.claude.tools import ToolRegistry


class BaseAgent(ABC):
    """Base class for all Quota agents.

    Each agent receives:
    - attio: Shared Attio CRM client for reading/writing records
    - claude_client: Anthropic async client for Claude API calls
    - system_prompt: Pre-loaded system prompt text (shared + agent-specific)
    - tool_registry: Pre-built registry of tools this agent can use
    - model: Which Claude model to use
    - batch_size: How many items to process per heartbeat
    """

    name: str = "base"

    def __init__(
        self,
        attio,
        claude_client: anthropic.AsyncAnthropic | None = None,
        system_prompt: str = "",
        tool_registry: ToolRegistry | None = None,
        model: str = "claude-sonnet-4-20250514",
        batch_size: int = 5,
    ):
        self.attio = attio
        self.claude_client = claude_client
        self.system_prompt = system_prompt
        self.tool_registry = tool_registry or ToolRegistry()
        self.model = model
        self.batch_size = batch_size

    @abstractmethod
    async def run(self, focus: str | None = None) -> dict:
        """Execute one heartbeat cycle. Returns result summary.

        Args:
            focus: Optional directive from CRO — what to prioritize or skip this run.
        """
        ...
