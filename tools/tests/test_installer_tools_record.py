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
#: T712: the header every install writes now - T708's, plus the declined-tools door.
T712_HEADER = (T708_HEADER[:-1] + b" A detected tool this repository does not use is "
               b"declined in a declined-tools = [...] line here; a declined tool that the "
               b"tools: line of .sdlc/profile.yml still names comes off that line too, or "
               b"the next run stages .sdlc/profile.yml.new with the shorter line, and moving "
               b"it over .sdlc/profile.yml settles it.\n")
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
    assert (t / REL).read_bytes().split(b"\n")[0] + b"\n" == T712_HEADER
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


# ── T710: a repository can decline a detected tool ───────────────────────────
# A `declined-tools = [...]` line, added by hand, silences the NOTE for those tools and
# keeps `--tools auto` and the detection fallback from adding them; an explicit list or
# `all` still installs one, and un-declines it. Each test failed before T710, whose
# record refused the line as a name it does not record.

DECLINED = 'declined-tools = ["cursor"]'


def _decline(target: Path, line: bytes = DECLINED.encode() + b"\n") -> None:
    with (target / REL).open("ab") as fh:
        fh.write(line)


def _declined_line(target: Path) -> str | None:
    found = [line for line in _lines(target) if line.startswith("declined-tools")]
    assert len(found) <= 1, found
    return found[0] if found else None


def _cursor_note(proc) -> list[str]:
    return [line for line in _text(proc).splitlines() if "NOTE" in line and "cursor" in line]


