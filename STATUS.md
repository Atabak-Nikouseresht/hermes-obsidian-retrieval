# Project status

**As of 2026-10-09**

| Field | State |
|---|---|
| Project | Hermes Personal OS Retrieval Plugin |
| Version | 0.2.2 |
| Status | Experimental |
| Development | Frozen |
| Global Hermes Plugin State | Disabled |
| Production Readiness | Not Accepted |
| Retrieval Engine | Implemented and benchmarked |
| Answer Generation | Not fully validated |
| Privacy | Synthetic checks passed; remote-use approval incomplete |
| Hermes Session Integration | Not fully verified |
| Further Development | Paused indefinitely pending an explicit new decision |

## What works

- Local metadata-gated Markdown indexing, SQLite FTS5/BM25 retrieval, and heading-aware evidence expansion are implemented.
- The repository's unit tests and Hermes configuration/doctor checks passed at the recorded levels in `docs/validation.md`.
- Synthetic indexing privacy checks passed in the recorded benchmark.

## What does not meet acceptance

- Answer-generation quality, expected-source citation accuracy, and missing-information handling did not meet the prior acceptance results.
- Broad contradiction handling failed in the prior acceptance run.
- The final 0.2.2 code has no complete successful post-fix answer benchmark or fully verified Hermes-session integration.
- Remote-use classification has not been approved for real personal notes.

## Current blockers and risks

- The stored index is not encrypted at the application layer; permission hardening is not a substitute for encryption or forensic erasure.
- Exact quote matching is not semantic entailment verification.
- The auxiliary model's effective route and fallback behavior must remain correctly constrained; remote use requires a separate per-note approval.
- Historical private benchmark questions and detailed output are intentionally absent from this repository.

## Safe operating assumptions

- Keep the plugin globally disabled.
- Do not point it at real personal notes for answer generation without a separate review and explicit decision.
- Keep remote transmission disabled; do not treat `ai_index: true` as permission to send note text to a remote model.
- Do not run expensive local-model benchmarks or use the retrieval-only score as evidence of answer correctness.

## Conditions for possible future reactivation

Reopening requires an explicit new decision. Before any enablement, the post-fix tool flow must be verified end to end on synthetic data; citations, claim support, contradiction handling, and abstention must receive independent review; the effective local/remote route and fallback chain must be verified; and privacy classification and storage protections must be reviewed. Meeting these conditions is a prerequisite for reconsideration, not an approval or a scheduled plan.
