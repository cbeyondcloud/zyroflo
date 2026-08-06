"""Which tests cover which service, and which endpoints and topics they name.

This answers the question a developer asks immediately after "what breaks?" — namely "what
do I edit?" — and it is the one extractor whose value is measured by what it does NOT emit.

## A test that names an endpoint is not a caller

A test source mentions paths and topics constantly: in a comment explaining the case, in a
mock stub, in an assertion. None of them is a call. An extractor that scans a test file the
way it scans production code invents an inbound edge to that endpoint, and once invented the
phantom appears in every blast radius forever — arriving as a caller nobody can find, which
is worse than a missing edge because someone will go looking for it.

So a mention in a test becomes a `DG_TESTS` edge at reduced confidence. Never a call.

## Only string literals count, and that is what parsing buys

The comment above a test case names the same path as the mock inside it. Read as text, that
is two mentions and two edges; read as a parse tree it is one literal and one comment, and
only the literal is a claim about behaviour. This is precisely the regex-miss class the
source-fact layer exists to retire, in the direction that invents rather than misses.

## Coverage is reported, not implied

Tests are found by naming convention, and a project that names them differently is missed.
The convention is written down in `repo_walk.looks_like_test`, the files claimed are visible
in the build report, and a source this cannot parse produces an unresolved row. What must
never happen is a component with tests reporting none of them and nothing saying so.
"""
from __future__ import annotations

from pathlib import Path

from tools.graph.core.lib import ids, repo_walk, srcfacts
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance
from tools.graph.core.spi import ExtractContext, RepoRef

#: Filename shapes that conventionally mean a test source. Kept in step with
#: `repo_walk.looks_like_test`, which decides the same question for the OTHER extractors —
#: they skip what this one claims, and a gap between the two would leave a file read by
#: nobody or, worse, read by both as different things.
INPUT_GLOBS = (
    "*Test.java", "*Tests.java", "*IT.java", "*TestCase.java", "src/test/*",
    "*.test.js", "*.spec.js", "*.test.jsx", "*.spec.jsx",
    "*.test.ts", "*.spec.ts", "*.test.tsx", "*.spec.tsx",
    "test_*.py", "*_test.py",
)

#: Extensions this can parse, and the grammar each needs.
LANGUAGES = {".java": "java", ".py": "python"}

#: How a test suite is labelled, by extension. Descriptive only — nothing branches on it.
KINDS = {".java": "junit", ".py": "pytest", ".js": "jest", ".jsx": "jest",
         ".ts": "jest", ".tsx": "jest"}

def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


class TestsExtractor:
    """Test suites, what they cover, and what they name."""

    name = "tests"
    INPUT_GLOBS = INPUT_GLOBS

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        for repo in ctx.repos:
            svc = ids.component_service_id(repo.alias)
            for path in ctx.inputs(repo):
                rel = _rel(repo, path)
                suffix = path.suffix.lower()
                if suffix not in KINDS:
                    continue                      # a resource under a test tree, not a suite
                tid = ids.test_id(repo.alias, rel)
                res.nodes.append(Node(id=tid, type="test",
                                      props={"kind": KINDS[suffix], "path": rel,
                                             "component": repo.alias}))
                res.nodes.append(Node(id=svc, type="svc", props={"component": repo.alias}))
                res.edges.append(Edge(src=tid, type="DG_TESTS", dst=svc, source_file=rel,
                                      line=0, extractor=self.name, confidence=1.0))
                self._literals(res, repo, rel, path, tid, suffix)
        return res

    def _literals(self, res: ExtractResult, repo: RepoRef, rel: str, path: Path,
                  tid: str, suffix: str) -> None:
        language = LANGUAGES.get(suffix)
        if language is None or not srcfacts.available(language):
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias,
                "kind": "grammar-unavailable", "source_file": rel, "line": 0,
                "why": (f"no parser is installed for {suffix} sources, so this suite is "
                        f"recorded as covering its component but the endpoints and topics it "
                        f"names are not. Reading it as text instead would count a path in a "
                        f"comment as a claim about behaviour"),
            })
            return
        try:
            parsed = srcfacts.parse(language, repo_walk.read_text(path))
        except Exception:
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias, "kind": "unparsable",
                "source_file": rel, "line": 0,
                "why": "the parser could not read this suite, so what it names is unknown",
            })
            return

        for cap in parsed.string_literals():
            if not ids.looks_like_path(cap.text):
                continue
            ep = ids.endpoint_id(cap.text)
            res.nodes.append(Node(id=ep, type="ep"))
            # DG_TESTS, never DG_CALLS. A test naming an endpoint exercises it; it does not
            # depend on it the way a caller does, and recording it as a call would put a
            # phantom consumer into every blast radius that endpoint ever appears in.
            res.edges.append(Edge(src=tid, type="DG_TESTS", dst=ep, source_file=rel,
                                  line=cap.line, extractor=self.name, confidence=0.8,
                                  provenance=Provenance.AMBIGUOUS,
                                  props={"literal": cap.text}))


EXTRACTORS = (TestsExtractor(),)
