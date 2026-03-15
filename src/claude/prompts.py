"""System prompt loader for Claude agents.

Reads markdown prompt files from the prompts/ directory.
Each agent gets: shared.md (company context) + {agent_name}.md (specific instructions).
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Resolve prompts directory relative to the package root
_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def load_prompt(agent_name: str) -> str:
    """Load and concatenate system prompt for an agent.

    Reads prompts/shared.md + prompts/{agent_name}.md and returns concatenated text.
    """
    shared_path = _PROMPTS_DIR / "shared.md"
    agent_path = _PROMPTS_DIR / f"{agent_name}.md"

    parts = []

    if shared_path.exists():
        parts.append(shared_path.read_text(encoding="utf-8").strip())
    else:
        logger.warning("Shared prompt not found: %s", shared_path)

    if agent_path.exists():
        parts.append(agent_path.read_text(encoding="utf-8").strip())
    else:
        logger.warning("Agent prompt not found: %s", agent_path)

    prompt = "\n\n---\n\n".join(parts)
    logger.info("Loaded prompt for %s (%d chars)", agent_name, len(prompt))
    return prompt


def load_all_prompts() -> dict[str, str]:
    """Load prompts for all agents. Returns dict of {agent_name: prompt_text}."""
    agents = [
        "scout",
        "outreach",
        "enablement",
        "channels",
        "cro",
        "inbox",
        "digest",
        "followup",
    ]
    return {name: load_prompt(name) for name in agents}
