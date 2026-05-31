#!/usr/bin/env python3
"""
Find missing EFTA documents by exploiting the page-based numbering system.

Each PDF is named EFTA########.pdf. Multi-page PDFs consume consecutive EFTA
numbers: a 20-page PDF starting at EFTA00003216 means pages are numbered
EFTA00003216 through EFTA00003235, and the next PDF starts at EFTA00003236.

Therefore: expected_next = current_efta_number + total_pages
Any gap between expected_next and the actual next document = missing EFTAs.
"""

import os
import sqlite3
import sys
from collections import defaultdict

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

DB_PATH = os.path.join(_find_data_dir(), "full_text_corpus.db")

def find_gaps():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Get all documents ordered by EFTA number, within each dataset
    cur.execute("""
        SELECT efta_number, dataset, total_pages
        FROM documents
        WHERE total_pages IS NOT NULL AND total_pages > 0
        ORDER BY efta_number
    """)

    docs = []
    for row in cur.fetchall():
        efta_str, dataset, pages = row
        efta_num = int(efta_str[4:])  # Strip "EFTA" prefix
        docs.append((efta_num, dataset, pages, efta_str))

    conn.close()

    # Known dataset boundaries (expected gaps between datasets)
    # From the dataset summary:
    dataset_ranges = {
        1:  (1, 3158),
        2:  (3159, 3857),
        3:  (3858, 5586),
        4:  (5705, 8320),
        5:  (8409, 8528),
        6:  (8529, 8998),
        7:  (9016, 9664),
        8:  (9676, 39023),
        9:  (39025, 1262781),
        10: (1262782, 2212882),
        11: (2212883, 2730262),
        12: (2730265, 2731783),
    }

    # Build set of all known EFTA numbers for quick lookup
    known_efta = {d[0] for d in docs}

    # Find inter-dataset gaps (expected, between datasets)
    inter_dataset_gaps = set()
    sorted_ds = sorted(dataset_ranges.keys())
    for i in range(len(sorted_ds) - 1):
        ds_end = dataset_ranges[sorted_ds[i]][1]
        ds_next_start = dataset_ranges[sorted_ds[i+1]][0]
        for n in range(ds_end + 1, ds_next_start):
            inter_dataset_gaps.add(n)

    # Process documents sequentially within each dataset
    gaps_by_dataset = defaultdict(list)
    total_missing = 0
    total_intra_gaps = 0

    # Group by dataset
    docs_by_ds = defaultdict(list)
    for efta_num, dataset, pages, efta_str in docs:
        docs_by_ds[dataset].append((efta_num, pages, efta_str))

    for ds in sorted(docs_by_ds.keys()):
        ds_docs = sorted(docs_by_ds[ds], key=lambda x: x[0])
        ds_start, ds_end = dataset_ranges.get(ds, (ds_docs[0][0], ds_docs[-1][0]))

        for i in range(len(ds_docs) - 1):
            efta_num, pages, efta_str = ds_docs[i]
            next_efta_num, _, next_efta_str = ds_docs[i + 1]

            expected_next = efta_num + pages

            if expected_next < next_efta_num:
                # GAP FOUND
                gap_size = next_efta_num - expected_next
                gap_start = expected_next
                gap_end = next_efta_num - 1

                gaps_by_dataset[ds].append({
                    'after': efta_str,
                    'after_pages': pages,
                    'before': next_efta_str,
                    'gap_start': gap_start,
                    'gap_end': gap_end,
                    'missing_count': gap_size,
                    'missing_range': f"EFTA{gap_start:08d}" + (f"-EFTA{gap_end:08d}" if gap_size > 1 else ""),
                })
                total_missing += gap_size
                total_intra_gaps += 1
            elif expected_next > next_efta_num:
                # OVERLAP — page count suggests next doc should start later
                # This means either total_pages is wrong or numbering is different
                gaps_by_dataset[ds].append({
                    'after': efta_str,
                    'after_pages': pages,
                    'before': next_efta_str,
                    'gap_start': None,
                    'gap_end': None,
                    'missing_count': 0,
                    'overlap': expected_next - next_efta_num,
                    'missing_range': f"OVERLAP: {efta_str} ({pages} pages) ends at {expected_next}, but next is {next_efta_num}",
                })

        # Also check: does the first doc in the dataset start at the expected range start?
        if ds_docs[0][0] > ds_start:
            gap_size = ds_docs[0][0] - ds_start
            gaps_by_dataset[ds].insert(0, {
                'after': f"DS{ds} expected start",
                'after_pages': 0,
                'before': ds_docs[0][2],
                'gap_start': ds_start,
                'gap_end': ds_docs[0][0] - 1,
                'missing_count': gap_size,
                'missing_range': f"EFTA{ds_start:08d}" + (f"-EFTA{ds_docs[0][0]-1:08d}" if gap_size > 1 else ""),
            })
            total_missing += gap_size
            total_intra_gaps += 1

        # Check: does the last doc in the dataset end at the expected range end?
        last_efta, last_pages, last_str = ds_docs[-1]
        expected_end = last_efta + last_pages - 1
        if expected_end < ds_end:
            gap_size = ds_end - expected_end
            gaps_by_dataset[ds].append({
                'after': last_str,
                'after_pages': last_pages,
                'before': f"DS{ds} expected end",
                'gap_start': expected_end + 1,
                'gap_end': ds_end,
                'missing_count': gap_size,
                'missing_range': f"EFTA{expected_end+1:08d}" + (f"-EFTA{ds_end:08d}" if gap_size > 1 else ""),
            })
            total_missing += gap_size
            total_intra_gaps += 1

    # Compute inter-dataset gap totals
    inter_ds_total = len(inter_dataset_gaps)

    # Print report
    print("# MISSING EFTA DOCUMENT ANALYSIS")
    print(f"# Generated from full_text_corpus.db ({len(docs):,} documents)")
    print()
    print(f"## Summary")
    print(f"- **Total documents in corpus:** {len(docs):,}")
    print(f"- **Total EFTA page-numbers spanned:** {dataset_ranges[12][1]:,} (EFTA00000001-EFTA{dataset_ranges[12][1]:08d})")
    print(f"- **Intra-dataset gaps found:** {total_intra_gaps:,}")
    print(f"- **Total missing EFTA page-numbers (within datasets):** {total_missing:,}")
    print(f"- **Inter-dataset boundary gaps:** {inter_ds_total:,} EFTA numbers in gaps between datasets (expected)")
    print()

    for ds in sorted(gaps_by_dataset.keys()):
        gaps = gaps_by_dataset[ds]
        ds_missing = sum(g['missing_count'] for g in gaps if 'overlap' not in g)
        overlaps = [g for g in gaps if 'overlap' in g]
        real_gaps = [g for g in gaps if 'overlap' not in g and g['missing_count'] > 0]

        ds_start, ds_end = dataset_ranges.get(ds, (0, 0))
        ds_span = ds_end - ds_start + 1
        ds_doc_count = len(docs_by_ds[ds])
        ds_page_sum = sum(d[1] for d in docs_by_ds[ds])

        print(f"## Dataset {ds}")
        print(f"- Range: EFTA{ds_start:08d} - EFTA{ds_end:08d} ({ds_span:,} EFTA numbers)")
        print(f"- Documents: {ds_doc_count:,} PDFs, {ds_page_sum:,} total pages")
        print(f"- Missing: **{ds_missing:,} EFTA numbers** across {len(real_gaps):,} gaps")
        if overlaps:
            print(f"- Overlaps (page count anomalies): {len(overlaps):,}")
        print()

        if real_gaps:
            # Show gaps — for large datasets, summarize
            if len(real_gaps) > 50:
                # Show top 20 largest gaps + summary
                sorted_gaps = sorted(real_gaps, key=lambda g: g['missing_count'], reverse=True)
                print(f"### Largest gaps (top 20 of {len(real_gaps):,}):")
                print()
                print("| Missing Range | Count | After Document | Before Document |")
                print("|---------------|-------|----------------|-----------------|")
                for g in sorted_gaps[:20]:
                    print(f"| {g['missing_range']} | {g['missing_count']:,} | {g['after']} ({g['after_pages']}pp) | {g['before']} |")
                print()

                # Size distribution
                sizes = [g['missing_count'] for g in real_gaps]
                print(f"### Gap size distribution:")
                print(f"- 1 missing: {sum(1 for s in sizes if s == 1):,} gaps")
                print(f"- 2-10 missing: {sum(1 for s in sizes if 2 <= s <= 10):,} gaps")
                print(f"- 11-100 missing: {sum(1 for s in sizes if 11 <= s <= 100):,} gaps")
                print(f"- 101-1000 missing: {sum(1 for s in sizes if 101 <= s <= 1000):,} gaps")
                print(f"- 1001+ missing: {sum(1 for s in sizes if s > 1000):,} gaps")
            else:
                print("| Missing Range | Count | After Document | Before Document |")
                print("|---------------|-------|----------------|-----------------|")
                for g in real_gaps:
                    print(f"| {g['missing_range']} | {g['missing_count']:,} | {g['after']} ({g['after_pages']}pp) | {g['before']} |")
            print()

        if overlaps:
            print(f"### Page count anomalies (overlaps):")
            for g in overlaps[:10]:
                print(f"- {g['missing_range']}")
            if len(overlaps) > 10:
                print(f"- ... and {len(overlaps) - 10} more")
            print()

if __name__ == "__main__":
    find_gaps()
