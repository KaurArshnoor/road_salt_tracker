from __future__ import annotations
import hashlib
import sqlite3
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import requests

from salt_tracker.db import get_conn
from salt_tracker.models import DiscoveredDocument

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "raw"

# Some state CDNs (Michigan's media/docstore host in particular) return 403
# to plain script-like requests -- no Referer, minimal Accept headers, no
# prior visit to establish a session cookie. This header set + a per-host
# warm-up request mimics an actual browser hitting the listing page first,
# then following a link from it.
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def register_discovered(docs: list[DiscoveredDocument]) -> int:
    """Insert newly discovered documents; ignore ones we've already seen at
    this exact URL (content-level dedupe happens after download, via hash,
    since a URL can be updated in place)."""
    inserted = 0
    with get_conn() as conn:
        for d in docs:
            try:
                conn.execute(
                    """INSERT INTO documents
                       (state, source_url, source_type, vendor_hint, fiscal_year_hint, doc_format_hint,
                        contract_reference, contract_period_raw, contract_period_start, contract_period_end)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (d.state, d.source_url, d.source_type, d.vendor_hint,
                     d.fiscal_year_hint, d.doc_format_hint,
                     d.contract_reference, d.contract_period_raw,
                     d.contract_period_start, d.contract_period_end),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                continue  # already registered
    return inserted


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_pending(session: requests.Session | None = None) -> dict:
    """Downloads every document in status='discovered'. If the resulting
    file hash matches a document we already downloaded from the *same state*
    (e.g. a re-post at a new URL, or an unchanged annual refresh), we mark
    this row as a duplicate and skip extraction on it rather than storing
    the file twice."""
    session = session or requests.Session()
    session.headers.update(BROWSER_HEADERS)
    warmed_hosts: set[str] = set()
    results = {"downloaded": 0, "duplicate": 0, "failed": 0}

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE status = 'discovered'"
        ).fetchall()

        for row in rows:
            host = urlparse(row["source_url"]).netloc
            root = f"https://{host}/"
            if host not in warmed_hosts:
                # best-effort: visiting the host root first picks up any
                # WAF/session cookie the CDN expects before serving a file
                # link; a failure here shouldn't block the actual download
                try:
                    session.get(root, timeout=15)
                except Exception:
                    pass
                warmed_hosts.add(host)

            try:
                resp = session.get(
                    row["source_url"], timeout=60,
                    headers={"Referer": root},
                )
                resp.raise_for_status()
            except Exception as e:
                conn.execute(
                    "UPDATE documents SET status='failed', error_message=? WHERE id=?",
                    (str(e), row["id"]),
                )
                results["failed"] += 1
                continue

            ext = row["doc_format_hint"] or row["source_url"].rsplit(".", 1)[-1].split("?")[0]
            state_dir = DATA_DIR / row["state"]
            state_dir.mkdir(parents=True, exist_ok=True)
            local_path = state_dir / f"doc_{row['id']}.{ext}"
            local_path.write_bytes(resp.content)

            file_hash = sha256_of(local_path)
            dup = conn.execute(
                "SELECT id FROM documents WHERE state=? AND file_hash=? AND id != ?",
                (row["state"], file_hash, row["id"]),
            ).fetchone()

            if dup:
                local_path.unlink(missing_ok=True)
                conn.execute(
                    "UPDATE documents SET status='downloaded', file_hash=?, "
                    "retrieval_date=?, doc_format=?, error_message=? WHERE id=?",
                    (file_hash, date.today().isoformat(), ext,
                     f"duplicate of document {dup['id']}", row["id"]),
                )
                results["duplicate"] += 1
            else:
                conn.execute(
                    "UPDATE documents SET status='downloaded', local_path=?, "
                    "file_hash=?, retrieval_date=?, doc_format=? WHERE id=?",
                    (str(local_path), file_hash, date.today().isoformat(), ext, row["id"]),
                )
                results["downloaded"] += 1

    return results
