"""T706: `--manifest` says which missing files this repository should have.

`missing` holds every owned path absent under ANY tools and layers, so a caller could not
tell a deleted file the repository's own install writes from a layer it never chose.
`expected_missing` is the part of `missing` this target's own plain re-run writes - its
detected tools, its recorded (or, with no record, inferred) layers and its recorded
workspace, planned by the installer's own `plan_install_ops` - and null when the record
cannot be read. Each test SPAWNS the installer and failed against the installer before
T706, which printed no `expected_missing` key.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.tests import _project  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "sdlc-init.py"
REL = ".sdlc/install-options"
REVIEWER = ".claude/agents/sdlc-reviewer.md"
SETTINGS = ".claude/settings.json"
RUBRIC = ".sdlc/review-rubric.md"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(INSTALLER), *args],
                          capture_output=True, timeout=600)


def _text(proc: subprocess.CompletedProcess) -> str:
    return (proc.stdout + proc.stderr).decode("utf-8", "replace")


def _fixture(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_bytes(b'[project]\nname = "tiny"\nversion = "0.0.1"\n')
    (root / "tiny.py").write_bytes(b"X = 1\n")
    _project.seed(root)
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True,
                   timeout=120)
    return root


def _install(target: Path, *extra: str) -> None:
    proc = _run("--target", str(target), "--no-graph", *extra)
    assert proc.returncode == 0, _text(proc)


def _manifest(target: Path) -> dict:
    proc = _run("--manifest", "--target", str(target))
    assert proc.returncode == 0, _text(proc)
    m = json.loads(proc.stdout.decode("utf-8"))
    assert "expected_missing" in m, sorted(m)
    if m["expected_missing"] is not None:
        # The subset property, checked on every manifest this file reads.
        assert set(m["expected_missing"]) <= set(m["missing"]), m
        assert m["expected_missing"] == sorted(m["expected_missing"])
    return m


def _snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob("*")) if ".git" not in p.relative_to(root).parts}


def test_a_healthy_install_expects_nothing_and_a_deleted_owned_file_is_expected(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t)
    m = _manifest(t)
    assert m["expected_missing"] == [], m["expected_missing"]
    assert m["missing"], "an install without extras leaves owned paths absent"
    (t / RUBRIC).unlink()
    m = _manifest(t)
    assert RUBRIC in m["missing"] and m["expected_missing"] == [RUBRIC]


def test_a_layer_never_chosen_is_missing_only(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t)
    m = _manifest(t)
    assert REVIEWER in m["missing"] and REVIEWER not in m["expected_missing"]
    assert SETTINGS in m["missing"] and SETTINGS not in m["expected_missing"]


def test_a_deleted_file_of_a_recorded_layer_is_expected(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--claude-extras")
    assert _manifest(t)["expected_missing"] == []
    (t / REVIEWER).unlink()
    m = _manifest(t)
    assert m["expected_missing"] == [REVIEWER]


def test_a_removed_claude_md_block_is_expected(tmp_path):
    """The extras layer merges its /sdlc pointer into CLAUDE.md; a plain install with no
    graph writes no CLAUDE.md block, so there it is missing only."""
    t = _fixture(tmp_path / "py")
    _install(t, "--claude-extras")
    (t / "CLAUDE.md").write_bytes(b"# my own notes, no harness block\n")
    m = _manifest(t)
    assert m["expected_missing"] == ["CLAUDE.md"], m["expected_missing"]
    plain = _fixture(tmp_path / "plain")
    _install(plain)
    (plain / "CLAUDE.md").write_bytes(b"# my own notes, no harness block\n")
    m = _manifest(plain)
    assert "CLAUDE.md" in m["missing"] and "CLAUDE.md" not in m["expected_missing"]


def test_a_repository_with_no_record_uses_the_inference_rule(tmp_path):
    """Pre-T705: the extras' own files are all there, so the layer is inferred and its
    merged settings.json - which inference does not read - is expected once deleted."""
    t = _fixture(tmp_path / "py")
    _install(t, "--claude-extras")
    (t / REL).unlink()
    (t / SETTINGS).unlink()
    m = _manifest(t)
    assert m["options"] is None
    assert m["expected_missing"] == sorted([REL, SETTINGS]), m["expected_missing"]
    # Without the layer's files, nothing of it is inferred: missing only.
    t2 = _fixture(tmp_path / "plain")
    _install(t2)
    (t2 / REL).unlink()
    assert _manifest(t2)["expected_missing"] == [REL]


def test_an_unreadable_record_gives_null(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--claude-extras")
    (t / REVIEWER).unlink()
    for bad in (b"\xff\xfe\x00not text\n", b"claude-extras = maybe\n"):
        (t / REL).write_bytes(bad)
        m = _manifest(t)
        assert m["expected_missing"] is None, bad
        assert REVIEWER in m["missing"], bad


def test_a_recorded_workspace_that_is_gone_gives_null(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t)
    (t / REL).write_bytes((t / REL).read_bytes() + b'workspace = "conf/gone.toml"\n')
    assert _manifest(t)["expected_missing"] is None


def _standalone(at: Path) -> Path:
    at.mkdir(parents=True)
    shutil.copy2(INSTALLER, at / "sdlc-init.py")
    shutil.copytree(ROOT / "sdlc_templates", at / "sdlc_templates")
    return at / "sdlc-init.py"


GENERATED = (".sdlc/ledger_check.py", ".github/workflows/sdlc-ledger.yml",
             ".sdlc/pipeline.md", ".claude/skills/sdlc-config/SKILL.md")


def test_expected_missing_does_not_depend_on_the_reader(tmp_path):
    """Fix round 1 (T704-R5's rule): the files only a full clone can GENERATE are expected
    whenever this repository's install writes them, so a standalone copy and a bundle
    report byte-identical `expected_missing` to the full clone's."""
    t = _fixture(tmp_path / "py")
    (t / ".github").mkdir()
    _install(t)
    for rel in GENERATED:
        assert (t / rel).is_file(), rel
        (t / rel).unlink()
    standalone = _standalone(tmp_path / "alone")
    bundle = tmp_path / "bundle" / "b.py"
    bundle.parent.mkdir()
    made = _run("--bundle", str(bundle))
    assert made.returncode == 0, _text(made)
    outs = []
    for reader in (INSTALLER, standalone, bundle):
        proc = subprocess.run([sys.executable, str(reader), "--manifest", "--target",
                               str(t)], capture_output=True, timeout=600)
        assert proc.returncode == 0, _text(proc)
        outs.append(json.dumps(json.loads(proc.stdout)["expected_missing"]).encode())
    assert outs[0] == outs[1] == outs[2], outs
    assert json.loads(outs[0]) == sorted(GENERATED)


def _load():
    spec = importlib.util.spec_from_file_location("sdlc_init_t706", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_one_function_decides_the_layers_for_both_callers(tmp_path, monkeypatch, capsys):
    """Review F4: `main` and `--manifest` take a run's layers from `applied_layers`
    alone. Patched to switch the extras on for a repository that never chose them,
    BOTH callers must change - an inlined copy of the union in either would not."""
    si = _load()
    t = _fixture(tmp_path / "py")
    _install(t)
    real = si.applied_layers

    def extras_on(root, loaded, given):
        on, reapplied, inferred, partial = real(root, loaded, given)
        return {**on, "claude-extras": True}, reapplied, inferred, partial

    assert REVIEWER not in si.expected_rels(t)
    capsys.readouterr()
    assert si.main(["--target", str(t), "--no-graph", "--dry-run"]) == 0
    assert REVIEWER not in capsys.readouterr().out
    monkeypatch.setattr(si, "applied_layers", extras_on)
    assert REVIEWER in si.expected_rels(t)
    assert si.main(["--target", str(t), "--no-graph", "--dry-run"]) == 0
    assert REVIEWER in capsys.readouterr().out


def test_layer_paths_plans_paths_only_and_its_sets_do_not_change(tmp_path, monkeypatch):
    """Review F1: the inference's four plans are a path query, so they plan with
    `paths_only` (no ledger check generated four times over), and the sets are the
    ones the full plans gave."""
    si = _load()
    t = _fixture(tmp_path / "py")
    (t / ".github").mkdir()
    _install(t)
    real = si.plan_install_ops
    seen: list[bool] = []

    def spy(*args, **kwargs):
        seen.append(kwargs.get("paths_only", False))
        return real(*args, **kwargs)

    monkeypatch.setattr(si, "plan_install_ops", spy)
    fast = si.layer_paths(t)
    assert seen and all(seen), seen
    monkeypatch.setattr(si, "plan_install_ops",
                        lambda *a, **k: real(*a, **{**k, "paths_only": False}))
    assert si.layer_paths(t) == fast


def test_null_names_every_cause_at_both_doors(tmp_path):
    """Review F2/F3: a recorded workspace that is there but is not a workspace, and a
    re-run the pre-INIT gate stops, give null - and the help and README say both."""
    t = tmp_path / "ws"
    ws = _workspace(t)
    _install(t, "--workspace", str(ws))
    assert _manifest(t)["expected_missing"] == []
    good = ws.read_bytes()
    ws.write_bytes(b"this is not toml [\n")
    assert _manifest(t)["expected_missing"] is None
    ws.write_bytes(good)
    (t / "sdlc-project.toml").unlink()
    before = _snapshot(t)
    m = _manifest(t)
    assert m["expected_missing"] is None
    assert _snapshot(t) == before, "the gate's question wrote something"
    help_text = " ".join(_run("--help").stdout.decode("utf-8").split())
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    for door in (help_text, readme):
        tail = door.split("expected_missing`", 1)[1][:700]
        assert "not a workspace" in tail and "sdlc-project.toml" in tail, tail


def _workspace(target: Path) -> Path:
    for name in ("orders", "billing"):
        c = target / "repos" / name
        c.mkdir(parents=True)
        (c / "README.md").write_bytes(name.encode() + b"\n")
        subprocess.run(["git", "init", "-q", str(c)], check=True, capture_output=True,
                       timeout=120)
    ws = target / "conf" / "sdlc-workspace.toml"
    ws.parent.mkdir(parents=True)
    ws.write_bytes(b'schema_version = 1\nroot = "../repos"\nname = ""\nprofile = ""\n')
    _project.seed(target)
    return ws


CADENCE = (".github/workflows/sdlc-corpus-replay.yml",
           ".github/workflows/sdlc-graph-snapshot.yml")


def test_the_cadence_workflows_do_not_depend_on_the_reader(tmp_path):
    """Review F4: the cadence jobs ask the READER for a github.com `origin`. Readers
    with none - a standalone copy and a bundle, under tmp_path, outside any repository
    so `git -C` cannot find the harness's own remote - report what the full clone does."""
    t = tmp_path / "ws"
    ws = _workspace(t)
    (t / ".github").mkdir()
    subprocess.run(["git", "init", "-q", str(t)], check=True, capture_output=True,
                   timeout=120)
    _install(t, "--workspace", str(ws), "--corpus-replay", "--snapshot-cadence")
    for rel in CADENCE:
        assert (t / rel).is_file(), rel
        (t / rel).unlink()
    standalone = _standalone(tmp_path / "alone")
    bundle = tmp_path / "bundle" / "b.py"
    bundle.parent.mkdir()
    assert _run("--bundle", str(bundle)).returncode == 0
    for reader in (standalone, bundle):
        origin = subprocess.run(["git", "-C", str(reader.parent), "remote", "get-url",
                                 "origin"], capture_output=True, timeout=60)
        assert origin.returncode != 0, "the reader must have no origin"
    outs = []
    for reader in (INSTALLER, standalone, bundle):
        proc = subprocess.run([sys.executable, str(reader), "--manifest", "--target",
                               str(t)], capture_output=True, timeout=600)
        assert proc.returncode == 0, _text(proc)
        outs.append(json.dumps(json.loads(proc.stdout)["expected_missing"]).encode())
    assert outs[0] == outs[1] == outs[2], outs
    assert json.loads(outs[0]) == sorted(CADENCE)


def _rerun_changes(t: Path) -> tuple[list[str], list[str]]:
    """(the manifest's `expected_missing`, the paths in its `missing` a real plain
    re-run then changes)."""
    m = _manifest(t)
    before = _snapshot(t)
    _install(t)
    after = _snapshot(t)
    return m["expected_missing"], sorted(r for r in m["missing"]
                                         if before.get(r) != after.get(r))


def test_expected_missing_is_what_a_real_rerun_writes(tmp_path):
    """Review F4: measured against the install itself, not a hand-written list, on two
    shapes - a plain install with owned files deleted, and an extras install with extras
    files and settings.json deleted and CLAUDE.md's block replaced by the adopter's text."""
    plain = _fixture(tmp_path / "plain")
    _install(plain)
    for rel in (RUBRIC, ".sdlc/plan-template.md", ".sdlc/providers.toml"):
        (plain / rel).unlink()
    expected, written = _rerun_changes(plain)
    assert expected == written and len(written) == 3, (expected, written)
    extras = _fixture(tmp_path / "extras")
    _install(extras, "--claude-extras")
    for rel in (REVIEWER, SETTINGS):
        (extras / rel).unlink()
    (extras / "CLAUDE.md").write_bytes(b"# mine\n")
    expected, written = _rerun_changes(extras)
    assert expected == written and "CLAUDE.md" in written and len(written) == 3, (
        expected, written)


def test_two_runs_are_identical_bytes_and_nothing_is_written(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--claude-extras")
    (t / REVIEWER).unlink()
    (t / "CLAUDE.md").write_bytes(b"# mine\n")
    before = _snapshot(t)
    one = _run("--manifest", "--target", str(t))
    two = _run("--manifest", "--target", str(t))
    assert one.returncode == two.returncode == 0, _text(one)
    assert one.stdout == two.stdout
    assert b'"expected_missing"' in one.stdout
    assert _snapshot(t) == before
