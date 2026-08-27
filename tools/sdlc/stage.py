"""The stage contract — item 36: what a phase RECEIVES, and what it must LEAVE.

`LIFECYCLE.md` is the normative phase list and `SKILL.md` was the working
implementation of a *different* numbering. Between them sat the thing this
module is: a phase said what it produced in a table nobody could execute, and
`profile.yml: phases[]` — the field that decides which phases run at all — was
read by no code in the harness. A phase list nothing reads is a phase list that
cannot be wrong, which is the same defect `gate_profile` had before item 63.

So the contract is data here, and prose everywhere else is derived from it or
checked against it:

**What a stage receives** — `render_prompt` emits provider-neutral prose plus a
block of exact values, one per line, running to the end of the line. This
generalises the block `RUNNER_CONTRACT.md` §2 pinned for one workflow. A value
a stage needs and nobody supplied is a REFUSAL, not a blank: item 33 paid for
that lesson twice, and "a requirement nobody states cannot be met" is the whole
reason the block exists.

**What a stage leaves** — every stage names the artifacts it owes. `verify`
answers whether they are there, and answers it in two parts, because an
artifact that exists but was never recorded through `state.record_artifact` is
not pinned: item 32 hashes the approved bytes so a gate goes VOID when they
move, and nothing was ever *required* to be in that set. An unpinned artifact
is a yes about bytes that can be swapped afterwards.

The one phase that named no artifact at all was **P7 — the phase G4 stands
at**, the merge gate that is hard in every regime. `70-triage.md` closes it,
and the omission is recorded rather than quietly fixed.

**Ordering is the spec's, never the profile's.** `phases[]` selects; it does
not sequence. A profile that lists them shuffled runs them in spec order, which
is what `LIFECYCLE.md` already promised and nothing enforced.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.sdlc import intake as adapter  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCHEMA_VERSION = 1

#: P5's conditional artifact, named HERE rather than in the module that writes
#: it. `suite.py` imports this module, so the dependency cannot run the other
#: way, and this module is embedded in the dependency-free ledger check while
#: `suite.py` is not. One home per name; the import graph decides which module
#: gets to be it. `suite.SCOPE_ADD` asks for this one.
SCOPE_ADD = "50-scope-add.md"
HARNESS = Path(__file__).resolve().parents[2]

#: Where the stage SOPs live in the harness. They are POINTED at, never copied
#: into an adopter — the same rule `workflows/` follows, so there is one copy to
#: keep current and one guard keeping it current.
SOP_DIR = Path("sdlc") / "stages"

#: A capability a stage needs before it can run at all. Blank means "always".
#: These are the left column of `LIFECYCLE.md`'s capability table, as data.
CAPABILITIES = ("", "graph", "deploy", "probes")


class Value(NamedTuple):
    """One line of a stage prompt's exact-value block."""

    key: str
    #: Where the harness gets it: "run" (computed here), "profile:<key>" (read
    #: from the profile), or "caller" (the operator must supply it).
    source: str
    what: str


class Stage(NamedTuple):
    phase: str
    name: str
    #: Artifacts the stage owes, relative to the run folder. Required.
    produces: tuple[str, ...]
    #: Artifacts it produces only when the trigger fires — an ADR is written
    #: when a dependency, boundary or data model changes, and demanding one for
    #: a typo fix would teach people to write empty ones.
    conditional: tuple[str, ...]
    #: The gate standing at the END of this stage, or "".
    gate: str
    needs: str
    values: tuple[Value, ...]


#: Values every stage's prompt states. The runner is told where it is, what it
#: is doing, and where the answer goes — nothing here is discoverable from the
#: prompt's prose, which is exactly why it is not IN the prose.
COMMON = (
    Value("phase", "run", "the phase being executed, on the spec's numbering"),
    Value("run", "run", "the run id; every artifact belongs to it"),
    Value("target", "run", "the repository root, and the working directory"),
    Value("sop", "run", "the procedure to follow, in the harness"),
    Value("artifacts-out", "run", "the run folder every artifact is written to"),
)

