"""Build the domain graph for a workspace.

    python tools/graph/build.py --workspace sdlc-workspace.toml

Six stages, in order: **discover → activate → extract → link → emit → report**.

Discovery is not reimplemented here. It reuses the installer's `read_workspace()` and
`discover_components()`, so "which directories are components" has exactly one
implementation and the graph is built over precisely the set the generated profile names.

## Output is JSONL, and loading a database is a separate step

This produces files, not database rows. The golden chain that gates M0 must be runnable by
anyone who checks the repo out, and a build that required a live database would make that
gate skip wherever one is absent — and a skipping test still prints green, which is worse
than no test. So the graph lands as deterministic JSONL and any store is downstream of it.

## The honest empty

An empty graph and a clean graph look identical from the outside, and confusing the two is
the single highest-value failure this project keeps rediscovering: an external tool that
returns nothing at exit 0, a scan that silently skipped every component, a binary that was
never pinned for this platform. So this pipeline distinguishes four states and never lets
the quiet ones exit 0:

  * **no extractors registered**  — refusal. The pipeline cannot produce a graph at all.
  * **none activated**            — refusal, listing every glob that matched nothing. This is
    a misconfiguration, not a workspace without couplings.
  * **components claimed by nobody** — reported by name, exit 0. A docs-only component
    legitimately has no extractor; a coverage hole is a finding, not a failure.
  * **activated, but zero edges**  — exit 0 with a prominent warning. It is a legitimate
    answer, and it is one nobody should be able to read past by accident.

## Unresolved is an output, not a log line

What an extractor could not resolve is written beside what it could. An extractor that
drops the reference it failed to understand makes the graph look complete when it is not,
and nothing downstream can tell the difference. Coverage is measured against this file.

## Not here yet, deliberately

`profiles/<name>/` is not scanned. A profile describes formats one organisation invented,
so it must be *selected* by the workspace rather than activated globally — every adopter
running every committed profile is wrong by construction. That selection mechanism lands
with the first profile, not before it.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.graph.core.lib import emit, manifest as manifest_lib, repo_walk  # noqa: E402
from tools.graph.core.model import ExtractResult, Node  # noqa: E402
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.outdir import SCRATCH_DIR, check_output_dir as _check_output_dir  # noqa: E402

SCHEMA_VERSION = 1

#: Where the reusable, technology-scoped extractors live. One subpackage per technology.
EXTRACTOR_ROOT = Path(__file__).resolve().parent / "extractors"


class BuildError(Exception):
    """A build that cannot be trusted to be complete. Always fatal, never a warning."""


# ── registration ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Registration:
    """One extractor, with the layer it came from and the globs that activate it."""

    name: str
    layer: str
    globs: tuple[str, ...]
    extractor: Any
    module: str = ""
    requires: tuple[str, ...] = ()


def _str_tuple(obj: Any, attr: str, name: str, where: str, *, required: bool) -> tuple[str, ...]:
    """Read a tuple-of-strings class attribute, refusing the bare-string trap.

    A string is iterable, so `INPUT_GLOBS = "pom.xml"` would silently become one pattern per
    character and `REQUIRES = "properties"` one requirement per letter. Both fail in the
    quiet direction, which is why this is an error with the fix spelled out rather than a
    coercion.
    """
    value = getattr(obj, attr, None)
    if value is None and not required:
        return ()
    if isinstance(value, str):
        raise BuildError(f"{where}: `{attr}` on {name!r} must be a TUPLE, not a bare string: "
                         f"write `{attr} = ({value!r},)`")
    if not isinstance(value, (tuple, list)) or (required and not value):
        raise BuildError(f"{where}: `{attr}` on {name!r} is missing or empty. An extractor "
                         f"with no globs can never activate, so it would register and then "
                         f"silently never run")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise BuildError(f"{where}: `{attr}` on {name!r} contains {item!r}, which is not "
                             f"a pattern")
    return tuple(value)


def order_extractors(regs: list[Registration]) -> list[Registration]:
    """Sort so every extractor runs after the ones it declares in `REQUIRES`.

    The shared cache makes order load-bearing, and directory-alphabetical order is arbitrary:
    an extractor that reads a resolved configuration view before the extractor that publishes
    it sees nothing and reports every reference as unresolved. That is loud, which is the only
    reason it was cheap to find — but it is still a wrong graph produced by a correct build.

    Kahn's algorithm with an alphabetical tie-break, so the order is deterministic rather
    than dependent on how the directory happened to enumerate.
    """
    by_name = {r.name: r for r in regs}
    pending = {r.name: {d for d in r.requires if d in by_name} for r in regs}
    out: list[Registration] = []
    while pending:
        ready = sorted(n for n, deps in pending.items() if not deps)
        if not ready:
            cycle = ", ".join(sorted(pending))
            raise BuildError(f"extractors depend on each other in a cycle and none can run "
                             f"first: {cycle}. `REQUIRES` describes what must have populated "
                             f"the shared cache already, so it cannot be mutual")
        for name in ready:
            out.append(by_name[name])
            del pending[name]
        for deps in pending.values():
            deps.difference_update(ready)
    return out


def _validate(obj: Any, where: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Check one extractor's registration contract, or raise.

    Every failure here is fatal. A malformed extractor that merely warned would be skipped,
    and a skipped extractor is indistinguishable from a workspace that genuinely has none of
    the couplings it reads — the graph comes out plausible and short.
    """
    name = getattr(obj, "name", None)
    if not isinstance(name, str) or not name.strip():
        raise BuildError(f"{where}: extractor has no usable `name` (got {name!r}). The name is "
                         f"recorded on every edge it emits, so provenance depends on it")

    globs = _str_tuple(obj, "INPUT_GLOBS", name, where, required=True)
    requires = _str_tuple(obj, "REQUIRES", name, where, required=False)

    if not callable(getattr(obj, "extract", None)):
        raise BuildError(f"{where}: {name!r} has no callable `extract(ctx)`")

    return name, globs, requires


