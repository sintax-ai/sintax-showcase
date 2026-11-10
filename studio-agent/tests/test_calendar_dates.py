"""Calendar arithmetic uses controlled clocks, never a model's guessed dates."""

from datetime import datetime
from types import SimpleNamespace

import pytest


def resolve(phrase, instant="2026-10-03T16:00:00+00:00", zone="America/New_York"):
    from calendar_dates import resolve_date

    return resolve_date(phrase, zone, now=datetime.fromisoformat(instant))


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("next Tuesday", "2026-10-06"),
        ("this Tuesday", "2026-10-06"),
        ("Tuesday", "2026-10-06"),
        ("today", "2026-10-03"),
        ("tomorrow", "2026-10-04"),
        ("day after tomorrow", "2026-10-05"),
        ("October 6th, 2026", "2026-10-06"),
        ("Tuesday, October 6, 2026", "2026-10-06"),
        ("2026-10-06", "2026-10-06"),
    ],
)
def test_known_dates_are_calculated_and_await_confirmation(phrase, expected):
    result = resolve(phrase)
    assert result.date.isoformat() == expected
    assert result.status == "needs_confirmation"
    assert "Is that the date you mean?" in result.speech


def test_reported_wrong_weekday_is_not_silently_accepted():
    result = resolve("Tuesday, October 7, 2026")
    assert result.date is None
    assert result.status == "needs_clarification"
    assert "Wednesday" in result.speech


@pytest.mark.parametrize(
    "phrase",
    [
        "next week",
        "10/07",
        "February 30, 2026",
        "2026-13-01",
        "next Tuesday or Wednesday",
        "",
    ],
)
def test_ambiguous_or_invalid_input_requests_clarification(phrase):
    result = resolve(phrase)
    assert result.date is None
    assert result.status == "needs_clarification"


@pytest.mark.parametrize(
    "phrase,instant,zone,expected",
    [
        ("today", "2026-10-04T02:00:00+00:00", "America/New_York", "2026-10-03"),
        ("today", "2026-10-04T02:00:00+00:00", "Europe/London", "2026-10-04"),
        ("tomorrow", "2026-12-31T17:00:00+00:00", "America/New_York", "2027-01-01"),
        ("tomorrow", "2028-02-28T17:00:00+00:00", "America/New_York", "2028-02-29"),
        ("tomorrow", "2026-03-08T06:30:00+00:00", "America/New_York", "2026-03-09"),
        ("today", "2026-11-01T05:30:00+00:00", "America/New_York", "2026-11-01"),
        ("today", "2026-11-01T06:30:00+00:00", "America/New_York", "2026-11-01"),
        ("next Tuesday", "2026-10-06T16:00:00+00:00", "America/New_York", "2026-10-13"),
        ("this Tuesday", "2026-10-06T16:00:00+00:00", "America/New_York", "2026-10-06"),
    ],
)
def test_timezone_and_calendar_boundaries(phrase, instant, zone, expected):
    assert resolve(phrase, instant, zone).date.isoformat() == expected


@pytest.mark.asyncio
async def test_tool_speaks_calculated_date_without_model_rewriting(monkeypatch):
    from livekit.agents import StopResponse

    import calendar_dates

    monkeypatch.setattr(
        calendar_dates,
        "utc_now",
        lambda: datetime.fromisoformat("2026-10-03T16:00:00+00:00"),
    )
    spoken = []

    async def say(text, **kwargs):
        spoken.append(text)

    tool = calendar_dates.CalendarTools("America/New_York")
    ctx = SimpleNamespace(
        session=SimpleNamespace(
            say=say,
            history=SimpleNamespace(
                items=[
                    SimpleNamespace(
                        type="message", role="user", text_content="next Tuesday"
                    )
                ]
            ),
        )
    )
    with pytest.raises(StopResponse):
        await tool.resolve_calendar_date(ctx, "next Tuesday")
    assert spoken == ["That's Tuesday, October 6, 2026. Is that the date you mean?"]


