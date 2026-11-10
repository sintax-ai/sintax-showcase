"""Regression for the empty post-tool reply seen in Example Studios's cloud call."""

import asyncio
from types import SimpleNamespace

import pytest
from livekit.agents.beta.tools import EndCallTool
from livekit.agents.voice.speech_handle import SpeechHandle

from agent import Assistant
from clients import load_client
from demos import DemoAssistant


@pytest.mark.asyncio
@pytest.mark.parametrize("assistant_type", [DemoAssistant, Assistant])
@pytest.mark.parametrize("slug", ["sample-studio", "sampleclub"])
async def test_end_call_speaks_selected_business_and_waits_before_shutdown(
    assistant_type, slug
):
    client = load_client(slug)
    assistant = assistant_type(client=client)
    tool = next(t for t in assistant.tools if isinstance(t, EndCallTool))
    turn = SpeechHandle.create()
    farewell = SpeechHandle.create(allow_interruptions=False)
    speaking = asyncio.Event()
    effects = []

    # Only audio transport and the running session boundary are substituted.
    # The registered production tool and its SDK shutdown callback are real.
    def say(text, *, allow_interruptions):
        assert client.name in text
        assert "thank" in text.lower() and "goodbye" in text.lower()
        assert allow_interruptions is False
        effects.append("speak")
        speaking.set()
        return farewell

    async def previous_playout():
        effects.append("previous_playout")

    session = SimpleNamespace(
        current_agent=SimpleNamespace(
            _get_activity_or_raise=lambda: SimpleNamespace(
                llm=None, realtime_llm_session=None
            )
        ),
        say=say,
        once=lambda *_: None,
        shutdown=lambda: effects.append("shutdown"),
    )
    ctx = SimpleNamespace(
        session=session,
        speech_handle=turn,
        disallow_interruptions=lambda: effects.append("protect_turn"),
        wait_for_playout=previous_playout,
    )
    task = asyncio.create_task(tool.tools[0](ctx))
    try:
        await asyncio.wait_for(speaking.wait(), timeout=1)
        assert not task.done(), "end_call must wait until the farewell finishes"
        assert "shutdown" not in effects
        farewell._mark_done()
        assert await asyncio.wait_for(task, timeout=1) is None  # No LLM follow-up.
        turn._mark_done()
        await asyncio.sleep(0)  # Run the SDK's turn-completion callback.
        assert effects == ["protect_turn", "previous_playout", "speak", "shutdown"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_session_finishes_farewell_without_post_tool_llm_request(
    monkeypatch,
):
    """Exercise real SDK scheduling, suppressing only external inference/room API."""
    from livekit.agents import Agent, AgentSession, llm
    from livekit.agents.beta.tools import end_call

    from call_ending import make_end_call_tool

    class ToolOnlyStream(llm.LLMStream):
        async def _run(self):
            self._event_ch.send_nowait(
                llm.ChatChunk(
                    id="fictional-request",
                    delta=llm.ChoiceDelta(
                        role="assistant",
                        tool_calls=[
                            llm.FunctionToolCall(
                                name="end_call",
                                arguments="{}",
                                call_id="fictional-tool",
                            )
                        ],
                    ),
                )
            )

    class ToolOnlyLLM(llm.LLM):
        calls = 0

        def chat(self, *, chat_ctx, tools, conn_options, **kwargs):
            self.calls += 1
            return ToolOnlyStream(
                self, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options
            )

    job_closed = asyncio.Event()
    cleanup = []
    job = SimpleNamespace(
        add_shutdown_callback=cleanup.append,
        shutdown=lambda **_: job_closed.set(),
    )
    monkeypatch.setattr(end_call, "get_job_context", lambda: job)
    model = ToolOnlyLLM()
    assistant = Agent(
        instructions="End the call when the caller is finished.",
        tools=[make_end_call_tool("Example Studios", extra_description="")],
    )
    async with AgentSession(llm=model) as session:
        await session.start(agent=assistant, record=False)
        await asyncio.wait_for(session.run(user_input="Thanks, bye."), timeout=5)
        await asyncio.wait_for(job_closed.wait(), timeout=5)
        assert model.calls == 1, "The farewell must not need a second LLM response"
        assert any(
            item.type == "message"
            and item.role == "assistant"
            and "Thank you for calling Example Studios. Goodbye!"
            in item.text_content
            for item in session.history.items
        )
        assert len(cleanup) == 1  # SDK room-deletion cleanup is still registered.
