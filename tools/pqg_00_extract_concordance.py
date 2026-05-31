#!/usr/bin/env python3
"""
PQG Phase 0: Complete Concordance Metadata Extraction

Parses ALL concordance files (DAT + OPT) across all datasets and House Estate
productions, extracts EVERY field, stores in a unified database, cross-references
between datasets, detects duplicates.

Input files:
  - concordance_files/datasets__DS01-12__VOL*.DAT (12 files, 2 fields each)
  - concordance_files/datasets__DS01-12__VOL*.OPT (12 files, 7 columns each)
  - datasets/house_estate/HOUSE_OVERSIGHT_009.dat (28 fields, 2,897 records)
  - datasets/house_estate/HOUSE_OVERSIGHT_009.opt (23,124 page entries)
  - epstein_files/house_estate_extracted/.../20250822.dat (4 fields, 1,658 records)
  - epstein_files/house_estate_extracted/.../20250822.opt (33,295 page entries)

Output:
  - epstein_files/concordance_complete.db (unified database)
  - epstein_files/CONCORDANCE_COMPLETE.csv (flat export)
"""

import sqlite3
import csv
import os
import sys
import json
import hashlib
from collections import defaultdict, Counter
from datetime import datetime

def _find_base_dir():
    """Find the parent directory containing epstein_files/ and concordance_files/."""
    if os.environ.get("EPSTEIN_DATA_DIR"):
        candidate = os.environ["EPSTEIN_DATA_DIR"]
        # EPSTEIN_DATA_DIR may point to epstein_files/ itself or its parent
        if os.path.basename(candidate) == "epstein_files":
            return os.path.dirname(candidate)
        if os.path.exists(os.path.join(candidate, "epstein_files")):
            return candidate
        return candidate
    # Check if repo root's parent has epstein_files/
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parent = os.path.dirname(repo_root)
    if os.path.exists(os.path.join(parent, "epstein_files", "full_text_corpus.db")):
        return parent
    # Check cwd parent
    cwd_parent = os.path.dirname(os.getcwd())
    if os.path.exists(os.path.join(cwd_parent, "epstein_files", "full_text_corpus.db")):
        return cwd_parent
    # Check if cwd itself contains epstein_files/
    if os.path.exists(os.path.join(os.getcwd(), "epstein_files", "full_text_corpus.db")):
        return os.getcwd()
    return os.getcwd()

BASE_DIR = _find_base_dir()
CONCORDANCE_DIR = os.path.join(BASE_DIR, "concordance_files")
HOUSE_ESTATE_DIR = os.path.join(BASE_DIR, "datasets/house_estate")
DOJ_FIRST_PROD_DIR = os.path.join(
    BASE_DIR,
    "epstein_files/house_estate_extracted/doj_first_production/"
    "DOJ Epstein Files - First Production/Prod 01_20250822"
)
OUTPUT_DB = os.path.join(BASE_DIR, "epstein_files/concordance_complete.db")
OUTPUT_CSV = os.path.join(BASE_DIR, "epstein_files/CONCORDANCE_COMPLETE.csv")
CORPUS_DB = os.path.join(BASE_DIR, "epstein_files/full_text_corpus.db")

# Dataset DAT/OPT file mapping
DS_FILES = {
    "DS01": ("datasets__DS01__VOL00001", "VOL00001"),
    "DS02": ("datasets__DS02__VOL00002", "VOL00002"),
    "DS03": ("datasets__DS03__VOL00003", "VOL00003"),
    "DS04": ("datasets__DS04__VOL00004", "VOL00004"),
    "DS05": ("datasets__DS05__VOL00005", "VOL00005"),
    "DS06": ("datasets__DS06__VOL00006", "VOL00006"),
    "DS07": ("datasets__DS07__VOL00007", "VOL00007"),
    "DS08": ("datasets__DS08__VOL00008", "VOL00008"),
    "DS09": ("datasets__DS09__VOL00009", "VOL00009"),
    "DS10": ("datasets__DS10__VOL00010", "VOL00010"),
    "DS11": ("datasets__DS11__VOL00011", "VOL00011"),
    "DS12": ("DataSet12__VOL00012", "VOL00012"),
}

# House Estate field names (28 fields) — order matches DAT header
HOUSE_ESTATE_FIELDS = [
    "bates_begin", "bates_end", "bates_begin_attach", "bates_end_attach",
    "attachment_document", "pages", "author", "custodian",
    "date_created", "date_last_modified", "date_received", "date_sent",
    "time_sent", "document_extension", "email_bcc", "email_cc",
    "email_from", "email_subject", "email_to", "original_filename",
    "file_size", "original_folder_path", "md5_hash", "parent_document_id",
    "document_title", "time_zone", "text_link", "native_link",
]

# DOJ First Production field names (4 fields)
DOJ_FIELDS = ["prod_beg", "prod_end", "filename", "file_path"]


