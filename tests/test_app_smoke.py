"""Part D: a headless smoke test that the Streamlit app renders both modes and all pages without raising."""
import os
import pytest


def test_app_renders_all_pages(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    monkeypatch.setenv("KYVO_DB", str(tmp_path / "smoke.db"))
    monkeypatch.setenv("KYVO_UPLOADS", str(tmp_path / "uploads"))
    from streamlit.testing.v1 import AppTest
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    at = AppTest.from_file(os.path.join(root, "app.py"), default_timeout=120).run()
    assert not at.exception                                   # vendor portal (default) renders

    at.radio(key="mode").set_value("Admin review").run()
    assert not at.exception
    for p in ["Overview", "New Submission", "Review Queue", "Change requests", "Reminders", "History"]:
        at.radio(key="nav").set_value(p).run()
        assert not at.exception, p

    # back to the vendor portal: sign up issues a Vendor ID, and the dashboard renders
    at.radio(key="mode").set_value("Vendor portal").run()
    at.text_input(key="su_pan").set_value("AABCM4821K")
    at.text_input(key="su_gstin").set_value("29AABCM4821K1Z5")
    at.text_input(key="su_email").set_value("anita@example.test")
    at.checkbox(key="su_captcha").set_value(True)
    at.button(key="su_btn").click().run()
    assert not at.exception
    msg = [s.value for s in at.success]
    assert msg and "Vendor ID" in msg[0]
    vid = msg[0].split("**")[1]
    at.session_state["vendor"] = vid
    at.run()
    assert not at.exception
