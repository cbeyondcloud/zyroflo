# M0 kill-gate condition (2) — extraction against a real multi-repo workspace

**Date:** 2026-08-10 · **Result: PASS, with four defects found** · **Repo at:** `a8f18fc`

## What this condition asks, and why the fixture cannot answer it

The committed fixture (item 75) proves the mechanism works and keeps working. It cannot prove
**recall against reality**, because the fixture's author chose what reality looks like. This
half runs the same builder against a real workspace of many independently-developed
repositories that nobody wrote for this tool, and records what it found, what it could not
resolve, and what it got wrong.

Both halves are required and neither substitutes for the other. That was written down before
this run, and this run is the reason it was worth writing down: **the single most important
finding below is a whole extractor that reads nothing at all on real input and is green on the
fixture.**

> **Nothing from the workspace is recorded here.** No component names, paths, remotes,
> hostnames or service names — those are the adopter's data and never enter this repository
> (Rev 12(c)). What follows is evidence about the *extraction mechanism*. Output went to
> `.tmp/`, which is gitignored, and the workspace config that pointed at it is gitignored too;
> both were verified with `git check-ignore` before the run rather than after.

## What ran

The builder, once, with a filled `sdlc-workspace.toml` naming only `root` — the template's own
instruction for a first pass, since overrides written before the first scan are written blind.
No profile. No overrides. No `[[canonical]]` blocks.

**Result: exit 0, status `ok`.** 25,537 nodes · 17,545 edges · 4,066 unresolved · 17 stack
extractors and 1 link pass, across two dozen-plus repositories spanning Java/Maven/Spring,
Kafka, SQL migrations, Helm, Docker Compose, Python and several JavaScript UI trees.

**It did not crash, hang, or abort on anything.** That is worth stating plainly because it is
the thing most likely to have gone wrong and did not: real trees carry unreadable files,
mixed encodings, generated output and directories with spaces, and the pipeline absorbed all
of it and kept going. The accelerator path was exercised for real here — `rg`/`fd` are present
on this machine and absent on CI, so this is the only run in which the fd backend has ever
been given a large real tree rather than a fixture.

## Finding 1 — an extractor reads NOTHING on any real chart, and says nothing about it

**`helm-configmaps` produced 0 nodes, 0 edges and 0 unresolved rows** against a chart tree
containing 100 `templates/` directories and 31 genuine ConfigMap documents.

It was not misconfigured and its globs were not wrong — the build report shows it **claimed**
the component, so it received the files and returned empty. The cause is a domain fact the
fixture smoothed away:

> **A Helm chart's `templates/` are Go templates, not YAML.** They open with lines like
> `{{- if .Values.x -}}` and interpolate `{{ }}` into keys and values throughout. **31 of 31
> ConfigMaps here are templated — and that is not a property of this workspace, it is what
> Helm is.** A YAML parser cannot read any of them, so this extractor cannot read any real
> chart's host map anywhere.

The extractor's own docstring calls the host map a fact that "exists nowhere else in a
workspace" — where service names become addresses. On real input that map is **empty**, and
nothing in the output says so.

**This is the sixth instance of "empty reads exactly like clean"** and the first one found by
pointing the tool at reality rather than by reasoning about it.

It also lands squarely on item 81b, which shipped four days ago: that fix gave this extractor
canonical ranking so a superseded chart tree could not supply live addresses. **The fix is
correct and it operates on data this extractor never receives.** A defect can be real, its fix
can be right, and both can sit on top of a reader that was never reading.

## Finding 2 — the core cause: `props.load_yaml_all` returns `[]` for "unparsable"

```python
def load_yaml_all(path: Path) -> list[Any]:
    try:
        return [d for d in yaml.safe_load_all(read_text(path)) if d is not None]
    except yaml.YAMLError:
        return []
```

**A file that cannot be parsed and a file with no documents return the same value**, so no
caller can distinguish them. Verified directly against a real chart template: `load_yaml_all`
returned `[]` while `yaml.safe_load_all` on the same text raised `while parsing a flow node`.
The error exists; it is discarded one frame below every reader.

This is in `core/lib/`, so it is shared by **every** YAML reader — the deployment reader, the
compose reader and anything item 9 adds. It is the more important half of Finding 1, because
fixing only the Helm case would leave the next YAML reader silent in exactly the same way.

