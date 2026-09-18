"""Derive the pre-push repo-wide-guard SWEEP SET by what a walk IS, not how it is spelled.

A *repo-wide guard* is a test whose input is the repository tree or its history: it walks
from the checkout root, so it can go red because of a file the run never named. Before every
push the whole set is run in one invocation, because a targeted `-k` run cannot see a guard
whose input is the tree, and twice it did not.

**The set must be DERIVED, and the obvious derivation is wrong the same way a hand-list is.**
The recipe this replaces greps each test file for the primitives a walk has been *spelled*
with so far -- `rglob`, `ls-files`, `os.walk`. That recipe under-derives, and it fails by the
very indirection a repo-wide guard is allowed to use:

* `test_artifact_crossings.py` walks the tree, but through `_sources()` **imported from**
  `test_dependencies.py` -- the primitive is not in the file that uses it, so a per-file grep
  cannot see it.
* `test_history_is_clean.py` walks git **history** (`rev-list --all`, `cat-file --batch`) and
  never touches the filesystem -- it matches none of the three filesystem primitives.

Four workers arrived at this from four directions. The recipe is not one pattern short; it is
the wrong *kind* of question twice over -- one miss needs the import graph, the other needs a
definition of "repo-wide" that does not mention the filesystem. So this module asks what a
repo-wide walk IS:

    A test is a repo-wide guard when it, a first-party test-helper it transitively imports, or a
    SHIPPED module function it CALLS, enumerates the WHOLE TREE or ALL HISTORY rooted at the
    repository ROOT --
      * a filesystem walk (rglob/glob/iterdir/os.walk/os.scandir) whose base traces to a
        ROOT anchor (a name derived from `Path(__file__)`), NOT to a `tmp_path`, a fixture,
        or a function parameter;
      * `git ls-files` / `git ls-tree` invoked against a ROOT anchor (`cwd=`/`-C`);
      * a git history read (`git rev-list` / `cat-file` / `for-each-ref`);
      * a call `MOD.func(...)` into a shipped (non-test) module whose `func` does one of the
        above on a module-constant root of ITS OWN (e.g. `build.discover` over `EXTRACTOR_ROOT`) --
        the walk primitive is then in production code no per-file grep of the test can see.

The classification is static (AST + the import graph); it runs nothing and touches no git,
so it works over a synthetic tree, which is how `test_the_derivation_reads_the_tree` watches
it drop a member when that member is removed from the tree -- because a derivation nobody has
watched fail is a hand-written list with extra steps.

**What this derivation cannot see, stated so the reader does not trust it past its evidence:**

* A walk reached through a SHIPPED (non-test) module is followed at an attribute-CALL site --
  `MOD.func(...)` where `func`, or another top-level function of MOD's OWN module that `func`
  calls, walks a module-constant root (`prepass.collect_rules`, `build.discover`, and by
  same-module delegation `build.build`/`main`/`discover_extractors`, `third_party._shipped`).
  The delegation closure is SAME-MODULE only, and a bare `import build` is not followed --
  following imports, or calls ACROSS modules, is the 66-file balloon this avoids. Residual blind
  spots: a walk reached only by a CROSS-module delegation (MOD.f calls a DIFFERENT module's
  walker); a default-root walker (`root = ANCHOR if root is None else root`) counted for EVERY
  caller, even one that always passes a non-root path (over-inclusion, the deliberate safe bias
  -- though an unconditional co-walk like `build`'s `LINK_ROOT` makes those callers genuine
  regardless); and a shipped module reached through a re-export or `importlib` the attribute-call
  resolution cannot follow.
* A SHIPPED history read (`rev-list`/`cat-file`/`for-each-ref`) counts only when anchored at a
  ROOT constant, NOT on a pure parameter: `metrics.commits(target)` runs `git -C str(target)
  rev-list` over the CALLER's repo, so it does not make `metrics.compute`'s callers repo-wide.
  The test-tree/self rule below still treats history unconditionally, which is why the synthetic
  `test_history.py` (a bare `rev-list` with no cwd) and `test_history_is_clean.py` are members.
* `rev-list`/`cat-file`/`for-each-ref` are treated as history walks unconditionally. Only
  `test_history_is_clean.py` uses them today, always against ROOT; a future test that reads
  the history of a THROWAWAY repo with those commands would be over-included. Over-inclusion
  runs one extra cheap guard; under-inclusion ships a bug, so the bias is deliberate.
* A walk base built at runtime that does not syntactically trace to a `__file__`-derived
  anchor (e.g. a root threaded through several parameters with no default) is invisible. The
  history rule above is the escape hatch for the one member in that shape.
* A walk into a fixtures/test-data subtree (`.../fixtures/...`) is NOT counted as repo-wide;
  it walks test data, not the source tree.

This file is itself a repo-wide guard (it walks the tree to derive the set), so it derives
itself into the sweep -- `test_the_derivation_includes_itself` pins that, and an integrator
re-deriving the set should expect it.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: git subcommands that read COMMITS or OBJECTS -- a history walk, whatever the working tree.
_HISTORY_SUBCOMMANDS = frozenset({"rev-list", "cat-file", "for-each-ref"})
#: git subcommands that enumerate the tracked tree; counted only when anchored at ROOT.
_TREE_SUBCOMMANDS = frozenset({"ls-files", "ls-tree"})
#: filesystem enumeration primitives (method names and os.* functions).
_FS_WALK_ATTRS = frozenset({"rglob", "glob", "iterdir", "walk", "scandir"})
#: path segments that mark a data/fixture subtree rather than the source tree.
_DATA_DIRS = frozenset({"fixtures", "fixture", "testdata", "golden"})
#: directories that are not first-party source and must never be walked -- the installed
#: environment above all: parsing site-packages' own `test_*.py` is slow AND a false-include
#: waiting to happen (a dependency whose test does `H = Path(__file__).parent; H.rglob(...)`
#: classifies as repo-wide), which is exactly the "walk over files nobody named" failure this
#: sweep exists to catch. Every other FS guard avoids the env by walking `tools/` or by going
#: through git (gitignore-aware); this one, which walks from ROOT, filters it out explicitly.
_SKIP_DIRS = frozenset({"__pycache__", ".venv", ".git"})

#: The repo-wide-guard sweep, held as the REGRESSION ANCHOR the derivation must cover (not the
#: definition, which is the code below): if the derivation stops covering one of these, the fix
#: is the derivation, not this list. It is the derived set MINUS this file (which
#: `test_the_derivation_includes_itself` pins on its own). SEVENTEEN of the 54 are what the
#: integrator's hand-list named; the derivation surfaced the other 37 -- the measure of how far
#: a hand-list drifts -- and each was opened and confirmed genuine:
#:   * subtree walks the hand-list omitted -- `test_connect.py`, `test_unresolved_contract.py`
#:     (walk `sdlc_templates/connectors`, `tools/graph/extractors`, like `test_core_genericity`'s
#:     `tools/graph/core`);
#:   * walks reached through a SHIPPED module -- a test that CALLS `prepass.collect_rules`,
#:     `build.discover` / `discover_extractors` / `build` / `main` / `build_from_snapshot` /
#:     `check_freshness`, `third_party._shipped`, or `prepass.prepass`. Those build/link entry
#:     points default the real `EXTRACTOR_ROOT` / `LINK_ROOT` registry into `discover`, and
#:     `build` walks `LINK_ROOT` UNCONDITIONALLY, so even a `build(FIXTURE/…)` caller is repo-wide
#:     (a new extractor or link package reddens it). A test passing a non-default extractor_root
#:     is still caught by the unconditional `LINK_ROOT` walk, so none is a false include.
#: A file dropped into any of those roots reddens the guard on a targeted run that never named it.
#: (Some members carry ENVIRONMENT-conditional tests -- `test_third_party.py` POSIX-only ones
#: that skip on Windows, `test_store_portability.py` Neo4j-backed ones that skip with no reachable
#: database -- so a strict `SDLC_FORBID_SKIPS=1` run of the whole sweep belongs on the
#: gate-of-record CI, which runs on Linux and starts Neo4j; there it is exit 0. Those skips are
#: environment-correct, not the accidental kind the flag exists to catch.)
KNOWN_MEMBERS = frozenset({
    "tools/graph/tests/test_build.py",
    "tools/graph/tests/test_canonical_proposal.py",
    "tools/graph/tests/test_context_packs.py",
    "tools/graph/tests/test_core_genericity.py",
    "tools/graph/tests/test_export_okf.py",
    "tools/graph/tests/test_extractor_build_deploy.py",
    "tools/graph/tests/test_extractor_gradle.py",
    "tools/graph/tests/test_extractor_npm.py",
    "tools/graph/tests/test_extractor_openapi.py",
    "tools/graph/tests/test_extractor_properties.py",
    "tools/graph/tests/test_extractor_source.py",
    "tools/graph/tests/test_generated_marking.py",
    "tools/graph/tests/test_golden_chain.py",
    "tools/graph/tests/test_golden_chain_pantry.py",
    "tools/graph/tests/test_golden_chains_further.py",
    "tools/graph/tests/test_host_pairing.py",
    "tools/graph/tests/test_hunk_seeds.py",
    "tools/graph/tests/test_impact_prototype.py",
    "tools/graph/tests/test_incremental_freshness.py",
    "tools/graph/tests/test_links_and_profile.py",
    "tools/graph/tests/test_mcp_server.py",
    "tools/graph/tests/test_producer_callsites.py",
    "tools/graph/tests/test_reference_consumer.py",
    "tools/graph/tests/test_report_hygiene.py",
    "tools/graph/tests/test_semantic_layer.py",
    "tools/graph/tests/test_snapshot_shards.py",
    "tools/graph/tests/test_spec_alignment.py",
    "tools/graph/tests/test_store_portability.py",
    "tools/graph/tests/test_traversal_rules.py",
    "tools/graph/tests/test_unresolved_contract.py",
    "tools/graph/tests/test_unresolved_emission_sites.py",
    "tools/graph/tests/test_unresolved_grouping.py",
    "tools/graph/tests/test_validation_dashboard.py",
    "tools/review/tests/test_hung_scanner.py",
    "tools/review/tests/test_prepass.py",
    "tools/tests/test_approvals.py",
    "tools/tests/test_artifact_crossings.py",
    "tools/tests/test_bake_off_decisions.py",
    "tools/tests/test_ban.py",
    "tools/tests/test_bounded_subprocess.py",
    "tools/tests/test_cli.py",
    "tools/tests/test_connect.py",
    "tools/tests/test_cross_repo_build.py",
    "tools/tests/test_dependencies.py",
    "tools/tests/test_history_is_clean.py",
    "tools/tests/test_metrics.py",
    "tools/tests/test_no_private_coordinates.py",
    "tools/tests/test_policy.py",
    "tools/tests/test_provider_smoke.py",
    "tools/tests/test_repo_contract.py",
    "tools/tests/test_sop_requirements.py",
    "tools/tests/test_stages.py",
    "tools/tests/test_third_party.py",
    "tools/tests/test_workflows.py",
})


# ── AST helpers ──────────────────────────────────────────────────────────────


def _iter_py(root: Path) -> list[Path]:
    return [p for p in sorted(root.rglob("*.py")) if not _SKIP_DIRS & set(p.parts)]


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def _is_test_module(root: Path, p: Path) -> bool:
    parts = p.relative_to(root).parts
    return "tests" in parts or p.name.startswith("test_")


def _dotted_to_file(root: Path, dotted: str) -> Path | None:
    """The first-party file a dotted module name resolves to under `root`, or None."""
    if not dotted:
        return None
    parts = dotted.split(".")
    cand = root.joinpath(*parts).with_suffix(".py")
    if cand.is_file():
        return cand
    pkg = root.joinpath(*parts, "__init__.py")
    return pkg if pkg.is_file() else None


def _data_literals(node: ast.AST) -> bool:
    """True if any string constant in `node` names a data/fixture path segment."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            segs = sub.value.replace("\\", "/").split("/")
            if any(seg.lower() in _DATA_DIRS for seg in segs):
                return True
    return False


