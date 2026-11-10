"""Mode boundaries: no demo inquiries or call attribution before selection."""

import pytest

import agent
from clients import load_client
from demos import DemoAssistant


def test_demo_ignores_client_and_stale_storage_credentials():
    config = agent.load_runtime(
        {
            "SINTAX_MODE": "demo",
            "SINTAX_CLIENT": "sample-studio",
            "SUPABASE_URL": "bad-url",
            "SUPABASE_SECRET_KEY": "FICTIONAL",
            "SUPABASE_BUSINESS_ID": "bad-id",
            "INQUIRY_DB_PATH": "must-not-exist.db",
            "TRANSCRIPT_DIR": "must-not-exist",
        }
    )
    assert config.client is None
    assert config.cloud is None
    assert config.storage_path is None
    assert config.transcript_dir is None
    assert config.demo_clients["101"].slug == "sampleclub"


def test_pilot_still_requires_explicit_client():
    with pytest.raises(ValueError, match="SINTAX_CLIENT"):
        agent.load_runtime({"SINTAX_MODE": "pilot"})


def test_pilot_uses_its_clients_database_identity():
    config = agent.load_runtime(
        {
            "SINTAX_MODE": "pilot",
            "SINTAX_CLIENT": "sampleclub",
            "SUPABASE_URL": "https://fictional.example.test",
            "SUPABASE_SECRET_KEY": "FICTIONAL",
            "SUPABASE_BUSINESS_ID": "wrong-id",
        }
    )
    assert config.cloud.business_id == load_client("sampleclub").business_id
    assert config.demo_clients is None


def test_unknown_mode_never_falls_back_to_pilot():
    with pytest.raises(ValueError, match="SINTAX_MODE"):
        agent.load_runtime({"SINTAX_MODE": "demoo", "SINTAX_CLIENT": "sampleclub"})


