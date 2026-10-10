"""Sample Tracker: Streamlit interface.  Run:  streamlit run sample_tracker/app.py

Set SAMPLE_TRACKER_DB to choose the database file (default: sample_tracker.db).
"""
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sample_tracker import db  # noqa: E402

st.set_page_config(page_title="Sample Tracker", page_icon="🧪", layout="wide")
DB_PATH = os.environ.get("SAMPLE_TRACKER_DB", "sample_tracker.db")


def conn():
    return db.connect(DB_PATH)


def show_table(rows):
    """st.dataframe across Streamlit versions (use_container_width is deprecated in newer ones)."""
    try:
        st.dataframe(rows, width="stretch", hide_index=True)
    except Exception:
        st.dataframe(rows, use_container_width=True, hide_index=True)


st.title("🧪 Sample Tracker")
st.caption("Laboratory sample inventory with a hash-chained, append-only chain-of-custody log. Use de-identified subject codes only.")

with st.sidebar:
    st.header("Session")
    actor = st.text_input("Your user ID", value=st.session_state.get("actor", ""), help="Recorded in the audit log, e.g. tech_01")
    st.session_state["actor"] = actor
    c = conn()
    if not db.list_samples(c):
        if st.button("Load demo data"):
            db.seed_demo(c)
            st.rerun()
    c.close()
    st.caption(f"Database: `{DB_PATH}`")

tab_inv, tab_reg, tab_upd, tab_log = st.tabs(["Inventory", "Register", "Update", "Audit log"])

with tab_inv:
    c = conn()
    col1, col2 = st.columns([1, 2])
    status = col1.selectbox("Status", ["All", *db.STATUSES])
    search = col2.text_input("Search sample ID, subject code or location")
    rows = db.list_samples(c, status, search)
    show_table(rows)
    st.caption(f"{len(rows)} sample(s)")
    c.close()

with tab_reg:
    n = st.session_state.get("reg_n", 0)        # bumping n gives the form fresh, empty widgets after a success
    done = st.session_state.pop("reg_msg", None)
    if done:
        st.success(done)
    with st.form(f"register_{n}"):
        a, b = st.columns(2)
        sid = a.text_input("Sample ID", placeholder="S-0457-T", key=f"reg_sid_{n}")
        subj = b.text_input("Subject code (de-identified)", placeholder="S-0457", key=f"reg_subj_{n}")
        typ = a.selectbox("Sample type", db.SAMPLE_TYPES, key=f"reg_typ_{n}")
        loc = b.text_input("Location", placeholder="Freezer A / Rack 2 / Box 4", key=f"reg_loc_{n}")
        note = st.text_input("Note (optional)", key=f"reg_note_{n}")
        if st.form_submit_button("Register sample"):
            try:
                c = conn()
                db.register_sample(c, sid, subj, typ, loc, actor, note)
                c.close()
                st.session_state["reg_msg"] = f"Registered {sid.strip()}."
                st.session_state["reg_n"] = n + 1
                st.rerun()
            except db.ValidationError as e:
                st.error(str(e))      # entries stay in the form so only the bad field needs fixing

with tab_upd:
    c = conn()
    live = [s["sample_id"] for s in db.list_samples(c) if s["status"] != "Discarded"]
    c.close()
    if not live:
        st.info("No active samples to update.")
    else:
        with st.form("update"):
            sid = st.selectbox("Sample", live)
            a, b = st.columns(2)
            new_status = a.selectbox("New status", ["(no change)", *db.STATUSES])
            new_loc = b.text_input("New location (blank = no change)")
            note = st.text_input("Note (optional)")
            st.caption("Samples are never deleted. To retire one, set its status to Discarded.")
            if st.form_submit_button("Save change"):
                try:
                    c = conn()
                    db.update_sample(c, sid, actor, None if new_status == "(no change)" else new_status, new_loc, note)
                    c.close()
                    st.success(f"Updated {sid}.")
                except db.ValidationError as e:
                    st.error(str(e))

with tab_log:
    c = conn()
    if st.button("Verify integrity"):
        res = db.verify_integrity(c)
        if res["ok"]:
            st.success(f"Chain intact: {res['entries']} entries, {res['samples']} samples. Head hash: {res['head_hash']}")
            st.caption("Record the head hash somewhere outside this database; it lets you detect a rewrite of the whole chain later.")
        else:
            st.error(f"{len(res['problems'])} problem(s) found")
            for p in res["problems"]:
                st.write(f"- **{p['kind']}**: {p['message']}")
    show_table(db.get_log(c))
    st.download_button("Download audit log (CSV)", db.export_audit_csv(c), "audit_log.csv", "text/csv")
    c.close()
