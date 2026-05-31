#!/usr/bin/env python3
"""
PQG Phase 2: Subpoena-to-Return Matching

Links subpoenas to their response documents using four matching strategies:
1. Explicit references (HIGH) — cover letters citing subpoena targets/dates
2. Concordance cross-refs (HIGH) — House Estate docs matching EFTA productions
3. Entity + temporal matching (MEDIUM) — same entity within plausible timeframe
4. Content keyword matching (LOW) — FTS5 search for target + data class terms

Input: prosecutorial_query_graph.db, concordance_complete.db, concordance_metadata.db, full_text_corpus.db
Output: prosecutorial_query_graph.db tables: returns, subpoena_return_links
        concordance_metadata.db.subpoena_efta_map populated
"""

import os
import sqlite3
import re
import json
from collections import defaultdict
from datetime import datetime, timedelta

def _find_base_dir():
    """Find the parent directory containing epstein_files/ and concordance_files/."""
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
CONCORDANCE_META_DB = f"{BASE_DIR}/concordance_files/concordance_metadata.db"
CORPUS_DB = f"{BASE_DIR}/epstein_files/full_text_corpus.db"


def add_tables(conn):
    """Add returns and subpoena_return_links tables to PQG database."""
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS returns (
            id INTEGER PRIMARY KEY,
            source TEXT,
            production_id INTEGER,
            sdny_bates_start TEXT,
            sdny_bates_end TEXT,
            efta_range_start TEXT,
            efta_range_end TEXT,
            page_count INTEGER,
            description TEXT,
            date_received TEXT,
            responding_entity TEXT
        );

        CREATE TABLE IF NOT EXISTS subpoena_return_links (
            id INTEGER PRIMARY KEY,
            subpoena_id INTEGER REFERENCES subpoenas(id),
            return_id INTEGER REFERENCES returns(id),
            confidence TEXT,
            match_method TEXT,
            match_evidence TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_returns_entity ON returns(responding_entity);
        CREATE INDEX IF NOT EXISTS idx_links_subpoena ON subpoena_return_links(subpoena_id);
        CREATE INDEX IF NOT EXISTS idx_links_return ON subpoena_return_links(return_id);
    """)
    conn.commit()


def parse_date_fuzzy(date_str):
    """Try to parse a date string into a datetime object."""
    if not date_str:
        return None

    formats = [
        '%B %d, %Y', '%B %d %Y', '%b %d, %Y', '%b %d %Y',
        '%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d',
        '%B %Y', '%b %Y',
    ]

    # Clean up common issues
    date_str = date_str.strip().rstrip('.')
    date_str = re.sub(r'\s+', ' ', date_str)

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    # Try extracting just month + year
    m = re.search(r'(\w+)\s+(\d{4})', date_str)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} 1, {m.group(2)}", '%B %d, %Y')
        except ValueError:
            pass

    return None


def normalize_entity_name(name):
    """Normalize an entity name for matching."""
    if not name:
        return ""
    name = name.lower().strip()
    # Remove common suffixes
    for suffix in [', n.a.', ' n.a.', ', inc.', ' inc.', ', llc', ' llc',
                   ', ltd', ' ltd', ' corp.', ' corporation', ' co.',
                   ' company', ', na', ' na']:
        name = name.replace(suffix, '')
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def load_productions(meta_conn):
    """Load production records from concordance_metadata.db."""
    cur = meta_conn.cursor()
    cur.execute("""
        SELECT id, efta_source, production_date, case_name,
               sdny_gm_start, sdny_gm_end, sdny_gm_start_num, sdny_gm_end_num,
               description, production_label, record_count
        FROM productions
    """)

    productions = []
    for row in cur.fetchall():
        productions.append({
            'id': row[0],
            'efta_source': row[1],
            'production_date': row[2],
            'case_name': row[3],
            'sdny_start': row[4],
            'sdny_end': row[5],
            'sdny_start_num': row[6],
            'sdny_end_num': row[7],
            'description': row[8] or '',
            'production_label': row[9] or '',
            'record_count': row[10],
        })

    return productions


def load_underscore_efta_ranges(meta_conn):
    """Load SDNY-to-EFTA range mappings."""
    cur = meta_conn.cursor()
    cur.execute("SELECT * FROM underscore_efta_ranges")
    columns = [desc[0] for desc in cur.description]
    ranges = []
    for row in cur.fetchall():
        ranges.append(dict(zip(columns, row)))
    return ranges


def strategy_explicit_references(pqg_conn, meta_conn, corpus_conn):
    """Strategy 1: Match via explicit references in cover letters and production indexes.

    Searches production descriptions for subpoena target names.
    """
    print("\n--- Strategy 1: Explicit References ---")
    pqg_cur = pqg_conn.cursor()

    # Load all subpoenas
    pqg_cur.execute("SELECT id, efta_number, target, date_issued, target_category FROM subpoenas")
    subpoenas = pqg_cur.fetchall()

    # Load productions
    productions = load_productions(meta_conn)

    matches = 0
    for sub_id, efta, target, date_issued, category in subpoenas:
        if not target or target == "[REDACTED]":
            continue

        target_norm = normalize_entity_name(target)
        target_words = set(target_norm.split())

        for prod in productions:
            desc_lower = prod['description'].lower()

            # Check if target name (or significant words) appears in description
            # Require at least 2 significant words match, or the full normalized name
            matching_words = sum(1 for w in target_words if len(w) > 3 and w in desc_lower)

            if target_norm in desc_lower or (len(target_words) > 1 and matching_words >= 2):
                # Create return record
                pqg_cur.execute("""
                    INSERT INTO returns (source, production_id, sdny_bates_start, sdny_bates_end,
                        efta_range_start, page_count, description, date_received, responding_entity)
                    VALUES ('production_index', ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    prod['id'], prod['sdny_start'], prod['sdny_end'],
                    prod['efta_source'], prod['record_count'],
                    prod['description'][:500], prod['production_date'], target,
                ))
                return_id = pqg_cur.lastrowid

                # Link subpoena to return
                evidence = f"Target '{target}' found in production description: {prod['description'][:200]}"
                pqg_cur.execute("""
                    INSERT INTO subpoena_return_links (subpoena_id, return_id, confidence, match_method, match_evidence)
                    VALUES (?, ?, 'HIGH', 'explicit_reference', ?)
                """, (sub_id, return_id, evidence))
                matches += 1

    pqg_conn.commit()
    print(f"  Found {matches} explicit reference matches")
    return matches


