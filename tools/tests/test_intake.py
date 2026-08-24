"""Item 31 — P0 intake: any shape in, one normalized record out, or a halt
that lists every reason at once.

The adapter's contract is mostly about refusing well: a spec with no body, a
tree nobody can reconstruct, a directory holding six candidate files. Each of
those has a test here, because "halts with reasons" is the phase spec's own
wording and a halt with one reason sends the operator round the loop twice.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import intake as I  # noqa: E402
from tools.sdlc import state as L  # noqa: E402

SPEC = ("# Add a retry to the outbound client\n\n"
        "Ticket: ABC-1\nPriority: high\n\n"
        "The client gives up on the first timeout. It should retry with backoff "
        "and surface the final failure to the caller.\n")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A committed git repo with a profile, which is what intake reads."""
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / "code.py").write_text("x = 1\n", encoding="utf-8")
    (r / ".sdlc" / "profile.yml").write_text(
        'stack: python\ntest_command: "python -c \\"print(1)\\""   # full suite\n',
        encoding="utf-8")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init")
    return r


# ── normalizing whatever arrived ─────────────────────────────────────────────


def test_a_heading_titles_the_run_and_fields_are_lifted():
    norm = I.normalize(SPEC)
    assert norm["title"] == "Add a retry to the outbound client"
    assert norm["fields"] == {"ticket": "ABC-1", "priority": "high"}
    assert norm["body"].startswith("The client gives up")


def test_a_title_field_or_the_first_line_will_do():
    assert I.normalize("Title: from a field\n\nbody here")["title"] == "from a field"
    assert I.normalize("just a sentence\n\nbody")["title"] == "just a sentence"


def test_nothing_is_dropped_on_the_way_through():
    """An unrecognised line is body, never a parse error - the input is
    somebody else's format and the record has to survive it."""
    raw = "# T\n\n- a bullet\n\n    indented block\n\n| a | table |\n"
    body = I.normalize(raw)["body"]
    for fragment in ("- a bullet", "indented block", "| a | table |"):
        assert fragment in body


def test_the_specification_hash_ignores_whitespace_and_case():
    a = I.spec_hash("Title", "one   two\nthree")
    assert a == I.spec_hash("title", " one two three ")
    assert a != I.spec_hash("Title", "one two four")


# ── where the spec comes from ────────────────────────────────────────────────


def test_explicit_beats_configured(tmp_path, monkeypatch):
    f = tmp_path / "s.md"
    f.write_text("from the file", encoding="utf-8")
    env = {I.INTAKE_ENV: str(tmp_path / "other.md")}
    assert I.resolve_source(None, "from --text", None, env)[0] == "from --text"
    assert I.resolve_source(str(f), None, None, env)[0] == "from the file"


def test_the_env_var_may_name_a_file_or_a_lone_file_in_a_directory(tmp_path):
    f = tmp_path / "one.md"
    f.write_text("body", encoding="utf-8")
    body, kind, name = I.resolve_source(None, None, None, {I.INTAKE_ENV: str(f)})
    assert (body, kind) == ("body", "env") and name == str(f)

    d = tmp_path / "dir"
    d.mkdir()
    (d / "only.md").write_text("the only one", encoding="utf-8")
    assert I.resolve_source(None, None, None, {I.INTAKE_ENV: str(d)})[0] == "the only one"


def test_a_directory_of_specs_asks_which_one(tmp_path):
    d = tmp_path / "many"
    d.mkdir()
    for name in ("a.md", "b.md", "c.txt"):
        (d / name).write_text("body", encoding="utf-8")
    with pytest.raises(RuntimeError, match="--pick"):
        I.resolve_source(None, None, None, {I.INTAKE_ENV: str(d)})
    assert I.resolve_source(None, None, "b.md", {I.INTAKE_ENV: str(d)})[0] == "body"
    with pytest.raises(RuntimeError, match="not in"):
        I.resolve_source(None, None, "z.md", {I.INTAKE_ENV: str(d)})


def test_with_nothing_configured_the_refusal_names_the_variable(tmp_path):
    with pytest.raises(RuntimeError, match=I.INTAKE_ENV):
        I.resolve_source(None, None, None, {})
    with pytest.raises(RuntimeError, match="no spec file"):
        I.resolve_source(str(tmp_path / "gone.md"), None, None, {})


# ── the profile's baseline command ───────────────────────────────────────────


def test_a_quoted_command_survives_its_inline_comment(repo):
    """Measured on the first real run: cutting the comment before the closing
    quote handed P5 a command with the quotes still on it."""
    assert I.profile_value(repo, "test_command") == 'python -c "print(1)"'