def parse_concordance_dat(filepath):
    """Parse a Concordance DAT file into header + records.

    Handles both UTF-8 (DS1-12, House Estate) and Latin-1 (DOJ First Prod) encodings.
    Concordance format: fields wrapped in þ (U+00FE), separated by DC4 (\\x14).
    """
    THORN = '\u00fe'  # þ
    DC4 = '\x14'

    # Try UTF-8 first (with BOM support), fall back to latin-1
    for encoding in ('utf-8-sig', 'latin-1'):
        try:
            with open(filepath, 'r', encoding=encoding) as f:
                content = f.read()
            # Verify we got the thorn character
            if THORN in content:
                break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"Cannot decode {filepath} with any supported encoding")

    # Parse records using state machine (handles multi-line field values)
    records = []
    current_chars = []
    in_field = False  # True when inside þ...þ pair

    for ch in content:
        current_chars.append(ch)
        if ch == THORN:
            in_field = not in_field
        elif ch == '\n' and not in_field:
            line = ''.join(current_chars).strip('\r\n')
            if line:
                records.append(line)
            current_chars = []

    # Last record (if file doesn't end with newline)
    if current_chars:
        line = ''.join(current_chars).strip('\r\n')
        if line:
            records.append(line)

    if not records:
        return [], []

    def parse_record(rec_str):
        """Split a single DAT record into field values."""
        segments = rec_str.split(DC4)
        fields = []
        for seg in segments:
            val = seg.strip(THORN).strip()
            fields.append(val)
        return fields

    header = parse_record(records[0])
    data = [parse_record(r) for r in records[1:]]
    return header, data


def parse_opt_file(filepath):
    """Parse an OPT file (CSV format with 7 columns).

    Returns list of dicts with keys:
      bates_number, volume, image_path, has_ocr, field5, field6, page_count
    """
    entries = []
    with open(filepath, 'r', encoding='utf-8-sig', errors='replace') as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or not row[0].strip():
                continue
            entry = {
                'bates_number': row[0].strip() if len(row) > 0 else '',
                'volume': row[1].strip() if len(row) > 1 else '',
                'image_path': row[2].strip() if len(row) > 2 else '',
                'has_ocr': row[3].strip() if len(row) > 3 else '',
                'field5': row[4].strip() if len(row) > 4 else '',
                'field6': row[5].strip() if len(row) > 5 else '',
                'page_count': row[6].strip() if len(row) > 6 else '',
            }
            entries.append(entry)
    return entries


