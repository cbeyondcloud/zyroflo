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
import subprocess
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


def test_maven_and_gradle_declare_reach_and_no_other_extractor_does():
    """Only the two readers whose own tools resolve modules across directories declare
    reach. A declaration that is not a real bool is refused at registration, with the other
    malformed extractors (`test_build.py`, D15a-33, review B15a-9)."""
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
    reading, with no group in its tree, takes the build's group from above it since T614, so
    both readings name one module (on `main` the child's named it after its alias, a second
    node)."""
    built = build(tmp_path, {"platform": {
        "settings.gradle": "rootProject.name = 'platform'\ninclude ':svc-a'\n",
        "build.gradle": "allprojects {\n    group = 'com.acme'\n}\n",
        "svc-a/build.gradle": "dependencies {\n    implementation 'org.x:lib:1.0'\n}\n"},
        "platform/svc-a": {}})
    edges = triples(built)
    assert {("svc:platform@platform", "DG_BUILT_FROM", "mod:com.acme:svc-a"),
            ("svc:platform@platform", "DG_DEPENDS_ON", "mod:org.x:lib"),
            ("svc:svc-a@svc-a", "DG_BUILT_FROM", "mod:com.acme:svc-a")} <= edges
    assert not any(dst == "mod:svc-a:svc-a" for _, _, dst in edges)


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


# ── a component that reads nothing by design (D15a-5, -13, -24, -25, -30, -35, -38, -41) ──

ROOT = Path(__file__).resolve().parents[3]
BUILD_PY = ROOT / "tools" / "graph" / "build.py"
CHILD_ONLY = {"platform": {"svc-a/docker-compose.yml": BUILD_DOT}, "platform/svc-a": {}}


def run_build(where: Path, *flags: str, out: str = "out") -> subprocess.CompletedProcess:
    """The build as a PROCESS, without `--empty-ok` unless asked: an exit code is what a
    person gets, and only a spawn shows it."""
    return subprocess.run(
        [sys.executable, str(BUILD_PY), "--workspace", str(where / "ws" / "workspace.toml"),
         "--out", str(where / out), "--run-id", "fixed", *flags],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))


def report_of(where: Path, out: str = "out") -> dict:
    return json.loads((where / out / "build_report.json").read_text(encoding="utf-8"))


BY_DESIGN = ("platform reads nothing by design: every file an extractor would read there "
             "belongs to its declared children (svc-a) - build them with --components svc-a")
#: Batch 15b (D15b-57): a `--components` build that leaves a component out - 15a's own
#: remedy included - is narrowed, and says so after the by-design line. The exact text.
NARROWED_ONE = ("this run was narrowed (`--components` left out 1 component(s)), so it "
                "judged none of the five rows what it could not see could change: "
                "`canonical-unmatched`, `apart-unmatched`, `deploys-unmatched`, "
                "`deploys-not-a-consumer` and `deploys-contradicted`, and none of the env "
                "ranking's cross-component `superseded-declaration` rows, judged in a whole run "
                "alone; it proposed no image join, named no image unreadable and compared no "
                "held chart's image, since a "
                "build it did not read may produce the image; it followed no `[[deploys]]` "
                "block whose `onto` it does not hold, or whose images it could not judge; and an "
                "`[[apart]]` refusal, judged over fewer claims, may move a chart onto its "
                "component's own node where a whole build holds it back, because the package "
                "that claims that node was not read. To judge them: build the whole workspace, "
                "with no `--components` or `--only`.")


@pytest.mark.parametrize("flags", [(), ("--empty-ok",)], ids=["bare", "empty-ok"])
def test_a_build_of_only_a_children_only_parent_exits_0_by_design(tmp_path, flags):
    """D15a-13: a parent holding only its child's files reads nothing BY DESIGN. Built
    alone it gets its own status, not `no-activation` (a misconfiguration) nor
    `all-failed`, and exits 0 in both spellings, with one plain line naming its children."""
    lay_down(tmp_path, CHILD_ONLY)
    run = run_build(tmp_path, "--components", "platform", *flags)
    assert run.returncode == 0, run.stdout + run.stderr
    report = report_of(tmp_path)
    assert report["status"] == "read-by-children"
    assert report["read_by_children"] == {"platform": ["svc-a"]}
    assert report["components_unclaimed"] == []
    # The line travels in the report as well as on stdout (review B15a-3): the printer is
    # handed the report alone.
    assert report["notes"] == [BY_DESIGN, NARROWED_ONE]
    assert BY_DESIGN in run.stdout and "!! " + BY_DESIGN not in run.stdout


def test_the_by_design_lines_remedy_reads_the_childs_claims(tmp_path):
    """Following the line's own way on is accepted, and reads the child's claim - an
    ordinary build, whose report carries only the narrowed line, since 15a's remedy is
    itself a narrowed build (batch 15b, D15b-57)."""
    lay_down(tmp_path, CHILD_ONLY)
    run = run_build(tmp_path, "--components", "svc-a")
    assert run.returncode == 0, run.stdout + run.stderr
    assert deploys(read(0, tmp_path / "out")) == ["svc-a/docker-compose.yml"]
    report = report_of(tmp_path)
    assert (report["status"], report["notes"]) == ("ok", [NARROWED_ONE])


def test_with_two_children_the_remedy_is_one_comma_joined_flag(tmp_path):
    """D15a-30: `--components` is comma-separated, so the remedy names `b,c`, and running
    it reads both children's claims."""
    lay_down(tmp_path, {"a": {"b/docker-compose.yml": BUILD_DOT,
                              "c/docker-compose.yml": compose(svc("api", ".", None))},
                        "a/b": {}, "a/c": {}})
    run = run_build(tmp_path, "--components", "a")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "belongs to its declared children (b, c) - build them with --components b,c" \
        in run.stdout
    again = run_build(tmp_path, "--components", "b,c", out="again")
    assert again.returncode == 0, again.stdout + again.stderr
    assert deploys(read(0, tmp_path / "again")) == ["b/docker-compose.yml",
                                                    "c/docker-compose.yml"]


