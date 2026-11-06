"""T704: the installer says what it owns - `sdlc-init.py --manifest`, and the tracked
`.sdlc/harness-version` every install writes.

A caller that fingerprints an adopter repository needs the harness version and every
installer-owned path with a digest that is the same for the same install on any machine.
Each test here SPAWNS the installer (a CLI's contract is a process: exit code, stdout, and
the target's bytes before and after), and each failed against the installer before T704,
which had no `--manifest` flag and wrote no version record.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.tests import _project  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "sdlc-init.py"


def _load():
    spec = importlib.util.spec_from_file_location("sdlc_init_manifest", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SI = _load()


def _run(installer: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(installer), *args],
                          capture_output=True, timeout=600)


def _text(proc: subprocess.CompletedProcess) -> str:
    return (proc.stdout + proc.stderr).decode("utf-8", "replace")


def _fixture(root: Path, stack: str) -> Path:
    root.mkdir(parents=True)
    if stack == "python":
        (root / "pyproject.toml").write_bytes(b'[project]\nname = "tiny"\nversion = "0.0.1"\n')
        (root / "tiny.py").write_bytes(b"X = 1\n")
    else:
        (root / "package.json").write_bytes(b'{"name": "tiny", "version": "0.0.1"}\n')
        (root / "index.js").write_bytes(b"module.exports = 1;\n")
    _project.seed(root)
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True,
                   timeout=120)
    return root


def _install(installer: Path, target: Path, *extra: str) -> None:
    proc = _run(installer, "--target", str(target), "--no-graph", "--tools", "claude", *extra)
    assert proc.returncode == 0, _text(proc)


def _manifest(installer: Path, target: Path) -> dict:
    proc = _run(installer, "--manifest", "--target", str(target))
    assert proc.returncode == 0, _text(proc)
    return json.loads(proc.stdout.decode("utf-8"))


def _harness_copy(at: Path) -> Path:
    """A harness clone in the shape the installer reads: `sdlc-init.py` beside its
    editable templates, plus the sources the skill's commands are rooted at, so the
    installed SKILL.md carries this clone's absolute path."""
    at.mkdir(parents=True)
    shutil.copy2(INSTALLER, at / "sdlc-init.py")
    shutil.copytree(ROOT / "sdlc_templates", at / "sdlc_templates")
    (at / "tools" / "sdlc").mkdir(parents=True)
    (at / "tools" / "sdlc" / "stage.py").write_bytes(b"import sys\nsys.exit(0)\n")
    (at / "sdlc" / "stages").mkdir(parents=True)
    return at


SKILL = ".claude/skills/sdlc/SKILL.md"


def test_the_version_constant_is_pyprojects_version():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert SI.HARNESS_VERSION == data["project"]["version"]


def test_an_install_writes_a_tracked_harness_version_and_the_manifest_reads_it(tmp_path):
    t = _fixture(tmp_path / "py", "python")
    dry = _run(INSTALLER, "--target", str(t), "--no-graph", "--tools", "claude", "--dry-run")
    assert dry.returncode == 0, _text(dry)
    assert ".sdlc/harness-version" in dry.stdout.decode("utf-8")
    assert not (t / ".sdlc").exists(), "a dry run wrote files"
    _install(INSTALLER, t)
    assert (t / ".sdlc" / "harness-version").read_bytes() == \
        (SI.HARNESS_VERSION + "\n").encode("ascii")
    ignored = subprocess.run(["git", "-C", str(t), "check-ignore", "-q",
                              ".sdlc/harness-version"], capture_output=True, timeout=120)
    assert ignored.returncode == 1, "the generated .gitignore ignores the version record"
    m = _manifest(INSTALLER, t)
    assert m["installer"] == SI.HARNESS_VERSION
    assert m["installed"] == SI.HARNESS_VERSION
    assert ".sdlc/harness-version" in m["files"]


