#!/usr/bin/env python3
"""
Build a comprehensive CSV catalog of all non-PDF native files in the
Epstein DOJ release. Merges data from:
  - Filesystem scan (all datasets)
  - media_classifications.json (technical metadata)
  - transcripts.db (speech transcription data)
  - full_text_corpus.db (PDF placeholder page counts)

Output: NATIVE_FILES_CATALOG.csv
"""

import csv
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

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

BASE_DIR = _find_data_dir()
DATASETS_DIR = os.path.join(BASE_DIR, "datasets")
MEDIA_CLASS_PATH = os.path.join(BASE_DIR, "media_classifications.json")
TRANSCRIPTS_DB = os.path.join(BASE_DIR, "transcripts.db")
CORPUS_DB = os.path.join(BASE_DIR, "full_text_corpus.db")
OUTPUT_CSV = os.path.join(BASE_DIR, "NATIVE_FILES_CATALOG.csv")

# Dataset number ranges for DOJ URL construction
DATASET_RANGES = {
    1: (1, 3158),
    2: (3159, 3857),
    3: (3858, 5586),
    4: (5705, 8320),
    5: (8409, 8528),
    6: (8529, 8998),
    7: (9016, 9664),
    8: (9676, 39023),
    9: (39025, 1262781),
    10: (1262782, 2205654),
    11: (2205655, 2730264),
    12: (2730265, 2731783),
}

# Extension to category mapping
CATEGORY_MAP = {
    'mp4': 'video',
    'avi': 'video',
    'mov': 'video',
    'm4v': 'video',
    'wmv': 'video',
    'vob': 'video',
    'ts': 'video',
    '3gp': 'video',
    'm4a': 'audio',
    'wav': 'audio',
    'mp3': 'audio',
    'opus': 'audio',
    'amr': 'audio',
    'xlsx': 'spreadsheet',
    'xls': 'spreadsheet',
    'csv': 'spreadsheet',
    'png': 'image',
    'pluginpayloadattachment': 'attachment',
}

EFTA_RE = re.compile(r'EFTA(\d{8})')


def efta_to_dataset(efta_num):
    """Map an EFTA number to its dataset."""
    n = int(efta_num.replace('EFTA', ''))
    for ds, (lo, hi) in sorted(DATASET_RANGES.items()):
        if lo <= n <= hi:
            return ds
    # Gap ranges — map to nearest lower dataset
    for ds in sorted(DATASET_RANGES.keys(), reverse=True):
        if n >= DATASET_RANGES[ds][0]:
            return ds
    return None


def doj_pdf_url(efta, dataset):
    """Build DOJ URL for the PDF placeholder."""
    return f"https://www.justice.gov/epstein/files/DataSet%20{dataset}/{efta}.pdf"


def scan_filesystem():
    """Walk all dataset directories and find non-PDF EFTA files."""
    files = {}
    # Map dataset directory names to dataset numbers
    ds_dirs = {
        'dataset1': 1, 'dataset2': 2, 'dataset3': 3, 'dataset4': 4,
        'dataset5': 5, 'dataset6': 6, 'dataset7': 7, 'dataset8': 8,
        'dataset9': 9, 'dataset9_full': 9, 'dataset10': 10,
        'dataset11': 11, 'dataset11_full': 11, 'dataset12': 12,
    }

    for root, dirs, filenames in os.walk(DATASETS_DIR):
        # Skip _old directories
        if '_old' in root:
            continue
        for fname in filenames:
            if fname.lower().endswith('.pdf'):
                continue
            m = EFTA_RE.search(fname)
            if not m:
                continue

            efta = f"EFTA{m.group(1)}"
            fpath = os.path.join(root, fname)
            ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''

            # Determine dataset from path
            rel = os.path.relpath(fpath, DATASETS_DIR)
            top_dir = rel.split(os.sep)[0]
            dataset = ds_dirs.get(top_dir)
            if dataset is None:
                dataset = efta_to_dataset(efta)

            try:
                size = os.path.getsize(fpath)
            except OSError:
                size = 0

            files[efta] = {
                'efta_number': efta,
                'dataset': dataset,
                'extension': ext,
                'file_size_bytes': size,
                'file_size_mb': round(size / (1024 * 1024), 2),
                'native_path': fpath,
                'category': CATEGORY_MAP.get(ext, 'other'),
            }

    return files