def test_a_file_two_levels_down_is_left_to_its_most_specific_child(tmp_path):
    """D15a-38: `a` and `b` both hold `b/c`'s file, and both name `c`, its most specific
    declared component; following that reads it."""
    lay_down(tmp_path, {"a": {"b/c/docker-compose.yml": BUILD_DOT}, "a/b": {}, "a/b/c": {}})
    run = run_build(tmp_path, "--components", "a,b")
    assert run.returncode == 0, run.stdout + run.stderr
    assert report_of(tmp_path)["read_by_children"] == {"a": ["c"], "b": ["c"]}
    again = run_build(tmp_path, "--components", "c", out="again")
    assert again.returncode == 0 and deploys(read(0, tmp_path / "again")) == [
        "c/docker-compose.yml"]


def test_a_full_build_names_the_by_design_parent_and_no_coverage_hole(tmp_path):
    lay_down(tmp_path, CHILD_ONLY)
    run = run_build(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    report = report_of(tmp_path)
    assert (report["status"], report["components_unclaimed"]) == ("ok", [])
    assert BY_DESIGN in run.stdout and "coverage hole" not in run.stdout


@pytest.mark.parametrize("estate, flags, code, status", [
    # 1: the parent's own pom.xml is its own, so an --only run that skips maven is a
    #    narrowing, not by design (D15a-24).
    ({"platform": {"pom.xml": AGGREGATOR, "svc-a/docker-compose.yml": BUILD_DOT},
      "platform/svc-a": {}}, ("--only", "compose-services"), 2, "no-activation"),
    # 2: only a child's pom.xml, which maven reads FOR the parent by reach.
    ({"platform": {"svc-a/pom.xml": CHILD_POM}, "platform/svc-a": {}}, (), 0, "ok"),
    # 2 under --only (D15a-35): still not by design, so main's verdict.
    ({"platform": {"svc-a/pom.xml": CHILD_POM}, "platform/svc-a": {}},
     ("--only", "compose-services"), 2, "no-activation"),
    # 4: a parent that reads its own compose file beside its child's.
    ({"platform": {"docker-compose.yml": EDGE_IMAGE, "svc-a/docker-compose.yml": BUILD_DOT},
      "platform/svc-a": {}}, (), 0, "ok"),
], ids=["own-pom-under-only", "reach", "reach-under-only", "own-compose"])
def test_the_by_design_verdict_needs_both_halves(tmp_path, estate, flags, code, status):
    """The predicate (D15a-24, D15a-35): no REGISTERED extractor claims the parent by a
    file it reads - a child's counting as its own where the extractor declares reach - and
    nothing claimed it in this run. Each cell is a false line under a weaker reading."""
    lay_down(tmp_path, estate)
    run = run_build(tmp_path, "--components", "platform", *flags)
    assert run.returncode == code, run.stdout + run.stderr
    report = report_of(tmp_path)
    assert (report["status"], report["read_by_children"]) == (status, {})
    assert "reads nothing by design" not in run.stdout


def test_an_only_run_that_reads_none_of_the_childrens_files_says_so(tmp_path):
    """D15a-41: the verdict is registry-relative, so `--only maven-modules` over a parent
    whose child holds only a compose file still reads nothing by design (exit 0, where
    `main` said `no-activation`), and the line says that this run's `--only` reads none of
    the children's files - so the remedy, followed without it, reads the claim."""
    lay_down(tmp_path, CHILD_ONLY)
    run = run_build(tmp_path, "--only", "maven-modules", "--components", "platform")
    assert run.returncode == 0, run.stdout + run.stderr
    assert (BY_DESIGN + " (this run's --only selects no extractor that reads their files)"
            in run.stdout)
    twin = run_build(tmp_path, "--only", "compose-services", "--components", "platform",
                     out="twin")
    assert twin.returncode == 0 and BY_DESIGN in twin.stdout
    assert "this run's --only" not in twin.stdout


@pytest.mark.parametrize("flags, code", [((), 2), (("--empty-ok",), 0)],
                         ids=["bare", "empty-ok"])
def test_a_mixed_narrowed_run_counts_only_the_component_that_is_not_by_design(
        tmp_path, flags, code):
    """D15a-39: nothing activated, but only one of the two components reads nothing by
    design; the run stays `no-activation`, counting the other alone, and the by-design one
    still gets its line."""
    lay_down(tmp_path, {"a": {"b/docker-compose.yml": BUILD_DOT}, "a/b": {},
                        "docs": {"notes.md": "x\n"}})
    run = run_build(tmp_path, "--components", "a,docs", *flags)
    assert run.returncode == code, run.stdout + run.stderr
    report = report_of(tmp_path)
    assert report["status"] == "no-activation"
    assert "in any of 1 component(s)" in run.stdout
    assert report["components_unclaimed"] == ["docs"]
    assert "a reads nothing by design" in run.stdout


def test_validate_grades_a_by_design_build_clean_and_unmeasurable(tmp_path):
    """D15a-25, D15a-36: `compute_clean_build` accepts `read-by-children` - nothing
    claimable was dropped - but the graph is empty, so `validate.py` still says
    `unmeasurable`, exit 3. On `main` it exits 2, `missed`."""
    lay_down(tmp_path, CHILD_ONLY)
    assert run_build(tmp_path, "--components", "platform").returncode == 0
    run = subprocess.run([sys.executable, str(ROOT / "tools" / "graph" / "validate.py"),
                          "--graph-dir", str(tmp_path / "out")],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         cwd=str(ROOT))
    assert run.returncode == 3, run.stdout + run.stderr
    assert "unmeasurable" in run.stdout
    assert "zero_silent_drops: PASS" in run.stdout


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


@pytest.mark.parametrize("parent, child", [
    ("platform", "platform/svc-a/"), ("platform/", "platform/svc-a"),
    ("platform", "./platform/svc-a"), ("platform", "platform//svc-a"),
], ids=["child-trailing-slash", "parent-trailing-slash", "child-dot-slash",
        "doubled-separator"])
def test_a_respelt_component_path_is_the_same_place(tmp_path, parent, child):
    """D534-42 through T591 (review B15a-8): the one reading of the component list,
    `workspace_layout`, takes a trailing or doubled `/` and a leading `./` as the same path,
    so however either component is spelt the parent leaves the child's file to the child."""
    built = build(tmp_path, {"platform": {"docker-compose.yml": BUILD_DOT,
                                          "svc-a/docker-compose.yml": BUILD_DOT},
                             "__components__": [("platform", parent), ("svc-a", child)]})
    assert built.code == 0
    assert deploys(built) == ["platform/docker-compose.yml", "svc-a/docker-compose.yml"]
