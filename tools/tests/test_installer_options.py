"""T705: the installer records the opt-in layers an install ran with.

`--claude-extras`, the two cadence workflows and `--workspace` each add or reshape files,
and before T705 nothing recorded them, so a plain re-run left those files at the old
version (or staged `.new` copies for a workspace's profile). Every install now writes a
TRACKED `.sdlc/install-options`; a later run applies the recorded layers together with the
ones it is given, and `--manifest` reports them under `options`. Each test SPAWNS the
installer and failed against the installer before T705, which wrote no such file.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.tests import _project  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "sdlc-init.py"
REL = ".sdlc/install-options"
HEADER = (b"# Written by sdlc-init.py: the tools and opt-in layers every re-run "
          b"applies. Delete a layer's line to drop that layer. To drop a tool, remove "
          b"it from the tools line here and from the tools: line of .sdlc/profile.yml "
          b"- a deleted tools line is read back from that profile line. The standalone "
          b"line is rewritten from the installer copy on every run.\n")
#: T708: every install records its resolved tools; the fixtures resolve to claude.
TOOLS = b'tools = ["claude"]\n'
REVIEWER = ".claude/agents/sdlc-reviewer.md"


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


def _install(target: Path, *extra: str) -> subprocess.CompletedProcess:
    proc = _run("--target", str(target), "--no-graph", *extra)
    assert proc.returncode == 0, _text(proc)
    return proc


def _record(target: Path) -> bytes:
    return (target / REL).read_bytes()


def _snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob("*")) if ".git" not in p.relative_to(root).parts}


def _manifest(target: Path) -> dict:
    proc = _run("--manifest", "--target", str(target))
    assert proc.returncode == 0, _text(proc)
    return json.loads(proc.stdout.decode("utf-8"))


def test_a_plain_rerun_keeps_the_extras_current(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--claude-extras")
    assert _record(t) == HEADER + b"claude-extras = true\n" + TOOLS
    current = (t / REVIEWER).read_bytes()
    # An older release's copy of an extras file: a plain re-run must bring it current.
    (t / REVIEWER).write_bytes(b"# an older release's reviewer\n")
    claude = (t / "CLAUDE.md").read_bytes()
    proc = _install(t)
    assert (t / REVIEWER).read_bytes() == current, _text(proc)
    assert (t / "CLAUDE.md").read_bytes() == claude, "the extras section was dropped"
    assert "re-applied" in _text(proc) and "--claude-extras" in _text(proc)
    assert _record(t) == HEADER + b"claude-extras = true\n" + TOOLS


def test_the_record_is_byte_stable_and_lf(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--claude-extras", "--corpus-replay", "--snapshot-cadence")
    one = _record(t)
    _install(t)
    assert _record(t) == one
    assert b"\r" not in one
    lines = one.decode("ascii").splitlines()
    assert lines[0].startswith("# ") and len([x for x in lines if x.startswith("#")]) == 1
    assert lines[1:] == sorted(lines[1:]) == [
        "claude-extras = true", "corpus-replay = true", "snapshot-cadence = true",
        'tools = ["claude"]']           # T708 (by design): the tools are recorded too
    # .sdlc/harness-version is untouched by the new record: still exactly one line.
    assert (t / ".sdlc" / "harness-version").read_bytes().count(b"\n") == 1


def test_per_run_flags_are_never_recorded(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--force", "--single-component", "--tools", "claude,gemini")
    # T708 (by design): --tools is recorded now, as the resolved list.
    assert _record(t) == HEADER + b'tools = ["claude", "gemini"]\n'


def test_the_record_is_the_union_of_recorded_and_given(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--claude-extras")
    _install(t, "--snapshot-cadence")
    assert _record(t) == HEADER + b"claude-extras = true\nsnapshot-cadence = true\n" + TOOLS
    assert (t / REVIEWER).is_file()


def test_a_dry_run_shows_the_record_and_writes_nothing(tmp_path):
    t = _fixture(tmp_path / "py")
    proc = _run("--target", str(t), "--no-graph", "--dry-run", "--claude-extras")
    assert proc.returncode == 0, _text(proc)
    assert REL in proc.stdout.decode("utf-8")
    assert not (t / ".sdlc").exists()


MALFORMED = {
    "unknown": b"colour = true\n",
    "false": b"claude-extras = false\n",
    "garbage": b"this is not an option line\n",
    "duplicate": b"claude-extras = true\nclaude-extras = true\n",
    "absolute": b'workspace = "/elsewhere/ws.toml"\n',
    "escapes": b'workspace = "../ws.toml"\n',
    "unquoted": b"workspace = ws.toml\n",
    "not-utf8": b"claude-extras = \xff\n",
}


def test_a_malformed_record_is_refused_by_name(tmp_path):
    for name, body in MALFORMED.items():
        t = _fixture(tmp_path / name)
        _install(t)
        (t / REL).write_bytes(HEADER + body)
        before = _snapshot(t)
        for argv in ((), ("--dry-run",), ("--claude-extras",)):
            proc = _run("--target", str(t), "--no-graph", *argv)
            err = proc.stderr.decode("utf-8", "replace")
            assert proc.returncode == 1, (name, argv, _text(proc))
            assert REL in err and "Traceback" not in err, (name, err)
            assert len(err.strip().splitlines()) == 1, (name, err)
            assert _snapshot(t) == before, (name, "a refused install wrote")
        # Fix round 1, F6: the manifest reports such a record as unreadable, as T704
        # reports any owned path it cannot read, and still describes the rest.
        m = _manifest(t)
        assert REL in m["unreadable"] and m["options"] is None, name
        assert REL not in m["per_repo"] and REL not in m["missing"], name


def test_a_refusal_names_the_remedy_for_its_cause(tmp_path):
    """Fix round 1, F8: "that line" only when there is a line."""
    t = _fixture(tmp_path / "py")
    _install(t)
    (t / REL).write_bytes(HEADER + b"colour = true\n")
    err = _run("--target", str(t), "--no-graph").stderr.decode("utf-8")
    assert "line 2" in err and "remove that line" in err, err
    (t / REL).write_bytes(b"\xff\xfe\n")
    err = _run("--target", str(t), "--no-graph").stderr.decode("utf-8")
    assert "that line" not in err and "delete the file" in err and REL in err, err


def test_a_hand_edit_that_drops_a_line_drops_the_layer(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--claude-extras", "--snapshot-cadence")
    (t / REL).write_bytes(HEADER + b"snapshot-cadence = true\r\n")   # a CRLF editor
    proc = _install(t)
    # Fix round 1, F7: rewritten in the canonical form, byte for byte.
    assert _record(t) == HEADER + b"snapshot-cadence = true\n" + TOOLS
    assert "--claude-extras" not in _text(proc).split("re-applied")[-1].split("\n")[0]
    assert "re-applied" in _text(proc) and "--snapshot-cadence" in _text(proc)
    (t / REL).write_bytes(HEADER)
    _install(t)
    assert _record(t) == HEADER + TOOLS


def test_the_manifest_reports_options(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--claude-extras")
    m = _manifest(t)
    assert m["options"] == {"claude-extras": True, "tools": ["claude"]}
    assert REL in m["per_repo"] and REL not in m["files"]
    (t / REL).write_bytes(HEADER)
    assert _manifest(t)["options"] == {}
    (t / REL).unlink()
    m = _manifest(t)
    assert m["options"] is None and REL in m["missing"]


#: What a no-flag `--no-graph` install of the python fixture writes, measured on the
#: T704 installer (11 paths), plus the one file T705 adds.
NO_FLAG_TREE = sorted([
    ".claude/skills/sdlc-config/SKILL.md", ".claude/skills/sdlc/SKILL.md",
    ".sdlc/.gitattributes", ".sdlc/.gitignore", ".sdlc/.installed-digests",
    ".sdlc/harness-version", ".sdlc/pipeline.md", ".sdlc/plan-template.md",
    ".sdlc/plans/.gitkeep", ".sdlc/policy.yml", ".sdlc/profile.yml",
    ".sdlc/providers.toml", ".sdlc/review-rubric.md", REL,
])


def test_no_record_and_no_flags_is_todays_tree_plus_the_record(tmp_path):
    a = _fixture(tmp_path / "a")
    b = _fixture(tmp_path / "b")
    before = set(_snapshot(a))
    _install(a)
    _install(b)
    assert _record(a) == HEADER + TOOLS
    added = sorted(set(_snapshot(a)) - before)
    added = [r for r in added if (a / r).is_file()]
    assert added == NO_FLAG_TREE, added
    # The record changes no other file: B without it is A without it, byte for byte.
    (b / REL).unlink()
    sa, sb = _snapshot(a), _snapshot(b)
    sa.pop(REL)
    assert sa == sb
    # And a re-run with the header-only record plans every file unchanged.
    proc = _run("--target", str(a), "--no-graph", "--dry-run")
    assert "0 file(s) would change" in proc.stdout.decode("utf-8"), _text(proc)


def test_the_record_is_not_ignored(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--claude-extras")
    assert (t / REL).is_file()          # check-ignore answers 1 for an absent path too
    ignored = subprocess.run(["git", "-C", str(t), "check-ignore", "-q", REL],
                             capture_output=True, timeout=120)
    assert ignored.returncode == 1, "the generated .gitignore ignores the record"


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


def test_a_workspace_inside_the_target_is_recorded_and_reapplied(tmp_path):
    t = tmp_path / "ws"
    ws = _workspace(t)
    _install(t, "--workspace", str(ws))
    assert _record(t) == HEADER + TOOLS + b'workspace = "conf/sdlc-workspace.toml"\n'
    profile = (t / ".sdlc" / "profile.yml").read_bytes()
    assert b"orders" in profile
    proc = _install(t)
    out = _text(proc)
    assert "0 file(s) written" in out, out
    assert not (t / ".sdlc" / "profile.yml.new").exists()
    assert (t / ".sdlc" / "profile.yml").read_bytes() == profile
    assert _manifest(t)["options"] == {"tools": ["claude"],
                                       "workspace": "conf/sdlc-workspace.toml"}


def test_a_workspace_outside_the_target_is_not_recorded_and_said(tmp_path):
    t = tmp_path / "ws"
    _workspace(t)
    outside = tmp_path / "elsewhere" / "sdlc-workspace.toml"
    outside.parent.mkdir()
    outside.write_bytes(b'schema_version = 1\nroot = "../ws/repos"\nname = ""\n'
                        b'profile = ""\n')
    proc = _install(t, "--workspace", str(outside))
    assert _record(t) == HEADER + TOOLS
    out = _text(proc)
    assert "outside the target" in out and REL in out, out


def test_the_help_says_how_to_drop_a_layer():
    # Wide, so argparse's wrap never breaks the file's name at its hyphen.
    proc = subprocess.run([sys.executable, str(INSTALLER), "--help"], capture_output=True,
                          timeout=120, env={**__import__("os").environ, "COLUMNS": "4000"})
    out = " ".join(proc.stdout.decode("utf-8").split())
    assert REL in out and "delete its line" in out


# ── fix round 1 ───────────────────────────────────────────────────────────────

def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "elsewhere" / "sdlc-workspace.toml"
    outside.parent.mkdir()
    outside.write_bytes(b'schema_version = 1\nroot = "../ws/repos"\nname = ""\n'
                        b'profile = ""\n')
    return outside


def test_an_outside_workspace_never_changes_the_record(tmp_path):
    """F1: an outside `--workspace` deleted a recorded workspace line."""
    t = tmp_path / "ws"
    ws = _workspace(t)
    _install(t, "--workspace", str(ws))
    recorded = HEADER + TOOLS + b'workspace = "conf/sdlc-workspace.toml"\n'
    assert _record(t) == recorded
    proc = _install(t, "--workspace", str(_outside(tmp_path)))
    assert _record(t) == recorded
    out = " ".join(_text(proc).split())
    assert 'still records workspace "conf/sdlc-workspace.toml"' in out, out
    again = _install(t)
    assert not (t / ".sdlc" / "profile.yml.new").exists(), _text(again)
    assert not (t / ".sdlc" / ".gitignore.new").exists(), _text(again)


EXTRAS_ONLY = [".claude/agents/sdlc-reviewer.md", ".claude/agents/sdlc-security.md",
               ".claude/hooks/sdlc-autolint.py", ".claude/hooks/sdlc-session-start.py"]


def test_a_pre_t705_extras_install_is_inferred_kept_and_recorded(tmp_path):
    """F2: an install from before the record recorded its layers nowhere."""
    t = _fixture(tmp_path / "py")
    _install(t, "--claude-extras")
    (t / REL).unlink()                   # what a T704 install leaves
    current = (t / REVIEWER).read_bytes()
    (t / REVIEWER).write_bytes(b"# an older release's reviewer\n")
    claude = (t / "CLAUDE.md").read_bytes()
    proc = _install(t)
    out = _text(proc)
    assert _record(t) == HEADER + b"claude-extras = true\n" + TOOLS, out
    assert (t / REVIEWER).read_bytes() == current
    assert (t / "CLAUDE.md").read_bytes() == claude
    notes = [line for line in out.splitlines() if "was inferred" in line]
    assert len(notes) == 1 and "--claude-extras" in notes[0], out
    for rel in EXTRAS_ONLY:
        assert rel in notes[0], (rel, notes[0])
    assert _manifest(t)["options"] == {"claude-extras": True, "tools": ["claude"]}


def test_a_pre_t705_plain_install_records_none(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t)
    (t / REL).unlink()
    proc = _install(t)
    assert _record(t) == HEADER + TOOLS
    assert "inferred" not in _text(proc)


def test_a_partial_layer_is_named_not_inferred(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--claude-extras")
    (t / REL).unlink()
    (t / ".claude" / "agents" / "sdlc-security.md").unlink()
    proc = _install(t)
    out = " ".join(_text(proc).split())
    assert _record(t) == HEADER + TOOLS
    assert "only some of the files --claude-extras installs" in out, out
    assert "pass --claude-extras" in out


def test_the_layer_paths_come_from_the_planner(tmp_path):
    """F2's rule: no hand list - the paths each switch alone plans, merged files left
    out. A new extras file in the planner would appear here without an edit."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sdlc_init_options", INSTALLER)
    si = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(si)
    paths = si.layer_paths(_fixture(tmp_path / "py"))
    assert paths["claude-extras"] == EXTRAS_ONLY
    assert paths["snapshot-cadence"] == [".github/workflows/sdlc-graph-snapshot.yml"]
    assert paths["corpus-replay"] == [".github/workflows/sdlc-corpus-replay.yml"]