def _load_module(path: Path, modname: str):
    # A package's `__init__.py` needs search locations set, or relative imports inside it
    # fail with a bare "attempted relative import with no known parent package".
    search = [str(path.parent)] if path.name == "__init__.py" else None
    spec = importlib.util.spec_from_file_location(modname, path,
                                                  submodule_search_locations=search)
    if spec is None or spec.loader is None:
        raise BuildError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def _dotted_name(pkg: Path) -> str | None:
    """The importable dotted name for `pkg`, when it genuinely sits in this repo's tree.

    Derived from the path rather than compared against `EXTRACTOR_ROOT`: that module global
    is the *default* root and tests point it elsewhere, so equality with it says nothing about
    whether a dotted import would resolve. Getting that backwards makes every test extractor
    fail to import — loudly, as it happens, which is the only reason it was cheap to find.
    """
    real = Path(__file__).resolve().parent / "extractors"
    return f"tools.graph.extractors.{pkg.name}" if pkg.parent == real else None


def discover_extractors(root: Path | None = None, layer: str = "stack") -> list[Registration]:
    """Import every subpackage of `root` and collect its `EXTRACTORS`.

    Scanning rather than maintaining a list means adding an extractor is adding a directory,
    with no second place to forget. The cost of scanning is that a broken module could be
    skipped silently, so it is not: an import error, a missing `EXTRACTORS`, an empty one, or
    a duplicate name all raise. The directory layout is the layer boundary, which is why the
    layer is passed in rather than guessed from the module name.
    """
    root = EXTRACTOR_ROOT if root is None else root
    if not root.is_dir():
        return []

    regs: list[Registration] = []
    seen: dict[str, str] = {}
    for pkg in sorted(p for p in root.iterdir() if p.is_dir()):
        if pkg.name.startswith((".", "_")):
            continue
        init = pkg / "__init__.py"
        if not init.is_file():
            raise BuildError(f"{pkg} has no __init__.py, so it registers nothing. Delete it or "
                             f"give it an `EXTRACTORS` tuple — a directory that looks like an "
                             f"extractor and is not one is how an edge class goes missing")
        where = f"{root.name}/{pkg.name}"
        try:
            # The repo's own extractors are imported under their real dotted name so there is
            # exactly one module object per extractor. Spec-loading them under a synthetic
            # name would give a second copy to anything that later imports them normally —
            # two classes, two module-level constants, and a shared-cache key that compares
            # equal while the code disagrees about what wrote it.
            if dotted := _dotted_name(pkg):
                mod = importlib.import_module(dotted)
            else:
                mod = _load_module(init, f"_sdlc_{layer}_{pkg.name}")
        except BuildError:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised with the location that failed
            raise BuildError(f"{where}: failed to import ({type(exc).__name__}: {exc})") from exc

        entries = getattr(mod, "EXTRACTORS", None)
        if entries is None:
            raise BuildError(f"{where}: no `EXTRACTORS` tuple. Every extractor package must "
                             f"export one, or it is imported and then ignored")
        if not isinstance(entries, (tuple, list)) or not entries:
            raise BuildError(f"{where}: `EXTRACTORS` is empty. An empty package is a mistake "
                             f"that reads as a technology this harness cannot see")

        for obj in entries:
            name, globs, requires = _validate(obj, where)
            if name in seen:
                raise BuildError(f"{where}: extractor name {name!r} is already registered by "
                                 f"{seen[name]}. Names are recorded on edges and drive the "
                                 f"output shards, so two would overwrite each other")
            seen[name] = where
            regs.append(Registration(name=name, layer=layer, globs=globs, requires=requires,
                                     extractor=obj, module=where))
    return order_extractors(regs)


