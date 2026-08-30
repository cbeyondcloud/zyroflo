"""The provider-swap smoke — item 29, the recurring M2-M6 gate.

The claim under test: the impact-review workflow is executable from
`AGENTS.md` + the Python CLIs ALONE — no MCP, no provider-specific skill
files — so Claude-coupling cannot accrete invisibly in the SOPs. The smoke
has three parts, and each names what it proves:

1. **Breadcrumbs** — the discovery chain a provider follows is closed: the
   target's `AGENTS.md` exists, names the impact-review workflow and the
   CLI-tier prefix, the workflow file it points at ships in the harness, and
   the graph it teaches actually exists at the target.
2. **Execution** — a runner executes the workflow's mechanical spine E2E:
   session clock on (hitl), deterministic pre-pass, context pack, session
   closed. The runner is pluggable (`--runner` = any shell command that
   receives the task prompt as its final argument); the built-in default is
   the **null provider** — a fixed CLI sequence with zero Claude surfaces.
   The null provider proves the CLI tier executes end-to-end; it does NOT
   prove an arbitrary agent can discover the steps — that is what a live
   `--runner` adds, and the gate record must say which ran.
3. **Verification** — artifacts, not exit codes: the review session's one
   summary row exists and is closed, `prepass.json` shows EVERY gate the
   pre-pass runs fired (an unfired gate outranks findings, exit 3 over 1 —
   the pre-pass's own rule, read from the pre-pass's own list, so a gate added
   there is checked here without anyone remembering to), and `pack.md` carries
   a Seeds block naming every diff file.

The smoke executes hitl against ITS OWN scratch metrics dir, never the
adopter's: a two-second mechanical review row in the real `reviews.jsonl`
would bias the exact median the M4 gate reads (item 27's rule, carried
through). The recurring gate's own history goes to
`<target>/.sdlc/metrics/provider-smoke.jsonl` — one row per run, naming the
runner, the MILESTONE it is evidence for and the TIER that ran it, and
`--status` reads it back. That last clause is what makes "green at every
milestone" a file rather than a memory: the first cut wrote this record and
nothing anywhere read it, so the claim was a sentence in this docstring and
an append-only file no question was ever put to.

**The gate must be able to fail, and must not be able to lie.** A max-effort
review of the first cut found it could do neither; each rule below is one of
those defects, closed:

* **A location nobody was told cannot be a requirement.** The artifact paths
  verify() reads have ONE spelling (`artifacts()`), and the live PROMPT names
  them to the runner. A live agent that follows the SOP writes where the SOP's
  `--out` sends it, so demanding an unstated path made every live run red no
  matter how well the provider performed.
* **Only this run's artifacts may vouch for this run.** `reset_scratch()`
  clears the previous run's pre-pass and pack before starting, and verify()
  refuses an artifact older than the run: a stale artifact reads exactly like
  a fresh one — this repository's "empty reads exactly like clean" failure,
  one level up.
* **A run that dies must not poison the next one.** A session left open by a
  dead run is cleared, not inherited: `hitl --begin` refuses while one exists,
  so the recurring gate could otherwise never go green again without a human.
* **Every string that reaches disk or console passes `safe()`** — scrub for
  values harvested from the environment, mask for a credential that exists
  only in the command line, both BEFORE truncation, since a clipped secret is
  still a leak.
* **One grammar, one layout.** Diff paths come from `impact.parse_diff` (the
  new side, so a rename is not a false red) and the metrics layout from
  `hitl.paths` — the generators own their own contracts.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from tools.graph.impact import parse_diff
from tools.review import hitl, prepass

from tools import cli

# Windows is a first-class target: piped stdout is cp1252, and runner output is
# arbitrary text decoded with errors="replace" (U+FFFD alone would raise).
cli.use_utf8()

HARNESS = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
WORKFLOW = "impact-review"

#: The durable record's basename. Spelled once: the writer and the reader had
#: it as two separate literals, so a rename in one of them would have left the
#: reader looking at a file nobody writes — and `--status` answers "no green
#: instance recorded" for an absent file, which is what a silently broken
#: reader looks like. `hitl` keeps its own basenames the same way.
RECORD = "provider-smoke.jsonl"

#: The scratch layout, spelled ONCE. The null provider writes here, the live
#: PROMPT names these paths to the runner, and verify() reads them: a gate whose
#: requirement is undiscoverable is a gate nothing can pass.
PREPASS_SUBDIR, PACK_SUBDIR = "prepass", "pack"

#: The task a LIVE runner receives (its final argument). Provider-neutral on
#: purpose: it names the entry file, the tier rule, and the output locations the
#: verification reads — nothing else. The discovery from AGENTS.md onward is
#: exactly what the live instance tests.
#:
#: "Open and FOLLOW that workflow document, step by step" is load-bearing and
#: is the THIRD instance of item 33's recorded class — a requirement the
#: runner is never told. The first live goose run (2026-08-30, M6-C2) read
#: AGENTS.md, never opened the workflow file it points at, improvised from the
#: block's one example command, invented an `--output` flag and an `hitl
#: record` spelling, and exited clean in 15 seconds — an honest red, but for a
#: requirement stated only as "execute the workflow it names", which an agent
#: can satisfy in its own head without ever reading the steps.
PROMPT = ("Read AGENTS.md at the repository root and execute the impact-review "
          "workflow it names. Open and FOLLOW that workflow document, step by "
          "step, in order - its steps name the exact CLI commands; do not "
          "improvise commands or flags the document does not state. Use ONLY "
          "the CLI tier - no MCP, no provider-specific skill or rules files. "
          "Record the review through the hitl CLI the workflow names, and "
          "close the session before you finish.\n"
          "These paths are exact - the verification reads these and nothing "
          "else. Where the workflow's steps write <repo> in a hitl.py command, "
          "use the hitl-target value below instead - the review must be "
          "recorded there, not in the repository's own metrics. Pass prepass-out "
          "and pack-out as those steps' --out values. Each value runs to the "
          "end of its line, so a path may contain spaces:\n"
          "component: {component}\n"
          "diff: {diff}\n"
          "prepass-out: {prepass}\n"
          "pack-out: {pack}\n"
          "hitl-target: {metrics}\n")


def artifacts(out_dir: Path) -> tuple[Path, Path]:
    """(pre-pass dir, pack dir) — the one spelling, for writer and reader."""
    return out_dir / PREPASS_SUBDIR, out_dir / PACK_SUBDIR


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_tool(cmd: list[str], cwd: Path | None = None,
             env: dict[str, str] | None = None) -> tuple[int, str]:
    """Module-level seam (the prepass pattern): tests fake the transcript."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=str(cwd) if cwd else None,
                              env=env, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, f"did not run: {exc}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# ── credentials: .env in, secrets never out ──────────────────────────────────
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.I)

