"""T371b: the configuration wizard's two front ends, over `sdlc config` (T371a).

- `/sdlc-config` - a skill the installer fans out the way it fans out the graph
  skill: its own file for Claude Code, Cursor and Windsurf, a section of the ONE
  harness block for Copilot, Gemini, Aider and Codex, and only where the harness
  beside the installer ships `tools/sdlc/config.py`.
- `sdlc config --interactive` - the terminal loop, which refuses without a TTY
  rather than hang where an agent's tool call has none.
- INIT's stop-and-fill message names both ways to answer.

The commands a document teaches are taken OUT of the installed text and run -
an assembled command would test this test, not the document.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import config as C  # noqa: E402
from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import stage as S  # noqa: E402
from tools.tests import _project  # noqa: E402

INSTALLER = ROOT / "sdlc-init.py"
CONFIG = ROOT / "tools" / "sdlc" / "config.py"
PROFILE = Path(".sdlc") / P.PROFILE_NAME
PROJECT = Path("sdlc-project.toml")
HEADING = "# Configure the SDLC harness (sdlc-config)"


def _si():
    spec = importlib.util.spec_from_file_location("sdlc_init_config_front_ends", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SI = _si()


def _run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600, **kw)


def _out(done: subprocess.CompletedProcess) -> str:
    return done.stdout + done.stderr


def _target(tmp_path: Path, seeded: bool = True) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    if seeded:
        _project.seed(root)
    subprocess.run(["git", "init", "-q", str(root)], capture_output=True, timeout=120)
    return root


def _install(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return _run([sys.executable, str(INSTALLER), "--target", str(root), "--no-graph",
                 *extra])


def _fenced(text: str) -> list[str]:
    """Every non-blank line inside a ``` fence."""
    out, inside = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            inside = not inside
            continue
        if inside and line.strip():
            out.append(line.strip())
    return out


def _bytes(root: Path) -> dict[str, bytes | None]:
    return {rel.as_posix(): ((root / rel).read_bytes() if (root / rel).is_file() else None)
            for rel in (PROFILE, S.DIAGRAM_FILE, PROJECT)}


@pytest.fixture(scope="module")
def installed(tmp_path_factory) -> Path:
    """ONE real install, copied for each test that needs one."""
    root = _target(tmp_path_factory.mktemp("installed"))
    done = _install(root)
    assert done.returncode == 0, _out(done)
    return root


@pytest.fixture
def repo(installed: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "copy"
    shutil.copytree(installed, dst)
    return dst


# ── /sdlc-config: the skill and its fan-out ──────────────────────────────────


def test_the_config_skill_is_rendered_for_this_harness():
    desc, body = SI.config_skill(ROOT)
    assert desc and "@@" not in body
    assert f'"{ROOT.resolve().as_posix()}/tools/sdlc/config.py"' in body
    assert body.lstrip().startswith(HEADING)
    assert "sdlc-graph" not in body, "a no-graph block must stay free of the graph's name"


def test_a_harness_without_the_engine_ships_no_config_skill(tmp_path):
    """A standalone installer cannot teach a command it does not ship."""
    assert SI.config_skill(tmp_path) is None


def test_file_kind_tools_get_an_sdlc_config_file():
    desc, body = SI.config_skill(ROOT)
    assert SI.config_tool_files("claude", body, desc) == [
        (".claude/skills/sdlc-config/SKILL.md",
         f"---\nname: sdlc-config\ndescription: {desc}\n---\n\n{body}", "file")]
    (rel, content, kind), = SI.config_tool_files("cursor", body, desc)
    assert (rel, kind) == (".cursor/rules/sdlc-config.mdc", "file")
    assert content.startswith("---\ndescription:")
    (rel, _content, kind), = SI.config_tool_files("windsurf", body, desc)
    assert (rel, kind) == (".windsurf/rules/sdlc-config.md", "file")


def test_block_kind_tools_carry_it_in_their_one_block():
    desc, body = SI.config_skill(ROOT)
    for tool in ("copilot", "gemini", "aider", "codex"):
        assert SI.config_tool_files(tool, body, desc) == [], tool


def test_every_flag_the_skill_cites_is_one_the_engine_accepts():
    _desc, body = SI.config_skill(ROOT)
    cited = set(re.findall(r"(?<![\w-])(--[a-z][a-z-]+)", body)) - {"--project"}
    assert {"--spec", "--dry-run", "--check", "--target"} <= cited, cited   # `--project` is uv's
    helped = _run([sys.executable, str(CONFIG), "--help"])
    assert helped.returncode == 0, _out(helped)
    missing = sorted(f for f in cited if f not in helped.stdout)
    assert missing == [], f"the skill cites flags config.py does not take: {missing}"


def test_a_real_install_writes_every_config_surface_in_one_block_each(tmp_path):
    root = _target(tmp_path)
    done = _install(root, "--tools", "claude,cursor,windsurf,gemini,codex")
    assert done.returncode == 0, _out(done)
    for rel in (".claude/skills/sdlc-config/SKILL.md", ".cursor/rules/sdlc-config.mdc",
                ".windsurf/rules/sdlc-config.md"):
        assert HEADING in (root / rel).read_text(encoding="utf-8"), rel
    for rel in ("GEMINI.md", "AGENTS.md"):
        text = (root / rel).read_text(encoding="utf-8")
        assert text.count(SI.BLOCK_BEGIN) == 1 and text.count(SI.BLOCK_END) == 1, rel
        assert "# SDLC Harness" in text and HEADING in text, rel
        assert text.index("# SDLC Harness") < text.index(HEADING), rel
    again = _install(root, "--tools", "claude,cursor,windsurf,gemini,codex")
    assert again.returncode == 0, _out(again)
    assert "sdlc-config" not in "".join(
        line for line in again.stdout.splitlines() if "~ update" in line), again.stdout


def test_the_command_the_skill_teaches_runs_where_it_was_installed(tmp_path):
    """Taken out of the INSTALLED skill's own fence and run from the repository
    the installer wrote - the command a runner would paste."""
    root = _target(tmp_path)
    assert _install(root, "--tools", "claude").returncode == 0
    skill = (root / ".claude" / "skills" / "sdlc-config" / "SKILL.md").read_text(
        encoding="utf-8")
    taught = [c for c in _fenced(skill)
              if c.startswith("uv run --project") and c.endswith("--spec")]
    assert len(taught) == 1, taught
    ran = subprocess.run(shlex.split(taught[0]), cwd=root, capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=600)
    assert ran.returncode == 0, taught[0] + "\n" + _out(ran)
    assert json.loads(ran.stdout)["installed"] is True


def test_a_lone_bundle_installs_no_config_skill(tmp_path):
    lone = tmp_path / "standalone"
    lone.mkdir()
    bundle = lone / "sdlc-init.py"
    made = _run([sys.executable, str(INSTALLER), "--bundle", str(bundle)])
    assert made.returncode == 0, _out(made)
    root = _target(tmp_path)
    done = _run([sys.executable, str(bundle), "--target", str(root), "--tools",
                 "claude,gemini"])
    assert done.returncode == 0, _out(done)
    assert not (root / ".claude" / "skills" / "sdlc-config").exists()
    assert HEADING not in (root / "GEMINI.md").read_text(encoding="utf-8")


# ── INIT's stop-and-fill names the wizard ────────────────────────────────────


def test_init_stopping_for_the_project_file_names_both_ways_to_answer(tmp_path):
    """The two commands are taken out of INIT's own message and run: the terminal
    one refuses cleanly with no TTY, and the scriptable one fills the file so the
    next INIT installs."""
    root = _target(tmp_path, seeded=False)
    stopped = _install(root)
    assert stopped.returncode == 2, _out(stopped)
    taught = [line.strip() for line in stopped.stdout.splitlines()
              if "tools/sdlc/config.py" in line]
    interactive = [c for c in taught if c.endswith("--interactive")]
    scripted = [c for c in taught if " set project." in c]
    assert len(interactive) == 1 and len(scripted) == 1, stopped.stdout

    refused = subprocess.run(shlex.split(interactive[0]), stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=600)
    assert refused.returncode == 2 and "/sdlc-config" in refused.stdout, _out(refused)

    head = scripted[0].split(" set ", 1)[0]
    filled = subprocess.run(shlex.split(head) + [
        "set", "project.description=A thing we ship", "project.owner=team-a",
        "project.criticality=low"], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=600)
    assert filled.returncode == 0, _out(filled)
    installed = _install(root)
    assert installed.returncode == 0, _out(installed)
    assert P.profile_project(root)["owner"] == "team-a"


# ── sdlc config --interactive ────────────────────────────────────────────────


class Script:
    """A person at a terminal, answering by question id; Enter where no answer
    is left. `EOFError` once `end` is reached, the way `input()` ends."""

    def __init__(self, **answers):
        self.answers = {k: list(v) if isinstance(v, list) else [v]
                        for k, v in answers.items()}
        self.asked: list[str] = []
        self.said: list[str] = []

    def ask(self, prompt: str) -> str:
        key = ("confirm" if prompt.startswith("Write these changes")
               else "again" if prompt.startswith("Answer again")
               else prompt.split(" [", 1)[0])
        self.asked.append(key)
        queue = self.answers.get(key)
        if queue:
            answer = queue.pop(0)
            if answer is EOFError:
                raise EOFError
            return answer
        if key in ("confirm", "again") and "end" in self.answers:
            raise EOFError
        return ""

    def say(self, text: str = "") -> None:
        self.said.append(str(text))

    @property
    def text(self) -> str:
        return "\n".join(self.said)


def test_the_loop_applies_one_change_after_the_preview_and_a_yes(repo):
    s = Script(gate_profile="team", confirm="y")
    assert C.interactive(repo, s.ask, s.say) == 0, s.text
    assert P.profile_dials(repo)["gate_profile"] == "team"
    assert (repo / S.DIAGRAM_FILE).read_text(encoding="utf-8") == S.diagram_for(repo)
    assert "G3 advisory" in s.text and "G3 hard" in s.text, "the preview shows now and after"


def test_declining_writes_nothing(repo):
    snap = _bytes(repo)
    s = Script(gate_profile="team", confirm="n")
    assert C.interactive(repo, s.ask, s.say) == 0, s.text
    assert _bytes(repo) == snap and "Nothing was written" in s.text


def test_a_malformed_answer_is_asked_again(repo):
    s = Script(gate_profile=["strict", "team"], confirm="y")
    assert C.interactive(repo, s.ask, s.say) == 0, s.text
    assert s.asked.count("gate_profile") == 2
    assert "solo, team, regulated" in s.text
    assert P.profile_dials(repo)["gate_profile"] == "team"


def test_a_refused_combination_offers_another_pass_keeping_the_answers(repo):
    """`regulated` alone is refused; the second pass keeps it (Enter) and adds
    what the regime needs, and the change applies as one."""
    s = Script(gate_profile=["regulated", ""], signing=["", "dsse"],
               **{"review.block_on": ["", "critical,high,medium"]},
               again="y", confirm="y")
    assert C.interactive(repo, s.ask, s.say) == 0, s.text
    assert "dsse" in s.text and "block_on drops medium" in s.text
    dials = P.profile_dials(repo)
    assert (dials["gate_profile"], dials["signing"]) == ("regulated", "dsse")
    assert dials["block_on"] == ["critical", "high", "medium"]


def test_a_refusal_not_retried_writes_nothing_and_says_so(repo):
    snap = _bytes(repo)
    s = Script(gate_profile="regulated", again="n")
    assert C.interactive(repo, s.ask, s.say) == 1, s.text
    assert _bytes(repo) == snap


def test_keyed_answers_take_several_pairs(repo):
    s = Script(**{"gates.<gate>": "G3=hard, G1=hard"}, confirm="y")
    assert C.interactive(repo, s.ask, s.say) == 0, s.text
    assert P.gate_overrides(repo) == {"G1": "hard", "G3": "hard"}


def test_models_are_asked_only_with_routing_on(repo):
    off = Script(end=True)
    C.interactive(repo, off.ask, off.say)
    assert not [k for k in off.asked if k.startswith("models.")], off.asked
    on = Script(model_routing="true", end=True)
    C.interactive(repo, on.ask, on.say)
    assert "models.default" in on.asked and "models.<step>" in on.asked, on.asked


def test_before_init_only_the_project_is_asked(tmp_path):
    root = _target(tmp_path, seeded=False)
    s = Script(**{"project.description": "A thing", "project.owner": "team-a",
                  "project.criticality": "medium"}, confirm="y")
    assert C.interactive(root, s.ask, s.say) == 0, s.text
    assert {k for k in s.asked if k != "confirm"} <= {
        q["id"] for q in C.questions() if q["id"].startswith("project.")}
    assert SI.read_project(root / PROJECT)["owner"] == "team-a"
    assert not (root / ".sdlc").exists()


def test_input_ending_writes_nothing(repo):
    snap = _bytes(repo)
    s = Script(gate_profile="team", confirm=[EOFError])
    assert C.interactive(repo, s.ask, s.say) == 2, s.text
    assert _bytes(repo) == snap and "nothing was written" in s.text.lower()


def test_without_a_terminal_it_refuses_by_name_instead_of_hanging(repo):
    snap = _bytes(repo)
    done = subprocess.run([sys.executable, str(CONFIG), "--target", str(repo),
                           "--interactive"], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)
    assert done.returncode == 2, _out(done)
    assert "/sdlc-config" in done.stdout and "set" in done.stdout, done.stdout
    assert _bytes(repo) == snap


def test_the_null_device_is_not_a_terminal():
    """Measured on Windows: `isatty()` is True for NUL, a character device that
    answers every read with EOF - so the question loop started there instead of
    refusing. The terminal test must say no to it on every platform."""
    with open(os.devnull, encoding="utf-8") as null:
        assert not C.is_terminal(null)
    read, write = os.pipe()
    try:
        with os.fdopen(read, encoding="utf-8") as pipe:
            assert not C.is_terminal(pipe)
    finally:
        os.close(write)


def test_interactive_is_its_own_action(repo):
    for extra in (["--spec"], ["--json"], ["--dry-run"], ["set", "gate_profile=team"]):
        done = subprocess.run([sys.executable, str(CONFIG), "--target", str(repo),
                               "--interactive", *extra], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=120)
        assert done.returncode == 2, (extra, _out(done))


@pytest.mark.skipif(os.name == "nt", reason="a pseudo-terminal is POSIX-only")
def test_at_a_real_terminal_the_loop_runs_end_to_end(repo):
    """The TTY path as a process: a pseudo-terminal on stdin, every question
    answered with Enter except `gate_profile`, then a yes."""
    import pty

    asked = [q["id"] for q in C.questions()
             if not q["id"].startswith("models.")]
    lines = ["team" if qid == "gate_profile" else "" for qid in asked] + ["y"]
    master, slave = pty.openpty()
    proc = subprocess.Popen([sys.executable, str(CONFIG), "--target", str(repo),
                             "--interactive"], stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    os.write(master, ("\n".join(lines) + "\n").encode("utf-8"))
    seen = b""
    while True:
        try:
            chunk = os.read(master, 4096)
        except OSError:
            break
        if not chunk:
            break
        seen += chunk
    assert proc.wait(timeout=120) == 0, seen.decode("utf-8", "replace")
    os.close(master)
    assert P.profile_dials(repo)["gate_profile"] == "team", seen.decode("utf-8", "replace")
