"""Per-step provider and model — T445a: the configuration, and who runs each step.

Three execution modes, and only the third needs a key:

1. **Driving tool** (the default, and what `/sdlc` means): the AI coding tool the
   developer already has open runs the step with its own model and subscription.
   The harness never sees either.
2. **Declared runner** (item 105): a CLI agent named in `runners:` and mapped in
   `phase_runners:` runs the step with its own configured provider.
3. **Model endpoint** (`model_routing: true`): the harness spawns its endpoint
   runner for the step, with the provider and model `models:` names, keyed from
   `.env`.

**The shipped default is pre-filled and INERT.** `models: default:
openai/gpt-4o-mini` is real configuration - validated, editable, spelled right
for the wizard - but with `model_routing` false (the shipped value) nothing here
resolves a step to a model, reads a key, or touches a network. No shipped package
can carry a key, so an ACTIVE default would fail on every fresh install; that is
the reason for dormancy, and it is pinned by a test.

**No provider is named in this code.** The catalog is DATA -
`.sdlc/providers.toml`, falling back to the harness's `sdlc_templates/` copy -
and a guard sweeps `tools/sdlc/*.py` for any string that spells one. Adopters add
a provider by adding a table.

Every refusal lands at step ENTRY, by name, the way `policy.runner_for` refuses:
nothing is half-done under the wrong model.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.sdlc import policy as guardrails  # noqa: E402

#: Where the catalog lives in an adopter repository, and the harness's copy.
CATALOG_REL = Path(".sdlc") / "providers.toml"
HARNESS_CATALOG = Path(__file__).resolve().parents[2] / "sdlc_templates" / "providers.toml"

#: What a provider table may carry. `protocol`, `base_url` and `key_var` are
#: required; prices are optional and user-maintained (a stale built-in price
#: would be a confident lie).
PROVIDER_KEYS = ("protocol", "base_url", "key_var", "price_in_per_mtok",
                 "price_out_per_mtok", "note")
PROTOCOLS = ("openai-chat", "anthropic-messages")

#: The one key in `models:` that is not a step name.
DEFAULT_KEY = "default"

#: `provider/model` - a provider id, one slash, a model name with no spaces.
_SPEC = re.compile(r"^(?P<provider>[a-z0-9][a-z0-9_.-]*)/(?P<model>\S+)$")

#: Who runs a step, in `stage --list`'s and the history's vocabulary.
DRIVING_TOOL = "driving-tool"
RUNNER = "runner"
MODEL_ENDPOINT = "model-endpoint"

_ROUTING_TRUE = ("true", "True", "on")
_ROUTING_FALSE = ("false", "False", "off", "")


class ModelError(RuntimeError):
    """The model configuration is declared and cannot be honoured - refused at
    step entry, by name, before any work is done under the wrong model."""


def model_routing(target: Path) -> bool:
    """Whether the harness calls models itself. Absent means false - the
    shipped, inert default. `on`/`off` are accepted spellings; anything else is
    refused, because a routing switch nobody can read is a spend nobody chose."""
    raw = guardrails.profile_top_value(target, "model_routing")
    text = "" if raw is None else str(raw).strip()
    if text in _ROUTING_TRUE:
        return True
    if text in _ROUTING_FALSE:
        return False
    raise ModelError(f"model_routing is {text!r} - write true or false (the "
                     f"shipped value is false: the driving tool's model does the work)")


def models_declared(target: Path) -> dict[str, str]:
    """`{step name | "default": "provider/model"}` from the profile's `models:`
    block, SHAPE-checked: an unknown step key and a value that is not
    `provider/model` are refused by name. Whether the provider exists is
    `model_for`'s question, since only it reads the catalog."""
    from tools.sdlc import stage   # lazy: stage imports this module for --list

    out: dict[str, str] = {}
    for line in guardrails._profile_block(target, "models"):
        key, sep, value = line.strip().partition(":")
        key, value = key.strip(), guardrails._unquote(value.strip())
        if not sep or not key:
            continue
        if key != DEFAULT_KEY and key not in stage.NAMES:
            raise ModelError(f"models: names {key!r}, which is not a step - one of "
                             f"{DEFAULT_KEY}, {', '.join(stage.NAMES)}")
        if not _SPEC.match(value):
            raise ModelError(f"models: {key} is {value!r} - write provider/model, "
                             f"e.g. the shipped default's shape, `<provider>/<model>`")
        out[key] = value
    return out


def provider_catalog(target: Path) -> tuple[dict[str, dict], str]:
    """({provider id: table}, where it was read from). The adopter's
    `.sdlc/providers.toml` wins; an install from before T445 falls back to the
    harness's copy, and the source says which."""
    import tomllib

    path = Path(target) / CATALOG_REL
    source = CATALOG_REL.as_posix()
    if not path.is_file():
        path, source = HARNESS_CATALOG, "the harness's sdlc_templates/providers.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ModelError(f"{source} could not be read: {exc}") from None
    catalog: dict[str, dict] = {}
    for pid, table in data.items():
        if not isinstance(table, dict):
            raise ModelError(f"{source}: {pid!r} must be a table ([{pid}])")
        if unknown := [k for k in table if k not in PROVIDER_KEYS]:
            raise ModelError(f"{source}: [{pid}] has unknown key(s) "
                             f"{', '.join(unknown)} - one of {', '.join(PROVIDER_KEYS)}")
        for req in ("protocol", "base_url", "key_var"):
            if not str(table.get(req, "")).strip():
                raise ModelError(f"{source}: [{pid}] needs {req}")
        if table["protocol"] not in PROTOCOLS:
            raise ModelError(f"{source}: [{pid}] protocol {table['protocol']!r} - one "
                             f"of {', '.join(PROTOCOLS)}")
        catalog[pid] = dict(table)
    return catalog, source