def test_a_recorded_workspace_that_is_gone_is_refused_by_the_record(tmp_path):
    """F4: the refusal names the record, its line and the remedy."""
    t = tmp_path / "ws"
    ws = _workspace(t)
    _install(t, "--workspace", str(ws))
    ws.unlink()
    before = _snapshot(t)
    for argv in ((), ("--dry-run",)):
        proc = _run("--target", str(t), "--no-graph", *argv)
        err = proc.stderr.decode("utf-8", "replace")
        assert proc.returncode == 1, _text(proc)
        # T708 (by design): the tools line sorts first, so the workspace is on line 3.
        assert f"{REL} line 3" in err and "conf/sdlc-workspace.toml" in err, err
        assert "remove it" in err and len(err.strip().splitlines()) == 1, err
        assert "workspace.example.toml" not in err, err
    (t / REL).write_bytes(HEADER + b'workspace = "conf"\n')        # a directory
    err = _run("--target", str(t), "--no-graph").stderr.decode("utf-8")
    assert f"{REL} line 2" in err and "names no file" in err, err
    assert _snapshot(t) == {**before, REL: HEADER + b'workspace = "conf"\n'}


def test_an_unreadable_record_does_not_abort_the_manifest(tmp_path):
    """F6: a directory at the record's path is unreadable, as at any owned path."""
    t = _fixture(tmp_path / "py")
    _install(t)
    (t / REL).unlink()
    (t / REL).mkdir()
    m = _manifest(t)
    assert REL in m["unreadable"] and m["options"] is None
    assert REL not in m["missing"] and REL not in m["per_repo"]
    proc = _run("--target", str(t), "--no-graph")
    assert proc.returncode == 1 and REL in proc.stderr.decode("utf-8"), _text(proc)


