"""T708: an install records its tools, and `--manifest`'s `expected_missing` plans with them.

`.sdlc/install-options` recorded the opt-in layers (T705) but not `--tools`, so a plain
re-run re-detected the tools - `copilot` whenever a `.github/` directory appeared - and
`expected_missing` (T706) planned with that detection: an intact `--tools claude` install
in a repository that merely holds a `.github/` listed copilot's file as expected and
missing. An install from a standalone copy of the installer (no `tools/` beside it) also
named the files that copy cannot generate. Every install now records its resolved tools
(`tools = [...]`) and, from such a copy, `standalone = true`; a plain re-run uses exactly
the recorded tools, and `expected_missing` plans with what that run would use. Each test
SPAWNS the installer and failed against the installer before T708.
"""
from __future__ import annotations

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
COPILOT = ".github/copilot-instructions.md"
CURSOR = ".cursor/rules/sdlc.mdc"
LEDGER = (".github/workflows/sdlc-ledger.yml", ".sdlc/ledger_check.py")
#: T705's header, the one every record before T708 carries.
T705_HEADER = (b"# Written by sdlc-init.py: the opt-in layers every re-run applies. "
               b"Delete a line to drop that layer.\n")
GENERATED = (".sdlc/ledger_check.py", ".github/workflows/sdlc-ledger.yml",
             ".sdlc/pipeline.md", ".claude/skills/sdlc-config/SKILL.md")


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


def _lines(target: Path) -> list[str]:
    return (target / REL).read_bytes().decode("utf-8").splitlines()


def _tools_line(target: Path) -> str | None:
    found = [line for line in _lines(target) if line.startswith("tools")]
    assert len(found) <= 1, found
    return found[0] if found else None


def _manifest(target: Path, installer: Path = INSTALLER) -> dict:
    proc = _run("--manifest", "--target", str(target), installer=installer)
    assert proc.returncode == 0, _text(proc)
    return json.loads(proc.stdout.decode("utf-8"))


def _standalone(at: Path) -> Path:
    """`sdlc-init.py` and `sdlc_templates/` alone - the shape `fetch_harness` copies."""
    at.mkdir(parents=True)
    shutil.copy2(INSTALLER, at / "sdlc-init.py")
    shutil.copytree(ROOT / "sdlc_templates", at / "sdlc_templates")
    return at / "sdlc-init.py"


def _bundle(at: Path) -> Path:
    out = at / "b.py"
    at.mkdir(parents=True)
    made = _run("--bundle", str(out))
    assert made.returncode == 0, _text(made)
    return out


def test_the_install_records_its_resolved_tools(tmp_path):
    t = _fixture(tmp_path / "listed")
    _install(t, "--tools", "claude,gemini")
    assert _tools_line(t) == 'tools = ["claude", "gemini"]', _lines(t)
    detected = _fixture(tmp_path / "auto")
    (detected / ".cursor").mkdir()
    _install(detected, "--tools", "auto")
    assert _tools_line(detected) == 'tools = ["claude", "cursor"]', _lines(detected)
    # The resolved list, never the word: a plain fresh install records what it detected.
    plain = _fixture(tmp_path / "plain")
    _install(plain)
    assert _tools_line(plain) == 'tools = ["claude"]', _lines(plain)
    assert _manifest(t)["options"]["tools"] == ["claude", "gemini"]


