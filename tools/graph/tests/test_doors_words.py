"""Batch 22 (design pass 22, section 5, D22-19 and D22-30): every door that names what a shard
records, what makes it stale or what refuses a publish says the batch's truth - grepped at every
door, the rule batch 17 paid for (a claim corrected at one door and left at another).

- a shard's parser stamp records each parser's VERSION, or that it was missing - no door says
  it records "whether each was installed";
- `--check-fresh` compares the extraction code and the files a shard read in other components
  too, and `--incremental` decides by the code, the parser versions and the record as well;
- the MCP `check_fresh` description lists the extraction code, changed or unreadable, among
  the causes of staleness;
- every door listing a publish's refusal causes names a file of the extraction code that
  cannot be read beside failures and contract violations;
- `load_narrowing`'s docstring states the one-channel rule.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402

PRESENCE = ("whether each was installed", "whether it was installed",
            "whether PyYAML was installed")


def doors() -> list[Path]:
    graph = ROOT / "tools" / "graph"
    return [graph / "build.py", graph / "snapshot.py", graph / "freshness.py",
            graph / "mcp_server.py", ROOT / "sdlc_templates" / "GRAPH-SKILL.md",
            ROOT / "sdlc_templates" / "snapshot-publish.yml", ROOT / "docs" / "pilot-plan.md",
            ROOT / "workflows" / "graph-build.md",
            *sorted((graph / "extractors").glob("*/__init__.py")),
            *sorted((graph / "profiles").glob("*/__init__.py"))]


def flowing(text: str) -> str:
    """A door's words as one line: each line's comment marker (`#`, `#:`) and indentation
    dropped, so a phrase a line break splits is still found."""
    return " ".join(" ".join(line.strip().lstrip("#:").strip() for line in text.splitlines())
                    .split())


@pytest.mark.parametrize("door", doors(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_door_says_a_stamp_records_presence_alone(door):
    text = flowing(door.read_text(encoding="utf-8"))
    assert not [phrase for phrase in PRESENCE if phrase in text]


def test_the_flowing_reading_finds_a_phrase_a_line_break_splits():
    assert "whether each was installed" in flowing("# needed and whether\n# each was installed")


def help_of(flag: str) -> str:
    """One flag's help as `build.py --help` prints it, its lines joined."""
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "graph" / "build.py"), "--help"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip().startswith(flag))
    block = [lines[start]]
    for line in lines[start + 1:]:
        if line.strip().startswith("--"):
            break
        block.append(line)
    return " ".join(" ".join(block).split())


def test_check_fresh_help_names_the_code_and_the_other_components_files():
    words = help_of("--check-fresh")
    assert "extraction code" in words and "other components" in words, words


def test_incremental_help_names_every_cause_it_decides_by():
    words = help_of("--incremental")
    for cause in ("code", "parser", "other components"):
        assert cause in words, (cause, words)


def test_publish_help_names_the_unreadable_code_among_its_refusals():
    assert "extraction code" in help_of("--publish-snapshot")


def tool_doc(name: str) -> str:
    tree = ast.parse((ROOT / "tools" / "graph" / "mcp_server.py").read_text(encoding="utf-8"))
    (fn,) = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name
             and ast.get_docstring(n) and "Per component" in ast.get_docstring(n)]
    return " ".join(ast.get_docstring(fn).split())


def test_the_mcp_check_fresh_description_lists_the_extraction_code():
    words = tool_doc("check_fresh")
    assert "extraction code" in words and "unreadable" in words, words
    assert "other component" in words, words


@pytest.mark.parametrize("door", [ROOT / "sdlc_templates" / "snapshot-publish.yml",
                                  ROOT / "docs" / "pilot-plan.md"],
                         ids=lambda p: p.name)
def test_each_refusal_door_names_the_unreadable_code(door):
    text = " ".join(door.read_text(encoding="utf-8").split())
    assert "extraction code" in text, door


def test_load_narrowings_docstring_states_the_one_channel_rule():
    doc = " ".join((B.load_narrowing.__doc__ or "").split())
    assert "own way on" in doc and "do what each reason says" in doc, doc


def test_graph_skill_says_a_harness_upgrade_stales_every_shard_once():
    text = " ".join((ROOT / "sdlc_templates" / "GRAPH-SKILL.md").read_text(
        encoding="utf-8").split())
    assert "extraction code" in text and "re-publish" in text