def model_for(target: Path, step: str, env: dict | None = None) -> dict:
    """Who runs `step` (a name, e.g. "requirements"), and with what.

    Resolution order, the plan's: (1) an explicit `phase_runners:` entry wins,
    unchanged; (2) with routing ON, `models:<step>`, then `models:default`,
    routes the step to the model endpoint; (3) otherwise the driving tool.

    An ACT step (`policy.ACT_PHASES`) cannot be served by a chat endpoint. A
    `models:<step>` entry naming one for it is refused by name - it needs a
    tools-capable runner declared for the step, which receives the model
    through its command's placeholders (T445c). `models:default` never forces
    an act step onto an endpoint: it stays with the driving tool.

    `env`, when given with routing on, is checked for the provider's key - the
    refusal names the variable and `.env`, never a 401 discovered mid-step.
    """
    runner = guardrails.runner_for(target, step)
    if runner is not None:
        return {"actor": RUNNER, "runner": runner["name"]}
    if not model_routing(target):
        return {"actor": DRIVING_TOOL}
    declared = models_declared(target)
    explicit = step in declared
    spec = declared.get(step) or declared.get(DEFAULT_KEY)
    if spec is None:
        return {"actor": DRIVING_TOOL}
    if step in guardrails.ACT_PHASES:
        if explicit:
            raise ModelError(
                f"models: {step} is {spec!r}, and {step} must ACT - edit files, run "
                f"suites - which a model endpoint cannot. Declare a tools-capable "
                f"runner for {step} in runners:/phase_runners:; it receives the "
                f"provider and model through its command's placeholders")
        return {"actor": DRIVING_TOOL}
    m = _SPEC.match(spec)
    catalog, source = provider_catalog(target)
    provider = m["provider"]
    if provider not in catalog:
        raise ModelError(f"models: {step if explicit else DEFAULT_KEY} names provider "
                         f"{provider!r}, which {source} does not declare - one of "
                         f"{', '.join(sorted(catalog)) or 'none'}. Add a [{provider}] table")
    table = catalog[provider]
    if env is not None and not str(env.get(table["key_var"], "")).strip():
        raise ModelError(f"{step} routes to {spec}, and {table['key_var']} is not set - "
                         f"put it in the harness .env (never in a profile, never in argv)")
    return {"actor": MODEL_ENDPOINT, "provider": provider, "model": m["model"],
            "protocol": table["protocol"], "base_url": table["base_url"],
            "key_var": table["key_var"]}


#: Tokens a declared runner's `command` may carry to receive the step's model
#: (T445c) - how an ACT step's `models:` entry reaches a tools-capable runner.
PLACEHOLDERS = ("{provider}", "{model}")

#: The harness's own endpoint runner (item 104), spawned for a model-endpoint step.
ENDPOINT_RUNNER = Path(__file__).resolve().parent / "http_runner.py"


def catalog_path(target: Path) -> Path:
    """The catalog file `provider_catalog` reads - the adopter's, else the harness's."""
    own = Path(target) / CATALOG_REL
    return own if own.is_file() else HARNESS_CATALOG


def endpoint_command(target: Path, resolved: dict) -> str:
    """The command line that runs a model-endpoint step: the harness's endpoint
    runner, told the provider, model and catalog - and never a key, which the
    runner reads from the environment by the variable the catalog names.
    Quoted, because a clone under a path with a space word-splits otherwise."""
    return (f'"{sys.executable}" "{ENDPOINT_RUNNER}" --provider {resolved["provider"]} '
            f'--model {resolved["model"]} --catalog "{catalog_path(target)}"')


def fill_placeholders(target: Path, step: str, command: str) -> tuple[str, str]:
    """(command with `{provider}`/`{model}` filled, "provider/model"), or the
    command untouched and "" when it carries neither token.

    A command that asks for the model when there is none to give is REFUSED by
    name: a runner handed the literal text `{model}` would pass it to its CLI as
    a model name, which is a guessed bill wearing a placeholder's clothes."""
    if not any(tok in command for tok in PLACEHOLDERS):
        return command, ""
    if not model_routing(target):
        raise ModelError(f"the runner for {step} puts {' / '.join(PLACEHOLDERS)} in "
                         f"its command and model_routing is false - set it true and "
                         f"name a model in models:, or drop the placeholders")
    declared = models_declared(target)
    spec = declared.get(step) or declared.get(DEFAULT_KEY)
    if spec is None:
        raise ModelError(f"the runner for {step} puts {' / '.join(PLACEHOLDERS)} in "
                         f"its command and models: names nothing for {step} and no "
                         f"default")
    m = _SPEC.match(spec)
    return (command.replace("{provider}", m["provider"]).replace("{model}", m["model"]),
            spec)


def describe(resolved: dict) -> str:
    """One step's actor as `stage --list` prints it."""
    if resolved["actor"] == RUNNER:
        return f"runner {resolved['runner']}"
    if resolved["actor"] == MODEL_ENDPOINT:
        return f"{resolved['provider']}/{resolved['model']}"
    return "driving tool"
