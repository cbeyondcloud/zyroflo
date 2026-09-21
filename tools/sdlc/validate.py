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

  A ledger the BASE has never seen is the hole in that sentence, and it is the
  harness's own ordinary workflow: a run folder is created on the run branch
  and first committed there, so on the merge that lands it there is nothing at
  the base to compare against and "only the base can see it" sees nothing. A
  ledger with no base copy is therefore COULD-NOT-LOOK, never clean, and
  `limits` says so by name on every run it could not judge. The branch's own
  history is asked as well: every committed version of a ledger in `base..HEAD`
  must be a byte-prefix of the one at HEAD, so a truncation made between two
  commits is a problem even when the base has never seen the file. What
  neither can catch is an author who rewrites the branch's history before
  pushing; that is why the limit is printed even when the walk found nothing.

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
#: flow; DERIVED from `policy.MERGE_GATE`, the one place it is named, so the CI
#: check and the runner cannot disagree.
MERGE_GATE = guardrails.MERGE_GATE
#: Branches the harness pushes. Only the merge-readiness question keys off it.
#: The fallback pattern's prefix, used only when the repository's own profile
#: cannot be read. The real answer comes from `patterns_of`.
BRANCH_PREFIX = "sdlc/"
#: Where a ledger sits relative to the repository root, as git spells paths.
LEDGER_SUFFIX = "/" + approvals.APPROVALS
CHECK_NAME = "ledger_check.py"


class CouldNotLook(RuntimeError):
    """A git command this check depends on failed, so a question it exists to
    ask was never asked.

    Raised only where the caller offered no `limits` list to state that through
    - because the one answer this must never give is the empty output of a
    failed command, handed back as though it were the result of looking.
    """


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
    """Every chain break and every approved artifact that has moved, named.

    Signatures are not judged here - see `limits`, which states what this check
    could not verify without making it a failure.
    """
    problems = []
    for run_id in run_ids(target):
        for problem in approvals.verify_chain(target, run_id):
            problems.append(f"{run_id}: {problem}")
        # When the library is present, a signature is checked for real and a
        # bad one is a genuine problem. When it is absent, `limits` says so.
        try:
            import securesystemslib.dsse  # noqa: F401
        except ImportError:
            pass
        else:
            for problem in approvals.verify_signatures(target, run_id):
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


def branch_history(target: Path, base: str, run_id: str, rel: str,
                   said: list[str] | None) -> list[str]:
    """Every commit in `base..HEAD` whose copy of this ledger is not a byte
    PREFIX of the copy at HEAD — the ledger shrank or was rewritten partway
    along its own branch. It reads the committed BLOB at each commit, never the
    file on disk; `append_only` says why both readings are needed.

    This is what the base comparison cannot reach: a run folder created on the
    branch has no copy at the base, so `ledgers_at(target, base)` never names
    it and nothing is compared. The branch's own history remembers instead.

    **A `git log` that fails is could-not-look, not a clean branch.** Its empty
    output is not evidence of absence — this project's headline rule, and this
    is the check a merge stands on. `is_commit` upstream refuses an unknown
    base, but the walk between two readable commits still dies on a corrupt or
    partially fetched object store, and `_git` reports a timeout the same way.
    So the failure goes into `said`, the limits channel: printed every run,
    never a problem, never fatal, naming the run and the command. With no such
    list to speak through it raises `CouldNotLook` rather than returning [].

    **What it does NOT catch: an author who rewrites the branch's history
    before pushing.** A rebase or an amend leaves commits that never say what
    they replaced, and no check reading only the pushed branch can tell that
    from a branch written that way the first time. `limits` still reports the
    missing base copy, which is the honest account of it.
    """
    head = blob_at(target, "HEAD", rel)
    if head is None:
        return []                       # not committed at HEAD: nothing to be a prefix of
    code, out = _git(target, "log", "--format=%H", f"{base}..HEAD", "--", rel)
    if code != 0:
        note = (f"{run_id}: `git log {base}..HEAD -- {rel}` failed (exit {code}), so "
                f"this run's branch history could not be walked - a ledger truncated "
                f"or rewritten between two commits on this branch would not be seen "
                f"from here. A git command that failed is not a ledger that is clean")
        if said is None:
            raise CouldNotLook(note)
        said.append(note)
        return []
    at_head = _git(target, "rev-parse", "HEAD")[1].strip()
    problems = []
    for sha in out.split():
        was = blob_at(target, sha, rel)
        if was is None or head.startswith(was):
            continue
        problems.append(
            f"{run_id}: the ledger at {sha[:12]} is not a prefix of the one at HEAD "
            f"({at_head[:12]}) - it was truncated or rewritten between those two "
            f"commits, and a ledger may only grow along its own branch")
    return problems


