"""
Activity Logger
===============
Persistent, file-backed activity log for the Smart Parts plugin.

Replaces the in-memory ring-buffer in views.py with a JSON Lines file
that survives Django worker restarts. Thread-safe via a lock.

Format: One JSON object per line (newline-delimited JSON / JSONL).
Cap: MAX_ENTRIES most recent entries are kept.
"""

import json
import logging
import os
import threading
from datetime import datetime
from typing import List, Optional, Dict, Any

logger = logging.getLogger('inventree_smart_parts.logger')

# ── Configuration ────────────────────────────────────────────────────
MAX_ENTRIES = 500

# Log file location: alongside this file (data/plugins/…/services/)
# Falls back to /tmp if the directory is not writable.
_LOG_FILE = os.path.join(os.path.dirname(__file__), '..', 'smart_parts_activity.jsonl')
_LOG_FILE = os.path.normpath(_LOG_FILE)

_lock = threading.Lock()
_in_memory_fallback: List[Dict[str, Any]] = []


# ═══════════════════════════════════════════════════════════════════
#  Internal helpers
# ═══════════════════════════════════════════════════════════════════

def _read_all() -> List[Dict[str, Any]]:
    """Read all log entries from the JSONL file."""
    entries = []
    try:
        if not os.path.exists(_LOG_FILE):
            return entries
        with open(_LOG_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass  # Skip corrupt lines
    except OSError as e:
        logger.warning(f"[SmartParts] Could not read log file {_LOG_FILE}: {e}")
    return entries


def _write_all(entries: List[Dict[str, Any]]) -> bool:
    """Write (overwrite) the JSONL file with the given entries. Returns True on success."""
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)
        with open(_LOG_FILE, 'w', encoding='utf-8') as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        return True
    except OSError as e:
        logger.warning(f"[SmartParts] Could not write log file {_LOG_FILE}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════

def log_activity(level: str, message: str, details: str = '') -> None:
    """
    Append a new activity log entry.

    Args:
        level:   'INFO', 'WARNING', or 'ERROR'
        message: Short human-readable description
        details: Optional extra context (e.g. error message)
    """
    entry = {
        'timestamp': datetime.now().isoformat(timespec='seconds'),
        'level': level.upper(),
        'message': str(message),
        'details': str(details),
    }

    with _lock:
        # Try persistent file first
        entries = _read_all()
        entries.append(entry)
        # Cap to MAX_ENTRIES (keep most recent)
        if len(entries) > MAX_ENTRIES:
            entries = entries[-MAX_ENTRIES:]
        if not _write_all(entries):
            # File write failed → in-memory fallback
            _in_memory_fallback.append(entry)
            if len(_in_memory_fallback) > MAX_ENTRIES:
                _in_memory_fallback.pop(0)


def get_logs(
    level_filter: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """
    Retrieve log entries, newest first.

    Args:
        level_filter: Optional level string ('INFO', 'WARNING', 'ERROR').
                      If None, all levels are returned.
        limit:        Maximum number of entries to return.

    Returns:
        List of log dicts: {timestamp, level, message, details}
    """
    with _lock:
        entries = _read_all()
        # Merge in-memory fallback entries (deduplicate by content+timestamp)
        if _in_memory_fallback:
            existing_ts = {e['timestamp'] for e in entries}
            for e in _in_memory_fallback:
                if e['timestamp'] not in existing_ts:
                    entries.append(e)

    # Sort newest first
    entries_sorted = sorted(entries, key=lambda e: e.get('timestamp', ''), reverse=True)

    # Apply level filter
    if level_filter:
        entries_sorted = [e for e in entries_sorted if e.get('level') == level_filter.upper()]

    return entries_sorted[:limit]


def clear_logs() -> None:
    """Remove all log entries (for testing / manual reset)."""
    with _lock:
        _write_all([])
        _in_memory_fallback.clear()


def get_recent(n: int = 10) -> List[Dict[str, Any]]:
    """Return the most recent n entries (newest first). Convenience wrapper."""
    return get_logs(limit=n)


def get_log_file_path() -> str:
    """Return the absolute path to the JSONL log file."""
    return _LOG_FILE
