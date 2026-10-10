import json
import sqlite3

import pytest

from sample_tracker import db


@pytest.fixture
def conn():
    c = db.connect()
    yield c
    c.close()


def reg(conn, sid="S-0457-T", **kw):
    args = dict(subject_code="S-0457", sample_type="Tissue", location="Freezer A", actor="tech_01", now="2026-09-02T09:00:00Z")
    args.update(kw)
    return db.register_sample(conn, sid, args["subject_code"], args["sample_type"], args["location"], args["actor"], now=args["now"])


def test_register_and_log(conn):
    reg(conn)
    (s,) = db.list_samples(conn)
    assert s["status"] == "Registered" and s["subject_code"] == "S-0457"
    log = db.get_log(conn)
    assert len(log) == 1 and log[0]["prev_hash"] == db.GENESIS and log[0]["action"] == "register"
    assert db.verify_integrity(conn)["ok"]


@pytest.mark.parametrize("kw", [
    {"subject_code": "Jane Doe"}, {"subject_code": "0457"}, {"sample_type": "Hair"}, {"location": ""},
    {"location": "x'; DROP TABLE samples;--"}, {"actor": "Jane Doe"}, {"actor": ""},
])
def test_validation_rejects(conn, kw):
    with pytest.raises(db.ValidationError):
        reg(conn, **kw)
    assert db.list_samples(conn) == [] and db.get_log(conn) == []


def test_bad_sample_id_and_duplicates(conn):
    with pytest.raises(db.ValidationError):
        reg(conn, sid="s 1")
    reg(conn)
    with pytest.raises(db.ValidationError):
        reg(conn)


def test_sql_injection_is_inert(conn):
    reg(conn)
    assert db.list_samples(conn, search="'; DROP TABLE samples;--") == []
    assert len(db.list_samples(conn)) == 1


def test_status_transitions(conn):
    reg(conn)
    assert db.update_sample(conn, "S-0457-T", "tech_01", status="In storage") == "status_change"
    assert db.update_sample(conn, "S-0457-T", "tech_01", status="Checked out", location="Bench 3") == "status_change+move"
    with pytest.raises(db.ValidationError):
        db.update_sample(conn, "S-0457-T", "tech_01", status="Registered")
    with pytest.raises(db.ValidationError):
        db.update_sample(conn, "S-0457-T", "tech_01")  # nothing to change
    db.update_sample(conn, "S-0457-T", "tech_01", status="Discarded")
    with pytest.raises(db.ValidationError):
        db.update_sample(conn, "S-0457-T", "tech_01", status="In storage")
    with pytest.raises(db.ValidationError):
        db.update_sample(conn, "S-0457-T", "tech_01", location="Elsewhere")
    assert db.verify_integrity(conn)["ok"] and len(db.get_log(conn)) == 4


def test_chain_links(conn):
    reg(conn)
    db.update_sample(conn, "S-0457-T", "tech_01", status="In storage")
    a, b = db.get_log(conn)
    assert b["prev_hash"] == a["entry_hash"]


def test_triggers_block_edits_through_normal_access(conn):
    reg(conn)
    for sql in ("UPDATE audit_log SET actor='x'", "DELETE FROM audit_log"):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql)


def _no_triggers(conn):
    conn.execute("DROP TRIGGER audit_log_no_update")
    conn.execute("DROP TRIGGER audit_log_no_delete")


def test_detects_altered_entry(conn):
    reg(conn)
    db.update_sample(conn, "S-0457-T", "tech_01", status="In storage")
    _no_triggers(conn)
    conn.execute("UPDATE audit_log SET actor='tech_99' WHERE seq=2")
    r = db.verify_integrity(conn)
    assert not r["ok"] and [p["kind"] for p in r["problems"]] == ["HASH_MISMATCH"] and r["problems"][0]["seq"] == 2


def test_detects_deleted_entry(conn):
    reg(conn)
    db.update_sample(conn, "S-0457-T", "tech_01", status="In storage")
    db.update_sample(conn, "S-0457-T", "tech_01", status="Checked out")
    _no_triggers(conn)
    conn.execute("DELETE FROM audit_log WHERE seq=2")
    kinds = {p["kind"] for p in db.verify_integrity(conn)["problems"]}
    assert {"SEQ_GAP", "LINK_BROKEN"} <= kinds


def test_deleting_the_last_entry_is_caught_only_by_state_check(conn):
    # A truncated tail leaves a valid chain; only the inventory comparison (or an externally
    # recorded head hash) reveals it. This is why the head hash should be stored elsewhere.
    reg(conn)
    db.update_sample(conn, "S-0457-T", "tech_01", status="In storage")
    head_before = db.verify_integrity(conn)["head_hash"]
    _no_triggers(conn)
    conn.execute("DELETE FROM audit_log WHERE seq=2")
    r = db.verify_integrity(conn)
    assert [p["kind"] for p in r["problems"]] == ["STATE_MISMATCH"]
    assert r["head_hash"] != head_before


def test_detects_forged_link(conn):
    reg(conn)
    db.update_sample(conn, "S-0457-T", "tech_01", status="In storage")
    _no_triggers(conn)
    conn.execute("UPDATE audit_log SET prev_hash=? WHERE seq=2", ("f" * 64,))
    kinds = {p["kind"] for p in db.verify_integrity(conn)["problems"]}
    assert {"LINK_BROKEN", "HASH_MISMATCH"} <= kinds


def test_detects_direct_inventory_edit(conn):
    reg(conn)
    conn.execute("UPDATE samples SET location='Somewhere else' WHERE sample_id='S-0457-T'")
    r = db.verify_integrity(conn)
    assert [p["kind"] for p in r["problems"]] == ["STATE_MISMATCH"]


def test_detects_unlogged_sample_and_orphan_log(conn):
    reg(conn)
    conn.execute("INSERT INTO samples VALUES ('X-001','S-0001','Blood','Box','In storage','t','t')")
    conn.execute("DELETE FROM samples WHERE sample_id='S-0457-T'")
    kinds = {p["kind"] for p in db.verify_integrity(conn)["problems"]}
    assert {"UNLOGGED_SAMPLE", "ORPHAN_LOG"} <= kinds


def test_export_csv(conn):
    reg(conn)
    out = db.export_audit_csv(conn)
    assert out.splitlines()[0].startswith("seq,ts,actor") and "S-0457-T" in out


def test_seed_demo_is_valid(conn):
    db.seed_demo(conn)
    r = db.verify_integrity(conn)
    assert r["ok"] and r["samples"] == 4 and json.loads(db.get_log(conn)[0]["detail"])["status"] == "Registered"


def test_detects_timestamps_going_backwards(conn):
    reg(conn, sid="S-0001-A", subject_code="S-0001", now="2026-09-02T10:00:00Z")
    reg(conn, sid="S-0002-A", subject_code="S-0002", now="2026-09-02T09:00:00Z")      # earlier than the entry before it
    r = db.verify_integrity(conn)
    assert [p["kind"] for p in r["problems"]] == ["TIME_ORDER"] and r["problems"][0]["seq"] == 2


def test_seed_demo_is_chronological(conn):
    db.seed_demo(conn)
    stamps = [r["ts"] for r in db.get_log(conn)]
    assert stamps == sorted(stamps)
