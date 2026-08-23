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
    """`state.list_runs` parses every state file on the way past and raises on a
    bad one. A required check that dies tells a reviewer nothing about which run
    is wrong, so this gate enumerates folders instead."""
    (S.runs_dir(repo) / "run-beta").mkdir()
    (S.runs_dir(repo) / "run-beta" / S.STATE_NAME).write_text("{not json",
                                                              encoding="utf-8")
    with pytest.raises(ValueError):
        S.list_runs(repo)  # the premise: the shared helper really does raise
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
