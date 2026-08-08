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
