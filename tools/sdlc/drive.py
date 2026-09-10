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

**A resume skips a phase; it does not decide one was right.** Re-invoking
continues, and item 127 made that stop re-running work a human had already
accepted. Item 143 fixed the two ways it got the question wrong: the
marker recording how the last attempt ENDED was read as what the phase IS
and was never cleared, so a resume became permanent and the lifecycle's
own loops back (P5->P4, P7->P3, P8->P4) walked past phases nothing had
re-run; and only an `owed` stop was resumable, so an APPROVED gate
re-spawned the phase whose document it had just pinned.

**One `--set` map, judged ONCE against the whole walk and read per phase.**
Item 109 makes a value the phase's contract does not name a REFUSAL rather
than a silent drop, correctly. Item 142 measured what that costs a LOOP:
`drive` handed the same map to every phase, so P6's `--diff` was refused at
P5 and supplying nothing stopped at P6 - and P5->P6->P7 has no gate to force
the break an operator would otherwise make. **Both properties are kept by
asking the question at the right altitude**: a key no phase AHEAD would read
is refused before anything spawns, naming the phases checked, and each phase
is then handed exactly the keys its own contract reads.

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
import shutil
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

#: **A phase that delivered and did not ANSWER - item 138, the tenth stop.**
#: The count is written down each time it moves, deliberately: item 106 said
#: six, item 127 made it eight, item 143 counted nine.
#:
#: A word of its own rather than `owed`, because the repairs are opposite.
#: `owed` says the document is missing or empty and somebody must write it;
#: this says the document is PRESENT, pinned, verifying clean - and identical
#: to the bytes a human rejected, so the runner it was re-spawned for did
#: nothing. Reporting that as `gate` is what the driver did, and it was right
#: by every check it had: measured, goose hit *no credits remaining*, wrote a
#: 365-byte transcript and exited 0, and the operator's only tell was reading
#: that transcript by hand.
#:
#: **Not RESUMABLE, and it needs no exemption to be safe**: the condition is
#: self-clearing, because the moment anybody changes the document the digests
#: differ and it stops firing. A marker that cannot go stale is item 143's
#: defect, and this one cannot outlive its cause.
UNANSWERED = "unanswered"

#: **The stops a LATER invocation may skip past, and no others - item
#: 143.** Item 127's W10 made the walk resumable for `owed` alone, because
#: that is the case it was found in: a human repairs what the phase owed,
#: and continuing must not re-run it under a paid runner. **`gate` belongs
#: here for a STRONGER reason than `owed` does**, and it is read off the
#: returns in `_step_once` rather than argued: a GATE stop is reached only
#: after `verify` AND `stale` have both passed, so it says the phase
#: delivered and its bytes are pinned, while an OWED stop says it did not.
#: The rest may never be skipped, each for its own reason: `unrouted`,
#: `refused` and `needs-value` are phases that never ran at all,
#: `ran-badly` and `could-not-spawn` are runs that failed, and `stale` is
#: bytes that moved after the phase recorded them.
RESUMABLE = (OWED, GATE)

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


def runner_env() -> dict[str, str]:
    """The environment this module SPAWNS a runner with.

    **One producer, read by the spawn and by the scrub** (item 115: compose the
    producer with the consumer rather than let each form its own opinion). The
    first cut spawned with `provider_smoke.runner_env()` - the shell PLUS every
    name the harness `.env` supplies - and scrubbed against `os.environ`, so
    the one set of credentials the harness itself ADDS, the provider key
    `RUNNER_CONTRACT` §3 tells adopters to put in `.env`, was exactly the set
    scrubbing could not see. It went verbatim into the transcript, and item 121
    commits the run record into the adopter's repository.

    It reads no file of its own: `provider_smoke.runner_env` already loads
    `.env`, and a second reader is how the two answers diverge.
    """
    from tools import provider_smoke as smoke

    return smoke.runner_env()


