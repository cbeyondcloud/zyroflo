"""Item 44 — the ledger gate: a tampered ledger is unmergeable, not merely detectable.

Item 32 wrote its own limit down: an unsigned chain detects *edits*, and a wholesale
rewrite re-hashes cleanly. `test_the_rewrite_item_32_cannot_see_is_the_reason_this_exists`
is this file's centre — it asserts both halves of that sentence at once, that the local
check calls the rewritten ledger intact and that the gate does not. Everything else
protects a way the gate could pass when it should not:

| the shape                              | what must happen                     |
|----------------------------------------|--------------------------------------|
| a base ref this clone cannot see       | exit 2 — never "nothing to compare"  |
| a run archived between base and head   | a MOVE, not a deletion               |
| a ledger appended to                    | clean                                |
| a ledger rewritten, truncated, deleted  | named, per run                       |
| a branch that is not `sdlc/*`           | tamper still checked; gate not asked |
| the emitted single file                 | same verdicts, no harness, stdlib    |

The last row is the one an adopter actually runs. `.sdlc/ledger_check.py` is generated,
so the test that matters is DIFFERENTIAL — the copy and the original judged against the
same repository, and asked to agree — not a comparison of two texts.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import state as S  # noqa: E402
from tools.sdlc import validate as V  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

INSTALLER = ROOT / "sdlc-init.py"
WORKFLOW = (ROOT / "sdlc_templates" / "ledger-check.yml").read_text(encoding="utf-8")


def load_installer():
    spec = importlib.util.spec_from_file_location("sdlc_init_ledger", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init_ledger"] = mod
    spec.loader.exec_module(mod)
    return mod


si = load_installer()


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return proc.stdout


def _commit(cwd: Path, message: str) -> str:
    _git(cwd, "add", "-A")
    _git(cwd, "-c", "user.email=dev@example.invalid", "-c", "user.name=Dev",
         "commit", "-qm", message)
    return _git(cwd, "rev-parse", "HEAD").strip()


@pytest.fixture(scope="module")
def _template(tmp_path_factory) -> Path:
    """One repository with one approved run, built once and copied per test."""
    repo = tmp_path_factory.mktemp("ledger-template") / "repo"
    repo.mkdir()
    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    (repo / "app.txt").write_text("hello\n", encoding="utf-8")
    _commit(repo, "base")
    state = S.create(repo, "requirement", "widget pagination", run_id="run-alpha")
    (S.run_dir(repo, state["run_id"]) / "30-design.md").write_text("the design\n",
                                                                   encoding="utf-8")
    S.record_artifact(repo, "run-alpha", "30-design.md")
    R.park(repo, "run-alpha", "P7")
    A.record(repo, "run-alpha", "G4", "approve", "dev@example.invalid", notes="ship it")
    _commit(repo, "run + G4")
    return repo


@pytest.fixture()
def repo(_template: Path, tmp_path: Path) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(_template, dest)
    return dest


@pytest.fixture()
def base(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def ledger_of(repo: Path, run_id: str = "run-alpha") -> Path:
    return A.approvals_path(repo, run_id)


def rewrite(path: Path, mutate) -> None:
    """Rewrite the whole ledger and re-chain it, which is what someone with write
    access would do — and what no unsigned chain can notice by itself."""
    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]
    prev = ""
    for row in rows:
        mutate(row)
        row["prev"] = prev
        row.pop("hash", None)
        row["hash"] = A.entry_hash(row)
        prev = row["hash"]
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                    encoding="utf-8", newline="\n")


# ── integrity: the working tree's own account of itself ──────────────────────


def test_a_clean_run_passes_and_says_what_it_checked(repo, base, capsys):
    assert V.main(["--ledger", "--target", str(repo), "--base", base,
                   "--branch", "sdlc/run-alpha"]) == 0
    out = capsys.readouterr().out
    assert "OK - the record holds." in out
    assert "append-only vs" in out and "G4 for run-alpha" in out


def test_an_artifact_edited_after_the_yes_voids_the_gate(repo, base):
    (S.run_dir(repo, "run-alpha") / "30-design.md").write_text("tampered\n",
                                                               encoding="utf-8")
    report = V.check(repo, base, "sdlc/run-alpha")
    assert any("changed since G4 approved it" in p for p in report["integrity"])
    assert any("approved different bytes" in p for p in report["merge_ready"])
    assert not report["append_only"], "the ledger itself was not touched"


def test_one_line_edited_in_place_breaks_the_chain(repo, base):
    path = ledger_of(repo)
    rows = path.read_text(encoding="utf-8").replace("dev@example.invalid",
                                                    "someone@example.invalid")
    path.write_text(rows, encoding="utf-8", newline="\n")
    report = V.check(repo, base, "")
    assert any("does not match its contents" in p for p in report["integrity"])


def test_an_entry_that_claims_a_different_run_is_named(repo):
    rewrite(ledger_of(repo), lambda r: r.update(run_id="run-beta"))
    assert any("claims run_id 'run-beta'" in p for p in V.integrity(repo))


def test_a_recorded_artifact_that_moved_is_named_even_with_no_approval(repo, tmp_path):
    S.create(repo, "defect", "second", run_id="run-beta")
    (S.run_dir(repo, "run-beta") / "note.md").write_text("x\n", encoding="utf-8")
    S.record_artifact(repo, "run-beta", "note.md")
    (S.run_dir(repo, "run-beta") / "note.md").write_text("y\n", encoding="utf-8")
    assert any("run-beta: artifact note.md" in p for p in V.integrity(repo))


def test_a_run_folder_with_no_state_json_is_reported_not_skipped(repo):
    (S.runs_dir(repo) / "run-orphan").mkdir()
    assert any("run-orphan" in p for p in V.integrity(repo))


def test_a_corrupt_state_json_is_a_finding_not_a_traceback(repo, base):
    """`state.list_runs` reads every state file on the way past and answers for
    a bad one. A required check that dies tells a reviewer nothing about which
    run is wrong, so this gate enumerates folders instead.

    The premise below moved on 2026-09-09, in the closure audit: it used to pin
    that the shared helper RAISES on a damaged record. An unreadable run is now
    a ROW carrying the reason, so the listing — and every future intake, which
    walks the same rows — survives one damaged record instead of dying on it.
    The subject is untouched: the gate still refuses, and still names the run.
    """
    (S.runs_dir(repo) / "run-beta").mkdir()
    (S.runs_dir(repo) / "run-beta" / S.STATE_NAME).write_text("{not json",
                                                              encoding="utf-8")
    # the premise: the shared helper really does answer for the damaged run
    (damaged,) = [r for r in S.list_runs(repo) if r["run_id"] == "run-beta"]
    assert damaged["kind"] == "unreadable"
    assert "not readable JSON" in damaged["unreadable"]
    assert V.main(["--ledger", "--target", str(repo), "--base", base]) == 1
    assert any("run-beta" in p for p in V.integrity(repo))


# ── append-only: the check the chain cannot make for itself ──────────────────


def test_the_rewrite_item_32_cannot_see_is_the_reason_this_exists(repo, base):
    """Both halves of item 32's recorded limit, asserted together. If the first
    assertion ever fails, the chain grew signatures and this item is redundant."""
    rewrite(ledger_of(repo), lambda r: r.update(approver="someone@example.invalid"))
    assert A.verify_chain(repo, "run-alpha") == [], \
        "a wholesale rewrite re-hashes cleanly - that is the premise of item 44"
    assert V.integrity(repo) == [], "and the working tree alone cannot tell"
    problems = V.append_only(repo, base)
    assert len(problems) == 1 and "rewritten, not appended to" in problems[0]


def test_appending_a_real_second_approval_stays_clean(repo, base):
    S.advance(repo, "run-alpha", phase="P9")
    A.record(repo, "run-alpha", "G5", "approve", "dev@example.invalid")
    assert V.append_only(repo, base) == []
    assert V.integrity(repo) == []


def test_a_truncated_ledger_is_named_as_truncation(repo, base):
    S.advance(repo, "run-alpha", phase="P9")
    A.record(repo, "run-alpha", "G5", "approve", "dev@example.invalid")
    _commit(repo, "two approvals")
    two = _git(repo, "rev-parse", "HEAD").strip()
    path = ledger_of(repo)
    path.write_text(path.read_text(encoding="utf-8").splitlines()[0] + "\n",
                    encoding="utf-8", newline="\n")
    problems = V.append_only(repo, two)
    assert len(problems) == 1 and "truncated" in problems[0]
    assert "2 entries at" in problems[0] and ", 1 here" in problems[0]


def test_a_deleted_ledger_is_not_a_missing_one(repo, base):
    ledger_of(repo).unlink()
    problems = V.append_only(repo, base)
    assert len(problems) == 1 and "is gone here" in problems[0]


def test_archiving_a_run_moves_its_ledger_and_that_is_not_a_deletion(repo, base):
    S.advance(repo, "run-alpha", status="closed")
    S.archive(repo, "run-alpha")
    assert not (S.runs_dir(repo) / "run-alpha").exists(), "the fixture must really move it"
    assert V.append_only(repo, base) == []


def test_reordering_entries_is_caught_at_the_first_one_that_differs(repo, base):
    S.advance(repo, "run-alpha", phase="P9")
    A.record(repo, "run-alpha", "G5", "approve", "dev@example.invalid")
    _commit(repo, "two approvals")
    two = _git(repo, "rev-parse", "HEAD").strip()
    path = ledger_of(repo)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8", newline="\n")
    problems = V.append_only(repo, two)
    assert len(problems) == 1 and "entry 1 differs" in problems[0]


def test_without_a_base_the_append_only_question_is_simply_not_asked(repo):
    rewrite(ledger_of(repo), lambda r: r.update(approver="someone@example.invalid"))
    report = V.check(repo, "", "")
    assert report["append_only"] == []
    assert report["problems"] == [], "a rewrite is invisible without the base - by design"


# ── merge readiness ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("branch, expected", [
    ("sdlc/run-alpha", "run-alpha"),
    ("refs/heads/sdlc/run-alpha", "run-alpha"),
    ("origin/sdlc/run-alpha", "run-alpha"),
    ("sdlc/run-alpha/", "run-alpha"),
    ("feature/run-alpha", ""),
    ("main", ""),
    ("", ""),
])
def test_only_an_sdlc_branch_names_a_run(branch, expected):
    assert V.run_of_branch(branch) == expected


def test_a_branch_naming_a_run_that_does_not_exist_is_a_finding(repo, base):
    report = V.check(repo, base, "sdlc/never-opened")
    assert any("has no folder under" in p for p in report["merge_ready"])


def test_a_rejected_gate_does_not_let_the_branch_merge(repo):
    S.create(repo, "requirement", "second", run_id="run-beta")
    R.park(repo, "run-beta", "P7")
    A.record(repo, "run-beta", "G4", "reject", "dev@example.invalid", notes="no")
    problems = V.merge_ready(repo, "run-beta")
    assert len(problems) == 1 and "rejected by" in problems[0]


def test_a_run_with_no_merge_gate_at_all_is_refused(repo):
    S.create(repo, "requirement", "second", run_id="run-beta")
    assert any("has no entry in the ledger" in p for p in V.merge_ready(repo, "run-beta"))


def test_tamper_is_still_checked_on_a_branch_that_is_not_sdlc(repo, base):
    """The plan scopes the CHECK to `sdlc/*` PRs; a tamper gate you dodge by
    renaming your branch is not a gate. Only the third question keys off it."""
    rewrite(ledger_of(repo), lambda r: r.update(approver="someone@example.invalid"))
    report = V.check(repo, base, "feature/anything")
    assert report["merge_ready"] == []
    assert report["append_only"], "the rewrite is caught regardless of branch name"


def test_the_merge_gate_is_one_approve_py_knows(repo):
    assert V.MERGE_GATE in A.GATES


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_a_base_ref_this_clone_cannot_see_is_an_error_never_a_pass(repo, capsys):
    """The failure this repository keeps finding in its own tools: nothing to
    compare against reading as nothing wrong."""
    assert V.main(["--ledger", "--target", str(repo), "--base", "origin/nope"]) == 2
    out = capsys.readouterr().out
    assert "is not a commit this checkout can see" in out and "fetch-depth: 0" in out


def test_a_shallow_clone_cannot_quietly_skip_the_comparison(repo, tmp_path, capsys):
    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", f"file://{repo.as_posix()}",
         str(shallow))
    assert V.main(["--ledger", "--target", str(shallow),
                   "--base", "HEAD~1"]) == 2
    assert "append-only cannot be proven" in capsys.readouterr().out


def test_neither_mode_given_is_a_usage_error(capsys):
    assert V.main([]) == 2
    assert "--ledger" in capsys.readouterr().out


def test_a_target_that_is_not_a_directory_is_refused(tmp_path, capsys):
    assert V.main(["--ledger", "--target", str(tmp_path / "nope")]) == 2
    assert "no directory at" in capsys.readouterr().out


def test_json_carries_every_group_and_the_harness_it_was_judged_by(repo, base, capsys):
    V.main(["--ledger", "--target", str(repo), "--base", base, "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert set(doc) >= {"integrity", "append_only", "merge_ready", "problems",
                        "harness", "runs", "base", "branch", "run"}
    assert doc["runs"] == 1


def test_exit_one_is_the_signal_a_required_check_blocks_on(repo, base):
    rewrite(ledger_of(repo), lambda r: r.update(approver="someone@example.invalid"))
    assert V.main(["--ledger", "--target", str(repo), "--base", base]) == 1


# ── the emitted single file, which is what an adopter's CI actually runs ─────


def _run_file(script: Path, repo: Path, base: str, branch: str = "") -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    argv = [sys.executable, str(script), "--ledger", "--target", str(repo),
            "--base", base]
    if branch:
        argv += ["--branch", branch]
    proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300, env=env, cwd=str(script.parent))
    return proc.returncode, proc.stdout


@pytest.mark.parametrize("tamper", [False, True])
def test_the_emitted_file_and_the_harness_reach_the_same_verdict(repo, base, tmp_path,
                                                                 tamper):
    """A generated copy is only trustworthy if it is judged against the original
    on the same repository. Comparing two TEXTS would prove nothing."""
    if tamper:
        rewrite(ledger_of(repo), lambda r: r.update(approver="x@example.invalid"))
    bundle = V.emit_check(tmp_path / "ledger_check.py")
    got_code, got_out = _run_file(bundle, repo, base, "sdlc/run-alpha")
    want_code, want_out = _run_file(ROOT / "tools" / "sdlc" / "validate.py", repo, base,
                                    "sdlc/run-alpha")
    assert (got_code, got_out) == (want_code, want_out)
    assert got_code == (1 if tamper else 0)


def test_the_emitted_file_imports_nothing_outside_the_standard_library(tmp_path):
    """No packages is the promise the whole harness is built on; an adopter's CI
    installs nothing to run this."""
    src = V.render_check(commit="deadbeef")
    stdlib = set(sys.stdlib_module_names)
    for line in src.splitlines():
        if line.startswith(("import ", "from ")) and "tools.sdlc" not in line:
            top = line.split()[1].split(".")[0]
            assert top in stdlib, f"the bundle reaches outside the stdlib: {line}"


def test_the_embedded_set_is_the_computed_closure_not_a_hand_written_list():
    names = V.closure()
    assert names[-1] == "validate" and "state" in names and "approve" in names
    assert names.index("state") < names.index("approve"), "dependency order"
    src = "\n".join((ROOT / "tools" / "sdlc" / f"{n}.py").read_text(encoding="utf-8")
                    for n in names)
    assert set(V._SIBLING.findall(src)) <= set(names), \
        "a module imported by the closure was left out of it"


def test_the_bundle_is_deterministic_and_stamps_the_harness_it_came_from():
    first = V.render_check(commit="deadbeef")
    assert first == V.render_check(commit="deadbeef"), \
        "a re-install must read as unchanged when the harness has not moved"
    assert 'HARNESS_COMMIT = "deadbeef"' in first


def test_the_bundle_reports_the_harness_it_came_from_not_the_repo_it_judges(
        repo, base, tmp_path):
    """`harness_version()` shells out to git from wherever it sits. In an
    adopter's checkout that is the adopter's commit — a wrong fact arrived at
    confidently, which is worse than an absent one."""
    bundle = V.emit_check(tmp_path / "ledger_check.py")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run([sys.executable, str(bundle), "--ledger", "--target",
                           str(repo), "--base", base, "--json"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300,
                          env=env, cwd=str(repo))
    reported = json.loads(proc.stdout)["harness"]
    assert reported != base, "it reported the repository under test as the harness"
    assert reported == S.harness_version()


def test_the_bundle_runs_with_no_harness_anywhere_on_the_path(repo, base, tmp_path):
    """The adopter's runner has a checkout of THEIR repo and nothing else."""
    bundle = V.emit_check(tmp_path / "elsewhere" / "ledger_check.py")
    code, out = _run_file(bundle, repo, base)
    assert code == 0 and "OK - the record holds." in out


