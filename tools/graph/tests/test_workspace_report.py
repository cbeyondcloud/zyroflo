"""Tests for the workspace probe.

This module had zero coverage while being the one whose output is handed to a human as
evidence. It already produced one confidently wrong answer -- a multi-pattern `find_files`
call returned empty and a workspace was reported as having no GitHub Actions at all when a
clear majority of its components had workflows. The root cause was fixed in `repo_walk`, but
nothing here re-checked the claim, so the tests below assert the *findings*, not the plumbing.

Coordinates in the fixtures are invented (Rev 7). Each test keeps the bug it was written for
and loses the names it was found with -- the bugs are generic, only the names were private.

Fixtures are real directories: `probe_*` shells out to git and walks the filesystem, so
mocking the walk would test the mock.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.graph import workspace_report as wr


def mkrepo(root: Path, name: str, files: dict[str, str]) -> Path:
    """Materialize a fixture repo. Keys are relative paths; parents are created."""
    repo = root / name
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    repo.mkdir(parents=True, exist_ok=True)
    return repo


POM_JDK8 = """<project>
  <properties><maven.compiler.source>1.8</maven.compiler.source></properties>
</project>"""

POM_JDK17 = """<project>
  <properties><java.version>17</java.version></properties>
</project>"""


# ── SNAPSHOT coupling: the report claimed a component depended on its OWN modules ──
POM_SELF_COUPLED = """<project>
  <groupId>com.example</groupId>
  <artifactId>orders</artifactId>
  <version>1.0.0-SNAPSHOT</version>
  <modules><module>orders-calculator</module></modules>
  <dependencies>
    <dependency>
      <groupId>com.example</groupId><artifactId>platform-tracing</artifactId>
      <version>2.9.0-SNAPSHOT</version>
    </dependency>
    <dependency>
      <groupId>com.example</groupId><artifactId>orders-calculator</artifactId>
      <version>1.0.0-SNAPSHOT</version>
    </dependency>
    <dependency>
      <groupId>org.springframework</groupId><artifactId>spring-web</artifactId>
      <version>5.3.20</version>
    </dependency>
  </dependencies>
