"""The endpoint runner, proven against a real HTTP server. Item 104.

Every test that talks HTTP talks it for real - a stdlib server on loopback
serving OpenAI-shaped responses - because the claim under test is "any
OpenAI-compatible gateway can serve this", and a mock of the module's own
internals would test the mock. The canned credential is entropy-free on
purpose: CI's gitleaks walks git HISTORY, so a key-shaped value outlives the
commit that added it.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import http_runner as H  # noqa: E402

RUNNER = ROOT / "tools" / "sdlc" / "http_runner.py"
#: Deliberately entropy-free and not key-shaped (see module docstring).
CANNED_KEY = "not-a-real-credential-abcdefgh"


class _Gateway(BaseHTTPRequestHandler):
    """An OpenAI-shaped gateway whose next answer the test scripts."""

    reply: dict | str = {}
    status = 200
    seen: list[dict] = []

    def do_POST(self):                                    # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        _Gateway.seen.append({
            "path": self.path,
            "auth": self.headers.get("Authorization", ""),
            "body": body,
        })
        raw = (_Gateway.reply if isinstance(_Gateway.reply, str)
               else json.dumps(_Gateway.reply)).encode("utf-8")
        self.send_response(_Gateway.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):                         # silence the server
        pass


@pytest.fixture(scope="module")
def gateway():
    server = HTTPServer(("127.0.0.1", 0), _Gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """Each test scripts its own gateway and owns its own environment."""
    _Gateway.reply = {"choices": [{"message": {"content": "an answer"}}]}
    _Gateway.status = 200
    _Gateway.seen = []
    for var in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
        monkeypatch.delenv(var, raising=False)


def run_main(monkeypatch, gateway, *argv, key=CANNED_KEY,
             key_var="OPENAI_API_KEY") -> int:
    if key is not None:
        monkeypatch.setenv(key_var, key)
    return H.main(["--base-url", gateway, "--model", "test-model", *argv])


# ── it asks, and what it asks is the contract's shape ───────────────────────


def test_it_prints_the_models_answer_and_exits_zero(monkeypatch, gateway, capsys):
    assert run_main(monkeypatch, gateway, "what touches the api?") == 0
    assert capsys.readouterr().out.strip() == "an answer"


def test_the_request_is_openai_shaped_with_the_prompt_as_the_user_message(
        monkeypatch, gateway):
    run_main(monkeypatch, gateway, "the prompt text")
    (req,) = _Gateway.seen
    assert req["path"].endswith("/chat/completions")
    assert req["auth"] == f"Bearer {CANNED_KEY}"
    assert req["body"]["model"] == "test-model"
    assert req["body"]["messages"][-1] == {"role": "user",
                                           "content": "the prompt text"}


def test_the_prompt_is_the_final_argument_with_flags_before_it(
        monkeypatch, gateway, capsys):
    """RUNNER_CONTRACT section 1: everything before the prompt belongs to the
    runner. A spawner appends the prompt to the declared command."""
    monkeypatch.setenv("OPENAI_API_KEY", CANNED_KEY)
    assert H.main(["--base-url", gateway, "--model", "test-model",
                   "--system", "be brief", "--max-tokens", "64",
                   "the actual task"]) == 0
    (req,) = _Gateway.seen
    assert req["body"]["messages"][0] == {"role": "system", "content": "be brief"}
    assert req["body"]["messages"][1]["content"] == "the actual task"
    assert req["body"]["max_tokens"] == 64


def test_env_supplies_model_and_base_and_a_flag_wins(monkeypatch, gateway, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", CANNED_KEY)
    monkeypatch.setenv("OPENAI_BASE_URL", gateway)
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    assert H.main(["hello"]) == 0
    assert _Gateway.seen[-1]["body"]["model"] == "env-model"
    assert H.main(["--model", "flag-model", "hello"]) == 0
    assert _Gateway.seen[-1]["body"]["model"] == "flag-model"


# ── the credential never rides argv and never reaches output ────────────────


def test_no_flag_on_this_parser_accepts_a_credential_value():
    """The contract forbids a credential in the command line, and the cheapest
    enforcement is that no flag EXISTS to put one in. `--key-var` takes a
    NAME; nothing takes a value."""
    src = RUNNER.read_text(encoding="utf-8")
    for flag in ("--api-key", "--key\"", "--token", "--secret", "--password"):
        assert flag not in src, f"{flag} would carry a credential in argv"


def test_a_missing_key_refuses_by_variable_name_before_any_request(
        monkeypatch, gateway, capsys):
    assert run_main(monkeypatch, gateway, "hi", key=None) == 2
    err = capsys.readouterr().err
    assert "OPENAI_API_KEY" in err and "is not set" in err
    assert _Gateway.seen == [], "it asked the endpoint without a credential"


def test_key_var_names_any_variable_so_no_vendor_is_special(
        monkeypatch, gateway, capsys):
    monkeypatch.setenv("GATEWAY_TOKEN", CANNED_KEY)
    assert H.main(["--base-url", gateway, "--model", "m",
                   "--key-var", "GATEWAY_TOKEN", "hi"]) == 0
    assert _Gateway.seen[-1]["auth"] == f"Bearer {CANNED_KEY}"


def test_an_http_error_names_the_status_and_never_the_key(
        monkeypatch, gateway, capsys):
    """A provider's 401 body is shown (truncated) so somebody can act on it -
    and the key's value must not be in what reaches the console, even if some
    layer echoed it. The scrub covers ANY secret-named variable, because
    --key-var can point at one the default scrub never heard of."""
    _Gateway.status = 401
    _Gateway.reply = {"error": {"message": f"bad token {CANNED_KEY}"}}
    monkeypatch.setenv("GATEWAY_TOKEN", CANNED_KEY)
    assert H.main(["--base-url", gateway, "--model", "m",
                   "--key-var", "GATEWAY_TOKEN", "hi"]) == 2
    err = capsys.readouterr().err
    assert "HTTP 401" in err
    assert CANNED_KEY not in err, "the key's value reached the console"
    assert "<GATEWAY_TOKEN>" in err


def test_a_short_secret_named_value_is_scrubbed_by_name_not_by_a_length_floor(
        gateway, tmp_path):
    """T216: the scrub must key on the NAME, never on a length floor. A secret
    under eight characters, echoed by the endpoint, must not survive to the
    console - a floor decided WHETHER to scrub when its only real job is whether
    a text replacement is safe (`drive.scrub` already re-scrubs unfloored, via
    `provider_smoke.named_secret_values`, and this runner is a supported DIRECT
    entry point since item 104). Spawned, because a CLI's contract is a process
    and this scrub lives on the exit-2 refusal path."""
    import os

    # Assembled at runtime so no secret-shaped literal sits on disk (item 108),
    # entropy-free and not provider-shaped like CANNED_KEY, and DELIBERATELY six
    # characters - the floor's blind spot.
    short = "".join(("a7", "b2", "c9"))
    assert len(short) < 8
    _Gateway.status = 401
    _Gateway.reply = {"error": {"message": f"the gateway rejected {short}"}}

    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = ""
    env["SHORT_TOKEN"] = short
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--base-url", gateway, "--model", "m",
         "--key-var", "SHORT_TOKEN", "answer me"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(tmp_path), env=env, timeout=300)
    assert proc.returncode == 2, f"{proc.stdout}\n{proc.stderr}"
    assert "HTTP 401" in proc.stderr, proc.stderr
    assert short not in proc.stderr, "a short secret-named value reached the console"
    assert "<SHORT_TOKEN>" in proc.stderr, proc.stderr


# ── refusals: an empty answer and no answer must differ ─────────────────────


def test_a_missing_model_refuses_rather_than_guessing_a_bill(
        monkeypatch, gateway, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", CANNED_KEY)
    assert H.main(["--base-url", gateway, "hi"]) == 2
    assert "guessed bill" in capsys.readouterr().err
    assert _Gateway.seen == []


@pytest.mark.parametrize("reply, why", [
    ({"choices": []}, "no choices"),
    ({"nothing": "here"}, "no choices"),
    ({"choices": [{"message": {"content": ""}}]}, "no content"),
    ({"choices": [{"message": {"content": "   "}}]}, "no content"),
    ({"choices": [{"message": {}}]}, "no content"),
    ("not json at all", "not JSON"),
])
def test_an_answer_with_nothing_in_it_is_refused_not_printed(
        monkeypatch, gateway, capsys, reply, why):
    """Exit 0 with blank stdout would hand the phase an answer that said
    nothing and call it one - item 40's shape at the model boundary."""
    _Gateway.reply = reply
    assert run_main(monkeypatch, gateway, "hi") == 2
    out, err = capsys.readouterr()
    assert out.strip() == "", "something was printed as though the model said it"
    assert why in err


