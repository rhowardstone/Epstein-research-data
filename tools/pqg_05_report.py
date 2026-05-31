#!/usr/bin/env python3
"""
PQG Phase 5: Report Generation

Generates:
  - PROSECUTORIAL_QUERY_GRAPH_REPORT.md
  - CONCORDANCE_SUMMARY_REPORT.md
  - pqg_gaps.csv
"""

import sqlite3
import csv
import json
import os
from datetime import datetime
from collections import Counter

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

PQG_REPORT = f"{BASE_DIR}/epstein_files/PROSECUTORIAL_QUERY_GRAPH_REPORT.md"
CONC_REPORT = f"{BASE_DIR}/epstein_files/CONCORDANCE_SUMMARY_REPORT.md"
GAPS_CSV = f"{BASE_DIR}/epstein_files/pqg_gaps.csv"

INVESTIGATIVE_TRACKS = {
    "financial": {
        "label": "Financial Network",
        "target_categories": ["Financial Institution", "Money Transfer Service", "Cryptocurrency / Fintech"],
    },
    "travel": {
        "label": "Travel & Transportation",
        "target_categories": ["Airline / Travel"],
    },
    "communications": {
        "label": "Communications Surveillance",
        "target_categories": ["Telecommunications", "Technology Company"],
    },
    "employment": {
        "label": "Staff & Employment",
        "target_categories": [],
    },
    "institutional": {
        "label": "Institutional Connections",
        "target_categories": ["Educational Institution", "Government / Corrections"],
    },
    "legal": {
        "label": "Legal & Compliance",
        "target_categories": ["Law Firm / Attorney", "Credit Reporting Agency"],
    },
    "medical": {
        "label": "Medical & Forensic",
        "target_categories": [],
    },
}


