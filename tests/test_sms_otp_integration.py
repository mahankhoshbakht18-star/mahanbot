import json
import os
import time

import database
from database import DBHandler


def _create_applicant(nid: str) -> None:
    DBHandler.init_db()
    with DBHandler._connect() as conn:
        conn.execute(
            "INSERT INTO applicants (full_name, national_id, data) VALUES (?, ?, ?)",
            ("Test Applicant", nid, json.dumps({"national_id": nid})),
        )
        conn.commit()


def test_device_otp_requires_waiting_and_is_one_time(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "otp.db"))
    nid = "0012345678"
    _create_applicant(nid)

    now = time.time()
    assert DBHandler.store_device_otp_if_waiting(
        nid,
        "123456",
        received_at=now,
        device_id="android-test",
        message_id="msg-before-wait",
    ) == "not_waiting"

    assert DBHandler.mark_otp_waiting(nid, now - 1)
    assert DBHandler.get_otp_state(nid)["status"] == "waiting"
    assert DBHandler.store_device_otp_if_waiting(
        nid,
        "123456",
        received_at=now,
        device_id="android-test",
        message_id="msg-1",
    ) == "accepted"

    record = DBHandler.get_otp_record(nid)
    assert record == {
        "otp": "123456",
        "ts": now,
        "ts_ms": int(now * 1000),
        "status": "received",
    }
    assert DBHandler.store_device_otp_if_waiting(
        nid,
        "123456",
        received_at=now,
        device_id="android-test",
        message_id="msg-1",
    ) == "duplicate"

    assert DBHandler.mark_otp_consumed(nid)
    assert DBHandler.get_otp_record(nid) is None
    assert DBHandler.get_otp_state(nid)["status"] == "consumed"


def test_old_otp_is_rejected_for_new_window(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "freshness.db"))
    nid = "0098765432"
    _create_applicant(nid)
    now = time.time()
    assert DBHandler.mark_otp_waiting(nid, now)
    assert DBHandler.store_device_otp_if_waiting(
        nid,
        "654321",
        received_at=now - 30,
        device_id="android-test",
        message_id="old-message",
    ) == "stale"
