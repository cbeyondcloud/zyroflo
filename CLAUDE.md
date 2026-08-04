# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## WAT Framework Overview

This repo uses the **WAT architecture** (Workflows, Agents, Tools) to keep AI reasoning separate from deterministic execution:

- **`workflows/`** — Markdown SOPs. Each file defines an objective, required inputs, which tool(s) to call, expected outputs, and edge-case handling.
- **`tools/`** — Python scripts that do the actual work: API calls, data transforms, file I/O, database queries.
- **`.tmp/`** — Disposable intermediate files (scraped data, exports). Safe to delete; regenerated on demand.
- **`.env`** — All credentials and API keys. Never store secrets anywhere else.
- **`credentials.json`, `token.json`** — Google OAuth tokens (gitignored).

Final outputs go to cloud services (Google Sheets, Slides, etc.), not local files.

## Running Tools

```bash
# Standard invocation — always run from repo root so .env is found
python tools/<script_name>.py

# .env is loaded inside each script via python-dotenv; no manual export needed
```

Before running any tool that makes paid API calls or consumes credits, confirm with the user first.

## How to Operate as Agent

1. **Check `tools/` before building anything.** Only create a new script when no existing tool covers the task.
2. **Always read the relevant `workflows/*.md` first.** It specifies which tool to call and in what order.
3. **On error:** read the full traceback, fix the script, retest. Then update the workflow with what you learned (rate limits, pagination quirks, retry logic, etc.).
4. **Don't create or overwrite workflow files without being asked.** Workflows are the user's persistent instructions — update them only when explicitly directed or when fixing a documented constraint.

## Workflow File Conventions

Each `workflows/*.md` must include these sections:

```
## Objective
## Required Inputs
## Steps          # ordered list of tool calls with args
## Expected Output
## Edge Cases / Notes
```

When you learn something new (a rate limit, a required header, an API quirk), add it to the relevant workflow's **Edge Cases / Notes** section.

## Self-Improvement Loop

When something breaks: fix the tool → verify the fix → update the workflow. This keeps the system accurate across future runs.
