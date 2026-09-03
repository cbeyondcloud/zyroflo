"""The reference runner — item 33: the contract, executable, with no model in it.

`RUNNER_CONTRACT.md` says what a runner adapter must do. This is that document
as a program: it receives the task prompt as its final argument, discovers
everything else from the target repository, drives the harness CLIs, and
leaves the artifacts the verification reads. It imports nothing from the
harness — an adapter is an OUTSIDE process, and one that quietly reached into
`tools/` would prove nothing about a runner that cannot.

**What it proves, and what it does not.** It proves the live `--runner` path
works end to end for a real external process, and that the discovery chain —
prompt → `AGENTS.md` → the CLI tier → artifacts at the stated paths — is
walkable *mechanically*. It does NOT prove an agent can decide anything: there
is no model here, it makes no judgements, and it finds nothing a human did not
already ask the pre-pass to find. A green run with this runner is a green
CONTRACT, never a green agent — the smoke's record names which ran, for
exactly that reason.

Its real job is to make the credentialed run cheap: when a live agent is
finally pointed at this harness, a failure means something about the agent,
because the path it walks has already been walked.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

#: This module is an OUTSIDE PROCESS and must import nothing from the harness -
#: the property the docstring above argues for. Declared HERE rather than listed
#: in a test, so the rule is enforced from the file it is about and a second such
#: module joins by saying so rather than by somebody remembering a roster.
OUTSIDE_PROCESS = True

#: Everything the runner needs is stated in the prompt or discoverable from the
#: repository. Each value runs to the END OF ITS LINE, which is what lets a path
#: contain spaces — the first cut of the prompt embedded these in prose, and the
#: first thing this runner did was capture a trailing comma into a directory
#: name. Anything the harness fails to state is a defect in the prompt, not in
#: the runner: that is how the paths came to be named in it at all.
_COMPONENT = re.compile(r"^component: (.+)$", re.M)
_DIFF = re.compile(r"^diff: (.+)$", re.M)
_PREPASS = re.compile(r"^prepass-out: (.+)$", re.M)
_PACK = re.compile(r"^pack-out: (.+)$", re.M)
_METRICS = re.compile(r"^hitl-target: (.+)$", re.M)
#: The CLI-tier prefix the installer writes into AGENTS.md, quoted since a
#: clone can live under a path with a space.
_UV = re.compile(r'uv run --project "([^"]+)"')
_SOP = re.compile(r"`([^`]*workflows/impact-review\.md)`")


def read_prompt(argv: list[str]) -> str:
    """The prompt is the FINAL argument — the contract's one calling rule."""
    if not argv:
        raise SystemExit("!! no prompt: the task is passed as the final argument")
    return argv[-1]


def discover(prompt: str, root: Path) -> dict:
    """Everything this runner needs, from the prompt and the repository."""
    agents = root / "AGENTS.md"
    if not agents.is_file():
        raise SystemExit(f"!! no AGENTS.md at {root} - nothing to discover from")
    text = agents.read_text(encoding="utf-8", errors="replace")
    harness = _UV.search(text)
    if not harness:
        raise SystemExit("!! AGENTS.md states no quoted `uv run --project \"<harness>\"` "
                         "prefix - the CLI tier is not discoverable")
    found = {"harness": Path(harness.group(1)), "sop": "",
             "graph_dir": root / ".sdlc" / "graph"}
    sop = _SOP.search(text)
    found["sop"] = sop.group(1) if sop else ""
    # pack.py refuses --diff without --component, so a runner that is never
    # told the alias cannot finish - measured the first time this ran.
    told = _COMPONENT.search(prompt)
    found["component"] = told.group(1).strip() if told else ""
    for key, pattern in (("diff", _DIFF), ("prepass", _PREPASS),
                         ("pack", _PACK), ("metrics", _METRICS)):
        m = pattern.search(prompt)
        if not m:
            raise SystemExit(f"!! the prompt never states the {key} - a runner cannot "
                             f"guess it, and the verification reads it")
        found[key] = m.group(1)
    return found


def cli(found: dict, script: str, *args: str) -> list[str]:
    """A harness CLI, invoked the way AGENTS.md teaches it."""
    harness = found["harness"]
    return ["uv", "run", "--project", str(harness),
            "python", str(harness / script), *args]


#: Restated from `provider_smoke`, which owns them - see `run` below for why
#: this file may not import it. Item 127.
NOT_STARTED_CODE = 127
NOT_STARTED_NOTE = "did not run: "


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=str(cwd), timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        # RESTATED, not imported: this file is an outside process and imports
        # nothing from the harness, which is the whole of what it proves. Item
        # 115's sweep ties this spelling to `provider_smoke.NOT_STARTED_NOTE`
        # by source text, so the two cannot drift the way the secret-scrub
        # pattern did.
        return NOT_STARTED_CODE, f"{NOT_STARTED_NOTE}{exc}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def execute(prompt: str, root: Path, component: str = "") -> int:
    """The SOP's mechanical spine, driven from the outside."""
    found = discover(prompt, root)
    component = found["component"] or component
    hitl = ["tools/review/hitl.py", "--target", found["metrics"]]

    code, out = run(cli(found, *hitl, "--begin",
                        *(["--component", component] if component else [])), root)
    if code != 0:
        print(f"!! hitl --begin failed ({code}): {out.strip()[:300]}")
        return 1

    steps = [
        ("pre-pass", cli(found, "tools/review/prepass.py", "--target", str(root),
                         "--diff", found["diff"], "--out", found["prepass"])),
        ("pack", cli(found, "tools/graph/pack.py", "--graph-dir",
                     str(found["graph_dir"]), "--diff", found["diff"],
                     *(["--component", component] if component else []),
                     "--out", found["pack"], "--no-svg")),
    ]
    problems = []
    for name, cmd in steps:
        code, out = run(cmd, root)
        # The pre-pass exits 1 for findings, which is not a failure; anything
        # else is. The pack has no such convention.
        ok = code in ((0, 1) if name == "pre-pass" else (0,))
        print(f"   {name}: exit {code}{'' if ok else '  <-- unexpected'}")
        if not ok:
            problems.append(f"{name} exited {code}: {out.strip()[:200]}")

    close = ["--end", "approved"] if not problems else ["--abandon"]
    code, out = run(cli(found, *hitl, *close), root)
    if code != 0:
        problems.append(f"hitl close failed ({code}): {out.strip()[:200]}")
    for p in problems:
        print(f"!! {p}")
    # The contract does not let this exit code stand for success — the smoke
    # judges artifacts. It is reported honestly anyway, because a runner that
    # lies about its own outcome is a runner nobody can debug.
    return 0 if not problems else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(
        description="A contract-conformant runner with no model in it (item 33). "
                    "The task prompt is the final argument.")
    ap.add_argument("--component", default="", metavar="ALIAS",
                    help="optional; the prompt does not state it and both CLIs "
                         "treat it as optional")
    ap.add_argument("--root", default=".", metavar="DIR",
                    help="the repository to work in (default: the current one, "
                         "which is where the harness invokes a runner)")
    known, _rest = ap.parse_known_args(argv[:-1] if len(argv) > 1 else [])
    prompt = read_prompt(argv)
    root = Path(known.root).resolve()
    print(f"reference runner: {len(prompt)} char prompt, root {root}")
    return execute(prompt, root, known.component)


if __name__ == "__main__":
    raise SystemExit(main())
