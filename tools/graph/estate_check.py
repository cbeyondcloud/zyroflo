"""Workspace reality check (M0, tracker item 6).

Answers the questions the plan cannot assume, because getting them wrong produces false
reds — and false reds erode trust in a gate faster than a missing gate does:

  * push/CI reality  — is there a GitHub remote? real Actions workflows? is `act` even
    applicable, or is CI somewhere `act` can never run?
  * build topology   — which components build standalone vs couple to siblings via
    pre-release (SNAPSHOT) versions?
  * runtime matrix   — which JDK/Node/Python each component demands. A real workspace
    routinely spans several majors at once, and an environment mismatch reads exactly like
    a code failure.
  * coverage tooling — is jacoco/lcov actually configured? Changed-file coverage is a gate,
    and a missing plugin must report as unmeasurable, never as low coverage.

Read-only. Writes a JSON report plus a human summary, to a directory you name.

    python tools/graph/estate_check.py --estate <path to your workspace>

The workspace root is an argument with no default, and the exclusion list is an argument
too: nothing here may assume whose machine it is running on. Item 72 retargets this at the
workspace config file and renames it to match what it actually is.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.graph.core.lib import props, repo_walk  # noqa: E402

SCHEMA_VERSION = 1

#: Directories to skip, supplied per run. Empty by default and deliberately so: which
#: children of a workspace root are components is a fact about someone's machine, and the
#: previous hardcoded pair (one developer's notes directory and one incomplete clone) made
#: this tool correct on exactly one laptop. Callers pass `--exclude`; item 69 moves the list
#: into the workspace config so it is declared once rather than retyped per invocation.
DEFAULT_EXCLUDED: frozenset[str] = frozenset()

JDK_HINTS = [
    (re.compile(r"<maven\.compiler\.(?:source|target|release)>\s*(1\.8|8|11|17|21)\s*<"), "pom-compiler"),
    (re.compile(r"<java\.version>\s*(1\.8|8|11|17|21)\s*<"), "pom-java-version"),
    (re.compile(r"<release>\s*(8|11|17|21)\s*<"), "pom-release"),
]

#: JDK majors worth believing. A version outside this set means the regex matched something
#: that is not a JDK version, so it is dropped rather than reported.
KNOWN_JDKS = {"8", "11", "17", "21", "24", "25"}

#: Capture only the TAG of a JDK base image. The previous pattern scanned the whole FROM
#: line for `[:-](\d+)`, so an image NAME carrying digits — `myrepo/openjdk-build-2024:latest`
#: — reported JDK '2024', and a repo could end up with a fabricated entry in the matrix the
#: build engine is supposed to select from.
_DOCKER_JDK_IMAGE = re.compile(
    r"^\s*FROM\s+\S*?(?:openjdk|eclipse-temurin|amazoncorretto|ibm-semeru|zulu)[^\s:@]*:([^\s]+)",
    re.I | re.M,
)
_TAG_MAJOR = re.compile(r"^(\d+)")

#: SNAPSHOT coupling must be read from <dependency> blocks only. Matching artifactId +
#: version anywhere in a manifest also matched the manifest's OWN coordinates, which is why
#: an earlier report listed several components as "consuming" their own submodules as though
#: they were cross-component dependencies.
_DEP_BLOCK = re.compile(r"<dependency>(.*?)</dependency>", re.S)
_DEP_ARTIFACT = re.compile(r"<artifactId>\s*([\w.\-]+)\s*</artifactId>", re.S)
_DEP_SNAPSHOT = re.compile(r"<version>\s*[^<]*-SNAPSHOT\s*</version>", re.S)
_ANY_ARTIFACT = re.compile(r"<artifactId>\s*([\w.\-]+)\s*</artifactId>")
_MODULE = re.compile(r"<module>\s*([\w.\-/]+)\s*</module>")


def dockerfile_jdks(text: str) -> set[str]:
    """JDK majors declared by base images, read from the image tag alone."""
    out: set[str] = set()
    for tag in _DOCKER_JDK_IMAGE.findall(text):
        m = _TAG_MAJOR.match(tag)
        if m and m.group(1) in KNOWN_JDKS:
            out.add(m.group(1))
    return out


def pom_snapshot_deps(text: str) -> list[str]:
    """artifactIds this pom depends on at a -SNAPSHOT version."""
    out = []
    for block in _DEP_BLOCK.findall(text):
        if not _DEP_SNAPSHOT.search(block):
            continue
        art = _DEP_ARTIFACT.search(block)
        if art:
            out.append(art.group(1))
    return out


def pom_own_artifacts(text: str) -> set[str]:
    """Artifact names this pom declares as its own: project + parent coordinates, and the
    <module> names of an aggregator.

    Everything before <dependencies> is the pom's own identity; subtracting these is what
    turns `snapshot_deps` into the cross-repo coupling the report claims it is. Module
    names are included because an aggregator names its children before their own poms are
    read — and on a repo whose child poms fall outside the scan cap, they are the only
    evidence that a "dependency" is really a sibling.
    """
    head = text[: text.find("<dependencies>")] if "<dependencies>" in text else text
    return set(_ANY_ARTIFACT.findall(head)) | {
        m.rsplit("/", 1)[-1] for m in _MODULE.findall(text)
    }


@dataclass
class RepoReport:
    alias: str
    path: str
    is_git: bool = False
    remotes: dict[str, str] = field(default_factory=dict)
    default_branch: str = ""
    head: str = ""
    dirty: bool = False
    github: bool = False

    ci_systems: list[str] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)

    build_systems: list[str] = field(default_factory=list)
    pom_count: int = 0
    modules: int = 0
    snapshot_deps: list[str] = field(default_factory=list)
    publishes_snapshot: bool = False

    jdk_versions: list[str] = field(default_factory=list)
    node_versions: list[str] = field(default_factory=list)
    python_versions: list[str] = field(default_factory=list)

    coverage_tools: list[str] = field(default_factory=list)
    coverage_measurable: bool = False

    task_runners: list[str] = field(default_factory=list)
    test_frameworks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def git(repo: Path, *args: str) -> str:
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def probe_git(rep: RepoReport, path: Path) -> None:
    if not (path / ".git").exists():
        rep.notes.append("not a git checkout — cannot compute diffs or run the pipeline here")
        return
    rep.is_git = True
    for line in git(path, "remote", "-v").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1] == "(fetch)":
            rep.remotes[parts[0]] = parts[1]
    rep.github = any("github.com" in u for u in rep.remotes.values())
    rep.head = git(path, "rev-parse", "HEAD")
    rep.dirty = bool(git(path, "status", "--porcelain"))
    ref = git(path, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    rep.default_branch = ref.rsplit("/", 1)[-1] if ref else git(path, "rev-parse", "--abbrev-ref", "HEAD")


def probe_ci(rep: RepoReport, path: Path) -> None:
    wf = repo_walk.find_files(path / ".github" / "workflows", ["*.yml", "*.yaml"]) if (path / ".github" / "workflows").is_dir() else []
    if wf:
        rep.ci_systems.append("github-actions")
        rep.workflows = sorted(p.name for p in wf)
    for marker, name in [
        ("Jenkinsfile", "jenkins"), (".gitlab-ci.yml", "gitlab"),
        ("azure-pipelines.yml", "azure"), ("cloudbuild.yaml", "cloud-build"),
        (".circleci/config.yml", "circleci"),
    ]:
        if (path / marker).exists():
            rep.ci_systems.append(name)
    # Jenkinsfiles are frequently nested rather than at the root in this estate.
    if "jenkins" not in rep.ci_systems and repo_walk.find_files(path, ["Jenkinsfile*"]):
        rep.ci_systems.append("jenkins")


def probe_build(rep: RepoReport, path: Path) -> None:
    poms = repo_walk.find_files(path, ["pom.xml"])
    pkgs = repo_walk.find_files(path, ["package.json"])
    pubspecs = repo_walk.find_files(path, ["pubspec.yaml"])
    pyprojects = repo_walk.find_files(path, ["pyproject.toml", "setup.py", "requirements.txt"])

    rep.pom_count = len(poms)
    if poms:
        rep.build_systems.append("maven")
    if pkgs:
        rep.build_systems.append("npm")
    if pubspecs:
        rep.build_systems.append("flutter")
    if pyprojects:
        rep.build_systems.append("python")
    if repo_walk.find_files(path, ["build.gradle", "build.gradle.kts"]):
        rep.build_systems.append("gradle")
    rep.modules = len(poms) + len(pkgs) + len(pubspecs)

    jdks: set[str] = set()
    snapshots: Counter[str] = Counter()
    own_artifacts: set[str] = set()
    for pom in poms[:400]:
        text = repo_walk.read_text(pom)
        head = text[: text.find("<dependencies>")] if "<dependencies>" in text else text
        if "-SNAPSHOT" in head:
            rep.publishes_snapshot = True
        own_artifacts |= pom_own_artifacts(text)
        for art in pom_snapshot_deps(text):
            snapshots[art] += 1
        for rx, _src in JDK_HINTS:
            for m in rx.finditer(text):
                jdks.add(m.group(1).replace("1.8", "8"))
        if "jacoco" in text:
            rep.coverage_tools.append("jacoco")
        if "surefire" in text:
            rep.test_frameworks.append("surefire")
    # A module depending on its sibling is intra-repo build order, not cross-repo coupling.
    rep.snapshot_deps = [a for a, _ in snapshots.most_common() if a not in own_artifacts][:15]

    for df in repo_walk.find_files(path, ["Dockerfile*"])[:80]:
        jdks |= dockerfile_jdks(repo_walk.read_text(df))
    rep.jdk_versions = sorted(jdks, key=lambda v: int(v))

    for pkg in pkgs[:60]:
        try:
            data = json.loads(repo_walk.read_text(pkg) or "{}")
        except json.JSONDecodeError:
            continue
        eng = (data.get("engines") or {}).get("node")
        if eng:
            rep.node_versions.append(str(eng))
        scripts = data.get("scripts") or {}
        if any(k in scripts for k in ("test", "test:unit")):
            rep.test_frameworks.append("npm-test")
        deps = {**(data.get("devDependencies") or {}), **(data.get("dependencies") or {})}
        if "jest" in deps:
            rep.test_frameworks.append("jest")
        if "nyc" in deps or "c8" in deps:
            rep.coverage_tools.append(deps.get("nyc") and "nyc" or "c8")
    rep.node_versions = sorted(set(rep.node_versions))

    if (path / ".python-version").exists():
        rep.python_versions.append(repo_walk.read_text(path / ".python-version").strip())
    rep.python_versions = sorted(set(v for v in rep.python_versions if v))

    for marker, name in [
        ("Makefile", "make"), ("justfile", "just"), ("Justfile", "just"),
        ("Taskfile.yml", "task"), ("noxfile.py", "nox"), ("tox.ini", "tox"),
        ("docker-compose.yml", "docker-compose"), ("docker-compose.yaml", "docker-compose"),
    ]:
        if (path / marker).exists():
            rep.task_runners.append(name)
    rep.task_runners = sorted(set(rep.task_runners))
    rep.coverage_tools = sorted(set(rep.coverage_tools))
    rep.test_frameworks = sorted(set(rep.test_frameworks))
    rep.coverage_measurable = bool(rep.coverage_tools)
    if "maven" in rep.build_systems and "jacoco" not in rep.coverage_tools:
        rep.notes.append("maven repo without jacoco — changed-file coverage is UNMEASURABLE here "
                         "(fail-loud, do not report as low coverage)")


def probe_repo(alias: str, path: Path) -> RepoReport:
    rep = RepoReport(alias=alias, path=str(path))
    probe_git(rep, path)
    probe_ci(rep, path)
    probe_build(rep, path)
    return rep


def summarize(reports: list[RepoReport]) -> dict[str, Any]:
    jdk = Counter(v for r in reports for v in r.jdk_versions)
    ci = Counter(c for r in reports for c in r.ci_systems)
    build = Counter(b for r in reports for b in r.build_systems)
    return {
        "repos": len(reports),
        "git_checkouts": sum(1 for r in reports if r.is_git),
        "github_remotes": sum(1 for r in reports if r.github),
        "dirty_worktrees": sorted(r.alias for r in reports if r.dirty),
        "ci_systems": dict(ci),
        "act_applicable": ci.get("github-actions", 0) > 0,
        "build_systems": dict(build),
        "jdk_matrix": dict(sorted(jdk.items(), key=lambda kv: int(kv[0]))),
        "snapshot_coupled_repos": sorted(r.alias for r in reports if r.snapshot_deps),
        "publishes_snapshot": sorted(r.alias for r in reports if r.publishes_snapshot),
        "coverage_measurable": sorted(r.alias for r in reports if r.coverage_measurable),
        "coverage_unmeasurable": sorted(r.alias for r in reports if not r.coverage_measurable),
        "total_poms": sum(r.pom_count for r in reports),
    }


def render(reports: list[RepoReport], summary: dict[str, Any],
           excluded: set[str] | frozenset[str] = frozenset()) -> str:
    L = ["# Workspace reality check (M0, tracker item 6)", "",
         f"Components probed: **{summary['repos']}**"
         + (f" (excluded: {', '.join(sorted(excluded))})" if excluded else ""), ""]

    L += ["## Push / CI reality", "",
          f"- git checkouts: {summary['git_checkouts']}/{summary['repos']}; "
          f"GitHub remotes: {summary['github_remotes']}",
          f"- CI systems found: {summary['ci_systems'] or 'none'}",
          f"- `act` applicable: **{summary['act_applicable']}** "
          f"({'GH Actions present — advisory local runs possible' if summary['act_applicable'] else 'no GH Actions workflows — act is moot; de-scope it'})",
          f"- dirty worktrees: {', '.join(summary['dirty_worktrees']) or 'none'}", ""]

    L += ["## Build topology", "",
          f"- build systems: {summary['build_systems']}",
          f"- total poms: {summary['total_poms']}",
          f"- repos publishing SNAPSHOT artifacts: {', '.join(summary['publishes_snapshot']) or 'none'}",
          f"- repos consuming SNAPSHOT deps (cross-repo coupling): "
          f"{', '.join(summary['snapshot_coupled_repos']) or 'none'}", ""]

    L += ["## Runtime matrix", "",
          f"- JDK versions declared (version -> repo count): {summary['jdk_matrix'] or 'none detected'}", ""]
    if len(summary["jdk_matrix"]) > 1:
        L += ["  > Multiple JDKs in one estate: the build engine must select a JDK per repo, "
              "or mismatches will surface as code failures.", ""]

    L += ["## Coverage tooling (Rev-3 gate input)", "",
          f"- measurable ({len(summary['coverage_measurable'])}): {', '.join(summary['coverage_measurable']) or 'none'}",
          f"- **unmeasurable** ({len(summary['coverage_unmeasurable'])}): {', '.join(summary['coverage_unmeasurable']) or 'none'}",
          "", "  > Unmeasurable repos must fail the coverage gate loudly as 'not measurable', "
          "never silently pass and never be reported as low coverage.", ""]

    L += ["## Per repo", "",
          "| repo | git | remote | CI | build | JDK | coverage | task runners |",
          "|---|---|---|---|---|---|---|---|"]
    for r in sorted(reports, key=lambda x: x.alias.lower()):
        L.append(
            f"| {r.alias} | {'yes' if r.is_git else 'NO'} | "
            f"{'github' if r.github else (','.join(r.remotes) or '—')} | "
            f"{','.join(r.ci_systems) or '—'} | {','.join(r.build_systems) or '—'} | "
            f"{','.join(r.jdk_versions) or '—'} | {','.join(r.coverage_tools) or '**none**'} | "
            f"{','.join(r.task_runners) or '—'} |"
        )
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--estate", required=True, metavar="PATH",
                    help="workspace root to probe (required — there is no sensible default, "
                         "and a wrong one silently reports an empty workspace)")
    ap.add_argument("--exclude", default="", metavar="NAMES",
                    help="comma-separated directory names to skip, e.g. personal notes or an "
                         "incomplete clone")
    ap.add_argument("--out", default=".tmp/graph/validation")
    args = ap.parse_args(argv)

    root = Path(args.estate)
    if not root.is_dir():
        print(f"!! workspace root not found: {root}")
        return 2

    excluded = {n.strip() for n in args.exclude.split(",") if n.strip()} or set(DEFAULT_EXCLUDED)
    dirs = [d for d in sorted(root.iterdir()) if d.is_dir() and d.name not in excluded
            and not d.name.startswith(".")]
    print(f"probing {len(dirs)} components under {root} …")
    reports = []
    for d in dirs:
        reports.append(probe_repo(d.name, d))
        print(f"  · {d.name}")

    summary = summarize(reports)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "estate_check.json").write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "estate": str(root),
                    "summary": summary, "repos": [asdict(r) for r in reports]},
                   indent=2, sort_keys=True),
        encoding="utf-8",
    )
    report_md = render(reports, summary, excluded)
    (out / "estate_check.md").write_text(report_md, encoding="utf-8")
    print("\n" + report_md)
    print(f"written: {out / 'estate_check.json'} and estate_check.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
