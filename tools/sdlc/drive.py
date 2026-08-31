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
        return 127, f"the runner command could not be parsed: {exc}"
    return smoke.run_tool(argv + [prompt], cwd=cwd, env=smoke.runner_env())


def _document_for(stage) -> str | None:
    """The single document this phase owes, or None where it owes a directory
    or several things. **Only a single-document phase can be filled from a chat
    runner's stdout** - the one piece of glue this module adds, and it is
    bounded here rather than attempted everywhere."""
    singles = [p for p in stage.produces if not p.endswith("/")]
    return singles[0] if len(singles) == 1 and len(stage.produces) == 1 else None


def step(target: Path, run_id: str, supplied: dict[str, str],
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
    if acknowledged is not None and routed["name"] not in acknowledged:
        return {**record, "stop": REFUSED,
                "why": f"{routed['name']} is routed to {stage.name} and was not "
                       f"named in --spend - the routing changed after the walk "
                       f"was acknowledged, and an unacknowledged runner does "
                       f"not spend"}

    try:
        prompt = phases.render_prompt(target, run_id, phase, supplied)
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


def runners_ahead(target: Path, run_id: str) -> list[str]:
    """Every runner the walk from here COULD spawn — what `--spend` must name.

    Best effort by design: it reports the routing as declared now, and a
    refusal ahead is reported by `step` when the walk reaches it rather than
    guessed at here.
    """
    state = ledger.load(target, run_id)
    phase = state.get("phase", ledger.PHASES[0])
    running = [s for s in phases.active(target)]
    names, seen = [], False
    for stage in running:
        seen = seen or stage.phase == phase
        if not seen:
            continue
        try:
            routed = guardrails.runner_for(target, stage.name)
        except guardrails.RunnerError:
            continue
        if routed and routed["name"] not in names:
            names.append(routed["name"])
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
        print(f"run {args.run} would spawn: {', '.join(ahead) or '(nothing routed)'}")
        print(f"acknowledge with --spend {','.join(ahead)}" if ahead else
              "nothing to acknowledge - no phase ahead is routed")
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