STAGES: tuple[Stage, ...] = (
    # P0's artifact is named by the module that WRITES it. Spelled as a literal
    # here, the contract deciding whether P0 delivered and the adapter that
    # delivers would be two independent spellings - and `intake.py`'s docstring
    # records that exact divergence happening once already, when the file was
    # `intake.md` on one side and `00-intake.md` on the other, so `--verify`
    # reported P0 as owing something the adapter had written.
    Stage("P0", "intake", (adapter.INTAKE_MD,), (), "", "", (
        Value("baseline-command", "profile:test_command",
              "the WORKSPACE's suite, run and recorded before anything changes; "
              "blank where components declare their own, which suite.py reads"),
        Value("conventions", "profile:conventions_files",
              "the repository's own documents, read before its code"),
    )),
    Stage("P1", "requirements", ("10-requirements.md",), (), "", "", ()),
    Stage("P2", "scoping", ("20-scope.md",), (), "G1", "graph", (
        Value("component", "caller", "the alias the changed paths are relative to"),
        Value("pack-out", "caller", "where the impact pack is written"),
    )),
    Stage("P3", "design", ("30-design.md",), ("30-adr-*.md",), "G2", "", ()),
    Stage("P4", "implementation", ("40-impl/",), (), "G3", "", (
        Value("component", "caller", "the component this agent owns; one agent, one"),
        Value("branch", "caller", "the branch the work lands on"),
        Value("worktree", "caller", "the isolated checkout to work in, or '-' for none"),
    )),
    Stage("P5", "test", ("50-tests.md",), (SCOPE_ADD,), "", "", (
        Value("test-command", "profile:test_command",
              "the WORKSPACE's suite; blank where components declare their own"),
        Value("coverage-command", "profile:coverage_command",
              "blank means coverage is unmeasurable, which is a finding, not a pass"),
        Value("coverage-threshold", "profile:coverage_threshold",
              "the minimum on CHANGED files, where the workspace sets one"),
        Value("baseline", "caller", "P0's recorded pass count; no NEW failures"),
    )),
    Stage("P6", "review", ("60-review.md",), (), "", "", (
        Value("diff", "caller", "the unified diff under review"),
        Value("component", "caller", "the alias the diff's paths are relative to"),
        Value("prepass-out", "caller", "where the deterministic pre-pass writes"),
        Value("pack-out", "caller", "where the context pack writes"),
        Value("hitl-target", "caller", "the repository the review session is recorded in"),
        Value("rubric", "run", "the scoring instrument; reported, never the gate"),
        Value("block-on", "profile:block_on", "THE exit condition, categorical"),
        Value("max-iterations", "profile:max_iterations", "the hard cap on the loop"),
    )),
    Stage("P7", "triage", ("70-triage.md",), (), "G4", "", ()),
    Stage("P8", "release", ("80-release.md",), (), "", "", (
        Value("branch", "caller", "the branch to publish; the merge click stays human"),
    )),
    Stage("P9", "deploy", ("90-deploy.md",), (), "G5", "deploy", (
        Value("deploy-command", "profile:deploy_command", "what a yes here will run"),
    )),
    Stage("P10", "verify", ("A0-verify.md",), (), "", "probes", ()),
    Stage("P11", "closeout", ("B0-closure.md",), (), "", "", ()),
)

#: `LIFECYCLE.md`'s own stated default, quoted rather than invented. A profile
#: that predates `phases[]` runs this, and `active()` says so out loud.
SOLO_DEFAULT = ("intake", "requirements", "design", "implementation", "test",
                "review", "triage", "closeout")

#: A conditional artifact needs something that DECIDES whether it is owed, or
#: it is a suggestion again - which is exactly what item 64 found `30-adr-*.md`
#: to be. Each entry is the module that judges, and the sentence the phase's
#: prompt adds about it. A test holds this map against the stages that declare
#: a conditional artifact, so one cannot be added with no judge behind it.
CONDITIONAL_JUDGE: dict[str, tuple[str, str, str]] = {
    "P3": ("tools.sdlc.adr", "--check",
           "Name every file you intend to change in backticks, workspace-relative, "
           "or the check has nothing to look at. Do not write an empty one to "
           "satisfy a checklist: a trigger that deserves no record is dismissed "
           "with a stated reason, which is a decision, and filler is not."),
    "P5": ("tools.sdlc.suite", "--execute",
           "It is owed exactly when the engine finds breakage in a component the "
           "approved design does not cover - which is a scope-add for G1, not a "
           "fix to make quietly here."),
}

NAMES = tuple(s.name for s in STAGES)
PHASES = tuple(s.phase for s in STAGES)


