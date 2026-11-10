import os, sys, pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import database as db
from sample_data import generate as gen

@pytest.fixture(autouse=True)
def _reset_clock():
    """The injectable clock is a process global; make sure a test that installs one cannot leak it."""
    yield
    db.set_clock(None)

@pytest.fixture(scope="session")
def sample_dir(tmp_path_factory):
    d = str(tmp_path_factory.mktemp("samples"))
    gen.build(d)
    return d

@pytest.fixture()
def env(tmp_path, sample_dir):
    """Fresh SQLite DB + upload dir per test; same workflow function the UI uses."""
    import workflow as wf
    conn = db.connect(str(tmp_path / "t.db"))
    up = str(tmp_path / "uploads")
    sc = {s["id"]: s for s in gen.load_scenarios(sample_dir)}
    def run(sid, **form_over):
        s = sc[sid]
        form = {**s["form"], **form_over}
        return wf.run_submission(conn, form, gen.scenario_files(sample_dir, s), up)
    return type("Env", (), dict(conn=conn, run=staticmethod(run), up=up, sc=sc, wf=wf, dir=sample_dir))
