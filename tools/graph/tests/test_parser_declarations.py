"""Batch 16 (the owner's ruling 30): each reader that can lack a parser declares the parsers it
uses, so a shard can be stamped with the parser state it was built under.

`PARSERS` is read with a sentinel - absent is not `()` - because a reader that files
`grammar-unavailable` and forgot the declaration would otherwise read as "uses no parser" and
its parser state would never be stamped (design round 3, D16-28). A file reaches a parser by
the reader's own rule: the three readers whose gate is finer than a suffix answer through
their own `parser_of`, which their `extract` also calls, and every other reader through the
case-folded suffix table (D16-14, D16-26, D16-35).
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
from tools.graph.core.lib import props, srcfacts  # noqa: E402

#: The declarations the design note lists, reader by reader (section 1).
DECLARED = {
    "compose-services": ("yaml",),
    "helm-deployments": ("yaml",),
    "helm-configmaps": ("yaml",),
    "openapi-specs": ("yaml",),
    "kafka-consumers": ("java",),
    "kafka-producers": ("java",),
    "spring-controllers": ("java",),
    "spring-call-concats": ("java",),
    "spring-rest-calls": (),
    "frontend-urls": ("javascript", "typescript", "tsx"),
    "tests": ("java", "python", "javascript", "typescript", "tsx"),
    "orderflow-write-mappings": ("yaml",),
}


def make_pkg(root: Path, name: str, body: str) -> Path:
    pkg = root / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return pkg


def registry() -> list[B.Registration]:
    """Every registered reader: the stack's and the shipped profile's."""
    seen: dict[str, str] = {}
    stack, _ = B.discover(None, "stack", seen)
    profile, _ = B.discover_package(B.PROFILE_ROOT / "orderflow", "profile", seen,
                                    root_label="profiles")
    return [*stack, *profile]


def reader(name: str) -> B.Registration:
    (reg,) = [r for r in registry() if r.name == name]
    return reg


# ── registration ─────────────────────────────────────────────────────────────

_READER = '''
    UNRESOLVED_FIELDS = {{"grammar-unavailable": ("language",)}}
    {module}
    class _E:
        name = "r"
        INPUT_GLOBS = ("*.kt", "*.yaml")
    {cls}
        def extract(self, ctx):
            return None
    EXTRACTORS = (_E(),)
'''


def stub(tmp_path: Path, module: str = "", cls: str = "") -> list[B.Registration]:
    make_pkg(tmp_path, "r", _READER.format(module=module, cls=cls))
    return B.discover_extractors(tmp_path)


def test_a_reader_filing_grammar_unavailable_without_parsers_is_refused(tmp_path):
    with pytest.raises(B.BuildError, match="declares `grammar-unavailable` but no `PARSERS`"):
        stub(tmp_path)


def test_an_empty_declaration_is_a_declaration(tmp_path):
    (reg,) = stub(tmp_path, cls="    PARSERS = ()")
    assert reg.parsers == ()


def test_a_module_declaration_is_read_and_a_class_one_narrows_it(tmp_path):
    (reg,) = stub(tmp_path, module="PARSERS = ('yaml',)")
    assert reg.parsers == ("yaml",)
    make_pkg(tmp_path / "two", "r", _READER.format(module="PARSERS = ('yaml',)",
                                                  cls="    PARSERS = ()"))
    (reg,) = B.discover_extractors(tmp_path / "two")
    assert reg.parsers == ()


@pytest.mark.parametrize("cls, expected", [
    ("    PARSERS = 'yaml'", "must be a TUPLE of parser names"),
    ("    PARSERS = ('',)", "must be a TUPLE of parser names"),
    ("    PARSERS = (7,)", "must be a TUPLE of parser names"),
    ("    PARSERS = ('kotlin',)", "no probe"),
    ("    PARSERS = ('kotlin',)\n"
     "        def parser_available(self, name):\n            return True", "no file rule"),
])
def test_a_malformed_or_unanswerable_declaration_is_refused(tmp_path, cls, expected):
    with pytest.raises(B.BuildError, match=expected):
        stub(tmp_path, cls=cls)


def test_a_profile_reader_answering_for_its_own_parser_is_accepted(tmp_path):
    (reg,) = stub(tmp_path, cls=(
        "    PARSERS = ('kotlin', 'yaml')\n"
        "        def parser_available(self, name):\n            return name == 'kotlin'\n"
        "        def parser_of(self, path, rel, text=None):\n"
        "            return 'kotlin' if path.suffix.lower() == '.kt' else None"))
    assert reg.parsers == ("kotlin", "yaml")
    probe = B.parser_probe([reg])
    assert probe("kotlin") is True
    assert B.file_parser(reg, Path("src/App.KT"), "src/App.KT") == "kotlin"


def test_a_parser_of_answer_outside_its_declaration_is_refused(tmp_path):
    (reg,) = stub(tmp_path, cls=(
        "    PARSERS = ('yaml',)\n"
        "        def parser_of(self, path, rel, text=None):\n            return 'java'"))
    with pytest.raises(B.BuildError,
                       match="r's parser_of returned 'java', which its PARSERS does not declare"):
        B.file_parser(reg, Path("x.yaml"), "x.yaml")


# ── the real registry ────────────────────────────────────────────────────────

def test_the_readers_declare_what_the_design_note_lists():
    regs = {r.name: r for r in registry()}
    assert {name: regs[name].parsers for name in DECLARED} == DECLARED


def test_guard_every_reader_filing_grammar_unavailable_declares_parsers():
    """Guard 1: the sentinel, not the registration's folded tuple - a reader whose declaration
    never arrives must not pass as one that uses no parser."""
    for reg in registry():
        if "grammar-unavailable" in reg.unresolved_fields:
            module = sys.modules[type(reg.extractor).__module__]
            declared = getattr(reg.extractor, "PARSERS", getattr(module, "PARSERS", None))
            assert declared is not None, reg.name


#: Every suffix a probe tries where a glob can hand a reader any file: the suffix table's, and
#: two it maps to no parser.
ANY_SUFFIX = (*B.SUFFIX_PARSERS, ".json", ".txt")


def _suffixes(globs: tuple[str, ...]) -> set[str]:
    """The suffixes a reader's globs can hand it: a glob ending in a wildcard (`src/test/*`)
    can hand it any file."""
    out: set[str] = set()
    for glob in globs:
        tail = glob.rsplit("/", 1)[-1]
        if tail.endswith("*"):
            out |= set(ANY_SUFFIX)
        elif "." in tail:
            out.add("." + tail.rsplit(".", 1)[-1].lower())
    return out


def _reachable(globs: tuple[str, ...]) -> set[str]:
    """The parsers a reader's globs can hand it a file for, through the suffix table."""
    return {B.SUFFIX_PARSERS[s] for s in _suffixes(globs) if s in B.SUFFIX_PARSERS}