class StageError(RuntimeError):
    """A stage contract that cannot be satisfied, or cannot be judged."""


def by_phase(phase: str) -> Stage:
    for s in STAGES:
        if s.phase == phase:
            return s
    raise StageError(f"unknown phase {phase!r} - one of {', '.join(PHASES)}")


def by_name(name: str) -> Stage:
    for s in STAGES:
        if s.name == name:
            return s
    raise StageError(f"unknown phase name {name!r} - one of {', '.join(NAMES)}")


def gate_of(phase: str) -> str:
    """The gate standing at the end of a phase, or "" — the inverse of
    `approve.GATE_PHASE`, which a test holds the two sides of together."""
    return by_phase(phase).gate


def sop_path(stage: Stage, harness: Path | None = None) -> Path:
    root = harness if harness is not None else HARNESS
    return root / SOP_DIR / f"{stage.phase}-{stage.name}.md"


# ── which phases this workspace runs ─────────────────────────────────────────


def declared(target: Path) -> tuple[list[str], str]:
    """The profile's `phases[]`, and where it came from.

    Absence is not silently defaulted: the spec names a solo default and this
    returns it *labelled*, so a caller can report which of the two it obeyed.
    An unknown NAME is refused — a typo that silently drops the review phase is
    the failure this whole field exists to make visible.
    """
    listed = guardrails.profile_list(target, "phases")
    if listed is None:
        return list(SOLO_DEFAULT), "the spec's solo default (the profile declares no phases[])"
    names = [str(v).strip() for v in listed if str(v).strip()]
    if not names:
        raise StageError(
            "phases[] is empty - a lifecycle with no phases is not a shorter "
            f"lifecycle, it is none. Remove the key to take the solo default "
            f"({', '.join(SOLO_DEFAULT)}), or name the phases you want")
    if unknown := [n for n in names if n not in NAMES]:
        raise StageError(
            f"phases[] names {', '.join(repr(u) for u in unknown)}, which "
            f"{'are' if len(unknown) > 1 else 'is'} not "
            f"{'phases' if len(unknown) > 1 else 'a phase'} - one of "
            f"{', '.join(NAMES)}")
    return names, ".sdlc/profile.yml phases[]"


def capable(target: Path, capability: str) -> bool:
    """Whether this workspace has what a capability-gated stage needs."""
    if not capability:
        return True
    if capability == "graph":
        return (target / ".sdlc" / "graph").is_dir()
    if capability == "deploy":
        return bool((guardrails.profile_scalar(target, "deploy_command") or "").strip())
    if capability == "probes":
        return bool(guardrails.profile_list(target, "probes"))
    raise StageError(f"unknown capability {capability!r} - one of "
                     f"{', '.join(repr(c) for c in CAPABILITIES if c)}")


def active(target: Path) -> list[Stage]:
    """The stages this workspace runs, in SPEC order.

    Two filters, and they say different things. A phase the profile did not
    list is one the workspace chose not to run. A phase it listed but has no
    capability for is one it cannot run — `skipped` reports that pair
    separately, because "you turned it off" and "you have no graph" are not the
    same sentence to anybody debugging a run.
    """
    names, _ = declared(target)
    return [s for s in STAGES if s.name in names and capable(target, s.needs)]


def skipped(target: Path) -> list[tuple[Stage, str]]:
    """Every stage that will not run, and which of the two reasons it is."""
    names, _ = declared(target)
    out: list[tuple[Stage, str]] = []
    for s in STAGES:
        if s.name not in names:
            out.append((s, "not listed in phases[]"))
        elif not capable(target, s.needs):
            out.append((s, f"listed, but this workspace has no {s.needs} capability"))
    return out


def next_phase(target: Path, phase: str) -> str:
    """The phase after this one for THIS workspace — skipping what it does not
    run, which is the only reason this is not `PHASES[i + 1]`."""
    running = [s.phase for s in active(target)]
    if phase not in running:
        raise StageError(f"{phase} is not an active phase here - active: "
                         f"{', '.join(running) or '(none)'}")
    i = running.index(phase)
    return running[i + 1] if i + 1 < len(running) else ""


# ── what a stage leaves behind ───────────────────────────────────────────────


def _exists(run_folder: Path, rel: str) -> bool:
    path = run_folder / rel.rstrip("/")
    return path.is_dir() if rel.endswith("/") else path.is_file()


