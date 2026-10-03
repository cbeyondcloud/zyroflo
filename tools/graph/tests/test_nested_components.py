"""T591: a component reads its own tree, not its declared children's (batch 15a).

A declared child's files were read by its parent too - and by the root component, which read
every file - so one file made claims and rows from two readings (plans/batch-15a-design-
note.md, T591). A component's files now exclude every file under the path of a component
declared inside it, by whole path segments, each file going to its most specific declared
child, judged from the WHOLE workspace's component list so a narrowed build reads exactly
what a full build assigns. Maven and gradle, whose own tools resolve modules across
directories, declare that reach (`READS_NESTED`) and keep reading a declared child's build
files inside the parent's stamp (the owner's ruling, 2026-10-03); npm follows the rule
strictly (2026-10-03).

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tools.graph.build as B  # noqa: E402
from tools.graph.tests._estate import build, compose, lay_down, read, spawn, svc  # noqa: E402

BUILD_DOT = compose(svc("web", ".", None))
EDGE_IMAGE = compose(svc("edge", None, None, image="nginx:1"))

AGGREGATOR = """<project>
  <groupId>com.acme</groupId><artifactId>platform</artifactId><version>1.0.0</version>
  <packaging>pom</packaging>
  <modules><module>svc-a</module></modules>
</project>
"""
CHILD_POM = """<project>
  <parent><groupId>com.acme</groupId><artifactId>platform</artifactId>
    <version>1.0.0</version></parent>
  <artifactId>svc-a</artifactId>
</project>
"""


def deploys(built, component: str | None = None) -> list[str]:
    """The deployment file of every `DG_DEPLOYS` edge, optionally from one component."""
    return sorted(e["src"].removeprefix("deploy:") for e in built.deploys
                  if component is None or e["src"].startswith(f"deploy:{component}/"))


def triples(built) -> set[tuple[str, str, str]]:
    """Every edge as (src, type, dst); `graph.jsonl`'s header record is not an edge."""
    return {(e["src"], e["type"], e["dst"]) for e in built.edges if e.get("kind") == "edge"}


# ── the rule ──────────────────────────────────────────────────────────────────


def test_a_childs_file_is_read_by_the_child_alone(tmp_path):
    built = build(tmp_path, {"platform": {"docker-compose.yml": EDGE_IMAGE,
                                          "svc-a/docker-compose.yml": BUILD_DOT},
                             "platform/svc-a": {}})
    assert deploys(built) == ["platform/docker-compose.yml", "svc-a/docker-compose.yml"]


def test_the_root_component_reads_only_what_lies_outside_its_children(tmp_path):
    """Review focus 1: a monorepo whose root is `.`, built end to end - each child's facts
    appear once, from the child."""
    built = build(tmp_path, {".": {"docker-compose.yml": EDGE_IMAGE,
                                   "svc-a/docker-compose.yml": BUILD_DOT},
                             "svc-a": {}, "__names__": {".": "mono"}})
    assert deploys(built) == ["mono/docker-compose.yml", "svc-a/docker-compose.yml"]


def test_each_level_reads_only_its_own_tree(tmp_path):
    """Three levels: `a` holds `b` holds `c`; a file two levels down is `c`'s alone, the most
    specific declared component holding it."""
    built = build(tmp_path, {"a": {"own/docker-compose.yml": EDGE_IMAGE,
                                   "b/mine/docker-compose.yml": EDGE_IMAGE,
                                   "b/c/docker-compose.yml": BUILD_DOT},
                             "a/b": {}, "a/b/c": {}})
    assert deploys(built) == ["a/own/docker-compose.yml", "b/mine/docker-compose.yml",
                              "c/docker-compose.yml"]


def test_a_sibling_named_like_a_child_stays_its_parents(tmp_path):
    """D15a-29: the file-side test is by whole path segments. A bare `startswith` - the trap
    T530 recorded in this module - would leave `svc-a-legacy/` and `svc-a.bak/` read by no
    one, with no row."""
    built = build(tmp_path, {"platform": {"svc-a/docker-compose.yml": BUILD_DOT,
                                          "svc-a-legacy/docker-compose.yml": EDGE_IMAGE,
                                          "svc-a.bak/docker-compose.yml": EDGE_IMAGE},
                             "platform/svc-a": {}})
    assert deploys(built, "platform") == ["platform/svc-a-legacy/docker-compose.yml",
                                          "platform/svc-a.bak/docker-compose.yml"]
    assert deploys(built, "svc-a") == ["svc-a/docker-compose.yml"]


