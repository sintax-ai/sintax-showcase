"""Speak the farewell directly; retain LiveKit's session and room cleanup."""

import logging

from livekit.agents.beta.tools import EndCallTool
from livekit.agents.llm import Toolset

logger = logging.getLogger(__name__)


def make_end_call_tool(business_name: str, *, extra_description: str) -> EndCallTool:
    async def speak_farewell(event: Toolset.ToolCalledEvent) -> None:
        ctx = event.ctx
        ctx.disallow_interruptions()
        await ctx.wait_for_playout()
        logger.info("farewell_playout_started", extra={"business": business_name})
        try:
            # This creates a NEW speech handle. Waiting on ctx.speech_handle here
            # would deadlock: that original turn is waiting for this tool.
            speech = ctx.session.say(
                f"Thank you for calling {business_name}. Goodbye!",
                allow_interruptions=False,
            )
            await speech.wait_for_playout()
            if speech.exception() is not None or speech.interrupted:
                logger.warning(
                    "farewell_playout_failed", extra={"business": business_name}
                )
            else:
                logger.info(
                    "farewell_playout_completed", extra={"business": business_name}
                )
        except Exception:
            # A speech failure must be visible, but must not strand the caller.
            # The SDK still closes the session and deletes the room afterward.
            logger.exception(
                "farewell_playout_failed", extra={"business": business_name}
            )

    return EndCallTool(
        delete_room=True,
        end_instructions=None,  # No post-tool LLM generation: it can return empty.
        on_tool_called=speak_farewell,
        extra_description=extra_description,
    )
