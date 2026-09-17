"""P10 — verify what was deployed by asking the running system. Item 48.

A deploy that returned zero is not a deploy that worked. **P9 asks the
deployment tool; this phase asks the service.** Item 46 is the argument stated
as an event: its first real run recorded `deployed: true` for a change that
never shipped, and the thing that made it look healthy was a health check
answered by the process the deploy was supposed to replace.

**`could not look` is not `healthy`, and neither is `nothing ran`.** A probe
that could not be attempted, a probe that got no answer, and a probe that got a
bad answer are three different sentences, recorded as three different statuses -
but **none of them is a pass**, and the run's verdict says so. Silence from a
service that is supposed to be serving is an answer.

**The rollback is PROPOSED, never run.** Rolling back is as much a change to a
live system as rolling forward, and it goes through the same gates. This module
imports nothing that can execute a command - no `subprocess`, no `os.system` -
so the property is structural rather than remembered, and a test holds it.

**A failing probe opens a defect, because the alternative is a message somebody
has to remember at three in the morning.** It is deduped through
`intake.duplicates` on a deterministic specification, so a flapping probe files
ONE run rather than one per attempt - and a run that CLOSED does not hold the
failure filed, which is item 66's policy and the same reasoning: the symptom
outlived the attempt.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import cli  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import stage as stages  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

SCHEMA_VERSION = 1
PHASE = "P10"

#: What this phase leaves behind. Asked for rather than spelled: item 94's rule,
#: and the duplicate-declaration guard enforces it.
RESULT = stages.VERIFY_RESULT

#: The profile key declaring what to ask. Read per component with a top-level
#: fallback, through the ONE reader this package already has for that shape.
PROBES = "probes"

#: A failed rollout enters the lifecycle as a defect, which is what it is.
KIND = "defect"

#: Three answers, and the middle one is the one a single boolean loses.
PASS, FAIL, UNREACHABLE = "pass", "fail", "unreachable"


class VerifyError(RuntimeError):
    """The verification could not be attempted, which is never a clean one."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def probes_of(target: Path, component: str = "") -> list[str]:
    """Every probe declared for this workspace, per component first.

    `suite.quarantined` is the reader, and it is reused rather than reimplemented
    on purpose: it already carries the three lessons `known_failures` learned the
    hard way - a per-component value wins, the top-level key is the fallback so a
    single-repository install can reach it at all, and `None` stays the EMPTY
    list rather than one entry named None.
    """
    from tools.sdlc import suite as engine

    try:
        every = guardrails.components(Path(target))
    except (guardrails.PolicyError, OSError, ValueError) as exc:
        raise VerifyError(f"the profile could not be read: {exc}") from None
    found: list[str] = []
    for comp in every:
        if component and comp.get("name") != component:
            continue
        for url in engine.quarantined(Path(target), comp, PROBES):
            if (url := str(url).strip()) and url not in found:
                found.append(url)
    return found


def deployed(target: Path, run_id: str) -> dict:
    """What P9 recorded, or a refusal. **The two phases are composed here.**

    P10 verifies a DEPLOYMENT, so it asks the deploy record what happened rather
    than assuming one did. A run with no record has nothing to verify, and a run
    whose deploy failed has nothing healthy to verify - which is the sentence
    `deploy.obligations` already uses, arrived at from the other side.
    """
    from tools.sdlc import deploy as rollout

    try:
        found = rollout.recorded(Path(target), run_id)
    except rollout.DeployError as exc:
        raise VerifyError(str(exc)) from None
    if found is None:
        raise VerifyError(
            f"{run_id} has no deploy record ({rollout.RESULT}), so there is "
            f"nothing to verify - a probe that passes here would be describing "
            f"whatever was already running")
    if not found.get("deployed"):
        # **`exited None` printed a could-not-look as if it were a number.**
        # P9 writes None when the deploy timed out or never started, and the
        # operator reading this sentence had to already know that to parse it.
        code = found.get("exit_code")
        said = (f"exited {code}" if code is not None else
                "did not report an exit code")
        raise VerifyError(
            f"the deploy for {run_id} {said}, so there is nothing healthy to "
            f"verify - the change is not out, and a green probe would be about "
            f"the build it failed to replace")
    return found


