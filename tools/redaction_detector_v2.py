#!/usr/bin/env python3
"""
Redaction Detector v2 - PDF-level redaction analysis for Epstein files.

Scans PDFs directly using PyMuPDF to detect:
1. Black filled rectangles in scanned images (redaction bars)
2. OCR text recoverable under those black regions ("bad" redactions)
3. PDF redaction annotations
4. Black overlay drawings in the PDF content stream

Outputs results to redaction_analysis_v2.db with per-redaction and per-document tables.
"""

import fitz
import numpy as np
import os
import re
import glob
import sqlite3
import logging
import time
import sys
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

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
LOG_PATH = os.path.join(BASE_DIR, "redaction_detector_v2.log")
DATASETS = [1, 2, 3, 4, 5, 6, 7, 8, 11, 12]

# Detection thresholds
BLACK_PIXEL_THRESHOLD = 30       # pixel value below which we consider it "black"
MIN_RECT_WIDTH_PX = 30           # minimum width of a black rectangle in image pixels
MIN_RECT_HEIGHT_PX = 5           # minimum height of a black rectangle in image pixels
ROW_BLACK_RATIO = 0.15           # fraction of row that must be black to count
COL_BLACK_RATIO = 0.5            # fraction of column within a band that must be black
BATCH_COMMIT_SIZE = 200          # commit to DB every N documents
NUM_WORKERS = 8                  # parallel workers

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("redaction_detector_v2")


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------
def init_db(db_path: str) -> sqlite3.Connection:
    """Create or open the database with the required schema."""
    conn = sqlite3.connect(db_path)
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
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# PDF collection
# ---------------------------------------------------------------------------
def collect_pdfs() -> list[str]:
    """Find all PDF files across the configured datasets."""
    pdfs: list[str] = []
    for ds in DATASETS:
        ds_dir = os.path.join(BASE_DIR, "datasets", f"dataset{ds}")
        if not os.path.isdir(ds_dir):
            log.warning("Dataset directory not found: %s", ds_dir)
            continue
        found = glob.glob(os.path.join(ds_dir, "**", "*.pdf"), recursive=True)
        pdfs.extend(found)
    return sorted(set(pdfs))


# ---------------------------------------------------------------------------
# EFTA extraction
# ---------------------------------------------------------------------------
_EFTA_RE = re.compile(r"(EFTA\d{8,})")

def extract_efta(pdf_path: str) -> str:
    """Extract the EFTA number from the filename."""
    basename = os.path.basename(pdf_path).replace(".pdf", "")
    m = _EFTA_RE.search(basename)
    return m.group(1) if m else basename


# ---------------------------------------------------------------------------
# Black-rectangle detection in embedded images
# ---------------------------------------------------------------------------
def find_black_rects_in_image(pix: fitz.Pixmap,
                               min_w: int = MIN_RECT_WIDTH_PX,
                               min_h: int = MIN_RECT_HEIGHT_PX):
    """
    Detect rectangular black regions in a Pixmap.

    Returns a list of (x0, y0, x1, y1) tuples in image-pixel coordinates.
    """
    # Convert pixmap to numpy grayscale array
    if pix.n == 4:  # CMYK or RGBA
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
        gray = np.min(arr, axis=2)  # use min channel: if any channel is bright, not black

    is_black = gray < BLACK_PIXEL_THRESHOLD

    # --- Find horizontal bands of largely-black rows ---
    row_ratio = np.mean(is_black, axis=1)
    black_rows = np.where(row_ratio > ROW_BLACK_RATIO)[0]

    if len(black_rows) == 0:
        return []

    # Group into contiguous bands (allowing small gaps of up to 3 rows)
    bands: list[tuple[int, int]] = []
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

    # --- Within each band, find horizontal extents ---
    rects: list[tuple[int, int, int, int]] = []
    for y0, y1 in bands:
        band_mask = is_black[y0:y1, :]
        col_ratio = np.mean(band_mask, axis=0)
        black_cols = np.where(col_ratio > COL_BLACK_RATIO)[0]
        if len(black_cols) < min_w:
            continue

        # Group contiguous column ranges
        segments: list[tuple[int, int]] = []
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

    # --- Merge overlapping / touching rectangles ---
    merged = _merge_rects(rects)
    return merged


