import tempfile
import unittest
from pathlib import Path

from personal_os_retrieval.indexer import LocalIndex, eligible_note, parse_frontmatter


class PrivacyGateTests(unittest.TestCase):
    def test_only_explicit_true_and_allowed_confidentiality_are_eligible(self):
        self.assertTrue(eligible_note({"ai_index": True, "confidentiality": "personal"}))
        self.assertTrue(eligible_note({"ai_index": True, "confidentiality": "public"}))
        for metadata in (
            {"ai_index": False, "confidentiality": "personal"},
            {"ai_index": "true", "confidentiality": "personal"},
            {"ai_index": True, "confidentiality": "private"},
            {"confidentiality": "personal"},
        ):
            with self.subTest(metadata=metadata):
                self.assertFalse(eligible_note(metadata))

    def test_frontmatter_gate_is_exact_and_closed_by_default(self):
        self.assertEqual(
            parse_frontmatter("---\nai_index: true\nconfidentiality: personal\n---\ntext"),
            {"ai_index": True, "confidentiality": "personal"},
        )
        self.assertEqual(parse_frontmatter("# no frontmatter\ntext"), {})

    def test_private_and_unmarked_notes_never_enter_or_return_from_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Allowed.md").write_text(
                "---\nai_index: true\nconfidentiality: personal\nknowledge_status: confirmed\n---\n# Thesis\nApproved sample is not yet set.\n",
                encoding="utf-8",
            )
            (root / "Private.md").write_text(
                "---\nai_index: true\nconfidentiality: private\n---\n# Secret\nneedle-private-token\n",
                encoding="utf-8",
            )
            (root / "Unmarked.md").write_text("# Secret\nneedle-unmarked-token\n", encoding="utf-8")
            with LocalIndex(root, root / "index.sqlite") as index:
                index.rebuild_or_update()
                rows = index.search("approved sample", limit=5)
                self.assertEqual([row["vault_relative_path"] for row in rows], ["Allowed.md"])
                self.assertEqual(index.search("needle-private-token", limit=5), [])
                self.assertEqual(index.search("needle-unmarked-token", limit=5), [])
                self.assertEqual(index.indexed_paths(), ["Allowed.md"])

    def test_embed_targets_are_not_transcluded_or_indexed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Allowed.md").write_text(
                "---\nai_index: true\nconfidentiality: personal\n---\n# Thesis\nVisible evidence.\n![[Private Attachment.pdf]]\n![[Private Note]]\n",
                encoding="utf-8",
            )
            (root / "Private Note.md").write_text(
                "---\nai_index: true\nconfidentiality: private\n---\n# Secret\nembed-leak-token\n",
                encoding="utf-8",
            )
            (root / "Private Attachment.pdf").write_bytes(b"not indexed")
            with LocalIndex(root, root / "index.sqlite") as index:
                index.rebuild_or_update()
                self.assertEqual(index.search("embed-leak-token", limit=5), [])
                self.assertEqual(index.search("Private Attachment", limit=5), [])
    def test_private_revocation_removes_fts_hits_and_purges_persisted_text(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            note = root / "Revoked.md"
            secret = "revoked-private-cache-sentinel"
            note.write_text(
                f"---\nai_index: true\nconfidentiality: personal\n---\n# Record\n{secret}\n",
                encoding="utf-8",
            )
            database = root / "index.sqlite"
            with LocalIndex(root, database) as index:
                index.rebuild_or_update()
                self.assertEqual(len(index.search(secret, limit=1)), 1)
                note.write_text(
                    f"---\nai_index: true\nconfidentiality: private\n---\n# Record\n{secret}\n",
                    encoding="utf-8",
                )
                self.assertEqual(index.search(secret, limit=1), [])
                self.assertEqual(index.indexed_paths(), [])
            self.assertNotIn(secret.encode(), database.read_bytes())
            wal = Path(str(database) + "-wal")
            if wal.exists():
                self.assertNotIn(secret.encode(), wal.read_bytes())
    def test_multiword_paraphrase_falls_back_when_all_terms_do_not_cooccur(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Research.md").write_text(
                "---\nai_index: true\nconfidentiality: personal\n---\n# Research design\nThe final sample remains undecided.\n",
                encoding="utf-8",
            )
            with LocalIndex(root, root / "index.sqlite") as index:
                results = index.search("what is the final sample design question undecided", limit=5)
                self.assertTrue(results)
                self.assertEqual(results[0]["vault_relative_path"], "Research.md")
    def test_search_results_are_unique_by_source_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Repeated.md").write_text(
                "---\nai_index: true\nconfidentiality: personal\n---\n# Repeat\n\n## First\ncommon evidence unique words.\n\n## Second\ncommon evidence repeated words.\n",
                encoding="utf-8",
            )
            with LocalIndex(root, root / "index.sqlite") as index:
                results = index.search("common evidence", limit=5)
                paths = [row["vault_relative_path"] for row in results]
                self.assertEqual(paths, ["Repeated.md"])


class IncrementalIndexTests(unittest.TestCase):
    def test_updates_and_revocations_are_reflected_before_results_are_returned(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            note = root / "Thesis.md"
            note.write_text(
                "---\nai_index: true\nconfidentiality: personal\n---\n# Design\nInitial method evidence.\n",
                encoding="utf-8",
            )
            with LocalIndex(root, root / "index.sqlite") as index:
                index.rebuild_or_update()
                self.assertEqual(len(index.search("initial method", limit=5)), 1)
                note.write_text(
                    "---\nai_index: true\nconfidentiality: personal\n---\n# Design\nRevised sample remains undecided.\n",
                    encoding="utf-8",
                )
                self.assertEqual(index.search("initial method", limit=5), [])
                self.assertEqual(len(index.search("revised sample", limit=5)), 1)
                note.write_text(
                    "---\nai_index: true\nconfidentiality: private\n---\n# Design\nRevised sample remains undecided.\n",
                    encoding="utf-8",
                )
                self.assertEqual(index.search("revised sample", limit=5), [])
                self.assertEqual(index.indexed_paths(), [])

    def test_sections_return_heading_date_status_and_excerpt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Research.md").write_text(
                "---\nai_index: true\nconfidentiality: public\nknowledge_status: mixed\n---\n# Research\n\n## Methodology\nUse a common window only after it is approved.\n",
                encoding="utf-8",
            )
            with LocalIndex(root, root / "index.sqlite") as index:
                index.rebuild_or_update()
                row = index.search("common window approved", limit=1)[0]
                self.assertEqual(row["vault_relative_path"], "Research.md")
                self.assertEqual(row["heading"], "Methodology")
                self.assertEqual(row["knowledge_status"], "mixed")
                self.assertTrue(row["modified"])
                self.assertIn("common window", row["excerpt"])
                self.assertIsInstance(row["score"], float)


if __name__ == "__main__":
    unittest.main()
