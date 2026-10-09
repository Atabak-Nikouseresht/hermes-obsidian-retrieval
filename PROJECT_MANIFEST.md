# Project manifest

**Archive scope:** reusable Personal OS Retrieval plugin source at version 0.2.2. The archive is a standalone local Git repository, separate from the staged Hermes copy and outside the Obsidian vault.

## Preserved files

- Root: `.gitignore`, `README.md`, `CHANGELOG.md`, `STATUS.md`, `PROJECT_MANIFEST.md`, `LICENSE`, `plugin.yaml`, `__init__.py`.
- Implementation: `personal_os_retrieval/answering.py`, `personal_os_retrieval/indexer.py`.
- Tests: `tests/test_answering.py`, `tests/test_expansion.py`, `tests/test_indexer.py`, `tests/test_plugin.py`.
- Benchmark code and notes: `benchmarks/run_benchmark.py`, `benchmarks/README.md`.
- Technical documentation: `docs/architecture.md`, `docs/validation.md`.

## Sanitization and exclusions

- The repository copy uses a generic `~/Obsidian` path default; the installed staged plugin remains unchanged.
- The original `benchmarks/questions.json` and `benchmarks/latest-results.json` are excluded. The question set contains personal-topic queries and private source-note references; the results file repeats query and gold-path details. No synthetic substitute is included because it would change the meaning of that evaluation set. Safe aggregate metrics are recorded in `docs/validation.md`.
- Generated `__pycache__`/`.pyc` files are excluded. The source plugin folder contained these disposable caches at inspection time.
- No SQLite index, vault note, thesis file, personal profile export, local Hermes configuration/authentication state, log, model conversation, `.env`, credential, or backup snapshot is included.
- No prior Git history existed in the source plugin location; the archive begins with a new local commit. No existing history was rewritten.
- The MIT `LICENSE` file was added after the author explicitly chose MIT for the public repository.

## Runtime data boundary

The plugin creates its local index under Hermes' plugin-data directory, outside this repository. Local authentication/configuration, vault contents, generated indexes, benchmark fixtures, and detailed run outputs must remain outside Git. `.gitignore` is defense in depth; each commit still requires review of the staged file list and diff.

## Dependencies

No packaging or dependency manifest was added. Runtime use requires the Hermes plugin API, Python, SQLite with FTS5, and Hermes' existing configuration parser dependency where configuration is read. The unit tests use Python's standard `unittest` framework.
