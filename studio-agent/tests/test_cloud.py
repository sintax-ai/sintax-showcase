"""Saving inquiries straight to Supabase. Uses a local fake; never touches the real project."""

import sqlite3
import time

import pytest
from fake_supabase import FakeSupabase

from agent import Assistant

BUSINESS = "d00c0c1d-7687-5992-bfbd-b69a214c566e"
DETAILS = {
    "name": "FICTIONAL Caller",
    "phone": "(201) 555-0100",
    "service": "recording",
    "preferred_time": "Friday at 3 PM",
    "duration": "2 hours",
    "preferences": "Room A",
}


@pytest.fixture
def fake():
    f = FakeSupabase()
    yield f
    f.close()


def config(fake, **overrides):
    import cloud

    # Tests allow plain http to the local fake; real config must be https.
    base = {
        "url": fake.url,
        "secret_key": fake.secret,
        "business_id": BUSINESS,
        "deadline": 3.0,
        "attempt_timeout": 1.0,
        "backoffs": (0.05, 0.1),
    }
    return cloud.CloudConfig(**(base | overrides))


def test_saves_to_supabase_with_stable_id(fake):
    import cloud

    result = cloud.save_inquiry_cloud(config(fake), "room-1", **DETAILS)
    assert result["status"] == "saved"
    [row] = fake.rows
    assert row["business_id"] == BUSINESS
    assert row["source_id"] == result["inquiry_id"]
    assert row["phone"] == "+12015550100"
    assert row["name"] == "FICTIONAL Caller"
    # Retries must never overwrite an owner's corrections.
    assert "resolution=ignore-duplicates" in fake.last_prefer
    assert "merge-duplicates" not in fake.last_prefer


def test_same_request_twice_is_one_row(fake):
    import cloud

    first = cloud.save_inquiry_cloud(config(fake), "room-1", **DETAILS)
    second = cloud.save_inquiry_cloud(config(fake), "room-1", **DETAILS)
    assert first["status"] == second["status"] == "saved"
    assert first["inquiry_id"] == second["inquiry_id"]
    assert len(fake.rows) == 1


def test_owner_correction_survives_a_resend(fake):
    import cloud

    result = cloud.save_inquiry_cloud(config(fake), "room-1", **DETAILS)
    fake.rows[0]["name"] = "Corrected By Owner"
    again = cloud.save_inquiry_cloud(config(fake), "room-1", **DETAILS)
    assert again["status"] == "saved"
    assert fake.rows[0]["name"] == "Corrected By Owner"
    assert result["inquiry_id"] == again["inquiry_id"]


def test_retries_a_temporary_server_error(fake):
    import cloud

    fake.fail_with = [503, 502]
    result = cloud.save_inquiry_cloud(config(fake), "room-1", **DETAILS)
    assert result["status"] == "saved"
    assert len(fake.posts()) == 3
    assert len(fake.rows) == 1


def test_gives_up_honestly_when_server_keeps_failing(fake):
    import cloud

    fake.fail_with = [503] * 10
    result = cloud.save_inquiry_cloud(config(fake), "room-1", **DETAILS)
    assert result["status"] == "failed"
    assert "inquiry_id" not in result
    assert "Do not claim success" in result["message"]
    assert fake.rows == []


def test_gives_up_within_deadline_when_offline():
    import cloud

    cfg = cloud.CloudConfig(
        url="http://127.0.0.1:9",
        secret_key="x",
        business_id=BUSINESS,
        deadline=1.5,
        attempt_timeout=0.5,
        backoffs=(0.05, 0.1),
    )
    start = time.monotonic()
    result = cloud.save_inquiry_cloud(cfg, "room-1", **DETAILS)
    assert result["status"] == "failed"
    assert time.monotonic() - start < 3


def test_wrong_key_fails_without_retrying(fake):
    import cloud

    result = cloud.save_inquiry_cloud(
        config(fake, secret_key="wrong"), "room-1", **DETAILS
    )
    assert result["status"] == "failed"
    assert len(fake.posts()) == 1


def test_database_rule_rejection_asks_for_correction(fake):
    import cloud

    fake.fail_with = [400]
    result = cloud.save_inquiry_cloud(config(fake), "room-1", **DETAILS)
    assert result["status"] == "needs_correction"
    assert len(fake.posts()) == 1


