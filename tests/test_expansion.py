import sqlite3
import tempfile
import unittest
from pathlib import Path

from personal_os_retrieval.indexer import LocalIndex, eligible_note, remote_eligible_note, secure_data_dir


class EvidenceExpansionTests(unittest.TestCase):
    def test_remote_approval_is_separate_from_local_index_eligibility(self):
        personal = {"ai_index": True, "confidentiality": "personal"}
        public = {"ai_index": True, "confidentiality": "public"}
        approved = {**personal, "ai_remote_share": True}
        self.assertTrue(eligible_note(personal))
        self.assertTrue(eligible_note(public))
        self.assertFalse(remote_eligible_note(personal))
        self.assertFalse(remote_eligible_note(public))
        self.assertTrue(remote_eligible_note(approved))
        self.assertFalse(remote_eligible_note({**approved, "confidentiality": "private"}))

    def test_expansion_reads_full_matching_section_with_stable_source_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            note = root / "Research.md"
            note.write_text(
                "---\nai_index: true\nconfidentiality: personal\n---\n"
                "# Research\n\n## Identification\nFirst exact fact.\n\n"
                "Second exact fact.\n\n## Other\nUnrelated fact.\n",
                encoding="utf-8",
            )
            with LocalIndex(root, root / "index.sqlite") as index:
                hit = index.search("identification exact fact", limit=1)[0]
                full = index.expand_section(hit, token_budget=500)
                again = index.expand_section(hit, token_budget=500)
                self.assertIn("First exact fact.", full["text"])
                self.assertIn("Second exact fact.", full["text"])
                self.assertNotIn("Unrelated fact.", full["text"])
                self.assertEqual(full["evidence_id"], again["evidence_id"])
                self.assertEqual(full["path"], "Research.md")
                self.assertEqual(full["heading"], "Identification")

    def test_expansion_applies_token_budget_and_does_not_transclude_embeds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            text = "ordinary evidence token " * 400
            (root / "Research.md").write_text(
                "---\nai_index: true\nconfidentiality: personal\n---\n"
                "# Research\n\n## Evidence\n" + text + "\n![[Hidden Note]]\n",
                encoding="utf-8",
            )
            (root / "Hidden Note.md").write_text(
                "---\nai_index: true\nconfidentiality: private\n---\n# Secret\nsecret-embed-token\n",
                encoding="utf-8",
            )
            with LocalIndex(root, root / "index.sqlite") as index:
                hit = index.search("ordinary evidence", limit=1)[0]
                expanded = index.expand_section(hit, token_budget=50)
                self.assertLessEqual(expanded["estimated_tokens"], 50)
                self.assertNotIn("secret-embed-token", expanded["text"])
                self.assertNotIn("Hidden Note", expanded["text"])

    def test_expansion_rechecks_eligibility_and_hash_before_returning_text(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            note = root / "Research.md"
            note.write_text(
                "---\nai_index: true\nconfidentiality: personal\n---\n# Research\n\n## Method\nLocal-only fact.\n",
                encoding="utf-8",
            )
            with LocalIndex(root, root / "index.sqlite") as index:
                hit = index.search("local-only fact", limit=1)[0]
                note.write_text(
                    "---\nai_index: true\nconfidentiality: private\n---\n# Research\n\n## Method\nLocal-only fact.\n",
                    encoding="utf-8",
                )
                with self.assertRaises(PermissionError):
                    index.expand_section(hit, token_budget=100)

    def test_direct_fallback_scans_titles_and_headings_not_only_fts_hits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Research.md").write_text(
                "---\nai_index: true\nconfidentiality: personal\n---\n"
                "# Thesis\n\n## Identification strategy\nThe approved window is not specified.\n",
                encoding="utf-8",
            )
            with LocalIndex(root, root / "index.sqlite") as index:
                direct = index.direct_search("identification strategy question", limit=2)
                self.assertEqual(len(direct), 1)
                self.assertEqual(direct[0]["vault_relative_path"], "Research.md")
                expanded = index.expand_section(direct[0], token_budget=200)
                self.assertIn("approved window is not specified", expanded["text"])

    def test_direct_note_reading_uses_full_note_sections_under_one_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Research.md").write_text(
                "---\nai_index: true\nconfidentiality: personal\n---\n"
                "# Thesis\n\n## Identification strategy\nThe sample is not yet approved.\n\n"
                "## Evidence status\nThe formal research question remains unconfirmed.\n",
                encoding="utf-8",
            )
            with LocalIndex(root, root / "index.sqlite") as index:
                candidates = index.direct_search("identification strategy", limit=1)
                evidence = index.read_note(candidates[0], token_budget=200)
                self.assertEqual([item["heading"] for item in evidence], ["Identification strategy", "Evidence status"])
                self.assertLessEqual(sum(item["estimated_tokens"] for item in evidence), 200)

    def test_secure_data_directory_applies_restrictive_os_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            protected = Path(temp) / "private-index"
            secure_data_dir(protected)
            self.assertTrue(protected.is_dir())
            database = protected / "index.sqlite"
            connection = sqlite3.connect(str(database))
            connection.execute("CREATE TABLE probe (value TEXT)")
            connection.execute("INSERT INTO probe VALUES ('ok')")
            connection.commit()
            connection.close()
            connection = sqlite3.connect(str(database))
            self.assertEqual(connection.execute("SELECT value FROM probe").fetchone()[0], "ok")
            connection.close()
            import os
            if os.name == "nt":
                import subprocess
                system_root = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
                icacls = system_root / "icacls.exe"
                self.assertTrue(icacls.is_file())
                acl = subprocess.run([str(icacls), str(protected)], check=True, capture_output=True, text=True).stdout.casefold()
                db_acl = subprocess.run([str(icacls), str(database)], check=True, capture_output=True, text=True).stdout.casefold()
                self.assertIn("system", acl)
                self.assertIn("administrators", acl)
                self.assertIn("system", db_acl)
                self.assertIn("administrators", db_acl)
                self.assertNotIn("authenticated users", acl)
                self.assertNotIn("everyone", acl)
                self.assertNotIn("builtin\\users", acl)
                self.assertNotIn("authenticated users", db_acl)
                self.assertNotIn("everyone", db_acl)
                self.assertNotIn("builtin\\users", db_acl)
            else:
                self.assertEqual(protected.stat().st_mode & 0o777, 0o700)

    def test_crlf_frontmatter_is_never_indexed_as_document_text(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            note = root / "CRLF.md"
            note.write_bytes(
                b"---\r\nai_index: true\r\nconfidentiality: personal\r\n---\r\n"
                b"# Research\r\n\r\n## Evidence\r\nOnly the note body is searchable.\r\n"
            )
            with LocalIndex(root, root / "index.sqlite") as index:
                self.assertEqual(index.search("confidentiality personal", limit=1), [])
                self.assertEqual(len(index.search("note body searchable", limit=1)), 1)


if __name__ == "__main__":
    unittest.main()
