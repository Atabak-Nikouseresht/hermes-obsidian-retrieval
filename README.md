# Hermes Personal OS Retrieval

> **Experimental — development frozen — globally disabled. Production readiness was not accepted.**

A Hermes plugin that searches eligible Markdown notes in an Obsidian vault using local SQLite FTS5/BM25 retrieval and can request structured, evidence-linked answers from a configured model. Version **0.2.2** is preserved here as a research/engineering archive, not a supported or production-ready release.

The repository is a sanitized copy. It is separate from the staged Hermes installation; no vault, thesis, authentication state, or local index is included.

## What it provides

- `personal_os_search`: metadata-gated local search with source path, heading, evidence identifier, excerpt, modification metadata, and rank score.
- `personal_os_answer`: retrieves and expands source sections, requests a schema-constrained answer through a Hermes auxiliary task, checks evidence identifiers and exact source quotes, and reports conflicts or missing information.

The implementation uses Python, the Hermes plugin API, SQLite FTS5, BM25 ranking, and simple YAML-frontmatter parsing. It adds no embeddings, vector database, external index service, or vault-write operation.

## Retrieval and answer flow

1. Hermes dispatches a tool request to the plugin.
2. The plugin checks note metadata and path eligibility; the index is refreshed from eligible Markdown files.
3. SQLite FTS5 retrieves heading-aware chunks and BM25 ranks them.
4. For answer generation, the plugin expands selected headings under an approximate evidence-token budget. A direct-note fallback is available for weak/empty retrieval or insufficient evidence.
5. The plugin sends structured evidence to the configured Hermes auxiliary task. It validates the response schema and retains only claims linked to a known evidence ID and an exact substring quote.
6. The response includes source metadata, missing information, and any validated conflicts.

Retrieval relevance and answer correctness are separate properties. Exact-quote matching establishes that text occurs in a source; it does **not** establish that the quote semantically supports the claim.

## Privacy and storage

- Local indexing requires the exact boolean `ai_index: true` and `confidentiality: personal` or `public`. Missing, false, quoted-string, `private`, and `highly_private` values are denied.
- Hidden paths, templates, attachments, trash, `.obsidian`, symlinks, and non-Markdown files are excluded. Links and embeds are not followed or transcluded.
- Remote model transmission is a separate permission. It is disabled by default. When explicitly enabled, a note must also have the exact boolean `ai_remote_share: true`; `ai_index: true` is not remote consent.
- The plugin records only whether a session route was loopback-local, not the endpoint or request payload. Unknown or remote routes fail closed unless remote transmission is explicitly enabled. Remote retrieval is limited to notes separately approved for sharing.
- The index resides under Hermes' plugin-data directory. Directory permissions and SQLite/FTS secure-delete, WAL truncation, and vacuuming reduce exposure, but the database has **no application-layer encryption** and cleanup is not a forensic-erasure guarantee.

Use a local model route and keep remote transmission disabled unless remote sharing has been deliberately approved and reviewed. A model route or fallback configuration can change the privacy boundary; do not assume that a local UI means local inference.

## Configuration

The manifest uses the generic `~/Obsidian` default. Set `vault_path` explicitly in your local Hermes plugin configuration if your vault is elsewhere. Keep that local configuration and authentication material out of Git. Other settings are:

- `allow_remote_transmission`: defaults to `false`.
- `evidence_token_budget`: defaults to 6,000 approximate tokens; this is tokenizer-independent and is not an exact model-token count.

The runtime depends on Hermes' plugin API and its Python environment. SQLite/FTS5 is provided by Python's SQLite build; configuration parsing uses PyYAML when Hermes' config file is present. No additional framework or packaging layer is required by this archive.

## Tests and benchmarks

Run the bounded unit suite from the repository root:

```text
python -m unittest discover -s tests -v
```

Hermes-specific checks can be run against the local repository path with `hermes plugins validate` and `hermes plugins doctor --ci`. The actual recorded validation and limitations are in [`docs/validation.md`](docs/validation.md).

The retrieval benchmark script is retained, but its original question fixture and detailed result JSON are deliberately excluded because they contain personal-topic queries and source-note references. See [`benchmarks/README.md`](benchmarks/README.md) before running it. No LLM benchmark should be inferred from the retrieval-only results.

## Current status and possible reconsideration

The plugin is **experimental, frozen, and disabled globally**. The latest complete answer-quality test did not pass acceptance; the final 0.2.2 changes have not completed a full live answer-quality and Hermes-session verification. Do not use it unattended or enable it on the strength of unit tests or retrieval metrics alone.

Any future reactivation requires a separate explicit decision and, at minimum, successful post-fix live integration on synthetic data, independent review of claim support/citations/conflicts/abstention, privacy and route verification, and confirmation that all real-note sharing remains explicitly approved. No further work is scheduled.
