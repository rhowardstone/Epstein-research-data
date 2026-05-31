#!/usr/bin/env python3
"""
Build a coverage map of EFTA document mirrors.

Checks which EFTA documents are available on each mirror by sending HEAD
requests, recording HTTP status and content-length. Results are stored in
a SQLite database for incremental building — safe to interrupt and resume.

Mirrors checked:
  - RollCall:    media-cdn.rollcall.com/epstein-files/{EFTA}.pdf
  - Kino/JDrive: assets.getkino.com/documents/{EFTA}.pdf

DOJ is canonical (always available behind age gate) and PlainSite serves
HTML viewer pages (not raw PDFs), so neither is checked here.

Usage:
  # Check 100 random unchecked documents across all datasets
  python3 tools/mirror_coverage.py --limit 100

  # Focus on Dataset 9
  python3 tools/mirror_coverage.py --dataset 9 --limit 500

  # Check specific EFTAs
  python3 tools/mirror_coverage.py --efta EFTA00001267 EFTA00003591

  # Show current coverage stats
  python3 tools/mirror_coverage.py --stats

  # Export coverage map to CSV
  python3 tools/mirror_coverage.py --export coverage_map.csv

  # Adjust request delay (default 0.2s between requests)
  python3 tools/mirror_coverage.py --delay 0.5 --limit 1000

  # Specify database location explicitly
  python3 tools/mirror_coverage.py --db /path/to/full_text_corpus.db --limit 100

Requires: pip install requests
"""

import argparse
import csv
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

# ── Path auto-detection ────────────────────────────────────────────

def find_corpus_db():
    """Find full_text_corpus.db by searching common locations."""
    candidates = [
        os.path.join(os.getcwd(), "full_text_corpus.db"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "full_text_corpus.db"),
        os.path.join(os.getcwd(), "..", "full_text_corpus.db"),
    ]
    # Also check sibling directories (data repo next to working dir)
    parent = os.path.dirname(os.getcwd())
    for name in os.listdir(parent) if os.path.isdir(parent) else []:
        candidates.append(os.path.join(parent, name, "full_text_corpus.db"))

    for path in candidates:
        if os.path.exists(path):
            return os.path.abspath(path)
    return None


# ── Mirror definitions ─────────────────────────────────────────────

MIRRORS = {
    "rollcall": {
        "label": "RollCall",
        "url": "https://media-cdn.rollcall.com/epstein-files/{efta}.pdf",
        "status_col": "rollcall_status",
        "size_col": "rollcall_size",
    },
    "kino": {
        "label": "Kino/JDrive",
        "url": "https://assets.getkino.com/documents/{efta}.pdf",
        "status_col": "kino_status",
        "size_col": "kino_size",
    },
}

DOJ_URL = "https://www.justice.gov/epstein/files/DataSet%20{ds}/{efta}.pdf"

# DS8 uses a different naming convention on Kino/JDrive CDN.
# Standard pattern: EFTA00009676.pdf (works for ~38% of DS8)
# Vol pattern: vol00008-official-doj-latest-efta00009676.pdf (works for 100%)
KINO_DS8_URL = "https://assets.getkino.com/documents/vol00008-official-doj-latest-{efta_lower}.pdf"

# ── Database setup ─────────────────────────────────────────────────

