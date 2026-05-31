#!/usr/bin/env python3
"""
Incremental Redaction Detector — adds new datasets to existing redaction_analysis_v2.db.

Based on redaction_detector_v2.py but:
  - Does NOT delete the existing database
  - Skips EFTA numbers already in document_summary
  - Tags new records with dataset_source
  - Accepts --datasets CLI arg to select which datasets to scan

Usage:
  python3 redaction_detector_incremental.py --datasets 9 11
  python3 redaction_detector_incremental.py --datasets 11 --workers 12
"""

import argparse
import fitz
import numpy as np
import os
import re
import glob
import sqlite3
import logging
import time
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def _find_data_dir():
    """Find the directory containing the database files."""
    if os.environ.get("EPSTEIN_DATA_DIR"):
        return os.environ["EPSTEIN_DATA_DIR"]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(repo_root, "datasets")):
        return repo_root
    if os.path.exists(os.path.join(os.getcwd(), "datasets")):
        return os.getcwd()
    parent = os.path.dirname(os.getcwd())
    for name in os.listdir(parent):
        candidate = os.path.join(parent, name, "datasets")
        if os.path.exists(candidate):
            return os.path.join(parent, name)
    return os.getcwd()

BASE_DIR = _find_data_dir()
DB_PATH = os.path.join(BASE_DIR, "redaction_analysis_v2.db")
LOG_PATH = os.path.join(BASE_DIR, "redaction_detector_incremental.log")

# Detection thresholds (same as v2)
BLACK_PIXEL_THRESHOLD = 30
MIN_RECT_WIDTH_PX = 30
MIN_RECT_HEIGHT_PX = 5
ROW_BLACK_RATIO = 0.15
COL_BLACK_RATIO = 0.5
BATCH_COMMIT_SIZE = 200
NUM_WORKERS = 8

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="a"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("redaction_incremental")


