"""Item 39 — the golden cross-repo build test: a shared library changes, and
the right repositories rebuild. Green path and red path.

Item 38 built the engine and tested it against graphs this suite wrote itself.
That answers "does the code do what I wrote", which is worth knowing and is not
what a golden test is for. Here the graph comes from a REAL build over the
committed fixture — the extractors parse three manifests in two build systems —
and the closure is asserted against `EXPECTED.md`, a hand-written answer key
that predates the engine.

**Two halves, and neither means much alone.**

*The golden half* says the closure resolves: change `orders-shared` and exactly
`orders-api`, `orders-worker` and `orders-notifier` rebuild, with the publisher
first and the three inert components untouched. It cannot say the closure is
being READ — an engine that ignored the manifests and returned a hard-coded
list would satisfy every assertion in it.

*The seam half* makes that claim falsifiable. Each seam renames ONE value on
ONE line in a copy: the dependency coordinate stops naming a module anybody
publishes, and that dependent must leave the closure while the other two stay.
Deleting the file would break it too and prove nothing — every pipeline notices
a missing file. Two controls hold the other side: a version bump and a rename
inside the inert component must change the closure **not at all**, or the suite
would be measuring sensitivity and calling it precision.

The engine's commands are `exit N`, so what is under test is which repositories
run and in what order, not whether Maven is installed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.graph import build as B  # noqa: E402
from tools.graph.tests import cross_repo as KEY  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.sdlc import suite as S  # noqa: E402

FIXTURE = ROOT / "tools" / "graph" / "tests" / "fixtures" / "orderflow"
WHO = "lead@example.invalid"
SPEC = ("The shared library must expose the arrival channel on an order event, "
        "and every component that depends on it must keep building.")

#: Every component of the fixture, so the profile describes the whole workspace
#: rather than the part this test happens to care about. A closure asked to
#: choose from four components has been given half its answer.
ALL = ("orders-api", "orders-worker", "orders-shared", "orders-notifier",
       "web-console", "deploy-charts", "legacy-reports")


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, timeout=120,
                   capture_output=True)


def profile(root: Path, red: tuple[str, ...] = ()) -> None:
    """Every component declares its own suite, which is item 38's contract."""
    lines = ["scope: estate", "gate_profile: team",
             "phases: [intake, requirements, design, implementation, test, "
             "review, triage, closeout]", "review:", "  block_on: [critical, high]",
             "components:"]
    for name in ALL:
        lines += [f'  - name: "{name}"', f'    path: "{name}"',
                  "    role: authoritative",
                  f'    test_command: "exit {1 if name in red else 0}"',
                  '    build_command: "exit 0"']
    (root / ".sdlc" / "profile.yml").write_text("\n".join(lines) + "\n",
                                                encoding="utf-8")


def workspace(tmp: Path, edits: list[tuple[str, str, str]] | None = None,
              repos: bool = True) -> Path:
    """A copy of the committed fixture with a REAL graph built over it.
    `edits` rename one value on one line each.

    `repos` makes every component its own git repository, which the path tests
    want because "cross-repo" is the claim. The seam and control tests are
    about the manifest join and read no git at all, and `git init` over seven
    components is 3.8 of every 7 seconds - measured, not guessed. Realism
    nothing reads is a slower suite, not a better one.
    """
    root = tmp / "estate"
    shutil.copytree(FIXTURE, root)
    for rel, old, new in (edits or []):
        path = root / rel
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, f"{rel} does not hold {old!r} exactly once"
        before = len(text.splitlines())
        path.write_text(text.replace(old, new), encoding="utf-8")
        assert len(path.read_text(encoding="utf-8").splitlines()) == before, \
            f"{rel} changed line count - every surviving citation just moved"
    for name in ALL if repos else ():
        where = root / name
        _git(where, "init", "-q", "-b", "main")
        _git(where, "add", "-A")
        _git(where, "-c", "user.email=d@x.invalid", "-c", "user.name=D",
             "commit", "-qm", "base")
    (root / ".sdlc").mkdir(exist_ok=True)
    B.build(root / "workspace.toml", root / ".sdlc" / "graph", run_id="crossrepo")
    profile(root)
    return root


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """One real build for the golden half. The seam half builds its own, since
    each seam is a different fixture."""
    return workspace(tmp_path_factory.mktemp("golden-cross-repo"))


def run_at(root: Path, scope: str = "orders-shared") -> str:
    """A run parked at P5 whose approved design scopes `scope` only."""
    st = L.create(root, "requirement", "the arrival channel")
    rid = st["run_id"]
    folder = L.run_dir(root, rid)
    (folder / "30-design.md").write_text(
        f"# Design\n\n## Change set\n\n### {scope}\n"
        f"- `{scope}/pom.xml` - publish the new enum.\n", encoding="utf-8")
    (folder / "50-tests.md").write_text("Filled in by P5.\n", encoding="utf-8")
    for rel in ("30-design.md", "50-tests.md"):
        L.record_artifact(root, rid, rel)
    L.advance(root, rid, phase="P5", status="running")
    return rid


