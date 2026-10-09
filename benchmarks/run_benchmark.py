from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

from personal_os_retrieval.indexer import LocalIndex, _chunks, _is_candidate, _TOKEN_RE, eligible_note, parse_frontmatter

STOPWORDS = {"a", "an", "and", "are", "as", "at", "be", "between", "can", "do", "does", "for", "from", "how", "in", "is", "it", "of", "on", "or", "the", "to", "what", "which", "who", "why", "with", "yet"}


def _tokens(text: str) -> set[str]:
    return {word.casefold() for word in _TOKEN_RE.findall(text) if word.casefold() not in STOPWORDS and len(word) > 1}


def _direct_note_baseline(root: Path, query: str, limit: int = 5) -> list[str]:
    """Transparent title/heading-only direct-file scan; not an old measured run."""
    q = _tokens(query)
    ranked: list[tuple[float, str]] = []
    for path in root.rglob("*.md"):
        relative = path.relative_to(root)
        if not _is_candidate(root, path):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        metadata = parse_frontmatter(text)
        if not eligible_note(metadata):
            continue
        if text.startswith("---\n"):
            end = text.find("\n---", 4)
            text = text[end + 4:] if end >= 0 else ""
        title, sections = _chunks(text)
        heading_text = " ".join(heading for heading, _ in sections)
        index_text = f"{relative.stem} {title} {heading_text}"
        words = _tokens(index_text)
        overlap = len(q & words)
        if overlap:
            score = overlap / max(1, len(q))
            ranked.append((score, relative.as_posix()))
    ranked.sort(key=lambda item: (-item[0], item[1].casefold()))
    return [path for _, path in ranked[:limit]]


def _metrics(runs: list[dict], method: str) -> dict:
    recalls = []
    reciprocal_ranks = []
    citation_correct = citation_total = top1_correct = 0
    evidence_supported = 0
    for run in runs:
        gold = set(run["gold"])
        found = run[method]
        recalls.append(len(gold & set(found)) / len(gold))
        rank = next((i for i, path in enumerate(found, 1) if path in gold), None)
        reciprocal_ranks.append(1 / rank if rank else 0.0)
        top1_correct += bool(found and found[0] in gold)
        citation_correct += sum(path in gold for path in found)
        citation_total += len(found)
        if any(anchor.casefold() in " ".join(run.get("excerpts", [])).casefold() for anchor in run.get("anchors", [])):
            evidence_supported += 1
    return {
        "recall_at_5": round(statistics.mean(recalls), 4) if recalls else 0,
        "mrr": round(statistics.mean(reciprocal_ranks), 4) if reciprocal_ranks else 0,
        "top1_source_citation_accuracy": round(top1_correct / len(runs), 4) if runs else 0,
        "source_citation_precision_at_5": round(citation_correct / citation_total, 4) if citation_total else 0,
        "evidence_anchor_coverage_not_answer_faithfulness": round(evidence_supported / len(runs), 4) if runs else 0,
    }


def _privacy_boundary_probe() -> dict:
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR") or None) as temp:
        root = Path(temp) / "vault"
        root.mkdir()
        (root / "Allowed.md").write_text(
            "---\nai_index: true\nconfidentiality: personal\n---\n# Allowed\neligiblecanarytoken\n![[Restricted.md]]\n![[Vault Attachment.pdf]]\n",
            encoding="utf-8",
        )
        (root / "Restricted.md").write_text(
            "---\nai_index: true\nconfidentiality: private\n---\n# Restricted\nprivatecanarytoken\n",
            encoding="utf-8",
        )
        (root / "Unmarked.md").write_text("# Unmarked\nunmarkedcanarytoken\n", encoding="utf-8")
        template_dir = root / "Templates"
        template_dir.mkdir()
        (template_dir / "Template.md").write_text(
            "---\nai_index: true\nconfidentiality: personal\n---\ntemplate-canary-token\n",
            encoding="utf-8",
        )
        (root / "Vault Attachment.pdf").write_bytes(b"attachmentcanarytoken")
        database = Path(temp) / "privacy.sqlite"
        with LocalIndex(root, database) as index:
            index.rebuild_or_update()
            checks = {
                "eligible_note_retrievable": bool(index.search("eligiblecanarytoken", limit=1)),
                "private_note_denied": not index.search("privatecanarytoken", limit=5),
                "unmarked_note_denied": not index.search("unmarkedcanarytoken", limit=5),
                "embedded_private_note_not_transcluded": not index.search("privatecanarytoken", limit=5),
                "attachment_not_indexed": not index.search("attachmentcanarytoken", limit=5),
                "template_folder_excluded": "Templates/Template.md" not in index.indexed_paths(),
            }
            eligible = set(index.indexed_paths())
            checks["only_eligible_markdown_paths_indexed"] = eligible == {"Allowed.md"}
            allowed = root / "Allowed.md"
            allowed.write_text(
                "---\nai_index: true\nconfidentiality: private\n---\n# Revoked\nrevocation-canary-token\n",
                encoding="utf-8",
            )
            checks["privacy_revocation_removes_results"] = not index.search("eligiblecanarytoken", limit=5)
            checks["privacy_revocation_purges_persisted_index_text"] = b"eligiblecanarytoken" not in database.read_bytes()
            return {"checks": checks, "violations": [name for name, passed in checks.items() if not passed]}