def load_media_classifications(files):
    """Merge media_classifications.json metadata."""
    if not os.path.exists(MEDIA_CLASS_PATH):
        print("  media_classifications.json not found, skipping")
        return

    with open(MEDIA_CLASS_PATH) as f:
        classifications = json.load(f)

    merged = 0
    for entry in classifications:
        efta = entry.get('efta', '')
        if not efta:
            # Try to extract from path
            m = EFTA_RE.search(entry.get('path', ''))
            if m:
                efta = f"EFTA{m.group(1)}"
            else:
                continue

        if efta in files:
            files[efta]['has_audio'] = entry.get('has_audio', '')
            files[efta]['has_video'] = entry.get('has_video', '')
            files[efta]['duration_seconds'] = entry.get('duration', '')
            files[efta]['video_resolution'] = entry.get('video_resolution', '')
            files[efta]['classification'] = entry.get('classification', '')
            merged += 1

    print(f"  Merged {merged} media classifications")


def load_transcripts(files):
    """Merge transcripts.db speech data."""
    if not os.path.exists(TRANSCRIPTS_DB):
        print("  transcripts.db not found, skipping")
        return

    conn = sqlite3.connect(TRANSCRIPTS_DB)
    cur = conn.cursor()

    # Get table info
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]

    if 'transcripts' not in tables:
        print("  No transcripts table found")
        conn.close()
        return

    cur.execute("PRAGMA table_info(transcripts)")
    cols = [r[1] for r in cur.fetchall()]

    cur.execute("""
        SELECT efta_number, word_count, duration_secs, length(transcript)
        FROM transcripts
    """)
    merged = 0
    for row in cur.fetchall():
        efta = row[0]
        if not efta:
            continue
        # Normalize
        if not efta.startswith('EFTA'):
            efta = f"EFTA{efta.zfill(8)}"

        if efta in files:
            files[efta]['transcript_word_count'] = row[1] or 0
            # Fill in duration from transcripts if not already set
            if row[2] and not files[efta].get('duration_seconds'):
                files[efta]['duration_seconds'] = round(row[2], 1)
            files[efta]['transcript_char_count'] = row[3] or 0
            files[efta]['has_transcript'] = True
            merged += 1

    conn.close()
    print(f"  Merged {merged} transcript records")


def load_corpus_placeholders(files):
    """Check which native files have PDF placeholders in full_text_corpus.db."""
    if not os.path.exists(CORPUS_DB):
        print("  full_text_corpus.db not found, skipping")
        return

    conn = sqlite3.connect(CORPUS_DB)
    cur = conn.cursor()

    efta_list = list(files.keys())
    found = 0
    batch_size = 500
    for i in range(0, len(efta_list), batch_size):
        batch = efta_list[i:i + batch_size]
        placeholders = ','.join('?' * len(batch))
        cur.execute(f"""
            SELECT efta_number, total_pages, file_size
            FROM documents
            WHERE efta_number IN ({placeholders})
        """, batch)
        for row in cur.fetchall():
            efta = row[0]
            if efta in files:
                files[efta]['pdf_placeholder_pages'] = row[1]
                files[efta]['pdf_placeholder_size'] = row[2]
                files[efta]['in_corpus_db'] = True
                found += 1

    conn.close()
    print(f"  Found {found} PDF placeholders in corpus DB")


def generate_descriptions(files):
    """Generate brief descriptions based on available metadata."""
    for efta, info in files.items():
        parts = []
        cat = info.get('category', 'other')
        ext = info.get('extension', '')
        cls = info.get('classification', '')

        ds = info.get('dataset')

        # Base description from category
        if cat == 'video':
            wc = info.get('transcript_word_count', 0)
            if ds == 8 and ext == 'mp4':
                # DS8 MP4s are MCC Metropolitan Correctional Center surveillance
                if wc and wc > 0:
                    parts.append(f'MCC surveillance video ({wc} words transcribed)')
                else:
                    parts.append('MCC surveillance video (silent)')
            elif cls == 'NO_AUDIO' or cls == 'SURVEILLANCE':
                parts.append('Silent surveillance video')
            elif info.get('has_transcript') and wc and wc > 0:
                parts.append(f'Video with speech ({wc} words transcribed)')
            elif info.get('has_transcript'):
                parts.append('Video (no speech detected)')
            elif cls == 'SPEECH_LIKELY':
                parts.append('Video with speech (transcribed)')
            else:
                parts.append('Video file')
        elif cat == 'audio':
            wc = info.get('transcript_word_count', 0)
            if info.get('has_transcript') and wc and wc > 0:
                parts.append(f'Audio recording ({wc} words transcribed)')
            elif info.get('has_transcript'):
                parts.append('Audio recording (no speech detected)')
            else:
                parts.append('Audio recording')
        elif cat == 'spreadsheet':
            parts.append(f'Spreadsheet ({ext.upper()})')
        elif cat == 'image':
            parts.append('Image file')
        elif cat == 'attachment':
            parts.append('Apple Messages attachment')
        else:
            parts.append(f'Native file (.{ext})')

        # Add resolution info for video
        res = info.get('video_resolution', '')
        if res and cat == 'video':
            parts.append(f'{res}')

        # Add duration
        dur = info.get('duration_seconds', '')
        if dur and dur != '':
            try:
                dur = float(dur)
                if dur >= 3600:
                    parts.append(f'{dur / 3600:.1f}h')
                elif dur >= 60:
                    parts.append(f'{dur / 60:.1f}min')
                else:
                    parts.append(f'{dur:.0f}s')
            except (ValueError, TypeError):
                pass

        info['brief_description'] = '; '.join(parts)


