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
import json
import sys
from fnmatch import fnmatch
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

#: Every artifact the phase contract names, and the ONE place each is named.
#:
#: They live here rather than with the modules that write them because the
#: import graph leaves no choice, which is item 94's rule rather than a
#: preference: this module is embedded in the dependency-free ledger check,
#: so it cannot import `tools.review.*` at all, and `suite.py` imports THIS
#: module, so the dependency cannot run the other way either. Everyone can
#: reach `stage`; `stage` can reach almost nobody. The writers ask.
#:
#: The first cut of the `evidence` field spelled all six here as literals
#: while their owners spelled them too, and item 94's sweep caught it in CI
#: one commit after that item shipped - the guard working on exactly the
#: class it was built for, against its own author.

#: P4's directory, and the two files inside it this contract names. The diff is
#: the central object of a review and nothing named it until now - which is why
#: P6's pre-pass takes a `--diff` a caller supplies by hand, and why the defect
#: judge had nowhere defined to look for the change it must check a claim
#: against. `40-impl/` is a directory, so callers match rather than compare.
IMPL_DIR = "40-impl/"
CHANGE_PATCH = "change.patch"
#: Owed by a DEFECT run and by no other kind - item 45. A fix with no
#: reproduction behind it is a claim that something was broken with nothing
#: behind the claim.
REPRO = "40-repro.md"
#: P8's CI verdict. Named here with the other artifacts for item 94's
#: reason - the writer asks for the name rather than spelling a second
#: copy of it, because this module is what the phase contract is made of.
CI_RESULT = "80-ci.json"
#: P9's record of what it actually ran. A deploy is the one act that changes
#: something OUTSIDE the repository, so what was deployed, when, and on
#: whose approval is written down rather than remembered.
DEPLOY_RESULT = "90-deploy.json"
#: P10's record of what it ASKED and what answered. P9 asks the
#: deployment tool; this is the running system's own reply, which is the
#: only thing that can contradict it.
VERIFY_RESULT = "A0-verify.json"
#: What P8 CUT: the version, the changelog, the notes and the tag, with
#: every step that was skipped because its command is blank named rather
#: than absent. Separate from the CI verdict because they answer different
#: questions - one is what somebody else's machine said, the other is what
#: this phase produced.
CUT_RESULT = "80-cut.json"
#: Documents a LATER phase reads, so item 132 spells each of them a second
#: time - and two spellings of one filename in one table is exactly the drift
#: item 94 exists to end. Measured before promoting them: every one was a bare
#: literal appearing EXACTLY ONCE, all of them in this file, so `stage.py` is
#: unambiguously the owner and item 94's guard now covers five more documents.
REQUIREMENTS = "10-requirements.md"
SCOPE = "20-scope.md"
DESIGN = "30-design.md"
REVIEW = "60-review.md"
DEPLOY_PLAN = "90-deploy.md"
SCOPE_ADD = "50-scope-add.md"
SUITE_RESULT = "50-suite.json"
SCA_RESULT, SCA_REPORT = "50-sca.json", "50-sca.md"
PREPASS_RESULT, PREPASS_REPORT = "prepass.json", "prepass.md"
LOOP_RECORD = "60-review-loop.json"
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
    #: Artifacts this phase WRITES and never owes. Not `conditional`,
    #: which means owed when a trigger fires and carries the obligation
    #: to have a judge - a test enforces that, and enforced it against
    #: the first cut of this field. These exist for ATTRIBUTION only:
    #: a gate must not be voided by a phase that runs after it, and to
    #: know that, something has to say whose artifact a document is.
    evidence: tuple[str, ...] = ()
    #: Artifacts this phase writes AFTER its own gate. Item 122, and it
    #: extends item 46's rule from REQUIRE to PIN: a gate cannot require the
    #: thing it authorises, and it cannot be a verdict about it either. G5
    #: approves a deploy PLAN; the record of what the deploy then did is
    #: produced by the act the approval permits, so pinning it makes the
    #: phase void its own gate by doing what it was approved to do.
    #:
    #: The PLAN stays pinned, and that boundary is the point: unpinning
    #: `90-deploy.md` would let the approved command change after the yes,
    #: which is the property item 32 exists for.
    after_gate: tuple[str, ...] = ()
    #: Run documents this phase READS and does not produce. **Item 132, found
    #: by walk 4.** P2's SOP told the runner to *"seed from a file the
    #: requirements name"* while P2's prompt named no document at all - so a
    #: paid runner located the harness, ran the freshness check, understood the
    #: seed form item 128 taught it, and then had to ASK A HUMAN which file to
    #: seed from and exited 0.
    #:
    #: Measured across all twelve: six phases NAME the document they read, in
    #: their `## Inputs` section and nowhere else, and rely on the runner
    #: finding it under `artifacts-out:`. **P2 alone described one without
    #: naming it**, so there was no chain to follow.
    #:
    #: Declaring them removes that chain for all seven, and lets the prompt say
    #: which of them this profile will NEVER produce - item 125's rule that an
    #: absent document is NAMED, never blank, reached from the other side. The
    #: shipped default `phases[]` omits scoping, so `20-scope.md` is never
    #: written and P3 was left to work that out of prose.
    reads: tuple[str, ...] = ()


