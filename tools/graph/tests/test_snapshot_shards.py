"""Item 13: the committed snapshot is per-component shards, and every claim about it.

Each component commits exactly the facts extracted from its own files into its own
`.sdlc/graph/`; the union is the graph; what link passes derive is recomputed at load and
never committed. The three consequences the plan named are each pinned here: derived
outputs stay out of shards (the loaded graph is byte-identical to a from-source build,
canonical damping included), the determinism check is per-shard (drift names the one
component whose files stopped matching), and a partial set loads LOUDLY.

Everything runs through `B.main(...)` in-process — the real CLI contract, exit codes
included, without subprocess cost.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import tools.graph.build as B
from tools.graph import snapshot

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"
COMPONENTS = ["orders-api", "orders-worker", "orders-shared", "web-console",
              "orders-notifier", "deploy-charts", "legacy-reports"]
GRAPH = Path(__file__).resolve().parents[1]
ROOT = GRAPH.parents[1]


def ws(tmp_path: Path) -> Path:
    """A disposable copy of the fixture — shards land in component trees, and the
    committed fixture is the answer key's subject, never a scratchpad."""
    work = tmp_path / "ws"
    shutil.copytree(FIXTURE, work)
    return work


def publish(work: Path, out: str = "out", run_id: str = "") -> int:
    args = ["--workspace", str(work / "workspace.toml"), "--out", str(work.parent / out),
            "--publish-snapshot"]
    if run_id:
        args += ["--run-id", run_id]
    return B.main(args)


def load(work: Path, out: str = "loaded") -> int:
    return B.main(["--workspace", str(work / "workspace.toml"),
                   "--out", str(work.parent / out), "--from-snapshot"])


def check(work: Path, out: str = "check") -> int:
    return B.main(["--workspace", str(work / "workspace.toml"),
                   "--out", str(work.parent / out), "--check-snapshot"])


def digests(work: Path) -> dict[str, str]:
    out = {}
    for name in COMPONENTS:
        manifest = work / name / ".sdlc" / "graph" / "snapshot.json"
        if manifest.exists():
            out[name] = json.loads(manifest.read_text(encoding="utf-8"))["sha256"]
    return out


def report_of(tmp_path: Path, out: str) -> dict:
    return json.loads((tmp_path / out / "build_report.json").read_text(encoding="utf-8"))


# ── publish ───────────────────────────────────────────────────────────────────
def test_every_component_gets_a_shard_in_its_own_tree(tmp_path):
    work = ws(tmp_path)
    assert publish(work) == 0
    for name in COMPONENTS:
        dir_ = work / name / ".sdlc" / "graph"
        for f in ("shard.jsonl", "shard.unresolved.jsonl", "snapshot.json"):
            assert (dir_ / f).exists(), f"{name} is missing {f}"


def test_a_shard_carries_no_run_metadata(tmp_path):
    """Byte-identical across rebuilds or useless as a committed artifact: a run id or a
    timestamp in the shard means every rebuild diffs, the diff is noise, and reviewers
    learn to ignore the one artifact whose whole job is to be diffable evidence."""
    work = ws(tmp_path)
    assert publish(work, run_id="aaaa11111111") == 0
    first = {n: (work / n / ".sdlc" / "graph" / "shard.jsonl").read_bytes()
             for n in COMPONENTS}
    first_digests = digests(work)
    assert publish(work, out="out2", run_id="bbbb22222222") == 0
    for name in COMPONENTS:
        assert (work / name / ".sdlc" / "graph" / "shard.jsonl").read_bytes() == first[name]
        assert '"run_id"' not in first[name].decode("utf-8")
    assert digests(work) == first_digests


def test_the_digest_covers_the_unresolved_half_too(tmp_path):
    """A shard that lost its unresolved rows still hashes differently — absence of the
    honest counterpart is a change, not a cleanup."""
    work = ws(tmp_path)
    publish(work)
    before = digests(work)["orders-api"]
    (work / "orders-api" / ".sdlc" / "graph" / "shard.unresolved.jsonl").unlink()
    assert snapshot._digest(work / "orders-api" / ".sdlc" / "graph") != before