def test_invalid_details_never_reach_supabase(fake):
    import cloud

    result = cloud.save_inquiry_cloud(
        config(fake), "room-1", **(DETAILS | {"phone": "123"})
    )
    assert result["status"] == "needs_correction"
    assert fake.requests == []


def test_config_from_env():
    import cloud

    assert cloud.CloudConfig.from_env({}) is None
    ok = {
        "SUPABASE_URL": "https://abc.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_x",
        "SUPABASE_BUSINESS_ID": BUSINESS,
    }
    assert cloud.CloudConfig.from_env(ok).business_id == BUSINESS
    with pytest.raises(ValueError, match="SUPABASE_SECRET_KEY"):
        cloud.CloudConfig.from_env(
            {k: v for k, v in ok.items() if k != "SUPABASE_SECRET_KEY"}
        )
    with pytest.raises(ValueError, match="https"):
        cloud.CloudConfig.from_env(ok | {"SUPABASE_URL": "http://abc.supabase.co"})
    with pytest.raises(ValueError, match="SUPABASE_BUSINESS_ID"):
        cloud.CloudConfig.from_env(ok | {"SUPABASE_BUSINESS_ID": "sample-studio"})


def test_secret_never_in_repr():
    import cloud

    cfg = cloud.CloudConfig(
        url="https://abc.supabase.co",
        secret_key="sb_secret_TOPSECRET",
        business_id=BUSINESS,
    )
    assert "TOPSECRET" not in repr(cfg)


# ---------- the agent's tool ----------


@pytest.mark.asyncio
async def test_tool_saves_to_cloud(fake, tmp_path):
    db = tmp_path / "local.db"
    agent = Assistant(storage_path=db, cloud=config(fake), session_id="room-1")
    result = await agent.save_booking_inquiry(**DETAILS, caller_confirmed=True)
    assert result["status"] == "saved"
    assert len(fake.rows) == 1
    # A local copy is kept too, with the same id.
    with sqlite3.connect(db) as conn:
        assert (
            conn.execute("select id from inquiries").fetchone()[0]
            == fake.rows[0]["source_id"]
        )


@pytest.mark.asyncio
async def test_tool_reports_failure_when_cloud_fails_even_with_local_copy(
    fake, tmp_path
):
    fake.fail_with = [503] * 10
    agent = Assistant(
        storage_path=tmp_path / "local.db", cloud=config(fake), session_id="room-1"
    )
    result = await agent.save_booking_inquiry(**DETAILS, caller_confirmed=True)
    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_tool_does_not_save_without_confirmation(fake):
    agent = Assistant(cloud=config(fake))
    result = await agent.save_booking_inquiry(**DETAILS, caller_confirmed=False)
    assert result["status"] == "needs_confirmation"
    assert fake.requests == []


# ---------- sending earlier local calls ----------


def local_db(tmp_path, rows):
    db = tmp_path / "inquiries.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE inquiries (id TEXT PRIMARY KEY, created_at TEXT, name TEXT, phone TEXT, service TEXT, preferred_time TEXT, duration TEXT, preferences TEXT)"
        )
        conn.executemany("INSERT INTO inquiries VALUES (?,?,?,?,?,?,?,?)", rows)
    return db


def test_backfill_sends_only_calls_supabase_does_not_have(fake, tmp_path):
    import cloud

    rows = [
        (
            "id-already",
            "2026-09-17T05:03:01.630402+00:00",
            "FICTIONAL A",
            "+15555550101",
            "recording",
            "t",
            "d",
            "",
        ),
        (
            "id-other-import",
            "2026-09-17T20:17:00.561108+00:00",
            "FICTIONAL B",
            "+15555550102",
            "recording",
            "t",
            "d",
            "",
        ),
        (
            "id-new",
            "2026-09-18T02:30:53.205606+00:00",
            "FICTIONAL C",
            "+15555550103",
            "recording",
            "t",
            "d",
            "",
        ),
    ]
    db = local_db(tmp_path, rows)
    # One already imported under the same id; one imported earlier under a different id.
    fake.rows = [
        {
            "business_id": BUSINESS,
            "source_id": "id-already",
            "created_at": rows[0][1],
            "phone": rows[0][3],
        },
        {
            "business_id": BUSINESS,
            "source_id": "manual-2",
            "created_at": "2026-09-17T20:17:00.561108Z",
            "phone": rows[1][3],
        },
    ]
    plan = cloud.compare(config(fake), db)
    assert [r["source_id"] for r in plan["missing"]] == ["id-new"]
    assert [r["source_id"] for r in plan["same_call_other_id"]] == ["id-other-import"]
    sent = cloud.backfill(config(fake), db)
    assert sent == 1
    assert {r["source_id"] for r in fake.rows} == {"id-already", "manual-2", "id-new"}
    assert cloud.backfill(config(fake), db) == 0


