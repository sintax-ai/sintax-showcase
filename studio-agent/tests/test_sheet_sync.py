"""Check retries and sheet edits without sending customer data to Google."""

import sqlite3

import pytest

from inquiries import save_inquiry
from sheet_sync import HEADERS, sync_once


class FakeSheet:
    def __init__(self):
        self.rows = [HEADERS.copy()]
        self.fail_after_write = False

    def read_rows(self):
        return self.rows

    def append_rows(self, rows):
        self.rows.extend(rows)
        if self.fail_after_write:
            raise TimeoutError("Response lost after Google accepted rows")


def inquiry(db):
    return save_inquiry(
        db,
        "test-call",
        name="Demo Caller",
        phone="2015550100",
        service="recording",
        preferred_time="September 25 at 3 PM",
        duration="2 hours",
        preferences="Studio A",
    )


def test_sync_preserves_status_and_does_not_duplicate(tmp_path):
    db = tmp_path / "test.db"
    receipt = inquiry(db)
    sheet = FakeSheet()
    assert sync_once(db, sheet) == 1
    assert sheet.rows[1][0] == receipt["inquiry_id"]
    assert sheet.rows[1][2:4] == ["Demo Caller", "+12015550100"]
    sheet.rows[1][8:] = ["Contacted", "Jordan called back"]
    assert sync_once(db, sheet) == 0
    assert sheet.rows[1][8:] == ["Contacted", "Jordan called back"]


def test_retry_after_ambiguous_write_does_not_duplicate(tmp_path):
    db = tmp_path / "test.db"
    inquiry(db)
    sheet = FakeSheet()
    sheet.fail_after_write = True
    with pytest.raises(TimeoutError):
        sync_once(db, sheet)
    sheet.fail_after_write = False
    assert sync_once(db, sheet) == 0
    assert len(sheet.rows) == 2
    with sqlite3.connect(db) as conn:
        assert conn.execute("select count(*) from inquiries").fetchone()[0] == 1


def test_wrong_sheet_headers_fail_before_writing(tmp_path):
    db = tmp_path / "test.db"
    inquiry(db)
    sheet = FakeSheet()
    sheet.rows[0] = ["Unrelated sheet"]
    with pytest.raises(ValueError, match="headers"):
        sync_once(db, sheet)
    assert len(sheet.rows) == 1