def test_an_off_case_file_in_a_child_is_reported_by_the_child_alone(tmp_path):
    """The walk's off-case near misses are excluded as its files are: `POM.XML` under a
    declared child is named once, by the child, where `main` named it from both readings."""
    built = build(tmp_path, {"platform": {"docker-compose.yml": EDGE_IMAGE,
                                          "svc-a/POM.XML": AGGREGATOR},
                             "platform/svc-a": {}})
    assert sorted((r["component"], r["source_file"]) for r in built.kinds("off-case-input")) == [
        ("svc-a", "POM.XML")]


def test_nested_paths_with_spaces(tmp_path):
    """Review focus 2: component paths with spaces in them."""
    built = build(tmp_path, {"my platform": {"docker-compose.yml": EDGE_IMAGE,
                                             "svc a/docker-compose.yml": BUILD_DOT},
                             "my platform/svc a": {},
                             "__names__": {"my platform": "platform",
                                           "my platform/svc a": "svc-a"}})
    assert deploys(built) == ["platform/docker-compose.yml", "svc-a/docker-compose.yml"]


def test_a_child_declared_before_its_parent(tmp_path):
    """Review focus 3: the exclusion does not depend on declaration order."""
    built = build(tmp_path, {"platform/svc-a": {},
                             "platform": {"docker-compose.yml": EDGE_IMAGE,
                                          "svc-a/docker-compose.yml": BUILD_DOT}})
    assert deploys(built) == ["platform/docker-compose.yml", "svc-a/docker-compose.yml"]


# ── by declaration, not by selection (D15a-1) ────────────────────────────────


def test_a_narrowed_build_reads_what_a_full_build_assigns(tmp_path):
    """D15a-1, and review focus 4: the exclusion comes from the WHOLE workspace's component
    list, so naming only the parent, or only the child, reads exactly what a full build
    gives each."""
    estate = {"platform": {"docker-compose.yml": EDGE_IMAGE,
                           "svc-a/docker-compose.yml": BUILD_DOT},
              "platform/svc-a": {}}
    full = build(tmp_path / "full", estate)
    parent = build(tmp_path / "parent", estate, "--components", "platform")
    child = build(tmp_path / "child", estate, "--components", "svc-a")
    assert deploys(parent) == deploys(full, "platform") == ["platform/docker-compose.yml"]
    assert deploys(child) == deploys(full, "svc-a") == ["svc-a/docker-compose.yml"]


# ── reach: the build tools that span a parent and its child (ruling, 2026-10-03) ──


@pytest.mark.parametrize("value", ["false", "no", 0.1, 1, 0, None])
def test_a_reach_declaration_must_be_a_real_bool(value):
    """D15a-33: `READS_NESTED = "false"` is truthy, and a mistyped declaration would widen
    an extractor's inputs and stamps in silence - refused at registration, as a bare-string
    `INPUT_GLOBS` is."""
    class Probe:
        name = "probe"
        INPUT_GLOBS = ("*.probe",)
        READS_NESTED = value

        def extract(self, ctx):
            raise AssertionError("never run")

    with pytest.raises(B.BuildError, match="READS_NESTED"):
        B._validate(Probe(), "probe-module")


def test_maven_and_gradle_declare_reach_and_no_other_extractor_does():
    assert sorted(r.name for r in B.discover_extractors() if r.reads_nested) == [
        "gradle-modules", "maven-modules"]


def test_a_maven_aggregator_still_resolves_its_childs_module(tmp_path):
    """Ruling 4 (D15a-2): maven reads each `<module>`'s pom by path, so the parent keeps the
    child's build files in its reading - and its stamp - and its facts stay as on `main`."""
    built = build(tmp_path, {"platform": {"pom.xml": AGGREGATOR, "svc-a/pom.xml": CHILD_POM},
                             "platform/svc-a": {}})
    built_from = {(s, d) for s, t, d in triples(built) if t == "DG_BUILT_FROM"}
    assert ("svc:platform@platform", "mod:com.acme:svc-a") in built_from
    assert ("svc:svc-a@svc-a", "mod:com.acme:svc-a") in built_from