# ── the installer wires both files, or neither, and says which ───────────────


@pytest.fixture()
def target(tmp_path: Path) -> Path:
    root = tmp_path / "adopter"
    (root / ".github").mkdir(parents=True)
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    return root


def test_a_github_repo_gets_the_check_and_the_workflow_together(target):
    assert si.no_ledger_gate_reason(target) is None
    ops = si.ledger_gate_ops(target)
    rels = [op["rel"] for op in ops]
    assert rels == [si.LEDGER_CHECK_REL, si.LEDGER_WORKFLOW_REL]
    assert all(op["status"] == "create" for op in ops)
    assert "HARNESS_COMMIT" in ops[0]["content"]
    assert ops[1]["content"] == WORKFLOW


def test_a_repo_with_no_github_dir_gets_neither_and_the_reason_names_it(tmp_path):
    reason = si.no_ledger_gate_reason(tmp_path)
    assert reason and ".github" in reason


def test_a_standalone_installer_says_so_rather_than_shipping_half_a_gate(tmp_path):
    """Copied out of its clone, sdlc-init.py has no `tools/` to generate from —
    the same Tier 0 signal the absent graph builder is."""
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    reason = si.no_ledger_gate_reason(tmp_path / "adopter", harness=lonely)
    assert reason and "standalone sdlc-init.py" in reason
    assert si.LEDGER_EMITTER.as_posix() in reason


