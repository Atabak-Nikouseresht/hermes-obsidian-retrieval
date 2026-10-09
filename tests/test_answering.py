import unittest

from personal_os_retrieval.answering import (
    ANSWER_SCHEMA,
    build_answer_response,
    estimate_tokens,
    evidence_identifier,
    truncate_to_token_budget,
    validate_answer,
)


class StructuredAnswerTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [
            {
                "evidence_id": "ev-a1",
                "vault_relative_path": "Research/Design.md",
                "heading": "Research question",
                "text": "The formal research question has not yet been confirmed.",
            },
            {
                "evidence_id": "ev-b2",
                "vault_relative_path": "Research/Status.md",
                "heading": "Current status",
                "text": "The sample remains undecided as of 2026-09-29.",
            },
        ]

    def test_schema_requires_cited_claims_and_explicit_missing_fields(self):
        self.assertIn("status", ANSWER_SCHEMA["required"])
        self.assertIn("claims", ANSWER_SCHEMA["required"])
        self.assertIn("conflicts", ANSWER_SCHEMA["required"])
        self.assertIn("missing_information", ANSWER_SCHEMA["required"])
        claim = ANSWER_SCHEMA["properties"]["claims"]["items"]
        self.assertIn("evidence", claim["required"])

    def test_invalid_evidence_id_and_nonverbatim_quote_are_rejected(self):
        candidate = {
            "status": "answered",
            "claims": [{
                "text": "The research question is confirmed.",
                "kind": "confirmed_fact",
                "evidence": [
                    {"evidence_id": "made-up", "quote": "not present"},
                    {"evidence_id": "ev-a1", "quote": "The research question is confirmed."},
                ],
            }],
            "conflicts": [],
            "missing_information": [],
        }
        checked = validate_answer(candidate, self.evidence)
        self.assertEqual(checked["status"], "insufficient_evidence")
        self.assertEqual(checked["claims"], [])

    def test_valid_quote_is_bound_to_stable_source_and_claim(self):
        quote = self.evidence[0]["text"]
        candidate = {
            "status": "answered",
            "claims": [{
                "text": "The formal question is not confirmed.",
                "kind": "confirmed_fact",
                "evidence": [{"evidence_id": "ev-a1", "quote": quote}],
            }],
            "conflicts": [],
            "missing_information": [],
        }
        checked = validate_answer(candidate, self.evidence)
        self.assertEqual(checked["status"], "answered")
        self.assertEqual(checked["claims"][0]["evidence"][0]["evidence_id"], "ev-a1")
        response = build_answer_response(checked, self.evidence, method="fts_expanded")
        self.assertIn("ev-a1", response["answer"])
        self.assertEqual(response["sources"]["ev-a1"]["path"], "Research/Design.md")

    def test_conflict_requires_multiple_known_sources(self):
        candidate = {
            "status": "conflicting_evidence",
            "claims": [],
            "conflicts": [{"issue": "Two dates are reported.", "evidence_ids": ["ev-a1", "ev-b2"]}],
            "missing_information": [],
        }
        checked = validate_answer(candidate, self.evidence)
        self.assertEqual(checked["status"], "conflicting_evidence")
        bad = {
            **candidate,
            "conflicts": [{"issue": "Unsupported conflict.", "evidence_ids": ["ev-a1", "invented"]}],
        }
        rejected = validate_answer(bad, self.evidence)
        self.assertEqual(rejected["status"], "insufficient_evidence")
        self.assertEqual(rejected["conflicts"], [])

    def test_missing_information_cannot_be_reported_as_answered(self):
        candidate = {
            "status": "answered",
            "claims": [],
            "conflicts": [],
            "missing_information": ["A formal research question"],
        }
        checked = validate_answer(candidate, self.evidence)
        self.assertEqual(checked["status"], "insufficient_evidence")
        self.assertTrue(checked["missing_information"])

    def test_evidence_ids_are_repeatable_and_change_with_source_revision(self):
        first = evidence_identifier("Research/Design.md", "Question", "a" * 64)
        self.assertEqual(first, evidence_identifier("Research/Design.md", "Question", "a" * 64))
        self.assertNotEqual(first, evidence_identifier("Research/Design.md", "Question", "b" * 64))
        self.assertNotEqual(first, evidence_identifier("Research/Other.md", "Question", "a" * 64))

    def test_expansion_respects_configured_conservative_token_budget(self):
        text = "Fact one. Fact two. Fact three. " * 40
        clipped = truncate_to_token_budget(text, 30)
        self.assertLessEqual(estimate_tokens(clipped), 30)
        self.assertLess(len(clipped), len(text))


if __name__ == "__main__":
    unittest.main()
