#!/usr/bin/env python3
"""
PQG Phase 3: Clause-Level Fulfillment Scoring

For each rider clause, assesses whether matched returns actually contain
the demanded data by sampling pages and checking for data-class keywords.

Input: prosecutorial_query_graph.db, concordance_complete.db, full_text_corpus.db
Output: prosecutorial_query_graph.db table: clause_fulfillment
"""

import os
import sqlite3
import re
from collections import Counter
from datetime import datetime

def _find_base_dir():
    """Find the parent directory containing epstein_files/."""
    if os.environ.get("EPSTEIN_DATA_DIR"):
        candidate = os.environ["EPSTEIN_DATA_DIR"]
        if os.path.basename(candidate) == "epstein_files":
            return os.path.dirname(candidate)
        if os.path.exists(os.path.join(candidate, "epstein_files")):
            return candidate
        return candidate
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parent = os.path.dirname(repo_root)
    if os.path.exists(os.path.join(parent, "epstein_files", "full_text_corpus.db")):
        return parent
    cwd_parent = os.path.dirname(os.getcwd())
    if os.path.exists(os.path.join(cwd_parent, "epstein_files", "full_text_corpus.db")):
        return cwd_parent
    if os.path.exists(os.path.join(os.getcwd(), "epstein_files", "full_text_corpus.db")):
        return os.getcwd()
    return os.getcwd()

BASE_DIR = _find_base_dir()
PQG_DB = f"{BASE_DIR}/epstein_files/prosecutorial_query_graph.db"
CONCORDANCE_DB = f"{BASE_DIR}/epstein_files/concordance_complete.db"
CORPUS_DB = f"{BASE_DIR}/epstein_files/full_text_corpus.db"

# Keywords that indicate fulfillment for each data class
FULFILLMENT_KEYWORDS = {
    'bank_records': [
        'account number', 'account no', 'balance', 'transaction',
        'deposit', 'withdrawal', 'wire transfer', 'statement',
        'debit', 'credit', 'check number', 'routing number',
    ],
    'phone_records': [
        'call detail', 'subscriber', 'cell site', 'tower',
        'phone number', 'incoming', 'outgoing', 'duration',
        'telephone number', 'mobile number',
    ],
    'email': [
        'from:', 'to:', 'subject:', 'sent:', 'received:',
        'inbox', 'email address', '@',
    ],
    'travel': [
        'passenger name', 'flight number', 'itinerary',
        'boarding', 'departure', 'arrival', 'pnr',
        'reservation', 'manifest',
    ],
    'personnel': [
        'employee', 'hire date', 'termination', 'salary',
        'payroll', 'position', 'department', 'social security',
    ],
    'video': [
        'surveillance', 'camera', 'footage', 'recording',
        'video', 'monitor', 'timestamp',
    ],
    'medical': [
        'diagnosis', 'treatment', 'patient', 'physician',
        'prescription', 'medical record', 'laboratory',
    ],
    'corporate': [
        'articles of incorporation', 'operating agreement',
        'registered agent', 'beneficial owner', 'officer',
        'director', 'shareholder', 'formation',
    ],
    'identification': [
        'identification', 'id number', 'driver license',
        'passport number', 'photograph', 'social security',
    ],
    'property': [
        'deed', 'mortgage', 'lease', 'title', 'property',
        'address', 'real estate', 'parcel',
    ],
    'correctional': [
        'inmate', 'booking', 'visitation', 'commissary',
        'cell assignment', 'housing', 'detention',
    ],
    'other': [
        'document', 'record', 'file', 'report',
    ],
}

# Max pages to sample per return for fulfillment check
MAX_SAMPLE_PAGES = 20


