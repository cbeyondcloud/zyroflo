# Third-party software

Zyroflo itself is MIT ([LICENSE](LICENSE)). **The drop-in tier - `sdlc-init.py` and everything it
installs into a repository - uses the Python standard library alone: no third-party code.** The
software below is used by the other tiers, by the suite, or fetched as tools. Nothing in this
list is copied into this repository; each keeps its own licence, which governs it.

## Python packages (`pyproject.toml`, pinned by `uv.lock`)

| Package | Licence | Used by |
|---|---|---|
| python-dotenv | BSD-3-Clause | reading `.env` |
| PyYAML | MIT | reading profiles and policies |
| neo4j (driver) | Apache-2.0 (with parts under the Python-2.0 licence) | the graph tier's store |
| tree-sitter | MIT | the graph tier's parsers |
| tree-sitter-language-pack, -java, -python, -javascript, -typescript | MIT | the graph tier's grammars |
| fastmcp (extra `serve`) | Apache-2.0 | the graph MCP server |
| in-toto, securesystemslib (extra `sdlc`) | Apache-2.0; MIT | DSSE-signed approvals |
| deepeval (extra `eval`) | Apache-2.0 | evaluation |
| pytest, pytest-cov, pydantic, jsonschema (extra `dev`) | MIT | the test suite |

Their own dependencies carry their own licences; `uv tree` lists them for a given environment.

## Tools the installer fetches (not shipped here)

`third_party/manifest.json` pins a version and a SHA-256 for each; `tools/third_party.py --fetch`
downloads the upstream release for the machine's platform into the gitignored `third_party/bin/`
and verifies it. They run as separate programs; none is linked into Zyroflo.

| Tool | Upstream | Licence |
|---|---|---|
| ripgrep (`rg`) | BurntSushi/ripgrep | MIT or Unlicense |
| fd | sharkdp/fd | MIT or Apache-2.0 |
| gitleaks | gitleaks/gitleaks | MIT |
| ast-grep | ast-grep/ast-grep | MIT |
| osv-scanner | google/osv-scanner | Apache-2.0 |
| d2 | terrastruct/d2 | MPL-2.0 |
| GitHub CLI (`gh`) | cli/cli | MIT |

## Services run beside it

| Service | Licence | How |
|---|---|---|
| Neo4j Community Edition (`neo4j:5-community`, `docker-compose.neo4j.yml`) | GPL-3.0 | a separate container the graph tier talks to over the network; not linked, not distributed |
| gitleaks container image (`ghcr.io/gitleaks/gitleaks`) | MIT | the CI secret scan (`tools/ci_local.py`, `.github/workflows/ci.yml`) |
