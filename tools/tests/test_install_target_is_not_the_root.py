"""T512: an install whose target is not the workspace root.

A component's `path` is relative to the WORKSPACE ROOT - discovery finds it that way, a
`[[component]]` author writes it that way, and a design document names a file that way
(`orders/pom.xml`). Every consumer joined it onto the TARGET instead, so an install into
`ws/` whose workspace said `root = "repos"` rendered `path: "orders"`, which resolved to
nothing, and no run could start: `intake` refused, "cannot change to .../ws/orders".

The profile now says where the root is (`workspace_root`), once, only when it is not the
target itself, and every consumer asks `policy.component_dir`. These tests spawn the real
installer and the real consumers, because what a person sees is a process.

T582: a root on another drive has no relative path to it, and an absolute one in a committed
file is wrong on the next machine. T512 refused the whole install for that - the dry run, a
graph-only estate install and a load from a published snapshot included, none of which
opens a component. The owner ruled to withhold instead: the install goes ahead, and the
profile carries `workspace_root_unreachable` in place of `workspace_root`, which every run
step that opens a component refuses on, with the reason. Leaving `workspace_root` out alone
would not do - its reader answers `.`, and the run steps would look inside the target.
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


#: Makes `os.path.relpath` raise, as it does across Windows drives, for any two paths on
#: opposite sides of the directory `STUB_DRIVE` names - the "other drive". Every Python
#: process the installer spawns imports it too, the graph build included, so a relpath
#: anywhere across the two is met as it would be on a real machine.
_ANOTHER_DRIVE = (
    "import os, os.path\n"
    "_real = os.path.relpath\n"
    "_drive = os.path.normcase(os.path.realpath(os.environ['STUB_DRIVE']))\n"
    "def _on(p):\n"
    "    p = os.path.normcase(os.path.realpath(p))\n"
    "    return p == _drive or p.startswith(_drive + os.sep)\n"
    "def _relpath(path, start=None):\n"
    "    if _on(path) != _on(os.curdir if start is None else start):\n"
    "        raise ValueError(\"path is on mount 'Z:', start on mount 'C:'\")\n"
    "    return _real(path) if start is None else _real(path, start)\n"
    "os.path.relpath = _relpath\n")

#: The line an install across drives writes in place of `workspace_root` (T582).
_WITHHELD = 'workspace_root_unreachable: "another drive or share"'


def _another_drive(tmp_path: Path, other: Path) -> dict:
    """An environment in which `other` sits on another drive from everything else."""
    stub = tmp_path / "stub"
    stub.mkdir(exist_ok=True)
    (stub / "sitecustomize.py").write_text(_ANOTHER_DRIVE, encoding="utf-8")
    return dict(os.environ, PYTHONPATH=str(stub), STUB_DRIVE=str(other))


def _across(tmp_path: Path) -> tuple[Path, Path, dict]:
    """The target on one drive; its workspace file and components, `repos/`, on another."""
    other = tmp_path / "otherdrive"
    for name in ("orders", "billing"):
        _component(other / "repos" / name)
    workspace = other / "sdlc-workspace.toml"
    workspace.write_text('schema_version = 1\nroot = "repos"\nname = ""\nprofile = ""\n',
                         encoding="utf-8")
    target = tmp_path / "orch"
    _component(target)
    return target, workspace, _another_drive(tmp_path, other)


def test_the_stub_puts_one_directory_on_another_drive(tmp_path: Path):
    """The positive control: the simulation raises across the two sides and only there."""
    other = tmp_path / "otherdrive"
    (other / "repos").mkdir(parents=True)
    probe = ("import os\n"
             f"print(os.path.relpath({str(other / 'repos')!r}, {str(other)!r}))\n"
             "try:\n"
             f"    os.path.relpath({str(other / 'repos')!r}, {str(tmp_path)!r})\n"
             "except ValueError:\n"
             "    print('raised')\n")
    run = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         env=_another_drive(tmp_path, other), timeout=120)
    assert run.stdout.split() == ["repos", "raised"], run.stdout + run.stderr


def test_a_root_on_another_drive_withholds_the_run_steps_and_installs_the_rest(
        tmp_path: Path):
    """T582: exit 0 and the scaffold written, the profile says why the run steps refuse,
    and the first of them - intake, which opens a run - refuses with that reason and opens
    nothing. Before the ruling this install refused at exit 1 and wrote nothing."""
    target, workspace, env = _across(tmp_path)
    run = _install(target, workspace, env=env)
    said = run.stdout + run.stderr
    assert run.returncode == 0, said[-3000:]
    assert "Traceback" not in said and "!! refusing" not in said, said[-3000:]
    assert "the /sdlc run steps are withheld" in run.stdout, run.stdout[-3000:]
    # Review R3: said of the line this install writes, and true of what still works -
    # `config --check` refuses a withheld run by design, so "the config" was not.
    assert "line this install writes in place of workspace_root" in run.stdout
    assert "the graph, its tools and `sdlc config set` work as they are" in run.stdout
    assert "run  /sdlc" not in run.stdout, "the sign-off hands over a run that refuses"
    # Review R7: the sign-off's remedy, like the NOTE's, covers a share too.
    assert ("stay withheld until this directory and the workspace root are on one drive "
            "or share" in run.stdout), run.stdout[-3000:]
    text = _profile(target)
    assert re.search(r"(?m)^" + re.escape(_WITHHELD) + r"[ ]+#", text), text[:3000]
    assert _root_line(text) is None, "no relative path exists to write"
    assert _paths(text) == ["billing", "orders"]
    for rel in (".sdlc/policy.yml", ".claude/skills/sdlc/SKILL.md"):
        assert (target / rel).is_file(), rel
    from tools.sdlc import schema
    keys = set(re.findall(r"(?m)^([a-z_]+):", text))
    assert "workspace_root_unreachable" in keys
    assert sorted(keys - set(schema.profile_schema()["properties"])) == []
    _git(target, "add", "-A")
    _git(target, "commit", "-q", "-m", "install")
    opened = _intake(target)
    said = opened.stdout + opened.stderr
    assert opened.returncode == 2, said[-3000:]
    assert "run steps are withheld" in said and "another drive" in said, said[-3000:]
    assert "Traceback" not in said
    assert not (target / ".sdlc" / "runs").exists(), "a withheld run opens nothing"


def test_a_dry_run_across_drives_says_what_it_would_withhold(tmp_path: Path):
    target, workspace, env = _across(tmp_path)
    run = _install(target, workspace, "--dry-run", env=env)
    said = run.stdout + run.stderr
    assert run.returncode == 0, said[-3000:]
    assert "the /sdlc run steps are withheld" in run.stdout, run.stdout[-3000:]
    assert not (target / ".sdlc").exists(), "a dry run writes nothing"


def test_every_run_step_refuses_while_the_withholding_line_stands(tmp_path: Path):
    """The line is read where every consumer asks, `policy.component_dir`: a run opened
    before it arrived - a layout that moved - cannot make its trees, spawned, and says why
    rather than answering with a traceback; a design document's file cannot be placed."""
    target = _above(tmp_path)
    assert _install(target, target / "sdlc-workspace.toml").returncode == 0
    opened = subprocess.run([sys.executable, str(ROOT / "tools" / "sdlc" / "intake.py"),
                             "--target", str(target), "--text", SPEC, "--id", "t582run"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=600)
    assert opened.returncode == 0, opened.stdout[-3000:] + opened.stderr[-2000:]
    profile = target / ".sdlc" / "profile.yml"
    profile.write_bytes(re.sub(rb'(?m)^workspace_root: "repos"[^\r\n]*(?=\r?\n)',
                               _WITHHELD.encode(), profile.read_bytes()))
    run = _tool(target, "tools/sdlc/worktree.py", "--run", "t582run", "--create")
    said = run.stdout + run.stderr
    assert run.returncode != 0, said[-3000:]
    assert "run steps are withheld" in said and "another drive" in said, said[-3000:]
    assert "Traceback" not in said, said[-3000:]
    trees = subprocess.run(["git", "-C", str(target / "repos" / "orders"), "worktree",
                            "list"], capture_output=True, text=True, check=True).stdout
    assert len(trees.splitlines()) == 1, f"no tree is made while withheld:\n{trees}"
    from tools.sdlc import adr, policy
    with pytest.raises(policy.ProfileError, match="run steps are withheld"):
        adr.locate(target, "orders/README.md")


def test_the_graph_still_builds_and_registers_across_drives(tmp_path: Path):
    """B9-6's lost flow, back: a graph-only estate install across drives builds the real
    graph from the other drive and registers the server - spawned, the build included, so
    a relative path taken anywhere across the two drives is met as it would be."""
    import shutil
    fixture = ROOT / "tools" / "graph" / "tests" / "fixtures" / "orderflow"
    other = tmp_path / "otherdrive"
    shutil.copytree(fixture, other / "orderflow")
    target = tmp_path / "orch"
    _component(target)
    _project.seed(target)
    run = subprocess.run([sys.executable, str(INSTALLER), "--target", str(target),
                          "--workspace", str(other / "orderflow" / "workspace.toml"),
                          "--estate"], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=900, env=_another_drive(tmp_path, other))
    said = run.stdout + run.stderr
    assert run.returncode == 0, said[-4000:]
    assert "the /sdlc run steps are withheld" in run.stdout, run.stdout[-3000:]
    graph = target / ".sdlc" / "graph" / "graph.jsonl"
    assert "orders-api" in graph.read_text(encoding="utf-8"), said[-3000:]
    assert "sdlc-graph" in (target / ".mcp.json").read_text(encoding="utf-8")
    assert _WITHHELD in _profile(target)


def test_a_snapshot_load_across_drives_goes_ahead(tmp_path: Path):
    """Review R6: the last of B9-6's flows, `--estate --from-snapshot`, which opens no
    component at all - it loads the shards each component published. Published here
    first, then loaded across drives: the whole snapshot, and the run steps withheld."""
    import shutil
    other = tmp_path / "otherdrive"
    shutil.copytree(ROOT / "tools" / "graph" / "tests" / "fixtures" / "orderflow",
                    other / "orderflow")
    workspace = other / "orderflow" / "workspace.toml"
    published = subprocess.run(
        [sys.executable, "-m", "tools.graph.build", "--workspace", str(workspace),
         "--out", str(tmp_path / "published"), "--publish-snapshot"], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    assert published.returncode == 0, published.stdout[-3000:] + published.stderr[-2000:]
    target = tmp_path / "orch"
    _component(target)
    _project.seed(target)
    run = subprocess.run([sys.executable, str(INSTALLER), "--target", str(target),
                          "--workspace", str(workspace), "--estate", "--from-snapshot"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=900, env=_another_drive(tmp_path, other))
    said = run.stdout + run.stderr
    assert run.returncode == 0, said[-4000:]
    assert "loaded 7 of 7 component shard(s)" in said, said[-3000:]
    assert "the /sdlc run steps are withheld" in run.stdout, run.stdout[-3000:]
    assert _WITHHELD in _profile(target)


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


def test_every_top_level_key_an_install_writes_is_described_by_the_schema(tmp_path: Path):
    """B9-15: `workspace_root` was described inside the `release:` block - a closed block
    nothing reads it from - and not at the top level, where the installer writes it. Every
    top-level key a real install renders, the new one included, is described there."""
    from tools.sdlc import schema
    target = _above(tmp_path)
    assert _install(target, target / "sdlc-workspace.toml").returncode == 0
    keys = set(re.findall(r"(?m)^([a-z_]+):", _profile(target)))
    described = schema.profile_schema()["properties"]
    assert "workspace_root" in keys
    assert sorted(keys - set(described)) == []
    assert "workspace_root" not in described["release"]["properties"]


SPELLINGS = ["'repos'", '"repos"', "repos", '"repos"   # a comment', "repos # a comment",
             '".."', "'..'", '"a#b"', '""', "''", '"repos" extra',
             # T583 (batch-9 review B9-19): inner spaces the consumers strip, and a TAB
             # before the comment - each read differently by the mirror, so a re-run
             # over a working install printed a spurious "change that line" NOTE.
             '" repos "', "repos\t# a comment"]


def test_the_installer_reads_the_kept_line_exactly_as_the_consumers_do(tmp_path: Path):
    """B9-18: the installer cannot import `policy` (it is stdlib-only and bundles into one
    script), so it mirrors `policy.workspace_root` - and a mirror drifts. This holds the two
    readers together over every spelling a person might write: `"repos" extra` read as
    `repos` kept the NOTE silent on a profile the run steps would refuse."""
    import importlib.util
    from tools.sdlc import policy
    spec = importlib.util.spec_from_file_location("sdlc_init_b918", INSTALLER)
    si = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(si)
    target = _above(tmp_path)
    assert _install(target, target / "sdlc-workspace.toml").returncode == 0
    profile = target / ".sdlc" / "profile.yml"
    written = profile.read_bytes()
    for spelling in SPELLINGS:
        profile.write_bytes(re.sub(rb'(?m)^workspace_root: "repos"[^\r\n]*(?=\r?\n)',
                                   b"workspace_root: " + spelling.encode(), written))
        assert si.kept_workspace_root(profile) == policy.workspace_root(target), spelling


# ── T582: the run steps withheld across drives ────────────────────────────────


def _swap_root_line(target: Path, line: str) -> None:
    """Replace the kept profile's `workspace_root` line - or, given "", remove it."""
    profile = target / ".sdlc" / "profile.yml"
    profile.write_bytes(re.sub(rb'(?m)^workspace_root: "[^"]*"[^\r\n]*' + (
        rb"(?=\r?\n)" if line else rb"\r?\n"), line.encode(), profile.read_bytes()))


@pytest.mark.parametrize("first, kept, again, said", [
    # withheld when installed, reachable now: the line that releases the run steps
    ("across", None, "near", 'replace that line with: workspace_root: "../otherdrive/repos"'),
    # reachable when installed, across drives now: the line that withholds them
    ("near", None, "across", f"Replace that line with: {_WITHHELD}"),
    # a profile from before T512, which names no root, across drives now
    ("near", "", "across", f"Add one top-level line to it: {_WITHHELD}"),
])
def test_a_rerun_names_the_line_that_withholds_or_releases_the_run_steps(
        tmp_path: Path, first: str, kept: str | None, again: str, said: str):
    """The re-run's `.new` carries the right line, but the file the run steps read is the
    one kept, and `--force` would replace every preserved file to fix one line - so the
    NOTE names the line, both ways round (T582, as B9-7 for `workspace_root`)."""
    target, workspace, env = _across(tmp_path)
    envs = {"across": env, "near": None}
    assert _install(target, workspace, env=envs[first]).returncode == 0
    if kept is not None:
        _swap_root_line(target, kept)
    run = _install(target, workspace, env=envs[again])
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
    assert said in run.stdout, run.stdout[-3000:]


def test_a_rerun_that_is_still_across_drives_names_no_line(tmp_path: Path):
    """The kept profile already withholds, and differs only by the adopter's own edit: the
    NOTE about the edit stands, and no line needs changing."""
    target, workspace, env = _across(tmp_path)
    assert _install(target, workspace, env=env).returncode == 0
    profile = target / ".sdlc" / "profile.yml"
    profile.write_bytes(profile.read_bytes() + b"# the adopter's own note\n")
    run = _install(target, workspace, env=env)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
    assert "your edited .sdlc/profile.yml was kept" in run.stdout, run.stdout[-3000:]
    assert "that line" not in run.stdout and "top-level line" not in run.stdout


def test_a_rerun_from_the_root_itself_names_the_line_to_delete(tmp_path: Path):
    target = tmp_path / "ws"
    for name in ("orders", "billing"):
        _component(target / name)
    (target / "sdlc-workspace.toml").write_text(
        'schema_version = 1\nroot = "."\nname = ""\nprofile = ""\n', encoding="utf-8")
    assert _install(target, target / "sdlc-workspace.toml").returncode == 0
    profile = target / ".sdlc" / "profile.yml"
    profile.write_bytes(re.sub(rb"(?m)^components:", _WITHHELD.encode() + b"\ncomponents:",
                               profile.read_bytes(), count=1))
    run = _install(target, target / "sdlc-workspace.toml")
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
    assert "To run /sdlc, delete that line" in run.stdout, run.stdout[-3000:]


def test_a_rerun_with_both_lines_kept_does_not_ask_for_a_second_root_line(tmp_path: Path):
    """Review R4: a hand-edited profile carrying both lines, now reachable where its
    `workspace_root` says. Replacing the withholding line, as the NOTE said, set the key
    twice, which the reader refuses; the line to delete is the withholding one alone."""
    target = _above(tmp_path)
    assert _install(target, target / "sdlc-workspace.toml").returncode == 0
    profile = target / ".sdlc" / "profile.yml"
    profile.write_bytes(re.sub(rb"(?m)^workspace_root: ", _WITHHELD.encode() +
                               b"\nworkspace_root: ", profile.read_bytes(), count=1))
    run = _install(target, target / "sdlc-workspace.toml")
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
    assert ("delete that line (the workspace_root line is already right)"
            in run.stdout), run.stdout[-3000:]


def test_a_rerun_from_the_root_itself_names_a_kept_root_line_to_delete(tmp_path: Path):
    """T586 (review R1): the root is the target itself now, and the kept profile still
    names `repos` - where a directory of the same name holds a different repository, so
    every run step would work on it without a word. The re-run named no line at all."""
    target = _above(tmp_path)
    assert _install(target, target / "sdlc-workspace.toml").returncode == 0
    for name in ("orders", "billing"):
        _component(target / name)
    (target / "sdlc-workspace.toml").write_text(
        'schema_version = 1\nroot = "."\nname = ""\nprofile = ""\n', encoding="utf-8")
    run = _install(target, target / "sdlc-workspace.toml")
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
    assert ("says workspace_root is 'repos', but the workspace root is this directory now"
            in run.stdout), run.stdout[-3000:]
    assert "wrong place. Delete that line" in run.stdout, run.stdout[-3000:]


def test_policy_refuses_while_the_withholding_line_stands(tmp_path: Path):
    from tools.sdlc import policy
    for lines in ([_WITHHELD, "components:"],
                  ['workspace_root: "repos"', _WITHHELD],        # wherever it stands
                  ["workspace_root_unreachable:", "components:"]):   # whatever it says
        with pytest.raises(policy.ProfileError, match="run steps are withheld"):
            policy.workspace_root(tmp_path, lines)
    nested = ["vcs:", "  " + _WITHHELD, 'workspace_root: "repos"']
    assert policy.workspace_root(tmp_path, nested) == "repos", "read at the top level only"


WITHHELD_SPELLINGS = [_WITHHELD, "workspace_root_unreachable: another drive",
                      "workspace_root_unreachable:", 'workspace_root_unreachable: ""',
                      "# " + _WITHHELD, 'workspace_root: "repos"',
                      # The review's own, which the table did not list:
                      " " + _WITHHELD, _WITHHELD.replace(":", " :", 1), _WITHHELD.upper()]


def test_the_installer_reads_the_withholding_line_exactly_as_the_consumers_do(
        tmp_path: Path):
    """The installer's mirror of the key and of its reading (it cannot import `policy`),
    held to the consumers' answer over the spellings a person might leave behind."""
    import importlib.util
    from tools.sdlc import policy
    spec = importlib.util.spec_from_file_location("sdlc_init_t582", INSTALLER)
    si = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(si)
    assert si.WORKSPACE_ROOT_UNREACHABLE == policy.WORKSPACE_ROOT_UNREACHABLE_KEY
    target = _above(tmp_path)
    assert _install(target, target / "sdlc-workspace.toml").returncode == 0
    profile = target / ".sdlc" / "profile.yml"
    written = profile.read_bytes()
    for spelling in WITHHELD_SPELLINGS:
        profile.write_bytes(re.sub(rb'(?m)^workspace_root: "repos"[^\r\n]*(?=\r?\n)',
                                   spelling.encode(), written))
        try:
            policy.workspace_root(target)
            refused = False
        except policy.ProfileError:
            refused = True
        assert si.kept_workspace_root_unreachable(profile) == refused, spelling


def test_the_config_check_says_a_withheld_run_would_refuse_and_set_still_works(
        tmp_path: Path):
    """`sdlc config --check` answers whether a run would accept this configuration, and a
    withheld one would not. The configuration itself is fine, so changing it still works."""
    target = _above(tmp_path)
    assert _install(target, target / "sdlc-workspace.toml").returncode == 0
    _swap_root_line(target, _WITHHELD)
    check = _tool(target, "tools/sdlc/config.py", "--check")
    assert check.returncode == 1, check.stdout[-3000:] + check.stderr[-2000:]
    assert "run steps are withheld" in check.stdout, check.stdout[-3000:]
    change = _tool(target, "tools/sdlc/config.py", "set", "extra_gates=design", "--dry-run")
    assert change.returncode == 0, change.stdout[-3000:] + change.stderr[-2000:]
    assert "would apply" in change.stdout, change.stdout[-3000:]
