"""P10 — verify what was deployed by asking the running system. Item 48.

The properties worth more than the rest of this file:

**A deploy that returned zero is not a deploy that worked.** P9 asks the
deployment tool; this phase asks the service — and item 46's first real run
recorded `deployed: true` for a change that never shipped, with the health check
answered by the process the deploy was supposed to replace.

**Three statuses, not a boolean.** A bad answer and no answer need different
repairs, and **neither is a pass**. Nor is *nothing was asked*.

**The rollback is PROPOSED, never run**, and that is checked structurally: this
module may not import anything that can execute a command.
"""
from __future__ import annotations

import ast
import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402
from tools.sdlc import verify as V  # noqa: E402

PROFILE = ("stack: python\ngate_profile: solo\n"
           "phases: [intake, requirements, design, implementation, test, review, "
           "triage, release, deploy, verify, closeout]\n"
           'test_command: "pytest -q"\nreview:\n  block_on: [critical, high]\n')


class _Quiet(http.server.BaseHTTPRequestHandler):
    """Answers with whatever code the fixture asked for, and says nothing."""

    code = 200

    def do_GET(self):                                   # noqa: N802
        self.send_response(self.code)
        self.end_headers()
        self.wfile.write(b'{"status":"UP"}')

    def log_message(self, *a):                          # noqa: D102
        pass


@pytest.fixture
def serving():
    """A real HTTP server on an ephemeral port. Started and stopped per test,
    because a probe test that mocks the probe measures the mock."""
    made = []

    def start(code: int = 200) -> str:
        handler = type("H", (_Quiet,), {"code": code})
        srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        made.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}/health"

    yield start
    for srv in made:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(PROFILE, encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    return r


def commit(repo: Path) -> None:
    """A real adopter COMMITS `.sdlc` - its own gitignore says so - and
    intake refuses to open a run against a tree nobody can reconstruct."""
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                   capture_output=True, timeout=120)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@e", "-c",
                    "user.name=t", "commit", "-qm", "base"], check=True,
                   capture_output=True, timeout=120)


def declare(repo: Path, *urls: str) -> None:
    p = repo / ".sdlc" / "profile.yml"
    listed = ", ".join(f'"{u}"' for u in urls)
    p.write_text(p.read_text(encoding="utf-8") + f"probes: [{listed}]\n",
                 encoding="utf-8")


def at_p10(repo: Path, deployed: bool = True) -> str:
    """A run that has actually deployed, because P10 verifies a DEPLOYMENT."""
    run_id = ledger.create(repo, "deploy-change", "a config change")["run_id"]
    folder = ledger.run_dir(repo, run_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ST.DEPLOY_RESULT).write_text(json.dumps({
        "run": run_id, "command": "deploy.sh", "where": str(repo),
        "at": "2026-08-30T00:00:00+00:00",
        "exit_code": 0 if deployed else 3, "deployed": deployed,
    }), encoding="utf-8")
    ledger.advance(repo, run_id, phase="P10", note="deployed, verifying")
    return run_id


# ── it verifies a DEPLOYMENT, and asks P9 what happened ──────────────────────


def test_a_run_that_never_deployed_has_nothing_to_verify(repo):
    """**The two phases composed.** A probe that passes with no deploy behind it
    is describing whatever was already running."""
    declare(repo, "http://127.0.0.1:1/health")
    run_id = ledger.create(repo, "deploy-change", "a change")["run_id"]
    ledger.advance(repo, run_id, phase="P10", note="")
    with pytest.raises(V.VerifyError) as exc:
        V.run(repo, run_id)
    assert "no deploy record" in str(exc.value)
    assert "whatever was already running" in str(exc.value)


def test_a_FAILED_deploy_has_nothing_healthy_to_verify(repo):
    declare(repo, "http://127.0.0.1:1/health")
    run_id = at_p10(repo, deployed=False)
    with pytest.raises(V.VerifyError) as exc:
        V.run(repo, run_id)
    assert "nothing healthy to verify" in str(exc.value)


def test_what_was_verified_is_recorded_not_inferred(repo, serving):
    """Item 46's lesson: the question its record could not answer about itself
    was WHICH TREE it had operated on. This one records what it verified."""
    declare(repo, serving(200))
    run_id = at_p10(repo)
    row = V.run(repo, run_id)
    assert row["deploy"]["command"] == "deploy.sh"
    assert row["deploy"]["where"] == str(repo)


# ── three statuses, and none of the other two is a pass ──────────────────────


def test_a_healthy_probe_passes(repo, serving):
    declare(repo, serving(200))
    run_id = at_p10(repo)
    row = V.run(repo, run_id)
    assert row["verdict"] == V.PASS
    assert row["passed"] == 1 and row["failed"] == 0 and row["unreachable"] == 0
    assert row["rollback_proposed"] is False and "rollback" not in row


