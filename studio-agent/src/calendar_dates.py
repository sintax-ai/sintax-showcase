"""Shared business clock and read-only calendar confirmation. No booking access."""

import logging
import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from livekit.agents import Agent, RunContext, StopResponse, function_tool, llm
from livekit.agents.llm import Toolset

logger = logging.getLogger(__name__)

WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

DAY_WORDS = (
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
    "thirteenth",
    "fourteenth",
    "fifteenth",
    "sixteenth",
    "seventeenth",
    "eighteenth",
    "nineteenth",
    "twentieth",
    "twenty first",
    "twenty second",
    "twenty third",
    "twenty fourth",
    "twenty fifth",
    "twenty sixth",
    "twenty seventh",
    "twenty eighth",
    "twenty ninth",
    "thirtieth",
    "thirty first",
)
CARDINAL_DAYS = (
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
    "twenty one",
    "twenty two",
    "twenty three",
    "twenty four",
    "twenty five",
    "twenty six",
    "twenty seven",
    "twenty eight",
    "twenty nine",
    "thirty",
    "thirty one",
)
WD = "(?:" + "|".join(w.lower() for w in WEEKDAYS) + ")"
MONTH = "(?:" + "|".join(m.lower() for m in MONTHS) + ")"
DAY = (
    r"(?:\d{1,2}(?:st|nd|rd|th)?|"
    + "|".join(
        w.replace(" ", "[ -]")
        for w in sorted((*DAY_WORDS, *CARDINAL_DAYS), key=len, reverse=True)
    )
    + ")"
)
ABSOLUTE = rf"(?:{WD},?\s+)?{MONTH}\s+(?:the\s+)?{DAY}(?:,?\s+\d{{4}})?"
WEEK_PHRASE = (
    rf"(?:{WD}\s+(?:of\s+)?(?:next|this)\s+week|(?:next|this)\s+week\s+(?:on\s+)?{WD})"
)
DATE_PHRASES = re.compile(
    rf"\b(?:{ABSOLUTE}|\d{{4}}-\d{{2}}-\d{{2}}|"
    rf"\d{{1,2}}[/-]\d{{1,2}}(?:[/-]\d{{2,4}})?|{WEEK_PHRASE}|"
    rf"(?:next|this)\s+{WD}|{WD}\s+(?:is\s+)?(?:the\s+)?{DAY}|"
    rf"(?:the\s+)?day after tomorrow|tomorrow|today|(?:next|this)\s+week|{WD})\b",
    re.IGNORECASE,
)
CLARIFY_DATE = "Could you give me the month, day, and year you mean?"


def normalize_months(text: str) -> str:
    for month in MONTHS:
        short = "sept?" if month == "September" else month[:3]
        text = re.sub(rf"\b{short}\.?(?=\s|$)", month, text, flags=re.IGNORECASE)
    return text


def date_phrases(text: str) -> list[str]:
    # Delivery tags must not hide a date from validation.
    text = normalize_months(re.sub(r"<[^>]*>", " ", text))
    return [m.group() for m in DATE_PHRASES.finditer(text)]


def caller_date(text: str, zone: str) -> "DateResolution | None":
    phrases = date_phrases(text)
    if not phrases:
        return None
    if len(phrases) != 1:
        return DateResolution("needs_clarification", None, CLARIFY_DATE)
    return resolve_date(phrases[0], zone)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def local_now(zone: str, now: datetime | None = None) -> datetime:
    instant = now if now is not None else utc_now()
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("Clock must include a timezone")
    return instant.astimezone(ZoneInfo(zone))


def spoken_date(value: date) -> str:
    # Fixed English names rather than the host machine's locale.
    return f"{WEEKDAYS[value.weekday()]}, {MONTHS[value.month - 1]} {value.day}, {value.year}"


@dataclass(frozen=True)
class DateResolution:
    status: str
    date: date | None
    speech: str