def test_a_broken_build_publishes_nothing(tmp_path, monkeypatch, capsys):
    """A shard published without a failed extractor's facts commits an absence that reads
    exactly like a clean answer. The refusal is printed, recorded, and non-zero — the
    command promised an artifact."""
    work = ws(tmp_path)
    real = B.run_extractors_owned

    def broken(*args, **kwargs):
        owned, failures, violations = real(*args, **kwargs)
        failures["properties"] = "RuntimeError: injected"
        return owned, failures, violations

    monkeypatch.setattr(B, "run_extractors_owned", broken)
    assert publish(work) == 2
    assert "snapshot: refused - " in capsys.readouterr().out
    assert digests(work) == {}, "a refused publish must write no shard at all"


# ── determinism, per shard ────────────────────────────────────────────────────
def test_drift_localises_to_the_component_whose_files_changed(tmp_path):
    """The per-shard determinism check is strictly more sensitive than a whole-graph
    diff: a drifting extractor names the one component whose files it read."""
    work = ws(tmp_path)
    publish(work)
    before = digests(work)
    props = work / "orders-worker" / "src" / "main" / "resources" / "application.properties"
    props.write_text(props.read_text(encoding="utf-8").replace(
        "order-events-v1", "order-events-v9"), encoding="utf-8")
    assert publish(work, out="out2") == 0
    after = digests(work)
    changed = [n for n in COMPONENTS if before[n] != after[n]]
    assert changed == ["orders-worker"], f"drift leaked across shard boundaries: {changed}"


def test_check_is_green_when_nothing_changed(tmp_path, capsys):
    work = ws(tmp_path)
    publish(work)
    assert check(work) == 0
    assert "shard(s) match their committed bytes" in capsys.readouterr().out


def test_check_names_the_drifted_component_and_exits_2(tmp_path, capsys):
    work = ws(tmp_path)
    publish(work)
    props = work / "orders-worker" / "src" / "main" / "resources" / "application.properties"
    props.write_text(props.read_text(encoding="utf-8").replace(
        "order-events-v1", "order-events-v9"), encoding="utf-8")
    assert check(work) == 2
    out = capsys.readouterr().out
    assert "snapshot: DRIFT in orders-worker" in out
    assert "DRIFT in orders-api" not in out


def test_check_reports_a_component_that_was_never_published(tmp_path, capsys):
    """Unpublished is not drift: nothing to compare is a different fact from comparing
    unequal, and conflating them would make the first publish read as a regression."""
    work = ws(tmp_path)
    publish(work)
    shutil.rmtree(work / "web-console" / ".sdlc")
    assert check(work) == 0
    assert "web-console has no committed shard" in capsys.readouterr().out


# ── load ──────────────────────────────────────────────────────────────────────
def test_the_loaded_graph_is_byte_identical_to_a_source_build(tmp_path):
    """The union of the shards plus recomputed link passes IS the graph. If this ever
    diverges, either a shard carries derived state or a link pass is not the pure
    function of facts-plus-config the plan requires it to be — both findings, not
    tolerances."""
    work = ws(tmp_path)
    assert publish(work, out="source") == 0
    assert load(work, out="loaded") == 0
    source = (tmp_path / "source" / "graph.jsonl").read_bytes()
    loaded = (tmp_path / "loaded" / "graph.jsonl").read_bytes()
    assert source == loaded


def test_the_loaded_graph_contains_recomputed_canonical_damping(tmp_path):
    """The legacy chart's rival claim arrives at 0.5 in the loaded view even though no
    shard stores a damped edge — the derived layer really is recomputed, not smuggled."""
    work = ws(tmp_path)
    publish(work)
    for name in COMPONENTS:
        shard = (work / name / ".sdlc" / "graph" / "shard.jsonl").read_text(encoding="utf-8")
        assert '"canonical": false' not in shard, f"{name} committed a derived verdict"
    load(work, out="loaded")
    edges = [json.loads(line) for line in
             (tmp_path / "loaded" / "graph.jsonl").read_text(encoding="utf-8").splitlines()
             if '"kind": "edge"' in line]
    damped = [e for e in edges if e["type"] == "DG_DEPLOYS" and e["confidence"] == 0.5]
    assert len(damped) == 1, "the legacy claim must arrive damped in the loaded view"


