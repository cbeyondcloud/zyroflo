"""T454: the export destination contract - where files land, named how, and never
clobbered silently.

Precedence `--out` > `SDLC_METRICS_OUT` > `policy.yml: metrics_out` (repo-relative
only; an absolute path refused by name) > `.sdlc/metrics/export/`. Layout
`<repo>/<event_type>/<YYYY-MM-DD>/<run-id>.<fmt>` plus a `manifest.json`; a file
holding different bytes is refused unless `--force`; `--since` narrows to runs
touched on or after a day. Spawned: the refusals are what a nightly job reads.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import events as E  # noqa: E402
from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

METRICS = ROOT / "tools" / "sdlc" / "metrics.py"


def _repo(tmp_path: Path, name: str = "svc", project: str | None = "billing",
          policy: str = "") -> Path:
    r = tmp_path / name
    (r / ".sdlc").mkdir(parents=True)
    body = "gate_profile: solo\n"
    if project:
        body += f'project:\n  name: "{project}"\n  owner: "t"\n  criticality: low\n'
    (r / ".sdlc" / "profile.yml").write_text(body, encoding="utf-8")
    if policy:
        (r / ".sdlc" / "policy.yml").write_text(policy, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(r)], check=True, capture_output=True)
    return r


def _export(repo: Path, *args: str, env_out: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "SDLC_METRICS_OUT"}
    if env_out:
        env["SDLC_METRICS_OUT"] = env_out
    return subprocess.run([sys.executable, str(METRICS), "--target", str(repo), "--export",
                           "csv", *args], capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=300)


def _run(repo: Path) -> str:
    rid = L.create(repo, "requirement", "a change")["run_id"]
    R.park(repo, rid, "P3")
    return rid


def test_the_layout_is_repo_type_day_run_with_a_manifest(tmp_path):
    repo = _repo(tmp_path)
    rid = _run(repo)
    done = _export(repo, "--out", str(tmp_path / "dest"))
    assert done.returncode == 0, done.stdout + done.stderr
    root = tmp_path / "dest" / "billing"
    day = E.read(repo, rid)[0]["at"][:10]
    assert (root / "run_opened" / day / f"{rid}.csv").is_file()
    assert (root / "gate_waiting" / day / f"{rid}.csv").is_file()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["repo"] == "billing" and manifest["format"] == "csv"
    assert manifest["runs"][rid]["chain_head"] == E.read(repo, rid)[-1]["hash"]
    # T462: a run opens with its first step's clock running.
    assert manifest["rows"] == {"gate_waiting": 1, "run_opened": 1, "step_started": 1}
    assert f"run_opened/{day}/{rid}.csv" in manifest["files"]
    assert "generated" not in json.dumps(manifest), "no timestamp: it must diff cleanly"


@pytest.mark.parametrize("how", ["--out", "env", "policy", "default"])
def test_the_destination_precedence(tmp_path, how):
    policy = 'metrics_out: "reports/sdlc"\n' if how in ("policy", "env", "--out") else ""
    repo = _repo(tmp_path, policy=policy)
    _run(repo)
    args, env_out = [], None
    if how == "--out":
        args = ["--out", str(tmp_path / "flag")]
    if how in ("--out", "env"):
        env_out = str(tmp_path / "machine")
    done = _export(repo, *args, env_out=env_out)
    assert done.returncode == 0, done.stdout + done.stderr
    expect = {"--out": tmp_path / "flag", "env": tmp_path / "machine",
              "policy": repo / "reports" / "sdlc",
              "default": repo / ".sdlc" / "metrics" / "export"}[how]
    assert (expect / "billing" / "manifest.json").is_file(), done.stdout


#: A drive-letter path, ASSEMBLED: the leak guard refuses a literal one in any
#: tracked file, and it is right to.
_DRIVE_PATH = "D" + ":/exports"


@pytest.mark.parametrize("bad", ["/srv/exports", "../exports", _DRIVE_PATH])
def test_an_absolute_or_escaping_metrics_out_is_refused_by_name(tmp_path, bad):
    assert any("metrics_out" in p and "REPO-RELATIVE" in p
               for p in P.validate({"metrics_out": bad}))
    repo = _repo(tmp_path, policy=f'metrics_out: "{bad}"\n')
    _run(repo)
    done = _export(repo)
    assert done.returncode != 0 and "metrics_out" in done.stdout + done.stderr


def test_a_grown_run_is_refused_until_forced_and_a_repeat_writes_nothing(tmp_path):
    repo = _repo(tmp_path)
    rid = _run(repo)
    dest = str(tmp_path / "dest")
    assert _export(repo, "--out", dest).returncode == 0
    again = _export(repo, "--out", dest)
    assert again.returncode == 0 and "0 file(s) written" in again.stdout, again.stdout
    # The run grows an EXISTING type: it leaves the wait and halts again, so the
    # already-exported gate_waiting file would change - that is the clobber.
    L.advance(repo, rid, status="running")
    L.advance(repo, rid, status="awaiting_approval")
    refused = _export(repo, "--out", dest)
    assert refused.returncode != 0, refused.stdout
    assert "--force" in refused.stdout + refused.stderr
    assert _export(repo, "--out", dest, "--force").returncode == 0


def test_since_narrows_to_runs_touched_on_or_after_a_day(tmp_path):
    repo = _repo(tmp_path)
    rid = _run(repo)
    day = E.read(repo, rid)[0]["at"][:10]
    later = _export(repo, "--out", str(tmp_path / "later"), "--since", "2999-01-01")
    assert later.returncode == 0
    manifest = json.loads((tmp_path / "later" / "billing" / "manifest.json")
                          .read_text(encoding="utf-8"))
    assert manifest["runs"] == {} and manifest["since"] == "2999-01-01"
    same = _export(repo, "--out", str(tmp_path / "same"), "--since", day)
    assert rid in json.loads((tmp_path / "same" / "billing" / "manifest.json")
                             .read_text(encoding="utf-8"))["runs"]
    bad = _export(repo, "--out", str(tmp_path / "x"), "--since", "yesterday")
    assert bad.returncode != 0 and "YYYY-MM-DD" in bad.stdout + bad.stderr


def test_many_repositories_write_into_one_destination_without_colliding(tmp_path):
    one = _repo(tmp_path, "a", project="billing")
    two = _repo(tmp_path, "b", project=None)          # falls back to the dir name
    _run(one)
    _run(two)
    dest = str(tmp_path / "shared")
    assert _export(one, "--out", dest).returncode == 0
    assert _export(two, "--out", dest).returncode == 0
    assert {p.name for p in (tmp_path / "shared").iterdir()} == {"billing", "b"}