def append_only(target: Path, base: str, said: list[str] | None = None) -> list[str]:
    """Every way the committed history of a ledger has been rewritten rather
    than extended. The check the hash chain cannot make for itself.

    Two comparisons, because one of them cannot see the harness's own workflow:
    against the copy at `base` where there is one, and along the branch's own
    commits either way — see `branch_history`.

    **They read different things, and that is why both are needed.** The base
    comparison reads the blob at `base` against the file ON DISK — what CI
    actually checked out and what the harness would go on to act on, so an
    edit that was never committed is still caught. `branch_history` reads the
    committed BLOB at each commit against the blob at HEAD — what the history
    holds, so a truncation that was committed and then grown back is caught
    even where the base has no copy to compare. Neither sees the other's case.

    `said` is the `limits` list: what could not be READ is stated there rather
    than counted as clean. Called without one, `branch_history` raises.
    """
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
    for run_id in run_ids(target):
        path = approvals.approvals_path(target, run_id)
        if not path.is_file():
            continue
        try:
            rel = repo_prefix(target) + path.resolve().relative_to(
                Path(target).resolve()).as_posix()
        except ValueError:
            continue                    # outside the target: not this gate's ledger
        problems += branch_history(target, base, run_id, rel, said)
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
    profile = Path(target) / ".sdlc" / guardrails.PROFILE_NAME
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


def limits(target: Path, base: str = "") -> list[str]:
    """What this check did NOT verify. Printed loudly; never fatal.

    Two things speak here, for one reason. The second is a ledger the base ref
    has never seen: `append_only` walks what the base remembers, so a run whose
    folder is first committed on the branch is not judged clean by it - it is
    not judged at all. Reporting nothing would make the harness's own ordinary
    merge look checked; making it a problem would block every first merge. So
    it is the same third channel, naming the run.

    **The distinction is the whole of it, and getting it wrong would have
    rebuilt the defect item 108 exists to fix.** Verifying a DSSE envelope
    needs a crypto library, and this function is embedded into
    `.sdlc/ledger_check.py`, which runs in an adopter's CI under a bare
    interpreter with no harness and no third-party packages - by design, item
    44. So there are two wrong answers and one right one:

    * say nothing, and a required CI gate reports a signed ledger as fully
      checked having checked only its hashes - the silent pass this project
      keeps finding;
    * call it a PROBLEM, and every signed ledger fails the merge gate forever,
      which would leave `gate_profile: regulated` exactly as unusable as it was
      before envelopes existed - the same dead end one gate along.

    So it is neither: a stated limit, printed every run, exit code untouched.
    The chain and the artifact pins ARE checked and those still fail loudly.
    **If the library happens to be present, the signatures are really verified
    and a bad one IS a problem** - this only speaks when nothing could look.
    """
    said = []
    try:
        import securesystemslib.dsse  # noqa: F401
    except ImportError:
        crypto = False
    else:
        crypto = True                   # verified for real in `integrity`
    for run_id in ([] if crypto else run_ids(target)):
        signed = sum(1 for e in approvals.read_chain(target, run_id)
                     if isinstance(e, dict) and e.get("signature"))
        if signed:
            said.append(
                f"{run_id}: {signed} entr(ies) carry a DSSE signature and this "
                f"check has no crypto library, so nothing verified them. The "
                f"chain and the artifact pins WERE checked. Run `tools/sdlc/approve.py "
                f"--verify --run {run_id}` where the harness is installed to "
                f"check the signatures themselves")
    said += unseen_at_base(target, base) if base else []
    return said


def unseen_at_base(target: Path, base: str) -> list[str]:
    """Every ledger present here that `base` has no copy of, named.

    Not a problem and not silence. `append_only` can only speak about ledgers
    the base ref remembers; for the rest it did not look, and a run folder
    created on its own branch - which is how this harness works - is exactly
    that case. A truncation or a fully re-hashed rewrite made before the first
    push leaves no trace the base could compare against.
    """
    at_base = ledgers_at(target, base)
    said = []
    for run_id in run_ids(target):
        if run_id in at_base:
            continue
        if not approvals.approvals_path(target, run_id).is_file():
            continue                    # no ledger here either: nothing to judge
        said.append(
            f"{run_id}: this ledger does not exist at {base}, so the append-only "
            f"comparison could not be made for it - a truncation or a rewrite of "
            f"this run's approvals before this push cannot be seen from here")
    return said