def generate_pqg_report(pqg_conn, conc_conn):
    """Generate the main Prosecutorial Query Graph report."""
    cur = pqg_conn.cursor()

    lines = []
    lines.append("# Prosecutorial Query Graph Report")
    lines.append("")
    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    lines.append("")

    # ============================================================
    # Executive Summary
    # ============================================================
    lines.append("## Executive Summary")
    lines.append("")

    total_subpoenas = cur.execute("SELECT COUNT(*) FROM subpoenas").fetchone()[0]
    matched = cur.execute("SELECT COUNT(DISTINCT subpoena_id) FROM subpoena_return_links").fetchone()[0]
    total_returns = cur.execute("SELECT COUNT(*) FROM returns").fetchone()[0]
    total_clauses = cur.execute("SELECT COUNT(*) FROM rider_clauses").fetchone()[0]
    total_gaps = cur.execute("SELECT COUNT(*) FROM investigative_gaps").fetchone()[0]

    gap_counts = {}
    for (severity, count) in cur.execute(
        "SELECT severity, COUNT(*) FROM investigative_gaps GROUP BY severity"
    ).fetchall():
        gap_counts[severity] = count

    match_rate = matched / max(total_subpoenas, 1) * 100

    lines.append(f"- **{total_subpoenas}** Grand Jury subpoenas analyzed")
    lines.append(f"- **{total_clauses}** individual demand clauses decomposed")
    lines.append(f"- **{matched}** subpoenas matched to returns ({match_rate:.1f}% match rate)")
    lines.append(f"- **{total_returns}** return/production records linked")
    lines.append(f"- **{total_gaps}** investigative gaps identified:")
    for sev in ['CRITICAL', 'HIGH', 'MODERATE']:
        if sev in gap_counts:
            lines.append(f"  - {sev}: {gap_counts[sev]}")
    lines.append("")

    # ============================================================
    # Match Confidence Breakdown
    # ============================================================
    lines.append("## Match Confidence Breakdown")
    lines.append("")
    lines.append("| Confidence | Subpoenas | Links | Method |")
    lines.append("|------------|-----------|-------|--------|")

    cur.execute("""
        SELECT confidence, COUNT(DISTINCT subpoena_id), COUNT(*),
               GROUP_CONCAT(DISTINCT match_method)
        FROM subpoena_return_links GROUP BY confidence
    """)
    for conf, sub_count, link_count, methods in cur.fetchall():
        lines.append(f"| {conf} | {sub_count} | {link_count} | {methods} |")
    lines.append("")

    # ============================================================
    # Investigative Track Analysis
    # ============================================================
    lines.append("## Investigative Track Analysis")
    lines.append("")

    for track_id, track in INVESTIGATIVE_TRACKS.items():
        categories = track["target_categories"]
        if not categories:
            continue

        placeholders = ','.join('?' for _ in categories)
        cur.execute(f"""
            SELECT COUNT(*) FROM subpoenas WHERE target_category IN ({placeholders})
        """, categories)
        track_count = cur.fetchone()[0]

        cur.execute(f"""
            SELECT COUNT(DISTINCT s.id) FROM subpoenas s
            JOIN subpoena_return_links l ON s.id = l.subpoena_id
            WHERE s.target_category IN ({placeholders})
        """, categories)
        track_matched = cur.fetchone()[0]

        # Fulfillment stats for this track
        cur.execute(f"""
            SELECT cf.status, COUNT(*)
            FROM clause_fulfillment cf
            JOIN rider_clauses rc ON cf.clause_id = rc.id
            JOIN subpoenas s ON rc.subpoena_id = s.id
            WHERE s.target_category IN ({placeholders})
            GROUP BY cf.status
        """, categories)
        fulfillment = dict(cur.fetchall())

        lines.append(f"### {track['label']}")
        lines.append("")
        lines.append(f"- Subpoenas: {track_count} ({track_matched} matched)")
        if fulfillment:
            total_scored = sum(fulfillment.values())
            lines.append(f"- Clause fulfillment: "
                        f"{fulfillment.get('FULFILLED', 0)} fulfilled, "
                        f"{fulfillment.get('PARTIAL', 0)} partial, "
                        f"{fulfillment.get('UNFULFILLED', 0)} unfulfilled, "
                        f"{fulfillment.get('UNKNOWN', 0)} unknown "
                        f"(out of {total_scored})")
        lines.append("")

    # ============================================================
    # Top 20 Most Critical Gaps
    # ============================================================
    lines.append("## Top 20 Most Critical Gaps")
    lines.append("")

    cur.execute("""
        SELECT gap_type, severity, description, evidence
        FROM investigative_gaps
        ORDER BY
            CASE severity
                WHEN 'CRITICAL' THEN 1
                WHEN 'HIGH' THEN 2
                WHEN 'MODERATE' THEN 3
            END,
            gap_type
        LIMIT 20
    """)

    for i, (gap_type, severity, desc, evidence) in enumerate(cur.fetchall(), 1):
        lines.append(f"### {i}. [{severity}] {gap_type}")
        lines.append("")
        lines.append(desc)
        lines.append("")

    # ============================================================
    # Complete Subpoena-Return Match Table
    # ============================================================
    lines.append("## Subpoena-Return Match Table")
    lines.append("")
    lines.append("| EFTA | Target | Date | Category | Matched | Confidence | Clauses |")
    lines.append("|------|--------|------|----------|---------|------------|---------|")

    cur.execute("""
        SELECT s.efta_number, s.target, s.date_issued, s.target_category, s.clause_count,
               GROUP_CONCAT(DISTINCT l.confidence) as confidences,
               COUNT(DISTINCT l.return_id) as return_count
        FROM subpoenas s
        LEFT JOIN subpoena_return_links l ON s.id = l.subpoena_id
        GROUP BY s.id
        ORDER BY s.efta_number
    """)

    for efta, target, date_issued, category, clause_count, confidences, return_count in cur.fetchall():
        target_display = (target or "REDACTED")[:40]
        matched_str = f"{return_count}" if return_count > 0 else "No"
        conf_str = confidences or "-"
        lines.append(f"| {efta} | {target_display} | {date_issued or '-'} | {category} | "
                     f"{matched_str} | {conf_str} | {clause_count} |")
    lines.append("")

    # ============================================================
    # Unsubpoenaed High-Frequency Persons
    # ============================================================
    lines.append("## Unsubpoenaed High-Frequency Persons")
    lines.append("")

    cur.execute("""
        SELECT description, evidence FROM investigative_gaps
        WHERE gap_type = 'UNSUBPOENAED_ENTITY'
        ORDER BY severity DESC
        LIMIT 30
    """)
    unsubpoenaed = cur.fetchall()
    if unsubpoenaed:
        for desc, evidence in unsubpoenaed:
            lines.append(f"- {desc}")
    else:
        lines.append("*No unsubpoenaed high-frequency persons detected.*")
    lines.append("")

    # ============================================================
    # Cross-Dataset Discrepancies
    # ============================================================
    lines.append("## Cross-Dataset Discrepancies")
    lines.append("")

    cur.execute("""
        SELECT description, evidence FROM investigative_gaps
        WHERE gap_type = 'CROSS_DATASET_DISCREPANCY'
        ORDER BY severity DESC
        LIMIT 20
    """)
    discrepancies = cur.fetchall()
    if discrepancies:
        for desc, evidence in discrepancies:
            lines.append(f"- {desc}")
    else:
        lines.append("*No cross-dataset discrepancies detected (or concordance data unavailable).*")
    lines.append("")

    # ============================================================
    # Redacted Subpoenas
    # ============================================================
    lines.append("## Redacted Subpoena Targets")
    lines.append("")

    cur.execute("""
        SELECT efta_number, date_issued, total_pages, clause_count
        FROM subpoenas WHERE target = '[REDACTED]'
        ORDER BY efta_number
    """)
    redacted = cur.fetchall()
    lines.append(f"**{len(redacted)} subpoenas** have fully redacted targets:")
    lines.append("")
    if redacted:
        lines.append("| EFTA | Date | Pages | Clauses |")
        lines.append("|------|------|-------|---------|")
        for efta, date_issued, pages, clauses in redacted:
            lines.append(f"| {efta} | {date_issued or '-'} | {pages} | {clauses} |")
    lines.append("")

    # ============================================================
    # Gap Summary by Type
    # ============================================================
    lines.append("## Gap Summary by Type")
    lines.append("")
    lines.append("| Gap Type | CRITICAL | HIGH | MODERATE | Total |")
    lines.append("|----------|----------|------|----------|-------|")

    cur.execute("""
        SELECT gap_type,
               SUM(CASE WHEN severity = 'CRITICAL' THEN 1 ELSE 0 END),
               SUM(CASE WHEN severity = 'HIGH' THEN 1 ELSE 0 END),
               SUM(CASE WHEN severity = 'MODERATE' THEN 1 ELSE 0 END),
               COUNT(*)
        FROM investigative_gaps
        GROUP BY gap_type
        ORDER BY COUNT(*) DESC
    """)
    for gap_type, critical, high, moderate, total in cur.fetchall():
        lines.append(f"| {gap_type} | {critical} | {high} | {moderate} | {total} |")
    lines.append("")

    # Write report
    with open(PQG_REPORT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"Wrote PQG report: {PQG_REPORT}")
    return len(lines)


