#!/usr/bin/env python3
"""
Ingest House Oversight Committee Estate Documents into full_text_corpus.db

Handles 6 collections with different formats:
1. DOJ First Production: Concordance (DOJ-OGR Bates), page images + natives
2. Seventh Production (Estate): Concordance (HOUSE_OVERSIGHT Bates), TEXT + IMAGES + NATIVES
3. Estate First Production: 4 PDFs
4. USVI Production: photos + videos + PDFs
5. Estate Production Dec 11: 92 photos
6. Release Dec 18: 68 photos

For page images (JPG/TIF), we run OCR to extract text.
For PDFs, we use PyMuPDF.
For pre-extracted text files, we ingest directly.

Uses multiprocessing for OCR on 180 workers.
"""

import argparse
import multiprocessing as mp
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None
    print("WARNING: PyMuPDF not available, PDF extraction disabled")

try:
    from PIL import Image
    import pytesseract
    HAS_OCR = True
except ImportError:
    HAS_OCR = False
    print("WARNING: pytesseract/Pillow not available, OCR disabled")

def _find_data_dir():
    """Find the directory containing the database files."""
    if os.environ.get("EPSTEIN_DATA_DIR"):
        return os.environ["EPSTEIN_DATA_DIR"]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(repo_root, "full_text_corpus.db")):
        return repo_root
    if os.path.exists(os.path.join(os.getcwd(), "full_text_corpus.db")):
        return os.getcwd()
    parent = os.path.dirname(os.getcwd())
    for name in os.listdir(parent):
        candidate = os.path.join(parent, name, "full_text_corpus.db")
        if os.path.exists(candidate):
            return os.path.join(parent, name)
    return os.getcwd()

_DATA_DIR = Path(_find_data_dir())
DB_PATH = _DATA_DIR / "full_text_corpus.db"
BASE = _DATA_DIR / "house_estate_extracted"
DATASET_NUM = 99  # House Estate = dataset 99

# Batch sizes
BATCH_SIZE = 500
PROGRESS_INTERVAL = 1000
MAX_WORKERS = int(os.environ.get("OCR_WORKERS", 47))  # default 47 (190 cores / 4 threads per tesseract)


def init_db(db_path):
    """Open DB connection with performance pragmas."""
    conn = sqlite3.connect(str(db_path), timeout=120)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-512000")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=2147483648")
    return conn


def get_existing(conn):
    """Get set of already-ingested document IDs."""
    return {row[0] for row in conn.execute("SELECT efta_number FROM documents").fetchall()}


def extract_pdf_text(pdf_path):
    """Extract text from PDF using PyMuPDF. Returns list of (page_num, text)."""
    if fitz is None:
        return []
    try:
        doc = fitz.open(str(pdf_path))
        pages = []
        for i in range(len(doc)):
            text = doc[i].get_text()
            pages.append((i, text))
        doc.close()
        return pages
    except Exception as e:
        return [(-1, f"ERROR: {e}")]


def ocr_image(image_path):
    """OCR a single image file. Returns text string."""
    if not HAS_OCR:
        return ""
    try:
        img = Image.open(str(image_path))
        text = pytesseract.image_to_string(img)
        return text
    except Exception as e:
        return f"OCR_ERROR: {e}"


def ocr_worker(args):
    """Worker function for multiprocessing OCR."""
    image_path, bates_id = args
    text = ocr_image(image_path)
    return (bates_id, text, len(text))


# ─── Collection 1: DOJ First Production ────────────────────────────