def test_two_stacks_share_their_digests_and_missing_is_named(tmp_path):
    py = _fixture(tmp_path / "py", "python")
    node = _fixture(tmp_path / "node", "javascript")
    _install(INSTALLER, py)
    _install(INSTALLER, node)
    a, b = _manifest(INSTALLER, py), _manifest(INSTALLER, node)
    shared = set(a["files"]) & set(b["files"])
    for rel in (SKILL, ".sdlc/policy.yml", ".sdlc/review-rubric.md",
                ".sdlc/plan-template.md", ".sdlc/harness-version"):
        assert rel in shared, rel
    # Fix round 1: what differs by design is under `per_repo`, so `files` compares clean.
    differ = sorted(r for r in shared if a["files"][r] != b["files"][r])
    assert differ == [], differ
    for rel in (".sdlc/profile.yml", ".sdlc/pipeline.md"):
        assert rel in a["per_repo"] and rel not in a["files"], rel
    assert a["per_repo"][".sdlc/profile.yml"] != b["per_repo"][".sdlc/profile.yml"]
    # Paths are posix and relative; owned-but-absent paths are named, never dropped.
    assert all("\\" not in r and not r.startswith("/") for r in a["files"])
    assert ".cursor/rules/sdlc.mdc" in a["missing"]
    assert a["missing"] == sorted(a["missing"])
    assert not set(a["missing"]) & set(a["files"])
    assert ".sdlc/.installed-digests" not in a["files"]
    assert a["policy"] == a["files"][".sdlc/policy.yml"]


def test_two_harness_clone_paths_give_the_same_skill_digest(tmp_path):
    clone_a = _harness_copy(tmp_path / "a" / "harness")
    clone_b = _harness_copy(tmp_path / "b dir" / "elsewhere")
    ta = _fixture(tmp_path / "ta", "python")
    tb = _fixture(tmp_path / "tb", "python")
    _install(clone_a / "sdlc-init.py", ta)
    _install(clone_b / "sdlc-init.py", tb)
    skill_a = (ta / SKILL).read_text(encoding="utf-8")
    assert clone_a.resolve().as_posix() in skill_a, "the fixture no longer embeds a path"
    assert (ta / SKILL).read_bytes() != (tb / SKILL).read_bytes()
    ma = _manifest(clone_a / "sdlc-init.py", ta)
    mb = _manifest(clone_b / "sdlc-init.py", tb)
    assert ma["files"][SKILL] == mb["files"][SKILL]
    # And read by the OTHER clone - the fleet case, where the reader's clone is not the
    # installer's: the target's own recorded path is masked too.
    cross = _manifest(clone_b / "sdlc-init.py", ta)
    assert cross["files"][SKILL] == ma["files"][SKILL]


def test_a_crlf_copy_has_the_same_digest(tmp_path):
    t = _fixture(tmp_path / "py", "python")
    _install(INSTALLER, t)
    before = _manifest(INSTALLER, t)
    for rel in (SKILL, ".sdlc/policy.yml"):
        p = t / rel
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    after = _manifest(INSTALLER, t)
    assert after["files"] == before["files"]