def test_a_bad_ANSWER_is_a_failure_not_an_unreachable(repo, serving):
    """Something is serving and unhappy. That is a different page to open than
    nothing being there, which is why the statuses are kept apart."""
    declare(repo, serving(500))
    run_id = at_p10(repo)
    row = V.run(repo, run_id, file_defects=False)
    assert row["verdict"] == V.FAIL
    assert row["failed"] == 1 and row["unreachable"] == 0
    assert row["probes"][0]["status"] == 500
    assert "HTTP 500" in row["probes"][0]["detail"]


def test_NO_answer_is_unreachable_and_still_not_a_pass(repo):
    """Nothing listening. Folding this into `fail` would lose the difference
    between a service that is broken and a service that is absent."""
    declare(repo, "http://127.0.0.1:1/health")
    run_id = at_p10(repo)
    row = V.run(repo, run_id, timeout=2, file_defects=False)
    assert row["verdict"] == V.FAIL
    assert row["unreachable"] == 1 and row["failed"] == 0
    assert row["probes"][0]["status"] is None
    assert "no answer" in row["probes"][0]["detail"]


def test_one_bad_probe_fails_the_run_however_many_passed(repo, serving):
    declare(repo, serving(200), serving(200), "http://127.0.0.1:1/health")
    run_id = at_p10(repo)
    row = V.run(repo, run_id, timeout=2, file_defects=False)
    assert row["verdict"] == V.FAIL and row["passed"] == 2


def test_no_probes_declared_is_refused_not_reported_clean(repo):
    """*Nothing ran* and *nothing failed* are identical output unless something
    insists they differ - item 40's shape, met at P8, at P9, and here."""
    run_id = at_p10(repo)
    with pytest.raises(V.VerifyError) as exc:
        V.run(repo, run_id)
    assert "nothing to ask" in str(exc.value)
    assert "green nobody earned" in str(exc.value)


def test_zero_probes_is_never_a_pass_verdict():
    assert V.verdict([]) == V.FAIL


# ── the rollback is proposed, and CANNOT be run ──────────────────────────────


