"""Contract tests for the two things verified only by hand until now.

1. `profile.yml` -- the ONE file that makes the generic harness fit a workspace. It is a
   template with `@@TOKEN@@` slots filled by `render_profile()`. A token added to the
   template but not to the renderer ships a literal `@@TOKEN@@` into a user's repo and
   breaks their YAML; nothing caught that class before.
2. `.gitignore` -- the merged union that keeps `.env` out of a repo with a remote. It was
   proven correct once, interactively. This re-proves it on every run.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "sdlc-init.py"


def load_installer():
    """Import sdlc-init.py despite the hyphen (not a valid module name)."""
    spec = importlib.util.spec_from_file_location("sdlc_init", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init"] = mod
    spec.loader.exec_module(mod)
    return mod


def install_into(target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(target)], check=True, capture_output=True)
    proc = subprocess.run([sys.executable, str(INSTALLER), "--target", str(target)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, f"installer failed:\n{proc.stdout}\n{proc.stderr}"
    return target / ".sdlc" / "profile.yml"


@pytest.fixture
def py_repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n', encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_x(): assert True\n", encoding="utf-8")
    return tmp_path


# ── the template/renderer contract ─────────────────────────────────────────────
def test_every_template_token_is_wired_to_the_renderer():
    """The failure this prevents: add `@@FOO@@` to the template, forget the renderer,
    ship a literal placeholder into someone's profile and break their YAML parse."""
    mod = load_installer()
    template = (ROOT / "sdlc_templates" / "profile.yml").read_text(encoding="utf-8")
    in_template = set(re.findall(r"@@[A-Z_]+@@", template))

    rendered = mod.render_profile(
        {"stack": "python", "test_command": "pytest -q", "coverage_command": "pytest --cov",
         "coverage_threshold": 90, "lint_command": "", "build_command": "",
         "deploy_command": "", "vcs": {}},
        ["claude"],
    )
    unfilled = set(re.findall(r"@@[A-Z_]+@@", rendered))
    assert not unfilled, f"template tokens with no renderer mapping: {sorted(unfilled)}"
    assert in_template, "sanity: the template should still use tokens"


def test_rendered_profile_is_valid_yaml_with_v2_shape():
    mod = load_installer()
    d = yaml.safe_load(mod.render_profile(
        {"stack": "java", "test_command": 'mvn -q test', "coverage_command": "",
         "coverage_threshold": 80, "lint_command": "", "build_command": "mvn -q package",
         "deploy_command": "", "vcs": {}},
        ["claude", "cursor"],
    ))
    assert d["schema_version"] == 2
    assert d["scope"] == "repo"
    assert d["gate_profile"] == "solo"
    assert d["signing"] == "none"
    assert d["review"]["block_on"] == ["critical", "high"]
    assert "components" not in d, "a single repo must not need a components list"


def test_command_values_containing_quotes_stay_valid_yaml():
    """`dotnet test --collect:"XPlat Code Coverage"` must not break the parse."""
    mod = load_installer()
    d = yaml.safe_load(mod.render_profile(
        {"stack": "dotnet", "test_command": 'dotnet test --collect:"XPlat Code Coverage"',
         "coverage_command": "", "coverage_threshold": 80, "lint_command": "",
         "build_command": "", "deploy_command": "", "vcs": {}},
        ["claude"],
    ))
    assert d["test_command"] == 'dotnet test --collect:"XPlat Code Coverage"'


def test_vcs_block_round_trips_deny_raw_git():
    mod = load_installer()
    d = yaml.safe_load(mod.render_profile(
        {"stack": "python", "test_command": "pytest", "coverage_command": "",
         "coverage_threshold": 90, "lint_command": "", "build_command": "", "deploy_command": "",
         "vcs": {"commit_command": './checkin.sh "{message}"', "push_command": "",
                 "deny_raw_git": True, "notes": "open-core split"}},
        ["claude"],
    ))
    assert d["vcs"]["deny_raw_git"] is True
    assert "{message}" in d["vcs"]["commit_command"]


