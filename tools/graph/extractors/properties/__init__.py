"""Key/value configuration files: parse, merge, interpolate, and publish a resolved view.

This extractor emits very few edges of its own. Its real product is the **resolved property
view** it leaves in `ctx.cache`, which several later extractors read: a value referenced in
source code, a host paired with a path, a topic name written in one file and consumed in
another all have to agree on what a key resolves to. Resolving it once is not an
optimisation — two extractors resolving the same key independently is two chances to
resolve it differently, and the graph would then contain both answers with no way to tell
which is wrong.

## Three things a naive reader of these files gets wrong

**One setting, several spellings.** `orders.publish.topic`, `ORDERS_PUBLISH_TOPIC` and
`orders-publish-topic` are one key under relaxed binding. Treated as three, a workspace
grows phantom entities beside the real one — and they look exactly like real ones.

**A placeholder is not a value.** `${ORDER_TOPIC:order-events-v1}` resolves to its default.
Taken literally, the entity downstream ends up named after the placeholder text.

**A placeholder with no default cannot be resolved at build time, and saying so is the whole
job.** `${AUDIT_TOPIC}` is recorded as unresolved and **kept out of the resolved view**, so a
consumer that looks the key up simply does not find it. Absence is the safe failure; a view
that returned the literal `${AUDIT_TOPIC}` would hand every consumer a plausible string.

## The cache contract

`ctx.cache["properties"][<component alias>]` is::

    {
      "values": {
        "<relaxed key>": {
          "value":     "order-events-v1",   # fully interpolated
          "file":      "src/main/resources/application.properties",
          "line":      5,
          "raw":       "${ORDER_TOPIC:order-events-v1}",
          "spellings": {"orders.publish.topic": {"file": ..., "line": 5, "raw": ...}, ...},
        },
      },
      "unresolved": {
        "<relaxed key>": {"raw": "${AUDIT_TOPIC}", "missing": ["AUDIT_TOPIC"],
                          "file": ..., "line": 14},
      },
      "scopes":    {(directory, base): {"values": ..., "unresolved": ...}, ...},
      "ambiguous": {"<relaxed key>": {"values": [...], "scopes": {(dir, base): entry}}},
    }

`scopes` and `ambiguous` are the item-90 layer and exist only on views this extractor
publishes; the compose extractor's per-service views stay `{"values", "unresolved"}`,
because a view that is per-service is already sharded by the thing a scope names.
`lookup` reads the shared two-key core wherever it came from.

**A key two scopes answer differently is in `ambiguous`, not in `values`.** `values` holds
only keys whose declaring scopes agree — which measurement shows is nearly all of them —
so a consumer reading it can never receive one arbitrary answer where two exist. For an
ambiguous key, `lookup` resolves through the asking file: the scope sharing the deepest
directory prefix with the file that is asking answers, a tie between agreeing scopes still
answers, and a tie between disagreeing ones returns None — absence is the safe failure,
and `ambiguity_of` hands the consumer the full disagreement to report.

**Resolution rule for consumers, and it matters for provenance:** look the key up by its
relaxed form, then — if `spellings` holds the exact spelling that was asked for — cite that
spelling's `file`/`line`. A consumer reading `${orders.publish.topic}` should be pointed at
the line declaring *that* spelling, not at whichever declaration happened to win the value.
Provenance that points at the wrong line is wrong in a way nothing notices unless someone
checks the number.

**A consumer must tolerate the cache being absent.** This extractor activates on files, so a
component with no configuration of this kind produces no entry, and a build run with
`--only` may not include it at all. The correct response is an `unresolved` row naming what
could not be looked up — never a crash, and never a guess.

## Deliberately not here

Layering across components — a chart's values or a deployment configmap overriding what the
component declares — is a link pass, not an extractor: it joins facts from two components
and neither one can see the other. Within a component, several files merge in sorted path
order with later declarations winning — deterministic, and honest only where the files are
one scope's layers rather than two scopes' answers, which is what the scope layer below
tells apart.

## The merged view stops where two scopes disagree

Item 89 stopped a component-wide merge from being *reported* as a conflict; item 90 stops
it from being *believed* as a value. The failure it ends: two applications in one component
each declare `server.port`, the flat view keeps whichever file sorts last, and a consumer
gets a confident answer that is wrong for one of them — silently, with provenance pointing
at a real line in a real file, which is the shape of wrongness nothing catches.

Measured before designed, on the same workspace as item 89: of 9,777 resolved keys, 2,751
are declared in more than one scope and **2,133 of those agree** — the flat view is right
about them and keeps them byte-identically. **618 disagree**, and of the 219 graph edges
that resolved a configuration key, 10 cited one of the 618 — **7 of the 10 were wrong**,
every one corrected by asking-file affinity with zero ties. Interpolation was measured to
never cross a scope boundary (zero value drift, zero resolvability drift, both directions,
whole estate), so per-scope resolution changes no answer that was not already arbitrary.

The affinity rule: among the scopes that resolve the key, the one sharing the deepest
directory prefix with the asking file answers. It is the module layout speaking — a
service's sources and its resources live under one subtree, another service's under
another — and where the asking file sits in neither subtree (deepest is a tie between
disagreeing scopes), the honest answer is None plus the disagreement, never a borrowed
value. On the measured estate that honesty retires 714 endpoint prefixes borrowed from
other services' configuration and invents nothing.

## A disagreement is only a conflict inside a shared scope

The merged view above is a lookup convenience. **Reporting** two declarations as disagreeing
is a much stronger claim: it says one of them is what the running system uses and the other
is not. That claim needs the two to be in the same scope, and **the only scope this
repository can prove is a single file** — anything wider is a fact about what a runtime
loads, which is exactly the deployment knowledge the module docstring says is absent here.

This was measured, not reasoned about. Of 2,850 `key-conflict` rows on a real workspace,
**six** were one file declaring a key twice. The rest were files no runtime ever merges:
**64.6%** a bundle and its own locale variant (`labels.properties` beside
`labels_en.properties`), **31.5%** two independent services that happen to share a
repository, **3.5%** `src/main` merged with `src/test` — `server.port` reported as an
ambiguity between `8080` and its own test override. The report was 99.8% artifact.

So `conflicts` compares within a file, and everything wider is summarised by
`cross_scope_disagreements` — **aggregated, and never dropped**. Aggregation is the point: a
per-key row for each of those 2,844 would be the same noise wearing a more accurate name, and
the finding is about the shape of the component rather than about any one key.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from tools.graph.core.lib import ids, props
from tools.graph.core.model import Edge, ExtractResult, Node
from tools.graph.core.spi import ExtractContext, RepoRef

#: Where the resolved view lands in the shared cache.
CACHE_KEY = "properties"

#: `cfg` node kind for this format, from the generic kind vocabulary.
CONFIG_KIND = "properties"


def _shared_depth(a_dir: str, b_dir: str) -> int:
    """How many leading path segments two directories share."""
    n = 0
    for x, y in zip(PurePosixPath(a_dir).parts, PurePosixPath(b_dir).parts):
        if x != y:
            break
        n += 1
    return n


def _nearest(scoped: dict, source_file: str) -> dict | None:
    """The scope entry the asking file selects, or None when the file selects none.

    Ranked by shared directory depth with the asking file — the module layout is the
    evidence: a service's sources and its resources live under one subtree. The deepest
    scope answers; tied scopes that agree still answer (with the first family's entry, in
    sorted order, so the answer is deterministic); tied scopes that disagree answer
    nothing, because picking one would be the arbitrary merge this layer exists to end.
    """
    src_dir = PurePosixPath(source_file).parent.as_posix()
    ranked = sorted(scoped.items(),
                    key=lambda kv: (-_shared_depth(src_dir, kv[0][0]), kv[0]))
    best = _shared_depth(src_dir, ranked[0][0][0])
    top = [entry for (directory, _), entry in ranked
           if _shared_depth(src_dir, directory) == best]
    if len({e["value"] for e in top}) == 1:
        return top[0]
    return None


def lookup(view: dict, key: str, source_file: str = "") -> dict | None:
    """Resolve one key against a component's view, preferring the requested spelling.

    Returns `{"value", "file", "line", "raw"}` or None. None means *not resolvable*, which
    covers "never declared", "declared but its placeholder has no default", and — new with
    the scope layer — "declared differently in scopes the asking file cannot choose
    between". The caller should record an unresolved row either way, and can consult
    `view["unresolved"]` / `ambiguity_of` to say which.

    `source_file` is the file doing the asking. It is what makes an ambiguous key
    answerable: the scope sharing the deepest directory prefix with it answers. Without
    one, an ambiguous key is simply absent — never one arbitrary answer where two exist.
    """
    relaxed = ids.relaxed_key(key)
    entry = view.get("values", {}).get(relaxed)
    if entry is None:
        contested = view.get("ambiguous", {}).get(relaxed)
        if contested is None or not source_file:
            return None
        entry = _nearest(contested["scopes"], source_file)
        if entry is None:
            return None
    spelling = entry.get("spellings", {}).get(key)
    if spelling is not None:
        return {"value": entry["value"], "file": spelling["file"],
                "line": spelling["line"], "raw": spelling["raw"]}
    return {"value": entry["value"], "file": entry["file"],
            "line": entry["line"], "raw": entry["raw"]}


def ambiguity_of(view: dict | None, key: str) -> dict | None:
    """The disagreement behind a failed lookup: `{"values", "scopes"}`, or None.

    For the consumer that just got None and owes its reader a why. "This key resolves to
    nothing" and "this key resolves to three different values and your file selects none
    of them" call for different sentences, and only the second names the real problem.
    """
    if view is None:
        return None
    return view.get("ambiguous", {}).get(ids.relaxed_key(key))


def view_for(cache: dict, alias: str) -> dict | None:
    """This component's resolved view, or None when nothing published one.

    None is a real answer, not an error: this extractor activates on files, so a component
    with no configuration of this kind has no entry, and `--only` can exclude it entirely.
    A consumer that gets None records an unresolved row naming what it could not look up.
    """
    return cache.get(CACHE_KEY, {}).get(alias)


#: A reference that is ENTIRELY one placeholder — the form a framework binds to a field or
#: an annotation argument. A value with text around the placeholder is handled by
#: `props.interpolate` instead; this is about telling "a key" from "a literal".
_WHOLE_PLACEHOLDER = re.compile(r"^\$\{([A-Za-z0-9_.\-]+)(?::-?([^}]*))?\}$")


def resolve_reference(view: dict | None, text: str, source_file: str = "") -> dict | None:
    """Resolve a configured reference — either a literal value or a whole `${key}`.

    Returns `{"value", "key", "file", "line"}`, or None when the text names a key that
    cannot be resolved. `key` is empty for a literal, and `file`/`line` are empty for one
    too: a literal's provenance is wherever it was written, which only the caller knows.

    `source_file` is the file the reference was read from, passed through to `lookup` so a
    key two scopes answer differently is answered by the scope this file belongs to. A
    reference with an inline default keeps it as the fallback: when the configuration
    cannot decide — absent key or unattributable disagreement — the read site's own
    default is the one answer that is nobody's guess.
    """
    stripped = text.strip()
    m = _WHOLE_PLACEHOLDER.match(stripped)
    if m is None:
        return {"value": stripped, "key": "", "file": "", "line": 0}

    key, default = m.group(1), m.group(2)
    hit = lookup(view, key, source_file) if view is not None else None
    if hit is not None:
        return {"value": hit["value"], "key": key, "file": hit["file"], "line": hit["line"]}
    if default is not None:
        return {"value": default, "key": key, "file": "", "line": 0}
    return None


def reference_ambiguity(view: dict | None, text: str) -> dict | None:
    """The disagreement behind a reference that would not resolve, or None.

    `ambiguity_of` for callers holding the raw `${key}` text rather than the key.
    """
    m = _WHOLE_PLACEHOLDER.match(text.strip())
    if m is None:
        return None
    return ambiguity_of(view, m.group(1))


def _declarations(repo: RepoRef, files: list[Path]) -> list[dict]:
    """Every declaration in every file, in a deterministic order.

    Sorted by path so a build is reproducible regardless of how the filesystem enumerates a
    directory, and preserving in-file order so "last declaration wins" means what it says.
    """
    out: list[dict] = []
    for path in sorted(files):
        rel = _rel(repo, path)
        text = props.read_text(path)
        for key, value, line in props.parse_properties(text):
            out.append({"key": key, "relaxed": ids.relaxed_key(key), "raw": value,
                        "file": rel, "line": line})
    return out


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


def _resolve_flat(declarations: list[dict]) -> dict:
    """One scope's declarations into `{"values", "unresolved"}` — the core resolution.

    Two passes, because a value may reference another key: the first builds a raw map so
    interpolation has something to look keys up in, the second expands. `interpolate` handles
    a chain by re-expanding its own output, so `a=${b}` where `b=${c:x}` still lands on `x`.
    """
    by_key: dict[str, list[dict]] = {}
    for d in declarations:
        by_key.setdefault(d["relaxed"], []).append(d)

    # Pass 1: the raw map interpolation resolves against. The LAST declaration wins the
    # value, which is what these files mean by a repeated key.
    raw_map = {k: decls[-1]["raw"] for k, decls in by_key.items()}

    values: dict[str, dict] = {}
    unresolved: dict[str, dict] = {}
    for key, decls in by_key.items():
        winner, first = decls[-1], decls[0]
        expanded, missing = props.interpolate(winner["raw"], raw_map)
        if missing:
            unresolved[key] = {"raw": winner["raw"], "missing": sorted(set(missing)),
                               "file": winner["file"], "line": winner["line"]}
            continue
        values[key] = {
            "value": expanded,
            # Provenance defaults to the FIRST declaration: it is where the setting is
            # introduced, and a later duplicate is usually an override rather than the
            # definition. A consumer that asked for a specific spelling gets that spelling's
            # line instead — see `lookup`.
            "file": first["file"], "line": first["line"], "raw": first["raw"],
            "spellings": {d["key"]: {"file": d["file"], "line": d["line"], "raw": d["raw"]}
                          for d in decls},
        }
    return {"values": values, "unresolved": unresolved}


def resolve(declarations: list[dict]) -> dict:
    """Turn raw declarations into the resolved view described in the module docstring.

    The scope layer is the authority and the flat merge is only its fast path.
    Declarations are partitioned by item 89's family and each scope resolved on its own —
    so interpolation cannot cross a scope boundary either. What `values` may then hold is
    exactly what the scopes support:

    * resolving scopes **agree** → the flat entry, byte-for-byte (measured: every agreeing
      key on the estate — the flat and scope answers never diverged);
    * resolving scopes **disagree** → the key moves to `ambiguous`, whole entry per scope,
      and `lookup` answers through the asking file or not at all;
    * **no scope resolves it** → not a value, whatever the flat merge interpolated it to:
      a value only the cross-scope merge could produce is the merge inventing knowledge,
      and the key's own scope's unresolved record is the truth (measured: zero such keys,
      enforced anyway — a corner that costs nothing to keep honest is kept honest);
    * a scope resolves it and another declares it **unresolvable** (placeholder, no
      default) → the resolving scopes' consensus stands, whichever half of the split the
      flat merge's sort order happened to land on. Declaring "this comes from the
      environment" is deferring to deployment, not disagreeing about the value. Measured:
      zero such keys estate-wide.
    """
    flat = _resolve_flat(declarations)

    present = scope_families(declarations)
    by_family: dict[tuple[str, str], list[dict]] = {}
    for d in declarations:
        by_family.setdefault(family_of(d["file"], present), []).append(d)
    scopes = {fam: _resolve_flat(by_family[fam]) for fam in sorted(by_family)}

    ambiguous: dict[str, dict] = {}
    for key in list(flat["values"]):
        answers = {fam: view["values"][key] for fam, view in scopes.items()
                   if key in view["values"]}
        distinct = {e["value"] for e in answers.values()}
        if not answers:
            # Only cross-scope interpolation can resolve a key no scope resolves. The
            # key's own scope recorded WHY it could not; that record replaces the invention.
            del flat["values"][key]
            fam = next(f for f in scopes if key in scopes[f]["unresolved"])
            flat["unresolved"][key] = scopes[fam]["unresolved"][key]
        elif len(distinct) > 1:
            del flat["values"][key]
            ambiguous[key] = {"values": sorted(distinct), "scopes": answers}
        elif flat["values"][key]["value"] not in distinct:
            # The scopes agree with each other and the flat merge disagrees with them —
            # again only cross-scope interpolation can do this. The scopes are right.
            flat["values"][key] = answers[next(iter(sorted(answers)))]

    # The other half of the resolvability split: the flat winner was the unresolvable
    # declaration, while a scope resolves the key. Same rule, so the answer does not
    # depend on which file happened to sort last.
    for key in list(flat["unresolved"]):
        answers = {fam: view["values"][key] for fam, view in scopes.items()
                   if key in view["values"]}
        if not answers:
            continue
        distinct = {e["value"] for e in answers.values()}
        del flat["unresolved"][key]
        if len(distinct) > 1:
            ambiguous[key] = {"values": sorted(distinct), "scopes": answers}
        else:
            flat["values"][key] = answers[next(iter(sorted(answers)))]

    return {**flat, "scopes": scopes, "ambiguous": ambiguous}


#: A locale-tagged sibling under the `ResourceBundle` scheme: `labels_en`, `labels_en_GB`.
_LOCALE_TAG = re.compile(r"^(?P<base>.+)_(?P<tag>[a-z]{2}(?:_[A-Z]{2})?)$")
#: A profile-tagged sibling under Spring's scheme: `application-dev`, `application-test`.
_PROFILE_TAG = re.compile(r"^(?P<base>.+)-(?P<tag>[A-Za-z0-9]+)$")


def _dir_and_stem(path: str) -> tuple[str, str]:
    p = PurePosixPath(path)
    return p.parent.as_posix(), p.stem


def family_of(path: str, present: set[tuple[str, str]]) -> tuple[str, str]:
    """The `(directory, base)` family a file belongs to — its own stem when it heads one.

    A tagged name only joins a family whose base file is **actually present beside it**, and
    that check is load-bearing rather than defensive. `common-labels.properties` parses just
    as happily as `common` with a profile called `labels`, and a first cut of this code made
    exactly that mistake — silently, because the wrong answer is still a plausible family
    name. Only the directory listing can tell a profile from a hyphen, so it is consulted.
    """
    directory, stem = _dir_and_stem(path)
    for tag in (_LOCALE_TAG, _PROFILE_TAG):
        m = tag.match(stem)
        if m and (directory, m.group("base")) in present:
            return directory, m.group("base")
    return directory, stem


def scope_families(declarations: list[dict]) -> set[tuple[str, str]]:
    """Every `(directory, stem)` that exists, so `family_of` can check a base file is real."""
    return {_dir_and_stem(d["file"]) for d in declarations}


def cross_scope_reason(files: list[str], present: set[tuple[str, str]]) -> str:
    """Why two files that never share a scope came to declare the same key.

    One of `UNRESOLVED_PATTERNS['cross-scope-keys']`. The distinction earns its place because
    the three call for different responses: a locale variant is working as designed and wants
    no action at all, two files in one directory may be a genuine split, and two directories
    are usually two independent applications that only share a repository.
    """
    families = {family_of(f, present) for f in files}
    if len(families) == 1:
        return "locale-or-profile-variant"
    if len({directory for directory, _ in families}) == 1:
        return "same-directory"
    return "separate-directories"


def _effective(decls: list[dict], raw_map: dict[str, str]) -> dict[str, str]:
    """Each file's own answer for one key: its LAST declaration, since last wins in a file."""
    per_file: dict[str, dict] = {}
    for d in decls:
        per_file[d["file"]] = d
    return {f: props.interpolate(d["raw"], raw_map)[0] for f, d in per_file.items()}