def _nonempty(run_folder: Path, rel: str) -> bool:
    path = run_folder / rel.rstrip("/")
    if rel.endswith("/"):
        return any(path.iterdir())
    return path.stat().st_size > 0


def verify(target: Path, run_id: str, phase: str) -> list[str]:
    """Everything the stage owes and has not delivered, as sentences.

    Three distinct failures, kept distinct on purpose: the artifact is absent,
    the artifact is empty, or the artifact exists and is not RECORDED. The
    third is the one worth having — item 32 pins an approved artifact's hash so
    the gate goes VOID when the bytes move, and until something requires a
    phase's output to be in that set, the pin covers nothing.
    """
    stage = by_phase(phase)
    folder = ledger.run_dir(target, run_id)
    if not folder.is_dir():
        raise StageError(f"no run folder for {run_id} at {folder}")
    state = ledger.load(target, run_id)
    recorded = set(state.get("artifacts", {}))
    problems: list[str] = []
    for rel in stage.produces:
        if not _exists(folder, rel):
            problems.append(f"{phase} ({stage.name}) owes {rel}, which is not in "
                            f"the run folder")
        elif not _nonempty(folder, rel):
            problems.append(f"{phase} ({stage.name}) owes {rel}, which is "
                            f"{'an empty directory' if rel.endswith('/') else 'empty'} "
                            f"- an artifact nobody wrote reads exactly like one "
                            f"nobody needed")
        elif rel not in recorded:
            problems.append(f"{phase} ({stage.name}) wrote {rel} but never recorded "
                            f"it (state.py --record --artifact {rel}), so its bytes "
                            f"are not pinned and a yes about them covers nothing")
    if stage.conditional:
        # Item 64, generalised by item 38. `conditional` was data nothing read:
        # the artifact was asked for in a prompt and required by nobody, so
        # whether one was owed was the runner's own opinion of its own work.
        # Each judging module decides from evidence instead. Imported HERE for
        # the reason `halted_at` states: they read this module for the artifact
        # contract, so a module-level import would be a cycle.
        import importlib
        if phase not in CONDITIONAL_JUDGE:
            raise StageError(
                f"{phase} names a conditional artifact ({', '.join(stage.conditional)}) "
                f"and CONDITIONAL_JUDGE has nothing that decides whether it is owed - "
                f"an artifact nobody judges is a suggestion")
        judge = importlib.import_module(CONDITIONAL_JUDGE[phase][0])
        problems += judge.obligations(target, run_id)
    return problems


def owed(target: Path, run_id: str, upto: str) -> list[str]:
    """What every active stage up to and including `upto` still owes."""
    running = [s.phase for s in active(target)]
    if upto not in PHASES:
        raise StageError(f"unknown phase {upto!r} - one of {', '.join(PHASES)}")
    limit = PHASES.index(upto)
    problems: list[str] = []
    for phase in running:
        if PHASES.index(phase) <= limit:
            problems.extend(verify(target, run_id, phase))
    return problems


def halted_at(target: Path, run_id: str, phase: str) -> list[str]:
    """Why this run may not LEAVE `phase` yet, on account of its gate.

    Item 41, and the sentence M4's kill gate is written in: *unskippable*
    gates. Every other piece was already here and none of them stopped a
    runner from simply moving on. `approve.py` refuses a yes about work that
    does not exist; `validate.py` refuses a merge without one; the halt itself
    — the thing the whole lifecycle is named for — was a paragraph in an SOP
    that the phase machine did not enforce.

    Which gates halt is the REGIME's answer, not this function's: `solo` does
    not stop at G3 and `team` does, and reading `hard_gates` rather than a
    constant is what keeps the dial honest in both directions.
    """
    # Imported HERE, not at module scope. `approve` imports this module for the
    # artifact contract (item 36) and this needs `approve` for the gate, which
    # is a genuine mutual dependency: the gate stands on the artifacts and the
    # phase machine stands on the gate. A module-level cycle between them
    # happens to resolve today and would break the first time either one used
    # the other while still importing.
    from tools.sdlc import approve as approvals

    stage = by_phase(phase)
    if not stage.gate:
        return []
    rules = guardrails.load(target)
    dials = guardrails.profile_dials(target)
    hard = guardrails.hard_gates(rules, dials, guardrails.gate_overrides(target))
    if stage.gate not in hard:
        return []
    ok, why = approvals.gate_state(target, run_id, stage.gate)
    if ok:
        return []
    return [f"{stage.gate} stands at the end of {phase} ({stage.name}) and is hard "
            f"under this regime, so the run does not move past it: {why}"]


