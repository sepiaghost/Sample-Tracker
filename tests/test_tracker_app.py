import os

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sample_tracker", "app.py")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMPLE_TRACKER_DB", str(tmp_path / "t.db"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception
    return at


def _load_demo(at):
    next(b for b in at.sidebar.button if b.label == "Load demo data").click().run()
    assert not at.exception


def test_demo_data_loads_and_verifies(app):
    app.sidebar.text_input[0].set_value("tech_01").run()
    _load_demo(app)
    next(b for b in app.tabs[3].button if b.label == "Verify integrity").click().run()
    assert any("Chain intact" in s.value for s in app.success)


def test_register_validation_and_success(app):
    app.sidebar.text_input[0].set_value("tech_01").run()
    form = app.tabs[1]
    form.text_input[0].set_value("S-0500-T")
    form.text_input[1].set_value("Jane Doe")          # a name, not a de-identified code
    form.text_input[2].set_value("Freezer A")
    next(b for b in form.button if b.label == "Register sample").click().run()
    assert any("de-identified" in e.value for e in app.error)
    form = app.tabs[1]
    assert form.text_input[0].value == "S-0500-T" and form.text_input[2].value == "Freezer A"   # nothing wiped on error
    form.text_input[1].set_value("S-0500")             # fix only the bad field
    next(b for b in form.button if b.label == "Register sample").click().run()
    assert any("Registered S-0500-T" in s.value for s in app.success)
    assert app.tabs[1].text_input[0].value == ""       # form resets after a successful save


def test_register_requires_valid_actor(app):
    form = app.tabs[1]
    form.text_input[0].set_value("S-0500-T")
    form.text_input[1].set_value("S-0500")
    form.text_input[2].set_value("Freezer A")
    next(b for b in form.button if b.label == "Register sample").click().run()
    assert any("Actor" in e.value for e in app.error)


def test_update_status_and_bad_transition(app):
    app.sidebar.text_input[0].set_value("tech_01").run()
    _load_demo(app)
    upd = app.tabs[2]
    upd.selectbox[0].select("S-0457-T")
    upd.selectbox[1].select("Registered")             # In storage -> Registered is not allowed
    next(b for b in upd.button if b.label == "Save change").click().run()
    assert any("Cannot change" in e.value for e in app.error)
    upd = app.tabs[2]
    upd.selectbox[1].select("Checked out")
    next(b for b in upd.button if b.label == "Save change").click().run()
    assert any("Updated S-0457-T" in s.value for s in app.success)


def test_tampering_is_reported_in_ui(app, tmp_path):
    import sqlite3
    app.sidebar.text_input[0].set_value("tech_01").run()
    _load_demo(app)
    c = sqlite3.connect(str(tmp_path / "t.db"))
    c.execute("UPDATE samples SET location='Elsewhere' WHERE sample_id='S-0457-T'")
    c.commit()
    c.close()
    app.run()
    next(b for b in app.tabs[3].button if b.label == "Verify integrity").click().run()
    assert any("problem" in e.value for e in app.error)
    assert any("STATE_MISMATCH" in m.value for m in app.markdown)
