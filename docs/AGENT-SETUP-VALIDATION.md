# Agent setup validation — 2026-11-02

Scope: agent-facing documentation, default-port migration, CLI-derived URLs, and container startup.
The repository-wide audit findings are a separate backlog; this change does not resolve all of them.

## Local results

- Linux / Rust 1.99.1: `cargo test --all-targets` passed (799 library tests, 5 server CLI tests, 17 integration tests).
- `cargo fmt --all --check` and `cargo clippy --all-targets -- -D warnings` passed.
- Rust 1.88.0: `cargo check --locked --all-targets` passed using a separate target directory.
- `python3 tools/smoke_agent_setup.py --binary target/debug/rustxng` passed all three cases:
  default 29483, explicit CLI port, and `SEARXNG_PORT`. Each starts the real binary with an isolated
  configuration and a fake local Mwmbl API, then checks health, identity, search results, and OpenSearch URLs.
- SealQA scoring unit tests: 7 passed. This change does not rerun the external SealQA baseline;
  that report and its recorded settings remain historical data for commit fef998b.
- Markdown lint passed for all root and docs Markdown files. Local documentation links and English/Japanese
  README heading structure were checked.
- v0.26.0 Linux musl release download: SHA-256 and `--version` verified during README preparation.
  Existing local service reuse and the documented five-result JSON search were also verified.
- Container build and runtime were checked through the local Docker-compatible Podman CLI.
  The non-root process started, `/healthz` and `/config` responded, OpenSearch advertised 29483,
  and host publication was `127.0.0.1:29483`. Runtime COPY permissions are explicit so checkout modes do not prevent startup.

## CI coverage

The Linux/macOS/Windows matrix builds the server and runs the same offline setup smoke tool.
A separate Rust 1.88.0 job checks the minimum supported version. Local checks do not substitute for
those platform-specific CI results; macOS and Windows were not run on this Linux workstation.

Raising the minimum Rust version enables additional Clippy suggestions. The Cargo lint policy explicitly
permits nested `if` statements and modulo expressions, retaining the existing parser/upstream plural-rule
style. Other warnings remain errors; no functional test failures are suppressed.

## Current Issue / PR review

- #21: Docker documentation and CI use explicit loopback host publication.
- #22: 29483 replaces 8888 as the default; migration and the IANA selection record are documented.
- #18: English and Japanese READMEs now have matching structure and install paths.
- #17 and #16: engine rest visibility and Bing market variants remain separate implementation work.
- PR #20: mergeable at review time; no check results were returned by GitHub.
- PR #12 and #13: conflicting at review time. The earlier audit records a successful local replay of their
  intended fixes; this setup change does not force-push their branches or merge those PRs.
