import importlib.util
import sqlite3

import pytest


def module():
    assert importlib.util.find_spec("inquiries") is not None, (
        "Inquiry storage not implemented"
    )
    import inquiries

    return inquiries


def details():
    return {
        "name": "Demo Caller",
        "phone": "(201) 555-0100",
        "service": "recording",
        "preferred_time": "Friday at 3 PM, date to confirm",
        "duration": "2 hours",
        "preferences": "Room A, no engineer preference",
    }


def test_save_persists_and_duplicate_does_not_create_second_row(tmp_path):
    m = module()
    p = tmp_path / "inquiries.db"
    first = m.save_inquiry(p, "session-one", **details())
    second = m.save_inquiry(p, "session-one", **details())
    assert first["status"] == second["status"] == "saved"
    assert first["inquiry_id"] == second["inquiry_id"]
    with sqlite3.connect(p) as db:
        rows = db.execute("select name, phone, service from inquiries").fetchall()
    assert rows == [("Demo Caller", "+12015550100", "recording")]


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", " "),
        ("phone", "123"),
        ("service", "unknown"),
        ("preferred_time", ""),
        ("duration", ""),
    ],
)
def test_rejects_incomplete_or_invalid_inquiry(tmp_path, field, value):
    m = module()
    d = details()
    d[field] = value
    result = m.save_inquiry(tmp_path / "inquiries.db", "s", **d)
    assert result["status"] == "needs_correction"
    assert not (tmp_path / "inquiries.db").exists()


def test_storage_failure_never_returns_success(tmp_path):
    result = module().save_inquiry(tmp_path, "s", **details())
    assert result["status"] == "failed"
    assert "inquiry_id" not in result
