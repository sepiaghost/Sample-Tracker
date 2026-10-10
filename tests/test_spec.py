"""Conformance of the tracker's output to docs/CUSTODY_DB_FORMAT.md and the shared fixture."""
import hashlib
import json
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from sample_tracker import db  # noqa: E402
from make_fixture import build  # noqa: E402


def spec_hash(prev, seq, ts, actor, sample_id, action, detail):
    """The hash exactly as written in the spec, independent of sample_tracker.db."""
    return hashlib.sha256("|".join([prev, str(seq), ts, actor, sample_id, action, detail]).encode("utf-8")).hexdigest()


def expected():
    with open(os.path.join(FIXTURES, "tracker_fixture.json")) as fh:
        return json.load(fh)


def test_worked_example_from_the_spec():
    detail = '{"location":"Freezer A / Rack 2 / Box 4","note":"","sample_type":"Tissue","status":"Registered","subject_code":"S-0457"}'
    want = "ff21e344971ff776c784b87daa2e3908acffd762e331d1c70073851a52baba9d"
    assert spec_hash("0" * 64, 1, "2026-09-02T09:00:00Z", "tech_01", "S-0457-T", "register", detail) == want
    assert db.entry_hash("0" * 64, 1, "2026-09-02T09:00:00Z", "tech_01", "S-0457-T", "register", detail) == want


def test_spec_document_contains_the_worked_example_and_fixture_hash():
    text = open(os.path.join(ROOT, "docs", "CUSTODY_DB_FORMAT.md")).read()
    assert "ff21e344971ff776c784b87daa2e3908acffd762e331d1c70073851a52baba9d" in text
    assert expected()["head_hash"] in text


def test_committed_fixture_matches_the_spec():
    conn = sqlite3.connect(os.path.join(FIXTURES, "tracker_fixture.db"))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY seq")]
    triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    conn.close()
    prev = "0" * 64
    for i, r in enumerate(rows, 1):
        assert r["seq"] == i and r["prev_hash"] == prev
        assert spec_hash(r["prev_hash"], r["seq"], r["ts"], r["actor"], r["sample_id"], r["action"], r["detail"]) == r["entry_hash"]
        assert r["detail"] == json.dumps(json.loads(r["detail"]), sort_keys=True, separators=(",", ":"))   # compact, key-sorted
        prev = r["entry_hash"]
    assert triggers == {"audit_log_no_update", "audit_log_no_delete"}
    assert (len(rows), prev) == (expected()["entries"], expected()["head_hash"])


def test_tracker_still_reproduces_the_fixture(tmp_path):
    """If this fails, the tracker changed how it writes the format: update the spec, regenerate the fixture, and update the toolkit repo."""
    got = build(str(tmp_path / "f.db"))
    assert got == expected()


def test_schema_columns_match_the_spec(tmp_path):
    conn = db.connect(str(tmp_path / "s.db"))
    cols = lambda t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
    assert cols("samples") == ["sample_id", "subject_code", "sample_type", "location", "status", "created_at", "updated_at"]
    assert cols("audit_log") == ["seq", "ts", "actor", "sample_id", "action", "detail", "prev_hash", "entry_hash"]
