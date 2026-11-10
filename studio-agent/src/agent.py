import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import (
    AgentServer,
    AgentSession,
    JobContext,
    TurnHandlingOptions,
    cli,
    function_tool,
    inference,
    room_io,
)
from livekit.plugins import ai_coustics

from calendar_dates import CalendarAgent
from call_ending import make_end_call_tool
from calls import finish_call_history, start_call_history
from clients import ClientConfig, load_client
from cloud import CloudConfig, attach_transcript, save_inquiry_cloud
from demos import DemoAssistant, load_demo_clients, run_demo
from inquiries import save_inquiry
from transcripts import save_transcript, spoken_turns

logger = logging.getLogger("agent")

load_dotenv(".env.local")


@dataclass(frozen=True)
class RuntimeConfig:
    mode: str
    client: ClientConfig | None = None
    cloud: CloudConfig | None = None
    storage_path: Path | None = None
    transcript_dir: str | None = None
    demo_clients: dict[str, ClientConfig] | None = None
    intake_clients: frozenset[str] = frozenset()


def load_runtime(env=None) -> RuntimeConfig:
    env = os.environ if env is None else env
    mode = env.get("SINTAX_MODE", "pilot")
    if mode == "demo":
        # Configure call history only after a verified keypad selection.
        # Local storage paths and SINTAX_CLIENT stay unused in the keypad mode.
        clients = load_demo_clients()
        requested = env.get("SINTAX_INTAKE_CLIENTS", "").strip()
        intake = (
            frozenset(slug.strip() for slug in requested.split(","))
            if requested
            else frozenset()
        )
        known = {client.slug for client in clients.values()}
        if not intake <= known:
            raise ValueError(
                "SINTAX_INTAKE_CLIENTS must contain only explicit client slugs in the keypad directory"
            )
        return RuntimeConfig(mode=mode, demo_clients=clients, intake_clients=intake)
    if mode != "pilot":
        raise ValueError("SINTAX_MODE must be demo or pilot")
    client = load_client(env.get("SINTAX_CLIENT", ""))
    return RuntimeConfig(
        mode=mode,
        client=client,
        cloud=CloudConfig.from_env(env, business_id=client.business_id),
        storage_path=Path(env["INQUIRY_DB_PATH"])
        if env.get("INQUIRY_DB_PATH")
        else None,
        transcript_dir=env.get("TRANSCRIPT_DIR") or None,
    )