def _merge_rects(rects: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Merge overlapping or adjacent rectangles."""
    if not rects:
        return []
    rects = sorted(rects, key=lambda r: (r[1], r[0]))
    merged = [list(rects[0])]
    for r in rects[1:]:
        m = merged[-1]
        # Overlap or adjacent (within 5px)?
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
# Single-PDF analysis (runs in worker process)
# ---------------------------------------------------------------------------
def analyze_pdf(pdf_path: str) -> dict:
    """
    Analyze one PDF for redactions.

    Returns a dict with:
        pdf_path, efta_number, redactions (list of dicts), error (str or None)
    """
    efta = extract_efta(pdf_path)
    result = {
        "pdf_path": pdf_path,
        "efta_number": efta,
        "redactions": [],
        "error": None,
    }

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        result["error"] = str(exc)
        return result

    try:
        for page_idx in range(doc.page_count):
            page = doc[page_idx]
            page_rect = page.rect

            # ---- 1. Check for PDF-level redaction annotations ----
            annots = page.annots()
            if annots:
                for annot in annots:
                    if annot.type[0] == 12:  # Redact annotation type
                        r = annot.rect
                        text = page.get_text("text", clip=r).strip()
                        result["redactions"].append({
                            "page": page_idx,
                            "type": "annotation",
                            "rect": (r.x0, r.y0, r.x1, r.y1),
                            "hidden_text": text if text else None,
                            "confidence": 0.95,
                        })

            # ---- 2. Check for black filled drawings in PDF stream ----
            drawings = page.get_drawings()
            for d in drawings:
                fill = d.get("fill")
                if fill is None:
                    continue
                # Check for black or near-black fill
                if isinstance(fill, tuple) and all(c < 0.15 for c in fill):
                    r = fitz.Rect(d["rect"])
                    # Skip tiny or page-sized rectangles
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

            # ---- 3. Check embedded images for black rectangles ----
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
                    continue  # skip tiny images (icons, etc.)

                black_rects = find_black_rects_in_image(pix)
                if not black_rects:
                    continue

                # Determine the image placement on the page to map coordinates
                # For full-page scans the image usually fills the page
                scale_x = page_rect.width / img_w
                scale_y = page_rect.height / img_h

                for bx0, by0, bx1, by1 in black_rects:
                    # Skip rectangles that are too large (probably page background)
                    rect_w = bx1 - bx0
                    rect_h = by1 - by0
                    if rect_w > img_w * 0.95 and rect_h > img_h * 0.95:
                        continue

                    # Map to page coordinates
                    px0 = bx0 * scale_x
                    py0 = by0 * scale_y
                    px1 = bx1 * scale_x
                    py1 = by1 * scale_y
                    clip = fitz.Rect(px0, py0, px1, py1)

                    # Check for hidden text in the OCR layer
                    text = page.get_text("text", clip=clip).strip()

                    # Filter out single-character noise and EFTA page numbers
                    if text and _EFTA_RE.fullmatch(text):
                        continue  # just the EFTA stamp

                    rtype = "bad_overlay" if text else "proper_redaction"

                    # Confidence scoring
                    area_ratio = (rect_w * rect_h) / (img_w * img_h)
                    if text:
                        conf = min(0.99, 0.70 + len(text) * 0.005 + area_ratio * 5)
                    else:
                        conf = min(0.85, 0.50 + area_ratio * 10)

                    result["redactions"].append({
                        "page": page_idx,
                        "type": rtype,
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
    t_start = time.time()
    log.info("=" * 70)
    log.info("Redaction Detector v2 starting")
    log.info("=" * 70)

    # Collect PDFs
    log.info("Collecting PDF paths from %d datasets ...", len(DATASETS))
    all_pdfs = collect_pdfs()
    log.info("Found %d PDF files", len(all_pdfs))

    if not all_pdfs:
        log.error("No PDFs found. Exiting.")
        return

    # Initialize database (remove old one if exists for clean run)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        log.info("Removed old database at %s", DB_PATH)
    conn = init_db(DB_PATH)
    cursor = conn.cursor()

    # Counters
    total_docs = len(all_pdfs)
    docs_done = 0
    docs_with_redactions = 0
    total_redactions_found = 0
    total_bad = 0
    total_proper = 0
    total_hidden_text = 0
    errors = 0

    log.info("Starting analysis with %d workers ...", NUM_WORKERS)

    # Process in parallel
    with ProcessPoolExecutor(max_workers=NUM_WORKERS) as pool:
        futures = {pool.submit(analyze_pdf, p): p for p in all_pdfs}

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
                         hidden_text, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    pdf_path, efta, rd["page"], rtype,
                    rd["rect"][0], rd["rect"][1], rd["rect"][2], rd["rect"][3],
                    hidden, rd["confidence"],
                ))

            n_total = len(redactions)
            cursor.execute("""
                INSERT OR REPLACE INTO document_summary
                    (pdf_path, efta_number, total_redactions,
                     bad_redactions, proper_redactions, has_recoverable_text)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (pdf_path, efta, n_total, n_bad, n_proper, has_text))

            if n_total > 0:
                docs_with_redactions += 1
            total_redactions_found += n_total
            total_bad += n_bad
            total_proper += n_proper
            if has_text:
                total_hidden_text += 1

            # Periodic commit and progress
            if docs_done % BATCH_COMMIT_SIZE == 0:
                conn.commit()
                elapsed = time.time() - t_start
                rate = docs_done / elapsed if elapsed > 0 else 0
                eta_s = (total_docs - docs_done) / rate if rate > 0 else 0
                log.info(
                    "Progress: %d/%d docs (%.1f%%) | "
                    "%d redactions (%d bad, %d proper) | "
                    "%d docs with hidden text | "
                    "%d errors | "
                    "%.1f docs/s | ETA %.0fs",
                    docs_done, total_docs,
                    100 * docs_done / total_docs,
                    total_redactions_found, total_bad, total_proper,
                    total_hidden_text, errors,
                    rate, eta_s,
                )

    # Final commit
    conn.commit()
    elapsed = time.time() - t_start

    log.info("=" * 70)
    log.info("COMPLETED in %.1f seconds (%.1f docs/s)", elapsed, total_docs / elapsed)
    log.info("=" * 70)
    log.info("Total PDFs scanned:          %d", total_docs)
    log.info("PDFs with redactions:        %d", docs_with_redactions)
    log.info("Total redaction regions:     %d", total_redactions_found)
    log.info("  Bad overlays (text under): %d", total_bad)
    log.info("  Proper redactions:         %d", total_proper)
    log.info("Docs with recoverable text:  %d", total_hidden_text)
    log.info("Errors:                      %d", errors)
    log.info("Database: %s", DB_PATH)

    # Print summary query
    cursor.execute("SELECT COUNT(*) FROM redactions")
    log.info("DB redactions rows: %d", cursor.fetchone()[0])
    cursor.execute("SELECT COUNT(*) FROM document_summary")
    log.info("DB document_summary rows: %d", cursor.fetchone()[0])
    cursor.execute("""
        SELECT redaction_type, COUNT(*) FROM redactions GROUP BY redaction_type
    """)
    for row in cursor.fetchall():
        log.info("  Type %-20s: %d", row[0], row[1])
    cursor.execute("""
        SELECT COUNT(*) FROM redactions WHERE hidden_text IS NOT NULL AND hidden_text != ''
    """)
    log.info("Redactions with hidden text:  %d", cursor.fetchone()[0])

    # Top documents by hidden text count
    cursor.execute("""
        SELECT efta_number, bad_redactions, total_redactions
        FROM document_summary
        WHERE bad_redactions > 0
        ORDER BY bad_redactions DESC
        LIMIT 20
    """)
    log.info("Top 20 documents with bad redactions:")
    for row in cursor.fetchall():
        log.info("  %-18s bad=%d total=%d", row[0], row[1], row[2])

    conn.close()
    log.info("Done.")


if __name__ == "__main__":
    main()
