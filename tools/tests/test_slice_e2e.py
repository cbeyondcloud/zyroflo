"""Item 41 — the thin vertical slice: one requirement from P0 to G4.

Every piece existed and nothing had driven them together, which is how the hole
this closes survived: **a hard gate could be walked past**. `approve.py`
refused a yes about work that did not exist, `validate.py` refused a merge with
no approval behind it, and the halt itself — the thing the lifecycle is named
for — was a paragraph in an SOP that the phase machine did not enforce.

So the tests here are about refusing, not about running. A walk that only takes
the happy path proves the pipeline moves; the gates are the product, and a gate
is only a gate if something stops at it.

The workspace is the SYNTHETIC fixture, three components of it — a service, its
shared library and its config component. The task line named three components
of the author's own estate; a test pinned to one machine's repositories cannot
run in CI and cannot be the M4 evidence. `slice.py --target` takes either.

The graph directory here is EMPTY on purpose. Scoping activates on the
directory's presence, and this file is about the phase machine, not about
extraction — a real build over these same components is field evidence, and
thirty seconds of extraction in every CI run would buy nothing this suite does
not already have from the golden chain.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import slice as SL  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.sdlc import validate as V  # noqa: E402
from tools.sdlc import worktree as W  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

WHO = "lead@example.invalid"
FIXTURE = ROOT / "tools" / "graph" / "tests" / "fixtures" / "orderflow"
#: A service, its shared library, and its config component — the three the task
#: line asks for, taken from the fixture so this runs anywhere.
THREE = ("orders-api", "orders-shared", "deploy-charts")

FULL = ("phases: [intake, requirements, scoping, design, implementation, test, "
        "review, triage, closeout]")

#: The legal shape `slice.py` itself names: a workspace whose `phases[]`
#: stops at `triage`, so the run never LEAVES the phase G4 stands at.
STOPS_AT_TRIAGE = ("phases: [intake, requirements, design, implementation, test, "
                   "review, triage]")


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, timeout=120,
                   capture_output=True)


def workspace(root: Path, names=THREE, phases: str = FULL,
              gate_profile: str = "team", graph: bool = True) -> Path:
    """Three real component repositories under a root that is NOT one."""
    root.mkdir(parents=True, exist_ok=True)
    lines = ["scope: estate", phases, f"gate_profile: {gate_profile}",
             'test_command: ""', "review:", "  block_on: [critical, high]",
             "components:"]
    for name in names:
        where = root / name
        if (FIXTURE / name).is_dir():
            subprocess.run([sys.executable, "-c",
                            "import shutil,sys; shutil.copytree(sys.argv[1], sys.argv[2])",
                            str(FIXTURE / name), str(where)], check=True, timeout=300)
        else:
            where.mkdir(parents=True)
            (where / "app.py").write_text("x = 1\n", encoding="utf-8")
        _git(where, "init", "-q", "-b", "main")
        _git(where, "add", "-A")
        _git(where, "-c", "user.email=d@x.invalid", "-c", "user.name=D",
             "commit", "-qm", "base")
        lines += [f'  - name: "{name}"', f'    path: "{name}"',
                  "    role: authoritative", "    vcs:",
                  '      push_remote: ""', '      branch_pattern: ""',
                  "      deny_raw_git: false", '      notes: ""']
    (root / ".sdlc").mkdir(parents=True, exist_ok=True)
    (root / ".sdlc" / "profile.yml").write_text("\n".join(lines) + "\n",
                                                encoding="utf-8")
    if graph:
        (root / ".sdlc" / "graph").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(scope="module")
def _template(tmp_path_factory) -> Path:
    """Built once — three git repositories is the expensive part, not the walk."""
    return workspace(tmp_path_factory.mktemp("slice") / "ws")


@pytest.fixture
def ws(_template: Path, tmp_path: Path) -> Path:
    dest = tmp_path / "ws"
    subprocess.run([sys.executable, "-c",
                    "import shutil,sys; shutil.copytree(sys.argv[1], sys.argv[2])",
                    str(_template), str(dest)], check=True, timeout=300)
    return dest


# ── the walk ─────────────────────────────────────────────────────────────────


def test_one_requirement_reaches_the_merge_gate(ws):
    report = SL.walk(ws, [], WHO, verbose=False)
    assert SL.verdict(report) == []
    assert report["merge_ready"] == [] and report["chain"] == []
    ok, why = A.gate_state(ws, report["run_id"], "G4")
    assert ok, why


def test_the_walk_takes_an_extra_gate_where_it_stands(tmp_path):
    """T548 (batch-3 review B3-4): the walk read `Stage.gate` and a hard-gate
    list that cannot hold an `extra_gates` stop, so it approved only G2 at the
    end of design and the move to implementation was refused - the slice could
    not walk such a workspace at all. It now takes every hard gate that stands
    at a step it leaves, and the verdict expects each one."""
    root = workspace(tmp_path / "ws", phases=FULL + "\nextra_gates: [design]")
    report = SL.walk(root, [], WHO, verbose=False)
    assert SL.verdict(report) == [], SL.verdict(report)
    refused = {s["gate"] for s in report["gates_refused_before_approval"]}
    assert {"G2", "G-design"} <= refused, refused


@pytest.mark.parametrize("phases, extra, gates", [
    # Review D-2: triage deleted, so G4 re-anchors onto review (P6); the old
    # guard asked for P7 by name and refused, saying G4 stood nowhere.
    ("phases: [intake, requirements, scoping, design, implementation, test, review]",
     "", {"G4"}),
    # Review D-1: an extra stop BESIDE the merge gate on the last step. Only
    # G4 was taken there, so `merge_ready` stayed RED for good.
    ("phases: [intake, requirements, scoping, design, implementation, test, review, "
     "triage]", "extra_gates: [triage]", {"G4", "G-triage"}),
    # Review D-1: an extra stop at the END of the lifecycle, after the merge
    # phase: exempted, and the slice printed GREEN over a hard gate nobody took.
    (FULL, "extra_gates: [closeout]", {"G4", "G-closeout"}),
])
def test_every_hard_gate_on_the_last_step_is_proven_too(tmp_path, phases, extra, gates):
    """The walk proves a gate by leaving its step and never leaves the last one,
    so each hard gate standing there is proven after the loop - by a move past
    the step, or by `halted_at` where nothing follows it - then taken."""
    root = workspace(tmp_path / "ws", phases=phases + (f"\n{extra}" if extra else ""))
    report = SL.walk(root, [], WHO, verbose=False)
    assert SL.verdict(report) == [], SL.verdict(report)
    refused = {s["gate"] for s in report["gates_refused_before_approval"]}
    assert gates <= refused, refused


def test_every_hard_gate_refuses_before_it_is_taken(ws):
    """The item, in one assertion. A walk that never had to stop proves the
    pipeline runs; it does not prove the gates hold, and the gates are the
    product."""
    report = SL.walk(ws, [], WHO, verbose=False)
    refused = {s["gate"] for s in report["gates_refused_before_approval"]}
    assert refused == {"G1", "G2", "G3", "G4"}
    for stop in report["gates_refused_before_approval"]:
        assert "does not move past it" in stop["refused"]
        assert "no entry in the ledger" in stop["refused"]


def test_the_walk_covers_the_three_components(ws):
    report = SL.walk(ws, [], WHO, verbose=False)
    assert report["components"] == sorted(THREE)
    for name in THREE:
        entry = W.load_record(ws, report["run_id"])["components"][name]
        assert entry["mode"] == "worktree" and Path(entry["home"]).is_dir()


def test_every_phase_pins_what_it_owes(ws):
    report = SL.walk(ws, [], WHO, verbose=False)
    owed = {rel for p in report["phases"] for rel in ST.by_phase(p).produces}
    assert owed <= set(report["artifacts"])


def test_the_developers_checkouts_are_left_alone(ws):
    """A slice over a real workspace must be safe to run. Nothing it does may
    reach a component's own tree."""
    before = {n: subprocess.run(["git", "-C", str(ws / n), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=120).stdout
              for n in THREE}
    SL.walk(ws, [], WHO, verbose=False)
    for name in THREE:
        proc = subprocess.run(["git", "-C", str(ws / name), "status", "--porcelain"],
                              capture_output=True, text=True, timeout=120)
        assert proc.stdout.strip() == "", f"{name} was modified by the walk"
        head = subprocess.run(["git", "-C", str(ws / name), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=120).stdout
        assert head == before[name]
        branch = subprocess.run(["git", "-C", str(ws / name), "rev-parse",
                                 "--abbrev-ref", "HEAD"], capture_output=True,
                                text=True, timeout=120).stdout.strip()
        assert branch == "main", f"{name} was left on {branch}"


def test_the_report_says_the_artifacts_are_stubs(ws):
    """A rehearsal that reads like a run is how green rows get misattributed to
    an agent — item 29's rule, applied to artifacts."""
    report = SL.walk(ws, [], WHO, verbose=False)
    assert "STUBS" in report["honesty"] and "proves nothing about an agent" in \
        report["honesty"]
    body = (L.run_dir(ws, report["run_id"]) / "30-design.md").read_text(encoding="utf-8")
    assert "SLICE STUB" in body and "No agent produced this" in body


def test_the_report_is_written_into_the_run_folder(ws):
    report = SL.walk(ws, [], WHO, verbose=False)
    on_disk = json.loads((L.run_dir(ws, report["run_id"]) / SL.REPORT)
                         .read_text(encoding="utf-8"))
    assert on_disk["run_id"] == report["run_id"]
    assert on_disk["hard_gates"] == report["hard_gates"]


# ── the gate is not skippable, tested on its own ─────────────────────────────


def test_advancing_past_a_hard_gate_without_an_approval_is_refused(ws):
    rid = L.create(ws, "requirement", "a change", run_id="r1")["run_id"]
    for phase in ("P0", "P1", "P2", "P3"):
        R.deliver(ws, rid, phase)
        if phase != "P3":
            L.advance(ws, rid, phase=ST.next_phase(ws, phase))
    L.advance(ws, rid, phase="P3")
    with pytest.raises(ST.StageError, match="does not move past it"):
        ST.advance(ws, rid, "P4")
    A.record(ws, rid, "G2", "approve", WHO)
    assert ST.advance(ws, rid, "P4")["phase"] == "P4"


def test_a_gate_the_regime_does_not_make_hard_does_not_halt(tmp_path):
    """`solo` folds G3 into G4. The dial sets HOW FINELY agent changes are
    reviewed, never whether — and a gate the regime never asked for must not
    invent a halt."""
    root = workspace(tmp_path / "solo", names=("only",), gate_profile="solo",
                     graph=False,
                     phases="phases: [intake, requirements, design, implementation, "
                            "test, review, triage, closeout]")
    rid = L.create(root, "requirement", "a change", run_id="r1")["run_id"]
    for phase in ("P0", "P1", "P3"):
        R.deliver(root, phase == "P0" and rid or rid, phase)
    A.record(root, rid, "G2", "approve", WHO, force=True)
    L.advance(root, rid, phase="P4")
    R.deliver(root, rid, "P4")
    assert ST.halted_at(root, rid, "P4") == [], \
        "G3 halted under solo, where the regime says it is folded into G4"
    assert ST.advance(root, rid, "P5")["phase"] == "P5"


def test_a_rejected_gate_does_not_open_the_way(ws):
    rid = L.create(ws, "requirement", "a change", run_id="r1")["run_id"]
    R.deliver(ws, rid, "P3")
    L.advance(ws, rid, phase="P3")
    A.record(ws, rid, "G2", "reject", WHO, notes="not yet")
    with pytest.raises(ST.StageError, match="rejected by"):
        ST.advance(ws, rid, "P4")


def test_going_backwards_out_of_a_GATED_phase_needs_no_gate(ws):
    """P7 loops to P3 and P5 loops to P4. Demanding the gate ahead in order to
    go BACK would make the loop the lifecycle is built around impossible.

    Leaving P7 is the case that matters and the first version of this test
    missed it: it went backwards out of P5, which carries no gate at all, so
    the assertion held whether the exemption existed or not. The mutation pass
    is what said so — neutering the exemption reddened nothing.
    """
    rid = L.create(ws, "requirement", "a change", run_id="r1")["run_id"]
    for phase in ("P3", "P4", "P5", "P6", "P7"):
        R.deliver(ws, rid, phase)
    L.advance(ws, rid, phase="P7")
    assert ST.gate_of("P7") == "G4" and "G4" in SL.hard_here(ws)
    assert ST.halted_at(ws, rid, "P7"), "G4 is unapproved, so forward is shut"
    assert ST.advance(ws, rid, "P3")["phase"] == "P3", \
        "triage could not loop back to design, which is the ordinary case"
    # ...and forward out of it is still shut, so the exemption is about
    # direction and not about the gate having quietly stopped mattering.
    L.advance(ws, rid, phase="P7")
    with pytest.raises(ST.StageError, match="does not move past it"):
        ST.advance(ws, rid, "P11")


def test_forcing_past_a_gate_records_that_it_was_forced(ws):
    rid = L.create(ws, "requirement", "a change", run_id="r1")["run_id"]
    R.deliver(ws, rid, "P3")
    L.advance(ws, rid, phase="P3")
    state = ST.advance(ws, rid, "P4", force=True)
    assert "forced past P3" in state["history"][-1]["note"]
    assert "G2" in state["history"][-1]["note"]


def test_an_artifact_that_moves_after_its_yes_closes_the_gate_again(ws):
    """Item 32's pin, reached from the phase machine: a gate approved over
    bytes that then changed is not an open way."""
    rid = L.create(ws, "requirement", "a change", run_id="r1")["run_id"]
    R.deliver(ws, rid, "P3")
    L.advance(ws, rid, phase="P3")
    A.record(ws, rid, "G2", "approve", WHO)
    assert ST.halted_at(ws, rid, "P3") == []
    (L.run_dir(ws, rid) / "30-design.md").write_text("edited after the yes",
                                                     encoding="utf-8")
    assert any("approved different bytes" in p for p in ST.halted_at(ws, rid, "P3"))


# ── the verdict catches a walk that proved nothing ───────────────────────────


def test_a_walk_where_a_gate_never_refused_is_not_green():
    report = {"chain": [], "merge_ready": [], "hard_gates": ["G2", "G3", "G4"],
              "phases": ["P0", "P3", "P4", "P7"], "artifacts": ["30-design.md"],
              "gates_refused_before_approval": [{"gate": "G2"}]}
    assert any("G3" in p and "does not refuse is not a gate" in p
               for p in SL.verdict(report))


def test_a_walk_that_pinned_nothing_is_not_green():
    report = {"chain": [], "merge_ready": [], "hard_gates": [], "phases": ["P0"],
              "artifacts": [], "gates_refused_before_approval": []}
    assert any("stood on nothing" in p for p in SL.verdict(report))


def test_the_merge_gate_is_expected_to_refuse_like_every_other_gate():
    """This test used to assert the opposite, and its premise was measured
    false. It read: G4 stands at the last gated phase and a walk that ends
    there never has to leave it — so `verdict` exempted the last gate, and a
    walk that took G4 unopposed printed GREEN while the docstring claimed every
    gate had been proven twice. The walk CAN attempt the move (against the next
    phase of the superset) and the refusal that comes back names G4, so the
    exemption bought nothing and hid the one gate every preset makes hard.
    """
    report = {"chain": [], "merge_ready": [], "hard_gates": ["G2", "G3", "G4"],
              "phases": ["P0", "P1", "P3", "P4", "P5", "P6", "P7"],
              "artifacts": ["00-intake.md"],
              "gates_refused_before_approval": [{"gate": "G2"}, {"gate": "G3"}]}
    assert any("G4" in p and "does not refuse is not a gate" in p
               for p in SL.verdict(report))


def test_a_gate_the_regime_does_not_make_hard_is_not_reported_unproven():
    """The control on the line above: asking about every hard gate must not
    become asking about every gate. A regime that never made G4 hard did not
    ask the walk to stop there, and inventing a failure would be the same
    defect wearing the other face."""
    report = {"chain": [], "merge_ready": [], "hard_gates": ["G2"],
              "phases": ["P0", "P3", "P7"], "artifacts": ["30-design.md"],
              "gates_refused_before_approval": [{"gate": "G2"}]}
    assert SL.verdict(report) == []


def test_the_last_gate_is_proven_when_the_walk_ends_at_its_phase(tmp_path):
    """The whole walk over the shape `slice.py:186-190` names. The loop takes a
    gate when the run LEAVES the phase carrying it; here nothing follows
    `triage`, so the attempt is the walk's own to make, and what is asserted is
    the recorded PROOF rather than the absence of an error."""
    root = workspace(tmp_path / "triage", names=("only",), graph=False,
                     phases=STOPS_AT_TRIAGE)
    report = SL.walk(root, [], WHO, verbose=False)
    assert report["phases"][-1] == "P7" and "G4" in report["hard_gates"]
    proof = [s for s in report["gates_refused_before_approval"]
             if s["gate"] == "G4"]
    assert len(proof) == 1, report["gates_refused_before_approval"]
    assert proof[0]["phase"] == "P7"
    assert "does not move past it" in proof[0]["refused"]
    assert "no entry in the ledger" in proof[0]["refused"]
    assert SL.verdict(report) == [] and report["merge_ready"] == []


# ── refusing to pretend ──────────────────────────────────────────────────────


def test_a_workspace_without_triage_walks_to_the_merge_gate_where_it_stands(tmp_path):
    """Review D-2. This test pinned "does not run the triage phase, so G4 never
    stands anywhere" for this very shape - but the pipeline re-anchors G4 onto
    review when triage is deleted, which is the case re-anchoring exists for,
    so the sentence was untrue and the workspace could reach its merge gate."""
    root = workspace(tmp_path / "short", names=("only",), gate_profile="solo",
                     graph=False,
                     phases="phases: [intake, requirements, implementation, review]")
    assert "G4" in ST.gates_by_phase(root).get("P6", [])
    report = SL.walk(root, [], WHO, verbose=False)
    assert SL.verdict(report) == [], SL.verdict(report)
    assert "G4" in {s["gate"] for s in report["gates_refused_before_approval"]}


def test_a_workspace_whose_pipeline_places_no_merge_gate_is_refused(tmp_path,
                                                                    monkeypatch):
    """The refusal itself, when it is TRUE: no active step carries G4."""
    root = workspace(tmp_path / "short", names=("only",), gate_profile="solo",
                     graph=False,
                     phases="phases: [intake, requirements, implementation, review]")
    monkeypatch.setattr(ST, "gates_by_phase", lambda target: {"P4": ["G3"]})
    with pytest.raises(SL.SliceError, match="no active phase carries G4"):
        SL.walk(root, [], WHO, verbose=False)


def test_the_walk_stops_if_a_gate_turns_out_to_be_skippable(ws, monkeypatch):
    """The one failure this walk exists to catch. If `advance` ever stops
    refusing, the slice must go red rather than sail through."""
    monkeypatch.setattr(ST, "halted_at", lambda *a, **k: [])
    with pytest.raises(SL.SliceError, match="the gate is skippable"):
        SL.walk(ws, [], WHO, verbose=False)


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_cli_walks_and_reports(ws, capsys):
    assert SL.main(["--target", str(ws), "--approver", WHO]) == 0
    out = capsys.readouterr().out
    assert "slice GREEN" in out and "4 gate(s) refused" in out
    assert "REFUSED the move" in out


def test_cli_refuses_without_an_approver(tmp_path, capsys):
    root = workspace(tmp_path / "anon", names=("only",), gate_profile="solo",
                     graph=False)
    assert SL.main(["--target", str(root)]) == 2
    assert "a gate taken by nobody is not taken" in capsys.readouterr().out


def test_cli_json_carries_the_refusals_and_the_honesty(ws, capsys):
    assert SL.main(["--target", str(ws), "--approver", WHO, "--json"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["problems"] == []
    assert len(parsed["gates_refused_before_approval"]) == 4
    assert "proves nothing about an agent" in parsed["honesty"]


def test_the_ledger_gate_reads_the_finished_run(ws):
    """The last link: what the walk produced is what CI would judge."""
    report = SL.walk(ws, [], WHO, verbose=False)
    branch = W.load_record(ws, report["run_id"])["components"]["orders-api"]["branch"]
    assert V.run_of_branch(branch, V.patterns_of(ws)) == report["run_id"]
    assert V.merge_ready(ws, report["run_id"]) == []
    assert V.integrity(ws) == []