def advance(target: Path, run_id: str, phase: str, note: str = "",
            force: bool = False) -> dict:
    """Move a run the way the contract says: the phase being LEFT must have
    delivered what it owes, and its gate must have been taken.

    `state.advance` stays the low-level operation and is deliberately not
    changed — a lifecycle whose ledger refuses to record a move it disagrees
    with is a ledger that lies. This is the layer that has an opinion.

    Moving BACKWARDS is exempt from the gate check and not from the artifact
    one. P5 loops to P4 and P7 loops to P3; a run returning to earlier work has
    not passed the gate ahead of it, and demanding one to go back would make
    the loop the lifecycle is built around impossible.
    """
    state = ledger.load(target, run_id)
    leaving = state.get("phase", ledger.PHASES[0])
    running = [s.phase for s in active(target)]
    forward = PHASES.index(phase) > PHASES.index(leaving)
    if leaving != phase and leaving in running:
        problems = verify(target, run_id, leaving)
        if forward:
            problems += halted_at(target, run_id, leaving)
        if problems:
            if not force:
                raise StageError(
                    f"{leaving} is not finished with, so moving to {phase} would "
                    f"leave a hole nothing later can fill: " + "; ".join(problems)
                    + " (--force records the override)")
            note = (note + " | " if note else "") + \
                f"forced past {leaving}: {'; '.join(problems)}"
    return ledger.advance(target, run_id, phase=phase, note=note)


# ── what a stage receives ────────────────────────────────────────────────────


def resolve(target: Path, stage: Stage, run_id: str, supplied: dict[str, str],
            harness: Path | None = None) -> dict[str, str]:
    """Fill every value the stage's prompt states, or refuse naming the gap.

    A value the caller must supply and did not is a refusal rather than a blank
    line. The alternative was tried by accident and is recorded in
    `RUNNER_CONTRACT.md` §9: a prompt that omits a value a CLI demands produces
    a runner that cannot finish while behaving perfectly.
    """
    root = harness if harness is not None else HARNESS
    folder = ledger.run_dir(target, run_id)
    out: dict[str, str] = {}
    missing: list[str] = []
    for value in COMMON + stage.values:
        if value.source == "run":
            out[value.key] = {
                "phase": stage.phase,
                "run": run_id,
                "target": str(target),
                "sop": str(sop_path(stage, root)),
                "artifacts-out": str(folder),
                "rubric": str(target / ".sdlc" / "review-rubric.md"),
            }[value.key]
        elif value.source.startswith("profile:"):
            key = value.source.partition(":")[2]
            found = guardrails.profile_scalar(target, key)
            if found is None:
                listed = guardrails.profile_list(target, key)
                found = ", ".join(str(v) for v in listed) if listed is not None else None
            out[value.key] = "" if found is None else str(found)
        elif value.key in supplied:
            given = supplied[value.key]
            # The block's whole invariant is one value per line, running to the
            # end of it. A value carrying a line break does not merely look
            # wrong — its second line reads as another key, so a caller who
            # controls one value controls all of them. Refused, not sanitised:
            # a value that had to be repaired was not the value anybody meant.
            if "\n" in given or "\r" in given:
                raise StageError(
                    f"{stage.phase}'s value {value.key} contains a line break, and "
                    f"every value in the block runs to the end of its line - the "
                    f"text after the break would be read as another key")
            out[value.key] = given
        else:
            missing.append(f"{value.key} ({value.what})")
    if extra := sorted(set(supplied) - {v.key for v in COMMON + stage.values}):
        raise StageError(
            f"{stage.phase} states no value called {', '.join(extra)} - a value "
            f"the contract does not name is one nothing will read")
    if missing:
        raise StageError(
            f"{stage.phase} ({stage.name}) needs {len(missing)} value(s) nobody "
            f"supplied: " + "; ".join(missing))
    return out


