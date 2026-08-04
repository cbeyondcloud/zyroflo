# SDLC Harness

A portable, **provider-agnostic** and **tool-agnostic** software-development lifecycle you drop into
**any** repo. One command scaffolds it; one command per task runs the whole lifecycle end-to-end —
under any LLM, inside any AI coding tool.

```bash
# 1. Install into a repo (run at its root, or use --target)
python /path/to/sdlc-init.py

# 2. In any AI coding tool opened on that repo:
/sdlc fix the null-pointer in the CSV importer
/sdlc add pagination to the /orders endpoint
/sdlc resume                                     # continue an interrupted run
```

No third-party packages. No network. No API keys. Free and OSS (MIT).

## What it does

Takes a single input — a bug or feature — through **10 phases** with **two human approval gates**:

```
0 Intake & Baseline    clean-tree + baseline tests + scope triage + explore
1 Requirements         clarify sweep, 2-4 options (incl. a free/OSS one), EARS criteria
2 Planning             plan doc + tracker in .sdlc/plans/, consistency check
GATE A  (you approve)  <-- STOPS
3 Implementation       branch, parallel worktrees where supported, TDD, commit per row
4 Review loop          score vs rubric /10, re-review until >=9.5 (max 5 iterations)
5 Triage               findings become top-priority tracker rows
GATE B  (you approve)  <-- STOPS
6 Fix cycle            loop 1-5 until no pending bug rows
7 Test & coverage gate full suite >= baseline, >=90% on changed files
8 Close-out            verify every criterion, docs, final commit, retrospective
9 Deploy (optional)    only on explicit approval; never auto-runs a paid action
```

## Why it's generic

Everything repo-specific lives in **`.sdlc/profile.yml`** — auto-detected (test/coverage/lint/build/
deploy commands, threshold, tools). The skill in `.claude/skills/sdlc/SKILL.md` reads the profile and
**hardcodes no command, no provider, no model**. The installer fans the same skill out to every AI
coding tool it detects (Claude Code, Cursor, Windsurf, Copilot, Gemini, Aider), each in its native
format. Durable state is plain markdown in `.sdlc/plans/`, so a run resumes across sessions and tools.

## What gets installed into a repo

```
.claude/skills/sdlc/SKILL.md      # + one entry point per detected tool (.cursor/rules/…, GEMINI.md, …)
.sdlc/profile.yml                 # auto-detected; the one file you edit
.sdlc/review-rubric.md
.sdlc/plan-template.md
.sdlc/plans/                      # per-run plan + tracker docs (durable, resumable state)
```

## Installer reference

| Flag | Purpose |
|------|---------|
| `--target DIR` | Install into `DIR` (default: current directory) |
| `--dry-run` | Show planned writes, change nothing |
| `--tools auto\|all\|<list>` | Which tools to wire (default: `auto` = detect; Claude always included) |
| `--force` | Overwrite a hand-edited `.sdlc/profile.yml` |
| `--claude-extras` | Also install the Claude Code power layer (subagents + hooks + permission allowlist) |
| `--bundle OUT` | Emit a single self-contained script (templates base64-embedded) |
| `--dump-templates DIR` | Write the templates out for editing |

Idempotent: re-running updates generated files in place, never duplicates, and never clobbers a
hand-edited `profile.yml` (it stages `profile.yml.new` instead). Console output is ASCII and stdout is
reconfigured to UTF-8, so it runs on a default Windows cp1252 terminal.

## Repos where raw `git` is unsafe

Some repos must not be driven with plain git — open-core splits with a second git dir, monorepos
that route files per destination, signing/policy wrappers. `profile.yml` has a `vcs:` block for this:

```yaml
vcs:
  commit_command: "./checkin-push.sh \"{message}\""   # {message} = commit text
  push_command: ""                                     # {branch} = branch name
  deny_raw_git: true    # harness must NEVER call git commit/push/worktree
  notes: "open-core split: .git is public, .git-commercial is private"
```

The installer detects this automatically: a `.git-*` dir beside `.git` sets `deny_raw_git: true`,
and a repo-level check-in script (`checkin-push.sh`, `commit-push.sh`, …) is wired as
`commit_command`. When `deny_raw_git` is on, the harness commits through your script, refuses to
`git push`, **and skips git worktrees** — a worktree carries only `.git`, so the second repo would
be silently lost. `--claude-extras` mirrors this in `settings.json` (raw `git commit`/`push`/
`worktree` denied, your script allowed). If the signal fires but no script is found, `/sdlc` stops
at Phase 0 and asks rather than guessing.

## Detected stacks

Python (pytest) · JavaScript/TypeScript (jest/vitest) · Go · Rust · Java (Maven/Gradle) ·
**.NET/C#** (dotnet test) · **Ruby** (rspec/rake + SimpleCov) · **C/C++** (CMake + ctest) ·
plus a generic fallback that leaves commands blank for you to fill.

## Optional: Claude Code power layer (`--claude-extras`)

The base install is tool-neutral. Add `--claude-extras` to also install a Claude Code-only layer
(other tools ignore it):

- **Reviewer subagents** — `.claude/agents/sdlc-reviewer.md` + `sdlc-security.md`. Phase 4 delegates
  to these isolated-context reviewers for sharper, more independent findings.
- **Permission allowlist** — `.claude/settings.json` auto-allowing exactly the profile's
  test/coverage/lint/build binaries and safe local git (status/diff/commit/branch/worktree), so the
  automated loop doesn't stall on prompts. Destructive ops (force-push, `reset --hard`, `clean -f`,
  `rm -rf`) are explicitly denied. Merges with any existing `settings.json` — never clobbers.
- **Automation hooks** — `.claude/hooks/`: a PostToolUse hook auto-runs the linter after edits
  (non-blocking), and a SessionStart hook surfaces any in-progress run so you can `/sdlc resume`.
- **Default-pointer block** — a managed block in `CLAUDE.md` / `AGENTS.md` so "fix this bug" /
  "add this feature" routes to `/sdlc` from session start.

`.mcp.json` isn't installed, but the skill is MCP-aware: if a GitHub/JIRA/Linear or Slack MCP is
connected, it offers to pull the task from an issue and post the tracker/close-out back.

```bash
python sdlc-init.py --target <second-workspace> --claude-extras
```

## Editing the harness

The canonical templates are `sdlc_templates/{SKILL.md, review-rubric.md, plan-template.md,
profile.yml}`. Edit those, then re-run the installer in a repo to refresh every tool. Tune scoring
per repo by pointing `review.rubric_overlay` in `profile.yml` at a repo-specific additions file.

For a single droppable file (no `sdlc_templates/` sibling needed):

```bash
python sdlc-init.py --bundle sdlc-init.bundled.py
```

## License

MIT — see [LICENSE](LICENSE).