def extractor_version(regs: list[Registration]) -> str:
    """A digest of the registered set — name, layer and globs.

    Stamped into the manifest so an incremental rebuild re-runs when the extractor set
    changes, not only when files do. Without it, adding an extractor would leave every
    unchanged file untouched and its edges would never appear.
    """
    payload = "\n".join(sorted(f"{r.layer}:{r.name}:{','.join(r.globs)}" for r in regs))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


# ── activation ────────────────────────────────────────────────────────────────


@dataclass
class Activation:
    """Which components each extractor claims, and which files it claimed them by."""

    by_extractor: dict[str, list[str]] = field(default_factory=dict)
    by_component: dict[str, list[str]] = field(default_factory=dict)
    files: dict[tuple[str, str], list[Path]] = field(default_factory=dict)

    def active(self) -> list[str]:
        return [n for n, comps in self.by_extractor.items() if comps]


def activate(regs: list[Registration], components: list[dict], root: Path) -> Activation:
    """Decide which extractors run against which components, by matching real files.

    **Not by stack label, deliberately.** `detect_stack()` returns one label per component
    chosen by first match, but a component routinely needs several extractors at once, and a
    tree holding only deployment charts detects as `unknown` while still carrying facts a
    chart extractor must read. Gating on the label would drop those without a word.

    Each component is walked ONCE with the union of every registered glob; the per-extractor
    split is then done in memory. That is equivalent to calling `find_files` per extractor —
    see `repo_walk.glob_match` — and turns a walk per (extractor, component) pair into a walk
    per component.
    """
    act = Activation(by_extractor={r.name: [] for r in regs},
                     by_component={c["name"]: [] for c in components})
    if not regs:
        return act

    union = sorted({g for r in regs for g in r.globs})
    for comp in components:
        path = root / comp["path"]
        found = repo_walk.find_files(path, union)
        rels = [(p, p.relative_to(path).as_posix(), p.name) for p in found]
        for reg in regs:
            hits = [p for p, rel, base in rels
                    if any(repo_walk.glob_match(rel, base, g) for g in reg.globs)]
            if not hits:
                continue
            act.by_extractor[reg.name].append(comp["name"])
            act.by_component[comp["name"]].append(reg.name)
            act.files[(reg.name, comp["name"])] = hits
    return act


# ── the pipeline ──────────────────────────────────────────────────────────────


def repo_refs(components: list[dict], root: Path, names: list[str] | None = None) -> list[RepoRef]:
    """Turn discovered components into the read-only view an extractor is given."""
    wanted = set(names) if names is not None else None
    return [
        RepoRef(alias=c["name"], path=root / c["path"],
                tags=[c.get("role", ""), c.get("stack", "")], props=dict(c))
        for c in components
        if wanted is None or c["name"] in wanted
    ]


def component_nodes(components: list[dict]) -> list[Node]:
    """One node per component, emitted by the pipeline rather than by any extractor.

    A component is a component whether or not anything reads it, so this is the one fact the
    graph holds that does not depend on an extractor claiming the tree. It is also what lets
    an inert component be visible: deleting one must change the node count, and nothing else.
    """
    return [
        Node(id=f"repo:{c['name']}", type="repo",
             props={"path": c["path"], "role": c.get("role", ""),
                    "stack": c.get("stack", ""), "shallow": bool(c.get("shallow"))})
        for c in components
    ]


