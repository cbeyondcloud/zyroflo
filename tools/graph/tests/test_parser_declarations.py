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

import json
import os
import shutil
import subprocess
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


# ── a reader that breaks its declaration, as a process ─────────────────────────────────────

#: A profile reader written into the test's own tree: `{parsers}`, `{answer}` (its rule's
#: answer for every file), `{row}` (whether it files `grammar-unavailable`) and `{language}`
#: (the parser that row names, or None for none) vary.
STUB = '''\
from tools.graph.core.model import ExtractResult, unresolved_row


class _Stub:
    name = "stub-reader"
    INPUT_GLOBS = ("*.stub",)
    DEPLOYMENT_READER = True
    CLAIM_GLOBS = ("*.stub",)
    PARSERS = {parsers}
    UNRESOLVED_FIELDS = {{"grammar-unavailable": ()}}

    def parser_of(self, path, rel, text=None):
        return __import__("os").environ.get("B22_STUB_ANSWER") or {answer!r}

    def extract(self, ctx):
        res = ExtractResult()
        for repo in ctx.repos:
            for path in ctx.inputs(repo):
                if {row!r}:
                    rec = unresolved_row(
                        self.name, repo.alias, "grammar-unavailable", ctx.rel(repo, path), 0,
                        "no parser for it")
                    language = {language!r}
                    if language is not None:
                        rec["language"] = language
                    res.unresolved.append(rec)
        return res


EXTRACTORS = (_Stub(),)
'''


def stub_workspace(where: Path, **stub) -> None:
    from tools.graph.tests._estate import lay_down
    toml = lay_down(where, {"a": {"x.stub": "x\n"}, "b": {"app.py": "print('b')\n"}})
    toml.write_bytes(toml.read_bytes().replace(b'profile = ""', b'profile = "./prof"'))
    write_stub(where, **stub)


def write_stub(where: Path, parsers: tuple = ("yaml",), answer: str | None = "yaml",
               row: bool = False, language: str | None = "yaml") -> None:
    prof = where / "ws" / "prof"
    prof.mkdir(exist_ok=True)
    (prof / "__init__.py").write_bytes(
        STUB.format(parsers=repr(parsers), answer=answer, row=row,
                    language=language).encode("utf-8"))


def run(where: Path, flag: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tools.graph.build", "--workspace",
         str(where / "ws" / "workspace.toml"), "--out", str(where / "out"), "--run-id",
         "fixed", "--empty-ok", flag],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, **env})


def test_a_reader_that_declares_no_parser_and_files_grammar_unavailable_breaks_its_contract(
        tmp_path):
    """Review B16-1: a reader that files `grammar-unavailable` says it could not parse, so its
    parser state must reach its shard's stamp - which only a declared parser can. Declaring
    none (`PARSERS = ()`) and filing one is a contract violation, refused where every row
    passes, so no shard is published whose parser state nothing records."""
    stub_workspace(tmp_path, parsers=(), answer=None, row=True)
    proc = run(tmp_path, "--publish-snapshot")
    assert (proc.returncode, proc.stderr) == (2, ""), proc.stdout + proc.stderr
    assert ("  contract: stub-reader: filed `grammar-unavailable` for a but declares no parser "
            "it can lack (`PARSERS = ()`) - declare the parser its gate needs, so its shard "
            "records the parser's version, or that it was missing") in proc.stdout.splitlines()
    assert "snapshot: refused - " in proc.stdout
    assert not (tmp_path / "ws" / "a" / ".sdlc" / "graph" / "snapshot.json").exists()
    write_stub(tmp_path, parsers=("yaml",), row=True)  # followed: the parser declared
    assert run(tmp_path, "--publish-snapshot").returncode == 0