T704_SUBJECT = ("feat(installer): --manifest says what the installer owns, and a tracked "
                "harness version (T704)")


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                          timeout=120, check=True).stdout.decode("utf-8").strip()


def _t704_commit() -> str:
    """The T704 commit, named by its SUBJECT (a rebase-proof name): the OLDEST commit
    touching sdlc-init.py whose message carries the subject, so neither the merge that
    repeats it nor a later "CORRECTION to <subject>" body is taken (review N4). Where
    none is found, the cause is named - no repository, or a shallow one."""
    assert (ROOT / ".git").exists(), (f"{ROOT} has no .git, so T704's installer has no "
                                      f"history to come from")
    found = _git("rev-list", "--fixed-strings", f"--grep={T704_SUBJECT}", "HEAD", "--",
                 "sdlc-init.py").split()
    if not found:
        shallow = _git("rev-parse", "--is-shallow-repository") == "true"
        assert not shallow, ("this clone is shallow, so T704's commit is not in it - "
                             "fetch the full history (git fetch --unshallow)")
        raise AssertionError(f"no commit touching sdlc-init.py carries {T704_SUBJECT!r}")
    return found[-1]


def _t704_installer(at: Path) -> Path:
    """The installer as T704 merged it, beside a copy of the templates."""
    found = _t704_commit()
    src = subprocess.run(["git", "-C", str(ROOT), "show", f"{found}:sdlc-init.py"],
                         capture_output=True, timeout=120, check=True).stdout
    # A positive check of the blob: T704's version record, and not yet T705's.
    assert b'HARNESS_VERSION_REL = ".sdlc/harness-version"' in src
    assert b"INSTALL_OPTIONS_REL" not in src
    at.mkdir(parents=True)
    import shutil
    shutil.copytree(ROOT / "sdlc_templates", at / "sdlc_templates")
    (at / "sdlc-init.py").write_bytes(src)
    return at / "sdlc-init.py"


