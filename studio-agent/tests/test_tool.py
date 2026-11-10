import sqlite3

import pytest

from agent import Assistant

ARGS = {
    "name": "Demo Caller",
    "phone": "2015550100",
    "service": "recording",
    "preferred_time": "September 25 at 3 PM",
    "duration": "2 hours",
    "preferences": "Room A",
}


@pytest.mark.asyncio
async def test_tool_requires_confirmation(tmp_path):
    db = tmp_path / "test.db"
    agent = Assistant(storage_path=db)
    result = await agent.save_booking_inquiry(**ARGS, caller_confirmed=False)
    assert result["status"] == "needs_confirmation"
    assert not db.exists()


@pytest.mark.asyncio
async def test_tool_reports_unavailable_without_storage():
    result = await Assistant().save_booking_inquiry(**ARGS, caller_confirmed=True)
    assert result["status"] == "unavailable"


@pytest.mark.asyncio
async def test_tool_saves_and_returns_receipt(tmp_path):
    db = tmp_path / "test.db"
    result = await Assistant(storage_path=db).save_booking_inquiry(
        **ARGS, caller_confirmed=True
    )
    assert result["status"] == "saved"
    with sqlite3.connect(db) as conn:
        assert conn.execute("select name from inquiries").fetchone()[0] == "Demo Caller"


@pytest.mark.asyncio
async def test_tool_enforces_the_clients_own_services(tmp_path):
    """A FICTIONAL client accepts its services and rejects other clients' codes."""
    import json

    from clients import load_client

    folder = tmp_path / "fictional-hvac"
    folder.mkdir()
    (folder / "client.json").write_text(
        json.dumps(
            {
                "name": "FICTIONAL HVAC",
                "business_id": "11111111-2222-3333-4444-555555555555",
                "greeting": "Hi, this is Max from FICTIONAL HVAC.",
                "voice": "fictional-voice",
                "timezone": "America/New_York",
                "services": ["repair", "install"],
            }
        )
    )
    (folder / "knowledge.md").write_text("Facts.")
    (folder / "instructions.md").write_text("Behave.")
    client = load_client("fictional-hvac", root=tmp_path)

    db = tmp_path / "test.db"
    agent = Assistant(storage_path=db, client=client)
    rejected = await agent.save_booking_inquiry(**ARGS, caller_confirmed=True)
    assert rejected["status"] == "needs_correction"  # "recording" is not HVAC

    accepted = await agent.save_booking_inquiry(
        **(ARGS | {"service": "repair"}), caller_confirmed=True
    )
    assert accepted["status"] == "saved"