def test_a_row_naming_a_parser_its_reader_does_not_declare_breaks_its_contract(tmp_path):
    """T636 (review B16-8): a deployment reader declaring a parser it does not lack - `PARSERS =
    ("yaml",)` - and filing `grammar-unavailable` for a grammar it never names published a
    shard the parser group, asking only for `yaml`, read as parsed. Only a parser the reader
    declares reaches its shard's stamp, so the row's parser must be one: refused where every
    row passes, and naming a declared parser, followed, builds."""
    stub_workspace(tmp_path, parsers=("yaml",), row=True, language="typescript")
    proc = run(tmp_path, "--publish-snapshot")
    assert (proc.returncode, proc.stderr) == (2, ""), proc.stdout + proc.stderr
    assert ("  contract: stub-reader: filed `grammar-unavailable` for a naming the parser "
            "'typescript', which it does not declare (`PARSERS = ('yaml',)`) - name a parser "
            "it declares, or declare the one it needs, so its shard records the parser's "
            "version, or that it was missing") in proc.stdout.splitlines()
    assert not (tmp_path / "ws" / "a" / ".sdlc" / "graph" / "snapshot.json").exists()
    write_stub(tmp_path, parsers=("yaml",), row=True, language="yaml")  # followed
    assert run(tmp_path, "--publish-snapshot").returncode == 0


def test_a_row_naming_no_parser_breaks_its_contract(tmp_path):
    """T636: a `grammar-unavailable` row that names no parser leaves the parser group nothing
    to ask, and its NOTE nothing to name."""
    stub_workspace(tmp_path, parsers=("yaml",), row=True, language=None)
    proc = run(tmp_path, "--publish-snapshot")
    assert (proc.returncode, proc.stderr) == (2, ""), proc.stdout + proc.stderr
    assert ("  contract: stub-reader: filed `grammar-unavailable` for a naming no parser - name "
            "the one it could not use in the row's `language`, a parser it declares "
            "(`PARSERS = ('yaml',)`), so its shard records the parser's version, or that it "
            "was missing") in proc.stdout.splitlines()
    write_stub(tmp_path, parsers=("yaml",), row=True, language="yaml")  # followed
    assert run(tmp_path, "--publish-snapshot").returncode == 0


def test_a_row_its_reader_no_longer_declares_is_noted_with_a_way_it_can_follow(tmp_path):
    """Review B17-2: a shard whose row names a parser its reader no longer declares is carried
    as one nothing can ask about, and the NOTE's way on said to install the parser that row
    names - for it, nothing to install. It says to name one the reader declares."""
    stub_workspace(tmp_path, parsers=("yaml",), row=True, language="yaml")
    assert run(tmp_path, "--publish-snapshot").returncode == 0
    write_stub(tmp_path, parsers=("typescript",), answer="typescript", row=True, language="yaml")
    proc = run(tmp_path, "--check-fresh")
    notes = [ln for ln in proc.stdout.splitlines() if ln.startswith("freshness: NOTE ")]
    assert len(notes) == 1 and "cannot be asked" in notes[0], proc.stdout
    assert notes[0].endswith(B.UNASKABLE_WAY), notes


