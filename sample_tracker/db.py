"""Sample Tracker data layer: SQLite inventory + hash-chained, append-only chain-of-custody log.

Hash spec (also implemented independently in genoforensics/custody.py so a tracker database
can be examined without trusting this code):

    entry_hash = SHA-256( prev_hash | seq | ts | actor | sample_id | action | detail )

joined with "|", where prev_hash is the previous entry's entry_hash (64 zeros for the first
entry) and detail is compact, key-sorted JSON describing the sample's state after the action.

Design choices:
- Samples are never deleted, only marked Discarded, so custody history stays complete.
- Subject codes must be de-identified codes (e.g. S-0457); free-text names are rejected.
- SQLite triggers block UPDATE/DELETE on the log as defence in depth. They stop mistakes and
  casual edits, not someone with write access to the file; the hash chain is what makes
  tampering *evident*.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from datetime import datetime, timezone

GENESIS = "0" * 64
SAMPLE_TYPES = ("Blood", "Tissue", "Plasma", "Serum", "DNA extract", "RNA extract", "Saliva")
STATUSES = ("Registered", "In storage", "Checked out", "In analysis", "Discarded")
TRANSITIONS = {
    "Registered": {"In storage", "Checked out", "Discarded"},
    "In storage": {"Checked out", "In analysis", "Discarded"},
    "Checked out": {"In storage", "In analysis", "Discarded"},
    "In analysis": {"In storage", "Checked out", "Discarded"},
    "Discarded": set(),
}
SAMPLE_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,31}$")
SUBJECT_RE = re.compile(r"^[A-Z]{1,3}-\d{3,6}$")
LOCATION_RE = re.compile(r"^[A-Za-z0-9 .:/#_-]{1,60}$")
ACTOR_RE = re.compile(r"^[A-Za-z0-9_.-]{2,40}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    sample_id    TEXT PRIMARY KEY,
    subject_code TEXT NOT NULL,
    sample_type  TEXT NOT NULL,
    location     TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('Registered','In storage','Checked out','In analysis','Discarded')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    seq        INTEGER PRIMARY KEY,
    ts         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    sample_id  TEXT NOT NULL,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL,
    prev_hash  TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""


class ValidationError(ValueError):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path=":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def entry_hash(prev_hash, seq, ts, actor, sample_id, action, detail) -> str:
    payload = "|".join([prev_hash, str(seq), ts, actor, sample_id, action, detail])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _clean(value, regex, label, hint):
    value = (value or "").strip()
    if not regex.match(value):
        raise ValidationError(f"{label} {hint}")
    return value


def _check_actor(actor):
    return _clean(actor, ACTOR_RE, "Actor", "must be 2-40 characters: letters, digits, '.', '_' or '-' (a user ID, not a full name).")


def _append_log(conn, ts, actor, sample_id, action, detail: dict):
    row = conn.execute("SELECT seq, entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    seq, prev = (row["seq"] + 1, row["entry_hash"]) if row else (1, GENESIS)
    d = json.dumps(detail, sort_keys=True, separators=(",", ":"))
    h = entry_hash(prev, seq, ts, actor, sample_id, action, d)
    conn.execute("INSERT INTO audit_log (seq, ts, actor, sample_id, action, detail, prev_hash, entry_hash) VALUES (?,?,?,?,?,?,?,?)",
                 (seq, ts, actor, sample_id, action, d, prev, h))
    return seq


def register_sample(conn, sample_id, subject_code, sample_type, location, actor, note="", now=None):
    sample_id = _clean(sample_id, SAMPLE_ID_RE, "Sample ID", "must be 3-32 characters: capital letters, digits, '-' or '_'.")
    subject_code = _clean(subject_code, SUBJECT_RE, "Subject code",
                          "must be a de-identified code like S-0457 (letters, dash, 3-6 digits). Do not enter names.")
    if sample_type not in SAMPLE_TYPES:
        raise ValidationError(f"Sample type must be one of: {', '.join(SAMPLE_TYPES)}.")
    location = _clean(location, LOCATION_RE, "Location", "must be 1-60 characters (letters, digits, spaces and . : / # _ -).")
    actor = _check_actor(actor)
    note = (note or "").strip()[:200]
    ts = now or utc_now_iso()
    if conn.execute("SELECT 1 FROM samples WHERE sample_id=?", (sample_id,)).fetchone():
        raise ValidationError(f"Sample {sample_id} is already registered.")
    with conn:
        conn.execute("INSERT INTO samples VALUES (?,?,?,?,?,?,?)",
                     (sample_id, subject_code, sample_type, location, "Registered", ts, ts))
        _append_log(conn, ts, actor, sample_id, "register",
                    {"status": "Registered", "location": location, "subject_code": subject_code, "sample_type": sample_type, "note": note})
    return sample_id


def update_sample(conn, sample_id, actor, status=None, location=None, note="", now=None):
    actor = _check_actor(actor)
    row = conn.execute("SELECT * FROM samples WHERE sample_id=?", (sample_id,)).fetchone()
    if not row:
        raise ValidationError(f"Sample {sample_id} not found.")
    new_status = status if status and status != row["status"] else None
    new_loc = None
    if location is not None and location.strip() and location.strip() != row["location"]:
        new_loc = _clean(location, LOCATION_RE, "Location", "must be 1-60 characters (letters, digits, spaces and . : / # _ -).")
    if new_status is None and new_loc is None:
        raise ValidationError("Nothing to change: give a new status and/or a new location.")
    if new_status is not None:
        if new_status not in STATUSES:
            raise ValidationError(f"Status must be one of: {', '.join(STATUSES)}.")
        if new_status not in TRANSITIONS[row["status"]]:
            allowed = ", ".join(sorted(TRANSITIONS[row["status"]])) or "none (terminal)"
            raise ValidationError(f"Cannot change {row['status']} -> {new_status}. Allowed: {allowed}.")
    elif row["status"] == "Discarded":
        raise ValidationError("Discarded samples cannot be changed.")
    ts = now or utc_now_iso()
    status_after, loc_after = new_status or row["status"], new_loc or row["location"]
    action = "+".join(a for a, v in (("status_change", new_status), ("move", new_loc)) if v)
    with conn:
        conn.execute("UPDATE samples SET status=?, location=?, updated_at=? WHERE sample_id=?", (status_after, loc_after, ts, sample_id))
        _append_log(conn, ts, actor, sample_id, action,
                    {"status": status_after, "location": loc_after, "subject_code": row["subject_code"], "sample_type": row["sample_type"],
                     "note": (note or "").strip()[:200], "from_status": row["status"], "from_location": row["location"]})
    return action


def list_samples(conn, status=None, search=""):
    q, args = "SELECT * FROM samples WHERE 1=1", []
    if status and status != "All":
        q += " AND status=?"
        args.append(status)
    if search:
        q += " AND (sample_id LIKE ? OR subject_code LIKE ? OR location LIKE ?)"
        args += [f"%{search}%"] * 3
    return [dict(r) for r in conn.execute(q + " ORDER BY sample_id", args)]


def get_log(conn, sample_id=None):
    if sample_id:
        rows = conn.execute("SELECT * FROM audit_log WHERE sample_id=? ORDER BY seq", (sample_id,))
    else:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY seq")
    return [dict(r) for r in rows]


def verify_integrity(conn) -> dict:
    """Walk the chain and compare the inventory with the last logged state of each sample."""
    problems, prev, last_state, prev_ts = [], GENESIS, {}, None
    rows = [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY seq")]
    expected_seq = 1
    for r in rows:
        if prev_ts is not None and r["ts"] < prev_ts:
            problems.append({"kind": "TIME_ORDER", "seq": r["seq"], "message": f"Entry {r['seq']} is dated {r['ts']}, earlier than the entry before it ({prev_ts}). "
                             "Possible back-dating, a clock that moved backwards, or entries inserted out of order."})
        prev_ts = r["ts"]
        if r["seq"] != expected_seq:
            problems.append({"kind": "SEQ_GAP", "seq": r["seq"], "message": f"Entry {r['seq']} found where {expected_seq} was expected (entries deleted or inserted)."})
            expected_seq = r["seq"]
        if r["prev_hash"] != prev:
            problems.append({"kind": "LINK_BROKEN", "seq": r["seq"], "message": f"Entry {r['seq']} does not link to the previous entry's hash."})
        calc = entry_hash(r["prev_hash"], r["seq"], r["ts"], r["actor"], r["sample_id"], r["action"], r["detail"])
        if calc != r["entry_hash"]:
            problems.append({"kind": "HASH_MISMATCH", "seq": r["seq"], "message": f"Entry {r['seq']} content does not match its stored hash (altered after it was written)."})
        prev, expected_seq = r["entry_hash"], expected_seq + 1
        try:
            last_state[r["sample_id"]] = json.loads(r["detail"])
        except ValueError:
            problems.append({"kind": "BAD_DETAIL", "seq": r["seq"], "message": f"Entry {r['seq']} detail is not valid JSON."})
    inv = {s["sample_id"]: s for s in conn.execute("SELECT * FROM samples")}
    for sid, s in inv.items():
        st = last_state.get(sid)
        if st is None:
            problems.append({"kind": "UNLOGGED_SAMPLE", "seq": None, "message": f"Sample {sid} is in the inventory but has no log entries."})
        elif (st.get("status"), st.get("location")) != (s["status"], s["location"]):
            problems.append({"kind": "STATE_MISMATCH", "seq": None,
                             "message": f"Sample {sid}: inventory shows '{s['status']}' at '{s['location']}' but the last logged state is '{st.get('status')}' at '{st.get('location')}' (changed without a log entry)."})
    for sid in last_state:
        if sid not in inv:
            problems.append({"kind": "ORPHAN_LOG", "seq": None, "message": f"Log refers to {sid}, which is not in the inventory."})
    return {"ok": not problems, "problems": problems, "entries": len(rows), "samples": len(inv), "head_hash": prev}


def export_audit_csv(conn) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["seq", "ts", "actor", "sample_id", "action", "detail", "prev_hash", "entry_hash"])
    for r in get_log(conn):
        w.writerow([r[k] for k in ("seq", "ts", "actor", "sample_id", "action", "detail", "prev_hash", "entry_hash")])
    return buf.getvalue()


def seed_demo(conn):
    """A handful of invented samples for trying the app."""
    t = "2026-09-%02dT%02d:%02d:00Z"
    reg = [("S-0457-T", "S-0457", "Tissue", "Freezer A / Rack 2 / Box 4"), ("S-0457-N", "S-0457", "Blood", "Freezer A / Rack 2 / Box 4"),
           ("S-0460-T", "S-0460", "Tissue", "Freezer A / Rack 3 / Box 1"), ("S-0460-N", "S-0460", "Blood", "Freezer A / Rack 3 / Box 1")]
    for i, (sid, subj, typ, loc) in enumerate(reg):          # register everything first (09:00-09:03) ...
        register_sample(conn, sid, subj, typ, loc, "demo_tech", now=t % (2, 9, i))
    for i, (sid, subj, typ, loc) in enumerate(reg):          # ... then move into storage (09:20-09:23)
        update_sample(conn, sid, "demo_tech", status="In storage", now=t % (2, 9, 20 + i))
    update_sample(conn, "S-0460-N", "demo_tech", status="Checked out", location="Sequencing core / bench 3", now=t % (10, 13, 5))
