"""Save confirmed inquiries straight to Supabase, where the client portal reads them.

Runs the same way on a developer's computer (demo) and on LiveKit Cloud (production).
Configuration comes from environment variables, never from code:

    SUPABASE_URL          https://<project>.supabase.co
    SUPABASE_SECRET_KEY   server-only secret key; never put it in client-portal/public/
    SUPABASE_BUSINESS_ID  which client business these calls belong to

Retries use the same id for the same call, and Supabase is told to ignore
duplicates, so a retry never creates a second request and never overwrites an
owner's corrections, status or notes.

Command line (reads studio-agent/.env.local and the local SQLite demo file):
    uv run python src/cloud.py check      # read-only: which local calls are missing online
    uv run python src/cloud.py backfill   # send only the missing ones
"""

import json
import logging
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from inquiries import clean_inquiry

logger = logging.getLogger("agent.cloud")

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}
FAILED = {
    "status": "failed",
    "message": "Could not verify saving. Do not claim success. Offer the studio contact.",
}


@dataclass(frozen=True)
class CloudConfig:
    url: str
    secret_key: str = field(repr=False)
    business_id: str
    deadline: float = 8.0  # the caller is waiting; give up honestly after this
    attempt_timeout: float = 4.0
    backoffs: tuple = (0.5, 1.0)

    @classmethod
    def from_env(
        cls, env=os.environ, business_id: str | None = None
    ) -> "CloudConfig | None":
        names = ("SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_BUSINESS_ID")
        values = {n: (env.get(n) or "").strip() for n in names}
        if not any(values.values()):
            return (
                None  # cloud saving not configured; the param alone doesn't enable it
            )
        if business_id:
            # The client folder's business_id wins over the legacy env var.
            values["SUPABASE_BUSINESS_ID"] = business_id.strip()
        missing = [n for n, v in values.items() if not v]
        if missing:
            raise ValueError(
                f"Cloud saving is half-configured; missing: {', '.join(missing)}"
            )
        if not values["SUPABASE_URL"].startswith("https://"):
            raise ValueError("SUPABASE_URL must start with https://")
        if not UUID.match(values["SUPABASE_BUSINESS_ID"].lower()):
            raise ValueError("SUPABASE_BUSINESS_ID must be the business's UUID")
        return cls(
            values["SUPABASE_URL"].rstrip("/"),
            values["SUPABASE_SECRET_KEY"],
            values["SUPABASE_BUSINESS_ID"].lower(),
        )

    def headers(self) -> dict:
        h = {"apikey": self.secret_key, "Content-Type": "application/json"}
        if self.secret_key.startswith("eyJ"):  # legacy service_role JWT
            h["Authorization"] = f"Bearer {self.secret_key}"
        return h


class _RejectedError(Exception):
    def __init__(self, status: int, code: str | None):
        super().__init__(f"HTTP {status} {code or ''}".strip())
        self.status, self.code = status, code


def _request(
    cfg: CloudConfig,
    method: str,
    path: str,
    body=None,
    prefer: str = "",
    timeout: float = 4.0,
):
    headers = cfg.headers() | ({"Prefer": prefer} if prefer else {})
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        cfg.url + path, data=data, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            text = res.read().decode() or "null"
            return json.loads(text)
    except urllib.error.HTTPError as e:
        try:
            code = json.loads(e.read().decode() or "{}").get("code")
        except ValueError:
            code = None
        raise _RejectedError(e.code, code) from None


def _insert(cfg: CloudConfig, row: dict) -> dict:
    """Insert one row, retrying temporary failures until the deadline."""
    stop = time.monotonic() + cfg.deadline
    waits = list(cfg.backoffs)
    attempt = 0
    while True:
        attempt += 1
        remaining = stop - time.monotonic()
        try:
            _request(
                cfg,
                "POST",
                "/rest/v1/inquiries?on_conflict=business_id,source_id",
                row,
                prefer="resolution=ignore-duplicates,return=minimal",
                timeout=max(0.1, min(cfg.attempt_timeout, remaining)),
            )
            return {"status": "saved"}
        except _RejectedError as e:
            if e.status == 400 and e.code == "23514":
                return {
                    "status": "needs_correction",
                    "field": "details",
                    "message": "The studio's system rejected the format. Re-check the details with the caller.",
                }
            if e.status not in RETRYABLE:
                logger.error(
                    "Supabase refused the inquiry (%s); check SUPABASE_* settings", e
                )
                return dict(FAILED)
            reason = str(e)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            reason = type(e).__name__
        wait = waits.pop(0) if waits else None
        if wait is None or time.monotonic() + wait >= stop:
            logger.error(
                "Could not save inquiry to Supabase after %d attempts (%s)",
                attempt,
                reason,
            )
            return dict(FAILED)
        time.sleep(wait)


