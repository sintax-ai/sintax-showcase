"""Saving one call's conversation. All call content here is FICTIONAL."""

import json
from datetime import UTC, datetime

from transcripts import save_transcript, transcript_path

CALL = datetime(2026, 9, 22, 14, 12, 33, tzinfo=UTC)
HISTORY = {
    "items": [
        {"role": "assistant", "content": ["Hi, this is Alex from FICTIONAL Studio."]},
        {"role": "user", "content": ["Do you have a room Saturday?"]},
    ]
}


def test_writes_the_conversation(tmp_path):
    path = save_transcript(
        tmp_path / "transcripts",
        "studio-_+15551234567_ab12",
        HISTORY,
        client="fictional-studio",
        now=CALL,
    )
    saved = json.loads(path.read_text())
    assert saved["history"] == HISTORY
    assert saved["client"] == "fictional-studio"
    assert saved["session_id"] == "studio-_+15551234567_ab12"
    assert saved["saved_at"] == CALL.isoformat()


def test_creates_the_folder(tmp_path):
    folder = tmp_path / "data" / "transcripts"
    assert not folder.exists()
    save_transcript(folder, "room", HISTORY, now=CALL)
    assert folder.is_dir()


def test_filename_sorts_by_time_and_drops_unsafe_characters(tmp_path):
    path = transcript_path(tmp_path, "studio-_+1555/1234567_ab12", CALL)
    assert path.name.startswith("20260922-141233-")
    assert "/" not in path.name[:-5] and "+" not in path.name


def test_two_calls_in_the_same_second_do_not_collide(tmp_path):
    first = save_transcript(tmp_path, "room-one", HISTORY, now=CALL)
    second = save_transcript(tmp_path, "room-two", HISTORY, now=CALL)
    assert first != second
    assert first.exists() and second.exists()


def test_unserializable_values_do_not_lose_the_call(tmp_path):
    path = save_transcript(tmp_path, "room", {"items": [object()]}, now=CALL)
    assert "object object" in path.read_text()


# ---------- what the owner reads: spoken turns only ----------

from transcripts import spoken_turns  # noqa: E402

RAW_HISTORY = {
    "items": [
        {"type": "agent_config_update", "id": "x"},
        {"type": "message", "role": "system", "content": ["You are a receptionist."]},
        {
            "type": "message",
            "role": "assistant",
            "content": [
                '<expr type="expression" label="happy"/> Hi, this is Alex from FICTIONAL Studio. '
                '<expr type="expression" label="confident"/> How can I help?'
            ],
        },
        {"type": "message", "role": "user", "content": ["I'd like to book Saturday."]},
        {"type": "function_call", "name": "save_booking_inquiry", "arguments": "{}"},
        {
            "type": "function_call_output",
            "name": "save_booking_inquiry",
            "output": "{}",
        },
        {"type": "message", "role": "assistant", "content": "Saved for review."},
        {"type": "message", "role": "user", "content": ["   "]},
        {"type": "message", "role": "user", "content": [{"type": "image"}]},
    ]
}


def test_spoken_turns_keep_only_what_was_heard():
    assert spoken_turns(RAW_HISTORY) == [
        {
            "speaker": "receptionist",
            "text": "Hi, this is Alex from FICTIONAL Studio. How can I help?",
        },
        {"speaker": "caller", "text": "I'd like to book Saturday."},
        {"speaker": "receptionist", "text": "Saved for review."},
    ]


def test_spoken_turns_of_an_empty_history():
    assert spoken_turns({}) == []
    assert spoken_turns({"items": []}) == []
