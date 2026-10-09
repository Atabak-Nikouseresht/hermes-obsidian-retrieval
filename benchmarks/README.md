# Benchmark artifacts

`run_benchmark.py` contains the retrieval-only comparison harness. It expects a local `benchmarks/questions.json` fixture with at least 20 unique questions and gold source paths. That original fixture is intentionally not committed: it contains personal-topic queries and source-note references. The associated detailed `latest-results.json` is also excluded because it preserves per-question query and gold-path data.

No synthetic replacement is supplied. Substituting fictional questions would change the meaning of the original evaluation. The existing aggregate retrieval metrics are documented, without private query/path rows, in [`../docs/validation.md`](../docs/validation.md).

The script writes detailed results by default. If you later provide a reviewed, non-sensitive fixture, keep the fixture and generated JSON local; both expected filenames are ignored by Git. Do not run the archived benchmark against a personal vault unless that use is separately reviewed and explicitly chosen. This harness does not generate answers or measure semantic answer faithfulness.
