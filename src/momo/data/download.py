"""Polite, resumable downloader for the public NSE archive files.

Design points
-------------
* **Resumable.** A file already on disk is never fetched again. A manifest
  (``data/raw/_manifest.csv``) remembers which dates returned "not found"
  (weekends, holidays) so re-runs do not ask for them again.
* **Polite.** Few parallel connections, a pause between requests, exponential
  back-off on errors. NSE throttles aggressive clients.
* **Verified.** A zip must be a real zip and a CSV must not be an HTML error
  page before it is accepted; partial downloads are written to a temp file and
  renamed only when complete.

Run it from a network that can reach ``nsearchives.nseindia.com`` (an Indian
home or campus connection works; many cloud providers are blocked by NSE).
"""

from __future__ import annotations

import datetime as dt
import io
import random
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd
import requests

from ..config import DataConfig
from .sources import RemoteFile, candidate_dates, remote_file, static_files

try:  # progress bar is optional
    from tqdm import tqdm
except Exception:  # pragma: no cover
    tqdm = None

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.nseindia.com/all-reports",
    "Connection": "keep-alive",
}

MANIFEST_NAME = "_manifest.csv"
MANIFEST_COLS = ["dataset", "date", "status", "bytes", "http_status", "url", "checked_at"]


def _looks_valid(content: bytes, kind: str) -> bool:
    """Reject HTML error pages and truncated archives masquerading as data."""
    if not content:
        return False
    head = content[:200].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        return False
    if kind == "zip":
        return zipfile.is_zipfile(io.BytesIO(content))
    return True


class Downloader:
    """Fetch daily and static NSE files into ``<data root>/raw``."""

    def __init__(self, cfg: DataConfig):
        self.cfg = cfg
        self.raw = cfg.raw
        self.raw.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()

    # -- HTTP ---------------------------------------------------------------
    def _session(self) -> requests.Session:
        """One HTTP session per worker thread (sessions are not thread-safe)."""
        if not hasattr(self._local, "session"):
            s = requests.Session()
            s.headers.update(BROWSER_HEADERS)
            self._local.session = s
        return self._local.session

    def _fetch(self, rf: RemoteFile) -> dict:
        """Download one file. Returns a manifest row."""
        url = self.cfg.archive_base_url.rstrip("/") + rf.url_path
        dest = self.raw / rf.local_rel
        row = {
            "dataset": rf.dataset,
            "date": rf.date.isoformat() if rf.date else Path(rf.local_rel).name,
            "status": "error",
            "bytes": 0,
            "http_status": None,
            "url": url,
            "checked_at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        if dest.exists() and dest.stat().st_size > 0:
            row.update(status="exists", bytes=dest.stat().st_size)
            return row

        for attempt in range(self.cfg.max_retries):
            try:
                time.sleep(self.cfg.pause_s * (1 + random.random()))
                resp = self._session().get(url, timeout=self.cfg.request_timeout_s)
                row["http_status"] = resp.status_code
                if resp.status_code == 404:
                    row["status"] = "missing"  # holiday / weekend / not published
                    return row
                if resp.status_code == 200 and _looks_valid(resp.content, rf.kind):
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    tmp = dest.with_suffix(dest.suffix + ".part")
                    tmp.write_bytes(resp.content)
                    tmp.replace(dest)  # atomic: never leave a half-written file
                    row.update(status="ok", bytes=len(resp.content))
                    return row
                if resp.status_code in (401, 403):
                    row["status"] = "blocked"  # server refuses this network - do not hammer it
                    return row
            except requests.RequestException as exc:
                row["http_status"] = type(exc).__name__
            time.sleep(min(30.0, (2 ** attempt) + random.random()))  # back off, then retry
        return row

    # -- manifest -----------------------------------------------------------
    @property
    def manifest_path(self) -> Path:
        return self.raw / MANIFEST_NAME

    def load_manifest(self) -> pd.DataFrame:
        if self.manifest_path.exists():
            return pd.read_csv(self.manifest_path, dtype=str)
        return pd.DataFrame(columns=MANIFEST_COLS)

    def _save_manifest(self, old: pd.DataFrame, new_rows: List[dict]) -> pd.DataFrame:
        new = pd.DataFrame(new_rows, columns=MANIFEST_COLS).astype(str)
        merged = pd.concat([old, new], ignore_index=True)
        merged = merged.drop_duplicates(subset=["dataset", "date"], keep="last")
        merged = merged.sort_values(["dataset", "date"]).reset_index(drop=True)
        merged.to_csv(self.manifest_path, index=False)
        return merged

    # -- public API ---------------------------------------------------------
    def run(
        self,
        start: Optional[dt.date] = None,
        end: Optional[dt.date] = None,
        datasets: Optional[Iterable[str]] = None,
        include_static: bool = True,
        retry_missing: bool = False,
        include_weekends: bool = True,
        progress: bool = True,
    ) -> pd.DataFrame:
        """Download everything in ``[start, end]`` that is not already on disk.

        Returns the updated manifest. Dates previously recorded as "missing" are
        skipped unless ``retry_missing`` is set (use it to pick up files that
        NSE publishes late).
        """
        start = start or self.cfg.download_start
        end = end or self.cfg.download_end or dt.date.today()
        if start is None:
            raise ValueError("No start date: set download_start in config/data.yaml or pass start=")
        datasets = list(datasets or self.cfg.datasets)

        old = self.load_manifest()
        skip = set()
        if not retry_missing and len(old):
            m = old[old["status"] == "missing"]
            skip = set(zip(m["dataset"], m["date"]))

        todo: List[RemoteFile] = []
        for d in candidate_dates(start, end, include_weekends):
            for ds in datasets:
                if (ds, d.isoformat()) in skip:
                    continue
                todo.append(remote_file(ds, d))
        if include_static:
            todo += static_files()

        rows: List[dict] = []
        blocked = False
        bar = tqdm(total=len(todo), desc="NSE download", unit="file") if (tqdm and progress) else None
        with ThreadPoolExecutor(max_workers=max(1, self.cfg.max_workers)) as pool:
            futures = [pool.submit(self._fetch, rf) for rf in todo]
            for fut in as_completed(futures):
                row = fut.result()
                rows.append(row)
                blocked = blocked or row["status"] == "blocked"
                if bar:
                    bar.update(1)
        if bar:
            bar.close()

        manifest = self._save_manifest(old, rows)
        if blocked:
            print(
                "\nWARNING: the NSE archive refused some requests (HTTP 401/403). "
                "This usually means the network you are on is blocked by NSE. "
                "Run the download from an Indian home or campus connection."
            )
        return manifest


def summarise_manifest(manifest: pd.DataFrame) -> pd.DataFrame:
    """Counts of files by dataset and status - printed after a download."""
    if manifest.empty:
        return pd.DataFrame()
    return manifest.groupby(["dataset", "status"]).size().unstack(fill_value=0)
