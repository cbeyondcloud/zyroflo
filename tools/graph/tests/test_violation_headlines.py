"""T637: a contract violation is headlined in the words of its own class.

The build's one headline called every violation "unresolved row(s) [that] do not carry the
fields their kind promises", so batch 16's declaration violation - a reader filing
`grammar-unavailable` while declaring no parser - sent a person to rows that were complete,
and the same sentence rode into `build_report.json`'s warnings. A row missing a field, a row
outside its kind's pattern vocabulary and a reader's declaration its rows contradict each send
a person somewhere else, so each has its own headline.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
from tools.graph.tests._estate import lay_down  # noqa: E402

#: A profile reader that breaks the classes `{broken}` names, once per file it reads.
PROFILE = '''\
from tools.graph.core.model import ExtractResult, unresolved_row


class _Reader:
    name = "stub-reader"
    INPUT_GLOBS = ("*.stub",)
    PARSERS = {parsers!r}
    UNRESOLVED_FIELDS = {{"stub-finding": ("service",), "grammar-unavailable": ()}}
    UNRESOLVED_PATTERNS = {{"stub-pattern": ("known",)}}

    def parser_of(self, path, rel, text=None):
        return {answer!r}

    def extract(self, ctx):
        res = ExtractResult()
        broken = {broken!r}
        for repo in ctx.repos:
            for path in ctx.inputs(repo):
                rel = ctx.rel(repo, path)
                if "fields" in broken:
                    res.unresolved.append(unresolved_row(
                        self.name, repo.alias, "stub-finding", rel, 0, "names no service"))
                if "patterns" in broken:
                    row = unresolved_row(self.name, repo.alias, "stub-pattern", rel, 0, "odd")
                    row["pattern"] = "odd"
                    res.unresolved.append(row)
                if "declaration" in broken:
                    row = unresolved_row(self.name, repo.alias, "grammar-unavailable", rel, 0,
                                         "no parser for it")
                    row["language"] = "toml"
                    res.unresolved.append(row)
        return res


EXTRACTORS = (_Reader(),)
'''


def headlines(where: Path, *broken: str, parsers: tuple = ("yaml",)) -> list[str]:
    """The build's harness-defect headlines - each warning's first sentence - as a person
    meets them, from a spawned build."""
    toml = lay_down(where, {"a": {"x.stub": "x\n"}, "b": {"app.py": "print('b')\n"}})
    toml.write_bytes(toml.read_bytes().replace(b'profile = ""', b'profile = "./prof"'))
    (where / "ws" / "prof").mkdir()
    (where / "ws" / "prof" / "__init__.py").write_bytes(PROFILE.format(
        broken=broken, parsers=parsers, answer=parsers[0] if parsers else None).encode("utf-8"))
    proc = subprocess.run(
        [sys.executable, "-m", "tools.graph.build", "--workspace", str(toml), "--out",
         str(where / "out"), "--run-id", "fixed", "--empty-ok"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert proc.returncode == 2, proc.stdout + proc.stderr
    report = json.loads((where / "out" / "build_report.json").read_text(encoding="utf-8"))
    return [w.split(". ")[0] for w in report["warnings"] if "DEFECT IN THE HARNESS" in w]


FIELDS = "1 unresolved row(s) do not carry the fields their kind promises"
PATTERNS = "1 unresolved row(s) name a pattern outside their kind's declared vocabulary"
DECLARATION = ("1 reader declaration(s) are contradicted by the rows the reader filed: a "
               "`grammar-unavailable` row names a parser its reader does not declare, or none")


def test_a_declaration_violation_is_headlined_as_one(tmp_path):
    assert headlines(tmp_path, "declaration") == [DECLARATION]


def test_a_reader_declaring_no_parser_is_headlined_as_a_declaration(tmp_path):
    """Batch 16's own case (review B16-1): `PARSERS = ()` and a `grammar-unavailable` row."""
    assert headlines(tmp_path, "declaration", parsers=()) == [DECLARATION]


def test_a_violation_of_each_class_reads_a_headline_true_of_its_own(tmp_path):
    assert headlines(tmp_path, "fields", "declaration") == [FIELDS, DECLARATION]


def test_a_pattern_violation_is_headlined_as_one(tmp_path):
    assert headlines(tmp_path, "patterns") == [PATTERNS]


#: Names a `violations` list may be extended with because their producer already tagged them.
TAGGED_NAMES = {"link_violations": "run_link_passes returns only tagged violations"}


def untagged(source: str) -> list[int]:
    """Lines where a `violations` list gains something not built as a `Violation` (review round
    1, its note on the lead's tagging ruling): a producer that forgets the tag is headlined as
    rows missing a field - the very headline T637 removes - so it fails here instead."""
    bad = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("append", "extend") and node.args
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "violations"):
            arg = node.args[0]
            tagged = (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name)
                      and arg.func.id in ("Violation", "_tagged"))
            if not tagged and not (isinstance(arg, ast.Name) and arg.id in TAGGED_NAMES):
                bad.append(node.lineno)
    return bad


def test_every_violation_the_build_collects_is_tagged():
    assert untagged((ROOT / "tools" / "graph" / "build.py").read_text(encoding="utf-8")) == []


def test_the_tag_guard_sees_an_untagged_producer():
    """The positive control."""
    assert untagged("violations.append('a sentence')\n"
                    "violations.extend(_tagged(['b'], 'fields'))\n"
                    "violations.extend(link_violations)\n"
                    "violations.extend(problems)\n") == [1, 4]


def test_check_freshness_says_a_note_prints_at_either_exit():
    """T637 (review B16-10): its docstring said a NOTE prints at exit 0 - the claim the two
    documents were corrected from; it prints at exit 2 as well."""
    assert "NOTE, at exit 0 as at exit 2," in " ".join(B.check_freshness.__doc__.split())