def _snapshot(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        st = p.stat()
        out[p.relative_to(root).as_posix()] = (
            p.read_bytes() if p.is_file() else None, st.st_mtime_ns)
    return out


def test_the_manifest_is_byte_stable_and_writes_nothing(tmp_path):
    t = _fixture(tmp_path / "py", "python")
    _install(INSTALLER, t)
    before = _snapshot(t)
    one = _run(INSTALLER, "--manifest", "--target", str(t))
    two = _run(INSTALLER, "--manifest", "--target", str(t))
    assert one.returncode == 0 and two.returncode == 0, _text(one) + _text(two)
    assert one.stdout == two.stdout
    assert one.stderr == b""
    assert b"\r\n" not in one.stdout
    assert one.stdout == (json.dumps(json.loads(one.stdout), sort_keys=True, indent=2)
                          + "\n").encode("utf-8")
    assert _snapshot(t) == before, "--manifest changed the target"


def test_profile_changed_reads_the_sidecar(tmp_path):
    t = _fixture(tmp_path / "py", "python")
    _install(INSTALLER, t)
    assert _manifest(INSTALLER, t)["profile_changed"] is False
    p = t / ".sdlc" / "profile.yml"
    p.write_bytes(p.read_bytes() + b"# an adopter's edit\n")
    assert _manifest(INSTALLER, t)["profile_changed"] is True
    (t / ".sdlc" / ".installed-digests").unlink()
    assert _manifest(INSTALLER, t)["profile_changed"] is None


def test_a_target_with_no_sdlc_is_refused_by_name(tmp_path):
    t = tmp_path / "bare"
    t.mkdir()
    proc = _run(INSTALLER, "--manifest", "--target", str(t))
    err = proc.stderr.decode("utf-8")
    assert proc.returncode != 0
    assert proc.stdout == b""
    assert "Traceback" not in err and ".sdlc" in err
    assert len(err.strip().splitlines()) == 1, err
    assert list(t.iterdir()) == [], "a refused manifest wrote into the target"


def test_an_install_flag_with_manifest_is_refused_by_name(tmp_path):
    t = _fixture(tmp_path / "py", "python")
    _install(INSTALLER, t)
    before = _snapshot(t)
    for flag in ("--dry-run", "--force", "--claude-extras", "--no-graph"):
        proc = _run(INSTALLER, "--manifest", "--target", str(t), flag)
        err = proc.stderr.decode("utf-8")
        assert proc.returncode == 2, _text(proc)
        assert flag in err and "--manifest" in err and "Traceback" not in err, err
        # Refused by name in one line, not answered by argparse's usage dump.
        assert "usage:" not in err and len(err.strip().splitlines()) == 1, err
        assert proc.stdout == b""
    assert _snapshot(t) == before


# ── fix round 1 ───────────────────────────────────────────────────────────────

def test_a_merged_file_digests_only_the_harness_block(tmp_path):
    t = _fixture(tmp_path / "py", "python")
    _install(INSTALLER, t, "--claude-extras", "--tools", "claude,gemini")
    before = _manifest(INSTALLER, t)
    for rel in ("CLAUDE.md", "GEMINI.md"):
        assert rel in before["files"], rel
        p = t / rel
        p.write_bytes(b"# The adopter's own heading\n\nTheir notes.\n\n" + p.read_bytes()
                      + b"\nMore of their own text.\n")
    after = _manifest(INSTALLER, t)
    assert after["files"] == before["files"], "the adopter's own text changed a digest"
    # The block itself is still what is digested: an edit inside it is drift.
    g = t / "GEMINI.md"
    g.write_bytes(g.read_bytes().replace(b"## ", b"## EDITED ", 1))
    assert _manifest(INSTALLER, t)["files"]["GEMINI.md"] != before["files"]["GEMINI.md"]


def test_a_merge_target_with_no_harness_block_is_missing(tmp_path):
    t = _fixture(tmp_path / "py", "python")
    (t / "CLAUDE.md").write_bytes(b"# Ours alone\n\nNo harness block here.\n")
    _install(INSTALLER, t)
    m = _manifest(INSTALLER, t)
    assert "CLAUDE.md" in m["missing"]
    assert "CLAUDE.md" not in m["files"] and "CLAUDE.md" not in m["per_repo"]


def test_settings_digest_ignores_the_adopters_entries_and_is_per_repo(tmp_path):
    t = _fixture(tmp_path / "py", "python")
    _install(INSTALLER, t, "--claude-extras")
    rel = ".claude/settings.json"
    before = _manifest(INSTALLER, t)
    assert rel in before["per_repo"] and rel not in before["files"]
    p = t / rel
    data = json.loads(p.read_text(encoding="utf-8"))
    data["model"] = "the-adopters-choice"
    data["permissions"]["allow"].append("Bash(make lint:*)")
    data["hooks"].setdefault("Stop", []).append({"hooks": [{"type": "command",
                                                            "command": "echo theirs"}]})
    p.write_bytes((json.dumps(data, indent=4) + "\n").encode("utf-8"))
    assert _manifest(INSTALLER, t)["per_repo"][rel] == before["per_repo"][rel]
    # Removing an entry the installer writes is drift.
    data["hooks"]["SessionStart"] = []
    p.write_bytes((json.dumps(data) + "\n").encode("utf-8"))
    assert _manifest(INSTALLER, t)["per_repo"][rel] != before["per_repo"][rel]


def test_every_rendered_file_is_masked_by_a_reader_from_another_clone(tmp_path):
    """The masker reads the installing clone's path off each file's OWN installer-owned
    text, in two phrasings, and masks only that file (T704-R4). What this checks: every
    file `plan_install_ops` renders from a clone at a path of its own - every tool, the
    extras, the graph and config skills, the block of each merged file - is masked by
    this repository's installer, file by file, so a template that renders the path in a
    phrasing the masker does not read fails here. What it does not reach: files drawn
    outside the planner (`.sdlc/pipeline.md`, drawn by `stage.py`)."""
    clone = _harness_copy(tmp_path / "Clone With Space-zq7" / "h")
    (clone / "tools" / "sdlc" / "config.py").write_bytes(b"import sys\nsys.exit(0)\n")
    spec = importlib.util.spec_from_file_location("sdlc_init_clone",
                                                  clone / "sdlc-init.py")
    other = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(other)
    assert other.harness_root() != SI.harness_root()
    target = _fixture(tmp_path / "t", "python")
    meta, body = other.parse_frontmatter(other.load_template("SKILL.md"))
    body, why = other.render_skill_body(body)
    assert why is None
    ops, _ = other.plan_install_ops(
        target, dict(other._MANIFEST_PROFILE), list(other.ALL_TOOLS), body,
        meta.get("description", ""), force=True, workspace=None, workspace_path=None,
        with_graph=True, extras_on=True, corpus_replay=True, snapshot_cadence=True,
        installed_digests={})
    texts = {op["rel"]: (SI.harness_block(op["content"]) if op["kind"] == "block"
                         else op["content"])
             for op in ops if op.get("content")}
    path = clone.resolve().as_posix()
    carrying = sorted(r for r, x in texts.items() if path in x)
    assert len(carrying) >= 5, f"the fixture stopped embedding the clone path: {carrying}"
    leaks = sorted(r for r, x in texts.items()
                   if path in SI._mask_recorded(x) or str(clone.resolve())
                   in SI._mask_recorded(x))
    assert leaks == [], f"the clone path survives masking in: {leaks}"


def test_the_guard_masks_per_file_so_a_new_phrasing_fails_it():
    """The positive control for the guard above (review finding 3): a file whose text
    names the path only in a phrasing the masker does not read is NOT masked by the
    spelling another file records."""
    recorded = 'Run `uv run --project "/opt/zq7/h" python x`.'
    other = "The harness sits at /opt/zq7/h/tools."
    assert "/opt/zq7/h" not in SI._mask_recorded(recorded)
    assert "/opt/zq7/h" in SI._mask_recorded(other)


# ── fix round 2 ───────────────────────────────────────────────────────────────

def _all_digests(m: dict) -> dict:
    return {**m["files"], **{"per_repo:" + k: v for k, v in m["per_repo"].items()}}


def _rich(tmp_path: Path) -> Path:
    t = _fixture(tmp_path / "py", "python")
    (t / ".github").mkdir()
    _install(INSTALLER, t, "--claude-extras", "--tools", "claude,gemini,copilot")
    return t


def test_adopter_text_outside_a_block_changes_no_digest(tmp_path):
    """Review finding 1, first reproduction: a backticked sentence above CLAUDE.md's block
    became a masking token for every file."""
    t = _rich(tmp_path)
    before = _manifest(INSTALLER, t)
    p = t / "CLAUDE.md"
    p.write_bytes(b"The config lives under `the`\n"
                  b"Ours lives under `C:/Users/someone/proj`\n"
                  b'Try `uv run --project "/usr/local" python x` and '
                  b'uv run --project "tools" too.\n\n' + p.read_bytes())
    after = _manifest(INSTALLER, t)
    assert _all_digests(after) == _all_digests(before)


def test_a_profile_value_masks_nothing_elsewhere(tmp_path):
    """Review finding 1, second reproduction: a profile.yml value holding
    `uv run --project "tools"` rewrote seven unrelated digests."""
    t = _rich(tmp_path)
    before = _manifest(INSTALLER, t)
    p = t / ".sdlc" / "profile.yml"
    text = p.read_bytes()
    assert b'lint_command: ""' in text
    p.write_bytes(text.replace(b'lint_command: ""',
                               b"lint_command: 'uv run --project \"tools\" ruff'", 1))
    after = _manifest(INSTALLER, t)
    assert after["files"] == before["files"]
    assert after["per_repo"][".sdlc/profile.yml"] != before["per_repo"][".sdlc/profile.yml"]


def test_the_readers_own_path_masks_nothing(tmp_path, monkeypatch):
    """Suspected item 1: a reader whose own path is a string adopter text holds. Masking
    reads paths off the target alone, so two readers at different paths agree."""
    t = _rich(tmp_path)
    reader = (tmp_path / "h").resolve()
    for rel in (".sdlc/profile.yml", ".sdlc/review-rubric.md"):
        p = t / rel
        p.write_bytes(p.read_bytes() + f"\n# see {reader.as_posix()}/x\n".encode("utf-8"))
    monkeypatch.setattr(SI, "harness_root", lambda: reader)
    one = SI.manifest(t)
    monkeypatch.setattr(SI, "harness_root", lambda: (tmp_path / "elsewhere").resolve())
    two = SI.manifest(t)
    assert one == two


def _standalone(at: Path) -> Path:
    at.mkdir(parents=True)
    shutil.copy2(INSTALLER, at / "sdlc-init.py")
    shutil.copytree(ROOT / "sdlc_templates", at / "sdlc_templates")
    return at / "sdlc-init.py"


def test_the_owned_set_does_not_narrow_with_the_reader(tmp_path):
    """Review finding 2 (T704-R5): a reader with no tools/ beside it - a standalone copy,
    or a bundle - names the same paths, with the same digests, as the full clone."""
    t = _rich(tmp_path)
    for rel in (".sdlc/ledger_check.py", ".github/workflows/sdlc-ledger.yml",
                ".sdlc/pipeline.md", ".claude/skills/sdlc-config/SKILL.md"):
        assert (t / rel).is_file(), rel
    full = _manifest(INSTALLER, t)
    standalone = _standalone(tmp_path / "alone")
    bundle = tmp_path / "bundle" / "b.py"
    bundle.parent.mkdir()
    made = _run(INSTALLER, "--bundle", str(bundle))
    assert made.returncode == 0, _text(made)
    for reader in (standalone, bundle):
        m = _manifest(reader, t)
        for key in ("files", "per_repo", "missing", "unreadable", "staged"):
            assert m[key] == full[key], (reader, key)


def test_an_unreadable_profile_is_no_verdict(tmp_path):
    """Review finding 4: a profile.yml nobody could read gave profile_changed true."""
    t = _rich(tmp_path)
    (t / ".sdlc" / "profile.yml").write_bytes(b"\xff\xfe not utf-8 \x80\n")
    m = _manifest(INSTALLER, t)
    assert ".sdlc/profile.yml" in m["unreadable"]
    assert m["profile_changed"] is None


def test_a_staged_new_file_is_reported(tmp_path):
    """Review finding 5: an install that declined to overwrite an edit leaves <rel>.new."""
    t = _fixture(tmp_path / "py", "python")
    _install(INSTALLER, t)
    p = t / ".sdlc" / "profile.yml"
    p.write_bytes(p.read_bytes() + b"# an adopter's edit\n")
    _install(INSTALLER, t)
    assert (t / ".sdlc" / "profile.yml.new").is_file()
    assert _manifest(INSTALLER, t)["staged"] == [".sdlc/profile.yml.new"]


def test_the_null_and_unreadable_branches(tmp_path):
    """Review finding 8: installed null, policy null, a non-UTF-8 file, and a merged file
    with unbalanced markers."""
    t = _rich(tmp_path)
    (t / ".sdlc" / "harness-version").unlink()
    (t / ".sdlc" / "policy.yml").unlink()
    (t / ".sdlc" / "review-rubric.md").write_bytes(b"\xff\xfe\x80\n")
    g = t / "GEMINI.md"
    g.write_bytes(g.read_bytes() + ("\n" + SI.BLOCK_BEGIN + "\n").encode("utf-8"))
    m = _manifest(INSTALLER, t)
    assert m["installed"] is None and ".sdlc/harness-version" in m["missing"]
    assert m["policy"] is None and ".sdlc/policy.yml" in m["missing"]
    assert ".sdlc/review-rubric.md" in m["unreadable"]
    assert "GEMINI.md" in m["unreadable"] and "GEMINI.md" not in m["files"]


def test_an_explicit_default_or_an_abbreviation_is_refused(tmp_path):
    """Review finding 9: `--tools auto` passed because it equals the default."""
    t = _fixture(tmp_path / "py", "python")
    _install(INSTALLER, t)
    for argv, named in ((["--tools", "auto"], "--tools"), (["--dry"], "--dry-run")):
        proc = _run(INSTALLER, "--manifest", "--target", str(t), *argv)
        err = proc.stderr.decode("utf-8")
        assert proc.returncode == 2 and named in err and proc.stdout == b"", _text(proc)


def test_a_component_command_rule_counts_as_the_installers(tmp_path):
    """Suspected item 2: a workspace install's allow rule comes from a component's command,
    which single-repository detection never sees. The rule is read off profile.yml."""
    t = tmp_path / "ws"
    (t / ".sdlc").mkdir(parents=True)
    (t / ".sdlc" / "profile.yml").write_bytes(
        b'test_command: ""\ncomponents:\n  - name: "svc"\n    test_command: "cargo test"\n')
    rule = SI.wrapper_permission("cargo test")
    assert rule
    ours = SI.build_settings({"test_command": "", "vcs": {}}, None)
    settings = {"permissions": {"allow": ours["permissions"]["allow"] + [rule],
                                "deny": ours["permissions"]["deny"]},
                "hooks": ours["hooks"]}
    with_rule = SI._owned_settings(t, json.dumps(settings))
    settings["permissions"]["allow"].remove(rule)
    without = SI._owned_settings(t, json.dumps(settings))
    assert with_rule != without, "the component's rule was read as the adopter's"


def _dangling_link(link: Path, tmp: Path) -> None:
    gone = tmp / "gone-target"
    if sys.platform == "win32":
        import _winapi
        gone.mkdir()
        _winapi.CreateJunction(str(gone), str(link))
        gone.rmdir()
    else:
        link.symlink_to(gone)
    assert not link.exists() and os.path.lexists(link)


def test_a_dangling_link_at_an_owned_path_is_unreadable_not_missing(tmp_path):
    """Suspected item 3: `_read` tests exists(), which is False for a dangling link."""
    t = _rich(tmp_path)
    (t / ".sdlc" / "review-rubric.md").unlink()
    _dangling_link(t / ".sdlc" / "review-rubric.md", tmp_path)
    m = _manifest(INSTALLER, t)
    assert ".sdlc/review-rubric.md" in m["unreadable"]
    assert ".sdlc/review-rubric.md" not in m["missing"]
