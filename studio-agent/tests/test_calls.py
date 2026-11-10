"""Call history uses a local HTTP server; no LiveKit inference or cloud writes."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from fake_supabase import FakeSupabase
from test_cloud import DETAILS, config

from agent import Assistant


@pytest.fixture
def fake():
    server = FakeSupabase()
    yield server
    server.close()


def report(path=None):
    return SimpleNamespace(
        audio_recording_path=path,
        started_at=1793667600.0,
        timestamp=1793667660.0,
        duration=60.0,
        room_id="RM_test",
    )


def test_call_exists_without_an_inquiry_and_retry_preserves_trash(fake):
    from calls import CallHistory

    h = CallHistory(config(fake), "room-1", mode="demo", started_at=1793667600)
    assert h.begin() is True
    UUID(h.call_id)
    assert len(fake.rows) == 0
    assert fake.calls[0]["mode"] == "demo"
    fake.calls[0]["is_trashed"] = True
    assert h.begin() is True
    assert len(fake.calls) == 1
    assert fake.calls[0]["is_trashed"] is True
    assert h.finalize(report()) is True
    assert fake.calls[0]["recording_status"] == "unavailable"
    assert fake.calls[0]["duration_seconds"] == 60


def test_audio_uploaded_before_available_with_indefinite_retention(fake, tmp_path):
    from calls import CallHistory

    h = CallHistory(config(fake), "room-1", mode="pilot")
    audio = tmp_path / "audio.ogg"
    audio.write_bytes(b"OggS FICTIONAL audio")
    assert h.finalize(report(audio)) is True
    row = fake.calls[0]
    key = f"{config(fake).business_id}/{h.call_id}/audio.ogg"
    assert row["recording_path"] == key
    assert row["recording_status"] == "available"
    assert row["recording_expires_at"] is None
    assert fake.audio[f"/storage/v1/object/call-recordings/{key}"] == audio.read_bytes()
    upload = next(
        i for i, r in enumerate(fake.requests) if r[1].startswith("/storage/")
    )
    available = next(
        i
        for i, r in enumerate(fake.requests)
        if isinstance(r[3], dict) and r[3].get("recording_status") == "available"
    )
    assert upload < available
    assert fake.requests[upload][2]["Content-Type"] == "audio/ogg"


def test_failed_upload_never_claims_available_and_retries_are_bounded(fake, tmp_path):
    from calls import CallHistory

    audio = tmp_path / "audio.ogg"
    audio.write_bytes(b"OggS FICTIONAL")
    fake.upload_fail_with = [503] * 10
    h = CallHistory(config(fake), "room-1", mode="demo")
    assert h.finalize(report(audio)) is True  # History is durable, audio isn't.
    row = fake.calls[0]
    assert row["recording_status"] == "unavailable"
    assert row["recording_path"] is None
    assert len([r for r in fake.requests if r[1].startswith("/storage/")]) == 3


def test_unonboarded_business_never_uploads_or_changes_identity(fake, tmp_path):
    from calls import CallHistory

    fake.call_fail_with = [409] * 10
    h = CallHistory(config(fake), "room-1", mode="demo")
    assert h.finalize(report(tmp_path / "absent")) is False
    assert fake.calls == []
    assert fake.audio == {}
    assert len(fake.requests) == 1


def test_same_room_is_isolated_by_business(fake):
    from dataclasses import replace

    from calls import CallHistory

    first = CallHistory(config(fake), "room-1", mode="pilot")
    other = CallHistory(
        replace(config(fake), business_id="11111111-2222-3333-4444-555555555555"),
        "room-1",
        mode="pilot",
    )
    assert first.call_id != other.call_id


def test_explicit_retention_uses_session_end(fake, tmp_path):
    from calls import CallHistory

    audio = tmp_path / "audio.ogg"
    audio.write_bytes(b"OggS FICTIONAL")
    h = CallHistory(config(fake), "room-1", mode="demo", retention_days=30)
    h.finalize(report(audio))
    assert datetime.fromisoformat(fake.calls[0]["recording_expires_at"]) == datetime(
        2026, 12, 3, 1, 1, tzinfo=UTC
    )


def test_call_id_is_not_part_of_inquiry_hash(fake):
    from cloud import save_inquiry_cloud

    first = save_inquiry_cloud(config(fake), "room-1", **DETAILS)
    second = save_inquiry_cloud(
        config(fake),
        "room-1",
        call_id="11111111-2222-3333-4444-555555555555",
        **DETAILS,
    )
    assert first["inquiry_id"] == second["inquiry_id"]


@pytest.mark.asyncio
async def test_all_requests_link_only_after_call_start_is_durable(fake):
    from calls import CallHistory

    h = CallHistory(config(fake), "room-1", mode="pilot")
    h.start()
    assistant = Assistant(cloud=config(fake), session_id="room-1", call_history=h)
    first = await assistant.save_booking_inquiry(**DETAILS, caller_confirmed=True)
    second = await assistant.save_booking_inquiry(
        **(DETAILS | {"preferred_time": "Saturday"}), caller_confirmed=True
    )
    assert first["status"] == second["status"] == "saved"
    assert len(fake.rows) == 2
    assert all(row["call_id"] == h.call_id for row in fake.rows)
    assert assistant.saved_inquiry_ids == {first["inquiry_id"], second["inquiry_id"]}
    assert fake.requests[0][1].startswith("/rest/v1/calls")


@pytest.mark.asyncio
async def test_history_outage_does_not_lose_requests_and_links_recover(fake):
    from calls import CallHistory

    fake.call_fail_with = [503] * 3
    h = CallHistory(config(fake), "room-1", mode="pilot")
    h.start()
    assistant = Assistant(cloud=config(fake), session_id="room-1", call_history=h)
    result = await assistant.save_booking_inquiry(**DETAILS, caller_confirmed=True)
    assert result["status"] == "saved"
    assert fake.rows[0].get("call_id") is None
    assert await h.finish(report()) is True
    assert fake.rows[0]["call_id"] == h.call_id


@pytest.mark.asyncio
async def test_session_end_uploads_sdk_report_and_handles_missing_audio(fake, tmp_path):
    from calls import finish_call_history, start_call_history

    ctx = SimpleNamespace(
        room=SimpleNamespace(name="room-1"),
        make_session_report=lambda: report(tmp_path / "missing.ogg"),
    )
    start_call_history(ctx, config(fake), mode="pilot")
    await finish_call_history(ctx)
    assert fake.calls[0]["recording_status"] == "unavailable"
    assert fake.calls[0]["ended_at"]


@pytest.mark.asyncio
async def test_demo_history_uses_only_verified_business_and_skips_bad_config(
    monkeypatch, fake
):
    import agent
    from calls import finish_call_history
    from clients import load_client

    selected = load_client("sampleclub")
    identities = []

    def cloud_config(env, *, business_id):
        identities.append(business_id)
        return config(fake)

    monkeypatch.setattr(agent.CloudConfig, "from_env", cloud_config)
    ctx = SimpleNamespace(
        room=SimpleNamespace(name="room-1"), make_session_report=report
    )
    agent.start_demo_history(ctx, selected)
    await finish_call_history(ctx)
    assert identities == [selected.business_id]
    assert fake.calls[0]["mode"] == "demo"
    assert fake.rows == []

    def invalid(*args, **kwargs):
        raise ValueError("FICTIONAL misconfiguration")

    monkeypatch.setattr(agent.CloudConfig, "from_env", invalid)
    other = SimpleNamespace(room=SimpleNamespace(name="room-2"))
    assert agent.start_demo_history(other, selected) is None
    assert not hasattr(other, "sintax_call_history")


@pytest.mark.asyncio
async def test_start_never_blocks_greeting_and_cancelled_save_does_not_cancel_history(
    monkeypatch, fake
):
    import asyncio
    import threading

    from calls import CallHistory

    h = CallHistory(config(fake), "room-1", mode="pilot")
    started, release = threading.Event(), threading.Event()
    real_begin = h.begin

    def slow_begin():
        started.set()
        release.wait(2)
        return real_begin()

    monkeypatch.setattr(h, "begin", slow_begin)
    h.start()
    try:
        await asyncio.to_thread(started.wait, 1)
        waiting = asyncio.create_task(h.ready_call_id())
        await asyncio.sleep(0)
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        assert not h._start_task.cancelled()
    finally:
        release.set()
    assert await h.ready_call_id() == h.call_id


@pytest.mark.asyncio
async def test_report_failure_is_isolated_from_shutdown():
    from calls import finish_call_history

    def broken():
        raise RuntimeError("FICTIONAL unavailable report")

    await finish_call_history(
        SimpleNamespace(sintax_call_history=object(), make_session_report=broken)
    )


@pytest.mark.asyncio
async def test_pilot_entrypoint_records_and_links_to_started_call(monkeypatch, fake):
    import agent
    from calls import finish_call_history
    from clients import load_client

    runtime = agent.RuntimeConfig(
        mode="pilot", client=load_client("sample-studio"), cloud=config(fake)
    )
    monkeypatch.setattr(agent, "load_runtime", lambda: runtime)
    captured = {}

    class Session:
        def __init__(self, **kwargs):
            pass

        async def start(self, **kwargs):
            captured.update(kwargs)

        async def generate_reply(self, **kwargs):
            pass

    class Context:
        room = SimpleNamespace(name="room-1")

        async def connect(self):
            pass

        def add_shutdown_callback(self, callback):
            pass

        def make_session_report(self):
            return report()

    monkeypatch.setattr(agent, "AgentSession", Session)
    ctx = Context()
    await agent.my_agent(ctx)
    assert captured["record"] is True
    assert captured["agent"].call_history is ctx.sintax_call_history
    await finish_call_history(ctx)
    assert fake.calls[0]["mode"] == "pilot"


def test_missing_report_duration_uses_elapsed_time_and_failed_retry_keeps_audio(
    fake, tmp_path
):
    from calls import CallHistory

    h = CallHistory(config(fake), "room-1", mode="pilot")
    audio = tmp_path / "audio.ogg"
    audio.write_bytes(b"OggS FICTIONAL")
    h.finalize(report(audio))
    missing = report()
    missing.duration = None
    h.finalize(missing)
    assert fake.calls[0]["recording_status"] == "available"
    assert fake.calls[0]["duration_seconds"] == 60