#: Values every stage's prompt states. The runner is told where it is, what it
#: is doing, and where the answer goes — nothing here is discoverable from the
#: prompt's prose, which is exactly why it is not IN the prose.
COMMON = (
    Value("phase", "run", "the phase being executed, on the spec's numbering"),
    Value("run", "run", "the run id; every artifact belongs to it"),
    Value("target", "run", "the repository root, and the working directory"),
    Value("sop", "run", "the procedure to follow, in the harness"),
    Value("artifacts-out", "run", "the run folder every artifact is written to"),
    # **Item 134, and walk 4 paid for it.** Every SOP tells the runner to run
    # harness CLIs, and the root they live under was DERIVABLE from `sop:` and
    # never stated - so a runner improvised the invocation. On walk 4 goose ran
    # `state.py --record ...` exactly as the SOP printed it, got "is not
    # recognized as an internal or external command", and stopped; the artifact
    # it had just written was never pinned, and no gate can stand on unpinned
    # bytes.
    #
    # `sdlc-init.py`'s `builder_hint` already records why the prefix matters
    # and it is not tidiness: a bare `uv run` resolves the PASTER's project,
    # which is a degraded build at exit 0 or a stranger's repository quietly
    # gaining a `.venv`. Improvising this fails SILENTLY.
    Value("harness", "run",
          "where the harness CLIs live - every `tools/...` path in the SOP is "
          "under THIS root and never under the repository being worked on"),
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
        # Item 125. P0 maps what the task touches, which needs the task; it
        # used to read that out of `00-intake.md` and then overwrite it.
        Value("spec", "run",
              "the specification, verbatim - intake's document, not this "
              "phase's to rewrite"),
        # Item 126, and it is the SOP that was right. P0-intake.md has said
        # since item 36 that "`baseline` states what P0 actually recorded, read
        # from the run itself" and that step 3 should "read what was recorded
        # (the prompt states it)" - while the value was declared on P5 ALONE,
        # so P0's prompt could never state it. On walk 2 the runner looked, did
        # not find it, and followed the SOP's next paragraph exactly: close the
        # run and re-open it with --baseline. The run's baseline was fine.
        #
        # Declared here rather than struck from the SOP because intake MEASURES
        # the baseline at run creation and reporting it is P0's job - the same
        # direction item 121 took when it made this value `run`-sourced.
        Value("baseline", "run",
              "what intake measured on the clean tree - REPORT it; a suite run "
              "in this phase would describe a different tree"),
    )),
    # Item 125: P1's values were EMPTY, so the phase whose whole job is the
    # requirement was never told the requirement. Its SOP said "plus
    # 00-intake.md", a discovery chain through the one file P0 is instructed
    # to overwrite - so following both documents exactly lost the specification
    # and then asked for requirements about nothing.
    Stage("P1", "requirements", (REQUIREMENTS,), (), "", "", (
        Value("spec", "run",
              "what this run is FOR, verbatim; every acceptance criterion "
              "below is checked against it at P11"),
    ), reads=(adapter.INTAKE_MD,)),
    # **The defect walk 4 found.** Step 2 of this SOP says to seed the pack
    # from "a file the requirements name" - and nothing told the runner the
    # requirements were a file, or where. It is the only phase that referred to
    # a run document without naming it.
    Stage("P2", "scoping", (SCOPE,), (), "G1", "graph", (
        Value("component", "caller", "the alias the changed paths are relative to"),
        # Item 124: `run`, not `caller`. `pack.py` states that packs belong
        # in scratch and never in git, and the run folder is committed.
        Value("pack-out", "run",
              "where the impact pack is written - the gitignored scratch dir, "
              "because a pack is derived and the run folder is committed"),
    ), reads=(REQUIREMENTS,)),
    Stage("P3", "design", (DESIGN,), ("30-adr-*.md",), "G2", "", (),
          reads=(REQUIREMENTS, SCOPE)),
    Stage("P4", "implementation", (IMPL_DIR,), (REPRO,), "G3", "", (
        Value("component", "caller", "the component this agent owns; one agent, one"),
        Value("branch", "caller", "the branch the work lands on"),
        Value("worktree", "run",
              "the isolated checkout to work in; the run's own record says "
              "where, so nobody has to be told twice"),
    ), reads=(DESIGN,)),
    Stage("P5", "test", ("50-tests.md",), (SCOPE_ADD,), "", "", (
        Value("test-command", "profile:test_command",
              "the WORKSPACE's suite; blank where components declare their own"),
        Value("coverage-command", "profile:coverage_command",
              "blank means coverage is unmeasurable, which is a finding, not a pass"),
        Value("coverage-threshold", "profile:coverage_threshold",
              "the minimum on CHANGED files, where the workspace sets one"),
        # Item 121: `run`, not `caller`. Only `intake.open_run` writes the
        # baseline, so asking a caller for it asked for something nobody
        # downstream of P0 possesses.
        Value("baseline", "run",
              "what P0 recorded this run is measured against; no NEW failures"),
    ), evidence=(SCA_RESULT, SCA_REPORT, SUITE_RESULT)),
    Stage("P6", "review", (REVIEW,), (), "", "", (
        Value("diff", "caller", "the unified diff under review"),
        Value("component", "caller", "the alias the diff's paths are relative to"),
        Value("prepass-out", "caller", "where the deterministic pre-pass writes"),
        Value("pack-out", "run",
              "where the context pack writes - the gitignored scratch dir, "
              "because a pack is derived and the run folder is committed"),
        Value("hitl-target", "caller", "the repository the review session is recorded in"),
        # The value whose absence made this phase review the BASE commit. The
        # pre-pass and the pack must read the bytes UNDER review, and since the
        # parallel-run model those are never in the repository root.
        Value("worktree", "run",
              "the tree the change under review lives in - what the pre-pass "
              "and the pack must be pointed at, never the repository root"),
        Value("rubric", "run", "the scoring instrument; reported, never the gate"),
        Value("block-on", "profile:block_on", "THE exit condition, categorical"),
        Value("max-iterations", "profile:max_iterations", "the hard cap on the loop"),
    ), evidence=(PREPASS_RESULT, PREPASS_REPORT, LOOP_RECORD)),
    Stage("P7", "triage", ("70-triage.md",), (), "G4", "", (),
          reads=(REVIEW,)),
    Stage("P8", "release", ("80-release.md",), (CI_RESULT, CUT_RESULT),
          "", "", (
        Value("branch", "caller", "the branch to publish; the merge click stays human"),
    )),
    Stage("P9", "deploy", (DEPLOY_PLAN,), (DEPLOY_RESULT,), "G5", "deploy", (
        Value("deploy-command", "profile:deploy_command", "what a yes here will run"),
    ), after_gate=(DEPLOY_RESULT,)),
    Stage("P10", "verify", ("A0-verify.md",), (VERIFY_RESULT,), "",
          "probes", (), reads=(DEPLOY_PLAN,)),
    Stage("P11", "closeout", ("B0-closure.md",), (), "", "", (),
          reads=(REQUIREMENTS,)),
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
    "P4": ("tools.sdlc.defect", "--check",
           "It is owed exactly when this run was opened as a DEFECT, and it is "
           "checked rather than counted: name each regression test in backticks, "
           "spelled as the change spells it, and the harness verifies that name "
           "is really in the diff. It does not guess what a test is called in "
           "your language - it checks that what you called it exists."),
    "P10": ("tools.sdlc.verify", "--probe",
            "It is owed once the run is AT P10 and probes are declared. "
            "A deploy nobody probed is one whose success is the "
            "deployment tool's opinion of itself - and the first real "
            "deploy this project ran recorded `deployed: true` for a "
            "change that never shipped, with the health check answered by "
            "the process it was supposed to replace. A failing probe "
            "PROPOSES a rollback and files a defect; it never rolls "
            "back, because that is as much a change to a live system as "
            "rolling forward."),
    "P9": ("tools.sdlc.deploy", "--deploy",
           "It is owed once the run is AT P9 and a deploy_command is declared. "
           "A deploy nobody recorded is one P10 cannot verify and nobody can "
           "roll back, and a command that did not run to completion is neither "
           "a deploy that failed nor one that worked. G5 gates it: a deploy "
           "costs something, so the approval is about THIS build, to THIS "
           "place, and is taken afresh every time."),
    "P8": ("tools.sdlc.release", "--checks",
           "It is owed once the run is AT P8: a branch nobody watched is not a "
           "branch whose checks passed. Four of the five answers are not green "
           "- red, pending, no checks at all, and a check state this phase has "
           "never seen - and NONE of them leaves the phase. The merge click is "
           "a human's in every case."),
    "P5": ("tools.sdlc.suite", "--execute",
           "It is owed exactly when the engine finds breakage in a component the "
           "approved design does not cover - which is a scope-add for G1, not a "
           "fix to make quietly here."),
}

NAMES = tuple(s.name for s in STAGES)
PHASES = tuple(s.phase for s in STAGES)


def artifacts_through(gate: str) -> tuple[str, ...]:
    """The artifact patterns a GATE is a verdict about: everything the phases up
    to and including its own owe.

    A gate stands at the end of a phase and approves the work up to it. It has
    no authority over a test report written afterwards, and until this existed
    it pinned one anyway - `approve.record` copied the run's WHOLE artifact map,
    so a G3 taken after a P6-to-P4 loop pinned P5's and P6's documents, and
    re-running those phases (the mandated next step) voided the gate that had
    just authorised the fix. Measured on the first run to loop: eight integrity
    problems, six of them phases moving forward exactly as designed.

    Patterns, not names: `30-adr-*.md` is conditional and glob-shaped, and
    `40-impl/` is a directory. Callers match rather than compare.
    """
    out: list[str] = []
    for stage in STAGES:
        out += (list(stage.produces) + list(stage.conditional)
                + list(stage.evidence))
        if stage.gate == gate:
            return tuple(out)
    raise StageError(f"no phase carries gate {gate!r} - one of "
                     f"{', '.join(s.gate for s in STAGES if s.gate)}")


