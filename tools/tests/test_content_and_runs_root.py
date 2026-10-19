"""Z2 and Z3: the harness used as an installed dependency by a framework.

Z2 - lifecycle content ships in the wheel. `sdlc/`, `sdlc_templates/` and
`third_party/manifest.json` sit at the checkout root, outside the `tools`
package, so an installed harness had no stage SOPs, templates or rubric.
They are now copied into the wheel under `tools/_content/`, and
`tools.sdlc.content.root()` answers where they are - the packaged copy when
installed, the checkout otherwise (unchanged). An installed harness also
names its version from its install record, never from the host repository.

Z3 - the run-folder root is a setting. A framework that keeps its runs out of
the committed tree (agentic-sdlc: `.agentic/local/runs/`, git-ignored) sets it
once per process with `state.set_runs_root`; child processes inherit it via
`SDLC_RUNS_ROOT`; the default stays `.sdlc/runs`.

And the clean-checkout check (`intake.dirty_paths`) exempts the run folders
wherever they are, including when `.sdlc/` is wholly untracked - git reports
that as ONE `?? .sdlc/` entry, which the `.sdlc/runs` prefix never matched.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import content as C  # noqa: E402
from tools.sdlc import intake as I  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.sdlc import suite as SU  # noqa: E402
from tools.sdlc import worktree as W  # noqa: E402

DEV = ["-c", "user.email=dev@x.invalid", "-c", "user.name=Dev"]


def git(where: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(where), *DEV, *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return proc.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with NOTHING committed under `.sdlc/` - a framework's repo."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    return root


@pytest.fixture(autouse=True)
def default_runs_root():
    yield
    L.set_runs_root(None)


def python_in_child(code: str, **env: str) -> subprocess.CompletedProcess:
    full = {k: v for k, v in os.environ.items() if k != L.RUNS_ROOT_ENV}
    full.update(env)
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=full,
                          capture_output=True, text=True, timeout=120)


# ── Z2: content in a checkout and in the wheel ───────────────────────────────


def test_in_a_checkout_the_content_root_is_the_checkout():
    assert not C.installed()
    assert C.root() == ROOT
    assert (C.root() / "sdlc" / "stages" / "P0-intake.md").is_file()
    assert (C.root() / "sdlc_templates" / "review-rubric.md").is_file()
    assert (C.root() / "third_party" / "manifest.json").is_file()


def test_installed_the_packaged_copy_wins_even_over_a_foreign_sdlc_folder(tmp_path, monkeypatch):
    packaged, foreign = tmp_path / "_content", tmp_path / "site-packages"
    (packaged / "sdlc" / "stages").mkdir(parents=True)
    (foreign / "sdlc" / "stages").mkdir(parents=True)  # another package's folder
    monkeypatch.setattr(C, "PACKAGED", packaged)
    monkeypatch.setattr(C, "CHECKOUT", foreign)

    assert C.installed() and C.root() == packaged


def test_the_packaged_copy_sits_inside_the_tools_package():
    assert C.PACKAGED == ROOT / "tools" / "_content"


def test_the_wheel_ships_the_content_at_the_packaged_root():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    shipped = meta["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert shipped == {
        "sdlc": "tools/_content/sdlc",
        "sdlc_templates": "tools/_content/sdlc_templates",
        "third_party/manifest.json": "tools/_content/third_party/manifest.json",
    }


def test_the_stage_reader_finds_its_sops_through_the_content_root(tmp_path, monkeypatch):
    for stage in ST.STAGES:
        assert ST.sop_path(stage).is_file(), stage.phase
    monkeypatch.setattr(C, "root", lambda: tmp_path)

    assert ST.sop_path(ST.STAGES[0]).parent == tmp_path / "sdlc" / "stages"


def test_the_harness_path_for_prompts_is_still_the_code_root():
    assert ST.HARNESS == ROOT  # `<harness>/tools/sdlc/stage.py` in an SOP must exist


# ── Z2: the version an installed harness records ─────────────────────────────


class FakeDist:
    version = "0.1.0"

    def __init__(self, direct_url: dict | None):
        self.direct_url = direct_url

    def read_text(self, name: str):
        return json.dumps(self.direct_url) if name == "direct_url.json" and self.direct_url else None


def test_installed_from_git_the_version_is_the_installed_commit(monkeypatch):
    commit = "a" * 40
    monkeypatch.setattr(C, "installed", lambda: True)
    monkeypatch.setattr(L, "_harness_distribution",
                        lambda: FakeDist({"url": "https://x/h.git", "vcs_info": {"commit_id": commit}}))

    assert L.harness_version() == commit


def test_installed_otherwise_the_version_is_the_package_version(monkeypatch):
    monkeypatch.setattr(C, "installed", lambda: True)
    monkeypatch.setattr(L, "_harness_distribution", lambda: FakeDist(None))

    assert L.harness_version() == "sdlc-harness==0.1.0"


def test_installed_and_unrecorded_the_version_is_unknown_never_the_hosts_commit(monkeypatch):
    monkeypatch.setattr(C, "installed", lambda: True)
    monkeypatch.setattr(L, "_harness_distribution", lambda: None)

    assert L.harness_version() == "unknown"


# ── Z3: the run-folder root ──────────────────────────────────────────────────


def test_the_default_runs_root_is_unchanged(repo):
    assert L.runs_dir(repo) == repo / ".sdlc" / "runs"


def test_a_framework_moves_every_run_folder_with_one_setting(repo):
    L.set_runs_root(".agentic/local/runs")
    rid = L.create(repo, "requirement", "a change")["run_id"]

    folder = repo / ".agentic" / "local" / "runs" / rid
    assert (folder / L.STATE_NAME).is_file() and (folder / "events.jsonl").is_file()
    assert L.run_rel(repo, rid) == f".agentic/local/runs/{rid}"
    assert not (repo / ".sdlc" / "runs").exists()


def test_none_restores_the_default(repo):
    L.set_runs_root(".agentic/local/runs")
    L.set_runs_root(None)

    assert L.runs_dir(repo) == repo / ".sdlc" / "runs"


def test_child_processes_inherit_the_setting():
    L.set_runs_root(".agentic/local/runs")
    child = python_in_child("from tools.sdlc import state; print(state.RUNS_REL.as_posix())",
                            **{L.RUNS_ROOT_ENV: os.environ[L.RUNS_ROOT_ENV]})

    assert child.stdout.strip() == ".agentic/local/runs"
    L.set_runs_root(None)
    assert L.RUNS_ROOT_ENV not in os.environ


def test_a_bad_inherited_root_is_refused_by_name():
    child = python_in_child("from tools.sdlc import state", **{L.RUNS_ROOT_ENV: "../escape"})

    assert child.returncode != 0 and L.RUNS_ROOT_ENV in child.stderr


@pytest.mark.parametrize("bad", ["", "/abs/runs", "C:runs", "../runs", "a/../../runs",
                                 ".sdlc\\..\\..\\runs", ".", 7, ".git/runs", "a/.GIT/runs",
                                 "CON/runs", "runs/nul.txt", "a./b", "a /b", "a<b", "a|b",
                                 "a?b", "a*b", 'a"b', "x" * 101, "/".join(["abcdefghij"] * 21),
                                 "con.tar.gz/runs"])
def test_a_root_outside_the_target_or_unsafe_on_windows_is_refused(bad):
    with pytest.raises(ValueError):
        L.set_runs_root(bad)
    assert L.RUNS_REL == Path(".sdlc") / "runs"


# ── the clean-checkout check ─────────────────────────────────────────────────


def test_a_wholly_untracked_sdlc_holding_only_runs_is_clean(repo):
    L.create(repo, "requirement", "a change")

    assert I.dirty_paths(repo) == ([], "")


def test_a_run_tree_can_be_made_in_a_repo_with_no_committed_sdlc(repo):
    rid = L.create(repo, "requirement", "a change")["run_id"]

    record = W.create(repo, rid)
    assert record["made"] == [repo.name]


def test_the_exemption_follows_the_runs_root(repo):
    L.set_runs_root(".agentic/local/runs")
    L.create(repo, "requirement", "a change")

    assert I.dirty_paths(repo) == ([], "")


def test_a_non_ascii_runs_root_is_still_exempt(repo):
    L.set_runs_root("données/runs")
    L.create(repo, "requirement", "a change")

    assert I.dirty_paths(repo) == ([], "")


def test_a_moved_root_holding_tracked_files_is_refused_not_exempted(repo):
    L.set_runs_root("src")  # a mistake: this is source code, not run folders
    (repo / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")

    dirty, why = I.dirty_paths(repo)
    assert dirty is None and "tracked" in why and "src" in why


def test_other_untracked_files_still_make_the_checkout_dirty(repo):
    L.create(repo, "requirement", "a change")
    (repo / ".sdlc" / "profile.yml").write_text("stack: python\n", encoding="utf-8")
    (repo / "stray.txt").write_text("not committed\n", encoding="utf-8")

    dirty, why = I.dirty_paths(repo)
    assert why == "" and sorted(dirty) == [".sdlc/profile.yml", "stray.txt"]


def test_the_suite_treats_moved_run_folders_as_harness_state():
    L.set_runs_root(".agentic/local/runs")

    assert SU.harness_state(".agentic/local/runs/r1/state.json")
    assert SU.harness_state("svc/.sdlc/metrics/m.jsonl")  # the other records are unchanged
    assert not SU.harness_state("src/app.py")


def test_a_one_word_runs_root_never_hides_code_of_the_same_name():
    L.set_runs_root("runs")

    assert SU.harness_state("runs/r1/state.json")
    assert not SU.harness_state("src/runs/handler.py")  # real code that shares the name


def test_a_dot_folder_runs_root_is_found_inside_each_component():
    L.set_runs_root(".agentic/local/runs")

    assert SU.harness_state("svc/.agentic/local/runs/r1/state.json")
    assert not SU.harness_state("svc/x.agentic/local/runs/r1/state.json")


def test_tracked_files_in_a_moved_root_are_found_from_a_subfolder(repo):
    (repo / "rr").mkdir()
    (repo / "rr" / ".gitkeep").write_text("", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "a tracked file in what becomes the runs root")
    L.set_runs_root("rr")

    dirty, why = I.dirty_paths(repo / "src")  # asked from a component's subfolder
    assert dirty is None and "tracked" in why
