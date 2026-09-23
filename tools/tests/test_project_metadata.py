"""T442: pre-INIT project metadata, STOP-AND-FILL (owner ruling 2026-09-19).

INIT asks four questions once - `name`, `description`, `owner`, `criticality`
(owner ruling 2026-09-23) - in `sdlc-project.toml`, or a workspace's `[project]`
table. With the file missing it writes the template and stops; with a required
field blank it names every blank and stops; either way it exits 2 having
installed NOTHING else. A filled file renders a `project:` block into
profile.yml that the runtime reads back and P0 states.

The installer is spawned throughout: a refusal and a traceback are the same
failing test in-process, and the refusal's words are the contract.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import policy  # noqa: E402
from tools.sdlc import stage  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.tests import _project  # noqa: E402

INSTALLER = ROOT / "sdlc-init.py"


def _installer():
    spec = importlib.util.spec_from_file_location("sdlc_init_project", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _target(tmp_path: Path) -> Path:
    root = tmp_path / "svc"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], capture_output=True, timeout=120)
    return root


def _install(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(INSTALLER), "--no-graph", *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600)


def _only(root: Path) -> set[str]:
    return {p.name for p in root.iterdir() if p.name != ".git"}


def test_no_file_writes_the_template_and_installs_nothing(tmp_path):
    root = _target(tmp_path)
    done = _install("--target", str(root))
    assert done.returncode == 2, done.stdout + done.stderr
    assert "stopping before install" in done.stdout, done.stdout
    assert "required and blank: description, owner, criticality" in done.stdout
    assert _only(root) == {"sdlc-project.toml"}, "nothing but the template is written"
    written = (root / "sdlc-project.toml").read_text(encoding="utf-8")
    assert 'name = "svc"' in written, "name is pre-filled from the directory"
    assert "@@" not in written, "no unrendered token reaches the adopter"


def test_dry_run_writes_not_even_the_template(tmp_path):
    root = _target(tmp_path)
    done = _install("--target", str(root), "--dry-run")
    assert done.returncode == 2, done.stdout
    assert "[dry-run] would write sdlc-project.toml" in done.stdout, done.stdout
    assert _only(root) == set()


def test_blank_required_fields_are_all_named_at_once(tmp_path):
    root = _target(tmp_path)
    (root / "sdlc-project.toml").write_text(
        '[project]\nname = "svc"\ndescription = "x"\n', encoding="utf-8")
    done = _install("--target", str(root))
    assert done.returncode == 2, done.stdout
    assert "required and blank: owner, criticality" in done.stdout, done.stdout
    assert not (root / ".sdlc").exists()


@pytest.mark.parametrize("body, words", [
    ('[project]\nname = "a"\ndescription = "b"\nowner = "c"\ncriticality = "urgent"\n',
     "one of low, medium, high"),
    ('[project]\nname = "a"\ndescription = "b"\nowner = "c"\ncriticality = "low"\n'
     'onwer = "typo"\n', "Did you mean `owner`?"),
    ('[project]\nname = "a"\ndescription = "b"\nowner = "c"\ncriticality = "low"\n'
     'compliance = "sox"\n', "must be a LIST"),
    ('schema_version = 1\n', "no [project] table"),
], ids=["criticality", "misspelt-key", "bare-string-list", "no-table"])
def test_a_malformed_file_is_refused_by_name(tmp_path, body, words):
    root = _target(tmp_path)
    (root / "sdlc-project.toml").write_text(body, encoding="utf-8")
    done = _install("--target", str(root))
    assert done.returncode == 2, done.stdout
    assert words in done.stdout, done.stdout
    assert "Traceback" not in done.stdout + done.stderr
    assert not (root / ".sdlc").exists()


def test_a_filled_file_installs_and_the_runtime_reads_it_back(tmp_path):
    root = _project.seed(_target(tmp_path))
    (root / "sdlc-project.toml").write_text(
        _project.FILLED.replace('criticality = "low"', 'criticality = "high"')
        + 'compliance = ["sox", "pci"]\n', encoding="utf-8")
    done = _install("--target", str(root))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "project        : fixture - owner test-suite, criticality high" in done.stdout
    assert "consider `gate_profile: team`" in done.stdout, "high suggests, never forces"
    assert "gate_profile: solo" in (root / ".sdlc" / "profile.yml").read_text(
        encoding="utf-8")
    meta = policy.profile_project(root)
    assert meta["name"] == "fixture" and meta["owner"] == "test-suite"
    assert meta["criticality"] == "high" and meta["compliance"] == ["sox", "pci"]
    assert meta["description"] == "A scratch repository a test installs into"


def test_p0_states_the_project_and_states_its_absence(tmp_path):
    """P0's `project` value: the metadata as one line; an install from before
    T442 gets a STATED absence naming the fix, never a blank."""
    root = _project.seed(_target(tmp_path))
    assert _install("--target", str(root)).returncode == 0
    rid = L.create(root, "requirement", "a change")["run_id"]
    prompt = stage.render_prompt(root, rid, "P0")
    assert "name=fixture; owner=test-suite; criticality=low" in prompt, prompt
    profile = root / ".sdlc" / "profile.yml"
    text = profile.read_text(encoding="utf-8")
    head, _, rest = text.partition("project:\n")
    rest = rest.split("\n\n", 1)[1]
    profile.write_text(head + rest, encoding="utf-8")
    assert "predates the pre-INIT metadata" in stage.render_prompt(root, rid, "P0")


def test_a_workspace_never_has_its_own_toml_edited(tmp_path):
    """The workspace form. With no `[project]` table in the workspace TOML the
    metadata lives where a single repo's does - `sdlc-project.toml` at the
    target - and THAT is written; the developer's own TOML is never touched.
    Measured: an earlier cut appended the table to the workspace TOML and wrote
    34 lines into a committed test fixture. A `[project]` table in the TOML,
    when present, wins."""
    repos = tmp_path / "repos"
    (repos / "billing" / ".git").mkdir(parents=True)
    (repos / "billing" / "go.mod").write_text("module billing\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    cfg = tmp_path / "ws.toml"
    cfg.write_text('root = "repos"\n', encoding="utf-8")
    before = cfg.read_bytes()
    done = _install("--target", str(target), "--workspace", str(cfg))
    assert done.returncode == 2, done.stdout + done.stderr
    assert cfg.read_bytes() == before, "the workspace TOML is the developer's"
    assert _only(target) == {"sdlc-project.toml"}, done.stdout
    (target / "sdlc-project.toml").unlink()
    cfg.write_text('root = "repos"\n' + _project.WORKSPACE_TABLE, encoding="utf-8")
    done = _install("--target", str(target), "--workspace", str(cfg))
    assert done.returncode == 0, done.stdout + done.stderr
    assert policy.profile_project(target)["owner"] == "test-suite"


def test_the_template_ships_in_the_bundle():
    """A standalone `sdlc-init.py` must be able to write the template it stops on."""
    si = _installer()
    assert "project.example.toml" in si._loaded_template_names()