def artifacts_after(gate: str) -> tuple[str, ...]:
    """The artifact patterns owed by phases AFTER this gate's own, plus what
    the gate's own phase writes after it.

    That last part is item 122. P9 writes the deploy PLAN, stops for G5, then
    deploys and records the outcome - so the phase's own artifact map spans the
    gate, and pinning all of it meant following the SOP exactly voided the
    approval that permitted the act. Measured: `governs("G5", "90-deploy.json")`
    answered True, and the outcome escaped only by not existing yet at approval
    time - which stops being true the moment a gate is RE-TAKEN, as one was on
    the first whole-lifecycle walk.
    """
    seen, out = False, []
    for stage in STAGES:
        if seen:
            out += (list(stage.produces) + list(stage.conditional)
                    + list(stage.evidence))
        if stage.gate == gate:
            # The gate's OWN phase contributes only what it writes after the
            # gate - not its whole output, which would unpin the very document
            # the approval is about.
            out += list(stage.after_gate)
            seen = True
    if not seen:
        raise StageError(f"no phase carries gate {gate!r}")
    return tuple(out)


def governs(gate: str, artifact: str) -> bool:
    """Is this recorded artifact one the gate is a verdict about?

    Stated as an EXCLUSION, and the direction matters. An include-list would
    quietly stop pinning anything the contract does not name — an adopter
    recording an extra piece of evidence would find no gate covering it, which
    gives away the property item 32 exists for: an approval is of specific
    bytes. So everything recorded is governed EXCEPT what a later phase owes.
    An unrecognised name is pinned, because the safe answer to "whose artifact
    is this" is "this gate's".

    `40-impl` and `40-impl/` are the same directory under two spellings — the
    record has carried both since a `--record` accepted the name without its
    slash — so the trailing separator is stripped on both sides rather than
    becoming a third rule somewhere else.
    """
    name = artifact.rstrip("/")
    return not any(fnmatch(name, pattern.rstrip("/"))
                   for pattern in artifacts_after(gate))


class StageError(RuntimeError):
    """A stage contract that cannot be satisfied, or cannot be judged."""


class StageRefusal(StageError):
    """A value somebody GAVE that this contract cannot take.

    **Item 127: `StageError` had grown five meanings and `drive.py` reported
    one.** Every failure out of `resolve` was mapped to the driver's
    `needs-value` stop, which tells an operator to SUPPLY something - and four
    of the five are the opposite of a missing value: a key the contract does
    not name, a run-sourced value supplied anyway (item 109), a value carrying
    a line break (items 41 and 131), and a component name the run has never
    heard of (item 131). Telling somebody to supply what they already supplied
    is could-not-look reported as nothing, one layer along.

    **A SUBCLASS rather than a new type**, for the reason `ProfileError` is one
    of `PolicyError`: every existing `except StageError` still catches it, so
    this reaches every handler without one of them changing, and only the
    caller that wants the distinction has to ask for it.
    """


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
        # A COMPONENT'S deploy counts. An estate declares its deploys per
        # component - that is what `sdlc-init.py` writes - so reading only
        # the top-level scalar reported the phase as one the workspace
        # CANNOT run, for exactly the workspaces that deploy per component.
        if (guardrails.profile_scalar(target, "deploy_command") or "").strip():
            return True
        try:
            every = guardrails.components(target)
        except (guardrails.PolicyError, OSError, ValueError):
            return False
        return any(str(c.get("deploy_command", "") or "").strip() for c in every)
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


#: A number a document states must agree with the number the harness measured.
#: Named here because `verify` is where a phase's evidence is judged, and a
#: claim contradicting the run's own recorded baseline is evidence contradicting
#: itself.
#: How a forced advance marks itself in the run's history. An override is the
#: one event a metrics reader must never miss, and it lives in a NOTE rather
#: than a field — so the wording is a constant here instead of a string two
#: modules spell independently. `metrics.py` reads this one.
FORCED_NOTE = "forced past "

CLAIM_CHECK = "tools.sdlc.claims"


def record_command(rel: str) -> str:
    """`tools/sdlc/state.py --target <repo> --record <id> --artifact <rel>`.

    **Item 147, and item 134 is why this is a function rather than an edit.**
    That item gave all twelve SOPs and the rendered prompt the `tools/sdlc/`
    prefix, on the finding that a bare script name sends a runner improvising
    - and it moved the DOCUMENTS, leaving the runtime messages, which are what
    a person reads at the moment the check fires. This module's own comment
    above `harness` records goose running `state.py --record ...` exactly as
    printed, getting *is not recognized as an internal or external command*,
    and stopping with the artifact it had just written left unpinned.

    **The old spelling was not merely unprefixed, it was unrunnable**, proven
    by running it: `--record` takes the RUN ID as its value and `--target` is
    required, so argparse answers *argument --record: expected one argument*.

    PLACEHOLDER form rather than the live target and run id, deliberately.
    Thirteen SOP instructions teach this exact string, and one grammar a
    runner meets in every surface is worth more than three values it would
    not have to substitute; it also keeps a machine path out of harness
    output, which item 120 removed from a committed document on purpose.
    """
    return f"tools/sdlc/state.py --target <repo> --record <id> --artifact {rel}"


def stale(target: Path, run_id: str, phase: str) -> list[str]:
    """This phase's artifacts that have MOVED since they were recorded.

    **Deliberately NOT part of `verify`, and the reason is a mechanism this
    harness documents as correct.** `approve.record` calls `verify`
    (`approve.py:434`), and LIFECYCLE.md says in as many words that *improving
    an artifact before approving it is the ordinary case, not a violation* -
    item 34 built the re-pin that makes it work. A hash-aware `verify` would
    therefore refuse exactly the human behaviour the harness asks for.

    So the question is asked HERE, by the caller that needs it. Item 121 made
    the same call for `release.unpublished_run`: it could not reuse
    `validate.merge_ready` because the two ask about different things, and
    reusing it would have answered the wrong question confidently.

    **What this is FOR** (item 127, walk 3's W9): a runner that writes a
    document and does not pin it, and a runner that writes it, pins it, and
    then writes it AGAIN, are one failure from the runner's side - and they got
    opposite treatment. `verify` halts the driver on the first and says nothing
    about the second, so the walk advanced over bytes that had moved. Nothing
    unsafe reaches a gate either way, because `approve` re-hashes; the cost is
    WHERE it is caught, which was at the gate, after later phases had built on
    it.

    Scoped to the phase's OWN artifacts, because an earlier phase's document
    that a human has legitimately edited is not this phase's business - the
    gate it belongs to will re-pin it.

    **Item T251, and it is two failures with one cause.** The question asked
    here is about what was RECORDED, and drift is a property of a recording -
    so the declaration class an artifact was named under, and the shape of its
    name, are both beside the point. The set was `produces | evidence`, so ALL
    SEVEN `conditional` entries in the table, across six phases, were absent
    from it whatever their shape - `30-adr-*.md`, `40-repro.md`,
    `50-scope-add.md`, `80-ci.json`, `80-cut.json`, `90-deploy.json`,
    `A0-verify.json`. Exactly ONE of the seven is a glob, and the test was
    `rel in owed`, a literal - so the glob is a second reason the same entry
    failed, and the reason P3 is where this was noticed. It is not the width of
    what was broken: the other six are plain literals the set alone dropped. An
    ADR written, pinned and then rewritten - verbatim the failure the paragraph
    above says this exists to catch - was reported by nothing. Widening it
    demands NOTHING new, because an artifact nobody recorded never appears in
    this walk.

    And the kinds are asked of `state.drift` rather than read back out of the
    sentence `state.verify_artifacts` writes. That sentence is `detail or
    said[kind]`, and kind `unhashable` ALWAYS carries a detail - so the prose
    this function used to grep for could never match it, and a recorded
    directory emptied after its pin drifted past in silence. `feedback.
    pending_edits` was already consuming the structured tuples for exactly this
    reason; this is the sibling that was left standing.
    """
    stage = by_phase(phase)
    folder = ledger.run_dir(Path(target), run_id)
    if not folder.is_dir():
        raise StageError(f"no run folder for {run_id} at {folder}")
    owed = (list(stage.produces) + list(stage.conditional)
            + list(getattr(stage, "evidence", ()) or ()))
    # The same normalisation `governs` uses, and for the same two reasons: the
    # patterns are globs, and `40-impl` and `40-impl/` are one directory under
    # two spellings the record has carried both of.
    mine = [p.rstrip("/") for p in owed]
    #: What each drift kind costs the reader, in this function's vocabulary.
    #: `state.py` owns the kinds; the sentences a phase is told them in are
    #: this module's, so neither has to match the other's prose.
    cost = {"changed": "its bytes have moved since",
            "missing": "it is no longer in the run folder",
            "unhashable": "its bytes can no longer be read"}
    recorded = ledger.load(Path(target), run_id).get("artifacts", {})
    moved = []
    for rel, kind, detail in ledger.drift(Path(target), run_id, recorded):
        name = rel.rstrip("/")
        if not any(fnmatch(name, pattern) for pattern in mine):
            continue
        why = cost.get(kind, kind)
        moved.append(f"{phase} ({stage.name}) recorded {rel} and {why}"
                     f"{' (' + detail + ')' if detail else ''} - record it "
                     f"again ({record_command(rel)}), or the next "
                     f"gate will be asked about bytes nobody approved")
    return moved


