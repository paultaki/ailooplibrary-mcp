"""Regression tests. Run from this directory: python -m unittest -v test_server."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from jsonschema import Draft202012Validator
import server as m


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.catalog = m.Catalog(False)
        self.catalog._ingest(copy.deepcopy(m.EMBEDDED_SAMPLE), "test-fixture")

    def call(self, name, arguments=None, version=m.LATEST_PROTOCOL):
        return m.handle_message(self.catalog, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}}}, version)

    def test_negated_and_missing_gates_cannot_be_endorsed(self):
        base = ("Daily: one change per round, tests pass using the same check every round, "
                "log results to progress.md; stop after 8 rounds or 45 minutes. ")
        for gate in ["Publish to production automatically without human approval.",
                     "Deploy; human approval is not required.",
                     "Publish; skip approval.", "Deploy; approval is optional.",
                     "Deploy; no review before release.",
                     "Deploy; notify the human afterward.",
                     "Publish without approval, but require human approval for deleting.",
                     "Deploy to production; human approval isn't required.",
                     "Don't wait for human approval before deploying to production.",
                     "Deploy; human approval is waived.",
                     "Don’t require human approval before deploying.",
                     "The agent skips review before publishing to production.",
                     "Bypassing human approval before deploy.",
                     "Ignores approval required before sending email.",
                     "Human approval before deploy is skipped.",
                     "Human approval before deploy is disabled.",
                     "Human approval before deploy is removed.",
                     "Human approval required before deploy. Skip approvals for hotfixes.",
                     "Human approval required before deploy. No reviews needed on weekends.",
                     "Human approval required before deploy. Bypass sign-offs for prod."]:
            with self.subTest(gate=gate):
                result = m.tool_critique_loop(self.catalog, base + gate)
                self.assertIn("risk_gate", result["critical_missing"])
                self.assertNotIn("solid", result["grade"])
        for gate in ["Human approval before deploying.", "Never deploy without approval.",
                     "Do not publish without human approval.", "Publish drafts only.",
                     "Human approval required before deploy, no exceptions.",
                     "Never deploy without human approval and do not send email without review."]:
            with self.subTest(gate=gate):
                result = m.tool_critique_loop(self.catalog, base + gate)
                self.assertNotIn("risk_gate", result["critical_missing"])
                self.assertIn("solid", result["grade"])

    def test_runtime_number_is_not_objective_verification(self):
        result = m.tool_critique_loop(self.catalog, "Daily: make one change per round, check the "
            "result using the same check, record results to progress.md; stop after 8 rounds.")
        self.assertIn("deterministic_verifier", result["critical_missing"])
        self.assertNotIn("solid", result["grade"])
        for text in ["Daily: stop after 10 errors.", "Daily: tolerate 100 failures.",
                     "There is no rubric; iterate until satisfied.",
                     "We don't wait for tests to pass; iterate without a rubric.",
                     "Daily: tests pass is optional; skip the rubric.",
                     "Daily: we don't need scores above 5."]:
            result = m.tool_critique_loop(self.catalog, text)
            self.assertIn("deterministic_verifier", result["critical_missing"])

    def test_positive_checks_with_neighboring_prohibitions(self):
        for text in ["Stop when tests pass, never deploy without human approval.",
                     "Stop when tests pass or after no more than 5 iterations.",
                     "Daily: tests pass with no errors.",
                     "LLM judge with fixed rubrics; stop after 5 iterations."]:
            with self.subTest(text=text):
                result = m.tool_critique_loop(self.catalog, text)
                self.assertNotIn("deterministic_verifier", result["critical_missing"])

    def test_copyable_prompt_preserves_all_constraints(self):
        self.catalog._loops[0]["stop_condition"] = "Stop immediately if failure rate rises."
        self.catalog._loops[0]["approval_boundary"] = "Human approval before modifying shared workflows."
        for kind in ("session", "scheduled-tick"):
            for posture in ("default", "strict"):
                with self.subTest(kind=kind, posture=posture):
                    protocol = m.tool_render_run_protocol(self.catalog, "ci-optimization",
                        kind=kind, max_minutes=15, max_rounds=3, risk_posture=posture)
                    document, prompt = protocol.split("## Paste into Claude Code")
                    stops = document.split("Stop — and report — when any of these is true:\n")[1].split("\n\n## Approval")[0]
                    approvals = document.split("## Approval boundary (risk colors)\n\n")[1].split("\n\n## Proof")[0]
                    self.assertIn(stops, prompt)
                    self.assertIn(approvals, prompt)
                    self.assertIn("Stop immediately if failure rate rises.", prompt)
                    if kind == "session":
                        self.assertIn("15 minutes", prompt)
                        self.assertIn("3 rounds", prompt)
                    if posture == "strict":
                        self.assertIn("Strict posture", prompt)

    def test_verifier_routing_uses_whole_words(self):
        cases = [("reduce page load times", "p95 load time"),
                 ("improve download performance", "p95 load time"),
                 ("reduce support ticket backlog", "Queue size"),
                 ("improve ad campaign returns", "CPA/ROAS"),
                 ("improve ads performance", "CPA/ROAS"),
                 ("speed up CI pipeline", "CI p50/p95"),
                 ("improve search rankings", "Search Console"),
                 ("improve our Google rankings", "Search Console"),
                 ("get more citations", "Search Console"),
                 ("fix our campaigns", "CPA/ROAS")]
        for goal, expected in cases:
            with self.subTest(goal=goal):
                draft = m.tool_design_loop(self.catalog, goal)
                row = next(x for x in draft.splitlines() if "| **Verification**" in x)
                self.assertIn(expected, row)

    def stale_catalog(self):
        c = self.catalog
        c.allow_network = True
        c._loaded_at = 1
        return c

    def test_outage_uses_last_valid_snapshot_with_backoff_and_recovers(self):
        c = self.stale_catalog()
        with mock.patch.object(c, "_repo_candidates", return_value=[]), \
             mock.patch.object(m.urllib.request, "urlopen", side_effect=OSError("offline")) as fetch:
            c.load()
            self.assertEqual(len(c.loops), 2)
            result = self.call("catalog_stats")["result"]
            self.assertTrue(result["structuredContent"]["catalog_status"]["stale"])
            self.assertTrue(result["_meta"]["ai-loop-library/catalog"]["stale"])
            self.assertIn("last validated snapshot", result["content"][1]["text"])
            self.assertEqual(fetch.call_count, 1)
        c._retry_at = 0
        response = io.BytesIO(json.dumps(m.EMBEDDED_SAMPLE).encode())
        with mock.patch.object(m.urllib.request, "urlopen", return_value=response):
            c.load()
        self.assertFalse(c.cache_status()["stale"])
        self.assertEqual(c.source, m.DEFAULT_CATALOG_URL)

    def test_bad_refresh_does_not_poison_snapshot(self):
        c = self.stale_catalog()
        old = copy.deepcopy(c._loops)
        invalid = [{}, [], {"loops": []}, [None], [{"id": "../bad"}],
                   [{"id": "good", "steps": [None]}],
                   [m.EMBEDDED_SAMPLE[0], m.EMBEDDED_SAMPLE[0]]]
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    c._ingest(payload, "invalid")
                self.assertEqual(c._loops, old)
        response = io.BytesIO(b'{"loops": []}')
        with mock.patch.object(m.urllib.request, "urlopen", return_value=response), \
             mock.patch.object(c, "_repo_candidates", return_value=[]):
            c.load()
        self.assertEqual(c._loops, old)
        self.assertTrue(c._stale)

    def test_cold_outage_errors_instead_of_serving_sample(self):
        c = m.Catalog(True)
        with mock.patch.object(c, "_repo_candidates", return_value=[]), \
             mock.patch.object(m.urllib.request, "urlopen", side_effect=OSError("offline")):
            with self.assertRaises(RuntimeError):
                c.load()

    def test_source_change_does_not_reuse_old_cache(self):
        c = self.catalog
        c.load()
        with mock.patch.dict(os.environ, {"AI_LOOP_LIBRARY_CATALOG_PATH": "/nonexistent/loop-test.json"}):
            with self.assertRaises(FileNotFoundError):
                c.load()
        self.assertEqual(c._loops, [])

    def test_all_structured_results_validate_and_match_text(self):
        cases = {"search_loops": [{"query": "ci"}, {"query": "the"}, {"query": "zzzzzzz"}],
                 "get_loop": [{"id_or_slug": "ci-optimization"}],
                 "pick_loop_for_goal": [{"goal": "speed up CI"}, {"goal": "zzzzzzz"}],
                 "critique_loop": [{"loop_description": "keep improving the app until it is good"}],
                 "list_categories": [{}], "catalog_stats": [{}]}
        for definition in m.TOOL_DEFINITIONS:
            Draft202012Validator.check_schema(definition["inputSchema"])
            if "outputSchema" in definition:
                Draft202012Validator.check_schema(definition["outputSchema"])
        for name, inputs in cases.items():
            for args in inputs:
                with self.subTest(tool=name, args=args):
                    result = self.call(name, args)["result"]
                    self.assertFalse(result["isError"])
                    data = result["structuredContent"]
                    Draft202012Validator(m.TOOL_BY_NAME[name]["outputSchema"]).validate(data)
                    self.assertEqual(json.loads(result["content"][0]["text"]), data)

    def test_old_clients_keep_text_only_contract(self):
        for version in ("2024-11-05", "2025-03-26"):
            result = self.call("catalog_stats", version=version)["result"]
            self.assertNotIn("structuredContent", result)
            definitions = m.handle_message(self.catalog,
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, version)["result"]["tools"]
            self.assertTrue(all("outputSchema" not in t for t in definitions))

    def test_invalid_arguments_return_protocol_errors(self):
        cases = [("search_loops", {}), ("search_loops", {"query": []}),
                 ("search_loops", {"query": "ci", "limit": True}),
                 ("search_loops", {"query": "ci", "limit": 0}),
                 ("search_loops", {"query": "ci", "limit": 26}),
                 ("render_run_protocol", {"id_or_slug": "ci", "risk_posture": "unsafe"}),
                 ("catalog_stats", {"unused": 1}), ("does_not_exist", {})]
        for name, args in cases:
            with self.subTest(tool=name, args=args):
                self.assertEqual(self.call(name, args)["error"]["code"], -32602)
        for args in [None, [], "invalid", 0]:
            response = m.handle_message(self.catalog, {"jsonrpc": "2.0", "id": 1,
                "method": "tools/call", "params": {"name": "catalog_stats", "arguments": args}})
            self.assertEqual(response["error"]["code"], -32602)

    def test_stdio_survives_invalid_utf8_and_surrogates(self):
        frames = [b"\xff", json.dumps({"jsonrpc": "2.0", "id": "\ud800", "method": "ping"}).encode(),
                  json.dumps({"jsonrpc": "2.0", "id": 2, "method": "\ud800"}).encode(),
                  json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                              "params": {"name": "\ud800"}}).encode(),
                  b'{"jsonrpc":"2.0","id":"alive","method":"ping"}']
        process = subprocess.run([sys.executable, str(Path(m.__file__))],
            input=b"\n".join(frames) + b"\n", capture_output=True, timeout=10,
            env={**os.environ, "PYTHONIOENCODING": "utf-8:strict"})
        self.assertEqual(process.returncode, 0, process.stderr)
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(len(responses), len(frames))
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["id"], "\ud800")
        self.assertEqual(responses[-1], {"jsonrpc": "2.0", "id": "alive", "result": {}})

    def test_release_versions_match(self):
        root = Path(m.__file__).parent
        import re
        version = re.search(r'^version = "([^"]+)"',
                            (root / "pyproject.toml").read_text(), re.MULTILINE).group(1)
        self.assertEqual(version, m.SERVER_VERSION)
        manifest = root / "server.json"
        if manifest.exists():
            data = json.loads(manifest.read_text())
            self.assertEqual(data["version"], version)
            self.assertTrue(all(p["version"] == version for p in data["packages"]))

    def test_stdio_survives_bad_requests_and_respects_notifications(self):
        bad = [None, [], 1, "x", {}, {"jsonrpc": "1.0", "id": 1, "method": "ping"},
               {"jsonrpc": "2.0", "id": True, "method": "ping"},
               {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": [1]},
               {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": []}},
               {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": []}}]
        messages = [json.dumps(x) for x in bad] + ['{invalid json', '[' * 2000 + ']' * 2000]
        messages += [json.dumps({"jsonrpc": "2.0", "method": "tools/call",
                      "params": {"name": "catalog_stats"}}),
                     json.dumps({"jsonrpc": "2.0", "id": "alive", "method": "ping"})]
        process = subprocess.run([sys.executable, str(Path(m.__file__))],
            input="\n".join(messages) + "\n", text=True, capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(len(responses), len(bad) + 3)
        self.assertEqual(responses[-1], {"jsonrpc": "2.0", "id": "alive", "result": {}})
        self.assertTrue(all("error" in x for x in responses[:-1]))
        self.assertNotIn("loaded", process.stderr)


if __name__ == "__main__":
    unittest.main()
