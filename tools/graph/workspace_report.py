"""Workspace reality check — an OPTIONAL Tier-2 diagnostic.

    python tools/graph/workspace_report.py --workspace sdlc-workspace.toml

Answers the questions a plan cannot assume, because getting them wrong produces false
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

## Why this is NOT the installer's scanner

An earlier revision had this becoming the INIT-time component scanner. It cannot be. This
module imports `props`, which imports `yaml`, and the installer is **stdlib-only by gate** —
retargeting it would either drag PyYAML onto the installer path or invent a subprocess
handshake for no gain. And it is not needed there: nothing `components[]` requires is
uniquely produced here, while everything it does require already comes from
`detect_stack()` / `detect_vcs()`.

So the dependency runs one way only — this diagnostic reuses the installer's
`read_workspace()` and `discover_components()`, and the installer knows nothing about this
file. That is also why "which directories are components" has exactly one implementation
rather than two that drift.

## The duplication that IS real, and bounded

`probe_build` knows about build systems, and so does `detect_stack()`. That is the price of
the zero-dependency installer gate, paid deliberately. It is bounded by
`test_every_detected_stack_is_probed_or_declared_unprobed`: every stack the installer can
detect must either be probed here or be listed in `UNPROBED`, so the gap is a visible
literal instead of an accident.

Read-only. Writes to `.tmp/` and REFUSES to write anywhere else inside this repo — a scan of a real
workspace is evidence, not product, and this tool's own output was committed once already.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402
from tools.graph.core.lib import props, repo_walk  # noqa: E402
from tools.graph.outdir import SCRATCH_DIR, check_output_dir as _check_output_dir  # noqa: E402

cli.use_utf8()

SCHEMA_VERSION = 1

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
    #: False when a git sub-probe could not be run here — a missing binary, a
    #: timeout, or a damaged checkout (a corrupt .git/index fails `status` while
    #: `remote -v` and `rev-parse HEAD` still succeed). A healthy clean repo and an
    #: unprobeable one share the same head='' / dirty=False shape, so this is what
    #: tells them apart: a failed MUST-SUCCEED sub-probe (`remote -v` or `status
    #: --porcelain`) taints the whole probe, and dirty=False is then a DEFAULT, not a
    #: measurement. An absent HEAD (unborn branch) or origin/HEAD is a healthy state,
    #: not could-not-probe, so it does not taint (see probe_git below).
    git_probed: bool = True

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


def git(repo: Path, *args: str) -> str | None:
    """Run a git command. Its stdout on success, or **None when git could not be run
    at all** — a missing binary, a timeout, or a non-zero exit. None is
    could-not-probe; "" is git ran and said nothing (a clean `status --porcelain`).
    Folding the two into "" reported an unprobeable checkout as a healthy clean one —
    an empty result from an external tool is not evidence of absence, this project's
    headline rule with git in fd's role."""
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def probe_git(rep: RepoReport, path: Path) -> None:
    if not (path / ".git").exists():
        rep.notes.append("not a git checkout — cannot compute diffs or run the pipeline here")
        return
    rep.is_git = True

    def could_not_probe(what: str) -> None:
        # A sub-probe git could not run — a missing binary, a timeout, or a damaged
        # checkout. Its result would otherwise fold into a healthy clean shape
        # (remotes=[], dirty=False), so the WHOLE probe becomes could-not-look,
        # distinct from a clean repo. Absence of a probe is not evidence of a clean
        # tree — this project's headline rule, with git in fd's role.
        rep.git_probed = False
        note = (f"git checkout present but `git {what}` could not be run here "
                f"(missing binary, timeout, or a damaged checkout) — its working-tree "
                f"state is UNKNOWN, not clean")
        if note not in rep.notes:
            rep.notes.append(note)

    remotes = git(path, "remote", "-v")
    if remotes is None:
        # git cannot run here at all; nothing else is worth attempting.
        could_not_probe("remote -v")
        return
    for line in remotes.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1] == "(fetch)":
            rep.remotes[parts[0]] = parts[1]
    rep.github = any("github.com" in u for u in rep.remotes.values())

    # `rev-parse HEAD` and `symbolic-ref origin/HEAD` legitimately exit non-zero on a
    # VALID repo — an unborn branch has no HEAD, and origin/HEAD is unset until a
    # fetch — so their None is a MEASURED absence (head='' and the default-branch
    # fallback say so), not could-not-look. `status --porcelain`, by contrast,
    # succeeds on every valid checkout (unborn included), so its None means the tree
    # could not be read: the one fold that reported a damaged checkout as clean.
    rep.head = git(path, "rev-parse", "HEAD") or ""
    status = git(path, "status", "--porcelain")
    if status is None:
        could_not_probe("status --porcelain")
    else:
        rep.dirty = bool(status)
    ref = git(path, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    rep.default_branch = (ref.rsplit("/", 1)[-1] if ref
                          else git(path, "rev-parse", "--abbrev-ref", "HEAD") or "")


def _spellings(path: Path, marker: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Every spelling of `marker` (a `/`-separated relative path) under `path` whose parts
    match it once case is ignored - `marker` itself among them when it is there exactly so -
    read part by part from each directory's own listing; and each directory on the way that
    could not be listed, with the reason.

    T580: the marker probes asked `.exists()`, which folds case on Windows, so a lowercase
    `jenkinsfile` read as Jenkins there and not on Linux. Read from the listing, a name
    means one thing on every platform. Every spelling is followed, so on a filesystem that
    keeps both an empty `.circleci/` hides no `.CircleCI/config.yml` (review B14-9). A
    directory that cannot be listed is its own answer, not absence (B14-1): `.exists()`
    needed no listing, and a root that could be entered but not read lost every marker in
    silence. One that is gone, or is not a directory, holds nothing. (`probe_git`'s `.git`
    test keeps `.exists()`: git reads its own directory with the filesystem's case rule,
    and so does that test.)"""
    found: list[str] = []
    unlisted: list[tuple[str, str]] = []

    def follow(here: Path, done: tuple[str, ...], rest: list[str]) -> None:
        if not rest:
            found.append("/".join(done))
            return
        try:
            names = sorted(child.name for child in here.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            return
        except OSError as exc:
            unlisted.append(("/".join(done), exc.strerror or type(exc).__name__))
            return
        for name in names:
            if name.casefold() == rest[0].casefold():
                follow(here / name, (*done, name), rest[1:])

    follow(path, (), marker.split("/"))
    return found, unlisted


def _note(rep: RepoReport, note: str) -> None:
    """Add `note` to the report's notes unless it is there already."""
    if note not in rep.notes:
        rep.notes.append(note)


def _note_file(rep: RepoReport, rel: str, why: str) -> None:
    """Name the file `rel` in the notes once, however many probes it is lost to."""
    if not any(note.startswith(f"`{rel}` ") for note in rep.notes):
        rep.notes.append(f"`{rel}` {why}")


def _markers(rep: RepoReport, path: Path, markers: list[tuple[str, str]],
             any_case: frozenset[str] = frozenset()) -> list[str]:
    """The tools whose markers are present. Each is read by the spellings named for it: the
    case spellings its tool reads - all of them for a marker in `any_case`, as `just` reads
    its justfile in any case - and `jenkinsfile`, which the nested probe counts too (B9-16).
    A file that is a marker only by folding case is named in the report's notes and not
    counted, rather than counted on one platform and dropped in silence on another (T580),
    and a directory that could not be listed is named as UNKNOWN (review B14-1). A name a
    tool also reads that is not a case spelling of a marker - `GNUmakefile`, `.justfile`,
    `compose.yaml`, `Taskfile.yaml` - is no marker here (T609). A marker is a name found, not
    a kind of entry asked for: a directory named `Makefile` counts as one (T613)."""
    found: list[str] = []
    readable = {marker for marker, _ in markers}
    for marker, name in markers:
        spellings, unlisted = _spellings(path, marker)
        if marker in spellings or (marker in any_case and spellings):
            found.append(name)
        for spelled in spellings:
            if marker not in any_case and spelled not in readable:
                _note_file(rep, spelled, f"is `{marker}` ({name}) only by folding case - "
                                         f"markers are read as their tools spell them, on "
                                         f"every platform, so it is not counted")
        for where, why in unlisted:
            _note(rep, f"{f'`{where}`' if where else 'the repository root'} could not be "
                       f"listed ({why}), so the markers in it are UNKNOWN, not absent")
    return found


def _find(rep: RepoReport, root: Path, patterns: list[str], base: Path | None = None
          ) -> list[Path]:
    """`repo_walk.find_files`, with each file a pattern names only by folding case named in
    the notes (relative to `base`, the repository, when `root` is below it) rather than
    dropped in silence - T580's rule for the markers, kept by every probe that walks the
    tree (review B14-5)."""
    misses: list[tuple[Path, tuple[str, ...]]] = []
    found = repo_walk.find_files(root, patterns, near_misses=misses)
    for miss, pats in misses:
        named = " and ".join(f"`{pat}`" for pat in pats)
        _note_file(rep, miss.relative_to(base or root).as_posix(),
                   f"matches {named} only by folding case - files are matched as their tools "
                   f"spell them, on every platform, so it is not counted")
    return found


def probe_ci(rep: RepoReport, path: Path) -> None:
    # The workflows directory is read as a marker is (review B14-12): `is_dir()` folded case
    # on Windows, so `.github/WORKFLOWS` counted there and was dropped on Linux in silence,
    # and the walk under it named a file by the spelling asked for, a path not on disk.
    workflows = path / ".github" / "workflows"
    wf = (_find(rep, workflows, ["*.yml", "*.yaml"], path)
          if _markers(rep, path, [(".github/workflows", "github-actions")]) else [])
    if wf:
        rep.ci_systems.append("github-actions")
        rep.workflows = sorted(p.name for p in wf)
    for name in _markers(rep, path, [
        ("Jenkinsfile", "jenkins"), ("jenkinsfile", "jenkins"), (".gitlab-ci.yml", "gitlab"),
        ("azure-pipelines.yml", "azure"), ("cloudbuild.yaml", "cloud-build"),
        (".circleci/config.yml", "circleci"),
    ]):
        if name not in rep.ci_systems:
            rep.ci_systems.append(name)
    # Jenkinsfiles are frequently nested rather than at the root in this estate.
    # Both spellings, named (B9-16), for the reason the image probe gives below: matching
    # no longer folds case anywhere (T531), and a folded match was a Windows-only answer.
    if "jenkins" not in rep.ci_systems and _find(rep, path, ["Jenkinsfile*", "jenkinsfile*"]):
        rep.ci_systems.append("jenkins")


def _probe_maven(rep: RepoReport, path: Path) -> int:
    poms = _find(rep, path, ["pom.xml"])
    if not poms:
        return 0
    rep.build_systems.append("maven")
    rep.pom_count = len(poms)

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
    # Without this subtraction every multi-module repo reports itself as its own dependency.
    rep.snapshot_deps = [a for a, _ in snapshots.most_common() if a not in own_artifacts][:15]
    rep.jdk_versions = sorted(set(rep.jdk_versions) | jdks, key=lambda v: int(v))
    return len(poms)


def _probe_gradle(rep: RepoReport, path: Path) -> int:
    if _find(rep, path, ["build.gradle", "build.gradle.kts"]):
        rep.build_systems.append("gradle")
    return 0


def _probe_npm(rep: RepoReport, path: Path) -> int:
    pkgs = _find(rep, path, ["package.json"])
    if not pkgs:
        return 0
    rep.build_systems.append("npm")
    for pkg in pkgs[:60]:
        try:
            data = json.loads(repo_walk.read_text(pkg) or "{}")
        except json.JSONDecodeError:
            # A malformed manifest is one repo's problem, not the whole scan's.
            continue
        if eng := (data.get("engines") or {}).get("node"):
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
    return len(pkgs)


def _probe_flutter(rep: RepoReport, path: Path) -> int:
    pubspecs = _find(rep, path, ["pubspec.yaml"])
    if pubspecs:
        rep.build_systems.append("flutter")
    return len(pubspecs)


def _probe_python(rep: RepoReport, path: Path) -> int:
    if _find(rep, path, ["pyproject.toml", "setup.py", "requirements.txt"]):
        rep.build_systems.append("python")
    if _markers(rep, path, [(".python-version", "python")]):
        rep.python_versions.append(repo_walk.read_text(path / ".python-version").strip())
    rep.python_versions = sorted({v for v in rep.python_versions if v})
    return 0


#: Build-system probes, keyed by the SAME stack names `sdlc-init.detect_stack()` returns.
#: Sharing the vocabulary is what lets the contract test below compare the two tables at
#: all; two probe sets under two naming schemes could diverge indefinitely without anyone
#: noticing. Each probe returns the module count it contributed.
STACK_PROBES = {
    "java-maven": _probe_maven,
    "java-gradle": _probe_gradle,
    "javascript": _probe_npm,
    "python": _probe_python,
    "flutter": _probe_flutter,
}

#: Stacks the installer can detect that this diagnostic does NOT probe deeply. Listed
#: rather than omitted: a stack missing from both tables is an oversight, and a stack
#: present here is a decision. `test_every_detected_stack_is_probed_or_declared_unprobed`
#: is what makes the difference visible.
UNPROBED = frozenset({"go", "rust", "dotnet", "ruby", "cpp-cmake"})


def probe_build(rep: RepoReport, path: Path) -> None:
    """Run every stack probe. A component may legitimately match several."""
    rep.modules = sum(probe(rep, path) for probe in STACK_PROBES.values())

    # Container images carry a runtime version no build manifest mentions, and they are
    # not owned by any one stack — a JVM image can sit in a repo with no pom at all.
    jdks = set(rep.jdk_versions)
    # Both spellings, named: BuildKit reads a lowercase `dockerfile` as the default, and
    # once matching stopped folding case on Windows (T531) that file had silently left the
    # report there too - one rule on every platform, and the file Docker itself reads.
    for df in _find(rep, path, ["Dockerfile*", "dockerfile*"])[:80]:
        jdks |= dockerfile_jdks(repo_walk.read_text(df))
    rep.jdk_versions = sorted(jdks, key=lambda v: int(v))

    rep.task_runners += _markers(rep, path, [
        ("Makefile", "make"), ("makefile", "make"), ("justfile", "just"),
        ("Taskfile.yml", "task"), ("taskfile.yml", "task"), ("noxfile.py", "nox"),
        ("tox.ini", "tox"), ("docker-compose.yml", "docker-compose"),
        ("docker-compose.yaml", "docker-compose"),
    ], any_case=frozenset({"justfile"}))
    rep.task_runners = sorted(set(rep.task_runners))
    rep.build_systems = sorted(set(rep.build_systems))
    rep.coverage_tools = sorted(set(rep.coverage_tools))
    rep.test_frameworks = sorted(set(rep.test_frameworks))
    rep.coverage_measurable = bool(rep.coverage_tools)
    if "maven" in rep.build_systems and "jacoco" not in rep.coverage_tools:
        rep.notes.append("maven repo without jacoco — changed-file coverage is UNMEASURABLE here "
                         "(fail-loud, do not report as low coverage)")
    if not rep.build_systems:
        # Otherwise an unprobed stack renders as a row of dashes, which reads as "nothing
        # configured here" when it means "this tool did not look". Same rule as everywhere
        # else in this project: an empty result must never be mistaken for a clean one.
        rep.notes.append(
            f"NOT PROBED — no build system this diagnostic understands "
            f"({', '.join(sorted(STACK_PROBES))}). Stacks it does not examine: "
            f"{', '.join(sorted(UNPROBED))}. Absence here is not evidence of absence.")


def probe_repo(alias: str, path: Path, rel: str | None = None) -> RepoReport:
    """Probe one component. `rel` is what gets RECORDED — pass the workspace-relative path.

    The absolute path is needed to read the files and must not be what lands in the report:
    a report full of `<somebody>/<their-machine>/...` is a machine fingerprint, and this
    tool's own output has been committed to a remote once already.
    """
    rep = RepoReport(alias=alias, path=rel if rel is not None else str(path))
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
        # A checkout git could not read: its dirty=False is a default, not a
        # measurement, so it is kept OUT of dirty_worktrees and named here instead —
        # could-not-probe is its own answer, distinct from a clean tree.
        "git_unprobeable": sorted(r.alias for r in reports if r.is_git and not r.git_probed),
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


def render(reports: list[RepoReport], summary: dict[str, Any]) -> str:
    L = ["# Workspace reality check", "",
         f"Components probed: **{summary['repos']}** "
         f"(the set your `sdlc-workspace.toml` declares)", ""]

    L += ["## Push / CI reality", "",
          f"- git checkouts: {summary['git_checkouts']}/{summary['repos']}; "
          f"GitHub remotes: {summary['github_remotes']}",
          f"- CI systems found: {summary['ci_systems'] or 'none'}",
          f"- `act` applicable: **{summary['act_applicable']}** "
          f"({'GH Actions present — advisory local runs possible' if summary['act_applicable'] else 'no GH Actions workflows — act is moot; de-scope it'})",
          f"- dirty worktrees: {', '.join(summary['dirty_worktrees']) or 'none'}",
          f"- git checkouts we could NOT probe (HEAD/dirtiness UNKNOWN, not clean): "
          f"{', '.join(summary['git_unprobeable']) or 'none'}", ""]

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
            f"{','.join(r.ci_systems) or '—'} | "
            f"{','.join(r.build_systems) or '**not probed**'} | "
            f"{','.join(r.jdk_versions) or '—'} | {','.join(r.coverage_tools) or '**none**'} | "
            f"{','.join(r.task_runners) or '—'} |"
        )

    unprobed = sorted(r.alias for r in reports if not r.build_systems)
    if unprobed:
        L += ["", f"> **Not probed:** {', '.join(unprobed)} — no build system this diagnostic "
                  f"understands ({', '.join(sorted(STACK_PROBES))}). It does not examine "
                  f"{', '.join(sorted(UNPROBED))}. **A blank row here means 'not looked at', "
                  f"not 'nothing configured'** — do not read it as a finding."]

    notes = [(r.alias, n) for r in sorted(reports, key=lambda x: x.alias.lower()) for n in r.notes]
    if notes:
        L += ["", "## Notes", ""] + [f"- **{alias}**: {n}" for alias, n in notes]
    return "\n".join(L) + "\n"


#: The one directory inside this repo that may receive a workspace scan. Gitignored.
def load_installer():
    """Import `sdlc-init.py` for its workspace reader.

    The hyphen makes it un-importable by name, hence the spec dance. The direction matters
    and only works one way: this Tier-2 diagnostic may depend on the stdlib-only installer,
    never the reverse — the installer importing this file would drag PyYAML onto a path
    gated as dependency-free.
    """
    import importlib.util
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("sdlc_init", root / "sdlc-init.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init"] = mod
    spec.loader.exec_module(mod)
    return mod


def check_output_dir(out: Path, repo_root: Path | None = None) -> str | None:
    """This report's binding of the shared output-location rule. See `outdir`."""
    return _check_output_dir(
        out, repo_root, what="report",
        detail=(f"This is a raw inventory of your workspace — component paths, remotes, "
                f"commit SHAs. It belongs in {SCRATCH_DIR}/ and only its CONCLUSIONS belong "
                f"in a document you write by hand."),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", metavar="TOML",
                    help="your filled-in sdlc-workspace.toml. Omit it to probe the repository "
                         "you are standing in; a multi-repo workspace needs one, because the "
                         "root, the include/exclude globs and the component roles all live "
                         "there and a tool that guessed them would be right on one machine")
    ap.add_argument("--target", metavar="DIR",
                    help="where to start looking for the enclosing repository (default: the "
                         "current directory). Cannot be combined with --workspace")
    ap.add_argument("--out", default=".tmp/graph", metavar="DIR",
                    help="output directory (default: .tmp/graph). Committed trees are refused. "
                         "This report is about a workspace even when the workspace is one "
                         "repository, so it has no in-repo default")
    args = ap.parse_args(argv)

    if args.target and args.workspace:
        print("!! --target and --workspace cannot be combined: --workspace already says which "
              "components to probe, and --target only says where to look for one.")
        return 2

    out = Path(args.out)
    if refusal := check_output_dir(out):
        print(f"!! {refusal}")
        return 2

    # Reuse the installer's reader and discovery so "which directories are components" has
    # exactly one implementation. A second glob semantic here would answer the same
    # question differently from the profile the harness actually runs on.
    si = load_installer()
    try:
        ws = (si.read_workspace(Path(args.workspace)) if args.workspace
              else si.workspace_for_repo(Path(args.target) if args.target else None))
        components = si.discover_components(ws)["components"]
    except si.WorkspaceError as exc:
        print(f"!! {exc}")
        return 2

    print(f"probing {len(components)} components under {ws['root']} …")
    reports = []
    for comp in components:
        reports.append(probe_repo(comp["name"], ws["root"] / comp["path"], rel=comp["path"]))
        print(f"  · {comp['name']}")

    summary = summarize(reports)
    out.mkdir(parents=True, exist_ok=True)
    # LF on every platform (T546), like the graph documents beside it.
    (out / "workspace_report.json").write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "summary": summary,
                    "repos": [asdict(r) for r in reports]},
                   indent=2, sort_keys=True),
        encoding="utf-8", newline="\n",
    )
    report_md = render(reports, summary)
    (out / "workspace_report.md").write_text(report_md, encoding="utf-8", newline="\n")
    print("\n" + report_md)
    print(f"written: {out / 'workspace_report.json'} and workspace_report.md")
    print("NOTE: this output names your components and their paths. It is evidence for you, "
          "not an artifact to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
