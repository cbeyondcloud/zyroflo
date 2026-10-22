# Security policy

## Supported versions

Security fixes land on `main`. There are no maintained release branches yet; run the latest
`main`.

## Reporting a vulnerability

**Please do not open a public GitHub issue for a security vulnerability.** Report it privately,
in one of two ways:

1. **GitHub Security Advisories** (preferred): the **Report a vulnerability** button under this
   repository's *Security* tab opens an advisory only the maintainers can see.
2. **Email** the maintainers at `sumit.gupta@cbeyond.com`.

Please include what the issue is and its impact, the steps to reproduce it (a proof of concept if
you have one), the commit or version affected and your configuration, and any fix you suggest.

## What to expect

- An acknowledgement within five working days.
- An assessment, and a fix or a mitigation plan, as soon as the issue is understood.
- Credit in the fix's notes, if you want it.

## Scope

In scope: the installer (`sdlc-init.py`), the gated pipeline (`tools/sdlc/`), the review tier
(`tools/review/`), the graph tier (`tools/graph/`) and the files they install into a repository -
including the approval ledger and its CI check, whose job is to make a tampered gate unmergeable.

The harness itself calls no model and opens no network connection unless a configuration asks it
to. Credentials belong in `.env`, never in a tracked file; a secret found in this repository's
history is a vulnerability - please report it the same way.
