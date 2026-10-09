# Validation record

**State date: 2026-10-09.** This record preserves historical results separately from the final 0.2.2 engineering checks. Retrieval metrics do not establish answer quality.

## Verified

- **Unit suite:** 35 tests passed in the archived copy on 2026-10-09 with `python -m unittest discover -s tests -v`; the run completed without model calls.
- **Hermes plugin checks:** on 2026-10-09, `hermes plugins validate . --json` passed all 13 checks and `hermes plugins doctor . --ci` passed with two tools and one hook registered.
- **Synthetic privacy boundary:** the prior harness passed 9/9 indexing privacy checks, including denial of private/unmarked notes, templates, attachments, and embedded targets; revocation removed indexed results/text. These were synthetic fixtures, not a complete real-vault privacy audit.
- **Retrieval-only benchmark:** 28 questions over 71 eligible notes; no privacy violations were recorded. The FTS5/BM25 system had Recall@5 **0.9464**, MRR **0.8839**, top-1 source accuracy **0.7857**, and source precision@5 **0.2773**, versus **0.6429**, **0.6726**, **0.6429**, and **0.1901** for a direct title/heading scan. An earlier recorded pass reported mean latency **48.456 ms vs 46.410 ms** (p95 **61.575 ms vs 59.341 ms**); the later saved aggregate from 2026-10-09 reported **46.261 ms vs 44.639 ms** (p95 **52.667 ms vs 48.198 ms**). FTS5/BM25 latency was slightly higher in both captures. Evidence-anchor coverage was **0.5714** for both methods and is not answer-faithfulness evidence. This benchmark did not call a model or generate answers.
- **Data protection baseline:** the current Thesis baseline comparison was repeated on 2026-10-09: 332 entries checked, 0 missing, 0 changed. The previous 84-file Obsidian Markdown hash check passed, but its per-file baseline was not accessible for repetition in this closure run; it is retained as historical evidence only. No vault or thesis writes were performed for this archive.
- **Local runtime:** at finalization, the real-vault index was absent, the test Ollama port was closed, and the project-owned background process records were exited. No test server or model benchmark was started for this archive.

## Failed

The latest complete answer-generation acceptance run before final 0.2.2 hardening was not accepted:

- 17/20 retrieval-based answers were nonempty/parseable, compared with 20/20 for the direct-note comparison.
- Expected-source citation linkage was 16/31 (51.6%) for retrieval answers versus 19/26 (73.1%) for the comparison. These are automated linkage proxies, not independent semantic review.
- Missing-information handling was correct in 0/4 retrieval cases versus 1/4 comparison cases.
- Broad contradiction handling failed to reliably surface conflicting notes and cite both.

After the final hardening, the isolated integration attempts still did not satisfy the acceptance criterion: one CLI run made no tool calls; another invoked both `personal_os_search` and `personal_os_answer` despite an answer-only instruction and produced no detected expected evidence marker; and the synthetic probe exited on a JSON decode error. These are failed/inconclusive verification attempts, not evidence of a successful compliant answer flow.

## Not verified

- A complete post-fix answer-quality benchmark and post-fix expected-source citation accuracy.
- Independent human review that each claim is semantically supported by its cited passage.
- Complete current Hermes-session integration after the final code changes.
- Reliable missing-information and broad contradiction handling across a representative set.
- Sustained real-world performance and behavior on a changing personal vault.
- Human approval of remote-use sensitivity classification for actual notes.
- Application-layer encryption or a forensic-erasure guarantee for the SQLite index.

## Interpretation

Passing unit tests, CLI validation, doctor checks, and retrieval ranking metrics does not establish production readiness. Version 0.2.2 remains **Experimental — Development Frozen — Globally Disabled**. Detailed private benchmark questions and per-question result rows are deliberately excluded from Git; only aggregate values are retained here.