def ingest_doj_first_production(conn, existing):
    """Ingest DOJ-OGR Concordance production."""
    prod_dir = BASE / "doj_first_production" / "DOJ Epstein Files - First Production" / "Prod 01_20250822"
    dat_file = prod_dir / "20250822.dat"
    opt_file = prod_dir / "20250822.opt"
    images_dir = prod_dir / "VOL00001" / "IMAGES"

    if not dat_file.exists():
        print("  DAT file not found, skipping")
        return 0

    # Parse DAT for document metadata
    with open(dat_file, 'r', encoding='latin-1') as f:
        lines = f.readlines()

    header = lines[0].replace('þ', '').split(chr(0x14))
    beg_idx = header.index('Prod Beg')
    end_idx = header.index('Prod End')
    fn_idx = header.index('Filename')

    docs = {}
    for line in lines[1:]:
        fields = line.replace('þ', '').split(chr(0x14))
        beg = fields[beg_idx].strip()
        end = fields[end_idx].strip()
        fn = fields[fn_idx].strip()
        if beg:
            beg_num = int(beg.replace('DOJ-OGR-', ''))
            end_num = int(end.replace('DOJ-OGR-', ''))
            pages = end_num - beg_num + 1
            docs[beg] = {'end': end, 'filename': fn, 'pages': pages}

    # Parse OPT for page-to-image mapping
    page_images = {}  # bates_id → image_path
    with open(opt_file, 'r', encoding='latin-1') as f:
        for line in f:
            parts = line.strip().split(',')
            if len(parts) >= 3:
                bates = parts[0].strip()
                img_path = parts[2].strip().replace('\\', '/')
                # Resolve relative path
                full_path = prod_dir / img_path.lstrip('./')
                if full_path.exists():
                    page_images[bates] = full_path

    print(f"  {len(docs)} documents, {len(page_images)} page images")

    # First pass: ingest using OCR on page images
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    skipped = 0

    # Build OCR work items
    ocr_items = []
    for bates_id in sorted(page_images.keys()):
        doc_id = f"DOJ-OGR-{bates_id.replace('DOJ-OGR-', '')}"
        if doc_id in existing:
            continue
        ocr_items.append((page_images[bates_id], bates_id))

    # Filter to only pages not yet processed
    already = set()
    for doc_beg, info in docs.items():
        if doc_beg in existing:
            already.add(doc_beg)
            skipped += 1

    new_docs = {k: v for k, v in docs.items() if k not in existing}
    print(f"  {len(new_docs)} new documents to process ({skipped} already in DB)")

    if not new_docs:
        return 0

    # OCR all page images in parallel
    print(f"  OCR-ing {len(ocr_items)} page images with {min(MAX_WORKERS, len(ocr_items))} workers...")
    page_texts = {}  # bates_id → text

    if ocr_items and HAS_OCR:
        workers = min(MAX_WORKERS, len(ocr_items))
        with mp.Pool(workers) as pool:
            for i, (bates_id, text, char_count) in enumerate(pool.imap_unordered(ocr_worker, ocr_items, chunksize=50)):
                page_texts[bates_id] = (text, char_count)
                if (i + 1) % PROGRESS_INTERVAL == 0:
                    print(f"    OCR progress: {i+1}/{len(ocr_items)}")
        print(f"    OCR complete: {len(page_texts)} pages")
    elif not HAS_OCR:
        print("  Skipping OCR (not available)")

    # Insert documents and pages
    for doc_beg, info in sorted(new_docs.items()):
        beg_num = int(doc_beg.replace('DOJ-OGR-', ''))
        end_num = int(info['end'].replace('DOJ-OGR-', ''))

        conn.execute(
            "INSERT OR IGNORE INTO documents (efta_number, dataset, file_path, total_pages, extraction_timestamp, file_size) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (doc_beg, DATASET_NUM, info['filename'], info['pages'], now, 0)
        )

        # Insert each page
        for page_num in range(info['pages']):
            page_bates = f"DOJ-OGR-{beg_num + page_num:08d}"
            text, char_count = page_texts.get(page_bates, ("", 0))
            conn.execute(
                "INSERT OR IGNORE INTO pages (efta_number, page_number, text_content, char_count) "
                "VALUES (?, ?, ?, ?)",
                (doc_beg, page_num, text, char_count)
            )

        inserted += 1
        if inserted % BATCH_SIZE == 0:
            conn.commit()
            print(f"    Inserted {inserted} documents...")

    conn.commit()
    return inserted


# ─── Collection 2: Seventh Production ──────────────────────────────