def test_guard_every_parser_a_rule_can_return_is_declared():
    """Guard 2: a reader using the default rule declares every parser its globs can reach; a
    reader with its own `parser_of` declares every name that rule returns (D16-48)."""
    sniffs = "openapi: 3.0.0\n"
    for reg in registry():
        if reg.parser_of is None:
            if reg.parsers or "grammar-unavailable" in reg.unresolved_fields:
                assert _reachable(reg.globs) <= set(reg.parsers), reg.name
            continue
        answers = {reg.parser_of(Path(f"x{suffix}"), f"x{suffix}", sniffs)
                   for suffix in _suffixes(reg.globs)}
        assert answers - {None} <= set(reg.parsers), reg.name


# ── which file reaches which parser ──────────────────────────────────────────

@pytest.mark.parametrize("name, rel, text, expected", [
    ("tests", "src/test/Helper.JAVA", None, "java"),
    ("tests", "src/test/helper.mjs", None, None),
    ("tests", "src/test/data.txt", None, None),
    ("tests", "tests/test_app.py", None, "python"),
    ("frontend-urls", "tests/util.js", None, None),
    ("frontend-urls", "src/app.js", None, "javascript"),
    ("frontend-urls", "src/App.TSX", None, "tsx"),
    ("frontend-urls", "package.json", None, None),
    ("openapi-specs", "settings.yaml", "key: value\n", None),
    ("openapi-specs", "api/spec.yaml", "openapi: 3.0.0\npaths: {}\n", "yaml"),
    ("openapi-specs", "api/spec.json", '{"openapi": "3.0.0"}', None),
    ("compose-services", "docker-compose.YML", None, "yaml"),
    ("spring-controllers", "src/Api.java", None, "java"),
    # The default rule answers only parsers the reader declares.
    ("spring-controllers", "src/main/resources/app.yaml", None, None),
    ("spring-rest-calls", "src/main/resources/app.properties", None, None),
])
def test_a_file_reaches_the_parser_its_readers_own_gate_needs(name, rel, text, expected):
    assert B.file_parser(reader(name), Path(rel), rel, text) == expected


def test_openapi_reads_the_file_when_no_text_is_handed_and_an_unreadable_one_needs_none(tmp_path):
    spec = tmp_path / "spec.yaml"
    spec.write_text("openapi: 3.0.0\npaths: {}\n", encoding="utf-8")
    assert B.file_parser(reader("openapi-specs"), spec, "spec.yaml") == "yaml"
    (tmp_path / "gone.yaml").mkdir()
    assert B.file_parser(reader("openapi-specs"), tmp_path / "gone.yaml", "gone.yaml") is None


def test_the_default_probes_are_the_readers_own_gates(monkeypatch):
    probe = B.parser_probe(registry())
    monkeypatch.setattr(props, "yaml_available", lambda: False)
    assert probe("yaml") is False
    monkeypatch.setattr(srcfacts, "available", lambda language: language != "java")
    assert (probe("java"), probe("tsx")) == (False, True)


def test_the_rule_by_reader_name_answers_as_the_registration_does():
    rule = B.parser_rule(registry())
    assert rule("tests", Path("src/test/Helper.JAVA"), "src/test/Helper.JAVA") == "java"
    assert rule("spring-rest-calls", Path("a.properties"), "a.properties") is None