# ---------------------------------------------------------------------------
# Database setup (non-destructive)
# ---------------------------------------------------------------------------
def init_db(db_path: str) -> sqlite3.Connection:
    """Open the existing database. Create tables only if they don't exist."""
    conn = sqlite3.connect(db_path, timeout=120)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS redactions (
            id INTEGER PRIMARY KEY,
            pdf_path TEXT,
            efta_number TEXT,
            page_number INTEGER,
            redaction_type TEXT,
            rect_x0 REAL, rect_y0 REAL, rect_x1 REAL, rect_y1 REAL,
            hidden_text TEXT,
            confidence REAL,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            dataset_source TEXT
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_efta ON redactions(efta_number)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_type ON redactions(redaction_type)")
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_hidden ON redactions(hidden_text)
        WHERE hidden_text IS NOT NULL
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS document_summary (
            id INTEGER PRIMARY KEY,
            pdf_path TEXT UNIQUE,
            efta_number TEXT,
            total_redactions INTEGER,
            bad_redactions INTEGER,
            proper_redactions INTEGER,
            has_recoverable_text BOOLEAN,
            scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            dataset_source TEXT
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_ds_efta ON document_summary(efta_number)")

    # Ensure dataset_source column exists (may not in old DBs)
    try:
        c.execute("SELECT dataset_source FROM redactions LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE redactions ADD COLUMN dataset_source TEXT")
    try:
        c.execute("SELECT dataset_source FROM document_summary LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE document_summary ADD COLUMN dataset_source TEXT")

    conn.commit()
    return conn


def get_already_scanned(conn: sqlite3.Connection) -> set:
    """Return set of EFTA numbers already in document_summary."""
    cursor = conn.execute("SELECT efta_number FROM document_summary")
    return {row[0] for row in cursor.fetchall()}


# ---------------------------------------------------------------------------
# PDF collection
# ---------------------------------------------------------------------------
def collect_pdfs(datasets: list[int]) -> list[str]:
    """Find all PDF files across the specified datasets."""
    pdfs: list[str] = []
    for ds in datasets:
        ds_dir = os.path.join(BASE_DIR, "datasets", f"dataset{ds}")
        if not os.path.isdir(ds_dir):
            log.warning("Dataset directory not found: %s", ds_dir)
            continue
        found = glob.glob(os.path.join(ds_dir, "**", "*.pdf"), recursive=True)
        log.info("  Dataset %d: %d PDFs found in %s", ds, len(found), ds_dir)
        pdfs.extend(found)
    return sorted(set(pdfs))


# ---------------------------------------------------------------------------
# EFTA extraction
# ---------------------------------------------------------------------------
_EFTA_RE = re.compile(r"(EFTA\d{8,})")

def extract_efta(pdf_path: str) -> str:
    basename = os.path.basename(pdf_path).replace(".pdf", "")
    m = _EFTA_RE.search(basename)
    return m.group(1) if m else basename


# ---------------------------------------------------------------------------
# Black-rectangle detection (identical to v2)
# ---------------------------------------------------------------------------
def find_black_rects_in_image(pix: fitz.Pixmap,
                               min_w: int = MIN_RECT_WIDTH_PX,
                               min_h: int = MIN_RECT_HEIGHT_PX):
    if pix.n == 4:
        pix2 = fitz.Pixmap(fitz.csRGB, pix)
        samples = pix2.samples
        h, w, n = pix2.height, pix2.width, pix2.n
    else:
        samples = pix.samples
        h, w, n = pix.height, pix.width, pix.n

    arr = np.frombuffer(samples, dtype=np.uint8).reshape(h, w, n)
    if n == 1:
        gray = arr[:, :, 0]
    else:
        gray = np.min(arr, axis=2)

    is_black = gray < BLACK_PIXEL_THRESHOLD
    row_ratio = np.mean(is_black, axis=1)
    black_rows = np.where(row_ratio > ROW_BLACK_RATIO)[0]

    if len(black_rows) == 0:
        return []

    bands = []
    start = black_rows[0]
    prev = black_rows[0]
    for r in black_rows[1:]:
        if r - prev > 3:
            if prev - start >= min_h:
                bands.append((start, prev + 1))
            start = r
        prev = r
    if prev - start >= min_h:
        bands.append((start, prev + 1))

    rects = []
    for y0, y1 in bands:
        band_mask = is_black[y0:y1, :]
        col_ratio = np.mean(band_mask, axis=0)
        black_cols = np.where(col_ratio > COL_BLACK_RATIO)[0]
        if len(black_cols) < min_w:
            continue
        segments = []
        seg_start = black_cols[0]
        seg_prev = black_cols[0]
        for c in black_cols[1:]:
            if c - seg_prev > 5:
                if seg_prev - seg_start >= min_w:
                    segments.append((seg_start, seg_prev + 1))
                seg_start = c
            seg_prev = c
        if seg_prev - seg_start >= min_w:
            segments.append((seg_start, seg_prev + 1))
        for x0, x1 in segments:
            rects.append((x0, y0, x1, y1))

    return _merge_rects(rects)


def _merge_rects(rects):
    if not rects:
        return []
    rects = sorted(rects, key=lambda r: (r[1], r[0]))
    merged = [list(rects[0])]
    for r in rects[1:]:
        m = merged[-1]
        if (r[0] <= m[2] + 5 and r[2] >= m[0] - 5 and
                r[1] <= m[3] + 5 and r[3] >= m[1] - 5):
            m[0] = min(m[0], r[0])
            m[1] = min(m[1], r[1])
            m[2] = max(m[2], r[2])
            m[3] = max(m[3], r[3])
        else:
            merged.append(list(r))
    return [tuple(m) for m in merged]


# ---------------------------------------------------------------------------
# Single-PDF analysis (identical to v2)
# ---------------------------------------------------------------------------
def analyze_pdf(pdf_path: str) -> dict:
    efta = extract_efta(pdf_path)
    result = {"pdf_path": pdf_path, "efta_number": efta, "redactions": [], "error": None}

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        result["error"] = str(exc)
        return result

    try:
        for page_idx in range(doc.page_count):
            page = doc[page_idx]
            page_rect = page.rect

            # 1. PDF-level redaction annotations
            annots = page.annots()
            if annots:
                for annot in annots:
                    if annot.type[0] == 12:
                        r = annot.rect
                        text = page.get_text("text", clip=r).strip()
                        result["redactions"].append({
                            "page": page_idx, "type": "annotation",
                            "rect": (r.x0, r.y0, r.x1, r.y1),
                            "hidden_text": text if text else None, "confidence": 0.95,
                        })

            # 2. Black filled drawings
            drawings = page.get_drawings()
            for d in drawings:
                fill = d.get("fill")
                if fill is None:
                    continue
                if isinstance(fill, tuple) and all(c < 0.15 for c in fill):
                    r = fitz.Rect(d["rect"])
                    if r.width < 10 or r.height < 5:
                        continue
                    if r.width >= page_rect.width * 0.95 and r.height >= page_rect.height * 0.95:
                        continue
                    text = page.get_text("text", clip=r).strip()
                    result["redactions"].append({
                        "page": page_idx,
                        "type": "bad_overlay" if text else "proper_redaction",
                        "rect": (r.x0, r.y0, r.x1, r.y1),
                        "hidden_text": text if text else None,
                        "confidence": 0.90 if text else 0.80,
                    })

            # 3. Embedded images with black rectangles
            images = page.get_images(full=True)
            if not images:
                continue
            for img_info in images:
                xref = img_info[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                except Exception:
                    continue
                img_w, img_h = pix.width, pix.height
                if img_w < 50 or img_h < 50:
                    continue
                black_rects = find_black_rects_in_image(pix)
                if not black_rects:
                    continue
                scale_x = page_rect.width / img_w
                scale_y = page_rect.height / img_h
                for bx0, by0, bx1, by1 in black_rects:
                    rect_w = bx1 - bx0
                    rect_h = by1 - by0
                    if rect_w > img_w * 0.95 and rect_h > img_h * 0.95:
                        continue
                    px0, py0 = bx0 * scale_x, by0 * scale_y
                    px1, py1 = bx1 * scale_x, by1 * scale_y
                    clip = fitz.Rect(px0, py0, px1, py1)
                    text = page.get_text("text", clip=clip).strip()
                    if text and _EFTA_RE.fullmatch(text):
                        continue
                    rtype = "bad_overlay" if text else "proper_redaction"
                    area_ratio = (rect_w * rect_h) / (img_w * img_h)
                    if text:
                        conf = min(0.99, 0.70 + len(text) * 0.005 + area_ratio * 5)
                    else:
                        conf = min(0.85, 0.50 + area_ratio * 10)
                    result["redactions"].append({
                        "page": page_idx, "type": rtype,
                        "rect": (round(px0, 2), round(py0, 2), round(px1, 2), round(py1, 2)),
                        "hidden_text": text if text else None,
                        "confidence": round(conf, 4),
                    })
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        doc.close()

    return result


# ---------------------------------------------------------------------------
# Main driver
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Incremental redaction detector")
    parser.add_argument("--datasets", nargs="+", type=int, required=True,
                        help="Dataset numbers to scan (e.g., 9 11)")
    parser.add_argument("--workers", type=int, default=NUM_WORKERS,
                        help=f"Parallel workers (default: {NUM_WORKERS})")
    parser.add_argument("--batch", type=int, default=BATCH_COMMIT_SIZE,
                        help=f"Batch commit size (default: {BATCH_COMMIT_SIZE})")
    args = parser.parse_args()

    ds_label = "ds" + "+".join(str(d) for d in args.datasets)

    t_start = time.time()
    log.info("=" * 70)
    log.info("Incremental Redaction Detector — datasets %s", args.datasets)
    log.info("=" * 70)

    # Collect PDFs from requested datasets
    log.info("Collecting PDFs from datasets %s ...", args.datasets)
    all_pdfs = collect_pdfs(args.datasets)
    log.info("Found %d total PDFs", len(all_pdfs))

    if not all_pdfs:
        log.error("No PDFs found. Exiting.")
        return

    # Open existing database (non-destructive)
    conn = init_db(DB_PATH)
    cursor = conn.cursor()

    # Resume: skip already-scanned documents
    already_scanned = get_already_scanned(conn)
    log.info("Already scanned: %d documents in database", len(already_scanned))

    # Filter to only new PDFs
    new_pdfs = []
    for p in all_pdfs:
        efta = extract_efta(p)
        if efta not in already_scanned:
            new_pdfs.append(p)

    log.info("New PDFs to scan: %d (skipping %d already done)",
             len(new_pdfs), len(all_pdfs) - len(new_pdfs))

    if not new_pdfs:
        log.info("Nothing new to scan. Done.")
        conn.close()
        return

    # Counters
    total_docs = len(new_pdfs)
    docs_done = 0
    docs_with_redactions = 0
    total_redactions_found = 0
    total_bad = 0
    total_proper = 0
    total_hidden_text = 0
    errors = 0

    log.info("Starting analysis with %d workers ...", args.workers)

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(analyze_pdf, p): p for p in new_pdfs}

        for future in as_completed(futures):
            docs_done += 1
            try:
                result = future.result()
            except Exception as exc:
                errors += 1
                if docs_done % 500 == 0:
                    log.warning("Worker exception at doc %d: %s", docs_done, exc)
                continue

            if result["error"]:
                errors += 1

            pdf_path = result["pdf_path"]
            efta = result["efta_number"]
            redactions = result["redactions"]

            n_bad = 0
            n_proper = 0
            has_text = False

            for rd in redactions:
                rtype = rd["type"]
                hidden = rd["hidden_text"]
                if rtype == "bad_overlay":
                    n_bad += 1
                else:
                    n_proper += 1
                if hidden:
                    has_text = True

                cursor.execute("""
                    INSERT INTO redactions
                        (pdf_path, efta_number, page_number, redaction_type,
                         rect_x0, rect_y0, rect_x1, rect_y1,
                         hidden_text, confidence, dataset_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    pdf_path, efta, rd["page"], rtype,
                    rd["rect"][0], rd["rect"][1], rd["rect"][2], rd["rect"][3],
                    hidden, rd["confidence"], ds_label,
                ))

            n_total = len(redactions)
            cursor.execute("""
                INSERT OR REPLACE INTO document_summary
                    (pdf_path, efta_number, total_redactions,
                     bad_redactions, proper_redactions, has_recoverable_text,
                     dataset_source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (pdf_path, efta, n_total, n_bad, n_proper, has_text, ds_label))

            if n_total > 0:
                docs_with_redactions += 1
            total_redactions_found += n_total
            total_bad += n_bad
            total_proper += n_proper
            if has_text:
                total_hidden_text += 1

            if docs_done % args.batch == 0:
                conn.commit()
                elapsed = time.time() - t_start
                rate = docs_done / elapsed if elapsed > 0 else 0
                eta_s = (total_docs - docs_done) / rate if rate > 0 else 0
                log.info(
                    "Progress: %d/%d (%.1f%%) | "
                    "%d redactions (%d bad) | "
                    "%d hidden text | %d errors | "
                    "%.1f docs/s | ETA %.0fs",
                    docs_done, total_docs, 100 * docs_done / total_docs,
                    total_redactions_found, total_bad,
                    total_hidden_text, errors, rate, eta_s,
                )

    conn.commit()
    elapsed = time.time() - t_start

    log.info("=" * 70)
    log.info("COMPLETED %s in %.1f seconds (%.1f docs/s)", ds_label, elapsed,
             total_docs / max(elapsed, 1))
    log.info("=" * 70)
    log.info("New PDFs scanned:            %d", total_docs)
    log.info("PDFs with redactions:        %d", docs_with_redactions)
    log.info("Total redaction regions:     %d", total_redactions_found)
    log.info("  Bad overlays (text under): %d", total_bad)
    log.info("  Proper redactions:         %d", total_proper)
    log.info("Docs with recoverable text:  %d", total_hidden_text)
    log.info("Errors:                      %d", errors)

    # DB totals
    cursor.execute("SELECT COUNT(*) FROM redactions")
    log.info("DB total redactions: %d", cursor.fetchone()[0])
    cursor.execute("SELECT COUNT(*) FROM document_summary")
    log.info("DB total documents:  %d", cursor.fetchone()[0])

    conn.close()
    log.info("Done.")


if __name__ == "__main__":
    main()
