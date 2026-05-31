#!/usr/bin/env python3
"""
PQG Phase 4: Graph Construction & Gap Detection

Builds the prosecutorial query graph as node/edge structure and identifies
investigative gaps across 8 categories.

Input: prosecutorial_query_graph.db, concordance_complete.db, persons_registry.json
Output: prosecutorial_query_graph.db tables: graph_nodes, graph_edges, investigative_gaps
"""

import sqlite3
import json
import os
import csv
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
PERSONS_REGISTRY = f"{BASE_DIR}/Epstein-research-data/persons_registry.json"
PERSON_SEARCH_CSV = f"{BASE_DIR}/epstein_files/person_search_results.csv"

import re


def parse_date_fuzzy(date_str):
    """Try to parse a date string into a datetime object."""
    if not date_str:
        return None
    formats = [
        '%B %d, %Y', '%B %d %Y', '%b %d, %Y', '%b %d %Y',
        '%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d', '%B %Y', '%b %Y',
    ]
    date_str = date_str.strip().rstrip('.')
    date_str = re.sub(r'\s+', ' ', date_str)
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    m = re.search(r'(\w+)\s+(\d{4})', date_str)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} 1, {m.group(2)}", '%B %d, %Y')
        except ValueError:
            pass
    return None


# Investigative track definitions
INVESTIGATIVE_TRACKS = {
    "financial": {
        "label": "Financial Network",
        "target_categories": ["Financial Institution", "Money Transfer Service", "Cryptocurrency / Fintech"],
        "data_classes": ["bank_records", "corporate"],
        "description": "Banking, wire transfers, shell companies, trust structures",
    },
    "travel": {
        "label": "Travel & Transportation",
        "target_categories": ["Airline / Travel"],
        "data_classes": ["travel"],
        "description": "Flight manifests, private aviation, passenger records",
    },
    "communications": {
        "label": "Communications Surveillance",
        "target_categories": ["Telecommunications", "Technology Company"],
        "data_classes": ["phone_records", "email"],
        "description": "Phone records, email accounts, digital communications",
    },
    "employment": {
        "label": "Staff & Employment",
        "target_categories": ["Other Entity"],
        "data_classes": ["personnel"],
        "description": "Employee records, payroll, household staff",
    },
    "institutional": {
        "label": "Institutional Connections",
        "target_categories": ["Educational Institution", "Government / Corrections"],
        "data_classes": ["correctional", "identification"],
        "description": "Schools, government agencies, correctional facilities",
    },
    "legal": {
        "label": "Legal & Compliance",
        "target_categories": ["Law Firm / Attorney", "Credit Reporting Agency"],
        "data_classes": ["other"],
        "description": "Legal representation, credit reports, compliance records",
    },
    "medical": {
        "label": "Medical & Forensic",
        "target_categories": [],
        "data_classes": ["medical", "video"],
        "description": "Medical records, surveillance footage, forensic evidence",
    },
}