def conflicts(view: dict, declarations: list[dict]) -> list[dict]:
    """Keys whose several spellings resolve to DIFFERENT values **within one file**.

    Not an error and not dropped: one of them is what the running system uses, and which one
    is a question about deployment that no file here answers. It is surfaced so a human sees
    the disagreement rather than a graph that silently picked a side.

    Scoped to a single file deliberately — see the module docstring. Across files the claim
    "one of these is what runs" is unsupported, and on a real workspace it was wrong 2,844
    times out of 2,850. Those are summarised by `cross_scope_disagreements` instead.
    """
    by_key: dict[str, list[dict]] = {}
    for d in declarations:
        by_key.setdefault(d["relaxed"], []).append(d)

    raw_map = {k: decls[-1]["raw"] for k, decls in by_key.items()}
    out = []
    for key, decls in sorted(by_key.items()):
        # An ambiguous key left `values`, but its within-file conflicts are still real —
        # the guard only skips keys nothing could resolve at all.
        if len(decls) < 2 or (key not in view["values"]
                              and key not in view.get("ambiguous", {})):
            continue
        per_file: dict[str, list[dict]] = {}
        for d in decls:
            per_file.setdefault(d["file"], []).append(d)
        for _, group in sorted(per_file.items()):
            if len(group) < 2:
                continue
            seen = {props.interpolate(d["raw"], raw_map)[0] for d in group}
            if len(seen) > 1:
                out.append({"key": key, "values": sorted(seen),
                            "declarations": [{"key": d["key"], "file": d["file"],
                                              "line": d["line"]} for d in group]})
    return out


