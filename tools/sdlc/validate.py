"""The ledger gate — item 44: a tampered ledger is *unmergeable*, not merely
detectable.

Item 32 shipped the hash chain and stated its limit out loud: hashes without
signatures detect *edits*, and someone who can rewrite the whole file can
recompute every hash in it. The answer recorded there was that the ledger is
COMMITTED, so a rewrite is a diff someone reviews. This module is that
sentence made mechanical. Three checks, and only the first is one item 32
could already make:

**integrity** — every run's chain walks, every approved artifact still hashes
to the bytes that were approved, and every entry says it is about the run
whose folder it sits in. Local truth about the working tree.

**append-only** — every entry that existed at the base ref is still there, at
the same position, byte for byte. This is the check the chain cannot make for
itself: a wholesale rewrite is internally consistent, and only something that
remembers the old file can see it. Git remembers. A run archived between base
and head moves its ledger, and that is a move, not a deletion.

**merge readiness** — a pull request from `sdlc/<run-id>` carries an approved
merge gate for that run, over artifacts that have not moved since.

**It runs on every pull request, not only on `sdlc/*`.** A tamper check you
can dodge by naming your branch something else is not a check; the plan's
`sdlc/*` scoping is kept only for the third question, which needs a run to be
about. And **a base ref this checkout cannot see is an error, never a pass** —
"nothing to compare against" reading as "nothing wrong" is the one failure
this repository keeps finding in its own tools.

`--emit-check` writes the whole thing out as ONE dependency-free file, which
is how an adopter's CI runs it: no harness clone, no packages, no network.
"""
from __future__ import annotations

import argparse
import ast
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.sdlc import approve as approvals  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

#: The gate a merge is defended with. G4 is "pre-merge approval" in the master
#: flow; spelled once here so the CI check and the runner cannot disagree.
MERGE_GATE = "G4"
#: Branches the harness pushes. Only the merge-readiness question keys off it.
#: The fallback pattern's prefix, used only when the repository's own profile
#: cannot be read. The real answer comes from `patterns_of`.
BRANCH_PREFIX = "sdlc/"
#: Where a ledger sits relative to the repository root, as git spells paths.
LEDGER_SUFFIX = "/" + approvals.APPROVALS
CHECK_NAME = "ledger_check.py"


# ── git, kept to the four questions this needs answered ──────────────────────


