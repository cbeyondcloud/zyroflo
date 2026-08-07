# `pantry` — the second fixture workspace

A Python / ASGI / compose workspace. Synthetic throughout: invented component names,
invented service names, invented keys.

**Read [`EXPECTED.md`](EXPECTED.md) first.** It is the hand-written answer key, and it
landed before any of this fixture's extractors existed.

## Why a second fixture exists at all

The first one (`orderflow`) proves the chain resolves. This one proves **the core did not
have to change to make a different stack resolve** — which the first one cannot prove about
itself, because code and fixture were written together and one can always be bent to fit
the other.

So the gate here has two halves, and the second is the one that matters:

1. the chain is green, and
2. `tools/graph/core/` is **byte-identical**.

A green chain bought with a core edit is a failure wearing a pass's clothes: it means the
SPI boundary was drawn around one stack, and every adopter after the first pays for it.

## What it deliberately does differently

| | `orderflow` | `pantry` |
|---|---|---|
| Where a service's config lives | in the component that reads it | in the **deployment layer**, outside it |
| Interpolation grammar | `${VAR:default}` | `${VAR:-default}` |
| Endpoint assembly | three annotations concatenated | constructor argument + decorator argument |
| Deployment technology | chart tree | compose files |
| Profile | required — one invented format | **none, and that is a claim** |

The last row is the compact version of the whole argument: a workspace that invents no
formats should need no profile, and a second fixture that quietly needed one would prove
the opposite of what it was built for.