def run_wide(target: Path, run_id: str) -> list[str]:
    """The problems that belong to the RUN rather than to any one phase.

    **Item 99's claims checker is run-wide, and it was asked per phase.** Its
    own signature says so — `problems(target, run_id)` is told no phase — and
    `verify` called it inside the body `owed` runs once per active phase. So
    `--verify --upto P5` printed ONE contradicted claim FIVE times and tallied
    `10 owed` where six things are owed. Item 140 is the recorded cost of this
    checker reporting a number the document does not carry: it blocked a gate.
    A tally that multiplies is the same failure from the other side — it is
    the number a human reads before approving, and they cannot act on it.

    ONE home, two callers: `verify` asks it for a single phase, because a
    wrong count is a contradiction wherever it is noticed and the documents
    carrying them accumulate as the run goes (`test_claims.py` pins that at
    P1, P2 and P5); `owed` asks it ONCE after its loop rather than through it.
    Measured on the run that produced item 99 — one hand correction of a KNOWN
    wrong number left four more instances standing in the same run's
    artifacts, in files the corrector had already opened.
    """
    # Named as a STRING and loaded with importlib, the same seam
    # CONDITIONAL_JUDGE uses, for the reason `halted_at` states: the judging
    # modules read THIS module for the artifact contract, so a module-level
    # import would be a cycle. `validate.sibling_imports` sees a string-named
    # sibling, so the bundle guard demands its entry rather than trusting one.
    import importlib

    return list(importlib.import_module(CLAIM_CHECK).problems(target, run_id))


