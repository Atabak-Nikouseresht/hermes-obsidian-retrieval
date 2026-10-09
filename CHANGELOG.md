# Changelog

## 0.2.2

Implemented in the staged project:

- Full-section evidence expansion with an approximate token budget.
- Direct-note fallback paths for weak retrieval and insufficient evidence.
- Structured claims, stable evidence identifiers, and source metadata.
- Exact-quote validation; unsupported claims are discarded, with abstention when no validated claim remains.
- Conflict and missing-information fields in the answer schema.
- Auxiliary-task route checks, separate remote-sharing eligibility, and fail-closed fallback-chain safeguards.
- Bounded retry handling for malformed or invalid structured output.
- Additional regression and privacy tests.

These are implementation changes, not proof of successful end-to-end answer quality. Version 0.2.2 did not complete a full post-fix answer acceptance test; see [`docs/validation.md`](docs/validation.md). No release dates or earlier commit history are inferred here.
