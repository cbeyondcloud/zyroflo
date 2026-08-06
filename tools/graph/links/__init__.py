"""Joins no single extractor can make.

An extractor sees one component's files. Some facts are only visible once every component
has been read — whether two deployment packages claim the same service, whether a topic one
component writes is produced by another, whether an endpoint anyone calls is served by
anyone at all. Those live here.

## The contract

Each subpackage exports::

    LINK_PASSES = (SomePass(),)

Every entry needs `name` and `link(ctx, nodes, edges)`. It may also declare `REQUIRES`,
naming passes or extractors that must run first — order is enforced the same way it is for
extractors, because the second pass over a graph sees what the first one decided.

**The lists are live.** A pass may rewrite an entry in place, which is how canonicality and
damping are applied; whatever it returns is added on top. Rewriting rather than dropping is
the point: a non-canonical variant that disappears takes a real coupling out of the graph,
and a variant about to break is exactly what blast radius exists to find.

## What belongs here, and what does not

A pass here may name no technology and no workspace — it works on the meta-model. A join
that only makes sense for a format one organisation invented belongs in that organisation's
profile, which may export `LINK_PASSES` too.
"""
