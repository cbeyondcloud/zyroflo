"""T445b: the endpoint runner speaks a second protocol and reads the catalog.

`--protocol anthropic-messages` is a second adapter beside item 104's
`openai-chat`; `--provider NAME` takes protocol, base URL and key variable from
T445a's `providers.toml`. Proven against a real loopback HTTP server, never a
paid call (ground rule 2). The canned key is entropy-free on purpose: CI's
gitleaks walks history.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import http_runner as H  # noqa: E402
from tools.sdlc import models as M  # noqa: E402

RUNNER = ROOT / "tools" / "sdlc" / "http_runner.py"
CANNED_KEY = "not-a-real-credential-abcdefgh"


class _Stub(BaseHTTPRequestHandler):
    """Answers in whichever protocol the request path asks for."""

    seen: list[dict] = []
    reply: dict | None = None

    def do_POST(self):                                    # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        _Stub.seen.append({"path": self.path, "headers": dict(self.headers),
                           "body": body})
        if _Stub.reply is not None:
            reply = _Stub.reply
        elif self.path.endswith("/messages"):
            reply = {"content": [{"type": "text", "text": "a messages answer"}]}
        else:
            reply = {"choices": [{"message": {"content": "a chat answer"}}]}
        raw = json.dumps(reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def stub():
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    _Stub.seen, _Stub.reply = [], None
    for var in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "STUB_KEY"):
        monkeypatch.delenv(var, raising=False)


def _catalog(tmp_path: Path, stub: str, protocol: str = "anthropic-messages") -> Path:
    path = tmp_path / "providers.toml"
    path.write_text(f'[stubco]\nprotocol = "{protocol}"\nbase_url = "{stub}"\n'
                    f'key_var = "STUB_KEY"\n', encoding="utf-8")
    return path


# ── the messages protocol ────────────────────────────────────────────────────


def test_the_messages_request_has_the_protocols_shape(monkeypatch, stub, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", CANNED_KEY)
    assert H.main(["--protocol", "anthropic-messages", "--base-url", stub,
                   "--model", "m-large", "--system", "be brief", "the prompt"]) == 0
    assert capsys.readouterr().out.strip() == "a messages answer"
    (req,) = _Stub.seen
    headers = {k.lower(): v for k, v in req["headers"].items()}
    assert req["path"].endswith("/messages")
    assert headers["x-api-key"] == CANNED_KEY and "authorization" not in headers
    assert headers["anthropic-version"] == H.MESSAGES_VERSION
    assert req["body"] == {"model": "m-large",
                           "max_tokens": H.MESSAGES_DEFAULT_MAX_TOKENS,
                           "system": "be brief",
                           "messages": [{"role": "user", "content": "the prompt"}]}


def test_text_blocks_are_joined_and_other_blocks_ignored(monkeypatch, stub, capsys):
    _Stub.reply = {"content": [{"type": "text", "text": "one "},
                               {"type": "tool_use", "id": "x"},
                               {"type": "text", "text": "two"}]}
    monkeypatch.setenv("OPENAI_API_KEY", CANNED_KEY)
    assert H.main(["--protocol", "anthropic-messages", "--base-url", stub,
                   "--model", "m", "p"]) == 0
    assert capsys.readouterr().out.strip() == "one two"


@pytest.mark.parametrize("reply, words", [
    ({"content": []}, "no text"),
    ({"content": [{"type": "text", "text": "  "}]}, "no text"),
    ({"id": "x"}, "no content blocks"),
], ids=["empty-list", "blank-text", "no-content"])
def test_an_empty_messages_answer_is_refused_not_printed(monkeypatch, stub, capsys,
                                                          reply, words):
    _Stub.reply = reply
    monkeypatch.setenv("OPENAI_API_KEY", CANNED_KEY)
    assert H.main(["--protocol", "anthropic-messages", "--base-url", stub,
                   "--model", "m", "p"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and words in captured.err, captured.err


# ── --provider reads the catalog ─────────────────────────────────────────────


def test_provider_supplies_protocol_base_and_key_var(monkeypatch, stub, tmp_path, capsys):
    monkeypatch.setenv("STUB_KEY", CANNED_KEY)
    assert H.main(["--provider", "stubco", "--catalog", str(_catalog(tmp_path, stub)),
                   "--model", "m", "p"]) == 0
    assert capsys.readouterr().out.strip() == "a messages answer"
    (req,) = _Stub.seen
    assert {k.lower(): v for k, v in req["headers"].items()}["x-api-key"] == CANNED_KEY


def test_an_explicit_flag_beats_the_catalog(monkeypatch, stub, tmp_path, capsys):
    monkeypatch.setenv("STUB_KEY", CANNED_KEY)
    catalog = _catalog(tmp_path, "http://127.0.0.1:1")     # a dead address
    assert H.main(["--provider", "stubco", "--catalog", str(catalog),
                   "--base-url", stub, "--protocol", "openai-chat",
                   "--model", "m", "p"]) == 0
    assert capsys.readouterr().out.strip() == "a chat answer"


def test_the_provider_path_names_its_own_key_when_missing(monkeypatch, stub, tmp_path,
                                                          capsys):
    """The catalog's key_var, not item 104's default, and no request is sent."""
    assert H.main(["--provider", "stubco", "--catalog", str(_catalog(tmp_path, stub)),
                   "--model", "m", "p"]) == 2
    assert "STUB_KEY is not set" in capsys.readouterr().err and _Stub.seen == []


def test_the_provider_path_never_guesses_a_model_from_the_openai_env(
        monkeypatch, stub, tmp_path, capsys):
    """OPENAI_MODEL is item 104's convenience for ITS path; a named provider
    that inherited it would bill one vendor's model name to another."""
    monkeypatch.setenv("STUB_KEY", CANNED_KEY)
    monkeypatch.setenv("OPENAI_MODEL", "some-model")
    assert H.main(["--provider", "stubco", "--catalog", str(_catalog(tmp_path, stub)),
                   "p"]) == 2
    assert "no model named - pass --model." in capsys.readouterr().err


