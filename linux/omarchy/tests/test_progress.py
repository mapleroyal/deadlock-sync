import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("progress", ROOT / "plugin/backend/progress.py")
progress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(progress)


def record(message, second=100):
    return {"MESSAGE": message, "__REALTIME_TIMESTAMP": str(second * 1_000_000)}


def summary(history=12, total=3):
    return record(f"gc: account 1 has {history} match(es) in history, {total} missing salts (~20s each)")


class ProgressParsingTests(unittest.TestCase):
    def test_preparing_has_no_invented_totals(self):
        value = progress.parse_records([record("Log files are being written to: /private/path")], "backfill")
        self.assertEqual(value["phase"], "preparing")
        self.assertIsNone(value["total"])
        self.assertNotIn("private", json.dumps(value))

    def test_processed_index_and_upload_count_are_distinct(self):
        value = progress.parse_records([
            summary(), record("gc: salt fetch failed for 2: temporary"),
            record("gc: ingested match 3 (2/3)", 102),
        ], "backfill")
        self.assertEqual((value["completed"], value["total"], value["uploaded"], value["failed"]), (2, 3, 1, 1))
        self.assertEqual(value["updated_at"], 102)
        self.assertEqual(value["history"], 12)

    def test_post_failure_advances_processed_but_not_uploaded(self):
        value = progress.parse_records([
            summary(), record("gc: ingested match 2 (1/3)"),
            record("gc: salt POST failed for 3: temporary"),
        ], "backfill")
        self.assertEqual((value["completed"], value["uploaded"], value["failed"]), (2, 1, 1))

    def test_truncated_prefix_does_not_invent_upload_counts(self):
        value = progress.parse_records([record("gc: ingested match 2 (7/10)")], "backfill")
        self.assertEqual((value["completed"], value["total"]), (7, 10))
        self.assertIsNone(value["uploaded"])
        self.assertIsNone(value["failed"])
        self.assertIsNone(value["history"])

    def test_new_account_summary_resets_current_pass(self):
        value = progress.parse_records([
            summary(), record("gc: ingested match 2 (1/3)"),
            summary(20, 4), record("gc: ingested match 3 (1/4)"),
        ], "backfill")
        self.assertEqual((value["completed"], value["total"], value["uploaded"], value["history"]), (1, 4, 1, 20))

    def test_ansi_binary_messages_and_invalid_values(self):
        message = list(b"\x1b[32mINFO\x1b[0m gc: ingested match 2 (1/3)")
        value = progress.parse_records([summary(), record(message), record([999]), record(None),
                                        record("gc: ingested match 2 (4/3)")], "backfill")
        self.assertEqual((value["completed"], value["uploaded"]), (1, 1))

    def test_fixed_terminal_phases(self):
        for text, phase in [
            ("gc: account 1 was rate-limited by Steam", "limited"),
            ("gc: no usable Steam account: secret", "failed"),
            ("gc: account 1 failed: <img src='private'>", "failed"),
            ("Done, all own matches processed.", "complete"),
        ]:
            value = progress.parse_records([summary(), record(text)], "backfill")
            self.assertEqual(value["phase"], phase)
            self.assertNotIn("private", json.dumps(value))
            self.assertNotIn("secret", json.dumps(value))

    def test_live_scan_phases_have_no_fake_percent(self):
        reading = record("memory: reading the game (pid 1)")
        complete = record("memory: read the game in 7.0s, salts for 2 matches", 110)
        self.assertEqual(progress.parse_records([reading], "live")["phase"], "reading")
        value = progress.parse_records([reading, complete], "live")
        self.assertEqual(value["phase"], "waiting")
        self.assertIsNone(value["total"])
        self.assertEqual(value["updated_at"], 110)

    def test_live_permission_failure_and_successful_upload_are_recognized(self):
        error = record("memory: not allowed to read Deadlock's memory: permission denied")
        self.assertEqual(progress.parse_records([error], "live")["phase"], "failed")
        upload = record("memory: ingested salts: <private data>", 120)
        value = progress.parse_records([error, upload], "live")
        self.assertEqual((value["phase"], value["updated_at"]), ("waiting", 120))
        self.assertNotIn("private", json.dumps(value))


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.units = {"deadlock-api-ingest-backfill.service": {"state": "activating", "invocation": "a" * 32}}

    def test_idle_and_invalid_invocation_never_read_journal(self):
        with patch.object(progress, "read_journal") as read:
            progress.snapshot({}, self.temp.name)
            self.units["deadlock-api-ingest-backfill.service"]["invocation"] = "bad;command"
            progress.snapshot(self.units, self.temp.name)
            read.assert_not_called()

    def test_runtime_cache_is_scoped_to_invocation_and_throttles_reads(self):
        with patch.object(progress, "read_journal", return_value=[summary()]) as read:
            first = progress.snapshot(self.units, self.temp.name)
            self.assertEqual(progress.snapshot(self.units, self.temp.name), first)
            self.assertEqual(read.call_count, 1)
            self.units["deadlock-api-ingest-backfill.service"]["invocation"] = "b" * 32
            progress.snapshot(self.units, self.temp.name)
            self.assertEqual(read.call_count, 2)
        cache = Path(self.temp.name) / "progress.json"
        self.assertEqual(cache.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("MESSAGE", cache.read_text())

    def test_timeout_is_only_missing_progress(self):
        with patch.object(progress, "read_journal", side_effect=subprocess.TimeoutExpired("journalctl", 3)):
            value = progress.snapshot(self.units, self.temp.name)
        self.assertEqual(value["mode"], "backfill")
        self.assertEqual(value["phase"], "preparing")

    def test_reader_scopes_query_and_bounds_records(self):
        output = json.dumps(record("gc: ingested match 2 (1/3)")) + "\n"
        with patch.object(progress.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")) as run:
            records = progress.read_journal("a" * 32)
        command = run.call_args.args[0]
        self.assertIn("_SYSTEMD_INVOCATION_ID=" + "a" * 32, command)
        self.assertIn("--lines=1000", command)
        self.assertNotIn("--all", command)
        self.assertEqual(len(records), 1)


if __name__ == "__main__":
    unittest.main()