# ── the golden half: the closure resolves, off a real build ──────────────────


def test_the_answer_key_and_its_transcription_agree():
    """`cross_repo.py` is a transcription. If it drifts from `EXPECTED.md`, the
    golden half asserts a closure nobody wrote down."""
    key = (FIXTURE / "EXPECTED.md").read_text(encoding="utf-8")
    section = key[key.index("## Item 39 —"):]
    for name, manifest, line, system in KEY.DEPENDENTS:
        assert f"`{name}`" in section, f"{name} is not in the answer key"
        assert f"{name}/{manifest}:{line}" in section, \
            f"the key does not cite {name}/{manifest}:{line}"
        assert system in section
    for name in KEY.NOT_REACHED:
        assert f"`{name}`" in section, f"{name} is not named as unreached"


def test_a_shared_library_change_reaches_exactly_its_dependents(built):
    ordered, cycles = S.closure(built, [KEY.PUBLISHER])
    assert set(ordered) == set(KEY.CLOSURE), (
        f"the answer key says {sorted(KEY.CLOSURE)}; the engine says {sorted(ordered)}")
    assert cycles == []
    assert ordered[0] == KEY.PUBLISHER, \
        "a dependent built before what it depends on is not a dependent build"


def test_the_inert_components_are_not_reached(built):
    ordered, _ = S.closure(built, [KEY.PUBLISHER])
    for name in KEY.NOT_REACHED:
        assert name not in ordered, f"{name} rebuilt for a change it cannot see"


def test_the_dependents_are_reached_from_two_build_systems(built):
    """The property that makes this cross-repo rather than cross-module: a
    fixture where every dependent were a Maven module could not tell the
    difference between a real join and a Maven-shaped one."""
    assert {system for _, _, _, system in KEY.DEPENDENTS} == {"maven", "gradle"}
    graph = S.direct_dependents(built)
    assert graph[KEY.PUBLISHER] == {name for name, _, _, _ in KEY.DEPENDENTS}


def test_the_join_is_the_published_module_and_it_is_cited(built):
    """Every dependent's edge is asserted at the line the answer key names."""
    _, edges = S.records.load(built)
    for name, manifest, line, _ in KEY.DEPENDENTS:
        hits = [e for e in edges
                if e["type"] == "DG_DEPENDS_ON" and e["dst"] == KEY.PUBLISHED
                and e["src"].endswith("@" + name)]
        assert len(hits) == 1, f"{name}: {len(hits)} edges onto the shared module"
        assert hits[0]["source_file"].replace("\\", "/").endswith(manifest)
        assert hits[0]["line"] == line, \
            f"{name} cited at line {hits[0]['line']}, key says {line}"


def test_the_extractor_subtracts_a_manifests_own_coordinates(built):
    """`orders-notifier/build.gradle:6` declares `notifier-core`, which its own
    second manifest publishes. It must not become its own dependent — and the
    layer that prevents it HERE is the extractor, which subtracts a manifest's
    own coordinates before any edge exists.

    The mutation pass named this: removing the closure's own `user != publisher`
    guard reddened nothing, because this fixture never produces a self-edge for
    it to catch. The property is defended twice and only one layer is visible
    from here, so this test says which one — and `test_suite.py` proves the
    closure's guard against a graph that does carry the self-edge.
    """
    owner, module = KEY.SELF_PUBLISHED
    _, edges = S.records.load(built)
    self_edges = [e for e in edges if e["type"] == "DG_DEPENDS_ON"
                  and e["dst"] == module and e["src"].endswith("@" + owner)]
    assert self_edges == [], (
        "the extractor stopped subtracting a manifest's own coordinates, so the "
        "closure's guard is now the only thing standing between this fixture and "
        "a component that rebuilds itself")
    assert owner not in S.direct_dependents(built).get(owner, set())
    assert S.closure(built, [owner])[0] == [owner]


def test_third_party_modules_drop_out_with_no_vendor_list(built):
    """They must be excluded because nothing here publishes them. If the
    closure ever needs a prefix list, the join is wrong."""
    _, edges = S.records.load(built)
    consumed = {e["dst"] for e in edges if e["type"] == "DG_DEPENDS_ON"}
    published = {e["dst"] for e in edges if e["type"] == "DG_BUILT_FROM"}
    for module in KEY.THIRD_PARTY:
        assert module in consumed, f"{module} is not even in the fixture any more"
        assert module not in published
    assert not (set(KEY.THIRD_PARTY) & published)


# ── the golden half: the green path ──────────────────────────────────────────


def test_the_green_path_builds_every_dependent_and_owes_nothing(built):
    rid = run_at(built)
    result = S.execute(built, rid, [KEY.PUBLISHER])
    S.save(built, rid, result)
    ran = {(row["component"], row["step"]) for row in result["steps"]
           if row["verdict"] == "green"}
    for name in KEY.CLOSURE:
        assert (name, "build") in ran and (name, "test") in ran, f"{name} did not run"
    for name in KEY.NOT_REACHED:
        assert not any(row["component"] == name for row in result["steps"])
    assert result["verdict"] == "green"
    assert S.obligations(built, rid) == []
    assert ST.advance(built, rid, "P6")["phase"] == "P6", \
        "a clean P5 must let the run move on"


