"""Copy local inquiries to a private Sheet; run one sync worker per Sheet.

Independent of the voice worker: Google outages never delay saving or speech.
Rows are append-only. Owner status/notes remain in Sheets, not SQLite.
"""

import argparse
import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

HEADERS = [
    "Request ID",
    "Received (New York)",
    "Customer",
    "Phone",
    "Service",
    "Requested time",
    "Duration",
    "Details",
    "Status",
    "Owner notes",
]
SERVICE_LABELS = {
    "recording": "Recording with engineer",
    "music_rental": "Recording room rental",
    "mixing": "Mixing",
    "mastering": "Mastering",
    "photo_rental": "Photo studio rental",
    "photoshoot": "Photography session",
    "recording_light_mix": "Recording + light mix",
}


def sync_once(db_path, sheet):
    """Re-read remote IDs each attempt, including after an uncertain append result."""
    remote = sheet.read_rows()
    if not remote or remote[0] != HEADERS:
        raise ValueError("Sheet headers do not match the inquiry template")
    known_ids = {row[0] for row in remote[1:] if row}
    # Read-only mode prevents accidentally creating a database at a mistyped path.
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        records = db.execute(
            "SELECT id, created_at, name, phone, service, preferred_time, duration, preferences FROM inquiries ORDER BY created_at, id"
        ).fetchall()
    rows = []
    for record in records:
        key, created, name, phone, service, requested, duration, details = record
        if key in known_ids:
            continue
        received = (
            datetime.fromisoformat(created)
            .astimezone(ZoneInfo("America/New_York"))
            .strftime("%Y-%m-%d %I:%M %p")
        )
        rows.append(
            [
                key,
                received,
                name,
                phone,
                SERVICE_LABELS.get(service, service),
                requested,
                duration,
                details,
                "New",
                "",
            ]
        )
    if rows:
        sheet.append_rows(rows)
    return len(rows)


class GoogleSheet:
    def __init__(self, spreadsheet_id, tab="Inquiries"):
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        self.http = AuthorizedSession(credentials)
        self.base = (
            "https://sheets.googleapis.com/v4/spreadsheets/"
            + quote(spreadsheet_id, safe="")
            + "/values/"
        )
        self.range = quote("'" + tab.replace("'", "''") + "'!A:J", safe="")

    def read_rows(self):
        response = self.http.get(self.base + self.range, timeout=20)
        response.raise_for_status()
        return response.json().get("values", [])

    def append_rows(self, rows):
        # RAW prevents caller text being interpreted as a spreadsheet formula.
        response = self.http.post(
            self.base + self.range + ":append",
            params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            json={"values": rows},
            timeout=20,
        )
        response.raise_for_status()


def main():
    load_dotenv(".env.local")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    db_path = Path(os.environ["INQUIRY_DB_PATH"])
    sheet = GoogleSheet(os.environ["GOOGLE_SHEET_ID"])
    # A local process lock avoids two workers racing to append the same request.
    import fcntl

    with open(str(db_path) + ".sheets.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                count = sync_once(db_path, sheet)
                logging.info("Sheet sync complete: %s new inquiries", count)
            except Exception as error:
                # Do not log request bodies, names, credentials, or phone numbers.
                logging.error(
                    "Sheet sync failed (%s); local inquiries retained",
                    type(error).__name__,
                )
                if args.once:
                    raise SystemExit(1) from None
            if args.once:
                break
            time.sleep(30)


if __name__ == "__main__":
    main()
