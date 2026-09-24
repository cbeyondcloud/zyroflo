"""T371a: `sdlc config`, the configuration wizard's ENGINE.

Non-interactive by design - an agent's tool call has no TTY - so every claim
about the command is made about the command as a PROCESS: spawned, its exit
code and its output read. The one in-process seam is the one it stands on,
`policy.proposed_profile`, which lets the harness's own readers answer about a
profile before a byte of it is written.

The property the row rests on: `sdlc config` never writes a configuration a run
would refuse, and a refused change writes nothing at all.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import models as M  # noqa: E402
from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import schema as SC  # noqa: E402
from tools.sdlc import stage as S  # noqa: E402
from tools.tests import _project  # noqa: E402

CONFIG = ROOT / "tools" / "sdlc" / "config.py"
INSTALLER = ROOT / "sdlc-init.py"
PROFILE = Path(".sdlc") / P.PROFILE_NAME
PROJECT = Path("sdlc-project.toml")
DIAGRAM = S.DIAGRAM_FILE
SHIPPED = ["intake", "requirements", "design", "implementation", "test", "review",
           "triage", "closeout"]
NO_TRIAGE = [p for p in SHIPPED if p != "triage"]


def _config(target: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CONFIG), "--target", str(target), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)


def _out(done: subprocess.CompletedProcess) -> str:
    return done.stdout + done.stderr


def _install(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(INSTALLER), "--target", str(root),
                           "--no-graph"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)


def _bare(tmp_path: Path) -> Path:
    """A repository INIT has not run in: git, and nothing else."""
    root = tmp_path / "bare"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], capture_output=True, timeout=120)
    return root


@pytest.fixture(scope="module")
def installed(tmp_path_factory) -> Path:
    """ONE real install - the installer as a process - copied for each test."""
    root = tmp_path_factory.mktemp("installed") / "repo"
    root.mkdir()
    _project.seed(root)
    subprocess.run(["git", "init", "-q", str(root)], capture_output=True, timeout=120)
    done = _install(root)
    assert done.returncode == 0, _out(done)
    return root


@pytest.fixture
def repo(installed: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "repo"
    shutil.copytree(installed, dst)
    return dst


def _bytes(root: Path) -> dict[str, bytes | None]:
    """Every file `sdlc config` may write, as bytes; None where absent."""
    return {rel.as_posix(): ((root / rel).read_bytes() if (root / rel).is_file() else None)
            for rel in (PROFILE, DIAGRAM, PROJECT)}


def _lines(root: Path) -> list[str]:
    return (root / PROFILE).read_text(encoding="utf-8").splitlines()


# ── the seam: a profile the readers can be ASKED about before it exists ──────


def _plain(root: Path, phases: list[str]) -> Path:
    (root / ".sdlc").mkdir(parents=True)
    (root / PROFILE).write_text(f"gate_profile: solo\nphases: [{', '.join(phases)}]\n",
                                encoding="utf-8")
    return root


def test_a_proposed_profile_is_what_every_reader_sees_and_the_file_is_untouched(tmp_path):
    root = _plain(tmp_path / "r", SHIPPED)
    before = (root / PROFILE).read_bytes()
    with P.proposed_profile(root, f"gate_profile: team\nphases: [{', '.join(NO_TRIAGE)}]\n"):
        assert P.profile_top_value(root, "gate_profile") == "team"
        assert "triage" not in [s.name for s in S.pipeline(root).steps]
    assert P.profile_top_value(root, "gate_profile") == "solo"
    assert "triage" in [s.name for s in S.pipeline(root).steps]
    assert (root / PROFILE).read_bytes() == before


def test_a_proposed_profile_meets_the_refusals_the_file_would(tmp_path):
    """The reader's refusals are the point of asking it: a candidate with a key
    set twice is refused exactly as the same text on disk would be."""
    root = _plain(tmp_path / "r", SHIPPED)
    with P.proposed_profile(root, "gate_profile: solo\ngate_profile: team\n"):
        with pytest.raises(P.ProfileError):
            P.profile_top_value(root, "gate_profile")


def test_the_proposal_ends_even_when_its_block_raises(tmp_path):
    root = _plain(tmp_path / "r", SHIPPED)
    with pytest.raises(RuntimeError):
        with P.proposed_profile(root, "gate_profile: team\n"):
            raise RuntimeError("the caller failed")
    assert P.profile_top_value(root, "gate_profile") == "solo"


def test_a_proposal_answers_for_its_own_target_only(tmp_path):
    a, b = _plain(tmp_path / "a", SHIPPED), _plain(tmp_path / "b", SHIPPED)
    with P.proposed_profile(a, "gate_profile: team\n"):
        assert P.profile_top_value(a, "gate_profile") == "team"
        assert P.profile_top_value(b, "gate_profile") == "solo"


# ── --spec: the questions, derived ────────────────────────────────────────────


def _spec(target: Path) -> dict:
    done = _config(target, "--spec")
    assert done.returncode == 0, _out(done)
    return json.loads(done.stdout)


def _q(spec: dict, qid: str) -> dict:
    return next(q for q in spec["questions"] if q["id"] == qid)


def test_the_spec_is_derived_from_the_committed_schemas(repo):
    spec = _spec(repo)
    profile = json.loads(SC.path_of("profile").read_text(encoding="utf-8"))["properties"]
    project = json.loads(SC.path_of("workspace").read_text(
        encoding="utf-8"))["properties"]["project"]["properties"]
    assert _q(spec, "phases")["choices"] == profile["phases"]["items"]["enum"]
    assert _q(spec, "extra_gates")["choices"] == profile["extra_gates"]["items"]["enum"]
    assert _q(spec, "gate_profile")["choices"] == profile["gate_profile"]["enum"]
    assert _q(spec, "signing")["choices"] == profile["signing"]["enum"]
    gates = _q(spec, "gates.<gate>")
    assert gates["keys"] == sorted(profile["gates"]["properties"])
    assert gates["choices"] == profile["gates"]["properties"]["G1"]["enum"]
    assert _q(spec, "models.<step>")["keys"] == sorted(
        k for k in profile["models"]["properties"] if k != "default")
    assert (_q(spec, "models.default")["pattern"]
            == profile["models"]["properties"]["default"]["pattern"])
    assert _q(spec, "model_routing")["type"] == "boolean"
    assert (_q(spec, "review.block_on")["choices"]
            == profile["review"]["properties"]["block_on"]["items"]["enum"])
    assert _q(spec, "gate_profile")["presets"]["regulated"]["block_on"] == list(
        P.PRESETS["regulated"]["block_on"])
    assert _q(spec, "project.criticality")["choices"] == [
        c for c in project["criticality"]["enum"] if c]
    assert _q(spec, "project.compliance")["type"] == "list"
    assert _q(spec, "phases")["locked"] == list(S.LOCKED_PHASES)
    assert _q(spec, "extra_gates")["subset_of"] == "phases"


def test_the_spec_says_which_project_fields_init_requires(repo):
    si = SC._installer()
    project = [q for q in _spec(repo)["questions"] if q["id"].startswith("project.")]
    assert [q["id"] for q in project] == [
        f"project.{k}" for k in (*si.PROJECT_REQUIRED, *si.PROJECT_OPTIONAL)]
    assert {q["id"] for q in project if q["required"]} == {
        f"project.{k}" for k in si.PROJECT_REQUIRED}


def test_the_spec_carries_the_current_values(repo):
    spec = _spec(repo)
    assert spec["installed"] is True
    assert _q(spec, "phases")["current"] == S.declared(repo)[0]
    assert _q(spec, "gate_profile")["current"] == "solo"
    assert _q(spec, "project.owner")["current"] == "test-suite"
    assert _q(spec, "model_routing")["current"] is False
    assert _q(spec, "gates.<gate>")["current"] == {}
    assert _q(spec, "models.default")["current"] == M.models_declared(repo)["default"]


def test_the_spec_before_init_says_the_pipeline_is_not_installed(tmp_path):
    spec = _spec(_bare(tmp_path))
    assert spec["installed"] is False
    assert _q(spec, "project.owner")["current"] is None
    assert _q(spec, "phases")["current"] is None


# ── set / apply: one change, validated as a run would, written whole ─────────


def test_set_rewrites_the_one_declaration_keeps_every_comment_and_redraws(repo):
    before = _lines(repo)
    done = _config(repo, "set", "phases=" + ",".join(NO_TRIAGE))
    assert done.returncode == 0, _out(done)
    after = _lines(repo)
    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert len(before) == len(after) and len(changed) == 1, changed
    assert changed[0][1] == f"phases: [{', '.join(NO_TRIAGE)}]"
    assert S.declared(repo)[0] == NO_TRIAGE
    assert (repo / DIAGRAM).read_text(encoding="utf-8") == S.diagram_for(repo)
    assert "moved: G4 from P7 triage to P6 review" in done.stdout, done.stdout


def test_a_scalar_keeps_its_comment_in_its_column(repo):
    before = next(l for l in _lines(repo) if l.startswith("gate_profile:"))
    done = _config(repo, "set", "gate_profile=team")
    assert done.returncode == 0, _out(done)
    after = next(l for l in _lines(repo) if l.startswith("gate_profile:"))
    assert after.startswith("gate_profile: team ")
    assert after.index("#") == before.index("#")
    assert after[after.index("#"):] == before[before.index("#"):]


def test_a_change_a_run_would_refuse_writes_nothing(repo):
    snap = _bytes(repo)
    done = _config(repo, "set", "phases=intake,design")
    assert done.returncode == 1, _out(done)
    assert "omits implementation, review" in done.stdout, done.stdout
    assert _bytes(repo) == snap


def test_an_extra_gate_on_a_step_that_does_not_run_is_refused(repo):
    snap = _bytes(repo)
    done = _config(repo, "set", "extra_gates=release")
    assert done.returncode == 1, _out(done)
    assert "a step this profile does not run" in done.stdout, done.stdout
    assert _bytes(repo) == snap


def test_an_extra_gate_lands_where_the_template_teaches_it_and_stands_hard(repo):
    done = _config(repo, "set", "extra_gates=test")
    assert done.returncode == 0, _out(done)
    lines = _lines(repo)
    at = lines.index("extra_gates: [test]")
    assert lines[at - 1].startswith("# extra_gates:"), lines[at - 2:at + 1]
    assert "G-test" in S.resolved_hard_gates(repo)
    assert (repo / DIAGRAM).read_text(encoding="utf-8") == S.diagram_for(repo)


def test_a_gate_may_be_hardened_never_softened_and_an_override_clears(repo):
    assert "G3" not in S.resolved_hard_gates(repo)
    done = _config(repo, "set", "gates.G3=hard")
    assert done.returncode == 0, _out(done)
    assert P.gate_overrides(repo) == {"G3": "hard"}
    assert "G3" in S.resolved_hard_gates(repo)
    soft = _config(repo, "set", "gates.G4=advisory")
    assert soft.returncode == 1 and "merge gate" in soft.stdout, _out(soft)
    cleared = _config(repo, "set", "gates.G3=")
    assert cleared.returncode == 0, _out(cleared)
    assert P.gate_overrides(repo) == {}
    assert "gates:" not in [line.strip() for line in _lines(repo)]


def test_regulated_needs_its_dials_and_one_change_can_carry_them_all(repo):
    """A regime is more than its gates: `regulated` refuses `signing: none` and a
    `block_on` without `medium`, so a wizard offering the choice must be able to
    apply it - in one change, since each half alone is refused."""
    alone = _config(repo, "set", "gate_profile=regulated")
    assert alone.returncode == 1, _out(alone)
    assert "dsse" in alone.stdout and "block_on drops medium" in alone.stdout, alone.stdout
    whole = _config(repo, "set", "gate_profile=regulated", "signing=dsse",
                    "review.block_on=critical,high,medium")
    assert whole.returncode == 0, _out(whole)
    dials = P.profile_dials(repo)
    assert (dials["gate_profile"], dials["signing"]) == ("regulated", "dsse")
    assert dials["block_on"] == ["critical", "high", "medium"]
    assert any(line.startswith("  block_on: [critical, high, medium]") and "#" in line
               for line in _lines(repo)), "the nested line keeps its indent and comment"


def test_apply_is_one_change_all_or_nothing(repo, tmp_path):
    snap = _bytes(repo)
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"gate_profile": "team", "extra_gates": ["release"]}),
                       encoding="utf-8")
    refused = _config(repo, "apply", "--from", str(answers))
    assert refused.returncode == 1, _out(refused)
    assert _bytes(repo) == snap
    answers.write_text(json.dumps({"gate_profile": "team", "extra_gates": ["test"],
                                   "project.owner": "platform"}), encoding="utf-8")
    done = _config(repo, "apply", "--from", str(answers))
    assert done.returncode == 0, _out(done)
    assert P.profile_dials(repo)["gate_profile"] == "team"
    assert "G-test" in S.resolved_hard_gates(repo)
    assert P.profile_project(repo)["owner"] == "platform"


def test_dry_run_shows_the_pipeline_now_and_as_it_will_be_and_writes_nothing(repo):
    snap = _bytes(repo)
    done = _config(repo, "--dry-run", "set", "gate_profile=team")
    assert done.returncode == 0, _out(done)
    assert "G3 advisory" in done.stdout and "G3 hard" in done.stdout, done.stdout
    assert _bytes(repo) == snap


def test_json_is_for_a_front_end(repo):
    done = _config(repo, "--json", "set", "gate_profile=team")
    assert done.returncode == 0, _out(done)
    body = json.loads(done.stdout)
    assert body["applied"] is True and body["problems"] == []
    assert {"key": "gate_profile", "from": "solo", "to": "team"} in body["changed"]
    assert {PROFILE.as_posix(), DIAGRAM.as_posix()} <= set(body["wrote"])
    assert any("G3 hard" in line for line in body["pipeline"]["after"])


def test_nothing_to_change_writes_nothing_and_says_so(repo):
    snap = _bytes(repo)
    done = _config(repo, "set", "gate_profile=solo")
    assert done.returncode == 0, _out(done)
    assert "already" in done.stdout, done.stdout
    assert _bytes(repo) == snap


def test_an_unknown_key_is_refused_with_a_hint(repo):
    snap = _bytes(repo)
    done = _config(repo, "set", "phase=intake")
    assert done.returncode == 2 and "did you mean 'phases'" in done.stdout, _out(done)
    assert _bytes(repo) == snap


def test_a_value_off_its_schema_is_refused_by_name(repo):
    done = _config(repo, "set", "gate_profile=strict")
    assert done.returncode == 2 and "solo, team, regulated" in done.stdout, _out(done)
    done = _config(repo, "set", "phases=intake,implementation,reveiw")
    assert done.returncode == 2 and "'reveiw'" in done.stdout, _out(done)
    done = _config(repo, "set", "gate_profile")
    assert done.returncode == 2 and "key=value" in done.stdout, _out(done)


def test_no_action_is_a_usage_error(repo):
    assert _config(repo).returncode == 2


# ── project metadata: sdlc-project.toml, and the profile once installed ──────


def test_before_init_a_project_field_writes_the_template_and_names_the_rest(tmp_path):
    root = _bare(tmp_path)
    done = _config(root, "set", "project.owner=team-a")
    assert done.returncode == 0, _out(done)
    project = SC._installer().read_project(root / PROJECT)
    assert project["owner"] == "team-a" and project["name"] == root.name
    assert "description, criticality" in done.stdout, done.stdout
    assert not (root / ".sdlc").exists()
    assert "# Who answers for it" in (root / PROJECT).read_text(encoding="utf-8")


def test_before_init_a_pipeline_key_is_refused_and_nothing_is_written(tmp_path):
    root = _bare(tmp_path)
    done = _config(root, "set", "gate_profile=team")
    assert done.returncode == 2 and "INIT" in done.stdout, _out(done)
    assert sorted(p.name for p in root.iterdir()) == [".git"]


def test_what_the_wizard_fills_before_init_is_what_init_accepts(tmp_path):
    root = _bare(tmp_path)
    done = _config(root, "set", "project.description=A thing we ship",
                   "project.owner=team-a", "project.criticality=medium")
    assert done.returncode == 0, _out(done)
    installed = _install(root)
    assert installed.returncode == 0, _out(installed)
    assert P.profile_project(root)["owner"] == "team-a"
    assert _config(root, "--check").returncode == 0


def test_after_init_a_project_field_is_written_to_both_homes(repo):
    done = _config(repo, "set", "project.owner=platform", "project.compliance=sox,pci")
    assert done.returncode == 0, _out(done)
    on_file = SC._installer().read_project(repo / PROJECT)
    assert on_file["owner"] == "platform" and on_file["compliance"] == ["sox", "pci"]
    assert P.profile_project(repo)["owner"] == "platform"
    assert P.profile_project(repo)["compliance"] == ["sox", "pci"]


def test_setting_the_files_value_again_repairs_a_profile_block_out_of_step(repo):
    """`--check` names two homes that disagree and says `set` writes both - so
    `set` must not skip a value merely because `sdlc-project.toml` holds it."""
    prof = repo / PROFILE
    prof.write_text(prof.read_text(encoding="utf-8").replace(
        'owner: "test-suite"', 'owner: "somebody-else"'), encoding="utf-8")
    stale = _config(repo, "--check")
    assert stale.returncode == 1 and "disagree about owner" in stale.stdout, _out(stale)
    done = _config(repo, "set", "project.owner=test-suite")
    assert done.returncode == 0 and "already" not in done.stdout, _out(done)
    assert P.profile_project(repo)["owner"] == "test-suite"
    assert _config(repo, "--check").returncode == 0


def test_a_project_block_from_a_workspace_table_is_not_overwritten(repo):
    """No `sdlc-project.toml`, yet a `project:` block: the metadata came from a
    workspace's own `[project]` table, which wins at INIT. Seeding a fresh file
    from the template would wipe that block - so it is refused by name."""
    (repo / PROJECT).unlink()
    snap = _bytes(repo)
    done = _config(repo, "set", "project.owner=platform")
    assert done.returncode == 2 and "workspace" in done.stdout, _out(done)
    assert _bytes(repo) == snap


def test_a_list_is_written_in_the_lifecycles_order(repo):
    done = _config(repo, "set", "extra_gates=review,test")
    assert done.returncode == 0, _out(done)
    assert "extra_gates: [test, review]" in _lines(repo)


def test_a_required_project_field_cannot_be_set_blank(repo):
    snap = _bytes(repo)
    done = _config(repo, "set", "project.owner=")
    assert done.returncode == 1 and "owner" in done.stdout, _out(done)
    assert _bytes(repo) == snap


def test_criticality_off_its_enum_is_refused(repo):
    done = _config(repo, "set", "project.criticality=extreme")
    assert done.returncode == 2 and "low, medium, high" in done.stdout, _out(done)


def test_free_text_reads_back_as_written_through_both_readers(repo):
    """Quotes, a ` #` and a backslash: the characters both formats escape."""
    text = 'says "hi" # not a comment \\ back'
    done = _config(repo, "set", f"project.description={text}")
    assert done.returncode == 0, _out(done)
    assert SC._installer().read_project(repo / PROJECT)["description"] == text
    assert P.profile_project(repo)["description"] == text


def test_a_value_the_readers_would_misread_is_refused_not_written(repo, tmp_path):
    """`profile_project` splits compliance on commas, so a tag holding one would
    be read back as two. The read-back refuses it rather than writing a file
    that tells a second story."""
    snap = _bytes(repo)
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"project.compliance": ["sox,pci-dss"]}), encoding="utf-8")
    done = _config(repo, "apply", "--from", str(answers))
    assert done.returncode == 1 and "read back" in done.stdout, _out(done)
    assert _bytes(repo) == snap


def test_a_line_break_in_a_value_is_refused(repo, tmp_path):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"project.description": "one\ntwo"}), encoding="utf-8")
    done = _config(repo, "apply", "--from", str(answers))
    assert done.returncode == 2, _out(done)


# ── models ────────────────────────────────────────────────────────────────────


def test_an_acting_step_cannot_be_given_a_model_endpoint(repo):
    snap = _bytes(repo)
    done = _config(repo, "set", "model_routing=true",
                   "models.implementation=openai/gpt-4o-mini")
    assert done.returncode == 1 and "must ACT" in done.stdout, _out(done)
    assert _bytes(repo) == snap


def test_a_provider_the_catalog_does_not_declare_is_refused_when_routing(repo):
    done = _config(repo, "set", "model_routing=true", "models.design=nosuch/model-1")
    assert done.returncode == 1 and "does not declare" in done.stdout, _out(done)


def test_a_chat_steps_model_joins_the_models_block(repo):
    default = M.models_declared(repo)["default"]
    done = _config(repo, "set", "models.design=openai/gpt-4o")
    assert done.returncode == 0, _out(done)
    assert M.models_declared(repo) == {"default": default, "design": "openai/gpt-4o"}


# ── line endings, and --check ─────────────────────────────────────────────────


def test_line_endings_are_kept(repo):
    prof = repo / PROFILE
    prof.write_bytes(prof.read_bytes().replace(b"\n", b"\r\n"))
    done = _config(repo, "set", "gate_profile=team")
    assert done.returncode == 0, _out(done)
    raw = prof.read_bytes()
    assert b"gate_profile: team" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n")


def test_check_is_clean_on_a_fresh_install(repo):
    done = _config(repo, "--check")
    assert done.returncode == 0, _out(done)


def test_check_names_a_stale_pipeline_diagram(repo):
    (repo / DIAGRAM).write_text("# Pipeline\n\nedited by hand\n", encoding="utf-8")
    done = _config(repo, "--check")
    assert done.returncode == 1 and "stale" in done.stdout, _out(done)


def test_check_names_what_a_run_would_refuse(repo):
    prof = repo / PROFILE
    prof.write_text(prof.read_text(encoding="utf-8").replace("review, triage", "triage"),
                    encoding="utf-8")
    done = _config(repo, "--check")
    assert done.returncode == 1 and "omits review" in done.stdout, _out(done)


def test_check_names_a_blank_required_project_field(repo):
    toml = repo / PROJECT
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        'owner = "test-suite"', 'owner = ""'), encoding="utf-8")
    done = _config(repo, "--check")
    assert done.returncode == 1 and "owner" in done.stdout, _out(done)


def test_check_on_a_target_that_is_not_there_could_not_look(tmp_path):
    done = _config(tmp_path / "nowhere", "--check")
    assert done.returncode == 2 and "does not exist" in done.stdout, _out(done)


# ── T472: a value that could not be read is not "already so" ─────────────────


def test_clearing_a_value_the_reader_refuses_repairs_it(repo):
    """`set models.requirements=` compared against the None an unreadable `models:`
    left and answered "nothing to change" at exit 0, with the bad value still in
    the file. Could-not-look is not clean: the clear goes through, and here - the
    edit being the repair - it is applied and the profile reads again."""
    prof = repo / PROFILE
    text = prof.read_text(encoding="utf-8")
    text = text.replace("model_routing: false", "model_routing: true", 1)
    text = text.replace("models:\n  default: openai/gpt-4o-mini",
                        "models:\n  default: openai/gpt-4o-mini\n  requirements: gpt4o", 1)
    prof.write_text(text, encoding="utf-8")
    assert _config(repo, "--check").returncode == 1, "the fixture: models is unreadable"
    done = _config(repo, "set", "models.requirements=")
    assert done.returncode == 0 and "nothing to change" not in done.stdout, _out(done)
    assert "(could not be read)" in done.stdout, _out(done)
    assert "requirements: gpt4o" not in prof.read_text(encoding="utf-8")
    assert _config(repo, "--check").returncode == 0


def test_clearing_under_an_unreadable_profile_is_refused_not_already_so(repo):
    """The same could-not-look, where the edit does NOT repair what is wrong: the
    change is validated like any other and refused, and nothing is written."""
    prof = repo / PROFILE
    prof.write_text(prof.read_text(encoding="utf-8").rstrip("\n")
                    + "\ngates:\n  G3: hard\nsigning: none\n", encoding="utf-8")
    before = _bytes(repo)
    done = _config(repo, "set", "gates.G3=")
    assert done.returncode == 1 and "nothing to change" not in done.stdout, _out(done)
    assert "set twice" in done.stdout, _out(done)
    assert _bytes(repo) == before, "a refused change wrote something"


# ── T476: a header that already holds a value takes no entry beneath it ──────


def test_an_empty_flow_header_is_opened_before_an_entry_goes_under_it(repo):
    """`gates: []` then `set gates.G3=hard` wrote `  G3: hard` beneath the flow
    value - the harness's reader took it, PyYAML could not parse the profile, and
    `--check` said OK with a note. The empty value is the same emptiness spelled
    as a bare header, so the header is opened and the entry goes under it."""
    import yaml

    prof = repo / PROFILE
    prof.write_text(prof.read_text(encoding="utf-8").rstrip("\n") + "\ngates: []\n",
                    encoding="utf-8")
    done = _config(repo, "set", "gates.G3=hard")
    assert done.returncode == 0, _out(done)
    assert yaml.safe_load(prof.read_text(encoding="utf-8"))["gates"] == {"G3": "hard"}
    check = _config(repo, "--check")
    assert check.returncode == 0 and "not checked" not in check.stdout, _out(check)


def test_a_header_holding_an_inline_map_is_refused_by_line(repo):
    """A filled inline map cannot take an entry beneath it, and rewriting it is a
    guess about its contents - so it is refused, naming the line, and nothing is
    written."""
    prof = repo / PROFILE
    prof.write_text(prof.read_text(encoding="utf-8").rstrip("\n") + "\ngates: {G2: hard}\n",
                    encoding="utf-8")
    before = _bytes(repo)
    done = _config(repo, "set", "gates.G3=hard")
    assert done.returncode == 2 and "inline" in done.stdout, _out(done)
    assert _bytes(repo) == before, "a refused change wrote something"