def scrub(text: str) -> str:
    """Redact secrets from recorded text, through `provider_smoke.safe`.

    `safe` is scrub PLUS `mask_inline`, so a credential carried in the runner's
    own command line - `--api-key sk-...`, which the harness never saw in the
    environment and therefore cannot match by value - is masked too. The first
    cut had neither: it recorded `record["command"]` verbatim.

    The secrets come from `runner_env()`, the environment the spawn uses, and
    they are the values of every secret-NAMED variable with no length floor:
    `secret_values`' `len >= 8` is a rule about replacing text safely, and
    using it here made a seven-character `*_KEY` `<redacted>` in the record's
    `env` and intact in the same run's transcript.
    """
    from tools import provider_smoke as smoke

    return smoke.safe(text, smoke.named_secret_values(runner_env()))


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

    **ARGV IS NOT ENOUGH WHERE cmd.exe IS THE LAUNCHER**, which was measured
    rather than assumed: a `.cmd` runner - the shape npm and pipx install on
    Windows - is run BY cmd.exe whatever this passes, so `spawn('x.cmd',
    'line one\\nline two\\nline three')` returned exit 0 having delivered
    `'RAN "line one'`. Exit 0 is a step the driver goes on from, so the runner
    was asked one line of a twelve-line contract and nothing recorded that the
    rest was dropped. `command_problem` refuses it here, in the same words
    `--plan` prints, because the two asking one question is the only thing
    that stops them answering differently (item 115).

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
    # The question `--plan` answers, asked by the thing it answers it ABOUT.
    problem = command_problem(command)
    if problem:
        return (smoke.NOT_STARTED_CODE, f"{smoke.NOT_STARTED_NOTE}{problem}")
    return smoke.run_tool(argv + [prompt], cwd=cwd, env=runner_env())


def _document_for(stage) -> str | None:
    """The single document this phase owes, or None where it owes a directory
    or several things. **Only a single-document phase can be filled from a chat
    runner's stdout** - the one piece of glue this module adds, and it is
    bounded here rather than attempted everywhere."""
    singles = [p for p in stage.produces if not p.endswith("/")]
    return singles[0] if len(singles) == 1 and len(stage.produces) == 1 else None


def _reads_from_caller(stage) -> set[str]:
    """Every key `resolve` would read out of `supplied` for this phase.

    Item 142. A `run` value is read from the run and a `profile:` value from
    `profile.yml`, so a caller's copy of either is read by NOBODY - which is
    why neither is here, and why supplying one is refused rather than filtered
    away. **Measured across all twelve phases, exactly five keys are ever read
    from a caller**: component, branch, diff, prepass-out and hitl-target.
    """
    return {value.key for value in stage.values
            if value.source != "run"
            and not value.source.startswith("profile:")}


def _stated_elsewhere(key: str) -> str:
    """Where the harness gets `key` from when a caller may not state it, or "".

    Derived from the contract rather than listed, so a value that changes its
    source says the right thing here without anybody remembering to come back -
    which has happened five times to this key set (items 109, 121, 124, 125,
    126 each moved one to `run`).
    """
    for phase in phases.PHASES:
        for value in phases.COMMON + phases.by_phase(phase).values:
            if value.key != key:
                continue
            if value.source == "run":
                return "the run itself"
            if value.source.startswith("profile:"):
                return f"profile.yml, as {value.source.partition(':')[2]}"
    return ""


def span_from(target: Path, run_id: str) -> list:
    """The phases this walk could still reach, as their stage contracts.

    **One home with `--plan`**: `plan_ahead` already computes exactly this and
    item 127 built it for the operator's question, so validating against a
    second opinion about where the walk goes would be the drift item 94 exists
    to end. It is every ACTIVE phase from here on rather than the phases before
    the next halting gate - the owner's ruling, and the reason is that a walk
    is resumable, so an operator names the whole remaining walk's values once
    and a typo still names no phase at all.
    """
    return [phases.by_phase(row["phase"]) for row in plan_ahead(target, run_id)]


def consumers(supplied: dict[str, str], span: list) -> dict[str, list[str]]:
    """Which phases in the span would READ each supplied key; [] means nobody.

    The visible half of item 142, and it is the reason the filtering is not a
    silent drop: `--plan` prints this, so an operator sees which phase each
    value is for before a runner is paid.
    """
    return {key: [stage.phase for stage in span
                  if key in _reads_from_caller(stage)]
            for key in sorted(supplied)}