def cross_scope_disagreements(view: dict, declarations: list[dict]) -> list[dict]:
    """Keys that resolve differently in files that share no scope — one row per reason.

    Aggregated on purpose. These are real observations and they are kept, but each one is a
    statement about the component's shape ("this component holds several independent
    configuration scopes and they overlap") rather than about a key, and emitting them
    per-key reproduces the exact flood this scoping exists to end.

    Each row carries a `sample` so the summary can be checked rather than believed — a count
    with nothing to look at is the kind of number a reader learns to scroll past.
    """
    by_key: dict[str, list[dict]] = {}
    for d in declarations:
        by_key.setdefault(d["relaxed"], []).append(d)

    raw_map = {k: decls[-1]["raw"] for k, decls in by_key.items()}
    present = scope_families(declarations)

    buckets: dict[str, dict] = {}
    for key, decls in sorted(by_key.items()):
        # Ambiguous keys are exactly the ones this report exists to count — they are no
        # longer in `values`, and skipping them here would drop the observation.
        if len(decls) < 2 or (key not in view["values"]
                              and key not in view.get("ambiguous", {})):
            continue
        per_file = _effective(decls, raw_map)
        if len(per_file) < 2 or len(set(per_file.values())) < 2:
            continue
        files = sorted(per_file)
        reason = cross_scope_reason(files, present)
        bucket = buckets.setdefault(reason, {"reason": reason, "keys": set(), "files": set(),
                                             "sample": None})
        bucket["keys"].add(key)
        bucket["files"].update(files)
        if bucket["sample"] is None:
            bucket["sample"] = {"key": key, "files": files[:2],
                                "values": sorted(set(per_file.values()))[:2]}
    return [{"reason": b["reason"], "keys": len(b["keys"]), "files": sorted(b["files"]),
             "sample": b["sample"]}
            for b in sorted(buckets.values(), key=lambda b: b["reason"])]