def test_solo_phase_list_matches_the_lifecycle_spec():
    """LIFECYCLE.md and the shipped default must not drift apart."""
    mod = load_installer()
    d = yaml.safe_load(mod.render_profile(
        {"stack": "python", "test_command": "pytest", "coverage_command": "",
         "coverage_threshold": 90, "lint_command": "", "build_command": "",
         "deploy_command": "", "vcs": {}}, ["claude"]))
    expected = ["intake", "requirements", "design", "implementation",
                "test", "review", "triage", "closeout"]
    assert d["phases"] == expected

    spec_text = (ROOT / "sdlc" / "LIFECYCLE.md").read_text(encoding="utf-8")
    assert ", ".join(expected) in spec_text.replace("`", ""), \
        "the spec's stated solo default must match what the installer writes"


# ── end-to-end install ─────────────────────────────────────────────────────────
def test_fresh_install_emits_a_parseable_v2_profile(py_repo: Path):
    profile = install_into(py_repo)
    text = profile.read_text(encoding="utf-8")
    assert "@@" not in text, "unrendered placeholder reached a user's repo"
    d = yaml.safe_load(text)
    assert d["schema_version"] == 2 and len(d["phases"]) == 8
    assert d["test_command"], "stack detection should have found a test command"


def test_reinstall_preserves_a_hand_edited_profile(py_repo: Path):
    """The installer's core promise: your edits survive an upgrade."""
    profile = install_into(py_repo)
    edited = profile.read_text(encoding="utf-8").replace(
        "coverage_scope: changed", "coverage_scope: whole   # mine")
    profile.write_text(edited, encoding="utf-8")

    install_into(py_repo)
    assert "coverage_scope: whole" in profile.read_text(encoding="utf-8")
    assert (profile.parent / "profile.yml.new").exists(), \
        "the new version must be staged alongside, not dropped"


def test_bundled_single_file_installer_emits_the_same_schema(tmp_path: Path, py_repo: Path):
    """`--bundle` embeds the templates; it must not fall behind the template dir."""
    bundle = tmp_path / "bundled.py"
    subprocess.run([sys.executable, str(INSTALLER), "--bundle", str(bundle)],
                   check=True, capture_output=True)
    target = tmp_path / "viabundle"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True, capture_output=True)
    (target / "pyproject.toml").write_text('[project]\nname="y"\n', encoding="utf-8")
    proc = subprocess.run([sys.executable, str(bundle), "--target", str(target)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    d = yaml.safe_load((target / ".sdlc" / "profile.yml").read_text(encoding="utf-8"))
    assert d["schema_version"] == 2 and d["gate_profile"] == "solo"


# ── .gitignore ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", [
    ".env", "credentials.json", "token.json",
    ".tmp/scratch.json", "third_party/bin/rg.exe", "third_party/bin/fd.exe",
    ".pytest_cache/x", ".coverage", "coverage.xml", "htmlcov/index.html",
    ".venv/pyvenv.cfg", "__pycache__/m.pyc", "mod.pyc",
    ".claude/settings.local.json",
])
def test_sensitive_and_generated_paths_stay_ignored(path: str):
    """`.env` holds a live Neo4j password. A secret in git history outlives the commit
    that added it, so this is asserted per path rather than trusted to review."""
    proc = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", path],
                          capture_output=True)
    assert proc.returncode == 0, f"{path} is NOT ignored"


@pytest.mark.parametrize("path", [
    "sdlc-init.py", "README.md", "third_party/manifest.json",
    "tools/graph/core/model.py", "sdlc/LIFECYCLE.md", "sdlc_templates/profile.yml",
])
def test_product_files_are_not_ignored(path: str):
    """The mirror image: an over-broad rule that silently stops tracking real code."""
    proc = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", path],
                          capture_output=True)
    assert proc.returncode == 1, f"{path} IS ignored but must be tracked"


def test_no_secret_is_currently_tracked():
    """Belt and braces: rules can be right while a file was committed before them."""
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                         capture_output=True, text=True, check=True).stdout.split("\n")
    tracked = {f.strip() for f in out if f.strip()}
    for bad in (".env", "credentials.json", "token.json", ".claude/settings.local.json"):
        assert bad not in tracked, f"{bad} is tracked in git"
    assert not [f for f in tracked if f.startswith("third_party/bin/")], \
        "vendored binaries must be fetched and verified, never committed"