def create_database(db_path):
    """Create the concordance_complete.db schema."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-200000")  # 200MB cache
    cur = conn.cursor()

    cur.executescript("""
        -- Core document table
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            bates_begin TEXT NOT NULL,
            bates_end TEXT,
            bates_begin_attach TEXT,
            bates_end_attach TEXT,
            is_attachment INTEGER DEFAULT 0,
            page_count INTEGER,

            original_filename TEXT,
            document_extension TEXT,
            file_size INTEGER,
            original_folder_path TEXT,
            md5_hash TEXT,
            document_title TEXT,

            author TEXT,
            custodian TEXT,

            date_created TEXT,
            date_last_modified TEXT,
            date_received TEXT,
            date_sent TEXT,
            time_sent TEXT,
            time_zone TEXT,

            email_from TEXT,
            email_to TEXT,
            email_cc TEXT,
            email_bcc TEXT,
            email_subject TEXT,

            parent_document_id TEXT,

            text_link TEXT,
            native_link TEXT,

            efta_number TEXT,
            corpus_doc_id INTEGER,

            UNIQUE(source, bates_begin)
        );

        -- Per-page index from OPT files
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            bates_number TEXT NOT NULL,
            document_id INTEGER REFERENCES documents(id),
            page_number INTEGER,
            volume TEXT,
            image_path TEXT,
            is_first_page INTEGER DEFAULT 0,
            UNIQUE(source, bates_number)
        );

        -- Cross-reference table
        CREATE TABLE IF NOT EXISTS cross_references (
            id INTEGER PRIMARY KEY,
            doc_id_a INTEGER REFERENCES documents(id),
            doc_id_b INTEGER REFERENCES documents(id),
            match_type TEXT,
            confidence TEXT,
            notes TEXT
        );

        -- Email threads
        CREATE TABLE IF NOT EXISTS email_threads (
            id INTEGER PRIMARY KEY,
            thread_root_id INTEGER REFERENCES documents(id),
            doc_id INTEGER REFERENCES documents(id),
            position_in_thread INTEGER,
            is_attachment INTEGER DEFAULT 0
        );

        -- Folder structure analysis
        CREATE TABLE IF NOT EXISTS folder_inventory (
            id INTEGER PRIMARY KEY,
            folder_path TEXT UNIQUE,
            doc_count INTEGER,
            source_device TEXT,
            notes TEXT
        );

        -- Summary statistics
        CREATE TABLE IF NOT EXISTS extraction_stats (
            id INTEGER PRIMARY KEY,
            source TEXT,
            total_documents INTEGER,
            total_pages INTEGER,
            fields_available INTEGER,
            fields_populated TEXT,
            extraction_timestamp TEXT
        );
    """)

    conn.commit()
    return conn


def extract_bates_num(bates_str):
    """Extract numeric portion from a Bates number string."""
    import re
    m = re.search(r'(\d+)$', bates_str)
    return int(m.group(1)) if m else 0


def ingest_ds_dat(conn, source, dat_path):
    """Ingest a DS1-12 DAT file (2 fields: Begin Bates, End Bates)."""
    header, records = parse_concordance_dat(dat_path)
    print(f"  [{source}] DAT: {len(records)} records, header: {header}")

    cur = conn.cursor()
    inserted = 0
    for fields in records:
        if len(fields) < 2:
            continue
        bates_begin = fields[0].strip()
        bates_end = fields[1].strip()
        if not bates_begin:
            continue

        # Calculate page count from Bates range
        begin_num = extract_bates_num(bates_begin)
        end_num = extract_bates_num(bates_end)
        page_count = (end_num - begin_num + 1) if end_num >= begin_num else 1

        cur.execute("""
            INSERT OR IGNORE INTO documents (source, bates_begin, bates_end, page_count, efta_number)
            VALUES (?, ?, ?, ?, ?)
        """, (source, bates_begin, bates_end, page_count, bates_begin))
        inserted += 1

    conn.commit()
    print(f"  [{source}] Inserted {inserted} documents")
    return inserted


def ingest_ds_opt(conn, source, opt_path, volume):
    """Ingest a DS1-12 OPT file into the pages table."""
    entries = parse_opt_file(opt_path)
    print(f"  [{source}] OPT: {len(entries)} page entries")

    cur = conn.cursor()

    # Build a lookup for document IDs
    cur.execute("SELECT id, bates_begin FROM documents WHERE source = ?", (source,))
    doc_lookup = {row[1]: row[0] for row in cur.fetchall()}

    # Process OPT entries — first page of each doc has a page_count value
    inserted = 0
    current_doc_id = None
    current_page_offset = 0

    for entry in entries:
        bates = entry['bates_number']
        is_first = bool(entry['page_count'])  # Non-empty page_count = first page
        vol = entry['volume'] or volume

        if is_first:
            current_doc_id = doc_lookup.get(bates)
            current_page_offset = 1
        else:
            current_page_offset += 1

        cur.execute("""
            INSERT OR IGNORE INTO pages (source, bates_number, document_id, page_number, volume, image_path, is_first_page)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (source, bates, current_doc_id, current_page_offset, vol, entry['image_path'], 1 if is_first else 0))
        inserted += 1

    conn.commit()
    print(f"  [{source}] Inserted {inserted} pages")
    return inserted