def render_prompt(target: Path, run_id: str, phase: str,
                  supplied: dict[str, str] | None = None,
                  harness: Path | None = None) -> str:
    """The runner-facing prompt: prose, then the exact-value block.

    The prose says what the phase is FOR and points at the SOP; it does not
    restate the steps. One copy of a procedure is the only number of copies
    that can be current.
    """
    stage = by_phase(phase)
    values = resolve(target, stage, run_id, supplied or {}, harness)
    produces = ", ".join(stage.produces) or "(nothing)"
    lines = [
        f"Execute phase {stage.phase} ({stage.name}) of the SDLC lifecycle for "
        f"run {run_id}.",
        "",
        f"Follow the SOP at the `sop:` path below, step by step, in order. It is "
        f"the procedure; this prompt is only the values it needs. Write what the "
        f"phase owes into the `artifacts-out:` directory: {produces}. Record each "
        f"one with `state.py --record`, or it is not pinned and no gate can stand "
        f"on it.",
    ]
    if stage.conditional:
        module, flags, sentence = CONDITIONAL_JUDGE[stage.phase]
        cli = module.rsplit(".", 1)[1] + ".py"
        lines += ["", f"Conditionally also: {', '.join(stage.conditional)}. Whether it "
                      f"is owed is not your judgement — `tools/sdlc/{cli} --target "
                      f"{target} --run {run_id} {flags}` decides it from evidence, and "
                      f"this phase is not finished while one is owed and missing. "
                      + sentence]
    if stage.gate:
        lines += ["", f"{stage.gate} stands at the end of this phase. You do not take "
                      f"it and you do not assume it: finish the work, then STOP. The "
                      f"approval CLI refuses an entry you write for yourself."]
    lines += ["", "Values — each runs to the end of its line:", ""]
    lines += [f"{key}: {values[key]}" for key in
              [v.key for v in COMMON + stage.values]]
    return "\n".join(lines) + "\n"


# ── CLI ──────────────────────────────────────────────────────────────────────


def _set_pairs(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise StageError(f"--set wants key=value, got {pair!r}")
        out[key.strip()] = value
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR",
                    help="the repository the run lives in")
    ap.add_argument("--run", metavar="ID", help="the run id")
    ap.add_argument("--phase", metavar="P", help="the phase, e.g. P6")
    ap.add_argument("--list", action="store_true",
                    help="the stages this workspace runs, and what it skips")
    ap.add_argument("--prompt", action="store_true",
                    help="render the runner prompt for --phase")
    ap.add_argument("--verify", action="store_true",
                    help="what --phase still owes (--upto for everything before it too)")
    ap.add_argument("--upto", action="store_true",
                    help="with --verify, judge every active phase up to --phase")
    ap.add_argument("--advance", metavar="P",
                    help="move the run to P, refusing while the phase it leaves owes")
    ap.add_argument("--force", action="store_true",
                    help="with --advance, override and RECORD the override")
    ap.add_argument("--note", default="", metavar="TEXT")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="a value the caller must supply for --prompt")
    args = ap.parse_args(argv)
    target = Path(args.target).resolve()

    try:
        if args.list:
            names, source = declared(target)
            print(f"phases from {source}")
            for s in active(target):
                gate = f"  -> {s.gate}" if s.gate else ""
                print(f"  {s.phase:<4} {s.name:<15} {', '.join(s.produces)}{gate}")
            for s, why in skipped(target):
                print(f"  {s.phase:<4} {s.name:<15} skipped: {why}")
            return 0
        if args.prompt:
            if not (args.run and args.phase):
                print("!! --prompt needs --run and --phase")
                return 2
            print(render_prompt(target, args.run, args.phase,
                                _set_pairs(args.set)), end="")
            return 0
        if args.verify:
            if not (args.run and args.phase):
                print("!! --verify needs --run and --phase")
                return 2
            problems = (owed(target, args.run, args.phase) if args.upto
                        else verify(target, args.run, args.phase))
            for p in problems:
                print(f"!! {p}")
            print(f"{args.phase}: {'clean' if not problems else str(len(problems)) + ' owed'}")
            return 1 if problems else 0
        if args.advance:
            if not args.run:
                print("!! --advance needs --run")
                return 2
            state = advance(target, args.run, args.advance, args.note, args.force)
            print(f"run {args.run}: {state['phase']} / {state['status']}")
            return 0
    except (StageError, guardrails.PolicyError, RuntimeError, ValueError) as exc:
        print(f"!! {exc}")
        return 2
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
