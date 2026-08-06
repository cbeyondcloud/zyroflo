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
4 Review loop          re-review until no critical/high finding is open (max 5 iterations);
                       the /10 rubric score is reported, never the gate
5 Triage               findings become top-priority tracker rows
GATE B  (you approve)  <-- STOPS
6 Fix cycle            loop 1-5 until no pending bug rows
7 Test & coverage gate full suite >= baseline, >=90% on changed files
8 Close-out            verify every criterion, docs, final commit, retrospective
9 Deploy (optional)    only on explicit approval; never auto-runs a paid action
```

## Why it's generic

Everything workspace-specific lives in **`profile.yml`** — auto-detected (test/coverage/lint/build/
deploy commands, threshold, tools). The skill in `.claude/skills/sdlc/SKILL.md` reads the profile and
**hardcodes no command, no provider, no model**. The installer fans the same skill out to every AI
coding tool it detects (Claude Code, Cursor, Windsurf, Copilot, Gemini, Aider), each in its native
format. Durable state is plain markdown in `.sdlc/plans/`, so a run resumes across sessions and tools.

Genericity runs on three axes, each of which is meant to be a test rather than a claim:

- **Provider-agnostic** — no provider, model, or tool name in the lifecycle.
- **Stack-agnostic** — commands are profile data; the harness never assumes pytest or Maven.
- **Scale-agnostic** — see below.

## Two tiers, one lifecycle

**One repo with zero dependencies and a multi-repo workspace with a graph substrate are the
same lifecycle.** They differ only in which capabilities are present and which phases the profile
activates — not in gates, not in artifacts, not in the review loop. Neither is a degraded
version of the other.

| | **Tier 0 — drop-in** | **Tier 1/2 — estate** |
|---|---|---|
| Scope | one repo | N repos (`components[]`) |
| Dependencies | **none** — no packages, no network, no API keys | uv, Docker, Neo4j |
| Phases active | 8 | 12 |
| Change review | whole branch diff at G4 | every agent diff, individually |
| Impact analysis | agent explores the repo | graph blast radius with `file:line` provenance |
| Install | `python sdlc-init.py` | `python sdlc-init.py --estate <path>` |

The full phase list, the capability-degradation table, and the gate regimes are specified in
**[`sdlc/LIFECYCLE.md`](sdlc/LIFECYCLE.md)**. Tier 0 is not a stripped-down mode you graduate
out of — it is the whole product for a solo developer, and its "no packages, no network"
property is a gate the estate tier is not allowed to break.

> **Status (2026-08-05):** Tier 0 is complete and is what `/sdlc` runs today. The estate tier
> is under construction — `profile.yml` schema v2, the estate-agnostic core library behind the
> `GraphStore` seam, and the Neo4j v1 store have landed; **the extractors, the graph builder
> and the pipeline layer have not**, so no graph can be built yet. `SKILL.md` still implements
> the 10-phase numbering it shipped with; `LIFECYCLE.md` documents the mapping onto the
> 12-phase superset.

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
| `--estate DIR` | Estate install: attach N repos, bring up the graph substrate *(under construction)* |
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
python sdlc-init.py --target ../your-repo --claude-extras
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