def ingest_house_estate_dat(conn, dat_path):
    """Ingest House Estate DAT (28 fields)."""
    source = "HOUSE_ESTATE_7TH"
    header, records = parse_concordance_dat(dat_path)
    print(f"  [{source}] DAT: {len(records)} records, {len(header)} fields")
    print(f"  [{source}] Header: {header}")

    # Map header names to our field names
    # The header has 28 fields in order matching HOUSE_ESTATE_FIELDS
    cur = conn.cursor()
    inserted = 0
    field_pop = Counter()

    for fields in records:
        # Pad fields to expected length
        while len(fields) < 28:
            fields.append('')

        # Map to named fields
        d = {}
        for i, fname in enumerate(HOUSE_ESTATE_FIELDS):
            val = fields[i].strip() if i < len(fields) else ''
            d[fname] = val
            if val:
                field_pop[fname] += 1

        bates_begin = d['bates_begin']
        if not bates_begin:
            continue

        # Determine page count
        page_count = None
        if d['pages']:
            try:
                page_count = int(d['pages'])
            except ValueError:
                pass
        if page_count is None and d['bates_begin'] and d['bates_end']:
            b = extract_bates_num(d['bates_begin'])
            e = extract_bates_num(d['bates_end'])
            page_count = (e - b + 1) if e >= b else 1

        # Determine if attachment
        is_attachment = 1 if d.get('attachment_document') else 0
        if not is_attachment and d['parent_document_id']:
            is_attachment = 1

        # File size
        file_size = None
        if d['file_size']:
            try:
                file_size = int(d['file_size'].replace(',', ''))
            except ValueError:
                pass

        cur.execute("""
            INSERT OR IGNORE INTO documents (
                source, bates_begin, bates_end, bates_begin_attach, bates_end_attach,
                is_attachment, page_count, original_filename, document_extension,
                file_size, original_folder_path, md5_hash, document_title,
                author, custodian, date_created, date_last_modified,
                date_received, date_sent, time_sent, time_zone,
                email_from, email_to, email_cc, email_bcc, email_subject,
                parent_document_id, text_link, native_link
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            source, bates_begin, d['bates_end'], d['bates_begin_attach'], d['bates_end_attach'],
            is_attachment, page_count, d['original_filename'], d['document_extension'],
            file_size, d['original_folder_path'], d['md5_hash'], d['document_title'],
            d['author'], d['custodian'], d['date_created'], d['date_last_modified'],
            d['date_received'], d['date_sent'], d['time_sent'], d['time_zone'],
            d['email_from'], d['email_to'], d['email_cc'], d['email_bcc'], d['email_subject'],
            d['parent_document_id'], d['text_link'], d['native_link'],
        ))
        inserted += 1

    conn.commit()
    print(f"  [{source}] Inserted {inserted} documents")
    print(f"  [{source}] Field population:")
    for fname in HOUSE_ESTATE_FIELDS:
        count = field_pop.get(fname, 0)
        pct = count / max(inserted, 1) * 100
        print(f"    {fname}: {count}/{inserted} ({pct:.0f}%)")

    # Record stats
    cur.execute("""
        INSERT INTO extraction_stats (source, total_documents, total_pages, fields_available, fields_populated, extraction_timestamp)
        VALUES (?, ?, NULL, ?, ?, ?)
    """, (source, inserted, 28, json.dumps(dict(field_pop)), datetime.now().isoformat()))
    conn.commit()

    return inserted


def ingest_house_estate_opt(conn, opt_path):
    """Ingest House Estate OPT file."""
    source = "HOUSE_ESTATE_7TH"
    entries = parse_opt_file(opt_path)
    print(f"  [{source}] OPT: {len(entries)} page entries")

    cur = conn.cursor()

    # Build document lookup
    cur.execute("SELECT id, bates_begin FROM documents WHERE source = ?", (source,))
    doc_lookup = {row[1]: row[0] for row in cur.fetchall()}

    inserted = 0
    current_doc_id = None
    current_page_offset = 0

    for entry in entries:
        bates = entry['bates_number']
        is_first = bool(entry['page_count'])
        vol = entry['volume'] or 'HOUSE_OVERSIGHT_009'

        if is_first:
            current_doc_id = doc_lookup.get(bates)
            current_page_offset = 1
        else:
            current_page_offset += 1

        cur.execute("""
            INSERT OR IGNORE INTO pages (source, bates_number, document_id, page_number, volume, image_path, is_first_page)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (source, bates, current_doc_id, current_page_offset, vol, entry['image_path'], 1 if is_first else 0))
        inserted += 1

    conn.commit()
    print(f"  [{source}] Inserted {inserted} pages")
    return inserted


def ingest_doj_first_prod_dat(conn, dat_path):
    """Ingest DOJ First Production DAT (4 fields)."""
    source = "DOJ_FIRST_PROD"
    header, records = parse_concordance_dat(dat_path)
    print(f"  [{source}] DAT: {len(records)} records, header: {header}")

    cur = conn.cursor()
    inserted = 0

    for fields in records:
        while len(fields) < 4:
            fields.append('')

        bates_begin = fields[0].strip()
        bates_end = fields[1].strip()
        filename = fields[2].strip()
        file_path = fields[3].strip()

        if not bates_begin:
            continue

        # Calculate page count
        begin_num = extract_bates_num(bates_begin)
        end_num = extract_bates_num(bates_end)
        page_count = (end_num - begin_num + 1) if end_num >= begin_num else 1

        cur.execute("""
            INSERT OR IGNORE INTO documents (source, bates_begin, bates_end, page_count, original_filename)
            VALUES (?, ?, ?, ?, ?)
        """, (source, bates_begin, bates_end, page_count, filename))
        inserted += 1

    conn.commit()

    # Record stats
    cur.execute("""
        INSERT INTO extraction_stats (source, total_documents, total_pages, fields_available, fields_populated, extraction_timestamp)
        VALUES (?, ?, NULL, ?, ?, ?)
    """, (source, inserted, 4,
          json.dumps({"prod_beg": inserted, "prod_end": inserted,
                      "filename": sum(1 for f in records if len(f) > 2 and f[2].strip()),
                      "file_path": sum(1 for f in records if len(f) > 3 and f[3].strip())}),
          datetime.now().isoformat()))
    conn.commit()

    print(f"  [{source}] Inserted {inserted} documents")
    return inserted


