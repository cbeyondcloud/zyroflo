"""T445c: the driver routes a step to a MODEL, acknowledged and recorded.

With `model_routing: true`, a step no runner is declared for goes to the
harness's endpoint runner under the provider/model `models:` names. `--spend`
acknowledges the MODEL (its name is `provider/model`), a missing key is refused
before anything spends, a declared runner receives the model through
`{provider}`/`{model}`, and the run's own history records which model ran.
No paid call: every spawn here is a fake runner or a refusal before one.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import drive as D  # noqa: E402
from tools.sdlc import intake as entry  # noqa: E402
from tools.sdlc import models as M  # noqa: E402
from tools.sdlc import stage as S  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

RUNNER_CMD = '"' + sys.executable + '" -c pass'
CATALOG = ('[acme]\nprotocol = "openai-chat"\nbase_url = "http://127.0.0.1:1"\n'
           'key_var = "ACME_TEST_KEY"\n')


def _profile(models_block: str, extra: str = "") -> str:
    return ("stack: python\ngate_profile: solo\n"
            "phases: [intake, requirements, design, implementation, test, review, "
            "triage, closeout]\n"
            "model_routing: true\n" + models_block +
            "runners:\n"
            "  - name: writer\n"
            f"    command: '{RUNNER_CMD}'\n"
            "    capabilities: chat\n" + extra +
            "phase_runners:\n"
            "  intake: writer\n")


def _repo(tmp_path: Path, profile: str) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(profile, encoding="utf-8")
    (r / ".sdlc" / "providers.toml").write_text(CATALOG, encoding="utf-8")
    (r / "code.py").write_text("x = 1\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "i"]):
        subprocess.run(["git", "-C", str(r), *args], check=True, capture_output=True)
    return r


def _run(repo: Path) -> str:
    return entry.open_run(repo, "title: a change\n\nkind: requirement\n\n"
                                "## What\n\nA small change.\n")["run_id"]


def answering(text: str = "the requirements, written by a model\n"):
    seen: list[dict] = []

    def run(command, prompt, cwd, timeout=3600):
        seen.append({"command": command})
        return 0, text

    run.seen = seen                                    # type: ignore[attr-defined]
    return run


@pytest.fixture(autouse=True)
def no_key(monkeypatch):
    monkeypatch.delenv("ACME_TEST_KEY", raising=False)


# ── a step routed to a model ─────────────────────────────────────────────────


def test_a_model_routed_step_spawns_the_endpoint_runner_under_that_model(
        tmp_path, monkeypatch):
    repo = _repo(tmp_path, _profile("models:\n  default: acme/m-small\n"))
    rid = _run(repo)
    monkeypatch.setenv("ACME_TEST_KEY", "x")
    first = D.step(repo, rid, {}, run=answering("intake notes\n"))
    assert first["advanced_to"] == "P1", first
    fake = answering()
    got = D.step(repo, rid, {}, run=fake)
    assert got["runner"] == "acme/m-small" and got["model"] == "acme/m-small", got
    (call,) = fake.seen
    assert "http_runner.py" in call["command"]
    assert "--provider acme --model m-small --catalog" in call["command"]
    assert "ACME_TEST_KEY" not in call["command"], "never a key on a command line"


def test_entering_a_model_routed_phase_is_recorded_in_the_history(tmp_path, monkeypatch):
    repo = _repo(tmp_path, _profile("models:\n  default: acme/m-small\n"))
    rid = _run(repo)
    S.advance(repo, rid, "P1")
    notes = [h.get("note", "") for h in ledger.load(repo, rid)["history"]]
    assert "model: acme/m-small" in notes[-1], notes


def test_a_missing_key_refuses_the_step_and_nothing_spawns(tmp_path):
    repo = _repo(tmp_path, _profile("models:\n  default: acme/m-small\n"))
    rid = _run(repo)
    S.advance(repo, rid, "P1")
    fake = answering()
    got = D.step(repo, rid, {}, run=fake)
    assert got["stop"] == D.REFUSED and "ACME_TEST_KEY is not set" in got["why"], got
    assert fake.seen == []


def test_a_model_changed_after_the_acknowledgement_does_not_spend(tmp_path, monkeypatch):
    """--spend named acme/m-small; the profile now says acme/m-large."""
    repo = _repo(tmp_path, _profile("models:\n  default: acme/m-large\n"))
    rid = _run(repo)
    S.advance(repo, rid, "P1")
    monkeypatch.setenv("ACME_TEST_KEY", "x")
    fake = answering()
    got = D.step(repo, rid, {}, run=fake, acknowledged={"acme/m-small"})
    assert got["stop"] == D.REFUSED and "not named in --spend" in got["why"], got
    assert fake.seen == []


# ── the operator's screen: --plan and --spend, spawned ───────────────────────


def _drive(repo: Path, *args: str, env_key: bool = False) -> subprocess.CompletedProcess:
    import os
    env = {k: v for k, v in os.environ.items() if k != "ACME_TEST_KEY"}
    if env_key:
        env["ACME_TEST_KEY"] = "x"
    return subprocess.run([sys.executable, str(ROOT / "tools" / "sdlc" / "drive.py"),
                           "--target", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", env=env, timeout=300)


def test_plan_names_the_model_and_spend_must_name_it(tmp_path):
    repo = _repo(tmp_path, _profile("models:\n  default: acme/m-small\n"))
    rid = _run(repo)
    plan = _drive(repo, "--run", rid, "--plan", env_key=True)
    assert "P1 (requirements): acme/m-small" in plan.stdout, plan.stdout
    assert "--spend writer,acme/m-small" in plan.stdout, plan.stdout
    bare = _drive(repo, "--run", rid, env_key=True)
    assert bare.returncode == 2 and "acme/m-small" in bare.stderr, bare.stderr


def test_a_missing_key_refuses_the_whole_walk_before_anything_spends(tmp_path):
    repo = _repo(tmp_path, _profile("models:\n  default: acme/m-small\n"))
    rid = _run(repo)
    plan = _drive(repo, "--run", rid, "--plan")
    assert plan.returncode == 2 and "ACME_TEST_KEY is not set" in plan.stdout, plan.stdout
    walk = _drive(repo, "--run", rid, "--spend", "writer,acme/m-small")
    assert walk.returncode == 2, walk.stdout + walk.stderr
    assert "P1 (requirements): " in walk.stderr and "ACME_TEST_KEY" in walk.stderr
    assert not (ledger.run_dir(repo, rid) / D.TRANSCRIPTS).exists(), "nothing ran"


# ── a declared runner receives the model through its command ─────────────────


def test_placeholders_hand_the_model_to_a_tools_capable_runner(tmp_path):
    builder = ("  - name: builder\n"
               f"    command: '{RUNNER_CMD} --model {{model}} --via {{provider}}'\n"
               "    capabilities: tools\n")
    profile = _profile("models:\n  default: acme/m-small\n"
                       "  implementation: acme/m-large\n", builder)
    profile = profile.replace("phase_runners:\n", "phase_runners:\n"
                                                  "  implementation: builder\n")
    repo = _repo(tmp_path, profile)
    routed = D.route_for(repo, "implementation")
    assert routed["name"] == "builder@acme/m-large"
    assert routed["command"].endswith("--model m-large --via acme")


def test_a_placeholder_with_routing_off_is_refused_by_name(tmp_path):
    builder = ("  - name: builder\n"
               f"    command: '{RUNNER_CMD} --model {{model}}'\n"
               "    capabilities: tools\n")
    profile = _profile("", builder).replace("model_routing: true\n", "")
    profile = profile.replace("phase_runners:\n",
                              "phase_runners:\n  implementation: builder\n")
    repo = _repo(tmp_path, profile)
    with pytest.raises(M.ModelError, match="model_routing is false"):
        D.route_for(repo, "implementation")


def test_routing_off_changes_nothing_about_a_walk(tmp_path):
    """The inert default, at the driver: an unrouted step is UNROUTED exactly
    as before T445, and no model is named anywhere in the record."""
    repo = _repo(tmp_path, _profile("").replace("model_routing: true\n", ""))
    rid = _run(repo)
    S.advance(repo, rid, "P1")
    got = D.step(repo, rid, {}, run=answering())
    assert got["stop"] == D.UNROUTED and "model" not in got, got
