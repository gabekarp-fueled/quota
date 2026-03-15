"""Agentic loop for Claude API calls.

Pattern: send messages + tools -> check stop_reason -> if tool_use, execute tools and loop
-> if end_turn, return final result.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic

from src.claude.tools import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """Record of a single tool call made during the loop."""

    name: str
    input: dict[str, Any]
    result: str


@dataclass
class AgentResult:
    """Result of a complete agentic loop execution."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    duration_seconds: float = 0.0


async def run_agent_loop(
    client: anthropic.AsyncAnthropic,
    model: str,
    system_prompt: str,
    tools: ToolRegistry,
    user_message: str,
    max_turns: int = 15,
    max_tokens: int = 4096,
) -> AgentResult:
    """Run a Claude agentic loop until completion or max turns.

    Args:
        client: Anthropic async client
        model: Model name (e.g. claude-sonnet-4-20250514)
        system_prompt: System prompt text
        tools: ToolRegistry with registered tools
        user_message: Initial user message to start the loop
        max_turns: Maximum number of API round-trips
        max_tokens: Max tokens per response

    Returns:
        AgentResult with final text, tool call history, and usage stats.
    """
    start_time = time.monotonic()
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    tool_schemas = tools.get_schemas()
    all_tool_calls: list[ToolCall] = []
    total_input_tokens = 0
    total_output_tokens = 0

    for turn in range(max_turns):
        logger.debug("Agent loop turn %d/%d", turn + 1, max_turns)

        try:
            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                tools=tool_schemas if tool_schemas else anthropic.NOT_GIVEN,
                messages=messages,
            )
        except anthropic.APIError as e:
            logger.error("Claude API error on turn %d: %s", turn + 1, e)
            return AgentResult(
                text=f"API error: {e}",
                tool_calls=all_tool_calls,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                turns=turn + 1,
                duration_seconds=time.monotonic() - start_time,
            )

        # Track usage
        if response.usage:
            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

        # Append assistant response to conversation
        messages.append({"role": "assistant", "content": response.content})

        # Check if done
        if response.stop_reason == "end_turn":
            final_text = _extract_text(response)
            logger.info(
                "Agent loop completed in %d turns (%d input, %d output tokens)",
                turn + 1,
                total_input_tokens,
                total_output_tokens,
            )
            return AgentResult(
                text=final_text,
                tool_calls=all_tool_calls,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                turns=turn + 1,
                duration_seconds=time.monotonic() - start_time,
            )

        # Handle tool use
        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info("Executing tool: %s(%s)", block.name, block.input)
                    result_str = await tools.execute(block.name, block.input)
                    all_tool_calls.append(
                        ToolCall(name=block.name, input=block.input, result=result_str)
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_str,
                        }
                    )
            messages.append({"role": "user", "content": tool_results})
        else:
            # Unexpected stop reason
            logger.warning("Unexpected stop_reason: %s", response.stop_reason)
            return AgentResult(
                text=_extract_text(response),
                tool_calls=all_tool_calls,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                turns=turn + 1,
                duration_seconds=time.monotonic() - start_time,
            )

    # Max turns reached
    logger.warning("Agent loop hit max turns (%d)", max_turns)
    return AgentResult(
        text="Max turns reached without completion.",
        tool_calls=all_tool_calls,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        turns=max_turns,
        duration_seconds=time.monotonic() - start_time,
    )


def _extract_text(response) -> str:
    """Extract text content from a Claude response."""
    texts = []
    for block in response.content:
        if hasattr(block, "text"):
            texts.append(block.text)
    return "\n".join(texts) if texts else ""
