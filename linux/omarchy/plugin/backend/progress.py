"""Bounded, read-only progress from the running service's own journal invocation.

Only fixed phases and numeric counters leave this module. Match/account IDs,
salts and arbitrary log text never enter the UI or the small runtime cache.
The upstream backfill numerator is a processed index, not an upload count.
"""
import json
from pathlib import Path
import re
import subprocess
import time

RUNNING = {"active", "activating", "deactivating"}
LIMIT = 1000
MAX_OUTPUT = 8 * 1024 * 1024
CACHE_SECONDS = 6
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
HISTORY = re.compile(r"gc: account \d+ has (\d+) match\(es\) in history, (\d+) missing salts")
INGESTED = re.compile(r"gc: ingested match \d+ \((\d+)/(\d+)\)")
COUNT_LIMIT = 10_000_000


def empty(mode=""):
    return {"mode": mode, "phase": "preparing" if mode == "backfill" else "watching",
            "completed": None, "total": None, "uploaded": None, "failed": None,
            "history": None, "updated_at": None, "issue": ""}


def parse_records(records, mode):
    result = empty(mode)
    baseline = False
    for record in records:
        if not isinstance(record, dict):
            continue
        message = record.get("MESSAGE")
        if isinstance(message, list) and len(message) <= 4096:
            try:
                message = bytes(message).decode("utf-8", "replace")
            except (TypeError, ValueError):
                continue
        if not isinstance(message, str) or len(message) > 4096:
            continue
        message = ANSI.sub("", message)
        recognized = False
        if mode == "backfill":
            history = HISTORY.search(message)
            ingested = INGESTED.search(message)
            if history:
                history_count, total = map(int, history.groups())
                if not 0 <= total <= history_count <= COUNT_LIMIT:
                    continue
                result.update(phase="recovering", completed=0, total=total,
                              uploaded=0, failed=0, history=history_count)
                baseline = True
                recognized = True
            elif ingested:
                completed, total = map(int, ingested.groups())
                if not 0 < completed <= total <= COUNT_LIMIT:
                    continue
                if result["total"] != total:
                    # A missing/truncated account summary cannot justify totals
                    # inferred by counting only the tail of successful uploads.
                    baseline = False
                    result.update(history=None, uploaded=None, failed=None)
                result.update(phase="recovering", completed=completed, total=total)
                if baseline:
                    result["uploaded"] += 1
                recognized = True
            elif any(marker in message for marker in (
                    "gc: salt fetch failed for ", "gc: salt POST failed for ",
                    "gc: salt POST task panicked:")):
                if baseline:
                    result["failed"] += 1
                if result["completed"] is not None:
                    result["completed"] = min(result["total"], result["completed"] + 1)
                recognized = True
            elif ("rate-limited by Steam" in message or "Steam rate limit reached." in message):
                result["phase"] = "limited"
                recognized = True
            elif ("gc: no usable Steam account" in message or
                  re.search(r"gc: account \d+ failed:", message) or
                  "could not be processed. Run this again to retry." in message):
                result["phase"] = "failed"
                recognized = True
            elif "Done, all own matches processed." in message:
                result["phase"] = "complete"
                recognized = True
        elif mode == "live":
            if "memory: reading the game (pid " in message:
                result["phase"] = "reading"
                recognized = True
            elif "memory: read the game in " in message:
                result["phase"] = "waiting"
                recognized = True
            elif any(marker in message for marker in (
                    "memory: failed", "memory: cannot", "memory: not allowed to read Deadlock's memory:")):
                result["phase"] = "failed"
                recognized = True
            elif "memory: ingested salts:" in message:
                result["phase"] = "waiting"
                recognized = True
        if "gc: store unavailable, not tracking salt attempts:" in message or "GC store error" in message:
            result["issue"] = "state_store"
            recognized = True
        if "Log files are being written to:" in message:
            recognized = True
        if recognized:
            stamp = str(record.get("__REALTIME_TIMESTAMP", ""))
            if stamp.isdigit():
                stamp = int(stamp) // 1_000_000
                if 0 < stamp <= 4_102_444_800:
                    result["updated_at"] = stamp
    return result


def read_journal(invocation):
    # journalctl's normal JSON output replaces oversized fields with null;
    # deliberately do not use --all. Record count and parse length are bounded.
    process = subprocess.run([
        "/usr/bin/journalctl", "--user", "--no-pager", "--output=json",
        "--output-fields=MESSAGE,__REALTIME_TIMESTAMP", f"--lines={LIMIT}",
        "_SYSTEMD_INVOCATION_ID=" + invocation,
    ], capture_output=True, text=True, timeout=3)
    if process.returncode or len(process.stdout) > MAX_OUTPUT:
        raise ValueError("Progress journal unavailable")
    records = []
    for line in process.stdout.splitlines()[-LIMIT:]:
        if len(line) > 32768:
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            pass
    return records


def snapshot(units, directory):
    active = next(((mode, unit) for mode, name in (
        ("backfill", "deadlock-api-ingest-backfill.service"),
        ("live", "deadlock-api-ingest.service"))
        if (unit := units.get(name, {})).get("state") in RUNNING), None)
    if not active:
        return empty()
    mode, unit = active
    invocation = unit.get("invocation", "")
    if not re.fullmatch(r"[a-f0-9]{32}", invocation):
        return empty(mode)
    cache_path = Path(directory) / "progress.json"
    cached = None
    now = time.monotonic()
    try:
        if cache_path.stat().st_size <= 4096:
            cached = json.loads(cache_path.read_text())
        if (isinstance(cached, dict) and cached.get("invocation") == invocation and
                cached.get("mode") == mode and isinstance(cached.get("value"), dict) and
                0 <= now - float(cached.get("read_at", 0)) < CACHE_SECONDS):
            return cached["value"]
    except (OSError, ValueError, TypeError):
        pass
    try:
        value = parse_records(read_journal(invocation), mode)
    except (OSError, ValueError, subprocess.SubprocessError):
        # A missing journal must never break lifecycle control or hide the job.
        value = empty(mode)
    try:
        temporary = cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"invocation": invocation, "mode": mode,
                                         "read_at": now, "value": value}))
        temporary.chmod(0o600)
        temporary.replace(cache_path)
    except OSError:
        pass
    return value