#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared HERE rather than in core: a central table would make core learn every
#: extractor's vocabulary, so adding a stack would force a core edit. The pipeline
#: checks these after every run and fails the build when a row does not carry them.
UNRESOLVED_FIELDS = {
    "key-conflict": ('key', 'values'),
    "placeholder": ('key', 'raw', 'missing'),
    "cross-scope-keys": ('keys', 'files', 'sample'),
}

#: The shapes a `key-conflict` comes in. Declared here for the same reason as the fields
#: above — core groups rows by the pattern they name and never learns what the names mean —
#: and declared *at all* because a kind with several shapes and no vocabulary collapses into
#: one bucket that reads as a single finding.
#:
#: **Measured before it was written, and the measurement overturned the assumption.** The
#: plan recorded the dominant shape as a production address against a `localhost` default.
#: On a real workspace that is 2.8% of the rows. **81% are `value-restates-key`** — a
#: localisation bundle where a label sits beside its own untranslated key, which is not a
#: deployment ambiguity at all. Naming the shapes is what made that visible; a vocabulary
#: guessed from the plan would have filed 2,206 rows under the wrong cause and looked tidy.
#:
#: **Then the grouping overturned the vocabulary as well.** With the shapes countable it was
#: possible to ask where the two declarations came from, and 99.8% of them had never shared a
#: scope: a bundle beside its own locale variant, two services sharing a repository, a file
#: beside its own test override. `value-restates-key` was not a shape of configuration
#: conflict at all — it was the signature of a merge that should never have happened. The
#: shapes below now describe only the disagreements that survive scoping.
UNRESOLVED_PATTERNS = {
    "key-conflict": ("value-restates-key", "local-vs-remote", "all-local",
                     "differing-values"),
    "cross-scope-keys": ("locale-or-profile-variant", "same-directory",
                         "separate-directories"),
}

