"""Item 24 — the three graph-tier workflows, and the guard that keeps them true.

`workflows/*.md` are runner-facing SOPs: an agent executes them from the markdown
plus the CLIs alone (the M3 verification runs impact-review from a second provider
that has no MCP). A stale SOP naming a dead flag or a renamed tool is a wrong
instruction delivered with full confidence, so the load-bearing tests here are
currency guards: every `--flag` a workflow cites must exist in a shipped graph
CLI's argparse source, and the MCP tool names must be exactly the server's — read
live from `TOOL_NAMES`, not transcribed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.graph.mcp_server import TOOL_NAMES  # noqa: E402

WORKFLOWS = ("graph-build.md", "impact-review.md", "feature-impact.md")
#: Every CLI the workflows may cite flags from — grown with the surface: the
#: graph tier, and item 25's review pre-pass.
CLI_SOURCES = ("tools/graph/build.py", "tools/graph/impact.py",
               "tools/graph/pack.py", "tools/graph/validate.py",
               "tools/graph/mcp_server.py", "tools/review/prepass.py",
               "tools/review/hitl.py")

#: Flags that appear inside the documented command PREFIX rather than a graph
#: CLI's own parser. `--project` is uv's: it pins the harness environment when a
#: command is pasted from any other directory.
PREFIX_FLAGS = {"--project"}


def text_of(name: str) -> str:
    return (ROOT / "workflows" / name).read_text(encoding="utf-8")


def cli_flag_union() -> set[str]:
    flags: set[str] = set()
    for cli in CLI_SOURCES:
        src = (ROOT / cli).read_text(encoding="utf-8")
        flags.update(re.findall(r'add_argument\("(--[a-z][a-z0-9-]*)"', src))
    return flags


def test_the_three_workflows_exist_with_the_sop_skeleton():
    """The house SOP shape, including the section the self-improvement loop
    writes into (CLAUDE.md: learned constraints land in Edge Cases / Notes)."""
    for name in WORKFLOWS:
        text = text_of(name)
        for section in ("## Purpose", "## Steps", "## Verify",
                        "## Edge Cases / Notes", "## Access tiers"):
            assert section in text, f"{name} is missing {section}"


@pytest.mark.parametrize("name", WORKFLOWS)
def test_every_workflow_names_both_access_tiers(name):
    """MCP when the server is registered, CLIs always: the tier doctrine is
    structural per document, because a workflow only one provider can execute
    is a provider lock-in written as prose."""
    text = text_of(name)
    assert "MCP" in text, f"{name} never mentions the MCP tier"
    assert "uv run --project <harness>" in text, \
        f"{name} lacks the runner-agnostic CLI prefix"


@pytest.mark.parametrize("name", WORKFLOWS)
def test_every_cited_flag_exists_in_a_shipped_graph_cli(name):
    """The currency guard. A flag documented here and absent from every graph
    CLI is an instruction that fails for whoever follows it — the exact rot
    mode of SOPs kept apart from the code they describe."""
    known = cli_flag_union() | PREFIX_FLAGS
    cited = set(re.findall(r"--[a-z][a-z0-9-]*", text_of(name)))
    dead = sorted(cited - known)
    assert not dead, f"{name} cites flags no shipped graph CLI accepts: {dead}"


def test_the_flag_union_is_not_vacuous():
    """The guard above passes trivially if the extraction regex rots and the
    union comes back empty; the flags every reader knows must be in it."""
    flags = cli_flag_union()
    assert {"--graph-dir", "--check-fresh", "--diff", "--budget"} <= flags


def test_all_eight_mcp_tools_are_documented():
    """The three workflows are the eight tools' user manual — read live from the
    server so a renamed tool breaks this test, not a reader's session."""
    union = "".join(text_of(n) for n in WORKFLOWS)
    missing = [t for t in TOOL_NAMES if f"`{t}`" not in union]
    assert not missing, f"undocumented server tools: {missing}"


def test_every_cited_graph_cli_ships():
    union = "".join(text_of(n) for n in WORKFLOWS)
    cited = set(re.findall(r"([\w-]+\.py)", union))
    assert "build.py" in cited, "the extraction regex found nothing it should"
    for cli in cited:
        candidates = [ROOT / "tools" / "graph" / cli,
                      ROOT / "tools" / "review" / cli, ROOT / cli]
        assert any(c.exists() for c in candidates), \
            f"the workflows cite {cli}, which ships nowhere"


@pytest.mark.parametrize("name", WORKFLOWS)
def test_no_machine_specific_paths(name):
    """Genericity: SOPs carry placeholders (`<harness>`, `<graph-dir>`), never a
    developer's drive or home directory."""
    text = text_of(name)
    assert not re.search(r"[A-Za-z]:\\", text), f"{name} carries a drive path"
    assert not re.search(r"(/home/|/Users/)", text), f"{name} carries a home path"
    assert "<harness>" in text


def test_impact_review_carries_the_seed_and_generated_doctrines():
    """The two doctrines a reviewer must not lose: a `none`-mode file
    contributed no graph evidence, and a generated row redirects the edit."""
    text = text_of("impact-review.md")
    for mode in ("`hunk`", "`provenance`", "`none`"):
        assert mode in text
    assert "regenerate, don't edit" in text
    assert "regenerate_from" in text
    assert "pack.json" in text, "the never-trimmed machine copy must be named"
    # Item 27: the clock starts at step one and closes at the end — review time
    # unmeasured at M2 makes M4's <15-min gate unreadable when it arrives.
    assert "--begin" in text and "--end" in text and "--abandon" in text
    assert "no reviewer identity" in text, \
        "the anti-Goodhart posture is part of the SOP, not an implementation note"


def test_feature_impact_states_the_semantic_layer_limit():
    """UC-04 before item 79 lands: entry points and domain aliases are not yet
    facts, and search is lexical. A workflow that oversells the graph makes
    every proposal built on it overconfident."""
    text = text_of("feature-impact.md")
    assert "item 79" in text
    assert "lexical" in text
    assert "entry points" in text


def test_graph_build_routes_freshness_validation_and_the_baseline():
    text = text_of("graph-build.md")
    assert "`check_fresh`" in text and "--check-fresh" in text, \
        "freshness must be named in both tiers"
    assert "--publish-snapshot" in text
    assert "--targets" in text, \
        "recalibrated floors reach the tool only explicitly - the workflow " \
        "must carry that doctrine, not just the default verdict"
    assert "zero-silent-drops" in text
