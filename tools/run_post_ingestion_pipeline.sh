#!/bin/bash
# Post-ingestion pipeline for House Estate documents
# Run after ingest_house_estate.py completes
# This chains: media transcription → spreadsheet ingestion → person registry → native catalog → DB export

set -e

# Auto-detect data directory
if [ -n "$EPSTEIN_DATA_DIR" ]; then
    BASE_DIR="$EPSTEIN_DATA_DIR"
elif [ -f "$(dirname "$(dirname "$(readlink -f "$0")")")/full_text_corpus.db" ]; then
    BASE_DIR="$(dirname "$(dirname "$(readlink -f "$0")")")"
elif [ -f "./full_text_corpus.db" ]; then
    BASE_DIR="$(pwd)"
else
    echo "Error: Cannot find full_text_corpus.db. Set EPSTEIN_DATA_DIR or run from the data directory."
    exit 1
fi
MEDIA_LIST="$BASE_DIR/house_estate_media_list.txt"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "=========================================="
echo "POST-INGESTION PIPELINE"
echo "Started: $(date)"
echo "=========================================="

# Step 0: Verify ingestion completed
echo ""
echo "--- Step 0: Verify corpus integrity ---"
python3 -c "
import sqlite3
conn = sqlite3.connect('$BASE_DIR/full_text_corpus.db')
total_docs = conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
total_pages = conn.execute('SELECT COUNT(*) FROM pages').fetchone()[0]
house_docs = conn.execute('SELECT COUNT(*) FROM documents WHERE dataset = 99').fetchone()[0]
house_pages = conn.execute('SELECT COUNT(*) FROM pages p JOIN documents d ON p.efta_number = d.efta_number WHERE d.dataset = 99').fetchone()[0]
print(f'Total corpus: {total_docs:,} docs, {total_pages:,} pages')
print(f'House Estate (ds99): {house_docs:,} docs, {house_pages:,} pages')
print(f'Non-House Estate: {total_docs - house_docs:,} docs')
if house_docs < 2000:
    print('WARNING: House Estate docs seem low — ingestion may have failed!')
    import sys; sys.exit(1)
print('Corpus looks good.')
conn.close()
"

# Step 1: Ingest House Estate spreadsheets (10 XLS/XLSX from Seventh Production)
echo ""
echo "--- Step 1: Ingest House Estate spreadsheets ---"
python3 -c "
import os, sqlite3, sys
sys.path.insert(0, '$BASE_DIR/tools')

try:
    import openpyxl, xlrd
except ImportError:
    print('Missing openpyxl/xlrd — skipping spreadsheet ingestion')
    sys.exit(0)

from ingest_spreadsheets import read_xlsx, read_xls

DB_PATH = '$BASE_DIR/full_text_corpus.db'
NATIVES_DIR = '$BASE_DIR/house_estate_extracted/seventh_production/Epstein Estate Documents - Seventh Production/NATIVES/001'

SPREADSHEETS = [
    ('HOUSE_OVERSIGHT_016552', 'xls'),
    ('HOUSE_OVERSIGHT_016599', 'xls'),
    ('HOUSE_OVERSIGHT_016600', 'xls'),
    ('HOUSE_OVERSIGHT_016601', 'xls'),
    ('HOUSE_OVERSIGHT_016694', 'xlsx'),
    ('HOUSE_OVERSIGHT_016695', 'xls'),
    ('HOUSE_OVERSIGHT_016696', 'xls'),
    ('HOUSE_OVERSIGHT_016697', 'xls'),
    ('HOUSE_OVERSIGHT_016698', 'xls'),
    ('HOUSE_OVERSIGHT_026582', 'xlsx'),
]

conn = sqlite3.connect(DB_PATH)
ingested = 0