def test_a_no_record_manifest_is_t704s_plus_two_entries(tmp_path):
    """F5: for a repository with no record, the manifest is T704's byte for byte apart
    from `"options": null` and `.sdlc/install-options` under `missing`."""
    old = _t704_installer(tmp_path / "t704")
    t = _fixture(tmp_path / "py")
    _install(t, "--claude-extras")
    (t / REL).unlink()
    proc = subprocess.run([sys.executable, str(old), "--manifest", "--target", str(t)],
                          capture_output=True, timeout=600)
    assert proc.returncode == 0, _text(proc)
    before = json.loads(proc.stdout.decode("utf-8"))
    after = _manifest(t)
    assert after.pop("options") is None
    after.pop("expected_missing")          # T706's key, which T704 never printed
    assert after.pop("layers") is None     # T711's key: null with no record, as options
    after["missing"].remove(REL)
    assert after == before


def test_hand_edits_round_trip_to_the_canonical_form(tmp_path):
    """F7: what a hand edit plausibly does is read, and the next run writes it back
    canonical; a commented-out line drops its layer."""
    t = _fixture(tmp_path / "py")
    _install(t, "--claude-extras", "--snapshot-cadence")
    (t / REL).write_bytes(b"\n# my own note\n\n  snapshot-cadence   =   true   # weekly\n"
                          b"\n\tclaude-extras=true\n" + b"\n" * 5000)
    _install(t)
    assert _record(t) == HEADER + b"claude-extras = true\nsnapshot-cadence = true\n" + TOOLS
    (t / REL).write_bytes(HEADER + b"# claude-extras = true\nsnapshot-cadence = true\n")
    _install(t)
    assert _record(t) == HEADER + b"snapshot-cadence = true\n" + TOOLS
    for bad in (b"Claude-Extras = true\n", b"claude-extras = True\n",
                b"claude-extras = true true\n", b'workspace = "a" "b"\n'):
        (t / REL).write_bytes(HEADER + bad)
        proc = _run("--target", str(t), "--no-graph")
        assert proc.returncode == 1 and f"{REL} line 2" in proc.stderr.decode(), bad