def _git(target: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", "-C", str(target), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return proc.returncode, proc.stdout


def is_commit(target: Path, ref: str) -> bool:
    """Whether this checkout can actually see the ref. Asked separately because
    `git show REF:path` fails identically for an unknown ref and an absent
    path, and treating the first as the second would let a shallow clone pass
    the append-only check by seeing nothing to compare."""
    return _git(target, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")[0] == 0


def repo_prefix(target: Path) -> str:
    """Where `target` sits inside its repository, as a git path prefix. Empty
    at the root. Ledgers elsewhere in a monorepo belong to another target."""
    code, out = _git(target, "rev-parse", "--show-prefix")
    return out.strip() if code == 0 else ""


def ledgers_at(target: Path, ref: str) -> dict[str, str]:
    """{run_id: root-relative path} for every ledger committed at `ref` under
    this target."""
    prefix = repo_prefix(target) + str(ledger.RUNS_REL).replace("\\", "/") + "/"
    code, out = _git(target, "ls-tree", "-r", "--name-only", "--full-name", ref)
    if code != 0:
        return {}
    found = {}
    for line in out.splitlines():
        if line.startswith(prefix) and line.endswith(LEDGER_SUFFIX):
            found[line[:-len(LEDGER_SUFFIX)].rsplit("/", 1)[-1]] = line
    return found


def blob_at(target: Path, ref: str, rel: str) -> str | None:
    """One committed file's text, or None when git could not produce it."""
    code, out = _git(target, "show", f"{ref}:{rel}")
    return out if code == 0 else None


# ── the three checks ─────────────────────────────────────────────────────────


def run_ids(target: Path) -> list[str]:
    """Every run folder, active then archived.

    Deliberately NOT `state.list_runs`, which parses each `state.json` on the way
    past and raises on one that is malformed. This gate's subject is run FOLDERS
    and the ledgers inside them; a corrupt state file is something to report, not
    something to die of. A required check that exits on a traceback tells a
    reviewer nothing about which run is wrong.
    """
    found = []
    for base in (ledger.runs_dir(target), ledger.archive_dir(target)):
        if not base.is_dir():
            continue
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            if base == ledger.runs_dir(target) and d.name == ledger.ARCHIVE_NAME:
                continue
            found.append(d.name)
    return found


def integrity(target: Path) -> list[str]:
    """Every chain break and every approved artifact that has moved, named."""
    problems = []
    for run_id in run_ids(target):
        for problem in approvals.verify_chain(target, run_id):
            problems.append(f"{run_id}: {problem}")
        for entry in approvals.read_chain(target, run_id):
            if "_unreadable" in entry:
                continue
            if entry.get("run_id") != run_id:
                problems.append(
                    f"{run_id}: an entry claims run_id {entry.get('run_id')!r} - a "
                    f"ledger moved between run folders proves nothing about either")
        # Artifact pins are checked on the LIVE entries only — the last verdict
        # per gate. Every entry is still walked above and by `verify_chain`,
        # because the chain is about the record. This asks whose verdict is
        # current: a G3 superseded by the lifecycle's own P6-to-P4 loop pinned
        # bytes the loop existed to replace, and reporting it kept a correct
        # change out of a merge.
        for entry in approvals.live_entries(target, run_id):
            for problem in approvals.verify_artifacts_for(target, run_id, entry):
                problems.append(f"{run_id}: {problem}")
        try:
            for problem in ledger.verify_artifacts(target, run_id):
                problems.append(f"{run_id}: artifact {problem}")
        except RuntimeError as exc:
            problems.append(f"{run_id}: {exc}")
    return problems


def append_only(target: Path, base: str) -> list[str]:
    """Every way the committed history of a ledger has been rewritten rather
    than extended. The check the hash chain cannot make for itself."""
    problems = []
    for run_id, rel in sorted(ledgers_at(target, base).items()):
        was = blob_at(target, base, rel)
        if was is None:
            problems.append(f"{run_id}: {rel} is listed at {base} but could not be read "
                            f"- refusing to call an unreadable base clean")
            continue
        path = approvals.approvals_path(target, run_id)
        if not path.is_file():
            problems.append(f"{run_id}: the ledger existed at {base} and is gone here "
                            f"(looked for {path.name} in both the active and archived "
                            f"run folders)")
            continue
        before = was.splitlines()
        now = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(now) < len(before):
            problems.append(f"{run_id}: {len(before)} entr{'y' if len(before) == 1 else 'ies'} "
                            f"at {base}, {len(now)} here - the ledger was truncated")
            continue
        for i, (old, new) in enumerate(zip(before, now), 1):
            if old != new:
                problems.append(f"{run_id}: entry {i} differs from the one committed at "
                                f"{base} - the ledger was rewritten, not appended to "
                                f"(a rewrite re-hashes cleanly; only the base can see it)")
                break
    return problems


def patterns_of(target: Path) -> list[str]:
    """Every branch pattern this repository publishes runs under.

    Item 37. `BRANCH_PREFIX` was hardcoded here, and a repository that renamed
    its run branches would have kept this gate installed, green, and silently
    not asking the merge question at all — the check would simply have decided
    no pull request was ever about a run. The patterns come from the repository
    itself now, and the same `policy.run_of_branch` the worktree model uses to
    CREATE the branch is what reads it back.

    A profile that cannot be read falls back to the default pattern rather than
    to no pattern: the integrity checks must keep running whatever else is
    wrong, and asking the merge question about the conventional branch name is
    strictly better than asking it about none.
    """
    return _patterns(target)[0]


def _patterns(target: Path) -> tuple[list[str], str]:
    """The patterns, and a note when the repository's own could not be read.

    The fallback is NOT silent, and that is the whole of what this second
    function buys. A profile that exists and cannot be read leaves the gate
    asking the merge question about a branch shape the repository may have
    stopped using — the exact failure this item removed from the hardcoded
    constant, reintroduced by an except clause. It is a finding.

    A profile that is simply ABSENT is not a finding: a repository with no
    harness install has no runs for the question to be about.
    """
    default = [guardrails.DEFAULTS["push_branch_prefix"] + guardrails.RUN_TOKEN]
    profile = Path(target) / ".sdlc" / "profile.yml"
    if not profile.is_file():
        return default, ""
    try:
        rules = guardrails.load(target)
        found = [guardrails.branch_pattern(rules, c.get("vcs"))
                 for c in guardrails.components(target)]
    except Exception as exc:
        return default, (f"{profile.name} could not be read ({type(exc).__name__}: "
                         f"{exc}), so the merge question is being asked about "
                         f"{default[0]} - which may not be what this repository "
                         f"publishes runs to")
    seen = list(dict.fromkeys(p for p in found if p))
    return (seen or default), ""


def run_of_branch(branch: str, patterns: list[str] | None = None) -> str:
    """The run this branch is about; empty for any other branch."""
    name = (branch or "").strip()
    for lead in ("refs/heads/", "origin/"):
        if name.startswith(lead):
            name = name[len(lead):]
    for pattern in (patterns or [BRANCH_PREFIX + guardrails.RUN_TOKEN]):
        if found := guardrails.run_of_branch(pattern, name):
            return found.strip("/")
    return ""


def merge_ready(target: Path, run_id: str) -> list[str]:
    """Whether this run may merge: an approved merge gate over bytes that have
    not moved. `gate_state` is the same call a runner makes before advancing —
    one question, one implementation."""
    if not ledger.run_dir(target, run_id).is_dir():
        problems = [f"{run_id}: the branch is named for a run that has no folder under "
                    f"{ledger.runs_dir(target)}"]
        return problems
    ok, why = approvals.gate_state(target, run_id, MERGE_GATE)
    return [] if ok else [f"{run_id}: {why}"]


def check(target: Path, base: str = "", branch: str = "") -> dict:
    """All three, in one report. The caller decides what to do about it."""
    patterns, note = _patterns(target)
    run_id = run_of_branch(branch, patterns)
    report = {
        "target": str(target), "base": base, "branch": branch, "run": run_id,
        "harness": ledger.harness_version(), "runs": len(run_ids(target)),
        "patterns": patterns, "pattern_note": note,
        "integrity": integrity(target),
        "append_only": append_only(target, base) if base else [],
        "merge_ready": merge_ready(target, run_id) if run_id else [],
    }
    report["problems"] = (report["integrity"] + report["append_only"]
                          + report["merge_ready"]
                          + ([note] if note else []))
    return report


# ── the same thing as one dependency-free file ───────────────────────────────

#: `from tools.sdlc import X` — the only cross-module import shape in this
#: package, so the closure below is computed rather than hand-listed. A list
#: someone maintains is a list that goes stale the first time an import moves.
#:
#: **Anchored at column 0 on purpose, and that is a decision rather than an
#: oversight.** An indented `from tools.sdlc import X` is a LAZY import, and in
#: this package a lazy import means exactly one thing: a cycle that cannot be
#: broken any other way. Following those would put the cycle back into the
#: bundle's ORDERING - measured, and the widened regex emits a file that dies
#: on its own first line, `policy` executing before the `intake` it needs.
#: Hoisting them instead was measured too: it makes correctness depend on which
#: module an adopter imports first. Both are recorded in
#: `LAZY_OUTSIDE_THE_BUNDLE` below, which is what carries the consequence.
_SIBLING = re.compile(r"^from tools\.sdlc import (\w+)", re.M)

#: Siblings an embedded module imports LAZILY and the bundle therefore does not
#: carry - each with the reason it is safe, which is always the same kind of
#: reason: nothing the ledger check can reach ever calls that path.
#:
#: **This is a landmine map, not an approval.** A module in here is one whose
#: import will raise `ImportError` inside an adopter's CI - on a required check,
#: with no harness to fall back on - the moment anything the bundle CAN reach
#: calls the function holding it. It does not fail today because
#: `validate.main(--ledger)` reads the ledger and never writes one; that is the
#: shape of the call graph, so it is written down here rather than trusted.
#:
#: Adding an entry is a deliberate act. `render_check` REFUSES to emit a bundle
#: carrying an undeclared one, so the failure lands in the maintainer's terminal
#: instead of in somebody else's pipeline three weeks later.
LAZY_OUTSIDE_THE_BUNDLE: dict[str, str] = {
    "defect": "stage.CONDITIONAL_JUDGE names it, so `stage.verify` reaches "
              "it for P4 - the phase whose conditional artifact is a defect's "
              "reproduction. Same path and same argument as `adr` below: "
              "`approve.record` calls verify, and the ledger check READS "
              "approvals rather than recording one. defect reaches tools.cli, "
              "which the loader below does not carry. This entry exists "
              "because the widened scanner demanded it, on the first judge "
              "added after it was widened",
    "adr": "stage.CONDITIONAL_JUDGE names it as a STRING and loads it with "
           "importlib, so `stage.verify` reaches it for any phase carrying a "
           "conditional artifact - P3's ADRs. `approve.record` calls verify, "
           "so the path exists; the ledger check does not take it, because it "
           "READS approvals and never records one. adr reaches the graph "
           "layer, which the loader below does not carry. This entry exists "
           "because the scanner could not see a string until it was taught to: "
           "the hole was real, undeclared, and proven by ModuleNotFoundError "
           "under a bare interpreter",
    "worktree": "stage._worktree_for reads the run's own record to say "
                "where a phase's change lives, so P6 stops pointing its "
                "review at the base checkout. The ledger check renders no "
                "prompt - it reads approvals - and worktree reaches "
                "tools.cli, which the loader below does not carry",
    "suite": "intake.baseline / component_baselines run the P5 engine. The "
             "ledger check never takes a baseline - it reads approvals - and "
             "suite reaches tools.cli, tools.review and the graph layer, none "
             "of which the loader below carries",
    "guard": "intake fences instruction-shaped spec bodies through the "
             "guardrail door. The ledger check parses no specifications",
    "feedback": "approve.record captures what a human edited before approving. "
                "The ledger check VERIFIES approvals and records none, and "
                "feedback imports tools.review.hitl at module level",
}


#: A sibling named as a STRING, for `importlib.import_module` to load later.
#: `tools.sdlc.adr` in a table is an import; it just does not look like one.
_NAMED_SIBLING = re.compile(r"^tools\.sdlc\.(\w+)$")


def sibling_imports(source: str) -> list[tuple[int, str]]:
    """Every sibling one module could need, at ANY indentation.

    `ast`, not the regex above: the regex answers "what does the bundle carry",
    which is deliberately column-0 only, and this answers the different
    question "what could this source ever need". Reading them off one pattern
    is how the two got conflated in the first place.

    **Two shapes, because a sibling can be named without an import statement.**
    `stage.CONDITIONAL_JUDGE` holds `"tools.sdlc.adr"` as a string and loads it
    with `importlib.import_module`, which is an import that does not look like
    one — so this scanner could not see it, `LAZY_OUTSIDE_THE_BUNDLE` never had
    to declare it, and the emitted check raised `ModuleNotFoundError: No module
    named 'tools.sdlc.adr'` under a bare interpreter the moment it verified a
    phase carrying a conditional artifact. Measured, not argued.

    It does not fail today for the reason every entry in that map gives —
    nothing the ledger check reaches records an approval — but **a hole the
    guard cannot see is exactly what this guard exists to prevent**, and the
    next judge added would have been the third invisible one.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == "tools.sdlc":
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if match := _NAMED_SIBLING.match(node.value.strip()):
                found.append((node.lineno, match.group(1)))
    return sorted(set(found))


def unsatisfiable(embedded: list[str], here: Path | None = None
                  ) -> dict[str, list[tuple[int, str]]]:
    """Per embedded module, the sibling imports the bundle cannot satisfy.

    A name that is absent from `embedded` raises ImportError if it is ever
    reached, whatever `LAZY_OUTSIDE_THE_BUNDLE` says about it - that map
    records which ones are known and argued, not which ones would work.
    """
    here = here or Path(__file__).resolve().parent
    out: dict[str, list[tuple[int, str]]] = {}
    for name in embedded:
        source = (here / f"{name}.py").read_text(encoding="utf-8")
        missing = [(line, mod) for line, mod in sibling_imports(source)
                   if mod not in embedded]
        if missing:
            out[name] = missing
    return out


def closure(entry: str = "validate", here: Path | None = None) -> list[str]:
    """Module names in dependency order, ending with `entry`."""
    here = here or Path(__file__).resolve().parent
    order: list[str] = []
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        src = (here / f"{name}.py").read_text(encoding="utf-8")
        for dep in _SIBLING.findall(src):
            visit(dep)
        order.append(name)

    visit(entry)
    return order


#: The loader the emitted file carries. It registers each module under the name
#: its siblings import it by, so the harness source runs unmodified — nothing
#: is rewritten on the way out, which is the only way a copy can be trusted to
#: behave like the original.
_BUNDLE = '''#!/usr/bin/env python3
"""The SDLC approval-ledger check, as one dependency-free file (harness item 44).

GENERATED — do not edit. Re-run the harness installer to refresh it.
Generated from harness commit {commit}.

    python {name} --ledger --target . --base origin/main --branch "$HEAD_REF"

Exit 0 = the ledger holds. 1 = it does not, and every reason is printed.
2 = the check could not be run (bad target, or a base ref this clone cannot
see — which is never reported as clean).
"""
import base64
import os
import sys
import types

HARNESS_COMMIT = "{commit}"
_MODULES = {modules}


def _load() -> None:
    for dotted in ("tools", "tools.sdlc"):
        if dotted not in sys.modules:
            pkg = types.ModuleType(dotted)
            pkg.__path__ = []  # a package with no directory: nothing else may be found in it
            sys.modules[dotted] = pkg
            parent, _, leaf = dotted.rpartition(".")
            if parent:
                setattr(sys.modules[parent], leaf, pkg)
    for name, blob in _MODULES:
        dotted = "tools.sdlc." + name
        mod = types.ModuleType(dotted)
        mod.__file__ = os.path.abspath(__file__)
        sys.modules[dotted] = mod
        source = base64.b64decode(blob).decode("utf-8")
        exec(compile(source, f"<{{dotted}}>", "exec"), mod.__dict__)
        setattr(sys.modules["tools.sdlc"], name, mod)


_load()
from tools.sdlc import state, validate  # noqa: E402

# This file KNOWS which harness produced it. Left alone, `harness_version()` shells
# out to git from wherever the file happens to sit, which in an adopter's checkout
# reports THEIR commit as the harness's - a wrong fact in the record, arrived at
# confidently.
state.harness_version = lambda: HARNESS_COMMIT

if __name__ == "__main__":
    raise SystemExit(validate.main())
'''


def render_check(here: Path | None = None, commit: str = "") -> str:
    """The emitted file's text. Deterministic for a given harness commit, so a
    re-install is an update only when the harness actually moved."""
    here = here or Path(__file__).resolve().parent
    names = closure(here=here)
    undeclared = sorted(
        f"{owner}.py:{line} imports `{mod}`"
        for owner, rows in unsatisfiable(names, here).items()
        for line, mod in rows if mod not in LAZY_OUTSIDE_THE_BUNDLE)
    if undeclared:
        # Refusing here is the whole point. Emitting anyway ships a file that
        # runs until the day something calls that path, and the day it does is
        # in an adopter's required check with no harness and a bare ImportError
        # for a message.
        raise RuntimeError(
            "this bundle would carry a module whose import it cannot satisfy: "
            + "; ".join(undeclared)
            + ". Either the module belongs in the closure (make the import "
              "top-level, and check the ordering still works), or it is "
              "unreachable from the ledger check and belongs in "
              "validate.LAZY_OUTSIDE_THE_BUNDLE with the reason written down")
    payload = [(n, base64.b64encode((here / f"{n}.py").read_text(encoding="utf-8")
                                    .encode("utf-8")).decode("ascii")) for n in names]
    literal = "[\n" + "".join(f'    ("{n}", "{b}"),\n' for n, b in payload) + "]"
    return _BUNDLE.format(commit=commit or ledger.harness_version(),
                          modules=literal, name=CHECK_NAME)


def emit_check(out: Path, here: Path | None = None, commit: str = "") -> Path:
    # Rendered BEFORE the file is opened. `open("w")` truncates, so building
    # the text inside the `with` meant a refusal from `render_check` left the
    # adopter's existing, working check at zero bytes - a guard destroying the
    # thing it guards. Found by the test that asks what the refusal LEAVES.
    text = render_check(here, commit)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify the approval ledger the way CI must: intact, "
                    "append-only against the base, and approved for merge (item 44).")
    ap.add_argument("--ledger", action="store_true",
                    help="run the ledger gate (the mode this CLI exists for)")
    ap.add_argument("--target", metavar="DIR", default=".")
    ap.add_argument("--base", metavar="REF", default="",
                    help="the ref this change is proposed against; enables the "
                         "append-only check")
    ap.add_argument("--branch", metavar="NAME", default="",
                    help="the head branch; `sdlc/<run-id>` enables the merge-gate check")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--emit-check", metavar="OUT",
                    help="write this check out as one dependency-free file and exit")
    args = ap.parse_args(argv)

    if args.emit_check:
        try:
            out = emit_check(Path(args.emit_check))
        except RuntimeError as exc:
            # 3, not 1. The installer SPAWNS this command, and 1 in this CLI
            # means "the ledger does not support a merge" - a crash wearing
            # that number would answer a question nobody asked, which is the
            # defect the review gates' own door was written against.
            print(f"!! cannot emit the ledger check: {exc}")
            return 3
        print(f"wrote {out} ({out.stat().st_size} bytes, "
              f"{len(closure())} modules embedded)")
        return 0
    if not args.ledger:
        print("!! pass --ledger to run the check, or --emit-check OUT to write it out")
        return 2

    target = Path(args.target).resolve()
    if not target.is_dir():
        print(f"!! no directory at {target}")
        return 2
    if args.base and not is_commit(target, args.base):
        print(f"!! {args.base} is not a commit this checkout can see - append-only "
              f"cannot be proven, and an unprovable check is not a passing one "
              f"(a CI checkout needs fetch-depth: 0)")
        return 2

    report = check(target, args.base, args.branch)
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if report["problems"] else 0

    scope = f"{report['runs']} run(s)"
    if args.base:
        scope += f", append-only vs {args.base}"
    if report["run"]:
        scope += f", {MERGE_GATE} for {report['run']}"
    print(f"ledger check: {scope}")
    for group, rows in (("integrity", report["integrity"]),
                        ("append-only", report["append_only"]),
                        ("merge-ready", report["merge_ready"])):
        for row in rows:
            print(f"  !! [{group}] {row}")
    if report["problems"]:
        print(f"FAILED - {len(report['problems'])} problem(s). This ledger does not "
              f"support a merge.")
        return 1
    print("OK - the record holds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
