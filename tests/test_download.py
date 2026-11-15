"""The downloader against a local web server that serves fixture files at the real URL paths."""

import datetime as dt
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from momo.config import DataConfig
from momo.data.download import Downloader, summarise_manifest
from momo.data.ingest import date_from_name
from momo.data.sources import UDIFF_SWITCH_DATE, remote_file
from momo.data.synthetic import FixtureSpec, generate_fixture

REQUESTS = []


class _Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep the test output quiet
        pass

    def do_GET(self):
        REQUESTS.append(self.path)
        super().do_GET()


@pytest.fixture()
def server(tmp_path):
    spec = FixtureSpec(start=dt.date(2024, 6, 24), end=dt.date(2024, 7, 19), n_companies=25, seed=5, layout="server")
    generate_fixture(tmp_path / "remote", spec)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, directory=str(tmp_path / "remote" / "server")))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", spec
    httpd.shutdown()


def test_download_resume_and_manifest(server, tmp_path):
    url, spec = server
    cfg = DataConfig(root=str(tmp_path / "local"), archive_base_url=url, max_workers=3, pause_s=0.0, max_retries=1)
    REQUESTS.clear()
    manifest = Downloader(cfg).run(spec.start, spec.end, ["cm_bhav", "pr", "ind_close"], progress=False)
    counts = summarise_manifest(manifest)
    assert counts.loc["cm_bhav", "ok"] >= 15 and counts.loc["pr", "ok"] == counts.loc["cm_bhav", "ok"]
    assert counts.loc["cm_bhav", "missing"] >= 6, "weekends and holidays come back as 'missing'"
    # both bhavcopy layouts were fetched: legacy before the switch date, UDiFF from it
    names = [p.name for p in (cfg.raw / "cm_bhav").rglob("*.zip")]
    assert any(n.startswith("cm") for n in names) and any(n.startswith("BhavCopy_NSE_CM") for n in names)
    assert (cfg.raw / "static" / "symbolchange.csv").exists()

    first_pass = len(REQUESTS)
    REQUESTS.clear()
    Downloader(cfg).run(spec.start, spec.end, ["cm_bhav", "pr", "ind_close"], progress=False)
    daily = [r for r in REQUESTS if "symbolchange" not in r and "/indices/ind_n" not in r and "EQUITY_L" not in r]
    assert first_pass > 50 and len(daily) == 0, "a second run must not ask again for files it has or knows are missing"


def test_url_and_file_name_conventions():
    legacy = remote_file("cm_bhav", dt.date(2020, 3, 23))
    assert legacy.url_path == "/content/historical/EQUITIES/2020/MAR/cm23MAR2020bhav.csv.zip"
    udiff = remote_file("cm_bhav", UDIFF_SWITCH_DATE)
    assert udiff.url_path == "/content/cm/BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip"
    assert remote_file("pr", dt.date(2020, 3, 23)).url_path == "/archives/equities/bhavcopy/pr/PR230320.zip"
    assert remote_file("ind_close", dt.date(2020, 3, 23)).url_path == "/content/indices/ind_close_all_23032020.csv"
    for dataset, day in (("cm_bhav", dt.date(2020, 3, 23)), ("cm_bhav", dt.date(2025, 1, 2)),
                         ("pr", dt.date(2019, 12, 31)), ("ind_close", dt.date(2021, 2, 1))):
        name = remote_file(dataset, day).local_rel.split("/")[-1]
        assert date_from_name(dataset, name) == day, "the date must survive the round trip through the file name"