def test_a_partial_set_loads_loudly_and_answers_for_what_is_present(tmp_path, capsys):
    work = ws(tmp_path)
    publish(work)
    shutil.rmtree(work / "web-console" / ".sdlc")
    assert load(work, out="partial") == 0
    out = capsys.readouterr().out
    assert "no committed shard" in out and "web-console" in out
    report = report_of(tmp_path, "partial")
    assert report["snapshot"]["missing"] == ["web-console"]
    assert len(report["snapshot"]["loaded"]) == len(COMPONENTS) - 1
    graph = (tmp_path / "partial" / "graph.jsonl").read_text(encoding="utf-8")
    assert "ui:web-console" not in graph


def test_a_tampered_shard_is_refused_by_name_and_the_load_exits_2(tmp_path, capsys):
    """Stale or edited evidence is worse than absence: the rows are NOT loaded, the
    component is named, and the exit is non-zero — a graph quietly built on bytes that
    fail their own digest would be item 80's staleness lesson at estate scale."""
    work = ws(tmp_path)
    publish(work)
    shard = work / "orders-api" / ".sdlc" / "graph" / "shard.jsonl"
    shard.write_text(shard.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert load(work, out="bad") == 2
    assert "snapshot: REFUSING orders-api" in capsys.readouterr().out
    report = report_of(tmp_path, "bad")
    assert report["status"] == "snapshot-mismatch"
    assert report["snapshot"]["mismatched"] == ["orders-api"]


def test_an_unknown_schema_version_is_refused_not_crashed(tmp_path):
    """T111 round 2, leader-flagged and reviewer-spawned. A shard whose BYTES verify but
    whose header declares a `schema_version` this build does not understand used to reach
    a person as `exit 1, empty stdout, a stderr traceback ending in ShardSchemaError` — the
    door at `build.py` catches its OWN `BuildError`, and a bare exception raised three
    layers down (`emit.read_jsonl`) is not that type, so it escaped uncaught.

    This is CLAUDE.md's rule stated outright — a CLI's contract is a process, not a
    function — applied to its own root cause: every OTHER test in this file calls
    `B.main(...)` in-process (this file's own docstring calls that "the real CLI contract,
    exit codes included, without subprocess cost"), and none of them could have told a
    controlled refusal apart from a propagating exception the way a genuine spawn does,
    because an in-process call that raises fails the CALLING test loudly rather than
    reproducing what an actual shell sees. So this one test genuinely spawns
    `sys.executable` and reads the exit code and both streams, the way a person would.

    The re-stamp keeps the shard's recorded digest CONSISTENT with its new bytes (via
    `snapshot._digest`, the same function the loader itself checks against), so the
    earlier, unrelated `mismatched`-by-digest branch does not fire first and mask this one.
    """
    work = ws(tmp_path)
    assert publish(work) == 0
    dir_ = work / "orders-api" / ".sdlc" / "graph"
    shard_path = dir_ / "shard.jsonl"
    lines = shard_path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    assert header["kind"] == "header", "the fixture's own first line must be the header"
    header["schema_version"] = 99999
    lines[0] = json.dumps(header, sort_keys=True)
    shard_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    manifest_path = dir_ / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"] = snapshot._digest(dir_)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(GRAPH / "build.py"), "--workspace",
         str(work / "workspace.toml"), "--out", str(tmp_path / "loaded"),
         "--from-snapshot"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))

    assert proc.returncode == 2, (
        f"a person running this door must see a REFUSAL (exit 2), not a crash:\n"
        f"returncode={proc.returncode}\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}")
    assert proc.stderr == "", f"no traceback should ever reach a person here: {proc.stderr!r}"
    assert "orders-api" in proc.stdout, (
        f"the refused component must be named on stdout: {proc.stdout!r}")