def write_csv(files):
    """Write the final CSV catalog."""
    fieldnames = [
        'efta_number',
        'dataset',
        'extension',
        'category',
        'file_size_bytes',
        'file_size_mb',
        'doj_pdf_link',
        'has_audio',
        'has_transcript',
        'transcript_word_count',
        'duration_seconds',
        'video_resolution',
        'brief_description',
    ]

    rows = []
    for efta in sorted(files.keys()):
        info = files[efta]
        ds = info.get('dataset', '')
        row = {
            'efta_number': efta,
            'dataset': ds,
            'extension': info.get('extension', ''),
            'category': info.get('category', ''),
            'file_size_bytes': info.get('file_size_bytes', ''),
            'file_size_mb': info.get('file_size_mb', ''),
            'doj_pdf_link': doj_pdf_url(efta, ds) if ds else '',
            'has_audio': info.get('has_audio', ''),
            'has_transcript': 'yes' if info.get('has_transcript') else '',
            'transcript_word_count': info.get('transcript_word_count', ''),
            'duration_seconds': info.get('duration_seconds', ''),
            'video_resolution': info.get('video_resolution', ''),
            'brief_description': info.get('brief_description', ''),
        }
        rows.append(row)

    with open(OUTPUT_CSV, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {OUTPUT_CSV}")
    return len(rows)


def print_summary(files):
    """Print summary statistics."""
    by_ext = {}
    by_ds = {}
    by_cat = {}
    total_size = 0
    with_transcript = 0
    with_audio = 0

    for info in files.values():
        ext = info.get('extension', 'unknown')
        ds = info.get('dataset', '?')
        cat = info.get('category', 'other')

        by_ext[ext] = by_ext.get(ext, 0) + 1
        by_ds[ds] = by_ds.get(ds, 0) + 1
        by_cat[cat] = by_cat.get(cat, 0) + 1
        total_size += info.get('file_size_bytes', 0)

        if info.get('has_transcript'):
            with_transcript += 1
        if info.get('has_audio') == True:
            with_audio += 1

    print(f"\n{'=' * 60}")
    print(f"NATIVE FILES CATALOG SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total files: {len(files):,}")
    print(f"Total size:  {total_size / (1024**3):.2f} GB")
    print(f"With audio:  {with_audio:,}")
    print(f"Transcribed: {with_transcript:,}")
    print()
    print("By category:")
    for cat in sorted(by_cat, key=by_cat.get, reverse=True):
        print(f"  {cat:20s} {by_cat[cat]:>5,}")
    print()
    print("By dataset:")
    for ds in sorted(by_ds):
        print(f"  Dataset {str(ds):5s} {by_ds[ds]:>5,}")
    print()
    print("By extension:")
    for ext in sorted(by_ext, key=by_ext.get, reverse=True):
        print(f"  .{ext:25s} {by_ext[ext]:>5,}")


def main():
    print("Building Native Files Catalog")
    print("=" * 60)

    print("\n1. Scanning filesystem...")
    files = scan_filesystem()
    print(f"   Found {len(files):,} non-PDF EFTA files")

    print("\n2. Loading media classifications...")
    load_media_classifications(files)

    print("\n3. Loading transcript data...")
    load_transcripts(files)

    print("\n4. Checking corpus DB for PDF placeholders...")
    load_corpus_placeholders(files)

    print("\n5. Generating descriptions...")
    generate_descriptions(files)

    print("\n6. Writing CSV...")
    count = write_csv(files)

    print_summary(files)

    return count


if __name__ == '__main__':
    main()