#: A credential can also arrive INSIDE the runner command (`--api-key sk-...`),
#: where no environment scan can find its value — only its flag.
_SECRET_FLAG = re.compile(r"^--?[\w.-]*(key|token|secret|password|credential)[\w.-]*$",
                          re.I)

#: dotenv strips an inline comment only when whitespace precedes the `#`.
_INLINE_COMMENT = re.compile(r"\s#")


def load_env(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser for the harness `.env`: comments (whole-line
    AND inline) skipped, an `export ` prefix dropped, one optional layer of
    surrounding quotes stripped, no interpolation.

    The grammar tracks dotenv's where that is cheap, because the same file is
    read by both and the shape the shipped `.env.example` teaches —
    `KEY=value  # what it is` — must not arrive with the comment welded onto
    the credential: the padded value would fail authentication AND defeat
    scrubbing, since what the runner echoes is the bare key. A non-UTF-8 file
    is decoded lossily rather than raising: every run reads this file, so one
    mangled byte must not kill a smoke that never needed the credential."""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            cut = _INLINE_COMMENT.search(value)
            if cut:
                value = value[:cut.start()].rstrip()
        if key:
            out[key] = value
    return out


def runner_env() -> dict[str, str]:
    """The LIVE runner's environment: the harness `.env` supplies what the
    shell does not — an exported variable wins over the file (least
    surprise). Only the live runner gets this: the null provider's harness
    CLIs need no LLM credential, and the fewer processes that see a secret,
    the smaller the surface.

    Windows resolves environment names case-INSENSITIVELY, so "the shell wins"
    has to be decided case-insensitively too: a plain setdefault would let the
    file's `Api_Key` shadow the shell's `API_KEY` inside the child."""
    env = dict(os.environ)
    fold = (lambda k: k.upper()) if os.name == "nt" else (lambda k: k)
    have = {fold(k) for k in env}
    for key, value in load_env(HARNESS / ".env").items():
        if fold(key) not in have:
            env[key] = value
            have.add(fold(key))
    return env


def secret_values(env: dict[str, str]) -> set[str]:
    """What scrubbing hides: values of secret-NAMED variables, long enough
    that redaction cannot mangle ordinary words ('true', '8080')."""
    return {v for k, v in env.items()
            if _SECRET_NAME.search(k) and len(v) >= 8}


def scrub(text: str, secrets: set[str]) -> str:
    """Longest-first, so a value containing another is gone before the
    shorter one runs. Applied at capture (BEFORE truncation — a clipped
    secret is still a leak) and again at every sink."""
    for s in sorted(secrets, key=len, reverse=True):
        text = text.replace(s, "<redacted>")
    return text


def mask_inline(text: str) -> str:
    """Redact a credential that lives only in the command line. scrub() hides
    values it can HARVEST from the environment; `--api-key sk-...` passed
    inline is by definition not one of them, and the record is durable."""
    out: list[str] = []
    mask_next = False
    for token in text.split(" "):
        if mask_next and token:
            out.append("<redacted>")
            mask_next = False
            continue
        head, sep, _ = token.partition("=")
        if sep and _SECRET_FLAG.match(head):
            out.append(f"{head}=<redacted>")
            continue
        if _SECRET_FLAG.match(token):
            mask_next = True
        out.append(token)
    return " ".join(out)


def safe(text: str, secrets: set[str]) -> str:
    """The one sink filter: every string that reaches disk or console."""
    return mask_inline(scrub(text, secrets))


def _clip(text: str, secrets: set[str], limit: int = 200) -> str:
    """Redact, THEN truncate. The other order leaves a partial credential."""
    return safe(text, secrets).strip()[:limit]


def split_runner(runner: str) -> list[str]:
    """Tokenise a `--runner` command line.

    POSIX lexing treats a backslash as an escape, so a Windows program path
    would launch with its separators eaten. There the non-POSIX lexer keeps
    them and we strip the quote pair ourselves. Raises ValueError on an
    unbalanced quote; callers turn that into a problem, never a traceback."""
    if os.name == "nt":
        return [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t
                for t in shlex.split(runner, posix=False)]
    return shlex.split(runner)


# ── part 1: the discovery chain ───────────────────────────────────────────────


def breadcrumbs(target: Path) -> list[str]:
    """Every broken link named; an empty list means a provider can walk it."""
    problems: list[str] = []
    agents = target / "AGENTS.md"
    if not agents.is_file():
        return [f"AGENTS.md missing at {target} - a graph install writes it "
                f"(sdlc-init.py); without it there is nothing for a provider to read"]
    text = agents.read_text(encoding="utf-8", errors="replace")
    if WORKFLOW not in text:
        problems.append(f"AGENTS.md never names the {WORKFLOW} workflow")
    if "uv run --project" not in text:
        problems.append("AGENTS.md lacks the CLI-tier prefix (uv run --project ...) "
                        "- the only tier every provider has")
    cited = re.search(r"`([^`]*workflows/impact-review\.md)`", text)
    if not cited:
        problems.append("AGENTS.md cites no path to the impact-review SOP")
    else:
        # A relative citation is read by an agent sitting AT the target, not from
        # whatever directory the smoke happens to have been invoked in.
        sop = Path(cited.group(1))
        if not sop.is_absolute():
            sop = target / sop
        if not sop.is_file():
            problems.append(f"AGENTS.md points at {cited.group(1)}, which does not "
                            f"exist - moved harness clone? re-run sdlc-init.py")
    if not (target / ".sdlc" / "graph" / "graph.jsonl").is_file():
        problems.append(f"no graph at {target / '.sdlc' / 'graph'} - the workflow "
                        f"reviews over a graph that must exist first")
    return problems


# ── part 2: execution ─────────────────────────────────────────────────────────


def reset_scratch(out_dir: Path) -> list[str]:
    """Start from known state; return what had to be cleared.

    Two carried-over states let the gate lie. Last run's pre-pass and pack read
    exactly like this run's, so they would vouch for a runner that wrote
    nothing; and a session left open by a run that died makes every later
    `hitl --begin` refuse, so the gate could never recover without a human.
    Only the smoke's OWN scratch is touched — `reviews.jsonl` is history and
    stays."""
    notes: list[str] = []
    for d in artifacts(out_dir):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            notes.append(f"cleared a stale {d.name}/ left by an earlier run")
    _, pending = hitl.paths(out_dir)
    if pending.exists():
        pending.unlink()
        notes.append("cleared a review session an earlier run left open")
    return notes


def null_provider(target: Path, diff: Path, component: str, component_path: str,
                  out_dir: Path, run=run_tool,
                  secrets: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """The mechanical spine of the SOP, with zero Claude surfaces.

    Steps mirror impact-review.md 1 / 3 / 4 / 9: clock on, machine pre-pass,
    pack, record real machine findings, close. Real diagnostics only — a
    fabricated finding would put a synthetic acceptance row in the stats."""
    py = sys.executable
    hitl_cli = [py, str(HARNESS / "tools" / "review" / "hitl.py"),
                "--target", str(out_dir)]
    pre_dir, pack_dir = artifacts(out_dir)
    problems: list[str] = []

    code, out = run(hitl_cli + ["--begin", "--component", component, "--diff-files",
                                str(_diff_file_count(diff))])
    if code != 0:
        return [f"hitl --begin failed ({code}): {_clip(out, secrets)}"]

    scope = target / component_path if component_path != "." else target
    code, out = run([py, str(HARNESS / "tools" / "review" / "prepass.py"),
                     "--target", str(scope), "--diff", str(diff),
                     "--out", str(pre_dir)])
    report = _read_json(pre_dir / prepass.RESULT_NAME)
    if code == 3:
        problems.append("pre-pass gate could not fire (exit 3, which outranks "
                        "findings): " + _clip(out, secrets))
    elif code == 1 and report is None:
        # Exit 1 is BOTH "findings" and Python's unhandled-traceback exit. The
        # report discriminates: a run that found things still wrote one.
        problems.append("pre-pass exited 1 without leaving a readable report - a "
                        "crash, not findings: " + _clip(out, secrets))
    elif code not in (0, 1):
        problems.append(f"pre-pass crashed ({code}): {_clip(out, secrets)}")

    code, out = run([py, str(HARNESS / "tools" / "graph" / "pack.py"),
                     "--graph-dir", str(target / ".sdlc" / "graph"),
                     "--diff", str(diff), "--component", component,
                     "--out", str(pack_dir), "--no-svg"])
    if code != 0:
        problems.append(f"pack failed ({code}): {_clip(out, secrets)}")

    for d in _prepass_diagnostics(pre_dir / prepass.RESULT_NAME):
        cmd = hitl_cli + ["--finding", d["id"], "--source", "prepass",
                          "--disposition", "accepted"]
        if d.get("severity"):
            cmd += ["--severity", d["severity"]]
        if d.get("path"):
            cmd += ["--path", d["path"]]
        if d.get("line"):
            cmd += ["--line", str(d["line"])]
        code, out = run(cmd)
        if code != 0:
            # Zero silent drops: a finding that failed to record would leave a
            # review row that looks complete and is not.
            problems.append(f"recording pre-pass finding {d['id']} failed "
                            f"({code}): {_clip(out, secrets)}")

    code, out = run(hitl_cli + (["--end", "approved"] if not problems
                                else ["--abandon"]))
    if code != 0:
        problems.append(f"hitl close failed ({code}): {_clip(out, secrets)}")
    return problems


def _diff_file_count(diff: Path) -> int:
    try:
        text = diff.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return len(parse_diff(text))


def _read_json(path: Path):
    """The document, or None — an unreadable artifact is never a traceback."""
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def _prepass_diagnostics(path: Path) -> list[dict]:
    """prepass.json nests diagnostics per source: sources[].diagnostics[]."""
    data = _read_json(path)
    rows = []
    if not isinstance(data, dict):
        return rows
    for source in data.get("sources") or []:
        if not isinstance(source, dict):
            continue
        for d in source.get("diagnostics") or []:
            if not isinstance(d, dict):
                continue
            loc = d.get("location") or {}
            rng = ((loc.get("range") or {}).get("start") or {})
            rows.append({"id": (d.get("code") or {}).get("value") or "prepass-finding",
                         "severity": d.get("severity", ""),
                         "path": loc.get("path", ""), "line": rng.get("line", 0)})
    return rows


def live_provider(runner: str, diff: Path, out_dir: Path, cwd: Path,
                  run=run_tool, secrets: set[str] | None = None,
                  component: str = "") -> list[str]:
    """A real second provider: the runner command gets the task prompt as its
    final argument and everything else is its own affair. What it produced is
    judged by verify() alone — the smoke does not trust a runner's exit code
    to mean the workflow happened. The runner's environment is the shell's
    plus the harness `.env` (runner_env), and everything it prints is
    scrubbed BEFORE truncation — the runner is the one process we hand the
    credential to, so its output is the realistic echo risk."""
    pre_dir, pack_dir = artifacts(out_dir)
    prompt = PROMPT.format(diff=diff, prepass=pre_dir, pack=pack_dir,
                           metrics=out_dir, component=component)
    env = runner_env()
    if secrets is None:
        secrets = secret_values(env)
    try:
        argv = split_runner(runner)
    except ValueError as exc:
        return [f"--runner is not a parseable command line ({exc}): "
                f"{mask_inline(runner)}"]
    if not argv:
        return ["--runner is empty - name the provider command to run"]
    code, out = run(argv + [prompt], cwd=cwd, env=env)
    if code != 0:
        return [f"runner exited {code}: {_clip(out, secrets, 300)}"]
    return []


# ── part 3: verification — artifacts, not exit codes ─────────────────────────


def _predates(path: Path, started: float | None) -> bool:
    """True when the artifact is older than this run (2s of filesystem
    timestamp slack). Only this run's work may vouch for this run."""
    if started is None or not path.exists():
        return False
    try:
        return path.stat().st_mtime < started - 2
    except OSError:
        return False


def verify(out_dir: Path, diff: Path, since: str,
           started: float | None = None) -> list[str]:
    problems: list[str] = []
    reviews, pending = hitl.paths(out_dir)
    rows = []
    for line in cli.jsonl_rows(reviews):
        # A corrupt row is a stated problem, never a traceback: dying here
        # would drop the run out of the recurring record entirely. The two
        # kinds of damage keep their own wording, which is why `cli.Line`
        # carries the reason rather than only the absence.
        if line.problem == cli.UNPARSEABLE:
            problems.append(f"unreadable row at {reviews.name}:{line.number} - "
                            f"the session log is corrupt")
            continue
        if line.problem:
            problems.append(f"row at {reviews.name}:{line.number} is not a "
                            f"session record")
            continue
        started_at = line.row.get("started_at")
        if isinstance(started_at, str) and started_at >= since:
            rows.append(line.row)
    completed = [r for r in rows if r.get("kind") == "review"]
    if not completed:
        problems.append("no completed review session recorded since the smoke "
                        "began (an abandoned row alone is a run that did not finish)")
    if pending.exists():
        problems.append("the review session was never closed "
                        "(review-in-progress.json remains)")

    pre_dir, pack_dir = artifacts(out_dir)
    pj = pre_dir / prepass.RESULT_NAME
    pre = _read_json(pj)
    if not isinstance(pre, dict):
        # `is None` was the only check, and a runner can write any JSON value.
        # `[]` raised AttributeError out of verify(), out of main() - which has
        # a door now, but did not - and the run appended NO row at all, which
        # is the one outcome the recurring record must never have.
        problems.append(f"no readable prepass.json object at {pj}"
                        + ("" if pre is None else
                           f" (the file holds {type(pre).__name__})"))
    elif _predates(pj, started):
        problems.append("prepass.json predates this run - a stale report cannot "
                        "vouch for a run that may never have written one")
    else:
        # ONE call, so this reader cannot adopt the rule in halves. It did:
        # it asked about missing and unfired gates and never about
        # `scope.empty_after_exclude`, so a change whose every file was folded
        # out of review recorded a GREEN smoke while `prepass.exit_code()`
        # returned 3 for the identical document.
        problems += [f"prepass.json: {why}"
                     for why in prepass.unreviewed(pre, expected=prepass.GATES)]

    pack_md = pack_dir / "pack.md"
    try:
        md = pack_md.read_text(encoding="utf-8", errors="replace")
        if _predates(pack_md, started):
            problems.append("pack.md predates this run - a stale pack cannot "
                            "vouch for a run that may never have written one")
        elif "## Seeds" not in md:
            problems.append("pack.md has no Seeds block")
        else:
            # ONE diff grammar, the generator's: parse_diff takes the NEW side,
            # so a rename is judged by the name the pack actually carries.
            diff_files = list(parse_diff(diff.read_text(encoding="utf-8",
                                                        errors="replace")))
            missing = [f for f in diff_files if Path(f).name not in md]
            if missing:
                problems.append(f"pack Seeds block never names {missing[:3]} "
                                f"- every changed file must appear with its mode")
        if not (pack_dir / "pack.json").is_file():
            problems.append("pack.json (the untrimmed machine copy) is missing")
    except OSError:
        problems.append(f"no readable pack.md at {pack_md}")
    return problems


# ── the record ────────────────────────────────────────────────────────────────


def record_path(target: Path) -> Path:
    """Where the recurring gate's history lives — one spelling, two callers."""
    return Path(target) / hitl.METRICS_DIR / RECORD


def record(target: Path, runner_name: str, verdict: str, problems: list[str],
           out_dir: Path, secrets: set[str] | None = None,
           milestone: str = "", tier: str = "null") -> Path:
    """The durable sink filters for itself — every recorded string passes
    through safe() regardless of which code path produced it, and the runner
    field too (an inline `--api-key ...` in the runner command would otherwise
    land on disk verbatim, and no environment scan can find its value)."""
    if tier not in RUNNER_TIERS:
        # The comment below says this field is not operator text. That is
        # only true if something makes it true.
        raise ValueError(f"not a runner tier: {tier!r} - one of "
                         f"{', '.join(RUNNER_TIERS)}")
    if secrets is None:
        secrets = secret_values(runner_env())
    path = record_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    # `tier` is not scrubbed because it is not operator text - it comes from
    # the closed RUNNER_TIERS set below. `milestone` is a label the operator typed.
    row = {"schema_version": SCHEMA_VERSION, "kind": "provider-smoke",
           "at": _now(), "workflow": WORKFLOW,
           "milestone": safe(milestone, secrets), "runner_tier": tier,
           "runner": safe(runner_name, secrets),
           "verdict": verdict, "problems": [safe(p, secrets) for p in problems],
           "artifacts": str(out_dir)}
    # newline="\n": one durable record, appended from Windows and from CI alike.
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return path


# ── the recurring question ───────────────────────────────────────────────────
#
# The record above was append-only from the first cut and READ BY NOTHING - the
# only callers anywhere were its own two tests. So the claim at the top of this
# file, that "green at every milestone" is a file rather than a memory, was not
# true as shipped: the row named no milestone, and no code ever asked the file
# a question. Both halves are closed here.
#
# RUNNER TIER is the sharper half, and measuring found it rather than the
# task line. It is named in full because "tier" is already taken in this
# module - the CLI tier is the ACCESS axis (no MCP, no skill files), and two
# meanings of one word in one file is item 36's vocabulary defect exactly.
# `--runner` was the only thing separating "a live agent ran this" from "a
# fixed CLI sequence ran this" - but the reference runner is ALSO a `--runner`,
# and section 8 of RUNNER_CONTRACT.md already says out loud that its green is
# "a green CONTRACT, never a green agent". This file cannot tell a command with
# a model in it from one without: both are an argv it spawns. So the tier is
# not inferred, it is DECLARED, and the default is the weakest honest claim -
# silence can only under-state, never over-state. `--agent` is the operator's
# attestation, and the record stores it as exactly that.
RUNNER_TIERS = ("null", "contract", "agent")
NULL_RUNNER = "null-provider (CLI-tier closure; not a live agent)"


def _rank(tier: str) -> int:
    """Weakest first, so a stronger tier satisfies a weaker requirement."""
    return RUNNER_TIERS.index(tier) if tier in RUNNER_TIERS else -1


def read_record(target: Path) -> tuple[list[dict], int]:
    """Every provider-smoke row at the target, in the order it was appended,
    plus a count of the lines that could not be read.

    File order, not the `at` field: rows are appended, so the file IS the
    sequence, while `at` is a clock each row wrote about itself.

    An unreadable line is COUNTED and reported - never skipped, never raised
    on. Item 44 recorded the raising half (a required check dying on a
    traceback names no run); the skipping half is this repository's oldest
    failure shape, because a history with holes in it reads exactly like a
    history that is simply short."""
    rows: list[dict] = []
    unreadable = 0
    for line in cli.jsonl_rows(record_path(target)):
        row = line.row
        if row is None or row.get("kind") != "provider-smoke":
            unreadable += 1
            continue
        # A row with NO `runner_tier` predates the field and is read by
        # latest_by_runner_tier below. A row that HAS one and names
        # something that is not a tier is CORRUPT, and must not be
        # laundered into a real one: such a row used to arrive as
        # `contract` and satisfy `--require contract`.
        if "runner_tier" in row and row["runner_tier"] not in RUNNER_TIERS:
            unreadable += 1
            continue
        rows.append(row)
    return rows, unreadable


def latest_by_runner_tier(rows: list[dict]) -> dict[str, dict[str, dict]]:
    """`{milestone: {tier: the last instance recorded at that tier}}`.

    Per (milestone, TIER), because collapsing the tiers loses the answer: a
    green agent instance followed by a red null one means the CLI tier broke,
    not that the agent evidence evaporated, and one verdict per milestone can
    only say one of those things.

    The LAST instance at a tier is the state of that tier. A green superseded
    by a red is not evidence - otherwise a gate could never be un-passed by
    re-running it, which is the only way anyone would find out.

    A row with no milestone is filed under `""`. The field arrived after the
    first instances were recorded, and folding those into some milestone would
    invent evidence while dropping them would be the same silent hole
    read_record refuses."""
    out: dict[str, dict[str, dict]] = {}
    for row in rows:
        milestone = str(row.get("milestone") or "")
        tier = str(row.get("runner_tier") or "")
        if not tier:
            # Written before the field existed: `runner` was the only signal,
            # and the built-in default is the one runner name this file itself
            # chose. Anything else was an operator's own command, whose tier
            # nobody attested - "contract" is the weakest claim that fits.
            tier = "null" if str(row.get("runner", "")).startswith("null-provider") \
                else "contract"
        out.setdefault(milestone, {})[tier] = row
    return out


def status(target: Path, milestone: str = "",
           require: str = "") -> tuple[str, int]:
    """The report, plus the exit code for the question - 0 satisfied, 1 not.

    With no `--milestone` nothing has been ASKED, so the report exits 0: it is
    a view, like `hitl --show`. Naming a milestone is what makes it a gate."""
    if require and _rank(require) < 0:
        return (f"!! not a runner tier: {require!r} - one of "
                f"{', '.join(RUNNER_TIERS)}", 2)
    if require and not milestone:
        # Accepting this and ignoring it would ship the exact defect this
        # function exists to close: a flag that is read by nothing.
        return ("!! --require needs a --milestone to be about", 2)
    rows, unreadable = read_record(target)
    state = latest_by_runner_tier(rows)
    lines: list[str] = []
    if unreadable:
        lines.append(f"!! {unreadable} unreadable row(s) in the record - "
                     f"this history is incomplete and cannot be counted")
    if not rows:
        lines.append("no provider-swap instances recorded here yet")
    # "" sorts last under this key: the unattributed rows are a footnote to the
    # milestones, not a milestone of their own.
    for key in sorted(state, key=lambda k: (k == "", k)):
        shown = [f"{tier}={state[key][tier].get('verdict', '?')} "
                 f"({state[key][tier].get('at', '?')})"
                 for tier in reversed(RUNNER_TIERS) if tier in state[key]]
        label = key or "(no milestone recorded - satisfies nothing)"
        lines.append(f"  {label}: " + ", ".join(shown))
    if not milestone:
        return "\n".join(lines), 0
    if unreadable:
        # It printed "cannot be counted" and then counted it. Exit 3 is
        # this project's word for could-not-look, and it outranks both
        # answers for the reason `prepass.exit_code` states: the question
        # is not answered while part of the evidence is unreadable.
        lines.append(f"!! {milestone}: the record cannot be counted, so "
                     f"it cannot evidence this milestone either way")
        return "\n".join(lines), 3
    want = require or RUNNER_TIERS[0]
    asked = (f"at the {want} runner tier or stronger" if require
             else "at any runner tier")
    green = [tier for tier, row in state.get(milestone, {}).items()
             if row.get("verdict") == "green" and _rank(tier) >= _rank(want)]
    if green:
        best = max(green, key=_rank)
        lines.append(f"OK - {milestone}: green at the {best} runner tier"
                     + (f", and {require} was required" if require
                        else f" ({asked})"))
        # A pass that leaves a stronger tier's red unsaid is a green with one
        # hidden inside it. The exit code answers what was ASKED; this line
        # answers what the reader is about to assume from it.
        unasked = [t for t, row in state[milestone].items()
                   if row.get("verdict") != "green" and _rank(t) > _rank(best)]
        for t in sorted(unasked, key=_rank, reverse=True):
            lines.append(f"   .. but {milestone}'s {t} runner tier is "
                         f"{state[milestone][t].get('verdict', '?')} and "
                         f"nothing asked about it")
        return "\n".join(lines), 0
    lines.append(f"!! {milestone}: NO green instance {asked} - the recurring "
                 f"gate is UNSATISFIED for it")
    return "\n".join(lines), 1


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Provider-swap smoke: impact-review E2E from AGENTS.md + CLIs "
                    "alone (recurring gate, M2-M6).")
    ap.add_argument("--target", required=True, metavar="DIR",
                    help="repo/workspace root with an installed harness "
                         "(AGENTS.md + .sdlc/graph)")
    ap.add_argument("--diff", metavar="FILE",
                    help="required unless --status")
    ap.add_argument("--component", metavar="ALIAS",
                    help="the diff's component (diffs do not say); required "
                         "unless --status")
    ap.add_argument("--milestone", default="", metavar="LABEL",
                    help="which milestone this instance is evidence for. Free "
                         "text on purpose - an adopter's milestones are their "
                         "own, and a list of THIS project's would be the one "
                         "thing a generic harness must not hard-code. Omitted "
                         "records the instance UNATTRIBUTED, which satisfies "
                         "no milestone and says so at the end of the run")
    ap.add_argument("--agent", action="store_true",
                    help="with --runner: attest that this command has a MODEL "
                         "in it. This file cannot tell - the reference runner "
                         "and a live agent are both an argv it spawns - so a "
                         "--runner alone records the `contract` tier, the "
                         "weakest honest claim, and this flag is what raises "
                         "it to `agent`")
    ap.add_argument("--status", action="store_true",
                    help="read the recurring gate's own record and report it, "
                         "writing nothing. Add --milestone to ASK about one "
                         "(exit 1 = no green instance for it)")
    ap.add_argument("--require", default="", metavar="TIER",
                    help=f"with --status --milestone: the weakest runner "
                         f"tier that "
                         f"counts as satisfied ({', '.join(RUNNER_TIERS)}); a "
                         f"stronger one satisfies a weaker requirement")
    ap.add_argument("--component-path", default=".", metavar="REL",
                    help="component dir relative to target, for the pre-pass "
                         "scope (default: the target itself)")
    ap.add_argument("--runner", metavar="CMD",
                    help="a live provider command; it receives the task prompt "
                         "as its final argument, and its environment is the "
                         "shell's plus the harness .env (shell wins) - put the "
                         "provider's API key in <harness>/.env rather than in "
                         "this command line. Everything it prints is "
                         "secret-scrubbed before it can reach the record. "
                         "Omitted = the built-in null provider "
                         "(CLI-tier closure proof, not a live agent)")
    ap.add_argument("--out", metavar="DIR",
                    help="scratch dir for artifacts (default: "
                         "<target>/.sdlc/.tmp/provider-smoke); its prepass/ and "
                         "pack/ are cleared at start, so only this run's work "
                         "can vouch for this run")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    if not target.is_dir():
        print(f"!! no directory at {target} - --target is the installed "
              f"repo/workspace root")
        return 2
    if args.status:
        report, code = status(target, args.milestone, args.require)
        print(f"provider-swap smoke record at {target}:")
        print(report)
        return code
    if args.require:
        print("!! --require states the tier a MILESTONE must already have, "
              "which is a question about the record - pass it with --status. "
              "On a run it would have been read by nothing")
        return 2
    if args.agent and not args.runner:
        print("!! --agent attests that a RUNNER has a model in it, and "
              "no --runner was given - the run would have recorded the "
              "null tier and dropped the attestation")
        return 2
    if not args.diff or not args.component:
        print("!! --diff and --component are required for a smoke run "
              "(--status reads the record instead)")
        return 2
    diff = Path(args.diff).resolve()
    if not diff.is_file():
        print(f"!! no diff at {diff}")
        return 2
    out_dir = Path(args.out).resolve() if args.out else \
        target / ".sdlc" / ".tmp" / "provider-smoke"
    out_dir.mkdir(parents=True, exist_ok=True)

    secrets = secret_values(runner_env())
    for note in reset_scratch(out_dir):
        print(f"  .. {note}")

    # An unexpected failure below is still an ATTEMPT at this milestone, and it
    # used to leave no row at all - so `--status` could not tell "never tried"
    # from "tried and died", which is the distinction the unreadable-row
    # counting exists to preserve. The failure becomes a red problem and the
    # record is written exactly as any other red.
    crashed = None
    problems = breadcrumbs(target)
    since, started = _now(), time.time()
    if not problems:
        try:
            if args.runner:
                problems += live_provider(args.runner, diff, out_dir, cwd=target,
                                          secrets=secrets,
                                          component=args.component)
            else:
                problems += null_provider(target, diff, args.component,
                                          args.component_path, out_dir,
                                          secrets=secrets)
            problems += verify(out_dir, diff, since, started=started)
        except Exception as exc:
            crashed = (f"the smoke did not finish: {type(exc).__name__}: {exc}")
            problems.append(crashed)

    runner_name = args.runner or NULL_RUNNER
    tier = ("agent" if args.agent else "contract") if args.runner else "null"
    verdict = "green" if not problems else "red"
    rec = record(target, runner_name, verdict, problems, out_dir,
                 secrets=secrets, milestone=args.milestone, tier=tier)
    print(f"provider-swap smoke [{WORKFLOW}]: {verdict}   "
          f"runner: {safe(runner_name, secrets)}")
    for p in problems:
        print(f"  !! {safe(p, secrets)}")
    print(f"  record: {rec}   runner tier: {tier}   milestone: "
          f"{safe(args.milestone, secrets) or '(none)'}")
    if not args.milestone:
        print("  .. this instance is UNATTRIBUTED and satisfies no milestone "
              "- pass --milestone to make it count")
    if crashed:
        # 3, not 1: a run that died did not judge the target, and 1 here is a
        # judged red. The row is already written either way.
        return 3
    return 0 if not problems else 1


def main(argv: list[str] | None = None) -> int:
    """The door, for a failure before a record can be written at all (a
    bad `--target`, an unreadable `.env`). Once the target is known, the
    smoke catches its own failure and records it as a red instead, so the
    attempt still appears in the recurring record."""
    return cli.door(_main, argv, "the provider-swap smoke", 3)


if __name__ == "__main__":
    raise SystemExit(main())
