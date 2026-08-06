# `orderflow` — the synthetic fixture workspace

Six components carrying one six-hop cross-component chain, eight decoys, and one inert
negative control. It is what the M0 kill gate builds against, and it was **committed
before any extractor existed**.

## What this is for, and what it cannot do

It proves the *mechanism*: that extraction resolves a real chain through real indirection
and does not invent edges. It **cannot** prove recall against reality, because the fixture's
author chose what reality looks like here. That is why item 76 — a developer-run build
against their own workspace, output to `.tmp/`, with a dated record — is a separate and
equally required half of the same gate.

**Saying either half substitutes for the other is the failure this pair exists to prevent.**

## The rules, and why each one exists

1. **`EXPECTED.md` is hand-written and lands first.** Never regenerate it. An expectation
   produced by running the tool asserts only that the tool is unchanged.
2. **A failing golden test means the extractor is wrong, until proven otherwise.** If the
   expectation really is wrong, fix it in its own commit with the reasoning — never in the
   same commit as the code that made it fail.
3. **Transcribed, not invented.** The layout, indirection depth, and inconsistent key
   spellings are copied from a real chain with every identifier substituted. This matters
   more than it sounds: *the reason a real chain is hard is exactly the mess an inventor
   smooths out*. An invented fixture is a description of what its author expected, and an
   extractor tested only against that passes by agreeing with itself.
4. **No file over 40 lines.** Below that the chain is too short to be cross-component;
   above it nobody re-reads the file when a test fails, and the fixture rots into a
   snapshot.
5. **Every decoy names the contract it exercises.** A decoy with no named contract is
   decoration — when it fails, nobody knows what it was defending.
6. **No leak-guard exemption.** If a name here needed one, it would be too close to a real
   one.

## Layout

```
orders-api/       Java/Maven · exposes the endpoint, publishes the topic   (D2 D3 D5 D6 D8)
orders-worker/    Java/Maven · consumes the topic, owns the table          (D7)
orders-shared/    Java/Maven · the library both services depend on
web-console/      npm        · the inbound caller                          (D1)
deploy-charts/    helm       · the host map, plus the canonical collision  (D4)
legacy-reports/   Java/Maven · P5 — the NEGATIVE CONTROL, participates in nothing
```

The chain, the decoys and the counts are specified in [`EXPECTED.md`](EXPECTED.md).
`workspace.toml` is the fixture's own config, so the golden build reaches these components
by exactly the route an adopter's build does.

## Editing

Adding a component or a decoy means adding it to `EXPECTED.md` **in the same commit** —
a fixture that has drifted ahead of its answer key is worse than no fixture, because the
gate still passes while no longer describing what it checks.