#: An address that only means anything on the machine that reads it.
_LOCAL = re.compile(r"localhost|127\.0\.0\.1|0\.0\.0\.0|::1", re.I)


def _same_token(a: str, b: str) -> bool:
    """Whether two strings are one token differing only in case and separators.

    The comparison is deliberately as loose as the *relaxed binding* that created the
    conflict: `a.b.host`, `A_B_HOST` and `a-b-host` are one key to this extractor, so a value
    that restates the key in any of those spellings restates it.
    """
    strip = re.compile(r"[^a-z0-9]")
    return strip.sub("", a.lower()) == strip.sub("", b.lower())


def conflict_pattern(key: str, values: list[str]) -> str:
    """Which shape of disagreement this is — one of `UNRESOLVED_PATTERNS['key-conflict']`.

    Order matters and is not arbitrary. `value-restates-key` is tested first because such a
    row is not a configuration conflict at all: it is a message bundle, where the convention
    for an untranslated string is to repeat its key. Classifying it by its addresses instead
    would put a translation gap in the same bucket as a service pointing at two hosts.
    """
    vals = [str(v) for v in values]
    if any(_same_token(v, key) for v in vals):
        return "value-restates-key"
    local = sum(1 for v in vals if _LOCAL.search(v))
    if local == len(vals):
        return "all-local"
    if local:
        return "local-vs-remote"
    return "differing-values"