def add_fulfillment_table(conn):
    """Create clause_fulfillment table."""
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS clause_fulfillment (
            id INTEGER PRIMARY KEY,
            clause_id INTEGER REFERENCES rider_clauses(id),
            return_id INTEGER REFERENCES returns(id),
            status TEXT,
            evidence TEXT,
            page_count_relevant INTEGER,
            notes TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_fulfill_clause ON clause_fulfillment(clause_id);
        CREATE INDEX IF NOT EXISTS idx_fulfill_return ON clause_fulfillment(return_id);
        CREATE INDEX IF NOT EXISTS idx_fulfill_status ON clause_fulfillment(status);
    """)
    conn.commit()


def get_return_efta_range(pqg_conn, return_id):
    """Get EFTA range for a return, converting from various formats."""
    cur = pqg_conn.cursor()
    cur.execute("""
        SELECT efta_range_start, efta_range_end, sdny_bates_start, sdny_bates_end, page_count
        FROM returns WHERE id = ?
    """, (return_id,))
    row = cur.fetchone()
    if not row:
        return None, None

    efta_start, efta_end = row[0], row[1]

    # Try to extract EFTA numbers
    if efta_start and efta_start.startswith('EFTA'):
        start_num = int(re.search(r'(\d+)', efta_start).group(1))
        if efta_end and efta_end.startswith('EFTA'):
            end_num = int(re.search(r'(\d+)', efta_end).group(1))
        elif row[4]:  # Use page_count
            end_num = start_num + row[4] - 1
        else:
            end_num = start_num + 10  # Small default range
        return start_num, end_num

    return None, None


def sample_return_pages(corpus_conn, efta_start_num, efta_end_num, max_pages=MAX_SAMPLE_PAGES):
    """Sample pages from a return's EFTA range."""
    if efta_start_num is None:
        return []

    cur = corpus_conn.cursor()

    # Get first few pages and some random ones
    efta_start_str = f"EFTA{efta_start_num:08d}"
    efta_end_str = f"EFTA{efta_end_num:08d}"

    cur.execute("""
        SELECT p.efta_number, p.page_number, p.text_content
        FROM pages p
        WHERE p.efta_number >= ? AND p.efta_number <= ?
        AND p.text_content IS NOT NULL AND p.text_content != ''
        LIMIT ?
    """, (efta_start_str, efta_end_str, max_pages))

    return cur.fetchall()


def score_clause_fulfillment(clause_text, data_class, page_texts):
    """Score whether a set of pages fulfills a clause demand.

    Returns (status, evidence, relevant_page_count).
    """
    if not page_texts:
        return 'UNKNOWN', 'No pages available for review', 0

    keywords = FULFILLMENT_KEYWORDS.get(data_class, FULFILLMENT_KEYWORDS['other'])

    # Count pages with relevant content
    relevant_pages = 0
    evidence_snippets = []

    for efta, page_num, text in page_texts:
        text_lower = text.lower()
        matching_kws = [kw for kw in keywords if kw in text_lower]
        if matching_kws:
            relevant_pages += 1
            if len(evidence_snippets) < 3:
                evidence_snippets.append(
                    f"{efta}:p{page_num} matched: {', '.join(matching_kws[:3])}"
                )

    total_pages = len(page_texts)

    if relevant_pages == 0:
        status = 'UNFULFILLED'
        evidence = f"No {data_class} keywords found in {total_pages} sampled pages"
    elif relevant_pages >= 3 or relevant_pages / total_pages >= 0.3:
        status = 'FULFILLED'
        evidence = '; '.join(evidence_snippets)
    else:
        status = 'PARTIAL'
        evidence = f"{relevant_pages}/{total_pages} pages relevant; " + '; '.join(evidence_snippets)

    return status, evidence, relevant_pages


def check_date_range_coverage(clause_date_start, clause_date_end, page_texts):
    """Check if return pages cover the requested date range."""
    if not clause_date_start or not page_texts:
        return None

    # Look for dates in page texts
    date_pattern = r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b'
    found_years = set()

    for _, _, text in page_texts:
        for m in re.finditer(date_pattern, text):
            year = int(m.group(3))
            if year < 100:
                year += 2000
            if 1990 <= year <= 2025:
                found_years.add(year)

    if not found_years:
        return None

    # Try to parse the requested range
    try:
        start_year = int(re.search(r'(\d{4})', clause_date_start).group(1))
        end_year = int(re.search(r'(\d{4})', clause_date_end).group(1)) if clause_date_end else start_year
    except (AttributeError, ValueError):
        return None

    requested_years = set(range(start_year, end_year + 1))
    covered_years = found_years & requested_years
    missing_years = requested_years - found_years

    if missing_years and len(missing_years) > len(requested_years) * 0.3:
        return f"Date range gap: requested {start_year}-{end_year}, found years {sorted(found_years)}, missing {sorted(missing_years)}"

    return None


def main():
    print("=" * 80)
    print("PQG Phase 3: Clause-Level Fulfillment Scoring")
    print("=" * 80)
    start_time = datetime.now()

    import os

    pqg_conn = sqlite3.connect(PQG_DB)
    add_fulfillment_table(pqg_conn)

    # Clear existing for re-run
    pqg_conn.execute("DELETE FROM clause_fulfillment")
    pqg_conn.commit()

    corpus_conn = sqlite3.connect(CORPUS_DB)
    pqg_cur = pqg_conn.cursor()

    # Get all clauses linked to returns
    pqg_cur.execute("""
        SELECT rc.id, rc.subpoena_id, rc.clause_text, rc.data_class,
               rc.date_range_start, rc.date_range_end,
               s.target, s.efta_number
        FROM rider_clauses rc
        JOIN subpoenas s ON rc.subpoena_id = s.id
        ORDER BY rc.subpoena_id, rc.clause_number
    """)
    all_clauses = pqg_cur.fetchall()
    print(f"Total clauses to score: {len(all_clauses)}")

    # Get subpoena-to-return links
    pqg_cur.execute("""
        SELECT subpoena_id, return_id, confidence
        FROM subpoena_return_links
        ORDER BY subpoena_id
    """)
    links_by_subpoena = {}
    for sub_id, ret_id, conf in pqg_cur.fetchall():
        if sub_id not in links_by_subpoena:
            links_by_subpoena[sub_id] = []
        links_by_subpoena[sub_id].append((ret_id, conf))

    status_counts = Counter()
    scored = 0
    # Cache sampled pages per return to avoid re-reading
    page_cache = {}

    for clause_id, sub_id, clause_text, data_class, date_start, date_end, target, efta in all_clauses:
        returns = links_by_subpoena.get(sub_id, [])

        if not returns:
            # No returns linked to this subpoena
            pqg_cur.execute("""
                INSERT INTO clause_fulfillment (clause_id, return_id, status, evidence, page_count_relevant, notes)
                VALUES (?, NULL, 'UNKNOWN', 'No returns linked to parent subpoena', 0, ?)
            """, (clause_id, f"Subpoena {efta} → {target}: no matched returns"))
            status_counts['UNKNOWN'] += 1
            scored += 1
            continue

        # Score against each linked return
        best_status = 'UNKNOWN'
        for ret_id, confidence in returns:
            if ret_id not in page_cache:
                efta_start, efta_end = get_return_efta_range(pqg_conn, ret_id)
                if efta_start:
                    page_cache[ret_id] = sample_return_pages(corpus_conn, efta_start, efta_end)
                else:
                    page_cache[ret_id] = []

            pages = page_cache[ret_id]
            status, evidence, relevant_count = score_clause_fulfillment(
                clause_text, data_class, pages
            )

            # Check date range coverage
            notes = None
            if date_start:
                date_note = check_date_range_coverage(date_start, date_end, pages)
                if date_note:
                    notes = date_note
                    if status == 'FULFILLED':
                        status = 'PARTIAL'
                        evidence += f"; {date_note}"

            pqg_cur.execute("""
                INSERT INTO clause_fulfillment (clause_id, return_id, status, evidence, page_count_relevant, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (clause_id, ret_id, status, evidence, relevant_count, notes))

            # Track best status
            priority = {'FULFILLED': 3, 'PARTIAL': 2, 'UNFULFILLED': 1, 'UNKNOWN': 0}
            if priority.get(status, 0) > priority.get(best_status, 0):
                best_status = status

            scored += 1

        status_counts[best_status] += 1

    pqg_conn.commit()

    # Summary
    print(f"\n{'=' * 80}")
    print("FULFILLMENT SCORING SUMMARY")
    print(f"{'=' * 80}")

    total_clauses = len(all_clauses)
    linked_clauses = sum(1 for c in all_clauses if c[1] in links_by_subpoena)
    print(f"Total clauses: {total_clauses}")
    print(f"Clauses with linked returns: {linked_clauses}")
    print(f"Fulfillment records created: {scored}")

    print("\n--- Status Distribution (best per clause) ---")
    for status, count in status_counts.most_common():
        pct = count / max(total_clauses, 1) * 100
        print(f"  {status}: {count} ({pct:.1f}%)")

    # Per data class
    pqg_cur.execute("""
        SELECT rc.data_class,
               SUM(CASE WHEN cf.status = 'FULFILLED' THEN 1 ELSE 0 END),
               SUM(CASE WHEN cf.status = 'PARTIAL' THEN 1 ELSE 0 END),
               SUM(CASE WHEN cf.status = 'UNFULFILLED' THEN 1 ELSE 0 END),
               SUM(CASE WHEN cf.status = 'UNKNOWN' THEN 1 ELSE 0 END),
               COUNT(*)
        FROM clause_fulfillment cf
        JOIN rider_clauses rc ON cf.clause_id = rc.id
        GROUP BY rc.data_class
        ORDER BY COUNT(*) DESC
    """)
    print("\n--- Fulfillment by Data Class ---")
    print(f"  {'Data Class':<20} {'FULL':>6} {'PART':>6} {'UNFUL':>6} {'UNK':>6} {'Total':>6}")
    for data_class, fulfilled, partial, unfulfilled, unknown, total in pqg_cur.fetchall():
        print(f"  {data_class:<20} {fulfilled:>6} {partial:>6} {unfulfilled:>6} {unknown:>6} {total:>6}")

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\nCompleted in {elapsed:.1f} seconds")

    pqg_conn.close()
    corpus_conn.close()


if __name__ == "__main__":
    main()
