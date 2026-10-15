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
is about no single repository, so it defaults to the gitignored scratch dir unless a hub
repository is named with `--home` to commit it.

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
import importlib.machinery
import importlib.util
import inspect
import json
import os
import posixpath
import sys
import uuid
from collections.abc import Callable, Collection
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402
from tools.graph.core.lib import (  # noqa: E402
    emit, ids, manifest as manifest_lib, props, repo_walk, srcfacts)
from tools.graph import snapshot as snapshot_lib  # noqa: E402
# One judgment of a shard (batch 16): the stamp's one producer and the verdict. It owns the
# stamp helpers; `stamp_matches` is re-exported here for the callers that name it through
# this module, and the rest are this module's own words.
from tools.graph import freshness  # noqa: E402
from tools.graph.freshness import (  # noqa: E402,F401
    ACTIVATION, ACTIVATION_DEPLOY, ACTIVATION_UNLISTED, STALE_CONFIG, stamp_matches)
# The grammars a source file's suffix maps to - the readers' own table - for the parsers a
# reader declares (batch 16, ruling 30).
from tools.graph.extractors import _lang  # noqa: E402
# The one normalising reading of the component list, which compose, the canonical pass and
# the join already share (D534-42); `activate` reads nesting through it (T591).
from tools.graph.extractors._canonical import workspace_layout  # noqa: E402
#: The one declared-child rule (T616), read through the module so the canonical pass and
#: this build can never hold two copies of it.
from tools.graph.extractors import _canonical as canonical_rules  # noqa: E402
from tools.graph.core.model import (  # noqa: E402
    ExtractResult, Node, check_patterns, check_unresolved, group_unresolved)
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.outdir import (  # noqa: E402
    OWN_ARTIFACT_DIR, SCRATCH_DIR, check_output_dir as _check_output_dir, enclosing_repo)

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
    #: Its tool resolves modules across directories (`READS_NESTED`), so a parent's reading
    #: keeps its declared children's files, inside the parent's stamp (T591).
    reads_nested: bool = False
    #: Why one of its claim files would claim nothing even read (`claims_nothing(root, rel,
    #: walked)`, T625): a deployment reader's answer the case record asks of a file lost to its
    #: case, so the record names no reader. `walked` is the component-relative paths of the
    #: files the build's walk found in the component that match some registered reader's
    #: glob - outside its declared children, or with theirs for a reader that reads nested
    #: (T591), and outside the build's own `.sdlc/` directory and the installer's own files
    #: (`installer_root_exclusions`); never an ancestor file a `READS_ANCESTORS` reader is
    #: handed (T614). They bound its answer (review B21-20, B21-28, B21-37, B21-41, B21-45).
    claims_nothing: Callable[[Path, str, frozenset[str]], str] | None = None
    #: Its tool resolves a module's identity from the build above it (`READS_ANCESTORS`), so a
    #: component declared inside another is also handed the files its globs match directly
    #: in each directory above it, inside its own stamp (T614) - where it is handed files of
    #: its own in that component; a component it reads nothing in is handed none from above.
    reads_ancestors: bool = False
    #: Its facts are the ones the accounting rows judge by - deployment nodes, their claims,
    #: host maps (`DEPLOYMENT_READER`, batch 15b): its failure narrows a run, and the
    #: activation walk records every file its own globs miss only by case.
    deployment_reader: bool = False
    #: Which of its globs it reads for service CLAIMS (`CLAIM_GLOBS`): a file one of them
    #: matches is a deployment file, whose loss is an `unread` node the rows name.
    claim_globs: tuple[str, ...] = ()
    #: What each of its other globs serves (`GLOB_PURPOSES`), so a file it misses by case is
    #: named with what is missing: `host-maps`, `chart-templates` or `package-dirs`.
    glob_purposes: dict[str, str] = field(default_factory=dict)
    #: The parsers it can lack (`PARSERS`, batch 16, ruling 30), so a shard records the parser
    #: state it was built under; `()` declares that it uses none. A reader whose gate is finer
    #: than a file's suffix answers which parser a file needs itself (`parser_of`), and one
    #: whose parser is neither YAML nor a `_lang` grammar says whether it is installed
    #: (`parser_available`).
    parsers: tuple[str, ...] = ()
    parser_of: Any = None
    parser_available: Any = None


#: The purposes a deployment reader's non-claim glob may serve (`GLOB_PURPOSES`).
GLOB_PURPOSES = ("chart-templates", "host-maps", "package-dirs")

#: The parser a file needs by its case-folded suffix, for a reader that declares that parser
#: and answers no `parser_of` of its own (batch 16): the grammars `_lang` maps, and YAML.
SUFFIX_PARSERS: dict[str, str] = {**_lang.GRAMMARS, ".yaml": "yaml", ".yml": "yaml"}


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


def _deployment_roles(obj: Any, name: str, where: str, globs: tuple[str, ...]
                      ) -> tuple[bool, tuple[str, ...], dict[str, str]]:
    """`DEPLOYMENT_READER`, `CLAIM_GLOBS` and `GLOB_PURPOSES` (batch 15b), validated as
    `READS_NESTED` is: each changes what a run judges, so a malformed one is refused rather
    than read loosely. A deployment reader says what EVERY one of its globs is for - a claim
    or a named purpose - so a file it misses by case is never named with an empty reason
    (D15b-135), and `build.py` names no reader to learn it."""
    deployment = getattr(obj, "DEPLOYMENT_READER", False)
    if not isinstance(deployment, bool):
        raise BuildError(f"{where}: `DEPLOYMENT_READER` on {name!r} is {deployment!r}; it must "
                         f"be True or False. True makes this extractor's failure narrow a run "
                         f"and its case-lost files nodes, so a wrong value changes both without "
                         f"a word")
    claims = _str_tuple(obj, "CLAIM_GLOBS", name, where, required=False)
    purposes = getattr(obj, "GLOB_PURPOSES", None)
    if not deployment:
        for attr, value in (("CLAIM_GLOBS", claims), ("GLOB_PURPOSES", purposes)):
            if value:
                raise BuildError(f"{where}: `{attr}` on {name!r} says what a deployment "
                                 f"reader's globs are for, so it is declared only on a "
                                 f"deployment reader (`DEPLOYMENT_READER = True`)")
        return False, (), {}
    purposes = {} if purposes is None else purposes
    if not isinstance(purposes, dict):
        raise BuildError(f"{where}: `GLOB_PURPOSES` on {name!r} must be a dict of glob to "
                         f"purpose, not {type(purposes).__name__}")
    for glob in (*claims, *purposes):
        if glob not in globs:
            raise BuildError(f"{where}: {name!r} declares a role for {glob!r}, which is not "
                             f"among its INPUT_GLOBS")
    for glob, purpose in purposes.items():
        if purpose not in GLOB_PURPOSES:
            raise BuildError(f"{where}: `GLOB_PURPOSES[{glob!r}]` on {name!r} is {purpose!r}; "
                             f"it must be one of {', '.join(GLOB_PURPOSES)}")
        if glob in claims:
            raise BuildError(f"{where}: {glob!r} on {name!r} is both a claim glob and a "
                             f"purpose; a glob read for claims is named in CLAIM_GLOBS alone")
    for glob in globs:
        if glob not in claims and glob not in purposes:
            raise BuildError(f"{where}: deployment reader {name!r} says nothing of {glob!r}: "
                             f"name it in CLAIM_GLOBS, or give it a GLOB_PURPOSES entry, so a "
                             f"file it misses by case is named with what is missing")
    return True, claims, dict(purposes)


def _parsers(obj: Any, name: str, where: str, mod: Any,
             unresolved_fields: dict[str, tuple[str, ...]]) -> tuple[tuple[str, ...], Any, Any]:
    """`PARSERS`, and the reader's own `parser_of` and `parser_available` (batch 16, ruling
    30): which parsers a shard's stamp records for this reader. Read with a SENTINEL - on the
    reader, then its module - because absent is not `()`: `_str_tuple` and `_declared_map`
    both fold the two, and a reader that files `grammar-unavailable` and forgot the
    declaration would then pass as one that uses no parser, its parser state never stamped
    (design round 3, D16-28). Every name needs a probe and a file rule: YAML and the `_lang`
    grammars have both by default; any other parser is the reader's to answer."""
    parser_of = getattr(obj, "parser_of", None)
    available = getattr(obj, "parser_available", None)
    declared = getattr(obj, "PARSERS", None)
    if declared is None and mod is not None:
        declared = getattr(mod, "PARSERS", None)
    if declared is None:
        if "grammar-unavailable" in unresolved_fields:
            raise BuildError(f"{where}: {name!r} declares `grammar-unavailable` but no `PARSERS`: "
                             f"declare the parsers it can lack (`PARSERS = ('yaml',)`, say), or "
                             f"`PARSERS = ()` if it uses none, so its shard records the parser "
                             f"state it was built under")
        return (), parser_of, available
    if not isinstance(declared, tuple) or not all(
            isinstance(parser, str) and parser.strip() for parser in declared):
        raise BuildError(f"{where}: `PARSERS` on {name!r} must be a TUPLE of parser names, got "
                         f"{declared!r}")
    defaults = {"yaml", *_lang.GRAMMARS.values()}
    for parser in declared:
        if parser in defaults:
            continue
        if available is None:
            raise BuildError(f"{where}: {name!r} declares the parser {parser!r}, which has no "
                             f"probe: only YAML and the `_lang` grammars are probed by default, "
                             f"so give the reader `parser_available(name)`")
        if parser_of is None:
            raise BuildError(f"{where}: {name!r} declares the parser {parser!r}, which no file "
                             f"suffix maps to, and has no file rule: give the reader "
                             f"`parser_of(path, rel, text=None)`")
    return declared, parser_of, available


def _validate(obj: Any, where: str
              ) -> tuple[str, tuple[str, ...], tuple[str, ...], bool, bool]:
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
    # A real `bool` or nothing: `READS_NESTED = "false"` is truthy, and a mistyped
    # declaration would widen this extractor's inputs and stamps in silence (D15a-33).
    reads_nested = getattr(obj, "READS_NESTED", False)
    if not isinstance(reads_nested, bool):
        raise BuildError(f"{where}: `READS_NESTED` on {name!r} is {reads_nested!r}; it must be "
                         f"True or False. True keeps a declared child's files in a parent's "
                         f"reading, so a wrong value changes what is read without a word")
    reads_ancestors = getattr(obj, "READS_ANCESTORS", False)
    if not isinstance(reads_ancestors, bool):
        raise BuildError(f"{where}: `READS_ANCESTORS` on {name!r} is {reads_ancestors!r}; it "
                         f"must be True or False. True hands a nested component the files "
                         f"above it wherever this reader has files of its own there, so a "
                         f"wrong value changes what is read without a word")

    if not callable(getattr(obj, "extract", None)):
        raise BuildError(f"{where}: {name!r} has no callable `extract(ctx)`")

    return name, globs, requires, reads_nested, reads_ancestors


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
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        # The standard `import` statement removes a module from sys.modules when its exec
        # raises; this low-level spec_from_file_location path does not get that for free.
        # Left in place, a half-executed module sits under this synthetic name, and
        # anything that imports it again by name in this same process reads that corpse
        # instead of retrying — reproduced: a first call with a body that sets one global
        # and then raises leaves that global readable afterward under the failed name.
        sys.modules.pop(modname, None)
        raise
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


#: Well-known Python files that sit beside real code without ever being an extractor
#: attempt. DECISION, made deliberately rather than defaulted: `discover()` refuses every
#: OTHER lone .py file by name, because a silently-ignored single-file extractor is the
#: defect this module exists to close. But `extractor_root` is an API parameter with no CLI
#: flag reaching it, so the only caller is code that can point it at any directory it
#: likes — including a fixture-shaped tree that carries pytest's own conftest.py for
#: reasons that have nothing to do with this registry. Refusing THAT is a false positive on
#: a file nobody wrote as an extractor, which is exactly the "loud failure nobody asked
#: for" this project has been burned by before. Kept to files with a concrete, named
#: collision — not a speculative "python files in general" allowlist, which would be the
#: same kind of unmeasured guess this validation exists to catch. Names here are canonical
#: NORMCASE form (see the comparison below) — already all-lowercase ASCII, so the frozenset
#: needs no transformation itself.
_NOT_AN_EXTRACTOR_ATTEMPT = frozenset({"conftest.py"})

#: Suffixes Python's own import machinery recognises as source, on EVERY platform — not
#: just the common one. A lone `.pyw` module is exactly as importable as a `.py` one
#: (`importlib.machinery.SOURCE_SUFFIXES` lists both unconditionally; `.pyw` is not a
#: Windows-only convention to the import system itself, only to the `pythonw` launcher), so
#: naming only `.py` here silently dropped a `.pyw` extractor attempt — the same defect this
#: whole function exists to close, reached through the other recognised extension.
_PY_SUFFIXES = frozenset(importlib.machinery.SOURCE_SUFFIXES)