def unread_values(supplied: dict[str, str], span: list) -> list[str]:
    """One refusal per supplied key no phase in this walk would read.

    **Judged ONCE, against the span the walk starts from.** Re-asking it per
    step would refuse the caller mid-walk for having supplied something a phase
    just behind them consumed: at P7 the span no longer holds P6, and P6's
    `diff` is exactly the value this item exists to let through.
    """
    checked = ", ".join(stage.phase for stage in span) or "(nothing ahead)"
    problems = []
    for key, where in consumers(supplied, span).items():
        if where:
            continue
        if elsewhere := _stated_elsewhere(key):
            problems.append(
                f"{key} is stated by {elsewhere}, so supplying it can only "
                f"disagree with what the harness already knows - remove the "
                f"--set")
        else:
            problems.append(
                f"no phase in this walk states a value called {key} - checked "
                f"{checked} - so nothing would read it")
    return problems


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


def _must_answer_a_rejection(target: Path, run_id: str, phase: str) -> bool:
    """Does this phase's gate stand REJECTED, so the work must be redone?

    Item 143. A rejection is an instruction to do the phase AGAIN - item
    136 carries the reason into the very next prompt so the runner can
    answer it - so it outranks every reason to skip. Without this, making
    a GATE stop resumable would have killed the reject->re-scope loop that
    items 136, 137, 139 and 140 were built to make work, at the one gate a
    human is waiting on.

    **A rejection nobody can READ is treated as one that stands** - and
    MEASURING which corruptions reach that branch narrowed it a long way
    from what this docstring first claimed. `standing_rejection` answers
    could-not-look with the gate and a reason rather than raising, and
    this cannot tell that apart from a verdict, so it re-runs: an absent
    rejection and an unreadable one are not the same thing, and the
    cheaper mistake is one runner call rather than a loop that silently
    declines to close.

    **It fires only when the ledger cannot be read AT ALL.** A corrupt
    LINE does not reach it: `read_chain` returns that line as an
    `_unreadable` marker rather than raising and `live_entries` skips the
    marker, so `standing_rejection` reports no rejection and this driver
    skips - which costs nothing, because `halted_at` then stops the walk
    at the gate anyway. **Found while pinning this and NOT fixed here**
    (item 45's rule, and it is a different module and a different
    question): the sentence a person then reads is *G2 has no entry in the
    ledger*, so an unreadable verdict is reported as nobody having
    decided - absent versus could-not-look, conflated inside
    `approve.gate_state`.
    """
    gate, _who, _why = phases.standing_rejection(target, run_id, phase)
    return bool(gate)