</project>"""


def test_a_pom_does_not_report_itself_as_a_snapshot_dependency(tmp_path: Path):
    """A real defect, kept with invented coordinates. A generated report listed several
    components as consuming their own submodules as *cross-component* SNAPSHOT dependencies,
    because the old regex matched artifactId+version anywhere in the file — including the
    manifest's own coordinates and its `<modules>` list. The bug is generic; only the names
    it was found with were private."""
    r = mkrepo(tmp_path, "orders", {"pom.xml": POM_SELF_COUPLED})
    rep = wr.RepoReport(alias="orders", path=str(r))
    wr.probe_build(rep, r)
    assert "orders" not in rep.snapshot_deps, "the pom's own artifactId is not a dependency"
    assert "orders-calculator" not in rep.snapshot_deps, "a sibling module is not cross-component"
    assert rep.snapshot_deps == ["platform-tracing"], "only the genuine external SNAPSHOT survives"


def test_a_released_version_is_not_snapshot_coupling(tmp_path: Path):
    """spring-web at 5.3.20 is a pinned release — coupling means SNAPSHOT, by definition."""
    r = mkrepo(tmp_path, "x", {"pom.xml": POM_SELF_COUPLED})
    rep = wr.RepoReport(alias="x", path=str(r))
    wr.probe_build(rep, r)
    assert "spring-web" not in rep.snapshot_deps


def test_publishing_a_snapshot_is_still_detected(tmp_path: Path):
    r = mkrepo(tmp_path, "x", {"pom.xml": POM_SELF_COUPLED})
    rep = wr.RepoReport(alias="x", path=str(r))
    wr.probe_build(rep, r)
    assert rep.publishes_snapshot is True


# ── Dockerfile JDK: the probe invented versions that do not exist ──────────────
@pytest.mark.parametrize("line,expected", [
    ("FROM eclipse-temurin:17-jdk-alpine", {"17"}),
    ("FROM openjdk:8-jdk-alpine", {"8"}),
    ("FROM openjdk:11.0.16-jre", {"11"}),
    ("FROM amazoncorretto:21", {"21"}),
    # The bad values the review found in the real output. A digit-carrying image NAME must
    # never be read as a JDK version: the build engine selects a JDK from this matrix, and
    # a fabricated entry sends it looking for a toolchain that cannot exist.
    ("FROM myrepo/openjdk-build-2024:latest", set()),
    ("FROM ghcr.io/org/openjdk-base-0:v1", set()),
    ("FROM node:20-alpine", set()),
])
def test_dockerfile_jdk_is_read_from_the_tag_only(line: str, expected: set):
    assert wr.dockerfile_jdks(line + "\n") == expected


def test_dockerfile_jdk_flows_through_the_probe(tmp_path: Path):
    r = mkrepo(tmp_path, "x", {"Dockerfile": "FROM myrepo/openjdk-build-2024:latest\n"
                                             "FROM eclipse-temurin:17-jre\n"})
    rep = wr.RepoReport(alias="x", path=str(r))
    wr.probe_build(rep, r)
    assert rep.jdk_versions == ["17"], f"fabricated versions leaked: {rep.jdk_versions}"


# ── CI detection: the class of finding that was wrong before ───────────────────
def test_github_actions_detected(tmp_path: Path):
    r = mkrepo(tmp_path, "a", {".github/workflows/ci.yml": "on: push"})
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_ci(rep, r)
    assert "github-actions" in rep.ci_systems
    assert rep.workflows == ["ci.yml"]


def test_workflows_found_under_both_yml_and_yaml(tmp_path: Path):
    """The exact regression: `.yml` and `.yaml` is a two-pattern lookup, which once
    returned nothing and read as 'this repo has no CI'."""
    r = mkrepo(tmp_path, "a", {
        ".github/workflows/ci.yml": "on: push",
        ".github/workflows/release.yaml": "on: tag",
    })
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_ci(rep, r)
    assert rep.workflows == ["ci.yml", "release.yaml"], \
        "a .yaml-only workflow must not be invisible"


def test_repo_without_workflows_reports_no_github_actions(tmp_path: Path):
    r = mkrepo(tmp_path, "a", {"README.md": "x"})
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_ci(rep, r)
    assert "github-actions" not in rep.ci_systems


@pytest.mark.parametrize("rel", [
    "Jenkinsfile",                      # at the root
    "services/Jenkinsfile",             # under a services tree
    "frontend/web-ui/Jenkinsfile",      # two deep, under a UI tree
], ids=["root", "one-deep", "two-deep"])
def test_jenkins_detected_at_root_and_nested(tmp_path: Path, rel: str):
    """A CI declaration is routinely nested rather than at the root. When this was measured
    against a real workspace, checking only the root would have misread roughly half its
    components as CI-less. The depths mirror layouts that actually occur."""
    r = mkrepo(tmp_path, "a", {rel: "pipeline {}"})
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_ci(rep, r)
    assert "jenkins" in rep.ci_systems


def test_excluded_directories_hide_ci_files_by_design(tmp_path: Path):
    """A documented trade-off, not an oversight.

    DEFAULT_EXCLUDES skips generic build-output names (`build`, `dist`, `out`, `target`),
    which also hides a genuine CI file placed under one. That is the right default -- a
    Jenkinsfile copied into build output is not a CI declaration -- but it is a silent
    miss, so it is pinned here. Verified when it was measured: no real CI file in any
    component sat under an excluded directory, so the count was unaffected.
    Revisit if a workspace legitimately keeps CI config under one of those names.
    """
    r = mkrepo(tmp_path, "a", {"build/Jenkinsfile": "pipeline {}"})
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_ci(rep, r)
    assert rep.ci_systems == [], "under an excluded dir, by design"


def test_both_ci_systems_coexist(tmp_path: Path):
    """Running two CI systems at once is common during a migration; finding one must not
    stop the search, or the report understates the CI surface."""
    r = mkrepo(tmp_path, "a", {".github/workflows/ci.yml": "on: push", "Jenkinsfile": "x"})
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_ci(rep, r)
    assert set(rep.ci_systems) == {"github-actions", "jenkins"}


def test_act_applicable_tracks_github_actions_presence(tmp_path: Path):
    with_gha = wr.RepoReport(alias="a", path="", ci_systems=["github-actions"])
    without = wr.RepoReport(alias="b", path="", ci_systems=["jenkins"])
    assert wr.summarize([with_gha])["act_applicable"] is True
    assert wr.summarize([without])["act_applicable"] is False
    assert wr.summarize([])["act_applicable"] is False


# ── JDK matrix ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("body,want", [
    (POM_JDK8, "8"),
    (POM_JDK17, "17"),
    ("<project><properties><maven.compiler.target>11</maven.compiler.target></properties></project>", "11"),
    ("<project><configuration><release>21</release></configuration></project>", "21"),
])
def test_jdk_extracted_from_pom(tmp_path: Path, body: str, want: str):
    r = mkrepo(tmp_path, "a", {"pom.xml": body})
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_build(rep, r)
    assert want in rep.jdk_versions


def test_java_8_is_normalized_from_the_1_8_spelling(tmp_path: Path):
    """`1.8` and `8` are the same JDK; two spellings would double-count the matrix."""
    r = mkrepo(tmp_path, "a", {"pom.xml": POM_JDK8})
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_build(rep, r)
    assert rep.jdk_versions == ["8"] and "1.8" not in rep.jdk_versions


@pytest.mark.parametrize("line,want", [
    ("FROM openjdk:8-jdk-alpine", "8"),
    ("FROM eclipse-temurin:17-jre", "17"),
    ("FROM amazoncorretto:11", "11"),
])
def test_jdk_extracted_from_dockerfile(tmp_path: Path, line: str, want: str):
    r = mkrepo(tmp_path, "a", {"Dockerfile": line + "\nRUN true\n"})
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_build(rep, r)
    assert want in rep.jdk_versions


def test_repo_spanning_two_jdks_reports_both(tmp_path: Path):
    """A single component declaring two JDK majors across its modules is what forces
    per-module toolchain selection, so collapsing it to one version erases the finding --
    and the build engine then picks the wrong JDK and reports a false red."""
    r = mkrepo(tmp_path, "a", {"svc-a/pom.xml": POM_JDK8, "svc-b/pom.xml": POM_JDK17})
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_build(rep, r)
    assert rep.jdk_versions == ["8", "17"], "sorted numerically, both retained"


def test_jdk_matrix_sorts_numerically_not_lexically(tmp_path: Path):
    reps = [wr.RepoReport(alias="a", path="", jdk_versions=["8", "11", "17"])]
    assert list(wr.summarize(reps)["jdk_matrix"]) == ["8", "11", "17"], \
        "lexical sort would order 11 < 17 < 8 and misrepresent the spread"


# ── coverage tooling: drives a Rev-3 gate, so a wrong answer gates wrongly ──────
def test_jacoco_makes_a_maven_repo_measurable(tmp_path: Path):
    r = mkrepo(tmp_path, "a", {"pom.xml": "<project><build><plugin>jacoco-maven-plugin</plugin></build></project>"})
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_build(rep, r)
    assert rep.coverage_tools == ["jacoco"] and rep.coverage_measurable is True


def test_maven_without_jacoco_is_unmeasurable_and_says_so_loudly(tmp_path: Path):
    """Unmeasurable must never be reported as low coverage -- the note is the fail-loud."""
    r = mkrepo(tmp_path, "a", {"pom.xml": "<project/>"})
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_build(rep, r)
    assert rep.coverage_measurable is False
    assert any("UNMEASURABLE" in n for n in rep.notes)


def test_npm_coverage_tools_detected(tmp_path: Path):
    r = mkrepo(tmp_path, "a", {"package.json": '{"devDependencies":{"c8":"^8.0.0"}}'})
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_build(rep, r)
    assert rep.coverage_tools == ["c8"]


def test_malformed_package_json_does_not_abort_the_probe(tmp_path: Path):
    r = mkrepo(tmp_path, "a", {"package.json": "{not json", "pom.xml": POM_JDK17})
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_build(rep, r)  # must not raise
    assert "17" in rep.jdk_versions, "one broken file must not lose the rest of the repo"


# ── build topology ─────────────────────────────────────────────────────────────
def test_build_systems_detected(tmp_path: Path):
    r = mkrepo(tmp_path, "a", {
        "pom.xml": "<project/>", "ui/package.json": "{}", "app/pubspec.yaml": "name: x",
        "svc/pyproject.toml": "[project]", "g/build.gradle": "plugins {}",
    })
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_build(rep, r)
    assert set(rep.build_systems) == {"maven", "npm", "flutter", "python", "gradle"}


def test_snapshot_coupling_detected(tmp_path: Path):
    r = mkrepo(tmp_path, "a", {"pom.xml": """<project>
      <version>1.0-SNAPSHOT</version>
      <dependencies>
        <dependency><artifactId>platform-tracing</artifactId><version>2.0-SNAPSHOT</version></dependency>
      </dependencies></project>"""})
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_build(rep, r)
    assert rep.publishes_snapshot is True
    assert "platform-tracing" in rep.snapshot_deps


def test_task_runners_detected(tmp_path: Path):
    r = mkrepo(tmp_path, "a", {"Makefile": "test:\n\techo", "tox.ini": "[tox]"})
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_build(rep, r)
    assert rep.task_runners == ["make", "tox"]


# ── report shape ───────────────────────────────────────────────────────────────
def test_summarize_and_render_survive_an_empty_estate():
    """A zero-repo estate must produce a report saying so, not a traceback."""
    s = wr.summarize([])
    assert s["repos"] == 0
    assert isinstance(wr.render([], s), str)


def test_render_marks_repos_with_no_coverage_tooling(tmp_path: Path):
    rep = wr.RepoReport(alias="bare", path="", build_systems=["maven"])
    out = wr.render([rep], wr.summarize([rep]))
    assert "**none**" in out and "bare" in out


def test_non_git_directory_is_flagged_not_silently_skipped(tmp_path: Path):
    r = mkrepo(tmp_path, "a", {"README.md": "x"})
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_git(rep, r)
    assert rep.is_git is False
    assert any("not a git checkout" in n for n in rep.notes)


def test_exclusions_come_from_the_workspace_config():
    """Rev 7, item 72. This used to assert a hardcoded pair of directory names -- one
    developer's notes directory and one incomplete clone -- which made the probe correct on
    exactly one machine. Then it became a `--exclude` flag. Now there is no exclusion
    mechanism here AT ALL: the tool reads the same `sdlc-workspace.toml` the installer
    does, so "which directories are components" has one implementation instead of two that
    answer differently."""
    assert not hasattr(wr, "DEFAULT_EXCLUDED"), "the constant is gone, not merely emptied"
    with pytest.raises(SystemExit):   # the flag itself no longer exists
        wr.main(["--workspace", "x.toml", "--exclude", "notes"])
    si = wr.load_installer()
    assert callable(si.read_workspace) and callable(si.discover_components)


# ── the stack tables are allowed to differ, but not silently ──────────────────
def test_every_detected_stack_is_probed_or_declared_unprobed():
    """Two implementations of "which marker means which stack" is the price of the
    zero-dependency installer gate, paid deliberately. This is what bounds it.

    A stack the installer can detect but this file neither probes nor lists is an
    oversight: the component installs fine and then reports no build system, no coverage
    tooling and no runtime version, which reads as "nothing configured" rather than as
    "not looked at".
    """
    si = wr.load_installer()
    detectable = set()
    for stack in ("python", "javascript", "go", "rust", "java-maven", "java-gradle",
                  "dotnet", "ruby", "cpp-cmake"):
        detectable.add(stack)
    unaccounted = detectable - set(wr.STACK_PROBES) - wr.UNPROBED
    assert not unaccounted, (
        f"stacks the installer detects that this diagnostic neither probes nor declares "
        f"UNPROBED: {sorted(unaccounted)}. Add a probe, or add it to UNPROBED and say why."
    )
    assert not (set(wr.STACK_PROBES) & wr.UNPROBED), "a stack cannot be both"


def test_the_detectable_stack_list_matches_what_the_installer_can_return(tmp_path: Path):
    """The test above hardcodes the installer's stack vocabulary, so it would keep passing
    if a NEW stack were added there. This proves each name is still real by driving
    detection with that stack's own marker file."""
    si = wr.load_installer()
    markers = {
        "python": ("pyproject.toml", "[project]\nname='x'\n"),
        "javascript": ("package.json", "{}"),
        "go": ("go.mod", "module x\n"),
        "rust": ("Cargo.toml", "[package]\nname='x'\n"),
        "java-maven": ("pom.xml", "<project/>"),
        "java-gradle": ("build.gradle", ""),
        "dotnet": ("x.sln", ""),
        "ruby": ("Gemfile", ""),
        "cpp-cmake": ("CMakeLists.txt", ""),
    }
    for stack, (marker, body) in markers.items():
        repo = tmp_path / stack
        repo.mkdir()
        (repo / marker).write_text(body, encoding="utf-8")
        assert si.detect_stack(repo)["stack"] == stack, f"{marker} no longer yields {stack}"


