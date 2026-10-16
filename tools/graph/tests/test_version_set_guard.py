"""Batch 22 (T617; design pass 22, section 3, option B): the extractor version digests the
extraction path, not every file of the harness - so a guard in three parts holds the set to
every module a build can run, or a reader importing harness code outside it would shape a shard
unseen.

1. Every module `discover_extractors()` loads lies in the set.
2. With a baseline taken after ALL THREE discoveries - the stack readers', the link passes' and
   the selected profile's - and before extraction, a full build of each of two fixture estates,
   one selecting no profile (`pantry`) and one selecting a profile (the golden `orderflow`),
   loads no further harness module outside the set and its named exclusions. A harness module
   is a `.py` under the running harness's root that git tracks - never an environment's
   site-packages, which `uv sync` puts under the root in `.venv/` - and a git listing that
   fails or comes back empty raises `CouldNotLook`, never a scan of nothing called clean.
3. Every `import` and `from ... import` in a file of the set that names a harness module names
   one in the set or a named exclusion, read from each file's syntax tree at any depth - the
   one named exception `build.py`'s import of `links.canonical` inside `main()` for
   `--propose-canonical`, a door that runs after the build and writes no shard.

The named exclusions: `tools/__init__.py` and `tools/cli.py`, which `build.py` imports for
`cli.use_utf8()` alone - stdout's encoding, never a shard's bytes - and `sdlc-init.py`, which
every door loads, its component list reaching every stamp through `config_sha`.

Its reach, stated: parts 1 and 2 see the two fixture estates' build paths; part 3 sees import
statements, never an import by computed name (`importlib.import_module`), which discovery makes
for readers (part 1) and `srcfacts` for third-party grammar modules alone.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
from tools.graph.core.lib import srcfacts  # noqa: E402
from tools.tests.ban import CouldNotLook  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXCLUSIONS = {"tools/__init__.py", "tools/cli.py", "sdlc-init.py"}
#: The one named exception (part 3): `build.py`'s `--propose-canonical` import, two files.
EXCEPTION = ("tools/graph/build.py",
             {"tools/graph/links/__init__.py", "tools/graph/links/canonical/__init__.py"})


def tracked(root: Path = ROOT, run=subprocess.run) -> set[str]:
    """The `.py` files git tracks under the harness root - a listing that fails or is empty
    raises `CouldNotLook`, as `test_no_stale_shas.py` does, never an empty set read as clean."""
    proc = run(["git", "-C", str(root), "ls-files", "-z", "--", "*.py"],
               capture_output=True, text=True, encoding="utf-8", errors="replace")
    names = {name for name in proc.stdout.split("\0") if name}
    if proc.returncode != 0 or not names:
        raise CouldNotLook(f"git ls-files answered exit {proc.returncode} with "
                           f"{len(names)} file(s): {proc.stderr.strip()}")
    return names


def spelled(files) -> set[str]:
    """The tracked harness `.py` files among `files` (absolute paths), by their spelling."""
    known = tracked()
    out = set()
    for name in files:
        path = Path(name).resolve()
        if path.is_relative_to(ROOT):
            rel = path.relative_to(ROOT).as_posix()
            if rel in known:
                out.add(rel)
    return out


def stack() -> list:
    return B.discover_extractors()


def with_orderflow() -> list:
    seen: dict[str, str] = {}
    regs, _links = B.discover(None, "stack", seen)
    profile, _ = B.discover_package(B.PROFILE_ROOT / "orderflow", "profile", seen,
                                    root_label="profiles")
    return B.order_extractors(regs + profile)


def the_set(regs) -> set[str]:
    return {spelling for spelling, _path in B.version_files(regs)}


# ── part 1 ─────────────────────────────────────────────────────────────────────────────────


DISCOVERY = """
import json, sys
sys.path.insert(0, sys.argv[1])
import tools.graph.build as B
before = set(sys.modules)
B.discover_extractors()
print(json.dumps(sorted(getattr(sys.modules[n], "__file__", None) or ""
                        for n in set(sys.modules) - before)))