def _step_once(target: Path, run_id: str, supplied: dict[str, str],
               run=spawn, acknowledged: set[str] | None = None,
               span: list | None = None) -> dict:
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

    # **Item 142, and it is asked before ROUTING because it is the
    # caller's own error**: a value nothing in this walk will read is wrong
    # whoever is routed and whatever the profile says. `span` is passed in
    # by `drive` so the judgement is made ONCE, at the phase the walk
    # started from; a lone `step` judges from where it stands.
    if unread := unread_values(supplied, span if span is not None
                               else span_from(target, run_id)):
        return {**record, "stop": REFUSED, "why": "; ".join(unread)}

    try:
        routed = guardrails.runner_for(target, stage.name)
    except guardrails.RunnerError as exc:
        return {**record, "stop": REFUSED, "why": str(exc)}
    if routed is None:
        return {**record, "stop": UNROUTED,
                "why": f"{stage.name} is not in phase_runners, so nobody is "
                       f"declared to run it - the driver is not a whoever"}
    record["runner"] = routed["name"]
    # **Item 149.** The resolved runner NAME was the whole record, and a runner
    # answers as its ENVIRONMENT tells it to. Recorded per step so the run's
    # own history can answer "under what" as well as "who" - by NAME, never by
    # guessing which names matter.
    record["env"] = supplied_env()

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
    #
    # **Item 143 widened the first clause and added the second.** A GATE
    # stop is resumable too (see `RESUMABLE`): the marker said `gate`, so
    # once a human APPROVED, the next invocation re-spawned the phase whose
    # document that gate had just pinned - measured, G2 approved and the
    # second walk spawned once. A real runner writes the document itself,
    # so the re-spawn moves approved bytes and item 32 then voids the gate
    # that had just been taken. **Unless it stands REJECTED**, which is the
    # case where re-running IS the point.
    if (_last_stop(target, run_id, phase) in RESUMABLE
            and not _must_answer_a_rejection(target, run_id, phase)
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

    # Each phase is handed exactly the keys its OWN contract reads. The
    # rest belong to another phase of this same walk and are carried, not
    # dropped - which is only safe because `unread_values` above has
    # already refused anything no phase would read.
    mine = {k: v for k, v in supplied.items()
            if k in _reads_from_caller(stage)}
    record["values"] = sorted(mine)

    try:
        prompt = phases.render_prompt(target, run_id, phase, mine)
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
    # **Item 138, and the ORDER is the whole of it.** This sits after `verify`
    # and `stale` - so the phase has delivered and its bytes are pinned - and
    # BEFORE `halted_at`, which is what used to answer here. A run that was
    # re-spawned to answer a rejection and came back with the rejected bytes
    # is not a run waiting at its gate; reporting it as one sends the operator
    # to approve a document the same operator has already refused.
    if same := phases.unanswered(target, run_id, phase):
        return {**record, "stop": UNANSWERED, "why": "; ".join(same)}
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
         run=spawn, acknowledged: set[str] | None = None,
         span: list | None = None) -> dict:
    """One step, with its OUTCOME recorded so a later invocation can read it.

    Item 127: `drive()` loops inside one process, but W10 is about the NEXT
    invocation - a human repairs an OWED stop and runs the driver again - so
    how the last attempt ended has to outlive the process. One place, so no
    return path can forget.
    """
    found = _step_once(target, run_id, supplied, run=run,
                       acknowledged=acknowledged, span=span)
    where = _stop_file(target, run_id, found["phase"])
    if found.get("stop"):
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_text(found["stop"], encoding="utf-8",
                         newline=chr(10))
    else:
        # **Item 143: the marker is STATE, and it is cleared the moment it
        # stops being true.** Nothing removed it, so W10's one-shot resume
        # became PERMANENT for that phase. Measured on a live run: it
        # carried `P2.stop: owed` written three days earlier while P2
        # verified clean and unstale, so a loop BACK to P2 was skipped as
        # already-delivered and advanced past, spawning nothing - and the
        # lifecycle's own diagram draws P5->P4, P7->P3 and P8->P4, so the
        # loops are the design rather than an edge. **What HAPPENED stays
        # in the transcript beside it**; this file only ever answered *how
        # did the last attempt end*, and once the phase has advanced that
        # is no longer an answer a later invocation may act on.
        where.unlink(missing_ok=True)
    return found


def drive(target: Path, run_id: str, supplied: dict[str, str],
          limit: int = 12, run=spawn,
          acknowledged: set[str] | None = None) -> list[dict]:
    """Step until something needs a human. Returns every step's record.

    `limit` is a runaway bound, not a policy: the phases are finite and each
    step advances or stops, so hitting it means something is looping and the
    loop should end rather than spend forever.
    """
    # **Item 142: computed ONCE, before the first step.** The span shrinks
    # as the walk advances, so re-deriving it per step would refuse the
    # caller at P7 for the `diff` P6 had just read.
    span = span_from(target, run_id)
    done: list[dict] = []
    for _ in range(limit):
        found = step(target, run_id, supplied, run=run,
                     acknowledged=acknowledged, span=span)
        done.append(found)
        if found["stop"]:
            break
    return done


#: What a value is replaced with when its NAME says it is a credential.
#: Not the value's own characters - a redaction that varied with the secret
#: would leak its length.
REDACTED = "<redacted>"

#: What a value is replaced with when it could be a LOCATION rather than a
#: setting. **The run record is committed into the adopter's repository (item
#: 121), so anything recorded here ships there** - and item 57 and item 120
#: were both an absolute machine path written into a file adopters commit,
#: somewhere this repository's own private-coordinate guard cannot see because
#: it scans THIS tree. The first cut of `supplied_env` recorded every
#: non-secret value and would have shipped one.
NOT_RECORDED = "<not recorded: may be a location>"


def recordable(value: str) -> bool:
    """Whether a value is a SETTING rather than a coordinate.

    A path separator, whitespace or an unusual length is enough to withhold
    it: this errs toward recording LESS, which is the safe direction here
    because the cost of over-recording is a private path in somebody else's
    repository and the cost of under-recording is a name with no value beside
    it. The names are always kept, so nothing disappears - only its value.

    It forms no opinion about what a value MEANS, which is the vendor
    knowledge item 104 ruled out; it asks only whether the value could be a
    place.
    """
    return bool(value) and len(value) <= 40 and not any(
        c in value for c in "/\\" ) and not any(c.isspace() for c in value)