def probe(url: str, timeout: int = 10) -> dict:
    """Ask one endpoint. **Three statuses, because they need three repairs.**

    `pass` - it answered, and answered well. `fail` - it answered and said no,
    which means something is running and unhappy. `unreachable` - nothing
    answered, which usually means nothing is there. Folding the last two into
    one boolean loses the difference between a service that is broken and a
    service that is absent, and those are not the same page to open at 3am.
    """
    started, t0 = _now(), time.perf_counter()
    row = {"url": url, "at": started, "status": None, "detail": ""}
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            code = int(getattr(resp, "status", 0) or 0)
            row["status"] = code
            row["result"] = PASS if 200 <= code < 300 else FAIL
            if row["result"] == FAIL:
                row["detail"] = f"answered HTTP {code}"
    except urllib.error.HTTPError as exc:
        # An HTTP error IS an answer: something is serving and refusing.
        row["status"] = int(exc.code)
        row["result"] = FAIL
        row["detail"] = f"answered HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        row["result"] = UNREACHABLE
        row["detail"] = f"no answer: {exc}"
    row["seconds"] = round(time.perf_counter() - t0, 2)
    return row


def verdict(rows: list[dict]) -> str:
    """`pass` only when something was asked and everything answered well.

    **Zero probes is not a pass.** It is item 40's shape, which item 47 met at
    P8 and item 46 met at P9: *nothing ran* and *nothing failed* are identical
    output unless something insists they differ.
    """
    if not rows:
        return FAIL
    return PASS if all(r.get("result") == PASS for r in rows) else FAIL


def rollback_proposal(target: Path, run_id: str, record: dict) -> str:
    """The concrete undo, written out and NOT run.

    Everything here comes from what the run already recorded - the command P9
    ran, the tree it ran in, the branch and the base commit - because a proposal
    that invents a step is one nobody can check. What it cannot know it says it
    cannot know: a deploy touches things outside the repository, and the only
    honest general statement about data is that this proposal does not cover it.
    """
    from tools.sdlc import worktree as trees

    branch = base = "(not recorded)"
    try:
        homes = (trees.load_record(Path(target), run_id).get("components") or {})
        for entry in homes.values():
            if isinstance(entry, dict):
                branch = str(entry.get("branch") or branch)
                base = str(entry.get("base") or base)
                break
    except (trees.WorktreeError, OSError, ValueError):
        pass
    command = str(record.get("command") or "(no command recorded)")
    # LIVE-sourced for the console: the record stores `where` relative to the run
    # root (item 120), and the `From` line a human reads must be the absolute
    # tree they would cd into. `target / where` resolves a relative record and
    # passes an old absolute one through unchanged.
    stored = str(record.get("where") or "").strip()
    where = str(Path(target) / stored) if stored else "(not recorded)"
    return (
        f"## Rollback proposal\n\n"
        f"**Proposed, not run.** Rolling back is as much a change to a live "
        f"system as rolling forward, and it takes the same gates.\n\n"
        f"| | |\n|---|---|\n"
        f"| Revert on | `{branch}` |\n"
        f"| Back to | `{base}` |\n"
        f"| Then re-run | `{command}` |\n"
        f"| From | `{where}` |\n\n"
        f"The rollback path is the deploy path, which is the property worth "
        f"having: an undo that uses different machinery is an undo nobody has "
        f"tested.\n\n"
        f"**What this does NOT cover.** A deploy changes something outside the "
        f"repository, and this proposal only names the repository half. Any "
        f"data written, any schema migrated and anything a downstream system "
        f"already consumed is outside what reverting a commit undoes, and "
        f"whoever takes this decision has to say what happens to it.\n")