def test_the_manifest_help_names_every_key(tmp_path):
    """F3: the help and the README list every key the manifest prints, and
    install-options among the per-repository files."""
    t = _fixture(tmp_path / "py")
    _install(t)
    printed = set(_manifest(t))
    proc = subprocess.run([sys.executable, str(INSTALLER), "--help"], capture_output=True,
                          timeout=120, env={**__import__("os").environ, "COLUMNS": "4000"})
    out = " ".join(proc.stdout.decode("utf-8").split())
    keys = ("installer", "installed", "files", "per_repo", "missing", "unreadable",
            "staged", "policy", "profile_changed", "options", "expected_missing",
            "layers")                                   # T711: the twelfth key
    assert printed == set(keys), printed
    for key in keys:
        assert f"`{key}`" in out, key
    assert "settings.json, install-options)" in out
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    for key in keys:
        assert f"`{key}`" in readme, key
    assert "`settings.json`, `install-options`" in readme
    assert "with twelve keys" in readme                 # T711: the count, from the list


# ── fix round 2 ───────────────────────────────────────────────────────────────

def test_a_stale_recorded_workspace_yields_to_a_given_one(tmp_path):
    """N1: the staleness check refused a run that named a good workspace itself."""
    t = tmp_path / "ws"
    ws = _workspace(t)
    _install(t, "--workspace", str(ws))
    moved = t / "conf2" / "sdlc-workspace.toml"
    moved.parent.mkdir()
    moved.write_bytes(ws.read_bytes().replace(b'"../repos"', b'"../repos"'))
    ws.unlink()
    _install(t, "--workspace", str(moved))
    assert _record(t) == HEADER + TOOLS + b'workspace = "conf2/sdlc-workspace.toml"\n'