def test_an_emitter_that_fails_blocks_the_file_instead_of_writing_nothing(target,
                                                                         tmp_path):
    fake = tmp_path / "fake-harness"
    (fake / si.LEDGER_EMITTER.parent).mkdir(parents=True)
    (fake / si.LEDGER_EMITTER).write_text(
        "import sys\nsys.stderr.write('emitter broke\\n')\nraise SystemExit(4)\n",
        encoding="utf-8")
    ops = si.ledger_gate_ops(target, harness=fake)
    assert len(ops) == 1 and ops[0]["status"] == "blocked"
    assert "exited 4" in ops[0]["reason"] and "emitter broke" in ops[0]["reason"]


def test_re_running_the_installer_reports_both_files_unchanged(target):
    for op in si.ledger_gate_ops(target):
        si.write_lf(op["path"], op["content"])
    assert all(op["status"] == "unchanged" for op in si.ledger_gate_ops(target))


def test_the_installed_check_runs_in_the_repo_it_was_installed_into(repo, base,
                                                                    tmp_path):
    (repo / ".github").mkdir(exist_ok=True)
    for op in si.ledger_gate_ops(repo):
        si.write_lf(op["path"], op["content"])
    code, out = _run_file(repo / si.LEDGER_CHECK_REL, repo, base, "sdlc/run-alpha")
    assert code == 0 and "OK" in out


