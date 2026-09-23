"""T446b: each spawned step's time, runner, model, tokens and cost - as reported.

The driver writes `step_started`/`step_finished` around every spawn; a runner
may report usage into `SDLC_USAGE_FILE` (the endpoint runner always does); no
report is `unavailable` with a reason, never 0; cost only where a price is
declared; a driving tool may self-report through `stage.py --report-usage`.
No paid call: fake runners, a loopback stub, and refusals.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import drive as D  # noqa: E402
from tools.sdlc import events as E  # noqa: E402
from tools.sdlc import http_runner as H  # noqa: E402
from tools.sdlc import intake as entry  # noqa: E402
from tools.sdlc import stage as S  # noqa: E402

RUNNER_CMD = '"' + sys.executable + '" -c pass'
SECRET = "sk-live-not-a-real-credential-planted"


def _repo(tmp_path: Path, extra: str = "", catalog: str | None = None) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(
        "stack: python\ngate_profile: solo\n"
        "phases: [intake, requirements, design, implementation, test, review, "
        "triage, closeout]\n" + extra +
        "runners:\n  - name: writer\n"
        f"    command: '{RUNNER_CMD}'\n    capabilities: chat\n"
        "phase_runners:\n  intake: writer\n", encoding="utf-8")
    if catalog:
        (r / ".sdlc" / "providers.toml").write_text(catalog, encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "i",
                  "--allow-empty"]):
        subprocess.run(["git", "-C", str(r), *args], check=True, capture_output=True)
    return r


def _run(repo: Path) -> str:
    return entry.open_run(repo, "title: a change\n\nkind: requirement\n\n"
                                "## What\n\nA small change.\n")["run_id"]


def runner(usage: list[dict] | None = None, sleep: float = 0.0, text: str = "doc\n"):
    """A fake runner that writes usage where the harness said to, as a real one would."""
    def run(command, prompt, cwd, timeout=3600):
        time.sleep(sleep)
        path = os.environ.get(E.USAGE_ENV)
        if usage and path:
            with open(path, "a", encoding="utf-8") as fh:
                for row in usage:
                    fh.write(json.dumps(row) + "\n")
        return 0, text
    return run


def _step_events(repo: Path, rid: str) -> tuple[dict, dict]:
    rows = E.read(repo, rid)
    return (next(r for r in rows if r["event"] == E.STEP_STARTED),
            next(r for r in rows if r["event"] == E.STEP_FINISHED))


def test_a_spawned_step_is_timed_and_its_reported_tokens_round_trip(tmp_path):
    repo = _repo(tmp_path)
    rid = _run(repo)
    got = D.step(repo, rid, {}, run=runner(
        [{"provider": "x", "model": "m", "input_tokens": 1200, "output_tokens": 340,
          "cached_tokens": 100}], sleep=0.05))
    assert got["advanced_to"] == "P1", got
    started, finished = _step_events(repo, rid)
    assert (started["step"], started["attempt"]) == ("P0", 1)
    assert finished["duration_ms"] >= 50, "at least the runner's forced sleep"
    assert finished["tokens"] == {"source": "reported", "calls": 1, "input": 1200,
                                  "output": 340, "cached": 100, "total": 1540}
    assert (finished["actor"], finished["runner"], finished["outcome"]) == (
        "runner", "writer", "advanced")
    assert finished["cost"]["source"] == "unpriced"


def test_a_runner_that_reports_nothing_is_unavailable_never_zero(tmp_path):
    repo = _repo(tmp_path)
    rid = _run(repo)
    D.step(repo, rid, {}, run=runner())
    _, finished = _step_events(repo, rid)
    assert finished["tokens"] == {"source": "unavailable",
                                  "reason": "runner reported no usage"}
    assert "0" not in json.dumps(finished["tokens"]), "no invented zero anywhere"


def test_a_declared_price_prices_a_model_step(tmp_path, monkeypatch):
    catalog = ('[acme]\nprotocol = "openai-chat"\nbase_url = "http://127.0.0.1:1"\n'
               'key_var = "ACME_TEST_KEY"\nprice_in_per_mtok = 2.0\n'
               'price_out_per_mtok = 8.0\nprice_currency = "USD"\n')
    repo = _repo(tmp_path, "model_routing: true\nmodels:\n  default: acme/m-small\n",
                 catalog)
    rid = _run(repo)
    monkeypatch.setenv("ACME_TEST_KEY", "x")
    D.step(repo, rid, {}, run=runner())                   # P0 by the writer
    D.step(repo, rid, {}, run=runner(
        [{"input_tokens": 1_000_000, "output_tokens": 500_000}]))
    finished = [r for r in E.read(repo, rid) if r["event"] == E.STEP_FINISHED][-1]
    assert (finished["actor"], finished["provider"], finished["model"]) == (
        "model-endpoint", "acme", "m-small")
    assert finished["protocol"] == "openai-chat"
    assert finished["cost"] == {"source": "priced", "amount": 6.0, "currency": "USD"}


def test_a_runners_output_never_reaches_the_trail(tmp_path):
    repo = _repo(tmp_path)
    rid = _run(repo)
    D.step(repo, rid, {}, run=runner(text=f"the answer, with {SECRET} in it\n"))
    assert SECRET not in E.events_path(repo, rid).read_text(encoding="utf-8")


def test_the_step_events_validate_against_the_schema(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ROOT / "sdlc_templates" / "schemas" / "events.schema.json")
                        .read_text(encoding="utf-8"))
    repo = _repo(tmp_path)
    rid = _run(repo)
    D.step(repo, rid, {}, run=runner([{"input_tokens": 5, "output_tokens": 2}]))
    for row in E.read(repo, rid):
        jsonschema.validate(row, schema)
    assert E.verify(repo, rid) == []


# ── the endpoint runner writes usage ─────────────────────────────────────────


class _Stub(BaseHTTPRequestHandler):
    reply: dict = {}

    def do_POST(self):                                    # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        raw = json.dumps(_Stub.reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def stub():
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.mark.parametrize("protocol, reply, want", [
    ("openai-chat",
     {"choices": [{"message": {"content": "a"}}],
      "usage": {"prompt_tokens": 11, "completion_tokens": 7,
                "prompt_tokens_details": {"cached_tokens": 3}}},
     {"input_tokens": 11, "output_tokens": 7, "cached_tokens": 3}),
    ("anthropic-messages",
     {"content": [{"type": "text", "text": "a"}],
      "usage": {"input_tokens": 13, "output_tokens": 5, "cache_read_input_tokens": 2}},
     {"input_tokens": 13, "output_tokens": 5, "cached_tokens": 2}),
], ids=["openai-chat", "anthropic-messages"])
def test_the_endpoint_runner_writes_the_providers_usage(stub, tmp_path, monkeypatch,
                                                        protocol, reply, want):
    _Stub.reply = reply
    path = tmp_path / "usage.jsonl"
    monkeypatch.setenv(H.USAGE_ENV, str(path))
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-credential")
    assert H.main(["--protocol", protocol, "--base-url", stub, "--model", "m", "p"]) == 0
    (line,) = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(line) == {"provider": "", "model": "m", **want}


def test_no_usage_in_the_response_writes_nothing(stub, tmp_path, monkeypatch):
    _Stub.reply = {"choices": [{"message": {"content": "a"}}]}
    path = tmp_path / "usage.jsonl"
    monkeypatch.setenv(H.USAGE_ENV, str(path))
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-credential")
    assert H.main(["--base-url", stub, "--model", "m", "p"]) == 0
    assert not path.exists(), "an absent count is absent, never a written zero"


def test_the_runner_and_the_harness_name_one_variable():
    """OUTSIDE_PROCESS forbids the import, so the spelling is welded by test."""
    assert H.USAGE_ENV == E.USAGE_ENV


# ── a driving tool self-reports ──────────────────────────────────────────────


def _stage(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "tools" / "sdlc" / "stage.py"),
                           *args], capture_output=True, text=True, encoding="utf-8",
                          timeout=300)


def test_report_usage_records_a_self_reported_step(tmp_path):
    repo = _repo(tmp_path)
    rid = _run(repo)
    done = _stage("--target", str(repo), "--run", rid, "--phase", "P0",
                  "--report-usage", "input=900,output=120")
    assert done.returncode == 0, done.stdout
    finished = E.last(repo, rid, E.STEP_FINISHED)
    assert finished["actor"] == "driving-tool" and finished["outcome"] == "self-reported"
    assert finished["tokens"]["source"] == "self-reported"
    assert (finished["tokens"]["input"], finished["tokens"]["total"]) == (900, 1020)


@pytest.mark.parametrize("counts", ["input=lots", "tokens=5", "input"])
def test_report_usage_refuses_what_it_cannot_read(tmp_path, counts):
    repo = _repo(tmp_path)
    rid = _run(repo)
    done = _stage("--target", str(repo), "--run", rid, "--phase", "P0",
                  "--report-usage", counts)
    assert done.returncode == 2 and "whole numbers" in done.stdout, done.stdout
    assert E.last(repo, rid, E.STEP_FINISHED) is None