def strategy_concordance_xref(pqg_conn, conc_conn):
    """Strategy 2: Match via concordance cross-references.

    House Estate docs may have filenames/metadata identifying which subpoena they respond to.
    """
    print("\n--- Strategy 2: Concordance Cross-References ---")

    if conc_conn is None:
        print("  concordance_complete.db not available, skipping")
        return 0

    pqg_cur = pqg_conn.cursor()
    conc_cur = conc_conn.cursor()

    # Load House Estate docs with filenames that might reference subpoenas
    conc_cur.execute("""
        SELECT id, bates_begin, original_filename, document_title, email_subject, original_folder_path
        FROM documents
        WHERE source = 'HOUSE_ESTATE_7TH'
        AND (original_filename IS NOT NULL OR document_title IS NOT NULL)
    """)
    he_docs = conc_cur.fetchall()

    # Load subpoenas
    pqg_cur.execute("SELECT id, efta_number, target, date_issued FROM subpoenas WHERE target != '[REDACTED]'")
    subpoenas = pqg_cur.fetchall()

    matches = 0
    for sub_id, efta, target, date_issued in subpoenas:
        target_norm = normalize_entity_name(target)
        if len(target_norm) < 4:
            continue

        for doc_id, bates, filename, title, subject, folder in he_docs:
            # Check if target appears in any text field
            searchable = ' '.join(filter(None, [filename, title, subject, folder])).lower()
            if target_norm in searchable:
                pqg_cur.execute("""
                    INSERT INTO returns (source, efta_range_start, description, responding_entity)
                    VALUES ('concordance_xref', ?, ?, ?)
                """, (bates, f"House Estate doc {bates}: {filename or title}", target))
                return_id = pqg_cur.lastrowid

                pqg_cur.execute("""
                    INSERT INTO subpoena_return_links (subpoena_id, return_id, confidence, match_method, match_evidence)
                    VALUES (?, ?, 'HIGH', 'concordance_xref', ?)
                """, (sub_id, return_id, f"Target '{target}' in House Estate metadata: {filename or title}"))
                matches += 1

    pqg_conn.commit()
    print(f"  Found {matches} concordance cross-reference matches")
    return matches