**The asymmetry that proves this is an oversight rather than a policy:** the deployment
extractor beside it *does* emit an `unparsable` row, and did so once in this run. The ConfigMap
extractor has no such path at all — so even if the parse error reached it, it has nowhere to
put it.

## Finding 3 — stack detection is root-only, so multi-module repositories detect as `unknown`

All but two components resolved to stack `unknown`, including several unambiguous
Java/Maven repositories. Cause: `detect_stack()` looks for a marker **at the component root**,
and these repositories carry 21–30 `pom.xml` files nested one or more levels down with none at
the top.

**Extraction is unaffected, and that is not luck.** Rev 9 corrected this plan's original
"activated by stack detection" to *activated by matching `INPUT_GLOBS` against real files*,
for exactly this reason. Had activation been gated on the detected label, **every Java
extractor would have been skipped on every one of these repositories, and the build would have
reported success.** This run is the first evidence that the correction was load-bearing rather
than tidy.

What *is* affected is `profile.yml`: test, build, lint and coverage commands are derived from
the detected stack, so those components would be rendered blank or wrong. That reaches item 38
(the build engine) and item 15, not the graph.

## Finding 4 — the unresolved report is individually correct and collectively unusable

**2,721 of the 4,066 unresolved rows are one kind** (`key-conflict`) from the property
substrate, spanning 2,537 distinct keys, 88% of them concentrated in a single component.

Each row is *right*: two spellings of one key resolve to different values and, as the row
itself says, which one applies is a deployment fact the repository does not contain. But the
dominant shape is a production address versus a `localhost` development default — **one
systematic pattern, reported 2,721 times as though it were 2,721 independent decisions.**

No human adjudicates that list, and a report nobody reads protects nothing. The fix is not to
suppress the rows — the ambiguity is real and dropping it silently is this project's cardinal
sin — but to **group them by pattern** so the report says "one pattern, 2,721 sites" and keeps
every site addressable. That is an input to item 11's validation dashboard, and it is exactly
the kind of requirement that could not have been designed from the fixture, where the
unresolved report holds three rows.

## Smaller findings

| # | Finding | Where it belongs |
|---|---|---|
| 5 | 266 `grammar-unavailable` rows carry **no `language` field** (from the test extractor), so a row whose entire purpose is to name what is missing does not name it. The 8 rows from the UI reader do carry it (7 `typescript`, 1 `tsx`) | its own fix |
| 6 | The known Windows TypeScript gap has a **measured cost here for the first time**: 8 UI source files unread that a Linux run would read. Small in this workspace; the open decision to pin `tree-sitter-typescript` now has a number attached rather than an argument | open decision |
| 7 | 206 `contested-service` rows — real identity collisions, **reported and not guessed**, which is the designed behaviour holding at a scale the fixture cannot reach (it has one). These are the `[[canonical]]` blocks a developer would fill in on a second pass, and they arrived exactly as item 10 predicts | confirms item 10 |
| 8 | 5 components matched no extractor and were **named in a warning as a coverage hole rather than a failure**, correctly staying exit 0. Spot-checked: at least one is a README-only repository, so "matched nothing" is the right answer, not a miss | correct as built |

## What this gate does and does not certify

**Does:** the pipeline survives real-world mess without crashing or aborting; activation by
globs rather than by detected stack is load-bearing and correct; collision reporting, coverage-
hole warnings and the exit-code contract all behave at real scale; and the fd accelerator has
now been exercised on a large real tree.

**Does not:** say anything about **recall value**. Whether these edges beat `grep` for a
developer answering an impact question is `judging-protocol.md` §3's recall@20 comparison at
M1, on real merged commits, and nothing here substitutes for it. Three gates, three different
questions.

## Verdict

**PASS.** The condition asks for a real-workspace run with a record of what extraction found
and what it got wrong; the run completed and the record is above. A pass with four defects is
the expected shape — a clean sheet would have been evidence that the run was too easy, since
the fixture already passes and the two halves exist precisely because they fail differently.

**None of the four is fixed here, deliberately.** A defect fixed outside the item that found it
arrives without a regression test — item 81 is where that rule was paid for and it holds here
too. Findings 1 and 2 are one item (the reader and the core swallow, fixed together or the
next YAML reader inherits it); Findings 3, 4 and 5 are their own.
