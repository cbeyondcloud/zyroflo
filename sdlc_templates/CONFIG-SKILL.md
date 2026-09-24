---
name: sdlc-config
description: Configure this repository's SDLC harness by question and answer through `sdlc config`, which never writes a configuration a run would refuse - the project metadata, which steps run, the gate regime, extra human gates, gate overrides, which review findings block, and model routing. Use when the user types /sdlc-config, or asks to change the pipeline, the gates, or the project metadata. Never hand-edit those keys in .sdlc/profile.yml or sdlc-project.toml.
---

# Configure the SDLC harness (sdlc-config)

Every change goes through `sdlc config`. It checks a change the way a run would
before it writes a byte, reads every value back, and writes a whole change or
none of it. **The interface is this chat**: ask the user, explain, apply.

Run it from the repository root, through the harness:

```
uv run --project "@@HARNESS@@" python "@@HARNESS@@/tools/sdlc/config.py" --target . --spec
```

Every command below is that prefix with other arguments in place of `--spec`.

## The loop

1. **Read the questions.** `--spec` prints them as JSON. Each has an `id`, a
   `prompt`, a `type` (`text`, `choice`, `list`, `multi`, `boolean`), its
   `choices` or `pattern`, and its `current` value. `gates.<gate>` and
   `models.<step>` are answered one key at a time (`gates.G3=hard`).
2. **Ask one question at a time**, in the spec's order: its prompt, its
   choices, its current value. `gate_profile` carries `presets` - each
   regime's hard gates, signing and blocking severities - so say what a choice
   means before the user makes it. Skip what the user does not want to change.
   Before INIT (`installed` is false) only the `project.*` questions apply.
3. **Preview a pipeline change.** `--dry-run set <id>=<value>` prints the
   pipeline now and after the change, and writes nothing. Show it.
4. **Apply.** `set <id>=<value> ...`. Answers that only make sense together go
   in ONE `set`, because each alone is refused: `regulated` needs
   `signing=dsse` and `medium` in `review.block_on`. An empty value
   (`gates.G3=`) clears an override. `apply --from <file>` takes a JSON object
   of answers instead.
5. **A refusal writes nothing.** Exit 1 lists every reason a run would refuse
   the result: explain them, and ask again. Exit 2 is a malformed answer - an
   unknown key, or a value off its choices - and it names what it expected.
6. **Finish** with `--check` (exit 0: a run and INIT would accept the
   configuration), and show the user `.sdlc/pipeline.md`, which every change
   redraws.

## Rules

- Never hand-edit a key `--spec` lists, in `.sdlc/profile.yml` or
  `sdlc-project.toml`: a hand edit skips the checks, the read-back, and the
  redrawn pipeline.
- The files a change writes - `sdlc-project.toml`, `.sdlc/profile.yml`,
  `.sdlc/pipeline.md` - are committed with the work that needed them.
- `--json` gives machine-readable output on any command.
- A person at their own terminal can answer the same questions with
  `--interactive`. It refuses without a terminal, so never run it from here.