@pytest.mark.asyncio
async def test_business_clock_refreshes_between_turns(monkeypatch):
    from livekit.agents import llm

    import calendar_dates

    clock = datetime.fromisoformat("2026-10-04T03:59:00+00:00")
    monkeypatch.setattr(calendar_dates, "utc_now", lambda: clock)
    agent = calendar_dates.CalendarAgent(
        timezone="America/New_York", instructions="Business rules."
    )
    assert "2026-10-03" in agent.instructions
    turn = llm.ChatContext()
    turn.add_message(role="system", content=agent.instructions)
    clock = datetime.fromisoformat("2026-10-04T04:01:00+00:00")
    await agent.on_user_turn_completed(turn, None)
    assert "2026-10-04" in agent.instructions
    assert "2026-10-03" not in agent.instructions
    assert "2026-10-04" in turn.items[-1].text_content


@pytest.mark.asyncio
async def test_same_day_turn_keeps_preemptive_context_unchanged(monkeypatch):
    from livekit.agents import llm

    import calendar_dates

    clock = datetime.fromisoformat("2026-10-03T16:00:00+00:00")
    monkeypatch.setattr(calendar_dates, "utc_now", lambda: clock)
    agent = calendar_dates.CalendarAgent(
        timezone="America/New_York", instructions="Rules."
    )
    turn = llm.ChatContext()
    turn.add_message(role="system", content=agent.instructions)
    before = turn.copy()
    clock = datetime.fromisoformat("2026-10-03T16:01:00+00:00")
    await agent.on_user_turn_completed(turn, None)
    assert turn.is_equivalent(before)


@pytest.mark.parametrize(
    "phrase,instant,expected",
    [
        ("Tuesday next week", "2026-10-30T05:30:00+00:00", "2026-11-03"),
        ("next week on Tuesday", "2026-10-30T05:30:00+00:00", "2026-11-03"),
        ("Tuesday of next week", "2026-10-30T05:30:00+00:00", "2026-11-03"),
        ("Tuesday, November 3rd", "2026-10-30T05:30:00+00:00", "2026-11-03"),
        ("November third", "2026-10-30T05:30:00+00:00", "2026-11-03"),
        ("Tuesday next week", "2026-11-02T17:00:00+00:00", "2026-11-10"),
        ("next Tuesday", "2026-11-02T17:00:00+00:00", "2026-11-03"),
        ("Tuesday next week", "2026-12-31T17:00:00+00:00", "2027-01-05"),
    ],
)
def test_natural_week_phrases_and_year_boundaries(phrase, instant, expected):
    result = resolve(phrase, instant)
    assert result.status == "needs_confirmation"
    assert result.date.isoformat() == expected


def test_missing_year_does_not_silently_roll_a_past_date_forward():
    assert resolve("November 3", "2026-12-01T17:00:00+00:00").date is None


@pytest.mark.parametrize(
    "text",
    [
        "Are you looking for Tuesday, November 4th?",
        "Let me double-check. Tuesday is actually November 4th. Monday is the 3rd.",
        'Tuesday is <expr type="expression" label="confident"/> November fourth.',
        "Monday the 3rd works for you?",
        "You mean 11/04?",
        "Tuesday is Nov. 4th.",
        "You mean November four?",
    ],
)
def test_unverified_date_claim_never_passes_output_guard(text):
    from calendar_dates import guard_calendar_reply

    result = resolve("Tuesday next week", "2026-10-30T05:30:00+00:00")
    checked = guard_calendar_reply(text, result, "America/New_York")
    assert checked == result.speech
    assert "November 4" not in checked


def test_verified_date_readback_and_ordinary_answers_survive_guard():
    from calendar_dates import guard_calendar_reply

    result = resolve("Tuesday next week", "2026-10-30T05:30:00+00:00")
    for text in [
        "Your request is for Tuesday, November 3rd at 5 PM.",
        "Your request is for November third, 2026.",
        "Your request is for Nov. 3, 2026.",
        "The photo studio costs $60 per hour. What time works?",
    ]:
        assert guard_calendar_reply(text, result, "America/New_York") == text


