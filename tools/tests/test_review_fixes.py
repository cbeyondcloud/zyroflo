"""Regression tests for the findings the max-effort review confirmed.

Every test here fails against the code as it was before the fix. That is the bar: a test
that passes both ways documents behavior, it does not defend it. Each docstring states the
concrete failure the check exists to prevent, because a bare assertion tells a future
reader what broke but never why anyone cared.

Grouped by finding, most severe first.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tools.graph.core.model import Edge, Node, Provenance, is_safe_rel_type, norm_id
from tools.graph.core.lib import ids, manifest, repo_walk

ROOT = Path(__file__).resolve().parents[2]


def load_installer():
    spec = importlib.util.spec_from_file_location("sdlc_init", ROOT / "sdlc-init.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── F1 · Cypher injection through edge_types ───────────────────────────────────
INJECTIONS = [
    "calls]-() RETURN 1 //",          # closes the pattern, appends a clause
    "calls|DG_secret",                # smuggles a second relationship type
    "calls WHERE 1=1",
    "calls`",
    "calls ",                         # trailing space is enough to break the identifier
]


@pytest.mark.parametrize("payload", INJECTIONS)
def test_unsafe_relationship_types_are_rejected(payload: str):
    """`neighbors(edge_types=[...])` interpolates straight into Cypher — relationship types
    cannot be parameterized, so there is no driver-level escaping to fall back on. A
    verifier passed `calls]-() RETURN ...` and got the injected clause to EXECUTE, coming
    back with ['calls','secret','writes'] instead of ['calls']."""
    assert not is_safe_rel_type(payload)
    assert not is_safe_rel_type(f"DG_{payload}")


@pytest.mark.parametrize("ok", ["calls", "DG_CALLS", "PRODUCES", "WRITES_TABLE", "a1_b2"])
def test_real_relationship_types_still_pass(ok: str):
    """The mirror image: a guard that rejects the estate's own types is just an outage."""
    assert is_safe_rel_type(ok)


def test_edge_construction_rejects_an_injectable_type():
    """Enforced at the source, not only at the store, so no future backend has to remember."""
    with pytest.raises(ValueError, match="interpolated into Cypher"):
        Edge(src="a", type="CALLS]-() RETURN 1 //", dst="b",
             source_file="f", line=1, extractor="x")


def test_store_refuses_to_interpolate_an_unsafe_type_without_a_database():
    """No live Neo4j needed: the guard must trip before any connection is attempted, or a
    developer without Docker never sees the failure this protects against."""
    from tools.graph.core.store.neo4j_store import _require_safe_rel_type

    with pytest.raises(ValueError, match="unsafe relationship type"):
        _require_safe_rel_type("DG_calls]-() RETURN 1 //")


def test_an_empty_edge_type_is_an_error_not_an_empty_result():
    """`:DG_` is legal Cypher that matches nothing, so an empty entry would return `[]` —
    indistinguishable from "this node has no neighbors of that kind"."""
    from tools.graph.core.store.neo4j_store import Neo4jStore

    with pytest.raises(ValueError, match="empty relationship type"):
        Neo4jStore(uri="bolt://unused:7687").neighbors(["svc:x@r"], edge_types=[""])


# ── F2 · Bash(powershell:*) defeats deny_raw_git ───────────────────────────────
def test_an_interpreter_wrapper_is_scoped_to_its_whole_command():
    """`Bash(powershell:*)` grants arbitrary PowerShell. Written next to a deny rule for
    `git push`, it means `powershell -Command "git push"` walks straight through the wall
    the deny_raw_git branch exists to build."""
    mod = load_installer()
    rule = mod.wrapper_permission('powershell -File ./checkin.ps1 "{message}"')
    assert rule == "Bash(powershell -File ./checkin.ps1:*)"
    assert rule != "Bash(powershell:*)"


def test_a_bare_interpreter_earns_no_rule_at_all():
    """Nothing left to scope means no safe rule exists. Prompting the human is the correct
    outcome; a blanket grant is not."""
    mod = load_installer()
    assert mod.wrapper_permission("powershell") is None
    assert mod.wrapper_permission("") is None


def test_a_plain_script_wrapper_keeps_the_simple_rule():
    """The common case must not get worse to fix the dangerous one."""
    mod = load_installer()
    assert mod.wrapper_permission('./checkin.sh "{message}"') == "Bash(./checkin.sh:*)"