def test_an_unquoted_value_loses_its_comment_and_a_missing_key_is_none(tmp_path):
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "profile.yml").write_text(
        "stack: python   # detected\nnested:\n  key: value\n", encoding="utf-8")
    assert I.profile_value(tmp_path, "stack") == "python"
    assert I.profile_value(tmp_path, "key") is None, "top-level only, and it says so"
    assert I.profile_value(tmp_path, "absent") is None


def test_the_baseline_is_recorded_but_not_run_unless_asked(repo):
    quiet = I.baseline(repo, run=False)
    assert quiet["command"] and quiet["ran"] is False and "NOT run" in quiet["note"]
    ran = I.baseline(repo, run=True)
    assert ran["ran"] is True and ran["exit_code"] == 0 and "green" in ran["note"]


def test_a_missing_test_command_is_said_out_loud(tmp_path):
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "profile.yml").write_text("stack: python\n", encoding="utf-8")
    row = I.baseline(tmp_path, run=True)
    assert row["command"] == "" and "nothing to compare against" in row["note"]


# ── the working tree ─────────────────────────────────────────────────────────


def test_a_clean_tree_is_clean_and_an_edit_shows(repo):
    assert I.tree_state(repo)["clean"] is True
    (repo / "code.py").write_text("x = 2\n", encoding="utf-8")
    state = I.tree_state(repo)
    assert state["clean"] is False and state["dirty_files"] == 1
    assert "code.py" in state["note"]


def test_the_ledgers_own_folder_does_not_dirty_the_tree(repo):
    """Measured on the first real run of this tool: intake WRITES a run folder,
    so counting it made the first intake dirty the tree for the second one."""
    L.create(repo, "requirement", "an earlier run")
    assert I.tree_state(repo)["clean"] is True
    (repo / ".sdlc" / "profile.yml").write_text("stack: other\n", encoding="utf-8")
    assert I.tree_state(repo)["clean"] is False, \
        "a changed profile still shows - it can change what the run does"


def _workspace(root: Path, dirty: list[str]) -> Path:
    """A workspace root that is NOT a repository, holding ones that are."""
    (root / ".sdlc").mkdir(parents=True, exist_ok=True)
    lines = ["stack: multi", "components:"]
    for name in ("alpha", "beta"):
        where = root / name
        where.mkdir()
        subprocess.run(["git", "-C", str(where), "init", "-q", "-b", "main"],
                       check=True, timeout=120, capture_output=True)
        (where / "app.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(where), "add", "-A"], check=True,
                       timeout=120, capture_output=True)
        subprocess.run(["git", "-C", str(where), "-c", "user.email=d@x.invalid",
                        "-c", "user.name=D", "commit", "-qm", "base"], check=True,
                       timeout=120, capture_output=True)
        if name in dirty:
            (where / "app.py").write_text("x = 2\n", encoding="utf-8")
        lines += [f'  - name: "{name}"', f'    path: "{name}"']
    (root / ".sdlc" / "profile.yml").write_text("\n".join(lines) + "\n",
                                                encoding="utf-8")
    return root


def test_an_unreadable_tree_is_a_stated_unknown(tmp_path):
    state = I.tree_state(tmp_path)  # not a git repo
    assert state["clean"] is None and state["note"]
    assert state["workspace"] is False


def test_a_workspace_root_whose_component_cannot_be_read_says_which(tmp_path, capsys):
    """An estate root holds many repositories and is not itself one. Item 31
    refused outright here; item 41 made the answer computable as the UNION of
    the components', so reaching this refusal now means a COMPONENT could not
    be read - and saying only 'not a git repository' would send the operator
    looking for a broken git at the root."""
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "profile.yml").write_text(
        "stack: multi\ncomponents:\n  - name: one\n    path: one\n", encoding="utf-8")
    assert I.tree_state(tmp_path)["workspace"] is True
    assert I.main(["--target", str(tmp_path), "--text",
                   "# A change\n\nA body long enough to clear the length check."]) == 2
    out = capsys.readouterr().out
    assert "WORKSPACE root" in out and "one:" in out
    assert not (tmp_path / ".sdlc" / "runs").exists(), "a refusal writes nothing"


def test_a_workspace_of_clean_components_is_clean(tmp_path):
    """Item 37 puts a run at the workspace level by design, spanning
    components. The clean-tree question has to be answerable there, and the
    answer is the union."""
    _workspace(tmp_path, dirty=[])
    state = I.tree_state(tmp_path)
    assert state["clean"] is True and state["workspace"] is True
    assert state["components"] == 2


