import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

try:
    from .test_codex_limits import USAGE
except ImportError:
    from test_codex_limits import USAGE


class GrokBotTests(unittest.TestCase):
    def _write_blob(self, path, value):
        path.write_text(json.dumps({"schemaVersion": 1, "value": value}), encoding="utf-8")

    def test_local_snapshots_count_activity_without_inventing_tokens(self):
        now = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
        base_ms = int(now.timestamp() * 1000)
        entries = [
            {"id": "u1", "kind": "message", "role": "user", "requestId": "r1",
             "timestampMs": base_ms},
            {"id": "a1", "kind": "send-message", "requestId": "r1",
             "timestampMs": base_ms + 1_000, "message": {"type": "text", "content": "one"}},
            {"id": "a2", "kind": "send-message", "requestId": "r1",
             "timestampMs": base_ms + 2_000, "message": {"type": "connector"}},
            {"id": "a3", "kind": "send-message", "requestId": "r1",
             "timestampMs": base_ms + 3_000, "message": {"type": "text", "content": "two"}},
            {"id": "u2", "kind": "message", "role": "user", "requestId": "r2",
             "timestampMs": base_ms + 400_000},
            {"id": "a4", "kind": "send-message", "requestId": "r2",
             "timestampMs": base_ms + 401_000, "message": {"type": "text", "content": "three"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_blob(root / "transcript.blob", {"entries": entries})
            self._write_blob(root / "transcript-copy.blob", {"entries": entries})
            self._write_blob(root / "roster.blob", {"rows": [{
                "id": "agent-1", "path": "/tmp/grok-project", "newestEntryId": "a4",
            }]})
            (root / "broken.blob").write_text("not json", encoding="utf-8")
            cache = {"v": USAGE._SCAN_CACHE_VERSION}
            with mock.patch.object(USAGE, "GROK_BOT_DIRS", [str(root)]):
                result = USAGE.scan_grok_bot(USAGE.range_bounds(), cache)
                again = USAGE.scan_grok_bot(USAGE.range_bounds(), cache)

        today = result["ranges"]["today"]
        self.assertEqual(today["sessions"], {"agent-1"})
        self.assertEqual(today["turns"], 2)
        self.assertEqual(today["calls"], 2)
        self.assertEqual(today["tools"], 1)
        self.assertEqual(today["duration"], 4)
        self.assertNotIn("tokens", today)
        self.assertEqual(again["ranges"]["today"]["calls"], 2)
        transcripts = [row for row in cache["grok_bot"].values()
                       if isinstance(row, dict) and row.get("kind") == "transcript"]
        self.assertEqual(sum(row.get("sid") == "agent-1" for row in transcripts), 1)
        self.assertTrue(all("project" not in row for row in transcripts))

    def test_quota_is_split_from_sand_usage(self):
        start = datetime.now().astimezone().replace(microsecond=0)
        reset = start + timedelta(days=7)
        quota = USAGE._normalize_grok_bot_quota({
            "hasNonZeroIncludedLimit": True,
            "usagePercent": 12.5,
            "currentPeriodStart": int(start.timestamp()),
            "nextResetTimestampUtc": int(reset.timestamp()),
            "grokPlanLabel": "X Premium+",
        }, identity={"account": "person@example.com"}, updated=1_800_000_000)

        self.assertTrue(quota["available"])
        self.assertEqual(quota["plan"], "X Premium+")
        self.assertEqual(quota["windows"][0]["used_pct"], 12.5)
        self.assertEqual(quota["windows"][0]["window_minutes"], 7 * 24 * 60)
        self.assertIsNone(quota["account"])

    def test_missing_included_allowance_does_not_report_fake_quota(self):
        quota = USAGE._normalize_grok_bot_quota({
            "hasNonZeroIncludedLimit": False,
            "usagePercent": 0,
        })
        self.assertEqual(quota, {})

    def test_bridge_usage_adds_official_tokens_models_and_cost(self):
        now = datetime.now().astimezone().replace(microsecond=0)
        event = {
            "timestamp": str(int(now.timestamp() * 1000)),
            "model": "grok-code-fast-1",
            "clientType": "sand",
            "tokenUsage": {
                "inputTokens": 100,
                "outputTokens": 20,
                "cacheReadTokens": 300,
                "cacheWriteTokens": 10,
                "totalCents": 1.5,
            },
        }
        payload = {
            "quotaFetched": True,
            "usageFetched": True,
            "updated": int(now.timestamp()),
            "sandUsage": {
                "hasNonZeroIncludedLimit": True,
                "usagePercent": 25,
            },
            "usageEventsDisplay": [event, dict(event)],
        }

        data = USAGE._grok_bot_provider_data(payload)

        today = data["usage"]["ranges"]["today"]
        self.assertTrue(data["available"])
        self.assertEqual(today["tokens"], 430)
        self.assertEqual(today["requests"], 1)
        self.assertEqual(today["cost"], 0.015)
        self.assertEqual(today["models"][0]["name"], "Grok Code Fast 1")
        self.assertEqual(data["usage"]["ranges"]["all"]["coverage"], "本年")

    def test_bridge_usage_is_kept_when_quota_is_unavailable(self):
        now = datetime.now().astimezone().replace(microsecond=0)
        payload = {
            "quotaFetched": False,
            "usageFetched": True,
            "usageEventsDisplay": [{
                "timestamp": str(int(now.timestamp() * 1000)),
                "model": "grok-code-fast-1",
                "tokenUsage": {"inputTokens": 10, "outputTokens": 5},
            }],
        }

        data = USAGE._grok_bot_provider_data(payload)

        self.assertFalse(data["available"])
        self.assertEqual(data["usage"]["ranges"]["today"]["tokens"], 15)

    def test_provider_scan_uses_native_grok_bot_without_cursor_login(self):
        native = {"available": True, "windows": [], "source": "grok-bot-api"}
        with mock.patch.object(
                USAGE, "_provider_quota_enabled",
                side_effect=lambda provider: provider == "grok_bot"), \
                mock.patch.object(USAGE, "_cursor_session", return_value=None), \
                mock.patch.object(
                    USAGE, "fetch_grok_bot_quota", return_value=native) as fetch_native, \
                mock.patch.object(USAGE, "fetch_cursor_quota") as fetch_cursor:
            result = USAGE.scan_provider_quotas()

        self.assertEqual(result["grok_bot"], native)
        fetch_native.assert_called_once_with()
        fetch_cursor.assert_not_called()

    def test_cursor_login_is_used_for_grok_bot_like_openusage(self):
        session = {"cookie": "WorkosCursorSessionToken=user%3A%3Atoken", "marker": "m1"}
        sand = {
            "hasNonZeroIncludedLimit": True,
            "usagePercent": 37.5,
            "grokPlanLabel": "X Premium+",
        }
        now = datetime.now().astimezone()
        events = [{
            "timestamp": str(int(now.timestamp() * 1000)),
            "model": "grok-code-fast-1",
            "clientType": "sand",
            "tokenUsage": {"inputTokens": 10, "outputTokens": 5, "totalCents": 20},
        }, {
            "timestamp": str(int(now.timestamp() * 1000)),
            "model": "cursor-small",
            "clientType": "cursor",
            "tokenUsage": {"inputTokens": 999, "outputTokens": 999, "totalCents": 999},
        }]
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(USAGE, "PROVIDER_QUOTA_CACHE", str(Path(tmp) / "quota.json")), \
                mock.patch.object(USAGE, "_cursor_session", return_value=session), \
                mock.patch.object(USAGE, "_provider_json_request", return_value=sand), \
                mock.patch.object(USAGE, "_fetch_sand_events_api2", return_value=events) as fetch_events, \
                mock.patch.object(USAGE, "_grok_bot_helper_sand_usage") as helper:
            first = USAGE.fetch_grok_bot_quota()
            second = USAGE.fetch_grok_bot_quota()

        self.assertEqual(first["source"], "cursor-sand-api")
        self.assertEqual(first["windows"][0]["used_pct"], 37.5)
        self.assertEqual(first["usage"]["ranges"]["today"]["tokens"], 15)
        self.assertEqual(second, first)
        fetch_events.assert_called_once()
        helper.assert_not_called()

    def test_active_account_id_comes_from_plaintext_account_index(self):
        account_id = "b" * 64
        with tempfile.TemporaryDirectory() as tmp:
            secrets = Path(tmp) / "sand-secrets.json"
            secrets.write_text(json.dumps({
                "cursor-accounts": json.dumps({
                    "active": account_id,
                    "accounts": {account_id: {"cursor-access-token": "ciphertext"}},
                }),
            }), encoding="utf-8")
            parsed = USAGE._grok_bot_active_account_id(str(secrets))

        self.assertEqual(parsed, account_id)

    def test_no_cursor_session_does_not_touch_grok_bot_keychain_helper(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(USAGE, "PROVIDER_QUOTA_CACHE", str(Path(tmp) / "quota.json")), \
                mock.patch.object(USAGE, "_cursor_session", return_value=None), \
                mock.patch.object(USAGE, "_grok_bot_helper_sand_usage") as helper:
            self.assertEqual(USAGE.fetch_grok_bot_quota(), {})

        helper.assert_not_called()

    def test_recent_usage_cache_survives_missing_authorization_marker(self):
        now = datetime.now().astimezone().replace(microsecond=0)
        cached = USAGE._grok_bot_provider_data({
            "quotaFetched": True,
            "usageFetched": True,
            "updated": int(now.timestamp()),
            "sandUsage": {
                "hasNonZeroIncludedLimit": True,
                "usagePercent": 25,
            },
            "usageEventsDisplay": [{
                "timestamp": str(int(now.timestamp() * 1000)),
                "model": "grok-bot-default",
                "tokenUsage": {"inputTokens": 100, "outputTokens": 20},
            }],
        })
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(USAGE, "PROVIDER_QUOTA_CACHE", str(Path(tmp) / "quota.json")):
            USAGE._save_provider_quota_cache("grok_bot", "old-marker", cached)
            with mock.patch.object(USAGE, "_grok_bot_active_account_id", return_value="a" * 64), \
                    mock.patch.object(USAGE, "_grok_bot_authorization_generation", return_value=None), \
                    mock.patch.object(USAGE, "_cursor_session", return_value=None):
                quota = USAGE.fetch_grok_bot_quota()

        self.assertTrue(quota["stale"])
        self.assertEqual(quota["source"], "cache")
        self.assertEqual(quota["usage"]["ranges"]["today"]["tokens"], 120)

    def test_historical_usage_has_no_quota_expiry(self):
        now = datetime.now().astimezone().replace(microsecond=0)
        cached = USAGE._grok_bot_provider_data({
            "quotaFetched": True,
            "usageFetched": True,
            "updated": int(now.timestamp()) - 2 * 60 * 60,
            "sandUsage": {
                "hasNonZeroIncludedLimit": True,
                "usagePercent": 25,
            },
            "usageEventsDisplay": [{
                "timestamp": str(int(now.timestamp() * 1000)),
                "model": "grok-bot-default",
                "tokenUsage": {"inputTokens": 100, "outputTokens": 20},
            }],
        })
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(USAGE, "PROVIDER_QUOTA_CACHE", str(Path(tmp) / "quota.json")):
            USAGE._save_provider_quota_cache(
                "grok_bot", "old-marker", cached,
                fetched_at=int(now.timestamp()) - 400 * 24 * 60 * 60,
            )
            with mock.patch.object(USAGE, "_grok_bot_active_account_id", return_value="a" * 64), \
                    mock.patch.object(USAGE, "_grok_bot_authorization_generation", return_value=None), \
                    mock.patch.object(USAGE, "_cursor_session", return_value=None):
                quota = USAGE.fetch_grok_bot_quota()

        self.assertFalse(quota["available"])
        self.assertEqual(quota["windows"], [])
        self.assertTrue(quota["stale"])
        self.assertEqual(quota["usage"]["ranges"]["today"]["tokens"], 120)

    def test_empty_cursor_sand_bundle_does_not_use_grok_bot_keychain(self):
        session = {"cookie": "WorkosCursorSessionToken=x", "marker": "m"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(USAGE, "PROVIDER_QUOTA_CACHE", str(Path(tmp) / "quota.json")), \
                mock.patch.object(USAGE, "_cursor_session", return_value=session), \
                mock.patch.object(USAGE, "_fetch_sand_bundle_via_cursor", return_value={}) as fetch, \
                mock.patch.object(USAGE, "_grok_bot_helper_sand_usage") as helper:
            self.assertEqual(USAGE.fetch_grok_bot_quota(), {})

        fetch.assert_called_once_with(session)
        helper.assert_not_called()

    def test_quota_failure_uses_one_hour_stale_cache(self):
        session = {"cookie": "WorkosCursorSessionToken=x", "marker": "cursor-marker"}
        marker = USAGE._provider_credential_marker("cursor-sand-v1", session["marker"])
        cached = {
            "available": True,
            "windows": [{"id": "grok-bot-period", "title": "本周期额度",
                         "used_pct": 20}],
            "source": "cursor-sand-api",
            "stale": False,
        }
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(USAGE, "PROVIDER_QUOTA_CACHE", str(Path(tmp) / "quota.json")):
            USAGE._save_provider_quota_cache(
                "grok_bot", marker, cached,
                fetched_at=int(datetime.now().timestamp()) - 10 * 60,
            )
            with mock.patch.object(USAGE, "_fetch_sand_bundle_via_cursor", return_value={}) as fetch:
                quota = USAGE.fetch_grok_bot_quota(session)

        fetch.assert_called_once_with(session)
        self.assertTrue(quota["stale"])
        self.assertEqual(quota["source"], "cache")


if __name__ == "__main__":
    unittest.main()