def merge_ready(target: Path, run_id: str) -> list[str]:
    """Whether this run may merge: an approved merge gate over bytes that have
    not moved. `gate_state` is the same call a runner makes before advancing —
    one question, one implementation."""
    try:
        folder = ledger.run_dir(target, run_id)
    except ValueError as exc:
        # The run id came from a branch name (`run_of_branch`), which is
        # attacker-influenced in fork CI. An id that would escape the runs
        # directory is refused BY NAME here, so this report names it rather than
        # the CLI dying on a traceback or the traversal being silently probed.
        return [str(exc)]
    if not folder.is_dir():
        # T398: the relative runs directory, not an absolute machine path. The
        # run is ABSENT (run_dir fell back to the archive path), so this names
        # the directory (RUNS_REL), matching state.py:320's choice for a
        # missing run rather than run_dir's archive fallback.
        problems = [f"{run_id}: the branch is named for a run that has no folder under "
                    f"{ledger.RUNS_REL.as_posix()}"]
        return problems
    ok, why = approvals.gate_state(target, run_id, MERGE_GATE)
    return [] if ok else [f"{run_id}: {why}"]


def check(target: Path, base: str = "", branch: str = "") -> dict:
    """All three, in one report. The caller decides what to do about it."""
    patterns, note = _patterns(target)
    run_id = run_of_branch(branch, patterns)
    # One limits list, handed to `append_only` rather than copied: what it could
    # not READ - a git command that failed - is stated in the same channel as
    # what it could not verify, and never returned as an empty result.
    said = limits(target, base)
    grew = append_only(target, base, said) if base else []
    report = {
        "target": str(target), "base": base, "branch": branch, "run": run_id,
        "harness": ledger.harness_version(), "runs": len(run_ids(target)),
        "patterns": patterns, "pattern_note": note,
        "integrity": integrity(target),
        "limits": said,
        "append_only": grew,
        "merge_ready": merge_ready(target, run_id) if run_id else [],
    }
    # `limits` is deliberately NOT in problems: it states what was not
    # checked, and a limit that failed the build would make a signed ledger
    # unmergeable.
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
    "verify": "stage.CONDITIONAL_JUDGE names it for P10, so `stage.verify` reaches it for the post-deploy phase. Same argument as `deploy` and `release`: `approve.record` calls verify, and the ledger check READS approvals rather than recording one. verify reaches tools.cli, `suite` and `intake`, none of which the loader carries at that point",
    "deploy": "stage.CONDITIONAL_JUDGE names it for P9, so `stage.verify` reaches it for the deploy phase. Same argument as `release` and `defect`: `approve.record` calls verify, and the ledger check READS approvals rather than recording one. deploy reaches tools.cli and `suite`, neither of which the loader carries",
    "release": "stage.CONDITIONAL_JUDGE names it for P8, so `stage.verify` "
               "reaches it for the release phase. Same path and same argument "
               "as `defect` and `adr`: `approve.record` calls verify, and the "
               "ledger check READS approvals rather than recording one. release "
               "reaches tools.cli and tools.review.prepass, neither of which "
               "the loader carries - and a bundle that pulled in prepass would "
               "pull in the vendored-binary layer with it, which is precisely "
               "what a dependency-free check may not have",
    "claims": "stage.CLAIM_CHECK names it as a STRING and `stage.verify` loads "
              "it with importlib at EVERY phase, so this is the widest of the "
              "lazy paths - wider than the conditional judges below, which "
              "only fire for the phases naming a conditional artifact. The "
              "argument is still the same one and it is the call graph, not a "
              "hope: `approve.record` calls verify, and the ledger check READS "
              "approvals rather than recording one, so nothing the bundle can "
              "reach ever takes this path. claims reaches tools.cli, which the "
              "loader does not carry. **If the ledger check ever learns to "
              "record an approval, this is the first entry that breaks**, and "
              "it breaks in an adopter's required CI with no harness present",
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
    for row in report.get("limits", []):
        print(f"  .. [not checked] {row}")
    if report["problems"]:
        print(f"FAILED - {len(report['problems'])} problem(s). This ledger does not "
              f"support a merge.")
        return 1
    print("OK - the record holds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
