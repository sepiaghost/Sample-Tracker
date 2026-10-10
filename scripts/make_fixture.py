"""Build the interoperability fixture with the real Sample Tracker code.

    python scripts/make_fixture.py            # writes tests/fixtures/tracker_fixture.{db,json}

The data come from ``seed_demo`` (fixed timestamps), so the head hash is deterministic.
The same two files are committed to the Genomic Forensics Toolkit repository, which verifies
them with its own independent implementation of docs/CUSTODY_DB_FORMAT.md.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from sample_tracker import db  # noqa: E402

OUT = os.path.join(ROOT, "tests", "fixtures")


def build(path):
    """Create a fixture database at ``path`` and return its expected values."""
    if os.path.exists(path):
        os.remove(path)
    conn = db.connect(path)
    db.seed_demo(conn)
    conn.commit()
    check = db.verify_integrity(conn)
    assert check["ok"], check
    conn.close()
    return {"entries": check["entries"], "samples": check["samples"], "head_hash": check["head_hash"]}


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    expected = build(os.path.join(OUT, "tracker_fixture.db"))
    with open(os.path.join(OUT, "tracker_fixture.json"), "w") as fh:
        json.dump(expected, fh, indent=2)
        fh.write("\n")
    print("wrote", OUT, expected)