def save_inquiry_cloud(
    cfg: CloudConfig,
    session_id: str,
    *,
    created_at: str | None = None,
    call_id: str | None = None,
    **details,
) -> dict:
    cleaned = clean_inquiry(session_id, **details)
    if isinstance(cleaned, dict):
        return cleaned
    fields, inquiry_id = cleaned
    row = {
        "business_id": cfg.business_id,
        "source_id": inquiry_id,
        "created_at": created_at or datetime.now(UTC).isoformat(),
        **fields,
    }
    if call_id is not None:
        row["call_id"] = call_id
    result = _insert(cfg, row)
    if result["status"] != "saved":
        return result
    return {
        "status": "saved",
        "inquiry_id": inquiry_id,
        "message": "Saved for the studio to review. Not a confirmed booking; no owner notification sent.",
    }


def attach_transcript(
    cfg: CloudConfig,
    inquiry_id: str,
    turns: list,
    *,
    deadline: float = 20.0,
    backoffs: tuple = (1.0, 2.0, 4.0),
) -> dict:
    """Attach the call's spoken turns to the inquiry saved during the call.

    Runs after the caller has hung up, so it may wait longer than a save.
    Fills only an empty transcript (the database also refuses rewrites), so a
    retry can never replace what was attached the first time.
    """
    if not turns:
        return {"status": "skipped", "message": "No spoken turns to attach."}
    body = {"transcript": {"turns": turns, "saved_at": datetime.now(UTC).isoformat()}}
    path = (
        "/rest/v1/inquiries"
        f"?business_id=eq.{cfg.business_id}"
        f"&source_id=eq.{urllib.parse.quote(inquiry_id, safe='')}"
        "&transcript=is.null"
    )
    stop = time.monotonic() + deadline
    waits = list(backoffs)
    attempt = 0
    while True:
        attempt += 1
        remaining = stop - time.monotonic()
        try:
            _request(
                cfg,
                "PATCH",
                path,
                body,
                prefer="return=minimal",
                timeout=max(0.1, min(cfg.attempt_timeout, remaining)),
            )
            return {"status": "attached", "inquiry_id": inquiry_id}
        except _RejectedError as e:
            if e.status not in RETRYABLE:
                logger.error("Supabase refused the transcript (%s)", e)
                return {"status": "failed", "message": str(e)}
            reason = str(e)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            reason = type(e).__name__
        wait = waits.pop(0) if waits else None
        if wait is None or time.monotonic() + wait >= stop:
            logger.error(
                "Could not attach transcript after %d attempts (%s)", attempt, reason
            )
            return {"status": "failed", "message": reason}
        time.sleep(wait)


# ---------- earlier calls saved only on this Mac ----------


def _local_rows(db: Path) -> list[dict]:
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(r) for r in conn.execute("SELECT * FROM inquiries ORDER BY created_at")
        ]


def _moment(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def compare(cfg: CloudConfig, db: Path) -> dict:
    """Read-only. Sort local calls into: already online, same call under another id, missing."""
    online = _request(
        cfg,
        "GET",
        f"/rest/v1/inquiries?business_id=eq.{cfg.business_id}&select=source_id,created_at,phone",
        timeout=cfg.attempt_timeout,
    )
    ids = {r["source_id"] for r in online}
    moments = {(_moment(r["created_at"]), r["phone"]) for r in online}
    plan = {"online": [], "same_call_other_id": [], "missing": []}
    for r in _local_rows(db):
        local = {
            "source_id": r["id"],
            **{
                k: r[k]
                for k in (
                    "created_at",
                    "name",
                    "phone",
                    "service",
                    "preferred_time",
                    "duration",
                    "preferences",
                )
            },
        }
        if r["id"] in ids:
            plan["online"].append(local)
        elif (_moment(r["created_at"]), r["phone"]) in moments:
            plan["same_call_other_id"].append(local)
        else:
            plan["missing"].append(local)
    return plan


def backfill(cfg: CloudConfig, db: Path) -> int:
    """Send only the local calls Supabase doesn't already have. Returns how many were sent."""
    sent = 0
    for row in compare(cfg, db)["missing"]:
        if _insert(cfg, {"business_id": cfg.business_id, **row})["status"] != "saved":
            raise RuntimeError(
                f"Could not send the call from {row['created_at']}; nothing after it was sent"
            )
        sent += 1
    return sent


def main(argv: list[str]) -> int:
    from dotenv import load_dotenv

    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env.local")
    cfg = CloudConfig.from_env()
    if cfg is None:
        print(
            "Cloud saving is not configured: add SUPABASE_URL, SUPABASE_SECRET_KEY and SUPABASE_BUSINESS_ID to .env.local"
        )
        return 1
    db = Path(os.getenv("INQUIRY_DB_PATH") or root / "data" / "inquiries.db")
    action = argv[1] if len(argv) > 1 else "check"
    plan = compare(cfg, db)
    print(f"Local calls: {sum(len(v) for v in plan.values())}")
    print(f"  already online:                    {len(plan['online'])}")
    print(f"  online under a different id (skip): {len(plan['same_call_other_id'])}")
    print(f"  missing online:                    {len(plan['missing'])}")
    for r in plan["missing"]:
        print(f"    - {r['created_at'][:16]}  {r['name']}  ({r['service']})")
    if action == "backfill" and plan["missing"]:
        print(f"Sent {backfill(cfg, db)} call(s) to Supabase.")
    elif action == "backfill":
        print("Nothing to send.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main(sys.argv))
