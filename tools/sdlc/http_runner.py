"""The endpoint runner — item 104: any OpenAI-compatible provider, nothing installed.

Every runner before this one was a CLI somebody installs. This one is an ENDPOINT:
`base_url` + model + a key named in the environment, spoken to over stdlib
`urllib`, so OpenAI, Azure, an enterprise litellm proxy or a local vLLM are the
same runner with a different declaration — which is item 105's connector argument
one layer up, applied to models instead of trackers.

**The harness still never calls a model.** This module IS a runner, spawned by
`--runner` like every other, which is the recorded provider-agnostic design
(*"tools never call LLMs here; the runner does"*). One LLM path, unchanged.

**It satisfies `RUNNER_CONTRACT.md` §1**: the prompt is the FINAL argument, the
working directory is whatever the spawner set, the environment is the shell's plus
the harness `.env`, and the credential is named by variable and **never carried in
argv** — the contract's own rule, because a value the harness never saw in the
environment cannot be scrubbed out of a recorded command line. There is no flag on
this parser that accepts a credential value, and a test sweeps for one.

**What a green run here means, bounded the way §8 bounds the reference runner.**
This runner can WRITE — hand it a design question and a real model answers — and
it cannot ACT: it edits no files, runs no suites, invokes no CLIs. Its capability
under item 105's vocabulary is `chat`, declared. A green run is a green ENDPOINT,
never a green agent workflow.

**An empty answer and no answer must differ.** A response with no choices, or a
choice with empty content, is REFUSED at exit 2 rather than printed — a runner
whose stdout is blank on success would hand the phase an answer that said nothing
and call it one, which is item 40's shape at the model boundary.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

#: This module is an OUTSIDE PROCESS and must import nothing from the harness -
#: the same declaration `reference_runner.py` and `reference_consumer.py` carry,
#: found by the sweep in `test_cli.py` rather than kept on a roster. A runner
#: that leaned on the harness would prove nothing about one that cannot.
OUTSIDE_PROCESS = True

# The console rule, hand-rolled because OUTSIDE_PROCESS forbids `cli.use_utf8`.
# Not optional here: this prints whatever a model answers, which can contain
# anything a codepage cannot spell.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

#: Appended to the declared base URL, which is the adopter's statement of where
#: their gateway lives (with or without a /v1 - the runner does not guess).
CHAT_PATH = "/chat/completions"
DEFAULT_BASE = "https://api.openai.com/v1"
DEFAULT_KEY_VAR = "OPENAI_API_KEY"

#: The wire formats this runner speaks (T445b) - the same ids the catalog's
#: `protocol` field and `models.PROTOCOLS` use. A second protocol is a second
#: adapter here, never a vendor SDK.
PROTOCOLS = ("openai-chat", "anthropic-messages")
MESSAGES_PATH = "/messages"
#: The messages protocol's required version header, pinned: a floating version
#: is a request shape nobody reviewed.
MESSAGES_VERSION = "2023-06-01"
#: The messages protocol REQUIRES max_tokens; this is the bound when the
#: caller names none - a ceiling on one answer, not a guess at a bill.
MESSAGES_DEFAULT_MAX_TOKENS = 4096
#: Where `--provider` looks for the catalog when `--catalog` is not given:
#: relative to the working directory, which RUNNER_CONTRACT.md §1 makes the
#: repository the step runs in.
DEFAULT_CATALOG = ".sdlc/providers.toml"

#: T446b: the file the harness names for usage (RUNNER_CONTRACT.md sec. 1),
#: spelled here because an OUTSIDE_PROCESS may not import `events.USAGE_ENV`;
#: a test ties the two spellings together.
USAGE_ENV = "SDLC_USAGE_FILE"

#: The last answer's token counts, normalised across the two protocols - set by
#: `ask`/`ask_messages`, empty when the provider reported none.
LAST_USAGE: dict = {}


def _usage(found: dict, protocol: str) -> dict:
    """{input_tokens, output_tokens, cached_tokens} from a response, or {} -
    never zeros invented for counts the provider did not send."""
    u = found.get("usage") if isinstance(found, dict) else None
    if not isinstance(u, dict):
        return {}
    if protocol == "anthropic-messages":
        got = {"input_tokens": u.get("input_tokens"),
               "output_tokens": u.get("output_tokens"),
               "cached_tokens": u.get("cache_read_input_tokens")}
    else:
        details = u.get("prompt_tokens_details") or {}
        got = {"input_tokens": u.get("prompt_tokens"),
               "output_tokens": u.get("completion_tokens"),
               "cached_tokens": details.get("cached_tokens") if isinstance(details, dict) else None}
    got = {k: v for k, v in got.items() if isinstance(v, int)}
    return got


def report_usage(provider: str, model: str) -> None:
    """Append this call's usage to the harness-named file, when it named one
    and the provider reported any. Silent otherwise: the harness records the
    absence as `unavailable`, which is the truth."""
    path = os.environ.get(USAGE_ENV, "").strip()
    if not path or not LAST_USAGE:
        return
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"provider": provider, "model": model, **LAST_USAGE},
                            sort_keys=True) + "\n")

#: How much of a provider's error body reaches the console. Enough to act on,
#: not enough to flood a transcript.
ERROR_BODY_CAP = 500

#: What the failure-path scrub treats as a credential's NAME - the same shape
#: `provider_smoke._SECRET_NAME` uses, restated here because an OUTSIDE_PROCESS
#: cannot import it and a runner that reached into the harness to avoid the copy
#: would cost the property it exists for.
#:
#: **WELDED, not merely claimed.** This copy dropped `CREDENTIAL` at birth and
#: nothing noticed, so the failure-path scrub printed a `*_CREDENTIAL` value the
#: original would have redacted. Item 115 ties the two spellings together with a
#: source-text test: change one and the other fails by name. A forced copy with
#: no tether drifts, and this one did.
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.I)


def ask(base_url: str, model: str, prompt: str, key: str, *,
        system: str = "", max_tokens: int | None = None,
        timeout: float = 300.0) -> str:
    """One question, one answer. No retries, no streaming, no tool loop -
    deliberately naive, the way `reference_consumer.py` is: "any
    OpenAI-compatible gateway can serve this" is a claim about the protocol,
    and a runner that needed a vendor SDK to make it would be evidence against
    itself.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body: dict = {"model": model, "messages": messages}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens

    req = urllib.request.Request(
        base_url.rstrip("/") + CHAT_PATH,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:ERROR_BODY_CAP]
        raise SystemExit(f"the endpoint answered HTTP {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"the endpoint could not be reached: {exc.reason}")
    except TimeoutError:
        raise SystemExit(f"the endpoint did not answer within {timeout:g}s")

    try:
        found = json.loads(raw)
    except ValueError:
        raise SystemExit(f"the endpoint's answer is not JSON: {raw[:ERROR_BODY_CAP]}")
    choices = found.get("choices") if isinstance(found, dict) else None
    if not isinstance(choices, list) or not choices:
        raise SystemExit("the endpoint answered with no choices - nothing to "
                         "hand the phase, so this is a refusal rather than an "
                         "empty success")
    LAST_USAGE.clear()
    LAST_USAGE.update(_usage(found, "openai-chat"))
    content = (choices[0].get("message") or {}).get("content") \
        if isinstance(choices[0], dict) else None
    if not isinstance(content, str) or not content.strip():
        raise SystemExit("the endpoint's answer has no content - an empty "
                         "answer and no answer must differ, so nothing is "
                         "printed as though the model said it")
    return content


def ask_messages(base_url: str, model: str, prompt: str, key: str, *,
                 system: str = "", max_tokens: int | None = None,
                 timeout: float = 300.0) -> str:
    """The `anthropic-messages` adapter (T445b): the same one-question shape
    as `ask`, spoken in the messages protocol - the key in `x-api-key`, a
    pinned version header, `system` beside the messages rather than inside
    them, and `max_tokens` always present because the protocol requires it.
    The answer is the concatenated text blocks; none is a refusal, exactly as
    no choices is for `ask`."""
    body: dict = {"model": model,
                  "max_tokens": max_tokens or MESSAGES_DEFAULT_MAX_TOKENS,
                  "messages": [{"role": "user", "content": prompt}]}
    if system:
        body["system"] = system
    req = urllib.request.Request(
        base_url.rstrip("/") + MESSAGES_PATH,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": key,
                 "anthropic-version": MESSAGES_VERSION},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:ERROR_BODY_CAP]
        raise SystemExit(f"the endpoint answered HTTP {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"the endpoint could not be reached: {exc.reason}")
    except TimeoutError:
        raise SystemExit(f"the endpoint did not answer within {timeout:g}s")
    try:
        found = json.loads(raw)
    except ValueError:
        raise SystemExit(f"the endpoint's answer is not JSON: {raw[:ERROR_BODY_CAP]}")
    LAST_USAGE.clear()
    LAST_USAGE.update(_usage(found, "anthropic-messages"))
    blocks = found.get("content") if isinstance(found, dict) else None
    if not isinstance(blocks, list):
        raise SystemExit("the endpoint answered with no content blocks - nothing "
                         "to hand the phase, so this is a refusal rather than an "
                         "empty success")
    text = "".join(b.get("text", "") for b in blocks
                   if isinstance(b, dict) and b.get("type") == "text"
                   and isinstance(b.get("text"), str))
    if not text.strip():
        raise SystemExit("the endpoint's answer has no text - an empty answer and "
                         "no answer must differ, so nothing is printed as though "
                         "the model said it")
    return text


def from_catalog(provider: str, catalog: str) -> dict:
    """One provider's table from a `providers.toml` (T445a's format), read with
    stdlib `tomllib` - this module may import nothing from the harness, so it
    reads the data file itself. Every miss is a refusal naming what to fix."""
    import tomllib
    try:
        with open(catalog, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        raise SystemExit(f"--provider {provider} needs a catalog and {catalog} does "
                         f"not exist - pass --catalog, or run from the repository "
                         f"whose .sdlc/providers.toml declares it")
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SystemExit(f"{catalog} could not be read: {exc}")
    table = data.get(provider)
    if not isinstance(table, dict):
        raise SystemExit(f"{catalog} declares no provider {provider!r} - one of "
                         f"{', '.join(sorted(k for k, v in data.items() if isinstance(v, dict))) or 'none'}")
    for req in ("protocol", "base_url", "key_var"):
        if not str(table.get(req, "")).strip():
            raise SystemExit(f"{catalog}: [{provider}] needs {req}")
    if table["protocol"] not in PROTOCOLS:
        raise SystemExit(f"{catalog}: [{provider}] protocol {table['protocol']!r} - "
                         f"this runner speaks {', '.join(PROTOCOLS)}")
    return table


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="The credential is read from the environment variable named by "
               "--key-var and is NEVER accepted as a flag value.")
    ap.add_argument("--base-url", default=None, metavar="URL",
                    help=f"OpenAI-compatible endpoint root; {CHAT_PATH} is "
                         f"appended. Default: $OPENAI_BASE_URL, then "
                         f"{DEFAULT_BASE}")
    ap.add_argument("--model", default=None, metavar="NAME",
                    help="model name. Default: $OPENAI_MODEL. With neither, "
                         "this refuses - a guessed model is a guessed bill")
    ap.add_argument("--key-var", default=None, metavar="ENV_NAME",
                    help="NAME of the environment variable holding the key "
                         f"(default {DEFAULT_KEY_VAR}, or the --provider's) - the "
                         "name, never the value")
    ap.add_argument("--protocol", default=None, choices=PROTOCOLS,
                    help="the wire format (default openai-chat, or the "
                         "--provider's)")
    ap.add_argument("--provider", default=None, metavar="NAME",
                    help="take protocol, base URL and key variable from this "
                         "provider's table in the catalog; an explicit flag "
                         "still wins")
    ap.add_argument("--catalog", default=DEFAULT_CATALOG, metavar="TOML",
                    help=f"the providers catalog --provider reads (default "
                         f"{DEFAULT_CATALOG}, relative to the working directory)")
    ap.add_argument("--system", default="", metavar="TEXT",
                    help="optional system message")
    ap.add_argument("--max-tokens", type=int, default=None, metavar="N")
    ap.add_argument("--timeout", type=float, default=300.0, metavar="SECONDS")
    ap.add_argument("prompt", help="the task prompt - the FINAL argument, per "
                                   "RUNNER_CONTRACT.md section 1")
    args = ap.parse_args(argv)

    # T445b: a --provider fills what the flags leave unsaid, from the catalog;
    # an explicit flag always wins. Without one, item 104's behaviour exactly.
    table = from_catalog(args.provider, args.catalog) if args.provider else {}
    key_var = args.key_var or table.get("key_var") or DEFAULT_KEY_VAR
    protocol = args.protocol or table.get("protocol") or PROTOCOLS[0]
    key = os.environ.get(key_var, "").strip()
    if not key:
        raise SystemExit(
            f"{key_var} is not set in the environment. The key belongs in "
            f"the harness .env (the spawner merges it in) - never in this "
            f"command line, where a recorded argv would carry it verbatim")
    model = args.model or ("" if args.provider
                           else os.environ.get("OPENAI_MODEL", "").strip())
    if not model:
        raise SystemExit("no model named - pass --model"
                         + ("" if args.provider else " or set OPENAI_MODEL")
                         + ". A guessed model is a guessed bill, so this refuses "
                           "rather than defaulting to one")
    base = (args.base_url or table.get("base_url")
            or ("" if args.provider else os.environ.get("OPENAI_BASE_URL", "").strip())
            or DEFAULT_BASE)

    speak = ask_messages if protocol == "anthropic-messages" else ask
    print(speak(base, model, args.prompt, key, system=args.system,
                max_tokens=args.max_tokens, timeout=args.timeout))
    report_usage(args.provider or "", model)
    return 0


def main(argv: list[str] | None = None) -> int:
    """The door: 0 = answered, 2 = could not ask. Never 1 - this runner has no
    findings vocabulary, and an uncaught crash exiting 1 is how a gate once
    read a traceback as an answer.

    Everything printed on the failure path passes through a one-line scrub:
    the key's value, should any layer echo it back, is replaced by the
    variable's NAME. Providers do not echo credentials, and the cost of
    assuming that forever is one replace call.
    """
    try:
        return _main(argv)
    except SystemExit as exc:
        if exc.code is None or isinstance(exc.code, int):
            raise                      # argparse's own exits pass through
        text = str(exc.code)
        for var, value in os.environ.items():
            # Any secret-NAMED variable, not only the default: --key-var can
            # point at anything, and a scrub that knew one name would miss it.
            #
            # The NAME decides whether to scrub, never a length floor. A floored
            # `len(value) >= 8` here let a secret under eight characters through
            # on a DIRECT invocation (item 104's supported path) - the harness
            # `drive.scrub` already re-scrubs unfloored for the same reason. The
            # floor's real job is a scrub-TEXT one (do not mangle `true`/`8080`),
            # not a decide-whether-to-scrub one; only an EMPTY value is skipped,
            # because replacing "" would rewrite every position in the text.
            if _SECRET_NAME.search(var) and value:
                text = text.replace(value, f"<{var}>")
        print(f"!! {text}", file=sys.stderr)
        return 2
    except Exception as exc:           # noqa: BLE001 - a door, not a guess
        print(f"!! the runner did not finish: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
