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
    content = (choices[0].get("message") or {}).get("content") \
        if isinstance(choices[0], dict) else None
    if not isinstance(content, str) or not content.strip():
        raise SystemExit("the endpoint's answer has no content - an empty "
                         "answer and no answer must differ, so nothing is "
                         "printed as though the model said it")
    return content


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
    ap.add_argument("--key-var", default=DEFAULT_KEY_VAR, metavar="ENV_NAME",
                    help="NAME of the environment variable holding the key "
                         f"(default {DEFAULT_KEY_VAR}) - the name, never the "
                         "value")
    ap.add_argument("--system", default="", metavar="TEXT",
                    help="optional system message")
    ap.add_argument("--max-tokens", type=int, default=None, metavar="N")
    ap.add_argument("--timeout", type=float, default=300.0, metavar="SECONDS")
    ap.add_argument("prompt", help="the task prompt - the FINAL argument, per "
                                   "RUNNER_CONTRACT.md section 1")
    args = ap.parse_args(argv)

    key = os.environ.get(args.key_var, "").strip()
    if not key:
        raise SystemExit(
            f"{args.key_var} is not set in the environment. The key belongs in "
            f"the harness .env (the spawner merges it in) - never in this "
            f"command line, where a recorded argv would carry it verbatim")
    model = args.model or os.environ.get("OPENAI_MODEL", "").strip()
    if not model:
        raise SystemExit("no model named - pass --model or set OPENAI_MODEL. "
                         "A guessed model is a guessed bill, so this refuses "
                         "rather than defaulting to one")
    base = args.base_url or os.environ.get("OPENAI_BASE_URL", "").strip() \
        or DEFAULT_BASE

    print(ask(base, model, args.prompt, key, system=args.system,
              max_tokens=args.max_tokens, timeout=args.timeout))
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
            if _SECRET_NAME.search(var) and len(value) >= 8:
                text = text.replace(value, f"<{var}>")
        print(f"!! {text}", file=sys.stderr)
        return 2
    except Exception as exc:           # noqa: BLE001 - a door, not a guess
        print(f"!! the runner did not finish: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
