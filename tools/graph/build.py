"""Build the domain graph for a workspace.

    cd <your repo> && python <harness>/tools/graph/build.py     # no config file at all
    python tools/graph/build.py --workspace sdlc-workspace.toml # a multi-repo workspace

Six stages, in order: **discover → activate → extract → link → emit → report**.

Discovery is not reimplemented here. It reuses the installer's `read_workspace()` and
`discover_components()`, so "which directories are components" has exactly one
implementation and the graph is built over precisely the set the generated profile names.

## One repository is the degenerate one-component case

With no `--workspace`, the enclosing repository is found by walking up from the current
directory and treated as a one-component workspace — `root` is reachable from inside a
repository, so the template's own rule (declare only what detection cannot reach) says it
must not be asked for. It stays required *inside a config file*, where "wherever you ran it
from" would name a different workspace on every machine, and it stays required for
multi-repo, which no scanner can infer from inside one clone.

Its output defaults to `<repo>/.sdlc/graph` — the repository's own facts, in the directory
it commits them from, so the next person to clone it inherits the graph. A workspace graph
is about no single repository and keeps defaulting to the gitignored scratch dir.

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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402
from tools.graph.core.lib import emit, manifest as manifest_lib, repo_walk  # noqa: E402
from tools.graph import snapshot as snapshot_lib  # noqa: E402
from tools.graph.core.model import (  # noqa: E402
    ExtractResult, Node, check_patterns, check_unresolved, group_unresolved)
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.outdir import (  # noqa: E402
    OWN_ARTIFACT_DIR, SCRATCH_DIR, check_output_dir as _check_output_dir)

cli.use_utf8()

SCHEMA_VERSION = 1

#: How many pattern groups the console prints. The full set is always in the report file —
#: this bounds the terminal, never the record.
TOP_PATTERNS = 12

#: Where the reusable, technology-scoped extractors live. One subpackage per technology.
EXTRACTOR_ROOT = Path(__file__).resolve().parent / "extractors"

#: Generic link passes — joins no single extractor can make, because they need the whole
#: graph rather than one component's files.
LINK_ROOT = Path(__file__).resolve().parent / "links"

#: Profiles committed with the harness. A workspace names one with `profile = "<name>"`;
#: anything containing a separator is instead a path to a profile the developer keeps
#: outside this repository, which is where a real workspace's profile belongs.
PROFILE_ROOT = Path(__file__).resolve().parent / "profiles"


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
    unresolved_fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    unresolved_patterns: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class LinkRegistration:
    """One link pass. No globs: it activates on the graph, not on files."""

    name: str
    layer: str
    link_pass: Any
    module: str = ""
    requires: tuple[str, ...] = ()
    unresolved_fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    unresolved_patterns: dict[str, tuple[str, ...]] = field(default_factory=dict)


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


def _declared_map(obj: Any, name: str, where: str, attr: str, noun: str, singular: str,
                  mod: Any = None) -> dict[str, tuple[str, ...]]:
    """Read and check a kind-keyed declaration (`UNRESOLVED_FIELDS`, `UNRESOLVED_PATTERNS`).

    Both have the same shape — a kind mapped to a tuple of non-empty names — and the same
    failure modes, so they share one reader. Two copies of this validation would be two
    chances to tighten one and not the other, which is the argument `_yaml` and `_lang`
    already made for their own subjects.

    Read from the **module** by default, exactly as `EXTRACTORS` is, because a package's
    extractors normally share a vocabulary — a class attribute narrows it for one extractor
    when they genuinely differ.

    **Looking in only one of those two places is how this check first shipped doing nothing.**
    The declarations were written at module level and read off the instance, so every one of
    them resolved to `{}` — "this kind promises nothing extra", which is indistinguishable
    from no declaration at all. That is precisely the defect item 85 exists to remove,
    reproduced one layer up inside its own fix, and the validator below could not catch it
    because a declaration that never arrives is never malformed. Only running it did.

    Fatal on a malformed declaration for the same reason: a typo that degraded to `{}` would
    silently disable the contract for exactly the extractor whose author got it wrong. Same
    trap as `INPUT_GLOBS = "pom.xml"` becoming one pattern per character, refused the same way.
    """
    declared = getattr(obj, attr, None)
    if declared is None and mod is not None:
        declared = getattr(mod, attr, None)
    if declared is None:
        return {}
    if not isinstance(declared, dict):
        raise BuildError(f"{where}: {name!r} has {attr} of type "
                         f"{type(declared).__name__}, expected a dict of kind -> {noun}")
    out: dict[str, tuple[str, ...]] = {}
    for kind, names in declared.items():
        if not isinstance(kind, str) or not kind.strip():
            raise BuildError(f"{where}: {name!r} declares {attr} under a "
                             f"non-string kind {kind!r}")
        if isinstance(names, str) or not isinstance(names, (tuple, list)):
            raise BuildError(
                f"{where}: {name!r} declares {attr}[{kind!r}] as "
                f"{names!r}. A bare string is iterable and would become one {singular} "
                f"per character — use a tuple, even for one: (\"language\",)")
        for one in names:
            if not isinstance(one, str) or not one.strip():
                raise BuildError(f"{where}: {name!r} declares {attr}[{kind!r}] "
                                 f"containing {one!r}, which is not a name")
        out[kind] = tuple(names)
    return out


def _unresolved_fields(obj: Any, name: str, where: str,
                       mod: Any = None) -> dict[str, tuple[str, ...]]:
    """What each kind this extractor emits promises beyond `UNRESOLVED_COMMON`."""
    return _declared_map(obj, name, where, "UNRESOLVED_FIELDS", "field names",
                         "required field", mod)


def _unresolved_patterns(obj: Any, name: str, where: str,
                         mod: Any = None) -> dict[str, tuple[str, ...]]:
    """The closed set of shapes each kind can take, for grouping the validation report.

    A kind absent here has no vocabulary and groups whole, which is a legitimate state and
    reported as such — **"this kind has one shape" and "nobody has classified it" must not
    look the same in a summary**, and only the second is a gap.
    """
    return _declared_map(obj, name, where, "UNRESOLVED_PATTERNS", "pattern names",
                         "pattern", mod)


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
    here = Path(__file__).resolve().parent
    for sub in ("extractors", "links", "profiles"):
        if pkg.parent == here / sub:
            return f"tools.graph.{sub}.{pkg.name}"
    return None


def _validate_link(obj: Any, where: str) -> tuple[str, tuple[str, ...]]:
    name = getattr(obj, "name", None)
    if not isinstance(name, str) or not name.strip():
        raise BuildError(f"{where}: link pass has no usable `name` (got {name!r}). The name is "
                         f"recorded on every edge it emits, so provenance depends on it")
    if not callable(getattr(obj, "link", None)):
        raise BuildError(f"{where}: {name!r} has no callable `link(ctx, nodes, edges)`")
    return name, _str_tuple(obj, "REQUIRES", name, where, required=False)


def discover(root: Path | None = None, layer: str = "stack",
             seen: dict[str, str] | None = None) -> tuple[list[Registration],
                                                          list[LinkRegistration]]:
    """Import every subpackage of `root` and collect its `EXTRACTORS` and `LINK_PASSES`.

    Scanning rather than maintaining a list means adding one is adding a directory, with no
    second place to forget. The cost of scanning is that a broken module could be skipped
    silently, so it is not: an import error, a package exporting neither tuple, an empty one,
    or a duplicate name all raise. The directory layout is the layer boundary, which is why
    the layer is passed in rather than guessed from the module name.

    `seen` is shared across roots so a profile cannot register a name a stack extractor
    already uses. Names are recorded on edges, so two would be indistinguishable in
    provenance — and the layer that lost the race would simply have its shard overwritten.
    """
    root = EXTRACTOR_ROOT if root is None else root
    if not root.is_dir():
        return [], []

    regs: list[Registration] = []
    links: list[LinkRegistration] = []
    seen = {} if seen is None else seen
    for pkg in sorted(p for p in root.iterdir() if p.is_dir()):
        if pkg.name.startswith((".", "_")):
            continue
        r, l = discover_package(pkg, layer, seen, root_label=root.name)
        regs += r
        links += l
    return regs, links


def discover_package(pkg: Path, layer: str, seen: dict[str, str] | None = None,
                     root_label: str = "") -> tuple[list[Registration],
                                                    list[LinkRegistration]]:
    """Register ONE package — the unit both a scanned root and a selected profile share.

    A profile is named directly rather than scanned for, so the directory a workspace points
    at IS the package. Scanning it for children instead finds nothing and registers nothing,
    which reads exactly like a profile that had nothing to contribute.
    """
    seen = {} if seen is None else seen
    init = pkg / "__init__.py"
    if not init.is_file():
        raise BuildError(f"{pkg} has no __init__.py, so it registers nothing. Delete it or "
                         f"give it an `EXTRACTORS` tuple — a directory that looks like an "
                         f"extractor and is not one is how an edge class goes missing")
    where = f"{root_label}/{pkg.name}" if root_label else pkg.name
    try:
        # The repo's own packages are imported under their real dotted name so there is
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
    passes = getattr(mod, "LINK_PASSES", None)
    if entries is None and passes is None:
        raise BuildError(f"{where}: exports neither `EXTRACTORS` nor `LINK_PASSES`. Every "
                         f"package under this root must export one, or it is imported and "
                         f"then ignored")
    for attr, value in (("EXTRACTORS", entries), ("LINK_PASSES", passes)):
        if value is not None and (not isinstance(value, (tuple, list)) or not value):
            # Item 21: a profile that only tunes traversal is a live capability, and
            # its rules file is the proof. Without one, empty stays a mistake.
            if not value and (pkg / "traversal_rules.yml").exists():
                continue
            raise BuildError(f"{where}: `{attr}` is empty. An empty package is a mistake "
                             f"that reads as a capability this harness does not have "
                             f"(a rules-only profile is the one exception, proven by a "
                             f"traversal_rules.yml beside its __init__.py)")

    def claim(name: str, kind: str) -> None:
        if name in seen:
            raise BuildError(f"{where}: {kind} name {name!r} is already registered by "
                             f"{seen[name]}. Names are recorded on edges and drive the "
                             f"output shards, so two would overwrite each other")
        seen[name] = where

    regs: list[Registration] = []
    links: list[LinkRegistration] = []
    for obj in entries or ():
        name, globs, requires = _validate(obj, where)
        claim(name, "extractor")
        regs.append(Registration(name=name, layer=layer, globs=globs, requires=requires,
                                 extractor=obj, module=where,
                                 unresolved_fields=_unresolved_fields(obj, name, where, mod),
                                 unresolved_patterns=_unresolved_patterns(
                                     obj, name, where, mod)))
    for obj in passes or ():
        name, requires = _validate_link(obj, where)
        claim(name, "link pass")
        links.append(LinkRegistration(name=name, layer=layer, requires=requires,
                                      link_pass=obj, module=where,
                                      unresolved_fields=_unresolved_fields(obj, name, where, mod),
                                      unresolved_patterns=_unresolved_patterns(
                                          obj, name, where, mod)))
    return regs, links


def discover_extractors(root: Path | None = None, layer: str = "stack") -> list[Registration]:
    """The extractor half of `discover`, ordered. Kept as the common entry point."""
    return order_extractors(discover(root, layer)[0])


def resolve_profile(ws: dict) -> Path | None:
    """The profile directory this workspace selected, or None when it selected none.

    **Selected, never activated globally.** A profile describes formats one organisation
    invented, so every adopter running every committed profile is wrong by construction —
    it would read another organisation's file shapes into their graph and be right often
    enough to be believed.

    A bare name resolves under the harness's own `profiles/`; anything containing a
    separator is a path relative to the workspace config, which is where a real workspace's
    profile belongs — it is that developer's code, not this repository's.

    A named profile that does not exist is FATAL. Continuing without it produces a graph
    missing exactly the edges nothing else can see, and nothing in the output would say so.
    """
    name = (ws.get("profile") or "").strip()
    if not name:
        return None
    if "/" in name or "\\" in name:
        candidate = (Path(ws["config_path"]).parent / name).resolve()
    else:
        candidate = PROFILE_ROOT / name
    if not candidate.is_dir():
        raise BuildError(
            f"`profile = {name!r}` names {candidate}, which does not exist.\n"
            f"  Refusing to continue: a profile holds the formats nothing else can read, so "
            f"building without it produces a graph that is short by exactly the edges you "
            f"wrote it for — and nothing in the output would say so.")
    return candidate


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
        # The build's own artifact directory is never workspace input. Latent until an
        # extractor globbed `*.json`: the chained build writes graph output and snapshot
        # manifests into `<component>/.sdlc/`, and a later build re-reading them would be
        # extracting facts from itself — every rebuild would then differ from the last
        # (the manifest changes per publish), which surfaced as freshness stamps that
        # never stabilised.
        rels = [(p, p.relative_to(path).as_posix(), p.name) for p in found
                if OWN_ARTIFACT_DIR not in p.relative_to(path).parts]
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


def config_digest(config: dict) -> str:
    """A digest of everything extractors and link passes can read from the workspace.

    Part of the freshness stamp: a `[[canonical]]` edit changes what a build concludes
    without touching one component file, and a stamp that ignored it would call the
    resulting shards fresh.
    """
    return hashlib.sha1(json.dumps(config, sort_keys=True, default=str)
                        .encode("utf-8")).hexdigest()


def input_digests(act: Activation, components: list[dict],
                  root: Path) -> dict[str, dict[str, str]]:
    """Per component, per extractor: a digest of the files that extractor would read.

    Names AND content, so a deleted or renamed file changes the digest. A file that
    cannot be read poisons the digest with an `unreadable-` prefix, which
    `stamp_matches` never accepts: we do not know its content, so the only safe answer
    is stale — the same rule `manifest.file_digest` records with `UNREADABLE`.
    """
    out: dict[str, dict[str, str]] = {}
    for (ext, comp), paths in act.files.items():
        base = root / _path_of(components, comp)
        h = hashlib.sha1()
        poisoned = False
        for p in sorted(paths):
            try:
                rel = p.relative_to(base).as_posix()
            except ValueError:
                rel = p.as_posix()
            digest = manifest_lib.file_digest(p)
            poisoned = poisoned or digest == manifest_lib.UNREADABLE
            h.update(rel.encode("utf-8"))
            h.update(b"\x00")
            h.update(digest.encode("utf-8"))
            h.update(b"\x00")
        out.setdefault(comp, {})[ext] = \
            (f"unreadable-{h.hexdigest()}" if poisoned else h.hexdigest())
    return out


def stamp_matches(current: str, stamped: str) -> bool:
    """Whether an input digest still matches its stamp. Unreadable never matches —
    on either side, in either build: content nobody could read is content nobody may
    claim is unchanged."""
    if not current or not stamped:
        return False
    if current.startswith("unreadable-") or stamped.startswith("unreadable-"):
        return False
    return current == stamped


def requires_closure(regs: list[Registration]) -> dict[str, frozenset[str]]:
    """Each extractor's transitive REQUIRES set — the declared cache-dependency graph.

    Declared is the whole point: an extractor may only read what a `REQUIRES` entry
    ordered to run before it, so the closure is exactly the set of extractors whose
    outputs could have reached it. Reuse decisions lean on this rather than on any
    guess about which cache entries were actually read.
    """
    direct = {r.name: set(r.requires) for r in regs}
    closure: dict[str, frozenset[str]] = {}

    def walk(name: str, seen: set[str]) -> set[str]:
        if name in closure:
            return set(closure[name])
        out: set[str] = set()
        for dep in direct.get(name, ()):
            if dep in seen:
                continue
            out.add(dep)
            out |= walk(dep, seen | {name})
        return out

    for name in direct:
        closure[name] = frozenset(walk(name, {name}))
    return closure


def plan_incremental(root: Path, components: list[dict], act: Activation,
                     regs: list[Registration], digests: dict[str, dict[str, str]],
                     version: str, config_sha: str
                     ) -> tuple[list[str], dict[str, str], set[str]]:
    """Decide what an incremental build may reuse. Returns (reused, rebuilt, cache_exts).

    A component is reusable only when every claim below holds; the first that fails is
    its recorded reason, because "rebuilt" without a why is a decision nobody can audit:

    * its shard carries freshness stamps (schema v2 — published after item 12);
    * the extractor set and workspace config match what the shard was built under;
    * every per-extractor input digest matches — its own files did not change;
    * nothing it depends on (transitive `REQUIRES`) changed **anywhere**: the shared
      cache lets one component's files feed another's facts (a compose file answering a
      sibling's env reads), and which entries a reader took is not recorded, so any
      change to a dependency's inputs in any component is conservatively a change for
      every dependent;
    * its shard bytes still match their recorded digest — stale stamps on tampered
      bytes would launder the tamper into a reuse.

    `cache_exts` is the price of the shared cache: the transitive dependencies of every
    rebuilt component's active extractors must RUN for all components so the cache holds
    what a full build's would — their facts for reused components are then discarded,
    because the shard already carries the identical rows (determinism, item 13's check).
    """
    stamps = {c["name"]: snapshot_lib.stamps_of(root, c) for c in components}

    changed_exts: set[str] = set()
    for comp in components:
        name = comp["name"]
        cur = digests.get(name, {})
        st = stamps[name]
        if st is None or st["extractor_version"] != version or st["config_sha"] != config_sha:
            changed_exts |= set(cur)
            continue
        for ext in set(cur) | set(st["inputs"]):
            if not stamp_matches(cur.get(ext, ""), st["inputs"].get(ext, "")):
                changed_exts.add(ext)

    closure = requires_closure(regs)
    reused: list[str] = []
    rebuilt: dict[str, str] = {}
    for comp in components:
        name = comp["name"]
        st = stamps[name]
        cur = digests.get(name, {})
        if st is None:
            rebuilt[name] = ("no freshness stamps — no committed shard, or one published "
                            "before stamps existed")
            continue
        if st["extractor_version"] != version:
            rebuilt[name] = "the extractor set changed since its shard was published"
            continue
        if st["config_sha"] != config_sha:
            rebuilt[name] = "the workspace configuration changed since its shard was published"
            continue
        if any(not stamp_matches(cur.get(e, ""), st["inputs"].get(e, ""))
               for e in set(cur) | set(st["inputs"])):
            rebuilt[name] = "its own files changed"
            continue
        deps = set()
        for ext in act.by_component.get(name) or []:
            deps |= closure.get(ext, frozenset())
        dirty = sorted(deps & changed_exts)
        if dirty:
            rebuilt[name] = (f"depends on {', '.join(dirty)}, whose inputs changed in "
                            f"another component")
            continue
        if snapshot_lib._digest(snapshot_lib.shard_dir(root, comp)) != st["sha256"]:
            rebuilt[name] = "its shard bytes no longer match their recorded digest"
            continue
        reused.append(name)

    cache_exts: set[str] = set()
    for name in rebuilt:
        for ext in act.by_component.get(name) or []:
            cache_exts |= closure.get(ext, frozenset())
    return reused, rebuilt, cache_exts


def filter_activation(act: Activation, rebuilt: set[str], cache_exts: set[str]) -> Activation:
    """The activation an incremental build runs: rebuilt components in full, plus the
    cache-feeding extractors everywhere — in the ORIGINAL claimed order, because the
    shared cache is filled in that order and a reordered fill is a different build."""
    files = {(e, c): v for (e, c), v in act.files.items()
             if c in rebuilt or e in cache_exts}
    return Activation(
        by_extractor={e: [c for c in comps if (e, c) in files]
                      for e, comps in act.by_extractor.items()},
        by_component={c: [e for e in exts if (e, c) in files]
                      for c, exts in act.by_component.items()},
        files=files)


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
                   root: Path, run_id: str,
                   config: dict) -> tuple[dict[str, ExtractResult], dict, list[str]]:
    """Run each activated extractor over only the components it claimed.

    Returns `(results, failures, contract_violations)`. The third is **always** returned
    rather than gathered through an opt-in argument: a check a caller can forget to ask for
    is a check that goes unrun, which is the same shape as the silence this enforcement
    exists to remove.

    An extractor is handed the components its globs matched, not the whole workspace, so it
    cannot read a tree that never activated it — and the report can say exactly which
    components each extractor covered.

    `cache` is shared across extractors on purpose: resolving a component's configuration is
    expensive and several extractors need the same answer, and two extractors resolving it
    twice is two chances to resolve it differently.
    """
    owned, failures, violations = run_extractors_owned(
        regs, act, components, root, run_id, config)
    results: dict[str, ExtractResult] = {}
    for name, per_component in owned.items():
        pooled = ExtractResult()
        for res in per_component.values():
            pooled.extend(res)
        results[name] = pooled
    return results, failures, violations


def run_extractors_owned(regs: list[Registration], act: Activation, components: list[dict],
                         root: Path, run_id: str, config: dict
                         ) -> tuple[dict[str, dict[str, ExtractResult]], dict, list[str]]:
    """`run_extractors`, with ownership kept: one `extract()` call per (extractor, component).

    Item 13's snapshot commits each component's facts into that component's own tree, and
    ownership cannot be read off a finished row — `source_file` is component-relative, so
    two components' `pom.xml` rows are indistinguishable once pooled. The only place a
    row's component is structurally known is the extract call that produced it, so the
    call is made per component and the answer is kept, rather than declared or inferred.

    Behaviour is otherwise the old loop's exactly. Extractors already iterate
    `ctx.repos` one repo at a time, so narrowing the context to one component changes no
    input any extractor sees; `cache` stays one shared object in the same claimed order,
    so cross-component resolution (a compose file answering for another component's env
    reads) is unchanged; and a failure still discards the WHOLE extractor — per-component
    salvage would be an improvement, but a different contract than the one the report and
    its tests pin today.
    """
    owned: dict[str, dict[str, ExtractResult]] = {}
    violations: list[str] = []
    cache: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for reg in regs:
        claimed = act.by_extractor.get(reg.name) or []
        if not claimed:
            continue
        per_component: dict[str, ExtractResult] = {}
        failed = ""
        for comp in claimed:
            ctx = ExtractContext(repos=repo_refs(components, root, [comp]),
                                 run_id=run_id, config=config, cache=cache,
                                 files={comp: act.files.get((reg.name, comp), [])})
            try:
                res = reg.extractor.extract(ctx)
            except Exception as exc:  # noqa: BLE001 - one extractor must not kill the build
                # A failing extractor is recorded and the build continues, for the same
                # reason a malformed file lands in `unresolved` rather than aborting: one
                # broken reader must not cost a workspace its whole graph. It is NOT silent
                # — it is a warning, it is in the report, and the edges it would have made
                # are visibly absent.
                failed = f"{type(exc).__name__}: {exc}"
                break
            if not isinstance(res, ExtractResult):
                failed = (f"returned {type(res).__name__}, not an ExtractResult — its "
                          f"nodes, edges and unresolved rows are all unreadable")
                break
            # Stamp the run centrally rather than trusting each extractor to carry
            # `ctx.run_id` through: an extractor that forgets produces edges the store
            # cannot attribute to a build, and that is invisible until someone tries to
            # expire an old run's rows.
            res.edges = [e if e.run_id == run_id else replace(e, run_id=run_id)
                         for e in res.edges]
            # Checked centrally, beside the run_id stamp and for the same reason: what must
            # be true of every row is enforced where every row passes, not at thirty-nine
            # call sites that each have to remember. The extractor's own `UNRESOLVED_FIELDS`
            # supplies the per-kind half, so core never learns the vocabulary.
            violations.extend(check_unresolved(res.unresolved, reg.unresolved_fields, reg.name))
            violations.extend(check_patterns(res.unresolved, reg.unresolved_patterns, reg.name))
            per_component[comp] = res
        if failed:
            failures[reg.name] = failed
            continue
        owned[reg.name] = per_component
    return owned, failures, violations


def run_link_passes(links: list[LinkRegistration], merged: ExtractResult,
                    components: list[dict], root: Path, run_id: str,
                    config: dict) -> tuple[dict[str, ExtractResult], dict, list[str]]:
    """Run each link pass over the whole graph, in declared order.

    A pass sees every node and edge, which is the point: whether a topic one component writes
    is produced by another, or which of two deployment packages is canonical, are facts no
    single extractor can reach from one component's files.

    The lists are handed over live. A pass may REWRITE entries — that is how canonicality and
    damping are applied — and whatever it returns is added on top. Passes run in order, so a
    later one sees what an earlier one decided.
    """
    out: dict[str, ExtractResult] = {}
    failures: dict[str, str] = {}
    violations: list[str] = []
    for lp in links:
        ctx = ExtractContext(repos=repo_refs(components, root), run_id=run_id, config=config)
        try:
            res = lp.link_pass.link(ctx, merged.nodes, merged.edges)
        except Exception as exc:  # noqa: BLE001 - one pass must not kill the build
            failures[lp.name] = f"{type(exc).__name__}: {exc}"
            continue
        if not isinstance(res, ExtractResult):
            failures[lp.name] = (f"returned {type(res).__name__}, not an ExtractResult — its "
                                 f"nodes, edges and unresolved rows are all unreadable")
            continue
        res.edges = [e if e.run_id == run_id else replace(e, run_id=run_id) for e in res.edges]
        violations.extend(check_unresolved(res.unresolved, lp.unresolved_fields, lp.name))
        violations.extend(check_patterns(res.unresolved, lp.unresolved_patterns, lp.name))
        out[lp.name] = res
    return out, failures, violations


def resolve_workspace(workspace: Path | None, target: Path | None = None) -> dict:
    """The workspace to build: the config file if there is one, else the repo we stand in.

    Two routes in, one shape out — see `sdlc-init.workspace_for_repo`. Kept separate from
    `build` because where output may be written depends on whether the graph is about one
    repository or several, and that has to be answerable before the build runs.
    """
    si = load_installer()
    try:
        if workspace is None:
            return si.workspace_for_repo(target)
        return si.read_workspace(workspace)
    except si.WorkspaceError as exc:
        raise BuildError(str(exc)) from exc


def build(workspace: Path | dict | None, out: Path, *, run_id: str = "",
          only: list[str] | None = None, components_filter: list[str] | None = None,
          extractor_root: Path | None = None, target: Path | None = None,
          publish_snapshot: bool = False, check_snapshot: bool = False,
          incremental: bool = False) -> dict:
    """Run the whole pipeline and return the build report.

    `workspace` is a config file, or None to infer the repository containing `target`
    (default: the cwd). A caller that already resolved one may pass the dict itself, so
    the discovery scan is not repeated.

    `publish_snapshot` writes each component's extracted facts into that component's own
    `.sdlc/graph/` (item 13); `check_snapshot` byte-compares a fresh extraction against
    those committed shards instead. Both refuse quietly-wrong states: a build with
    failures or contract violations publishes nothing, because a shard missing an
    extractor's facts commits an absence that reads exactly like a clean answer.
    """
    # **A question writes nothing, and `out` is what it is asking about.** Under
    # `--check-snapshot` `out` is the committed `.sdlc/graph/` (item 102) whose bytes the
    # command exists to leave alone, so `out` is not a name ANY line of this function may
    # use: it is rebound to None on the first statement and every write is skipped.
    # Measured before it was removed: nothing outside this function read what the check
    # produced, and what it produced was the provenance of the publish under audit,
    # replaced — a fresh `run_id` in `build_report.json`, `snapshot.published` overwritten
    # by the answer to a different question, and `manifest.json` re-stamped — leaving the
    # tree the next `intake.open_run` refuses by name.
    #
    # This is the SIXTH instance of a-check-that-writes here (items 96, 100, 65, 120, 133)
    # and item 120's defect verbatim, one flag along in this same module, with its own fix
    # — `--check-fresh`, which writes nothing — standing beside it as the control.
    # **Removed rather than redirected**, for item 133's reason: a scratch is a location
    # somebody can point back into the repository, and this is the second time that has
    # had to be undone here.
    #
    # **At the TOP, not at the emit block 150 lines down.** Nothing above the emit block
    # writes today, so the narrower placement was correct today; item 169's lesson is that
    # a guard drawn one statement further is defeated by the next statement added, and the
    # next write added to this function must land under the guard without anybody
    # re-deriving that it does.
    out = None if check_snapshot else out
    si = load_installer()
    ws = workspace if isinstance(workspace, dict) else resolve_workspace(workspace, target)
    try:
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

    seen: dict[str, str] = {}
    regs, links = discover(extractor_root, "stack", seen)
    generic, generic_links = discover(LINK_ROOT, "link", seen)
    regs, links = regs + generic, links + generic_links

    profile_dir = resolve_profile(ws)
    if profile_dir is not None:
        profile_regs, profile_links = discover_package(
            profile_dir, "profile", seen, root_label="profiles")
        regs, links = regs + profile_regs, links + profile_links

    regs, links = order_extractors(regs), order_extractors(links)
    if only:
        wanted = set(only)
        known = {r.name for r in regs} | {lp.name for lp in links}
        unknown = sorted(wanted - known)
        if unknown:
            raise BuildError(f"--only names {', '.join(unknown)}, which is not registered. "
                             f"Registered: {', '.join(sorted(known)) or '(none)'}")
        regs = [r for r in regs if r.name in wanted]      # already topologically ordered
        links = [lp for lp in links if lp.name in wanted]

    run_id = run_id or uuid.uuid4().hex[:12]
    act = activate(regs, components, root)
    config = {"workspace_name": ws.get("name", ""), "canonical": ws.get("canonical", []),
              "generated": ws.get("generated", []),
              "entrypoint": ws.get("entrypoint", []),
              "alias": ws.get("alias", []),
              "components": components, "profile": str(profile_dir or "")}

    inc: dict | None = None
    rebuilt_set: set[str] = set()
    run_act = act
    if incremental:
        digests = input_digests(act, components, root)
        reused, rebuilt, cache_exts = plan_incremental(
            root, components, act, regs, digests,
            extractor_version(regs), config_digest(config))
        rebuilt_set = set(rebuilt)
        # Cache-feeding extractors run everywhere so the shared cache holds what a full
        # build's would; only their runs in REBUILT components contribute facts. The
        # distinction is visible in the report, not silent.
        inc = {"reused": sorted(reused), "rebuilt": rebuilt,
               "cache_effect_extractors": sorted(cache_exts)}
        run_act = filter_activation(act, rebuilt_set, cache_exts)
        if reused:
            print(f"incremental: reusing {len(reused)} of {len(components)} committed "
                  f"shard(s); rebuilding "
                  f"{', '.join(sorted(rebuilt)) if rebuilt else 'nothing'}")
        else:
            print("incremental: nothing reusable - running a full build "
                  "(every component: " +
                  "; ".join(f"{n}: {why}" for n, why in sorted(rebuilt.items())[:3]) +
                  (" ..." if len(rebuilt) > 3 else "") + ")")

    owned, failures, violations = run_extractors_owned(
        regs, run_act, components, root, run_id, config)
    results = {}
    for name, per_component in owned.items():
        pooled = ExtractResult()
        for comp_name, res in per_component.items():
            # Under an incremental build a reused component's fresh rows exist only for
            # the cache effect — its shard already carries the identical rows, and both
            # in the pool would still emit once (the emitters dedup), but the report's
            # totals would count them twice.
            if inc is None or comp_name in rebuilt_set:
                pooled.extend(res)
        results[name] = pooled

    # ── link ──────────────────────────────────────────────────────────────────
    merged = ExtractResult(nodes=component_nodes(components))
    for reg in regs:
        if (res := results.get(reg.name)) is not None:
            merged.extend(res)
    if inc is not None and inc["reused"]:
        loaded, _, missing, mismatched = snapshot_lib.load(
            root, [c for c in components if c["name"] in set(inc["reused"])])
        if missing or mismatched:
            # plan_incremental verified every reused shard moments ago; failing here
            # means the tree changed underneath the build, and continuing would pool an
            # unknown mixture of old and new.
            raise BuildError(
                f"incremental: shard(s) for {', '.join(sorted(missing + mismatched))} "
                f"changed between the reuse decision and the load - re-run the build")
        merged.extend(loaded)
    link_results, link_failures, link_violations = run_link_passes(
        links, merged, components, root, run_id, config)
    failures.update(link_failures)
    violations.extend(link_violations)

    # ── emit ──────────────────────────────────────────────────────────────────
    # Shards are written per producer so a failure can be traced to one of them; `merged`
    # already holds every extractor's output (the link stage needed it) and is extended only
    # with what the link passes ADDED. Extending it again here would double every edge.
    for lp in links:
        if (res := link_results.get(lp.name)) is not None:
            merged.extend(res)
    # `out` is already None under `--check-snapshot` — rebound at the head of the function,
    # not here, so the guarantee covers every statement rather than the ones below this one.
    if out is not None:
        extract_dir = out / "extract"
        for reg in regs:
            if (res := results.get(reg.name)) is not None:
                emit.write_jsonl(extract_dir / f"{reg.name}.jsonl", res, extractor=reg.name)
                emit.write_unresolved(extract_dir / f"{reg.name}.unresolved.jsonl",
                                      res.unresolved)
        for lp in links:
            if (res := link_results.get(lp.name)) is not None:
                emit.write_jsonl(out / "link" / f"{lp.name}.jsonl", res, extractor=lp.name)
                emit.write_unresolved(out / "link" / f"{lp.name}.unresolved.jsonl",
                                      res.unresolved)
        emit.write_jsonl(out / "graph.jsonl", merged, extractor="merged")
        # Item 21: a profile's traversal rules ride beside the graph they will rank, so
        # `impact.py --graph-dir` finds them without knowing the workspace. Copied, not
        # referenced — the graph dir must answer alone.
        if profile_dir is not None and (profile_dir / "traversal_rules.yml").exists():
            (out / "traversal_rules.yml").write_bytes(
                (profile_dir / "traversal_rules.yml").read_bytes())
        emit.write_unresolved(out / "unresolved.jsonl", merged.unresolved)

        mf = manifest_lib.Manifest(run_id=run_id, extractor_version=extractor_version(regs))
        for comp in components:
            path = root / comp["path"]
            mf.repos[comp["name"]] = manifest_lib.RepoStamp(
                alias=comp["name"], head=manifest_lib.git_head(path),
                dirty=manifest_lib.git_dirty(path))
        # No per-file digest map. `input_digests` above already digests the same
        # content per extractor and is what `stamp_matches` reads; a second copy,
        # keyed per file, was written here on every build and read by nothing -
        # and since `.sdlc/graph/` is committed, it shipped 1.34 MB of 40-hex
        # strings into every adopter's repository, where the secret gate reported
        # 269 of them as API keys.
        for name, res in {**results, **link_results}.items():
            mf.counts[name] = len(res.nodes) + len(res.edges)
        mf.save(out / "manifest.json")

    report = _report(ws, components, regs, links, act, results, link_results,
                     failures, merged, run_id, profile_dir, violations,
                     reused=len(inc["reused"]) if inc else 0)
    if inc is not None:
        report["incremental"] = inc

    if publish_snapshot or check_snapshot:
        # Facts only — assembled from the OWNED results, never from `merged`, which the
        # link passes have already rewritten in place. What a link pass concluded is
        # derived from the whole graph and belongs to no shard; it is recomputed at load.
        if failures or violations:
            # A shard published without a failed extractor's facts commits an absence
            # that reads exactly like a clean answer, in the artifact whose whole job
            # is to be diffable evidence.
            report["snapshot"] = {"refused": (
                f"{len(failures)} extractor failure(s) and {len(violations)} contract "
                f"violation(s) mean these facts are incomplete; an incomplete shard "
                f"reads exactly like a clean one")}
            print("snapshot: refused - " + report["snapshot"]["refused"])
        else:
            # An incremental publish rewrites only the rebuilt shards: the reused ones
            # were verified byte-identical to what their files extract to, so rewriting
            # them would change nothing and re-stamp nothing.
            pub_components = ([c for c in components if c["name"] in rebuilt_set]
                              if inc is not None else components)
            facts = component_facts(pub_components, regs, owned)
            if check_snapshot:
                # Item 133: no scratch is handed over, because `out` is the
                # committed `.sdlc/graph/` and a question must not write into
                # the repository it audits.
                drifted, unpublished = snapshot_lib.check(root, components, facts)
                report["snapshot"] = {"drifted": drifted, "unpublished": unpublished}
                for name in drifted:
                    print(f"snapshot: DRIFT in {name} - its committed shard no longer "
                          f"matches what its files extract to")
                for name in unpublished:
                    print(f"snapshot: {name} has no committed shard to check")
                if not drifted:
                    print(f"snapshot: {len(components) - len(unpublished)} shard(s) match "
                          f"their committed bytes")
            else:
                stamp_inputs = input_digests(act, components, root)
                version, cfg_sha = extractor_version(regs), config_digest(config)
                stamps = {c["name"]: {"inputs": stamp_inputs.get(c["name"], {}),
                                      "extractor_version": version,
                                      "config_sha": cfg_sha}
                          for c in pub_components}
                digests = snapshot_lib.publish(root, pub_components, facts, stamps)
                report["snapshot"] = {"published": sorted(digests)}
                print(f"snapshot: {len(digests)} shard(s) published, one per component, "
                      f"into each component's own .sdlc/graph/")

    if out is not None:
        (out / "build_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def component_facts(components: list[dict], regs: list[Registration],
                    owned: dict[str, dict[str, ExtractResult]]) -> dict[str, ExtractResult]:
    """Each component's own facts: its repo node plus what every extractor read from ITS
    files, in registration order so serialization is deterministic."""
    facts: dict[str, ExtractResult] = {}
    for comp in components:
        res = ExtractResult(nodes=component_nodes([comp]))
        for reg in regs:
            per_component = owned.get(reg.name) or {}
            if comp["name"] in per_component:
                res.extend(per_component[comp["name"]])
        facts[comp["name"]] = res
    return facts


def build_from_snapshot(workspace: Path | dict | None, out: Path, *,
                        target: Path | None = None) -> dict:
    """The graph from committed shards: no extraction, link passes recomputed at load.

    A partial set loads LOUDLY — the report and stdout name every component with no
    shard, because a developer with 3 of 26 components cloned has a graph that answers
    for 3, and silence about the other 23 is the "empty reads exactly like clean"
    failure at estate scale. A shard failing its own digest is refused outright: stale
    evidence about the wrong bytes is worse than absence.

    A load that finds NO shard at all is that same failure at full size and cannot read
    as a clean build: `status` is `snapshot-empty` (never `ok`), so the CLI exits
    non-zero, because a zero-node graph reported clean is exactly what an adopter's first
    rebuild from the committed snapshot must not silently produce.
    """
    si = load_installer()
    ws = workspace if isinstance(workspace, dict) else resolve_workspace(workspace, target)
    try:
        discovered = si.discover_components(ws)
    except si.WorkspaceError as exc:
        raise BuildError(str(exc)) from exc
    root: Path = ws["root"]
    components = discovered["components"]

    seen: dict[str, str] = {}
    _, links = discover(None, "stack", seen)
    _, generic_links = discover(LINK_ROOT, "link", seen)
    links = links + generic_links
    profile_dir = resolve_profile(ws)
    if profile_dir is not None:
        _, profile_links = discover_package(profile_dir, "profile", seen,
                                            root_label="profiles")
        links = links + profile_links
    links = order_extractors(links)

    merged, present, missing, mismatched = snapshot_lib.load(root, components)
    for name in mismatched:
        print(f"snapshot: REFUSING {name} - its shard does not match its own recorded "
              f"digest; a hand edit or a half-written publish. Rebuild and re-publish it")
    if not present:
        # Nothing loaded at all. The partial-set wording below ("answers only for what
        # is present") would be a lie, because nothing is present; and a zero-node graph
        # reported `ok` at exit 0 is item 34's "empty reads exactly like clean" in the
        # one command a fresh clone runs to rebuild from the committed snapshot. Named
        # here and refused via `status` below, on the same ladder as a mismatch.
        print(f"snapshot: 0 of {len(components)} component(s) loaded - there is nothing "
              f"to load and this is not a graph, only a build that could not look. "
              f"Publish shards first (build --publish-snapshot)."
              + (f" Missing: {', '.join(missing)}" if missing else ""))
    elif missing:
        print(f"snapshot: {len(missing)} of {len(components)} component(s) have no "
              f"committed shard - the graph below answers only for what is present. "
              f"Missing: {', '.join(missing)}")

    run_id = uuid.uuid4().hex[:12]
    config = {"workspace_name": ws.get("name", ""), "canonical": ws.get("canonical", []),
              "generated": ws.get("generated", []),
              "entrypoint": ws.get("entrypoint", []),
              "alias": ws.get("alias", []),
              "components": components, "profile": str(profile_dir or "")}
    link_results, link_failures, link_violations = run_link_passes(
        links, merged, components, root, run_id, config)
    for res in link_results.values():
        merged.extend(res)

    emit.write_jsonl(out / "graph.jsonl", merged, extractor="merged")
    # Item 21: a profile's traversal rules ride beside the graph they will rank, so
    # `impact.py --graph-dir` finds them without knowing the workspace. Copied, not
    # referenced — the graph dir must answer alone.
    if profile_dir is not None and (profile_dir / "traversal_rules.yml").exists():
        (out / "traversal_rules.yml").write_bytes(
            (profile_dir / "traversal_rules.yml").read_bytes())
    emit.write_unresolved(out / "unresolved.jsonl", merged.unresolved)
    report = {
        "status": ("snapshot-mismatch" if mismatched
                   else "snapshot-empty" if not present
                   else "ok"),
        "run_id": run_id,
        "workspace": {"name": _workspace_name(ws)},
        "snapshot": {"loaded": present, "missing": missing, "mismatched": mismatched},
        "link_failures": link_failures,
        "contract_violations": link_violations,
        "totals": {"nodes": len({n.id for n in merged.nodes}), "edges": len(merged.edges),
                   "unresolved": len(merged.unresolved)},
    }
    (out / "build_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def _workspace_name(ws: dict) -> str:
    """The workspace's NAME, never its path.

    **Item 57's rule, in a second module and a worse place.** The dashboard once
    embedded the machine's absolute path into a file adopters COMMIT; so did this,
    and `.sdlc/graph/build_report.json` is committed by design (item 102), so every
    adopter who ships their graph published the directory layout of the machine that
    built it. The harness's own private-coordinate guard sweeps the HARNESS tree and
    cannot see a file the harness writes into somebody else's repository.

    Falls back to the directory's own name so a single-repo report, which declares no
    workspace name, still says WHICH workspace it is about.
    """
    return str(ws.get("name") or "") or Path(str(ws.get("root") or "")).name


def _name_of(path: object) -> str:
    """A path's last segment, or "". What identifies a config or a profile is its
    name; the directories above it are the machine's, not the workspace's.

    **Both separators, explicitly, rather than `Path(text).name`.** A build report
    is written on one machine and read on any, and on POSIX a backslash is an
    ordinary character - so `Path` would hand back a Windows path whole and this
    function would publish the coordinates it exists to strip. Caught while
    auditing this item's own tests for the platform dependence that made item
    122's first cut pass on Windows and fail on Linux.
    """
    text = str(path or "").strip().replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1] if text else ""


def check_freshness(workspace: Path | dict | None, *,
                    target: Path | None = None) -> dict:
    """Is the committed snapshot current with the tree? Content answers, not commits.

    **It writes NOTHING, and that is item 120.** This wrote its three-key verdict to
    `build_report.json` — the same name the build's own provenance carries — so asking
    whether the graph was fresh REPLACED the committed record of how it was built: 12
    keys and 1,237 lines became 3 keys and 5, in a file `.sdlc/graph/` ships to every
    adopter. It then blocked the run it was asked during, because the worktree gate
    correctly refused a tree the harness had just dirtied.

    **The hazard was already written down, and fixed for ONE caller.** The MCP server
    handed this a scratch directory with a comment saying exactly why. A question does
    not persist its answer into a different question's file, so the write is gone
    rather than redirected — measured first: nothing anywhere read what it wrote.

    The input side of item 13's determinism check, and the cheap one: hash every file the
    extractors would read (seconds) instead of re-extracting everything (minutes), and
    compare against the stamps each shard recorded at publish. A commit SHA cannot answer
    this question — HEAD moves without a component's files moving, and a dirty tree moves
    files without HEAD — so commits stay in the build report as provenance and the digest
    of what would actually be read decides. Shard bytes are verified too: fresh stamps on
    tampered bytes would be a currency claim about evidence that no longer exists.

    A component with no shard, or one published before stamps existed, is stale — there
    is nothing to be current AGAINST, and exit 0 here must mean "the committed graph is
    the tree", not "nothing contradicted an absent record".
    """
    si = load_installer()
    ws = workspace if isinstance(workspace, dict) else resolve_workspace(workspace, target)
    try:
        discovered = si.discover_components(ws)
    except si.WorkspaceError as exc:
        raise BuildError(str(exc)) from exc
    root: Path = ws["root"]
    components = discovered["components"]

    seen: dict[str, str] = {}
    regs, links = discover(None, "stack", seen)
    generic, generic_links = discover(LINK_ROOT, "link", seen)
    regs, links = regs + generic, links + generic_links
    profile_dir = resolve_profile(ws)
    if profile_dir is not None:
        profile_regs, profile_links = discover_package(
            profile_dir, "profile", seen, root_label="profiles")
        regs = regs + profile_regs
    regs = order_extractors(regs)

    act = activate(regs, components, root)
    config = {"workspace_name": ws.get("name", ""), "canonical": ws.get("canonical", []),
              "generated": ws.get("generated", []),
              "entrypoint": ws.get("entrypoint", []),
              "alias": ws.get("alias", []),
              "components": components, "profile": str(profile_dir or "")}
    digests = input_digests(act, components, root)
    version, cfg_sha = extractor_version(regs), config_digest(config)

    fresh: list[str] = []
    stale: dict[str, str] = {}
    for comp in components:
        name = comp["name"]
        st = snapshot_lib.stamps_of(root, comp)
        cur = digests.get(name, {})
        if st is None:
            stale[name] = ("no freshness stamps — no committed shard, or one published "
                           "before stamps existed")
            continue
        if st["extractor_version"] != version:
            stale[name] = "the extractor set changed since its shard was published"
            continue
        if st["config_sha"] != cfg_sha:
            stale[name] = "the workspace configuration changed since its shard was published"
            continue
        moved = sorted(e for e in set(cur) | set(st["inputs"])
                       if not stamp_matches(cur.get(e, ""), st["inputs"].get(e, "")))
        if moved:
            stale[name] = f"files read by {', '.join(moved)} changed"
            continue
        if snapshot_lib._digest(snapshot_lib.shard_dir(root, comp)) != st["sha256"]:
            stale[name] = "its shard bytes no longer match their recorded digest"
            continue
        fresh.append(name)

    for name, why in sorted(stale.items()):
        print(f"freshness: STALE {name} - {why}")
    if not stale:
        print(f"freshness: {len(fresh)} component(s) current - the committed snapshot "
              f"is the tree")

    return {
        "status": "fresh" if not stale else "stale",
        "workspace": {"name": _workspace_name(ws)},
        "freshness": {"fresh": sorted(fresh), "stale": stale},
    }


def _path_of(components: list[dict], name: str) -> str:
    return next(c["path"] for c in components if c["name"] == name)


def _report(ws: dict, components: list[dict], regs: list[Registration],
            links: list[LinkRegistration], act: Activation,
            results: dict[str, ExtractResult], link_results: dict[str, ExtractResult],
            failures: dict[str, str], merged: ExtractResult, run_id: str,
            profile_dir: Path | None, violations: list[str] | None = None,
            reused: int = 0) -> dict:
    """Assemble the report, and decide which of the empties this build is.

    `reused` is how many components an incremental build pooled from committed shards:
    a build that ran nothing because everything was reusable has empty `results` and a
    whole graph, which is the opposite of `all-failed`.
    """
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
    elif not results and not reused:
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
        warnings.append(f"{name!r} failed and emitted nothing: {why}")

    # A contract violation is a defect in THIS TOOL, not a finding about the workspace, and
    # the two must not be read as the same kind of thing. It therefore rides beside `status`
    # rather than becoming one: the graph can be perfectly `ok` while a row it emitted fails
    # to name what its kind promised. The exit code is what stops it being cosmetic.
    violations = list(violations or [])
    if violations:
        warnings.append(
            f"{len(violations)} unresolved row(s) do not carry the fields their kind "
            f"promises. This is a DEFECT IN THE HARNESS rather than a finding about the "
            f"workspace: every one of those rows exists to name something that is missing, "
            f"and cannot. The graph above is unaffected; the validation report is not.")

    # A requirement that did not run is survivable — the consumer records what it could not
    # look up — but it changes what the graph MEANS, so it cannot be left to the reader to
    # notice that half the edges are missing because of a flag.
    present = {r.name for r in regs} | {lp.name for lp in links}
    for reg in [*regs, *links]:
        absent = [d for d in reg.requires if d not in present]
        if absent:
            warnings.append(
                f"{reg.name!r} needs {', '.join(absent)}, which did not run. It still ran, "
                f"and everything it could not resolve is in `unresolved` — but edges it "
                f"would otherwise have produced are missing.")

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        # `config` is empty when the workspace was inferred rather than read, and
        # `inferred_from` names the repository it was inferred from — how a graph came to
        # describe what it describes is part of the record, not an implementation detail.
        # NAMES, never paths — item 120. `root` is gone rather than shortened,
        # because a basename of it is `name` said twice; the other three keep
        # their keys and lose the machine's directories above them.
        "workspace": {"name": _workspace_name(ws),
                      "config": _name_of(ws.get("config_path")),
                      "inferred_from": _name_of(ws.get("inferred_from")),
                      "profile": _name_of(profile_dir)},
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
        "links": [
            {"name": lp.name, "layer": lp.layer, "module": lp.module,
             "requires": list(lp.requires),
             "nodes": len(link_results[lp.name].nodes) if lp.name in link_results else 0,
             "edges": len(link_results[lp.name].edges) if lp.name in link_results else 0,
             "unresolved": (len(link_results[lp.name].unresolved)
                            if lp.name in link_results else 0),
             "failed": failures.get(lp.name, "")}
            for lp in links
        ],
        "totals": {"nodes": len(merged.nodes), "edges": len(merged.edges),
                   "unresolved": len(merged.unresolved), "components": len(components)},
        # Every unresolved row, collapsed into the systematic findings it belongs to. This is
        # a way of READING the report, not of shortening it: `unresolved.jsonl` still holds
        # every row, and `sites` here holds every one of this group's. What it replaces is a
        # flat list of four thousand individually-correct rows that nobody adjudicates —
        # and a report nobody reads protects nothing, which is the same failure as a
        # permanently-red gate arrived at from the opposite direction.
        "unresolved_patterns": [
            {"extractor": g.extractor, "kind": g.kind, "pattern": g.pattern,
             "graded": g.graded, "count": g.count, "components": list(g.components),
             "sites": [{"component": r.get("component", ""),
                        "source_file": r.get("source_file", ""), "line": r.get("line", 0)}
                       for r in g.rows]}
            for g in group_unresolved(merged.unresolved)
        ],
        "warnings": warnings,
        "contract_violations": violations,
    }


def load_installer():
    """Import `sdlc-init.py` for its workspace reader and component discovery.

    The hyphen makes it un-importable by name, hence the spec dance. The direction matters
    and only works one way: this Tier-2 pipeline may depend on the stdlib-only installer,
    never the reverse.
    """
    root = Path(__file__).resolve().parents[2]
    return _load_module(root / "sdlc-init.py", "sdlc_init")


def check_output_dir(out: Path, repo_root: Path | None = None, *,
                     own_repo: Path | None = None) -> str | None:
    """This pipeline's binding of the shared output-location rule. See `outdir`."""
    return _check_output_dir(
        out, repo_root, own_repo=own_repo, what="graph",
        detail=(f"A built graph carries component names, file paths and line numbers. One "
                f"about a single repository belongs in that repository's {OWN_ARTIFACT_DIR}/"
                f"graph; one spanning a workspace belongs in {SCRATCH_DIR}/, because there is "
                f"no single repository whose facts it is."),
    )


# ── cli ───────────────────────────────────────────────────────────────────────


def _print_report(report: dict, live: dict | None = None) -> None:
    """The build summary, for a terminal.

    **The absolute path comes from the LIVE workspace, never from the report.**
    Item 120 took machine paths out of `build_report.json`, which is committed -
    but a console line is nobody else's repository, so the operator still sees
    where the build ran. Reading it back out of the document was how the path got
    into the document in the first place.
    """
    ws = report["workspace"]
    where = str((live or {}).get("root") or "")
    if ws.get("inferred_from"):
        print(f"repository: {where or ws['inferred_from']}   "
              f"(inferred — no workspace config, so `name` is blank)")
    else:
        print(f"workspace: {where or ws.get('name', '')}")
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
    if report.get("links"):
        print()
        print(f"{'link pass':<22} {'layer':<10} {'nodes':>6} {'edges':>6} {'unres':>6}")
        for lp in report["links"]:
            print(f"{lp['name']:<22} {lp['layer']:<10} {lp['nodes']:>6} {lp['edges']:>6} "
                  f"{lp['unresolved']:>6}")
    t = report["totals"]
    print()
    print(f"totals: {t['nodes']} nodes, {t['edges']} edges, {t['unresolved']} unresolved")

    groups = report["unresolved_patterns"]
    if groups:
        shown = groups[:TOP_PATTERNS]
        print(f"\nunresolved by pattern  "
              f"({len(groups)} pattern(s) over {t['unresolved']} row(s), largest first)")
        for g in shown:
            # An ungraded group is a whole KIND in one bucket, not a pattern anyone chose.
            # Unmarked it reads as the same sort of finding as a graded one and claims a
            # thoroughness nobody has applied to it — the failure this report exists to
            # remove, and it must not come back in the summary of it. Marked with a symbol
            # and a legend rather than a suffix, because a suffix is what gets truncated.
            label = g["pattern"] if g["graded"] else f"{g['kind']} *"
            n = len(g["components"])
            # NOT "0 components". A `contested-service` row names no owning component ON
            # PURPOSE — the finding is a collision BETWEEN components, and filing it against
            # one would imply the very decision the link pass refused to make. Rendering
            # that as a zero would read as a defect in the count.
            where = (g["components"][0] if n == 1 else
                     f"{n} components" if n else "spans components")
            print(f"  {g['count']:6}  {g['extractor']:18.18}  {label:30.30}  {where}")
        if len(groups) > len(shown):
            # Said out loud: a truncated list that does not admit it reads exactly like a
            # complete one, and the difference is this report's entire subject.
            rest = sum(g["count"] for g in groups[len(shown):])
            print(f"  {rest:6}  ... in {len(groups) - len(shown)} further pattern(s); all of "
                  f"them are in build_report.json")
        if any(not g["graded"] for g in shown):
            print("     * grouped by kind alone — its extractor declares no pattern "
                  "vocabulary, so this is every row of that kind rather than one shape")

    for w in report["warnings"]:
        print(f"\n!! {w}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the domain graph for a workspace.")
    ap.add_argument("--workspace", metavar="TOML",
                    help="your filled-in sdlc-workspace.toml. OMIT IT to build the repository "
                         "you are standing in — from inside a repo, `root` is reachable, so it "
                         "is not asked for. A multi-repo workspace always needs one: no scanner "
                         "can infer the directory holding your clones from inside one of them")
    ap.add_argument("--target", metavar="DIR",
                    help="where to start looking for the enclosing repository (default: the "
                         "current directory). Cannot be combined with --workspace")
    ap.add_argument("--out", metavar="DIR",
                    help=f"output directory. Defaults to <repo>/{OWN_ARTIFACT_DIR}/graph when "
                         f"building one repository — its own facts, in the directory it commits "
                         f"them from — and to {SCRATCH_DIR}/graph for a workspace, which is "
                         f"about no single repository. Other committed trees are refused")
    ap.add_argument("--only", metavar="NAMES",
                    help="comma-separated extractor names to run (default: all registered)")
    ap.add_argument("--components", metavar="NAMES",
                    help="comma-separated component names to build (default: all discovered)")
    ap.add_argument("--run-id", default="", metavar="ID",
                    help="stamp this build with a known id instead of a fresh one")
    ap.add_argument("--empty-ok", action="store_true",
                    help="exit 0 when the build ran clean and no extractor activated. For a "
                         "caller probing coverage (the chained install): an unclaimed stack "
                         "is a fact about the extractor set there, not a broken build. A "
                         "deliberate build keeps treating no-activation as the "
                         "misconfiguration it usually is, and no-extractors / all-failed / "
                         "contract violations stay non-zero for everyone")
    ap.add_argument("--publish-snapshot", action="store_true",
                    help="after the build, write each component's extracted FACTS into that "
                         "component's own .sdlc/graph/ (shard.jsonl + digest). Facts only: "
                         "what link passes derive is recomputed at load, never committed. "
                         "Refused when the build had failures or contract violations")
    ap.add_argument("--from-snapshot", action="store_true",
                    help="build the graph from the committed shards instead of extracting: "
                         "load every component's .sdlc/graph/, verify digests, recompute "
                         "link passes. A component with no shard is reported loudly and the "
                         "graph answers only for what is present; a shard failing its own "
                         "digest is refused (exit 2). A load that finds no shard at all is "
                         "empty, not a graph, and exits 2 too")
    ap.add_argument("--check-snapshot", action="store_true",
                    help="rebuild == snapshot, per shard: extract fresh and byte-compare "
                         "against each component's committed shard. Drift names the "
                         "component whose files stopped extracting to their committed bytes "
                         "and exits 2 - per-component on purpose, so a drifting extractor "
                         "localises to the tree it read")
    ap.add_argument("--incremental", action="store_true",
                    help="reuse committed shards for components whose inputs are unchanged "
                         "and extract only the rest. Content decides: per-extractor input "
                         "digests recorded at publish, plus the extractor set and workspace "
                         "config. A component whose declared dependencies (REQUIRES) "
                         "changed anywhere is rebuilt too, and the extractors those "
                         "dependencies name re-run everywhere so the shared cache holds "
                         "what a full build's would. The output is byte-identical to a "
                         "full build of the same tree. With nothing to reuse it IS a full "
                         "build, and says so")
    ap.add_argument("--check-fresh", action="store_true",
                    help="is the committed snapshot current with the tree? Hashes every "
                         "file the extractors would read (seconds) and compares against "
                         "the stamps recorded at publish - no extraction. Exit 0 means "
                         "the committed graph IS the tree; stale components are named "
                         "with why, exit 2. Content answers, not commits: a moved HEAD "
                         "with unmoved files is fresh, a dirty tree is stale")
    ap.add_argument("--propose-canonical", action="store_true",
                    help="after the build, draft canonical.proposed.toml in the output "
                         "directory: one [[canonical]] block per contested service, "
                         "grouped by which components are contesting, every decision "
                         "slot left blank. The tool writes the paperwork; the developer "
                         "writes the answer and moves the blocks into their workspace "
                         "config. Nothing to propose = no file, and the console says so")
    args = ap.parse_args(argv)

    picked = [f for f, on in (("--publish-snapshot", args.publish_snapshot),
                              ("--from-snapshot", args.from_snapshot),
                              ("--check-snapshot", args.check_snapshot),
                              ("--check-fresh", args.check_fresh)) if on]
    if len(picked) > 1:
        print(f"!! {' and '.join(picked)} cannot be combined: publishing, loading, "
              f"checking bytes and checking freshness are different statements about the "
              f"same shards, and combining them hides which one failed.")
        return 2
    if args.from_snapshot and (args.only or args.components):
        print("!! --from-snapshot runs no extractors, so --only and --components have "
              "nothing to act on. Narrow a LOAD by cloning fewer components - a partial "
              "set is supported and reported.")
        return 2
    if args.incremental and (args.from_snapshot or args.check_snapshot or args.check_fresh):
        print("!! --incremental is a way of RUNNING the build; --from-snapshot, "
              "--check-snapshot and --check-fresh are statements about not running it. "
              "Pick one.")
        return 2
    if args.incremental and (args.only or args.components):
        print("!! --incremental decides what to run from the stamps and the declared "
              "dependencies; --only and --components would make that decision about a "
              "build that is not the one the stamps describe.")
        return 2
    # `--check-snapshot` belongs in this PRE-FLIGHT clause and not beside the proposal it
    # refuses: it runs the whole extraction, so a refusal placed there arrives after the
    # report and after "asked only: <out> is untouched" — the reader is told the run
    # succeeded and then told it did not. Every mode named here is refused before anything
    # runs, which is the only answer that costs the reader nothing.
    if args.propose_canonical and (args.check_fresh or args.from_snapshot
                                   or args.check_snapshot):
        print("!! --propose-canonical drafts from the build this command runs, reading the "
              "unresolved rows that build writes; --check-fresh runs none, --from-snapshot "
              "replays one and --check-snapshot only asks a question - it writes nothing. "
              "Run a build (full or --incremental) with the flag instead.")
        return 2
    if args.check_fresh and (args.only or args.components):
        print("!! --check-fresh compares the whole committed snapshot against the whole "
              "tree; a narrowed answer would call the graph fresh while unchecked "
              "components go stale.")
        return 2

    # Refused rather than ignored: --target answers "which repository am I in", which a
    # config file has already answered. Silently dropping it would mean the thing the
    # developer asked for did not happen, and nothing would say so.
    if args.target and args.workspace:
        print("!! --target and --workspace cannot be combined: --workspace already says which "
              "components to build, and --target only says where to look for one.")
        return 2

    # The workspace is resolved BEFORE the output check, because whether a path may be
    # written depends on whether this graph is about one repository or several.
    try:
        ws = resolve_workspace(Path(args.workspace) if args.workspace else None,
                               Path(args.target) if args.target else None)
    except BuildError as exc:
        print(f"!! {exc}")
        return 2

    own_repo = ws.get("inferred_from")
    out = Path(args.out) if args.out else (
        Path(own_repo) / OWN_ARTIFACT_DIR / "graph" if own_repo else Path(SCRATCH_DIR) / "graph")
    if refusal := check_output_dir(out, own_repo=own_repo):
        print(f"!! {refusal}")
        return 2

    def _split(v: str | None) -> list[str] | None:
        return [s.strip() for s in v.split(",") if s.strip()] if v else None

    if args.from_snapshot:
        try:
            report = build_from_snapshot(ws, out)
        except BuildError as exc:
            print(f"!! {exc}")
            return 2
        t = report["totals"]
        s = report["snapshot"]
        print(f"snapshot: loaded {len(s['loaded'])} of {len(s['loaded']) + len(s['missing'])} "
              f"component shard(s); totals: {t['nodes']} nodes, {t['edges']} edges, "
              f"{t['unresolved']} unresolved")
        print(f"\nwrote {out}/graph.jsonl, {out}/unresolved.jsonl, {out}/build_report.json")
        return 2 if report["status"] != "ok" else 0

    if args.check_fresh:
        try:
            report = check_freshness(ws)
        except BuildError as exc:
            print(f"!! {exc}")
            return 2
        return 0 if report["status"] == "fresh" else 2

    try:
        report = build(ws, out, run_id=args.run_id,
                       only=_split(args.only), components_filter=_split(args.components),
                       publish_snapshot=args.publish_snapshot,
                       check_snapshot=args.check_snapshot,
                       incremental=args.incremental)
    except BuildError as exc:
        print(f"!! {exc}")
        return 2

    _print_report(report, ws)
    if args.check_snapshot:
        # It wrote nothing, so it says nothing about writing. A line naming three files
        # this run did not produce sends the reader to whichever run last did.
        print(f"\nasked only: {out} is untouched, and the verdict is above and in the "
              f"exit code")
    else:
        print(f"\nwrote {out}/graph.jsonl, {out}/unresolved.jsonl, {out}/build_report.json")
    if args.propose_canonical:
        from tools.graph.links import canonical as _canonical
        rows = [json.loads(line) for line in
                (out / "unresolved.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
        contested = [r for r in rows if r.get("kind") == "contested-service"]
        if not contested:
            print("propose-canonical: nothing contested is undeclared — no proposal "
                  "to write")
        else:
            (out / "canonical.proposed.toml").write_text(
                _canonical.propose(contested), encoding="utf-8")
            sets = {tuple(r.get("components") or ("?",)) for r in contested}
            print(f"propose-canonical: {len(contested)} contested service(s) in "
                  f"{len(sets)} claimant group(s) -> {out}/canonical.proposed.toml "
                  f"(every decision slot blank; fill, move into your workspace config, "
                  f"rebuild)")
    # A build that read nothing must not exit 0. Every other tool in this repo treats a
    # confidently empty result as the failure it is, and the pipeline that produces the
    # graph is the last place to make an exception.
    if report.get("contract_violations"):
        # Non-zero even at status `ok`: the graph is fine and the harness is not, and a CI
        # check that only ever reads `status` would never learn the difference. Checked
        # BEFORE `--empty-ok` on purpose - the flag excuses an empty graph, never a defect.
        for problem in report["contract_violations"]:
            print(f"  contract: {problem}")
        return 2
    failing = {"no-extractors", "no-activation", "all-failed"}
    if args.empty_ok:
        # The verdict is decided HERE, from the report in memory, because this is the one
        # place that cannot be fooled by a stale file: a caller that re-reads
        # build_report.json after a non-zero exit cannot tell this run's report from the
        # last one's without an id dance. An exit code cannot be stale.
        failing.discard("no-activation")
    if report["status"] in failing:
        return 2
    snapshot = report.get("snapshot", {})
    if args.publish_snapshot and "published" not in snapshot:
        return 2       # the command promised an artifact and the refusal said why, above
    if args.check_snapshot and (snapshot.get("drifted") or "refused" in snapshot):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
