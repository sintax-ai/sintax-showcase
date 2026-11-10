"""Inquiry validation and local storage. No calendar reservation or owner notification is performed."""

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

# Engine default, used when no client list is passed (tests). Each client's real
# list lives in clients/<slug>/client.json and must match the portal menu in
# client-portal/public/services.mjs.
SERVICES = {
    "recording",
    "music_rental",
    "mixing",
    "mastering",
    "photo_rental",
    "photoshoot",
    "recording_light_mix",
}

# Limits match the database rules in client-portal/supabase/migrations/002_owner_corrections.sql.
LIMITS = {"name": 200, "preferred_time": 500, "duration": 500, "service": 50}


def clean_inquiry(
    session_id: str,
    *,
    name: str,
    phone: str,
    service: str,
    preferred_time: str,
    duration: str,
    preferences: str = "",
    services=None,
) -> tuple[dict, str] | dict:
    """Return (clean fields, stable inquiry id), or a needs_correction result.

    The id depends only on the call and its details, so saving the same request
    twice (a retry) produces the same id and never a second record.
    """
    fields = {
        "name": name.strip(),
        "phone": phone.strip(),
        "service": service.strip(),
        "preferred_time": preferred_time.strip(),
        "duration": duration.strip(),
        "preferences": preferences.strip(),
    }
    for field in ("name", "phone", "service", "preferred_time", "duration"):
        if not fields[field] or len(fields[field]) > LIMITS.get(field, 500):
            return {"status": "needs_correction", "field": field}
    if len(fields["preferences"]) > 2000 or fields["service"] not in (
        services or SERVICES
    ):
        return {"status": "needs_correction", "field": "service or preferences"}
    if re.search(r"[^\d\s+().-]", fields["phone"]):
        return {"status": "needs_correction", "field": "phone"}
    digits = re.sub(r"\D", "", fields["phone"])
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) != 11 or not digits.startswith("1"):
        return {
            "status": "needs_correction",
            "field": "phone",
            "message": "Demo supports US/Canada callback numbers.",
        }
    fields["phone"] = "+" + digits
    inquiry_id = hashlib.sha256(
        (session_id + json.dumps(fields, sort_keys=True)).encode()
    ).hexdigest()[:24]
    return fields, inquiry_id


def save_inquiry(path: Path, session_id: str, **details) -> dict:
    cleaned = clean_inquiry(session_id, **details)
    if isinstance(cleaned, dict):
        return cleaned
    fields, inquiry_id = cleaned
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path, timeout=5) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS inquiries (id TEXT PRIMARY KEY, created_at TEXT, name TEXT, phone TEXT, service TEXT, preferred_time TEXT, duration TEXT, preferences TEXT)"
            )
            db.execute(
                "INSERT OR IGNORE INTO inquiries VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (inquiry_id, datetime.now(UTC).isoformat(), *fields.values()),
            )
        path.chmod(0o600)
    except (OSError, sqlite3.Error):
        return {
            "status": "failed",
            "message": "Could not verify saving. Do not claim success. Offer the studio contact.",
        }
    return {
        "status": "saved",
        "inquiry_id": inquiry_id,
        "message": "Saved locally for demo review only. Not a confirmed booking; no owner notification sent.",
    }
