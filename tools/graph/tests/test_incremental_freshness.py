"""Item 12: incremental build + freshness policy — content decides, commits are provenance.

Measured before designed, on the item-89 workspace: a full extraction runs 122.6s, hashing
every file the extractors would read runs 2.3s, link passes 0.01s — a 53x gap, which is
the whole case for reuse. The plan's phrase was "commit-stamped graph"; the stamp that can
actually answer "is this graph the tree?" is **content**: a moved HEAD with unmoved files
is fresh, a dirty tree is stale, and a commit SHA gets both wrong. Commit SHAs stay in the
build report (`manifest.json`) as provenance for humans — the fifth premise corrected by
measuring first (84, 88, 89, 13, now 12).

The reuse rule leans only on DECLARED facts: per-(extractor, component) input digests,
the extractor version, the workspace config digest, and the transitive `REQUIRES` graph —
because the shared cache lets one component's files feed another's facts, and which
entries a reader took is not recorded. The price of that cache is also paid here: the
dependencies of every rebuilt component re-run EVERYWHERE so the cache holds what a full
build's would, and their facts for reused components are discarded, because the shard
already carries the identical rows.

THE invariant, asserted byte-for-byte in both fixtures: **an incremental build's output
is indistinguishable from a full build of the same tree.**
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import tools.graph.build as B
from tools.graph import snapshot

ROOT = Path(__file__).resolve().parents[3]

ORDERFLOW = Path(__file__).resolve().parent / "fixtures" / "orderflow"
PANTRY = Path(__file__).resolve().parent / "fixtures" / "pantry"
COMPONENTS = ["orders-api", "orders-worker", "orders-shared", "web-console",
              "orders-notifier", "deploy-charts", "legacy-reports"]


def ws(tmp_path: Path, fixture: Path = ORDERFLOW) -> Path:
    work = tmp_path / "ws"
    shutil.copytree(fixture, work)
    return work


def run(work: Path, out: str, *flags: str, run_id: str = "") -> int:
    args = ["--workspace", str(work / "workspace.toml"),
            "--out", str(work.parent / out), *flags]
    if run_id:
        args += ["--run-id", run_id]
    return B.main(args)


def graph_bytes(tmp_path: Path, out: str) -> tuple[bytes, bytes]:
    return ((tmp_path / out / "graph.jsonl").read_bytes(),
            (tmp_path / out / "unresolved.jsonl").read_bytes())


def report_of(tmp_path: Path, out: str) -> dict:
    return json.loads((tmp_path / out / "build_report.json").read_text(encoding="utf-8"))


def freshness_of(work: Path) -> dict:
    """The freshness verdict, ASKED rather than read off disk.

    These four tests used to read `build_report.json` out of the check's output
    directory - which is exactly what item 120 removed, because writing it there
    overwrote the committed record of how the graph was built. The verdict was
    always the return value; only the tests were going the long way round.
    """
    return B.check_freshness(work / "workspace.toml")


def edit_worker_properties(work: Path) -> None:
    props = work / "orders-worker" / "src" / "main" / "resources" / "application.properties"
    props.write_text(props.read_text(encoding="utf-8").replace(
        "order-events-v1", "order-events-v9"), encoding="utf-8")


# ── THE invariant: incremental == full, byte for byte ─────────────────────────


def test_an_all_reused_build_is_byte_identical_to_the_full_build(tmp_path):
    work = ws(tmp_path)
    assert run(work, "full", "--publish-snapshot") == 0
    assert run(work, "inc", "--incremental") == 0
    assert graph_bytes(tmp_path, "inc") == graph_bytes(tmp_path, "full")
    report = report_of(tmp_path, "inc")
    assert report["status"] == "ok", "a build that reused everything is whole, not all-failed"
    assert sorted(report["incremental"]["reused"]) == sorted(COMPONENTS)
    assert report["incremental"]["rebuilt"] == {}


def test_a_one_edit_incremental_is_byte_identical_to_a_fresh_full_build(tmp_path):
    """The edit invalidates its component, and through declared REQUIRES every component whose
    extractors could have read the changed inputs from another component. `properties` writes
    each entry under the component it ran for (`CACHE_SCOPE`, batch 22, T633), so a properties
    edit reaches no other component's facts: the three components whose readers require it
    were rebuilt by `main` for nothing, and are reused - byte-identity proves it."""
    work = ws(tmp_path)
    run(work, "seed", "--publish-snapshot")
    edit_worker_properties(work)
    assert run(work, "inc", "--incremental") == 0
    assert run(work, "full") == 0
    assert graph_bytes(tmp_path, "inc") == graph_bytes(tmp_path, "full")
    inc = report_of(tmp_path, "inc")["incremental"]
    assert inc["rebuilt"] == {"orders-worker": "its own files changed"}
    assert set(inc["reused"]) == set(COMPONENTS) - {"orders-worker"}


def test_the_cache_effect_crosses_components_in_the_second_stack(tmp_path):
    """Pantry's compose file lives in one component and answers another's env reads. A
    rebuilt reader needs that cache populated even though the compose component is
    reused — the extractors it depends on re-run everywhere, facts discarded where the
    shard already speaks. Byte-identity is the proof the cache held what full's would."""
    work = ws(tmp_path, PANTRY)
    run(work, "seed", "--publish-snapshot")
    # touch a python source in one service; the compose component is untouched
    changed = next(p for p in work.rglob("*.py") if "test" not in p.name.lower())
    changed.write_text(changed.read_text(encoding="utf-8") + "\n# item 12\n",
                       encoding="utf-8")
    assert run(work, "inc", "--incremental") == 0
    assert run(work, "full") == 0
    assert graph_bytes(tmp_path, "inc") == graph_bytes(tmp_path, "full")
    inc = report_of(tmp_path, "inc")["incremental"]
    assert inc["reused"], "nothing was reused — the test is not testing reuse"
    assert inc["cache_effect_extractors"], (
        "a rebuilt reader with REQUIRES must force its dependencies to run everywhere")