def generate_concordance_report(conc_conn):
    """Generate the concordance summary report."""
    if conc_conn is None:
        print("  concordance_complete.db not available, skipping concordance report")
        return 0

    cur = conc_conn.cursor()
    lines = []

    lines.append("# Concordance Summary Report")
    lines.append("")
    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    lines.append("")

    # ============================================================
    # Overall Stats
    # ============================================================
    lines.append("## Overall Statistics")
    lines.append("")

    total_docs = cur.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    total_pages = cur.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    lines.append(f"- **{total_docs:,}** total documents across all sources")
    lines.append(f"- **{total_pages:,}** total pages indexed")
    lines.append("")

    # By source
    lines.append("### Documents by Source")
    lines.append("")
    lines.append("| Source | Documents | Pages |")
    lines.append("|--------|-----------|-------|")

    cur.execute("""
        SELECT d.source, COUNT(DISTINCT d.id),
               (SELECT COUNT(*) FROM pages p WHERE p.source = d.source)
        FROM documents d
        GROUP BY d.source
        ORDER BY d.source
    """)
    for source, doc_count, page_count in cur.fetchall():
        lines.append(f"| {source} | {doc_count:,} | {page_count:,} |")
    lines.append("")

    # ============================================================
    # Metadata Coverage
    # ============================================================
    lines.append("## Metadata Coverage")
    lines.append("")
    lines.append("Fields with non-empty values across all sources:")
    lines.append("")
    lines.append("| Field | Count | % of Total |")
    lines.append("|-------|-------|-----------|")

    for field in ['author', 'email_from', 'email_to', 'email_cc', 'email_bcc',
                  'email_subject', 'original_filename', 'md5_hash',
                  'original_folder_path', 'date_created', 'date_sent',
                  'date_received', 'document_extension', 'custodian',
                  'parent_document_id', 'document_title', 'file_size']:
        count = cur.execute(
            f"SELECT COUNT(*) FROM documents WHERE {field} IS NOT NULL AND {field} != ''"
        ).fetchone()[0]
        pct = count / max(total_docs, 1) * 100
        if count > 0:
            lines.append(f"| {field} | {count:,} | {pct:.2f}% |")
    lines.append("")

    # ============================================================
    # Email Metadata Summary
    # ============================================================
    lines.append("## Email Metadata")
    lines.append("")

    email_count = cur.execute(
        "SELECT COUNT(*) FROM documents WHERE email_from IS NOT NULL AND email_from != ''"
    ).fetchone()[0]
    lines.append(f"**{email_count} emails** with header metadata extracted.")
    lines.append("")

    if email_count > 0:
        lines.append("### Email Senders (Top 20)")
        lines.append("")
        cur.execute("""
            SELECT email_from, COUNT(*) as cnt
            FROM documents
            WHERE email_from IS NOT NULL AND email_from != ''
            GROUP BY email_from
            ORDER BY cnt DESC
            LIMIT 20
        """)
        for sender, count in cur.fetchall():
            lines.append(f"- {sender}: {count}")
        lines.append("")

        lines.append("### Email Subjects (Sample)")
        lines.append("")
        cur.execute("""
            SELECT bates_begin, email_from, email_to, email_subject, date_sent
            FROM documents
            WHERE email_subject IS NOT NULL AND email_subject != ''
            LIMIT 20
        """)
        lines.append("| Bates | From | To | Subject | Date |")
        lines.append("|-------|------|----|---------|------|")
        for bates, frm, to, subj, date_sent in cur.fetchall():
            lines.append(f"| {bates} | {(frm or '')[:30]} | {(to or '')[:30]} | {(subj or '')[:40]} | {date_sent or '-'} |")
        lines.append("")

    # ============================================================
    # Folder Structure Analysis
    # ============================================================
    lines.append("## Folder Structure Analysis")
    lines.append("")

    cur.execute("""
        SELECT source_device, SUM(doc_count), COUNT(*)
        FROM folder_inventory
        GROUP BY source_device
        ORDER BY SUM(doc_count) DESC
    """)
    folder_data = cur.fetchall()
    if folder_data:
        lines.append("| Source Device | Documents | Folders |")
        lines.append("|--------------|-----------|---------|")
        for device, doc_count, folder_count in folder_data:
            lines.append(f"| {device} | {doc_count} | {folder_count} |")
        lines.append("")

        # Top folders
        lines.append("### Top Folder Paths")
        lines.append("")
        cur.execute("""
            SELECT folder_path, doc_count, source_device
            FROM folder_inventory
            ORDER BY doc_count DESC
            LIMIT 15
        """)
        for path, count, device in cur.fetchall():
            lines.append(f"- `{path}` ({count} docs, {device})")
        lines.append("")

    # ============================================================
    # Cross-References
    # ============================================================
    lines.append("## Cross-Reference Hits")
    lines.append("")

    cur.execute("SELECT match_type, COUNT(*) FROM cross_references GROUP BY match_type")
    xref_data = cur.fetchall()
    if xref_data:
        for match_type, count in xref_data:
            lines.append(f"- **{match_type}**: {count} matches")
    else:
        lines.append("*No cross-references found between sources.*")
    lines.append("")

    # ============================================================
    # Duplicate Detection (MD5)
    # ============================================================
    lines.append("## Duplicate Detection (MD5)")
    lines.append("")

    md5_dupes = cur.execute(
        "SELECT COUNT(*) FROM cross_references WHERE match_type = 'md5_match'"
    ).fetchone()[0]
    if md5_dupes > 0:
        lines.append(f"**{md5_dupes}** MD5 duplicate pairs detected.")
        lines.append("")
        cur.execute("""
            SELECT da.bates_begin, da.source, db.bates_begin, db.source, cr.notes
            FROM cross_references cr
            JOIN documents da ON cr.doc_id_a = da.id
            JOIN documents db ON cr.doc_id_b = db.id
            WHERE cr.match_type = 'md5_match'
            LIMIT 10
        """)
        lines.append("| Doc A | Source A | Doc B | Source B | MD5 |")
        lines.append("|-------|---------|-------|---------|-----|")
        for ba, sa, bb, sb, notes in cur.fetchall():
            lines.append(f"| {ba} | {sa} | {bb} | {sb} | {(notes or '')[:20]} |")
    else:
        lines.append("*No MD5 duplicates found.*")
    lines.append("")

    # ============================================================
    # Extraction Statistics
    # ============================================================
    lines.append("## Extraction Statistics")
    lines.append("")

    cur.execute("SELECT source, total_documents, total_pages, fields_available, extraction_timestamp FROM extraction_stats")
    stats = cur.fetchall()
    if stats:
        lines.append("| Source | Documents | Pages | Fields | Timestamp |")
        lines.append("|--------|-----------|-------|--------|-----------|")
        for source, docs, pages, fields, ts in stats:
            lines.append(f"| {source} | {docs or '-'} | {pages or '-'} | {fields or '-'} | {(ts or '')[:19]} |")
    lines.append("")

    # Write report
    with open(CONC_REPORT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"Wrote concordance report: {CONC_REPORT}")
    return len(lines)