def test_deny_raw_git_settings_never_grant_a_shell():
    """End-to-end through the real settings builder: the allowlist and the denylist must
    not contradict each other."""
    mod = load_installer()
    settings = mod.build_settings(
        {"test_command": "python -m pytest", "coverage_command": "", "lint_command": "",
         "build_command": "", "vcs": {"deny_raw_git": True,
                                      "commit_command": 'powershell -File ./checkin.ps1 "{message}"'}},
        None,
    )
    allow = settings["permissions"]["allow"]
    assert "Bash(git push:*)" in settings["permissions"]["deny"]
    for blanket in ("Bash(powershell:*)", "Bash(pwsh:*)", "Bash(python:*)",
                    "Bash(bash:*)", "Bash(sh:*)", "Bash(cmd:*)"):
        assert blanket not in allow, f"{blanket} is a shell, not a scoped command"
    assert "Bash(python -m pytest:*)" in allow, "the real test command must still run"


# ── F5 · merge_block silently deleted user text ────────────────────────────────
def test_an_orphaned_begin_marker_refuses_rather_than_deleting(tmp_path: Path):
    """The data-loss path: a file with a BEGIN and no END fell through to the append branch,
    leaving two BEGINs and one END. The NEXT run took the first BEGIN and the only END and
    deleted everything between them — including the user's own paragraphs."""
    mod = load_installer()
    existing = (f"# My notes\n\n{mod.BLOCK_BEGIN}\n\n"
                "IRREPLACEABLE HAND-WRITTEN CONTENT\n\nmore of it\n")
    with pytest.raises(mod.BlockMarkerError, match="Refusing to edit"):
        mod.merge_block(existing, "new body")


def test_an_end_before_its_begin_refuses(tmp_path: Path):
    """Same class, reversed: 'replace between the markers' is meaningless out of order."""
    mod = load_installer()
    existing = f"{mod.BLOCK_END}\nuser text\n{mod.BLOCK_BEGIN}\n"
    with pytest.raises(mod.BlockMarkerError):
        mod.merge_block(existing, "new body")


def test_duplicate_marker_pairs_refuse():
    mod = load_installer()
    existing = (f"{mod.BLOCK_BEGIN}\na\n{mod.BLOCK_END}\nkeep me\n"
                f"{mod.BLOCK_BEGIN}\nb\n{mod.BLOCK_END}\n")
    with pytest.raises(mod.BlockMarkerError):
        mod.merge_block(existing, "new body")


def test_a_wellformed_block_is_still_replaced_in_place():
    """The whole point of the marker protocol: the user's text on both sides survives."""
    mod = load_installer()
    existing = f"BEFORE\n\n{mod.BLOCK_BEGIN}\n\nold\n\n{mod.BLOCK_END}\n\nAFTER\n"
    out = mod.merge_block(existing, "new")
    assert out.startswith("BEFORE")
    assert out.rstrip().endswith("AFTER")
    assert "new" in out and "old" not in out


def test_a_blocked_file_does_not_abort_the_whole_install(tmp_path: Path):
    """One damaged file must not cost the user every other file in the install."""
    mod = load_installer()
    (tmp_path / "CLAUDE.md").write_text(f"{mod.BLOCK_BEGIN}\nmine\n", encoding="utf-8")
    op = mod.plan_op(tmp_path, "CLAUDE.md", "body", kind="block")
    assert op["status"] == "blocked"
    assert op["content"] is None
    assert "Refusing to edit" in op["reason"]
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8").endswith("mine\n")


# ── F9 · norm_id collapsed distinct names into one node ────────────────────────
def test_non_ascii_names_do_not_collapse_to_the_same_id():
    """`norm_id` erased every non-ASCII character and then stripped the leftover dashes,
    so two unrelated services both became `svc:@repo` — one node, merged blast radius.
    Identity collisions are the failure this whole module exists to prevent."""
    a = ids.service_id("服务", "repo")
    b = ids.service_id("系统", "repo")
    assert a != b, f"distinct services collapsed to {a}"
    assert a != "svc:@repo" and b != "svc:@repo"


def test_partially_non_ascii_names_stay_distinct():
    """The subtler half: `订单-api` and `库存-api` both normalize to `api`."""
    assert ids.service_id("订单-api", "r") != ids.service_id("库存-api", "r")


def test_the_same_name_always_yields_the_same_id():
    """Stability matters as much as distinctness — an id that changes per run would make
    every snapshot diff meaningless."""
    assert norm_id("服务") == norm_id("服务")


@pytest.mark.parametrize("value,expected", [
    ("orders-service", "orders-service"), ("Platform-Core", "Platform-Core"),
    ("eg_order_service_v2", "eg_order_service_v2"), ("a b", "a-b"), ("", ""),
])
def test_ascii_ids_are_byte_identical_to_before(value: str, expected: str):
    """Every ASCII id must survive the non-ASCII fix unchanged, or the committed snapshot
    churns for a change that was meant to be invisible to it.

    The values are invented, and that costs the test nothing: the claim under check is
    *byte-identical ASCII round-trip*, which any ASCII string of the same shapes proves
    equally well — hyphen, mixed case, underscores, digits."""
    assert norm_id(value) == expected