def run_extractors(regs: list[Registration], act: Activation, components: list[dict],
                   root: Path, run_id: str, config: dict) -> tuple[dict[str, ExtractResult], dict]:
    """Run each activated extractor over only the components it claimed.

    An extractor is handed the components its globs matched, not the whole workspace, so it
    cannot read a tree that never activated it — and the report can say exactly which
    components each extractor covered.

    `cache` is shared across extractors on purpose: resolving a component's configuration is
    expensive and several extractors need the same answer, and two extractors resolving it
    twice is two chances to resolve it differently.
    """
    results: dict[str, ExtractResult] = {}
    cache: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for reg in regs:
        claimed = act.by_extractor.get(reg.name) or []
        if not claimed:
            continue
        ctx = ExtractContext(repos=repo_refs(components, root, claimed),
                             run_id=run_id, config=config, cache=cache,
                             files={c: act.files.get((reg.name, c), []) for c in claimed})
        try:
            res = reg.extractor.extract(ctx)
        except Exception as exc:  # noqa: BLE001 - one extractor must not kill the build
            # A failing extractor is recorded and the build continues, for the same reason a
            # malformed file lands in `unresolved` rather than aborting: one broken reader
            # must not cost a workspace its whole graph. It is NOT silent — it is a warning,
            # it is in the report, and the edges it would have made are visibly absent.
            failures[reg.name] = f"{type(exc).__name__}: {exc}"
            continue
        if not isinstance(res, ExtractResult):
            failures[reg.name] = (f"returned {type(res).__name__}, not an ExtractResult — its "
                                  f"nodes, edges and unresolved rows are all unreadable")
            continue
        # Stamp the run centrally rather than trusting each extractor to carry `ctx.run_id`
        # through: an extractor that forgets produces edges the store cannot attribute to a
        # build, and that is invisible until someone tries to expire an old run's rows.
        res.edges = [e if e.run_id == run_id else replace(e, run_id=run_id) for e in res.edges]
        results[reg.name] = res
    return results, failures