# ── the stamps: content-derived, byte-stable, no run metadata ─────────────────


def test_stamps_are_byte_stable_across_rebuilds_of_the_same_tree(tmp_path):
    """Item 13's no-run-metadata rule extended to the freshness layer: two publishes of
    one tree under different run ids write identical snapshot.json bytes — a commit SHA
    or a timestamp here would diff on every rebuild and train reviewers to ignore the
    one artifact whose job is to be diffable."""
    work = ws(tmp_path)
    run(work, "one", "--publish-snapshot", run_id="aaaa11111111")
    first = {n: (work / n / ".sdlc" / "graph" / "snapshot.json").read_bytes()
             for n in COMPONENTS}
    run(work, "two", "--publish-snapshot", run_id="bbbb22222222")
    claimed = {c["name"]: c["claimed_by"]
               for c in report_of(tmp_path, "two")["components"]}
    for name in COMPONENTS:
        now = (work / name / ".sdlc" / "graph" / "snapshot.json").read_bytes()
        assert now == first[name], f"{name}'s stamps are not content-derived"
        assert b'"run_id"' not in now
        text = json.loads(now.decode("utf-8"))
        # A CLAIMED component publishing without input stamps has lost its freshness
        # evidence. A component no extractor claims reads nothing, and `{}` is that
        # fact stated honestly — a fabricated stamp would be the actual bug.
        if claimed[name]:
            assert text["inputs"], f"{name} published without input stamps"
        assert text["extractor_version"]
        assert text["config_sha"]


# ── freshness: the committed snapshot vs the tree, in seconds ─────────────────


