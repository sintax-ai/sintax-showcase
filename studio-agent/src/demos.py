"""Conversation-only demos: deterministic keypad selection, fresh agent per call."""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

from livekit import rtc
from livekit.agents import Agent, AgentSession, JobContext, inference, room_io
from livekit.plugins import ai_coustics

from calendar_dates import CalendarAgent
from call_ending import make_end_call_tool
from clients import DEFAULTS, ClientConfig, load_client

ROOT = Path(__file__).resolve().parent.parent
# Neutral lobby voice, independent of which business is eventually selected.
LOBBY_VOICE = "REPLACE_WITH_YOUR_VOICE_ID"
WELCOME = (
    "Welcome to SINTAX. Enter your three digit access code on your "
    "phone keypad. Press star to start over. If you need a code, ask the person "
    "who invited you."
)
RETRY = "That code was incomplete or not recognized. Enter your three digit access code again."
GOODBYE = (
    "We couldn't connect you. Please check your access code and call again. Goodbye."
)


def _unique_codes(pairs):
    result = {}
    for code, slug in pairs:
        if code in result:
            raise ValueError(f"Duplicate demo code: {code}")
        result[code] = slug
    return result


def load_demo_clients(path: Path = ROOT / "demo_codes.json") -> dict[str, ClientConfig]:
    """Validate the active directory before accepting calls. No implicit clients."""
    mapping = json.loads(path.read_text(), object_pairs_hook=_unique_codes)
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("Demo directory must contain at least one code")
    clients = {}
    for code, slug in mapping.items():
        if not re.fullmatch(r"[0-9]{3}", code):
            raise ValueError("Demo codes must have exactly three ASCII digits")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
            raise ValueError("Demo directory values must be client folder names")
        clients[code] = load_client(slug)
    return clients


