"""`discover_components()` — everything the workspace config deliberately does NOT declare.

The design rule item 69 rests on is "declare only what detection cannot reach". This
is the other half of that bargain: if discovery is weak, the developer ends up hand-
declaring components after all, and the config file grows into the estate inventory it
was designed not to be.

Fake workspaces are built in `tmp_path` with invented names. A `.git` is created as a
plain directory rather than by running `git init` — discovery reads the filesystem and
never shells out, so the test should not either. That also keeps it fast enough to
parametrize freely.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_installer():
    """Import sdlc-init.py despite the hyphen (not a valid module name)."""
    spec = importlib.util.spec_from_file_location("sdlc_init", ROOT / "sdlc-init.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init"] = mod
    spec.loader.exec_module(mod)
    return mod


si = load_installer()


def make_repo(parent: Path, name: str, *, files: dict | None = None,
              shallow: bool = False, git: str = "dir") -> Path:
    """A directory that looks like a repository to the filesystem."""
    repo = parent / name
    repo.mkdir(parents=True, exist_ok=True)
    if git == "dir":
        git_dir = repo / ".git"
        git_dir.mkdir()
    elif git == "file":
        # A worktree or submodule: `.git` is a FILE pointing elsewhere.
        git_dir = parent / f".git-store-{name}"
        git_dir.mkdir()
        (repo / ".git").write_text(f"gitdir: {git_dir.as_posix()}\n", encoding="utf-8")
    else:  # not a repo at all
        git_dir = None
    if git_dir is not None and shallow:
        (git_dir / "shallow").write_text("deadbeef\n", encoding="utf-8")
    for rel, body in (files or {}).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return repo


def ws(root: Path, **kw) -> dict:
    """A read_workspace() result, assembled directly so these tests exercise
    discovery rather than re-testing the reader."""
    return {"schema_version": 1, "config_path": root.parent / "sdlc-workspace.toml",
            "root": root, "name": kw.get("name", ""),
            "include": kw.get("include", []), "exclude": kw.get("exclude", []),
            "overrides": kw.get("overrides", {}), "canonical": kw.get("canonical", [])}


def override(name: str, **kw) -> dict:
    """An override block shaped the way read_workspace() emits one."""
    return {"name": name, "role": kw.pop("role", si.DEFAULT_ROLE),
            "vcs": kw.pop("vcs", {}), **kw}


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    d = tmp_path / "workspaces"
    d.mkdir()
    return d


# ── the default: immediate children carrying a .git ───────────────────────────
def test_immediate_children_with_a_git_are_the_default(root: Path):
    make_repo(root, "orders", files={"pom.xml": "<project/>"})
    make_repo(root, "billing", files={"go.mod": "module billing\n"})
    (root / "notes").mkdir()          # no .git — not a component

    report = si.discover_components(ws(root))
    assert [c["name"] for c in report["components"]] == ["billing", "orders"]
    assert report["skipped"] == ["notes"]


def test_detection_fills_in_what_the_config_does_not_declare(root: Path):
    """The whole bargain: the developer declared `root` and nothing else, and every
    per-component value below came from the detectors the single-repo path uses."""
    make_repo(root, "orders", files={"pom.xml": "<project/>"})
    comp = si.discover_components(ws(root))["components"][0]
    assert comp["stack"] == "java-maven"
    assert comp["test_command"] == "mvn -q test"
    assert comp["role"] == "authoritative"
    assert comp["path"] == "orders"
    assert comp["vcs"]["deny_raw_git"] is False


def test_the_component_list_is_ordered_deterministically(root: Path):
    for name in ("zeta", "alpha", "mid"):
        make_repo(root, name)
    names = [c["name"] for c in si.discover_components(ws(root))["components"]]
    assert names == sorted(names)


# ── a .git FILE is a repository too ───────────────────────────────────────────
def test_a_git_file_counts_as_a_repository(root: Path):
    """A worktree and a submodule both carry a `.git` FILE, not a directory.

    Testing `is_dir()` alone would silently drop every submodule in the workspace,
    and the symptom is simply that the repo is missing — never an error.
    """
    make_repo(root, "vendored", git="file")
    assert [c["name"] for c in si.discover_components(ws(root))["components"]] == ["vendored"]


def test_a_git_file_pointing_nowhere_useful_is_not_a_component(root: Path):
    repo = root / "broken"
    repo.mkdir()
    (repo / ".git").write_text("this is not a gitdir pointer\n", encoding="utf-8")
    make_repo(root, "orders")
    report = si.discover_components(ws(root))
    assert [c["name"] for c in report["components"]] == ["orders"]
    assert report["skipped"] == ["broken"]


# ── shallow clones are reported, never filtered ───────────────────────────────
@pytest.mark.parametrize("git", ["dir", "file"])
def test_a_shallow_clone_is_flagged_and_still_included(root: Path, git: str):
    """It is readable but un-diffable, so half the harness works on it and half does
    not. Dropping it would hide that; including it silently would mislead. The only
    honest option is to include it and say so."""
    make_repo(root, "trimmed", shallow=True, git=git)
    comp = si.discover_components(ws(root))["components"][0]
    assert comp["name"] == "trimmed" and comp["shallow"] is True


def test_an_ordinary_clone_is_not_flagged_shallow(root: Path):
    make_repo(root, "orders")
    assert si.discover_components(ws(root))["components"][0]["shallow"] is False


# ── include / exclude ─────────────────────────────────────────────────────────
def test_include_is_a_whitelist_and_reaches_deeper_than_one_level(root: Path):
    make_repo(root / "services", "orders")
    make_repo(root / "services", "billing")
    make_repo(root, "top-level-thing")

    report = si.discover_components(ws(root, include=["services/*"]))
    assert [c["name"] for c in report["components"]] == ["billing", "orders"]
    assert [c["path"] for c in report["components"]] == ["services/billing", "services/orders"]


def test_exclude_applies_after_include(root: Path):
    make_repo(root / "services", "orders")
    make_repo(root / "services", "scratch")
    report = si.discover_components(ws(root, include=["services/*"], exclude=["services/scratch"]))
    assert [c["name"] for c in report["components"]] == ["orders"]


def test_exclude_works_against_the_default_scan_too(root: Path):
    make_repo(root, "orders")
    make_repo(root, "personal-notes")
    report = si.discover_components(ws(root, exclude=["personal-notes"]))
    assert [c["name"] for c in report["components"]] == ["orders"]


def test_include_and_exclude_share_one_matching_rule(root: Path):
    """Two different glob semantics for two adjacent fields would force the developer
    to know which field they were in to predict what a pattern meant."""
    make_repo(root / "a", "one")
    make_repo(root / "b", "two")
    assert [c["name"] for c in si.discover_components(ws(root, include=["*/*"]))["components"]] \
        == ["one", "two"]
    assert [c["name"] for c in si.discover_components(
        ws(root, include=["*/*"], exclude=["*/two"]))["components"]] == ["one"]


def test_overlapping_include_patterns_do_not_duplicate_a_component(root: Path):
    make_repo(root / "services", "orders")
    report = si.discover_components(ws(root, include=["services/*", "services/orders"]))
    assert [c["name"] for c in report["components"]] == ["orders"]


# ── the empty workspace ───────────────────────────────────────────────────────
def test_zero_components_is_a_hard_error_naming_what_was_scanned(root: Path):
    """The single most valuable check here. A too-narrow `include`, a typo, or a
    workspace laid out one level deeper than assumed all produce zero components —
    and an empty workspace is indistinguishable from a clean one everywhere
    downstream."""
    (root / "notes").mkdir()
    with pytest.raises(si.WorkspaceError, match="indistinguishable from a clean one"):
        si.discover_components(ws(root))


def test_the_empty_error_reports_the_directories_it_rejected(root: Path):
    """Naming them is what turns "nothing found" into "found these, none had a .git",
    which is the difference between a dead end and an obvious fix."""
    (root / "notes").mkdir()
    (root / "scratch").mkdir()
    with pytest.raises(si.WorkspaceError, match="notes, scratch"):
        si.discover_components(ws(root))


def test_an_include_that_matches_nothing_reports_it_as_such(root: Path):
    make_repo(root, "orders")
    with pytest.raises(si.WorkspaceError, match="the patterns in `include`"):
        si.discover_components(ws(root, include=["services/*"]))


# ── overrides ─────────────────────────────────────────────────────────────────
def test_an_override_wins_over_detection(root: Path):
    make_repo(root, "orders", files={"pom.xml": "<project/>"})
    report = si.discover_components(
        ws(root, overrides={"orders": override("orders", test_command="mvn -q verify")}))
    assert report["components"][0]["test_command"] == "mvn -q verify"


def test_an_unmentioned_field_keeps_what_detection_found(root: Path):
    make_repo(root, "orders", files={"pom.xml": "<project/>"})
    report = si.discover_components(
        ws(root, overrides={"orders": override("orders", role="legacy")}))
    comp = report["components"][0]
    assert comp["role"] == "legacy"
    assert comp["test_command"] == "mvn -q test", "a role override erased a detected command"


def test_a_blank_override_value_means_inherit_not_erase(root: Path):
    """The template's example block ships with blank values. A developer who
    uncomments it wholesale must not thereby wipe every detected command — that would
    turn a documentation-reading gesture into a silent, workspace-wide regression."""
    make_repo(root, "orders", files={"pom.xml": "<project/>"})
    report = si.discover_components(ws(root, overrides={
        "orders": override("orders", test_command="", build_command="   ", stack="")}))
    comp = report["components"][0]
    assert comp["test_command"] == "mvn -q test"
    assert comp["stack"] == "java-maven"


def test_an_override_matching_nothing_is_a_hard_error(root: Path):
    """The quietest possible failure otherwise: a deliberate instruction, ignored,
    with no symptom except behaviour that looks like it was never honoured."""
    make_repo(root, "orders")
    with pytest.raises(si.WorkspaceError, match="matches no discovered component"):
        si.discover_components(ws(root, overrides={"odrers": override("odrers")}))


def test_the_unmatched_override_error_lists_what_was_discovered(root: Path):
    make_repo(root, "orders")
    make_repo(root, "billing")
    with pytest.raises(si.WorkspaceError, match="billing, orders"):
        si.discover_components(ws(root, overrides={"typo": override("typo")}))


def test_a_component_can_be_renamed_by_giving_its_path(root: Path):
    """`path` says which directory you mean; `name` says what to call it. Without
    this, a rename would be indistinguishable from a typo and rejected as unknown."""
    make_repo(root, "orders-service", files={"pom.xml": "<project/>"})
    report = si.discover_components(
        ws(root, overrides={"orders": override("orders", path="orders-service")}))
    comp = report["components"][0]
    assert comp["name"] == "orders" and comp["path"] == "orders-service"


def test_an_override_path_matching_nothing_is_a_hard_error(root: Path):
    make_repo(root, "orders")
    with pytest.raises(si.WorkspaceError, match="not a discovered component"):
        si.discover_components(ws(root, overrides={"x": override("x", path="services/nope")}))


def test_a_rename_that_collides_with_an_existing_component_is_a_hard_error(root: Path):
    """Every edge in the graph is keyed on the component name, so a collision would
    silently merge two components' facts into one node.

    Two override blocks cannot collide — the reader already rejects duplicate block
    names. The reachable case is a rename landing on a name a *different*, un-
    overridden directory already has, which nothing before this point can see.
    """
    make_repo(root, "orders")
    make_repo(root, "orders-service")
    with pytest.raises(si.WorkspaceError, match="duplicate component names"):
        si.discover_components(ws(root, overrides={
            "orders": override("orders", path="orders-service")}))


def test_a_vcs_override_merges_key_by_key(root: Path):
    """A developer setting only `deny_raw_git` must not thereby erase a detected
    `commit_command` they never mentioned."""
    make_repo(root, "orders", files={"checkin-push.sh": "#!/bin/sh\n"})
    report = si.discover_components(
        ws(root, overrides={"orders": override("orders", vcs={"deny_raw_git": True})}))
    vcs = report["components"][0]["vcs"]
    assert vcs["deny_raw_git"] is True
    assert vcs["commit_command"], "the detected commit command was erased by a partial override"


def test_known_failures_survive_the_merge(root: Path):
    make_repo(root, "orders")
    report = si.discover_components(ws(root, overrides={
        "orders": override("orders", known_failures=["test_flaky_thing"])}))
    assert report["components"][0]["known_failures"] == ["test_flaky_thing"]


# ── canonical blocks ──────────────────────────────────────────────────────────
def test_a_canonical_owner_must_be_a_discovered_component(root: Path):
    """This check is impossible in the reader — nothing has been discovered yet. It
    belongs here, at the first moment the claim can be tested at all."""
    make_repo(root, "orders")
    with pytest.raises(si.WorkspaceError, match="not a discovered component"):
        si.discover_components(ws(root, canonical=[{"service": "orders", "owner": "billing"}]))


def test_a_canonical_owner_that_exists_is_accepted(root: Path):
    make_repo(root, "orders")
    make_repo(root, "orders-legacy")
    report = si.discover_components(
        ws(root, canonical=[{"service": "orders", "owner": "orders"}]))
    assert len(report["components"]) == 2


def test_a_canonical_owner_is_checked_against_the_RENAMED_name(root: Path):
    """Ordering matters: overrides are applied before this check, so an owner must
    name what the component is finally called, not what its directory was."""
    make_repo(root, "orders-service")
    report = si.discover_components(ws(
        root,
        overrides={"orders": override("orders", path="orders-service")},
        canonical=[{"service": "orders", "owner": "orders"}]))
    assert report["components"][0]["name"] == "orders"
