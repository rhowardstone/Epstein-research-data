#!/usr/bin/env python3
"""
Download the 31 EFTA documents that exist on the DOJ server but are
missing from the archive.org bulk download, extract their text, and
add them to full_text_corpus.db.
"""

import os
import sqlite3
import subprocess
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF not installed. Run: pip install PyMuPDF")
    sys.exit(1)

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

_DATA_DIR = _find_data_dir()
DB_PATH = os.path.join(_DATA_DIR, "full_text_corpus.db")
BASE_DIR = os.path.join(_DATA_DIR, "datasets")

# 31 EFTAs available on DOJ but missing from local corpus
RECOVERABLE = {
    # DS1
    "EFTA00000467": 1,
    "EFTA00000468": 1,
    # DS8
    "EFTA00009781": 8,
    # DS9
    "EFTA00593870": 9, "EFTA00597207": 9, "EFTA00645624": 9,
    "EFTA00709804": 9, "EFTA00709805": 9, "EFTA00709806": 9, "EFTA00709807": 9,
    "EFTA00770595": 9, "EFTA00774768": 9,
    "EFTA00823190": 9, "EFTA00823191": 9, "EFTA00823192": 9,
    "EFTA00823221": 9, "EFTA00823319": 9,
    "EFTA00877475": 9, "EFTA00892252": 9, "EFTA00901740": 9, "EFTA00912980": 9,
    "EFTA00919433": 9, "EFTA00919434": 9,
    "EFTA00932520": 9, "EFTA00932521": 9, "EFTA00932522": 9, "EFTA00932523": 9,
    "EFTA01135215": 9, "EFTA01135708": 9, "EFTA01175426": 9, "EFTA01220934": 9,
}

def get_download_dir(dataset):
    """Get the directory to store recovered PDFs."""
    recover_dir = os.path.join(BASE_DIR, f"dataset{dataset}", "recovered")
    os.makedirs(recover_dir, exist_ok=True)
    return recover_dir

def download_pdf(efta, dataset):
    """Download a PDF from the DOJ server."""
    url = f"https://www.justice.gov/epstein/files/DataSet%20{dataset}/{efta}.pdf"
    out_dir = get_download_dir(dataset)
    out_path = os.path.join(out_dir, f"{efta}.pdf")

    if os.path.exists(out_path):
        print(f"  Already downloaded: {out_path}")
        return out_path

    result = subprocess.run(
        ["curl", "-s", "-b", "justiceGovAgeVerified=true", "-o", out_path, "-w", "%{http_code}", url],
        capture_output=True, text=True
    )
    code = result.stdout.strip()
    if code == "200" and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        size = os.path.getsize(out_path)
        print(f"  Downloaded: {efta}.pdf ({size:,} bytes)")
        return out_path
    else:
        print(f"  FAILED: {efta} HTTP {code}")
        if os.path.exists(out_path):
            os.remove(out_path)
        return None

def extract_and_insert(efta, dataset, pdf_path, conn):
    """Extract text from PDF and insert into full_text_corpus.db."""
    cur = conn.cursor()

    # Check if already in DB
    cur.execute("SELECT id FROM documents WHERE efta_number = ?", (efta,))
    if cur.fetchone():
        print(f"  Already in DB: {efta}")
        return False

    try:
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        file_size = os.path.getsize(pdf_path)

        # Insert document record
        cur.execute("""
            INSERT INTO documents (efta_number, dataset, file_path, total_pages, extraction_timestamp, file_size)
            VALUES (?, ?, ?, ?, datetime('now'), ?)
        """, (efta, dataset, pdf_path, total_pages, file_size))

        # Extract and insert page text
        for page_num in range(total_pages):
            page = doc[page_num]
            text = page.get_text()
            char_count = len(text)
            cur.execute("""
                INSERT INTO pages (efta_number, page_number, text_content, char_count)
                VALUES (?, ?, ?, ?)
            """, (efta, page_num + 1, text, char_count))

        doc.close()
        conn.commit()
        print(f"  Inserted: {efta} ({total_pages} pages, {file_size:,} bytes)")
        return True

    except Exception as e:
        print(f"  ERROR extracting {efta}: {e}")
        conn.rollback()
        return False

def main():
    print("=" * 60)
    print("RECOVERING 31 MISSING EFTA DOCUMENTS FROM DOJ SERVER")
    print("=" * 60)
    print()

    conn = sqlite3.connect(DB_PATH)

    downloaded = 0
    inserted = 0
    failed = 0

    for efta in sorted(RECOVERABLE.keys()):
        dataset = RECOVERABLE[efta]
        print(f"\n[{efta}] Dataset {dataset}")

        pdf_path = download_pdf(efta, dataset)
        if pdf_path:
            downloaded += 1
            if extract_and_insert(efta, dataset, pdf_path, conn):
                inserted += 1
        else:
            failed += 1

    conn.close()

    print()
    print("=" * 60)
    print(f"RESULTS: {downloaded} downloaded, {inserted} inserted into DB, {failed} failed")
    print("=" * 60)

if __name__ == "__main__":
    main()