class PropertiesExtractor:
    """Reads key/value configuration files and publishes the resolved view."""

    name = "properties"
    INPUT_GLOBS = ("*.properties",)

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        cache: dict[str, Any] = ctx.cache.setdefault(CACHE_KEY, {})

        for repo in ctx.repos:
            files = ctx.inputs(repo)
            decls = _declarations(repo, files)
            view = resolve(decls)
            cache[repo.alias] = view

            for path in files:
                rel = _rel(repo, path)
                node_id = ids.config_id(repo.alias, rel)
                res.nodes.append(Node(id=node_id, type="cfg",
                                      props={"kind": CONFIG_KIND, "path": rel,
                                             "component": repo.alias}))
                res.edges.append(Edge(src=node_id, type="DG_IN_REPO",
                                      dst=ids.repo_id(repo.alias), source_file=rel, line=0,
                                      extractor=self.name))

            for key, info in sorted(view["unresolved"].items()):
                res.unresolved.append({
                    "extractor": self.name, "component": repo.alias, "kind": "placeholder",
                    "key": key, "raw": info["raw"], "missing": info["missing"],
                    "source_file": info["file"], "line": info["line"],
                    "why": (f"{', '.join(info['missing'])} has no default and nothing in this "
                            f"component declares it, so the value cannot be known from the "
                            f"repository alone"),
                })
            for c in conflicts(view, decls):
                res.unresolved.append({
                    "extractor": self.name, "component": repo.alias, "kind": "key-conflict",
                    "key": c["key"], "values": c["values"],
                    "pattern": conflict_pattern(c["key"], c["values"]),
                    "source_file": c["declarations"][0]["file"],
                    "line": c["declarations"][0]["line"],
                    "why": (f"{len(c['declarations'])} spellings of one key resolve to "
                            f"different values ({', '.join(c['values'])}) in one file; which "
                            f"one applies is a deployment fact this repository does not "
                            f"contain"),
                })
            for s in cross_scope_disagreements(view, decls):
                sample = s["sample"]
                res.unresolved.append({
                    "extractor": self.name, "component": repo.alias,
                    "kind": "cross-scope-keys", "pattern": s["reason"],
                    "keys": s["keys"], "files": s["files"], "sample": sample,
                    "source_file": sample["files"][0], "line": 0,
                    "why": (f"{s['keys']} key(s) resolve differently across {len(s['files'])} "
                            f"file(s) that share no scope ({s['reason']}), e.g. "
                            f"{sample['key']} is {' and '.join(sample['values'])}; nothing in "
                            f"this repository says a runtime merges them, so this is reported "
                            f"as overlap rather than as a conflict"),
                })
        return res


EXTRACTORS = (PropertiesExtractor(),)