def ingest_doj_first_prod_opt(conn, opt_path):
    """Ingest DOJ First Production OPT file."""
    source = "DOJ_FIRST_PROD"
    entries = parse_opt_file(opt_path)
    print(f"  [{source}] OPT: {len(entries)} page entries")

    cur = conn.cursor()

    # Build document lookup
    cur.execute("SELECT id, bates_begin FROM documents WHERE source = ?", (source,))
    doc_lookup = {row[1]: row[0] for row in cur.fetchall()}

    inserted = 0
    current_doc_id = None
    current_page_offset = 0

    for entry in entries:
        bates = entry['bates_number']
        is_first = bool(entry['page_count'])
        vol = entry['volume'] or 'VOL00001'

        if is_first:
            current_doc_id = doc_lookup.get(bates)
            current_page_offset = 1
        else:
            current_page_offset += 1

        cur.execute("""
            INSERT OR IGNORE INTO pages (source, bates_number, document_id, page_number, volume, image_path, is_first_page)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (source, bates, current_doc_id, current_page_offset, vol, entry['image_path'], 1 if is_first else 0))
        inserted += 1

    conn.commit()
    print(f"  [{source}] Inserted {inserted} pages")
    return inserted


def cross_reference_md5(conn):
    """Find duplicate documents via MD5 hash matching."""
    print("\n--- Cross-referencing via MD5 hashes ---")
    cur = conn.cursor()

    # Find MD5 duplicates within and across sources
    cur.execute("""
        SELECT md5_hash, GROUP_CONCAT(id), COUNT(*) as cnt
        FROM documents
        WHERE md5_hash IS NOT NULL AND md5_hash != ''
        GROUP BY md5_hash
        HAVING cnt > 1
    """)

    matches = 0
    for md5, id_list, cnt in cur.fetchall():
        ids = [int(x) for x in id_list.split(',')]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                cur.execute("""
                    INSERT OR IGNORE INTO cross_references (doc_id_a, doc_id_b, match_type, confidence, notes)
                    VALUES (?, ?, 'md5_match', 'HIGH', ?)
                """, (ids[i], ids[j], f"MD5: {md5}"))
                matches += 1

    conn.commit()
    print(f"  Found {matches} MD5 duplicate pairs")
    return matches


def cross_reference_filenames(conn):
    """Find potential matches via original filename."""
    print("\n--- Cross-referencing via filenames ---")
    cur = conn.cursor()

    cur.execute("""
        SELECT original_filename, GROUP_CONCAT(id), GROUP_CONCAT(source), COUNT(*) as cnt
        FROM documents
        WHERE original_filename IS NOT NULL AND original_filename != ''
        GROUP BY original_filename
        HAVING cnt > 1
    """)

    matches = 0
    for fname, id_list, source_list, cnt in cur.fetchall():
        sources = source_list.split(',')
        ids = [int(x) for x in id_list.split(',')]
        # Only count cross-source matches
        if len(set(sources)) > 1:
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    si, sj = sources[i], sources[j]
                    if si != sj:
                        cur.execute("""
                            INSERT OR IGNORE INTO cross_references (doc_id_a, doc_id_b, match_type, confidence, notes)
                            VALUES (?, ?, 'filename_match', 'MEDIUM', ?)
                        """, (ids[i], ids[j], f"Filename: {fname}"))
                        matches += 1

    conn.commit()
    print(f"  Found {matches} cross-source filename matches")
    return matches


def map_corpus_ids(conn):
    """Map documents to full_text_corpus.db document IDs where possible."""
    print("\n--- Mapping to full_text_corpus.db ---")
    if not os.path.exists(CORPUS_DB):
        print("  full_text_corpus.db not found, skipping")
        return 0

    corpus_conn = sqlite3.connect(CORPUS_DB)
    corpus_cur = corpus_conn.cursor()

    cur = conn.cursor()
    mapped = 0

    # For EFTA-based sources (DS01-12), direct mapping
    for source_prefix in [f"DS{i:02d}" for i in range(1, 13)]:
        cur.execute("""
            SELECT id, bates_begin FROM documents WHERE source = ? AND efta_number IS NOT NULL
        """, (source_prefix,))
        docs = cur.fetchall()

        batch = []
        for doc_id, bates_begin in docs:
            corpus_cur.execute("SELECT id FROM documents WHERE efta_number = ?", (bates_begin,))
            row = corpus_cur.fetchone()
            if row:
                batch.append((row[0], doc_id))

        cur.executemany("UPDATE documents SET corpus_doc_id = ? WHERE id = ?", batch)
        mapped += len(batch)

    conn.commit()
    corpus_conn.close()
    print(f"  Mapped {mapped} documents to corpus IDs")
    return mapped


def reconstruct_email_threads(conn):
    """Reconstruct email threads from Parent Document ID + attachment flags."""
    print("\n--- Reconstructing email threads ---")
    cur = conn.cursor()

    # Find all documents with parent_document_id set
    cur.execute("""
        SELECT d.id, d.parent_document_id, d.bates_begin, d.email_subject, d.date_sent
        FROM documents d
        WHERE d.parent_document_id IS NOT NULL AND d.parent_document_id != ''
    """)
    children = cur.fetchall()
    print(f"  Found {len(children)} documents with parent references")

    # Build parent→children map
    parent_map = defaultdict(list)
    for child_id, parent_bates, child_bates, subject, date_sent in children:
        parent_map[parent_bates].append((child_id, child_bates, subject, date_sent))

    # Find root documents (parents that are not themselves children)
    all_parent_bates = set(parent_map.keys())
    cur.execute("""
        SELECT id, bates_begin FROM documents
        WHERE bates_begin IN ({})
    """.format(','.join('?' for _ in all_parent_bates)), list(all_parent_bates))

    threads_created = 0
    for root_id, root_bates in cur.fetchall():
        # This is a thread root
        cur.execute("""
            INSERT INTO email_threads (thread_root_id, doc_id, position_in_thread, is_attachment)
            VALUES (?, ?, 1, 0)
        """, (root_id, root_id))

        # Add children
        for pos, (child_id, child_bates, subject, date_sent) in enumerate(parent_map[root_bates], 2):
            cur.execute("""
                INSERT INTO email_threads (thread_root_id, doc_id, position_in_thread, is_attachment)
                VALUES (?, ?, ?, 1)
            """, (root_id, child_id, pos))

        threads_created += 1

    conn.commit()
    print(f"  Reconstructed {threads_created} email threads")
    return threads_created


def analyze_folder_structure(conn):
    """Analyze Original Folder Path entries to identify device categories."""
    print("\n--- Analyzing folder structure ---")
    cur = conn.cursor()

    cur.execute("""
        SELECT original_folder_path, COUNT(*) as cnt
        FROM documents
        WHERE original_folder_path IS NOT NULL AND original_folder_path != ''
        GROUP BY original_folder_path
        ORDER BY cnt DESC
    """)

    folders = cur.fetchall()
    print(f"  Found {len(folders)} unique folder paths")

    for folder_path, doc_count in folders:
        # Classify source device
        fp_lower = folder_path.lower()
        if 'macintosh hd' in fp_lower or '/users/' in fp_lower:
            source_device = "Macintosh HD"
        elif 'google' in fp_lower or 'takeout' in fp_lower:
            source_device = "Google Takeout"
        elif 'eml' in fp_lower or 'mail' in fp_lower:
            source_device = "Email Archive"
        elif 'iphone' in fp_lower or 'ipad' in fp_lower or 'mobile' in fp_lower:
            source_device = "Mobile Device"
        elif 'external' in fp_lower or 'usb' in fp_lower:
            source_device = "External Storage"
        elif 'd:\\' in fp_lower or 'c:\\' in fp_lower:
            source_device = "Windows PC"
        else:
            source_device = "Unknown"

        cur.execute("""
            INSERT OR REPLACE INTO folder_inventory (folder_path, doc_count, source_device)
            VALUES (?, ?, ?)
        """, (folder_path, doc_count, source_device))

    conn.commit()
    print(f"  Classified {len(folders)} folders into device categories")
    return len(folders)


def build_fts5_index(conn):
    """Build FTS5 full-text search index on document text fields."""
    print("\n--- Building FTS5 index ---")
    cur = conn.cursor()

    # Drop existing FTS table if any
    cur.execute("DROP TABLE IF EXISTS documents_fts")

    cur.execute("""
        CREATE VIRTUAL TABLE documents_fts USING fts5(
            bates_begin, author, custodian, email_from, email_to,
            email_subject, original_filename, document_title, original_folder_path,
            content=documents, content_rowid=id
        )
    """)

    cur.execute("""
        INSERT INTO documents_fts(rowid, bates_begin, author, custodian, email_from, email_to,
            email_subject, original_filename, document_title, original_folder_path)
        SELECT id, COALESCE(bates_begin, ''), COALESCE(author, ''), COALESCE(custodian, ''),
            COALESCE(email_from, ''), COALESCE(email_to, ''), COALESCE(email_subject, ''),
            COALESCE(original_filename, ''), COALESCE(document_title, ''),
            COALESCE(original_folder_path, '')
        FROM documents
    """)

    conn.commit()
    count = cur.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0]
    print(f"  FTS5 index built with {count} entries")
    return count


def create_indexes(conn):
    """Create performance indexes."""
    print("\n--- Creating indexes ---")
    cur = conn.cursor()
    cur.executescript("""
        CREATE INDEX IF NOT EXISTS idx_docs_source ON documents(source);
        CREATE INDEX IF NOT EXISTS idx_docs_bates ON documents(bates_begin);
        CREATE INDEX IF NOT EXISTS idx_docs_efta ON documents(efta_number);
        CREATE INDEX IF NOT EXISTS idx_docs_md5 ON documents(md5_hash);
        CREATE INDEX IF NOT EXISTS idx_docs_corpus ON documents(corpus_doc_id);
        CREATE INDEX IF NOT EXISTS idx_pages_source ON pages(source);
        CREATE INDEX IF NOT EXISTS idx_pages_bates ON pages(bates_number);
        CREATE INDEX IF NOT EXISTS idx_pages_doc ON pages(document_id);
        CREATE INDEX IF NOT EXISTS idx_xref_a ON cross_references(doc_id_a);
        CREATE INDEX IF NOT EXISTS idx_xref_b ON cross_references(doc_id_b);
        CREATE INDEX IF NOT EXISTS idx_threads_root ON email_threads(thread_root_id);
        CREATE INDEX IF NOT EXISTS idx_threads_doc ON email_threads(doc_id);
    """)
    conn.commit()
    print("  Indexes created")


def export_csv(conn, csv_path):
    """Export all document metadata to CSV."""
    print(f"\n--- Exporting CSV to {csv_path} ---")
    cur = conn.cursor()

    cur.execute("""
        SELECT source, bates_begin, bates_end, bates_begin_attach, bates_end_attach,
            is_attachment, page_count, original_filename, document_extension,
            file_size, original_folder_path, md5_hash, document_title,
            author, custodian, date_created, date_last_modified,
            date_received, date_sent, time_sent, time_zone,
            email_from, email_to, email_cc, email_bcc, email_subject,
            parent_document_id, text_link, native_link, efta_number, corpus_doc_id
        FROM documents
        ORDER BY source, bates_begin
    """)

    fieldnames = [
        "source", "bates_begin", "bates_end", "bates_begin_attach", "bates_end_attach",
        "is_attachment", "page_count", "original_filename", "document_extension",
        "file_size", "original_folder_path", "md5_hash", "document_title",
        "author", "custodian", "date_created", "date_last_modified",
        "date_received", "date_sent", "time_sent", "time_zone",
        "email_from", "email_to", "email_cc", "email_bcc", "email_subject",
        "parent_document_id", "text_link", "native_link", "efta_number", "corpus_doc_id",
    ]

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(fieldnames)
        for row in cur:
            writer.writerow(row)

    # Count rows
    row_count = cur.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    print(f"  Exported {row_count} records to CSV")
    return row_count


def print_summary(conn):
    """Print final summary statistics."""
    cur = conn.cursor()
    print("\n" + "=" * 80)
    print("CONCORDANCE COMPLETE — SUMMARY")
    print("=" * 80)

    # Document counts by source
    cur.execute("SELECT source, COUNT(*) FROM documents GROUP BY source ORDER BY source")
    total_docs = 0
    for source, count in cur.fetchall():
        print(f"  {source}: {count:,} documents")
        total_docs += count
    print(f"  TOTAL: {total_docs:,} documents")

    # Page counts
    cur.execute("SELECT source, COUNT(*) FROM pages GROUP BY source ORDER BY source")
    total_pages = 0
    print()
    for source, count in cur.fetchall():
        print(f"  {source}: {count:,} pages")
        total_pages += count
    print(f"  TOTAL: {total_pages:,} pages")

    # Metadata richness
    print("\n--- Metadata Coverage ---")
    for field in ['author', 'email_from', 'email_to', 'email_subject', 'original_filename',
                  'md5_hash', 'original_folder_path', 'date_created', 'date_sent',
                  'document_extension', 'custodian', 'parent_document_id']:
        cur.execute(f"SELECT COUNT(*) FROM documents WHERE {field} IS NOT NULL AND {field} != ''")
        count = cur.fetchone()[0]
        pct = count / max(total_docs, 1) * 100
        print(f"  {field}: {count:,} ({pct:.2f}%)")

    # Cross-references
    cur.execute("SELECT match_type, COUNT(*) FROM cross_references GROUP BY match_type")
    print("\n--- Cross-References ---")
    for match_type, count in cur.fetchall():
        print(f"  {match_type}: {count}")

    # Email threads
    cur.execute("SELECT COUNT(DISTINCT thread_root_id) FROM email_threads")
    thread_count = cur.fetchone()[0]
    print(f"\n--- Email Threads: {thread_count} ---")

    # Folder inventory
    cur.execute("SELECT source_device, SUM(doc_count), COUNT(*) FROM folder_inventory GROUP BY source_device ORDER BY SUM(doc_count) DESC")
    print("\n--- Source Devices ---")
    for device, doc_count, folder_count in cur.fetchall():
        print(f"  {device}: {doc_count} docs in {folder_count} folders")

    print("\n" + "=" * 80)


def main():
    print("=" * 80)
    print("PQG Phase 0: Complete Concordance Metadata Extraction")
    print("=" * 80)
    start_time = datetime.now()

    # Remove existing DB
    if os.path.exists(OUTPUT_DB):
        os.remove(OUTPUT_DB)
        print(f"Removed existing {OUTPUT_DB}")

    conn = create_database(OUTPUT_DB)

    # ========================================
    # Phase 1a: Ingest DS1-12 DAT files
    # ========================================
    print("\n--- Phase 1a: DS1-12 DAT files ---")
    total_ds_docs = 0
    for source, (file_prefix, volume) in sorted(DS_FILES.items()):
        dat_path = os.path.join(CONCORDANCE_DIR, f"{file_prefix}.DAT")
        if os.path.exists(dat_path):
            total_ds_docs += ingest_ds_dat(conn, source, dat_path)
        else:
            print(f"  [{source}] DAT not found: {dat_path}")

    # ========================================
    # Phase 1b: Ingest DS1-12 OPT files
    # ========================================
    print("\n--- Phase 1b: DS1-12 OPT files ---")
    total_ds_pages = 0
    for source, (file_prefix, volume) in sorted(DS_FILES.items()):
        opt_path = os.path.join(CONCORDANCE_DIR, f"{file_prefix}.OPT")
        if os.path.exists(opt_path):
            total_ds_pages += ingest_ds_opt(conn, source, opt_path, volume)
        else:
            print(f"  [{source}] OPT not found: {opt_path}")

    # Record DS stats
    cur = conn.cursor()
    for source in sorted(DS_FILES.keys()):
        doc_count = cur.execute("SELECT COUNT(*) FROM documents WHERE source = ?", (source,)).fetchone()[0]
        page_count = cur.execute("SELECT COUNT(*) FROM pages WHERE source = ?", (source,)).fetchone()[0]
        cur.execute("""
            INSERT INTO extraction_stats (source, total_documents, total_pages, fields_available, fields_populated, extraction_timestamp)
            VALUES (?, ?, ?, 2, ?, ?)
        """, (source, doc_count, page_count,
              json.dumps({"begin_bates": doc_count, "end_bates": doc_count}),
              datetime.now().isoformat()))
    conn.commit()

    # ========================================
    # Phase 2a: Ingest House Estate DAT
    # ========================================
    print("\n--- Phase 2a: House Estate DAT ---")
    he_dat_path = os.path.join(HOUSE_ESTATE_DIR, "HOUSE_OVERSIGHT_009.dat")
    if os.path.exists(he_dat_path):
        ingest_house_estate_dat(conn, he_dat_path)
    else:
        print(f"  House Estate DAT not found: {he_dat_path}")

    # ========================================
    # Phase 2b: Ingest House Estate OPT
    # ========================================
    print("\n--- Phase 2b: House Estate OPT ---")
    he_opt_path = os.path.join(HOUSE_ESTATE_DIR, "HOUSE_OVERSIGHT_009.opt")
    if os.path.exists(he_opt_path):
        ingest_house_estate_opt(conn, he_opt_path)
    else:
        print(f"  House Estate OPT not found: {he_opt_path}")

    # ========================================
    # Phase 3a: Ingest DOJ First Production DAT
    # ========================================
    print("\n--- Phase 3a: DOJ First Production DAT ---")
    doj_dat_path = os.path.join(DOJ_FIRST_PROD_DIR, "20250822.dat")
    if os.path.exists(doj_dat_path):
        ingest_doj_first_prod_dat(conn, doj_dat_path)
    else:
        print(f"  DOJ First Production DAT not found: {doj_dat_path}")

    # ========================================
    # Phase 3b: Ingest DOJ First Production OPT
    # ========================================
    print("\n--- Phase 3b: DOJ First Production OPT ---")
    doj_opt_path = os.path.join(DOJ_FIRST_PROD_DIR, "20250822.opt")
    if os.path.exists(doj_opt_path):
        ingest_doj_first_prod_opt(conn, doj_opt_path)
    else:
        print(f"  DOJ First Production OPT not found: {doj_opt_path}")

    # ========================================
    # Phase 4: Cross-referencing
    # ========================================
    print("\n--- Phase 4: Cross-referencing ---")
    cross_reference_md5(conn)
    cross_reference_filenames(conn)
    map_corpus_ids(conn)

    # ========================================
    # Phase 5: Thread reconstruction
    # ========================================
    reconstruct_email_threads(conn)

    # ========================================
    # Phase 6: Folder analysis
    # ========================================
    analyze_folder_structure(conn)

    # ========================================
    # Phase 7: Indexes + FTS5
    # ========================================
    create_indexes(conn)
    build_fts5_index(conn)

    # ========================================
    # Phase 8: CSV export
    # ========================================
    export_csv(conn, OUTPUT_CSV)

    # ========================================
    # Summary
    # ========================================
    print_summary(conn)

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\nCompleted in {elapsed:.1f} seconds")

    conn.close()


if __name__ == "__main__":
    main()