def test_an_edge_row_missing_provenance_is_refused_not_crashed(tmp_path):
    """T111 round 3, leader-flagged and reviewer-spawned: a SIBLING of the test above, not
    a repeat of it. A shard with a valid, consistent digest whose header is perfectly fine
    but whose edge row is missing `provenance` used to reach a person as `exit 1` and an
    18-line stderr traceback ending in `rows.RowFieldError` - round 1's exact defect
    (a bare exception escaping this door uncaught), one refusal over: `snapshot.load`'s
    catch used to name `emit.ShardSchemaError` only, and `RowFieldError` is a DIFFERENT
    `ValueError` subclass `check_row` raises from three stack frames further in.
    """
    work = ws(tmp_path)
    assert publish(work) == 0
    dir_ = work / "orders-api" / ".sdlc" / "graph"
    shard_path = dir_ / "shard.jsonl"
    lines = shard_path.read_text(encoding="utf-8").splitlines()
    edge_index = next(i for i, ln in enumerate(lines) if '"kind": "edge"' in ln)
    edge = json.loads(lines[edge_index])
    del edge["provenance"]
    lines[edge_index] = json.dumps(edge, sort_keys=True)
    shard_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    manifest_path = dir_ / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"] = snapshot._digest(dir_)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(GRAPH / "build.py"), "--workspace",
         str(work / "workspace.toml"), "--out", str(tmp_path / "loaded"),
         "--from-snapshot"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))

    assert proc.returncode == 2, (
        f"a person running this door must see a REFUSAL (exit 2), not a crash:\n"
        f"returncode={proc.returncode}\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}")
    assert proc.stderr == "", f"no traceback should ever reach a person here: {proc.stderr!r}"
    assert "orders-api" in proc.stdout, (
        f"the refused component must be named on stdout: {proc.stdout!r}")


def test_a_load_that_finds_no_shard_anywhere_is_not_ok_and_exits_2(tmp_path, capsys):
    """Not one component has a committed shard: the load read nothing, so this is the
    empty graph - item 34's "empty reads exactly like clean" in the one command a fresh
    clone runs to rebuild from the snapshot. A partial set answers for what is present
    and stays exit 0 (the test above); a set with NOTHING present is not a workspace
    with no couplings, it is a build that could not look, and it rides the same
    status->exit ladder as `snapshot-mismatch`. No `publish()` here: the fixture ships
    no shards, so every component is missing."""
    work = ws(tmp_path)
    assert load(work, out="empty") == 2
    out = capsys.readouterr().out
    assert "nothing to load" in out
    report = report_of(tmp_path, "empty")
    assert report["status"] == "snapshot-empty"
    assert report["snapshot"]["loaded"] == []
    assert len(report["snapshot"]["missing"]) == len(COMPONENTS)
    assert report["totals"]["nodes"] == 0


# ── the single-repo degenerate case ───────────────────────────────────────────
def test_one_repository_is_the_general_path_run_once(tmp_path):
    """Rev 13's thesis in the artifact layer: a single repo publishes its one shard into
    its own .sdlc/graph/, beside the resolved view the default out already writes there
    — committed facts and derived view coexisting in the directory that travels."""
    repo = tmp_path / "solo"
    shutil.copytree(FIXTURE / "orders-api", repo)
    (repo / ".git").mkdir()
    code = B.main(["--target", str(repo), "--publish-snapshot"])
    assert code == 0
    graph_dir = repo / ".sdlc" / "graph"
    assert (graph_dir / "shard.jsonl").exists(), "the committed half"
    assert (graph_dir / "graph.jsonl").exists(), "the resolved view"
    assert (graph_dir / "snapshot.json").exists()


# ── the flag matrix stays honest ──────────────────────────────────────────────
@pytest.mark.parametrize("pair", [
    ("--publish-snapshot", "--from-snapshot"),
    ("--publish-snapshot", "--check-snapshot"),
    ("--from-snapshot", "--check-snapshot"),
], ids=lambda p: p[0].strip("-") + "+" + p[1].strip("-"))
def test_the_three_statements_cannot_be_combined(tmp_path, capsys, pair):
    work = ws(tmp_path)
    code = B.main(["--workspace", str(work / "workspace.toml"),
                   "--out", str(tmp_path / "x"), *pair])
    assert code == 2
    assert "cannot be combined" in capsys.readouterr().out


def test_from_snapshot_refuses_extractor_narrowing(tmp_path, capsys):
    work = ws(tmp_path)
    code = B.main(["--workspace", str(work / "workspace.toml"),
                   "--out", str(tmp_path / "x"), "--from-snapshot", "--only", "properties"])
    assert code == 2
    assert "runs no extractors" in capsys.readouterr().out