def export_gaps_csv(pqg_conn):
    """Export all investigative gaps to CSV."""
    cur = pqg_conn.cursor()

    cur.execute("""
        SELECT gap_type, severity, description, related_subpoena_ids, related_clause_ids, evidence
        FROM investigative_gaps
        ORDER BY
            CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MODERATE' THEN 3 END,
            gap_type
    """)

    fieldnames = ['gap_type', 'severity', 'description', 'related_subpoena_ids', 'related_clause_ids', 'evidence']
    with open(GAPS_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in cur.fetchall():
            writer.writerow(dict(zip(fieldnames, row)))

    count = cur.execute("SELECT COUNT(*) FROM investigative_gaps").fetchone()[0]
    print(f"Exported {count} gaps to {GAPS_CSV}")
    return count


def main():
    print("=" * 80)
    print("PQG Phase 5: Report Generation")
    print("=" * 80)
    start_time = datetime.now()

    pqg_conn = sqlite3.connect(PQG_DB)
    conc_conn = sqlite3.connect(CONCORDANCE_DB) if os.path.exists(CONCORDANCE_DB) else None

    # Generate reports
    pqg_lines = generate_pqg_report(pqg_conn, conc_conn)
    conc_lines = generate_concordance_report(conc_conn)
    gap_count = export_gaps_csv(pqg_conn)

    print(f"\n{'=' * 80}")
    print("REPORT GENERATION COMPLETE")
    print(f"{'=' * 80}")
    print(f"  PQG Report: {PQG_REPORT} ({pqg_lines} lines)")
    print(f"  Concordance Report: {CONC_REPORT} ({conc_lines} lines)")
    print(f"  Gaps CSV: {GAPS_CSV} ({gap_count} records)")

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\nCompleted in {elapsed:.1f} seconds")

    pqg_conn.close()
    if conc_conn:
        conc_conn.close()


if __name__ == "__main__":
    main()