async def collect_demo_client(
    room: rtc.Room,
    caller_identity: str,
    clients: Mapping[str, ClientConfig],
    say: Callable[[str], Awaitable],
    *,
    timeout: float = 30,
    prepare: Callable[[], Awaitable] | None = None,
) -> ClientConfig | None:
    """Collect only the linked caller's keypad input; never use speech or a fallback.

    State and subscriptions belong to one call. Register before greeting so input
    during the prompt is handled immediately. Bound the queue and the time per attempt.
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=32)
    disconnected = asyncio.Event()

    def on_digit(event):
        participant = event.participant
        if (
            participant is not None
            and participant.identity == caller_identity
            and event.digit in "0123456789*#"
            and len(event.digit) == 1
            and not queue.full()
        ):
            queue.put_nowait(event.digit)

    def on_disconnect(participant):
        if participant.identity == caller_identity:
            disconnected.set()
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(None)

    async def speak(text):
        if disconnected.is_set():
            return False
        speech = asyncio.ensure_future(say(text))
        hung_up = asyncio.create_task(disconnected.wait())
        try:
            await asyncio.wait((speech, hung_up), return_when=asyncio.FIRST_COMPLETED)
            if disconnected.is_set():
                return False
            await speech  # Propagate speech failures; never silently choose a client.
            return True
        finally:
            for task in (speech, hung_up):
                if not task.done():
                    task.cancel()
            await asyncio.gather(speech, hung_up, return_exceptions=True)

    async def read_digits():
        digits = ""
        while True:
            digit = await queue.get()
            if digit is None:
                return None
            if digit == "*":
                digits = ""
            elif digit == "#":
                return digits
            else:
                digits += digit
                if len(digits) == 3:
                    return digits

    room.on("sip_dtmf_received", on_digit)
    room.on("participant_disconnected", on_disconnect)
    try:
        if prepare is not None:
            await prepare()
        for attempt in range(3):
            speech = asyncio.create_task(speak(WELCOME if attempt == 0 else RETRY))
            entry = asyncio.create_task(read_digits())
            try:
                done, _ = await asyncio.wait(
                    (speech, entry), return_when=asyncio.FIRST_COMPLETED
                )
                if speech in done and not await speech:
                    return None
                try:
                    # Listen during speech; retain the full timeout after it ends.
                    digits = await asyncio.wait_for(entry, timeout)
                except TimeoutError:
                    digits = ""
            finally:
                # Cancelling speak also stops playback before retry or handoff.
                for task in (speech, entry):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(speech, entry, return_exceptions=True)
            if disconnected.is_set():
                return None
            if not disconnected.is_set() and digits in clients:
                return clients[digits]
            # Discard buffered extras from the failed attempt, not future typing.
            while not queue.empty():
                queue.get_nowait()
        await speak(GOODBYE)
        return None
    finally:
        room.off("sip_dtmf_received", on_digit)
        room.off("participant_disconnected", on_disconnect)


class DemoAssistant(CalendarAgent):
    """No storage dependencies and no inquiry-save tool, by construction."""

    def __init__(self, client: ClientConfig):
        self.client = client
        instructions = (ROOT / "demo_instructions.md").read_text()
        super().__init__(
            timezone=client.timezone,
            instructions=(
                f"Business: {client.name}\n"
                + instructions
                + "\n\nBUSINESS DEMO CUSTOMIZATION\n"
                + "The following preset and business instructions customize wording, intake, and the team name. "
                + "Business instructions take priority over preset style and generic wording examples above. "
                + "Shared accuracy, date verification, confirmation, identity honesty, and demo capability limits always remain in force.\n"
                + client.demo_style_instructions
                + "\n"
                + client.demo_instructions
                + "\nBusiness service categories: "
                + ", ".join(sorted(client.services))
                + "\n\nBUSINESS FACTS:\n"
                + client.knowledge
                + "\n\n"
                + (ROOT / "call_ending_instructions.md").read_text()
            ),
            tools=[
                make_end_call_tool(
                    client.name,
                    extra_description="You MUST call this tool when the caller says goodbye, says they are finished, asks to hang up, or declines further help after the request is handled. Do not merely say goodbye and leave the line open. The tool says the final goodbye and then disconnects the call. Do not end for silence, a mid-conversation thank-you, hold on, or another question.",
                )
            ],
            stt=inference.STT(model=client.stt_model, language=client.language),
            llm=inference.LLM(model=client.llm_model),
            tts=inference.TTS(
                model=client.tts_model,
                voice=client.voice,
                extra_kwargs=client.tts_options,
            ),
            turn_handling={
                "turn_detection": inference.TurnDetector(),
                "interruption": {"mode": "adaptive"},
                "preemptive_generation": {"enabled": True},
            },
            expressive=client.expressive,
        )

    async def on_enter(self):
        await self.session.say(
            f"Hi, this is Sophia at {self.client.name}. How can I help you?",
            allow_interruptions=False,
        )
        self.session.input.set_audio_enabled(True)


async def run_demo(
    ctx: JobContext,
    clients: Mapping[str, ClientConfig],
    *,
    on_client_selected: Callable[[ClientConfig], None] | None = None,
    assistant_factory: Callable[[ClientConfig, AgentSession], Agent] | None = None,
):
    """Run the lobby; notify history only after deterministic business selection."""
    await ctx.connect()
    caller = await asyncio.wait_for(ctx.wait_for_participant(), timeout=30)
    session = AgentSession()
    session.input.set_audio_enabled(False)

    async def prepare():
        await session.start(
            agent=Agent(
                instructions="A deterministic keypad menu. Do not converse or choose a business.",
                stt=None,
                llm=None,
                tts=inference.TTS(model=DEFAULTS["tts_model"], voice=LOBBY_VOICE),
                turn_handling={"turn_detection": "manual"},
            ),
            room=ctx.room,
            record=True,
            session_host=False,
            room_options=room_io.RoomOptions(
                participant_identity=caller.identity,
                text_input=False,
                audio_input=room_io.AudioInputOptions(
                    noise_cancellation=ai_coustics.audio_enhancement(
                        model=ai_coustics.EnhancerModel.QUAIL_VF_S
                    ),
                ),
            ),
        )

    async def say(text):
        try:
            await session.say(text, allow_interruptions=False)
        except asyncio.CancelledError:
            # Cancelling the Python waiter alone does not stop LiveKit playback.
            # Force is needed because lobby speech ignores voice interruptions.
            await session.interrupt(force=True)
            raise

    client = await collect_demo_client(
        ctx.room, caller.identity, clients, say, prepare=prepare
    )
    if client is None:
        await session.aclose()
        await ctx.delete_room()
        ctx.shutdown(reason="demo code not selected")
        return
    if on_client_selected is not None:
        on_client_selected(client)
    # update_agent starts a fresh activity with this client's own models/context.
    assistant = (
        assistant_factory(client, session)
        if assistant_factory
        else DemoAssistant(client)
    )
    session.update_agent(assistant)