def build(workspace: Path, out: Path, *, run_id: str = "", only: list[str] | None = None,
          components_filter: list[str] | None = None,
          extractor_root: Path | None = None) -> dict:
    """Run the whole pipeline and return the build report."""
    si = load_installer()
    try:
        ws = si.read_workspace(workspace)
        discovered = si.discover_components(ws)
    except si.WorkspaceError as exc:
        raise BuildError(str(exc)) from exc

    root: Path = ws["root"]
    components = discovered["components"]
    if components_filter:
        wanted = set(components_filter)
        unknown = sorted(wanted - {c["name"] for c in components})
        if unknown:
            raise BuildError(f"--components names {', '.join(unknown)}, which the workspace does "
                             f"not contain. Known: {', '.join(c['name'] for c in components)}")
        components = [c for c in components if c["name"] in wanted]

    regs = discover_extractors(extractor_root)
    if only:
        wanted = set(only)
        unknown = sorted(wanted - {r.name for r in regs})
        if unknown:
            raise BuildError(f"--only names {', '.join(unknown)}, which is not registered. "
                             f"Registered: {', '.join(r.name for r in regs) or '(none)'}")
        regs = [r for r in regs if r.name in wanted]      # already topologically ordered

    run_id = run_id or uuid.uuid4().hex[:12]
    act = activate(regs, components, root)
    config = {"workspace_name": ws.get("name", ""), "canonical": ws.get("canonical", []),
              "components": components}
    results, failures = run_extractors(regs, act, components, root, run_id, config)

    # ── emit ──────────────────────────────────────────────────────────────────
    merged = ExtractResult(nodes=component_nodes(components))
    extract_dir = out / "extract"
    for reg in regs:
        res = results.get(reg.name)
        if res is None:
            continue
        emit.write_jsonl(extract_dir / f"{reg.name}.jsonl", res, extractor=reg.name)
        emit.write_unresolved(extract_dir / f"{reg.name}.unresolved.jsonl", res.unresolved)
        merged.extend(res)
    emit.write_jsonl(out / "graph.jsonl", merged, extractor="merged")
    emit.write_unresolved(out / "unresolved.jsonl", merged.unresolved)

    mf = manifest_lib.Manifest(run_id=run_id, extractor_version=extractor_version(regs))
    for comp in components:
        path = root / comp["path"]
        mf.repos[comp["name"]] = manifest_lib.RepoStamp(
            alias=comp["name"], head=manifest_lib.git_head(path),
            dirty=manifest_lib.git_dirty(path))
    for (ext_name, comp_name), paths in act.files.items():
        mf.record_files(f"{comp_name}#{ext_name}", paths, root / _path_of(components, comp_name))
    for name, res in results.items():
        mf.counts[name] = len(res.nodes) + len(res.edges)
    mf.save(out / "manifest.json")

    report = _report(ws, components, regs, act, results, failures, merged, run_id)
    (out / "build_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def _path_of(components: list[dict], name: str) -> str:
    return next(c["path"] for c in components if c["name"] == name)


def _report(ws: dict, components: list[dict], regs: list[Registration], act: Activation,
            results: dict[str, ExtractResult], failures: dict[str, str],
            merged: ExtractResult, run_id: str) -> dict:
    """Assemble the report, and decide which of the four empties this build is."""
    warnings: list[str] = []
    unclaimed = sorted(n for n, exts in act.by_component.items() if not exts)

    if not regs:
        status = "no-extractors"
        warnings.append(
            "no extractors are registered, so this build cannot produce a graph. Every "
            "component was discovered and none was read.")
    elif not act.active():
        status = "no-activation"
        warnings.append(
            f"{len(regs)} extractor(s) are registered and none matched a single file in any "
            f"of {len(components)} component(s). That is a misconfiguration, not a workspace "
            f"without couplings — check the globs listed below against the trees on disk.")
    elif not results:
        # One extractor failing is survivable and stays a warning. EVERY activated extractor
        # failing is not the same fact, and without this branch it would land in `no-edges`
        # and exit 0 — a wholly broken pipeline reporting a workspace with no couplings.
        status = "all-failed"
        warnings.append(
            f"every one of the {len(act.active())} activated extractor(s) failed. Nothing was "
            f"read successfully, so this graph is not a statement about the workspace.")
    elif not merged.edges:
        status = "no-edges"
        warnings.append(
            "extraction ran and produced ZERO edges. The components exist as nodes and "
            "nothing connects them, which is a legitimate answer and an unlikely one.")
    else:
        status = "ok"

    if unclaimed and status in ("ok", "no-edges"):
        warnings.append(
            # "none of the extractors that ran", not "no extractor at all": `--only` narrows
            # the set, and a message that overstated the gap would send someone hunting for a
            # coverage hole they created with a flag one line earlier.
            f"{len(unclaimed)} component(s) matched none of the extractors that ran and "
            f"contribute nothing beyond their own node: {', '.join(unclaimed)}. That is a "
            f"coverage hole rather than a failure — a docs-only component legitimately has "
            f"none.")
    for name, why in sorted(failures.items()):
        warnings.append(f"extractor {name!r} failed and emitted nothing: {why}")

    # A requirement that did not run is survivable — the consumer records what it could not
    # look up — but it changes what the graph MEANS, so it cannot be left to the reader to
    # notice that half the edges are missing because of a flag.
    present = {r.name for r in regs}
    for reg in regs:
        absent = [d for d in reg.requires if d not in present]
        if absent:
            warnings.append(
                f"extractor {reg.name!r} needs {', '.join(absent)}, which did not run. It "
                f"still ran, and everything it could not resolve is in `unresolved` — but "
                f"edges it would otherwise have produced are missing.")

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "workspace": {"root": str(ws["root"]), "name": ws.get("name", ""),
                      "config": str(ws.get("config_path", ""))},
        "components": [
            {"name": c["name"], "path": c["path"], "role": c.get("role", ""),
             "stack": c.get("stack", ""), "claimed_by": act.by_component.get(c["name"], [])}
            for c in components
        ],
        "components_unclaimed": unclaimed,
        "extractors": [
            {"name": r.name, "layer": r.layer, "module": r.module, "globs": list(r.globs),
             "requires": list(r.requires), "components": act.by_extractor.get(r.name, []),
             "nodes": len(results[r.name].nodes) if r.name in results else 0,
             "edges": len(results[r.name].edges) if r.name in results else 0,
             "unresolved": len(results[r.name].unresolved) if r.name in results else 0,
             "failed": failures.get(r.name, "")}
            for r in regs
        ],
        "totals": {"nodes": len(merged.nodes), "edges": len(merged.edges),
                   "unresolved": len(merged.unresolved), "components": len(components)},
        "warnings": warnings,
    }