def strategy_entity_temporal(pqg_conn, meta_conn):
    """Strategy 3: Match via entity name + temporal proximity.

    For each subpoena target, find productions from the same entity
    received within 14-120 days after subpoena issuance.
    """
    print("\n--- Strategy 3: Entity + Temporal Matching ---")
    pqg_cur = pqg_conn.cursor()

    # Load subpoenas with dates
    pqg_cur.execute("""
        SELECT id, efta_number, target, date_issued, target_category
        FROM subpoenas
        WHERE target != '[REDACTED]' AND date_issued IS NOT NULL AND date_issued != ''
    """)
    subpoenas = pqg_cur.fetchall()

    # Load productions with dates
    productions = load_productions(meta_conn)

    # Already-matched pairs (avoid duplicates from strategy 1)
    pqg_cur.execute("SELECT subpoena_id, return_id FROM subpoena_return_links")
    existing = set((row[0], row[1]) for row in pqg_cur.fetchall())
    existing_sub_ids = set(row[0] for row in existing)

    matches = 0
    for sub_id, efta, target, date_issued, category in subpoenas:
        # Skip if already matched with HIGH confidence
        if sub_id in existing_sub_ids:
            continue

        sub_date = parse_date_fuzzy(date_issued)
        if not sub_date:
            continue

        target_norm = normalize_entity_name(target)
        if len(target_norm) < 4:
            continue

        for prod in productions:
            prod_date = parse_date_fuzzy(prod['production_date'])
            if not prod_date:
                continue

            # Check temporal window: 14-365 days after subpoena
            delta = (prod_date - sub_date).days
            if not (14 <= delta <= 365):
                continue

            # Check entity match in description
            desc_norm = prod['description'].lower()
            target_words = [w for w in target_norm.split() if len(w) > 3]

            word_matches = sum(1 for w in target_words if w in desc_norm)
            if target_norm in desc_norm or (len(target_words) > 1 and word_matches >= 2):
                pqg_cur.execute("""
                    INSERT INTO returns (source, production_id, sdny_bates_start, sdny_bates_end,
                        efta_range_start, page_count, description, date_received, responding_entity)
                    VALUES ('entity_temporal', ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    prod['id'], prod['sdny_start'], prod['sdny_end'],
                    prod['efta_source'], prod['record_count'],
                    prod['description'][:500], prod['production_date'], target,
                ))
                return_id = pqg_cur.lastrowid

                pqg_cur.execute("""
                    INSERT INTO subpoena_return_links (subpoena_id, return_id, confidence, match_method, match_evidence)
                    VALUES (?, ?, 'MEDIUM', 'entity_temporal', ?)
                """, (sub_id, return_id,
                      f"Target '{target}' ~matched production {delta} days later: {prod['description'][:200]}"))
                matches += 1

    pqg_conn.commit()
    print(f"  Found {matches} entity+temporal matches")
    return matches


def strategy_content_keyword(pqg_conn, corpus_conn):
    """Strategy 4: Match via FTS5 content keyword search.

    For unmatched subpoenas, search corpus for target name near
    transmittal letter keywords.
    """
    print("\n--- Strategy 4: Content Keyword Matching ---")
    pqg_cur = pqg_conn.cursor()

    # Find unmatched subpoenas
    pqg_cur.execute("""
        SELECT s.id, s.efta_number, s.target, s.target_category
        FROM subpoenas s
        WHERE s.target != '[REDACTED]'
        AND s.id NOT IN (SELECT DISTINCT subpoena_id FROM subpoena_return_links)
    """)
    unmatched = pqg_cur.fetchall()
    print(f"  {len(unmatched)} unmatched subpoenas to search for")

    corpus_cur = corpus_conn.cursor()
    matches = 0

    for sub_id, efta, target, category in unmatched:
        target_words = [w for w in target.split() if len(w) > 3]
        if not target_words:
            continue

        # Build FTS5 query: target words + transmittal keywords
        # Search for pages mentioning the target near legal/production language
        fts_query = ' '.join(f'"{w}"' for w in target_words[:3])

        try:
            corpus_cur.execute("""
                SELECT efta_number, page_number, snippet(pages_fts, 2, '>>>', '<<<', '...', 50)
                FROM pages_fts
                WHERE pages_fts MATCH ?
                AND efta_number != ?
                LIMIT 20
            """, (fts_query, efta))

            results = corpus_cur.fetchall()
        except Exception:
            continue

        # Filter for results that look like transmittal/cover letters
        transmittal_keywords = [
            'pursuant to', 'in response to', 'subpoena', 'enclosed',
            'production', 'herewith', 'attached', 'records responsive',
            'grand jury', 'compliance with',
        ]

        for res_efta, page_num, snippet_text in results:
            snippet_lower = snippet_text.lower()
            if any(kw in snippet_lower for kw in transmittal_keywords):
                pqg_cur.execute("""
                    INSERT INTO returns (source, efta_range_start, description, responding_entity)
                    VALUES ('corpus_match', ?, ?, ?)
                """, (res_efta, f"FTS match at {res_efta} p.{page_num}: {snippet_text[:200]}", target))
                return_id = pqg_cur.lastrowid

                pqg_cur.execute("""
                    INSERT INTO subpoena_return_links (subpoena_id, return_id, confidence, match_method, match_evidence)
                    VALUES (?, ?, 'LOW', 'content_keyword', ?)
                """, (sub_id, return_id, f"FTS: '{fts_query}' matched at {res_efta}:{page_num}"))
                matches += 1
                break  # One match per subpoena for this strategy

    pqg_conn.commit()
    print(f"  Found {matches} content keyword matches")
    return matches


def populate_subpoena_efta_map(pqg_conn, meta_conn):
    """Populate concordance_metadata.db.subpoena_efta_map with HIGH-confidence matches."""
    print("\n--- Populating subpoena_efta_map ---")
    pqg_cur = pqg_conn.cursor()
    meta_cur = meta_conn.cursor()

    # Get all HIGH-confidence links
    pqg_cur.execute("""
        SELECT s.efta_number, s.target, r.sdny_bates_start, r.sdny_bates_end,
               r.description, r.production_id
        FROM subpoena_return_links l
        JOIN subpoenas s ON l.subpoena_id = s.id
        JOIN returns r ON l.return_id = r.id
        WHERE l.confidence = 'HIGH'
    """)

    inserted = 0
    for sub_efta, target, sdny_start, sdny_end, desc, prod_id in pqg_cur.fetchall():
        try:
            meta_cur.execute("""
                INSERT INTO subpoena_efta_map (subpoena_efta, subpoena_target, return_sdny_start,
                    return_sdny_end, return_description, production_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (sub_efta, target, sdny_start, sdny_end, desc, prod_id))
            inserted += 1
        except sqlite3.IntegrityError:
            pass

    meta_conn.commit()
    print(f"  Inserted {inserted} HIGH-confidence mappings into subpoena_efta_map")
    return inserted


def main():
    print("=" * 80)
    print("PQG Phase 2: Subpoena-to-Return Matching")
    print("=" * 80)
    start_time = datetime.now()

    import os

    # Connect to databases
    pqg_conn = sqlite3.connect(PQG_DB)
    add_tables(pqg_conn)

    # Clear existing matches for re-run
    pqg_conn.execute("DELETE FROM subpoena_return_links")
    pqg_conn.execute("DELETE FROM returns")
    pqg_conn.commit()

    meta_conn = sqlite3.connect(CONCORDANCE_META_DB) if os.path.exists(CONCORDANCE_META_DB) else None

    conc_conn = None
    if os.path.exists(CONCORDANCE_DB):
        conc_conn = sqlite3.connect(CONCORDANCE_DB)

    corpus_conn = sqlite3.connect(CORPUS_DB)

    # Run matching strategies
    s1 = strategy_explicit_references(pqg_conn, meta_conn, corpus_conn)
    s2 = strategy_concordance_xref(pqg_conn, conc_conn)
    s3 = strategy_entity_temporal(pqg_conn, meta_conn)
    s4 = strategy_content_keyword(pqg_conn, corpus_conn)

    # Populate subpoena_efta_map
    if meta_conn:
        # Clear existing
        meta_conn.execute("DELETE FROM subpoena_efta_map")
        meta_conn.commit()
        populate_subpoena_efta_map(pqg_conn, meta_conn)

    # Summary
    pqg_cur = pqg_conn.cursor()
    print(f"\n{'=' * 80}")
    print("MATCHING SUMMARY")
    print(f"{'=' * 80}")

    total_subpoenas = pqg_cur.execute("SELECT COUNT(*) FROM subpoenas").fetchone()[0]
    matched_subpoenas = pqg_cur.execute(
        "SELECT COUNT(DISTINCT subpoena_id) FROM subpoena_return_links"
    ).fetchone()[0]
    total_returns = pqg_cur.execute("SELECT COUNT(*) FROM returns").fetchone()[0]
    total_links = pqg_cur.execute("SELECT COUNT(*) FROM subpoena_return_links").fetchone()[0]

    print(f"Total subpoenas: {total_subpoenas}")
    print(f"Matched subpoenas: {matched_subpoenas} ({matched_subpoenas/max(total_subpoenas,1)*100:.1f}%)")
    print(f"Unmatched subpoenas: {total_subpoenas - matched_subpoenas}")
    print(f"Total returns: {total_returns}")
    print(f"Total links: {total_links}")

    print("\n--- By Confidence ---")
    pqg_cur.execute("""
        SELECT confidence, COUNT(DISTINCT subpoena_id), COUNT(*)
        FROM subpoena_return_links
        GROUP BY confidence
    """)
    for conf, sub_count, link_count in pqg_cur.fetchall():
        print(f"  {conf}: {sub_count} subpoenas, {link_count} links")

    print("\n--- By Method ---")
    pqg_cur.execute("""
        SELECT match_method, COUNT(DISTINCT subpoena_id), COUNT(*)
        FROM subpoena_return_links
        GROUP BY match_method
    """)
    for method, sub_count, link_count in pqg_cur.fetchall():
        print(f"  {method}: {sub_count} subpoenas, {link_count} links")

    print("\n--- Top Matched Targets ---")
    pqg_cur.execute("""
        SELECT s.target, COUNT(DISTINCT l.return_id), GROUP_CONCAT(DISTINCT l.confidence)
        FROM subpoena_return_links l
        JOIN subpoenas s ON l.subpoena_id = s.id
        GROUP BY s.target
        ORDER BY COUNT(DISTINCT l.return_id) DESC
        LIMIT 15
    """)
    for target, return_count, confidences in pqg_cur.fetchall():
        print(f"  {target}: {return_count} returns ({confidences})")

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\nCompleted in {elapsed:.1f} seconds")

    # Close connections
    pqg_conn.close()
    if meta_conn:
        meta_conn.close()
    if conc_conn:
        conc_conn.close()
    corpus_conn.close()


if __name__ == "__main__":
    main()
