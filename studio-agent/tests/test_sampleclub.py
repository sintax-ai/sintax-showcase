"""SAMPLECLUB demo wiring; all caller records are FICTIONAL and stay in a local fake."""

import pytest
from fake_supabase import FakeSupabase

from agent import Assistant
from clients import load_client
from cloud import CloudConfig, attach_transcript


def test_selecting_sampleclub_keeps_business_identity_separate(monkeypatch):
    mic = load_client("sample-studio")
    monkeypatch.setenv("SINTAX_CLIENT", "sampleclub")
    sampleclub = load_client()
    assert sampleclub.name == "Example Pickleball Club"
    assert sampleclub.business_id != mic.business_id
    assert "Example Pickleball Club" in sampleclub.greeting
    assert "Example Studios" not in sampleclub.prompt
    assert load_client("sample-studio") == mic


@pytest.mark.asyncio
async def test_sampleclub_inquiry_and_transcript_use_its_business_id():
    client = load_client("sampleclub")
    fake = FakeSupabase()
    try:
        cloud = CloudConfig(fake.url, fake.secret, client.business_id)
        agent = Assistant(client=client, cloud=cloud, session_id="FICTIONAL-sampleclub")
        details = {
            "name": "FICTIONAL Demo Caller",
            "phone": "2015550100",
            "service": "private_event",
            "preferred_time": "October 15, 2026 at 6 PM",
            "duration": "2 hours",
            "preferences": "FICTIONAL team event for 12 people",
        }
        result = await agent.save_booking_inquiry(**details, caller_confirmed=False)
        assert result["status"] == "needs_confirmation"
        assert fake.rows == []
        result = await agent.save_booking_inquiry(**details, caller_confirmed=True)
        assert result["status"] == "saved"
        [row] = fake.rows
        assert row["business_id"] == client.business_id
        assert row["service"] == "private_event"
        assert row["phone"] == "+12015550100"
        turns = [{"speaker": "caller", "text": "FICTIONAL team event inquiry."}]
        attached = attach_transcript(cloud, agent.saved_inquiry_id, turns)
        assert attached["status"] == "attached"
        assert fake.rows[0]["transcript"]["turns"] == turns
        rejected = await agent.save_booking_inquiry(
            **(details | {"service": "recording"}), caller_confirmed=True
        )
        assert rejected["status"] == "needs_correction"
        assert len(fake.rows) == 1
    finally:
        fake.close()
