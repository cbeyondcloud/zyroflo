"""The build pipeline: registration, activation, the four empties, and isolation.

No extractor exists yet, which is what makes these tests worth writing now: every one of
them is about the *pipeline's* behaviour, and pipeline behaviour is what decides whether a
missing extractor shows up as a warning or as a graph that merely looks small.
"""
from __future__ import annotations

import json
import ntpath
import os
import posixpath
import sys
import textwrap
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph.core.lib import repo_walk  # noqa: E402
from tools.graph.core.model import Edge, ExtractResult, Node  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────


def make_extractor_pkg(root: Path, name: str, body: str) -> Path:
    """Write one extractor subpackage. `body` is the whole __init__.py."""
    pkg = root / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return pkg


#: A minimal well-formed extractor, parameterised by name, globs and what it emits.
EXTRACTOR_TEMPLATE = '''
    from tools.graph.core.model import Edge, ExtractResult, Node

    class _E:
        name = {name!r}
        INPUT_GLOBS = {globs!r}

        def extract(self, ctx):
            res = ExtractResult()
            for repo in ctx.repos:
                res.nodes.append(Node(id=f"svc:{{repo.alias}}", type="svc"))
            {body}
            return res

    EXTRACTORS = (_E(),)
'''


def simple_extractor(root: Path, pkg: str, name: str, globs: tuple[str, ...],
                     body: str = "pass") -> None:
    make_extractor_pkg(root, pkg, EXTRACTOR_TEMPLATE.format(name=name, globs=globs, body=body))


EDGE_BODY = '''
            if len(ctx.repos) >= 1:
                res.edges.append(Edge(src=f"svc:{ctx.repos[0].alias}", type="DG_CALLS",
                                      dst="ep:/x", source_file="a.txt", line=1,
                                      extractor="e"))
'''


def make_workspace(tmp_path: Path, components: dict[str, list[str]],
                   name: str = "") -> Path:
    """Create a workspace of declared components, each holding the given relative files.

    Components are DECLARED with an explicit `path` rather than given a `.git`, which is the
    same route the committed fixture takes: git refuses to track a nested `.git`, silently.
    """
    root = tmp_path / "ws"
    root.mkdir(parents=True, exist_ok=True)
    lines = ["schema_version = 1", 'root = "."', f'name = "{name}"', ""]
    for comp, files in components.items():
        (root / comp).mkdir(parents=True, exist_ok=True)
        for rel in files:
            f = root / comp / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x\n", encoding="utf-8")
        lines += ["[[component]]", f'name = "{comp}"', f'path = "{comp}"', ""]
    cfg = root / "workspace.toml"
    cfg.write_text("\n".join(lines), encoding="utf-8")
    return cfg


def run_build(cfg: Path, out: Path, ext_root: Path | None, **kw) -> dict:
    return B.build(cfg, out, run_id=kw.pop("run_id", "testrun0001"),
                   extractor_root=ext_root, **kw)


# ── registration: every violation is fatal, and says what to do ───────────────


def test_a_missing_extractor_root_registers_nothing(tmp_path: Path):
    assert B.discover_extractors(tmp_path / "nope") == []


# ── T401: absent is not the same as could-not-read, and a lone file is not silent ──


def test_a_lone_module_at_the_root_is_refused_by_name(tmp_path: Path):
    """A single-file extractor is the more natural first attempt at adding one, and a bare
    DIRECTORY in the same spot is already a hard error (test_a_directory_without_an_init_is_
    a_hard_error) whose own message says a directory that looks like an extractor and is not
    one is how an edge class goes missing. A lone .py file must fail exactly as loudly, not
    vanish beside a real package -- and cell 7 requires the message NAME it, so a mutant that
    keeps the refusal but drops the filename must fail this test, not just "raises something"."""
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    stray = tmp_path / "beta.py"
    stray.write_text("EXTRACTORS = ()\n", encoding="utf-8")
    with pytest.raises(B.BuildError, match=r"lone module.*not a package") as excinfo:
        B.discover_extractors(tmp_path)
    assert str(stray) in str(excinfo.value), (
        "the refusal must name the offending file, not just describe the shape of the defect")


def test_a_stray_non_python_file_at_the_root_is_silently_ignored(tmp_path: Path):
    """Behaviour-only kill for "the per-entry is_dir guard is gone": editor/OS litter
    (README, .DS_Store-shaped, anything not a .py) is not a candidate extractor and must not
    block discovery of a real package beside it. Distinguishing this from the guard being
    deleted entirely does not depend on any message wording -- discovery either succeeds or
    it does not -- and this is the only test that exercises the suffix branch at all."""
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    (tmp_path / "NOTES.txt").write_text("not code\n", encoding="utf-8")
    assert [r.name for r in B.discover_extractors(tmp_path)] == ["alpha"]


def test_a_conftest_at_the_root_does_not_block_discovery(tmp_path: Path):
    """DECISION (written beside discover(), in _NOT_AN_EXTRACTOR_ATTEMPT): a caller-supplied
    extractor root is reachable only through the `extractor_root` API parameter -- no CLI
    flag exposes it -- and can legitimately be a fixture-shaped directory carrying pytest's
    own conftest.py for reasons that have nothing to do with this registry. A well-known
    Python file that is provably not an extractor attempt is skipped like a dotted or
    underscored name, rather than treated as one more silent-drop risk: the alternative
    (refuse every lone .py file, no exceptions) turns a fix for one silent failure into a
    loud false positive nobody asked for, on a file nobody wrote as an extractor."""
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    (tmp_path / "conftest.py").write_text("import pytest\n", encoding="utf-8")
    assert [r.name for r in B.discover_extractors(tmp_path)] == ["alpha"]


def test_a_directory_named_conftest_py_registers_its_extractors(tmp_path: Path):
    """The exemption in _NOT_AN_EXTRACTOR_ATTEMPT is for a FILE, never a directory -- it
    must be checked ONLY inside the not-a-directory branch. A directory that happens to
    share a name with a well-known Python file is exactly as real a package as any other
    name, and exempting it by name alone is a fresh silent drop in the row whose whole
    purpose is removing one."""
    pkg = tmp_path / "conftest.py"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(textwrap.dedent("""
        from tools.graph.core.model import ExtractResult
        class _E:
            name = "conftest_pkg"
            INPUT_GLOBS = ("*.txt",)
            def extract(self, ctx):
                return ExtractResult()
        EXTRACTORS = (_E(),)
    """), encoding="utf-8")
    regs, _ = B.discover(tmp_path)
    assert [r.name for r in regs] == ["conftest_pkg"]


def test_a_bare_directory_named_conftest_py_is_still_a_hard_error(tmp_path: Path):
    """Same defect, sharper: a bare directory (no __init__.py) named conftest.py must keep
    the ordinary "has no __init__.py" refusal every other bare directory gets. Losing it
    silently would be the exact failure class T401 exists to close, reintroduced through
    the exemption's own name."""
    (tmp_path / "conftest.py").mkdir()
    with pytest.raises(B.BuildError, match="no __init__.py"):
        B.discover_extractors(tmp_path)


#: The two platforms' real, un-guessed decision primitives -- not a guess at what each
#: platform does, but the actual stdlib modules that `os.path.normcase` and
#: `importlib.machinery.SOURCE_SUFFIXES` resolve to there. `ntpath`/`posixpath` are always
#: importable regardless of the HOST platform (CPython ships both), and
#: `importlib._bootstrap_external`'s own source reads:
#:     SOURCE_SUFFIXES = ['.py']
#:     if _MS_WINDOWS:
#:         SOURCE_SUFFIXES.append('.pyw')
#: confirmed by reading that module's source directly, in this repository's own venv, rather
#: than assumed from what `SOURCE_SUFFIXES` happens to read on whichever host runs the
#: suite -- `.pyw` is Windows-only to the INTERPRETER, not only to the `pythonw` launcher.
_WINDOWS_PY_SUFFIXES = frozenset({".py", ".pyw"})
_POSIX_PY_SUFFIXES = frozenset({".py"})