def test_an_unchanged_tree_is_fresh_at_exit_0(tmp_path, capsys):
    work = ws(tmp_path)
    run(work, "seed", "--publish-snapshot")
    assert run(work, "fresh", "--check-fresh") == 0
    assert "component(s) current" in capsys.readouterr().out
    assert freshness_of(work)["status"] == "fresh"
    assert not (tmp_path / "fresh" / "build_report.json").exists(), (
        "the freshness question wrote a build report, which is the defect")


def test_a_changed_file_makes_its_component_and_its_dependents_stale(tmp_path, capsys):
    """Batch 16 (T630): `--check-fresh` lists exactly the components `--incremental` rebuilds.
    Batch 22 (T633): `properties` is component-scoped, so the three components whose readers
    require it read nothing of `orders-worker`'s properties and stay current, as the
    incremental reuses them."""
    work = ws(tmp_path)
    run(work, "seed", "--publish-snapshot")
    edit_worker_properties(work)
    assert run(work, "fresh", "--check-fresh") == 2
    out = capsys.readouterr().out
    assert "STALE orders-worker" in out
    assert "properties" in out, "the reading extractor is named, not just the component"
    report = freshness_of(work)
    assert report["freshness"]["stale"] == {
        "orders-worker": "files read by properties, spring-rest-calls changed"}
    assert report["freshness"]["fresh"] == sorted(set(COMPONENTS) - {"orders-worker"})


def test_no_snapshot_is_stale_not_vacuously_fresh(tmp_path, capsys):
    """Exit 0 must mean 'the committed graph IS the tree' — with nothing committed there
    is nothing to be current against, and silence would read as currency."""
    work = ws(tmp_path)
    assert run(work, "fresh", "--check-fresh") == 2
    assert "no freshness stamps" in capsys.readouterr().out


def test_a_pre_freshness_shard_is_stale_and_not_reusable(tmp_path):
    """A shard published by item 13's code carries a digest and no stamps. It is honest
    evidence with no currency claim — stale for freshness, rebuilt for incremental."""
    work = ws(tmp_path)
    run(work, "seed", "--publish-snapshot")
    manifest = work / "orders-api" / ".sdlc" / "graph" / "snapshot.json"
    recorded = json.loads(manifest.read_text(encoding="utf-8"))
    manifest.write_text(json.dumps(
        {"schema_version": 1, "sha256": recorded["sha256"]}, indent=2, sort_keys=True)
        + "\n", encoding="utf-8")
    assert run(work, "fresh", "--check-fresh") == 2
    assert run(work, "inc", "--incremental") == 0
    inc = report_of(tmp_path, "inc")["incremental"]
    assert "published before stamps existed" in inc["rebuilt"]["orders-api"]