def _spawn_with(where: Path, workspace: Path, preamble: str) -> subprocess.CompletedProcess:
    """The build as a process, with `preamble` run in the child before it imports the build."""
    runner = where / "run_with.py"
    runner.write_text(f"import sys\nsys.path.insert(0, {str(ROOT)!r})\n{preamble}"
                      "import tools.graph.build as B\nraise SystemExit(B.main(sys.argv[1:]))\n",
                      encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(runner), "--workspace", str(workspace), "--out",
         str(where / "out"), "--run-id", "fixed", "--empty-ok"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")


def _rows(where: Path) -> list[dict]:
    return [json.loads(line) for line in
            (where / "out" / "unresolved.jsonl").read_text(encoding="utf-8").split("\n")
            if line.strip()]


def test_the_tests_reader_names_a_suffix_no_grammar_maps_as_its_own_kind(tmp_path):
    """T636 (review B16-9): the `tests` reader filed `grammar-unavailable` for its other meaning
    too - no grammar maps to a suite's suffix, a parser never asked for - with the suffix as
    its `language`, which the new contract refuses: no reader declares a suffix as a parser.
    It is its own kind now, `grammar-unmapped`, naming the suffix. Forced: every suite suffix
    shipped has a grammar (`test_every_suite_extension_has_a_grammar_mapped`), so the branch
    runs only on that drift - here, `.js` dropped from the map in the child."""
    from tools.graph.tests._estate import lay_down
    toml = lay_down(tmp_path, {"a": {"a.test.js": "test('x', () => {});\n"},
                               "b": {"app.py": "print('b')\n"}})
    proc = _spawn_with(tmp_path, toml, "from tools.graph.extractors import _lang\n"
                                       "_lang.GRAMMARS.pop('.js')\n")
    assert (proc.returncode, proc.stderr) == (0, ""), proc.stdout + proc.stderr
    tests_rows = [r for r in _rows(tmp_path) if r["extractor"] == "tests"]
    assert [(r["kind"], r.get("suffix")) for r in tests_rows] == [
        ("grammar-unmapped", ".js")], tests_rows


ORDERFLOW = Path(__file__).resolve().parent / "fixtures" / "orderflow"


def test_every_shipped_reader_with_its_parser_denied_names_a_parser_it_declares(tmp_path):
    """T636's review focus: the new contract must never refuse a shipped reader on a real
    estate. With every parser denied in the child, orderflow builds as on `main` - narrowed,
    exit 0, no contract line - and every `grammar-unavailable` row names a parser its reader
    declares."""
    work = tmp_path / "ws"
    shutil.copytree(ORDERFLOW, work)
    proc = _spawn_with(tmp_path, work / "workspace.toml",
                       "from tools.graph.core.lib import props, srcfacts\n"
                       "props.yaml_available = lambda: False\n"
                       "srcfacts.available = lambda name: False\n")
    assert (proc.returncode, proc.stderr) == (0, ""), proc.stdout + proc.stderr
    assert "contract:" not in proc.stdout, proc.stdout
    declared = {r.name: r.parsers for r in B.discover_extractors()}
    lacked = sorted({(r["extractor"], r.get("language")) for r in _rows(tmp_path)
                     if r["kind"] == "grammar-unavailable"})
    assert lacked, "every parser denied, and no reader said so"
    assert [pair for pair in lacked if pair[1] not in declared.get(pair[0], ())] == [], lacked


def test_a_rule_answering_a_parser_it_does_not_declare_is_refused_on_every_door(tmp_path):
    """Review B16-6: the refusal pinned above in-process, as a person meets it - each door
    that asks a reader's rule exits 2 with the reader's words and an empty stderr.

    Batch 22 (T617): the reader's answer moves with its environment, not its code - a change
    to a reader's code changes the extractor version, so claim 4 calls every shard stale
    before any door asks the rule (pinned below)."""
    stub_workspace(tmp_path)
    assert run(tmp_path, "--publish-snapshot").returncode == 0
    for flag in ("--check-fresh", "--from-snapshot", "--incremental", "--publish-snapshot"):
        proc = run(tmp_path, flag, B22_STUB_ANSWER="kotlin")
        assert (proc.returncode, proc.stderr) == (2, ""), (flag, proc.stdout + proc.stderr)
        assert ("!! stub-reader's parser_of returned 'kotlin', which its PARSERS does not "
                "declare") in proc.stdout.splitlines(), (flag, proc.stdout)
    write_stub(tmp_path, answer="kotlin")
    proc = run(tmp_path, "--check-fresh")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert [line for line in proc.stdout.splitlines() if line.startswith("freshness: STALE")] == [
        f"freshness: STALE {c} - the extractors or their code changed since its shard was "
        f"published" for c in ("a", "b")], proc.stdout