def _discover_under(monkeypatch, tmp_path: Path, *, normcase, suffixes) -> list[str]:
    """Run the real discover_extractors() with the real per-platform primitives
    substituted in, so a test proves both platforms' actual decision by executing it, not
    by trusting an `if os.name == ...: / else:` branch that only ever runs the host's own
    half. Never patches production code -- `_PY_SUFFIXES` is derived from the live
    interpreter's own list and is deliberately left alone; only the TEST's view of what
    each platform's list contains is substituted.

    Covers the CODE's per-entry decision only: `os.path.normcase` and `_PY_SUFFIXES`, the
    two things discover() actually reads. It does NOT make the FILESYSTEM behave like the
    other platform -- directory iteration order and how `Path.name`/`Path.suffix` parse a
    real on-disk name still follow the HOST, not the substitution, so a future test relying
    on either would pass here for the wrong reason.

    Patches `B.os` -- the `os` NAME as `tools.graph.build`'s own module namespace resolves
    it -- rather than the real `os.path` module. `os.path` is not a copy of `ntpath`/
    `posixpath`; it IS whichever one the host uses, so setting an attribute on it mutates
    that REAL shared module for as long as this test's `monkeypatch` fixture is alive, and
    a second call's own keyword argument (evaluated AFTER the first call already ran) can
    read back the first call's corruption instead of the function it named -- proven by
    `test_discover_under_does_not_corrupt_the_real_normcase_functions` below, which failed
    exactly this way before this indirection existed. A fresh, private stand-in exposing
    only `.path.normcase` makes that corruption structurally impossible: nothing shared is
    ever mutated, so no call's substitution can outlive itself or leak into the next one."""
    monkeypatch.setattr(B, "os", types.SimpleNamespace(
        path=types.SimpleNamespace(normcase=normcase)))
    monkeypatch.setattr(B, "_PY_SUFFIXES", suffixes)
    return [r.name for r in B.discover_extractors(tmp_path)]


def test_discover_under_does_not_corrupt_the_real_normcase_functions(
        tmp_path: Path, monkeypatch):
    """The aliasing hazard, reproduced HERE even though the real failure was on Linux CI:
    `os.path` is not a copy of `ntpath`/`posixpath`, it IS whichever one the host uses -- so
    `monkeypatch.setattr(os.path, "normcase", X)` mutates that REAL module's `normcase`, and
    the next call's own keyword argument (`normcase=posixpath.normcase`, evaluated AFTER the
    first call already ran) can read back an already-corrupted function instead of the true
    one. On THIS host `os.path IS ntpath`, so the two back-to-back calls every test above
    makes -- Windows-simulating then POSIX-simulating, the same order used throughout this
    file -- leave the REAL `ntpath.normcase` overwritten with `posixpath.normcase` once the
    second call has run, because it was `ntpath` this whole time regardless of which
    platform either call intended to simulate. Asserting BOTH real modules' own `normcase`
    still behave as themselves after this exact call sequence makes an otherwise
    Linux-only failure reproduce on Windows too."""
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    _discover_under(monkeypatch, tmp_path, normcase=ntpath.normcase,
                    suffixes=_WINDOWS_PY_SUFFIXES)
    _discover_under(monkeypatch, tmp_path, normcase=posixpath.normcase,
                    suffixes=_POSIX_PY_SUFFIXES)
    assert ntpath.normcase("BETA.PY") == "beta.py", (
        "the real ntpath.normcase must still fold case after both simulated calls")
    assert posixpath.normcase("BETA.PY") == "BETA.PY", (
        "the real posixpath.normcase must still be identity after both simulated calls")


def test_an_uppercase_python_suffix_is_handled_per_platform_import_rules(
        tmp_path: Path, monkeypatch):
    """DECISION (written beside the suffix/name comparisons in discover(), using
    os.path.normcase): Windows -- a first-class target per CLAUDE.md -- is case-insensitive,
    so BETA.PY names the same importable file as beta.py and must be refused just as loudly.
    POSIX filesystems are case-SENSITIVE, so BETA.PY is not a file the language would ever
    load there -- refusing it on CI (Linux, the gate of record) would be a loud FALSE
    refusal, trading the original silent drop for a wrong answer on the platform that
    actually gates this repository. Both halves are executed for real in this one test via
    _discover_under, regardless of which platform actually hosts the run."""
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    stray = tmp_path / "BETA.PY"
    stray.write_text("EXTRACTORS = ()\n", encoding="utf-8")

    with pytest.raises(B.BuildError, match=r"lone module.*not a package"):
        _discover_under(monkeypatch, tmp_path, normcase=ntpath.normcase,
                        suffixes=_WINDOWS_PY_SUFFIXES)
    assert _discover_under(monkeypatch, tmp_path, normcase=posixpath.normcase,
                           suffixes=_POSIX_PY_SUFFIXES) == ["alpha"]


def test_a_lone_pyw_module_is_refused_only_where_it_is_importable(
        tmp_path: Path, monkeypatch):
    """`.pyw` is importable ONLY on Windows -- confirmed by reading
    importlib._bootstrap_external's own source (quoted beside _WINDOWS_PY_SUFFIXES above),
    not assumed from this session's own SOURCE_SUFFIXES, which reads both only because this
    session happens to run on Windows. On POSIX the interpreter genuinely cannot load a
    .pyw file, so it is exactly as inert as any other unrelated file, and refusing it would
    be the FALSE-refusal defect this row was sent back for two rounds ago, in its other
    form. Production `_PY_SUFFIXES` is derived from the live interpreter's own list, so it
    is ALREADY correct on both platforms by construction; this test proves that by
    exercising both platforms' real decision, not by hardcoding what should pass."""
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    stray = tmp_path / "beta.pyw"
    stray.write_text("EXTRACTORS = ()\n", encoding="utf-8")

    with pytest.raises(B.BuildError, match=r"lone module.*not a package"):
        _discover_under(monkeypatch, tmp_path, normcase=ntpath.normcase,
                        suffixes=_WINDOWS_PY_SUFFIXES)
    assert _discover_under(monkeypatch, tmp_path, normcase=posixpath.normcase,
                           suffixes=_POSIX_PY_SUFFIXES) == ["alpha"]


def test_an_allcaps_conftest_spelling_is_silently_ignored_on_both_platforms(
        tmp_path: Path, monkeypatch):
    """Not the divergent case -- kept as a boundary check. CONFTEST.PY's suffix is ALSO
    upper-cased, so on POSIX it is masked by the suffix check above (not a recognised
    source suffix there) before the name comparison is ever reached; on Windows both
    comparisons fold case and it is masked by the deliberate exemption instead. Same
    observable outcome, different mechanism -- the mechanism that actually depends on
    case-folding the NAME is only provable by a spelling whose suffix stays lowercase,
    which is the next test."""
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    (tmp_path / "CONFTEST.PY").write_text("import pytest\n", encoding="utf-8")

    assert _discover_under(monkeypatch, tmp_path, normcase=ntpath.normcase,
                           suffixes=_WINDOWS_PY_SUFFIXES) == ["alpha"]
    assert _discover_under(monkeypatch, tmp_path, normcase=posixpath.normcase,
                           suffixes=_POSIX_PY_SUFFIXES) == ["alpha"]


def test_a_capitalised_conftest_spelling_is_the_divergent_case(tmp_path: Path, monkeypatch):
    """THE case the two platforms genuinely disagree on: suffix stays lowercase (`.py`, so
    it always passes the suffix check on either platform), only the name's first letter is
    capitalised. On Windows (case-insensitive) Conftest.py IS the file conftest.py names,
    so it must stay exempt. On POSIX (case-sensitive, and what CI runs) it is a DIFFERENT,
    legitimately named, importable module -- not the special file the exemption exists for
    -- so exempting it there would silently drop a real module: this row's own defect
    class, reintroduced by the exemption meant to prevent it. Both halves executed for
    real, here, via _discover_under."""
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    stray = tmp_path / "Conftest.py"
    stray.write_text("EXTRACTORS = ()\n", encoding="utf-8")

    assert _discover_under(monkeypatch, tmp_path, normcase=ntpath.normcase,
                           suffixes=_WINDOWS_PY_SUFFIXES) == ["alpha"]
    with pytest.raises(B.BuildError, match=r"lone module.*not a package"):
        _discover_under(monkeypatch, tmp_path, normcase=posixpath.normcase,
                        suffixes=_POSIX_PY_SUFFIXES)


def test_a_root_that_is_a_file_is_refused_not_silently_empty(tmp_path: Path):
    """CLAUDE.md: an empty result is not evidence of absence. A root that EXISTS but is not
    a directory must not read the same as a root with nothing behind it."""
    bad_root = tmp_path / "not_a_dir.py"
    bad_root.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(B.BuildError, match=r"could not be scanned"):
        B.discover_extractors(bad_root)


def test_a_root_that_cannot_be_listed_is_refused_not_silently_empty(tmp_path: Path, monkeypatch):
    """The same fold as the file case, for a directory whose listing fails rather than one
    that is the wrong shape entirely — both are "could not read", neither is "absent"."""
    root = tmp_path / "locked"
    root.mkdir()

    real_iterdir = Path.iterdir

    def fake_iterdir(self):
        if self == root:
            raise PermissionError(13, "Permission denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", fake_iterdir)
    with pytest.raises(B.BuildError, match=r"could not be scanned"):
        B.discover_extractors(root)


def test_a_wrong_type_LINK_PASSES_is_rejected_even_beside_a_rules_only_yml(tmp_path: Path):
    """The rules-only escape hatch (Item 21) forgives a correctly-typed EMPTY tuple. It must
    never forgive a value of the wrong type merely because that wrong value is also falsy —
    that is the isinstance check being bypassed by the same `not value` the hatch keys on."""
    pkg = tmp_path / "rulesonly"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("EXTRACTORS = ()\nLINK_PASSES = 0\n", encoding="utf-8")
    (pkg / "traversal_rules.yml").write_text("hops: 1\n", encoding="utf-8")
    with pytest.raises(B.BuildError, match=r"`LINK_PASSES` must be a TUPLE"):
        B.discover_package(pkg, "profile")