for efta, ext in SPREADSHEETS:
    path = os.path.join(NATIVES_DIR, f'{efta}.{ext}')
    if not os.path.exists(path):
        print(f'  NOT FOUND: {path}')
        continue

    # Check if native content already ingested
    existing = conn.execute(
        'SELECT COUNT(*) FROM pages WHERE efta_number = ? AND page_number > 1000',
        (efta,)
    ).fetchone()[0]
    if existing > 0:
        print(f'  SKIP (already): {efta}')
        continue

    if ext == 'xlsx':
        sheets = read_xlsx(path)
    else:
        sheets = read_xls(path)

    if not sheets:
        print(f'  EMPTY: {efta}')
        continue

    for i, (sname, text) in enumerate(sheets):
        full_text = f'[Native {ext.upper()} — Sheet: {sname}]\n\n{text}'
        conn.execute(
            'INSERT OR IGNORE INTO pages (efta_number, page_number, text_content, char_count) VALUES (?, ?, ?, ?)',
            (efta, 10001 + i, full_text, len(full_text))
        )

    conn.commit()
    print(f'  INGESTED: {efta}.{ext} — {len(sheets)} sheets')
    ingested += 1

conn.close()
print(f'Spreadsheet ingestion: {ingested} files')
"

# Step 2: Media transcription (78 WAV/MP4/MOV files, ~23GB)
echo ""
echo "--- Step 2: Media transcription (GPU) ---"
if [ -f "$MEDIA_LIST" ] && [ -s "$MEDIA_LIST" ]; then
    MEDIA_COUNT=$(wc -l < "$MEDIA_LIST")
    echo "Found $MEDIA_COUNT media files in list"
    python3 "$BASE_DIR/tools/transcribe_media.py" \
        --file-list "$MEDIA_LIST" \
        --model-size large-v3 \
        --max-duration 7200
else
    echo "No media list found at $MEDIA_LIST — skipping"
fi

# Step 3: Update person registry
echo ""
echo "--- Step 3: Update person registry ---"
python3 "$BASE_DIR/tools/build_person_registry.py"

# Step 4: Update native files catalog
echo ""
echo "--- Step 4: Update native files catalog ---"
python3 "$BASE_DIR/tools/build_native_files_catalog.py" || echo "Native catalog update failed (non-critical)"

# Step 5: Final verification
echo ""
echo "--- Step 5: Final verification ---"
python3 -c "
import sqlite3, json, os

# Corpus stats
conn = sqlite3.connect('$BASE_DIR/full_text_corpus.db')
docs = conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
pages = conn.execute('SELECT COUNT(*) FROM pages').fetchone()[0]
house = conn.execute('SELECT COUNT(*) FROM documents WHERE dataset = 99').fetchone()[0]
fts = conn.execute('SELECT COUNT(*) FROM pages_fts').fetchone()[0]
conn.close()

# Transcripts stats
conn = sqlite3.connect('$BASE_DIR/transcripts.db')
tr_total = conn.execute('SELECT COUNT(*) FROM transcripts').fetchone()[0]
tr_speech = conn.execute('SELECT COUNT(*) FROM transcripts WHERE word_count > 0').fetchone()[0]
tr_words = conn.execute('SELECT SUM(word_count) FROM transcripts').fetchone()[0] or 0
conn.close()

# Person registry stats
reg_path = '$BASE_DIR/persons_registry.json'
if os.path.exists(reg_path):
    with open(reg_path) as f:
        reg = json.load(f)
    persons = len(reg)
else:
    persons = 0

print('========================================')
print('FINAL PIPELINE STATS')
print('========================================')
print(f'Corpus:     {docs:>12,} documents')
print(f'            {pages:>12,} pages')
print(f'            {fts:>12,} FTS entries')
print(f'House (99): {house:>12,} documents')
print(f'Transcripts:{tr_total:>12,} total')
print(f'            {tr_speech:>12,} with speech')
print(f'            {tr_words:>12,} words')
print(f'Persons:    {persons:>12,}')
print('========================================')
"

echo ""
echo "Pipeline complete at $(date)"
echo "Next: compress databases and create v2.2 release"
