import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SPEC = importlib.util.spec_from_file_location(
    "personal_os_retrieval_plugin", PLUGIN_ROOT / "__init__.py", submodule_search_locations=[str(PLUGIN_ROOT)],
)
plugin = importlib.util.module_from_spec(PLUGIN_SPEC)
sys.modules[PLUGIN_SPEC.name] = plugin
PLUGIN_SPEC.loader.exec_module(plugin)


class FakeLlm:
    def __init__(self):
        self.calls = 0
        self.call_options = []

    def complete_structured(self, **kwargs):
        self.calls += 1
        self.call_options.append(kwargs)
        payload = json.loads(kwargs["input"][0]["text"])
        source = payload["sources"][0]
        if self.calls == 1:
            parsed = {
                "status": "answered",
                "claims": [{
                    "text": "Unverified claim.", "kind": "confirmed_fact",
                    "evidence": [{"evidence_id": source["evidence_id"], "quote": "made up quote"}],
                }],
                "conflicts": [], "missing_information": [],
            }
        else:
            quote = source["text"].splitlines()[0]
            parsed = {
                "status": "answered",
                "claims": [{
                    "text": "The recorded fact is supported.", "kind": "confirmed_fact",
                    "evidence": [{"evidence_id": source["evidence_id"], "quote": quote}],
                }],
                "conflicts": [], "missing_information": [],
            }
        return SimpleNamespace(parsed=parsed)


class MalformedOnceLlm(FakeLlm):
    def complete_structured(self, **kwargs):
        if self.calls == 0:
            self.calls = 1
            self.call_options.append(kwargs)
            raise json.JSONDecodeError("malformed structured answer", "{", 0)
        return super().complete_structured(**kwargs)


class ConflictLlm:
    def complete_structured(self, **kwargs):
        payload = json.loads(kwargs["input"][0]["text"])
        sources = payload["sources"]
        date_sources = [item for item in sources if "2026-10-01" in item["text"] or "2026-10-15" in item["text"]]
        update = next(item for item in sources if "explicitly supersedes" in item["text"].casefold())
        current_status_quote = next(line.strip() for line in update["text"].splitlines() if "current project status is completed" in line.casefold())
        parsed = {
            "status": "conflicting_evidence",
            "claims": [{
                "text": "The explicitly superseding note records the current status as completed.",
                "kind": "confirmed_fact",
                "evidence": [{"evidence_id": update["evidence_id"], "quote": current_status_quote}],
            }],
            "conflicts": [{
                "issue": "The notes give different planned milestone dates.",
                "evidence_ids": [item["evidence_id"] for item in date_sources[:2]],
            }],
            "missing_information": [],
        }
        return SimpleNamespace(parsed=parsed)


class MissingLlm:
    def complete_structured(self, **kwargs):
        return SimpleNamespace(parsed={
            "status": "insufficient_evidence",
            "claims": [],
            "conflicts": [],
            "missing_information": ["The approved annual budget amount is not established."],
        })


class FakeContext:
    def __init__(self, settings):
        self.settings = settings
        self.tools = {}
        self.hooks = {}
        self.llm = FakeLlm()
        self.auxiliary_tasks = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_tool(self, name, handler, **kwargs):
        self.tools[name] = handler

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_auxiliary_task(self, key, **kwargs):
        self.auxiliary_tasks[key] = kwargs


class PluginPrivacyAndAnswerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        (self.vault / "Personal.md").write_text(
            "---\nai_index: true\nconfidentiality: personal\n---\n"
            "# Personal record\n\n## Verified fact\nThe appointment date is not recorded.\n",
            encoding="utf-8",
        )
        (self.vault / "RemoteApproved.md").write_text(
            "---\nai_index: true\nconfidentiality: personal\nai_remote_share: true\n---\n"
            "# Approved record\n\n## Public detail\nThis synthetic test record is approved for remote sharing.\n",
            encoding="utf-8",
        )
        self.home = self.root / "hermes-home"

    def tearDown(self):
        self.temp.cleanup()

    def _registered(self, settings=None):
        context = FakeContext({"vault_path": str(self.vault), **(settings or {})})
        plugin.register(context)
        return context

    def test_local_search_works_but_remote_transmission_is_denied_by_default(self):
        context = self._registered()
        with patch.object(plugin, "get_hermes_home", return_value=self.home):
            context.hooks["pre_api_request"](session_id="local-test", base_url="http://127.0.0.1:11434/v1")
            local = json.loads(context.tools["personal_os_search"]({"query": "appointment date"}, session_id="local-test"))
            self.assertTrue(local["success"])
            self.assertTrue(local["results"])
            self.assertIn("appointment date", local["results"][0]["excerpt"].casefold())

            context.hooks["pre_api_request"](session_id="remote-test", base_url="https://provider.example/v1")
            remote = json.loads(context.tools["personal_os_search"]({"query": "appointment date"}, session_id="remote-test"))
            self.assertFalse(remote["success"])
            serialized = json.dumps(remote)
            self.assertNotIn("appointment date", serialized)
            self.assertNotIn("Personal.md", serialized)

    def test_remote_needs_profile_and_per_note_approval(self):
        context = self._registered({"allow_remote_transmission": True})
        with patch.object(plugin, "get_hermes_home", return_value=self.home):
            context.hooks["pre_api_request"](session_id="remote-approved", base_url="https://provider.example/v1")
            result = json.loads(context.tools["personal_os_search"]({"query": "synthetic test record"}, session_id="remote-approved"))
            self.assertEqual([item["vault_relative_path"] for item in result["results"]], ["RemoteApproved.md"])
            self.assertNotIn("appointment date", json.dumps(result).casefold())

    def test_unknown_route_fails_closed_and_explicit_false_is_not_truthy(self):
        context = self._registered({"allow_remote_transmission": "true"})
        with patch.object(plugin, "get_hermes_home", return_value=self.home):
            result = json.loads(context.tools["personal_os_search"]({"query": "appointment"}, session_id="missing-route"))
        self.assertFalse(result["success"])
        self.assertNotIn("Personal.md", json.dumps(result))

    def test_answer_blocks_remote_auxiliary_route_and_fallback_before_reading_notes(self):
        context = self._registered()
        self.home.mkdir()
        config_path = self.home / "config.yaml"
        config_path.write_text(
            "auxiliary:\n  personal_os_retrieval_answer:\n    provider: custom\n"
            "    base_url: https://provider.example/v1\nfallback_providers: []\n",
            encoding="utf-8",
        )
        with patch.object(plugin, "get_hermes_home", return_value=self.home):
            context.hooks["pre_api_request"](session_id="aux-remote", base_url="http://127.0.0.1:11434/v1")
            remote_aux = json.loads(context.tools["personal_os_answer"](
                {"question": "What appointment date is recorded?"}, session_id="aux-remote",
            ))
            self.assertFalse(remote_aux["success"])
            self.assertNotIn("Personal.md", json.dumps(remote_aux))
            self.assertEqual(context.llm.calls, 0)

            config_path.write_text(
                "auxiliary:\n  personal_os_retrieval_answer:\n    provider: auto\n"
                "fallback_providers: [openrouter]\n",
                encoding="utf-8",
            )
            remote_fallback = json.loads(context.tools["personal_os_answer"](
                {"question": "What appointment date is recorded?"}, session_id="aux-remote",
            ))
        self.assertFalse(remote_fallback["success"])
        self.assertNotIn("Personal.md", json.dumps(remote_fallback))
        self.assertEqual(context.llm.calls, 0)

    def test_task_fallback_chain_is_remote_and_denied_before_reading_notes(self):
        context = self._registered()
        self.home.mkdir()
        (self.home / "config.yaml").write_text(
            "auxiliary:\n  personal_os_retrieval_answer:\n    provider: auto\n"
            "    fallback_chain:\n      - provider: openrouter\n        model: remote-test\n"
            "fallback_providers: []\n",
            encoding="utf-8",
        )
        with patch.object(plugin, "get_hermes_home", return_value=self.home):
            context.hooks["pre_api_request"](session_id="task-fallback-remote", base_url="http://127.0.0.1:11434/v1")
            result = json.loads(context.tools["personal_os_answer"](
                {"question": "What appointment date is recorded?"}, session_id="task-fallback-remote",
            ))
        self.assertFalse(result["success"])
        self.assertNotIn("Personal.md", json.dumps(result))
        self.assertEqual(context.llm.calls, 0)

    def test_remote_auxiliary_answer_uses_only_per_note_approved_evidence(self):
        context = self._registered({"allow_remote_transmission": True})
        self.home.mkdir()
        (self.home / "config.yaml").write_text(
            "auxiliary:\n  personal_os_retrieval_answer:\n    provider: auto\n"
            "    fallback_chain:\n      - provider: openrouter\n        model: remote-test\n"
            "fallback_providers: []\n",
            encoding="utf-8",
        )
        with patch.object(plugin, "get_hermes_home", return_value=self.home):
            context.hooks["pre_api_request"](session_id="aux-approved", base_url="http://127.0.0.1:11434/v1")
            answer = json.loads(context.tools["personal_os_answer"](
                {"question": "What synthetic test record is approved for remote sharing?"},
                session_id="aux-approved",
            ))
        self.assertTrue(answer["success"], answer)
        self.assertEqual({source["path"] for source in answer["sources"].values()}, {"RemoteApproved.md"})
        self.assertNotIn("Personal.md", json.dumps(answer))
        self.assertNotIn("appointment date", json.dumps(answer).casefold())

    def test_answer_pipeline_retries_invalid_quote_and_returns_valid_citation(self):
        context = self._registered()
        with patch.object(plugin, "get_hermes_home", return_value=self.home):
            context.hooks["pre_api_request"](session_id="answer-local", base_url="http://127.0.0.1:11434/v1")
            answer = json.loads(context.tools["personal_os_answer"]({"question": "What appointment date is recorded?"}, session_id="answer-local"))
        self.assertTrue(answer["success"])
        self.assertEqual(context.llm.calls, 2)
        self.assertEqual(answer["status"], "answered")
        citation = next(iter(answer["sources"]))
        self.assertEqual(answer["sources"][citation]["path"], "Personal.md")
        self.assertIn(f"[{citation}]", answer["answer"])
        self.assertIn("The appointment date is not recorded.", answer["sources"][citation]["quote"])
        self.assertEqual(context.llm.call_options[-1]["task"], "personal_os_retrieval_answer")

    def test_answer_pipeline_retries_json_parse_exception(self):
        context = self._registered()
        context.llm = MalformedOnceLlm()
        with patch.object(plugin, "get_hermes_home", return_value=self.home):
            context.hooks["pre_api_request"](session_id="malformed-local", base_url="http://127.0.0.1:11434/v1")
            answer = json.loads(context.tools["personal_os_answer"](
                {"question": "What appointment date is recorded?"}, session_id="malformed-local",
            ))
        self.assertTrue(answer["success"], answer)
        self.assertEqual(context.llm.calls, 2)
        self.assertEqual(answer["status"], "answered")

    def test_registers_cited_answer_auxiliary_task(self):
        context = self._registered()
        task = context.auxiliary_tasks["personal_os_retrieval_answer"]
        self.assertEqual(task["display_name"], "Personal OS cited answer")
        self.assertEqual(task["defaults"]["provider"], "auto")
        self.assertEqual(task["defaults"]["reasoning_effort"], "none")

    def test_conflicting_dates_preserve_explicitly_superseding_status(self):
        shared = chr(10).join(["---", "ai_index: true", "confidentiality: personal", "---", ""])
        def note(title, heading, body):
            return shared + chr(10).join([f"# {title}", "", f"## {heading}", body, ""])

        (self.vault / "Project-A.md").write_text(
            note("Project Evidence", "Milestone record", "The planned milestone date is 2026-10-01. The current project status was planned as of 2026-09-01."),
            encoding="utf-8",
        )
        (self.vault / "Project-B.md").write_text(
            note("Project Evidence", "Milestone record", "The planned milestone date is 2026-10-15. The current project status was in progress as of 2026-09-10."),
            encoding="utf-8",
        )
        (self.vault / "Project-Update.md").write_text(
            note("Project Update", "Current status", "This note explicitly supersedes earlier status records. The planned milestone date remains unresolved here. As of 2026-09-20, the current project status is completed."),
            encoding="utf-8",
        )
        context = self._registered()
        context.llm = ConflictLlm()
        with patch.object(plugin, "get_hermes_home", return_value=self.home):
            context.hooks["pre_api_request"](session_id="conflict-local", base_url="http://127.0.0.1:11434/v1")
            answer = json.loads(context.tools["personal_os_answer"](
                {"question": "What is the planned milestone date and current project status?"},
                session_id="conflict-local",
            ))
        self.assertTrue(answer["success"], answer)
        self.assertEqual(answer["status"], "conflicting_evidence")
        self.assertEqual(len(answer["conflicts"]), 1)
        self.assertIn("different planned milestone dates", answer["conflicts"][0]["issue"])
        cited_paths = {source["path"] for source in answer["sources"].values()}
        self.assertTrue({"Project-A.md", "Project-B.md", "Project-Update.md"}.issubset(cited_paths))
        self.assertIn("completed", answer["answer"].casefold())

    def test_missing_fact_returns_explicit_abstention_without_claims(self):
        body = chr(10).join([
            "---", "ai_index: true", "confidentiality: personal", "---", "# Budget", "",
            "## Annual amount", "No approved annual budget amount is recorded.", "",
        ])
        (self.vault / "Budget.md").write_text(body, encoding="utf-8")
        context = self._registered()
        context.llm = MissingLlm()
        with patch.object(plugin, "get_hermes_home", return_value=self.home):
            context.hooks["pre_api_request"](session_id="missing-local", base_url="http://127.0.0.1:11434/v1")
            answer = json.loads(context.tools["personal_os_answer"](
                {"question": "What approved annual budget amount is recorded?"},
                session_id="missing-local",
            ))
        self.assertTrue(answer["success"])
        self.assertEqual(answer["status"], "insufficient_evidence")
        self.assertEqual(answer["claims"], [])
        self.assertTrue(answer["missing_information"])


if __name__ == "__main__":
    unittest.main()