def supplied_env() -> dict[str, str]:
    """What the HARNESS adds to a runner's environment, ready to record.

    **Item 149's second instance, and it is the one the walk paid for.** A
    runner's behaviour is a function of its COMMAND and its ENVIRONMENT, and
    this harness composes both: it declares the command in `profile.yml` and
    it supplies the environment out of `.env`. Item 105 records the resolved
    runner's NAME and nothing else - so on 2026-09-06 the harness `.env`
    carried `GOOSE_PROVIDER=claude-code` while goose's own config and the
    runner's own note both said OpenAI, and **the runner called "goose" would
    have invoked Claude while every document in the project said otherwise**.
    Nothing anywhere would have recorded the difference, and the run history
    could not have told anybody afterwards.

    **It reads nothing about any vendor, and that is the constraint that
    shaped it.** Item 104 ruled that a capability gate naming models cuts
    against a harness that names no vendor, and the same applies here: this
    does not know what a provider is, cannot parse a runner's banner, and
    forms no opinion about whether an environment is right. It records what
    the harness itself put there, which is the part the harness is
    accountable for.

    **Only what the harness ADDS**, because `runner_env` lets an exported
    variable win over the file: a name the shell already carries is the
    operator's, not this file's, and claiming it would be the record
    overstating what the harness did.

    **Names always; values only where the name is not secret-shaped AND the
    value could not be a location.** The name rule is `provider_smoke`'s own
    rather than a second opinion (item 115, which exists because a copy of it
    had already drifted). The location rule exists because the run record is
    COMMITTED into the adopter's repository, and the first cut of this
    function recorded every non-secret value - which would have shipped an
    absolute machine path out of `.env`, item 57's defect and item 120's,
    somewhere this repository's own guard cannot see. A provider and a model
    are exactly what this is for; a key and a path are exactly what it must
    never carry.
    """
    from tools import provider_smoke as smoke

    fold = (lambda k: k.upper()) if os.name == "nt" else (lambda k: k)
    shell = {fold(k) for k in os.environ}
    out: dict[str, str] = {}
    for key, value in smoke.load_env(smoke.HARNESS / ".env").items():
        if fold(key) in shell:
            continue
        if smoke.secret_named(key):
            out[key] = REDACTED
        else:
            out[key] = value if recordable(value) else NOT_RECORDED
    return dict(sorted(out.items()))


#: The two suffixes `CreateProcess` routes THROUGH cmd.exe instead of
#: launching. They are not "cannot launch" - they START, and cmd.exe then
#: delivers only `'RAN "line one'` of a three-line prompt at exit 0, measured
#: on a real shim. Windows owns this pair; it is asked BEFORE the image test
#: below precisely because a `.cmd` is not an executable image and yet runs.
SHELL_SUFFIXES = (".cmd", ".bat")

#: The first two bytes of every file Windows launches DIRECTLY. **Windows
#: dispatches on the file's CONTENT, not on its suffix**, and that was measured
#: rather than reasoned about: one real PE copied to `.exe`, `.com`, `.vbs`,
#: `.js`, `.foo`, `.ps1` and to a name with no suffix at all started from an
#: outright spelling every time, while a TEXT `.ps1`, `.vbs` and `.js` each
#: answered `[WinError 193] %1 is not a valid Win32 application`.
#:
#: So the question is asked as an ALLOW-LIST - *is this an executable image* -
#: and never as a list of refused suffixes. A deny-list is silent about every
#: value added after it is written: `PATHEXT` is per-machine and grows, and the
#: first cut of this rule named `.cmd`/`.bat` and cleared `.ps1` spelled
#: outright, plus bare names resolving to `.vbs`, `.js`, `.COM` and to an
#: extensionless executable, on the machine it was written on.
EXECUTABLE_IMAGE = b"MZ"