def test_a_plain_rerun_keeps_the_recorded_tools_and_names_a_detected_one(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude")
    (t / ".github").mkdir()
    proc = _install(t)
    out = " ".join(_text(proc).split())
    printed = proc.stdout.decode("utf-8").replace("\r\n", "\n")
    assert "tools          : claude\n" in printed, out
    assert not (t / COPILOT).exists(), out
    assert _tools_line(t) == 'tools = ["claude"]'
    note = [line for line in _text(proc).splitlines() if "copilot" in line]
    assert note and "--tools auto" in " ".join(note), out


def test_auto_spelled_out_adds_a_detected_tool(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude")
    (t / ".github").mkdir()
    _install(t, "--tools", "auto")
    assert (t / COPILOT).is_file()
    assert _tools_line(t) == 'tools = ["claude", "copilot"]', _lines(t)


def test_an_explicit_list_is_unioned_with_the_record(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude,cursor")
    current = (t / CURSOR).read_bytes()
    (t / CURSOR).write_bytes(b"an older release's rule\n")
    _install(t, "--tools", "claude")
    assert _tools_line(t) == 'tools = ["claude", "cursor"]', _lines(t)
    assert (t / CURSOR).read_bytes() == current


def test_expected_missing_ignores_a_tool_never_installed(tmp_path):
    # Shape B: `.github/` there before the install, which chose claude alone.
    b = _fixture(tmp_path / "b")
    (b / ".github").mkdir()
    _install(b, "--tools", "claude")
    m = _manifest(b)
    assert COPILOT in m["missing"] and COPILOT not in m["expected_missing"], m
    assert m["expected_missing"] == [], m["expected_missing"]
    # Shape A6: `.github/` made after the install. The ledger gate is `.github/`-driven,
    # and a re-run really writes it (out of T708's scope), so those two stay.
    a6 = _fixture(tmp_path / "a6")
    _install(a6, "--tools", "claude")
    (a6 / ".github").mkdir()
    m = _manifest(a6)
    assert m["expected_missing"] == sorted(LEDGER), m["expected_missing"]
    # And it is still what a real plain re-run writes.
    _install(a6)
    assert not (a6 / COPILOT).exists()
    assert all((a6 / rel).is_file() for rel in LEDGER)
    assert _manifest(a6)["expected_missing"] == []


def test_a_record_without_tools_falls_back_to_the_profile_line(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude")
    (t / REL).write_bytes(T705_HEADER)          # what every T705-era install wrote
    (t / ".github").mkdir()
    m = _manifest(t)
    assert COPILOT not in m["expected_missing"], m["expected_missing"]
    _install(t)
    assert not (t / COPILOT).exists()
    assert _tools_line(t) == 'tools = ["claude"]', _lines(t)
    # Where the profile line cannot be read, detection decides, as before T708.
    d = _fixture(tmp_path / "detect")
    _install(d, "--tools", "claude")
    (d / REL).write_bytes(T705_HEADER)
    (d / ".sdlc" / "profile.yml").unlink()
    (d / ".github").mkdir()
    assert COPILOT in _manifest(d)["expected_missing"]
    _install(d)
    assert (d / COPILOT).is_file()


BAD_TOOLS = {
    "unknown": b'tools = ["vim"]\n',
    "empty": b"tools = []\n",
    "not-a-list": b'tools = "claude"\n',
    "repeat": b'tools = ["claude", "claude"]\n',
    "unquoted": b"tools = [claude]\n",
}


def test_a_bad_tools_line_is_refused_by_name(tmp_path):
    for name, body in BAD_TOOLS.items():
        t = _fixture(tmp_path / name)
        _install(t)
        (t / REL).write_bytes(T705_HEADER + body)
        proc = _run("--target", str(t), "--no-graph")
        err = proc.stderr.decode("utf-8", "replace")
        assert proc.returncode == 1, (name, _text(proc))
        assert f"{REL} line 2: tools" in err, (name, err)
        assert "remove that line" in err and "Traceback" not in err, (name, err)
        assert len(err.strip().splitlines()) == 1, (name, err)
        m = _manifest(t)
        assert m["expected_missing"] is None and REL in m["unreadable"], name
        assert m["options"] is None, name


def test_a_standalone_install_expects_none_of_what_it_cannot_generate(tmp_path):
    alone = _standalone(tmp_path / "alone")
    bundle = _bundle(tmp_path / "bundle")
    c = _fixture(tmp_path / "c")
    _install(c, "--tools", "claude", installer=alone)
    d = _fixture(tmp_path / "d")
    (d / ".github").mkdir()
    _install(d, "--tools", "claude,copilot", installer=alone)
    for t in (c, d):
        assert "standalone = true" in _lines(t), _lines(t)
        assert not any((t / rel).exists() for rel in GENERATED), t
        outs = [json.dumps(_manifest(t, reader)["expected_missing"])
                for reader in (alone, bundle, INSTALLER)]
        assert outs[0] == outs[1] == outs[2] == "[]", (t.name, outs)
        assert _manifest(t)["options"]["standalone"] is True


def test_a_full_clone_rerun_of_a_standalone_install_clears_the_line(tmp_path):
    alone = _standalone(tmp_path / "alone")
    t = _fixture(tmp_path / "d")
    (t / ".github").mkdir()
    _install(t, "--tools", "claude,copilot", installer=alone)
    assert "standalone = true" in _lines(t)
    _install(t)
    for rel in GENERATED:
        assert (t / rel).is_file(), rel
    assert not any(line.startswith("standalone") for line in _lines(t)), _lines(t)
    assert _tools_line(t) == 'tools = ["claude", "copilot"]'
    assert _manifest(t)["expected_missing"] == []


# ── fix round 1 ───────────────────────────────────────────────────────────────

#: The header every T708 install writes (review finding 2): the remedies it names are the
#: ones that work, and it says what deleting the tools line or the standalone line does.
T708_HEADER = (b"# Written by sdlc-init.py: the tools and opt-in layers every re-run "
               b"applies. Delete a layer's line to drop that layer. To drop a tool, remove "
               b"it from the tools line here and from the tools: line of .sdlc/profile.yml "
               b"- a deleted tools line is read back from that profile line. The standalone "
               b"line is rewritten from the installer copy on every run.\n")
CONFIG_SKILL = ".claude/skills/sdlc-config/SKILL.md"


def _partial(at: Path) -> Path:
    """The review's finding-1 copy: the installer, its templates, and `tools/` with only
    the config engine taken out - two of the three generators present."""
    at.mkdir(parents=True)
    shutil.copy2(INSTALLER, at / "sdlc-init.py")
    shutil.copytree(ROOT / "sdlc_templates", at / "sdlc_templates")
    (at / "tools").mkdir()
    shutil.copy2(ROOT / "tools" / "__init__.py", at / "tools" / "__init__.py")
    shutil.copytree(ROOT / "tools" / "sdlc", at / "tools" / "sdlc",
                    ignore=shutil.ignore_patterns("__pycache__", "config.py"))
    assert not (at / "tools" / "sdlc" / "config.py").exists()
    return at / "sdlc-init.py"


def test_a_partial_copy_records_no_standalone_and_hides_no_deleted_file(tmp_path):
    """Review finding 1 (P2), its reproduction: a copy missing ONE generator recorded
    `standalone = true`, so `expected_missing` hid two deleted files that copy rewrites.
    The owner's Q3 (a): standalone only when all three are absent; a partial copy is
    planned as a full clone - over-reporting the one path it cannot write, never hiding
    one it does."""
    copy = _partial(tmp_path / "partial")
    t = _fixture(tmp_path / "d")
    (t / ".github").mkdir()
    _install(t, "--tools", "claude,copilot", installer=copy)
    assert not any(line.startswith("standalone") for line in _lines(t)), _lines(t)
    for rel in (".sdlc/ledger_check.py", ".sdlc/pipeline.md"):
        assert (t / rel).is_file(), rel
    assert not (t / CONFIG_SKILL).exists()
    for reader in (copy, INSTALLER):
        assert _manifest(t, reader)["expected_missing"] == [CONFIG_SKILL]
    for rel in (".sdlc/ledger_check.py", ".sdlc/pipeline.md"):
        (t / rel).unlink()
    m = _manifest(t)
    assert m["expected_missing"] == sorted([CONFIG_SKILL, ".sdlc/ledger_check.py",
                                            ".sdlc/pipeline.md"]), m["expected_missing"]
    _install(t, installer=copy)
    for rel in (".sdlc/ledger_check.py", ".sdlc/pipeline.md"):
        assert (t / rel).is_file(), rel


def test_one_decision_says_standalone_and_shapes_the_plan(tmp_path, capsys, monkeypatch):
    """Ruling 1: the recorded fact is the decision the install's own plan uses. Patched to
    say standalone for this full clone, BOTH the record and the plan must change."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sdlc_init_t708", INSTALLER)
    si = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(si)
    t = _fixture(tmp_path / "py")
    capsys.readouterr()
    assert si.main(["--target", str(t), "--no-graph", "--dry-run"]) == 0
    assert CONFIG_SKILL in capsys.readouterr().out
    monkeypatch.setattr(si, "copy_is_standalone", lambda harness=None: True)
    assert si.main(["--target", str(t), "--no-graph"]) == 0
    assert CONFIG_SKILL not in capsys.readouterr().out
    assert "standalone = true" in _lines(t)
    assert not (t / CONFIG_SKILL).exists()


def test_the_header_names_remedies_that_work(tmp_path):
    """Review finding 2: deleting the tools line brought every tool back from the profile
    at exit 0, with nothing said, while the header offered it as the way to drop one."""
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude,cursor,gemini")
    assert (t / REL).read_bytes().split(b"\n")[0] + b"\n" == T708_HEADER
    # What the header says a deleted tools line does: read back from the profile line.
    (t / REL).write_bytes(T708_HEADER)
    _install(t)
    assert _tools_line(t) == 'tools = ["claude", "cursor", "gemini"]'
    # And a deleted standalone line comes back on the next run from a standalone copy, as
    # the header says: it is rewritten from the installer copy every run.
    alone = _standalone(tmp_path / "alone")
    s = _fixture(tmp_path / "s")
    _install(s, "--tools", "claude", installer=alone)
    (s / REL).write_bytes(T708_HEADER + b'tools = ["claude"]\n')
    _install(s, installer=alone)
    assert "standalone = true" in _lines(s)


def _written(proc) -> str:
    return " ".join(_text(proc).split())


def test_dropping_a_tool_by_the_remedy_converges(tmp_path):
    """Review finding 3: removing a tool from the tools line alone left the profile's
    tools: line disagreeing with the record for ever - every plain run wrote and staged
    .sdlc/profile.yml.new. The remedy names both lines; followed, nothing is written."""
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude,cursor,gemini")
    record = (t / REL).read_bytes().replace(b'"cursor", ', b"")
    profile = (t / ".sdlc" / "profile.yml").read_bytes().replace(b"cursor, ", b"")
    assert b'tools = ["claude", "gemini"]' in record and b"tools: [claude, gemini]" in profile
    (t / REL).write_bytes(record)
    (t / ".sdlc" / "profile.yml").write_bytes(profile)
    for _ in range(2):
        proc = _install(t)
        assert "0 file(s) written" in _written(proc), _written(proc)
        assert _tools_line(t) == 'tools = ["claude", "gemini"]'
        assert _manifest(t)["staged"] == []
    # Only the tools line edited: that run stages the profile and says to move it over;
    # done, the third run writes nothing and nothing is staged.
    u = _fixture(tmp_path / "u")
    _install(u, "--tools", "claude,cursor,gemini")
    (u / REL).write_bytes((u / REL).read_bytes().replace(b'"cursor", ', b""))
    staged = u / ".sdlc" / "profile.yml.new"
    _install(u)
    assert staged.is_file()
    staged.replace(u / ".sdlc" / "profile.yml")
    proc = _install(u)
    assert "0 file(s) written" in _written(proc), _written(proc)
    assert _manifest(u)["staged"] == [] and not staged.exists()


def test_a_bad_tools_line_names_its_own_cause(tmp_path):
    """Review finding 5a: TOML-legal spellings the installer does not read were refused
    as "not a list of quoted names"."""
    for body, cause in ((b'tools = ["claude",]\n', "trailing comma"),
                        (b"tools = ['claude']\n", "double quotes")):
        t = _fixture(tmp_path / cause.replace(" ", "-"))
        _install(t)
        (t / REL).write_bytes(T708_HEADER + body)
        err = _run("--target", str(t), "--no-graph").stderr.decode("utf-8", "replace")
        assert f"{REL} line 2: tools" in err and cause in err, err
        assert "not a list of quoted names" not in err, err


def test_run_tools_takes_a_list_only_as_listed(tmp_path):
    """Review finding 5b: a raw --tools value handed to `run_tools` landed in the union
    branch with nothing listed."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sdlc_init_t708b", INSTALLER)
    si = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(si)
    root = tmp_path / "r"
    root.mkdir()
    assert si.run_tools(root, {}, None, ["cursor"])[0] == ["claude", "cursor"]
    try:
        si.run_tools(root, {}, "claude,cursor")
    except ValueError:
        pass
    else:
        raise AssertionError("a raw --tools list was read as an empty one")


def _wide_help() -> str:
    import os
    proc = subprocess.run([sys.executable, str(INSTALLER), "--help"], capture_output=True,
                          timeout=120, env={**os.environ, "COLUMNS": "4000"})
    return " ".join(proc.stdout.decode("utf-8").split())


#: Review finding 5d: the door text T708 added, pinned at both doors (findings 1-4).
DOOR_TEXT = (
    "remove it from the tools line",
    "tools: line of .sdlc/profile.yml",
    "lacks all three harness generators",
    "partial copy is not a shipped shape",
    "installed by a standalone copy before this release",
    "where the target has a .github/ directory",       # fix round 2: the gate's condition
)


def test_the_doors_say_what_the_record_does():
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    readme = readme.replace("`", "")
    help_text = _wide_help()
    for phrase in DOOR_TEXT:
        assert phrase in help_text, ("--help", phrase)
        assert phrase in readme, ("README", phrase)


def test_the_unapplied_note_names_the_profile_key(tmp_path):
    """Review finding 5c: the profile's key is `tools:`."""
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude")
    (t / REL).write_bytes(T705_HEADER)
    (t / ".cursor").mkdir()
    out = _written(_install(t))
    assert "the tools: line of .sdlc/profile.yml does not list" in out, out