def ingest_seventh_production(conn, existing):
    """Ingest Seventh Production from pre-extracted text files."""
    prod_dir = BASE / "seventh_production" / "Epstein Estate Documents - Seventh Production"
    text_dirs = sorted((prod_dir / "TEXT").glob("*")) if (prod_dir / "TEXT").exists() else []

    # Also parse DAT for metadata
    dat_file = prod_dir / "DATA" / "HOUSE_OVERSIGHT_009.dat"
    metadata = {}
    if dat_file.exists():
        with open(dat_file, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        lines = content.strip().split('\n')
        header = lines[0].replace('þ', '').split(chr(0x14))
        beg_idx = header.index('Bates Begin')
        pages_idx = header.index('Pages')
        for line in lines[1:]:
            fields = line.replace('þ', '').split(chr(0x14))
            beg = fields[beg_idx].strip()
            pages = fields[pages_idx].strip()
            if beg:
                metadata[beg] = int(pages) if pages else 1

    # Collect all text files
    text_files = []
    for td in text_dirs:
        if td.is_dir():
            text_files.extend(sorted(td.glob("*.txt")))

    print(f"  {len(text_files)} text files, {len(metadata)} metadata entries")

    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    skipped = 0

    for tf in text_files:
        efta = tf.stem  # HOUSE_OVERSIGHT_010477
        if efta in existing:
            skipped += 1
            continue

        text = tf.read_text(encoding='utf-8', errors='replace')
        total_pages = metadata.get(efta, 1)

        conn.execute(
            "INSERT OR IGNORE INTO documents (efta_number, dataset, file_path, total_pages, extraction_timestamp, file_size) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (efta, DATASET_NUM, str(tf), total_pages, now, tf.stat().st_size)
        )
        conn.execute(
            "INSERT OR IGNORE INTO pages (efta_number, page_number, text_content, char_count) "
            "VALUES (?, ?, ?, ?)",
            (efta, 0, text, len(text))
        )

        inserted += 1
        if inserted % BATCH_SIZE == 0:
            conn.commit()
            print(f"    Inserted {inserted} documents...")

    conn.commit()
    print(f"  Inserted {inserted}, skipped {skipped}")
    return inserted


# ─── Collection 3: Estate First Production (4 PDFs) ───────────────

def ingest_estate_first_production(conn, existing):
    """Ingest 4 PDFs from Estate First Production."""
    prod_dir = BASE / "estate_first_production" / "Epstein Estate Documents - First Production"
    if not prod_dir.exists():
        print("  Directory not found")
        return 0

    pdfs = sorted(prod_dir.glob("*.pdf")) + sorted(prod_dir.glob("*.PDF"))
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0

    for pdf in pdfs:
        stem = pdf.stem
        req_match = re.search(r'\d+', stem)
        efta = f"HOUSE_OVERSIGHT_REQ{req_match.group()}" if req_match else f"HOUSE_OVERSIGHT_{stem.replace(' ', '_')}"

        if efta in existing:
            continue

        pages = extract_pdf_text(pdf)
        if pages and pages[0][0] == -1:
            # Error
            conn.execute(
                "INSERT OR IGNORE INTO documents (efta_number, dataset, file_path, total_pages, extraction_timestamp, file_size, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (efta, DATASET_NUM, str(pdf), 0, now, pdf.stat().st_size, pages[0][1])
            )
        else:
            conn.execute(
                "INSERT OR IGNORE INTO documents (efta_number, dataset, file_path, total_pages, extraction_timestamp, file_size) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (efta, DATASET_NUM, str(pdf), len(pages), now, pdf.stat().st_size)
            )
            for page_num, text in pages:
                conn.execute(
                    "INSERT OR IGNORE INTO pages (efta_number, page_number, text_content, char_count) "
                    "VALUES (?, ?, ?, ?)",
                    (efta, page_num, text, len(text))
                )
            inserted += 1
            print(f"    {efta}: {len(pages)} pages")

    conn.commit()
    return inserted


# ─── Collections 4-6: Photo/Video collections ─────────────────────

def ingest_photo_collection(conn, existing, name, subdir):
    """Ingest a photo/video collection. OCR photos, register videos."""
    coll_dir = BASE / subdir
    if not coll_dir.exists():
        print(f"  Directory not found: {coll_dir}")
        return 0

    files = sorted(coll_dir.iterdir())
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0

    # Separate photos, videos, and PDFs
    photos = [f for f in files if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.cr2', '.tif', '.tiff')]
    videos = [f for f in files if f.suffix.lower() in ('.mp4', '.mp3', '.wav', '.mov', '.avi')]
    pdfs = [f for f in files if f.suffix.lower() == '.pdf']

    # PDFs via PyMuPDF
    for pdf in pdfs:
        efta = f"HOUSE_ESTATE_{name}_{pdf.stem}"
        if efta in existing:
            continue
        pages = extract_pdf_text(pdf)
        if pages and pages[0][0] != -1:
            conn.execute(
                "INSERT OR IGNORE INTO documents (efta_number, dataset, file_path, total_pages, extraction_timestamp, file_size) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (efta, DATASET_NUM, str(pdf), len(pages), now, pdf.stat().st_size)
            )
            for pn, text in pages:
                conn.execute(
                    "INSERT OR IGNORE INTO pages (efta_number, page_number, text_content, char_count) "
                    "VALUES (?, ?, ?, ?)",
                    (efta, pn, text, len(text))
                )
            inserted += 1

    # Photos: extract Bates number from filename if present, otherwise use filename
    photo_items = []
    for photo in photos:
        # Try to extract HOUSE_OVERSIGHT_NNNNNN from filename
        match = re.search(r'HOUSE_OVERSIGHT_(\d+)', photo.stem)
        if match:
            efta = f"HOUSE_OVERSIGHT_{match.group(1)}"
        else:
            efta = f"HOUSE_ESTATE_{name}_{photo.stem}"

        if efta in existing:
            continue
        photo_items.append((str(photo), efta))

    if photo_items and HAS_OCR:
        print(f"  OCR-ing {len(photo_items)} photos...")
        workers = min(180, len(photo_items))
        with mp.Pool(workers) as pool:
            for i, (bates_id, text, char_count) in enumerate(pool.imap_unordered(ocr_worker, photo_items, chunksize=10)):
                conn.execute(
                    "INSERT OR IGNORE INTO documents (efta_number, dataset, file_path, total_pages, extraction_timestamp, file_size) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (bates_id, DATASET_NUM, str(photo_items[0][0]), 1, now, 0)
                )
                conn.execute(
                    "INSERT OR IGNORE INTO pages (efta_number, page_number, text_content, char_count) "
                    "VALUES (?, ?, ?, ?)",
                    (bates_id, 0, text, char_count)
                )
                inserted += 1
                if (i + 1) % 50 == 0:
                    conn.commit()
                    print(f"    {i+1}/{len(photo_items)} photos OCR'd")

    # Videos: register as documents with no text (transcription is separate)
    for video in videos:
        match = re.search(r'HOUSE_OVERSIGHT_(\d+)', video.stem)
        if match:
            efta = f"HOUSE_OVERSIGHT_{match.group(1)}"
        else:
            efta = f"HOUSE_ESTATE_{name}_{video.stem}"

        if efta in existing:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO documents (efta_number, dataset, file_path, total_pages, extraction_timestamp, file_size) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (efta, DATASET_NUM, str(video), 0, now, video.stat().st_size)
        )
        inserted += 1

    conn.commit()
    return inserted


def main():
    print("=" * 70)
    print("HOUSE ESTATE FULL PIPELINE INGESTION")
    print("=" * 70)

    conn = init_db(DB_PATH)
    existing = get_existing(conn)
    before = len(existing)
    print(f"Corpus before: {before:,} documents")
    print()

    total_inserted = 0

    # 1. Seventh Production (text files — fast, no OCR needed)
    print("─── 1. Seventh Production (pre-extracted text) ───")
    n = ingest_seventh_production(conn, existing)
    existing = get_existing(conn)
    total_inserted += n
    print(f"  → {n} documents inserted")
    print()

    # 2. Estate First Production (4 PDFs)
    print("─── 2. Estate First Production (4 PDFs) ───")
    n = ingest_estate_first_production(conn, existing)
    existing = get_existing(conn)
    total_inserted += n
    print(f"  → {n} documents inserted")
    print()

    # 3. DOJ First Production (OCR-heavy — 33K page images)
    print("─── 3. DOJ First Production (33K page images, OCR) ───")
    n = ingest_doj_first_production(conn, existing)
    existing = get_existing(conn)
    total_inserted += n
    print(f"  → {n} documents inserted")
    print()

    # 4. USVI Production (photos + videos + PDFs)
    print("─── 4. USVI Production ───")
    n = ingest_photo_collection(conn, existing, "USVI", "usvi_production")
    existing = get_existing(conn)
    total_inserted += n
    print(f"  → {n} documents inserted")
    print()

    # 5. Estate Production Dec 11 (92 photos)
    print("─── 5. Estate Production Dec 11 (92 photos) ───")
    n = ingest_photo_collection(conn, existing, "DEC11", "estate_production_dec11")
    existing = get_existing(conn)
    total_inserted += n
    print(f"  → {n} documents inserted")
    print()

    # 6. Release Dec 18 (68 photos)
    print("─── 6. Release Dec 18 (68 photos) ───")
    n = ingest_photo_collection(conn, existing, "DEC18", "release_dec18")
    existing = get_existing(conn)
    total_inserted += n
    print(f"  → {n} documents inserted")
    print()

    # Final stats
    after_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    after_pages = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    house_docs = conn.execute("SELECT COUNT(*) FROM documents WHERE dataset = ?", (DATASET_NUM,)).fetchone()[0]

    print("=" * 70)
    print(f"RESULTS:")
    print(f"  Total inserted: {total_inserted:,} documents")
    print(f"  Corpus now: {after_docs:,} documents, {after_pages:,} pages")
    print(f"  House Estate: {house_docs:,} documents (dataset={DATASET_NUM})")
    print("=" * 70)

    conn.close()


if __name__ == "__main__":
    main()