def run(vault: Path, database: Path, questions_path: Path) -> dict:
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    if len(questions) < 20:
        raise ValueError("Benchmark requires at least 20 questions")
    if len({q["id"] for q in questions}) != len(questions):
        raise ValueError("Benchmark question IDs must be unique")
    with LocalIndex(vault, database) as index:
        index_counts = index.rebuild_or_update()
        runs = []
        fts_latencies = []
        direct_latencies = []
        for question in questions:
            started = time.perf_counter()
            result_rows = index.search(question["query"], limit=5)
            fts_latencies.append((time.perf_counter() - started) * 1000)
            started = time.perf_counter()
            direct_paths = _direct_note_baseline(vault, question["query"])
            direct_latencies.append((time.perf_counter() - started) * 1000)
            fts_paths = [row["vault_relative_path"] for row in result_rows]
            runs.append({
                "id": question["id"],
                "type": question["type"],
                "query": question["query"],
                "gold": question["gold"],
                "fts": fts_paths,
                "direct_title_heading_scan": direct_paths,
                "anchors": question.get("anchors", []),
                "excerpts": [row["excerpt"] for row in result_rows],
            })
        eligible_paths = set(index.indexed_paths())
        unknown_gold_paths = sorted({path for q in questions for path in q["gold"] if path not in eligible_paths})
    def latency(values):
        ordered = sorted(values)
        if not ordered:
            return {"mean_ms": 0, "p50_ms": 0, "p95_ms": 0}
        p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
        return {"mean_ms": round(statistics.mean(ordered), 3), "p50_ms": round(statistics.median(ordered), 3), "p95_ms": round(p95, 3)}
    privacy_probe = _privacy_boundary_probe()
    return {
        "benchmark": "Personal OS local retrieval",
        "questions": len(questions),
        "index_counts": index_counts,
        "eligible_notes": len(eligible_paths),
        "unknown_or_ineligible_gold_paths": unknown_gold_paths,
        "methods": {
            "sqlite_fts5": {**_metrics([{**r, "fts": r["fts"]} for r in runs], "fts"), "latency": latency(fts_latencies)},
            "direct_title_heading_scan": {**_metrics([{**r, "direct_title_heading_scan": r["direct_title_heading_scan"]} for r in runs], "direct_title_heading_scan"), "latency": latency(direct_latencies)},
        },
        "privacy_boundary_probe": privacy_probe,
        "privacy_violations_in_this_benchmark": len(privacy_probe["violations"]),
        "answer_faithfulness": "not measured; benchmark does not call a model or generate answers",
        "baseline_note": "Direct title/heading scan is a reproducible comparison baseline; historical manual retrieval had no saved 20-question scores and cannot be compared numerically.",
        "external_services_used": False,
        "details": [{k: v for k, v in r.items() if k != "excerpts"} for r in runs],
    }


if __name__ == "__main__":
    plugin_root = Path(__file__).resolve().parents[1]
    default_vault = Path.home() / "Obsidian"
    vault = Path(sys.argv[1]) if len(sys.argv) > 1 else default_vault
    output = Path(sys.argv[3]) if len(sys.argv) > 3 else plugin_root / "benchmarks" / "latest-results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) > 2:
        result = run(vault, Path(sys.argv[2]), plugin_root / "benchmarks" / "questions.json")
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR") or None) as temp:
            result = run(vault, Path(temp) / "benchmark.sqlite", plugin_root / "benchmarks" / "questions.json")
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "details"}, ensure_ascii=False, indent=2))
