from pathlib import Path
import tempfile
import unittest

from llm_harness.config import ModelPriceConfig
from llm_harness.metrics import AdapterMetricsSink, UsageContext, UsageLedger


class MetricsTests(unittest.TestCase):
    def test_adapter_metric_uses_configured_price_and_persists_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = UsageLedger(Path(tmp))
            context = UsageContext("critic", "deepseek-critic", "deepseek", "deepseek-chat", "model_json")
            sink = AdapterMetricsSink(
                ledger,
                context,
                (ModelPriceConfig("deepseek", "deepseek-chat", 2.0, 4.0),),
            )
            sink("outbound", {"adapter": "model-json-critic"})
            sink(
                "inbound",
                {
                    "request_id": "request-1",
                    "latency_ms": 125,
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "total_tokens": 1500,
                    "ok": True,
                },
            )
            events = ledger.events()
            summary = ledger.summary()["groups"][0]

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].cost_amount, 0.004)
        self.assertEqual(events[0].cost_source, "configured_price")
        self.assertEqual(summary["total_tokens"], 1500)
        self.assertEqual(summary["costs"], {"USD": 0.004})

    def test_acp_usage_update_and_prompt_response_merge_into_one_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = UsageLedger(Path(tmp))
            sink = AdapterMetricsSink(
                ledger,
                UsageContext("worker", "codex", "openai", "gpt-test", "acp", task_id="T1"),
            )
            sink("outbound", {"jsonrpc": "2.0", "id": 3, "method": "session/prompt"})
            sink(
                "inbound",
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "session-1",
                        "update": {
                            "sessionUpdate": "usage_update",
                            "used": 53000,
                            "size": 200000,
                            "cost": {"amount": 0.045, "currency": "USD"},
                        },
                    },
                },
            )
            sink(
                "inbound",
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "result": {
                        "stopReason": "end_turn",
                        "usage": {"inputTokens": 35000, "outputTokens": 12000, "totalTokens": 53000},
                    },
                },
            )
            event = ledger.events()[0]

        self.assertEqual(event.invocation_id, "session-1")
        self.assertEqual(event.input_tokens, 35000)
        self.assertEqual(event.context_size_tokens, 200000)
        self.assertEqual(event.cost_amount, 0.045)
        self.assertEqual(event.cost_source, "provider")

    def test_quality_events_do_not_inflate_invocation_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = UsageLedger(Path(tmp))
            context = UsageContext("worker", "claude", "anthropic", "sonnet", "claude_code")
            ledger.record_quality(context, 1.0, "done")
            ledger.record_quality(context, 0.0, "rework_required")
            summary = ledger.summary()["groups"][0]

        self.assertEqual(summary["invocations"], 0)
        self.assertEqual(summary["quality_score"], 0.5)
        self.assertEqual(summary["quality_samples"], 2)


if __name__ == "__main__":
    unittest.main()
