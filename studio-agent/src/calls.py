"""Business-scoped call history and private recording uploads, independent of requests.

The SDK finalizes its recording before on_session_end and deletes it afterward.
Keep uploads inside that callback; never send credentials or local paths to the portal.
"""

import asyncio
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cloud import RETRYABLE, CloudConfig, _RejectedError, _request

logger = logging.getLogger("agent.calls")


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def _attempt(cfg, operation):
    """Bound both attempts and socket waits; log only sanitized error categories."""
    stop = time.monotonic() + cfg.deadline
    for attempt in range(len(cfg.backoffs) + 1):
        try:
            operation(max(0.1, min(cfg.attempt_timeout, stop - time.monotonic())))
            return True
        except _RejectedError as exc:
            if exc.status not in RETRYABLE:
                logger.warning(
                    "Call history operation refused (HTTP %s, code %s)",
                    exc.status,
                    exc.code,
                )
                return False
            reason = f"HTTP {exc.status}"
        except (OSError, ValueError) as exc:
            reason = type(exc).__name__
        if (
            attempt == len(cfg.backoffs)
            or time.monotonic() + cfg.backoffs[attempt] >= stop
        ):
            logger.warning(
                "Call history operation failed after %d attempts (%s)",
                attempt + 1,
                reason,
            )
            return False
        time.sleep(cfg.backoffs[attempt])
    return False


def _upload(cfg, key, path, timeout):
    # Stream the file instead of reading an entire long call into RAM.
    with path.open("rb") as audio:
        size = path.stat().st_size
        if not size:
            raise ValueError("Empty recording")
        headers = cfg.headers() | {
            "Content-Type": "audio/ogg",
            "Content-Length": str(size),
            "x-upsert": "true",
        }
        request = urllib.request.Request(
            cfg.url + "/storage/v1/object/call-recordings/" + key,
            data=audio,
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            # Never log response content, which may include sensitive input.
            raise _RejectedError(exc.code, None) from None


class CallHistory:
    def __init__(
        self,
        cfg: CloudConfig,
        session_id: str,
        *,
        mode: str,
        started_at: float | None = None,
        retention_days: int | None = None,
    ):
        if mode not in {"demo", "pilot"}:
            raise ValueError("Unknown call mode")
        self.cfg = cfg
        self.session_id = session_id
        self.mode = mode
        self.started_at = time.time() if started_at is None else started_at
        self.retention_days = retention_days
        self.call_id = str(uuid.uuid5(uuid.UUID(cfg.business_id), session_id))
        self.durable = False
        self.inquiry_ids: set[str] = set()
        self._start_task: asyncio.Task | None = None

    def begin(self):
        row = {
            "id": self.call_id,
            "business_id": self.cfg.business_id,
            "session_id": self.session_id,
            "mode": self.mode,
            "started_at": _iso(self.started_at),
            "recording_status": "pending",
        }
        self.durable = _attempt(
            self.cfg,
            lambda timeout: _request(
                self.cfg,
                "POST",
                "/rest/v1/calls?on_conflict=business_id,session_id",
                row,
                prefer="resolution=ignore-duplicates,return=minimal",
                timeout=timeout,
            ),
        )
        return self.durable

    def start(self):
        # Scheduling is synchronous: the greeting never waits for the database.
        if self._start_task is None:
            self._start_task = asyncio.create_task(asyncio.to_thread(self.begin))

    async def ready_call_id(self):
        self.start()
        # A cancelled tool must not cancel the shared durable start operation.
        await asyncio.shield(self._start_task)
        return self.call_id if self.durable else None

    async def finish(self, report):
        await self.ready_call_id()
        return await asyncio.to_thread(self.finalize, report)

    def finalize(self, report):
        if not self.durable and not self.begin():
            return False
        ended = report.timestamp
        started = (
            report.started_at if report.started_at is not None else self.started_at
        )
        duration = (
            report.duration if report.duration is not None else max(0, ended - started)
        )
        body = {
            "started_at": _iso(started),
            "ended_at": _iso(ended),
            "duration_seconds": max(0, duration),
            "room_id": report.room_id or None,
        }
        key = f"{self.cfg.business_id}/{self.call_id}/audio.ogg"
        # Upload waits are longer, after hangup; capped well below SDK callback timeout.
        upload_cfg = replace(self.cfg, deadline=90.0, attempt_timeout=25.0)
        available = bool(report.audio_recording_path) and _attempt(
            upload_cfg,
            lambda timeout: _upload(
                upload_cfg, key, Path(report.audio_recording_path), timeout
            ),
        )
        body.update(
            recording_status="available" if available else "unavailable",
            recording_path=key if available else None,
            recording_expires_at=(
                datetime.fromtimestamp(ended, UTC) + timedelta(days=self.retention_days)
            ).isoformat()
            if available and self.retention_days
            else None,
        )
        path = (
            f"/rest/v1/calls?business_id=eq.{self.cfg.business_id}&id=eq.{self.call_id}"
        )
        # A later failed retry must not downgrade a previously uploaded recording.
        if not available:
            path += "&recording_status=neq.available"
        saved = _attempt(
            self.cfg,
            lambda timeout: _request(
                self.cfg,
                "PATCH",
                path,
                body,
                prefer="return=minimal",
                timeout=timeout,
            ),
        )
        for inquiry_id in self.inquiry_ids:
            link_path = (
                f"/rest/v1/inquiries?business_id=eq.{self.cfg.business_id}"
                f"&source_id=eq.{urllib.parse.quote(inquiry_id, safe='')}&call_id=is.null"
            )
            _attempt(
                self.cfg,
                lambda timeout, path=link_path: _request(
                    self.cfg,
                    "PATCH",
                    path,
                    {"call_id": self.call_id},
                    prefer="return=minimal",
                    timeout=timeout,
                ),
            )
        return saved


def start_call_history(ctx, cfg: CloudConfig | None, *, mode: str):
    if cfg is None:
        return None
    value = os.getenv("CALL_RECORDING_RETENTION_DAYS", "").strip()
    try:
        days = int(value) if value else None
        if days is not None and days <= 0:
            raise ValueError("Retention must be positive")
        history = CallHistory(cfg, ctx.room.name, mode=mode, retention_days=days)
        ctx.sintax_call_history = history
        history.start()
        return history
    except (ValueError, TypeError):
        logger.warning(
            "Call history not configured: check business identity and CALL_RECORDING_RETENTION_DAYS"
        )
        return None


async def finish_call_history(ctx):
    history = getattr(ctx, "sintax_call_history", None)
    if history is None:
        return
    try:
        saved = await history.finish(ctx.make_session_report())
        logger.info("Call history finalization %s", "saved" if saved else "failed")
    except Exception as exc:
        # Do not log arbitrary exception messages, report data, or credentials.
        logger.warning("Call history finalization failed (%s)", type(exc).__name__)
