"""Reusable, technology-scoped extractors — the layer most adopters never write.

An extractor here knows exactly ONE technology and works on **any** repository using it.
That is the whole reason this layer exists separately from `profiles/`: filed under one
organisation's profile, a build-manifest or a message-broker extractor would have to be
rewritten by the next adopter running the same technology, which is a rewrite of code that
should have been free.

## The contract

Each subpackage is one technology. Its `__init__.py` MUST export::

    EXTRACTORS = (SomeExtractor(), AnotherExtractor())

Every entry needs `name` (unique across the whole registry), `INPUT_GLOBS` (non-empty,
repo-relative) and `extract(ctx)`. All three are checked at registration and a violation is
a hard error — an extractor that fails to register produces a whole class of missing edges,
and a missing edge class looks exactly like a codebase that does not have those couplings.

## What may be named here, and what may not

A module in this directory MAY name its own technology — that is what it is for, and the
layer test permits it. It may NEVER name a workspace, an organisation, or a repository.
Anything that can only be understood by knowing one organisation's inventions belongs in
`profiles/<name>/` instead.

## Activation

Registration is not activation. An extractor runs against a component only when its
`INPUT_GLOBS` match real files there — never because a component's detected stack label
says so. A component's stack is a single label chosen by first match, but a component
routinely needs several extractors at once, and a tree that holds only deployment charts
detects as `unknown` while still carrying facts a chart extractor must read. Gating on the
label would drop those silently.
"""