def test_this_module_cannot_execute_anything(repo):
    """**Structural, not remembered.** Rolling back is as much a change to a
    live system as rolling forward, so the module that proposes one may not
    import anything able to run it.

    Checked by AST rather than by searching the text, because this file's own
    docstring names `subprocess` and `os.system` while describing the rule -
    the exact false positive a substring sweep would produce, and the reason
    item 92's census gates and item 26's literal scan both had to stop reading
    prose.
    """
    tree = ast.parse((ROOT / "tools" / "sdlc" / "verify.py").read_text(
        encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            if node.module in ("os", "subprocess"):
                imported.update(a.name for a in node.names)
    assert "subprocess" not in imported, "P10 must not be able to run a command"
    assert "os" not in imported, "os.system and os.spawn* are a command runner"


def test_a_failure_proposes_a_rollback_with_the_runs_own_facts(repo, serving):
    declare(repo, serving(503))
    run_id = at_p10(repo)
    row = V.run(repo, run_id, file_defects=False)
    proposal = row["rollback"]
    assert row["rollback_proposed"] is True
    assert "Proposed, not run" in proposal
    assert "deploy.sh" in proposal, "the undo must reuse the deploy path"
    assert "does NOT cover" in proposal, "data is outside a commit revert"


def test_a_pass_proposes_nothing(repo, serving):
    declare(repo, serving(200))
    row = V.run(repo, at_p10(repo))
    assert row["rollback_proposed"] is False


# ── a failure enters the lifecycle as a run, once ────────────────────────────


def test_a_failing_probe_opens_a_defect(repo, serving):
    """Not a message somebody has to remember at three in the morning."""
    url = serving(500)
    declare(repo, url)
    commit(repo)
    row = V.run(repo, at_p10(repo))
    assert len(row["defects_filed"]) == 1
    filed = ledger.load(repo, row["defects_filed"][0])
    assert filed["kind"] == V.KIND == "defect"


def test_a_FLAPPING_probe_files_one_defect_and_not_one_per_attempt(repo, serving):
    """The identity is a deterministic specification over the URL, so the same
    failing endpoint is the same defect however often it is asked."""
    url = serving(500)
    declare(repo, url)
    commit(repo)
    first = V.run(repo, at_p10(repo))
    second = V.run(repo, at_p10(repo))
    assert len(first["defects_filed"]) == 1
    assert second["defects_filed"] == []
    assert second["defects_already_open"] == first["defects_filed"]


def test_a_CLOSED_run_does_not_hold_the_failure_filed(repo, serving):
    """Item 66's policy and its reason: if the probe still fails after somebody's
    attempt went away, the symptom outlived the attempt."""
    url = serving(500)
    declare(repo, url)
    commit(repo)
    first = V.run(repo, at_p10(repo))
    opened = first["defects_filed"][0]
    ledger.advance(repo, opened, phase="P11", status="closed", note="done")
    again = V.run(repo, at_p10(repo))
    assert again["defects_filed"] and again["defects_filed"] != [opened]


def test_no_file_probes_without_opening_anything(repo, serving):
    declare(repo, serving(500))
    row = V.run(repo, at_p10(repo), file_defects=False)
    assert row["verdict"] == V.FAIL
    assert row["defects_filed"] == [] and row["defects_already_open"] == []


# ── evidence, whether it passed or not ──────────────────────────────────────


def test_a_failed_verification_is_recorded_too(repo, serving):
    """The state somebody has to act on. A phase that only wrote evidence on
    success would hide exactly that case."""
    declare(repo, serving(500))
    run_id = at_p10(repo)
    V.run(repo, run_id, file_defects=False)
    assert V.recorded(repo, run_id)["verdict"] == V.FAIL
    assert (ledger.run_dir(repo, run_id) / V.RESULT).is_file()


def test_an_unreadable_record_is_refused_not_read_as_verified(repo):
    run_id = at_p10(repo)
    (ledger.run_dir(repo, run_id) / V.RESULT).write_text("{not json",
                                                          encoding="utf-8")
    owed = V.obligations(repo, run_id)
    assert len(owed) == 1 and "could not be read" in owed[0]


# ── wired into the phase contract ───────────────────────────────────────────


def test_p10_owes_its_record_through_the_conditional_seam():
    assert ST.VERIFY_RESULT in ST.by_phase("P10").conditional
    assert ST.CONDITIONAL_JUDGE["P10"][0] == "tools.sdlc.verify"
    assert ST.VERIFY_RESULT == V.RESULT          # one name, asked for


def test_a_run_at_p10_with_no_record_owes_one(repo, serving):
    declare(repo, serving(200))
    owed = V.obligations(repo, at_p10(repo))
    assert len(owed) == 1 and V.RESULT in owed[0]
    assert "opinion of itself" in owed[0]


def test_a_failed_verification_holds_the_phase(repo, serving):
    declare(repo, serving(500))
    run_id = at_p10(repo)
    V.run(repo, run_id, file_defects=False)
    owed = V.obligations(repo, run_id)
    assert len(owed) == 1 and "did not pass" in owed[0]
    assert "rollback proposal" in owed[0]


def test_a_passing_verification_owes_nothing(repo, serving):
    declare(repo, serving(200))
    run_id = at_p10(repo)
    V.run(repo, run_id)
    assert V.obligations(repo, run_id) == []


def test_an_earlier_phase_owes_nothing_here(repo, serving):
    declare(repo, serving(200))
    run_id = ledger.create(repo, "deploy-change", "a change")["run_id"]
    assert V.obligations(repo, run_id) == []


def test_a_repository_with_no_probes_owes_nothing(repo):
    """A repository that declares no probes is not one whose probes failed."""
    assert V.obligations(repo, at_p10(repo)) == []


def test_the_lazy_import_is_declared_in_the_bundle_map():
    from tools.sdlc import validate as Val

    assert "verify" in Val.LAZY_OUTSIDE_THE_BUNDLE


def test_the_phase_is_capable_only_when_probes_are_declared(repo, serving):
    assert ST.capable(repo, "probes") is False
    declare(repo, serving(200))
    assert ST.capable(repo, "probes") is True


# ── where probes come from ──────────────────────────────────────────────────


def test_a_component_declares_its_own_probes(tmp_path):
    """An estate probes per component, and the reader is `suite.quarantined` -
    reused rather than reimplemented, because it already carries the top-level
    fallback that `known_failures` had to learn the hard way."""
    root = tmp_path / "estate"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text(
        "scope: estate\ngate_profile: team\n"
        "components:\n"
        '  - name: "api"\n    path: "api"\n    role: authoritative\n'
        '    probes: ["http://127.0.0.1:9/api"]\n'
        '  - name: "web"\n    path: "web"\n    role: authoritative\n'
        '    probes: ["http://127.0.0.1:9/web"]\n',
        encoding="utf-8")
    assert V.probes_of(root, "api") == ["http://127.0.0.1:9/api"]
    assert sorted(V.probes_of(root)) == ["http://127.0.0.1:9/api",
                                         "http://127.0.0.1:9/web"]


def test_a_single_repository_reaches_the_top_level_key(repo, serving):
    """A single-repository install declares no `components:`, so without the
    fallback the key is UNREACHABLE - the defect `533aa0d` fixed for the test
    quarantine, which would otherwise be re-earned here."""
    url = serving(200)
    declare(repo, url)
    assert V.probes_of(repo) == [url]


def test_the_template_declares_the_key_the_capability_reads():
    """`stage.capable` asked for `probes` and the shipped profile template never
    wrote it, so the phase could not be turned on by filling the file in that
    an adopter is handed. Writer and reader, composed."""
    text = (ROOT / "sdlc_templates" / "profile.yml").read_text(encoding="utf-8")
    assert "\nprobes:" in text


# ── the CLI ─────────────────────────────────────────────────────────────────


def test_the_cli_asks_nothing_without_the_flag(repo, serving, capsys):
    declare(repo, serving(500))
    run_id = at_p10(repo)
    assert V.main(["--target", str(repo), "--run", run_id]) == 0
    assert "nothing was asked" in capsys.readouterr().out
    assert V.recorded(repo, run_id) is None


def test_the_cli_exits_nonzero_when_the_probes_did_not_pass(repo, serving, capsys):
    declare(repo, serving(500))
    run_id = at_p10(repo)
    assert V.main(["--target", str(repo), "--run", run_id, "--probe",
                   "--no-file"]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "PROPOSED" in out


def test_the_cli_exits_zero_when_they_did(repo, serving, capsys):
    declare(repo, serving(200))
    run_id = at_p10(repo)
    assert V.main(["--target", str(repo), "--run", run_id, "--probe"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_a_defect_the_intake_REFUSES_is_recorded_not_swallowed(repo, serving):
    """**A defect nobody could file is not a defect that did not happen.**

    Intake legitimately refuses to open a run against a tree nobody can
    reconstruct, and this phase runs at the one moment nobody is watching. A
    probe failure swallowed by that refusal would leave a red rollout with no
    run and no trace of why - zero silent drops, and the obligation says the
    rollout is red and nothing is tracking it.

    Found by running the tests rather than by design: the first cut let the
    refusal propagate, so a dirty tree turned a failing probe into a traceback.
    """
    declare(repo, serving(500))            # deliberately NOT committed
    run_id = at_p10(repo)
    row = V.run(repo, run_id)
    assert row["verdict"] == V.FAIL
    assert row["defects_filed"] == []
    assert len(row["defects_refused"]) == 1
    assert "uncommitted" in row["defects_refused"][0]["why"]
    owed = V.obligations(repo, run_id)
    assert len(owed) == 1 and "nothing is tracking it" in owed[0]


def test_allow_dirty_files_it_anyway_and_the_override_is_the_operators(repo, serving):
    """The refusal is not a dead end - it is a decision somebody takes, and
    `intake.open_run` records the override the same way it always has."""
    declare(repo, serving(500))
    row = V.run(repo, at_p10(repo), allow_dirty=True)
    assert len(row["defects_filed"]) == 1 and row["defects_refused"] == []


def test_a_response_the_harness_cannot_read_a_status_from_is_not_a_pass(repo, monkeypatch):
    """**could-not-look is not healthy**, at the one place it is easy to miss.

    urllib raises `HTTPError` for 4xx and 5xx, so the success branch almost
    never sees a bad code - which is exactly why the check inside it is worth
    a test rather than worth deleting. A response object this harness cannot
    read a status from reaches that branch, and treating it as healthy would be
    a green earned by not looking.

    Found by mutation: replacing the whole comparison with `PASS` survived the
    suite, because every existing case went through `HTTPError`.
    """
    class _NoStatus:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(V.urllib.request, "urlopen",
                        lambda *a, **k: _NoStatus())
    row = V.probe("http://127.0.0.1:9/health")
    assert row["result"] == V.FAIL and row["status"] == 0


def test_the_json_exit_code_answers_the_same_question_as_the_plain_one(repo, serving, capsys):
    """Two exit paths, one meaning. `--json` is the shape an automated caller
    uses, so it is the one where a wrong 0 is never seen by a human - and it was
    the one no test held, found by mutating each return separately."""
    declare(repo, serving(500))
    run_id = at_p10(repo)
    assert V.main(["--target", str(repo), "--run", run_id, "--probe",
                   "--no-file", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == V.FAIL

    healthy = serving(200)
    (repo / ".sdlc" / "profile.yml").write_text(PROFILE, encoding="utf-8")
    declare(repo, healthy)
    good = at_p10(repo)
    assert V.main(["--target", str(repo), "--run", good, "--probe",
                   "--json"]) == 0