def verify(target: Path, run_id: str, phase: str, *,
           include_run_wide: bool = True,
           include_conditional: bool = True) -> list[str]:
    """Everything the stage owes and has not delivered, as sentences.

    Three distinct failures, kept distinct on purpose: the artifact is absent,
    the artifact is empty, or the artifact exists and is not RECORDED. The
    third is the one worth having — item 32 pins an approved artifact's hash so
    the gate goes VOID when the bytes move, and until something requires a
    phase's output to be in that set, the pin covers nothing.

    `include_run_wide=False` leaves out what `run_wide` answers, and the only
    caller that passes it is `owed`, which asks once for the whole run instead
    of once per phase. Every other caller judges a single phase, where asking
    once and asking per phase are the same question.

    `include_conditional=False` leaves out the CONDITIONAL_JUDGE seam, and the
    only caller that passes it is `owed` when it has already found the run's
    phase unreadable (T356): the judges that read that phase would each
    rediscover the same fact and append their own "could not tell", so `owed`
    states it once and stops asking them.
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
                            f"it ({record_command(rel)}), so its bytes "
                            f"are not pinned and a yes about them covers nothing")
    # Read at EVERY phase rather than owed by one, because a wrong count is a
    # contradiction wherever it is noticed and the documents carrying them
    # accumulate as the run goes: P1's criterion, P2's scope table, P5's
    # evidence. Asked THROUGH `run_wide`, which is where the reason it is not
    # a per-phase question now lives.
    if include_run_wide:
        problems += run_wide(target, run_id)
    if include_conditional and stage.conditional:
        # Item 64, generalised by item 38. `conditional` was data nothing read:
        # the artifact was asked for in a prompt and required by nobody, so
        # whether one was owed was the runner's own opinion of its own work.
        # Each judging module decides from evidence instead. Imported HERE for
        # the reason `halted_at` states: they read this module for the artifact
        # contract, so a module-level import would be a cycle.
        #
        # `obligations(target, run_id)` is shaped like a run-wide question and
        # is NOT one: CONDITIONAL_JUDGE picks the module by phase, and no two
        # phases in it name the same module, so `owed`'s loop cannot ask one
        # twice. Checked rather than assumed — a second judging module shared
        # between phases would have the defect `run_wide` was split out for.
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
    """What every active stage up to and including `upto` still owes.

    The per-phase question is asked per phase and the run-wide one ONCE. Until
    this function asked for `include_run_wide=False`, `--verify --upto P5`
    printed one contradicted claim five times and said `10 owed` for six owed
    things — see `run_wide`.
    """
    running = [s.phase for s in active(target)]
    if upto not in PHASES:
        raise StageError(f"unknown phase {upto!r} - one of {', '.join(PHASES)}")
    limit = PHASES.index(upto)
    # T356: an unreadable phase label is ONE fact about the run, not one per
    # judge. `owed` invokes every active conditional phase's judge, and the
    # three that read the run's phase (release, deploy, verify) each caught the
    # same `Unreadable` and appended their own "could not tell" - one bad label
    # became three lines, T252's shape reintroduced by T336's fix on a path
    # `run_wide` does not cover. Read it ONCE here; when it cannot be read no
    # conditional phase can say what it owes, so the judges are left out and the
    # fact is stated a single time. The per-phase artifact checks, which do not
    # read the stored label, still run.
    judge_the_conditional = True
    unreadable: list[str] = []
    try:
        ledger.phase_of(target, run_id)
    except ledger.Unreadable as exc:
        judge_the_conditional = False
        unreadable = [f"the run's phase could not be read, so no conditional "
                      f"phase can say what it owes: {exc.why}"]
    problems: list[str] = []
    for phase in running:
        if PHASES.index(phase) <= limit:
            problems.extend(verify(target, run_id, phase,
                                   include_run_wide=False,
                                   include_conditional=judge_the_conditional))
    return problems + unreadable + run_wide(target, run_id)


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
    # Item 112: one spelling. The note keeps saying WHAT was overridden; this
    # says THAT one was, as a field, so `metrics.py` reads the same shape here
    # as it does for a forced approval instead of matching a sentence.
    overridden = False
    # T254: the phase being ENTERED is refused BY NAME here, before the
    # `PHASES.index(phase)` two lines down answered an unknown one with a raw
    # `tuple.index(x): x not in tuple` while `--verify` gave a sentence.
    if phase not in PHASES:
        raise StageError(f"unknown phase {phase!r} - one of {', '.join(PHASES)}")
    # T353: the phase being LEFT is READ, not defaulted. `state.get("phase",
    # PHASES[0])` turned a run whose phase could not be read into one silently
    # at P0 - `advance` then skipped the verification for the phase it invented
    # and could move the run on. `phase_of` refuses by name (missing, null or
    # not-a-phase) instead of a silent P0 or a raw ValueError at `.index`.
    leaving = ledger.phase_of(target, run_id)
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
                f"{FORCED_NOTE}{leaving}: {'; '.join(problems)}"
            overridden = True
    # Item 105: entering a phase IS its resolve moment for the runner routing,
    # so an invalid route is refused here BY NAME - never discovered mid-phase
    # - and a valid one is RECORDED, because enterprise routing without
    # attribution is a rumor: the run folder alone answers "which agent did P4
    # on run X". Unmapped stays silent - whoever is driving drives.
    #
    # **FORWARD only, for the same reason the gate check is.** The first cut
    # guarded this on `leaving != phase` alone, so a routing problem refused
    # BACKWARD moves too - and P5 loops to P4 and P7 loops to P3 are the loop
    # this lifecycle is built around, as the docstring above says. Measured: a
    # run at P5 with its artifacts satisfied could not return to P4 once the
    # constitution's `allowed_runners` was tightened, and the only way back was
    # `--force`, which would have stamped an override onto a legitimate loop
    # and polluted the very record `metrics.py` reads to answer "which runs
    # were forced". A run going BACK is not entering a phase to have work done
    # under a runner; it is returning to work it already owns.
    if leaving != phase and forward:
        try:
            routed = guardrails.runner_for(target, by_phase(phase).name)
        except guardrails.RunnerError as exc:
            if not force:
                raise StageError(
                    f"entering {phase} is refused by the runner routing: {exc} "
                    f"(--force records the override)")
            note = (note + " | " if note else "") + \
                f"{FORCED_NOTE}routing into {phase}: {exc}"
            overridden = True
        else:
            if routed is not None:
                note = (note + " | " if note else "") + f"runner: {routed['name']}"
    return ledger.advance(target, run_id, phase=phase, note=note,
                          forced=overridden)


# ── what a stage receives ────────────────────────────────────────────────────


def _worktree_for(target: Path, run_id: str, component: str) -> str:
    """Where this run's change actually lives, from the run's OWN record.

    The harness creates the tree and writes down where it put it, and then this
    was a value it ASKED THE CALLER for - so P4 could be told the wrong path,
    and P6 was not told at all. Its SOP named `<repo>`, and since the
    parallel-run model a run's change never lives in `<repo>`: the review
    pre-pass copied the touched files out of the BASE checkout and reviewed the
    code as it was before the change. It reported a defect the change had just
    fixed, and a defect a change INTRODUCES would have been invisible to it.

    The main checkout is the honest answer in two cases, and both are the
    record's to state rather than this function's to assume: a component whose
    `vcs.deny_raw_git` denies it a tree, and a run that has not reached P4 yet.

    Imported lazily. `worktree` reaches `tools.cli`, and `stage` is embedded in
    the dependency-free `.sdlc/ledger_check.py` whose loader carries
    `tools.sdlc.*` and nothing else - a module-level import here would follow
    the copy into an adopter's CI and raise there. Declared in
    `validate.LAZY_OUTSIDE_THE_BUNDLE` with that reason, because nothing the
    ledger check can reach ever renders a prompt.
    """
    from tools.sdlc import worktree as trees

    try:
        record = trees.load_record(Path(target), run_id)
    except (trees.WorktreeError, OSError):
        return str(target)
    homes = record.get("components") or {}
    entry = homes.get(component) if component else None
    if entry is None and len(homes) == 1:
        entry = next(iter(homes.values()))
    if entry is None and component and homes:
        # **Item 131.** The two legitimate root answers - `deny_raw_git`, and a
        # phase before P4 - are both STATED in the record, and this is a third
        # case indistinguishable from them: a component name the run has never
        # heard of. `resolve` refuses an unknown --set KEY and never checked the
        # component VALUE, so one typo resolved `worktree` to the main checkout
        # and at P6 that is the recorded catastrophic case - the pre-pass and
        # the pack read the BASE tree, report a defect the change just fixed,
        # and cannot see one the change introduced.
        raise StageRefusal(
            f"this run has no worktree recorded for a component called "
            f"{component!r} - it knows {', '.join(sorted(homes)) or 'none'}. "
            f"A name the run does not know would resolve to the repository "
            f"root, which at P6 means reviewing the tree WITHOUT the change")
    if not isinstance(entry, dict):
        return str(target)
    # The recorded home, whatever the MODE. `worktree.create` writes
    # `home = s.get("home", s["where"])`, so a component denied a tree by
    # `vcs.deny_raw_git` still records one - its own directory in the main
    # checkout - and that is exactly where its change lives. Keying on
    # `mode == "worktree"` and falling back to the repository root looked
    # safer and was less correct: for a component of an estate it would have
    # named the workspace root rather than the component. The mutation pass
    # found that, against the first cut of this line.
    return str(entry.get("home") or "").strip() or str(target)


def _spec_for(target: Path, run_id: str) -> str:
    """Where this run's specification is — item 125.

    A PATH rather than the text, because a prompt value runs to the end of its
    line and a specification does not; `sop:` is handed over the same way and
    for the same reason.

    **The document is intake's and no phase declares it**, which is what makes
    a path safe to hand over here. It used to live inside `00-intake.md` — also
    P0's one declared artifact — so pointing a phase at it was pointing it at a
    file the next runner was told to overwrite.

    An absent one is NAMED rather than resolved to a blank. A blank reads as a
    run with nothing to build, which is never true: every run has a
    specification, so its absence is damage and the sentence says whose.
    """
    path = ledger.run_dir(Path(target), run_id) / adapter.SPEC_MD
    if path.is_file():
        return str(path)
    return (f"{adapter.SPEC_MD} is not in the run folder, so what this run is "
            f"FOR cannot be read. Intake writes it at run creation and no phase "
            f"may rewrite it - if a phase overwrote it, restore it from the "
            f"commit that opened the run rather than reconstructing it")


def _pack_out_for(target: Path, run_id: str) -> str:
    """Where this run's context pack is written — the ONE gitignored directory.

    **A pack is a derived artifact and `pack.py` says so in its own `--out`
    help**: *packs are derived artifacts and belong in scratch, never in git*.
    The harness ships the directory for exactly that — `outdir.SCRATCH_DIR`,
    which `sdlc-init.py` writes into every adopter's `.gitignore` — and then
    asked the CALLER which directory to use, so both SOPs told a runner to pass
    one by hand and item 114's walk put it in the run folder.

    That is not untidy, it is the rule broken: since item 121 the run folder is
    COMMITTED with the code it describes, so a pack written there ships to
    everyone who clones. Item 109's fix for `worktree`, third time.

    **`prepass-out` deliberately stays a caller value.** `prepass.json` and
    `prepass.md` are P6's declared EVIDENCE in this contract; a pack is not.
    One belongs in the record and one belongs in scratch, and the difference is
    written down here rather than inferred from where somebody put them.

    Imported BY STRING, which is item 105's pattern rather than a flourish:
    `outdir` is `tools.graph`, and this module is embedded in the
    dependency-free `.sdlc/ledger_check.py` whose loader carries `tools.sdlc.*`
    and nothing else - a plain import is refused by the bundle guard, which
    refused this one. Declared in the test's `LAZY_STRING_IMPORTS` with the
    reason, and the ImportError becomes a named refusal rather than a traceback:
    nothing the bundle can reach renders a prompt, so it is never taken there.
    """
    from importlib import import_module

    try:
        _outdir = import_module("tools.graph.outdir")
        scratch, own = _outdir.SCRATCH_DIR, _outdir.OWN_ARTIFACT_DIR
    except ImportError as exc:
        raise StageError(
            f"pack-out resolves to the harness's scratch directory and "
            f"tools.graph.outdir, which names it, could not be imported "
            f"({exc}) - a pack belongs in scratch and never in git, so this "
            f"is refused rather than guessed at") from None
    # **Item 131: INSIDE `.sdlc/`.** The docstring above says
    # `sdlc-init.py` writes `outdir.SCRATCH_DIR` into every adopter's
    # `.gitignore`, and that is off by one directory: the installer writes
    # `<repo>/.sdlc/.gitignore`, whose rules reach `.sdlc/` and nothing above
    # it. Verified with git itself - `git check-ignore .tmp/<run>/pack/pack.md`
    # exits 1 (NOT ignored) while `.sdlc/.tmp/...` exits 0 - so the pack
    # dirtied the adopter's tree and `intake.dirty_paths`, which excludes only
    # `.sdlc/runs` and `.sdlc/metrics`, refused the NEXT run over a file the
    # harness itself had written. The same failure the `__pycache__` entry was
    # added to stop.
    return str(Path(target) / own / scratch / run_id / "pack")


def _estate_baseline(comps: dict) -> str:
    """Every component's baseline, on ONE line, because a value is one line.

    **Item 131.** `intake.open_run` writes the block as
    `{**baseline(...), "build": ..., **component_baselines(...)}`, and item 38's
    own docstring says a multi-component workspace has NO top-level
    `test_command` - so the merged block ALWAYS carries `ran: False` while the
    real numbers sit under `components`. `_baseline_for` read the top level and
    answered *NOT measured*, and P0's SOP then tells the operator to close the
    run and re-open it with `--baseline`.

    That is verbatim the walk-2 failure item 126 exists to end, recreated for
    every estate: item 126 fixed the single-repo case, which is the one the walk
    happened to use. Item 38 recorded this shape once already - *"an estate
    recorded 'no test_command' while a dozen declared suites sat one indent
    level down"* - and it re-entered through a new reader.

    A component that did NOT run is named as such rather than omitted: a rollup
    that silently counted only the successes would be the measured-zero fold
    this project keeps refusing.
    """
    said, measured = [], 0
    for name in sorted(comps):
        row = comps[name] if isinstance(comps.get(name), dict) else {}
        if not row.get("ran"):
            said.append(f"{name} NOT measured")
            continue
        measured += 1
        bit = f"{name} exit {row.get('exit_code')}"
        if row.get("tests") is not None:
            bit += f", {row['tests']} test(s)"
        said.append(bit)
    return (f"{measured} of {len(comps)} component(s) measured: "
            + "; ".join(said))


def _baseline_for(target: Path, run_id: str) -> str:
    """What P0 actually recorded, from the run's OWN intake document.

    **This was a value the prompt ASKED THE CALLER for, and no caller can
    answer it.** The baseline block is written by `intake.open_run` and by
    nothing else, so it exists before any runner reads a prompt - and P0's SOP
    told the runner to run the suite and record the numbers, which is an
    instruction addressed to somebody who can no longer act on it. `worktree`
    had the same two-channel split and item 109 closed it the same way.

    **Recording it later is not the fix, and that is why this is a reader.**
    `intake`'s own contract says P5 compares against a baseline measured on a
    clean tree at intake; a baseline taken after P0 describes a different tree,
    so the missing mechanism is missing on purpose and the instruction was the
    part that was wrong.

    A baseline that was NOT run says so here, in the words P5 will use, rather
    than resolving to a blank that reads as zero failures.
    """
    path = ledger.run_dir(Path(target), run_id) / adapter.INTAKE_JSON
    if not path.is_file():
        return (f"{adapter.INTAKE_JSON} is not there, so P0 never recorded one - "
                f"nothing downstream can be judged against it")
    try:
        row = (json.loads(path.read_text(encoding="utf-8")).get("baseline")
               or {})
    except (OSError, ValueError, AttributeError) as exc:
        return (f"{adapter.INTAKE_JSON} could not be read ({exc}), so what this "
                f"run is measured against is unknown - repair the document")
    if not isinstance(row, dict):
        return f"{adapter.INTAKE_JSON} records no baseline block"
    # Item 131: an ESTATE's baselines live under `components`, and the merged
    # top level always says `ran: False` because a multi-component workspace
    # declares no top-level test command. Asked before the top-level verdict,
    # because on such a workspace that verdict describes nothing that ran.
    comps = row.get("components")
    if (isinstance(comps, dict) and comps
            and any(isinstance(c, dict) and c.get("ran")
                    for c in comps.values())):
        return _estate_baseline(comps)
    note = str(row.get("note") or "").strip()
    if not row.get("ran"):
        # **`intake`'s own note is not repeated here, and that is the item.**
        # It reads "pass --baseline to execute it", which is sound advice at
        # run creation and useless by the time a phase prompt is rendered - the
        # exact shape of the defect, propagated one layer on. What a phase can
        # act on is stated instead, and whose act the remedy is.
        return (f"NOT measured{f' ({note})' if note else ''}. There is nothing "
                f"to compare a suite against, so a red result cannot be told "
                f"from a pre-existing one. Re-opening the run with --baseline "
                f"is the OPERATOR's act at intake, not this phase's - report "
                f"the absence rather than inventing a number")
    parts = [f"exit {row.get('exit_code')}"]
    if row.get("tests") is not None:
        parts.append(f"{row['tests']} test(s)")
    return f"{', '.join(parts)}" + (f" - {note}" if note else "")


def resolve(target: Path, stage: Stage, run_id: str, supplied: dict[str, str],
            harness: Path | None = None) -> dict[str, str]:
    """Fill every value the stage's prompt states, or refuse naming the gap.

    A value the caller must supply and did not is a refusal rather than a blank
    line. The alternative was tried by accident and is recorded in
    `RUNNER_CONTRACT.md` §9: a prompt that omits a value a CLI demands produces
    a runner that cannot finish while behaving perfectly.

    **Four answers, and every one of them says so out loud (item 145).** A
    value is stated by the run, or by `profile.yml`, or by the caller, or by
    nobody - and supplying one of the first two is REFUSED, because a supplied
    value nothing reads is the same defect as a value nobody supplies.
    """
    root = harness if harness is not None else HARNESS
    folder = ledger.run_dir(target, run_id)
    out: dict[str, str] = {}
    missing: list[str] = []
    for value in COMMON + stage.values:
        if value.source == "run":
            # A value the harness already knows is never asked of a caller, and
            # a caller who supplies one anyway is REFUSED rather than ignored:
            # a supplied value that nothing reads is the same defect as a value
            # nobody supplies, arriving from the other side.
            if value.key in supplied:
                raise StageRefusal(
                    f"{stage.phase} resolves {value.key} from the run itself, so "
                    f"supplying it can only disagree with the run - remove the "
                    f"--set and the harness will state it")
            if value.key == "worktree":
                out[value.key] = _worktree_for(target, run_id,
                                               supplied.get("component", ""))
                continue
            if value.key == "baseline":
                out[value.key] = _baseline_for(target, run_id)
                continue
            if value.key == "pack-out":
                out[value.key] = _pack_out_for(target, run_id)
                continue
            if value.key == "spec":
                out[value.key] = _spec_for(target, run_id)
                continue
            out[value.key] = {
                "phase": stage.phase,
                "run": run_id,
                "target": str(target),
                "sop": str(sop_path(stage, root)),
                "harness": str(root),
                "artifacts-out": str(folder),
                "rubric": str(target / ".sdlc" / "review-rubric.md"),
            }[value.key]
        elif value.source.startswith("profile:"):
            key = value.source.partition(":")[2]
            # **Item 145.** A value the phase NAMES and reads from
            # `profile.yml` was silently ignored when a caller supplied one:
            # the `extra` check below cannot see it, because the contract does
            # name the key, and this branch never looked at `supplied`.
            # **Measured by SPAWNING the CLI**: `--set block-on=nothing-at-all`
            # exited 0 and rendered the profile's list with no word about the
            # value it discarded, and at P9 a `--set deploy-command=` meant as
            # a dry run was swallowed at the one phase that spends. Item 109's
            # rule for the third time - a supplied value nothing reads is the
            # same defect as a value nobody supplies, arriving from the other
            # side.
            #
            # **REFUSED rather than honoured as an override, and that is a
            # ruling.** `policy.yml` is tighten-only (item 35), so a
            # command-line override of a profile dial would be a loosening no
            # gate records and no audit can show - `block-on` is exactly such a
            # dial. The profile is the document this run is governed by, and
            # changing it is an edit somebody can see. **What would overturn
            # it**: a declared, recorded override channel, which is a different
            # product decision rather than a fix to this branch.
            if value.key in supplied:
                raise StageRefusal(
                    f"{stage.phase} reads {value.key} from profile.yml, as "
                    f"{key}, so supplying it can only disagree with the "
                    f"document this run is governed by - remove the --set and "
                    f"change the profile, which is an edit somebody can see")
            found = guardrails.profile_scalar(target, key)
            if found is None:
                listed = guardrails.profile_list(target, key)
                found = ", ".join(str(v) for v in listed) if listed is not None else None
            out[value.key] = "" if found is None else str(found)
        elif value.key in supplied:
            out[value.key] = supplied[value.key]
        else:
            missing.append(f"{value.key} ({value.what})")
    if extra := sorted(set(supplied) - {v.key for v in COMMON + stage.values}):
        raise StageRefusal(
            f"{stage.phase} states no value called {', '.join(extra)} - a value "
            f"the contract does not name is one nothing will read")
    if missing:
        raise StageError(
            f"{stage.phase} ({stage.name}) needs {len(missing)} value(s) nobody "
            f"supplied: " + "; ".join(missing))
    # **The block's whole invariant is one value per line, running to the end
    # of it.** A value carrying a line break does not merely look wrong - its
    # second line reads as another key, so whoever controls one value controls
    # all of them. Refused, not sanitised: a value that had to be repaired was
    # not the value anybody meant.
    #
    # **Item 131 moved this out of the caller branch, where it had been since
    # item 41.** Items 121, 124, 125 and 126 each moved a value from `caller`
    # to `run` - `baseline`, `pack-out`, `spec`, and `worktree` before them -
    # and every one of them left the guarded channel for an unguarded one.
    # `_baseline_for` interpolates a NOTE read out of `00-intake.json`, so a
    # newline there forged a second key line and nothing looked. The tell was
    # already in the suite: item 125 had to repoint the red-team forgery test
    # away from `pack-out` BECAUSE that value moved, and nothing replaced the
    # coverage on the channel it moved into.
    for key, given in out.items():
        if "\n" not in given and "\r" not in given:
            continue
        # Whose defect it is decides what the sentence can ask for. A caller
        # can fix what they typed; a run-sourced value was written by the
        # harness, so the operator is told which resolver produced it rather
        # than asked to repair something they never wrote.
        whose = (f"you supplied it" if key in supplied else
                 f"the harness resolved it from the run, so this is a defect "
                 f"in whatever wrote it")
        raise StageRefusal(
            f"{stage.phase}'s value {key} contains a line break, and every "
            f"value in the block runs to the end of its line - the text after "
            f"the break would be read as another key ({whose})")
    return out


def produced_by(name: str) -> Stage | None:
    """The stage that owes this document, or None if nothing in the contract
    does. Derived rather than tabulated: a second table of who-writes-what is
    the parallel statement item 94 spends a whole guard preventing."""
    return next((s for s in STAGES if name in s.produces), None)


def reads_status(target: Path, run_id: str, name: str) -> str:
    """Present, not-written-yet, and never-going-to-exist are THREE sentences.

    Item 125's rule - an absent document is NAMED, never blank - reached from
    the other side, and the third case is not hypothetical: the shipped default
    `phases[]` omits scoping, so under the profile every adopter is handed
    `20-scope.md` is never written by anybody. A runner told to read it, and
    not told that, hunts for a document nothing will ever produce.

    A phase whose profile cannot be read gets the weaker sentence rather than a
    refusal: this function describes a document, and failing the whole prompt
    over a question about one is the check destroying what it reports on.
    """
    where = ledger.run_dir(target, run_id) / name
    if where.is_file():
        return f"present at {where}"
    owner = produced_by(name)
    if owner is None:
        return ("no phase in this contract produces it - say so in what you "
                "write, and carry on")
    try:
        live = {s.phase for s in active(target)}
    except (StageError, guardrails.PolicyError, OSError, ValueError) as exc:
        # COULD-NOT-LOOK, and it gets its own sentence. The first cut of this
        # function returned the not-written-yet line here, which says the
        # producing phase is active and has not run - a claim this branch is
        # in no position to make, because it could not read the profile that
        # decides it. The mutation pass found it before a mutation ran: two
        # identical returns meant one anchor matched twice.
        return (f"{owner.phase} ({owner.name}) writes it; whether this profile "
                f"runs that phase could not be read ({exc}), so check before "
                f"you rely on it being absent")
    if owner.phase not in live:
        return (f"NOT produced by this run - {owner.phase} ({owner.name}) is "
                f"not an active phase in this profile, so nothing will write "
                f"it; scope what you can without it and say that you did")
    return f"not written yet; {owner.phase} ({owner.name}) produces it"


def standing_rejection(target: Path, run_id: str,
                       phase: str) -> tuple[str, str, str]:
    """The LIVE rejection against this phase's gate: (gate, who, why).

    **Item 136, and walk 4 found it by the only means available — a human
    actually rejecting something.** Across three prior real runs the ledger held
    19 approvals and ZERO rejections, so this path had never been walked. When
    G1 was finally rejected, the reason was written into a hash-chained,
    tamper-evident record that NOTHING READS: `render_prompt` never mentioned a
    verdict, `stage` never imported `approve`, no SOP told a runner to look, and
    the halt sentence named WHO said no without saying WHY. The prompt handed to
    the next runner was byte-identical to the one that produced the rejected
    document, so re-running the phase could only redo the same work.

    **The LIVE verdict only** — item 42's rule. A rejection superseded by a
    later approval is not standing, and telling a runner to answer a complaint
    somebody has since withdrawn is worse than silence.

    Could-not-look gets its own answer, as everywhere else: an unreadable
    ledger returns the reason rather than raising, because this describes a
    verdict and failing the whole prompt over it would stop the phase entirely.
    """
    stage = by_phase(phase)
    entry, problem = _live_gate_entry(target, run_id, phase)
    if problem:
        return stage.gate, "", problem
    if entry is None or entry.get("verdict") != "reject":
        return "", "", ""
    return (stage.gate, str(entry.get("approver") or ""),
            str(entry.get("notes") or ""))


def _live_gate_entry(target: Path, run_id: str,
                     phase: str) -> tuple[dict | None, str]:
    """`(the live ledger entry for this phase's gate, a could-not-look note)`.

    **Item 138 needed the ENTRY and `standing_rejection` threw it away**,
    keeping three strings out of a row that also carries the artifact map item
    32 has pinned at every verdict since the ledger existed. So the digests a
    rejection was ABOUT were unreachable from the phase machine - not missing,
    unread, which is item 136's finding one field along.

    One walk answering both questions, for item 146's reason: what a rejection
    SAID and what it was ABOUT must not come from two readers that can drift.

    LAZY, and it must be: `approve` imports THIS module at column 0, so a
    module-level import here is a cycle. `approve` is already inside
    `validate.closure()`, so the emitted ledger check carries it and this needs
    no `LAZY_OUTSIDE_THE_BUNDLE` entry - which is checked, not assumed.
    """
    stage = by_phase(phase)
    if not stage.gate:
        return None, ""
    from tools.sdlc import approve as gatekeeper
    try:
        live = gatekeeper.live_entries(target, run_id)
    except (OSError, ValueError, KeyError) as exc:
        return None, f"(the approval ledger could not be read: {exc})"
    for entry in live:
        if entry.get("gate") == stage.gate:
            return entry, ""
    return None, ""


def unanswered(target: Path, run_id: str, phase: str) -> list[str]:
    """Artifacts byte-identical to what a STANDING rejection was about.

    **Item 138, and it overturns item 127's W12 on that ruling's own terms.**
    W12 ruled that a runner exiting 0 having decided to stop cannot be told
    from one that finished, and that the OWED stop catches the consequence.
    Measured on the case item 136 created, it does not: goose was re-spawned
    to answer a rejected G1, hit *no credits remaining*, wrote a 365-byte
    transcript and EXITED 0 - and the driver reported GATE, correctly by every
    check it had, because the phase HAD delivered. `20-scope.md` was on disk,
    its hash matched the pin, verify said clean. **W12's safety net only holds
    while a phase has never delivered, and answering a rejection is exactly
    the case where it always has.** UNCHANGED IS NOT STALE, so W9 cannot fire
    either: the document matches its pin perfectly, it simply was not written.

    **The discriminator needs no runner protocol** - which is the thing W12
    named as what would overturn it - because it asks nothing of the runner.
    A human said no about specific bytes; a document identical to those bytes
    has not answered them, whoever produced it and however they exited.

    **Bounded to a STANDING rejection, deliberately.** Outside one, unchanged
    is a legitimate answer: a runner may conclude a document was already
    right, which is item 42's shape and what this item's own filing warned
    about. Here the human's live verdict contradicts it.
    """
    entry, _problem = _live_gate_entry(target, run_id, phase)
    if entry is None or entry.get("verdict") != "reject":
        return []
    was = entry.get("artifacts") or {}
    now = ledger.load(target, run_id).get("artifacts", {})
    stage = by_phase(phase)
    owed = set(stage.produces) | set(getattr(stage, "evidence", ()) or ())
    same = sorted(rel for rel in owed
                  if was.get(rel) and was.get(rel) == now.get(rel))
    if not same:
        return []
    return [f"{phase} ({stage.name}) was re-run against a standing "
            f"{stage.gate} rejection and {', '.join(same)} "
            f"{'is' if len(same) == 1 else 'are'} byte-identical to what was "
            f"rejected, so the phase has not answered it. The rejected draft "
            f"is kept: {ledger.restore_command(run_id, same[0], was[same[0]])}"]


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
        f"one with `tools/sdlc/state.py --record` (under `harness:` below), or it "
        f"is not pinned and no gate can stand on it.",
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
        # **Item 136.** A human said no and the reason reached nobody. It is a
        # BLOCK rather than a value because a value runs to the end of its line
        # and item 131's guard REFUSES a resolved value carrying a line break -
        # a rejection reason is prose and would have been refused outright.
        #
        # FENCED with `intake.fence`, which is item 43(e)'s helper and not a
        # second one: this text is written by a person, it sits ABOVE the value
        # block, and an unfenced line reading `spec: /etc/passwd` inside it
        # would arrive looking exactly like a value the harness stated.
        gate, who_said, why = standing_rejection(target, run_id, phase)
        if gate:
            lines += ["", f"{gate} WAS REJECTED"
                          + (f" by {who_said}" if who_said else "")
                          + ", and the run is halted until a fresh verdict. "
                            "Answering this IS the work of this phase - not "
                            "re-recording what was rejected. What they said, "
                            "quoted as data:",
                      "", adapter.fence(why or "(no reason was recorded)")]
    if stage.reads:
        # **Item 132.** Naming these is the whole fix: six phases named the
        # document they read and one - P2 - described it, so a paid runner
        # asked a human where to look and stopped. The status is here because
        # a name alone still leaves the runner to discover that the document
        # is one this profile never writes.
        lines += ["", "Documents this phase READS and does not write - open "
                      "them before you start:", ""]
        lines += [f"{name}: {reads_status(target, run_id, name)}"
                  for name in stage.reads]
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


#: Every subcommand this CLI offers, in the order `main` tries them, and
#: whether it READS `--set`. ONE table rather than a check inside each branch:
#: three branches each refusing separately is three things that have to keep
#: agreeing, and the next subcommand added would silently join the discarding
#: side. `test_stages.py` derives its cases from this table AND from the
#: parser, so a subcommand in one and not the other is a failure, not a gap.
SUBCOMMANDS: tuple[tuple[str, bool], ...] = (
    ("--list", False),
    ("--prompt", True),
    ("--verify", False),
    ("--advance", False),
)


def _chosen(args: argparse.Namespace) -> tuple[str, bool]:
    """The subcommand `main` will run, and whether it reads `--set`."""
    for flag, reads_set in SUBCOMMANDS:
        if getattr(args, flag.lstrip("-").replace("-", "_")):
            return flag, reads_set
    return "", False


def _refuse_unread_set(chosen: str, pairs: list[str]) -> None:
    """Refuse a `--set` the chosen subcommand will never read, by name.

    **Item 145's ruling, arriving at the other door of this same module.**
    `resolve` REFUSES a supplied value nothing reads — *a supplied value that
    is silently ignored is indistinguishable from one that was honoured* — and
    `test_stages.py` pins it there. This door stayed silent: `--set` was read
    under `--prompt` and DISCARDED under `--list`, `--verify` and `--advance`.
    Measured by spawning: `--advance --force --set this-is-not-a-pair` moved
    the run from P0 to P1 and exited 0, with the pair neither parsed nor
    mentioned. THREE subcommands discarded it, where the defect was reported
    at two — and the one that stayed silent is the one that changes state.

    `StageRefusal` rather than a print and a code of its own, so the single
    sentence in `main` that turns a refusal into an exit stays the only one.
    That is exit 2 — what `--prompt` already answers a malformed `--set` with,
    and the module's code for *the caller got it wrong*; 1 is reserved for
    `--verify` finding things owed, which this is not.
    """
    if pairs:
        raise StageRefusal(
            f"{chosen} does not read --set, so {', '.join(pairs)} would be "
            f"discarded - only --prompt states values to a runner, and a "
            f"supplied value that is silently ignored is indistinguishable "
            f"from one that was honoured")


def _parser() -> argparse.ArgumentParser:
    """The CLI's own flags, in one callable so a guard can read them.

    Built here rather than inside `main` for exactly one reason: the guard that
    holds `SUBCOMMANDS` and this parser to naming the same subcommands has to
    be able to ask the parser, and a parser only `main` can build is one only
    `main` can be asked about.
    """
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
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _parser()
    args = ap.parse_args(argv)
    target = Path(args.target).resolve()

    try:
        # BEFORE any branch runs, because one of them MOVES THE RUN: the
        # discard was measured at exit 0 *after* `--advance` had written the new
        # phase, so a refusal printed afterwards would be a refusal of
        # something that already happened.
        chosen, reads_set = _chosen(args)
        if chosen and not reads_set:
            _refuse_unread_set(chosen, args.set)
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
            # Item 117's third channel, `validate.limits`'s idiom exactly:
            # printed every run, never in `problems`, exit code untouched, and
            # SILENT when PyYAML is present and parsing — because then the
            # profile's shape really was cross-checked and a disagreement is a
            # refusal rather than a note. This phase check is the one command an
            # ordinary run issues at every phase, which is why the sentence
            # lands here rather than in a CLI nothing spawns.
            for limit in guardrails.profile_limits(target):
                print(f"  .. [not checked] {limit}")
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