# ── git probe ──────────────────────────────────────────────────────────────────
def test_probe_git_reads_remote_head_and_dirtiness(tmp_path: Path):
    import subprocess
    r = tmp_path / "a"
    r.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(r), *a], capture_output=True, check=True)
    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    run("remote", "add", "origin", "https://github.com/o/r.git")
    (r / "f.txt").write_text("x", encoding="utf-8")
    run("add", "-A")
    run("commit", "-q", "-m", "c")

    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_git(rep, r)
    assert rep.is_git is True
    assert rep.github is True, "a github.com remote must be recognized"
    assert rep.remotes["origin"].endswith("o/r.git")
    assert len(rep.head) == 40
    assert rep.dirty is False

    (r / "f.txt").write_text("changed", encoding="utf-8")
    rep2 = wr.RepoReport(alias="a", path=str(r))
    wr.probe_git(rep2, r)
    assert rep2.dirty is True, "an uncommitted change must be visible to the probe"


def test_non_github_remote_is_not_reported_as_github(tmp_path: Path):
    """`act` applicability and push strategy both key off this."""
    import subprocess
    r = tmp_path / "a"
    r.mkdir()
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(r), "remote", "add", "origin",
                    "https://gitlab.com/o/r.git"], check=True, capture_output=True)
    rep = wr.RepoReport(alias="a", path=str(r))
    wr.probe_git(rep, r)
    assert rep.github is False and "origin" in rep.remotes