def test_a_declined_tool_is_not_named_and_the_record_settles(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude")
    (t / ".cursor").mkdir()
    _decline(t)
    proc = _install(t)
    assert not _cursor_note(proc), _text(proc)
    assert not (t / CURSOR).exists()
    assert _declined_line(t) == DECLINED, _lines(t)
    assert _tools_line(t) == 'tools = ["claude"]', _lines(t)
    again = _install(t)
    assert "0 file(s) written" in _written(again), _written(again)
    assert not _cursor_note(again) and _declined_line(t) == DECLINED


def test_the_note_names_the_declined_tools_remedy(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude")
    (t / ".cursor").mkdir()
    proc = _install(t)
    note = " ".join(" ".join(_cursor_note(proc)).split())
    assert note and "--tools auto" in note, _text(proc)          # T708's NOTE, as before
    out = _written(proc)
    assert "declined-tools = [...]" in out and "to stop this note" in out, out


def test_auto_does_not_add_a_declined_tool(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude")
    (t / ".cursor").mkdir()
    (t / ".github").mkdir()
    _decline(t)
    _install(t, "--tools", "auto")
    assert not (t / CURSOR).exists()
    assert (t / COPILOT).is_file()                   # a detected tool not declined is added
    assert _declined_line(t) == DECLINED, _lines(t)
    assert _tools_line(t) == 'tools = ["claude", "copilot"]', _lines(t)


def test_an_explicit_list_installs_and_undeclines_a_tool(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude")
    (t / ".cursor").mkdir()
    _decline(t, b'declined-tools = ["cursor", "gemini"]\n')
    _install(t, "--tools", "claude,cursor")
    assert (t / CURSOR).is_file()
    assert _tools_line(t) == 'tools = ["claude", "cursor"]', _lines(t)
    assert _declined_line(t) == 'declined-tools = ["gemini"]', _lines(t)
    # `all` installs every tool, so nothing is left declined and the line goes.
    _install(t, "--tools", "all")
    assert _declined_line(t) is None, _lines(t)


def test_a_tool_both_installed_and_declined_is_refused_by_line(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude,cursor")
    _decline(t)
    proc = _run("--target", str(t), "--no-graph")
    err = " ".join(proc.stderr.decode("utf-8", "replace").split())
    assert proc.returncode == 1, _text(proc)
    assert f"{REL} line 3" in err and "both installed" in err and "cursor" in err, err
    assert "remove that line" in err and "Traceback" not in err, err
    m = _manifest(t)
    assert m["options"] is None and REL in m["unreadable"], m


BAD_DECLINED = {
    "baseline": (b'declined-tools = ["claude"]\n', "cannot be declined"),
    "empty": (b"declined-tools = []\n", "it lists no tools"),
    "unknown": (b'declined-tools = ["nope"]\n', "'nope' is not a tool"),
    "repeat": (b'declined-tools = ["cursor", "cursor"]\n', "names a tool twice"),
}


def test_a_bad_declined_tools_line_is_refused_by_its_own_cause(tmp_path):
    for name, (body, cause) in BAD_DECLINED.items():
        t = _fixture(tmp_path / name)
        _install(t, "--tools", "claude")
        _decline(t, body)
        proc = _run("--target", str(t), "--no-graph")
        err = " ".join(proc.stderr.decode("utf-8", "replace").split())
        assert proc.returncode == 1, (name, _text(proc))
        assert f"{REL} line 3: declined-tools" in err and cause in err, (name, err)
        assert "Traceback" not in err, (name, err)
        m = _manifest(t)
        assert m["options"] is None and REL in m["unreadable"], (name, m)


def test_the_detection_fallback_leaves_out_a_declined_tool(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude")
    (t / REL).write_bytes(T708_HEADER + DECLINED.encode() + b"\n")    # no tools line
    (t / ".sdlc" / "profile.yml").unlink()                           # no profile line
    (t / ".cursor").mkdir()
    assert CURSOR not in _manifest(t)["expected_missing"]
    _install(t)
    assert not (t / CURSOR).exists()
    assert _declined_line(t) == DECLINED, _lines(t)


def test_the_manifest_carries_declined_tools_and_it_is_not_a_layer(tmp_path):
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude", "--claude-extras")
    (t / ".cursor").mkdir()
    _decline(t)
    m = _manifest(t)
    assert m["options"]["declined-tools"] == ["cursor"], m["options"]
    assert m["expected_missing"] == [], m["expected_missing"]
    assert m["layers"] == ["claude-extras"], m["layers"]               # T711


#: T710's door text, at --help (the --tools option and the epilog, each), in the README and
#: in docs/bootstrap.md (review F2).
DECLINED_DOOR = "declined-tools = [...] line of .sdlc/install-options"


def _help_doors() -> tuple[str, str]:
    """(the --tools option's help, the epilog) of the wide `--help` - review F3: the
    whole text held the phrase twice, so deleting either sentence left a test green."""
    help_text = _wide_help()
    epilog = help_text[help_text.index("exit status:"):]
    options = help_text[:help_text.index("exit status:")]
    start = options.rindex("--tools TOOLS")         # the option block, not the usage line
    end = options.index("--force", start)
    assert "comma list of tools" in options[start:end], options[start:end]
    return options[start:end], epilog


def test_the_doors_name_the_declined_tools_line():
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert DECLINED_DOOR in readme.replace("`", ""), "README"
    guide = " ".join((ROOT / "docs" / "bootstrap.md").read_text(encoding="utf-8").split())
    assert DECLINED_DOOR in guide.replace("`", ""), "docs/bootstrap.md"
    tools_help, epilog = _help_doors()
    assert DECLINED_DOOR in tools_help, ("--tools help", tools_help)
    assert DECLINED_DOOR in epilog, "epilog"


# ── T710 fix round 1 ──────────────────────────────────────────────────────────

def _profile_base_declined(target: Path) -> None:
    """Review F1's chain: an install that wired cursor, its record replaced by one with
    no tools line and cursor declined, and `.cursor/` gone - the base is the profile's
    `tools: [claude, cursor]` line."""
    _install(target, "--tools", "claude,cursor")
    assert b"tools: [claude, cursor]" in (target / ".sdlc" / "profile.yml").read_bytes()
    (target / REL).write_bytes(T705_HEADER + DECLINED.encode() + b"\n")
    shutil.rmtree(target / ".cursor")


def test_a_profile_base_never_reinstalls_a_declined_tool(tmp_path):
    """Review F1 (P2): with no tools line, the profile's tools: line was the base and was
    not filtered, so a plain run reinstalled the declined tool and deleted the line."""
    plain = _fixture(tmp_path / "plain")
    _profile_base_declined(plain)
    m = _manifest(plain)
    assert not any(rel.startswith(".cursor/") for rel in m["expected_missing"]), m
    proc = _install(plain)
    assert not (plain / CURSOR).exists(), _text(proc)
    assert _declined_line(plain) == DECLINED, _lines(plain)
    assert _tools_line(plain) == 'tools = ["claude"]', _lines(plain)
    auto = _fixture(tmp_path / "auto")
    _profile_base_declined(auto)
    _install(auto, "--tools", "auto")
    assert not (auto / CURSOR).exists()
    assert _declined_line(auto) == DECLINED, _lines(auto)


def test_a_run_that_undeclines_a_tool_says_so(tmp_path):
    """Review F6: an explicit list or `all` dropped a hand-written declaration in silence."""
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude")
    _decline(t)
    proc = _install(t, "--tools", "claude,cursor")
    notes = [line for line in _text(proc).splitlines()
             if "NOTE" in line and "declined-tools" in line]
    assert len(notes) == 1 and "cursor" in notes[0], _text(proc)
    assert "no longer declines it" in notes[0], notes[0]      # T712 3c: the wet verb
    assert _declined_line(t) is None
    # A plain run after it un-declines nothing and says nothing of the kind.
    again = _install(t)
    assert not [line for line in _text(again).splitlines()
                if "NOTE" in line and "declined-tools" in line], _text(again)


# ── T712 (2, 3c): the profile step at every declined-tools door ──────────────

#: The clause every declined-tools door carries (T712, owner Q4 A): a declined tool the
#: profile's `tools:` line still names leaves `.sdlc/profile.yml.new` staged on every run
#: until that line is settled - README:336's own drop-a-tool wording, reused.
PROFILE_STEP = ("a declined tool that the tools: line of .sdlc/profile.yml still names "
                "comes off that line too, or the next run stages .sdlc/profile.yml.new "
                "with the shorter line, and moving it over .sdlc/profile.yml settles it")


def _passage(text: str, start: str, end: str | None) -> str:
    """The declined-tools passage of one door: from `start` to `end` (or the end), with
    whitespace collapsed and backticks dropped, so the clause is asked of THIS passage
    and not of the drop-a-tool sentence before it. "" when the door has no such passage."""
    flat = " ".join(text.split()).replace("`", "")
    if (at := flat.find(start)) < 0:
        return ""
    return flat[at:flat.index(end, at)] if end else flat[at:]


def test_every_declined_tools_door_names_the_profile_step(tmp_path):
    """Each door is asked on its own, and every door that lacks the clause is named."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs" / "bootstrap.md").read_text(encoding="utf-8")
    tools_help, epilog = _help_doors()
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude")
    header = _lines(t)[0]
    assert header.startswith("# Written by sdlc-init.py"), header
    by_hand = "A detected tool the repository does not use is declined by hand"
    doors = {
        "README": _passage(readme, by_hand, "An install from an installer"),
        "docs/bootstrap.md": _passage(guide, by_hand, "Exit codes matter here"),
        "--tools help": _passage(tools_help, "declined-tools = [...]", None),
        "epilog": _passage(epilog, "declined by hand", "An install from an installer"),
        "the record's header": _passage(header, "declined-tools = [...]", None),
    }
    assert all(doors[d] for d in ("README", "docs/bootstrap.md", "--tools help",
                                  "epilog")), doors
    assert [door for door, text in doors.items() if PROFILE_STEP not in text] == []


def _declined_profile_tool(target: Path) -> None:
    """The item-2 chain: cursor detected and installed, then declined by the both-lines
    refusal's own remedy - off the tools line, onto a declined-tools line - while the
    profile's `tools:` line still names it."""
    (target / ".cursor").mkdir()
    _install(target, "--tools", "claude,cursor")
    profile = target / ".sdlc" / "profile.yml"
    assert b"tools: [claude, cursor]" in profile.read_bytes()
    header = (target / REL).read_bytes().split(b"\n")[0] + b"\n"
    (target / REL).write_bytes(header + b'tools = ["claude"]\n' + DECLINED.encode() + b"\n")


def test_declining_a_profile_tool_settles_by_following_the_door(tmp_path):
    """T712 (2): passes on the pre-T712 code by design - it is the "follow the remedy"
    half of the door test above (its mutation is named in the T712 report)."""
    t = _fixture(tmp_path / "py")
    _declined_profile_tool(t)
    profile, staged = t / ".sdlc" / "profile.yml", t / ".sdlc" / "profile.yml.new"
    for _ in range(2):                   # staged again on every run until it is settled
        proc = _install(t)
        assert ".sdlc/profile.yml.new" in _text(proc), _text(proc)
        assert b"\ntools: [claude] " in staged.read_bytes()
    # Do what the door says: move the staged copy over the profile.
    staged.replace(profile)
    proc = _install(t)
    assert "Done. 0 file(s) written" in _text(proc), _text(proc)
    assert not staged.exists()
    assert _declined_line(t) == DECLINED, _lines(t)
    assert _tools_line(t) == 'tools = ["claude"]', _lines(t)
    assert CURSOR not in _text(proc), _text(proc)


def test_a_dry_run_that_would_undecline_says_would(tmp_path):
    """T712 (3c): the dry-run verb had no test - passes on the pre-T712 code, red if the
    verbs are swapped (the mutation named in the T712 report)."""
    t = _fixture(tmp_path / "py")
    _install(t, "--tools", "claude")
    _decline(t)
    before = (t / REL).read_bytes()
    proc = _install(t, "--dry-run", "--tools", "claude,cursor")
    notes = [line for line in _text(proc).splitlines()
             if "NOTE" in line and "declined-tools" in line]
    assert len(notes) == 1 and "would no longer decline it" in notes[0], _text(proc)
    assert (t / REL).read_bytes() == before
    assert not (t / CURSOR).exists()


def test_route_1_hand_editing_the_profile_line_settles_with_nothing_staged(tmp_path):
    """T712 fix round 1, F6: the clause's first route - take the declined tool off the
    profile's `tools:` line by hand - settles at once: nothing staged, and the next run
    writes nothing."""
    t = _fixture(tmp_path / "py")
    _declined_profile_tool(t)
    profile, staged = t / ".sdlc" / "profile.yml", t / ".sdlc" / "profile.yml.new"
    body = profile.read_bytes()
    assert body.count(b"tools: [claude, cursor]") == 1
    profile.write_bytes(body.replace(b"tools: [claude, cursor]", b"tools: [claude]"))
    first = _install(t)
    assert "= unchanged" in _text(first) and not staged.exists(), _text(first)
    again = _install(t)
    assert "Done. 0 file(s) written" in _text(again) and not staged.exists(), _text(again)
    assert _declined_line(t) == DECLINED, _lines(t)
    assert CURSOR not in _text(again), _text(again)


def test_the_both_lines_refusal_names_the_profile_step(tmp_path):
    """T712 fix round 1, F2: the refusal's remedy (take the tool off one line) is how a
    repository reaches the state the clause is about, so the refusal carries it; then
    the remedy is followed and accepted."""
    t = _fixture(tmp_path / "py")
    (t / ".cursor").mkdir()
    _install(t, "--tools", "claude,cursor")
    header = (t / REL).read_bytes().split(b"\n")[0] + b"\n"
    (t / REL).write_bytes(header + b'tools = ["claude", "cursor"]\n'
                          + DECLINED.encode() + b"\n")
    refused = _run("--target", str(t), "--no-graph")
    said = " ".join(_text(refused).split())
    assert refused.returncode == 1 and "both installed" in said, said
    assert PROFILE_STEP in said, said
    (t / REL).write_bytes(header + b'tools = ["claude"]\n' + DECLINED.encode() + b"\n")
    proc = _install(t)
    assert ".sdlc/profile.yml.new" in _text(proc), _text(proc)