def _is_file_derived(node: ast.AST) -> bool:
    """True if the expression is built from `__file__` (a checkout-relative path)."""
    return any(isinstance(n, ast.Name) and n.id == "__file__" for n in ast.walk(node))


class _Ctx:
    """Everything the walk classifier needs about one module and its neighbours."""

    def __init__(self) -> None:
        # module file -> {anchor name -> is_data}
        self.anchors: dict[Path, dict[str, bool]] = {}
        # this module's alias -> resolved first-party module file
        self.alias: dict[Path, dict[str, Path]] = {}
        # this module's raw first-party imports: (module file, symbol or None for the module)
        self.raw: dict[Path, list[tuple[Path, str | None]]] = {}
        # this module's imported TEST-tree module files (for the walker closure)
        self.test_imports: dict[Path, set[Path]] = {}


def _collect_anchors(tree: ast.Module) -> dict[str, bool]:
    """Module-level names bound to a `__file__`-derived path -> whether it is a data subtree.

    Two passes so `TOOLS = ROOT / "tools"` sees `ROOT`. A name is an anchor if its value is
    file-derived, or is an existing anchor extended with `/` or `.joinpath(...)`.
    """
    anchors: dict[str, bool] = {}
    for _ in range(2):
        for node in tree.body:
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            if value is None:
                continue
            base_anchor = _leftmost_anchor_name(value)
            is_anchor = _is_file_derived(value) or (base_anchor in anchors)
            if not is_anchor:
                continue
            is_data = _data_literals(value) or anchors.get(base_anchor or "", False)
            for tgt in targets:
                if isinstance(tgt, ast.Name):
                    anchors[tgt.id] = anchors.get(tgt.id, False) or is_data
    return anchors


