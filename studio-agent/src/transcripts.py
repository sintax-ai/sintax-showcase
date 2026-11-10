"""Keep a copy of each call's conversation, so real mistakes become test cases.

Written once, after the call ends, to the folder in TRANSCRIPT_DIR. Unset that
variable and nothing is written. The default folder (data/) is git-ignored:
call content stays on this machine and is never uploaded anywhere.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
SPEAKERS = {"user": "caller", "assistant": "receptionist"}
# Delivery tags for the voice (<expr .../>) are never heard as words; drop them.
TAG = re.compile(r"<[^<>]+>")


def spoken_turns(history: dict) -> list[dict]:
    """The conversation as the two people heard it: who spoke, what they said.

    System prompts, tool calls and voice delivery tags are left out. This is
    what the owner sees in the portal, so it must read like a call, not a log.
    """
    turns = []
    for item in history.get("items", []):
        if item.get("type", "message") != "message":
            continue
        speaker = SPEAKERS.get(item.get("role"))
        if speaker is None:
            continue
        content = item.get("content", [])
        if isinstance(content, str):
            content = [content]
        text = " ".join(part for part in content if isinstance(part, str))
        text = " ".join(TAG.sub(" ", text).split())
        if text:
            turns.append({"speaker": speaker, "text": text})
    return turns


def transcript_path(folder, session_id: str, now: datetime | None = None) -> Path:
    """A sortable, filesystem-safe path: 20260922-141233-room-name.json."""
    now = now or datetime.now(UTC)
    slug = UNSAFE.sub("-", session_id).strip("-")[:60] or "call"
    return Path(folder) / f"{now.strftime('%Y%m%d-%H%M%S')}-{slug}.json"


def save_transcript(
    folder,
    session_id: str,
    history: dict,
    *,
    client: str | None = None,
    now: datetime | None = None,
) -> Path:
    """Write one call's history and return the file path."""
    now = now or datetime.now(UTC)
    path = transcript_path(folder, session_id, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "session_id": session_id,
        "client": client,
        "saved_at": now.isoformat(),
        "history": history,
    }
    path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return path