def test_config_business_id_comes_from_the_client_folder():
    import cloud

    env = {"SUPABASE_URL": "https://abc.supabase.co", "SUPABASE_SECRET_KEY": "k"}
    cfg = cloud.CloudConfig.from_env(env, business_id=BUSINESS)
    assert cfg.business_id == BUSINESS
    # The client folder wins over a stale env var, and the param alone enables nothing.
    cfg = cloud.CloudConfig.from_env(
        env | {"SUPABASE_BUSINESS_ID": "11111111-2222-3333-4444-555555555555"},
        business_id=BUSINESS,
    )
    assert cfg.business_id == BUSINESS
    assert cloud.CloudConfig.from_env({}, business_id=BUSINESS) is None


# ---------- attaching the transcript after the call ----------

TURNS = [
    {"speaker": "receptionist", "text": "Hi, this is Alex from FICTIONAL Studio."},
    {"speaker": "caller", "text": "I'd like two hours Friday."},
]


def test_transcript_attaches_to_the_saved_request(fake):
    import cloud

    saved = cloud.save_inquiry_cloud(config(fake), "room-1", **DETAILS)
    result = cloud.attach_transcript(config(fake), saved["inquiry_id"], TURNS)
    assert result["status"] == "attached"
    [row] = fake.rows
    assert row["transcript"]["turns"] == TURNS
    assert row["transcript"]["saved_at"]
    [(_, path, _, _)] = fake.patches()
    assert f"source_id=eq.{saved['inquiry_id']}" in path
    assert f"business_id=eq.{BUSINESS}" in path
    assert "transcript=is.null" in path  # fill only when empty; never rewrite


def test_transcript_is_never_rewritten_by_a_retry(fake):
    import cloud

    saved = cloud.save_inquiry_cloud(config(fake), "room-1", **DETAILS)
    cloud.attach_transcript(config(fake), saved["inquiry_id"], TURNS)
    cloud.attach_transcript(
        config(fake), saved["inquiry_id"], [{"speaker": "caller", "text": "other"}]
    )
    assert fake.rows[0]["transcript"]["turns"] == TURNS


def test_transcript_attach_retries_a_temporary_error(fake):
    import cloud

    saved = cloud.save_inquiry_cloud(config(fake), "room-1", **DETAILS)
    fake.fail_with = [503]
    result = cloud.attach_transcript(
        config(fake), saved["inquiry_id"], TURNS, deadline=3.0, backoffs=(0.05,)
    )
    assert result["status"] == "attached"
    assert len(fake.patches()) == 2


def test_transcript_attach_gives_up_within_deadline_when_offline():
    import cloud

    cfg = cloud.CloudConfig(
        url="http://127.0.0.1:9",
        secret_key="x",
        business_id=BUSINESS,
        attempt_timeout=0.5,
    )
    start = time.monotonic()
    result = cloud.attach_transcript(cfg, "abc", TURNS, deadline=1.0, backoffs=(0.05,))
    assert result["status"] == "failed"
    assert time.monotonic() - start < 3


def test_nothing_to_attach_is_skipped_without_a_request(fake):
    import cloud

    result = cloud.attach_transcript(config(fake), "abc", [])
    assert result["status"] == "skipped"
    assert fake.patches() == []


@pytest.mark.asyncio
async def test_tool_remembers_which_request_it_saved(fake):
    agent = Assistant(cloud=config(fake))
    assert agent.saved_inquiry_id is None
    refused = await agent.save_booking_inquiry(**DETAILS, caller_confirmed=False)
    assert refused["status"] == "needs_confirmation" and agent.saved_inquiry_id is None
    saved = await agent.save_booking_inquiry(**DETAILS, caller_confirmed=True)
    assert agent.saved_inquiry_id == saved["inquiry_id"]