# ── item 133: a pure question must not write into the repository it audits ────
#
# **Walk 4 filed this, and it is item 120's F6 one flag along in the same
# module.** That item fixed `--check-fresh`, which used to REPLACE the committed
# build report, and left its neighbour standing — this project's dominant shape,
# *a rule hardened where somebody was looking*.
#
# `build.py --check-snapshot` handed `snapshot.check` a scratch at
# `out / "snapshot-check"`, and `out` IS the committed `.sdlc/graph/`. Measured
# on a real install: 3 files, 837 KB, not gitignored, and counted by
# `intake.dirty_paths` — so asking whether the snapshot still matched left the
# tree dirty and the NEXT `intake.open_run` would refuse it by name.
#
# **Why no existing test saw it**: every one of them points `--out` at
# `work.parent`, outside the workspace. A real install points it INSIDE. The
# fixture spelled a location the defect could not show up in — item 118's shape,
# where every test passed because the fixture wrote the reader's name.


def _tree(root: Path) -> dict[str, tuple[int, str]]:
    """Every file's PATH **and its bytes**, as (size, sha256).

    **Item T233: this returned a set of paths, and that is why the test below
    could not see the defect it is named for.** A file the check REPLACED is
    byte-for-byte invisible to a set of names — and replacing files was exactly
    what `--check-snapshot` still did after item 133 removed the scratch
    DIRECTORY that a set of names could see. A helper that cannot express the
    property its test asserts is a green test about nothing.
    """
    return {str(p.relative_to(root)): (p.stat().st_size,
                                       hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob("*") if p.is_file()}


def _diff(before: dict[str, tuple[int, str]],
          after: dict[str, tuple[int, str]]) -> str:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    return (f"added={added[:6]} removed={removed[:6]} "
            f"modified={[(k, before[k][0], after[k][0]) for k in changed][:6]}")


def test_the_tree_helper_can_see_a_file_that_was_replaced(tmp_path):
    """**A positive control for the helper the test below trusts**, because it
    did not have one and that is the whole of item T233's second half: `_tree`
    returned a set of names, a check that REPLACED two committed files added no
    name, and the test asserting "writes nothing" passed for six items while the
    thing it is named for was happening. A helper that cannot fail cannot
    defend anything, so this one is asked to.
    """
    root = tmp_path / "r"
    root.mkdir()
    same, replaced = root / "same.txt", root / "replaced.txt"
    same.write_text("unchanged", encoding="utf-8")
    replaced.write_text("before", encoding="utf-8")
    before = _tree(root)
    replaced.write_text("after!", encoding="utf-8")     # same length, new bytes
    assert _tree(root) != before, (
        "_tree cannot tell a replaced file from an untouched one, so every "
        "assertion built on it is about file NAMES and says nothing about bytes")
    assert _tree(root)["same.txt"] == before["same.txt"]


def test_checking_the_snapshot_writes_nothing_into_the_repository_it_audits(tmp_path):
    """**The defect, stated as the behaviour it was**, at the address a real
    install asks from: the graph is published into `<repo>/.sdlc/graph` and
    committed, and then the question is asked about it.

    Red at the pre-item SHA in two ways: `build_report.json` came back with a
    fresh `run_id` and `snapshot.published` replaced by the answer to a
    different question, and `manifest.json` came back re-stamped — so asking
    whether the snapshot still held REPLACED the provenance of the publish that
    made it, and left a tree the next `intake.open_run` refuses by name.

    `--check-fresh` is the CONTROL, and it is the reason this was a finding
    rather than a guess: item 120 fixed that flag, this one stood beside it, and
    a future change that reintroduces a write to EITHER of them fails here.
    """
    work = ws(tmp_path)
    inside = work / ".sdlc" / "graph"
    args = ["--workspace", str(work / "workspace.toml"), "--out", str(inside)]
    assert B.main(args + ["--publish-snapshot"]) == 0
    committed = _tree(work)
    assert (inside / "build_report.json").name in {Path(p).name for p in committed}, (
        "the fixture published nothing into the audited directory, so this "
        "proves nothing")

    assert B.main(args + ["--check-snapshot"]) == 0
    after = _tree(work)
    assert after == committed, (
        f"a question about the snapshot wrote into the repository it audits: "
        f"{_diff(committed, after)}")

    assert B.main(args + ["--check-fresh"]) in (0, 2)
    assert _tree(work) == committed, (
        f"--check-fresh, item 120's fix and this one's control, has regressed: "
        f"{_diff(committed, _tree(work))}")


def test_drift_is_still_found_with_the_output_inside_the_repository(tmp_path, capsys):
    """The property must SURVIVE at the address where the defect lived. The
    existing drift test points `--out` at `work.parent`, OUTSIDE the workspace —
    which is exactly why it never saw this — so this one asks the same question
    from where a real install asks it."""
    work = ws(tmp_path)
    assert publish(work) == 0
    props = (work / "orders-worker" / "src" / "main" / "resources"
             / "application.properties")
    props.write_text(props.read_text(encoding="utf-8").replace(
        "order-events-v1", "order-events-v9"), encoding="utf-8")
    inside = work / ".sdlc" / "graph"
    assert B.main(["--workspace", str(work / "workspace.toml"),
                   "--out", str(inside), "--check-snapshot"]) == 2
    # **Item T233 changed the channel, not the property.** This read the verdict out of
    # `build_report.json` inside the audited directory — which is to say it depended on
    # the very write that replaced the publish's own provenance. The verdict now reaches
    # its caller the way `--check-fresh`'s does: the exit code and stdout.
    assert "DRIFT in orders-worker" in capsys.readouterr().out


def test_a_component_with_no_committed_shard_is_still_named(tmp_path, capsys):
    """The other half of the verdict, and the branch that returns BEFORE any
    scratch is touched — which is why the harness's own repository never
    reproduced this defect and a published fixture had to.

    Read from stdout since item T233, for the same reason as the drift test
    above: the check no longer writes a report, because the report it wrote was
    the audited directory's own.

    **The equality survives the channel change.** This was
    `sorted(report["snapshot"]["unpublished"]) == sorted(COMPONENTS)`; asking
    stdout the same question with a comprehension OVER `COMPONENTS` would have
    kept the all-seven-are-named half and silently dropped the other — an eighth
    name, or one named twice, would have passed. So the names are parsed OUT of
    the output and the two lists are compared, which is what the report channel
    was doing. A channel change is exactly where an assertion loses a half."""
    work = ws(tmp_path)
    assert check(work) == 0
    out = capsys.readouterr().out
    named = re.findall(r"^snapshot: (\S+) has no committed shard to check$",
                       out, flags=re.MULTILINE)
    assert sorted(named) == sorted(COMPONENTS), (
        f"the check named {sorted(named)}, not {sorted(COMPONENTS)}")


def test_check_accepts_no_location_from_its_caller(tmp_path):
    """**The parameter is gone rather than redirected**, so the defect is
    unrepresentable rather than merely fixed: a location nobody outside can read
    back is one nobody outside should be able to choose. A guard, because the
    next caller is the one this is for."""
    import inspect
    names = list(inspect.signature(snapshot.check).parameters)
    assert names == ["root", "components", "facts_by_component"], (
        f"check takes {names} — a caller that can name the scratch is a caller "
        f"that can put it back inside the repository")


def test_the_scratch_it_owns_does_not_outlive_the_question(tmp_path, monkeypatch):
    """Nothing accumulates anywhere, which is the half a redirect would have
    left open: a scratch pointed at a durable directory grows one shard set per
    check, forever.

    **This cannot be red at the pre-item SHA** — the old code used no temporary
    directory at all — so it is a forward guard rather than a proof of the
    defect, and the mutation pass is what makes it worth having.

    **It was VACUOUS on its first cut and a mutation said so.** Setting `TMPDIR`
    / `TEMP` / `TMP` reaches nothing: `tempfile` caches its answer in
    `tempfile.tempdir` at first use, and pytest has already used it, so the
    holding directory stayed empty whatever the code did and a `mkdtemp` that
    never cleans up survived. The module attribute is what `gettempdir` reads.
    """
    import tempfile as tf
    holding = tmp_path / "tmp"
    holding.mkdir()
    monkeypatch.setattr(tf, "tempdir", str(holding))
    assert Path(tf.gettempdir()) == holding, "the redirect itself did not take"
    work = ws(tmp_path)
    assert publish(work) == 0
    assert check(work) == 0
    left = [p for p in holding.iterdir()]
    assert left == [], f"the scratch survived the call: {left[:3]}"
