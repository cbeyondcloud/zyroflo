"""Formats one organisation invented, and the identity decisions only it can make.

This is the layer nobody inherits. Everything reusable — a build system, a message broker, a
web framework, a migration convention — lives in `extractors/<technology>/` and any adopter
using that technology gets it for free. What is left here is the irreducible remainder: a
configuration format someone wrote for themselves, and the curated decisions about identity
that no scanner can reach.

## Selected, never activated

A profile is chosen by the workspace, with `profile = "<name>"` in its config. It is not
scanned into every build the way the stack extractors are, and the difference is not
tidiness: another organisation's file shapes read into your graph would be wrong *often
enough to be believed*, since the shapes are plausible and the names are yours.

A bare name resolves here. A value containing a separator is a path relative to the
workspace config — which is where a real workspace's profile belongs, since it is that
developer's code and not this repository's. **The only profile committed here is the
fixture's**, because the fixture must build by exactly the route an adopter's build takes.

## The contract

Same as a stack extractor's: export `EXTRACTORS`, `LINK_PASSES`, or both. A profile may name
its own workspace — that is what the layer is for — and it shares one name namespace with
every other layer, so it cannot shadow an extractor that already exists.
"""
