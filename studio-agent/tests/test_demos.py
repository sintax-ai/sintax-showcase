"""FICTIONAL callers, local events only: no phone calls or inference requests."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from livekit import rtc

import demos
from clients import load_client


@pytest.fixture(autouse=True)
def fictional_inference_credentials(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://fictional.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "FICTIONAL")
    monkeypatch.setenv(
        "LIVEKIT_API_SECRET", "FICTIONAL-local-test-secret-never-used-on-network"
    )


class Room(rtc.EventEmitter):
    pass


class Speech:
    def __init__(self):
        self.messages = []
        self.ready = asyncio.Queue()

    async def __call__(self, text):
        self.messages.append(text)
        self.ready.put_nowait(text)


def key(room, digit, who="caller"):
    room.emit(
        "sip_dtmf_received",
        SimpleNamespace(digit=digit, participant=SimpleNamespace(identity=who)),
    )


@pytest.fixture
def clients():
    first = load_client("sampleclub")
    return {
        "101": first,
        "202": replace(first, slug="fictional-second", name="FICTIONAL Second"),
    }


def test_directory_selects_sampleclub_and_sample_studio_separately():
    clients = demos.load_demo_clients()
    assert clients["101"].slug == "sampleclub"
    assert clients["102"].slug == "sample-studio"
    assert clients["101"].business_id != clients["102"].business_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,slug",
    [("201", "sample-home-services")],
)
async def test_prospect_keypad_selects_its_own_profile(code, slug):
    directory = demos.load_demo_clients()
    assert code in directory
    assert len({c.business_id for c in directory.values()}) == len(directory)
    room, say = Room(), Speech()
    task = asyncio.create_task(
        demos.collect_demo_client(room, "caller", directory, say)
    )
    await say.ready.get()
    for digit in code:
        key(room, digit)
    selected = await task
    assert selected.slug == slug
    assert selected.business_id != directory["101"].business_id


@pytest.mark.parametrize(
    "mapping",
    [{}, {"1": "sampleclub"}, {"abc": "sampleclub"}, {"101": "../sampleclub"}, {"101": "missing"}],
)
def test_bad_directory_refuses_startup(tmp_path, mapping):
    path = tmp_path / "codes.json"
    path.write_text(json.dumps(mapping))
    with pytest.raises(ValueError):
        demos.load_demo_clients(path)


@pytest.mark.asyncio
async def test_valid_code_ignores_other_participants_and_cleans_listeners(clients):
    room, say = Room(), Speech()
    task = asyncio.create_task(demos.collect_demo_client(room, "caller", clients, say))
    await say.ready.get()
    for d in "202":
        key(room, d, "someone-else")
    for d in "101":
        key(room, d)
    assert (await task).slug == "sampleclub"
    assert not room._events.get("sip_dtmf_received")
    assert not room._events.get("participant_disconnected")


@pytest.mark.asyncio
async def test_calls_select_independent_businesses(clients):
    a, b, sa, sb = Room(), Room(), Speech(), Speech()
    ta = asyncio.create_task(demos.collect_demo_client(a, "caller", clients, sa))
    tb = asyncio.create_task(demos.collect_demo_client(b, "caller", clients, sb))
    await sa.ready.get()
    await sb.ready.get()
    for d in "101":
        key(a, d)
    for d in "202":
        key(b, d)
    ca, cb = await asyncio.gather(ta, tb)
    assert ca.slug == "sampleclub"
    assert cb.slug == "fictional-second"


@pytest.mark.asyncio
async def test_clear_and_retry_after_partial_submit(clients):
    room, say = Room(), Speech()
    task = asyncio.create_task(demos.collect_demo_client(room, "caller", clients, say))
    await say.ready.get()
    for d in "1#":
        key(room, d)
    await say.ready.get()
    for d in "20*101":
        key(room, d)
    assert (await task).slug == "sampleclub"


@pytest.mark.asyncio
async def test_three_invalid_attempts_never_fall_back(clients):
    room, say = Room(), Speech()
    task = asyncio.create_task(demos.collect_demo_client(room, "caller", clients, say))
    for _ in range(3):
        await say.ready.get()
        for d in "999":
            key(room, d)
    assert await task is None
    assert "goodbye" in say.messages[-1].lower()


@pytest.mark.asyncio
async def test_silence_times_out_without_selecting_client(clients):
    room, say = Room(), Speech()
    assert (
        await demos.collect_demo_client(room, "caller", clients, say, timeout=0.001)
        is None
    )
    assert not room._events.get("sip_dtmf_received")


@pytest.mark.asyncio
async def test_disconnect_and_cancellation_cleanup(clients):
    for cancel in (False, True):
        room, say = Room(), Speech()
        task = asyncio.create_task(
            demos.collect_demo_client(room, "caller", clients, say)
        )
        await say.ready.get()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            room.emit("participant_disconnected", SimpleNamespace(identity="caller"))
            assert await task is None
        assert not room._events.get("sip_dtmf_received")
        assert not room._events.get("participant_disconnected")


@pytest.mark.asyncio
async def test_demo_has_no_save_capability(clients):
    from livekit.agents.beta.tools import EndCallTool

    from calendar_dates import CalendarTools

    demo = demos.DemoAssistant(clients["101"])
    assert not hasattr(demo, "save_booking_inquiry")
    assert {type(tool) for tool in demo.tools} == {EndCallTool, CalendarTools}
    assert not hasattr(demo, "cloud")
    assert not hasattr(demo, "storage_path")


def test_duplicate_codes_are_rejected(tmp_path):
    path = tmp_path / "codes.json"
    path.write_text('{"101":"sampleclub","101":"sample-studio"}')
    with pytest.raises(ValueError, match="Duplicate"):
        demos.load_demo_clients(path)


@pytest.mark.asyncio
async def test_disconnect_during_prompt_cleans_up(clients):
    room = Room()

    async def say(text):
        room.emit("participant_disconnected", SimpleNamespace(identity="caller"))

    assert await demos.collect_demo_client(room, "caller", clients, say) is None
    assert not room._events.get("sip_dtmf_received")


@pytest.mark.asyncio
async def test_keypad_input_during_welcome_is_not_lost(clients):
    room = Room()

    async def say(text):
        for d in "101":
            key(room, d)

    assert (
        await demos.collect_demo_client(room, "caller", clients, say)
    ).slug == "sampleclub"


@pytest.mark.asyncio
async def test_prompt_failure_cleans_up(clients):
    room = Room()

    async def say(text):
        raise RuntimeError("FICTIONAL speech failure")

    with pytest.raises(RuntimeError, match="speech failure"):
        await demos.collect_demo_client(room, "caller", clients, say)
    assert not room._events.get("sip_dtmf_received")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "early, digits, selected",
    [
        ("", "101", "sampleclub"),
        ("", "999", None),
        ("202", "101", "fictional-second"),
        ("disconnect", "101", None),
    ],
)
async def test_call_flow_enables_recording_and_hands_off_only_valid_codes(
    monkeypatch, clients, early, digits, selected
):
    """Fake only the network/audio session boundary; run the real menu and agents."""
    room = Room()
    trace = {"agents": [], "speech": [], "audio": [], "closed": False, "deleted": False}

    class Session:
        def __init__(self):
            self.input = SimpleNamespace(set_audio_enabled=trace["audio"].append)

        async def start(self, **kwargs):
            trace["start"] = kwargs
            if early == "disconnect":
                room.emit(
                    "participant_disconnected", SimpleNamespace(identity="caller")
                )
            else:
                for digit in early:
                    key(room, digit)

        async def say(self, text, **kwargs):
            trace["speech"].append(text)
            for d in digits:
                key(room, d)

        def update_agent(self, value):
            trace["agents"].append(value)

        async def aclose(self):
            trace["closed"] = True

    class Context:
        async def connect(self):
            pass

        async def wait_for_participant(self):
            return SimpleNamespace(identity="caller")

        async def delete_room(self):
            trace["deleted"] = True

        def shutdown(self, **kwargs):
            trace["shutdown"] = kwargs

    ctx = Context()
    ctx.room = room
    monkeypatch.setattr(demos, "AgentSession", Session)
    selections = []
    factories = []

    def factory(client, session):
        factories.append(client.slug)
        return demos.DemoAssistant(client)

    await demos.run_demo(
        ctx, clients, on_client_selected=selections.append, assistant_factory=factory
    )
    assert factories == ([selected] if selected else [])
    assert [client.slug for client in selections] == ([selected] if selected else [])
    assert trace["start"]["record"] is True
    assert trace["start"]["session_host"] is False
    assert trace["start"]["room_options"].text_input is False
    assert trace["start"]["room_options"].participant_identity == "caller"
    assert trace["audio"] == [False]
    if selected:
        assert trace["agents"][0].client.slug == selected
        assert not trace["closed"]
        assert not trace["deleted"]
    else:
        assert trace["agents"] == []
        assert trace["closed"] and trace["deleted"]


@pytest.mark.asyncio
async def test_hangup_cancels_pending_prompt(clients):
    room, started, cancelled = Room(), asyncio.Event(), asyncio.Event()

    async def say(text):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(demos.collect_demo_client(room, "caller", clients, say))
    await started.wait()
    room.emit("participant_disconnected", SimpleNamespace(identity="caller"))
    assert await asyncio.wait_for(task, 0.1) is None
    assert cancelled.is_set()
    assert not room._events.get("sip_dtmf_received")


@pytest.mark.asyncio
@pytest.mark.parametrize("retry", [False, True])
async def test_valid_code_selects_during_unfinished_menu(clients, retry):
    room, started, cancelled = Room(), asyncio.Queue(), []

    async def say(text):
        started.put_nowait(text)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(text)

    task = asyncio.create_task(demos.collect_demo_client(room, "caller", clients, say))
    assert await started.get() == demos.WELCOME
    if retry:
        for digit in "999":
            key(room, digit)
        assert await asyncio.wait_for(started.get(), 0.3) == demos.RETRY
    # Another participant's code and an incomplete caller code cannot select.
    for digit in "202":
        key(room, digit, who="someone-else")
    key(room, "1")
    await asyncio.sleep(0)
    assert not task.done()
    assert (demos.RETRY if retry else demos.WELCOME) not in cancelled
    for digit in "01":
        key(room, digit)
    try:
        selected = await asyncio.wait_for(task, 0.3)
        assert selected.slug == "sampleclub"
        assert (demos.RETRY if retry else demos.WELCOME) in cancelled
        assert not room._events.get("sip_dtmf_received")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_menu_audio_is_stopped_before_business_handoff(monkeypatch, clients):
    room, speaking = Room(), asyncio.Event()
    trace = []

    class Session:
        def __init__(self):
            self.input = SimpleNamespace(set_audio_enabled=lambda value: None)

        async def start(self, **kwargs):
            pass

        async def say(self, text, **kwargs):
            speaking.set()
            await asyncio.Event().wait()

        async def interrupt(self, *, force):
            assert force is True  # Menu speech disables automatic voice interruption.
            trace.append("audio-stopped")

        def update_agent(self, assistant):
            trace.append(assistant.client.slug)

    class Context:
        async def connect(self):
            pass

        async def wait_for_participant(self):
            return SimpleNamespace(identity="caller")

    ctx = Context()
    ctx.room = room
    monkeypatch.setattr(demos, "AgentSession", Session)
    task = asyncio.create_task(demos.run_demo(ctx, clients))
    await speaking.wait()
    for digit in "101":
        key(room, digit)
    await asyncio.wait_for(task, 0.3)
    assert trace == ["audio-stopped", "sampleclub"]


@pytest.mark.asyncio
async def test_spoken_business_greeting_is_natural_and_named_sophia(clients):
    demo = demos.DemoAssistant(clients["101"])
    spoken = []
    enabled = []

    async def say(text, **kwargs):
        spoken.append(text)

    demo._activity = SimpleNamespace(
        session=SimpleNamespace(
            say=say, input=SimpleNamespace(set_audio_enabled=enabled.append)
        )
    )
    await demo.on_enter()
    assert spoken == [
        "Hi, this is Sophia at Example Pickleball Club. How can I help you?"
    ]
    assert enabled == [True]


@pytest.mark.asyncio
async def test_caller_facing_menu_never_announces_a_demo(clients):
    room, say = Room(), Speech()
    await demos.collect_demo_client(room, "caller", clients, say, timeout=0.001)
    assert len(say.messages) == 4
    for message in say.messages:
        assert not any(
            word in message.lower()
            for word in ("demo", "practice", "artificial", " ai ")
        )