# ── the golden half: the red path ────────────────────────────────────────────


def test_the_red_path_stops_the_run_and_names_the_repository(tmp_path):
    """A dependent in another repository goes red against a green baseline.
    The engine must call it a regression, refuse to leave P5, and — because the
    approved design scoped only the shared library — propose a scope-add."""
    root = workspace(tmp_path)
    rid = run_at(root)
    (L.run_dir(root, rid) / "00-intake.json").write_text(json.dumps(
        {"baseline": {"components": {name: {"exit_code": 0} for name in ALL}}}),
        encoding="utf-8")
    profile(root, red=("orders-worker",))
    result = S.execute(root, rid, [KEY.PUBLISHER])
    S.save(root, rid, result)

    assert result["verdict"] == "new-failures"
    assert S.stopped(result) == [("orders-worker", "new-failures")]
    note = [r["note"] for r in result["steps"]
            if r["component"] == "orders-worker" and r["step"] == "test"][0]
    assert "was GREEN" in note, (
        "the verdict is right by the wrong route - it must be a regression "
        f"against a green baseline, not an unjudgeable red one: {note}")
    assert result["unscoped"] == ["orders-worker"], (
        "the design scoped orders-shared; a break in another repository is a "
        "scope-add, not a fix to make here")
    owed = S.obligations(root, rid)
    assert any("orders-worker (new-failures)" in o for o in owed), owed
    assert any(S.SCOPE_ADD in o for o in owed), owed
    with pytest.raises(ST.StageError, match="orders-worker"):
        ST.advance(root, rid, "P6")

    text = S.scope_add_text(result)
    assert "orders-worker" in text and "G1" in text
    (L.run_dir(root, rid) / S.SCOPE_ADD).write_text(text, encoding="utf-8")
    L.record_artifact(root, rid, S.SCOPE_ADD)
    assert not any(S.SCOPE_ADD in o for o in S.obligations(root, rid))


def test_the_publisher_going_red_is_still_a_regression(tmp_path):
    """The seed itself is not exempt from the bar it sets for its dependents."""
    root = workspace(tmp_path, repos=False)
    rid = run_at(root)
    (L.run_dir(root, rid) / "00-intake.json").write_text(json.dumps(
        {"baseline": {"components": {KEY.PUBLISHER: {"exit_code": 0}}}}),
        encoding="utf-8")
    profile(root, red=(KEY.PUBLISHER,))
    result = S.execute(root, rid, [KEY.PUBLISHER])
    assert S.stopped(result) == [(KEY.PUBLISHER, "new-failures")]
    assert result["unscoped"] == [], "the publisher IS the approved scope"


# ── the seam half: cut one line, and the closure must notice ─────────────────


@pytest.mark.parametrize("seam", KEY.SEAMS, ids=lambda s: s[0])
def test_cutting_a_dependency_coordinate_drops_exactly_that_dependent(tmp_path, seam):
    """One value renamed on one line: the coordinate stops naming a module
    anybody in this workspace publishes. The file, its syntax and its line
    count are untouched, so nothing but a genuine read of that manifest can
    make this fail."""
    label, rel, old, new, drops = seam
    root = workspace(tmp_path, [(rel, old, new)], repos=False)
    ordered, _ = S.closure(root, [KEY.PUBLISHER])
    assert drops not in ordered, f"{label}: {drops} survived a cut coordinate"
    assert set(ordered) == set(KEY.CLOSURE) - {drops}, (
        f"{label} took more than its own dependent with it: "
        f"{sorted(set(KEY.CLOSURE) - {drops} - set(ordered))} also left")


@pytest.mark.parametrize("control", KEY.CONTROLS, ids=lambda c: c[0])
def test_a_control_change_leaves_the_closure_identical(tmp_path, control):
    """Breakage alone proves nothing: a test asserting only that a mutation
    broke the closure passes for an engine so brittle that every change breaks
    everything. A version bump is the most common shared-library change there
    is, and it must not move who rebuilds."""
    label, rel, old, new = control
    root = workspace(tmp_path, [(rel, old, new)], repos=False)
    ordered, cycles = S.closure(root, [KEY.PUBLISHER])
    assert set(ordered) == set(KEY.CLOSURE), f"{label} moved the closure"
    assert ordered[0] == KEY.PUBLISHER and cycles == []


def test_the_seams_and_the_controls_touch_different_files():
    """A control that happened to edit a seam's own manifest would be asserting
    two contradictory things about one line."""
    seam_files = {rel for _, rel, _, _, _ in KEY.SEAMS}
    control_files = {rel for _, rel, _, _ in KEY.CONTROLS}
    assert not (seam_files & control_files)
    assert len(seam_files) == len(KEY.DEPENDENTS), "a dependent with no seam"