def init_coverage_db(db_dir):
    """Create coverage database and table if they don't exist."""
    db_path = os.path.join(db_dir, "mirror_coverage.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS coverage (
            efta_number TEXT PRIMARY KEY,
            dataset INTEGER,
            doj_url TEXT,
            rollcall_status INTEGER,
            rollcall_size INTEGER,
            rollcall_url TEXT,
            kino_status INTEGER,
            kino_size INTEGER,
            kino_url TEXT,
            checked_at TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_coverage_dataset
        ON coverage(dataset)
    """)
    conn.commit()
    return conn, db_path


def get_unchecked_eftas(corpus_conn, coverage_conn, dataset=None, limit=None):
    """Get document-level EFTAs not yet checked, in random order."""
    already = set()
    rows = coverage_conn.execute("SELECT efta_number FROM coverage").fetchall()
    for r in rows:
        already.add(r[0])

    query = "SELECT efta_number, dataset FROM documents WHERE dataset BETWEEN 1 AND 12"
    params = []
    if dataset:
        query += " AND dataset = ?"
        params.append(dataset)
    query += " ORDER BY RANDOM()"
    if limit:
        query += " LIMIT ?"
        params.append(limit * 3)

    rows = corpus_conn.execute(query, params).fetchall()
    result = [(r[0], r[1]) for r in rows if r[0] not in already]

    if limit:
        result = result[:limit]
    return result


# ── HTTP checking ──────────────────────────────────────────────────

def check_mirror(session, url):
    """HEAD request a mirror URL. Returns (status_code, content_length)."""
    try:
        r = session.head(url, timeout=15, allow_redirects=True)
        size = r.headers.get("Content-Length")
        return r.status_code, int(size) if size else None
    except Exception:
        return -1, None


def check_efta(session, efta, dataset, delay=0.2):
    """Check one EFTA across all mirrors. Returns dict of results."""
    result = {
        "efta_number": efta,
        "dataset": dataset,
        "doj_url": DOJ_URL.format(ds=dataset, efta=efta),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    for key, mirror in MIRRORS.items():
        url = mirror["url"].format(efta=efta)
        status, size = check_mirror(session, url)

        # DS8 fallback: try vol00008 naming convention on Kino
        if key == "kino" and status != 200 and dataset == 8:
            efta_lower = efta.lower()
            alt_url = KINO_DS8_URL.format(efta_lower=efta_lower)
            alt_status, alt_size = check_mirror(session, alt_url)
            if alt_status == 200:
                status, size, url = alt_status, alt_size, alt_url
            time.sleep(delay)

        result[mirror["status_col"]] = status
        result[mirror["size_col"]] = size
        result[f"{key}_url"] = url
        time.sleep(delay)

    return result


def save_result(conn, result):
    """Insert or update a coverage result."""
    conn.execute("""
        INSERT OR REPLACE INTO coverage
        (efta_number, dataset, doj_url,
         rollcall_status, rollcall_size, rollcall_url,
         kino_status, kino_size, kino_url,
         checked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        result["efta_number"], result["dataset"], result["doj_url"],
        result["rollcall_status"], result["rollcall_size"], result["rollcall_url"],
        result["kino_status"], result["kino_size"], result["kino_url"],
        result["checked_at"],
    ))
    conn.commit()


# ── Stats & export ─────────────────────────────────────────────────

def show_stats(coverage_conn, corpus_conn, db_path):
    """Print coverage statistics."""
    total_docs = corpus_conn.execute(
        "SELECT COUNT(*) FROM documents WHERE dataset BETWEEN 1 AND 12"
    ).fetchone()[0]
    checked = coverage_conn.execute("SELECT COUNT(*) FROM coverage").fetchone()[0]

    print(f"\n  Coverage database: {db_path}")
    print(f"  Total documents in corpus: {total_docs:,}")
    print(f"  Documents checked: {checked:,} ({100*checked/total_docs:.1f}%)\n")

    print(f"  {'DS':>4}  {'Total':>8}  {'Checked':>8}  {'RC 200':>7}  {'RC 403':>7}  {'Kino 200':>8}  {'Kino 404':>8}")
    print(f"  {'─'*4}  {'─'*8}  {'─'*8}  {'─'*7}  {'─'*7}  {'─'*8}  {'─'*8}")

    for ds in range(1, 13):
        ds_total = corpus_conn.execute(
            "SELECT COUNT(*) FROM documents WHERE dataset = ?", (ds,)
        ).fetchone()[0]
        ds_checked = coverage_conn.execute(
            "SELECT COUNT(*) FROM coverage WHERE dataset = ?", (ds,)
        ).fetchone()[0]
        rc_200 = coverage_conn.execute(
            "SELECT COUNT(*) FROM coverage WHERE dataset = ? AND rollcall_status = 200", (ds,)
        ).fetchone()[0]
        rc_403 = coverage_conn.execute(
            "SELECT COUNT(*) FROM coverage WHERE dataset = ? AND rollcall_status = 403", (ds,)
        ).fetchone()[0]
        kino_200 = coverage_conn.execute(
            "SELECT COUNT(*) FROM coverage WHERE dataset = ? AND kino_status = 200", (ds,)
        ).fetchone()[0]
        kino_404 = coverage_conn.execute(
            "SELECT COUNT(*) FROM coverage WHERE dataset = ? AND kino_status = 404", (ds,)
        ).fetchone()[0]

        print(f"  DS{ds:>2}  {ds_total:>8,}  {ds_checked:>8,}  {rc_200:>7,}  {rc_403:>7,}  {kino_200:>8,}  {kino_404:>8,}")

    rc_200_all = coverage_conn.execute(
        "SELECT COUNT(*) FROM coverage WHERE rollcall_status = 200"
    ).fetchone()[0]
    kino_200_all = coverage_conn.execute(
        "SELECT COUNT(*) FROM coverage WHERE kino_status = 200"
    ).fetchone()[0]

    if checked:
        print(f"\n  RollCall available: {rc_200_all:,}/{checked:,} ({100*rc_200_all/checked:.1f}%)")
        print(f"  Kino available:    {kino_200_all:,}/{checked:,} ({100*kino_200_all/checked:.1f}%)")

    mismatches = coverage_conn.execute("""
        SELECT COUNT(*) FROM coverage
        WHERE rollcall_status = 200 AND kino_status = 200
        AND rollcall_size IS NOT NULL AND kino_size IS NOT NULL
        AND rollcall_size != kino_size
    """).fetchone()[0]
    both_ok = coverage_conn.execute("""
        SELECT COUNT(*) FROM coverage
        WHERE rollcall_status = 200 AND kino_status = 200
        AND rollcall_size IS NOT NULL AND kino_size IS NOT NULL
    """).fetchone()[0]
    if both_ok:
        print(f"\n  Size mismatches (RC vs Kino): {mismatches}/{both_ok}")