@pytest.mark.asyncio
async def test_model_stream_cannot_leak_wrong_date_and_correction_is_rechecked(
    monkeypatch,
):
    from livekit.agents import Agent, llm

    import calendar_dates

    monkeypatch.setattr(
        calendar_dates,
        "utc_now",
        lambda: datetime.fromisoformat("2026-10-30T05:30:00+00:00"),
    )
    requests = []

    async def model(agent, chat_ctx, tools, settings):
        requests.append(chat_ctx)
        for part in ["Tuesday is ", "November ", "4th. Monday is the 3rd."]:
            yield llm.ChatChunk(id="test", delta=llm.ChoiceDelta(content=part))

    monkeypatch.setattr(Agent.default, "llm_node", model)
    agent = calendar_dates.CalendarAgent(timezone="America/New_York", instructions="")
    ctx = llm.ChatContext()
    ctx.add_message(role="user", content="Can we do Tuesday next week?")
    output = [c async for c in agent.llm_node(ctx, [], None)]
    assert output == ["That's Tuesday, November 3, 2026. Is that the date you mean?"]
    ctx.add_message(role="assistant", content="Tuesday is November 4th.")
    ctx.add_message(role="user", content="No. I think it's Tuesday, November 3rd.")
    output = [c async for c in agent.llm_node(ctx, [], None)]
    assert output == [
        "Let me confirm: Tuesday, November 3, 2026. Is that the date you mean?"
    ]
    assert len(requests) == 1  # The correction is calculated, not another model guess.


@pytest.mark.asyncio
async def test_calendar_tool_uses_callers_words_not_model_invented_year(monkeypatch):
    from livekit.agents import StopResponse

    import calendar_dates

    monkeypatch.setattr(
        calendar_dates,
        "utc_now",
        lambda: datetime.fromisoformat("2026-10-30T05:30:00+00:00"),
    )
    spoken = []

    async def say(text, **kwargs):
        spoken.append(text)

    ctx = SimpleNamespace(
        session=SimpleNamespace(
            say=say,
            history=SimpleNamespace(
                items=[
                    SimpleNamespace(
                        type="message",
                        role="user",
                        text_content="I'd like Tuesday next week.",
                    )
                ]
            ),
        )
    )
    tool = calendar_dates.CalendarTools("America/New_York")
    with pytest.raises(StopResponse):
        await tool.resolve_calendar_date(ctx, "Tuesday, November 4, 2025")
    assert spoken == ["That's Tuesday, November 3, 2026. Is that the date you mean?"]


def test_opening_weekdays_do_not_get_rewritten_as_the_requested_date():
    from calendar_dates import guard_calendar_reply

    result = resolve("Tuesday next week", "2026-10-30T05:30:00+00:00")
    text = "The business opens Monday through Friday."
    assert guard_calendar_reply(text, result, "America/New_York") == text


@pytest.mark.asyncio
async def test_date_check_survives_real_session_tool_execution(monkeypatch):
    from livekit.agents import Agent, AgentSession, llm

    import calendar_dates

    monkeypatch.setattr(
        calendar_dates,
        "utc_now",
        lambda: datetime.fromisoformat("2026-10-30T05:30:00+00:00"),
    )

    class UnusedModel(llm.LLM):
        def chat(self, **kwargs):
            raise AssertionError("Model transport is replaced by a controlled stream")

    async def model(agent, chat_ctx, tools, settings):
        yield llm.ChatChunk(
            id="test",
            delta=llm.ChoiceDelta(
                content="Tuesday is November 4th.",
                tool_calls=[
                    llm.FunctionToolCall(
                        name="resolve_calendar_date",
                        arguments='{"date_phrase": "Tuesday, November 4, 2025"}',
                        call_id="fixture-calendar",
                    )
                ],
            ),
        )

    monkeypatch.setattr(Agent.default, "llm_node", model)
    async with AgentSession(llm=UnusedModel()) as session:
        await session.start(
            calendar_dates.CalendarAgent(timezone="America/New_York", instructions=""),
            record=False,
        )
        await session.run(user_input="Can we do Tuesday next week?")
        replies = [
            i.text_content
            for i in session.history.items
            if i.type == "message" and i.role == "assistant"
        ]
        assert replies == [
            "That's Tuesday, November 3, 2026. Is that the date you mean?"
        ]