def resolve_date(
    phrase: str, zone: str, *, now: datetime | None = None
) -> DateResolution:
    """Resolve bounded English date phrases; confirm the proposed date and year.

    'Next Tuesday' means the next strictly future Tuesday; 'this Tuesday' or
    bare Tuesday can include today. Every result requires caller confirmation
    because people use 'next' differently. 'Tuesday next week' is Tuesday in the
    following Monday-to-Sunday calendar week. A missing year uses the current
    business year only if that date has not passed; otherwise ask for the year.
    Numeric order, ranges, times and availability are out of scope.
    """
    today = local_now(zone, now).date()
    text = re.sub(r"\s+", " ", normalize_months(phrase).strip().lower()).rstrip(".?!")
    day_words = [*enumerate(DAY_WORDS, 1), *enumerate(CARDINAL_DAYS, 1)]
    for number, word in sorted(day_words, key=lambda pair: -len(pair[1])):
        text = re.sub(r"\b" + word.replace(" ", "[ -]") + r"\b", str(number), text)
    text = re.sub(r"\bthe (\d)", r"\1", text)
    value = None
    relative = {
        "today": 0,
        "tomorrow": 1,
        "day after tomorrow": 2,
        "the day after tomorrow": 2,
    }
    if text in relative:
        value = today + timedelta(days=relative[text])
    else:
        week = re.fullmatch(
            rf"({WD}) (?:of )?(next|this) week|(next|this) week (?:on )?({WD})", text
        )
        weekday = re.fullmatch(
            r"(?:(next|this) )?(" + "|".join(w.lower() for w in WEEKDAYS) + r")", text
        )
        if week:
            day = week[1] or week[4]
            which_week = week[2] or week[3]
            monday = today - timedelta(days=today.weekday())
            value = monday + timedelta(
                days=WEEKDAYS.index(day.title()) + (7 if which_week == "next" else 0)
            )
        elif weekday:
            offset = (WEEKDAYS.index(weekday[2].title()) - today.weekday()) % 7
            if weekday[1] == "next" and offset == 0:
                offset = 7
            value = today + timedelta(days=offset)
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            with suppress(ValueError):
                value = date.fromisoformat(text)
        else:
            absolute = re.fullmatch(
                r"(?:("
                + "|".join(w.lower() for w in WEEKDAYS)
                + r"),? )?("
                + "|".join(m.lower() for m in MONTHS)
                + r") (\d{1,2})(?:st|nd|rd|th)?(?:,? (\d{4}))?",
                text,
            )
            if absolute:
                with suppress(ValueError):
                    value = date(
                        int(absolute[4]) if absolute[4] else today.year,
                        MONTHS.index(absolute[2].title()) + 1,
                        int(absolute[3]),
                    )
                if (
                    value is not None
                    and absolute[1]
                    and absolute[1] != WEEKDAYS[value.weekday()].lower()
                ):
                    return DateResolution(
                        "needs_clarification",
                        None,
                        f"Let me clarify: {spoken_date(value)} is the calendar date I found. Would you prefer that date, or the weekday you mentioned?",
                    )
                if value is not None and not absolute[4] and value < today:
                    value = None  # Never silently move a past month/day to next year.
    if value is None:
        return DateResolution(
            "needs_clarification",
            None,
            CLARIFY_DATE,
        )
    return DateResolution(
        "needs_confirmation",
        value,
        f"That's {spoken_date(value)}. Is that the date you mean?",
    )


def guard_calendar_reply(text: str, verified: DateResolution | None, zone: str) -> str:
    """Check recognized date claims before any model text is sent to TTS.

    Bounded English protection, not a general natural-language fact checker.
    Standalone weekdays remain available for ordinary opening-hours discussion.
    Once a date is in scope, weekday references must also match that date.
    """
    phrases = date_phrases(text)
    # A list of opening weekdays contains no calendar arithmetic. If a calendar
    # date is also present, check accompanying weekdays as part of that claim.
    if not any(not re.fullmatch(WD, p.lower()) for p in phrases):
        return text
    if verified is None:
        phrases = [p for p in phrases if not re.fullmatch(WD, p.lower())]
    for phrase in phrases:
        if verified is None or verified.date is None:
            return verified.speech if verified else CLARIFY_DATE
        expected = verified.date
        if re.fullmatch(WD, phrase.lower()):
            valid = phrase.lower() == WEEKDAYS[expected.weekday()].lower()
        else:
            # A verified January readback can cross the year boundary. Use that
            # explicit verified year, not the server's current year, to check it.
            if re.fullmatch(ABSOLUTE, phrase, re.IGNORECASE) and not re.search(
                r"\b\d{4}\b", phrase
            ):
                phrase += f", {expected.year}"
            result = resolve_date(phrase, zone)
            valid = result.date == expected
        if not valid:
            logger.warning("calendar_output_blocked", extra={"timezone": zone})
            return verified.speech
    return text


class CalendarTools(Toolset):
    """Bound to the selected business, never to a timezone chosen by the model."""

    def __init__(self, zone: str):
        self.zone = zone
        self.last_resolution: DateResolution | None = None
        super().__init__(
            id="calendar", tools=[function_tool(self.resolve_calendar_date)]
        )

    async def resolve_calendar_date(self, ctx: RunContext, date_phrase: str) -> None:
        """Check a caller's date and speak the exact calendar confirmation.

        date_phrase: Copy ONLY the date words the caller actually said, verbatim
        (e.g. 'next Tuesday'). Do not translate relative dates to numbers, add a
        year, or calculate anything yourself. Exclude time of day and other words.
        Call BEFORE saying any computed date or weekday/date combination. This
        tool speaks and waits for the caller; don't speak a date before/after it.
        This does not check availability, book or save anything.
        """
        latest = next(
            (
                i.text_content
                for i in reversed(ctx.session.history.items)
                if i.type == "message" and i.role == "user"
            ),
            "",
        )
        # The model selects the tool, but the actual caller transcript supplies
        # its date. A model-invented year must never become authoritative input.
        result = caller_date(latest, self.zone)
        if result is None:
            result = DateResolution("needs_clarification", None, CLARIFY_DATE)
        if date_phrases(latest) != [date_phrase]:
            logger.info("calendar_argument_rechecked_from_caller")
        self.last_resolution = result
        logger.info(
            "calendar_date_checked",
            extra={"timezone": self.zone, "resolution_status": result.status},
        )
        await ctx.session.say(result.speech, allow_interruptions=True)
        # Prevent the automatic LLM follow-up from rewording the computed date.
        raise StopResponse()


