# Custody database format (v1)

This is the published contract between two independent projects:

- **Sample Tracker** writes databases in this format.
- **Genomic Forensics Toolkit** reads and verifies them, using its own implementation of this document and no code from the tracker.

Keeping the format in a document means either project can change internally without breaking the other, and anyone can write a third implementation. Both repositories carry an identical copy of this file and a shared test fixture (see [Interoperability fixture](#interoperability-fixture)).

## Container

A SQLite 3 file with two tables and two triggers.

```sql
CREATE TABLE samples (
    sample_id    TEXT PRIMARY KEY,
    subject_code TEXT NOT NULL,
    sample_type  TEXT NOT NULL,
    location     TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('Registered','In storage','Checked out','In analysis','Discarded')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE audit_log (
    seq        INTEGER PRIMARY KEY,
    ts         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    sample_id  TEXT NOT NULL,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL,
    prev_hash  TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
```

`samples` is the current inventory. `audit_log` is the append-only chain-of-custody history. The two triggers are defence in depth against mistakes and casual edits. They do not stop someone with write access to the file; the hash chain is what makes tampering evident.

## Entry hash

Each `audit_log` row carries `entry_hash`, defined as:

```
entry_hash = SHA-256( prev_hash | seq | ts | actor | sample_id | action | detail )
```

- The seven fields are converted to text, joined with the single character `|`, and encoded as UTF-8 before hashing. The result is lowercase hexadecimal.
- `seq` is written in decimal with no padding.
- `prev_hash` is the `entry_hash` of the row with `seq - 1`. For the first entry it is 64 zero characters (`"0" * 64`).
- `ts` is UTC in the form `YYYY-MM-DDTHH:MM:SSZ`.
- `actor` is a user ID (2-40 characters of letters, digits, `.`, `_`, `-`), not a full name.
- `detail` is the JSON text stored in the row, hashed exactly as stored. Writers produce compact, key-sorted JSON (`separators=(",", ":")`, `sort_keys=True`) so the same state always gives the same bytes. Verifiers must hash the stored text and must not re-serialise it.

`seq` starts at 1 and increases by 1 with no gaps.

### Worked example

Input fields for the first entry of a log:

| Field | Value |
|---|---|
| prev_hash | `0000000000000000000000000000000000000000000000000000000000000000` |
| seq | `1` |
| ts | `2026-09-02T09:00:00Z` |
| actor | `tech_01` |
| sample_id | `S-0457-T` |
| action | `register` |
| detail | `{"location":"Freezer A / Rack 2 / Box 4","note":"","sample_type":"Tissue","status":"Registered","subject_code":"S-0457"}` |

`entry_hash` = `ff21e344971ff776c784b87daa2e3908acffd762e331d1c70073851a52baba9d`

Both repositories test against this vector.

## Actions and `detail`

| `action` | Meaning |
|---|---|
| `register` | Sample added. `detail` has `status`, `location`, `subject_code`, `sample_type`, `note`. |
| `status_change` | Status changed. |
| `move` | Location changed. |
| `status_change+move` | Both changed in one step. |

For the three update actions, `detail` holds the state **after** the change (`status`, `location`, `subject_code`, `sample_type`, `note`) plus the state before it (`from_status`, `from_location`).

**Invariant used by verifiers:** for every sample, the `status` and `location` in the `detail` of its latest log entry must equal that sample's row in `samples`. A mismatch means the inventory was changed without a log entry.

## Verifier checks

A verifier following this format should check, in order:

1. **Schema:** both tables exist.
2. **Sequence:** `seq` is contiguous from 1.
3. **Links:** each `prev_hash` equals the previous row's `entry_hash` (zeros for the first).
4. **Hashes:** recompute every `entry_hash` from the row's stored fields.
5. **Time order:** `ts` does not go backwards.
6. **Inventory vs log:** the invariant above holds for every sample; every sample has history; every sample ID that appears in the log exists in the inventory.
7. **Triggers:** `audit_log_no_update` and `audit_log_no_delete` are present.

Record the head hash (the last `entry_hash`) outside the database. A full rewrite of the chain, or deletion of the last entries, produces a self-consistent file and is detectable only against that external value.

## Interoperability fixture

`tests/fixtures/tracker_fixture.db` is a small database **built by the real Sample Tracker application** (`scripts/make_fixture.py` in the tracker repository). `tests/fixtures/tracker_fixture.json` records its expected values:

```json
{"entries": 9, "samples": 4, "head_hash": "a6f08dd773bbe85c773fa76f6fdf8882e68421d299025a767bf4cc188b24a85a"}
```

- The tracker repository regenerates the fixture in its tests and checks that the head hash is unchanged. This catches any change to how the tracker writes the format.
- The toolkit repository verifies the committed copy with its independent verifier and checks it reports a clean chain with the same head hash. This catches any change to how the toolkit reads the format.

If you change the format, change this document, bump the version in the title, regenerate the fixture, and update both repositories.