def spec_for(url: str) -> str:
    """The intake specification for one failing probe.

    **Deterministic**, because `intake.spec_hash` over this text is the identity
    `intake.duplicates` matches on. A run id or a timestamp in here would make
    the same failing endpoint a new defect every time it was probed, which is
    the opposite of filing once.
    """
    return (f"title: post-deploy probe failure: {url}\n\n"
            f"kind: {KIND}\n\n"
            f"## Symptom\n\n"
            f"The post-deploy probe `{url}` did not report healthy after a "
            f"deployment this repository recorded as successful.\n\n"
            f"## Why this is a defect and not a note\n\n"
            f"P9 recorded a deploy that worked and P10 asked the running system, "
            f"which disagreed. One of the two is wrong and neither can say which "
            f"from where it stands.\n\n"
            f"## Where it came from\n\n"
            f"Filed by `verify.py` at P10, not written by hand. The probe is one "
            f"this repository declares in its own profile, so somebody decided "
            f"it was worth asking.\n")


def open_runs_for(target: Path, url: str) -> list[str]:
    """Runs already open for this failing probe.

    Item 66's policy, and for its reason: a run that CLOSED does not hold the
    failure filed. If the probe is still failing after somebody's attempt went
    away, the symptom outlived the attempt.
    """
    from tools.sdlc import connect

    # ONE home for this rule, shared with `debt.py`, which carried the same
    # eighteen lines differing only in how it built a title.
    title = f"post-deploy probe failure: {url}"
    try:
        return connect.open_runs_for(Path(target), title, spec_for(url))
    except connect.ConnectError as exc:
        raise VerifyError(str(exc)) from None


def file_defect(target: Path, url: str, allow_dirty: bool = False) -> dict | None:
    """Open a defect for one failing probe, or None when one is already open.

    `intake.open_run`, the ordinary front door - a failed rollout is a change
    like any other and takes the same gates, and a second way to open a run
    would be a second answer to what a run IS.
    """
    from tools.sdlc import intake as entry

    if open_runs_for(Path(target), url):
        return None
    return entry.open_run(Path(target), spec_for(url), kind=KIND,
                          src_kind="probe", src_name=url,
                          allow_dirty=allow_dirty)


def recorded(target: Path, run_id: str) -> dict | None:
    """What this run recorded about its verification, or None if it never ran."""
    path = ledger.run_dir(Path(target), run_id) / RESULT
    if not path.is_file():
        return None
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VerifyError(f"{path} could not be read: {exc}") from None
    return found if isinstance(found, dict) else None


def run(target: Path, run_id: str, component: str = "", timeout: int = 10,
        file_defects: bool = True, allow_dirty: bool = False) -> dict:
    """Probe, record, and on failure propose the rollback and file the defect.

    **Refuses first, probes second, records always** - the shape P9 uses, for
    the same reason: a verification that only wrote evidence when it passed
    would hide the one case somebody has to act on.
    """
    target = Path(target)
    deploy_record = deployed(target, run_id)
    urls = probes_of(target, component)
    if not urls:
        raise VerifyError(
            "no probes are declared, so there is nothing to ask - a phase that "
            "asked nothing has not verified anything, and reporting it clean "
            "would be a green nobody earned")

    rows = [probe(url, timeout) for url in urls]
    answer = verdict(rows)
    row = {
        "schema_version": SCHEMA_VERSION, "run": run_id, "at": _now(),
        "verdict": answer, "probes": rows,
        "passed": sum(1 for r in rows if r.get("result") == PASS),
        "failed": sum(1 for r in rows if r.get("result") == FAIL),
        "unreachable": sum(1 for r in rows if r.get("result") == UNREACHABLE),
        # WHAT was verified, so a later reader is not left inferring it from a
        # timestamp - item 46's lesson, where the question the record could not
        # answer about itself was which tree it had operated on.
        "deploy": {"command": deploy_record.get("command"),
                   "where": deploy_record.get("where"),
                   "at": deploy_record.get("at")},
        "rollback_proposed": answer != PASS,
        "defects_filed": [], "defects_already_open": [],
        # **A defect nobody could file is not a defect that did not
        # happen.** Intake legitimately refuses a dirty tree, and a probe
        # failure swallowed by that refusal would leave a red rollout with
        # no run and no trace of why - zero silent drops, at the one
        # moment somebody is not watching.
        "defects_refused": [],
    }
    if answer != PASS:
        row["rollback"] = rollback_proposal(target, run_id, deploy_record)
        for bad in [r for r in rows if r.get("result") != PASS]:
            if not file_defects:
                continue
            try:
                opened = file_defect(target, bad["url"], allow_dirty)
            except Exception as exc:
                row["defects_refused"].append(
                    {"url": bad["url"], "why": str(exc)})
                continue
            if opened is None:
                row["defects_already_open"].extend(
                    open_runs_for(target, bad["url"]))
            else:
                row["defects_filed"].append(opened["run_id"])
    path = ledger.run_dir(target, run_id) / RESULT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    return row