"""


def loaded_by(script: str, *args: str) -> list[str]:
    proc = subprocess.run([sys.executable, "-c", script, str(ROOT), *args], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return [f for f in json.loads(proc.stdout.strip().splitlines()[-1]) if f]


def test_part_1_every_module_discovery_loads_lies_in_the_set():
    loaded = spelled(loaded_by(DISCOVERY))
    assert len(loaded) >= 10, f"discovery loaded {sorted(loaded)}: the probe saw too little"
    assert loaded <= the_set(stack()), sorted(loaded - the_set(stack()))


# ── part 2 ─────────────────────────────────────────────────────────────────────────────────


BUILD = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import tools.graph.build as B
baseline = []
real = B.activate


def activate(*args, **kwargs):
    # After all three discoveries - stack, links, the selected profile - before extraction.
    if not baseline:
        baseline.append(set(sys.modules))
    return real(*args, **kwargs)


B.activate = activate
B.build(Path(sys.argv[2]), Path(sys.argv[3]), run_id="guard")
assert baseline, "the build never reached activation"
print(json.dumps({"baseline": sorted(baseline[0]),
                  "after": sorted(getattr(sys.modules[n], "__file__", None) or ""
                                  for n in set(sys.modules) - baseline[0])}))
"""


def build_probe(toml: Path, out: Path) -> tuple[set[str], list[str]]:
    """The modules held at the baseline, and the files of those loaded after it."""
    proc = subprocess.run([sys.executable, "-c", BUILD, str(ROOT), str(toml), str(out)],
                          cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    seen = json.loads(proc.stdout.strip().splitlines()[-1])
    return set(seen["baseline"]), [f for f in seen["after"] if f]


def outside(loaded: list[str], regs) -> list[str]:
    """Part 2's verdict: each harness module loaded after the baseline outside the set and
    its exclusions."""
    return sorted(spelled(loaded) - the_set(regs) - EXCLUSIONS)


@pytest.mark.parametrize("estate, regs", [("pantry", stack), ("orderflow", with_orderflow)])
def test_part_2_a_build_loads_no_harness_module_outside_the_set(tmp_path, estate, regs):
    baseline, loaded = build_probe(FIXTURES / estate / "workspace.toml", tmp_path / "out")
    assert outside(loaded, regs()) == []
    # The baseline is taken after all THREE discoveries (round 7): the link passes' packages
    # and, where one is selected, the profile's are already held - a baseline before the
    # profile's discovery would see the profile load after it.
    assert {"tools.graph.links.canonical", "tools.graph.links.namejoin"} <= baseline
    if estate == "orderflow":
        assert "tools.graph.profiles.orderflow" in baseline


def test_part_2_never_counts_an_environments_site_packages(tmp_path):
    """`.venv/` lies under the harness root and git tracks none of it: a grammar module loaded
    during extraction is never a harness module (D22-85)."""
    _baseline, loaded = build_probe(FIXTURES / "orderflow" / "workspace.toml",
                                    tmp_path / "out")
    grammar = [f for f in loaded if "tree_sitter_java" in Path(f).parts]
    if srcfacts.available("java"):   # branch and assert, never skip
        assert grammar, "the Java grammar is loaded during extraction, after the baseline"
        assert not spelled(grammar), grammar
    else:
        assert not grammar
    assert not spelled(f for f in loaded if "site-packages" in Path(f).parts)


CONTROL_PROFILE = """\
from tools.graph.core.model import ExtractResult


class Reader:
    name = "b22-imports-outside"
    INPUT_GLOBS = ("*.b22",)

    def extract(self, ctx):
        import tools.graph.pack  # noqa: F401 - outside the version's set
        return ExtractResult()


EXTRACTORS = (Reader(),)
"""


def test_part_2_catches_a_reader_importing_outside_the_set_inside_extract(tmp_path):
    from tools.graph.tests._estate import lay_down
    toml = lay_down(tmp_path, {"a": {"x.b22": "x\n"}})
    toml.write_text(toml.read_text(encoding="utf-8").replace('profile = ""',
                                                             'profile = "./prof"'),
                    encoding="utf-8")
    (toml.parent / "prof").mkdir()
    (toml.parent / "prof" / "__init__.py").write_text(CONTROL_PROFILE, encoding="utf-8")
    _baseline, loaded = build_probe(toml, tmp_path / "out")
    regs = stack() + B.discover_package(toml.parent / "prof", "profile", {},
                                        root_label="profiles")[0]
    assert "tools/graph/pack.py" in outside(loaded, regs)


def test_the_listing_refuses_to_answer_with_nothing():
    """`CouldNotLook` on an empty listing at exit 0 and on a failed one - the rule this repo
    has caught the most bugs with."""
    class Answered:
        def __init__(self, code, out):
            self.returncode, self.stdout, self.stderr = code, out, "no"

    with pytest.raises(CouldNotLook):
        tracked(run=lambda *a, **k: Answered(0, ""))
    with pytest.raises(CouldNotLook):
        tracked(run=lambda *a, **k: Answered(128, "tools/x.py\0"))
    assert tracked(run=lambda *a, **k: Answered(0, "tools/x.py\0")) == {"tools/x.py"}


# ── part 3 ─────────────────────────────────────────────────────────────────────────────────


def module_files(dotted: str) -> set[str]:
    """The tracked harness files importing `dotted` loads - each package `__init__.py` on its
    path and the module itself - or none where it names no harness module."""
    known = tracked()
    out: set[str] = set()
    parts = dotted.split(".")
    for i in range(1, len(parts) + 1):
        base = "/".join(parts[:i])
        for rel in (f"{base}/__init__.py", f"{base}.py"):
            if rel in known:
                out.add(rel)
                break
        else:
            if i < len(parts) and not out:
                return set()
    return out


def imports_in(spelling: str, source: str) -> set[str]:
    """Every tracked harness file an import statement in `source` names, at any depth."""
    package = spelling.rsplit("/", 1)[0].split("/")
    named: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                named |= module_files(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[:len(package) - node.level + 1]
                dotted = ".".join(base + ([node.module] if node.module else []))
            else:
                dotted = node.module or ""
            named |= module_files(dotted)
            for alias in node.names:
                named |= module_files(f"{dotted}.{alias.name}") - module_files(dotted)
    return named


def stray_imports(regs) -> dict[str, list[str]]:
    """Part 3's verdict: per file of the set, each harness file its imports name outside the
    set, its exclusions and the one named exception."""
    allowed = the_set(regs) | EXCLUSIONS
    out: dict[str, list[str]] = {}
    for spelling, path in B.version_files(regs):
        named = imports_in(spelling, path.read_text(encoding="utf-8"))
        if spelling == EXCEPTION[0]:
            named -= EXCEPTION[1]
        if stray := sorted(named - allowed):
            out[spelling] = stray
    return out


def test_part_3_every_import_in_the_set_names_the_set_or_an_exclusion():
    assert stray_imports(with_orderflow()) == {}
    assert imports_in("tools/graph/build.py",
                      (ROOT / "tools/graph/build.py").read_text(encoding="utf-8")) >= EXCEPTION[1], (
        "the named exception is still where it is named")


@pytest.mark.parametrize("source, named", [
    ("import tools.graph.pack\n", {"tools/__init__.py", "tools/graph/__init__.py",
                                   "tools/graph/pack.py"}),
    ("def f():\n    from tools.graph import impact\n", {"tools/__init__.py",
                                                        "tools/graph/__init__.py",
                                                        "tools/graph/impact.py"}),
    ("from tools import cli\n", {"tools/__init__.py", "tools/cli.py"}),
    ("import yaml\nimport json\n", set()),
    ("from ..model import SCHEMA_VERSION\n", {"tools/__init__.py", "tools/graph/__init__.py",
                                              "tools/graph/core/__init__.py",
                                              "tools/graph/core/model.py"}),
], ids=["a-top-level-import", "nested-in-a-function", "an-exclusion", "site-packages",
        "relative"])
def test_part_3_reads_each_import_statement(source, named):
    assert imports_in("tools/graph/core/lib/x.py", source) == named


def test_part_3_catches_an_import_outside_the_set_tools_included(tmp_path, monkeypatch):
    real = B.version_files
    stray = tmp_path / "stray.py"
    stray.write_text("import tools.sdlc.adr\nfrom tools.graph import pack\n", encoding="utf-8")

    def with_stray(regs, root=None):
        return [*real(regs, root), ("tools/graph/extractors/stray.py", stray)]
    monkeypatch.setattr(B, "version_files", with_stray)
    found = stray_imports(stack())
    assert set(found) == {"tools/graph/extractors/stray.py"}
    assert "tools/graph/pack.py" in found["tools/graph/extractors/stray.py"]
    assert {"tools/sdlc/__init__.py", "tools/sdlc/adr.py"} <= set(
        found["tools/graph/extractors/stray.py"]), "tools/ included"
