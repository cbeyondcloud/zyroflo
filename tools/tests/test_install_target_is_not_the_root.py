"""T512: an install whose target is not the workspace root.

A component's `path` is relative to the WORKSPACE ROOT - discovery finds it that way, a
`[[component]]` author writes it that way, and a design document names a file that way
(`orders/pom.xml`). Every consumer joined it onto the TARGET instead, so an install into
`ws/` whose workspace said `root = "repos"` rendered `path: "orders"`, which resolved to
nothing, and no run could start: `intake` refused, "cannot change to .../ws/orders".

The profile now says where the root is (`workspace_root`), once, only when it is not the
target itself, and every consumer asks `policy.component_dir`. These tests spawn the real
installer and the real consumers, because what a person sees is a process.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.tests import _project  # noqa: E402

INSTALLER = ROOT / "sdlc-init.py"
SPEC = "Add an index on the orders table so the nightly report stops timing out."


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), "-c", "user.name=t", "-c", "user.email=t@t",
                    *args], check=True, capture_output=True)


def _component(where: Path) -> None:
    where.mkdir(parents=True, exist_ok=True)
    (where / "README.md").write_text(f"{where.name}\n", encoding="utf-8")
    _git(where, "init", "-q")
    _git(where, "add", "-A")
    _git(where, "commit", "-q", "-m", "init")


def _install(target: Path, workspace: Path, *args: str,
             env: dict | None = None) -> subprocess.CompletedProcess:
    _project.seed(target)
    return subprocess.run([sys.executable, str(INSTALLER), "--workspace", str(workspace),
                           "--target", str(target), "--no-graph", *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600, env=env)


def _intake(target: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "tools" / "sdlc" / "intake.py"),
                           "--target", str(target), "--text", SPEC, "--pick", "orders"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600)


def _profile(target: Path) -> str:
    return (target / ".sdlc" / "profile.yml").read_text(encoding="utf-8")


def _root_line(text: str) -> str | None:
    found = re.search(r'^workspace_root: "([^"]*)"(?:[ ]+#.*)?$', text, re.M)
    return found.group(1) if found else None


def _paths(text: str) -> list[str]:
    return re.findall(r'^    path: "([^"]*)"$', text, re.M)


def _above(tmp_path: Path) -> Path:
    """`ws/` holds the workspace file; its components live in `ws/repos/`."""
    target = tmp_path / "ws"
    for name in ("orders", "billing"):
        _component(target / "repos" / name)
    (target / "sdlc-workspace.toml").write_text(
        'schema_version = 1\nroot = "repos"\nname = ""\nprofile = ""\n', encoding="utf-8")
    return target


def test_an_install_above_the_workspace_root_can_start_a_run(tmp_path: Path):
    target = _above(tmp_path)
    run = _install(target, target / "sdlc-workspace.toml")
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
    text = _profile(target)
    assert _root_line(text) == "repos"
    assert _paths(text) == ["billing", "orders"], "paths stay as the workspace root sees them"
    opened = _intake(target)
    assert opened.returncode == 0, opened.stdout[-3000:] + opened.stderr[-2000:]


def _tool(target: Path, rel: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / rel), "--target", str(target), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600)


def test_every_consumer_reaches_the_components_from_a_target_that_is_not_the_root(
        tmp_path: Path):
    """B9-9: the run lifecycle end to end, spawned, from an install above the root - the
    trees are made in the component repositories, listed and removed, and the supply-chain
    gate finds both components. Before T512 each of these looked under the target and
    refused ("not a directory")."""
    target = _above(tmp_path)
    assert _install(target, target / "sdlc-workspace.toml").returncode == 0
    opened = subprocess.run([sys.executable, str(ROOT / "tools" / "sdlc" / "intake.py"),
                             "--target", str(target), "--text", SPEC, "--id", "b9run"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=600)
    assert opened.returncode == 0, opened.stdout[-3000:] + opened.stderr[-2000:]
    for args in (["--create"], ["--list"], ["--remove"]):
        run = _tool(target, "tools/sdlc/worktree.py", "--run", "b9run", *args)
        assert run.returncode == 0, (args, run.stdout[-3000:] + run.stderr[-2000:])
        assert "not a directory" not in run.stdout + run.stderr
    sca = _tool(target, "tools/review/sca.py")
    said = sca.stdout + sca.stderr
    assert "not a directory" not in said, said[-3000:]
    assert "billing" in said and "orders" in said, said[-3000:]


def test_an_install_beside_its_components_names_the_root_above_it(tmp_path: Path):
    """The orchestration repository beside the components it drives: the target is one
    component, and the workspace root is its parent."""
    work = tmp_path / "work"
    for name in ("orch", "orders", "billing"):
        _component(work / name)
    target = work / "orch"
    (target / "sdlc-workspace.toml").write_text(
        'schema_version = 1\nroot = ".."\nname = ""\nprofile = ""\n', encoding="utf-8")
    run = _install(target, target / "sdlc-workspace.toml")
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
    text = _profile(target)
    assert _root_line(text) == ".."
    assert _paths(text) == ["billing", "orch", "orders"]
    from tools.sdlc import policy
    assert policy.component_dir(target, "orders").resolve() == (work / "orders").resolve()
    assert policy.component_dir(target, "orch").resolve() == target.resolve()


def test_an_install_whose_target_is_the_root_writes_no_workspace_root(tmp_path: Path):
    """The ordinary layout is unchanged: the field is written only when it is not `.`."""
    target = tmp_path / "ws"
    for name in ("orders", "billing"):
        _component(target / name)
    (target / "sdlc-workspace.toml").write_text(
        'schema_version = 1\nroot = "."\nname = ""\nprofile = ""\n', encoding="utf-8")
    run = _install(target, target / "sdlc-workspace.toml")
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
    text = _profile(target)
    assert "workspace_root" not in text
    assert _paths(text) == ["billing", "orders"]


def test_a_root_no_relative_path_reaches_is_refused_before_anything_is_written(
        tmp_path: Path):
    """A root on another drive cannot be named in a committed file. Refused, by name, at
    exit 1, with nothing written - spawned, because a unit test cannot tell this refusal
    from a traceback. The other drive is simulated: `os.path.relpath` raises for the root,
    exactly as it does across Windows drives."""
    target = _above(tmp_path)
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "sitecustomize.py").write_text(
        "import os, os.path\n"
        "_real = os.path.relpath\n"
        "def _relpath(path, start=None):\n"
        "    if os.path.normcase(os.path.abspath(path)) == os.environ['STUB_ROOT']:\n"
        "        raise ValueError(\"path is on mount 'Z:', start on mount 'C:'\")\n"
        "    return _real(path, start) if start is not None else _real(path)\n"
        "os.path.relpath = _relpath\n", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(stub),
               STUB_ROOT=os.path.normcase(str((target / "repos").resolve())))
    run = _install(target, target / "sdlc-workspace.toml", env=env)
    said = run.stdout + run.stderr
    assert run.returncode == 1, said[-3000:]
    assert "!! refusing" in said and "another drive" in said, said[-3000:]
    assert "Traceback" not in said
    assert not (target / ".sdlc").exists(), "a refusal writes nothing"


def test_a_cadence_job_whose_checkout_cannot_reach_the_root_is_withheld(tmp_path: Path):
    """The job checks out the target alone. A workspace file inside it whose `root` names a
    directory beside it would build nothing every week - withheld with the reason, while the
    install itself goes ahead (the installer's convention for the same predicate)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sdlc_init_t512", INSTALLER)
    si = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(si)
    work = tmp_path / "work"
    for name in ("orch", "orders"):
        _component(work / name)
    target = work / "orch"
    inside = target / "sdlc-workspace.toml"
    inside.write_text('schema_version = 1\nroot = ".."\nname = ""\nprofile = ""\n',
                      encoding="utf-8")
    reason = si.no_workspace_in_checkout_reason(target, inside, "--snapshot-cadence",
                                                "every week")
    assert reason and "outside" in reason and "install itself is unaffected" in reason
    inside.write_text('schema_version = 1\nroot = "."\nname = ""\nprofile = ""\n',
                      encoding="utf-8")
    assert si.no_workspace_in_checkout_reason(target, inside, "--snapshot-cadence",
                                              "every week") is None


def test_a_rerun_over_a_profile_that_does_not_name_its_root_says_the_one_line_fix(
        tmp_path: Path):
    """A profile written before T512 keeps root-relative paths without saying where the
    root is. The re-run's `.new` has the field, but the file an adopter runs from is the
    one kept - so the NOTE names the line to add, not `--force`, which would replace every
    preserved file to fix one line."""
    target = _above(tmp_path)
    assert _install(target, target / "sdlc-workspace.toml").returncode == 0
    profile = target / ".sdlc" / "profile.yml"
    profile.write_bytes(re.sub(rb'(?m)^workspace_root: "repos"[^\r\n]*\r?\n', b"",
                               profile.read_bytes()))
    run = _install(target, target / "sdlc-workspace.toml")
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
    assert 'workspace_root: "repos"' in run.stdout, run.stdout[-3000:]


@pytest.mark.parametrize("kept, said", [
    ("workspace_root: 'repos'", None),
    ('workspace_root: "repos"   # where the components live', None),
    ('workspace_root: "wrong"', 'Change that line to: workspace_root: "repos"'),
])
def test_the_rerun_note_reads_the_kept_line_as_its_consumers_do(tmp_path: Path, kept: str,
                                                                said: str | None):
    """B9-7: the NOTE re-parsed the kept file with a raw pattern while the consumers read
    it through `policy.workspace_root`, so a correct install spelled in single quotes or
    with a trailing comment was told to add a line it already had - and a wrong value was
    told to "add" rather than "change"."""
    target = _above(tmp_path)
    assert _install(target, target / "sdlc-workspace.toml").returncode == 0
    profile = target / ".sdlc" / "profile.yml"
    profile.write_bytes(re.sub(rb'(?m)^workspace_root: "repos"[^\r\n]*(?=\r?\n)',
                               kept.encode(), profile.read_bytes()))
    run = _install(target, target / "sdlc-workspace.toml")
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
    if said is None:
        assert "workspace_root" not in run.stdout, run.stdout[-3000:]
    else:
        assert said in run.stdout, run.stdout[-3000:]


def test_policy_reads_workspace_root_at_the_top_level_only(tmp_path: Path):
    from tools.sdlc import policy
    lines = ['vcs:', '  workspace_root: "nested"', 'workspace_root: "repos"', 'components:']
    assert policy.workspace_root(tmp_path, lines) == "repos"
    assert policy.workspace_root(tmp_path, ["components:"]) == "."
    with pytest.raises(policy.ProfileError, match="RELATIVE"):
        policy.workspace_root(tmp_path, ['workspace_root: "' + "D" + ':/work"'])


def test_a_design_document_names_a_component_file_as_the_workspace_root_does(
        tmp_path: Path):
    """D9-16: `adr.locate` matches a token by the component's PREFIX, which is its `path`
    as the workspace root sees it. Rendered relative to the target instead, `../orders`
    matched nothing a design document writes, so a real file read as "not in the
    workspace". The prefix stays root-relative; the file is found where it is."""
    target = _above(tmp_path)
    assert _install(target, target / "sdlc-workspace.toml").returncode == 0
    from tools.sdlc import adr
    assert adr.locate(target, "orders/README.md") == [("orders", "README.md")]


#: A record's `path`, in either quote style, by subscript or by `.get`.
_PATH_ITEM = r"""(?P<var>\w+)(?:\[\s*["']path["']\s*\]|\.get\(\s*["']path["'])"""

#: The shapes a component-to-directory join takes when it skips `component_dir` (B9-8):
#: `/` onto the target, `Path(target, ...)`, `target.joinpath(...)` and `os.path.join(...)`,
#: keyed on the `path` subscript whatever the record is called. What a line-level pattern
#: cannot see, and does not claim to: the target under another name (`t / comp["path"]`) and
#: a join two statements away (`rel = comp["path"]; target / rel`).
_JOINS = [re.compile(shape + _PATH_ITEM) for shape in (
    r"""(?:target|root)\)?\s*/\s*\(?\s*""",
    r"""Path\(\s*(?:target|root)\s*,\s*""",
    r"""(?:target|root)\)?\.joinpath\(\s*""",
    r"""os\.path\.join\(\s*(?:str\(\s*)?(?:target|root)\s*\)?\s*,\s*""",
)]

#: Joins onto the target that are not components, by module and record, with the reason.
_NOT_COMPONENTS = {
    ("tools/sdlc/learning.py", "proposal"): "a file a learning proposal names",
    ("tools/sdlc/learning.py", "applied"): "the file an applied proposal changed",
}


def _joins(source: str) -> list[tuple[int, str]]:
    """`(line, record)` for each place `source` joins a record's `path` onto the target."""
    return [(n, m.group("var")) for n, line in enumerate(source.splitlines(), 1)
            for pattern in _JOINS for m in pattern.finditer(line)]


@pytest.mark.parametrize("line", [
    'where = Path(target) / comp["path"]',
    'where = target / (spec["path"] or ".")',
    'git(Path(target) / entry["path"], *args)',
    'dirty = dirty_paths(Path(target) / (comp.get("path") or "."))',
    "where = target / comp['path']",
    'where = Path(target, comp["path"])',
    'where = target.joinpath(comp.get("path") or ".")',
    'where = os.path.join(str(target), comp["path"])',
    'where = target / rec["path"]',
])
def test_the_join_detector_sees_every_shape_it_names(line: str):
    """Positive controls, one per shape: a detector that matches nothing passes every tree."""
    assert len(_joins(line)) == 1, line


def test_the_join_detector_passes_the_helper_itself():
    assert _joins('where = guardrails.component_dir(target, comp["path"])') == []


def test_no_module_under_sdlc_or_review_joins_a_component_path_itself():
    """Every consumer of a component's directory - they live in `tools/sdlc` and
    `tools/review` - asks `policy.component_dir`: one missed call site is a component that
    resolves under the wrong directory for exactly the installs T512 is for. (The graph
    layer joins onto the WORKSPACE root, which is right, and is not what this checks.)"""
    offenders = []
    for folder in (ROOT / "tools" / "sdlc", ROOT / "tools" / "review"):
        for py in sorted(folder.glob("*.py")):
            source = py.read_text(encoding="utf-8")
            ast.parse(source)
            rel = py.relative_to(ROOT).as_posix()
            offenders += [f"{rel}:{n} ({var})" for n, var in _joins(source)
                          if (rel, var) not in _NOT_COMPONENTS]
    assert offenders == []