# ── end to end: the artifact that gets read as evidence ────────────────────────
def workspace_at(tmp_path: Path, body: str = 'root = "repos"\n') -> Path:
    cfg = tmp_path / "sdlc-workspace.toml"
    cfg.write_text(body, encoding="utf-8")
    return cfg


def git_init(path: Path) -> None:
    """A `.git` directory is all discovery looks for; no need to shell out."""
    (path / ".git").mkdir(parents=True, exist_ok=True)


def test_main_writes_both_reports_with_the_expected_shape(tmp_path: Path, monkeypatch):
    """The JSON/MD pair is handed to a human as findings, so its shape is a contract."""
    import json
    repos = tmp_path / "repos"
    svc = mkrepo(repos, "svc", {"pom.xml": POM_JDK17, ".github/workflows/ci.yml": "on: push"})
    git_init(svc)
    git_init(mkrepo(repos, "notes", {"scratch.md": "x"}))
    out = tmp_path / "out"

    cfg = workspace_at(tmp_path, 'root = "repos"\nexclude = ["notes"]\n')
    monkeypatch.setattr("sys.argv", ["workspace_report.py", "--workspace", str(cfg),
                                     "--out", str(out)])
    assert wr.main() == 0

    data = json.loads((out / "workspace_report.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == wr.SCHEMA_VERSION
    assert [r["alias"] for r in data["repos"]] == ["svc"], "the config's exclude must be honored"
    assert data["summary"]["act_applicable"] is True
    assert data["summary"]["jdk_matrix"] == {"17": 1}
    assert "Workspace reality check" in (out / "workspace_report.md").read_text(encoding="utf-8")


def test_the_report_records_workspace_relative_paths_not_absolute_ones(tmp_path: Path, monkeypatch):
    """The absolute path is needed to read the files and must not be what lands in the
    report. A file full of `<somebody>/<their-machine>/...` is a machine fingerprint, and
    this tool's output reached a remote once already — it cost a four-pass history rewrite
    to take back."""
    import json
    repos = tmp_path / "repos"
    git_init(mkrepo(repos, "svc", {"pom.xml": POM_JDK17}))
    out = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["workspace_report.py", "--workspace",
                                     str(workspace_at(tmp_path)), "--out", str(out)])
    assert wr.main() == 0

    raw = (out / "workspace_report.json").read_text(encoding="utf-8")
    assert json.loads(raw)["repos"][0]["path"] == "svc"
    assert str(tmp_path) not in raw, "an absolute machine path reached the report"


def test_main_refuses_to_run_without_a_workspace_config(capsys):
    """No default. A default that points at one developer's machine reports an empty
    workspace everywhere else -- and an empty report reads exactly like a clean one, which
    is the silent-wrong-answer class this project keeps paying for."""
    with pytest.raises(SystemExit) as exc:
        wr.main([])
    assert exc.value.code == 2
    assert "--workspace" in capsys.readouterr().err


def test_main_fails_loudly_on_a_bad_workspace_config(tmp_path: Path, monkeypatch, capsys):
    cfg = workspace_at(tmp_path, 'root = "nowhere"\n')
    monkeypatch.setattr("sys.argv", ["workspace_report.py", "--workspace", str(cfg)])
    assert wr.main() == 2, "a bad root must be an error, not an empty report"
    assert "does not exist" in capsys.readouterr().out


# ── the output location is a refusal, not a convention ────────────────────────
@pytest.mark.parametrize("bad", ["docs", "docs/m0", "a/docs/b", ".sdlc", "sdlc_templates"])
def test_writing_into_a_committed_tree_is_refused(tmp_path: Path, monkeypatch, capsys, bad: str):
    """The rule has already been broken once: an earlier revision wrote into `docs/` by
    default, the reports were committed, they reached a remote, and removing them cost a
    four-pass history rewrite. A docstring would not have stopped that; a non-zero exit
    would have."""
    git_init(mkrepo(tmp_path / "repos", "svc", {"pom.xml": POM_JDK17}))
    monkeypatch.setattr("sys.argv", ["workspace_report.py", "--workspace",
                                     str(workspace_at(tmp_path)), "--out", str(tmp_path / bad)])
    assert wr.main() == 2
    assert "refusing to write" in capsys.readouterr().out
    assert not (tmp_path / bad).exists(), "the refusal still created the directory"


def test_the_default_output_is_a_scratch_directory():
    """`.tmp/` is gitignored. A default that lands in a tracked tree makes committing the
    scan the path of least resistance, which is exactly how it happened the first time."""
    assert wr.check_output_dir(Path(".tmp/graph")) is None
    assert wr.check_output_dir(Path("docs/m0")) is not None


# ── an unprobed stack must not read as an unconfigured one ────────────────────
def test_a_component_with_no_probed_build_system_says_not_probed(tmp_path: Path):
    """A Go or Rust component renders as a row of dashes. Without a word for it, that reads
    as "nothing configured here" when it means "this tool did not look" — the same
    empty-is-not-clean confusion the rest of this project keeps paying for."""
    repo = mkrepo(tmp_path, "svc", {"go.mod": "module svc\n"})
    rep = wr.RepoReport(alias="svc", path="svc")
    wr.probe_build(rep, repo)
    assert rep.build_systems == []
    assert any("NOT PROBED" in n for n in rep.notes)
    assert any("not evidence of absence" in n for n in rep.notes)


def test_the_rendered_report_flags_unprobed_components(tmp_path: Path):
    repo = mkrepo(tmp_path, "svc", {"go.mod": "module svc\n"})
    rep = wr.probe_repo("svc", repo, rel="svc")
    text = wr.render([rep], wr.summarize([rep]))
    assert "**not probed**" in text, "a blank build cell needs a word, not a dash"
    assert "not 'nothing configured'" in text


def test_a_probed_component_is_not_flagged(tmp_path: Path):
    repo = mkrepo(tmp_path, "svc", {"pom.xml": POM_JDK17})
    rep = wr.probe_repo("svc", repo, rel="svc")
    assert not any("NOT PROBED" in n for n in rep.notes)
    assert "**not probed**" not in wr.render([rep], wr.summarize([rep]))


def test_per_repo_notes_reach_the_rendered_report(tmp_path: Path):
    """The notes carry the fail-loud findings — unmeasurable coverage, non-git dirs. They
    were computed and then never rendered, so nobody reading the report ever saw them."""
    repo = mkrepo(tmp_path, "svc", {"pom.xml": POM_JDK17})
    rep = wr.probe_repo("svc", repo, rel="svc")
    assert "UNMEASURABLE" in wr.render([rep], wr.summarize([rep]))