def _extras_by_hand(t: Path) -> None:
    for rel in EXTRAS_ONLY:
        (t / rel).parent.mkdir(parents=True, exist_ok=True)
        (t / rel).write_bytes(b"# the repository's own file\n")


def test_a_brand_new_install_never_infers(tmp_path):
    """N2: inference is for an upgrade - a target with no .sdlc/ has no earlier layer."""
    t = _fixture(tmp_path / "py")
    _extras_by_hand(t)
    proc = _install(t)
    assert _record(t) == HEADER + TOOLS, _text(proc)
    assert "inferred" not in _text(proc)


def test_a_dry_run_says_the_inferred_layer_would_be_applied(tmp_path):
    """N3: the NOTE's verb says what this run did."""
    t = _fixture(tmp_path / "py")
    _install(t, "--claude-extras")
    (t / REL).unlink()
    proc = _run("--target", str(t), "--no-graph", "--dry-run")
    out = " ".join(_text(proc).split())
    assert proc.returncode == 0, out
    assert "--claude-extras would be applied and recorded" in out, out
    assert not (t / REL).exists()
    out = " ".join(_text(_install(t)).split())
    assert "--claude-extras applied and recorded" in out, out


def test_a_blocked_inferred_layer_says_recorded_not_applied(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t)
    (t / REL).unlink()
    wf = t / ".github" / "workflows" / "sdlc-graph-snapshot.yml"
    wf.parent.mkdir(parents=True)
    wf.write_bytes(b"name: an earlier release's snapshot job\n")
    out = " ".join(_text(_install(t)).split())
    assert "--snapshot-cadence recorded, not applied (blocked)" in out, out
    assert "applied and recorded" not in out, out
    assert _record(t) == HEADER + b"snapshot-cadence = true\n" + TOOLS


def test_a_stale_recorded_workspace_is_said_when_an_outside_one_is_given(tmp_path):
    """N5: the NOTE claimed a plain run applies a recorded workspace whose file is gone -
    that run is refused (F4). It must say the file is missing and name the remedy."""
    t = tmp_path / "ws"
    ws = _workspace(t)
    _install(t, "--workspace", str(ws))
    ws.unlink()
    proc = _install(t, "--workspace", str(_outside(tmp_path)))
    out = " ".join(_text(proc).split())
    assert _record(t) == HEADER + TOOLS + b'workspace = "conf/sdlc-workspace.toml"\n'
    assert "which a run without --workspace applies" not in out, out
    assert ('still records workspace "conf/sdlc-workspace.toml", whose file is no longer '
            'there - fix that line to name the workspace file, or remove it') in out, out


def test_the_t704_lookup_finds_the_t704_commit_itself():
    """N4: `log -1 --grep` took the newest match - the merge whose body repeats the
    subject; a later "CORRECTION to <subject>" body would take it instead."""
    assert _git("show", "-s", "--format=%s", _t704_commit()) == T704_SUBJECT