class Assistant(CalendarAgent):
    def __init__(
        self,
        *,
        storage_path=None,
        cloud=None,
        session_id=None,
        client=None,
        call_history=None,
        keypad=False,
    ) -> None:
        self.storage_path = storage_path
        self.cloud = cloud
        self.client = client or load_client()
        self.session_id = session_id or str(uuid.uuid4())
        # Set once a request is saved, so the transcript can be attached to it
        # after the call ends.
        self.saved_inquiry_id = None
        self.saved_inquiry_ids = set()
        self.call_history = call_history
        self.keypad = keypad
        # Handoffs must replace the lobby's disabled STT and manual turn mode.
        voice_options = {}
        if keypad:
            voice_options = {
                "stt": inference.STT(
                    model=self.client.stt_model, language=self.client.language
                ),
                "tts": inference.TTS(
                    model=self.client.tts_model,
                    voice=self.client.voice,
                    extra_kwargs=self.client.tts_options,
                ),
                "turn_handling": {
                    "turn_detection": inference.TurnDetector(),
                    "interruption": {"mode": "adaptive"},
                    "preemptive_generation": {"enabled": True},
                },
                "expressive": self.client.expressive,
            }
        super().__init__(
            **voice_options,
            timezone=self.client.timezone,
            tools=[
                make_end_call_tool(
                    self.client.name,
                    extra_description="You MUST call this tool when the caller says goodbye, says they are finished, asks to hang up, or declines further help after the request is handled. Do not merely say goodbye and leave the line open. The tool says the final goodbye and then disconnects the call. Do not end for silence, a mid-conversation thank-you, hold on, or another question. Complete any explicitly authorized inquiry save and report its result before ending; never save without confirmation.",
                )
            ],
            llm=inference.LLM(model=self.client.llm_model),
            instructions=self.client.prompt
            + "\n\n"
            + (
                Path(__file__).resolve().parent.parent / "call_ending_instructions.md"
            ).read_text(),
        )

    async def on_enter(self):
        if self.keypad:
            await self.session.say(self.client.greeting, allow_interruptions=False)
            self.session.input.set_audio_enabled(True)

    @function_tool
    async def save_booking_inquiry(
        self,
        name: str,
        phone: str,
        service: str,
        preferred_time: str,
        duration: str,
        preferences: str,
        caller_confirmed: bool,
    ) -> dict:
        """Save an inquiry only after the caller explicitly confirms readback and saving.

        service: one of this business's exact service codes listed in your
        instructions. Use preferences for room, staff, package and other requests.
        This does not reserve appointments or notify staff. caller_confirmed must
        reflect an actual affirmative reply, not an assumption.
        """
        if not caller_confirmed:
            return {
                "status": "needs_confirmation",
                "message": "Read back and ask permission before saving.",
            }
        if self.storage_path is None and self.cloud is None:
            return {
                "status": "unavailable",
                "message": "Storage is not configured. No request was saved.",
            }
        details = {
            "name": name,
            "phone": phone,
            "service": service,
            "preferred_time": preferred_time,
            "duration": duration,
            "preferences": preferences,
            "services": self.client.services,
        }
        local = None
        if self.storage_path is not None:
            # A copy on this machine; the owner's portal reads Supabase.
            local = await asyncio.to_thread(
                save_inquiry, self.storage_path, self.session_id, **details
            )
        result = local
        if self.cloud is not None:
            call_id = (
                await self.call_history.ready_call_id() if self.call_history else None
            )
            result = await asyncio.to_thread(
                save_inquiry_cloud,
                self.cloud,
                self.session_id,
                call_id=call_id,
                **details,
            )
        if result.get("status") == "saved":
            self.saved_inquiry_id = result.get("inquiry_id")
            self.saved_inquiry_ids.add(self.saved_inquiry_id)
            if self.call_history is not None and self.cloud is not None:
                self.call_history.inquiry_ids.add(self.saved_inquiry_id)
        return result


def start_demo_history(ctx, client):
    try:
        cloud = CloudConfig.from_env(os.environ, business_id=client.business_id)
    except ValueError:
        logger.warning("Demo call history unavailable: check SUPABASE_* configuration")
        return None
    return start_call_history(ctx, cloud, mode="demo")


async def attach_request_transcripts(assistant, history):
    if assistant.cloud is None:
        return
    turns = spoken_turns(history)
    for inquiry_id in assistant.saved_inquiry_ids:
        try:
            result = await asyncio.to_thread(
                attach_transcript, assistant.cloud, inquiry_id, turns
            )
            logger.info("transcript %s", result["status"])
        except Exception:
            logger.exception("could not attach the transcript to the request")


def make_keypad_assistant(ctx, client, session, intake_clients):
    history = start_demo_history(ctx, client)
    if client.slug not in intake_clients:
        return DemoAssistant(client)
    assistant = Assistant(
        client=client,
        cloud=history.cfg if history is not None else None,
        session_id=ctx.room.name,
        call_history=history,
        keypad=True,
    )
    if assistant.cloud is None:
        # Keep the real tool's unavailable response; never simulate a saved request.
        logger.warning("Enabled keypad intake has no cloud storage configured")

    async def keep_request_transcripts():
        await attach_request_transcripts(
            assistant, session.history.to_dict(exclude_timestamp=False)
        )

    ctx.add_shutdown_callback(keep_request_transcripts)
    return assistant


server = AgentServer()