def _leftmost_anchor_name(node: ast.expr) -> str | None:
    """The name at the root of a `NAME / x / y` or `NAME.joinpath(...)` chain."""
    cur: ast.AST = node
    while True:
        if isinstance(cur, ast.Name):
            return cur.id
        if isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
            cur = cur.left
        elif isinstance(cur, ast.Call):
            cur = cur.func
        elif isinstance(cur, ast.Attribute):
            cur = cur.value
        elif isinstance(cur, ast.Subscript):
            cur = cur.value
        else:
            return None


def _resolve_imports(
        root: Path, tree: ast.Module) -> tuple[dict[str, Path], list[tuple[Path, str | None]]]:
    """(alias -> module file, [(module file, imported symbol or None for the whole module)]).

    The symbol is kept because importing a walker's *data constant* is not itself a walk;
    only importing its walk FUNCTION, or the module, carries the walk to the importer. The
    closure in `_classify` uses it to reject the constant case.
    """
    alias: dict[str, Path] = {}
    raw: list[tuple[Path, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                f = _dotted_to_file(root, a.name)
                if f is not None:
                    alias[a.asname or a.name.split(".")[0]] = f
                    raw.append((f, None))
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            mf = _dotted_to_file(root, node.module)
            for a in node.names:
                sub = _dotted_to_file(root, f"{node.module}.{a.name}")
                if sub is not None:                       # `from pkg import submodule`
                    alias[a.asname or a.name] = sub
                    raw.append((sub, None))
                elif mf is not None:                      # `from module import symbol`
                    raw.append((mf, a.name))
    return alias, raw


# ── the classifier ───────────────────────────────────────────────────────────


def _repo_wide_anchor(node: ast.expr, own: dict[str, bool], ctx: _Ctx, me: Path) -> bool:
    """True if `node` resolves to a ROOT anchor that is NOT a data/fixture subtree."""
    if isinstance(node, ast.Name):
        return node.id in own and not own[node.id]
    if isinstance(node, ast.Attribute):
        base = node.value
        if isinstance(base, ast.Name) and base.id in ctx.alias.get(me, {}):
            tgt = ctx.alias[me][base.id]
            other = ctx.anchors.get(tgt, {})
            return node.attr in other and not other[node.attr]
        return False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _repo_wide_anchor(node.left, own, ctx, me) and not _data_literals(node.right)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr == "joinpath":
            return (_repo_wide_anchor(node.func.value, own, ctx, me)
                    and not any(_data_literals(a) for a in node.args))
    if _is_file_derived(node):
        return not _data_literals(node)
    return False


def _string_args(call: ast.Call) -> set[str]:
    """String constants passed to a call, flattening a first list/tuple argument."""
    out: set[str] = set()
    for arg in call.args:
        if isinstance(arg, (ast.List, ast.Tuple)):
            out |= {e.value for e in arg.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            out.add(arg.value)
    return out


def _call_root_anchored(call: ast.Call, own: dict[str, bool], ctx: _Ctx, me: Path) -> bool:
    """A git subprocess call anchored at ROOT: `cwd=<anchor>` or `str(<anchor>)` in the argv."""
    for kw in call.keywords:
        if kw.arg == "cwd" and _repo_wide_anchor(kw.value, own, ctx, me):
            return True
    for sub in ast.walk(call):
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                and sub.func.id == "str" and sub.args
                and _repo_wide_anchor(sub.args[0], own, ctx, me)):
            return True
    return False


def _module_directly_walks(tree: ast.Module, own: dict[str, bool], ctx: _Ctx, me: Path,
                           history_needs_anchor: bool = False) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # filesystem enumeration
        if isinstance(func, ast.Attribute) and func.attr in _FS_WALK_ATTRS:
            if func.attr in {"walk", "scandir"} and isinstance(func.value, ast.Name) \
                    and func.value.id == "os":
                if node.args and _repo_wide_anchor(node.args[0], own, ctx, me):
                    return True
            elif _repo_wide_anchor(func.value, own, ctx, me):
                return True
        # git enumeration
        strs = _string_args(node)
        if strs & _HISTORY_SUBCOMMANDS and (
                not history_needs_anchor or _call_root_anchored(node, own, ctx, me)):
            return True
        if (strs & _TREE_SUBCOMMANDS) and _call_root_anchored(node, own, ctx, me):
            return True
    return False


def _walking_functions(tree: ast.Module, own: dict[str, bool], ctx: _Ctx, me: Path) -> set[str]:
    """Top-level functions of `tree` that reach a walk -- directly, or by calling one that does.

    This is what makes the import closure precise. `test_dependencies._sources` walks only
    because it calls `_tracked_python`, which runs `git ls-files`; a text helper like
    `test_sop_requirements.unrunnable_commands` walks nothing, so importing it does not make
    the importer a repo-wide guard.
    """
    funcs = {n.name: n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    walks = {name for name, fn in funcs.items() if _module_directly_walks(fn, own, ctx, me)}
    calls = {name: {c.func.id for c in ast.walk(fn)
                    if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                    and c.func.id in funcs}
             for name, fn in funcs.items()}
    changed = True
    while changed:
        changed = False
        for name in funcs:
            if name not in walks and (calls[name] & walks):
                walks.add(name)
                changed = True
    return walks


def _local_anchors(fn: ast.AST, own: dict[str, bool], ctx: _Ctx, me: Path) -> dict[str, bool]:
    """Names bound WITHIN `fn` to a module-constant ROOT anchor, so a walk on them is a walk on
    the constant. Three shapes reach a shipped module's own default root:
      * `root = ANCHOR if root is None else root`  -- a parameter that DEFAULTS to the anchor;
      * `for d in [ANCHOR, ...]:`                   -- a loop over a list that includes it;
      * `p = ANCHOR / "x"`                          -- a plain rebinding.
    A PURE parameter (`def f(root): root.rglob(...)`, no anchor default) is NOT here, which is
    why a shipped function that merely parameterises its walk is not treated as repo-wide.
    """
    la: dict[str, bool] = {}
    for _ in range(2):                                    # two passes so a chain resolves
        merged = {**own, **la}
        for n in ast.walk(fn):
            if (isinstance(n, ast.Assign) and len(n.targets) == 1
                    and isinstance(n.targets[0], ast.Name)):
                v = n.value
                if isinstance(v, ast.IfExp):
                    if (_repo_wide_anchor(v.body, merged, ctx, me)
                            or _repo_wide_anchor(v.orelse, merged, ctx, me)):
                        la[n.targets[0].id] = False
                elif _repo_wide_anchor(v, merged, ctx, me):
                    la[n.targets[0].id] = False
            elif (isinstance(n, ast.For) and isinstance(n.target, ast.Name)
                    and isinstance(n.iter, (ast.List, ast.Tuple))):
                if any(_repo_wide_anchor(e, merged, ctx, me) for e in n.iter.elts):
                    la[n.target.id] = False
    return la


def _shipped_root_walkers(root: Path, trees: dict[Path, ast.Module],
                          ctx: _Ctx) -> set[tuple[Path, str]]:
    """(module file, function name) for each SHIPPED (non-test) function that reaches a walk of a
    module-constant ROOT anchor of its OWN module -- directly, or through a SAME-MODULE call
    chain. The walk primitive is in production code, so a per-file grep AND a test-tree-only
    import closure both miss the test that calls it.

    The closure mirrors `_walking_functions` but is confined to ONE module: a shipped function is
    a walker when it, or another top-level function of the SAME module it calls, walks a constant
    root. This catches `discover_extractors` -> `discover`, `build` -> `discover`, `main` ->
    `build` (all in build.py), each of which defaults the extractor/link root to the real
    registry. Staying same-module is what keeps it bounded: following calls ACROSS modules is the
    20 -> 66 cross-module balloon the reviewer measured, and it is deliberately NOT done here.

    A history read (`rev-list`/`cat-file`/`for-each-ref`) counts here only when anchored at a
    ROOT constant (`history_needs_anchor`), unlike the test-tree rule that treats it
    unconditionally: `metrics.commits(target)` runs `git -C str(target) rev-list` over a
    PARAMETER repo -- the caller's tree, not this one -- so it must not make its callers repo-wide.
    """
    out: set[tuple[Path, str]] = set()
    for mf, tree in trees.items():
        if _is_test_module(root, mf):
            continue
        own = ctx.anchors[mf]
        funcs = {n.name: n for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        walkers = {name for name, fn in funcs.items()
                   if _module_directly_walks(fn, {**own, **_local_anchors(fn, own, ctx, mf)},
                                             ctx, mf, history_needs_anchor=True)}
        calls = {name: {c.func.id for c in ast.walk(fn)
                        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                        and c.func.id in funcs}
                 for name, fn in funcs.items()}
        changed = True
        while changed:
            changed = False
            for name in funcs:
                if name not in walkers and (calls[name] & walkers):
                    walkers.add(name)
                    changed = True
        out |= {(mf, name) for name in walkers}
    return out


def _calls_shipped_walker(tree: ast.Module, alias: dict[str, Path],
                          shipped: set[tuple[Path, str]]) -> bool:
    """True if the module CALLS `MOD.func(...)` where `MOD` is an imported shipped module and
    `func` is one of its root-walkers. The attribute-CALL site is the whole precision: a bare
    `import build` is not enough, so a shared production import does not drag its every importer
    into the sweep (the naive "follow any first-party import" balloons 20 -> 66)."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in alias
                and (alias[node.func.value.id], node.func.attr) in shipped):
            return True
    return False


def repo_wide_guard_files(root: Path) -> set[str]:
    """The repo-wide-guard sweep set under `root`, derived from what each test WALKS.

    Pure static analysis: no subprocess, no git, no import of the tree -- so it is
    deterministic and runs over a synthetic tree as readily as over the checkout.
    """
    return _classify(_iter_py(root), root)


def _classify(files: list[Path], root: Path) -> set[str]:
    trees: dict[Path, ast.Module] = {}
    for p in files:
        try:
            trees[p] = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue

    ctx = _Ctx()
    for p, tree in trees.items():
        ctx.anchors[p] = _collect_anchors(tree)
    for p, tree in trees.items():
        ctx.alias[p], ctx.raw[p] = _resolve_imports(root, tree)
    walking_funcs: dict[Path, set[str]] = {
        p: _walking_functions(tree, ctx.anchors[p], ctx, p) for p, tree in trees.items()
    }

    # Walker-closure edges: follow an import into a TEST-tree module only when it carries the
    # walk -- the module itself, a submodule, or one of its WALKING functions -- never a data
    # constant (`test_schema` <- `test_policy.ACCEPTED`) and never a text helper that walks
    # nothing (`test_provider_files` <- `test_sop_requirements.unrunnable_commands`).
    for p in trees:
        edges: set[Path] = set()
        for mf, sym in ctx.raw[p]:
            if mf not in trees or not _is_test_module(root, mf):
                continue
            if sym is None or sym in walking_funcs.get(mf, set()):
                edges.add(mf)
        ctx.test_imports[p] = edges

    # A walk can also arrive through a SHIPPED module at an attribute-CALL site: the test calls
    # `MOD.func(...)` where `func`'s own body walks a module-constant ROOT anchor. The calling
    # test is then repo-wide though the walk primitive is production code (`_shipped_root_walkers`).
    shipped = _shipped_root_walkers(root, trees, ctx)
    direct: dict[Path, bool] = {
        p: (_module_directly_walks(tree, ctx.anchors[p], ctx, p)
            or _calls_shipped_walker(tree, ctx.alias[p], shipped))
        for p, tree in trees.items()
    }

    def reaches_walker(start: Path) -> bool:
        seen: set[Path] = set()
        stack = [start]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            if direct.get(cur):
                return True
            stack.extend(ctx.test_imports.get(cur, ()))
        return False

    return {
        _rel(root, p) for p in trees
        if p.name.startswith("test_") and reaches_walker(p)
    }


# ── the derivation, over this checkout ───────────────────────────────────────


@pytest.fixture(scope="module")
def derived() -> set[str]:
    # A direct ROOT walk lives here, not behind a parameter, so this file is a repo-wide
    # guard its OWN derivation can see (`repo_wide_guard_files` stays parameterised for the
    # synthetic control below, and a root threaded through a parameter is invisible to the
    # classifier -- the residual limit the module docstring states).
    modules = [p for p in ROOT.rglob("*.py") if not _SKIP_DIRS & set(p.parts)]
    return _classify(modules, ROOT)


def test_the_derivation_finds_the_import_reached_member(derived: set[str]):
    """The first miss: `test_artifact_crossings.py` walks the tree through `_sources`
    IMPORTED from `test_dependencies.py`, so its walk primitive is in another file and the
    per-file grep cannot see it. The import graph can."""
    assert "tools/tests/test_artifact_crossings.py" in derived


def test_the_derivation_finds_the_history_walking_member(derived: set[str]):
    """The second miss: `test_history_is_clean.py` walks git HISTORY, touching no file, so it
    matches none of the filesystem primitives the grep enumerates."""
    assert "tools/tests/test_history_is_clean.py" in derived


def test_the_derivation_covers_every_known_member(derived: set[str]):
    """Every file the integrator's sweep names is derived. A missing one is a hole in the
    derivation, and the message names it rather than a bare count."""
    missing = sorted(KNOWN_MEMBERS - derived)
    assert not missing, f"the derivation missed sweep members: {missing}"


def test_the_derivation_includes_itself(derived: set[str]):
    """This file walks the tree to derive the set, so it is a repo-wide guard and derives
    itself into the sweep. Pinned so an integrator re-deriving the set expects this file too."""
    assert "tools/tests/test_repo_wide_guards.py" in derived


def test_a_fixture_only_walker_is_not_derived(derived: set[str]):
    """The grep's OTHER failure was over-inclusion: it flagged tests that only walk a
    `tmp_path`/fixture. `test_impact_ab.py` runs `git -C str(<fixture>)` and `ws_root.rglob`
    over a workspace it builds, and reads no ROOT-anchored tree or history -- so it is not a
    repo-wide guard, and asking what a walk IS excludes it where the grep could not."""
    assert "tools/tests/test_impact_ab.py" not in derived


# ── the positive control: watched failing on a member removed from the tree ──


def _write(base: Path, rel: str, text: str) -> Path:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _synthetic_tree(base: Path) -> None:
    """A miniature checkout exercising each classification path exactly once."""
    _write(base, "proj/tests/helper.py",
           "from pathlib import Path\n"
           "ROOT = Path(__file__).resolve().parents[2]\n"
           "def walk():\n    return list(ROOT.rglob('*.py'))\n")
    _write(base, "proj/tests/test_direct.py",
           "from pathlib import Path\n"
           "ROOT = Path(__file__).resolve().parents[2]\n"
           "def test_it():\n    assert list(ROOT.rglob('*.py'))\n")
    _write(base, "proj/tests/test_imported.py",
           "from proj.tests.helper import walk\n"
           "def test_it():\n    assert walk()\n")
    _write(base, "proj/tests/test_history.py",
           "import subprocess\n"
           "def test_it():\n"
           "    subprocess.run(['git', 'rev-list', '--all'], capture_output=True)\n")
    _write(base, "proj/tests/test_fixture.py",
           "def test_it(tmp_path):\n    assert list(tmp_path.rglob('*')) == []\n")
    # A file-derived, ROOT-anchored walk whose base names a `fixtures/` segment: it walks test
    # DATA, not the source tree, so it must NOT be counted -- mirrors the real
    # `test_fixture_integrity.py` (`FIXTURE = Path(__file__)...parent / "fixtures" / ...`).
    _write(base, "proj/tests/test_data_subtree.py",
           "from pathlib import Path\n"
           "FIX = Path(__file__).resolve().parents[2] / 'fixtures'\n"
           "def test_it():\n    assert list(FIX.rglob('*.py'))\n")
    # A SHIPPED (non-test) module: one function walks its own module-constant root by default,
    # the other walks a PURE parameter. A test that CALLS the first is repo-wide though the walk
    # primitive is production code; a test that calls only the second is not.
    _write(base, "proj/prod.py",
           "from pathlib import Path\n"
           "SRC = Path(__file__).resolve().parents[1] / 'src'\n"
           "def scan_default(root=None):\n"           # default-param -> module constant
           "    root = SRC if root is None else root\n"
           "    return list(root.iterdir())\n"
           "def scan_loop():\n"                        # loop over a list holding the constant
           "    for d in [SRC]:\n        return list(d.glob('*.py'))\n    return []\n"
           "def scan_delegate():\n"                    # SAME-MODULE delegation to a walker
           "    return scan_loop()\n"
           "def scan_param(root):\n"                   # pure parameter -> NOT a constant walk
           "    return list(root.rglob('*.py'))\n"
           "def scan_history_param(repo):\n"           # git history on a PARAM repo -> caller's tree
           "    import subprocess\n"
           "    return subprocess.run(['git', '-C', str(repo), 'rev-list', '--all'])\n")
    _write(base, "proj/tests/test_shipped_caller.py",
           "from proj import prod as P\n"
           "def test_it():\n    assert P.scan_default(None) is not None\n")
    _write(base, "proj/tests/test_shipped_loop.py",
           "from proj import prod as P\n"
           "def test_it():\n    assert P.scan_loop() is not None\n")
    _write(base, "proj/tests/test_shipped_delegate.py",
           "from proj import prod as P\n"
           "def test_it():\n    assert P.scan_delegate() is not None\n")
    _write(base, "proj/tests/test_shipped_param.py",
           "from proj import prod as P\n"
           "def test_it(tmp_path):\n    assert P.scan_param(tmp_path) == []\n")
    _write(base, "proj/tests/test_shipped_history_param.py",
           "from proj import prod as P\n"
           "def test_it(tmp_path):\n    assert P.scan_history_param(tmp_path) is not None\n")
    _write(base, "proj/tests/test_plain.py",
           "def test_it():\n    assert 1 == 1\n")


def test_the_derivation_reads_the_tree_not_a_constant(tmp_path: Path):
    """The control the recipe never had: build a tree, classify it, then REMOVE a member and
    watch the derivation drop it. A derivation that returned a constant would pass the first
    half and fail the second; this one reads the tree each time.

    The classification paths are covered together, because each is a way the grep was wrong: a
    direct ROOT walk, a walk reached only by IMPORT, a HISTORY walk, and a walk reached through a
    SHIPPED module at an attribute-CALL site -- the shipped case in three shapes (a default-param
    constant, a loop-over-list constant, and a SAME-MODULE delegation to another walker). Four
    non-members -- a `tmp_path` walk, a ROOT-anchored walk into a `fixtures/` subtree, a shipped
    call that walks only a PARAMETER, and a test that walks nothing -- prove the classifier is not
    simply naming every test file.
    """
    _synthetic_tree(tmp_path)
    derived = repo_wide_guard_files(tmp_path)
    assert "proj/tests/test_direct.py" in derived
    assert "proj/tests/test_imported.py" in derived, "import-reached walk was not followed"
    assert "proj/tests/test_history.py" in derived
    assert "proj/tests/test_shipped_caller.py" in derived, (
        "a call into a shipped module's root-walker was not followed")
    assert "proj/tests/test_shipped_loop.py" in derived, (
        "a shipped loop-over-list constant walk was not followed")
    assert "proj/tests/test_shipped_delegate.py" in derived, (
        "a SAME-MODULE delegation to a shipped walker was not followed")
    assert "proj/tests/test_fixture.py" not in derived, "a tmp_path walk is not repo-wide"
    assert "proj/tests/test_data_subtree.py" not in derived, (
        "a ROOT-anchored walk into a fixtures/ subtree walks test data, not the source tree")
    assert "proj/tests/test_shipped_param.py" not in derived, (
        "a shipped call that walks only a parameter is not repo-wide")
    assert "proj/tests/test_shipped_history_param.py" not in derived, (
        "a shipped git-history read on a PARAM repo is the caller's tree, not repo-wide")
    assert "proj/tests/test_plain.py" not in derived

    # Remove the import-reached member's walk source from the tree: the derivation must now
    # drop the importer, because it is following the import into the tree, not a hand-list.
    (tmp_path / "proj/tests/helper.py").unlink()
    after = repo_wide_guard_files(tmp_path)
    assert "proj/tests/test_imported.py" not in after, (
        "the derivation still named a member whose only walk primitive was just deleted -- "
        "it is not reading the tree"
    )
    assert "proj/tests/test_direct.py" in after, "removing one member must not drop the others"


def test_the_grep_recipe_under_derives_where_this_derivation_does_not(tmp_path: Path):
    """The still-real defect, reproduced as an assertion. The documented recipe -- grep each
    test file's own text for `rglob`/`ls-files`/`os.walk` -- cannot name a member whose walk
    arrives by import or by history, while the derivation names both."""
    import re

    recipe = re.compile(r"rglob|ls-files|os\.walk")

    def recipe_hits(path: Path) -> bool:
        return bool(recipe.search(path.read_text(encoding="utf-8", errors="replace")))

    for rel in ("tools/tests/test_artifact_crossings.py",
                "tools/tests/test_history_is_clean.py"):
        assert not recipe_hits(ROOT / rel), (
            f"{rel} now matches the grep recipe; this control no longer proves the miss"
        )

    derived = repo_wide_guard_files(ROOT)
    assert "tools/tests/test_artifact_crossings.py" in derived
    assert "tools/tests/test_history_is_clean.py" in derived