@pytest.mark.asyncio
async def test_entrypoint_demo_never_registers_inquiry_or_local_transcript_storage(
    monkeypatch, tmp_path
):
    seen = []
    monkeypatch.setenv("SINTAX_MODE", "demo")
    monkeypatch.setenv("SUPABASE_URL", "invalid")
    monkeypatch.setenv("INQUIRY_DB_PATH", str(tmp_path / "no.db"))
    monkeypatch.setenv("TRANSCRIPT_DIR", str(tmp_path / "no-transcripts"))

    async def transport(ctx, clients, *, assistant_factory):
        seen.append(clients["101"].slug)

    monkeypatch.setattr(agent, "run_demo", transport, raising=False)

    class Context:
        room = type("Room", (), {"name": "FICTIONAL-room"})()

        def add_shutdown_callback(self, callback):
            pytest.fail("Demo must not register an inquiry/transcript callback")

    await agent.my_agent(Context())
    assert seen == ["sampleclub"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_each_demo_agent_has_its_own_context():
    from dataclasses import replace

    first = load_client("sampleclub")
    second = replace(
        first, slug="fictional", name="FICTIONAL", knowledge="FICTIONAL UNIQUE FACT"
    )
    a, b = DemoAssistant(first), DemoAssistant(second)
    assert a.chat_ctx is not b.chat_ctx
    assert "FICTIONAL UNIQUE FACT" not in a.instructions
    assert "FICTIONAL UNIQUE FACT" in b.instructions


def test_intake_allowlist_is_explicit_and_validated():
    assert agent.load_runtime({"SINTAX_MODE": "demo"}).intake_clients == frozenset()
    config = agent.load_runtime(
        {"SINTAX_MODE": "demo", "SINTAX_INTAKE_CLIENTS": " sample-studio "}
    )
    assert config.intake_clients == frozenset({"sample-studio"})
    for invalid in ("*", "unknown-client", "sample-studio,", ",", "sample-studio,sampleclubc"):
        with pytest.raises(ValueError, match="SINTAX_INTAKE_CLIENTS"):
            agent.load_runtime(
                {"SINTAX_MODE": "demo", "SINTAX_INTAKE_CLIENTS": invalid}
            )


@pytest.mark.asyncio
async def test_verified_102_gets_real_save_tool_but_other_clients_stay_demo(
    monkeypatch,
):
    from types import SimpleNamespace

    from fake_supabase import FakeSupabase
    from test_cloud import DETAILS, config

    fake = FakeSupabase()
    callbacks = []
    ctx = SimpleNamespace(
        room=SimpleNamespace(name="fictional-keypad-intake"),
        add_shutdown_callback=callbacks.append,
    )
    history = {
        "items": [
            {
                "type": "message",
                "role": "user",
                "content": ["FICTIONAL recording inquiry"],
            }
        ]
    }
    session = SimpleNamespace(history=SimpleNamespace(to_dict=lambda **kwargs: history))
    monkeypatch.setattr(
        agent.CloudConfig, "from_env", lambda *args, **kwargs: config(fake)
    )
    try:
        assistant = agent.make_keypad_assistant(
            ctx, load_client("sample-studio"), session, frozenset({"sample-studio"})
        )
        assert isinstance(assistant, agent.Assistant)
        assert callable(assistant.save_booking_inquiry)
        assert assistant.storage_path is None
        assert assistant.stt is not None and assistant.tts is not None
        first = await assistant.save_booking_inquiry(**DETAILS, caller_confirmed=False)
        assert first["status"] == "needs_confirmation"
        assert fake.rows == []
        first = await assistant.save_booking_inquiry(**DETAILS, caller_confirmed=True)
        second = await assistant.save_booking_inquiry(
            **(DETAILS | {"preferred_time": "Saturday"}), caller_confirmed=True
        )
        assert first["status"] == second["status"] == "saved"
        assert len(fake.rows) == 2
        assert all(
            row["call_id"] == ctx.sintax_call_history.call_id for row in fake.rows
        )
        await callbacks[0]()
        assert all(row.get("transcript") for row in fake.rows)
        other_ctx = SimpleNamespace(
            room=SimpleNamespace(name="fictional-other-demo"),
            add_shutdown_callback=lambda cb: pytest.fail(
                "Non-enabled demos must not save request transcripts"
            ),
        )
        other = agent.make_keypad_assistant(
            other_ctx, load_client("sampleclub"), session, frozenset({"sample-studio"})
        )
        assert isinstance(other, DemoAssistant)
        assert not hasattr(other, "save_booking_inquiry")
        await other_ctx.sintax_call_history.ready_call_id()
    finally:
        fake.close()


@pytest.mark.asyncio
async def test_opted_in_missing_cloud_never_falls_back_to_simulated_success(
    monkeypatch,
):
    from types import SimpleNamespace

    from test_cloud import DETAILS

    monkeypatch.setattr(agent.CloudConfig, "from_env", lambda *args, **kwargs: None)
    ctx = SimpleNamespace(
        room=SimpleNamespace(name="fictional-missing-cloud"),
        add_shutdown_callback=lambda cb: None,
    )
    assistant = agent.make_keypad_assistant(
        ctx, load_client("sample-studio"), SimpleNamespace(), frozenset({"sample-studio"})
    )
    assert isinstance(assistant, agent.Assistant)
    assert assistant.storage_path is None
    result = await assistant.save_booking_inquiry(**DETAILS, caller_confirmed=True)
    assert result["status"] == "unavailable"


@pytest.mark.asyncio
async def test_intake_handoff_greets_then_enables_audio():
    from types import SimpleNamespace

    trace = []
    assistant = agent.Assistant(client=load_client("sample-studio"), keypad=True)

    async def say(text, **kwargs):
        trace.append(("greeting", text))

    assistant._activity = SimpleNamespace(
        session=SimpleNamespace(
            say=say,
            input=SimpleNamespace(
                set_audio_enabled=lambda value: trace.append(("audio", value))
            ),
        )
    )
    await assistant.on_enter()
    assert trace == [("greeting", load_client("sample-studio").greeting), ("audio", True)]


@pytest.mark.asyncio
async def test_entrypoint_applies_explicit_intake_route_only_after_selection(
    monkeypatch,
):
    from types import SimpleNamespace

    from fake_supabase import FakeSupabase
    from test_cloud import config

    fake = FakeSupabase()
    monkeypatch.setenv("SINTAX_MODE", "demo")
    monkeypatch.setenv("SINTAX_INTAKE_CLIENTS", "sample-studio")
    monkeypatch.setattr(
        agent.CloudConfig, "from_env", lambda *args, **kwargs: config(fake)
    )
    callbacks, selected = [], []
    ctx = SimpleNamespace(
        room=SimpleNamespace(name="fictional-entrypoint-intake"),
        add_shutdown_callback=callbacks.append,
    )

    async def transport(context, clients, *, assistant_factory):
        assert not hasattr(context, "sintax_call_history")
        selected.append(assistant_factory(clients["102"], SimpleNamespace()))

    monkeypatch.setattr(agent, "run_demo", transport)
    try:
        await agent.my_agent(ctx)
        assert isinstance(selected[0], agent.Assistant)
        assert selected[0].client.slug == "sample-studio"
        assert selected[0].cloud.business_id == load_client("sample-studio").business_id
        assert len(callbacks) == 1
        await ctx.sintax_call_history.ready_call_id()
    finally:
        fake.close()