def export_csv(coverage_conn, path):
    """Export coverage data to CSV."""
    rows = coverage_conn.execute("""
        SELECT efta_number, dataset, doj_url,
               rollcall_status, rollcall_size, rollcall_url,
               kino_status, kino_size, kino_url,
               checked_at
        FROM coverage
        ORDER BY efta_number
    """).fetchall()

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "efta_number", "dataset", "doj_url",
            "rollcall_status", "rollcall_size", "rollcall_url",
            "kino_status", "kino_size", "kino_url",
            "checked_at",
        ])
        writer.writerows(rows)

    print(f"  Exported {len(rows):,} rows to {path}")


# ── Main ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Build EFTA document mirror coverage map."
    )
    parser.add_argument("--db", metavar="PATH",
                        help="Path to full_text_corpus.db (auto-detected if omitted)")
    parser.add_argument("--limit", type=int, default=100,
                        help="Max documents to check this run (default: 100)")
    parser.add_argument("--dataset", type=int,
                        help="Only check documents from this dataset (1-12)")
    parser.add_argument("--efta", nargs="+",
                        help="Check specific EFTA number(s)")
    parser.add_argument("--delay", type=float, default=0.2,
                        help="Seconds between requests per mirror (default: 0.2)")
    parser.add_argument("--stats", action="store_true",
                        help="Show coverage statistics and exit")
    parser.add_argument("--export", metavar="FILE",
                        help="Export coverage map to CSV and exit")
    args = parser.parse_args()

    # Find corpus database
    corpus_path = args.db or find_corpus_db()
    if not corpus_path or not os.path.exists(corpus_path):
        print("Error: Cannot find full_text_corpus.db")
        print("  Specify with --db /path/to/full_text_corpus.db")
        print("  Or run from the directory containing the database.")
        sys.exit(1)

    db_dir = os.path.dirname(corpus_path)
    corpus_conn = sqlite3.connect(corpus_path)
    coverage_conn, coverage_path = init_coverage_db(db_dir)

    if args.stats:
        show_stats(coverage_conn, corpus_conn, coverage_path)
        return

    if args.export:
        export_csv(coverage_conn, args.export)
        return

    try:
        import requests
    except ImportError:
        print("Error: 'requests' package required. Install with: pip install requests")
        sys.exit(1)

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (compatible; research-mirror-check)"

    if args.efta:
        eftas = []
        for e in args.efta:
            row = corpus_conn.execute(
                "SELECT efta_number, dataset FROM documents WHERE efta_number = ?", (e,)
            ).fetchone()
            if row:
                eftas.append((row[0], row[1]))
            else:
                print(f"  Warning: {e} not found in corpus, skipping")
    else:
        eftas = get_unchecked_eftas(corpus_conn, coverage_conn,
                                     dataset=args.dataset, limit=args.limit)

    if not eftas:
        print("  Nothing to check — all documents already covered (or dataset empty).")
        print("  Use --stats to see current coverage.")
        return

    print(f"  Checking {len(eftas)} documents (delay: {args.delay}s per mirror)")
    print(f"  Corpus: {corpus_path}")
    print(f"  Results: {coverage_path}\n")

    checked = 0
    rc_ok = 0
    kino_ok = 0
    start = time.time()

    for i, (efta, ds) in enumerate(eftas):
        result = check_efta(session, efta, ds, delay=args.delay)
        save_result(coverage_conn, result)
        checked += 1

        rc_status = result["rollcall_status"]
        kino_status = result["kino_status"]
        if rc_status == 200:
            rc_ok += 1
        if kino_status == 200:
            kino_ok += 1

        if (i + 1) % 10 == 0 or rc_status != 200 or kino_status != 200:
            elapsed = time.time() - start
            rate = checked / elapsed if elapsed > 0 else 0
            print(f"  [{i+1}/{len(eftas)}] DS{ds:>2} {efta}  "
                  f"RC={rc_status}  Kino={kino_status}  "
                  f"({rate:.1f}/s, RC:{rc_ok}/{checked}, Kino:{kino_ok}/{checked})")

    elapsed = time.time() - start
    print(f"\n  Done: {checked} docs in {elapsed:.0f}s")
    print(f"  RollCall: {rc_ok}/{checked} available ({100*rc_ok/checked:.1f}%)")
    print(f"  Kino:     {kino_ok}/{checked} available ({100*kino_ok/checked:.1f}%)")


if __name__ == "__main__":
    main()
