"""T445a: per-step provider and model - the configuration, INERT by default.

With `model_routing: false` (shipped) nothing resolves a step to a model, reads
a key, or touches a network: the AI coding tool already open does the work. With
it true, `models:` routes each non-acting step to a provider/model from the
`.sdlc/providers.toml` catalog. Every refusal is by name, at step entry.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import models as M  # noqa: E402
from tools.sdlc import stage  # noqa: E402
from tools.tests import _project  # noqa: E402

INSTALLER = ROOT / "sdlc-init.py"


@pytest.fixture(scope="module")
def installed(tmp_path_factory) -> Path:
    """A real install - the shipped profile and catalog, byte for byte."""
    root = tmp_path_factory.mktemp("m") / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], capture_output=True, timeout=120)
    _project.seed(root)
    done = subprocess.run([sys.executable, str(INSTALLER), "--target", str(root),
                           "--no-graph"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)
    assert done.returncode == 0, done.stdout + done.stderr
    return root


def _profile(tmp_path: Path, body: str, catalog: str | None = None) -> Path:
    (tmp_path / ".sdlc").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".sdlc" / "profile.yml").write_text(body, encoding="utf-8")
    if catalog is not None:
        (tmp_path / ".sdlc" / "providers.toml").write_text(catalog, encoding="utf-8")
    return tmp_path


CATALOG = ('[acme]\nprotocol = "openai-chat"\nbase_url = "http://127.0.0.1:1"\n'
           'key_var = "ACME_KEY"\n'
           '[other]\nprotocol = "anthropic-messages"\nbase_url = "http://127.0.0.1:2"\n'
           'key_var = "OTHER_KEY"\n')
ON = "model_routing: true\n"


class _NoTouch(dict):
    """An environment that fails the test if anything reads it."""

    def get(self, *a, **k):
        raise AssertionError("a key was read with routing off")

    __getitem__ = get


# ── the shipped default is real and inert ────────────────────────────────────


def test_the_installed_profile_ships_the_default_dormant(installed):
    assert M.model_routing(installed) is False
    assert M.models_declared(installed) == {"default": "openai/gpt-4o-mini"}
    assert (installed / ".sdlc" / "providers.toml").is_file()


def test_routing_off_resolves_no_step_to_a_model_and_reads_no_key(installed):
    """The dormancy promise: every step is the driving tool's, and an
    environment that explodes on access is never touched."""
    for s in stage.pipeline(installed).steps:
        assert M.model_for(installed, s.name, env=_NoTouch()) == {"actor": M.DRIVING_TOOL}


def test_the_resolver_imports_nothing_that_reaches_a_network():
    """Inert by construction, not only by branch: `models.py` imports no socket,
    http or urllib module, so resolving a step cannot open a connection."""
    tree = ast.parse((ROOT / "tools" / "sdlc" / "models.py").read_text(encoding="utf-8"))
    names = {a.name.split(".")[0] for n in ast.walk(tree)
             if isinstance(n, (ast.Import, ast.ImportFrom))
             for a in (n.names if isinstance(n, ast.Import) else [ast.alias(n.module or "")])}
    assert not names & {"socket", "http", "urllib", "ssl", "requests"}, names


def test_list_names_who_runs_every_step(installed):
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "sdlc" / "stage.py"),
                           "--target", str(installed), "--list"],
                          capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert done.returncode == 0, done.stdout
    rows = done.stdout.split("phases from", 1)[1]
    assert "intake          00-intake.md  [driving tool]" in rows, rows
    assert rows.count("[driving tool]") == len(stage.pipeline(installed).steps)


# ── routing on: resolution order ─────────────────────────────────────────────


def test_default_applies_to_a_document_step(tmp_path):
    t = _profile(tmp_path, ON + "models:\n  default: acme/m-small\n", CATALOG)
    got = M.model_for(t, "requirements")
    assert got["actor"] == M.MODEL_ENDPOINT
    assert (got["provider"], got["model"], got["key_var"]) == ("acme", "m-small", "ACME_KEY")


def test_a_step_entry_beats_the_default(tmp_path):
    t = _profile(tmp_path, ON + "models:\n  default: acme/m-small\n"
                               "  design: other/m-large\n", CATALOG)
    assert M.model_for(t, "design")["provider"] == "other"
    assert M.model_for(t, "requirements")["provider"] == "acme"


def test_an_explicit_runner_beats_models(tmp_path):
    t = _profile(tmp_path, ON + "models:\n  default: acme/m\n"
                 "runners:\n  - name: writer\n    command: \"w\"\n    capabilities: chat\n"
                 "phase_runners:\n  design: writer\n", CATALOG)
    assert M.model_for(t, "design") == {"actor": M.RUNNER, "runner": "writer"}


def test_the_default_never_forces_an_acting_step_onto_an_endpoint(tmp_path):
    t = _profile(tmp_path, ON + "models:\n  default: acme/m\n", CATALOG)
    assert M.model_for(t, "implementation") == {"actor": M.DRIVING_TOOL}


# ── refusals, by name ────────────────────────────────────────────────────────


@pytest.mark.parametrize("body, words", [
    ("models:\n  desgin: acme/m\n", "'desgin', which is not a step"),
    ("models:\n  default: acme\n", "write provider/model"),
    ("model_routing: maybe\n", "model_routing is 'maybe'"),
], ids=["unknown-step", "no-slash", "bad-switch"])
def test_a_malformed_models_block_is_refused_even_with_routing_off(tmp_path, body, words):
    """Checked by `--list` whatever the switch says, so a typo surfaces the day
    it is written - spawned, because the refusal is what a person reads."""
    t = _profile(tmp_path, body)
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "sdlc" / "stage.py"),
                           "--target", str(t), "--list"],
                          capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert done.returncode == 2, done.stdout
    assert words in done.stdout, done.stdout


def test_an_explicit_model_on_an_acting_step_is_refused(tmp_path):
    t = _profile(tmp_path, ON + "models:\n  review: other/m-large\n", CATALOG)
    with pytest.raises(M.ModelError, match="review must ACT.*tools-capable runner"):
        M.model_for(t, "review")


def test_an_unknown_provider_is_refused_naming_the_catalog(tmp_path):
    t = _profile(tmp_path, ON + "models:\n  default: nowhere/m\n", CATALOG)
    with pytest.raises(M.ModelError, match="'nowhere'.*acme, other.*Add a \\[nowhere\\]"):
        M.model_for(t, "requirements")


def test_a_missing_key_is_refused_naming_the_variable_and_env(tmp_path):
    t = _profile(tmp_path, ON + "models:\n  default: acme/m\n", CATALOG)
    with pytest.raises(M.ModelError, match="ACME_KEY is not set.*harness .env"):
        M.model_for(t, "requirements", env={})
    assert M.model_for(t, "requirements", env={"ACME_KEY": "x"})["provider"] == "acme"


def test_a_malformed_catalog_is_refused_by_name(tmp_path):
    t = _profile(tmp_path, ON + "models:\n  default: acme/m\n",
                 '[acme]\nprotocol = "carrier-pigeon"\nbase_url = "x"\nkey_var = "K"\n')
    with pytest.raises(M.ModelError, match="protocol 'carrier-pigeon'"):
        M.model_for(t, "requirements")


# ── no provider is named in the harness's code ───────────────────────────────

#: The one exemption, with its reason: the endpoint runner is where the wire
#: protocols are SPOKEN - it must spell each protocol's own header names
#: (`anthropic-version`) - and it keeps item 104's direct-path defaults for a
#: caller with no catalog. Since T445b a `--provider` call takes everything
#: from the catalog, so no provider choice lives in code.
EXEMPT = {"http_runner.py"}


def _code_strings(path: Path) -> list[tuple[int, str]]:
    """Every string constant that is NOT a docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    getattr(body[0], "value", None), ast.Constant):
                docs.add(id(body[0].value))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs]


def test_no_harness_module_names_a_provider_or_the_default_model():
    """The catalog is data. Derived from the shipped catalog's own provider ids
    and the shipped default model, so a provider added there is guarded here."""
    catalog, _ = M.provider_catalog(Path("/nonexistent-target"))
    banned = set(catalog) | {"gpt-4o-mini"}
    hits = []
    for path in sorted((ROOT / "tools" / "sdlc").glob("*.py")):
        if path.name in EXEMPT:
            continue
        for line, text in _code_strings(path):
            # A PROTOCOL id names a wire format the code must speak, not a
            # provider anyone chose - exempt exactly those strings, nothing else.
            if text in M.PROTOCOLS:
                continue
            low = text.lower()
            hits += [f"{path.name}:{line}: {word!r}" for word in banned
                     if word in low]
    assert not hits, hits


def test_the_provider_guard_can_fail(tmp_path):
    """Positive control: a module that spells a catalog provider is caught."""
    bad = tmp_path / "bad.py"
    bad.write_text('"""A docstring may say openai."""\nURL = "https://api.openai.com"\n',
                   encoding="utf-8")
    assert [t for _, t in _code_strings(bad)] == ["https://api.openai.com"]
