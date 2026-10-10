# Sample Tracker

A small laboratory sample tracker with a **hash-chained, append-only chain-of-custody log**. Streamlit + SQLite; the data layer uses only the Python standard library.

It is a standalone project. Its database format is published in [`docs/CUSTODY_DB_FORMAT.md`](docs/CUSTODY_DB_FORMAT.md), so other tools can verify its databases without using this code. One such tool is the companion [Genomic Forensics Toolkit](https://github.com/sepiaghost/Genomic-Forensics-Toolkit), which examines tracker databases as digital evidence. Neither project imports the other.

> The demo data are invented. Use de-identified subject codes only (for example `S-0457`); the app rejects free-text names.

## Quick start

```bash
pip install -r requirements-dev.txt
streamlit run sample_tracker/app.py        # SAMPLE_TRACKER_DB=path.db to choose the database file
pytest -q                                   # 34 tests
```

Python 3.9+.

## What it does

Four tabs: **Inventory** (filter and search), **Register**, **Update**, and **Audit log** (verify integrity, export CSV).

Design choices that matter for custody:

- Samples are never deleted, only set to **Discarded**, so history stays complete. Status changes follow an allowed-transition table.
- Subject codes, sample IDs, locations and user IDs are validated. A user ID is not a full name.
- Every change appends a log entry whose hash covers the previous entry's hash: `SHA-256(prev_hash | seq | ts | actor | sample_id | action | detail)`.
- SQLite triggers block UPDATE and DELETE on the log. They stop mistakes and casual edits, not someone with write access to the file; the hash chain is what makes tampering *evident*.
- "Verify integrity" walks the chain and compares the inventory with the last logged state of each sample. Record the head hash somewhere outside the database: a tail deletion or a full chain rewrite is invisible without it.

## Interoperability

`docs/CUSTODY_DB_FORMAT.md` is the contract. `tests/fixtures/tracker_fixture.db` is a small database built by this application (`python scripts/make_fixture.py`), with its expected values in `tracker_fixture.json`. The tests check that:

- the fixture follows the spec when recomputed independently of `sample_tracker/db.py`,
- the worked hash example in the spec reproduces,
- the tracker still generates a fixture with the same head hash.

The toolkit repository carries the same two fixture files and verifies them with its own implementation. If you change the format, change the spec, regenerate the fixture, and update both repositories.

## Layout

```
sample_tracker/
  db.py        SQLite inventory + hash-chained log, validation, integrity check
  app.py       Streamlit UI
docs/CUSTODY_DB_FORMAT.md    the published format
scripts/make_fixture.py      builds the shared fixture with the real app
tests/                       validation, tamper detection, Streamlit UI, spec conformance
```

## Limits

- A hash chain proves entries are consistent with each other, not that what was entered was true.
- A full chain rewrite, or removal of the last entries, is detectable only against a head hash stored elsewhere.
- Anyone with write access to the file can drop the triggers; they are a safety net, not access control.
- There is no login. The "actor" is whatever user ID is typed in, so the log records who the operator *said* they were. A production system needs authentication in front of it.