def spawn_opens(program: str) -> str | None:
    """The file `spawn`'s `CreateProcess` will OPEN for `program`, or None.

    **Not the file `shutil.which` reports, and the gap is the whole of half 1.**
    Measured with a real PE installed under each suffix and spelled BARE:
    `x.com`, `x.vbs`, `x.js`, `x.cmd` and an extensionless `x` were all found by
    `which` - which honours `PATHEXT` and, since 3.12, the bare name itself -
    and all five answered `[WinError 2]` at the spawn. Only `x.exe` started.
    `CreateProcess` appends `.exe` to a name that carries no extension and
    tries nothing else, so PATHEXT is a cmd.exe fact that the spawn never
    consults.

    A spelling that carries a directory separator is opened literally: an
    absolute path to an extensionless PE and `.{sep}name` both started.
    """
    if os.sep in program or (os.altsep and os.altsep in program):
        return shutil.which(program)
    return shutil.which(program if Path(program).suffix else program + ".exe")


def executable_image(path: str) -> bool | None:
    """Whether `path` is a file Windows can launch, or None if it could not be
    read. **The third answer is deliberate** - "could not look" is not "no",
    and collapsing the two would let an unreadable runner be reported with a
    reason that is not the true one. Both callers refuse; they say why
    differently."""
    try:
        with open(path, "rb") as handle:
            return handle.read(len(EXECUTABLE_IMAGE)) == EXECUTABLE_IMAGE
    except OSError:
        return None


def command_problem(command: str) -> str:
    """Why this declared runner command could not START, or "" if it could.

    **Item 149's free half, and the walk of 2026-09-06 is why it exists.** A
    profile declared the command `goose run ...` and `goose` was not on that
    machine's PATH. Item 127's `could-not-spawn` catches exactly that - AT the
    spawn, correctly and with no spend - but `--plan`, whose whole job is
    answering *what would run* before anybody decides to pay, reported the
    runner as routed and said nothing. The operator learns at the spawn what
    a lookup could have told them beforehand.

    **It asks the question the SPAWN asks, not a second one - and the spawn
    now asks it HERE.** `spawn` tokenises with `provider_smoke.split_runner`
    and this uses the same call, so a command that cannot be PARSED is
    reported here in the words that module already owns rather than by a
    private opinion about quoting (item 115: two definitions of one thing
    drift). Sharing the CALL was not enough on Windows: this resolved with
    `shutil.which`, which honours `PATHEXT` and so finds the `.cmd` shim npm
    and pipx install, while the spawn's `CreateProcess` appends only `.exe` -
    so `--plan` printed no `!!` and exited 0 for a runner that answered
    `[WinError 2]` a moment later, which is verbatim the defect this function
    exists to close, for the commonest Windows install shape. The two now
    agree by CONSTRUCTION rather than by a second check: `spawn` calls this
    and refuses on any answer it gives.

    **A cmd.exe shim is refused rather than resolved, and that is the second
    measurement.** Spelled outright, `w118probe.cmd` STARTS - and cmd.exe
    delivers only the first line of a prompt `render_prompt` guarantees is
    multi-line, expanding any `%NAME%` in the rest into the argument on the
    way. `spawn`'s docstring records that truncation as the shell form's
    defect and argv as the fix; argv fixed it for a program Windows launches
    directly, and a `.cmd` is not one. A prompt that cannot be delivered
    intact must not cost a paid call, so this is a refusal at BOTH ends, not
    a warning on one.

    **The Windows rule is an ALLOW-LIST, and the first cut of it was not.**
    Naming `.cmd` and `.bat` closed the shape that was in front of us and left
    the identical defect live at four more: `.ps1` spelled outright (`which`
    resolves it, the spawn answers `[WinError 193]`), and bare names resolving
    to `.vbs`, `.js`, `.COM` or to an extensionless executable (`[WinError 2]`
    every time, measured with a real PE under each). A deny-list is silent
    about every value added after it is written - `sca.py` learned this at
    `b58ccc9`, where a status neither list anticipated made the allow-list say
    3 and the deny-list say 0 - and PATHEXT is a per-machine list that grows.
    So `""` is returned only where BOTH questions CreateProcess asks come back
    yes: `spawn_opens` says the spawn will open the very file `which` named,
    and `executable_image` says that file is one Windows launches. Nothing is
    cleared by not being on a list.

    **PATH is `os.environ`'s, and that is provable rather than assumed.**
    `spawn` runs under this module's `runner_env()`, which adds a name from the
    harness `.env` only where the shell does not already have it - and PATH is
    always set in a shell - so the two agree for this key by construction. A
    test pins that, and reading `.env` here would widen where a secret is
    loaded for the sake of a lookup that cannot differ.

    **A report, never a promise.** A command can appear between this and the
    spawn, or vanish; `runners_ahead` already documents that best-effort
    posture for routing, and `step` remains the thing that decides.
    """
    from tools import provider_smoke as smoke

    try:
        argv = smoke.split_runner(command)
    except ValueError as exc:
        return f"its command cannot be parsed: {exc}"
    if not argv:
        return "its command is empty, so there is nothing to run"
    program = argv[0]
    # `which` answers both shapes - an absolute path and a bare name -
    # and it is the ACCURATE question: it checks the execute bit, where
    # `Path.is_file` would call a non-executable file runnable. Measured
    # before this line was written that way.
    resolved = shutil.which(program)
    if not resolved:
        return (f"{program!r} is not on PATH, so the spawn would answer "
                f"'did not start' rather than running the phase")
    if os.name != "nt":
        return ""
    opens = spawn_opens(program)
    if not opens:
        return (f"{program!r} resolves to {Path(resolved).name}, which the "
                f"spawn cannot even find: PATHEXT is a cmd.exe list and "
                f"CreateProcess consults none of it - it appends only '.exe' "
                f"to a bare name, so it would answer 'did not start' rather "
                f"than running the phase")
    if Path(opens).suffix.lower() in SHELL_SUFFIXES:
        return (f"{program!r} resolves to {Path(opens).name}, which Windows "
                f"runs THROUGH cmd.exe - and cmd.exe delivers only the first "
                f"line of the prompt, so the phase must not be spawned")
    image = executable_image(opens)
    if image is None:
        return (f"{program!r} resolves to {Path(opens).name}, which could not "
                f"be read, so whether Windows can launch it is unknown and the "
                f"phase must not be spawned on a guess")
    if not image:
        return (f"{program!r} resolves to {Path(opens).name}, which is not a "
                f"Windows executable image, so CreateProcess would answer "
                f"'%1 is not a valid Win32 application' rather than running "
                f"the phase")
    return ""