def test_a_rules_only_package_with_correctly_empty_tuples_still_registers(tmp_path: Path):
    """The positive case the hatch exists for must keep working once the isinstance check
    runs unconditionally: a correctly-typed empty tuple beside a traversal_rules.yml is
    still a legitimate rules-only profile, not an error."""
    pkg = tmp_path / "rulesonly2"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("EXTRACTORS = ()\nLINK_PASSES = ()\n", encoding="utf-8")
    (pkg / "traversal_rules.yml").write_text("hops: 1\n", encoding="utf-8")
    regs, links = B.discover_package(pkg, "profile")
    assert regs == [] and links == []


def test_a_failed_import_does_not_leave_a_corpse_for_a_retry(tmp_path: Path):
    """Ride-along: the standard `import` statement removes a module from sys.modules when
    its exec raises; discover_package's low-level loader path did not get that for free, so
    a half-executed module sat under its synthetic name for anything that imported it again
    by name in the same process."""
    pkg = tmp_path / "flaky"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("TOKEN = 'first'\nraise RuntimeError('boom')\n",
                                     encoding="utf-8")
    with pytest.raises(B.BuildError, match="failed to import"):
        B.discover_package(pkg, "stack")

    modname = "_sdlc_stack_flaky"
    assert modname not in sys.modules, (
        "a half-executed module must not survive its own failed import")

    (pkg / "__init__.py").write_text(textwrap.dedent("""
        from tools.graph.core.model import ExtractResult
        class _E:
            name = "flaky"
            INPUT_GLOBS = ("*.txt",)
            def extract(self, ctx):
                return ExtractResult()
        EXTRACTORS = (_E(),)
    """), encoding="utf-8")
    regs, _ = B.discover_package(pkg, "stack")
    assert [r.name for r in regs] == ["flaky"]


def test_a_well_formed_package_registers(tmp_path: Path):
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    regs = B.discover_extractors(tmp_path)
    assert [r.name for r in regs] == ["alpha"]
    assert regs[0].globs == ("*.txt",)
    assert regs[0].layer == "stack"
    assert regs[0].module.endswith("/alpha")


def test_an_extractor_root_outside_the_repo_loads_even_when_it_is_the_default(
        tmp_path: Path, monkeypatch):
    """`EXTRACTOR_ROOT` is the DEFAULT root, not proof that a dotted import would resolve.

    Choosing how to load a package by comparing against that global makes every extractor
    outside the repo tree fail to import the moment anything points the default at one.
    """
    ext = tmp_path / "ext"
    simple_extractor(ext, "alpha", "alpha", ("*.txt",))
    monkeypatch.setattr(B, "EXTRACTOR_ROOT", ext)
    assert [r.name for r in B.discover_extractors()] == ["alpha"]


def test_the_repos_own_extractors_load_under_their_real_dotted_name():
    """One module object per extractor. Spec-loading them under a synthetic name would hand a
    second copy to anything that later imports them normally — two classes, two module-level
    constants, and a shared-cache key that compares equal while the code disagrees about who
    wrote it."""
    import tools.graph.extractors.properties as direct

    regs = {r.name: r for r in B.discover_extractors()}
    assert type(regs["properties"].extractor) is direct.PropertiesExtractor


def test_private_and_cache_directories_are_skipped(tmp_path: Path):
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / ".hidden").mkdir()
    assert [r.name for r in B.discover_extractors(tmp_path)] == ["alpha"]


def test_a_directory_without_an_init_is_a_hard_error(tmp_path: Path):
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    (tmp_path / "beta").mkdir()
    with pytest.raises(B.BuildError, match="no __init__.py"):
        B.discover_extractors(tmp_path)


def test_an_import_error_names_the_package_that_failed(tmp_path: Path):
    make_extractor_pkg(tmp_path, "broken", "import a_module_that_does_not_exist_xyz\n")
    with pytest.raises(B.BuildError, match=r"broken.*failed to import.*ModuleNotFoundError"):
        B.discover_extractors(tmp_path)


def test_a_package_registering_nothing_is_a_hard_error(tmp_path: Path):
    """A package may export extractors, link passes, or both. Exporting neither means it is
    imported and then ignored, which reads as a capability this harness does not have."""
    make_extractor_pkg(tmp_path, "quiet", "X = 1\n")
    with pytest.raises(B.BuildError, match="neither `EXTRACTORS` nor `LINK_PASSES`"):
        B.discover_extractors(tmp_path)


def test_an_empty_EXTRACTORS_is_a_hard_error(tmp_path: Path):
    make_extractor_pkg(tmp_path, "empty", "EXTRACTORS = ()\n")
    with pytest.raises(B.BuildError, match="`EXTRACTORS` is empty"):
        B.discover_extractors(tmp_path)


def test_two_extractors_with_one_name_is_a_hard_error(tmp_path: Path):
    simple_extractor(tmp_path, "alpha", "same", ("*.txt",))
    simple_extractor(tmp_path, "beta", "same", ("*.md",))
    with pytest.raises(B.BuildError, match="already registered"):
        B.discover_extractors(tmp_path)


@pytest.mark.parametrize("attrs, expected", [
    (["name = ''", "INPUT_GLOBS = ('*.txt',)"], "no usable `name`"),
    (["INPUT_GLOBS = ('*.txt',)"], "no usable `name`"),
    (["name = 'a'", "INPUT_GLOBS = '*.txt'"], "must be a TUPLE"),
    (["name = 'a'", "INPUT_GLOBS = ()"], "missing or empty"),
    (["name = 'a'"], "missing or empty"),
    (["name = 'a'", "INPUT_GLOBS = ('',)"], "which is not a pattern"),
    (["name = 'a'", "INPUT_GLOBS = (7,)"], "which is not a pattern"),
    # T591 (D15a-33): `READS_NESTED = "false"` is truthy, and a mistyped reach declaration
    # would widen an extractor's inputs and stamps in silence, so it must be a real bool.
    *[(["name = 'a'", "INPUT_GLOBS = ('*.txt',)", f"READS_NESTED = {value}"], "READS_NESTED")
      for value in ("'false'", "'no'", "0.1", "1", "0", "None")],
    # Batch 15b: a deployment reader declares itself, as a real bool - its failure
    # narrows a run and its case-lost files become nodes, so a truthy string would
    # change both in silence.
    *[(["name = 'a'", "INPUT_GLOBS = ('*.txt',)", f"DEPLOYMENT_READER = {value}"],
       "DEPLOYMENT_READER") for value in ("'true'", "1", "0", "None")],
    # Which of its globs it reads for claims, and what each other glob serves.
    (["name = 'a'", "INPUT_GLOBS = ('*.txt',)", "CLAIM_GLOBS = ('*.txt',)"],
     "only on a deployment reader"),
    (["name = 'a'", "INPUT_GLOBS = ('*.txt',)", "GLOB_PURPOSES = {'*.txt': 'host-maps'}"],
     "only on a deployment reader"),
    (["name = 'a'", "INPUT_GLOBS = ('*.txt',)", "DEPLOYMENT_READER = True",
      "CLAIM_GLOBS = '*.txt'"], "must be a TUPLE"),
    (["name = 'a'", "INPUT_GLOBS = ('*.txt',)", "DEPLOYMENT_READER = True",
      "CLAIM_GLOBS = ('*.md',)"], "not among its INPUT_GLOBS"),
    (["name = 'a'", "INPUT_GLOBS = ('*.txt',)", "DEPLOYMENT_READER = True",
      "GLOB_PURPOSES = {'*.md': 'host-maps'}"], "not among its INPUT_GLOBS"),
    (["name = 'a'", "INPUT_GLOBS = ('*.txt',)", "DEPLOYMENT_READER = True",
      "GLOB_PURPOSES = {'*.txt': 'addresses'}"], "one of chart-templates, host-maps, "
                                                 "package-dirs"),
    (["name = 'a'", "INPUT_GLOBS = ('*.txt',)", "DEPLOYMENT_READER = True",
      "GLOB_PURPOSES = [('*.txt', 'host-maps')]"], "must be a dict"),
    (["name = 'a'", "INPUT_GLOBS = ('*.txt',)", "DEPLOYMENT_READER = True",
      "CLAIM_GLOBS = ('*.txt',)", "GLOB_PURPOSES = {'*.txt': 'host-maps'}"],
     "both a claim glob and a purpose"),
    (["name = 'a'", "INPUT_GLOBS = ('*.txt', '*.md')", "DEPLOYMENT_READER = True",
      "CLAIM_GLOBS = ('*.txt',)"], r"says nothing of '\*\.md'"),
    (["name = 'a'", "INPUT_GLOBS = ('*.txt',)", "DEPLOYMENT_READER = True"],
     r"says nothing of '\*\.txt'"),
])
def test_a_malformed_extractor_is_rejected_at_registration(tmp_path: Path, attrs, expected):
    make_extractor_pkg(tmp_path, "bad", "class _E:\n"
                       + "".join(f"    {a}\n" for a in attrs)
                       + "    def extract(self, ctx):\n        return None\n"
                       + "EXTRACTORS = (_E(),)\n")
    with pytest.raises(B.BuildError, match=expected):
        B.discover_extractors(tmp_path)