def test_tampered_shard_bytes_are_stale_even_with_matching_stamps(tmp_path, capsys):
    """Fresh stamps on tampered bytes would launder the tamper into a currency claim."""
    work = ws(tmp_path)
    run(work, "seed", "--publish-snapshot")
    shard = work / "orders-api" / ".sdlc" / "graph" / "shard.jsonl"
    shard.write_text(shard.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert run(work, "fresh", "--check-fresh") == 2
    assert "shard bytes no longer match" in capsys.readouterr().out
    assert run(work, "inc", "--incremental") == 0
    inc = report_of(tmp_path, "inc")["incremental"]
    assert "shard bytes" in inc["rebuilt"]["orders-api"]
    assert "orders-api" not in inc["reused"]


def test_a_changed_extractor_set_makes_everything_stale(tmp_path, monkeypatch):
    work = ws(tmp_path)
    run(work, "seed", "--publish-snapshot")
    monkeypatch.setattr(B, "extractor_version", lambda regs: "someothernewversion")
    assert run(work, "fresh", "--check-fresh") == 2
    stale = freshness_of(work)["freshness"]["stale"]
    assert set(stale) == set(COMPONENTS)
    assert all("extractors or their code changed" in why for why in stale.values())


def test_a_changed_workspace_config_makes_everything_stale(tmp_path):
    work = ws(tmp_path)
    run(work, "seed", "--publish-snapshot")
    toml = work / "workspace.toml"
    toml.write_text(toml.read_text(encoding="utf-8")
                    + '\n[[canonical]]\nservice = "orders-worker"\nowner = "deploy-charts"\n',
                    encoding="utf-8")
    assert run(work, "fresh", "--check-fresh") == 2
    stale = freshness_of(work)["freshness"]["stale"]
    assert all("configuration changed" in why for why in stale.values())


# ── ruling 17 (batch 15b): the stamp digests only what a reader reads ─────────
# The shared part every shard's stamp holds: the [[canonical]] blocks without their
# citations, the layout (names, normalised paths), the profile by NAME. The part each
# shard holds of its own: its component's path as recorded, role, stack and shallow flag
# - everything its `repo:` node records (refined by the owner, 2026-10-07).

STALE_CONFIG = "the workspace configuration changed since its shard was published"


def spawn_build(work: Path, *flags: str) -> subprocess.CompletedProcess:
    """The build as a person runs it - a process, read by exit code and printed text."""
    return subprocess.run(
        [sys.executable, "-m", "tools.graph.build", "--workspace",
         str(work / "workspace.toml"), "--out", str(work.parent / "out-spawned"), *flags],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")


def stale_lines(proc: subprocess.CompletedProcess) -> dict[str, str]:
    """`freshness: STALE <component> - <reason>`, by component."""
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if line.startswith("freshness: STALE "):
            name, _, why = line[len("freshness: STALE "):].partition(" - ")
            out[name] = why
    return out


def edit_toml(work: Path, old: str, new: str) -> None:
    toml = work / "workspace.toml"
    text = toml.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"fixture anchor {old!r} x{text.count(old)}"
    toml.write_text(text.replace(old, new), encoding="utf-8")


ORDERS_API_BLOCK = 'name = "orders-api"\npath = "orders-api"\n'


def test_a_pipeline_field_edit_leaves_the_snapshot_fresh(tmp_path):
    """`test_command` is read by no extractor and no link pass, so it is not in any
    stamp: an edit to it leaves every shard current."""
    work = ws(tmp_path)
    assert run(work, "seed", "--publish-snapshot") == 0
    edit_toml(work, ORDERS_API_BLOCK, ORDERS_API_BLOCK + 'test_command = "make test"\n')
    proc = spawn_build(work, "--check-fresh")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert stale_lines(proc) == {}


def test_a_published_tree_moved_to_another_directory_stays_fresh(tmp_path):
    """The profile is stamped by NAME: a profile path relative to the workspace file
    resolves to another absolute directory once the tree moves, and that is the
    machine's, not the workspace's."""
    work = ws(tmp_path)
    shutil.copytree(B.PROFILE_ROOT / "orderflow", work / "prof")
    edit_toml(work, 'profile = "orderflow"', 'profile = "./prof"')
    assert run(work, "seed", "--publish-snapshot") == 0
    moved = tmp_path / "elsewhere" / "ws"
    shutil.copytree(work, moved)
    proc = spawn_build(moved, "--check-fresh")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert stale_lines(proc) == {}


def test_a_comment_line_above_any_block_changes_no_stamp(tmp_path):
    """A block's citation is its line, and a line moves when a comment is added above
    it. Only the [[canonical]] blocks are stamped, and without their citations."""
    work = ws(tmp_path)
    toml = work / "workspace.toml"
    toml.write_text(toml.read_text(encoding="utf-8")
                    + '\n[[apart]]\nservice = "nothing-here"\ncomponent = "deploy-charts"\n',
                    encoding="utf-8")
    assert run(work, "seed", "--publish-snapshot") == 0
    lines = toml.read_text(encoding="utf-8").split("\n")
    kinds = ("[[canonical]]", "[[entrypoint]]", "[[alias]]", "[[apart]]")
    shifted = []
    for line in lines:
        if line.startswith(kinds):
            shifted.append("# a comment above the block")
        shifted.append(line)
    assert len(shifted) - len(lines) == 5, "one comment above each of the five blocks"
    toml.write_text("\n".join(shifted), encoding="utf-8")
    proc = spawn_build(work, "--check-fresh")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert stale_lines(proc) == {}


def test_a_respelt_component_path_is_the_same_place_to_both_checks(tmp_path):
    """T615: the installer records a component's location, never its block's spelling, so a
    re-spelling (`./orders-api`) changes no shard's bytes - and `--check-fresh` and
    `--check-snapshot` agree that every shard is current (on `main` the `repo:` node took the
    spelling, and both called the shard stale). D15b-91's own-field rule is held by the role
    edits below, which still stale that component alone."""
    work = ws(tmp_path)
    assert run(work, "seed", "--publish-snapshot") == 0
    edit_toml(work, ORDERS_API_BLOCK, 'name = "orders-api"\npath = "./orders-api"\n')
    fresh = spawn_build(work, "--check-fresh")
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    assert not stale_lines(fresh)
    snap = spawn_build(work, "--check-snapshot")
    assert snap.returncode == 0, snap.stdout + snap.stderr
    assert not [line for line in snap.stdout.splitlines()
                if line.startswith("snapshot: DRIFT in ")], snap.stdout


def test_a_role_edit_stales_that_component_alone(tmp_path):
    work = ws(tmp_path)
    assert run(work, "seed", "--publish-snapshot") == 0
    edit_toml(work, ORDERS_API_BLOCK, ORDERS_API_BLOCK + 'role = "legacy"\n')
    proc = spawn_build(work, "--check-fresh")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert stale_lines(proc) == {"orders-api": STALE_CONFIG}


def test_a_role_edit_rebuilds_that_component_alone_incrementally(tmp_path):
    """An own field changes only that component's `repo:` node, never a fact another
    component's extractors read from the shared cache - so the edit invalidates that
    shard and nothing that depends on its extractors (ruling 17's "that component
    only", for reuse as for `--check-fresh`)."""
    work = ws(tmp_path)
    assert run(work, "seed", "--publish-snapshot") == 0
    edit_toml(work, ORDERS_API_BLOCK, ORDERS_API_BLOCK + 'role = "legacy"\n')
    assert run(work, "inc", "--incremental") == 0
    inc = report_of(tmp_path, "inc")["incremental"]
    assert inc["rebuilt"] == {"orders-api": STALE_CONFIG}
    assert sorted(inc["reused"]) == sorted(set(COMPONENTS) - {"orders-api"})
    assert run(work, "full") == 0
    assert graph_bytes(tmp_path, "inc") == graph_bytes(tmp_path, "full")


def test_a_canonical_path_edit_stales_every_shard(tmp_path):
    work = ws(tmp_path)
    assert run(work, "seed", "--publish-snapshot") == 0
    edit_toml(work, 'path = "charts/orderflow"', 'path = "charts/orderflow/values.yaml"')
    proc = spawn_build(work, "--check-fresh")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert stale_lines(proc) == {name: STALE_CONFIG for name in COMPONENTS}


# ── the incremental republish closes the loop ─────────────────────────────────


def test_incremental_publish_rewrites_only_the_rebuilt_shards(tmp_path):
    work = ws(tmp_path)
    run(work, "seed", "--publish-snapshot")
    before = {n: (work / n / ".sdlc" / "graph" / "snapshot.json").read_bytes()
              for n in COMPONENTS}
    edit_worker_properties(work)
    assert run(work, "inc", "--incremental", "--publish-snapshot") == 0
    report = report_of(tmp_path, "inc")
    assert set(report["snapshot"]["published"]) == set(report["incremental"]["rebuilt"])
    for name in report["incremental"]["reused"]:
        now = (work / name / ".sdlc" / "graph" / "snapshot.json").read_bytes()
        assert now == before[name], f"{name} was reused and must not be re-stamped"
    assert run(work, "fresh", "--check-fresh") == 0, \
        "after the republish the snapshot must BE the tree again"


# ── the build never reads its own artifacts ───────────────────────────────────


def test_the_builds_own_artifact_directory_is_never_input(tmp_path):
    """Latent until an extractor globbed `*.json` (item 9a): the chained build writes
    graph output and snapshot manifests into `<component>/.sdlc/`, and a build that
    re-reads them is extracting facts from itself — every rebuild then differs from the
    last, which surfaced as freshness stamps that never stabilised. A planted spec
    inside `.sdlc/` must activate nothing and reach no graph."""
    work = ws(tmp_path)
    planted = work / "orders-api" / ".sdlc" / "graph" / "planted.yaml"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text("openapi: 3.0.1\npaths:\n  /planted/from/artifacts:\n    get: {}\n",
                       encoding="utf-8")
    assert run(work, "out") == 0
    graph = (tmp_path / "out" / "graph.jsonl").read_text(encoding="utf-8")
    assert "/planted/from/artifacts" not in graph


def test_the_installers_own_file_is_never_input_to_freshness(tmp_path):
    """T153 round 2: `check_freshness()` has its OWN `activate()` call, separate from
    `build()`'s, and round 1 tested only the `build()` call site -- reverting the
    exclusion at THIS call site alone still passed the whole suite (81 passed), which is
    exactly the gap. A component whose only change since its published snapshot is the
    installer's own `.mcp.json` appearing must stay fresh: if `activate()` here ever
    claimed it for `openapi-specs`, that extractor would newly appear in the component's
    input digest and freshness would read it as 'files read by openapi-specs changed'
    even though nothing about the adopter's own code moved.
    """
    work = ws(tmp_path)
    run(work, "seed", "--publish-snapshot")
    si = B.load_installer()
    (work / "orders-api" / si.MCP_CONFIG).write_text(
        '{"mcpServers": {}}\n', encoding="utf-8")
    assert run(work, "fresh", "--check-fresh") == 0, (
        "the installer's own file must not make an otherwise-unchanged component stale")
    assert freshness_of(work)["status"] == "fresh"


# ── the unreadable rule, at the unit it lives in ──────────────────────────────


def test_an_unreadable_file_never_matches_its_own_stamp():
    assert not B.stamp_matches("unreadable-abc", "unreadable-abc")
    assert not B.stamp_matches("abc", "unreadable-abc")
    assert not B.stamp_matches("", "")
    assert B.stamp_matches("abc", "abc")


def test_requires_closure_is_transitive():
    class R:
        def __init__(self, name, requires):
            self.name, self.requires = name, requires
    regs = [R("a", ()), R("b", ("a",)), R("c", ("b",))]
    closure = B.requires_closure(regs)
    assert closure["c"] == frozenset({"a", "b"})
    assert closure["a"] == frozenset()


# ── the flag matrix stays honest ──────────────────────────────────────────────


@pytest.mark.parametrize("flags", [
    ("--check-fresh", "--publish-snapshot"),
    ("--check-fresh", "--from-snapshot"),
    ("--incremental", "--from-snapshot"),
    ("--incremental", "--check-snapshot"),
    ("--incremental", "--check-fresh"),
    ("--incremental", "--only", "properties"),
    ("--check-fresh", "--components", "orders-api"),
], ids=lambda f: "+".join(x.strip("-") for x in f if x.startswith("--")))
def test_contradictory_flags_are_refused(tmp_path, capsys, flags):
    work = ws(tmp_path)
    assert run(work, "x", *flags) == 2
    out = capsys.readouterr().out
    assert "cannot be combined" in out or "Pick one" in out or "--incremental" in out \
        or "--check-fresh" in out
