# Contributing to Quota

Thank you for your interest in contributing. Quota is an open-source AI sales agent framework and welcomes contributions of all kinds: bug fixes, new integrations, documentation improvements, and new agents.

## Getting Started

1. Fork the repository and clone your fork
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```
3. Copy `.env.example` to `.env` and fill in at minimum `DATABASE_URL` and `ANTHROPIC_API_KEY`
4. Run the development server:
   ```bash
   uvicorn src.main:app --reload
   ```

## Project Structure

```
src/
  agents/       # Agent implementations (extend BaseAgent)
  tools/        # Tool registries passed to agents
  routers/      # FastAPI routers (api, heartbeats, webhooks, health)
  claude/       # Anthropic client wrapper and prompt loader
  db/           # SQLAlchemy models and session management
  config.py     # Settings (loaded from environment)
  main.py       # FastAPI app entrypoint
  scheduler.py  # Background asyncio scheduler

prompts/        # Markdown system prompt files (editable via UI)
ui/             # React dashboard (Vite + Tailwind)
```

## Adding a New Agent

1. Create `src/agents/your_agent.py` extending `BaseAgent`
2. Implement `async def run(self, focus: str | None = None) -> dict`
3. Register your tools in `run()` using `ToolRegistry`
4. Add a prompt file at `prompts/your_agent.md`
5. Add a heartbeat endpoint in `src/routers/heartbeats.py`
6. Seed the agent in `src/main.py` → `_AGENT_DEFAULTS`
7. Add the agent name to `_VALID_AGENTS` in `src/tools/dispatch_tools.py` if CRO should be able to dispatch it

## Adding a New Integration

Create a new file in `src/tools/` following the pattern of existing tool files:

```python
from src.claude.tools import ToolRegistry

def register_my_tools(registry: ToolRegistry, client: MyClient) -> None:
    registry.register(
        name="my_tool_name",
        description="...",
        input_schema={"type": "object", "properties": {...}, "required": [...]},
        handler=my_async_handler,
    )
```

Make the integration optional (wrap in `try/except ImportError`) so Quota works without it.

## Code Style

- Python: [Ruff](https://docs.astral.sh/ruff/) is configured in `pyproject.toml`. Run `ruff check .` and `ruff format .` before committing.
- JavaScript/JSX: Prettier defaults. No semicolons required (the existing code omits them in some files — be consistent with the file you're editing).
- Keep agent implementations stateless where possible; share state through the database.
- All personally identifiable information, company names, and product descriptions must be stripped from any contributed prompts. Use `[YOUR X]` placeholders.

## Pull Requests

- Target the `main` branch
- Include a clear description of what the PR does and why
- If adding a new integration, include setup instructions in the PR description
- Tests are appreciated but not required for initial contributions

## Reporting Issues

Open a GitHub issue with:
- A clear description of the problem
- Steps to reproduce
- Your Python version and OS
- Relevant log output (redact API keys)
