"""`sdlc-init.py --workspace` end to end.

The other three files test the pieces in isolation. This one runs the installer as a
subprocess, the way a developer does, because the failures that survive unit tests are
the wiring ones: an exception that escapes as a traceback, a flag that silently does
nothing, a note that never prints.

`--workspace` supplies the CONTENT (which components exist); `--target` still supplies
the LOCATION. The harness points AT the workspace through the config and keeps zero
footprint inside the components themselves.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "sdlc-init.py"


def load_installer():
    spec = importlib.util.spec_from_file_location("sdlc_init", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init"] = mod
    spec.loader.exec_module(mod)
    return mod


si = load_installer()

MARKERS = {
    "orders": ("pom.xml", "<project/>"),
    "billing": ("go.mod", "module billing\n"),
    "web-ui": ("package.json", '{"scripts": {"test": "jest"}}'),
}


@pytest.fixture()
def bench(tmp_path: Path):
    """A workspace of three repos, a config beside it, and a target to install into."""
    repos = tmp_path / "repos"
    for name, (marker, body) in MARKERS.items():
        (repos / name / ".git").mkdir(parents=True)
        (repos / name / marker).write_text(body, encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()

    def config(body: str = 'root = "repos"\n') -> Path:
        path = tmp_path / "ws.toml"
        path.write_text(body, encoding="utf-8")
        return path

    config.repos = repos      # type: ignore[attr-defined]
    config.target = target    # type: ignore[attr-defined]
    config.tmp = tmp_path     # type: ignore[attr-defined]
    return config


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(INSTALLER), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def installed_profile(target: Path) -> dict:
    return yaml.safe_load((target / ".sdlc" / "profile.yml").read_text(encoding="utf-8"))


# ── the acceptance condition from the milestone gate ──────────────────────────
def test_workspace_mode_renders_a_populated_components_list(bench):
    """M0 kill-gate condition (4), second half: `--workspace` renders a populated
    `components[]` on a real multi-component workspace."""
    proc = run("--workspace", str(bench()), "--target", str(bench.target))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    doc = installed_profile(bench.target)
    assert doc["scope"] == "estate"
    assert doc["gate_profile"] == "team"
    got = {c["name"]: c["stack"] for c in doc["components"]}
    assert got == {"orders": "java-maven", "billing": "go", "web-ui": "javascript"}


def test_the_installed_profile_has_no_unrendered_tokens(bench):
    run("--workspace", str(bench()), "--target", str(bench.target))
    text = (bench.target / ".sdlc" / "profile.yml").read_text(encoding="utf-8")
    assert "@@" not in text, "an unrendered placeholder reached the profile"


def test_the_harness_writes_nothing_inside_the_components(bench):
    """The zero-footprint claim, as a test. The harness points AT the workspace; a
    stray `.sdlc/` inside someone's service repo is a file they did not ask for in a
    tree the harness does not own."""
    run("--workspace", str(bench()), "--target", str(bench.target))
    for name in MARKERS:
        present = {p.name for p in (bench.repos / name).iterdir()}
        assert present == {".git", MARKERS[name][0]}, f"{name} was modified: {present}"


# ── flags compose the way the single-repo path taught people to expect ────────
def test_dry_run_writes_nothing(bench):
    proc = run("--workspace", str(bench()), "--target", str(bench.target), "--dry-run")
    assert proc.returncode == 0
    assert "[dry-run]" in proc.stdout
    assert not (bench.target / ".sdlc").exists()


def test_a_re_run_preserves_an_edited_profile(bench):
    """The drop-in's headline promise — re-run to repair, never clobber — has to hold
    in workspace mode too, or the first re-run silently discards the developer's
    per-component edits."""
    cfg = bench()
    run("--workspace", str(cfg), "--target", str(bench.target))
    profile = bench.target / ".sdlc" / "profile.yml"
    profile.write_text(profile.read_text(encoding="utf-8") + "\n# my edit\n", encoding="utf-8")

    proc = run("--workspace", str(cfg), "--target", str(bench.target))
    assert proc.returncode == 0
    assert "# my edit" in profile.read_text(encoding="utf-8")
    assert (bench.target / ".sdlc" / "profile.yml.new").is_file()


def test_a_re_run_without_edits_reports_no_change(bench):
    cfg = bench()
    run("--workspace", str(cfg), "--target", str(bench.target))
    proc = run("--workspace", str(cfg), "--target", str(bench.target))
    assert "0 file(s) written" in proc.stdout


def test_include_and_exclude_reach_the_installer(bench):
    """A flag that parses but does not affect the outcome is the worst kind: it looks
    honoured."""
    cfg = bench('root = "repos"\nexclude = ["web-ui"]\n')
    run("--workspace", str(cfg), "--target", str(bench.target))
    names = {c["name"] for c in installed_profile(bench.target)["components"]}
    assert names == {"orders", "billing"}


def test_an_override_reaches_the_installed_profile(bench):
    cfg = bench('root = "repos"\n[[component]]\nname = "web-ui"\nrole = "legacy"\n'
                'test_command = "npm run test:ci"\n')
    run("--workspace", str(cfg), "--target", str(bench.target))
    web = next(c for c in installed_profile(bench.target)["components"] if c["name"] == "web-ui")
    assert web["role"] == "legacy"
    assert web["test_command"] == "npm run test:ci"


# ── failures are messages, not tracebacks ─────────────────────────────────────
@pytest.mark.parametrize("body,fragment", [
    ('root = "nowhere"\n', "does not exist"),
    ('name = "acme"\n', "`root` is required"),
    ('root = "repos"\nroots = "x"\n', "unknown key"),
    ('root = "repos"\n[[component]]\nname = "nope"\n', "matches no discovered component"),
])
def test_a_bad_config_exits_with_a_message(bench, body: str, fragment: str):
    """The developer wrote this file. A stack trace blames the harness for their typo,
    and buries the one line that says what to fix."""
    proc = run("--workspace", str(bench(body)), "--target", str(bench.target))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr, proc.stderr
    assert fragment in proc.stderr, proc.stderr
    assert not (bench.target / ".sdlc").exists(), "a rejected config still wrote files"


def test_an_empty_workspace_is_refused_before_anything_is_written(tmp_path: Path):
    empty = tmp_path / "repos"
    empty.mkdir()
    (empty / "notes").mkdir()
    target = tmp_path / "target"
    target.mkdir()
    cfg = tmp_path / "ws.toml"
    cfg.write_text('root = "repos"\n', encoding="utf-8")

    proc = run("--workspace", str(cfg), "--target", str(target))
    assert proc.returncode != 0
    assert "indistinguishable from a clean one" in proc.stderr
    assert not (target / ".sdlc").exists()


def test_a_missing_workspace_file_points_at_the_template(bench):
    proc = run("--workspace", str(bench.tmp / "absent.toml"), "--target", str(bench.target))
    assert proc.returncode != 0 and "workspace.example.toml" in proc.stderr


# ── the notes are the findings ────────────────────────────────────────────────
def test_a_shallow_clone_is_reported_at_install_time(bench):
    """It is not written into the profile — it goes stale the moment someone runs
    `git fetch --unshallow` — so this print is the only place the developer learns of
    it."""
    (bench.repos / "billing" / ".git" / "shallow").write_text("deadbeef\n", encoding="utf-8")
    proc = run("--workspace", str(bench()), "--target", str(bench.target))
    assert "shallow clone(s): billing" in proc.stdout
    assert "shallow" not in (bench.target / ".sdlc" / "profile.yml").read_text(encoding="utf-8")


def test_a_blank_workspace_name_says_what_protection_is_not_active(bench):
    """`name` is optional, so its absence must be loud. It seeds the leak guard, and a
    guard nobody seeded protects nothing while still looking like protection."""
    proc = run("--workspace", str(bench()), "--target", str(bench.target))
    assert "leak guard has no workspace term" in proc.stdout


def test_a_named_workspace_produces_no_such_note(bench):
    proc = run("--workspace", str(bench('root = "repos"\nname = "acme"\n')),
               "--target", str(bench.target))
    assert "leak guard has no workspace term" not in proc.stdout


def test_directories_without_a_git_are_reported_not_silently_dropped(bench):
    (bench.repos / "scratch").mkdir()
    proc = run("--workspace", str(bench()), "--target", str(bench.target))
    assert "had no .git and are not components: scratch" in proc.stdout


# ── the top-level profile in workspace mode ───────────────────────────────────
def test_top_level_commands_are_blank_because_components_carry_them(bench):
    """A single top-level `test_command` for a workspace with no single build would run
    one component's suite and report it as the workspace's."""
    run("--workspace", str(bench()), "--target", str(bench.target))
    doc = installed_profile(bench.target)
    assert doc["test_command"] == "" and doc["build_command"] == ""
    assert all(c["test_command"] for c in doc["components"])


def test_the_top_level_stack_is_mixed_when_components_disagree(bench):
    run("--workspace", str(bench()), "--target", str(bench.target))
    assert installed_profile(bench.target)["stack"] == "mixed"


def test_the_top_level_stack_names_the_stack_when_they_all_agree(tmp_path: Path):
    repos = tmp_path / "repos"
    for name in ("alpha", "beta"):
        (repos / name / ".git").mkdir(parents=True)
        (repos / name / "go.mod").write_text(f"module {name}\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    cfg = tmp_path / "ws.toml"
    cfg.write_text('root = "repos"\n', encoding="utf-8")
    run("--workspace", str(cfg), "--target", str(target))
    assert installed_profile(target)["stack"] == "go"


def test_deny_raw_git_is_the_union_not_the_intersection():
    """If any component forbids raw git, the fallback forbids it too. Under-restricting
    is the direction that lets an agent push through a wall a component put up."""
    safe = {"vcs": {"deny_raw_git": False}, "name": "a", "stack": "go"}
    strict = {"vcs": {"deny_raw_git": True}, "name": "b", "stack": "go"}
    assert si.workspace_profile([safe, safe])["vcs"]["deny_raw_git"] is False
    merged = si.workspace_profile([safe, strict])
    assert merged["vcs"]["deny_raw_git"] is True
    assert "b" in merged["vcs"]["notes"], "the note must name which component forced it"


def test_the_permission_allowlist_sees_component_commands():
    """A multi-component profile leaves top-level commands blank, so reading only the
    top level yields an allowlist permitting none of the workspace's build tools — an
    install that looks configured and then prompts on every single command."""
    profile = si.workspace_profile([
        {"name": "orders", "stack": "java-maven", "test_command": "mvn -q test", "vcs": {}},
        {"name": "web-ui", "stack": "javascript", "test_command": "npm test", "vcs": {}}])
    allow = set(si.build_settings(profile, None)["permissions"]["allow"])
    assert "Bash(mvn:*)" in allow and "Bash(npm test:*)" in allow


# ── the single-repo path is untouched ─────────────────────────────────────────
def test_without_the_flag_nothing_changes(tmp_path: Path):
    target = tmp_path / "solo"
    target.mkdir()
    (target / ".git").mkdir()
    (target / "go.mod").write_text("module solo\n", encoding="utf-8")

    proc = run("--target", str(target))
    assert proc.returncode == 0
    doc = installed_profile(target)
    assert doc["scope"] == "repo" and doc["gate_profile"] == "solo"
    assert "components" not in doc
    assert doc["test_command"] == "go test ./...", "single-repo commands must stay top-level"