def test_a_deployment_reader_is_registered_with_its_globs_roles(tmp_path: Path):
    make_extractor_pkg(tmp_path, "good", "class _E:\n"
                       "    name = 'a'\n"
                       "    INPUT_GLOBS = ('*.txt', '*.md')\n"
                       "    DEPLOYMENT_READER = True\n"
                       "    CLAIM_GLOBS = ('*.txt',)\n"
                       "    GLOB_PURPOSES = {'*.md': 'host-maps'}\n"
                       "    def extract(self, ctx):\n        return None\n"
                       "EXTRACTORS = (_E(),)\n")
    (reg,) = B.discover_extractors(tmp_path)
    assert (reg.deployment_reader, reg.claim_globs, reg.glob_purposes) == (
        True, ("*.txt",), {"*.md": "host-maps"})


def test_an_ordinary_extractor_declares_no_deployment_role(tmp_path: Path):
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    (reg,) = B.discover_extractors(tmp_path)
    assert (reg.deployment_reader, reg.claim_globs, reg.glob_purposes) == (False, (), {})


def test_the_in_tree_deployment_readers_declare_themselves():
    """`build.py` names no extractor (batch 15b): the readers whose facts the accounting
    rows read - deployment nodes, their claims, host maps - say so themselves."""
    regs = {r.name: r for r in B.discover_extractors()}
    assert sorted(n for n, r in regs.items() if r.deployment_reader) == [
        "compose-services", "helm-configmaps", "helm-deployments"]
    assert regs["compose-services"].claim_globs == regs["compose-services"].globs
    assert regs["helm-deployments"].claim_globs == (
        "values.yaml", "values.yml", "Chart.yaml", "Chart.yml")
    assert regs["helm-configmaps"].claim_globs == ()
    assert regs["helm-configmaps"].glob_purposes == {
        "templates/*.yaml": "host-maps", "templates/*.yml": "host-maps"}


def test_an_extractor_without_extract_is_rejected(tmp_path: Path):
    make_extractor_pkg(tmp_path, "bad", """
        class _E:
            name = "a"
            INPUT_GLOBS = ("*.txt",)
        EXTRACTORS = (_E(),)
    """)
    with pytest.raises(B.BuildError, match="no callable `extract"):
        B.discover_extractors(tmp_path)


def test_the_bare_string_message_shows_the_fix(tmp_path: Path):
    """A one-pattern tuple is easy to write as a string, and a string is iterable — so
    without this check the globs would silently become one pattern per character."""
    make_extractor_pkg(tmp_path, "bad", """
        class _E:
            name = "a"
            INPUT_GLOBS = "pom.xml"
            def extract(self, ctx):
                return None
        EXTRACTORS = (_E(),)
    """)
    with pytest.raises(B.BuildError, match=r"INPUT_GLOBS = \('pom.xml',\)"):
        B.discover_extractors(tmp_path)


# ── ordering: the shared cache makes it load-bearing ──────────────────────────


def requiring(root: Path, pkg: str, name: str, requires: tuple[str, ...]) -> None:
    make_extractor_pkg(root, pkg, f"""
        from tools.graph.core.model import ExtractResult
        class _E:
            name = {name!r}
            INPUT_GLOBS = ("*.txt",)
            REQUIRES = {requires!r}
            def extract(self, ctx):
                return ExtractResult()
        EXTRACTORS = (_E(),)
    """)


def test_an_extractor_runs_after_what_it_requires(tmp_path: Path):
    """Directory-alphabetical order is arbitrary, and the shared cache is not.

    An extractor that reads a resolved view before the extractor that publishes it sees
    nothing and reports every reference as unresolved — a wrong graph from a correct build.
    """
    ext = tmp_path / "ext"
    requiring(ext, "aaa", "consumer", ("provider",))
    requiring(ext, "zzz", "provider", ())
    assert [r.name for r in B.discover_extractors(ext)] == ["provider", "consumer"]


def test_ordering_is_deterministic_among_independent_extractors(tmp_path: Path):
    ext = tmp_path / "ext"
    for pkg in ("c", "a", "b"):
        requiring(ext, pkg, pkg, ())
    assert [r.name for r in B.discover_extractors(ext)] == ["a", "b", "c"]


def test_a_chain_of_requirements_is_ordered_end_to_end(tmp_path: Path):
    ext = tmp_path / "ext"
    requiring(ext, "one", "one", ("two",))
    requiring(ext, "two", "two", ("three",))
    requiring(ext, "three", "three", ())
    assert [r.name for r in B.discover_extractors(ext)] == ["three", "two", "one"]


def test_a_cycle_is_a_hard_error_naming_its_members(tmp_path: Path):
    ext = tmp_path / "ext"
    requiring(ext, "a", "a", ("b",))
    requiring(ext, "b", "b", ("a",))
    with pytest.raises(B.BuildError, match="cycle.*a, b"):
        B.discover_extractors(ext)


def test_requires_written_as_a_bare_string_is_rejected(tmp_path: Path):
    """A string is iterable, so `REQUIRES = "properties"` would become one requirement per
    letter — none of which names a registered extractor, so the ordering silently reverts to
    alphabetical and the failure surfaces as a graph with edges missing."""
    ext = tmp_path / "ext"
    make_extractor_pkg(ext, "bad", """
        class _E:
            name = "a"
            INPUT_GLOBS = ("*.txt",)
            REQUIRES = "properties"
            def extract(self, ctx):
                return None
        EXTRACTORS = (_E(),)
    """)
    with pytest.raises(B.BuildError, match=r"REQUIRES.*TUPLE.*\('properties',\)"):
        B.discover_extractors(ext)


def test_a_requirement_that_did_not_run_is_a_warning_not_a_refusal(tmp_path: Path):
    """`--only` legitimately excludes a provider while debugging. The consumer still runs and
    reports what it could not look up — but the report has to say the graph is short."""
    cfg = make_workspace(tmp_path, {"svc": ["a.txt"]})
    ext = tmp_path / "ext"
    requiring(ext, "aaa", "consumer", ("provider",))
    requiring(ext, "zzz", "provider", ())

    report = run_build(cfg, tmp_path / "out", ext, only=["consumer"])
    assert any("needs provider, which did not run" in w for w in report["warnings"])


def test_a_requirement_naming_an_unregistered_extractor_does_not_break_ordering(tmp_path: Path):
    ext = tmp_path / "ext"
    requiring(ext, "a", "a", ("never-registered",))
    assert [r.name for r in B.discover_extractors(ext)] == ["a"]


# ── the version stamp ─────────────────────────────────────────────────────────


def test_extractor_version_changes_when_the_registered_set_changes(tmp_path: Path):
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    one = B.extractor_version(B.discover_extractors(tmp_path))
    assert one == B.extractor_version(B.discover_extractors(tmp_path))

    simple_extractor(tmp_path, "beta", "beta", ("*.md",))
    assert B.extractor_version(B.discover_extractors(tmp_path)) != one


def test_extractor_version_changes_when_only_the_globs_change(tmp_path: Path):
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    before = B.extractor_version(B.discover_extractors(tmp_path))
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt", "*.md"))
    assert B.extractor_version(B.discover_extractors(tmp_path)) != before


# ── activation is by FILES, not by the detected stack label ───────────────────