class CalendarAgent(Agent):
    """Give both pilot and demo agents the same date rules and fresh local clock."""

    def __init__(self, *, timezone: str, instructions: str, tools=None, **kwargs):
        self.business_timezone = timezone
        self.base_instructions = instructions
        self.calendar = CalendarTools(timezone)
        super().__init__(
            instructions=self._with_clock(),
            tools=[*(tools or []), self.calendar],
            **kwargs,
        )

    def _with_clock(self) -> str:
        return self.base_instructions + self._clock_rules()

    def _clock_rules(self) -> str:
        now = local_now(self.business_timezone)
        return (
            f"\n\nBUSINESS CLOCK AND DATE RULES\n"
            f"Business timezone: {self.business_timezone}. Current local date: {now.date().isoformat()}. "
            f"Today is {spoken_date(now.date())}. This latest clock overrides earlier clock readings and dates in static business facts.\n"
            "Use resolve_calendar_date BEFORE converting any relative date (today, tomorrow, next Tuesday) "
            "or stating/confirming a weekday paired with a calendar date. Pass the caller's exact date words, "
            "not a date you calculated. Do not state a date before calling the tool. "
            "The tool calculates from a fresh clock and speaks the confirmation itself. Wait for the caller's answer. "
            "If the caller corrects the date, check it again. Never claim to double-check without actually verifying. "
            "Acknowledge corrections calmly; do not insist the caller is wrong based on your own calculation. "
            "If wording is unsupported, clarify; never guess after a clarification request. "
            "Preserve time of day separately without timezone conversion. "
            "Never infer availability, opening hours, booking or saving from calendar arithmetic."
        )

    async def llm_node(self, chat_ctx, tools, model_settings):
        latest = next(
            (
                i.text_content
                for i in reversed(chat_ctx.items)
                if i.type == "message" and i.role == "user"
            ),
            "",
        )
        current = caller_date(latest, self.business_timezone)
        verified = current if current is not None else self.calendar.last_resolution
        if current is not None:
            # Scope this to the current call. A new unresolved date invalidates
            # the old date so a correction cannot silently reuse stale context.
            self.calendar.last_resolution = current
            if re.search(
                r"\b(no|actually|incorrect|wrong|i think|i meant)\b",
                latest,
                re.IGNORECASE,
            ):
                logger.info("calendar_correction_checked")
                yield current.speech.replace("That's ", "Let me confirm: ", 1)
                return

        # Buffer this reply BEFORE the TTS boundary so streamed partial dates
        # cannot escape validation. This adds buffering, not another LLM call.
        chunks = [
            c
            async for c in Agent.default.llm_node(self, chat_ctx, tools, model_settings)
        ]
        calendar_calls = []
        text_parts = []
        for chunk in chunks:
            if isinstance(chunk, str):
                text_parts.append(chunk)
            elif isinstance(chunk, llm.ChatChunk) and chunk.delta:
                text_parts.append(chunk.delta.content or "")
                calendar_calls.extend(
                    c
                    for c in chunk.delta.tool_calls
                    if c.name == "resolve_calendar_date"
                )
        if calendar_calls:
            # The tool speaks the checked result itself. Suppress any speculative
            # preamble (and other actions) accompanying a calendar verification.
            yield llm.ChatChunk(
                id="calendar-verification",
                delta=llm.ChoiceDelta(role="assistant", tool_calls=calendar_calls),
            )
            return
        text = "".join(text_parts)
        checked = guard_calendar_reply(text, verified, self.business_timezone)
        if checked != text:
            yield checked
            return  # Do not execute actions bundled with a rejected date claim.
        for chunk in chunks:
            yield chunk

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        clock_rules = self._clock_rules()
        instructions = self.base_instructions + clock_rules
        if instructions == self.instructions:
            return  # Preserve preemptive generation while the local date is unchanged.
        await self.update_instructions(instructions)
        # LiveKit copied this context before the hook. Refresh the current turn
        # too; its temporary message also invalidates stale preemptive replies.
        turn_ctx.add_message(role="system", content=clock_rules)