def hard_gates(target: Path) -> list[str]:
    """Which gates this regime actually HALTS at.

    **Item 147.** `plan_ahead` reported each phase's DECLARED gate and `--plan`
    printed *HALTS* for every one, while `stage.halted_at` - the function that
    decides it for real - says in its own docstring that *"which gates halt is
    the REGIME's answer, not this function's: `solo` does not stop at G3 and
    `team` does"*. Measured on the shipped `solo` preset, whose hard gates are
    G2 and G4: `--plan` claimed a halt at G1, G3 AND G5, three of the five,
    on the one screen an operator reads BEFORE deciding to spend.

    `slice.py` already asks this question beside its own declared-gate lookup,
    so the answer was one import away and the walk's own planner was the
    surface that did not ask it.

    **It does NOT soften an unreadable policy into a third answer, and that
    was measured rather than assumed.** The first cut returned None for one,
    on the reasoning that `--plan` reports rather than moves. Running it
    proved the branch UNREACHABLE: `runner_for` calls `policy.load` for every
    row, so an invalid `policy.yml` already ends `plan_ahead` with that
    reader's own by-line refusal, and an unreadable PROFILE stops
    `phases.active` earlier still. A defensive branch nobody can reach is a
    claim nobody has checked, so the refusal is left where it already is - and
    it is the right one: every answer on this screen is read out of the
    constitution, so a plan drawn from one nobody can read is not a plan.
    """
    return guardrails.hard_gates(guardrails.load(target),
                                 guardrails.profile_dials(target),
                                 guardrails.gate_overrides(target))