def load_installer():
    """Import `sdlc-init.py` for its workspace reader and component discovery.

    The hyphen makes it un-importable by name, hence the spec dance. The direction matters
    and only works one way: this Tier-2 pipeline may depend on the stdlib-only installer,
    never the reverse.
    """
    root = Path(__file__).resolve().parents[2]
    return _load_module(root / "sdlc-init.py", "sdlc_init")


def check_output_dir(out: Path, repo_root: Path | None = None) -> str | None:
    """This pipeline's binding of the shared output-location rule. See `outdir`."""
    return _check_output_dir(
        out, repo_root, what="graph",
        detail=(f"A built graph carries your workspace's component names, file paths and "
                f"line numbers. It belongs in {SCRATCH_DIR}/; the COMMITTED baseline is "
                f"published deliberately with --publish-snapshot, not by an output path."),
    )


# ── cli ───────────────────────────────────────────────────────────────────────


def _print_report(report: dict) -> None:
    ws = report["workspace"]
    print(f"workspace: {ws['root']}")
    print(f"run:       {report['run_id']}   status: {report['status']}")
    print()
    print(f"{'component':<22} {'role':<14} {'stack':<14} extractors")
    for c in report["components"]:
        claimed = ", ".join(c["claimed_by"]) if c["claimed_by"] else "—"
        print(f"{c['name']:<22} {c['role']:<14} {c['stack']:<14} {claimed}")
    print()
    if report["extractors"]:
        print(f"{'extractor':<22} {'layer':<10} {'nodes':>6} {'edges':>6} {'unres':>6}  globs")
        for e in report["extractors"]:
            print(f"{e['name']:<22} {e['layer']:<10} {e['nodes']:>6} {e['edges']:>6} "
                  f"{e['unresolved']:>6}  {', '.join(e['globs'])}")
    else:
        print("extractors: none registered")
    t = report["totals"]
    print()
    print(f"totals: {t['nodes']} nodes, {t['edges']} edges, {t['unresolved']} unresolved")
    for w in report["warnings"]:
        print(f"\n!! {w}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the domain graph for a workspace.")
    ap.add_argument("--workspace", required=True, metavar="TOML",
                    help="your filled-in sdlc-workspace.toml. Required, with no default: the "
                         "root and the component set live there, and a tool that guessed them "
                         "would be correct on exactly one machine")
    ap.add_argument("--out", default=f"{SCRATCH_DIR}/graph", metavar="DIR",
                    help=f"output directory (default: {SCRATCH_DIR}/graph). Committed trees are "
                         f"refused")
    ap.add_argument("--only", metavar="NAMES",
                    help="comma-separated extractor names to run (default: all registered)")
    ap.add_argument("--components", metavar="NAMES",
                    help="comma-separated component names to build (default: all discovered)")
    ap.add_argument("--run-id", default="", metavar="ID",
                    help="stamp this build with a known id instead of a fresh one")
    args = ap.parse_args(argv)

    out = Path(args.out)
    if refusal := check_output_dir(out):
        print(f"!! {refusal}")
        return 2

    def _split(v: str | None) -> list[str] | None:
        return [s.strip() for s in v.split(",") if s.strip()] if v else None

    try:
        report = build(Path(args.workspace), out, run_id=args.run_id,
                       only=_split(args.only), components_filter=_split(args.components))
    except BuildError as exc:
        print(f"!! {exc}")
        return 2

    _print_report(report)
    print(f"\nwrote {out}/graph.jsonl, {out}/unresolved.jsonl, {out}/build_report.json")
    # A build that read nothing must not exit 0. Every other tool in this repo treats a
    # confidently empty result as the failure it is, and the pipeline that produces the
    # graph is the last place to make an exception.
    return 2 if report["status"] in ("no-extractors", "no-activation", "all-failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
