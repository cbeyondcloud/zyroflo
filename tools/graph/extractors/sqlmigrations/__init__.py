"""Which component owns which table, read from the migrations that create it.

Ownership is not a property anyone writes down; it is inferred from *who created the thing*.
That inference is what turns "this write configuration targets `order_events`" into "and
therefore this change reaches the component that owns it", which is the last hop of a chain
that starts in a completely different repository.

## Why creation and not use

Several components read a table and one creates it. Treating a mention as ownership makes
every reader an owner, and the blast radius of a schema change then names everyone and
distinguishes nobody — a graph that returns something for every query and is trustworthy for
none.

## Patterns, deliberately, and their limit

There is no parser here for this language, so this reads `CREATE TABLE` with a pattern. That
is honest for a narrow shape and it is stated rather than hidden: a table created inside a
procedure body, by a templating layer, or by a tool outside version control is not found.
Constructs this does not model — indexes, constraints, views — are skipped rather than
recorded as unresolved, because `unresolved` means "a reference I could not follow", and
widening it to "a statement I do not model" would drown the references in noise.

## Why this package is not named after a migration tool

It was, until a second fixture of a different stack made the cost visible. Nothing in this
file is specific to any one tool or language: it reads `CREATE TABLE`, which is SQL. A
package named after one ecosystem's migration runner tells every adopter of a *different*
one that their migrations are unsupported — and the globs had grown to match, covering that
tool's file conventions and nothing else, so the claim became self-fulfilling. A workspace
whose migrations are `migrations/0001_*.sql` got zero edges and no explanation.

The layering rule permits a stack extractor to name its own technology. The technology here
is SQL, not the runner, and the naming now says so.
"""
from __future__ import annotations

import re
from pathlib import Path

from tools.graph.core.lib import ids, repo_walk
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance, unresolved_row
from tools.graph.core.spi import ExtractContext, RepoRef

#: `CREATE TABLE [IF NOT EXISTS] [schema.]name`, quoted or bare, in any case.
_CREATE_TABLE = re.compile(
    r"\bCREATE\s+(?:(?:GLOBAL|LOCAL)\s+)?(?:TEMP(?:ORARY)?\s+|UNLOGGED\s+)?TABLE\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?(?:(\w+)[\"'`\]]?\.[\"'`\[]?)?(\w+)",
    re.I,
)

#: A line comment, stripped before matching so a commented-out statement is not a creation.
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)

#: This extractor's only unresolved kind: a migration file whose bytes could not be read, so
#: the table it creates is missing from the graph rather than absent from the schema. It
#: promises nothing beyond the common fields, hence the empty tuple.
UNRESOLVED_FIELDS = {"unreadable": ()}


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


def _blank_comments(text: str) -> str:
    """Replace comment bodies with spaces, preserving every newline.

    Preserving them is the point: line numbers are part of every edge's claim, so removing
    the comments outright would shift every statement after the first one.
    """
    def blank(m: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    return _LINE_COMMENT.sub(blank, _BLOCK_COMMENT.sub(blank, text))


def created_tables(text: str) -> list[dict]:
    """Every table this script creates: schema, name, and the line of the statement."""
    stripped = _blank_comments(text)
    out = []
    for m in _CREATE_TABLE.finditer(stripped):
        out.append({"schema": m.group(1) or "", "table": m.group(2),
                    "line": stripped.count("\n", 0, m.start()) + 1})
    return out


class MigrationsExtractor:
    """Table ownership, from the migration that creates each one."""

    name = "sql-migrations"
    #: Two families, and the second is what the first missed. The `V__`/`U__`/`R__` prefixes
    #: are one runner's convention; a numbered file under a `migrations/` directory is the
    #: convention nearly everything else uses, and matching only the first reported a
    #: workspace as having no migrations at all rather than as unsupported.
    INPUT_GLOBS = ("V*__*.sql", "U*__*.sql", "R__*.sql",
                   "db/migration/*.sql", "db/changelog/*.sql",
                   "migrations/*.sql", "db/migrations/*.sql", "[0-9][0-9]*_*.sql")

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        for repo in ctx.repos:
            svc = ids.component_service_id(repo.alias)
            for path in ctx.inputs(repo):
                rel = _rel(repo, path)
                text, read_err = repo_walk.read_text_reporting(path)
                if read_err:
                    res.unresolved.append(unresolved_row(
                        self.name, repo.alias, "unreadable", rel, 0,
                        "this migration could not be read, so any table it creates is missing "
                        "from the graph rather than absent from the schema"))
                    continue
                found = created_tables(text)
                if not found:
                    continue
                res.nodes.append(Node(id=svc, type="svc", props={"component": repo.alias}))
                for t in found:
                    table = ids.table_id(t["table"], t["schema"])
                    res.nodes.append(Node(id=table, type="table",
                                          props={"schema": t["schema"], "name": t["table"]}))
                    res.edges.append(Edge(
                        src=svc, type="DG_OWNS_TABLE", dst=table, source_file=rel,
                        line=t["line"], extractor=self.name, confidence=1.0,
                        provenance=Provenance.INFERRED,
                        props={"migration": path.name, "schema": t["schema"]}))
        return res


EXTRACTORS = (MigrationsExtractor(),)