@pytest.mark.parametrize("body, words", [
    (None, "does not exist"),
    ('[other]\nprotocol = "openai-chat"\nbase_url = "x"\nkey_var = "K"\n',
     "declares no provider 'stubco' - one of other"),
    ('[stubco]\nprotocol = "smoke-signals"\nbase_url = "x"\nkey_var = "K"\n',
     "protocol 'smoke-signals'"),
    ('[stubco]\nprotocol = "openai-chat"\nbase_url = "x"\n', "needs key_var"),
], ids=["no-file", "no-provider", "bad-protocol", "no-key-var"])
def test_a_catalog_miss_is_refused_by_name(monkeypatch, tmp_path, capsys, body, words):
    path = tmp_path / "providers.toml"
    if body is not None:
        path.write_text(body, encoding="utf-8")
    assert H.main(["--provider", "stubco", "--catalog", str(path),
                   "--model", "m", "p"]) == 2
    assert words in capsys.readouterr().err


def test_the_runner_and_the_resolver_speak_the_same_protocols():
    """One vocabulary: a protocol the catalog may name is one this runner speaks."""
    assert tuple(H.PROTOCOLS) == tuple(M.PROTOCOLS)


def test_the_shipped_catalog_is_readable_by_the_runner():
    """The harness's own providers.toml parses through the runner's reader."""
    for provider in ("openai", "google", "anthropic"):
        assert H.from_catalog(provider, str(M.HARNESS_CATALOG))["protocol"] in H.PROTOCOLS


def test_it_runs_as_a_bare_script_with_a_provider(stub, tmp_path):
    """Spawned with no harness on the path, as a runner is: OUTSIDE_PROCESS."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["STUB_KEY"] = CANNED_KEY
    done = subprocess.run([sys.executable, str(RUNNER), "--provider", "stubco",
                           "--catalog", str(_catalog(tmp_path, stub)),
                           "--model", "m", "the prompt"],
                          capture_output=True, text=True, encoding="utf-8",
                          cwd=str(tmp_path), env=env, timeout=120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "a messages answer"
