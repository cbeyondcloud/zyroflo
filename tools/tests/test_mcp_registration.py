"""Item 23 — INIT registers the graph server in `.mcp.json`, and owns one key.

The registration is the half of item 23 the installer carries (the estate-mode
docstring deferred it here by name): after a chained build that produced a graph,
`<target>/.mcp.json` gains the `sdlc-graph` server so AI tools reading that file
serve the graph on their next start. The file is MERGED — a developer's other
servers and top-level keys survive byte-meaning-for-byte-meaning — and a file that
cannot be parsed as an MCP config is left untouched and named, never overwritten.

The spawn discipline is item 80's, made permanent: the entry's command is the
absolute uv path, because the AI tool spawns it with its working directory at the
TARGET repo — exactly where Windows sends a bare program name to be found first.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "sdlc-init.py"
FIXTURE_WS = ROOT / "tools" / "graph" / "tests" / "fixtures" / "orderflow" / "workspace.toml"


def load_installer():
    spec = importlib.util.spec_from_file_location("sdlc_init_mcp", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init_mcp"] = mod
    spec.loader.exec_module(mod)
    return mod


si = load_installer()

assert si.find_uv(), (
    "uv is not on PATH; this suite asserts on the registered absolute uv path and "
    "needs one to exist. `uv run pytest` guarantees it.")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / "pom.xml").write_text(
        "<project><artifactId>demo</artifactId></project>\n", encoding="utf-8")
    return tmp_path


class GraphWriter:
    """A builder fake that leaves a graph behind — the registration's gate."""

    def __init__(self, repo: Path, code: int = 0):
        self.repo, self.code = repo, code

    def __call__(self, cmd: list[str], cwd: Path) -> int:
        out = self.repo / si.GRAPH_OUT
        out.mkdir(parents=True, exist_ok=True)
        (out / "graph.jsonl").write_text('{"kind": "header"}\n', encoding="utf-8")
        (out / "build_report.json").write_text(
            json.dumps({"status": "ok", "run_id": "this-run"}), encoding="utf-8")
        return self.code


def install(root: Path, **kw) -> int:
    kw.setdefault("tools", ["claude"])
    kw.setdefault("force", False)
    kw.setdefault("dry_run", False)
    return si.install(root, **kw)


def registered(root: Path) -> dict:
    data = json.loads((root / si.MCP_CONFIG).read_text(encoding="utf-8"))
    return data["mcpServers"][si.MCP_SERVER_NAME]


# ── the entry, and its spawn discipline ───────────────────────────────────────


def test_a_successful_chain_registers_the_server(repo, monkeypatch, capsys):
    monkeypatch.setattr(si, "run_builder", GraphWriter(repo))
    assert install(repo) == 0
    entry = registered(repo)
    assert Path(entry["command"]).is_absolute(), \
        "the AI tool spawns this with cwd at the target repo — a bare name is " \
        "the planted-binary hole item 80's review demonstrated"
    assert Path(entry["command"]).name.lower().startswith("uv")
    args = entry["args"]
    assert str(si.harness_root() / si.GRAPH_SERVER) in args
    assert args[args.index("--graph-dir") + 1] == str(repo / si.GRAPH_OUT)
    assert args[args.index("--target") + 1] == str(repo)
    assert "--extra" in args and args[args.index("--extra") + 1] == "serve", \
        "fastmcp is the optional serve extra; the entry must self-heal it"
    assert args[args.index("--project") + 1] == str(si.harness_root())
    assert f"registered {si.MCP_SERVER_NAME}" in capsys.readouterr().out


def test_registration_merges_and_owns_only_its_key(repo, monkeypatch):
    (repo / si.MCP_CONFIG).write_text(json.dumps(
        {"mcpServers": {"mine": {"command": "keep-me"}}, "note": "keep"},
        indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(si, "run_builder", GraphWriter(repo))
    assert install(repo) == 0
    data = json.loads((repo / si.MCP_CONFIG).read_text(encoding="utf-8"))
    assert data["mcpServers"]["mine"] == {"command": "keep-me"}
    assert data["note"] == "keep"
    assert si.MCP_SERVER_NAME in data["mcpServers"]


def test_a_config_that_cannot_be_parsed_is_named_and_untouched(repo, monkeypatch,
                                                               capsys):
    (repo / si.MCP_CONFIG).write_text("{not json", encoding="utf-8")
    before = (repo / si.MCP_CONFIG).read_bytes()
    monkeypatch.setattr(si, "run_builder", GraphWriter(repo))
    assert install(repo) == 0, "registration is a convenience, never the verdict"
    assert (repo / si.MCP_CONFIG).read_bytes() == before
    assert "NOT registered" in capsys.readouterr().out


def test_a_config_of_the_wrong_shape_is_refused_the_same_way(repo, monkeypatch,
                                                             capsys):
    (repo / si.MCP_CONFIG).write_text('{"mcpServers": []}', encoding="utf-8")
    monkeypatch.setattr(si, "run_builder", GraphWriter(repo))
    assert install(repo) == 0
    assert "NOT registered" in capsys.readouterr().out
    assert json.loads((repo / si.MCP_CONFIG).read_text(encoding="utf-8")) == \
        {"mcpServers": []}


def test_a_rerun_is_idempotent_and_says_so(repo, monkeypatch, capsys):
    monkeypatch.setattr(si, "run_builder", GraphWriter(repo))
    assert install(repo) == 0
    first = (repo / si.MCP_CONFIG).read_bytes()
    capsys.readouterr()
    assert install(repo) == 0
    assert "already registered" in capsys.readouterr().out
    assert (repo / si.MCP_CONFIG).read_bytes() == first


def test_no_graph_means_no_registration(repo, monkeypatch, capsys):
    """A server registered over nothing greets every session with a startup
    refusal. Two ways to have no graph: the chain was skipped, or it ran and
    wrote none."""
    monkeypatch.setattr(si, "run_builder", GraphWriter(repo))
    assert install(repo, graph=False) == 0
    assert not (repo / si.MCP_CONFIG).exists()

    class Empty:
        def __call__(self, cmd, cwd):
            return 0  # exits clean, writes nothing

    monkeypatch.setattr(si, "run_builder", Empty())
    capsys.readouterr()
    assert install(repo) == 0
    assert not (repo / si.MCP_CONFIG).exists()


def test_an_estate_registration_names_the_workspace(repo, monkeypatch):
    """Estate freshness is a statement about the WORKSPACE tree, not the target
    repo the graph lands in — the entry must carry the workspace config, resolved,
    because the spawner's cwd is the target and a relative path would miss."""
    monkeypatch.setattr(si, "run_builder", GraphWriter(repo))
    monkeypatch.setattr(si, "run_capture",
                        lambda cmd, cwd: (0, "platform: test\nok rg 1.0.0\n"))
    ws = si.read_workspace(FIXTURE_WS)
    assert install(repo, workspace=ws, estate=True,
                   workspace_path=FIXTURE_WS) == 0
    args = registered(repo)["args"]
    assert args[args.index("--workspace") + 1] == str(FIXTURE_WS.resolve())
    assert "--target" not in args


def test_a_dry_run_names_the_registration_it_would_do(repo, monkeypatch, capsys):
    monkeypatch.setattr(si, "run_builder", GraphWriter(repo))
    assert install(repo, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "would register" in out and si.MCP_SERVER_NAME in out
    assert not (repo / si.MCP_CONFIG).exists()