def test_an_unreachable_endpoint_is_could_not_ask(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", CANNED_KEY)
    assert H.main(["--base-url", "http://127.0.0.1:9",       # discard port
                   "--model", "m", "--timeout", "2", "hi"]) == 2
    assert "could not be reached" in capsys.readouterr().err


# ── it is genuinely outside ─────────────────────────────────────────────────


def test_it_declares_itself_an_outside_process():
    assert H.OUTSIDE_PROCESS is True


def test_it_runs_as_a_bare_script_with_no_harness_on_the_path(
        gateway, tmp_path, monkeypatch):
    """The strongest form: spawned from a directory that is not the harness,
    PYTHONPATH cleared, credential in the environment the way a spawner
    supplies it - the exact shape `provider_smoke --runner` produces."""
    import os

    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = ""
    env["OPENAI_API_KEY"] = CANNED_KEY
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--base-url", gateway,
         "--model", "test-model", "answer me"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(tmp_path), env=env, timeout=300)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert proc.stdout.strip() == "an answer"


def test_its_green_is_bounded_the_way_section_8_bounds_the_reference_runner():
    """A green run is a green ENDPOINT, never a green agent workflow - kept in
    the module where somebody reading it will meet it, like the consumer's."""
    doc = H.__doc__ or ""
    assert "never a green agent" in doc
    assert "chat" in doc, "the capability under item 105's vocabulary is unstated"
