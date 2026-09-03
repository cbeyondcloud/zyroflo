"""The driver — item 106: unattended phase execution BETWEEN the human gates.

The owner's requirement (3): *define the pipeline — which runs automatically and
where a human is needed.* Both halves were already declared and only one was
executed. `phases[]` says which phases run (item 36), `gate_profile` says which
gates halt (item 63), `phase_runners:` says who runs each (item 105), and
`stage.verify` says what a phase still owes. **This module adds no vocabulary.
It is a loop over those four answers**, which is why it can be read in one
sitting and why nothing here can invent a pipeline the profile did not state.

**Every human moment is a STOP, and the stop is the point.** The driver halts —
resumably, because `state.json` is the state and re-invoking continues — at: a
hard gate not yet approved, a phase nobody routed, a routing refusal, a phase
that still owes artifacts, a value only a caller can supply, and any runner exit
it cannot read as done.

**There is no `--force` here, deliberately.** `stage.py` has one because a human
is standing there taking responsibility in the moment. **An unattended loop that
could force past a gate is precisely the machine the halt rule exists to
refuse**, and the absence is asserted by a test rather than remembered.

**It never approves.** `approve.py` is not imported and not spawned - asserted
by AST, the way item 48 asserts the rollback proposal executes nothing and item
47 asserts the merge click stays human. A driver that could record a gate would
make every gate in the profile decorative.

**Paid work is acknowledged before it starts.** Runners spend; a loop that
spawns them spends repeatedly and without anybody watching. `--spend` must name
the runners it is about to invoke, so CLAUDE.md's confirm-before-a-paid-call
rule survives the loop that does the calling.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import cli  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import stage as phases  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

SCHEMA_VERSION = 1

#: Where a spawn's transcript lands, under the run's own folder — an unattended
#: run nobody can reconstruct is an incident report waiting to be unwritable.
TRANSCRIPTS = "drive"

#: Why the loop stopped. Each is a different act by a different person, which is
#: the whole reason they are not one word: a gate needs a decision, a missing
#: route needs a profile edit, an owed artifact needs work.
GATE, UNROUTED, REFUSED, OWED, NEEDS_VALUE, RAN_BADLY, DONE = (
    "gate", "unrouted", "refused", "owed", "needs-value", "ran-badly", "done")

#: **A runner that never STARTED - item 127, and the seventh stop.** This
#: module was built with six and the count was written down as six; the pin
#: moves deliberately rather than by accident. `run_tool` has always returned
#: `(127, "did not run: ...")` for a spawn failure, and reading only the code
#: reported it as `ran-badly - goose exited 127`, which describes a process
#: that RAN. On walk 3 the true reason - `[WinError 2] The system cannot find
#: the file specified` - survived only in the transcript. A runner that is not
#: installed and a runner that misbehaved need opposite repairs, which is the
#: whole reason these are separate words rather than one.
COULD_NOT_SPAWN = "could-not-spawn"

#: **Stale is not owed, and item 127 keeps them apart.** A phase that never
#: recorded its artifact and one that recorded it and then wrote it again are
#: one failure from the RUNNER's side, and they had opposite treatment: the
#: first halted the walk and the second was silent, so walk 3 advanced over
#: bytes that had moved. `stage.stale` is asked here rather than inside
#: `stage.verify`, because `approve.record` calls verify and LIFECYCLE.md makes
#: improving an artifact before approving it the ordinary case.
STALE = "stale"

#: A secret-NAMED variable's value never reaches a transcript or a note.
#: **Imported, not restated.** The first cut copied `provider_smoke`'s regex on
#: the grounds that a cross-package import would pull this module into the
#: ledger check's closure - which is false: `validate.closure()` is computed
#: from what VALIDATE transitively imports, so an import here cannot add this
#: module to it, and this file already imports `tools.cli`. The copy had
#: already drifted at birth (it dropped `CREDENTIAL`), so a `*_CREDENTIAL`
#: value reached the durable transcript that the original would have redacted.


class DriveError(RuntimeError):
    """The loop cannot start. Distinct from a STOP, which is the loop working."""


def scrub(text: str) -> str:
    """Redact secrets from recorded text, through `provider_smoke.safe`.

    `safe` is scrub PLUS `mask_inline`, so a credential carried in the runner's
    own command line - `--api-key sk-...`, which the harness never saw in the
    environment and therefore cannot match by value - is masked too. The first
    cut had neither: it recorded `record["command"]` verbatim.
    """
    from tools import provider_smoke as smoke

    return smoke.safe(text, smoke.secret_values(dict(os.environ)))


def spawn(command: str, prompt: str, cwd: Path, timeout: int = 3600) -> tuple[int, str]:
    """Run one runner: its command, then the prompt as the FINAL argument.

    **ARGV, never a shell.** The first cut built `f'{command} "{prompt}"'` with
    `shell=True`, reasoning that the declared command is a command LINE rather
    than a list. It is - and `provider_smoke.split_runner` already solves
    exactly that, Windows-aware, which is why this now composes the machinery
    the conformance smoke uses instead of a second implementation of §1. The
    shell form was broken three ways and a real spawn would have shown any of
    them: `render_prompt` ALWAYS returns multi-line text, so cmd.exe truncated
    every prompt at its first newline and ran the remaining lines as commands;
    a `"` anywhere in a value (a connector-supplied issue title reaches here)
    escaped the quoting and injected; and no environment was passed, so a
    runner whose key lives in the harness `.env` - where `RUNNER_CONTRACT` §3
    says to put it - greened the smoke and then failed under the driver.

    `timeout` is accepted for the callers that state one; `run_tool` owns the
    spawn and turns a timeout into `(127, "did not run: ...")` rather than an
    exception that would leave the phase with no transcript.
    """
    from tools import provider_smoke as smoke

    try:
        argv = smoke.split_runner(command)
    except ValueError as exc:
        # Item 127: the same sentinel, because a command that cannot be PARSED
        # never started either - and the caller should not have to know which
        # of the two ways it failed to start in order to say so.
        return (smoke.NOT_STARTED_CODE,
                f"{smoke.NOT_STARTED_NOTE}the runner command could not be "
                f"parsed: {exc}")
    return smoke.run_tool(argv + [prompt], cwd=cwd, env=smoke.runner_env())


def _document_for(stage) -> str | None:
    """The single document this phase owes, or None where it owes a directory
    or several things. **Only a single-document phase can be filled from a chat
    runner's stdout** - the one piece of glue this module adds, and it is
    bounded here rather than attempted everywhere."""
    singles = [p for p in stage.produces if not p.endswith("/")]
    return singles[0] if len(singles) == 1 and len(stage.produces) == 1 else None


def _stop_file(target: Path, run_id: str, phase: str) -> Path:
    return ledger.run_dir(target, run_id) / TRANSCRIPTS / f"{phase}.stop"


def _last_stop(target: Path, run_id: str, phase: str) -> str:
    """How this driver's last attempt at `phase` ended, or "".

    Item 127. `drive()` loops within one invocation, but W10 is about the NEXT
    invocation - after a human has repaired an OWED stop - so the outcome has
    to outlive the process. Written beside the transcript because that is where
    a person already looks for what happened.
    """
    try:
        return _stop_file(target, run_id, phase).read_text(
            encoding="utf-8").strip()
    except OSError:
        return ""


def _step_once(target: Path, run_id: str, supplied: dict[str, str],
               run=spawn, acknowledged: set[str] | None = None) -> dict:
    """Execute the run's CURRENT phase, or say why it cannot be.

    Returns a record: `{stop, phase, ...}` where `stop` is one of the reasons
    above. **The order of the checks is the design**: routing before spawning,
    because a misroute must never spend; the gate check after the phase's own
    work, because a gate is taken on what the phase produced.

    `acknowledged` is the set `--spend` named, and it is re-checked HERE rather
    than only before the walk. The first cut validated it once against
    `runners_ahead()` and then re-read the routing from `profile.yml` on every
    iteration - and `profile.yml` is not a protected path, while the
    tool-capable runners this loop spawns run with `cwd=target`. A mid-walk
    edit to `runners:`/`phase_runners:` could therefore spend on a runner
    nobody acknowledged. None means no acknowledgement was required (the
    in-process callers and the tests); an empty set means one was required and
    named nothing.
    """
    state = ledger.load(target, run_id)
    phase = state.get("phase", ledger.PHASES[0])
    stage = phases.by_phase(phase)
    record: dict = {"phase": phase, "name": stage.name, "run": run_id}

    try:
        routed = guardrails.runner_for(target, stage.name)
    except guardrails.RunnerError as exc:
        return {**record, "stop": REFUSED, "why": str(exc)}
    if routed is None:
        return {**record, "stop": UNROUTED,
                "why": f"{stage.name} is not in phase_runners, so nobody is "
                       f"declared to run it - the driver is not a whoever"}
    record["runner"] = routed["name"]

    # **Part 5, and the module's own docstring already promised it: the walk is
    # RESUMABLE.** It was not. `step` spawned unconditionally, so after a human
    # repaired an OWED stop by pinning the document the runner wrote,
    # continuing re-ran the phase under a PAID runner and overwrote the very
    # document that had just been accepted. On walk 3 the only way forward was
    # `stage.py --advance` by hand, outside the tool.
    #
    # **The discriminator is this driver's OWN transcript, not "owes
    # nothing".** `intake.open_run` writes and records `00-intake.md` before
    # the driver ever starts, so a phase-owes-nothing test would skip P0 on
    # every fresh run - measured, not reasoned about. A transcript means THIS
    # driver already ran the phase; owing nothing and having nothing stale mean
    # it delivered. All three, or it spawns.
    # **Only a phase that stopped at OWED may be skipped**, and my own test
    # found why the transcript alone is not enough: a transcript says THIS RAN,
    # not this SUCCEEDED. P0's artifact is written and pinned by
    # `intake.open_run` before the driver starts, so a phase whose runner
    # FAILED still verifies clean - and a transcript-only check silently
    # skipped a phase nobody had successfully run. The outcome is recorded
    # beside the transcript rather than inferred from it.
    if (_last_stop(target, run_id, phase) == OWED
            and not phases.verify(target, run_id, phase)
            and not phases.stale(target, run_id, phase)):
        record["skipped"] = "already delivered by an earlier step of this walk"
        if held := phases.halted_at(target, run_id, phase):
            return {**record, "stop": GATE, "why": "; ".join(held)}
        nxt = phases.next_phase(target, phase)
        if not nxt:
            return {**record, "stop": DONE,
                    "why": f"{phase} is the last active phase"}
        try:
            phases.advance(target, run_id, nxt, note="advanced by the driver")
        except phases.StageError as exc:
            return {**record, "stop": REFUSED, "why": str(exc)}
        return {**record, "stop": "", "advanced_to": nxt}
    if acknowledged is not None and routed["name"] not in acknowledged:
        return {**record, "stop": REFUSED,
                "why": f"{routed['name']} is routed to {stage.name} and was not "
                       f"named in --spend - the routing changed after the walk "
                       f"was acknowledged, and an unacknowledged runner does "
                       f"not spend"}

    try:
        prompt = phases.render_prompt(target, run_id, phase, supplied)
    except phases.StageRefusal as exc:
        # Item 127. Four of `StageError`'s five meanings are the OPPOSITE of a
        # missing value - a key the contract does not name, a run-sourced value
        # supplied anyway, a value carrying a line break, a component the run
        # never heard of - and all five were reported as `needs-value`, which
        # tells an operator to supply what they already supplied.
        return {**record, "stop": REFUSED, "why": str(exc)}
    except phases.StageError as exc:
        return {**record, "stop": NEEDS_VALUE, "why": str(exc)}

    code, output = run(routed["command"], prompt, target)
    folder = ledger.run_dir(target, run_id) / TRANSCRIPTS
    folder.mkdir(parents=True, exist_ok=True)
    transcript = folder / f"{phase}.txt"
    transcript.write_text(scrub(output), encoding="utf-8", newline="\n")
    record["exit_code"] = code
    record["transcript"] = str(transcript)
    record["command"] = scrub(routed["command"])

    from tools import provider_smoke as smoke

    if smoke.did_not_start(code, output):
        return {**record, "stop": COULD_NOT_SPAWN,
                "why": f"{routed['name']} never started: {output.strip()}. "
                       f"Nothing was spent. Install it, or fix the command in "
                       f"`runners:` - this is not a runner that misbehaved"}
    if code != 0:
        return {**record, "stop": RAN_BADLY,
                "why": f"{routed['name']} exited {code}; its output is at "
                       f"{transcript}"}

    # The chat glue, bounded: a `chat` runner returns text and the phase owes a
    # file. Only for a phase owing exactly ONE document, and only when the
    # runner could not have written it itself.
    if routed.get("capabilities") == "chat":
        document = _document_for(stage)
        if document:
            path = ledger.run_dir(target, run_id) / document
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(scrub(output), encoding="utf-8", newline="\n")
                ledger.record_artifact(target, run_id, document)
                record["wrote"] = document

    if problems := phases.verify(target, run_id, phase):
        return {**record, "stop": OWED, "why": "; ".join(problems)}
    # **Walk 3's W9.** A phase that never recorded its artifact halted the walk
    # and one that recorded it and then wrote it AGAIN did not, though from the
    # runner's side they are one failure. Nothing unsafe reached a gate -
    # `approve` re-hashes and item 32 voids over moved bytes - so the cost was
    # WHERE it was caught: at the gate, after later phases had built on it.
    if moved := phases.stale(target, run_id, phase):
        return {**record, "stop": STALE, "why": "; ".join(moved)}
    if held := phases.halted_at(target, run_id, phase):
        return {**record, "stop": GATE, "why": "; ".join(held)}

    nxt = phases.next_phase(target, phase)
    if not nxt:
        return {**record, "stop": DONE, "why": f"{phase} is the last active phase"}
    # `stage.advance` re-checks everything and records the routing (item 105).
    # Calling it rather than `ledger.advance` is what keeps ONE opinion about
    # whether a run may move - and it is called WITHOUT force, which is the
    # whole posture of this module.
    # `stage.advance` can refuse for the phase being ENTERED (its own routing
    # check). Uncaught, that left the driver reporting the same condition two
    # ways: a misroute on the CURRENT phase became a clean REFUSED record at
    # exit 1, while the identical misroute one phase ahead escaped as a
    # StageError through `cli.door` as a generic exit 2. One vocabulary.
    try:
        phases.advance(target, run_id, nxt, note="advanced by the driver")
    except phases.StageError as exc:
        return {**record, "stop": REFUSED, "why": str(exc)}
    return {**record, "stop": "", "advanced_to": nxt}


def step(target: Path, run_id: str, supplied: dict[str, str],
         run=spawn, acknowledged: set[str] | None = None) -> dict:
    """One step, with its OUTCOME recorded so a later invocation can read it.

    Item 127: `drive()` loops inside one process, but W10 is about the NEXT
    invocation - a human repairs an OWED stop and runs the driver again - so
    how the last attempt ended has to outlive the process. One place, so no
    return path can forget.
    """
    found = _step_once(target, run_id, supplied, run=run,
                       acknowledged=acknowledged)
    if found.get("stop"):
        where = _stop_file(target, run_id, found["phase"])
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_text(found["stop"], encoding="utf-8",
                         newline=chr(10))
    return found


def drive(target: Path, run_id: str, supplied: dict[str, str],
          limit: int = 12, run=spawn,
          acknowledged: set[str] | None = None) -> list[dict]:
    """Step until something needs a human. Returns every step's record.

    `limit` is a runaway bound, not a policy: the phases are finite and each
    step advances or stops, so hitting it means something is looping and the
    loop should end rather than spend forever.
    """
    done: list[dict] = []
    for _ in range(limit):
        found = step(target, run_id, supplied, run=run,
                     acknowledged=acknowledged)
        done.append(found)
        if found["stop"]:
            break
    return done


def plan_ahead(target: Path, run_id: str) -> list[dict]:
    """Every phase the walk would reach from here, with who runs it.

    **Item 127.** `runners_ahead` walked exactly this and threw the phases
    away, so `--plan` could answer *who* and never *which phases, or where it
    stops* - which is the operator's actual question before spending. The
    routing is reported as DECLARED NOW and a refusal ahead is still `step`'s
    to make when the walk reaches it, which is the same best-effort posture
    `runners_ahead` documented and is why this cannot be treated as a promise.

    Each row: `{phase, name, runner, gate}`. `runner` is None where nothing is
    routed - which `--plan` must show rather than omit, because a phase nobody
    runs is exactly the thing an operator wants to see before starting.
    """
    state = ledger.load(target, run_id)
    phase = state.get("phase", ledger.PHASES[0])
    rows, seen = [], False
    for stage in phases.active(target):
        seen = seen or stage.phase == phase
        if not seen:
            continue
        try:
            routed = guardrails.runner_for(target, stage.name)
        except guardrails.RunnerError:
            routed = None
        rows.append({"phase": stage.phase, "name": stage.name,
                     "runner": (routed or {}).get("name"),
                     "gate": stage.gate or ""})
    return rows


def runners_ahead(target: Path, run_id: str) -> list[str]:
    """Every runner the walk from here COULD spawn — what `--spend` must name.

    Best effort by design: it reports the routing as declared now, and a
    refusal ahead is reported by `step` when the walk reaches it rather than
    guessed at here.
    """
    names = []
    for row in plan_ahead(target, run_id):
        if row["runner"] and row["runner"] not in names:
            names.append(row["runner"])
    return names


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR")
    ap.add_argument("--run", required=True, metavar="RUN_ID")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="a value the phase's prompt states and only a caller "
                         "knows (component, branch, diff, ...); repeatable")
    ap.add_argument("--spend", metavar="RUNNERS",
                    help="acknowledge the paid work: a comma list naming every "
                         "runner this walk may spawn. Required to execute - "
                         "runners spend, and a loop spends repeatedly with "
                         "nobody watching")
    ap.add_argument("--plan", action="store_true",
                    help="say what WOULD run and stop; spawns nothing")
    ap.add_argument("--limit", type=int, default=12, metavar="N")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    supplied = {}
    for pair in args.set:
        key, sep, value = pair.partition("=")
        if not sep:
            raise DriveError(f"--set {pair!r} is not KEY=VALUE")
        supplied[key.strip()] = value

    ahead = runners_ahead(target, args.run)
    if args.plan:
        rows = plan_ahead(target, args.run)
        print(f"run {args.run} would walk {len(rows)} phase(s):")
        for row in rows:
            who = row["runner"] or "(nothing routed - the walk stops here)"
            gate = f"  -> {row['gate']} HALTS after this phase" if row["gate"] else ""
            print(f"  {row['phase']} ({row['name']}): {who}{gate}")
        # Item 127: `--plan` returned before `args.spend` was read, so
        # `--plan --spend goose` printed "acknowledge with --spend goose" -
        # advice to do the thing that had just been done.
        named = {n.strip() for n in (args.spend or "").split(",") if n.strip()}
        missing = [n for n in ahead if n not in named]
        if not ahead:
            print("nothing to acknowledge - no phase ahead is routed")
        elif not missing:
            print(f"--spend already names every runner ahead "
                  f"({', '.join(ahead)}); this walk is acknowledged")
        else:
            print(f"acknowledge with --spend {','.join(ahead)}")
        return 0
    if not args.spend:
        print(f"!! --spend is required: this walk may spawn "
              f"{', '.join(ahead) or '(nothing routed)'}, and every one of them "
              f"costs. Re-run with --spend {','.join(ahead)}", file=sys.stderr)
        return 2
    named = {n.strip() for n in args.spend.split(",") if n.strip()}
    if unnamed := [n for n in ahead if n not in named]:
        print(f"!! --spend does not name {', '.join(unnamed)}, which this walk "
              f"may spawn - acknowledge every runner or route fewer phases",
              file=sys.stderr)
        return 2

    steps = drive(target, args.run, supplied, limit=args.limit,
                  acknowledged=named)
    if args.as_json:
        print(json.dumps({"run": args.run, "steps": steps}, indent=2,
                         sort_keys=True))
    else:
        for found in steps:
            if not found["stop"]:
                print(f"  {found['phase']} ({found['name']}) ran under "
                      f"{found['runner']} -> {found['advanced_to']}")
            else:
                print(f"  {found['phase']} ({found['name']}): "
                      f"{found['stop'].upper()} - {found['why']}")
    last = steps[-1] if steps else {}
    return 0 if last.get("stop") in ("", DONE, GATE) else 1


def main(argv: list[str] | None = None) -> int:
    """The door. 0 = the walk ended somewhere a human is meant to be (a gate,
    the end, or mid-flight); 1 = it stopped on something to FIX; 2 = it could
    not start. A gate is not a failure - it is the lifecycle working."""
    cli.use_utf8()
    return cli.door(_main, argv, "the driver", 2)


if __name__ == "__main__":
    raise SystemExit(main())
