"""Item 26 — provider files via the 6-tool fan-out, extended with the graph tier.

The design under pin: ONE graph-tier skill template, rendered per install and
fanned out through the existing 6-tool mechanism — file-kind tools (claude,
cursor, windsurf) get a second `sdlc-graph` file, block-kind tools (copilot,
gemini, aider) get the graph section INSIDE their one harness block (merge_block
supports exactly one BEGIN/END per file — a second block would trip its own
refusal), and the instruction files CLAUDE.md / AGENTS.md gain a graph addendum
block, AGENTS.md now created when a graph ships. Everything gates on the same
`no_chain_reason` the chained build itself uses: provider files that teach a
graph the install will not build are instructions to nowhere.

The SOPs are deliberately NOT copied into the provider files — the skill points
at `<harness>/workflows/*.md`, which test_workflows currency-guards; duplicated
SOP prose in six formats is six copies that rot independently.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _load_init():
    spec = importlib.util.spec_from_file_location("sdlc_init", ROOT / "sdlc-init.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("sdlc_init", mod)
    spec.loader.exec_module(mod)
    return mod


si = _load_init()


@pytest.fixture()
def harness(tmp_path: Path) -> Path:
    h = tmp_path / "harness-clone"
    h.mkdir()
    return h


# ── the template ──────────────────────────────────────────────────────────────


def test_the_template_ships_with_frontmatter_and_placeholders_only():
    raw = si.load_template("GRAPH-SKILL.md")
    meta, body = si.parse_frontmatter(raw)
    assert meta.get("name") == "sdlc-graph"
    assert len(meta.get("description", "")) > 40, \
        "the description is what makes the skill trigger; a stub cannot"
    assert "@@HARNESS@@" in body and "@@GRAPH_DIR@@" in body
    assert not re.search(r"[A-Za-z]:\\", raw), "template carries a drive path"
    assert not re.search(r"(/home/|/Users/)", raw), "template carries a home path"


def test_every_flag_the_template_cites_exists_in_a_shipped_cli():
    """The workflows' currency-guard rule, applied to the skill: a documented
    flag no CLI accepts is a wrong instruction delivered with confidence."""
    flags: set[str] = set()
    for cli in ("tools/graph/build.py", "tools/graph/impact.py",
                "tools/graph/pack.py", "tools/graph/validate.py"):
        flags.update(re.findall(r'add_argument\("(--[a-z][a-z0-9-]*)"',
                                (ROOT / cli).read_text(encoding="utf-8")))
    assert {"--graph-dir", "--check-fresh"} <= flags, "extraction regex rotted"
    _, body = si.parse_frontmatter(si.load_template("GRAPH-SKILL.md"))
    cited = set(re.findall(r"--[a-z][a-z0-9-]*", body))
    dead = sorted(cited - flags - {"--project"})  # --project is uv's, per the SOPs
    assert not dead, f"GRAPH-SKILL.md cites flags no shipped CLI accepts: {dead}"


def test_the_rendered_skill_names_the_workflows_both_tiers_and_the_paths(harness):
    desc, body = si.graph_skill(harness)
    assert desc, "the rendered description feeds the per-tool frontmatter"
    for wf in ("graph-build.md", "impact-review.md", "feature-impact.md"):
        assert wf in body, f"the skill must point at {wf}, not restate it"
    assert "uv run --project" in body, "the CLI tier is the any-runner fallback"
    assert si.MCP_SERVER_NAME in body, "the MCP tier names the registered server"
    assert harness.resolve().as_posix() in body
    assert ".sdlc/graph" in body
    assert "@@" not in body, "unrendered placeholder shipped"


# ── the fan-out ───────────────────────────────────────────────────────────────


def test_file_kind_tools_get_a_second_skill_file(harness):
    desc, body = si.graph_skill(harness)
    claude = si.graph_tool_files("claude", body, desc)
    assert claude == [(".claude/skills/sdlc-graph/SKILL.md",
                       f"---\nname: sdlc-graph\ndescription: {desc}\n---\n\n{body}",
                       "file")]
    (rel, content, kind), = si.graph_tool_files("cursor", body, desc)
    assert rel == ".cursor/rules/sdlc-graph.mdc" and kind == "file"
    assert content.startswith("---\ndescription:")
    (rel, _, kind), = si.graph_tool_files("windsurf", body, desc)
    assert rel == ".windsurf/rules/sdlc-graph.md" and kind == "file"


def test_block_kind_tools_get_no_second_file_because_one_block_is_the_law(harness):
    """merge_block refuses files with two BEGINs; a second block per tool would
    arm that refusal against our own output. The graph section joins the ONE
    sdlc block instead — pinned at the install level below."""
    desc, body = si.graph_skill(harness)
    for tool in ("copilot", "gemini", "aider"):
        assert si.graph_tool_files(tool, body, desc) == []


def _install(root: Path, tools, monkeypatch, *, graph_on: bool,
             claude_extras: bool = False):
    """Run install() with the chain stubbed: the gate decision is the module's
    real one unless graph_on forces it, and no real build ever runs."""
    if graph_on:
        monkeypatch.setattr(si, "no_chain_reason", lambda *a, **k: None)
    monkeypatch.setattr(si, "chain_graph_build", lambda *a, **k: 0)
    monkeypatch.setattr(si, "register_mcp", lambda *a, **k: "mcp: stubbed")
    return si.install(root, list(tools), force=False, dry_run=False,
                      claude_extras=claude_extras)


def test_no_graph_no_surfaces(tmp_path, monkeypatch, capsys):
    """The gate: an install that will not build a graph writes no graph skill,
    no addendum, and creates no AGENTS.md — instructions to nowhere."""
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(si, "chain_graph_build", lambda *a, **k: 0)
    assert si.install(root, ["claude", "gemini"], force=False, dry_run=False,
                      graph=False) == 0
    assert not (root / ".claude" / "skills" / "sdlc-graph").exists()
    assert not (root / "AGENTS.md").exists()
    assert not (root / "CLAUDE.md").exists()
    assert "sdlc-graph" not in (root / "GEMINI.md").read_text(encoding="utf-8")


def test_graph_install_writes_every_surface(tmp_path, monkeypatch, capsys):
    root = tmp_path / "repo"
    root.mkdir()
    assert _install(root, ["claude", "cursor", "windsurf", "gemini"],
                    monkeypatch, graph_on=True) == 0
    assert (root / ".claude" / "skills" / "sdlc-graph" / "SKILL.md").is_file()
    assert (root / ".cursor" / "rules" / "sdlc-graph.mdc").is_file()
    assert (root / ".windsurf" / "rules" / "sdlc-graph.md").is_file()
    gemini = (root / "GEMINI.md").read_text(encoding="utf-8")
    assert gemini.count(si.BLOCK_BEGIN) == 1 and gemini.count(si.BLOCK_END) == 1, \
        "the graph section must share the ONE harness block"
    assert "# SDLC Harness" in gemini and "sdlc-graph" in gemini
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.count(si.BLOCK_BEGIN) == 1 and "sdlc-graph" in agents, \
        "a graph install creates the cross-tool AGENTS.md"
    claude_md = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert "sdlc-graph" in claude_md, \
        "the CLAUDE.md addendum ships with the graph, extras or not"


def test_reinstall_is_idempotent_on_every_surface(tmp_path, monkeypatch, capsys):
    root = tmp_path / "repo"
    root.mkdir()
    _install(root, ["claude", "gemini"], monkeypatch, graph_on=True)
    before = {p: p.read_text(encoding="utf-8")
              for p in (root / "GEMINI.md", root / "AGENTS.md", root / "CLAUDE.md",
                        root / ".claude" / "skills" / "sdlc-graph" / "SKILL.md")}
    _install(root, ["claude", "gemini"], monkeypatch, graph_on=True)
    for p, text in before.items():
        assert p.read_text(encoding="utf-8") == text, f"re-run changed {p.name}"
    out = capsys.readouterr().out
    assert "~ update" not in out.split("Done.")[-1] if "Done." in out else True


def test_user_text_around_the_agents_block_survives(tmp_path, monkeypatch, capsys):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "AGENTS.md").write_text("# My agents\n\nHouse rules first.\n",
                                    encoding="utf-8")
    _install(root, ["claude"], monkeypatch, graph_on=True)
    text = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "House rules first." in text
    assert text.count(si.BLOCK_BEGIN) == 1
    _install(root, ["claude"], monkeypatch, graph_on=True)
    text2 = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert text2.count(si.BLOCK_BEGIN) == 1 and "House rules first." in text2


def test_extras_without_graph_keeps_the_old_pointer_rules(tmp_path, monkeypatch, capsys):
    """Pre-item behavior, preserved exactly: with --claude-extras and no graph,
    CLAUDE.md gets the /sdlc pointer and AGENTS.md is only touched if it
    already exists."""
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(si, "chain_graph_build", lambda *a, **k: 0)
    assert si.install(root, ["claude"], force=False, dry_run=False,
                      claude_extras=True, graph=False) == 0
    claude_md = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert "/sdlc" in claude_md and "sdlc-graph" not in claude_md
    assert not (root / "AGENTS.md").exists()


def test_dry_run_plans_the_surfaces_and_writes_nothing(tmp_path, monkeypatch, capsys):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(si, "no_chain_reason", lambda *a, **k: None)
    assert si.install(root, ["claude", "gemini"], force=False, dry_run=True) == 0
    out = capsys.readouterr().out
    assert ".claude/skills/sdlc-graph/SKILL.md" in out
    assert "AGENTS.md" in out and "CLAUDE.md" in out
    assert not (root / ".claude").exists() and not (root / "AGENTS.md").exists()


# ── the gate is the chain's own ──────────────────────────────────────────────


def test_the_gate_is_no_chain_reason_itself(tmp_path, monkeypatch, capsys):
    """Not a parallel reimplementation: forcing the module's gate open is the
    ONLY thing the surface tests stub, so a divergence between 'will build'
    and 'ships graph files' cannot exist by construction. This test pins the
    negative side with the REAL gate: a non-repository target refuses the
    chain, so no surface ships."""
    root = tmp_path / "not-a-repo"
    root.mkdir()
    monkeypatch.setattr(si, "chain_graph_build", lambda *a, **k: 0)
    assert si.install(root, ["claude"], force=False, dry_run=False) == 0
    assert not (root / ".claude" / "skills" / "sdlc-graph").exists()
    assert not (root / "AGENTS.md").exists()


def test_estate_mode_gate_is_capability_only(monkeypatch, tmp_path):
    """Estate installs chain the workspace build (item 15), so the graph
    surfaces ride the same decision there — capability, not repo shape."""
    monkeypatch.setattr(si, "graph_capability", lambda h: None)
    assert si.no_chain_reason(tmp_path, enabled=True,
                              workspace={"root": tmp_path}, estate=True) is None
    monkeypatch.setattr(si, "graph_capability", lambda h: "engine missing")
    assert si.no_chain_reason(tmp_path, enabled=True,
                              workspace={"root": tmp_path}, estate=True)


def test_the_addendum_names_the_pieces(harness):
    text = si.graph_addendum(harness)
    assert ".sdlc/graph" in text
    assert si.MCP_SERVER_NAME in text
    assert "impact-review" in text, \
        "the addendum's one job is routing a reviewer into the SOP"
    assert harness.resolve().as_posix() in text
