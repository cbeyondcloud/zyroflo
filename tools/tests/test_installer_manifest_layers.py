"""T711: `--manifest` names the recorded opt-in layers apart from the record's other fields.

Since T708 `.sdlc/install-options` holds `tools`, `standalone` and `workspace` beside the
switches, and `--manifest` printed them in one flat `options` object, so a reader that
re-states the layers a re-run applies kept its own list of the non-layer keys - and read
any new non-layer key as a layer. The manifest now carries a top-level `layers` list: the
recorded switches (`RECORDED_SWITCHES`) the record sets, sorted; `[]` for a record with
none; null exactly when `options` is null. `options` is unchanged. Each test SPAWNS the
installer and failed against the installer before T711, which printed no `layers` key.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.tests import _project  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "sdlc-init.py"
REL = ".sdlc/install-options"


def _switches() -> tuple[str, ...]:
    """`RECORDED_SWITCHES`, read from the installer module itself."""
    spec = importlib.util.spec_from_file_location("sdlc_init_t711", INSTALLER)
    si = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(si)
    return tuple(si.RECORDED_SWITCHES)


SWITCHES = _switches()


def _run(*args: str, installer: Path = INSTALLER) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(installer), *args],
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


def _install(target: Path, *extra: str, installer: Path = INSTALLER):
    proc = _run("--target", str(target), "--no-graph", *extra, installer=installer)
    assert proc.returncode == 0, _text(proc)
    return proc


def _manifest(target: Path) -> dict:
    proc = _run("--manifest", "--target", str(target))
    assert proc.returncode == 0, _text(proc)
    return json.loads(proc.stdout.decode("utf-8"))


def test_layers_lists_the_recorded_switches_and_options_keeps_the_rest(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude,cursor", "--claude-extras", "--corpus-replay")
    m = _manifest(t)
    assert m["layers"] == ["claude-extras", "corpus-replay"], m["layers"]
    assert m["options"]["tools"] == ["claude", "cursor"], m["options"]
    assert m["options"]["claude-extras"] is True and m["options"]["corpus-replay"] is True


@pytest.mark.parametrize("key", SWITCHES)
def test_each_recorded_switch_is_a_layer(tmp_path, key):
    t = _fixture(tmp_path / "py")
    _install(t, "--" + key)
    m = _manifest(t)
    assert m["options"].get(key) is True, m["options"]
    assert m["layers"] == [key], m["layers"]


def test_standalone_and_workspace_are_recorded_but_not_layers(tmp_path):
    alone = tmp_path / "alone"
    alone.mkdir()
    shutil.copy2(INSTALLER, alone / "sdlc-init.py")
    shutil.copytree(ROOT / "sdlc_templates", alone / "sdlc_templates")
    s = _fixture(tmp_path / "s")
    _install(s, "--tools", "claude", installer=alone / "sdlc-init.py")
    m = _manifest(s)
    assert m["options"]["standalone"] is True, m["options"]
    assert m["layers"] == [], m["layers"]

    w = tmp_path / "ws"
    for name in ("orders", "billing"):
        c = w / "repos" / name
        c.mkdir(parents=True)
        (c / "README.md").write_bytes(name.encode() + b"\n")
        subprocess.run(["git", "init", "-q", str(c)], check=True, capture_output=True,
                       timeout=120)
    ws = w / "conf" / "sdlc-workspace.toml"
    ws.parent.mkdir(parents=True)
    ws.write_bytes(b'schema_version = 1\nroot = "../repos"\nname = ""\nprofile = ""\n')
    _project.seed(w)
    _install(w, "--workspace", str(ws), "--claude-extras")
    m = _manifest(w)
    assert m["options"]["workspace"] == "conf/sdlc-workspace.toml", m["options"]
    assert m["layers"] == ["claude-extras"], m["layers"]


def test_the_readme_layers_sentence_names_every_recorded_switch():
    """Review F4: the README spells the switches out; a later switch must reach it."""
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    start = readme.index("and `layers` (")
    sentence = readme[start:readme.index(")", readme.index("never `tools`", start))]
    for key in SWITCHES:
        assert f"`{key}`" in sentence, key


def test_layers_is_empty_for_tools_alone_and_null_without_a_record(tmp_path):
    t = _fixture(tmp_path / "tools-only")
    _install(t, "--tools", "claude")
    m = _manifest(t)
    assert set(m["options"]) == {"tools"} and m["layers"] == [], m
    # No record (an install before T705): null, like options - recorded, not inferred.
    (t / REL).unlink()
    m = _manifest(t)
    assert m["options"] is None and m["layers"] is None, m
    assert REL in m["missing"]
    # An unreadable record (a name it does not record): null, and named.
    (t / REL).write_bytes(b"bogus = true\n")
    m = _manifest(t)
    assert m["options"] is None and m["layers"] is None, m
    assert REL in m["unreadable"]