def test_a_gradle_build_still_resolves_its_childs_subproject(tmp_path):
    """Ruling 4: `settings.gradle` includes the subproject and gives it its group, so the
    parent keeps the child's build files and its facts stay as on `main`. The child's own
    reading, with no group in its tree, still names the module after its alias: T614's
    second identity, pinned as `main`'s until that row."""
    built = build(tmp_path, {"platform": {
        "settings.gradle": "rootProject.name = 'platform'\ninclude ':svc-a'\n",
        "build.gradle": "allprojects {\n    group = 'com.acme'\n}\n",
        "svc-a/build.gradle": "dependencies {\n    implementation 'org.x:lib:1.0'\n}\n"},
        "platform/svc-a": {}})
    edges = triples(built)
    assert {("svc:platform@platform", "DG_BUILT_FROM", "mod:com.acme:svc-a"),
            ("svc:platform@platform", "DG_DEPENDS_ON", "mod:org.x:lib"),
            ("svc:svc-a@svc-a", "DG_BUILT_FROM", "mod:svc-a:svc-a")} <= edges


def test_a_childs_build_file_edit_marks_its_parent_stale_and_another_edit_does_not(tmp_path):
    """Freshness follows the files each reading reads. A child's `pom.xml` is in its parent's
    maven reading by reach, so editing it makes both stale; a child's other files are no
    longer the parent's, so editing one makes the child alone stale (on `main`, both)."""
    props = "svc-a/src/main/resources/application.properties"
    estate = {"platform": {"pom.xml": AGGREGATOR, "svc-a/pom.xml": CHILD_POM, props: "a=1\n"},
              "platform/svc-a": {}}
    toml = lay_down(tmp_path, estate)
    assert build(tmp_path, None, "--publish-snapshot").code == 0
    platform = tmp_path / "ws" / "platform"

    (platform / props).write_text("a=2\n", encoding="utf-8")
    assert sorted(B.check_freshness(toml)["freshness"]["stale"]) == ["svc-a"]
    (platform / props).write_text("a=1\n", encoding="utf-8")

    (platform / "svc-a" / "pom.xml").write_text(CHILD_POM.replace("svc-a</artifactId>",
                                                                  "svc-b</artifactId>"),
                                                encoding="utf-8")
    assert sorted(B.check_freshness(toml)["freshness"]["stale"]) == ["platform", "svc-a"]


# ── npm follows the rule strictly (the owner's ruling, 2026-10-03; D15a-16, D15a-28) ──


@pytest.mark.parametrize("depends", [True, False], ids=["root-depends", "ordinary-root"])
def test_a_workspace_root_no_longer_builds_its_declared_members_module(tmp_path, depends):
    """With a workspace member declared as a component, the root no longer reads the member's
    manifest, so its `DG_BUILT_FROM` to the member's module goes. Where the root depends on
    the member it now depends on it; where it does not, only the member's own build edge
    remains. On `main` the root claims to build it, with no depends-on edge."""
    root = {"name": "@acme/root", "private": True, "workspaces": ["svc-a"],
            **({"dependencies": {"@acme/child": "1.0.0"}} if depends else {})}
    member = {"name": "@acme/child", "version": "1.0.0"}
    built = build(tmp_path, {"platform": {"package.json": json.dumps(root),
                                          "svc-a/package.json": json.dumps(member)},
                             "platform/svc-a": {}})
    module = next(d for s, t, d in triples(built)
                  if s == "svc:svc-a@svc-a" and t == "DG_BUILT_FROM")
    from_root = sorted((t, d) for s, t, d in triples(built)
                       if s == "svc:platform@platform" and d == module)
    assert from_root == ([("DG_DEPENDS_ON", module)] if depends else [])


# ── a child spelt in another case than its directory (D15a-19) ───────────────


def test_a_child_spelt_in_another_case_than_its_directory(tmp_path):
    """By T531's exact rule a child declared as `platform/SVC-A` over the directory
    `platform/svc-a` is not inside its parent, everywhere. What follows differs by
    filesystem, and both halves are asserted: on Windows the declared path resolves, so the
    parent and the case-twin both read the file, on two nodes, as on `main`; on a
    case-sensitive filesystem the declared path does not exist, so the loader refuses it."""
    lay_down(tmp_path, {"platform": {"svc-a/docker-compose.yml": BUILD_DOT},
                        "__components__": [("platform", "platform"),
                                           ("svca", "platform/SVC-A")]})
    run = spawn(tmp_path)
    if os.name == "nt":
        assert run.returncode == 0, run.stdout + run.stderr
        assert deploys(read(0, tmp_path / "out")) == ["platform/svc-a/docker-compose.yml",
                                                      "svca/docker-compose.yml"]
    else:
        assert run.returncode == 2, run.stdout + run.stderr
        assert "does not exist" in run.stdout + run.stderr