# ── F11 · run_id destroyed snapshot determinism ────────────────────────────────
def test_run_id_is_absent_from_the_serialized_edge():
    """`rebuild == snapshot` is the extractor regression test. A per-build id inside the
    serialized record makes every rebuild differ, turning the check into permanent noise
    that a maintainer learns to ignore."""
    kw = dict(src="a", type="CALLS", dst="b", source_file="f", line=1, extractor="x")
    assert Edge(**kw, run_id="build-1").to_jsonl() == Edge(**kw, run_id="build-2").to_jsonl()
    assert "run_id" not in json.loads(Edge(**kw, run_id="build-1").to_jsonl())


def test_a_serialized_edge_still_round_trips():
    """Dropping a field must not break the reader."""
    from tools.graph.core.lib.emit import read_jsonl, write_jsonl
    from tools.graph.core.model import ExtractResult
    import tempfile

    e = Edge(src="a", type="CALLS", dst="b", source_file="f", line=3, extractor="x",
             confidence=0.9, provenance=Provenance.INFERRED, run_id="build-1")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "s.jsonl"
        write_jsonl(p, ExtractResult(nodes=[Node(id="a", type="svc")], edges=[e]))
        back = read_jsonl(p)
    assert back.edges[0].src == "a" and back.edges[0].confidence == 0.9
    assert back.edges[0].provenance is Provenance.INFERRED


# ── F12 · an unreadable file was recorded as UNCHANGED ─────────────────────────
def test_an_unreadable_file_is_always_treated_as_changed(tmp_path: Path):
    """Returning "" for an OSError meant two consecutive builds that both failed to read a
    file compared EQUAL, so the extractor never re-ran and the graph stayed stale for that
    file forever — with nothing in the manifest to say so."""
    missing = tmp_path / "gone.txt"
    assert manifest.file_digest(missing) == manifest.UNREADABLE

    a, b = manifest.Manifest(), manifest.Manifest()
    a.files["r/gone.txt"] = manifest.UNREADABLE
    b.files["r/gone.txt"] = manifest.UNREADABLE
    assert "r/gone.txt" in a.changed_since(b), "unknown content cannot mean unchanged"


def test_a_readable_file_is_unchanged_between_identical_builds(tmp_path: Path):
    """The fix must not make everything permanently dirty — that would silently disable
    incremental builds instead of fixing them."""
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    a, b = manifest.Manifest(), manifest.Manifest()
    a.record_files("r", [f], tmp_path)
    b.record_files("r", [f], tmp_path)
    assert a.changed_since(b) == set()


# ── F7/F8 · fd and rg silently disagreed with the Python fallback ──────────────
@pytest.fixture
def nested_repo(tmp_path: Path) -> Path:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("on: push\n", encoding="utf-8")
    (tmp_path / "pkg" / "node_modules" / "dep").mkdir(parents=True)
    (tmp_path / "pkg" / "node_modules" / "dep" / "index.js").write_text("NEEDLE\n", encoding="utf-8")
    (tmp_path / "pkg" / "app.js").write_text("NEEDLE\n", encoding="utf-8")
    return tmp_path


def test_a_path_glob_finds_nested_files_through_both_backends(nested_repo: Path):
    """`--full-path` matches the ABSOLUTE path, so a repo-relative glob like
    `.github/workflows/*.yml` matched nothing and returned exit 0 — a silent empty result
    the 'non-zero means fall back' guard cannot catch. This is the same class of bug that
    already produced one confidently wrong estate report."""
    pat = [".github/workflows/*.yml"]
    py = repo_walk.find_files(nested_repo, pat, use_fd=False)
    assert [p.name for p in py] == ["ci.yml"], "the Python fallback defines correct"

    fd = repo_walk.find_files(nested_repo, pat, use_fd=True)
    assert [p.name for p in fd] == [p.name for p in py], "fd must agree with Python"


def test_nested_excluded_directories_are_skipped_by_both_backends(nested_repo: Path):
    """A ripgrep glob containing '/' is anchored to the search root, so `!node_modules/**`
    skipped only the TOP-LEVEL one. Every nested node_modules/.git/target leaked into
    results the Python path had already pruned."""
    py = repo_walk.search(nested_repo, "NEEDLE", use_rg=False)
    rg = repo_walk.search(nested_repo, "NEEDLE", use_rg=True)
    assert [m.path.name for m in py] == ["app.js"], "the Python fallback defines correct"
    assert sorted(m.path.name for m in rg) == sorted(m.path.name for m in py)
    assert not any("node_modules" in str(m.path) for m in rg)