def plan_ahead(target: Path, run_id: str) -> list[dict]:
    """Every phase the walk would reach from here, with who runs it.

    **Item 127.** `runners_ahead` walked exactly this and threw the phases
    away, so `--plan` could answer *who* and never *which phases, or where it
    stops* - which is the operator's actual question before spending. The
    routing is reported as DECLARED NOW and a refusal ahead is still `step`'s
    to make when the walk reaches it, which is the same best-effort posture
    `runners_ahead` documented and is why this cannot be treated as a promise.

    Each row: `{phase, name, runner, gate, halts}`. `runner` is None where
    nothing is routed - which `--plan` must show rather than omit, because a
    phase nobody runs is exactly the thing an operator wants to see before
    starting. `gate` stays the phase's DECLARED gate, which is a property of
    the contract; `halts` is the REGIME's answer about that gate and is the
    one an operator is actually asking (item 147).

    The regime is read ONCE, before the loop, for item 142's reason one
    question along: it is a property of the run rather than of a phase, and
    asking it per row would be twelve reads of two documents to answer the
    same question twelve times.
    """
    state = ledger.load(target, run_id)
    phase = state.get("phase", ledger.PHASES[0])
    hard = hard_gates(target)
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
                     "gate": stage.gate or "",
                     "halts": stage.gate in hard,
                     # Item 149: routed is not the same as runnable, and the
                     # difference is free to measure.
                     "problem": command_problem(str((routed or {}).get(
                         "command", "")) ) if routed else ""})
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
            # **Item 147: three sentences, because they are three different
            # facts and each one calls for a different act.** A gate that
            # halts is a person to go and find; a gate this regime does not
            # halt at is a phase the walk goes straight through, and telling
            # an operator otherwise makes them wait for a stop that never
            # comes; and a regime nobody could read is not either answer.
            if not row["gate"]:
                gate = ""
            elif row["halts"]:
                gate = f"  -> {row['gate']} HALTS after this phase"
            else:
                gate = (f"  -> {row['gate']} is declared here and this regime "
                        f"does not halt at it")
            print(f"  {row['phase']} ({row['name']}): {who}{gate}")
        # Item 127: `--plan` returned before `args.spend` was read, so
        # `--plan --spend goose` printed "acknowledge with --spend goose" -
        # advice to do the thing that had just been done.
        # Item 142: an operator planning a walk finds out here that a value
        # is for a phase this walk never reaches, or for nobody at all -
        # before a runner is paid rather than after.
        span = span_from(target, args.run)
        for key, where in consumers(supplied, span).items():
            if where:
                print(f"  {key} is read by {', '.join(where)}")
        unread = unread_values(supplied, span)
        for problem in unread:
            print(f"!! {problem}")
        # **Item 149.** A phase whose runner cannot start is not a phase this
        # walk would run, and `--plan` is the one screen an operator reads
        # BEFORE deciding to spend. Reported per phase because two phases can
        # route to different runners and only one of them be missing.
        unrunnable = [f"{row['phase']} routes to {row['runner']!r} and "
                      f"{row['problem']}" for row in rows if row["problem"]]
        for problem in unrunnable:
            print(f"!! {problem}")
        # **Item 149's second half, on the same screen as its first.** A
        # runner answers as its ENVIRONMENT tells it to, and the harness
        # supplies part of that environment - so an operator deciding whether
        # to spend should see what will be handed over, BEFORE it is. Shown
        # rather than judged: this module knows nothing about what any of
        # these names mean, and inventing an opinion would be the vendor
        # knowledge item 104 ruled out.
        if ahead:
            supplied = supplied_env()
            if supplied:
                print("  the harness supplies, from its own .env: "
                      + ", ".join(f"{k}={v}" for k, v in supplied.items()))
            else:
                print("  the harness supplies nothing from its own .env; the "
                      "runner answers to the shell it inherits")
        named = {n.strip() for n in (args.spend or "").split(",") if n.strip()}
        missing = [n for n in ahead if n not in named]
        if not ahead:
            print("nothing to acknowledge - no phase ahead is routed")
        elif not missing:
            print(f"--spend already names every runner ahead "
                  f"({', '.join(ahead)}); this walk is acknowledged")
        else:
            print(f"acknowledge with --spend {','.join(ahead)}")
        # A plan carrying a value nothing would read is not a plan that
        # runs, and saying so at exit 0 would be this module reporting a
        # refusal as an answer. **Item 149 folds in for the same reason**: a
        # plan naming a runner that cannot start is not a plan that runs
        # either, and the operator finding out at the spawn is the whole
        # defect this closes.
        return 2 if (unread or unrunnable) else 0
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
                used = ", ".join(found.get("values") or [])
                print(f"  {found['phase']} ({found['name']}) ran under "
                      f"{found['runner']} -> {found['advanced_to']}"
                      + (f" (read {used})" if used else ""))
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