def test_one_dirty_component_makes_the_workspace_dirty_and_is_named(tmp_path):
    _workspace(tmp_path, dirty=["beta"])
    state = I.tree_state(tmp_path)
    assert state["clean"] is False and state["dirty_files"] == 1
    assert "beta" in state["note"] and "alpha" not in state["note"], \
        "the operator has to be told which component, not just that one of them is"


def test_a_workspace_run_opens_and_pins_p0(tmp_path):
    """The end of item 31's deferral: a run against a workspace root."""
    _workspace(tmp_path, dirty=[])
    rec = I.open_run(tmp_path, "# A workspace change\n\n"
                               "A body long enough to clear the length check.")
    assert rec["tree"]["workspace"] is True and rec["tree"]["clean"] is True
    assert set(L.load(tmp_path, rec["run_id"])["artifacts"]) == \
        {"00-intake.md", "00-intake.json"}


# ── refusing, with every reason ──────────────────────────────────────────────


def test_every_reason_is_listed_at_once():
    problems = I.validate({"title": "", "body": "tiny", "raw": "tiny", "fields": {}}, "epic",
                          {"clean": False, "dirty_files": 3, "note": "a; b"}, False)
    joined = " ".join(problems)
    assert len(problems) == 4
    for expected in ("no title", "characters", "unknown kind", "uncommitted"):
        assert expected in joined


def test_allow_dirty_removes_only_that_reason():
    tree = {"clean": False, "dirty_files": 1, "note": "x"}
    assert I.validate({"title": "T", "body": "b" * 80, "raw": "b" * 80,
                       "fields": {}},
                      "requirement", tree, True) == []


def test_main_refuses_without_opening_a_run(repo, capsys):
    assert I.main(["--target", str(repo), "--text", "# T\n\ntoo short"]) == 2
    out = capsys.readouterr().out
    assert "intake refused" in out and "no run was opened" in out
    assert L.list_runs(repo) == []


# ── the happy path ───────────────────────────────────────────────────────────


def test_main_writes_both_artifacts_and_records_their_hashes(repo, capsys):
    assert I.main(["--target", str(repo), "--text", SPEC]) == 0
    (row,) = L.list_runs(repo)
    d = L.run_dir(repo, row["run_id"])
    assert (d / "00-intake.md").is_file() and (d / "00-intake.json").is_file()

    st = L.load(repo, row["run_id"])
    assert set(st["artifacts"]) == {"00-intake.md", "00-intake.json"}
    assert L.verify_artifacts(repo, row["run_id"]) == []
    assert st["phase"] == "P0" and st["status"] == "running"

    rec = json.loads((d / "00-intake.json").read_text(encoding="utf-8"))
    assert rec["schema_version"] == I.SCHEMA_VERSION
    assert rec["fields"]["ticket"] == "ABC-1"
    assert rec["baseline"]["ran"] is False

    md = (d / "00-intake.md").read_text(encoding="utf-8")
    assert "Add a retry" in md and "ABC-1" in md
    assert "P2's question" in md, "the record says what it deliberately does not answer"


def test_the_same_spec_twice_is_reported_never_merged(repo, capsys):
    assert I.main(["--target", str(repo), "--text", SPEC]) == 0
    first = L.list_runs(repo)[0]["run_id"]
    capsys.readouterr()
    assert I.main(["--target", str(repo), "--text", SPEC]) == 0
    out = capsys.readouterr().out
    assert "possible duplicate" in out and first in out
    assert len(L.list_runs(repo)) == 2, "a duplicate still gets its own run"


def test_a_dirty_tree_can_be_accepted_and_the_override_is_recorded(repo, capsys):
    (repo / "code.py").write_text("x = 2\n", encoding="utf-8")
    assert I.main(["--target", str(repo), "--text", SPEC, "--allow-dirty"]) == 0
    row = L.list_runs(repo)[0]
    rec = json.loads((L.run_dir(repo, row["run_id"]) / "00-intake.json")
                     .read_text(encoding="utf-8"))
    assert rec["tree_override"] is True and rec["tree"]["clean"] is False
    assert "--allow-dirty" in capsys.readouterr().out


def test_the_baseline_flag_runs_the_suite_and_records_the_result(repo):
    assert I.main(["--target", str(repo), "--text", SPEC, "--baseline"]) == 0
    row = L.list_runs(repo)[0]
    rec = json.loads((L.run_dir(repo, row["run_id"]) / "00-intake.json")
                     .read_text(encoding="utf-8"))
    assert rec["baseline"]["ran"] is True and rec["baseline"]["exit_code"] == 0
    assert rec["baseline"]["seconds"] is not None