@server.rtc_session(agent_name="studio-agent", on_session_end=finish_call_history)
async def my_agent(ctx: JobContext):
    config = load_runtime()
    if config.mode == "demo":
        await run_demo(
            ctx,
            config.demo_clients,
            assistant_factory=lambda client, session: make_keypad_assistant(
                ctx, client, session, config.intake_clients
            ),
        )
        return
    client = config.client
    call_history = start_call_history(ctx, config.cloud, mode="pilot")
    # Logging setup
    # Add any other context you want in all log entries here
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    # Set up a voice AI pipeline using AssemblyAI, Fish Audio, and the LiveKit turn detector
    session = AgentSession(
        # Speech-to-text (STT) is your agent's ears, turning the user's speech into text that the LLM can understand
        # See all available models at https://docs.livekit.io/agents/models/stt/
        stt=inference.STT(model=client.stt_model, language=client.language),
        # Text-to-speech (TTS) is your agent's voice, turning the LLM's text into speech that the user can hear
        # See all available models as well as voice selections at https://docs.livekit.io/agents/models/tts/
        tts=inference.TTS(
            model=client.tts_model, voice=client.voice, extra_kwargs=client.tts_options
        ),
        turn_handling=TurnHandlingOptions(
            # The LiveKit turn detector determines when the user is done speaking and the agent should respond.
            # TurnDetector is an end-of-turn model that listens to the user's audio directly, combining
            # semantic understanding with acoustic cues (intonation, pitch, rhythm) for state-of-the-art accuracy.
            # AgentSession supplies the required VAD automatically.
            # See more at https://docs.livekit.io/agents/build/turns
            turn_detection=inference.TurnDetector(),
            # Adaptive interruptions use the turn detector to tell a real interruption from a
            # backchannel like "mhm" or "right", so the agent keeps talking through the latter.
            interruption={"mode": "adaptive"},
            # allow the LLM to generate a response while waiting for the end of turn
            # See more at https://docs.livekit.io/agents/build/audio/#preemptive-generation
            preemptive_generation={"enabled": True},
        ),
        # Let the model add delivery tags for emotion and emphasis in Fish Audio speech.
        expressive=client.expressive,
    )

    assistant = Assistant(
        storage_path=config.storage_path,
        cloud=config.cloud,
        session_id=ctx.room.name,
        client=client,
        call_history=call_history,
    )

    # Start the session, which initializes the voice pipeline and warms up the models
    await session.start(
        agent=assistant,
        room=ctx.room,
        record=config.cloud is not None,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(
                    model=ai_coustics.EnhancerModel.QUAIL_VF_S
                ),
            ),
        ),
    )

    # # Add a virtual avatar to the session, if desired
    # # For other providers, see https://docs.livekit.io/agents/models/avatar/
    # avatar = anam.AvatarSession(
    #     persona_config=anam.PersonaConfig(
    #         name="...",
    #         avatarId="...",  # See https://docs.livekit.io/agents/models/avatar/plugins/anam
    #     ),
    # )
    # # Start the avatar and wait for it to join
    # await avatar.start(session, room=ctx.room)

    # Keep this call's conversation once it ends. Two copies, both optional:
    #   - the full history as a file in TRANSCRIPT_DIR (for building evals);
    #   - the spoken turns attached to the saved request in Supabase, where the
    #     owner reads them under "What was said". Only if a request was saved.
    # This runs after the caller has hung up and can never delay a call.
    async def keep_transcript():
        history = session.history.to_dict(exclude_timestamp=False)
        if config.transcript_dir:
            try:
                path = await asyncio.to_thread(
                    save_transcript,
                    config.transcript_dir,
                    ctx.room.name,
                    history,
                    client=client.slug,
                )
                logger.info("transcript saved", extra={"path": str(path)})
            except Exception:
                logger.exception("could not save the call transcript")
        await attach_request_transcripts(assistant, history)

    ctx.add_shutdown_callback(keep_transcript)

    # Join the room and connect to the user
    await ctx.connect()
    await session.generate_reply(instructions=f'Say exactly: "{client.greeting}"')


if __name__ == "__main__":
    load_runtime()  # Refuse invalid mode/directory/client before accepting calls.
    cli.run_app(server)
