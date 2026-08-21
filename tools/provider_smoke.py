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
   summary row exists and is closed, `prepass.json` shows BOTH gates fired
   (an unfired gate outranks findings, exit 3 over 1 — the pre-pass's own
   rule), and `pack.md` carries a Seeds block naming every diff file.

The smoke executes hitl against ITS OWN scratch metrics dir, never the
adopter's: a two-second mechanical review row in the real `reviews.jsonl`
would bias the exact median the M4 gate reads (item 27's rule, carried
through). The recurring gate's own history goes to
`<target>/.sdlc/metrics/provider-smoke.jsonl` — one row per run, naming the
runner, so "green at every milestone" is a file, not a memory.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
WORKFLOW = "impact-review"

#: The task a LIVE runner receives (its final argument). Provider-neutral on
#: purpose: it names the entry file and the tier rule, nothing else — the
#: discovery from AGENTS.md onward is exactly what the live instance tests.
PROMPT = ("Read AGENTS.md at the repository root and execute the impact-review "
          "workflow it names, for the diff at {diff}. Use ONLY the CLI tier - "
          "no MCP, no provider-specific skill or rules files. Record the review "
          "through the hitl CLI the workflow names, with --target {metrics}, "
          "then close the session.")


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


def load_env(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser for the harness `.env`: comments and blanks
    skipped, one optional layer of surrounding quotes stripped, no
    interpolation. The harness ships no dotenv dependency and this stays
    zero-dependency too."""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def runner_env() -> dict[str, str]:
    """The LIVE runner's environment: the harness `.env` supplies what the
    shell does not — an exported variable wins over the file (least
    surprise). Only the live runner gets this: the null provider's harness
    CLIs need no LLM credential, and the fewer processes that see a secret,
    the smaller the surface."""
    env = dict(os.environ)
    for key, value in load_env(HARNESS / ".env").items():
        env.setdefault(key, value)
    return env


def secret_values(env: dict[str, str]) -> set[str]:
    """What scrubbing hides: values of secret-NAMED variables, long enough
    that redaction cannot mangle ordinary words ('true', '8080')."""
    return {v for k, v in env.items()
            if _SECRET_NAME.search(k) and len(v) >= 8}


def scrub(text: str, secrets: set[str]) -> str:
    """Longest-first, so a value containing another is gone before the
    shorter one runs. Applied at capture for the live runner (BEFORE
    truncation — a clipped secret is still a leak) and again at every sink."""
    for s in sorted(secrets, key=len, reverse=True):
        text = text.replace(s, "<redacted>")
    return text


# ── part 1: the discovery chain ───────────────────────────────────────────────


def breadcrumbs(target: Path) -> list[str]:
    """Every broken link named; an empty list means a provider can walk it."""
    problems: list[str] = []
    agents = target / "AGENTS.md"
    if not agents.is_file():
        return [f"AGENTS.md missing at {target} - a graph install writes it "
                f"(sdlc-init.py); without it there is nothing for a provider to read"]
    text = agents.read_text(encoding="utf-8")
    if WORKFLOW not in text:
        problems.append(f"AGENTS.md never names the {WORKFLOW} workflow")
    if "uv run --project" not in text:
        problems.append("AGENTS.md lacks the CLI-tier prefix (uv run --project ...) "
                        "- the only tier every provider has")
    cited = re.search(r"`([^`]*workflows/impact-review\.md)`", text)
    if not cited:
        problems.append("AGENTS.md cites no path to the impact-review SOP")
    elif not Path(cited.group(1)).is_file():
        problems.append(f"AGENTS.md points at {cited.group(1)}, which does not exist "
                        f"- moved harness clone? re-run sdlc-init.py")
    if not (target / ".sdlc" / "graph" / "graph.jsonl").is_file():
        problems.append(f"no graph at {target / '.sdlc' / 'graph'} - the workflow "
                        f"reviews over a graph that must exist first")
    return problems


# ── part 2: execution ─────────────────────────────────────────────────────────


def null_provider(target: Path, diff: Path, component: str, component_path: str,
                  out_dir: Path, run=run_tool) -> list[str]:
    """The mechanical spine of the SOP, with zero Claude surfaces.

    Steps mirror impact-review.md 1 / 3 / 4 / 9: clock on, machine pre-pass,
    pack, record real machine findings, close. Real diagnostics only — a
    fabricated finding would put a synthetic acceptance row in the stats."""
    py = sys.executable
    hitl = [py, str(HARNESS / "tools" / "review" / "hitl.py"),
            "--target", str(out_dir)]
    problems: list[str] = []

    code, out = run(hitl + ["--begin", "--component", component, "--diff-files",
                            str(_diff_file_count(diff))])
    if code != 0:
        return [f"hitl --begin failed ({code}): {out.strip()[:200]}"]

    scope = target / component_path if component_path != "." else target
    code, out = run([py, str(HARNESS / "tools" / "review" / "prepass.py"),
                     "--target", str(scope), "--diff", str(diff),
                     "--out", str(out_dir / "prepass")])
    if code == 3:
        problems.append("pre-pass gate could not fire (exit 3, which outranks "
                        "findings): " + out.strip()[:200])
    elif code not in (0, 1):
        problems.append(f"pre-pass crashed ({code}): {out.strip()[:200]}")

    code, out = run([py, str(HARNESS / "tools" / "graph" / "pack.py"),
                     "--graph-dir", str(target / ".sdlc" / "graph"),
                     "--diff", str(diff), "--component", component,
                     "--out", str(out_dir / "pack"), "--no-svg"])
    if code != 0:
        problems.append(f"pack failed ({code}): {out.strip()[:200]}")

    for d in _prepass_diagnostics(out_dir / "prepass" / "prepass.json"):
        cmd = hitl + ["--finding", d["id"], "--source", "prepass",
                      "--disposition", "accepted"]
        if d.get("severity"):
            cmd += ["--severity", d["severity"]]
        if d.get("path"):
            cmd += ["--path", d["path"]]
        if d.get("line"):
            cmd += ["--line", str(d["line"])]
        run(cmd)

    verdict = "approved" if not problems else "rework"
    code, out = run(hitl + (["--end", verdict] if not problems else ["--abandon"]))
    if code != 0:
        problems.append(f"hitl close failed ({code}): {out.strip()[:200]}")
    return problems


def _diff_file_count(diff: Path) -> int:
    try:
        text = diff.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return len(re.findall(r"^diff --git ", text, flags=re.M))


def _prepass_diagnostics(path: Path) -> list[dict]:
    """prepass.json nests diagnostics per source: sources[].diagnostics[]."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = []
    for source in data.get("sources", []):
        for d in source.get("diagnostics", []):
            loc = d.get("location") or {}
            rng = ((loc.get("range") or {}).get("start") or {})
            rows.append({"id": (d.get("code") or {}).get("value") or "prepass-finding",
                         "severity": d.get("severity", ""),
                         "path": loc.get("path", ""), "line": rng.get("line", 0)})
    return rows


def live_provider(runner: str, diff: Path, out_dir: Path, cwd: Path,
                  run=run_tool) -> list[str]:
    """A real second provider: the runner command gets the task prompt as its
    final argument and everything else is its own affair. What it produced is
    judged by verify() alone — the smoke does not trust a runner's exit code
    to mean the workflow happened. The runner's environment is the shell's
    plus the harness `.env` (runner_env), and everything it prints is
    scrubbed BEFORE truncation — the runner is the one process we hand the
    credential to, so its output is the realistic echo risk."""
    prompt = PROMPT.format(diff=diff, metrics=out_dir)
    env = runner_env()
    secrets = secret_values(env)
    code, out = run(shlex.split(runner) + [prompt], cwd=cwd, env=env)
    if code != 0:
        return [f"runner exited {code}: {scrub(out, secrets).strip()[:300]}"]
    return []


# ── part 3: verification — artifacts, not exit codes ─────────────────────────


def verify(out_dir: Path, diff: Path, since: str) -> list[str]:
    problems: list[str] = []
    reviews = out_dir / ".sdlc" / "metrics" / "reviews.jsonl"
    rows = []
    if reviews.is_file():
        for line in reviews.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("started_at", "") >= since:
                    rows.append(r)
    completed = [r for r in rows if r.get("kind") == "review"]
    if not completed:
        problems.append("no completed review session recorded since the smoke "
                        "began (an abandoned row alone is a run that did not finish)")
    pending = out_dir / ".sdlc" / "metrics" / "review-in-progress.json"
    if pending.exists():
        problems.append("the review session was never closed "
                        "(review-in-progress.json remains)")

    pj = out_dir / "prepass" / "prepass.json"
    try:
        pre = json.loads(pj.read_text(encoding="utf-8"))
        # prepass.json's own vocabulary: tools[].name / tools[].status, where a
        # tool that ran says "ok" (findings or not) and an unfired gate says
        # "not-run"/"error" — the status the pre-pass's exit 3 outranks 1 for.
        fired = {t.get("name"): t.get("status") for t in pre.get("tools", [])}
        for tool in ("gitleaks", "ast-grep"):
            if fired.get(tool) != "ok":
                problems.append(f"pre-pass gate '{tool}' did not fire "
                                f"(status: {fired.get(tool)!r})")
    except (OSError, ValueError):
        problems.append(f"no readable prepass.json at {pj}")

    pack_md = out_dir / "pack" / "pack.md"
    try:
        md = pack_md.read_text(encoding="utf-8")
        if "## Seeds" not in md:
            problems.append("pack.md has no Seeds block")
        else:
            diff_files = re.findall(r"^diff --git a/(\S+)",
                                    diff.read_text(encoding="utf-8", errors="replace"),
                                    flags=re.M)
            missing = [f for f in diff_files if Path(f).name not in md]
            if missing:
                problems.append(f"pack Seeds block never names {missing[:3]} "
                                f"- every changed file must appear with its mode")
        if not (out_dir / "pack" / "pack.json").is_file():
            problems.append("pack.json (the untrimmed machine copy) is missing")
    except OSError:
        problems.append(f"no readable pack.md at {pack_md}")
    return problems


# ── the record ────────────────────────────────────────────────────────────────


def record(target: Path, runner_name: str, verdict: str, problems: list[str],
           out_dir: Path) -> Path:
    """The durable sink scrubs for itself — every recorded string passes
    through scrub() regardless of which code path produced it, and the runner
    field too (an inline `--api-key ...` in the runner command would
    otherwise land on disk verbatim)."""
    secrets = secret_values(runner_env())
    path = target / ".sdlc" / "metrics" / "provider-smoke.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"schema_version": SCHEMA_VERSION, "kind": "provider-smoke",
           "at": _now(), "workflow": WORKFLOW,
           "runner": scrub(runner_name, secrets),
           "verdict": verdict, "problems": [scrub(p, secrets) for p in problems],
           "artifacts": str(out_dir)}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Provider-swap smoke: impact-review E2E from AGENTS.md + CLIs "
                    "alone (recurring gate, M2-M6).")
    ap.add_argument("--target", required=True, metavar="DIR",
                    help="repo/workspace root with an installed harness "
                         "(AGENTS.md + .sdlc/graph)")
    ap.add_argument("--diff", required=True, metavar="FILE")
    ap.add_argument("--component", required=True, metavar="ALIAS",
                    help="the diff's component (diffs do not say)")
    ap.add_argument("--component-path", default=".", metavar="REL",
                    help="component dir relative to target, for the pre-pass "
                         "scope (default: the target itself)")
    ap.add_argument("--runner", metavar="CMD",
                    help="a live provider command; it receives the task prompt "
                         "as its final argument, and its environment is the "
                         "shell's plus the harness .env (shell wins) - put the "
                         "provider's API key in <harness>/.env. Everything it "
                         "prints is secret-scrubbed before it can reach the "
                         "record. Omitted = the built-in null provider "
                         "(CLI-tier closure proof, not a live agent)")
    ap.add_argument("--out", metavar="DIR",
                    help="scratch dir for artifacts (default: "
                         "<target>/.sdlc/.tmp/provider-smoke)")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    diff = Path(args.diff).resolve()
    if not diff.is_file():
        print(f"!! no diff at {diff}")
        return 2
    out_dir = Path(args.out).resolve() if args.out else \
        target / ".sdlc" / ".tmp" / "provider-smoke"
    out_dir.mkdir(parents=True, exist_ok=True)

    problems = breadcrumbs(target)
    since = _now()
    if not problems:
        if args.runner:
            problems += live_provider(args.runner, diff, out_dir, cwd=target)
        else:
            problems += null_provider(target, diff, args.component,
                                      args.component_path, out_dir)
        problems += verify(out_dir, diff, since)

    runner_name = args.runner or "null-provider (CLI-tier closure; not a live agent)"
    verdict = "green" if not problems else "red"
    secrets = secret_values(runner_env())
    rec = record(target, runner_name, verdict, problems, out_dir)
    print(f"provider-swap smoke [{WORKFLOW}]: {verdict}   "
          f"runner: {scrub(runner_name, secrets)}")
    for p in problems:
        print(f"  !! {scrub(p, secrets)}")
    print(f"  record: {rec}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
