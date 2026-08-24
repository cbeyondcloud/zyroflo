"""`EXPECTED.md`'s dependent-build closure, in machine-readable form. Not a
test module.

One definition, imported by both halves of item 39's gate — the golden test
that says the closure resolves, and the seam cuts that say it is being READ.
Two copies would drift, and a drifted copy weakens the gate silently.

**This file is a transcription, never a source.** `EXPECTED.md` is the answer
key, written by reading the fixture's manifests before the engine that computes
this existed. Every row here is copied from its table, line numbers included.
If the two disagree, the answer key is right — and changing a row here to make
a test pass is the same act as editing the answer key, done somewhere nobody is
looking for it.
"""
from __future__ import annotations

#: The component whose change starts the closure.
PUBLISHER = "orders-shared"

#: The module it publishes (`orders-shared/pom.xml:2-3`).
PUBLISHED = "mod:com.example.orderflow:orders-shared"

#: (component, manifest, line, build system) — one row per dependent, from
#: `EXPECTED.md`'s table. THREE manifests in TWO build systems, which is the
#: property that makes this cross-repo rather than cross-module.
DEPENDENTS: list[tuple[str, str, int, str]] = [
    ("orders-api", "pom.xml", 6, "maven"),
    ("orders-worker", "pom.xml", 6, "maven"),
    ("orders-notifier", "build.gradle", 5, "gradle"),
]

#: Everything a change to the publisher must build and test.
CLOSURE = frozenset({PUBLISHER} | {name for name, _, _, _ in DEPENDENTS})

#: Components the closure must NOT reach. As much of the claim as `CLOSURE` is:
#: a closure that reaches everything has computed nothing.
NOT_REACHED = ("legacy-reports", "web-console", "deploy-charts")

#: A component declaring a module it publishes ITSELF (`build.gradle:6` against
#: `notifier-core/build.gradle`). It must not become its own dependent.
SELF_PUBLISHED = ("orders-notifier", "mod:com.example.orderflow:notifier-core")

#: Coordinates consumed here and published by nobody here. They must drop out
#: because the join finds no publisher, never because a vendor list names them.
THIRD_PARTY = ("mod:org.apache.kafka:kafka-clients",
               "mod:org.springframework.kafka:spring-kafka")

#: The seams. Each renames ONE value on ONE line, in a copy — never a deletion,
#: because any pipeline notices a missing file and almost none of them are
#: reading it. After the cut, `drops` must leave the closure and everything
#: else must stay, for that seam's own reason.
SEAMS: list[tuple[str, str, str, str, str]] = [
    ("orders-api's maven coordinate", "orders-api/pom.xml",
     "<artifactId>orders-shared</artifactId>",
     "<artifactId>orders-elsewhere</artifactId>", "orders-api"),
    ("orders-worker's maven coordinate", "orders-worker/pom.xml",
     "<artifactId>orders-shared</artifactId>",
     "<artifactId>orders-elsewhere</artifactId>", "orders-worker"),
    ("orders-notifier's gradle coordinate", "orders-notifier/build.gradle",
     "implementation 'com.example.orderflow:orders-shared:2.1.0'",
     "implementation 'com.example.orderflow:orders-elsewhere:2.1.0'",
     "orders-notifier"),
]

#: Changes that must alter the closure NOT AT ALL. Without these the suite
#: would be measuring sensitivity and calling it precision: a test that only
#: asserts "the mutation broke it" passes for an engine so brittle that every
#: change breaks everything.
CONTROLS: list[tuple[str, str, str, str]] = [
    ("a version bump on the shared library", "orders-shared/pom.xml",
     "<version>2.1.0</version>", "<version>2.2.0</version>"),
    ("renaming the inert component's artifact", "legacy-reports/pom.xml",
     "<artifactId>legacy-reports</artifactId>",
     "<artifactId>legacy-reporting</artifactId>"),
]