def test_activation_matches_files_not_the_stack_label(tmp_path: Path):
    """The regression that motivated the design.

    `detect_stack()` returns ONE label per component, chosen by first match. A tree holding
    only deployment charts detects as `unknown`, and a component with a build manifest
    detects as its build system regardless of what else it contains. Gating activation on
    that label would silently drop every extractor whose technology is not the label — which
    is not a smaller graph, it is a graph missing whole edge classes with nothing to say so.
    """
    cfg = make_workspace(tmp_path, {"charts-only": ["values.yaml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "charts", "charts", ("values.yaml",))

    report = run_build(cfg, tmp_path / "out", ext)
    comp = report["components"][0]
    assert comp["stack"] == "unknown", "precondition: nothing here identifies a stack"
    assert comp["claimed_by"] == ["charts"]


def test_one_component_can_activate_several_extractors(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml", "app.properties", "V1__x.sql"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",))
    simple_extractor(ext, "b", "b", ("*.properties",))
    simple_extractor(ext, "c", "c", ("V*__*.sql",))

    report = run_build(cfg, tmp_path / "out", ext)
    assert report["components"][0]["claimed_by"] == ["a", "b", "c"]


def test_an_extractor_that_matches_nothing_claims_nothing(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",))
    simple_extractor(ext, "z", "z", ("*.nope",))

    report = run_build(cfg, tmp_path / "out", ext)
    by_name = {e["name"]: e for e in report["extractors"]}
    assert by_name["z"]["components"] == []
    assert by_name["a"]["components"] == ["svc"]


def test_an_extractor_sees_only_the_components_it_claimed(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"one": ["pom.xml"], "two": ["package.json"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",))

    report = run_build(cfg, tmp_path / "out", ext)
    graph = json.loads(json.dumps(report))          # report is plain data
    assert graph["extractors"][0]["components"] == ["one"]
    # the extractor emits one svc node per repo it is handed, so the count is the proof
    assert graph["extractors"][0]["nodes"] == 1


def test_activation_walks_a_nested_path_glob(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["src/main/resources/application.properties"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "p", "p", ("src/main/resources/*.properties",))
    report = run_build(cfg, tmp_path / "out", ext)
    assert report["components"][0]["claimed_by"] == ["p"]


def test_the_installers_own_root_file_does_not_activate_an_extractor(tmp_path: Path):
    """T153 (W71's measurement on T145): `.mcp.json`, written by the installer at the
    adopter's root, is an ordinary file to any glob-based extractor -- `openapi-specs`
    globs `*.json` and only sniffs content for a spec marker AFTER activation -- so once a
    repository commits its install, the extractor SET a build reports depends on whether
    the harness's own bootstrap file is present, not on anything in the adopter's own code.
    `real-json` is the control: a component holding an ordinary same-glob file still
    activates, so the exclusion is proven specific to the installer's own file rather than
    a blanket suppression of `*.json`.
    """
    mcp_config = B.load_installer().MCP_CONFIG
    cfg = make_workspace(tmp_path, {"mcp-only": [mcp_config], "real-json": ["config.json"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "jsonwatch", "jsonwatch", ("*.json",))

    report = run_build(cfg, tmp_path / "out", ext)
    by_name = {e["name"]: e for e in report["extractors"]}
    assert by_name["jsonwatch"]["components"] == ["real-json"], (
        f"the harness's own {mcp_config!r} must not activate a *.json extractor; "
        f"got {by_name['jsonwatch']['components']!r}")


def test_a_same_named_file_below_the_component_root_still_activates(tmp_path: Path):
    """The exclusion is the installer's file AT THE COMPONENT ROOT specifically, matched by
    full relative path -- not any file sharing its basename anywhere in the tree, which
    would silently drop a file the installer never wrote and the adopter owns.
    """
    mcp_config = B.load_installer().MCP_CONFIG
    cfg = make_workspace(tmp_path, {"svc": [f"nested/{mcp_config}"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "jsonwatch", "jsonwatch", ("*.json",))

    report = run_build(cfg, tmp_path / "out", ext)
    assert report["components"][0]["claimed_by"] == ["jsonwatch"]


def test_the_installers_own_ci_workflow_does_not_activate_an_extractor(tmp_path: Path):
    """T153 round 2: the installer also writes `.github/workflows/*.yml` (the CI ledger
    gate, `sdlc-init.py:LEDGER_WORKFLOW_REL`, written whenever `.github/` exists) -- the
    SAME glob-based activation bug one directory deeper, since `.github` is not in
    `repo_walk.DEFAULT_EXCLUDES` and hidden directories are walked on purpose. `real-yaml`
    is the control, and its file sits at the same nesting depth so this also exercises a
    NESTED exclusion, which the flat `.mcp.json` case never could: a mutant that swapped
    the posix-normalised path comparison for a bare `str()` conversion stays silent on a
    root file but would stop matching here on Windows, where `str()` joins path segments
    with backslashes instead of `/`.
    """
    si = B.load_installer()
    cfg = make_workspace(tmp_path, {"workflow-only": [si.LEDGER_WORKFLOW_REL],
                                    "real-yaml": [".github/workflows/adopters-own.yml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "yamlwatch", "yamlwatch", ("*.yml",))

    report = run_build(cfg, tmp_path / "out", ext)
    by_name = {e["name"]: e for e in report["extractors"]}
    assert by_name["yamlwatch"]["components"] == ["real-yaml"], (
        f"the harness's own {si.LEDGER_WORKFLOW_REL!r} must not activate a *.yml extractor; "
        f"got {by_name['yamlwatch']['components']!r}")


def test_the_installers_own_claude_extras_do_not_activate_an_extractor(tmp_path: Path):
    """T153 round 3: the identical defect a THIRD time, under `.claude/` --
    `--claude-extras` writes `.claude/settings.json` (an ordinary `*.json` file) and two
    `.claude/hooks/*.py` scripts (ordinary `*.py` files) -- claimable by any extractor
    globbing those extensions, same mechanism as rounds 1 and 2. Every control sits at the
    SAME depth as its sibling leak, so the comparison isolates the installer's own name
    from the adopter's rather than the directory.
    """
    si = B.load_installer()
    hooks = sorted(f".claude/{n}" for n in si.EXTRA_TEMPLATE_FILES if n.startswith("hooks/"))
    assert len(hooks) == 2, f"expected exactly the two shipped hook scripts, got {hooks}"

    cfg = make_workspace(tmp_path, {
        "settings-only": [".claude/settings.json"],
        "real-json": ["config.json"],
        "hooks-only": hooks,
        "real-py": [".claude/hooks/adopters_own.py"],
    })
    ext = tmp_path / "ext"
    simple_extractor(ext, "jsonwatch", "jsonwatch", ("*.json",))
    simple_extractor(ext, "pywatch", "pywatch", ("*.py",))

    report = run_build(cfg, tmp_path / "out", ext)
    by_name = {e["name"]: e for e in report["extractors"]}
    assert by_name["jsonwatch"]["components"] == ["real-json"], (
        "the harness's own .claude/settings.json must not activate a *.json extractor; "
        f"got {by_name['jsonwatch']['components']!r}")
    assert by_name["pywatch"]["components"] == ["real-py"], (
        "the harness's own hook scripts must not activate a *.py extractor; "
        f"got {by_name['pywatch']['components']!r}")


def _planned_installer_destinations(si, root: Path) -> list[dict]:
    """Every destination the installer's OWN planning functions can name, called
    DIRECTLY rather than walking one installed tree -- T153 round 4.

    Round 3's guard called `install()` once (all tools, extras on, graph off, no
    `.github/`) and walked the result. That is complete across DIRECTORIES but blind
    across CONFIGURATIONS: `.github/workflows/*.yml` is only ever written when
    `.github/` pre-exists, so a guard that walks one fixture without it can never see
    that class, no matter how carefully it walks. Measured: drop all three workflow
    paths from `installer_root_exclusions` and a walk-based guard over that one
    fixture stays green at 0 leaks.

    This is the reviewer's own method for establishing completeness in the first
    place: call `surface_ops`, `claude_extras_ops`, `ledger_gate_ops`,
    `replay_job_ops`, `snapshot_job_ops` directly and read each returned `rel`. A
    BLOCKED op (no workspace, no `.github/`, an unreachable emitter) still carries the
    `rel` it was blocked for, so this does not depend on which flags a particular
    install used or which preconditions this process happens to satisfy -- confirmed
    with the same drop-three-paths measurement: this version reports all three as
    leaks regardless.

    Scoped to what these five functions can name; `install()`'s own `.sdlc/*` calls
    are not separately re-derived because every one of them already lands under
    `OWN_ARTIFACT_DIR`, which is excluded unconditionally, by construction.
    """
    skill_meta, skill_body = si.parse_frontmatter(si.load_template("SKILL.md"))
    skill_body, _no_cmds = si.render_skill_body(skill_body)
    description = skill_meta.get("description", "Run the full SDLC for a bug fix or feature.")
    ops: list[dict] = []
    ops += si.surface_ops(root, si.ALL_TOOLS, skill_body, description,
                          with_graph=True, extras_on=True)
    ops += si.claude_extras_ops(root, {})
    ops += si.ledger_gate_ops(root)
    ops += si.replay_job_ops(root, None)
    ops += si.snapshot_job_ops(root, None)
    return ops


def _unexcluded_leaks(rels: set[str], si) -> list[str]:
    """Of `rels`, everything outside `OWN_ARTIFACT_DIR` a registered extractor glob
    would claim and `installer_root_exclusions` does not cover. Shared by the guard
    and its own positive control, so the control tests the SAME logic the guard runs
    rather than a copy that could drift from it.
    """
    seen: dict[str, str] = {}
    regs, _links = B.discover(None, "stack", seen)
    globs = sorted({g for r in regs for g in r.globs})
    exclusions = B.installer_root_exclusions(si)
    leaks = []
    for rel in sorted(rels):
        if B.OWN_ARTIFACT_DIR in Path(rel).parts:
            continue
        if rel in exclusions:
            continue
        matched = [g for g in globs if repo_walk.glob_match(rel, Path(rel).name, g)]
        if matched:
            leaks.append(f"{rel} (matches {matched[0]!r})")
    return leaks


def test_every_planned_installer_destination_any_extractor_would_claim_is_excluded(
        tmp_path: Path):
    """T153 round 4's guard, ENUMERATION-shaped (see `_planned_installer_destinations`
    for why walking one installed tree was not enough): this row has now found the
    identical defect in THREE separate passes (a root file, a CI workflow, `.claude/`'s
    own extras) because a hand-kept exclusion list leaks every time somebody reads the
    installer instead of running it. This calls the REAL planning functions and the
    REAL default extractor registry, then fails BY NAME on anything outside
    `OWN_ARTIFACT_DIR` a registered glob would claim and `installer_root_exclusions`
    does not yet cover. A fourth leak is a red test here, not a fourth reading of this
    row. Its own positive control is
    `test_the_completeness_guard_fails_on_a_planted_destination`, immediately below.
    """
    si = B.load_installer()
    root = tmp_path / "repo"
    root.mkdir()
    ops = _planned_installer_destinations(si, root)
    leaks = _unexcluded_leaks({o["rel"] for o in ops}, si)
    assert leaks == [], (
        "installer-planned destination(s) a registered extractor would claim, and "
        f"installer_root_exclusions() does not exclude: {leaks}")


def test_the_completeness_guard_fails_on_a_planted_destination(tmp_path: Path):
    """The guard above is worth nothing if nothing has ever watched it fire
    (CLAUDE.md's own rule for a scanner control, applied to this one): a session-only
    probe protects nobody, because nobody re-runs it. COMMITTED here instead. Plants
    two destinations `installer_root_exclusions` has never named, at the two shapes
    round 4's reviewer specifically tried against round 3's guard and confirmed it
    caught: a brand-new TOP-LEVEL directory, and a path three levels inside a KNOWN
    directory (`.claude/`) -- then asserts the SAME leak-detection function the guard
    above calls reports both by name.
    """
    si = B.load_installer()
    root = tmp_path / "repo"
    root.mkdir()
    rels = {o["rel"] for o in _planned_installer_destinations(si, root)}

    planted_top_level = "a-brand-new-tool/nobody-added-yet.py"
    planted_nested = ".claude/hooks/nested/three/deep.py"
    rels |= {planted_top_level, planted_nested}

    leaks = _unexcluded_leaks(rels, si)
    assert any(planted_top_level in leak for leak in leaks), (
        f"the guard must catch a planted TOP-LEVEL leak; got {leaks}")
    assert any(planted_nested in leak for leak in leaks), (
        f"the guard must catch a planted leak nested inside a KNOWN directory; got {leaks}")


# ── the four empties, each with its own exit code ─────────────────────────────


def test_no_extractors_registered_is_a_refusal(tmp_path: Path, monkeypatch):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    report = run_build(cfg, tmp_path / "out", tmp_path / "no-extractors-here")
    assert report["status"] == "no-extractors"
    assert any("no extractors are registered" in w for w in report["warnings"])

    monkeypatch.setattr(B, "EXTRACTOR_ROOT", tmp_path / "no-extractors-here")
    assert B.main(["--workspace", str(cfg), "--out", str(tmp_path / "out2")]) == 2


def test_registered_but_nothing_activated_is_a_refusal(tmp_path: Path):
    """Distinct from 'no extractors', and it must be — one is an unbuilt pipeline, the other
    is globs that do not match the trees on disk, and they are fixed in different places."""
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("*.nope",))

    report = run_build(cfg, tmp_path / "out", ext)
    assert report["status"] == "no-activation"
    assert any("misconfiguration" in w for w in report["warnings"])
    assert any("1 extractor(s) are registered" in w for w in report["warnings"])


def test_activation_with_zero_edges_warns_but_succeeds(tmp_path: Path, monkeypatch):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",))          # emits a node, no edges

    report = run_build(cfg, tmp_path / "out", ext)
    assert report["status"] == "no-edges"
    assert any("ZERO edges" in w for w in report["warnings"])

    monkeypatch.setattr(B, "EXTRACTOR_ROOT", ext)
    rc = B.main(["--workspace", str(cfg), "--out", str(tmp_path / "out2")])
    assert rc == 0, "a workspace with no couplings is a legitimate answer"


def test_a_normal_build_is_ok(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)

    report = run_build(cfg, tmp_path / "out", ext)
    assert report["status"] == "ok"
    assert report["totals"]["edges"] == 1
    assert report["warnings"] == []


def test_components_claimed_by_nobody_are_named(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"], "docs": ["README.md"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)

    report = run_build(cfg, tmp_path / "out", ext)
    assert report["status"] == "ok"
    assert report["components_unclaimed"] == ["docs"]
    assert any("docs" in w and "coverage hole" in w for w in report["warnings"])


def test_an_unclaimed_component_is_not_an_error(tmp_path: Path, monkeypatch):
    """A coverage hole is a finding, not a failure — a docs-only component legitimately has
    no extractor, and exiting non-zero on one would train people to ignore the exit code."""
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"], "docs": ["README.md"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)
    monkeypatch.setattr(B, "EXTRACTOR_ROOT", ext)
    assert B.main(["--workspace", str(cfg), "--out", str(tmp_path / "out")]) == 0


# ── one broken extractor must not cost the workspace its graph ────────────────


def test_a_raising_extractor_is_recorded_and_the_build_continues(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    make_extractor_pkg(ext, "boom", """
        class _E:
            name = "boom"
            INPUT_GLOBS = ("pom.xml",)
            def extract(self, ctx):
                raise ValueError("upstream ate the file")
        EXTRACTORS = (_E(),)
    """)
    simple_extractor(ext, "good", "good", ("pom.xml",), body=EDGE_BODY)

    report = run_build(cfg, tmp_path / "out", ext)
    assert report["status"] == "ok"
    assert report["totals"]["edges"] == 1, "the working extractor still ran"
    boom = next(e for e in report["extractors"] if e["name"] == "boom")
    assert "ValueError: upstream ate the file" in boom["failed"]
    assert any("boom" in w and "emitted nothing" in w for w in report["warnings"])


def test_every_activated_extractor_failing_is_a_refusal(tmp_path: Path, monkeypatch):
    """One broken reader is a warning; all of them broken is not the same fact.

    Without its own state this lands in `no-edges` and exits 0 — a wholly broken pipeline
    reporting a workspace with no couplings, which a CI check on the exit code would pass.
    """
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    make_extractor_pkg(ext, "boom", """
        class _E:
            name = "boom"
            INPUT_GLOBS = ("pom.xml",)
            def extract(self, ctx):
                raise ValueError("nope")
        EXTRACTORS = (_E(),)
    """)
    report = run_build(cfg, tmp_path / "out", ext)
    assert report["status"] == "all-failed"
    assert any("not a statement about the workspace" in w for w in report["warnings"])

    monkeypatch.setattr(B, "EXTRACTOR_ROOT", ext)
    assert B.main(["--workspace", str(cfg), "--out", str(tmp_path / "out2")]) == 2


def test_one_failure_among_several_is_still_a_successful_build(tmp_path: Path, monkeypatch):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    make_extractor_pkg(ext, "boom", """
        class _E:
            name = "boom"
            INPUT_GLOBS = ("pom.xml",)
            def extract(self, ctx):
                raise ValueError("nope")
        EXTRACTORS = (_E(),)
    """)
    simple_extractor(ext, "good", "good", ("pom.xml",), body=EDGE_BODY)
    monkeypatch.setattr(B, "EXTRACTOR_ROOT", ext)
    assert B.main(["--workspace", str(cfg), "--out", str(tmp_path / "out")]) == 0


def test_an_extractor_returning_the_wrong_type_is_recorded(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    make_extractor_pkg(ext, "wrong", """
        class _E:
            name = "wrong"
            INPUT_GLOBS = ("pom.xml",)
            def extract(self, ctx):
                return {"nodes": [], "edges": []}
        EXTRACTORS = (_E(),)
    """)
    report = run_build(cfg, tmp_path / "out", ext)
    wrong = next(e for e in report["extractors"] if e["name"] == "wrong")
    assert "not an ExtractResult" in wrong["failed"]


# ── emission ──────────────────────────────────────────────────────────────────


def test_each_extractor_gets_its_own_shard_plus_a_merged_graph(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)
    out = tmp_path / "out"

    run_build(cfg, out, ext)
    assert (out / "extract" / "a.jsonl").is_file()
    assert (out / "extract" / "a.unresolved.jsonl").is_file()
    assert (out / "graph.jsonl").is_file()
    assert (out / "unresolved.jsonl").is_file()
    assert (out / "manifest.json").is_file()
    assert (out / "build_report.json").is_file()


def test_unresolved_rows_reach_the_merged_file(tmp_path: Path):
    """`unresolved` is an output, not a log line: an extractor that drops what it could not
    understand makes the graph look complete when it is not."""
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    make_extractor_pkg(ext, "u", """
        from tools.graph.core.model import ExtractResult
        class _E:
            name = "u"
            INPUT_GLOBS = ("pom.xml",)
            def extract(self, ctx):
                return ExtractResult(unresolved=[{"why": "placeholder has no default",
                                                  "raw": "${SOME_KEY}"}])
        EXTRACTORS = (_E(),)
    """)
    out = tmp_path / "out"
    report = run_build(cfg, out, ext)

    assert report["totals"]["unresolved"] == 1
    body = (out / "unresolved.jsonl").read_text(encoding="utf-8")
    assert "placeholder has no default" in body


def test_every_component_becomes_a_node_even_with_no_extractor(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"one": ["a.txt"], "two": ["b.txt"], "three": ["c.txt"]})
    report = run_build(cfg, tmp_path / "out", tmp_path / "none")
    assert report["totals"]["nodes"] == 3


def test_removing_an_inert_component_changes_only_its_own_node(tmp_path: Path):
    """The pipeline half of the fixture's negative control: a component contributes its own
    node and nothing else, so deleting an inert one cannot perturb anything between others."""
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)

    with_inert = make_workspace(tmp_path / "w1", {"svc": ["pom.xml"], "inert": ["notes.md"]})
    without = make_workspace(tmp_path / "w2", {"svc": ["pom.xml"]})
    r1 = run_build(with_inert, tmp_path / "o1", ext)
    r2 = run_build(without, tmp_path / "o2", ext)

    assert r1["totals"]["nodes"] == r2["totals"]["nodes"] + 1
    edges1 = [ln for ln in (tmp_path / "o1" / "graph.jsonl").read_text().splitlines()
              if '"kind": "edge"' in ln]
    edges2 = [ln for ln in (tmp_path / "o2" / "graph.jsonl").read_text().splitlines()
              if '"kind": "edge"' in ln]
    assert edges1 == edges2


# ── determinism ───────────────────────────────────────────────────────────────


def test_two_builds_of_one_workspace_are_byte_identical(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)

    run_build(cfg, tmp_path / "o1", ext)
    run_build(cfg, tmp_path / "o2", ext)
    assert (tmp_path / "o1" / "graph.jsonl").read_bytes() == \
           (tmp_path / "o2" / "graph.jsonl").read_bytes()


def test_a_different_run_id_does_not_change_the_graph(tmp_path: Path):
    """`rebuild == snapshot` is an extractor regression check, so build metadata must stay
    out of the serialized record — otherwise the check is red on every run and gets ignored."""
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)

    run_build(cfg, tmp_path / "o1", ext, run_id="aaaaaaaa")
    run_build(cfg, tmp_path / "o2", ext, run_id="bbbbbbbb")
    assert (tmp_path / "o1" / "graph.jsonl").read_bytes() == \
           (tmp_path / "o2" / "graph.jsonl").read_bytes()


def test_the_run_id_is_stamped_onto_edges_in_memory(tmp_path: Path):
    """An extractor that forgets to carry ctx.run_id would emit edges no build owns, which is
    invisible until someone tries to expire an old run's rows."""
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)

    regs = B.discover_extractors(ext)
    comps = [{"name": "svc", "path": "svc", "role": "authoritative", "stack": "java-maven"}]
    root = cfg.parent
    act = B.activate(regs, comps, root, workspace=comps)
    results, failures, violations = B.run_extractors(regs, act, comps, root, "zzz12345", {})
    assert failures == {} and violations == []
    assert all(e.run_id == "zzz12345" for e in results["a"].edges)


# ── the manifest ──────────────────────────────────────────────────────────────


def test_the_manifest_stamps_every_component_and_counts_every_extractor(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"one": ["pom.xml"], "two": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)
    out = tmp_path / "out"

    run_build(cfg, out, ext)
    mf = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert set(mf["repos"]) == {"one", "two"}
    assert mf["counts"]["a"] > 0
    assert mf["extractor_version"]
    # No per-file map is asserted, because none is written. The files an
    # extractor read are digested per (component, extractor) by `input_digests`
    # and read back by `stamp_matches`; the per-FILE copy that used to live here
    # was read by nothing and shipped 1.34 MB of 40-hex strings into every
    # adopter's committed graph, where the secret gate reported 269 of them as
    # API keys. Held by test_manifest_entropy.py.
    assert "files" not in mf


def test_two_builds_of_one_workspace_write_the_same_manifest(tmp_path: Path):
    """`.sdlc/graph/` is COMMITTED on purpose, so the manifest is a reviewed artifact: two
    builds of identical inputs must produce identical BYTES, or every re-run dirties the
    adopter's repository and the `rebuild == snapshot` regression check is red on every run
    and gets ignored. The run ids differ on purpose — a build id is metadata about the run,
    not a fact about the graph, which is why item 102 took the digest map out of this same
    file. The graph shards are already held this way (test_golden_chain.py); the manifest
    was not."""
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)

    run_build(cfg, tmp_path / "o1", ext, run_id="aaaaaaaaaaaa")
    run_build(cfg, tmp_path / "o2", ext, run_id="bbbbbbbbbbbb")
    first = (tmp_path / "o1" / "manifest.json").read_bytes()
    second = (tmp_path / "o2" / "manifest.json").read_bytes()
    assert first == second, "the committed manifest records the graph, not the run"
    assert b"run_id" not in first, "build metadata must not reach the serialized record"


def test_a_real_change_does_move_the_manifest(tmp_path: Path):
    """The control for the test above: identical bytes must mean "nothing changed", not
    "the file says nothing". A component that actually joined the workspace has to show up
    in the manifest a reviewer diffs."""
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)
    run_build(make_workspace(tmp_path, {"svc": ["pom.xml"]}),
              tmp_path / "o1", ext, run_id="aaaaaaaaaaaa")
    before = (tmp_path / "o1" / "manifest.json").read_bytes()

    run_build(make_workspace(tmp_path, {"svc": ["pom.xml"], "other": ["pom.xml"]}),
              tmp_path / "o2", ext, run_id="aaaaaaaaaaaa")
    after = (tmp_path / "o2" / "manifest.json").read_bytes()

    assert after != before, "a real change must still move the committed manifest"
    assert set(json.loads(after)["repos"]) == {"svc", "other"}
    assert json.loads(after)["counts"]["a"] > json.loads(before)["counts"]["a"]


# ── selection flags fail loudly on a name that does not exist ─────────────────


def test_only_with_an_unknown_name_is_a_hard_error(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",))
    with pytest.raises(B.BuildError, match="--only names typo.*Registered: a"):
        run_build(cfg, tmp_path / "out", ext, only=["typo"])


def test_components_with_an_unknown_name_is_a_hard_error(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    with pytest.raises(B.BuildError, match="--components names nope.*Known: svc"):
        run_build(cfg, tmp_path / "out", None, components_filter=["nope"])


def test_only_restricts_what_runs(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)
    simple_extractor(ext, "b", "b", ("pom.xml",), body=EDGE_BODY)
    report = run_build(cfg, tmp_path / "out", ext, only=["a"])
    assert [e["name"] for e in report["extractors"]] == ["a"]


def test_components_restricts_what_is_built(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"one": ["pom.xml"], "two": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)
    report = run_build(cfg, tmp_path / "out", ext, components_filter=["one"])
    assert [c["name"] for c in report["components"]] == ["one"]


# ── the output location is a refusal, and an ALLOWLIST ────────────────────────


@pytest.mark.parametrize("bad", ["", "docs", "docs/m0", "graph/snapshot", "tools/graph"])
def test_writing_inside_the_repo_but_outside_scratch_is_refused(tmp_path: Path, bad: str):
    repo = tmp_path / "repo"
    (repo / ".tmp").mkdir(parents=True)
    assert B.check_output_dir(repo / bad if bad else repo, repo_root=repo) is not None


@pytest.mark.parametrize("ok", [".tmp", ".tmp/graph", ".tmp/anything/deep"])
def test_writing_under_the_scratch_dir_is_allowed(tmp_path: Path, ok: str):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    assert B.check_output_dir(repo / ok, repo_root=repo) is None


def test_a_path_outside_the_repo_is_not_this_tools_business(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    assert B.check_output_dir(tmp_path / "elsewhere", repo_root=repo) is None


def test_the_refusal_fires_through_main(tmp_path: Path, capsys):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    rc = B.main(["--workspace", str(cfg), "--out", "docs/m0"])
    assert rc == 2
    assert "refusing to write" in capsys.readouterr().out


def test_the_default_output_is_inside_the_scratch_dir():
    assert B.check_output_dir(Path(".tmp/graph")) is None


# ── the workspace config is the only route in ─────────────────────────────────


def test_a_broken_workspace_config_surfaces_as_a_build_error(tmp_path: Path, capsys):
    cfg = tmp_path / "ws.toml"
    cfg.write_text('schema_version = 1\nroot = "does-not-exist"\n', encoding="utf-8")
    with pytest.raises(B.BuildError):
        run_build(cfg, tmp_path / "out", None)
    assert B.main(["--workspace", str(cfg), "--out", str(tmp_path / "out")]) == 2
    assert "!!" in capsys.readouterr().out


# ── zero-config: the repository you are standing in (item 77) ─────────────────
#
# `test_main_requires_a_workspace` used to live here and pinned `B.main([])` raising
# SystemExit. Item 77 reverses that, so the test is RETARGETED rather than deleted: the
# claim worth keeping was never "a bare invocation fails" but **"a bare invocation never
# silently guesses"** — outside a repository it says so and names the directory, and inside
# one it builds exactly that repository. Same property, different mechanism.


def make_repo(parent: Path, name: str, files: tuple[str, ...] = ("pom.xml",)) -> Path:
    repo = parent / name
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    for rel in files:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("x\n", encoding="utf-8")
    return repo


def test_a_bare_invocation_outside_any_repository_is_a_hard_error(tmp_path: Path, capsys):
    rc = B.main(["--target", str(tmp_path), "--out", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 2
    assert "not inside a git repository" in out and str(tmp_path.resolve()) in out


def test_a_bare_invocation_inside_a_repository_builds_that_repository(tmp_path: Path, capsys):
    repo = make_repo(tmp_path / "ws", "orders")
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)
    report = B.build(None, tmp_path / "out", target=repo, extractor_root=ext,
                     run_id="testrun0001")
    assert [c["name"] for c in report["components"]] == ["orders"]
    # The NAME, never the path — item 120. This asserted the absolute path of
    # the machine that built it, in a file `.sdlc/graph/` commits, which is what
    # the assertion was pinning in place.
    assert report["workspace"]["inferred_from"] == repo.name
    assert report["workspace"]["config"] == ""
    assert "root" not in report["workspace"], (
        "the committed report carries the build machine's directory layout")


def test_the_report_says_a_workspace_was_inferred_rather_than_read(tmp_path: Path, capsys):
    """How a graph came to describe what it describes is part of the record. A build that
    inferred its workspace must not print indistinguishably from one that was told."""
    repo = make_repo(tmp_path / "ws", "orders")
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)
    report = B.build(None, tmp_path / "out", target=repo, extractor_root=ext)

    # Without the live workspace the console can only say what the report says,
    # and the report no longer carries a path. The DISTINCTION this test is about
    # survives either way.
    B._print_report(report)
    printed = capsys.readouterr().out
    assert "inferred" in printed and repo.name in printed
    assert str(repo.resolve()) not in printed

    # With it, the operator still sees where the build ran — a console line is
    # nobody else's repository. That is the half item 120 kept.
    B._print_report(report, {"root": repo.resolve()})
    printed = capsys.readouterr().out
    assert "inferred" in printed and str(repo.resolve()) in printed


def test_target_and_workspace_cannot_be_combined(tmp_path: Path, capsys):
    """Refused, not ignored. Silently dropping --target means the thing the developer asked
    for did not happen and nothing says so — the failure this whole file is built around."""
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    rc = B.main(["--workspace", str(cfg), "--target", str(tmp_path), "--out",
                 str(tmp_path / "out")])
    assert rc == 2
    assert "cannot be combined" in capsys.readouterr().out


# ── where a repository's own graph may be written (Rev 12) ────────────────────


def test_a_repository_may_carry_its_own_graph(tmp_path: Path):
    """The Rev 12 rule: a repository may carry its own facts and nothing else. Its own
    `.sdlc/` is the committed home for them, so it is allowed where any other in-repo path
    would be refused."""
    repo = make_repo(tmp_path / "ws", "orders")
    assert B.check_output_dir(repo / ".sdlc" / "graph", own_repo=repo) is None


def test_writing_outside_sdlc_in_its_own_repository_is_refused(tmp_path: Path):
    repo = make_repo(tmp_path / "ws", "orders")
    refusal = B.check_output_dir(repo / "graphdump", own_repo=repo)
    assert refusal is not None and ".sdlc" in refusal


def test_a_graph_may_not_be_written_into_a_different_repository(tmp_path: Path):
    """The half the old rule could not see. It declared everything outside this repo the
    developer's own business, which stopped being true once output was written into
    repositories at all: a workspace graph committed into one component carries every
    sibling's names into a tree that may not be as private as the workspace is."""
    subject = make_repo(tmp_path / "ws", "orders")
    other = make_repo(tmp_path / "ws", "billing")
    refusal = B.check_output_dir(other / ".sdlc" / "graph", own_repo=subject)
    assert refusal is not None and "is not about" in refusal


def test_a_workspace_graph_is_refused_by_every_repository_but_a_declared_home(tmp_path: Path):
    """With no home declared a spanning graph names no owner on its own, so no repository may
    carry it. Declaring a hub as its home (`own_repo`, from `--home`) permits exactly that
    hub's own `.sdlc/` — every OTHER repository stays refused even so."""
    other = make_repo(tmp_path / "ws", "billing")
    # No declared home: refused.
    assert B.check_output_dir(other / ".sdlc" / "graph") is not None
    # `other` declared as its home: its own .sdlc/ is permitted...
    assert B.check_output_dir(other / ".sdlc" / "graph", own_repo=other) is None
    # ...but a different repository stays refused even with `other` declared.
    third = make_repo(tmp_path / "ws", "shipping")
    assert B.check_output_dir(third / ".sdlc" / "graph", own_repo=other) is not None


def test_the_zero_config_default_output_is_the_repositorys_own_sdlc(tmp_path: Path, capsys):
    """Rev 12's destination, and the reason item 77 needed one: a build with no config file
    still has to write somewhere, and that somewhere is the repository's own committed
    artifact directory rather than a scratch dir the developer would have to go find.

    Runs the REAL extractor set, so this also proves the whole pipeline reaches the end
    zero-config rather than only that the argument parsing does.
    """
    repo = make_repo(tmp_path / "ws", "orders")
    B.main(["--target", str(repo)])
    assert str(repo / ".sdlc" / "graph") in capsys.readouterr().out


def test_a_config_file_still_defaults_to_the_scratch_dir(tmp_path: Path, capsys):
    """Unchanged by item 77: a workspace graph is about no single repository, so it keeps
    writing to the gitignored scratch dir rather than into any component."""
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    B.main(["--workspace", str(cfg)])
    assert str(Path(B.SCRATCH_DIR) / "graph") in capsys.readouterr().out


def test_the_dirty_stamp_is_taken_before_the_build_writes_into_the_tree(tmp_path: Path):
    """T423: `git_dirty` is a plain `git status --porcelain`, and the build asked it
    AFTER writing its shards. With the graph inside the component - where a committed
    `.sdlc/graph/` lives - the build saw its own output as the dirt, so a clean checkout
    was recorded dirty on every rebuild and the fact meant nothing. A real repository,
    committed and clean, with the output landing inside it."""
    import subprocess

    repo = tmp_path / "ws" / "orders"
    repo.mkdir(parents=True)
    (repo / "pom.xml").write_text("x\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)
    out = repo / ".sdlc" / "graph"
    B.build(None, out, target=repo, extractor_root=ext, run_id="testrun0001")
    stamp = json.loads((out / "manifest.json").read_text(encoding="utf-8"))["repos"]["orders"]
    assert stamp["dirty"] is False, "a clean checkout was stamped dirty by the build's own output"
    # The control: the tree really is dirty NOW - the output is there, uncommitted -
    # so a stamp taken after the writes could not have read clean.
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                            capture_output=True, text=True, check=True).stdout
    assert ".sdlc/" in status, status


def test_an_ungraded_group_prints_marked_and_legended(tmp_path: Path, capsys):
    """T427: the printed report marks a group that is a whole KIND in one bucket - not
    a pattern anyone chose - with `*` and a legend, and nothing pinned it: deleting the
    marker left every test green, beside a comment calling it the point of the report.
    A real build's report, given one ungraded and one graded group."""
    repo = make_repo(tmp_path / "ws", "orders")
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)
    report = B.build(None, tmp_path / "out", target=repo, extractor_root=ext,
                     run_id="testrun0001")
    report["unresolved_patterns"] = [
        {"extractor": "a", "kind": "unnamed-call", "pattern": "", "graded": False,
         "count": 3, "components": ["orders"]},
        {"extractor": "a", "kind": "named", "pattern": "a-shape", "graded": True,
         "count": 1, "components": ["orders"]}]
    report["totals"]["unresolved"] = 4
    capsys.readouterr()
    B._print_report(report)
    printed = capsys.readouterr().out
    assert "unnamed-call *" in printed, printed
    assert "grouped by kind alone" in printed, printed
    assert "a-shape" in printed and "a-shape *" not in printed, printed