def obligations(target: Path, run_id: str) -> list[str]:
    """What P10 owes, for `stage.verify` through the CONDITIONAL_JUDGE seam."""
    try:
        found = recorded(Path(target), run_id)
    except VerifyError as exc:
        return [str(exc)]
    try:
        at = ledger.phase_of(Path(target), run_id)
    except Exception as exc:
        return [f"{PHASE} could not tell whether this run owes {RESULT}: {exc} "
                f"- a phase nobody could read is not a run that has not "
                f"reached {PHASE}"]
    if at != PHASE:
        return []
    try:
        if not probes_of(Path(target)):
            # A repository that declares no probes is not one whose probes
            # failed. `stage.active` already folds the phase out; this judge
            # does not invent an obligation for it.
            return []
    except VerifyError as exc:
        return [str(exc)]
    if found is None:
        return [f"{PHASE} has recorded no verification ({RESULT}) - a deploy "
                f"nobody probed is one whose success is the deployment tool's "
                f"opinion of itself "
                f"(tools/sdlc/verify.py --target <repo> --run <id> --probe)"]
    if refused := found.get("defects_refused") or []:
        return [f"{len(refused)} probe failure(s) could not be filed as a "
                f"defect: {refused[0].get('why', 'no reason recorded')} "
                f"- the rollout is red and nothing is tracking it"]
    if found.get("verdict") != PASS:
        return [f"the post-deploy probes did not pass "
                f"({found.get('failed', 0)} failed, "
                f"{found.get('unreachable', 0)} unreachable) - the rollback "
                f"proposal is in {RESULT} and is somebody's decision to take"]
    return []


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR")
    ap.add_argument("--run", required=True, metavar="ID")
    ap.add_argument("--component", default="", metavar="NAME")
    ap.add_argument("--probe", action="store_true",
                    help="ask the declared probes; without it nothing is asked")
    ap.add_argument("--no-file", action="store_true",
                    help="do not open a defect for a failing probe")
    ap.add_argument("--timeout", type=int, default=10, metavar="S")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    if not args.probe:
        found = {"probes": probes_of(target, args.component),
                 "recorded": recorded(target, args.run)}
        if args.json:
            print(json.dumps(found, indent=2, sort_keys=True, default=str))
        else:
            for url in found["probes"]:
                print(f"  probe: {url}")
            if not found["probes"]:
                print("  (no probes declared)")
            print("  --probe asks them; nothing was asked")
        return 0 if found["probes"] else 1

    row = run(target, args.run, args.component, args.timeout,
              file_defects=not args.no_file, allow_dirty=args.allow_dirty)
    if args.json:
        print(json.dumps(row, indent=2, sort_keys=True))
        return 0 if row["verdict"] == PASS else 1
    print(f"verify: {row['verdict'].upper()} - {row['passed']} passed, "
          f"{row['failed']} failed, {row['unreachable']} unreachable")
    for r in row["probes"]:
        mark = "ok " if r["result"] == PASS else "!! "
        print(f"  {mark}{r['url']}  {r['result']}"
              + (f" ({r['detail']})" if r["detail"] else "")
              + f"  {r['seconds']}s")
    for opened in row["defects_filed"]:
        print(f"  filed defect run {opened}")
    for live in row["defects_already_open"]:
        print(f"  already open: {live}")
    for bad in row["defects_refused"]:
        print(f"  !! NOT filed for {bad['url']}: {bad['why']}")
    if row["rollback_proposed"]:
        print("  a rollback is PROPOSED in the record and was not run")
    return 0 if row["verdict"] == PASS else 1


def main(argv: list[str] | None = None) -> int:
    """The door. `3` is could-not-look, which must never read as verified."""
    cli.use_utf8()
    return cli.door(_main, argv, "the post-deploy verify phase", 3)


if __name__ == "__main__":
    raise SystemExit(main())