# ── the workflow: what it must say, and what it must never say ───────────────


def test_the_workflow_never_interpolates_an_actions_expression_into_a_shell_line():
    """A branch name is attacker-chosen text. `${{ }}` inside `run:` is how an
    untrusted string becomes an executed one; the environment is the seam."""
    import yaml
    doc = yaml.safe_load(WORKFLOW)
    for step in doc["jobs"]["ledger"]["steps"]:
        assert "${{" not in step.get("run", ""), f"expression in a run line: {step}"
    env = doc["jobs"]["ledger"]["steps"][-1]["env"]
    assert set(env) == {"BASE_SHA", "HEAD_REF"}
    assert all("${{" in v for v in env.values())


def test_the_workflow_fetches_enough_history_to_have_a_base_at_all():
    import yaml
    doc = yaml.safe_load(WORKFLOW)
    checkout = next(s for s in doc["jobs"]["ledger"]["steps"]
                    if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0, \
        "without it every run exits 2, and a check that always errors gets turned off"


def test_the_workflow_runs_on_every_pull_request_not_only_sdlc_branches():
    import yaml
    doc = yaml.safe_load(WORKFLOW)
    triggers = doc.get("on") or doc[True]
    assert set(triggers) == {"pull_request"}
    assert not (triggers["pull_request"] or {}), \
        "a branch filter here is a tamper check you dodge by renaming your branch"


def test_every_flag_the_workflow_uses_exists_in_the_shipped_cli():
    """The workflows' currency rule: a generated file that teaches a flag the CLI
    dropped is a required check that fails for the wrong reason."""
    src = (ROOT / "tools" / "sdlc" / "validate.py").read_text(encoding="utf-8")
    import yaml
    run = yaml.safe_load(WORKFLOW)["jobs"]["ledger"]["steps"][-1]["run"]
    flags = {token for token in run.split() if token.startswith("--")}
    assert flags == {"--ledger", "--target", "--base", "--branch"}
    for flag in flags:
        assert f'"{flag}"' in src, f"validate.py no longer accepts {flag}"


def test_the_workflow_says_how_to_actually_make_the_check_required():
    """Installing a job is not enforcing it, and a header that implied otherwise
    would leave an adopter believing in a gate GitHub is not applying."""
    assert "branch protection" in WORKFLOW.lower()
    assert "Require status checks" in WORKFLOW
    assert si.LEDGER_CHECK_REL.split("/")[-1] in WORKFLOW


def test_the_workflow_states_the_hole_no_ci_check_can_close():
    """A `pull_request` job runs the copy of itself that is on the branch, so a
    branch can weaken its own gate. Every CI policy check has this hole; one
    that did not say so would be selling a guarantee it does not have."""
    assert "CODEOWNERS" in WORKFLOW
    assert "runs\n# the version of itself that is ON THE BRANCH" in WORKFLOW
    for path in (si.LEDGER_CHECK_REL, ".github/workflows/"):
        assert path.lstrip(".") in WORKFLOW, f"CODEOWNERS advice omits {path}"


# ── what the emitted file can and cannot carry ───────────────────────────────

#: String-form imports an embedded module may make LAZILY, each with the reason
#: it cannot die in the emitted check. Spelled out — the CONTRACT_ADAPTERS
#: idiom — so a new one is a decision somebody writes down, never a drift. The
#: reason must hold two ways: the branch is unreached by `--ledger`, AND if it
#: ever were reached, the ImportError is caught and becomes a named refusal
#: rather than a crash.
LAZY_STRING_IMPORTS = {
    ("stage", "tools.graph.outdir"):
        "stage._pack_out_for asks it where scratch is, because pack.py states "
        "that a pack belongs there and never in git and the run folder is "
        "committed - one home for the name, item 94. Reached only when a PROMPT "
        "is rendered, and the emitted check renders none (--ledger reads "
        "approvals); if reached anyway, _pack_out_for catches the ImportError "
        "and refuses by name",
    ("policy", "tools.provider_smoke"):
        "reached only under require_conformance, which the emitted check "
        "never evaluates (--ledger routes no runners); if reached anyway, "
        "runner_for catches the ImportError and refuses by name",
}


def test_no_embedded_module_reaches_outside_tools_sdlc():
    """The constraint that nearly ate a refactor, made mechanical.

    `closure()` follows `^from tools\\.sdlc import (\\w+)` and the emitted
    loader registers each module as `tools.sdlc.<name>`. So a module in the
    closure that imports anything ELSE from the harness is embedded WITHOUT
    its dependency, and the exec'd source raises ImportError in an adopter's
    CI — where there is no harness to fall back on and this is a required
    check.

    Nothing said so. A sweep folding every hand-rolled console block onto
    `tools.cli` would have added `from tools import cli` to `approve.py` and
    `validate.py`, and the local suite would have stayed green because the
    local suite has a harness. `approve.read_chain` keeps its own reader for
    exactly this reason, and says so where a reader will find it.

    **The sweep covers the string spelling too** — `importlib.import_module`
    with a `tools.` literal — because a lazy import that dodged the line scan
    would be the same death spelled differently. Each one allowed must sit in
    `LAZY_STRING_IMPORTS` with its reason.
    """
    here = ROOT / "tools" / "sdlc"
    offenders = []
    for name in V.closure():
        for n, line in enumerate((here / f"{name}.py").read_text(
                encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("from tools", "import tools")) and \
                    not stripped.startswith("from tools.sdlc import "):
                offenders.append(f"{name}.py:{n}: {stripped}")
            for mod in re.findall(
                    r"import_module\(\s*['\"](tools\.[\w.]+)['\"]", stripped):
                if not mod.startswith("tools.sdlc.") and \
                        (name, mod) not in LAZY_STRING_IMPORTS:
                    offenders.append(
                        f"{name}.py:{n}: import_module({mod!r}) is not "
                        f"declared in LAZY_STRING_IMPORTS")
    assert offenders == [], (
        "these imports cannot be carried by the emitted ledger check: "
        + "; ".join(offenders))


def test_every_declared_string_import_still_exists():
    """The exemption table's own guard: an entry for a line somebody deleted
    is a reason covering nothing, and the next real offender hides behind it."""
    here = ROOT / "tools" / "sdlc"
    for (name, mod), reason in LAZY_STRING_IMPORTS.items():
        src = (here / f"{name}.py").read_text(encoding="utf-8")
        assert f'"{mod}"' in src or f"'{mod}'" in src, \
            f"LAZY_STRING_IMPORTS declares {name}.py imports {mod} and it does not"
        assert reason.strip(), "an exemption with no reason is a hole"


# ── the same constraint, one level in: the NAME, not just the package ────────
#
# The test above asks whether an import's PREFIX is `tools.sdlc`. It never asks
# whether the thing imported is in the closure - and `closure()` is anchored at
# column 0, so an indented `from tools.sdlc import suite` satisfies that test
# while being carried by nothing. Found during the first field run: intake had
# four such imports and approve a fifth, and the bundle survives only because
# `validate.main(--ledger)` reads approvals and never writes one.


def _bare(tmp_path: Path, script: str) -> subprocess.CompletedProcess:
    """Run `script` with the harness genuinely unreachable.

    `-I -S`: isolated (no PYTHONPATH, no user site) and no site.py, so an
    editable install's .pth does not put `tools` back on the path. The child
    asserts that for itself, because a probe that quietly ran WITH the harness
    would pass while proving nothing - which is what the first cut of it did.
    """
    (tmp_path / "probe.py").write_text(script, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run([sys.executable, "-I", "-S", "probe.py"],
                          cwd=str(tmp_path), env=env, capture_output=True,
                          text=True, timeout=120)


def test_every_sibling_import_in_the_bundle_is_carried_or_declared():
    """`ast`, at every indentation, and the NAME is what is checked."""
    embedded = V.closure()
    undeclared = [f"{owner}.py:{line} imports `{mod}`"
                  for owner, rows in V.unsatisfiable(embedded).items()
                  for line, mod in rows
                  if mod not in V.LAZY_OUTSIDE_THE_BUNDLE]
    assert undeclared == [], (
        "the emitted ledger check would carry a module whose import it cannot "
        "satisfy, and it raises ImportError in an adopter's CI the first time "
        "anything reaches it: " + "; ".join(undeclared))


def test_emitting_refuses_a_bundle_it_knows_is_broken():
    """The teeth. Without the declaration these five sites are undeclared, and
    the emitter must refuse rather than ship a file that works until it does
    not."""
    real = dict(V.LAZY_OUTSIDE_THE_BUNDLE)
    try:
        V.LAZY_OUTSIDE_THE_BUNDLE.clear()
        with pytest.raises(RuntimeError) as caught:
            V.render_check()
    finally:
        V.LAZY_OUTSIDE_THE_BUNDLE.update(real)
    message = str(caught.value)
    assert "suite" in message and "intake.py:" in message, \
        "the refusal must name the module and the line, not just object"
    assert "LAZY_OUTSIDE_THE_BUNDLE" in message, \
        "and it must say what to do about it"
    assert V.render_check(), "the declaration restored, emitting works again"


def test_the_emit_refusal_is_an_answer_and_not_a_traceback(tmp_path, capsys):
    """1 in this CLI means the ledger does not support a merge, and the
    installer spawns this command. An uncaught RuntimeError would exit 1 and
    say the wrong thing about a repository."""
    real = dict(V.LAZY_OUTSIDE_THE_BUNDLE)
    try:
        V.LAZY_OUTSIDE_THE_BUNDLE.clear()
        code = V.main(["--emit-check", str(tmp_path / "check.py")])
    finally:
        V.LAZY_OUTSIDE_THE_BUNDLE.update(real)
    assert code == 3, "could-not-emit is its own answer, not a merge verdict"
    out = capsys.readouterr().out
    assert out.startswith("!!") and "suite" in out
    assert not (tmp_path / "check.py").is_file(), \
        "and no half-honest file is left behind"


def test_the_declaration_is_a_map_of_real_holes_and_cannot_rot():
    """Every declared name must actually be missing AND actually be imported.
    An entry for a module the bundle now carries is a stale exemption, and the
    next real one hides behind it."""
    embedded = V.closure()
    reached = {mod for rows in V.unsatisfiable(embedded).values()
               for _, mod in rows}
    for name, reason in V.LAZY_OUTSIDE_THE_BUNDLE.items():
        assert name not in embedded, \
            f"{name} IS carried by the bundle now - drop the exemption"
        assert name in reached, \
            f"nothing in the bundle imports {name} any more - drop the exemption"
        assert len(reason) > 40, f"{name}'s exemption states no reason"


def test_widening_the_closure_regex_puts_back_the_cycle_it_breaks():
    """A control, green by design, pinning a measured rejection.

    Matching indented imports too is the obvious fix and it does not work: an
    indented sibling import in this package means a cycle, and following it
    orders `policy` BEFORE the `intake` it needs at module scope. The emitted
    file then dies on its own loader. Measured, and pinned here so the next
    person to reach for the one-character fix finds the answer first.
    """
    import re as _re
    original = V._SIBLING
    try:
        V._SIBLING = _re.compile(r"^\s*from tools\.sdlc import (\w+)", _re.M)
        widened = V.closure()
    finally:
        V._SIBLING = original
    assert widened.index("policy") < widened.index("intake"), \
        "the widened closure no longer inverts the pair - re-measure the ruling"
    assert V.closure().index("intake") < V.closure().index("policy"), \
        "the real closure orders them the way module scope needs"


def test_the_bundle_really_does_die_on_a_declared_hole(tmp_path):
    """A control, green by design: the landmine is real, not an AST artifact.

    The declaration says these paths are unreachable from the ledger check, not
    that they are harmless. This reaches one on purpose, under an interpreter
    with no harness, and requires the ImportError - so `LAZY_OUTSIDE_THE_BUNDLE`
    stays a map of live holes rather than becoming a list of things somebody
    once worried about.
    """
    check = V.emit_check(tmp_path / "check.py")
    # `baseline` returns before the lazy import when no command is declared, so
    # without this the probe reports NO-CRASH and proves the opposite of what
    # it was written for. The first cut did exactly that.
    (tmp_path / ".sdlc").mkdir(exist_ok=True)
    (tmp_path / ".sdlc" / "profile.yml").write_text(
        'stack: python\ntest_command: "python -c \\"pass\\""\n', encoding="utf-8")
    result = _bare(tmp_path, f'''
import importlib.util, sys
if importlib.util.find_spec("tools") is not None:
    print("HARNESS-REACHABLE"); raise SystemExit(3)
spec = importlib.util.spec_from_file_location("c", r"{check}")
mod = importlib.util.module_from_spec(spec)
sys.modules["c"] = mod
spec.loader.exec_module(mod)
intake = sys.modules["tools.sdlc.intake"]
print("PROFILE-VALUE-OK", intake.profile_value(".", "stack"))
try:
    intake.baseline(".", run=True)
except ImportError as exc:
    print("IMPORTERROR", exc); raise SystemExit(9)
print("NO-CRASH"); raise SystemExit(0)
''')
    assert "HARNESS-REACHABLE" not in result.stdout, \
        "the child could import the harness, so this proves nothing"
    assert "PROFILE-VALUE-OK" in result.stdout, \
        f"the bundle did not even load: {result.stdout}{result.stderr}"
    assert result.returncode == 9 and "suite" in result.stdout, (
        "reaching intake.baseline from inside the bundle no longer raises - if "
        "suite is carried now, drop its exemption; if the path is gone, drop "
        f"it too. Got: {result.stdout}{result.stderr}")


def test_the_closure_guard_is_looking_at_something():
    """Its own guard: an empty closure, or one that never reads a `from tools`
    line, would make the assertion above vacuous."""
    names = V.closure()
    assert "approve" in names and "validate" in names
    joined = "\n".join((ROOT / "tools" / "sdlc" / f"{n}.py").read_text(
        encoding="utf-8") for n in names)
    assert joined.count("from tools.sdlc import ") > 5


def test_the_merge_gate_does_not_read_a_superseded_verdict(repo):
    """Composed, because the mutation pass proved nothing was composing it.

    `live_entries` had its own tests and `integrity` had its own tests, and
    reverting `integrity` to walk the whole chain killed neither - the fifth
    time in one day that a producer and a consumer were each tested and never
    run together. On the first run to take the P6-to-P4 loop this is what kept
    a correct change out of a merge: an obsolete G3, reported as violated,
    correctly and about nothing.
    """
    rid = "run-alpha"
    # The fixture already holds one G4 approving this design. Revise it and
    # approve again — which is what a loop back through the phases produces.
    (S.run_dir(repo, rid) / "30-design.md").write_text(
        "the design, revised after review\n", encoding="utf-8")
    S.record_artifact(repo, rid, "30-design.md")
    A.record(repo, rid, "G4", "approve", "dev@example.invalid", "round two")

    problems = [p for p in V.integrity(repo) if "30-design.md" in p]
    assert problems == [], (
        "the superseded round-one entry is being checked against today's tree: "
        + "; ".join(problems))
    assert len(A.read_chain(repo, rid)) == 2, \
        "the superseded entry stays in the chain; only the VERDICT is superseded"
    assert A.verify_chain(repo, rid) == [], "and the chain still covers both"


# ── a sibling named as a string is an import the scanner could not see ───────
#
# `stage.CONDITIONAL_JUDGE` holds `"tools.sdlc.adr"` and loads it with
# `importlib.import_module`, so `stage.verify` reaches it for any phase carrying
# a conditional artifact. The scanner read `from tools.sdlc import X` and
# nothing else, so the hole was never declared and `render_check` never refused
# it - while the emitted file raised `ModuleNotFoundError: No module named
# 'tools.sdlc.adr'` under a bare interpreter the moment it verified P3.
#
# It does not fail in practice for the reason every entry in the map gives:
# nothing the ledger check reaches records an approval. **A hole the guard
# cannot see is what the guard exists to prevent**, and the next judge added
# would have been the third invisible one.


def test_a_sibling_named_as_a_string_is_seen_as_an_import():
    source = ('TABLE = {"P3": ("tools.sdlc.adr", "--check", "why")}\n'
              "import importlib\n"
              "def go():\n"
              "    return importlib.import_module(TABLE['P3'][0])\n")
    assert V.sibling_imports(source) == [(1, "adr")]


def test_an_import_statement_is_still_seen():
    """The negative control: widening the scanner must not lose what it had."""
    source = "def go():\n    from tools.sdlc import suite\n    return suite\n"
    assert V.sibling_imports(source) == [(2, "suite")]


def test_a_string_that_is_not_a_sibling_is_not_an_import():
    """`tools.review.sca` is not a sibling of this package, and a sentence
    mentioning the package is not an import at all. A scanner that cried wolf
    would put entries in the landmine map that are not landmines."""
    source = ('A = "tools.review.sca"\n'
              'B = "tools.sdlc"\n'
              'C = "see tools.sdlc.adr for the trigger rules"\n'
              'D = "tools.sdlc.adr.something"\n')
    assert V.sibling_imports(source) == []


def test_every_judge_the_contract_names_is_declared_or_embedded():
    """The rule rather than the instance. A phase gains a conditional artifact
    by gaining a judge, and the judge is named as a string - so the next one
    must be argued for in `LAZY_OUTSIDE_THE_BUNDLE` or carried by the bundle,
    and cannot simply be invisible."""
    from tools.sdlc import stage as ST

    embedded = set(V.closure())
    for phase, (module, _, _) in ST.CONDITIONAL_JUDGE.items():
        name = module.rsplit(".", 1)[1]
        assert name in embedded or name in V.LAZY_OUTSIDE_THE_BUNDLE, (
            f"{phase}'s judge {module} is neither embedded nor declared - the "
            f"emitted check would raise ImportError the moment it verified "
            f"{phase}")


def test_the_emitted_check_raises_on_the_hole_it_declares(tmp_path):
    """The declaration is a landmine MAP, not an approval, and this is what it
    maps. Under an interpreter with no harness, `verify` on a phase carrying a
    conditional artifact fails exactly as the entry says it would - which is
    why nothing the ledger check reaches may ever call it.

    Through `_bare`, whose own docstring records the mistake the first cut of
    this test repeated: a probe that quietly runs WITH the harness passes while
    proving nothing.
    """
    out = V.emit_check(tmp_path / V.CHECK_NAME)
    assert out.is_file()
    where = tmp_path / "repo"
    (where / ".sdlc" / "runs" / "r1").mkdir(parents=True)
    (where / ".sdlc" / "runs" / "r1" / "state.json").write_text(
        json.dumps({"run_id": "r1", "phase": "P3", "status": "running",
                    "artifacts": {}}), encoding="utf-8")
    proc = _bare(tmp_path, "\n".join([
        "import importlib.util, sys",
        "if importlib.util.find_spec('tools') is not None:",
        "    print('NOT ISOLATED'); raise SystemExit(1)",
        f"spec = importlib.util.spec_from_file_location('lc', r'{out}')",
        "m = importlib.util.module_from_spec(spec)",
        "spec.loader.exec_module(m)",
        "stage = sys.modules['tools.sdlc.stage']",
        "try:",
        f"    stage.verify(r'{where}', 'r1', 'P3')",
        "    print('NO-RAISE')",
        "except ModuleNotFoundError as exc:",
        "    print('RAISED', exc)",
    ]))
    assert "RAISED" in proc.stdout, proc.stdout + proc.stderr
    # The name is whichever declared hole `verify` reaches FIRST, and it moved
    # once already: `claims` is asked at every phase and now precedes the
    # conditional judges, so pinning `adr` by name pinned an ordering nobody
    # promised. The property is that the raised module is a DECLARED hole -
    # naming an undeclared one would mean the map is wrong, which is the thing
    # worth failing over.
    raised = proc.stdout.split("RAISED", 1)[1]
    named = [m for m in V.LAZY_OUTSIDE_THE_BUNDLE if f"tools.sdlc.{m}" in raised]
    assert named, f"{raised.strip()!r} names no module in LAZY_OUTSIDE_THE_BUNDLE"


# ── what the check did NOT verify, stated without failing ───────────────────


def _signed_entry_repo(tmp_path):
    """A repo whose ledger carries a signature-shaped block, written by hand.

    Hand-written on purpose: this file's subject is the EMITTED check, which
    must behave the same whether the signature is real or not — it cannot tell,
    and that is the whole point of `limits`.
    """
    import json as _json
    repo = tmp_path / "signedrepo"
    d = repo / ".sdlc" / "runs" / "r1"
    d.mkdir(parents=True)
    (d / "state.json").write_text(_json.dumps(
        {"run_id": "r1", "phase": "P4", "status": "running", "artifacts": {}}),
        encoding="utf-8")
    entry = {"schema_version": 1, "kind": "approval", "run_id": "r1", "gate": "G3",
             "verdict": "approve", "approver": "a@b.invalid", "at": "2026-01-01T00:00:00+00:00",
             "artifacts": {}, "prev": ""}
    entry["hash"] = A.entry_hash(entry)
    entry["signature"] = {"dsse": {"payload": "e30=", "payloadType": "x",
                                   "signatures": []},
                          "public_key": {}, "keyid": "deadbeef"}
    (d / "approvals.jsonl").write_text(_json.dumps(entry, sort_keys=True) + "\n",
                                       encoding="utf-8")
    return repo


def test_a_signature_the_check_cannot_verify_is_stated_not_failed(tmp_path,
                                                                  monkeypatch):
    """**The distinction that would otherwise rebuild the defect item 108 fixed.**

    Calling an unverifiable signature a PROBLEM makes every signed ledger fail
    the merge gate forever, leaving `gate_profile: regulated` exactly as
    unusable as it was before envelopes existed — the same dead end one gate
    along. Saying nothing is the silent pass. So: stated every run, exit
    untouched."""
    import builtins
    real = builtins.__import__

    def no_crypto(name, *a, **k):
        if name.startswith("securesystemslib"):
            raise ImportError("pretend it is absent")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_crypto)
    repo = _signed_entry_repo(tmp_path)
    said = V.limits(repo)
    assert said and "nothing verified them" in said[0]
    assert "WERE checked" in said[0], "it must say what it DID check"
    report = V.check(repo)
    assert said[0] not in report["problems"], "a limit must not fail the build"
    assert report["limits"] == said


def test_the_limit_is_silent_when_the_library_is_present(tmp_path):
    """It speaks only when nothing could look. With the library installed the
    signatures are really verified, in `integrity`, where a bad one fails."""
    assert V.limits(_signed_entry_repo(tmp_path)) == []


def test_an_unsigned_ledger_states_no_limit(tmp_path, monkeypatch):
    """Absent is not unverifiable: a repository that never signs must not be
    told something went unchecked."""
    import builtins
    real = builtins.__import__

    def no_crypto(name, *a, **k):
        if name.startswith("securesystemslib"):
            raise ImportError("absent")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_crypto)
    repo = _signed_entry_repo(tmp_path)
    import json as _json
    path = repo / ".sdlc" / "runs" / "r1" / "approvals.jsonl"
    entry = _json.loads(path.read_text(encoding="utf-8").strip())
    del entry["signature"]
    path.write_text(_json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    assert V.limits(repo) == []


# ── a ledger the base has never seen: could-not-look, not clean ──────────────


def _second_run_on_the_branch(repo: Path, run_id: str = "run-beta") -> str:
    """A run created and approved on the branch — the harness's own workflow,
    where the run folder is first committed by the merge that lands it."""
    S.create(repo, "defect", "a second thing", run_id=run_id)
    R.park(repo, run_id, "P7")
    A.record(repo, run_id, "G4", "approve", "dev@example.invalid", notes="ship it")
    return _commit(repo, f"{run_id} + G4")


def test_a_ledger_the_base_has_never_seen_is_stated_not_passed(repo, base, tmp_path):
    """W43's D10: the append-only check walks what the BASE remembers, so a run
    whose folder is created on the branch is not judged clean by it — it is not
    judged at all. Stated in the third channel, exit code untouched."""
    _second_run_on_the_branch(repo)
    bundle = V.emit_check(tmp_path / "ledger_check.py")
    code, out = _run_file(bundle, repo, base, "sdlc/run-beta")
    assert code == 0, out
    assert "  .. [not checked] run-beta: this ledger does not exist at" in out
    assert "cannot be seen from here" in out
    assert "OK - the record holds." in out
    assert "run-alpha" not in out, \
        "run-alpha HAS a base copy: it was compared, so it is not a limit"


def test_a_ledger_the_base_does_have_states_no_such_limit(repo, base):
    """The positive control for the test above: with a base copy present the
    comparison really was made, and nothing may be reported as unchecked."""
    assert V.unseen_at_base(repo, base) == []
    assert V.limits(repo, base) == []
    assert V.check(repo, base, "sdlc/run-alpha")["limits"] == []


def test_the_unseen_limit_is_not_a_problem_and_does_not_fail_the_build(repo, base):
    """Making it a problem would block every first merge this harness makes —
    the same dead end the signature limit was written to avoid."""
    _second_run_on_the_branch(repo)
    report = V.check(repo, base, "sdlc/run-beta")
    assert any("could not be made" in row for row in report["limits"])
    assert report["problems"] == []
    assert report["append_only"] == []
