# The core baseline — every approved change to `tools/graph/core/`, and why

`test_the_core_was_not_touched` (item 19's gate) asserts that `tools/graph/core/` is
byte-identical to the **last entry below**. The gate exists because a second stack forcing a
core edit would mean the extraction SPI had been shaped around the first one — and that has to
arrive as a failing test, not as a paragraph nobody re-reads.

**Crossing it is allowed. Crossing it silently is not.** When core legitimately changes, the
change lands in its own commit — leaving this gate RED, deliberately — and a *separate* commit
appends a line here with the reason. That is the same discipline the answer keys follow: an
expectation is never edited in the commit that made it fail, because the two together are
indistinguishable from bending the expectation to fit a bug.

A red gate is therefore a question, not a verdict: *was this core change intended, and is it
written down?*

Format: one entry per approved change, oldest first. The test reads the **last** SHA.

```
<full-sha>  <date>  <one-line reason>
```

---

e145be46e88e8108f3c7b1c91769ff24fde48059  2026-08-07  BASELINE — the pantry answer key lands; item 19's claim is that a second stack needs no core edit, and it did not.
7ea875562f5f82abe3efcb3b3a8c1a4cc955571b  2026-08-08  repo_walk: a RELATIVE root returned zero files at exit 0 through the fd accelerator. NOT stack-driven — found by the POSIX gate run comparing backends deliberately, and it reproduces on Windows. Third instance of this module's cardinal failure; see docs/m0/posix-gate-record.md.
d727cdf608f080a0f36e25adda87322af8c4e7c7  2026-08-10  props.load_yaml_all: an unparsable file and an empty one both returned []. Found by the item-76 real-workspace run, where it had already silenced helm-configmaps over a whole chart tree at 0 unresolved; see docs/m0/real-workspace-gate-record.md.
46317783e463ffca584b724364bd6a1e42f38fa4  2026-08-11  model: UNRESOLVED_COMMON + unresolved_row + check_unresolved. The MECHANISM only, never the vocabulary — per-kind fields are declared by the extractor that emits them, so adding a stack still forces no core edit. Found by the item-76 run, where 266 rows named what was unreadable without naming it and 206 named a contested service without naming a claimant; 530 violations at first measurement, 0 after (item 85).
d8ff8f31d3533f0d125cd2fcfdb0c037f3006a52  2026-08-12  model: group_unresolved + check_patterns + PATTERN_FIELD. The MECHANISM only — core groups rows by the pattern they name and never learns what a pattern means, so the vocabulary stays with the emitting extractor and adding a stack still forces no core edit. Sibling of item 85's check_unresolved and kept beside it deliberately: one contract, one home. Found by item 76, where 2,721 of 4,066 rows were one kind reported as though it were 2,721 decisions (item 84).
9b674cb15477575365a935e5b4da9284e403f6d3  2026-08-12  srcfacts._MODULES: each entry now names its module AND its factory, because tree_sitter_typescript publishes language_typescript()/language_tsx() and no language() — a shape the one-module-one-grammar table could not hold. Not vocabulary: adding a language still touches only this table, and extractors/_lang needed no change. The reason is portability, not coverage — the pack has no Windows wheels, so typescript/tsx resolved on Linux and not here and two developers building one commit got graphs differing by 782 edges, which item 13's committed snapshot would turn into churn by author (item 88).