def create_graph_tables(conn):
    """Create graph and gap detection tables."""
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS graph_nodes (
            id INTEGER PRIMARY KEY,
            node_type TEXT,
            node_id TEXT,
            label TEXT,
            properties TEXT
        );

        CREATE TABLE IF NOT EXISTS graph_edges (
            id INTEGER PRIMARY KEY,
            source_node INTEGER REFERENCES graph_nodes(id),
            target_node INTEGER REFERENCES graph_nodes(id),
            edge_type TEXT,
            weight REAL,
            properties TEXT
        );

        CREATE TABLE IF NOT EXISTS investigative_gaps (
            id INTEGER PRIMARY KEY,
            gap_type TEXT,
            severity TEXT,
            description TEXT,
            related_subpoena_ids TEXT,
            related_clause_ids TEXT,
            evidence TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_nodes_type ON graph_nodes(node_type);
        CREATE INDEX IF NOT EXISTS idx_nodes_id ON graph_nodes(node_id);
        CREATE INDEX IF NOT EXISTS idx_edges_source ON graph_edges(source_node);
        CREATE INDEX IF NOT EXISTS idx_edges_target ON graph_edges(target_node);
        CREATE INDEX IF NOT EXISTS idx_gaps_type ON investigative_gaps(gap_type);
        CREATE INDEX IF NOT EXISTS idx_gaps_severity ON investigative_gaps(severity);
    """)
    conn.commit()


def build_graph(conn):
    """Build the query graph from subpoenas, returns, and clauses."""
    print("\n--- Building Graph ---")
    cur = conn.cursor()
    node_cache = {}  # (type, id) -> node_id

    def add_node(node_type, node_id, label, properties=None):
        key = (node_type, node_id)
        if key in node_cache:
            return node_cache[key]
        cur.execute("""
            INSERT INTO graph_nodes (node_type, node_id, label, properties)
            VALUES (?, ?, ?, ?)
        """, (node_type, node_id, label, json.dumps(properties) if properties else None))
        nid = cur.lastrowid
        node_cache[key] = nid
        return nid

    def add_edge(source, target, edge_type, weight=1.0, properties=None):
        cur.execute("""
            INSERT INTO graph_edges (source_node, target_node, edge_type, weight, properties)
            VALUES (?, ?, ?, ?, ?)
        """, (source, target, edge_type, weight, json.dumps(properties) if properties else None))

    # 1. Add investigative track nodes
    for track_id, track in INVESTIGATIVE_TRACKS.items():
        add_node("investigative_track", track_id, track["label"], track)

    # 2. Add subpoena nodes
    cur.execute("SELECT id, efta_number, target, target_category, date_issued, clause_count FROM subpoenas")
    subpoenas = cur.fetchall()

    for sub_id, efta, target, category, date_issued, clause_count in subpoenas:
        sub_node = add_node("subpoena", str(sub_id), f"Subpoena: {target or 'REDACTED'}",
                           {"efta": efta, "target": target, "category": category,
                            "date": date_issued, "clauses": clause_count})

        # Add target node (if not redacted)
        if target and target != "[REDACTED]":
            target_node = add_node("target", target, target, {"category": category})
            add_edge(sub_node, target_node, "demands")

        # Link to investigative track
        for track_id, track in INVESTIGATIVE_TRACKS.items():
            if category in track["target_categories"]:
                track_node = node_cache[("investigative_track", track_id)]
                add_edge(sub_node, track_node, "belongs_to_track")
                break

    # 3. Add data class nodes
    cur.execute("SELECT DISTINCT data_class FROM rider_clauses")
    for (data_class,) in cur.fetchall():
        dc_node = add_node("data_class", data_class, data_class.replace('_', ' ').title())

    # 4. Add return nodes and edges
    cur.execute("""
        SELECT r.id, r.source, r.responding_entity, r.description, r.date_received, r.page_count,
               l.subpoena_id, l.confidence, l.match_method
        FROM returns r
        JOIN subpoena_return_links l ON r.id = l.return_id
    """)

    for ret_id, source, entity, desc, date_recv, page_count, sub_id, conf, method in cur.fetchall():
        ret_node = add_node("return", str(ret_id), f"Return: {entity or 'Unknown'}",
                           {"source": source, "description": desc[:200] if desc else None,
                            "date": date_recv, "pages": page_count, "confidence": conf})

        sub_node = node_cache.get(("subpoena", str(sub_id)))
        if sub_node:
            weight = {"HIGH": 1.0, "MEDIUM": 0.7, "LOW": 0.4}.get(conf, 0.5)
            add_edge(sub_node, ret_node, "produces", weight, {"method": method, "confidence": conf})

    # 5. Link clauses to data classes
    cur.execute("""
        SELECT rc.id, rc.subpoena_id, rc.data_class, rc.clause_text
        FROM rider_clauses rc
    """)
    for clause_id, sub_id, data_class, clause_text in cur.fetchall():
        dc_node = node_cache.get(("data_class", data_class))
        sub_node = node_cache.get(("subpoena", str(sub_id)))
        if dc_node and sub_node:
            add_edge(sub_node, dc_node, "demands", properties={"clause_id": clause_id})

    conn.commit()

    # Summary
    node_count = cur.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
    edge_count = cur.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
    print(f"  Graph: {node_count} nodes, {edge_count} edges")

    cur.execute("SELECT node_type, COUNT(*) FROM graph_nodes GROUP BY node_type")
    for ntype, count in cur.fetchall():
        print(f"    {ntype}: {count}")

    cur.execute("SELECT edge_type, COUNT(*) FROM graph_edges GROUP BY edge_type")
    for etype, count in cur.fetchall():
        print(f"    {etype}: {count}")

    return node_count, edge_count


def detect_unfulfilled_demands(conn):
    """Gap: subpoena issued, no matching return found."""
    print("\n--- Detecting UNFULFILLED_DEMAND gaps ---")
    cur = conn.cursor()

    cur.execute("""
        SELECT s.id, s.efta_number, s.target, s.target_category, s.date_issued, s.clause_count
        FROM subpoenas s
        WHERE s.id NOT IN (SELECT DISTINCT subpoena_id FROM subpoena_return_links)
    """)

    gaps = 0
    for sub_id, efta, target, category, date_issued, clause_count in cur.fetchall():
        severity = "HIGH" if (target and target != "[REDACTED]" and clause_count > 0) else "MODERATE"

        cur.execute("""
            INSERT INTO investigative_gaps (gap_type, severity, description, related_subpoena_ids, evidence)
            VALUES ('UNFULFILLED_DEMAND', ?, ?, ?, ?)
        """, (
            severity,
            f"Subpoena to {target or 'REDACTED'} ({date_issued or 'undated'}) has no matched returns. "
            f"{clause_count} demand clauses with no identified responsive documents.",
            json.dumps([sub_id]),
            f"EFTA {efta}, category: {category}, target: {target}",
        ))
        gaps += 1

    conn.commit()
    print(f"  Found {gaps} unfulfilled demand gaps")
    return gaps


def detect_partial_responses(conn):
    """Gap: return exists but missing key data classes."""
    print("\n--- Detecting PARTIAL_RESPONSE gaps ---")
    cur = conn.cursor()

    # Find subpoenas with some fulfilled and some unfulfilled clauses
    cur.execute("""
        SELECT s.id, s.efta_number, s.target,
               SUM(CASE WHEN cf.status = 'FULFILLED' THEN 1 ELSE 0 END) as fulfilled,
               SUM(CASE WHEN cf.status = 'UNFULFILLED' THEN 1 ELSE 0 END) as unfulfilled,
               SUM(CASE WHEN cf.status = 'PARTIAL' THEN 1 ELSE 0 END) as partial_count,
               COUNT(DISTINCT cf.id) as total
        FROM subpoenas s
        JOIN rider_clauses rc ON rc.subpoena_id = s.id
        LEFT JOIN clause_fulfillment cf ON cf.clause_id = rc.id
        WHERE s.id IN (SELECT DISTINCT subpoena_id FROM subpoena_return_links)
        GROUP BY s.id
        HAVING unfulfilled > 0 AND fulfilled > 0
    """)

    gaps = 0
    for sub_id, efta, target, fulfilled, unfulfilled, partial_count, total in cur.fetchall():
        # Get unfulfilled data classes
        cur.execute("""
            SELECT DISTINCT rc.data_class FROM rider_clauses rc
            JOIN clause_fulfillment cf ON cf.clause_id = rc.id
            WHERE rc.subpoena_id = ? AND cf.status = 'UNFULFILLED'
        """, (sub_id,))
        missing_classes = [row[0] for row in cur.fetchall()]

        severity = "HIGH" if unfulfilled > fulfilled else "MODERATE"

        cur.execute("""
            INSERT INTO investigative_gaps (gap_type, severity, description, related_subpoena_ids, evidence)
            VALUES ('PARTIAL_RESPONSE', ?, ?, ?, ?)
        """, (
            severity,
            f"Subpoena to {target}: {fulfilled} clauses fulfilled, {unfulfilled} unfulfilled, "
            f"{partial_count} partial. Missing data classes: {', '.join(missing_classes)}.",
            json.dumps([sub_id]),
            f"EFTA {efta}, missing: {missing_classes}",
        ))
        gaps += 1

    conn.commit()
    print(f"  Found {gaps} partial response gaps")
    return gaps


def detect_unsubpoenaed_entities(conn):
    """Gap: known associates with 100+ doc hits who were never subpoenaed."""
    print("\n--- Detecting UNSUBPOENAED_ENTITY gaps ---")
    cur = conn.cursor()

    # Load person search results (high-frequency persons)
    if not os.path.exists(PERSON_SEARCH_CSV):
        print(f"  {PERSON_SEARCH_CSV} not found, skipping")
        return 0

    high_freq_persons = []
    with open(PERSON_SEARCH_CSV, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                doc_count = int(row.get('total_docs', row.get('doc_count', 0)))
            except (ValueError, TypeError):
                doc_count = 0
            if doc_count >= 100:
                high_freq_persons.append({
                    'name': row.get('name', ''),
                    'doc_count': doc_count,
                    'category': row.get('category', ''),
                })

    # Get all subpoena targets (normalized)
    cur.execute("SELECT target FROM subpoenas WHERE target IS NOT NULL AND target != '[REDACTED]'")
    subpoenaed = set()
    for (target,) in cur.fetchall():
        subpoenaed.add(target.lower().strip())

    gaps = 0
    for person in high_freq_persons:
        name = person['name']
        name_lower = name.lower().strip()
        category = person['category']

        # Skip if already subpoenaed
        if name_lower in subpoenaed:
            continue
        # Skip if any part of the name matches a subpoena target
        if any(name_lower in t or t in name_lower for t in subpoenaed if len(t) > 5):
            continue

        # Skip FOIA redaction markers and generic entries
        if name.startswith('(b)') or name.startswith('(') or len(name) < 4:
            continue

        # Skip if category suggests not relevant for subpoena
        if category in ('victim', 'other'):
            continue

        severity = "HIGH" if person['doc_count'] >= 500 else "MODERATE"

        cur.execute("""
            INSERT INTO investigative_gaps (gap_type, severity, description, evidence)
            VALUES ('UNSUBPOENAED_ENTITY', ?, ?, ?)
        """, (
            severity,
            f"'{name}' appears in {person['doc_count']} documents but was never subpoenaed. "
            f"Category: {category}.",
            f"Person: {name}, docs: {person['doc_count']}, category: {category}",
        ))
        gaps += 1

    conn.commit()
    print(f"  Found {gaps} unsubpoenaed entity gaps")
    return gaps


def detect_temporal_gaps(conn):
    """Gap: suspicious quiet periods between subpoena clusters."""
    print("\n--- Detecting TEMPORAL_GAP gaps ---")
    cur = conn.cursor()

    cur.execute("""
        SELECT id, efta_number, target, date_issued
        FROM subpoenas
        WHERE date_issued IS NOT NULL AND date_issued != ''
        ORDER BY date_issued
    """)

    subpoenas = cur.fetchall()

    # Parse dates and find gaps
    dated = []
    for sub_id, efta, target, date_str in subpoenas:
        # parse_date_fuzzy is defined at module level
        dt = parse_date_fuzzy(date_str)
        if dt:
            dated.append((dt, sub_id, efta, target, date_str))

    dated.sort(key=lambda x: x[0])

    gaps = 0
    for i in range(1, len(dated)):
        prev_date, prev_id, prev_efta, prev_target, prev_str = dated[i - 1]
        curr_date, curr_id, curr_efta, curr_target, curr_str = dated[i]

        gap_days = (curr_date - prev_date).days
        if gap_days > 90:  # More than 3 months
            severity = "CRITICAL" if gap_days > 365 else ("HIGH" if gap_days > 180 else "MODERATE")

            cur.execute("""
                INSERT INTO investigative_gaps (gap_type, severity, description,
                    related_subpoena_ids, evidence)
                VALUES ('TEMPORAL_GAP', ?, ?, ?, ?)
            """, (
                severity,
                f"{gap_days}-day gap between subpoenas ({prev_str} → {curr_str}). "
                f"Last target before gap: {prev_target}. First target after: {curr_target}.",
                json.dumps([prev_id, curr_id]),
                f"Gap: {prev_str} to {curr_str} ({gap_days} days)",
            ))
            gaps += 1

    conn.commit()
    print(f"  Found {gaps} temporal gaps")
    return gaps


def detect_redacted_targets(conn):
    """Gap: fully-redacted subpoenas — who are they?"""
    print("\n--- Detecting REDACTED_TARGET gaps ---")
    cur = conn.cursor()

    cur.execute("""
        SELECT id, efta_number, date_issued, clause_count, total_pages
        FROM subpoenas
        WHERE target = '[REDACTED]'
    """)

    gaps = 0
    for sub_id, efta, date_issued, clause_count, total_pages in cur.fetchall():
        severity = "HIGH" if clause_count > 3 else "MODERATE"

        cur.execute("""
            INSERT INTO investigative_gaps (gap_type, severity, description,
                related_subpoena_ids, evidence)
            VALUES ('REDACTED_TARGET', ?, ?, ?, ?)
        """, (
            severity,
            f"Subpoena {efta} ({date_issued or 'undated'}, {total_pages} pages, "
            f"{clause_count} clauses) has a fully redacted target. "
            f"The identity of this entity is deliberately concealed.",
            json.dumps([sub_id]),
            f"EFTA {efta}, pages: {total_pages}, clauses: {clause_count}",
        ))
        gaps += 1

    conn.commit()
    print(f"  Found {gaps} redacted target gaps")
    return gaps


def detect_follow_up_missing(conn):
    """Gap: initial subpoena returned partial data, no follow-up issued."""
    print("\n--- Detecting FOLLOW_UP_MISSING gaps ---")
    cur = conn.cursor()

    # Find targets that got PARTIAL fulfillment but no second subpoena
    cur.execute("""
        SELECT s.id, s.target, s.date_issued, s.target_category,
               COUNT(DISTINCT rc.id) as total_clauses,
               SUM(CASE WHEN cf.status = 'PARTIAL' THEN 1 ELSE 0 END) as partial_clauses,
               SUM(CASE WHEN cf.status = 'UNFULFILLED' THEN 1 ELSE 0 END) as unfulfilled_clauses
        FROM subpoenas s
        JOIN rider_clauses rc ON rc.subpoena_id = s.id
        LEFT JOIN clause_fulfillment cf ON cf.clause_id = rc.id
        WHERE s.target IS NOT NULL AND s.target != '[REDACTED]'
        GROUP BY s.id
        HAVING (partial_clauses + unfulfilled_clauses) > total_clauses / 2
    """)

    # Check which targets received only one subpoena
    target_subpoena_counts = {}
    cur.execute("SELECT target, COUNT(*) FROM subpoenas WHERE target != '[REDACTED]' GROUP BY target")
    for target, count in cur.fetchall():
        target_subpoena_counts[target] = count

    gaps = 0
    for sub_id, target, date_issued, category, total, partial, unfulfilled in cur.fetchall():
        if target_subpoena_counts.get(target, 0) <= 1:
            cur.execute("""
                INSERT INTO investigative_gaps (gap_type, severity, description,
                    related_subpoena_ids, evidence)
                VALUES ('FOLLOW_UP_MISSING', 'HIGH', ?, ?, ?)
            """, (
                f"Subpoena to {target} ({date_issued}) received only partial compliance "
                f"({partial} partial + {unfulfilled} unfulfilled out of {total} clauses) "
                f"but no follow-up subpoena was issued.",
                json.dumps([sub_id]),
                f"Target: {target}, partial: {partial}, unfulfilled: {unfulfilled}",
            ))
            gaps += 1

    conn.commit()
    print(f"  Found {gaps} follow-up missing gaps")
    return gaps


def detect_track_dead_ends(conn):
    """Gap: investigative track started strong then abruptly stopped."""
    print("\n--- Detecting INVESTIGATIVE_TRACK_DEAD_END gaps ---")
    cur = conn.cursor()

    for track_id, track in INVESTIGATIVE_TRACKS.items():
        # Count subpoenas in this track by date
        categories = track["target_categories"]
        if not categories:
            continue

        placeholders = ','.join('?' for _ in categories)
        cur.execute(f"""
            SELECT id, target, date_issued, target_category
            FROM subpoenas
            WHERE target_category IN ({placeholders})
            AND date_issued IS NOT NULL AND date_issued != ''
            ORDER BY date_issued
        """, categories)

        track_subpoenas = cur.fetchall()
        if len(track_subpoenas) < 3:
            continue

        # Check if the last subpoena in this track is significantly earlier
        # than the overall last subpoena
        cur.execute("""
            SELECT MAX(date_issued) FROM subpoenas
            WHERE date_issued IS NOT NULL AND date_issued != ''
        """)
        overall_latest = cur.fetchone()[0]

        last_in_track = track_subpoenas[-1][2]  # date_issued

        # Simple heuristic: if track ended more than 6 months before the overall end
        # parse_date_fuzzy is defined at module level
        overall_dt = parse_date_fuzzy(overall_latest)
        track_dt = parse_date_fuzzy(last_in_track)

        if overall_dt and track_dt and (overall_dt - track_dt).days > 180:
            gap_days = (overall_dt - track_dt).days
            cur.execute("""
                INSERT INTO investigative_gaps (gap_type, severity, description,
                    related_subpoena_ids, evidence)
                VALUES ('INVESTIGATIVE_TRACK_DEAD_END', 'HIGH', ?, ?, ?)
            """, (
                f"'{track['label']}' track had {len(track_subpoenas)} subpoenas but stopped "
                f"{gap_days} days before the last overall subpoena. "
                f"Last track subpoena: {last_in_track} to {track_subpoenas[-1][1]}.",
                json.dumps([s[0] for s in track_subpoenas[-3:]]),
                f"Track: {track_id}, last: {last_in_track}, overall last: {overall_latest}",
            ))

    conn.commit()
    print("  Track dead-end analysis complete")


def detect_cross_dataset_discrepancies(conn, conc_conn):
    """Gap: same document with different redaction levels across datasets."""
    print("\n--- Detecting CROSS_DATASET_DISCREPANCY gaps ---")

    if conc_conn is None:
        print("  concordance_complete.db not available, skipping")
        return 0

    cur = conn.cursor()
    conc_cur = conc_conn.cursor()

    # Find documents that appear in cross_references
    conc_cur.execute("""
        SELECT cr.doc_id_a, cr.doc_id_b, cr.match_type, cr.notes,
               da.source as source_a, da.bates_begin as bates_a, da.page_count as pages_a,
               db.source as source_b, db.bates_begin as bates_b, db.page_count as pages_b
        FROM cross_references cr
        JOIN documents da ON cr.doc_id_a = da.id
        JOIN documents db ON cr.doc_id_b = db.id
        WHERE da.source != db.source
    """)

    gaps = 0
    for (doc_a, doc_b, match_type, notes,
         source_a, bates_a, pages_a,
         source_b, bates_b, pages_b) in conc_cur.fetchall():

        # Flag if page counts differ (possible different redaction levels)
        if pages_a and pages_b and pages_a != pages_b:
            cur.execute("""
                INSERT INTO investigative_gaps (gap_type, severity, description, evidence)
                VALUES ('CROSS_DATASET_DISCREPANCY', 'MODERATE', ?, ?)
            """, (
                f"Document appears in {source_a} ({bates_a}, {pages_a} pages) and "
                f"{source_b} ({bates_b}, {pages_b} pages) with different page counts. "
                f"May indicate different redaction levels.",
                f"Match: {match_type}, {notes}",
            ))
            gaps += 1

    conn.commit()
    print(f"  Found {gaps} cross-dataset discrepancy gaps")
    return gaps


def main():
    print("=" * 80)
    print("PQG Phase 4: Graph Construction & Gap Detection")
    print("=" * 80)
    start_time = datetime.now()

    conn = sqlite3.connect(PQG_DB)
    create_graph_tables(conn)

    # Clear existing for re-run
    conn.execute("DELETE FROM graph_nodes")
    conn.execute("DELETE FROM graph_edges")
    conn.execute("DELETE FROM investigative_gaps")
    conn.commit()

    # Build graph
    build_graph(conn)

    # Detect gaps
    print("\n" + "=" * 40)
    print("GAP DETECTION")
    print("=" * 40)

    conc_conn = None
    if os.path.exists(CONCORDANCE_DB):
        conc_conn = sqlite3.connect(CONCORDANCE_DB)

    g1 = detect_unfulfilled_demands(conn)
    g2 = detect_partial_responses(conn)
    g3 = detect_unsubpoenaed_entities(conn)

    try:
        g4 = detect_temporal_gaps(conn)
    except Exception as e:
        print(f"  Temporal gap detection failed: {e}")
        g4 = 0

    g5 = detect_redacted_targets(conn)
    g6 = detect_follow_up_missing(conn)

    try:
        detect_track_dead_ends(conn)
    except Exception as e:
        print(f"  Track dead-end detection failed: {e}")

    g8 = detect_cross_dataset_discrepancies(conn, conc_conn)

    # Summary
    cur = conn.cursor()
    print(f"\n{'=' * 80}")
    print("GAP DETECTION SUMMARY")
    print(f"{'=' * 80}")

    cur.execute("SELECT gap_type, severity, COUNT(*) FROM investigative_gaps GROUP BY gap_type, severity ORDER BY gap_type, severity")
    print("\n--- Gaps by Type and Severity ---")
    for gap_type, severity, count in cur.fetchall():
        print(f"  {gap_type} [{severity}]: {count}")

    total_gaps = cur.execute("SELECT COUNT(*) FROM investigative_gaps").fetchone()[0]
    critical = cur.execute("SELECT COUNT(*) FROM investigative_gaps WHERE severity = 'CRITICAL'").fetchone()[0]
    high = cur.execute("SELECT COUNT(*) FROM investigative_gaps WHERE severity = 'HIGH'").fetchone()[0]
    moderate = cur.execute("SELECT COUNT(*) FROM investigative_gaps WHERE severity = 'MODERATE'").fetchone()[0]

    print(f"\nTotal gaps: {total_gaps}")
    print(f"  CRITICAL: {critical}")
    print(f"  HIGH: {high}")
    print(f"  MODERATE: {moderate}")

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\nCompleted in {elapsed:.1f} seconds")

    conn.close()
    if conc_conn:
        conc_conn.close()


if __name__ == "__main__":
    main()