def discover(root: Path | None = None, layer: str = "stack",
             seen: dict[str, str] | None = None) -> tuple[list[Registration],
                                                          list[LinkRegistration]]:
    """Import every subpackage of `root` and collect its `EXTRACTORS` and `LINK_PASSES`.

    Scanning rather than maintaining a list means adding one is adding a directory, with no
    second place to forget. The cost of scanning is that a broken module could be skipped
    silently, so it is not: an import error, a package exporting neither tuple, an empty
    one, a duplicate name, and a lone module sitting where a package belongs all raise — the
    last of these because a single-file extractor is the more natural first attempt at
    adding one, and it must fail exactly as loudly as a bare directory with no __init__.py
    already does two lines below. "Lone module" means any suffix Python's own import
    machinery recognises as source (`.py` and `.pyw`, both platforms, not just the common
    one) — anything else is litter, skipped rather than refused. The one exception among
    those is a FILE (never a directory) whose name, after `os.path.normcase`, is in
    `_NOT_AN_EXTRACTOR_ATTEMPT` (currently just `conftest.py`): a well-known Python file
    that sits beside real code without ever being an extractor attempt is skipped like a
    dotted or underscored name, not refused. `normcase` folds case on Windows and is the
    identity on POSIX, so a differently-cased spelling is the same file only where the
    filesystem itself agrees it is. The directory layout is the layer boundary, which is why
    the layer is passed in rather than guessed from the module name.

    A root that does not exist returns empty: nothing was ever there to scan, which is the
    same "empty" an existing-but-vacant directory would report, and
    `test_a_missing_extractor_root_registers_nothing` pins exactly that. A root that EXISTS
    but cannot be scanned as a directory — a file sits there, or listing it fails — is a
    different fact: the caller cannot tell whether extractors are behind it, so that is
    never folded into the same silent empty result. It raises, naming why.

    `seen` is shared across roots so a profile cannot register a name a stack extractor
    already uses. Names are recorded on edges, so two would be indistinguishable in
    provenance — and the layer that lost the race would simply have its shard overwritten.
    """
    root = EXTRACTOR_ROOT if root is None else root
    try:
        entries = sorted(root.iterdir())
    except FileNotFoundError:
        return [], []
    except OSError as exc:
        raise BuildError(
            f"{root} exists but could not be scanned for extractor packages "
            f"({type(exc).__name__}: {exc}). Reading this the same as an empty directory "
            f"would silently drop every extractor under it — absent is not the same as "
            f"could-not-read."
        ) from exc

    regs: list[Registration] = []
    links: list[LinkRegistration] = []
    seen = {} if seen is None else seen
    for pkg in entries:
        if pkg.name.startswith((".", "_")):
            continue
        if not pkg.is_dir():
            # Both comparisons go through `os.path.normcase`, never plain `.lower()`: it
            # lower-cases on Windows and is the IDENTITY on POSIX, which is exactly right on
            # both, not just on the platform this was first measured on. `.lower()` folded
            # case everywhere, which is wrong on POSIX in two opposite directions at once —
            # both reproduced and both closed by this swap: an upper-cased SUFFIX (BETA.PY)
            # is not a file Python's import machinery would ever load on a case-sensitive
            # filesystem, so refusing it there is a LOUD FALSE POSITIVE on CI, the gate of
            # record; a capitalised spelling of the exempt NAME (Conftest.py) is a genuinely
            # different, importable file on POSIX — not the special one — so exempting it
            # there is a SILENT DROP of a real module, this row's own defect class,
            # reintroduced by the exemption meant to prevent it. `_NOT_AN_EXTRACTOR_ATTEMPT`
            # is checked ONLY here, inside the not-a-directory branch — never at the top of
            # this loop — so it can only ever exempt a FILE, never a directory sharing that
            # name.
            #
            # KNOWN GAP, decided rather than paid for: `normcase` is also the POSIX identity
            # on macOS, whose default filesystem is case-insensitive in practice — so this
            # swap does not correctly fold case there, the same failure shape this row
            # closes for Windows. The exactly-right predicate would stat and compare inode
            # identity, at the cost of one extra stat per entry, on every platform, forever,
            # to serve one CLAUDE.md does not name as a target and CI does not run on.
            # CLAUDE.md names Windows explicitly and is silent on macOS; CI (the gate of
            # record) is Linux. Paying a permanent per-entry stat cost everywhere to close a
            # gap on an unstated, un-gated platform is the wrong trade — so the swap is
            # taken as-is and this paragraph is the record of the gap it leaves open.
            if os.path.normcase(pkg.suffix) not in _PY_SUFFIXES or (
                    os.path.normcase(pkg.name) in _NOT_AN_EXTRACTOR_ATTEMPT):
                continue  # litter (an unrecognised suffix) or a named non-extractor file
            raise BuildError(
                f"{pkg} is a lone module directly under {root}, not a package. An "
                f"extractor is a directory whose __init__.py exports `EXTRACTORS` or "
                f"`LINK_PASSES` — discover_package already refuses a directory that is "
                f"missing that file, for the same reason: a single-file extractor is the "
                f"more natural first attempt, and leaving it silently unimported is "
                f"exactly how an edge class goes missing. Move it into its own directory "
                f"with an __init__.py."
            )
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
        if value is None:
            continue
        if not isinstance(value, (tuple, list)):
            # Checked unconditionally, before the rules-only escape hatch below ever looks
            # at the value: that hatch keys on falsiness, and 0 or "" are falsy without
            # being the empty TUPLE it exists to forgive. Keying it on `not value` alone
            # let a wrong-typed `LINK_PASSES = 0` beside a traversal_rules.yml register
            # cleanly with this check never running.
            raise BuildError(f"{where}: `{attr}` must be a TUPLE or list of entries, got "
                             f"{type(value).__name__} ({value!r})")
        if not value:
            # Item 21: a profile that only tunes traversal is a live capability, and its
            # rules file is the proof. Without one, empty stays a mistake. This forgives a
            # correctly-typed EMPTY tuple only — the isinstance check above has already run
            # by the time we get here, so a wrong type never reaches this branch.
            if (pkg / "traversal_rules.yml").exists():
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
        name, globs, requires, reads_nested, reads_ancestors = _validate(obj, where)
        deployment, claims, purposes = _deployment_roles(obj, name, where, globs)
        claim(name, "extractor")
        fields_declared = _unresolved_fields(obj, name, where, mod)
        parsers, parser_of, parser_available = _parsers(obj, name, where, mod, fields_declared)
        hook = getattr(obj, "claims_nothing", None)
        if hook is not None and not (callable(hook) and _accepts(hook, 3)):
            raise BuildError(f"{where}: `claims_nothing` on {name!r} is {hook!r}; it must be a "
                             f"callable `(component root, path, walked) -> reason`, or absent")
        regs.append(Registration(name=name, layer=layer, globs=globs, requires=requires,
                                 extractor=obj, module=where, reads_nested=reads_nested,
                                 reads_ancestors=reads_ancestors, claims_nothing=hook,
                                 deployment_reader=deployment, claim_globs=claims,
                                 glob_purposes=purposes, parsers=parsers, parser_of=parser_of,
                                 parser_available=parser_available,
                                 unresolved_fields=fields_declared,
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


def parser_probe(regs: list[Registration]) -> Callable[[str], bool]:
    """Whether each declared parser is installed NOW, asked as the readers themselves ask
    (batch 16, ruling 30): YAML through `props.yaml_available`, a `_lang` grammar through
    `srcfacts.available`, any other parser through the first reader in registry order that
    declares it - registration guarantees that reader answers `parser_available`."""
    grammars = set(_lang.GRAMMARS.values())
    custom: dict[str, Callable[[str], bool]] = {}
    for reg in regs:
        for parser in reg.parsers:
            if parser != "yaml" and parser not in grammars and parser not in custom:
                custom[parser] = reg.parser_available

    def probe(parser: str) -> bool:
        if parser == "yaml":
            return props.yaml_available()
        if parser in grammars:
            return srcfacts.available(parser)
        return bool(custom[parser](parser))
    return probe


def file_parser(reg: Registration, path: Path, rel: str, text: str | None = None) -> str | None:
    """The parser a file `reg` is handed needs, by the reader's own rule (batch 16): its own
    `parser_of` where it has one - the method its `extract` calls for its gate, so the two
    never disagree - else the case-folded suffix table, restricted to the parsers it declares.
    A `parser_of` answer outside `PARSERS` is refused where it is produced, never looked up as
    a probe that may not exist (design round 5, D16-48)."""
    if reg.parser_of is None:
        parser = SUFFIX_PARSERS.get(path.suffix.lower())
        return parser if parser in reg.parsers else None
    parser = reg.parser_of(path, rel, text)
    if parser is not None and parser not in reg.parsers:
        raise BuildError(f"{reg.name}'s parser_of returned {parser!r}, which its PARSERS does "
                         f"not declare")
    return parser


def parser_rule(regs: list[Registration]) -> Callable[[str, Path, str], str | None]:
    """`file_parser` keyed by reader name, as the stamp and the verdict ask it."""
    by_name = {reg.name: reg for reg in regs}

    def rule(reader: str, path: Path, rel: str) -> str | None:
        reg = by_name.get(reader)
        return None if reg is None else file_parser(reg, path, rel)
    return rule


# ── activation ────────────────────────────────────────────────────────────────


@dataclass
class Activation:
    """Which components each extractor claims, and which files it claimed them by."""

    by_extractor: dict[str, list[str]] = field(default_factory=dict)
    by_component: dict[str, list[str]] = field(default_factory=dict)
    files: dict[tuple[str, str], list[Path]] = field(default_factory=dict)
    #: Per component, `(file, globs, extractors)` for each file NO extractor claims that
    #: globs match once case is ignored (T531); `off_case_groups` makes the rows.
    off_case: dict[str, list[tuple[str, tuple[str, ...], list[str]]]] = field(
        default_factory=dict)
    #: Components that read nothing BY DESIGN, with the children holding their files: no
    #: REGISTERED extractor would claim one by a file it reads - its own, or a child's where
    #: the extractor declares reach - and some claimable file lies in a declared child.
    #: Registry-relative, so `--only` cannot flip it (D15a-24, D15a-35).
    by_design: dict[str, list[str]] = field(default_factory=dict)
    #: The by-design components whose children's files no extractor THIS RUN selected would
    #: read - so the remedy, followed with this run's `--only`, would read nothing (D15a-41).
    by_design_unread: set[str] = field(default_factory=set)
    #: Per component, `(file, ((reader, glob), ...))` for each file a DEPLOYMENT READER's own
    #: globs match only once case is ignored - whoever else claims it, which is why T531's
    #: `off_case` (files no extractor claims) missed `Docker-Compose.yml` and `Values.yaml`
    #: that `openapi-specs` reads by `*.yml`/`*.yaml` (batch 15b, T573 decision 6). Each pair
    #: is a glob that misses the file while no glob of the same reader serving the same role
    #: matches it as spelled. Registry-relative, like `off_case`, so `--only` cannot change it.
    deploy_off_case: dict[str, list[tuple[str, tuple[tuple[str, str], ...]]]] = field(
        default_factory=dict)
    #: Per component, per case-lost claim file, why its reader would claim nothing from it even
    #: read (T625) - asked of the reader, so the case record names none.
    nothing_claimed: dict[str, dict[str, str]] = field(default_factory=dict)
    #: Per component, `(directory, cause)` for each directory its walk entered but could not
    #: list (T611): no extractor read a file in it, and `unlisted_row` says so. One under a
    #: declared child is the child's, as an off-case file is.
    unlisted: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    def active(self) -> list[str]:
        return [n for n, comps in self.by_extractor.items() if comps]


def installer_root_exclusions(si) -> frozenset[str]:
    """Every root-relative file the installer itself can write, as `activate()`'s
    `root_exclusions` (T153, rounds 2 and 3).

    ONE place both `activate()` call sites read, rather than each hand-writing its own
    `frozenset({...})` literal — round 1 fixed `.mcp.json` at both sites by duplicating a
    one-item set; round 2 found the SAME defect one directory deeper, at
    `.github/workflows/`; round 3 found it a THIRD time, under `.claude/` (the
    `--claude-extras` settings document and its two hook scripts). Three passes over one
    hand-kept idea is the argument for `test_every_installer_written_file_that_any_
    extractor_would_claim_is_excluded` in test_build.py: that guard calls the REAL
    installer and the REAL extractor registry, so a FOURTH leak fails a test here rather
    than waiting for a fourth reading of this row.

    DERIVATION, round 3: `.claude/agents/sdlc-reviewer.md`, `.claude/agents/sdlc-
    security.md`, and the two `.claude/hooks/*.py` scripts are all named already, as
    `sdlc-init.py`'s own `EXTRA_TEMPLATE_FILES` list (`installer_root_exclusions` reads it
    directly, so it can never drift from what `claude_extras_ops` actually writes there).
    `.claude/settings.json` is NOT derived the same way: naming it would mean adding a
    constant inside `claude_extras_ops`, a function inside `sdlc-init.py` — the file every
    row in this swarm can touch, five commits into it today alone by the time this was
    measured. A guarded literal is safe (the completeness guard still catches a rename or
    a move); a merge conflict in the shared installer for one string is not worth avoiding
    it. Left as a literal on purpose — see the row's log for the full reasoning.
    """
    hook_and_agent_files = {f".claude/{name}" for name in si.EXTRA_TEMPLATE_FILES}
    return frozenset({si.MCP_CONFIG, si.LEDGER_WORKFLOW_REL, si.REPLAY_WORKFLOW_REL,
                      si.SNAPSHOT_WORKFLOW_REL, ".claude/settings.json"}
                     | hook_and_agent_files)


def glob_role(reg: Registration, glob: str) -> str:
    """What one of a deployment reader's globs is read for: `claims`, or its purpose."""
    return "claims" if glob in reg.claim_globs else reg.glob_purposes.get(glob, "")


def deployment_misses(rel: str, base: str, registry: list[Registration]
                      ) -> tuple[tuple[str, str], ...]:
    """`(reader, glob)` for each deployment reader's glob that misses this file only by
    case, where no glob of that reader serving the same role matches it as spelled. Per
    role, both ways: a `templates/Values.yaml` a reader counts as a template is still a
    CLAIM it lost, and a reader declaring two spellings of one claim file (`Chart.yaml`,
    `chart.yaml`) reads either as spelled, so the other's folded match loses nothing
    (review B15b-8)."""
    lost: list[tuple[str, str]] = []
    for reg in registry:
        if not reg.deployment_reader:
            continue
        served = {glob_role(reg, g) for g in reg.globs if repo_walk.glob_match(rel, base, g)}
        lost += [(reg.name, g) for g in reg.globs
                 if glob_role(reg, g) not in served
                 and not repo_walk.glob_match(rel, base, g)
                 and repo_walk.glob_match(rel.lower(), base.lower(), g.lower())]
    return tuple(sorted(lost))

#: What each `kind` the pipeline emits promises beyond `UNRESOLVED_COMMON` - declared in
#: this module like every extractor's, and checked the same way.
UNRESOLVED_FIELDS = {
    "off-case-input": ("globs", "extractors", "files"),
    "deployment-off-case": ("globs", "extractors", "files"),
    "unlisted-directory": ("error",),
}


def unlisted_row(component: str, directory: str, cause: str) -> dict:
    """A directory the activation walk entered but could not list (T611): every file in it is
    unknown to every extractor - never absent, so it is said, with what lifts it. Before, the
    walk skipped it in silence, and fd skipped it at exit 0."""
    where = "the component's own directory" if directory == "." else f"`{directory}`"
    return {
        "extractor": ACTIVATION, "component": component, "kind": "unlisted-directory",
        "source_file": directory, "line": 0, "error": cause,
        "why": (f"{where} could not be listed ({cause}), so no extractor read a file in it - "
                f"what those files declare is unknown, not absent. Allow listing it, then "
                f"rebuild"),
    }


def off_case_groups(entries: list[tuple[str, tuple[str, ...], list[str]]]
                    ) -> list[tuple[list[str], list[str], list[str]]]:
    """A component's off-case files, one group per (globs, extractors): `(files, globs,
    extractors)`, in one order. B9-2: forty migrations with a lowercase prefix are ONE fact
    about a naming convention, and forty rows of it rode into the committed shard."""
    groups: dict[tuple[tuple[str, ...], tuple[str, ...]], list[str]] = {}
    for rel, globs, owners in entries:
        groups.setdefault((tuple(globs), tuple(owners)), []).append(rel)
    return [(sorted(files), list(globs), list(owners))
            for (globs, owners), files in sorted(groups.items())]


def off_case_row(component: str, files: list[str], globs: list[str],
                 extractors: list[str]) -> dict:
    """Files no extractor read because they miss patterns only by case (T531).

    Matching is case-sensitive on every platform, so one commit builds one graph; a Windows
    checkout used to read `v3__add_index.sql` as a migration and a Linux one silently did
    not. Scoped to files claimed by NO extractor: `Audit.java` also misses `*IT.java` by
    case, and reporting it would bury the real losses in files `*.java` reads anyway. Every
    pattern a file misses, and every extractor declaring one, is named - not the first in
    sort order, which sent a reader to the wrong extractor (B9-3). One row per group of
    files missing the same patterns, the files listed (B9-2).
    """
    return {
        "extractor": ACTIVATION, "component": component, "kind": "off-case-input",
        "source_file": files[0], "line": 0, "globs": globs, "extractors": extractors,
        "files": files,
        "why": (f"no extractor read {len(files)} file(s) here: each matches "
                f"{', '.join(f'`{g}`' for g in globs)} - the pattern(s) "
                f"{', '.join(extractors)} claim files by - only once case is ignored. "
                f"Matching is case-sensitive on every platform so the same commit builds "
                f"the same graph everywhere; until they are spelled as the patterns name "
                f"them, what they declare is missing and this row is the only record"),
    }


def _nothing_claimed(rel: str, pairs: tuple[tuple[str, str], ...],
                     registry: list[Registration], root: Path, own: frozenset[str],
                     nested: frozenset[str]) -> str:
    """Why the reader a case-lost file is a claim file of would claim nothing from it even
    read, asked of that reader (`claims_nothing`, T625), or "": a library chart's values
    file, renamed, still deploys nothing. It is handed the files the walk found that it can
    be handed (review B21-28, B21-45): `own`, outside the component's declared children, or
    `nested`, theirs too, for a reader that reads nested (T591) - never the build's own
    `.sdlc/` directory or the installer's own files (`installer_root_exclusions`)."""
    regs = {r.name: r for r in registry}
    for reader, glob in pairs:
        reg = regs[reader]
        if glob in reg.claim_globs and reg.claims_nothing is not None:
            if why := reg.claims_nothing(root, rel, nested if reg.reads_nested else own):
                return why
    return ""


def _accepts(fn: Callable, count: int) -> bool:
    """Whether `fn` can be called with `count` positional arguments - a hook's arity refused
    at registration, never a traceback mid-build (review B21-27). A callable whose signature
    cannot be read is taken at its word."""
    try:
        inspect.signature(fn).bind(*range(count))
    except TypeError:
        return False
    except ValueError:
        return True
    return True


def deployment_case_groups(lost: list[tuple[str, tuple[tuple[str, str], ...]]],
                           named: set[str], nothing: dict[str, str] | None = None
                           ) -> list[tuple[list[str], tuple[tuple[str, str], ...], str]]:
    """A component's case-lost deployment-reader files that T531's row does not already
    name, one group per set of `(reader, glob)` misses and what claims nothing from them
    (T625): `(files, pairs, why)`, in one order."""
    nothing = nothing or {}
    groups: dict[tuple[tuple[tuple[str, str], ...], str], list[str]] = {}
    for rel, pairs in lost:
        if rel not in named:
            groups.setdefault((pairs, nothing.get(rel, "")), []).append(rel)
    return [(sorted(files), pairs, why) for (pairs, why), files in sorted(groups.items())]


def case_purposes(pairs: tuple[tuple[str, str], ...], registry: list[Registration],
                  nothing: str = "") -> list[str]:
    """What a case-lost file leaves missing: one clause per purpose its missed globs
    serve, each naming only the readers serving it (D15b-111, D15b-124, D15b-135). Where
    its reader would claim nothing from it even read (`nothing`, T625), the claims clause
    says so, never that what it declares is missing."""
    regs = {r.name: r for r in registry}
    by_role: dict[str, set[str]] = {}
    for reader, glob in pairs:
        by_role.setdefault(glob_role(regs[reader], glob), set()).add(reader)
    clauses = []
    for role in ("claims", *GLOB_PURPOSES):
        readers = sorted(by_role.get(role, ()))
        if not readers:
            continue
        who, s = ", ".join(f"`{r}`" for r in readers), "s" if len(readers) == 1 else ""
        clauses.append({
            "claims": (f"{who} read{s} claims by it, but would read none from it: {nothing}"
                       if nothing else
                       f"{who} read{s} claims by it, so what it declares is missing"),
            "host-maps": f"{who} read{s} host maps by it, so every address it publishes "
                         f"is missing",
            "chart-templates": f"{who} count{s} it as a template of its chart",
            "package-dirs": f"{who} read{s} it to know which directories are charts, so "
                            f"the charts a claim on the root ranks are missing",
        }[role])
    return clauses


def deployment_off_case_row(component: str, files: list[str],
                            pairs: tuple[tuple[str, str], ...],
                            registry: list[Registration], nothing: str = "") -> dict:
    """Files a deployment reader misses only by case that T531's `off-case-input` does not
    name, because another extractor reads them (batch 15b): a mis-cased
    `Docker-Compose.yml` or `Values.yaml` - `openapi-specs` reads both - was silent."""
    globs = sorted({glob for _, glob in pairs})
    readers = sorted({reader for reader, _ in pairs})
    one = len(files) == 1
    return {
        "extractor": ACTIVATION, "component": component, "kind": "deployment-off-case",
        "source_file": files[0], "line": 0, "globs": globs, "extractors": readers,
        "files": files,
        "why": (f"{'this file matches' if one else f'each of {len(files)} files matches'} "
                f"{', '.join(f'`{g}`' for g in globs)} - the pattern(s) "
                f"{', '.join(f'`{r}`' for r in readers)} read files by - only once case is "
                f"ignored: {'; '.join(case_purposes(pairs, registry, nothing))}. Matching is "
                f"case-sensitive on every platform so the same commit builds the same graph "
                f"everywhere; rename {'it' if one else 'each'} as the patterns spell it"),
    }


def case_lost_nodes(component: str, lost: list[tuple[str, tuple[tuple[str, str], ...]]],
                    named: set[str], registry: list[Registration],
                    nothing: dict[str, str] | None = None) -> list[Node]:
    """An `unread` deployment node for each case-lost file a deployment reader reads for a
    CLAIM (batch 15b, T573 decision 6): a fact about the component's file names, published
    as `repo:` nodes are, marked with the kind of the row that explains it. A case-lost
    template loses addresses, not a deployment, and gets its row alone (D15b-103)."""
    regs = {r.name: r for r in registry}
    nothing = nothing or {}
    return [Node(id=ids.deployment_id(component, rel), type="deploy",
                 props={"path": rel, "component": component,
                        "unread": "off-case-input" if rel in named else "deployment-off-case",
                        # T625: renamed, it would still claim nothing - its reader said why.
                        **({"claims_nothing": nothing[rel]} if rel in nothing else {})})
            for rel, pairs in lost
            if any(glob in regs[reader].claim_globs for reader, glob in pairs)]


def _dirs_above(name: str, layout: dict[str, str]) -> list[str]:
    """The workspace-relative directories above component `name`, nearest first, up to the
    root of the outermost component holding it (T614); none for a component no other holds -
    the directories above a top-level component lie in no component, and are not read."""
    mine = layout.get(name)
    if mine is None or mine == ".":
        return []
    holders = [path for other, path in layout.items()
               if other != name and path != mine and (path == "." or mine.startswith(path + "/"))]
    if not holders:
        return []
    outermost = "." if "." in holders else min(holders, key=lambda path: path.count("/"))
    out, here = [], posixpath.dirname(mine)
    while True:
        out.append(here or ".")
        if (here or ".") == outermost or not here:
            return out
        here = posixpath.dirname(here)


def _files_above(root: Path, dirs: list[str], globs: tuple[str, ...]
                 ) -> tuple[list[Path], list[tuple[str, str]]]:
    """(the regular files directly in each of `dirs` whose names one of `globs` matches, each
    directory that could not be listed with its cause) - T614. A file is counted as the walk
    counts one (`_is_regular`, T612); a directory that cannot be listed is never read as empty
    (T611)."""
    found: list[Path] = []
    gaps: list[tuple[str, str]] = []
    for directory in dirs:
        folder = root if directory == "." else root / directory
        try:
            with os.scandir(folder) as entries:
                names = sorted(entry.name for entry in entries)
        except OSError as exc:
            gaps.append((directory, repo_walk.os_error_word(exc)))
            continue
        found += [folder / name for name in names
                  if any(repo_walk.glob_match(name, name, g) for g in globs)
                  and repo_walk._is_regular(str(folder / name))]
    return found, gaps


def activate(regs: list[Registration], components: list[dict], root: Path,
            root_exclusions: frozenset[str] = frozenset(),
            all_regs: list[Registration] | None = None, *,
            workspace: list[dict]) -> Activation:
    """Decide which extractors run against which components, by matching real files.

    **Not by stack label, deliberately.** `detect_stack()` returns one label per component
    chosen by first match, but a component routinely needs several extractors at once, and a
    tree holding only deployment charts detects as `unknown` while still carrying facts a
    chart extractor must read. Gating on the label would drop those without a word.

    Each component is walked ONCE with the union of every registered glob; the per-extractor
    split is then done in memory. That is equivalent to calling `find_files` per extractor —
    see `repo_walk.glob_match` — and turns a walk per (extractor, component) pair into a walk
    per component.

    `root_exclusions` names files the HARNESS ITSELF writes at a component's root (T153,
    W71's measurement on T145): `.mcp.json` is an ordinary `*.json` file to any glob-based
    extractor, so once a repository commits its install, a build's extractor SET depends on
    whether the harness's own bootstrap file is present rather than on the adopter's code —
    the harness's own artifacts appearing in the graph of somebody else's code. Callers pass
    this DERIVED from the installer itself (`load_installer().MCP_CONFIG`), never a name
    hand-kept in this walker, so it can never name a file the installer does not actually
    write.

    `all_regs` is the whole registry when `--only` narrowed `regs`. The walk and the
    off-case rows are measured against it (B9-13): a file some UNSELECTED extractor reads
    is no loss, and a narrowed run must not report one that a full build contradicts.

    **A component reads its own tree, not its declared children's (T591).** `workspace` is
    the WHOLE workspace's component list, required so no caller can narrow it by leaving it
    out: a child declared inside a component is judged from it, never from `components`,
    which `--components` narrows (D15a-1). A file under a declared child - by whole path
    segments - is that child's alone, and so is an off-case near miss there. Before this,
    a parent read its children's files too, and the root component read every file, so one
    file made claims and rows from two readings. An extractor that declares
    `READS_NESTED` (maven's `<modules>`, gradle's `settings.gradle`) resolves modules
    across directories by its tool's own rule, so its readings keep the children's files,
    inside the parent's stamp, as before (the owner's ruling, 2026-10-03).
    """
    act = Activation(by_extractor={r.name: [] for r in regs},
                     by_component={c["name"]: [] for c in components})
    if not regs:
        return act

    registry = all_regs if all_regs is not None else regs
    union = sorted({g for r in registry for g in r.globs})
    layout = workspace_layout({"components": workspace})
    for comp in components:
        path = root / comp["path"]
        children = canonical_rules.declared_children(comp["name"], layout)
        misses: list[tuple[Path, tuple[str, ...]]] = []
        gaps: list[tuple[Path, str]] = []
        found = repo_walk.find_files(path, union, near_misses=misses, unlisted=gaps)
        # The build's own artifact directory is never workspace input. Latent until an
        # extractor globbed `*.json`: the chained build writes graph output and snapshot
        # manifests into `<component>/.sdlc/`, and a later build re-reading them would be
        # extracting facts from itself — every rebuild would then differ from the last
        # (the manifest changes per publish), which surfaced as freshness stamps that
        # never stabilised. `root_exclusions` is the same class one level up: the
        # installer's own root files are harness bootstrap, not adopter source, and are
        # excluded by exact component-relative path rather than by basename anywhere in
        # the tree — unlike OWN_ARTIFACT_DIR these are ordinary filenames an adopter could
        # otherwise legitimately use deeper in their own tree.
        rels = [(p, p.relative_to(path).as_posix(), p.name) for p in found
                if OWN_ARTIFACT_DIR not in p.relative_to(path).parts
                and p.relative_to(path).as_posix() not in root_exclusions]
        # Each file's holder: None for this component's own, else its most specific
        # declared child (T591).
        held = {rel: canonical_rules.child_holding(rel, children) for _, rel, _ in rels}
        # T614: the directories above it, for the readers that resolve identity from there.
        above = _dirs_above(comp["name"], layout)
        above_read: dict[tuple[str, ...], list[Path]] = {}
        # T611: each directory this walk could not list, scoped as an off-case file is - one
        # under a declared child is the child's, and its own walk reports it.
        for p, cause in gaps:
            rel = p.relative_to(path).as_posix()
            if OWN_ARTIFACT_DIR in p.relative_to(path).parts:
                continue
            if rel != "." and canonical_rules.child_holding(rel, children) is not None:
                continue
            act.unlisted.setdefault(comp["name"], []).append((rel, cause))
        own_misses: list[tuple[str, str]] = []
        for p, globs in misses:
            rel = p.relative_to(path).as_posix()
            if OWN_ARTIFACT_DIR in p.relative_to(path).parts or rel in root_exclusions:
                continue
            if canonical_rules.child_holding(rel, children) is not None:
                continue        # the child's own walk reports it
            own_misses.append((rel, p.name))
            act.off_case.setdefault(comp["name"], []).append(
                (rel, globs, sorted(r.name for r in registry
                                    if any(g in r.globs for g in globs))))
        # The deployment readers' case record (batch 15b): over this component's own files,
        # read by some extractor or by none, each deployment reader's globs that miss one
        # only by case - each asked of the files a reader here can be handed (B21-28).
        walked_own = frozenset(rel for _, rel, _ in rels if held[rel] is None)
        walked_nested = frozenset(rel for _, rel, _ in rels)
        for rel, base in sorted([(rel, base) for _, rel, base in rels if held[rel] is None]
                                + own_misses):
            if lost := deployment_misses(rel, base, registry):
                act.deploy_off_case.setdefault(comp["name"], []).append((rel, lost))
                if why := _nothing_claimed(rel, lost, registry, root / comp["path"],
                                           walked_own, walked_nested):
                    act.nothing_claimed.setdefault(comp["name"], {})[rel] = why
        for reg in regs:
            hits = [p for p, rel, base in rels
                    if (reg.reads_nested or held[rel] is None)
                    and any(repo_walk.glob_match(rel, base, g) for g in reg.globs)]
            if not hits:
                continue
            if reg.reads_ancestors and above:
                if reg.globs not in above_read:
                    above_files, above_gaps = _files_above(root, above, reg.globs)
                    above_read[reg.globs] = above_files
                    comp_base = root / comp["path"]
                    for directory, cause in above_gaps:
                        gap = freshness.handed_path(
                            root if directory == "." else root / directory, comp_base)
                        if (gap, cause) not in act.unlisted.get(comp["name"], []):
                            act.unlisted.setdefault(comp["name"], []).append((gap, cause))
                hits = hits + above_read[reg.globs]
            act.by_extractor[reg.name].append(comp["name"])
            act.by_component[comp["name"]].append(reg.name)
            act.files[(reg.name, comp["name"])] = hits
        left = [(rel, base) for _, rel, base in rels if held[rel] is not None]
        own = [rel for _, rel, _ in rels if held[rel] is None]
        if left and not own and not any(
                reg.reads_nested and any(repo_walk.glob_match(rel, base, g)
                                         for rel, base in left for g in reg.globs)
                for reg in registry):
            act.by_design[comp["name"]] = sorted({held[rel] for rel, _ in left})
            if not any(repo_walk.glob_match(rel, base, g)
                       for reg in regs for rel, base in left for g in reg.globs):
                act.by_design_unread.add(comp["name"])
    return act


# ── the pipeline ──────────────────────────────────────────────────────────────


def _json_sha(value: object) -> str:
    return hashlib.sha1(json.dumps(value, sort_keys=True, default=str)
                        .encode("utf-8")).hexdigest()


def config_digest(config: dict, component: str) -> str:
    """The workspace-configuration part of ONE component's freshness stamp.

    Only what a READER can read (ruling 17, batch 15b), since a load judges by the stamp
    (ruling 16). Every shard holds the shared part: the `[[canonical]]` blocks without their
    citations - compose's env ranking and helm's host-map ranking read them while extracting,
    so an edit changes what a build concludes without touching one component file - the
    layout as `workspace_layout` normalises it (every reader resolves paths against it,
    T579; `a` and `./a` are one place), and the profile by NAME, never the absolute path a
    moved tree resolves it to. Each shard also holds its own component's `path` as the profile
    records it (the location, normalised by the installer since T615),
    `role`, `stack` and `shallow` - everything its `repo:` node records, so `--check-fresh`
    never calls current a shard `--check-snapshot` calls drifted (D15b-91; the owner's
    refinement, 2026-10-07). Declarations only link passes read (`[[alias]]`,
    `[[entrypoint]]`, `[[apart]]`, `[[generated]]`), the pipeline-only component fields, the
    workspace's name and every run-scoped key leave it: link passes run on every build and
    every load, so nothing they read needs a shard to go stale.

    `<shared>:<own>`, in the one `config_sha` a shard records: a change to the shared part
    may change what ANY component's extractors leave in the shared cache, while an own field
    changes only that component's `repo:` node - `plan_incremental` reads the halves apart,
    so an own-field edit rebuilds that component alone.
    """
    canonical = [{k: v for k, v in block.items() if k not in ("file", "line")}
                 for block in config.get("canonical") or []]
    shared = {"canonical": canonical, "layout": workspace_layout(config),
              "profile": _name_of(config.get("profile"))}
    mine = next((c for c in config.get("components") or [] if c.get("name") == component),
                {})
    own = {"path": mine.get("path", ""), "role": mine.get("role", ""),
           "stack": mine.get("stack", ""), "shallow": bool(mine.get("shallow"))}
    return f"{_json_sha(shared)}:{_json_sha(own)}"


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
            # A file above the component (T614) as `../x`, never its absolute path.
            rel = freshness.handed_path(p, base)
            digest = manifest_lib.file_digest(p)
            poisoned = poisoned or digest == manifest_lib.UNREADABLE
            h.update(rel.encode("utf-8"))
            h.update(b"\x00")
            h.update(digest.encode("utf-8"))
            h.update(b"\x00")
        out.setdefault(comp, {})[ext] = \
            (f"unreadable-{h.hexdigest()}" if poisoned else h.hexdigest())
    # B9-4: the activation walk's off-case files, by name and pattern - their content is
    # read by nobody, which is the point. Without this a new off-case file left the stamp
    # unchanged, so `--incremental` reused a shard without its row and `--check-fresh`
    # called a drifted snapshot current.
    for comp, entries in act.off_case.items():
        h = hashlib.sha1()
        for rel, globs, _owners in sorted(entries):
            h.update(rel.encode("utf-8"))
            h.update(b"\x00")
            h.update("|".join(globs).encode("utf-8"))
            h.update(b"\x00")
        out.setdefault(comp, {})[ACTIVATION] = h.hexdigest()
    # Batch 15b: the deployment readers' case record, in an entry of its own (D15b-105,
    # D15b-123). A case-lost deployment file is an `unread` node and a row whatever else
    # reads it, so a rename that fixes one must stale the shard; and two records in one
    # entry printed both `--check-fresh` clauses for either.
    for comp, lost in act.deploy_off_case.items():
        h = hashlib.sha1()
        for rel, pairs in sorted(lost):
            h.update(rel.encode("utf-8"))
            h.update(b"\x00")
            h.update("|".join(f"{reader}:{glob}" for reader, glob in pairs).encode("utf-8"))
            h.update(b"\x00")
        out.setdefault(comp, {})[ACTIVATION_DEPLOY] = h.hexdigest()
    # T611: the directories the walk could not list, in an entry of their own (D15b-123),
    # POISONED as an unreadable file's digest is: content nobody could list is content
    # nobody may claim unchanged, so `stamp_matches` never accepts it and the component is
    # stale until the directory can be listed. Without the entry `--incremental` reused a
    # shard missing its row, and `--check-fresh` called the snapshot the tree (B9-4).
    for comp, gaps in act.unlisted.items():
        h = hashlib.sha1()
        for rel, cause in sorted(gaps):
            h.update(rel.encode("utf-8"))
            h.update(b"\x00")
            h.update(cause.encode("utf-8"))
            h.update(b"\x00")
        out.setdefault(comp, {})[ACTIVATION_UNLISTED] = f"unreadable-{h.hexdigest()}"
    return out


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
                     regs: list[Registration], config: dict
                     ) -> tuple[list[str], dict[str, str], set[str], dict[str, ExtractResult]]:
    """Decide what an incremental build may reuse. Returns (reused, rebuilt, cache_exts,
    facts): `facts` is each reused shard's facts and rows, from the one reading the decision
    was made on - the build pools them, with no second load to race the decision.

    A component is reused exactly when its shard's verdict is `current` - the one judgment
    `--check-fresh` prints and a load narrows by (batch 16, the owner's ruling 29) - and
    rebuilt otherwise, with the verdict's reason, because "rebuilt" without a why is a
    decision nobody can audit. So a refused shard is rebuilt alone unless what it read
    moved (T629), no reason is borrowed from another claim (T630), and a damaged shard is a
    reason, never a traceback (T628). A non-current shard's facts are dropped once judged.

    `cache_exts` is the price of the shared cache: the transitive dependencies of every
    rebuilt component's active extractors must RUN for all components so the cache holds
    what a full build's would — their facts for reused components are then discarded,
    because the shard already carries the identical rows (determinism, item 13's check).
    """
    readings, verdicts = shard_verdicts(root, components, regs, act, config)
    reused = [c["name"] for c in components if verdicts[c["name"]].state == "current"]
    rebuilt = {c["name"]: verdicts[c["name"]].plan for c in components
               if verdicts[c["name"]].state != "current"}
    facts = {name: readings[name].facts for name in reused}
    closure = requires_closure(regs)

    cache_exts: set[str] = set()
    for name in rebuilt:
        for ext in act.by_component.get(name) or []:
            cache_exts |= closure.get(ext, frozenset())
    return reused, rebuilt, cache_exts, facts


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

    Behaviour is otherwise the old loop's exactly. Narrowing `ctx.repos` to one component
    hides the other components from an extractor that looks across them - which is how
    compose's `build: ../orders` stopped resolving (T579) - so a fact that needs another
    component reaches an extractor through `config["components"]`, the whole workspace's
    list, and never through `ctx.repos` (T534 v4). `cache` stays one shared object in the
    same claimed order, so cross-component resolution (a compose file answering for another
    component's env reads) is unchanged; and a failure still discards the WHOLE extractor —
    per-component salvage would be an improvement, but a different contract than the one
    the report and its tests pin today.
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
            violations.extend(_tagged(check_unresolved(
                res.unresolved, reg.unresolved_fields, reg.name), "fields"))
            violations.extend(_tagged(check_patterns(
                res.unresolved, reg.unresolved_patterns, reg.name), "patterns"))
            per_component[comp] = res
        if failed:
            failures[reg.name] = failed
            continue
        # Batch 16 (review B16-1) and T636 (B16-8): a `grammar-unavailable` row says the
        # reader could not parse, and names in `language` the parser it could not use. Only a
        # parser the reader declares reaches its shard's stamp and can be asked about, so a
        # row naming another, or none - or any row, from a reader declaring none - is refused
        # here, where every row passes, never a shard whose parser state nothing records.
        stray: dict[str | None, set[str]] = {}
        for comp, res in per_component.items():
            for row in res.unresolved:
                if row.get("kind") != "grammar-unavailable":
                    continue
                named = row.get("language") if isinstance(row.get("language"), str) else None
                if named not in reg.parsers:
                    stray.setdefault(named, set()).add(comp)
        if stray and not reg.parsers:
            lost = [comp for comp in per_component if any(comp in c for c in stray.values())]
            violations.append(Violation(
                f"{reg.name}: filed `grammar-unavailable` for {', '.join(lost)} but declares no "
                f"parser it can lack (`PARSERS = ()`) - declare the parser its gate needs, so "
                f"its shard records whether it was installed", "declaration"))
        for named, comps in sorted(stray.items() if reg.parsers else (),
                                   key=lambda item: item[0] or ""):
            where = ", ".join(comp for comp in per_component if comp in comps)
            violations.append(Violation(
                f"{reg.name}: filed `grammar-unavailable` for {where} naming no parser - name "
                f"the one it could not use in the row's `language`, a parser it declares "
                f"(`PARSERS = {reg.parsers!r}`), so its shard records whether it was installed"
                if named is None else
                f"{reg.name}: filed `grammar-unavailable` for {where} naming the parser "
                f"{named!r}, which it does not declare (`PARSERS = {reg.parsers!r}`) - name a "
                f"parser it declares, or declare the one it needs, so its shard records whether "
                f"it was installed", "declaration"))
        owned[reg.name] = per_component
    return owned, failures, violations


def run_link_passes(links: list[LinkRegistration], merged: ExtractResult,
                    components: list[dict], root: Path, run_id: str,
                    config: dict, *, narrowed: bool,
                    deployment_inputs: dict[str, list[str]],
                    deployment_claims: dict[str, list[str]],
                    deployment_off_case: dict[str, list[str]]
                    ) -> tuple[dict[str, ExtractResult], dict, list[str]]:
    """Run each link pass over the whole graph, in declared order.

    A pass sees every node and edge, which is the point: whether a topic one component writes
    is produced by another, or which of two deployment packages is canonical, are facts no
    single extractor can reach from one component's files.

    The lists are handed over live. A pass may REWRITE entries — that is how canonicality and
    damping are applied — and whatever it returns is added on top. Passes run in order, so a
    later one sees what an earlier one decided.

    Three keys are RUN-SCOPED (batch 15b): `narrowed` - this run could not see the whole
    workspace through its deployment readers, so no accounting row is judged - and, per
    component, the files its deployment readers were handed (`deployment_inputs`), the
    claim files among them (`deployment_claims`) and the files they missed only by case
    (`deployment_off_case`), which decide whether a component was read at all. Every pass gets its own copy of the config with them added;
    the caller's `config` - the dict the freshness stamps digest - never holds them.
    """
    out: dict[str, ExtractResult] = {}
    failures: dict[str, str] = {}
    violations: list[str] = []
    scoped = {**config, "narrowed": narrowed, "deployment_inputs": deployment_inputs,
              "deployment_claims": deployment_claims,
              "deployment_off_case": deployment_off_case}
    for lp in links:
        ctx = ExtractContext(repos=repo_refs(components, root), run_id=run_id,
                             config=dict(scoped))
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
        violations.extend(_tagged(check_unresolved(
            res.unresolved, lp.unresolved_fields, lp.name), "fields"))
        violations.extend(_tagged(check_patterns(
            res.unresolved, lp.unresolved_patterns, lp.name), "patterns"))
        out[lp.name] = res
    return out, failures, violations


class Violation(str):
    """A contract violation's sentence, tagged with its class (T637): `fields` - a row missing
    a field its kind promises; `patterns` - a row outside its kind's pattern vocabulary;
    `declaration` - a reader's declaration a row contradicts. Each class sends a person
    somewhere else, so each is headlined in its own words. A `str`, so every reader of the
    list - the `contract:` lines, `build_report.json` - reads it as before."""

    kind = "fields"

    def __new__(cls, text: str, kind: str = "fields") -> "Violation":
        made = super().__new__(cls, text)
        made.kind = kind
        return made


def _tagged(problems: list[str], kind: str) -> list[Violation]:
    return [Violation(problem, kind) for problem in problems]


#: Each class's headline, first sentence and tail (T637).
VIOLATION_HEADLINES = {
    "fields": ("{n} unresolved row(s) do not carry the fields their kind promises",
               "every one of those rows exists to name something that is missing, and cannot. "
               "The graph above is unaffected; the validation report is not."),
    "patterns": ("{n} unresolved row(s) name a pattern outside their kind's declared "
                 "vocabulary",
                 "each groups as a finding of its own that is none. The graph above is "
                 "unaffected; the validation report is not."),
    "declaration": ("{n} reader declaration(s) are contradicted by the rows the reader filed: "
                    "a `grammar-unavailable` row names a parser its reader does not declare, "
                    "or none",
                    "whether that parser was installed reaches no shard's stamp. The graph "
                    "above is unaffected; no snapshot can record it."),
}


def violation_headlines(violations: list[str]) -> list[str]:
    """One headline per class of the violations present, in `VIOLATION_HEADLINES`' order: a
    build's warnings and a load's alike (T637; review round 1 - the load printed bare lines)."""
    lines = []
    for kind, (headline, tail) in VIOLATION_HEADLINES.items():
        n = sum(1 for v in violations if getattr(v, "kind", "fields") == kind)
        if n:
            lines.append(f"{headline.format(n=n)}. This is a DEFECT IN THE HARNESS rather than a "
                         f"finding about the workspace: {tail}")
    return lines


def link_failure_line(name: str, why: str) -> str:
    """The sentence a crashed link pass is reported with, on a build and on a load (T618): its
    findings are missing, which must never read like a pass that found nothing."""
    return (f"link pass {name!r} failed, so every row it would have emitted is missing - its "
            f"findings are absent, not clean ({why}). Fix or remove the pass, then rebuild.")


def deployment_scope(act: Activation, components: list[dict], root: Path,
                     registry: list[Registration]) -> dict[str, dict[str, list[str]]]:
    """The run-scoped lists the accounting rows read (batch 15b, T573 decision 3): per
    component, the files its deployment readers were handed, and the files they missed only
    by case. A component a deployment reader was handed one of its files, or missed one of
    by case, was READ - even where no node came of it (D15b-122, D15b-132)."""
    deployment = {r.name: r for r in registry if r.deployment_reader}
    handed: dict[str, set[str]] = {}
    claims: dict[str, set[str]] = {}
    for (ext, comp), paths in act.files.items():
        reg = deployment.get(ext)
        if reg is None:
            continue
        base = root / _path_of(components, comp)
        for p in paths:
            # Spelled as every reader of `act.files` spells a path (T614): a file above the
            # component, handed to a reader declaring `READS_ANCESTORS`, is no file of the
            # component's own, so the accounting rows leave it out (review B21-5).
            rel = freshness.handed_path(p, base)
            if rel == ".." or rel.startswith("../") or Path(rel).is_absolute():
                continue
            handed.setdefault(comp, set()).add(rel)
            # The claim files among them: read for a service claim, so a file read that
            # declares nothing is named by the rows (D15b-122, D15b-136).
            if any(repo_walk.glob_match(rel, p.name, g) for g in reg.claim_globs):
                claims.setdefault(comp, set()).add(rel)
    return {"deployment_inputs": {c: sorted(v) for c, v in sorted(handed.items())},
            "deployment_claims": {c: sorted(v) for c, v in sorted(claims.items())},
            "deployment_off_case": {c: sorted(rel for rel, _ in lost)
                                    for c, lost in sorted(act.deploy_off_case.items())}}


#: What a narrowed run does not judge, and the one exception it makes, said in every
#: narrowed line (batch 15b; T573 decision 3, D15b-72).
NARROWED_TAIL = ("so it judged neither `canonical-unmatched` nor `apart-unmatched`, which "
                 "what it could not see could change; and an `[[apart]]` refusal, judged over "
                 "fewer claims, may move a chart onto its component's own node where a whole "
                 "build holds it back, because the package that claims that node was not read")
#: What the configuration a shard records now covers, said beside `STALE_CONFIG` (claim 5's
#: words, owned by `freshness.py`) in a load's line (D15b-84).
CONFIG_COVERS = (" (the configuration a shard records: the `[[canonical]]` blocks, the "
                 "layout, the profile's name, and each component's own path as recorded, "
                 "role, stack and shallow flag)")


def _capped(names: list[str], cap: int = 5) -> str:
    """At most `cap` names, the cap admitted - a truncated list that does not say so reads
    exactly like a complete one, as the report's row printer knows (D15b-75)."""
    return ", ".join(names[:cap]) + (f" and {len(names) - cap} more" if len(names) > cap
                                     else "")


def _lacking(rows: list[dict], registry: list[Registration], *, failed: Collection[str] = (),
             probe: Callable[[str], bool] | None = None
             ) -> dict[str, tuple[list[str], list[str]]]:
    """Each deployment reader with a `grammar-unavailable` row - its parser missing, so it read
    none of its files and published no deployment fact (D15b-130) - in the registry's order: the
    components it filed one for, and the parsers those rows name in `language` (T636) - ""
    for a row naming no parser its reader declares, which nothing can ask about, so it is
    carried, never read as installed (review B16-1). With `probe` (a load), a row naming a
    declared parser installed NOW is passed over: its shard is stale by its parser stamp
    instead, so no line says "no parser installed" of an installed parser (D16-53). One rule
    for a build's own rows and a load's shards' rows alike (review B15b-21: a shard published by
    such a build loaded as current, and its load judged both accounting rows over it)."""
    by_name = {r.name: r for r in registry}
    found: dict[str, tuple[set[str], set[str]]] = {}
    for row in rows:
        reg = by_name.get(str(row.get("extractor", "")))
        if (row.get("kind") != "grammar-unavailable" or reg is None
                or not reg.deployment_reader or reg.name in failed):
            continue
        named = row.get("language")
        parser = named if isinstance(named, str) and named in reg.parsers else ""
        if probe is not None and parser and probe(parser):
            continue
        comps, parsers = found.setdefault(reg.name, (set(), set()))
        comps.add(str(row.get("component", "")))
        parsers.add(parser)
    return {r.name: (sorted(found[r.name][0]), sorted(found[r.name][1]))
            for r in registry if r.name in found}


def _lacked_words(name: str, parsers: list[str] | None) -> str:
    """What a reader's rows can say of its parser (T636): the parsers they name, missing; and,
    for a row naming none its reader declares, that whether one is installed cannot be asked."""
    if parsers is None:
        return "no parser installed"
    named = [p for p in parsers if p]
    words = [f"no parser {', '.join(f'`{p}`' for p in named)} installed"] if named else []
    if "" in parsers:
        words.append(f"a row names no parser `{name}` declares, so whether one is installed "
                     f"cannot be asked")
    # ", and ", never "; ": the reasons a line lists are themselves joined with "; ", and a
    # second clause must not read as a reason of its own (review B17-2).
    return ", and ".join(words)


def _lacked_way(name: str, parsers: list[str]) -> str:
    """What lifts it: install each parser the rows name, or name a declared one in the row."""
    named = [p for p in parsers if p]
    many = len(named) > 1
    ways = ([f"install {', '.join(f'`{p}`' for p in named)}, the parser{'s' if many else ''} "
             f"`{name}`'s `grammar-unavailable` row{'s' if many else ''} "
             f"name{'' if many else 's'}"] if named else [])
    if "" in parsers:
        ways.append(f"name, in `{name}`'s `grammar-unavailable` row, a parser it declares")
    return ", and ".join(ways)


def build_narrowing(workspace_components: list[dict], components: list[dict],
                    only: list[str] | None, failures: dict[str, str], rows: list[dict],
                    registry: list[Registration]) -> str:
    """The line a build adds to its report's notes when it could not see the whole
    workspace through its deployment readers, or "" (batch 15b, T573 decision 3): every
    reason, and what lifts each - so following it is accepted. A failure of any other
    reader narrows nothing (round 8 measured a failed `maven-modules` narrowing the run)."""
    deployment = [r.name for r in registry if r.deployment_reader]
    left_out = len(workspace_components) - len(components)
    failed = [n for n in deployment if n in failures]
    # D15b-130: with its parser missing a deployment reader files `grammar-unavailable`
    # instead of failing, and the rows would be judged over a graph with no deployment facts.
    lacking = _lacking(rows, registry, failed=failed)
    unparsed = list(lacking)
    reasons = ([f"`--components` left out {left_out} component(s)"] if left_out else []) \
        + (["`--only`"] if only else []) \
        + [f"`{n}` failed" for n in failed] \
        + [f"`{n}` could not parse its files - {_lacked_words(n, lacking[n][1])}"
           for n in unparsed]
    if not reasons:
        return ""
    ways = (["build the whole workspace, with no `--components` or `--only`"]
            if left_out or only else []) \
        + [f"fix what `{n}`'s warning names" for n in failed] \
        + [_lacked_way(n, lacking[n][1]) for n in unparsed]
    return (f"this run was narrowed ({'; '.join(reasons)}), {NARROWED_TAIL}. To judge "
            f"both: {'; '.join(ways)}"
            + (" - a whole-workspace build is narrowed too while a deployment reader fails "
               "or has no parser" if failed or unparsed else "") + ".")


def load_narrowing(missing: list[str], refused: dict[str, freshness.Verdict],
                   stale: dict[str, str], unparsed: dict[str, list[str]] | None = None,
                   lacked: dict[str, list[str]] | None = None) -> str:
    """The line a `--from-snapshot` load adds when a component's shard is missing, refused,
    or stale by the one verdict `--check-fresh` prints, or "" (ruling 16; batch 16): the
    components grouped by reason, each reason word for word, each group capped. A refusal's
    exact remedy is on its REFUSING line; the way on here keeps "or build the whole
    workspace", which no refusal blocks. The parser group is every deployment reader whose
    loaded shard says it had no parser and that still has none (`still_unparsed`, D16-53)."""
    digest = sorted(n for n, v in refused.items() if v.words == snapshot_lib.DIGEST_WORDS)
    groups = ([f"no shard: {_capped(sorted(missing))}"] if missing else []) \
        + ([f"refused for a digest mismatch: {_capped(digest)}"] if digest else [])
    by_refusal: dict[str, list[str]] = {}
    for name, v in sorted(refused.items()):
        if name not in digest:
            by_refusal.setdefault(v.load, []).append(name)
    groups += [f"refused - {why}: {_capped(names)}" for why, names in sorted(by_refusal.items())]
    by_reason: dict[str, list[str]] = {}
    for name, why in sorted(stale.items()):
        if name not in missing and name not in refused:
            by_reason.setdefault(why, []).append(name)
    groups += [f"stale - {why}{CONFIG_COVERS if why == STALE_CONFIG else ''}: "
               f"{_capped(names)}" for why, names in sorted(by_reason.items())]
    unparsed = unparsed or {}
    groups += parser_group(unparsed, lacked)
    if not groups:
        return ""
    ways = (["re-publish the shards it names from a build of the workspace (`--check-fresh` "
             "then exits 0), or build the whole workspace"]
            if missing or refused or by_reason else []) \
        + ([parser_way(lacked)] if unparsed else [])
    return (f"this load was narrowed ({'; '.join(groups)}), {NARROWED_TAIL}. To judge "
            f"both: {'; '.join(ways)}.")


#: The parser group's way on: the load's, and `--check-fresh`'s NOTE (batch 16, D16-21).
PARSER_WAY = ("install the parser each `grammar-unavailable` row names, then re-publish the "
              "shards it names")
#: The way on for a row naming no parser its reader declares (T636, review B17-2): there is
#: nothing to install for it - its reader must name a parser.
UNASKABLE_WAY = ("name, in each `grammar-unavailable` row that names none, a parser its reader "
                 "declares, then re-publish the shards it names")


def parser_way(lacked: dict[str, list[str]] | None) -> str:
    """The parser group's way on, one its every row can follow (review B17-2): install each
    parser the rows name, and, for a row naming none its reader declares ("" in `lacked`),
    name one - "install" alone sent that row to a parser nobody can install."""
    parsers = [p for named in (lacked or {}).values() for p in named]
    if not parsers or all(parsers):
        return PARSER_WAY
    if not any(parsers):
        return UNASKABLE_WAY
    return ("install the parser each `grammar-unavailable` row names, and name, in each that "
            "names none, a parser its reader declares, then re-publish the shards it names")


def parser_group(unparsed: dict[str, list[str]],
                 lacked: dict[str, list[str]] | None = None) -> list[str]:
    """The parser group's words, one per deployment reader: the load's, and `--check-fresh`'s
    NOTE, so the two never word one reader differently (batch 16, D16-21) - each naming the
    parsers its rows say it lacked, from `lacked` (T636)."""
    return [f"`{name}` could not parse its files when its shard was published - "
            f"{_lacked_words(name, (lacked or {}).get(name))}: "
            f"{_capped(names)}" for name, names in unparsed.items()]


def still_unparsed(readings: dict[str, snapshot_lib.ShardReading],
                   verdicts: dict[str, freshness.Verdict], regs: list[Registration],
                   probe: Callable[[str], bool]) -> dict[str, list[str]]:
    """The parser group (D16-53): each deployment reader with a `grammar-unavailable` row in a
    current or stale shard - every shard a load reads - whose parser is still missing NOW. A
    parser installed since makes the shard stale by its parser stamp instead, so no line says
    "no parser installed" of an installed parser. The parser asked for is the one each row
    names (T636, review B16-8): asking every parser the reader declares read a row's parser
    installed since as missing while another was. A row naming no parser its reader declares
    cannot be asked, so it is carried, never read as installed (review B16-1: `all(())` is
    True, and the load fell silent over a shard holding none of that reader's facts)."""
    return {name: comps for name, (comps, _)
            in _lacking(_loaded_rows(readings, verdicts), regs, probe=probe).items()}


def unparsed_parsers(readings: dict[str, snapshot_lib.ShardReading],
                     verdicts: dict[str, freshness.Verdict], regs: list[Registration],
                     probe: Callable[[str], bool]) -> dict[str, list[str]]:
    """The parsers `still_unparsed`'s rows name, per reader - "" for a row naming none its
    reader declares (T636): what the parser group's words name."""
    return {name: parsers for name, (_, parsers)
            in _lacking(_loaded_rows(readings, verdicts), regs, probe=probe).items()}


def _loaded_rows(readings: dict[str, snapshot_lib.ShardReading],
                 verdicts: dict[str, freshness.Verdict]) -> list[dict]:
    """Every row of every shard a load reads: the current and the stale."""
    return [row for name, reading in readings.items()
            if verdicts[name].state in ("current", "stale") and reading.facts is not None
            for row in reading.facts.unresolved]


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
    #: T534 v4 (ruling 3): extractors resolve against the WHOLE workspace's component list
    #: even when `--components` narrows what is extracted, so a component's facts never
    #: depend on which others a run was given.
    workspace_components = list(components)
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
    registry = regs
    if only:
        wanted = set(only)
        known = {r.name for r in regs} | {lp.name for lp in links}
        unknown = sorted(wanted - known)
        if unknown:
            raise BuildError(f"--only names {', '.join(unknown)}, which is not registered. "
                             f"Registered: {', '.join(sorted(known)) or '(none)'}")
        regs = [r for r in regs if r.name in wanted]      # already topologically ordered
        link_names = {lp.name for lp in links}
        links = [lp for lp in links if lp.name in wanted]
        # T534 v5 (D534-23): a link pass reads what the passes it REQUIRES decided -
        # `mark-canonical` reads contests after `join-by-name` has moved claims - and unlike
        # an extractor reading an absent cache, no pass reports that its input was never
        # made. Named without them, it would answer about a graph no build produces.
        for lp in links:
            if missing := sorted(r for r in lp.requires
                                 if r in link_names and r not in wanted):
                raise BuildError(
                    f"--only names {lp.name}, which reads what {', '.join(missing)} "
                    f"decide{'s' if len(missing) == 1 else ''} first. Add "
                    f"{', '.join(missing)} to --only, or leave {lp.name} out")

    run_id = run_id or uuid.uuid4().hex[:12]
    act = activate(regs, components, root, root_exclusions=installer_root_exclusions(si),
                   all_regs=registry, workspace=workspace_components)
    config = {"workspace_name": ws.get("name", ""), "canonical": ws.get("canonical", []),
              "generated": ws.get("generated", []),
              "entrypoint": ws.get("entrypoint", []),
              "alias": ws.get("alias", []),
              "apart": ws.get("apart", []),
              "components": workspace_components, "profile": str(profile_dir or "")}

    inc: dict | None = None
    rebuilt_set: set[str] = set()
    reused_facts: dict[str, ExtractResult] = {}
    run_act = act
    if incremental:
        reused, rebuilt, cache_exts, reused_facts = plan_incremental(
            root, components, act, regs, config)
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

    # Batch 15b: the deployment readers' case record reaches the extractors in their own
    # copy of the config, run-scoped - `helm-deployments` counts a template lost to its case
    # as one of its chart's own (ruling 20, D15b-139) - never in the dict the stamps digest.
    extract_config = {**config, "deployment_off_case":
                      deployment_scope(act, components, root, registry)["deployment_off_case"]}
    owned, failures, violations = run_extractors_owned(
        regs, run_act, components, root, run_id, extract_config)
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
    # The activation walk's own rows, for the components this build extracted: a reused
    # shard already carries its component's, exactly as it carries every extractor's.
    for comp in components:
        if inc is None or comp["name"] in rebuilt_set:
            for files, globs, owners in off_case_groups(act.off_case.get(comp["name"], [])):
                merged.unresolved.append(off_case_row(comp["name"], files, globs, owners))
            named = {rel for rel, _, _ in act.off_case.get(comp["name"], [])}
            lost = act.deploy_off_case.get(comp["name"], [])
            nothing = act.nothing_claimed.get(comp["name"], {})
            for files, pairs, why in deployment_case_groups(lost, named, nothing):
                merged.unresolved.append(
                    deployment_off_case_row(comp["name"], files, pairs, registry, why))
            merged.nodes.extend(case_lost_nodes(comp["name"], lost, named, registry, nothing))
            for directory, cause in act.unlisted.get(comp["name"], []):
                merged.unresolved.append(unlisted_row(comp["name"], directory, cause))
    violations.extend(_tagged(check_unresolved(
        [r for r in merged.unresolved if r.get("extractor") == ACTIVATION],
        UNRESOLVED_FIELDS, ACTIVATION), "fields"))
    for reg in regs:
        if (res := results.get(reg.name)) is not None:
            merged.extend(res)
    # The reused shards' facts and rows, from the reading the reuse was decided on (batch
    # 16): nothing is read twice, so nothing can change between the decision and the merge.
    for facts in reused_facts.values():
        merged.extend(facts)
    narrowing = build_narrowing(workspace_components, components, only, failures,
                                merged.unresolved, registry)
    link_results, link_failures, link_violations = run_link_passes(
        links, merged, components, root, run_id, config, narrowed=bool(narrowing),
        **deployment_scope(act, components, root, registry))
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
        # T423: the provenance stamps are taken BEFORE this build writes anything.
        # `git_dirty` is a plain `git status --porcelain`, so asked after the shards
        # land inside a component - where a committed `.sdlc/graph/` lives - the build
        # saw its OWN output as the dirt, and "built from a clean checkout" read dirty
        # on every rebuild. The question is about the developer's tree, not ours.
        stamps = {comp["name"]: manifest_lib.RepoStamp(
                      alias=comp["name"], head=manifest_lib.git_head(root / comp["path"]),
                      dirty=manifest_lib.git_dirty(root / comp["path"]))
                  for comp in components}
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
        mf.repos.update(stamps)
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
    if narrowing:
        report["notes"].append(narrowing)
    if inc is not None:
        report["incremental"] = inc

    if publish_snapshot or check_snapshot:
        # Facts only — assembled from the OWNED results, never from `merged`, which the
        # link passes have already rewritten in place. What a link pass concluded is
        # derived from the whole graph and belongs to no shard; it is recomputed at load.
        if failures or violations:
            # A shard published without a failed extractor's facts commits an absence
            # that reads exactly like a clean answer, in the artifact whose whole job
            # is to be diffable evidence. A crashed link pass is named as one (T618): its
            # findings are recomputed at load and no shard holds them, but the build failed.
            linked = sorted(name for name in failures if name in link_failures)
            extracted = len(failures) - len(linked)
            report["snapshot"] = {"refused": (
                f"{extracted} extractor failure(s), {len(linked)} link pass failure(s) and "
                f"{len(violations)} contract violation(s) mean these facts are incomplete; "
                f"an incomplete shard reads exactly like a clean one"
                if extracted or violations else
                f"{len(linked)} link pass failure(s): the build failed, and a snapshot is "
                f"published only from a build that passed - fix or remove the pass, then "
                f"publish again")}
            print("snapshot: refused - " + report["snapshot"]["refused"])
        else:
            # An incremental publish rewrites only the rebuilt shards: the reused ones
            # were verified byte-identical to what their files extract to, so rewriting
            # them would change nothing and re-stamp nothing.
            pub_components = ([c for c in components if c["name"] in rebuilt_set]
                              if inc is not None else components)
            facts = with_off_case(component_facts(pub_components, regs, owned),
                                  act, registry)
            if check_snapshot:
                # Item 133: no scratch is handed over, because `out` is the
                # committed `.sdlc/graph/` and a question must not write into
                # the repository it audits.
                drifted, unpublished, unreadable, uncomparable = snapshot_lib.check(
                    root, components, facts)
                report["snapshot"] = {"drifted": drifted, "unpublished": unpublished,
                                      "unreadable": unreadable,
                                      "uncomparable": uncomparable}
                for name in drifted:
                    print(f"snapshot: DRIFT in {name} - its committed shard no longer "
                          f"matches what its files extract to")
                for name in unpublished:
                    print(f"snapshot: {name} has no committed shard to check")
                for name in uncomparable:
                    # COULD-NOT-LOOK on the FRESH side (T425): the check needs this
                    # component's freshly-extracted facts turned into shard bytes to
                    # byte-compare, and a node/edge carrying a non-JSON `props` value
                    # cannot be serialized. This used to escape `snapshot.check` as an
                    # uncaught TypeError and reach a person as a traceback at exit 1 - a
                    # question answered with NO answer. NOT a raw count of the pool (a
                    # confident wrong answer) and NOT DRIFT (a fact this build cannot
                    # serialize is not evidence the committed bytes changed). It rides the
                    # same non-zero exit ladder as DRIFT and the manifest-side unreadable.
                    print(f"snapshot: REFUSING {name} - its freshly extracted facts "
                          f"could not be serialized, so this build cannot produce "
                          f"comparable bytes and cannot verify whether its committed "
                          f"shard matches; a fact this build cannot serialize is not "
                          f"evidence the shard drifted. Fix the extractor emitting a "
                          f"non-JSON property, then re-check")
                for name in unreadable:
                    # Batch 16 (D16-40): a shard FILE that cannot be read, not its
                    # manifest - its own words, and a remedy the publish accepts.
                    cause = snapshot_lib.unreadable_cause(
                        root, next(c for c in components if c["name"] == name))
                    if cause:
                        print(f"snapshot: REFUSING {name} - {cause}; this build cannot "
                              f"verify whether its shard matches - remove the non-file or "
                              f"fix what blocks reading it, then re-publish (build "
                              f"--publish-snapshot)")
                        continue
                    # COULD-NOT-LOOK (T436): the manifest exists but cannot be read, so
                    # this build cannot verify whether the shard matches. NOT reported as a
                    # clean match (the old exit-0 defect) and NOT as DRIFT - a corrupt
                    # manifest is not evidence the bytes changed. It rides the same non-zero
                    # exit ladder as DRIFT, below.
                    print(f"snapshot: REFUSING {name} - its committed "
                          f"{snapshot_lib.MANIFEST} exists but could not be read, so this "
                          f"build cannot verify whether its shard matches; a corrupt "
                          f"manifest is not evidence the bytes drifted. Re-publish it "
                          f"(build --publish-snapshot)")
                if len(unpublished) == len(components):
                    # NOT ONE component has a committed shard, so this gate compared
                    # nothing and cannot report a clean tree. `snapshot.check` returns an
                    # empty `drifted`, which used to fall through to the "N shard(s) match"
                    # line below and exit 0 - an empty result read as a clean answer, inside
                    # the drift gate itself. The sibling `build_from_snapshot` refuses the
                    # same empty snapshot (status `snapshot-empty`, non-zero exit); T235
                    # hardened one of the pair and left this one. Same status word, same
                    # exit ladder, and a sentence true of what it examined - the per-
                    # component "no committed shard" lines above still name every one.
                    report["snapshot"]["status"] = "snapshot-empty"
                    print(f"snapshot: REFUSING - 0 of {len(components)} component(s) have a "
                          f"committed shard, so this checked nothing and cannot report a "
                          f"clean tree. Publish shards first (build --publish-snapshot)")
                elif not drifted and not unreadable and not uncomparable:
                    print(f"snapshot: {len(components) - len(unpublished)} shard(s) match "
                          f"their committed bytes")
            else:
                stamp_inputs = input_digests(act, components, root)
                version = extractor_version(regs)
                # Ruling 30: the parser state each shard was built under, from the one
                # producer the verdict compares against (D16-31).
                closure, probe, parser_of = (requires_closure(regs), parser_probe(regs),
                                             parser_rule(regs))
                memo: dict = {}
                stamps = {c["name"]: {"inputs": stamp_inputs.get(c["name"], {}),
                                      "extractor_version": version,
                                      "config_sha": config_digest(config,
                                                                  c["name"]),
                                      "parsers": freshness.parser_stamp(
                                          c["name"], act, closure, probe, parser_of, root,
                                          components, memo)}
                          for c in pub_components}
                try:
                    digests = snapshot_lib.publish(root, pub_components, facts, stamps)
                except snapshot_lib.PublishRefused as exc:
                    # No `published` key, so the exit is 2 (D16-49).
                    report["snapshot"] = {"refused": str(exc)}
                    print("snapshot: refused - " + report["snapshot"]["refused"])
                else:
                    report["snapshot"] = {"published": sorted(digests)}
                    print(f"snapshot: {len(digests)} shard(s) published, one per "
                          f"component, into each component's own .sdlc/graph/")

    if out is not None:
        # LF on every platform (T546): committed, like the shards it reports on.
        (out / "build_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8",
            newline="\n")
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


def with_off_case(facts: dict[str, ExtractResult], act: Activation,
                  registry: list[Registration]) -> dict[str, ExtractResult]:
    """`facts` with the activation walk's rows about each component's files added (T531),
    and the deployment readers' case-lost files - their rows and their `unread` nodes
    (batch 15b) - and the directories the walk could not list (T611).

    They are facts about the component's files like any extractor's rows, so they ride in
    its shard and `rebuild == snapshot` holds. Added AFTER `component_facts` rather than
    inside it, so that function's contract - what the extractors read - is unchanged.
    """
    for name, res in facts.items():
        for files, globs, owners in off_case_groups(act.off_case.get(name, [])):
            res.unresolved.append(off_case_row(name, files, globs, owners))
        named = {rel for rel, _, _ in act.off_case.get(name, [])}
        lost = act.deploy_off_case.get(name, [])
        nothing = act.nothing_claimed.get(name, {})
        for files, pairs, why in deployment_case_groups(lost, named, nothing):
            res.unresolved.append(deployment_off_case_row(name, files, pairs, registry, why))
        res.nodes.extend(case_lost_nodes(name, lost, named, registry, nothing))
        for directory, cause in act.unlisted.get(name, []):
            res.unresolved.append(unlisted_row(name, directory, cause))
    return facts


def build_from_snapshot(workspace: Path | dict | None, out: Path, *,
                        target: Path | None = None, run_id: str = "") -> dict:
    """The graph from committed shards: no extraction, link passes recomputed at load.

    A partial set loads LOUDLY — the report and stdout name every component with no
    shard, because a developer with 3 of 26 components cloned has a graph that answers
    for 3, and silence about the other 23 is the "empty reads exactly like clean"
    failure at estate scale. A shard it cannot trust - its digest, its manifest, its bytes,
    its facts or its rows - is refused outright, by name and with its remedy: evidence
    about the wrong bytes is worse than absence.

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
    regs, links = discover(None, "stack", seen)
    generic, generic_links = discover(LINK_ROOT, "link", seen)
    regs, links = regs + generic, links + generic_links
    profile_dir = resolve_profile(ws)
    if profile_dir is not None:
        profile_regs, profile_links = discover_package(profile_dir, "profile", seen,
                                                       root_label="profiles")
        regs, links = regs + profile_regs, links + profile_links
    regs, links = order_extractors(regs), order_extractors(links)

    config = {"workspace_name": ws.get("name", ""), "canonical": ws.get("canonical", []),
              "generated": ws.get("generated", []),
              "entrypoint": ws.get("entrypoint", []),
              "alias": ws.get("alias", []),
              "apart": ws.get("apart", []),
              "components": components, "profile": str(profile_dir or "")}
    # Ruling 16, and batch 16's one judgment of a shard: the load reads every shard once and
    # judges it by the verdict `--check-fresh` prints (an activation walk and an input hash -
    # seconds). It loads every current and stale shard - a stale one narrows the run - and
    # refuses, by name and with its remedy, every shard it cannot trust.
    act = activate(regs, components, root, root_exclusions=installer_root_exclusions(si),
                   workspace=components)
    readings, verdicts = shard_verdicts(root, components, regs, act, config)
    merged = ExtractResult()
    present: list[str] = []
    missing: list[str] = []
    mismatched: list[str] = []
    for comp in components:
        name, verdict = comp["name"], verdicts[comp["name"]]
        if verdict.state == "missing":
            missing.append(name)
        elif verdict.state == "refused":
            mismatched.append(name)
            # The digest class keeps its sentence, whose remedy is inside it (ruling 29).
            print(f"snapshot: REFUSING {name} - {verdict.load}"
                  + ("" if verdict.load_words
                     else f"; {verdict.remedy} (build --publish-snapshot)"))
        else:
            merged.extend(readings[name].facts)
            present.append(name)
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

    # T426: pinnable, as the ordinary build's is. The report this writes is committed,
    # so a rebuild that could only mint a fresh identifier could never be reproduced.
    run_id = run_id or uuid.uuid4().hex[:12]
    probe = parser_probe(regs)
    narrowing = load_narrowing(
        missing, {name: verdicts[name] for name in mismatched},
        {name: v.load for name, v in verdicts.items() if v.state == "stale"},
        still_unparsed(readings, verdicts, regs, probe),
        unparsed_parsers(readings, verdicts, regs, probe))
    link_results, link_failures, link_violations = run_link_passes(
        links, merged, components, root, run_id, config, narrowed=bool(narrowing),
        **deployment_scope(act, components, root, regs))
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
        # Plain lines, as a build's: the narrowed line (batch 15b).
        "notes": [narrowing] if narrowing else [],
        "link_failures": link_failures,
        "contract_violations": link_violations,
        # A crashed link pass and each contract violation class, said as a build says them
        # (T618, T637), in the report a repository commits (review round 1).
        "warnings": ([link_failure_line(name, why) for name, why in sorted(link_failures.items())]
                     + violation_headlines(link_violations)),
        "totals": {"nodes": len({n.id for n in merged.nodes}), "edges": len(merged.edges),
                   "unresolved": len(merged.unresolved)},
    }
    (out / "build_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        newline="\n")
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


def shard_verdicts(root: Path, components: list[dict], regs: list[Registration],
                   act: Activation, config: dict, *, keep_facts: bool = True
                   ) -> tuple[dict[str, snapshot_lib.ShardReading],
                              dict[str, freshness.Verdict]]:
    """Every component's reading of its committed shard and its verdict: the ONE judgment of
    a shard (batch 16, ruling 29), so `--check-fresh`, a `--from-snapshot` load and
    `--incremental` never disagree about one. Each shard is read once (`read_shard`), and
    `keep_facts=False` keeps only its rows - all a question about currency needs."""
    readings = {c["name"]: snapshot_lib.read_shard(root, c, keep_facts=keep_facts)
                for c in components}
    verdicts = freshness.judge(
        readings, components, act, requires_closure(regs), input_digests(act, components, root),
        extractor_version(regs), {c["name"]: config_digest(config, c["name"]) for c in components},
        parser_probe(regs), parser_rule(regs), root)
    return readings, verdicts


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

    It prints the one verdict (batch 16): STALE for a stale or missing shard, dependents
    included; REFUSED, with the remedy, for a shard it cannot trust; and NOTE, at exit 0 as at
    exit 2, for a deployment reader whose shard says it had no parser and that still has none.
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

    act = activate(regs, components, root, root_exclusions=installer_root_exclusions(si),
                   workspace=components)
    config = {"workspace_name": ws.get("name", ""), "canonical": ws.get("canonical", []),
              "generated": ws.get("generated", []),
              "entrypoint": ws.get("entrypoint", []),
              "alias": ws.get("alias", []),
              "apart": ws.get("apart", []),
              "components": components, "profile": str(profile_dir or "")}
    readings, verdicts = shard_verdicts(root, components, regs, act, config, keep_facts=False)
    probe = parser_probe(regs)
    unparsed = still_unparsed(readings, verdicts, regs, probe)
    lacked = unparsed_parsers(readings, verdicts, regs, probe)
    fresh = [name for name, v in verdicts.items() if v.state == "current"]
    stale = {name: v.words for name, v in verdicts.items() if v.state in ("stale", "missing")}
    refused = {name: v for name, v in verdicts.items() if v.state == "refused"}
    # One remedy text for the line and the dict: the MCP door serves the dict alone, and a
    # refusal handed over without its remedy is one an agent can only guess at (review B16-3).
    remedies = {name: f"{v.remedy} (build --publish-snapshot)" for name, v in refused.items()}

    for name, why in sorted(stale.items()):
        print(f"freshness: STALE {name} - {why}")
    for name, v in sorted(refused.items()):
        print(f"freshness: REFUSED {name} - {v.words}; {remedies[name]}")
    for name, comps in unparsed.items():
        # Each reader's line with its own way on - one its rows can follow (review B17-2).
        (line,) = parser_group({name: comps}, lacked)
        print(f"freshness: NOTE {line}; {parser_way({name: lacked.get(name, [])})}")
    if not stale and not refused:
        print(f"freshness: {len(fresh)} component(s) current - the committed snapshot "
              f"is the tree")

    return {
        "status": "fresh" if not stale and not refused else "stale",
        "workspace": {"name": _workspace_name(ws)},
        "freshness": {"fresh": sorted(fresh), "stale": stale,
                      "refused": {name: v.words for name, v in refused.items()},
                      "remedies": remedies,
                      # The MCP door serves this dict alone (review B17-3): per reader, its
                      # components and the parsers its rows name ("" for a row naming none it
                      # declares); and one way on for the whole set, where each NOTE line
                      # carries its own reader's (review B17-14).
                      "unparsed": unparsed, "unparsed_parsers": lacked,
                      "unparsed_way": parser_way(lacked) if unparsed else ""},
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
    # A component that read nothing BY DESIGN - every file it holds that an extractor would
    # read belongs to its declared children (T591) - is neither a coverage hole nor a
    # misconfiguration; `(b)`, that nothing claimed it in this run, is a belt (D15a-45).
    by_design = {name: kids for name, kids in sorted(act.by_design.items())
                 if not act.by_component.get(name)}
    unclaimed = sorted(n for n, exts in act.by_component.items()
                       if not exts and n not in by_design)
    notes = [f"{name} reads nothing by design: every file an extractor would read there "
             f"belongs to its declared children ({', '.join(kids)}) - build them with "
             f"--components {','.join(kids)}"
             + (" (this run's --only selects no extractor that reads their files)"
                if name in act.by_design_unread else "")
             for name, kids in by_design.items()]

    if not regs:
        status = "no-extractors"
        warnings.append(
            "no extractors are registered, so this build cannot produce a graph. Every "
            "component was discovered and none was read.")
    elif by_design and not act.active() and not unclaimed:
        # Every component this run selected reads nothing by design (D15a-13). Checked
        # before `no-activation` so the run never reaches `all-failed` or `no-edges`, whose
        # wording would be false; it is not a failing status.
        status = "read-by-children"
    elif not act.active():
        status = "no-activation"
        warnings.append(
            f"{len(regs)} extractor(s) are registered and none matched a single file in any "
            f"of {len(components) - len(by_design)} component(s). That is a misconfiguration, "
            f"not a workspace without couplings — check the globs listed below against the "
            f"trees on disk.")
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
    link_names = {lp.name for lp in links}
    for name, why in sorted(failures.items()):
        warnings.append(link_failure_line(name, why) if name in link_names
                        else f"{name!r} failed and emitted nothing: {why}")

    # A contract violation is a defect in THIS TOOL, not a finding about the workspace, and
    # the two must not be read as the same kind of thing. It therefore rides beside `status`
    # rather than becoming one: the graph can be perfectly `ok` while a row it emitted fails
    # to name what its kind promised. The exit code is what stops it being cosmetic.
    violations = list(violations or [])
    # One headline per class (T637): counted all as rows missing a field, a reader's
    # declaration its rows contradict sent a person to rows that were complete.
    warnings.extend(violation_headlines(violations))

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
        # Names only - no paths (item 120) - so `schema_version` stays 1 (T591, D15a-24).
        "read_by_children": by_design,
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
                        "source_file": r.get("source_file", ""), "line": r.get("line", 0),
                        # A row that stands for several files says how many, or forty
                        # unread files read as one here (B9-17): `off-case-input`, and the
                        # properties extractor's `cross-scope-keys` - any row carrying a
                        # `files` list (T584). A `grammar-unavailable` row carries its
                        # count as a number, which its own `why` states, so it gets none
                        # here - which does not mean one file (review B14-11).
                        **({"files": len(r["files"])} if isinstance(r.get("files"), list)
                           else {})}
                       for r in g.rows]}
            for g in group_unresolved(merged.unresolved)
        ],
        # Plain lines, printed before the `!!` warnings: findings that are not faults.
        "notes": notes,
        "warnings": warnings,
        "contract_violations": violations,
        # T618: beside `contract_violations`, and for the same reason - each fails the build
        # at any status, and a CI that reads only `status` must still learn of it.
        "link_failures": {lp.name: failures[lp.name] for lp in links if lp.name in failures},
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
                f"graph; one spanning a workspace belongs in {SCRATCH_DIR}/, or in the "
                f"{OWN_ARTIFACT_DIR}/ of a hub repository named with --home to commit it (the "
                f"estate install), since otherwise no single repository is the one whose "
                f"facts it is."),
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

    for note in report.get("notes", []):
        print(f"\n{note}")
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
                         f"them from — to a hub's {OWN_ARTIFACT_DIR}/graph when a workspace "
                         f"names one with --home, and to {SCRATCH_DIR}/graph for a workspace "
                         f"with no hub, which is about no single repository. Any other "
                         f"committed tree is refused")
    ap.add_argument("--home", metavar="REPO",
                    help=f"the repository that COMMITS this workspace graph into its own "
                         f"{OWN_ARTIFACT_DIR}/ — the hub the estate install targets. It must be "
                         f"a repository ROOT: a subdirectory would place the graph under the "
                         f"enclosing repository, a tree it is not about, and is refused. "
                         f"Without it a workspace graph is about no single repository, so only "
                         f"{SCRATCH_DIR}/ accepts it and any committed tree is refused; with it "
                         f"the refusal is lifted for exactly this repository's "
                         f"{OWN_ARTIFACT_DIR}/ and left intact for every other")
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
                         "graph answers only for what is present - and a derived fact that "
                         "needed the missing shard, such as a service joined by name to a "
                         "chart there, is derived differently; a shard failing its own "
                         "digest is refused (exit 2). A load that finds no shard at all is "
                         "empty, not a graph, and exits 2 too")
    ap.add_argument("--check-snapshot", action="store_true",
                    help="rebuild == snapshot, per shard: extract fresh and byte-compare "
                         "against each component's committed shard. Drift names the "
                         "component whose files stopped extracting to their committed bytes "
                         "and exits 2 - per-component on purpose, so a drifting extractor "
                         "localises to the tree it read. A tree where no component has a "
                         "committed shard at all examined nothing and exits 2 too - it "
                         "refuses to call a tree it never checked clean")
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
    # T524 and T525, --check-fresh's reasoning for its byte-comparing sibling: narrowed by
    # component it called a tree clean while another drifted, and narrowed by extractor it
    # reported drift on an unchanged tree - whose stated remedy, re-publishing, was T521.
    if args.check_snapshot and (args.only or args.components):
        print("!! --check-snapshot compares every committed shard against a full "
              "extraction; narrowed by --components it calls the tree clean while an "
              "unchecked component drifts, and narrowed by --only every shard reads as "
              "drift, because each was published with every extractor's facts.")
        return 2
    # T521: a shard holds one component's facts from EVERY extractor, and publishing
    # replaces it. Narrowed by --only, one build overwrote every committed shard with
    # that extractor's facts alone, at exit 0 - and a fresh clone's --from-snapshot then
    # rebuilt the partial graph as `status: ok`.
    if args.publish_snapshot and args.only:
        print("!! --publish-snapshot writes each component's committed shard from this "
              "build's facts, and --only would leave only the named extractors' facts in "
              "every one of them. Publish from a full build, or --incremental.")
        return 2
    # T587 (T534 v4): T521's reasoning, by component. A component's facts can depend on
    # what other components' extractors left in the shared cache - a compose file answering
    # another component's env reads - so a narrowed build committed facts no full build
    # has, and the next --check-snapshot blamed that component's unchanged files.
    if args.publish_snapshot and args.components:
        print("!! --publish-snapshot writes each named component's committed shard from this "
              "build's facts, and --components leaves out the components whose files feed "
              "them - a compose file answering another component's env reads - so each "
              "shard would hold facts no full build produces. Publish from a full build, "
              "or --incremental.")
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

    # A single repository's graph is committed from the repository it was inferred from; a
    # workspace graph is committed from no repository UNLESS a hub is named to hold it
    # (--home, the estate install). Either way `own_repo` is the one repository whose
    # OWN_ARTIFACT_DIR may carry this output. check_output_dir's refusal is unchanged and
    # still denies every other committed tree.
    home = Path(args.home).resolve() if args.home else None
    if home is not None and (holder := enclosing_repo(home)) != home:
        # The declared home MUST be a repository ROOT, or it defeats the refusal it lifts: a
        # SUBDIRECTORY would place the graph under the ENCLOSING repository's tree, carrying
        # every component's names into a repository the graph is not about — the exact case
        # the refusal exists to prevent, and one the ordinary chained command reaches with no
        # hand-typed flag by targeting a subdirectory. Checked on the DECLARED home, not the
        # output path, so check_output_dir's own refusal is left untouched.
        where = (f"it is inside {holder}, whose tree this graph is not about"
                 if holder is not None else
                 "nothing at or above it is a git repository")
        print(f"!! --home {args.home} is not a repository root, so it cannot be the hub that "
              f"commits a workspace graph into its own {OWN_ARTIFACT_DIR}/: {where}.")
        return 2
    own_repo = ws.get("inferred_from") or home
    out = Path(args.out) if args.out else (
        Path(own_repo) / OWN_ARTIFACT_DIR / "graph" if own_repo else Path(SCRATCH_DIR) / "graph")
    if refusal := check_output_dir(out, own_repo=own_repo):
        print(f"!! {refusal}")
        return 2

    def _split(v: str | None) -> list[str] | None:
        return [s.strip() for s in v.split(",") if s.strip()] if v else None

    if args.from_snapshot:
        try:
            report = build_from_snapshot(ws, out, run_id=args.run_id)
        except BuildError as exc:
            print(f"!! {exc}")
            return 2
        t = report["totals"]
        s = report["snapshot"]
        # T526: a refused shard is neither loaded nor missing, and left out of the
        # denominator it made a load of 6 of 7 read "loaded 6 of 6". Batch 16: every
        # refusal is counted, not only the digest's.
        refused = s.get("mismatched", [])
        print(f"snapshot: loaded {len(s['loaded'])} of "
              f"{len(s['loaded']) + len(s['missing']) + len(refused)} component shard(s)"
              + (f", {len(refused)} refused" if refused else "")
              + f"; totals: {t['nodes']} nodes, {t['edges']} edges, "
              f"{t['unresolved']} unresolved")
        for note in report.get("notes", []):
            print(f"\n{note}")
        # T618: a load runs the link passes too, and fails as a build does when one crashed
        # or broke its contract - it printed neither and exited 0. Each headlined as a build
        # headlines it (T637, review round 1), then each violation's own line.
        for warning in report.get("warnings", []):
            print(f"\n!! {warning}")
        for problem in report.get("contract_violations", []):
            print(f"  contract: {problem}")
        print(f"\nwrote {out}/graph.jsonl, {out}/unresolved.jsonl, {out}/build_report.json")
        if report.get("link_failures") or report.get("contract_violations"):
            return 2
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
        # On the newline `write_unresolved` joins with, never `splitlines()`, which also ends a
        # line at the U+2028 a row holds raw (T635).
        rows = [json.loads(line) for line in
                (out / "unresolved.jsonl").read_text(encoding="utf-8").split("\n")
                if line.strip()]
        contested = [r for r in rows if r.get("kind") == "contested-service"]
        names = _canonical.drafted(contested)
        changes = _canonical.changed(contested)
        draft = out / "canonical.proposed.toml"
        if not names:
            # Batch 15b (D15b-128): the build regenerates the draft, so a previous one left
            # beside "nothing drafted" points a developer at a decided or unrankable contest.
            if draft.is_file():
                draft.unlink()
            if not contested:
                print("propose-canonical: nothing contested is undeclared — no proposal "
                      "to write")
            else:
                print(f"propose-canonical: 0 of {len(contested)} contested service(s) "
                      f"drafted: every one is held - see their rows")
        else:
            draft.write_text(_canonical.propose(contested), encoding="utf-8", newline="\n")
            left = len(contested) - len(names)
            print(f"propose-canonical: {len(names)} of {len(contested)} contested service(s) "
                  f"drafted -> {draft}"
                  + (f", {len(changes)} of them as a change to an existing block"
                     if changes else "")
                  + (f"; {left} could not be - see their rows" if left else "")
                  + (" (make each change to the block it names; rebuild)"
                     if len(changes) == len(names) else
                     " (fill each blank block and move it into your workspace config; make "
                     "each change to the block it names; rebuild)" if changes else
                     " (every decision slot blank; fill, move into your workspace config, "
                     "rebuild)"))
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
    if report.get("link_failures"):
        # T618, the same rule and the same place: a crashed link pass lost every row it would
        # have emitted, so "no findings" and "the pass crashed" must not share exit 0 - and
        # `--empty-ok`, which excuses an empty graph, never excuses it. The `!!` line above
        # named the pass and the remedy.
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
    if args.check_snapshot and (snapshot.get("drifted") or snapshot.get("unreadable")
                                or snapshot.get("uncomparable")
                                or "refused" in snapshot
                                or snapshot.get("status") == "snapshot-empty"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
