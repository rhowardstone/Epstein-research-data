#!/usr/bin/env python3
"""
Extract and organize all Grand Jury Subpoena RIDER documents from full_text_corpus.db.

Produces:
  1. GRAND_JURY_SUBPOENAS.csv — structured index of all subpoena riders
  2. Prints summary statistics and analysis
"""

import sqlite3
import csv
import re
import os
import json
from collections import Counter, defaultdict

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
OUTPUT_CSV = os.path.join(_DATA_DIR, "GRAND_JURY_SUBPOENAS.csv")

# EFTA-to-Dataset mapping
DATASET_RANGES = [
    (1,       3158,  1),
    (3159,    3857,  2),
    (3858,    5586,  3),
    (5705,    8320,  4),
    (8409,    8528,  5),
    (8529,    8998,  6),
    (9016,    9664,  7),
    (9676,   39023,  8),
    (39025, 1262781, 9),
    (1262782, 2205654, 10),
    (2205655, 2730264, 11),
    (2730265, 2731783, 12),
]

def efta_to_dataset(efta_num):
    """Map EFTA number to dataset number."""
    for start, end, ds in DATASET_RANGES:
        if start <= efta_num <= end:
            return ds
    # Gap handling: find nearest lower
    for start, end, ds in reversed(DATASET_RANGES):
        if efta_num >= start:
            return ds
    return None

def doj_url(efta_str):
    """Construct DOJ URL for an EFTA document."""
    num = int(efta_str.replace("EFTA", ""))
    ds = efta_to_dataset(num)
    if ds is None:
        return ""
    return f"https://www.justice.gov/epstein/files/DataSet%20{ds}/{efta_str}.pdf"

def extract_rider_documents(conn):
    """Find all documents with RIDER pages."""
    cur = conn.cursor()

    # Find all pages containing formal RIDER text
    # Include: "RIDER (Grand Jury Subpoena to...", "SUBPOENA RIDER" (standalone header)
    # Exclude: FBI internal emails that merely DISCUSS riders
    cur.execute("""
        SELECT efta_number, page_number, text_content
        FROM pages
        WHERE (text_content LIKE '%RIDER%Grand Jury Subpoena%'
               OR text_content LIKE '%RIDER%Subpoena to%'
               OR (text_content LIKE '%SUBPOENA RIDER%'
                   AND text_content NOT LIKE '%From:%To:%Subject:%'))
        ORDER BY efta_number, page_number
    """)

    rider_pages = cur.fetchall()
    print(f"Found {len(rider_pages)} RIDER pages across documents")

    # Group by document, filtering out false positives
    docs = defaultdict(list)
    for efta, page_num, text in rider_pages:
        # Skip court filings that mention "RIDER" in legal context, not as subpoena riders
        if 'Case 9:08-cv-80119' in text:
            continue
        # Skip pages where "RIDER" appears only in a legal citation context
        if 'Privilege Against Self-Incrimination' in text and 'RIDER' not in text[:100]:
            continue
        docs[efta].append((page_num, text))

    print(f"Found {len(docs)} unique documents with RIDER pages")
    return docs

def parse_rider_text(text):
    """Parse a RIDER page to extract target, date, and requested items."""
    result = {
        "target": "",
        "date": "",
        "statutes": [],
        "requested_categories": [],
        "raw_rider_header": "",
    }

    # Extract the RIDER header line
    # Pattern: RIDER (Grand Jury Subpoena to [TARGET], dated [DATE])
    # or: RIDER\n(Grand Jury Subpoena to [TARGET], dated [DATE])
    # Note: Many subpoenas have redacted targets — "RIDER (Grand Jury Subpoena to [blank] dated Aug 15, 2019)"
    header_patterns = [
        # Target + date, comma-separated
        r'RIDER\s*\(?(?:Grand Jury )?Subpoena\s+(?:to|for)\s+(.+?),?\s+dated\s+(\w+\s+\d{1,2},?\s+\d{4})\)?',
        # Target + date, no comma
        r'RIDER\s*\(?(?:Grand Jury )?Subpoena\s+(?:to|for)\s+(.+?)\s+dated\s+(\w+\s+\d{1,2},?\s+\d{4})\)?',
        # Broader date match
        r'RIDER\s*\(.*?(?:to|for)\s+(.+?),?\s+dated\s+(.+?)\)',
    ]

    for pattern in header_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            raw_target = match.group(1).strip()
            raw_date = match.group(2).strip()
            result["raw_rider_header"] = match.group(0).strip()

            # Clean up the date — remove trailing parentheses and junk
            raw_date = re.sub(r'\)\s*$', '', raw_date).strip()
            # Remove trailing non-date text
            date_match = re.match(r'(\w+\s+\d{1,2},?\s+\d{4})', raw_date)
            if date_match:
                result["date"] = date_match.group(1)
            else:
                result["date"] = raw_date

            # Check if target is actually empty/redacted
            # Redacted targets look like: empty string, or "dated August", or very short garbled text,
            # or "answer any question if a truthful..." (fell through to Advice of Rights text),
            # or "limit its coverage to compelled testimony..." (Advice of Rights boilerplate)
            junk_phrases = [
                'answer any question', 'refuse to answer', 'truthful answer',
                'incriminate you', 'ads ice of rights', 'advice of rights',
                'limit its coverage', 'compelled testimony', 'tend to incriminate',
                'the grand jury', 'this subpoena',
            ]
            target_lower = raw_target.lower().strip()
            is_junk = (
                not raw_target or
                raw_target.startswith('dated') or
                raw_target.startswith('\n') or
                len(raw_target.strip()) < 3 or
                len(raw_target) > 200 or
                any(phrase in target_lower for phrase in junk_phrases) or
                # "dated August 15" captured as target = redacted
                re.match(r'^dated\s', raw_target, re.IGNORECASE)
            )
            if is_junk:
                result["target"] = "[REDACTED]"
            else:
                # Clean up target — remove trailing newlines, junk, periods
                raw_target = raw_target.split('\n')[0].strip()
                raw_target = re.sub(r'[,.\s]+$', '', raw_target)
                result["target"] = raw_target
            break

    # If no structured header found, try to get the target from nearby text
    if not result["target"]:
        m = re.search(r'RIDER.*?(?:to|for)\s+(.+?)(?:\n|,|dated)', text, re.IGNORECASE | re.DOTALL)
        if m:
            raw = m.group(1).strip()[:200]
            if raw and len(raw) > 3 and 'answer any question' not in raw.lower():
                result["target"] = raw
            else:
                result["target"] = "[REDACTED]"
        else:
            # SUBPOENA RIDER format (non-Grand Jury)
            m2 = re.search(r'SUBPOENA RIDER\s*\n\s*(.+?)(?:\n|$)', text, re.IGNORECASE)
            if m2:
                result["target"] = m2.group(1).strip()[:200]

    # Extract statutes (18 U.S.C. § XXXX, etc.)
    statute_matches = re.findall(r'(\d+\s+U\.?S\.?C\.?\s*§+\s*\d+[a-z]?(?:\([a-z0-9]+\))?)', text, re.IGNORECASE)
    result["statutes"] = list(set(statute_matches))

    # Extract requested document categories
    # Look for numbered or lettered items after the header
    categories = []

    # Common patterns for requested items
    item_patterns = [
        r'(?:^|\n)\s*(?:\d+|[a-z])[.)]\s+(.+?)(?=\n\s*(?:\d+|[a-z])[.)]|\Z)',
        r'(?:^|\n)\s*[-•]\s+(.+?)(?=\n\s*[-•]|\Z)',
    ]

    # Find the rider content section (after the header, before any signature block)
    rider_section = text
    header_end = text.find("RIDER")
    if header_end >= 0:
        rider_section = text[header_end:]

    # Extract numbered/lettered items
    items = re.findall(r'(?:^|\n)\s*(?:\d+|[a-zA-Z])[.)]\s+(.+?)(?=\n\s*(?:\d+|[a-zA-Z])[.)]|\n\n|\Z)',
                       rider_section, re.DOTALL)

    for item in items:
        cleaned = item.strip().replace('\n', ' ')
        cleaned = re.sub(r'\s+', ' ', cleaned)
        if len(cleaned) > 10 and len(cleaned) < 500:
            categories.append(cleaned)

    result["requested_categories"] = categories

    return result

def classify_target(target):
    """Classify a subpoena target into a category."""
    target_lower = target.lower()

    # Banks / Financial
    bank_keywords = ['bank', 'chase', 'wells fargo', 'citibank', 'citi ', 'ubs', 'credit union',
                     'ameritrade', 'santander', 'capital one', 'firstbank', 'fifth third',
                     'navy federal', 'usaa', 'td bank', 'umb bank', 'gold coast', 'mcu',
                     'credit one', 'first premier', 'jpmorgan', 'deutsche', 'barclays',
                     'morgan stanley', 'goldman']
    if any(kw in target_lower for kw in bank_keywords):
        return "Financial Institution"

    # Tech companies
    tech_keywords = ['google', 'facebook', 'meta', 'amazon', 'apple', 'microsoft', 'oath',
                     'yahoo', 'lyft', 'uber', '4chan', 'paypal', 'venmo', 'square',
                     'twitter', 'snap', 'tiktok']
    if any(kw in target_lower for kw in tech_keywords):
        return "Technology Company"

    # Airlines / Travel
    airline_keywords = ['airlines', 'airline', 'airways', 'air ', 'jetblue', 'delta',
                       'united', 'southwest', 'alaska air', 'american air', 'expedia',
                       'reporting corporation', 'travelport', 'sabre']
    if any(kw in target_lower for kw in airline_keywords):
        return "Airline / Travel"

    # Telecom
    telecom_keywords = ['at&t', 'verizon', 't-mobile', 'sprint', 'centurylink', 'comcast',
                       'telephone', 'telecom', 'wireless', 'cellular']
    if any(kw in target_lower for kw in telecom_keywords):
        return "Telecommunications"

    # Credit agencies
    credit_keywords = ['experian', 'transunion', 'equifax', 'credit bureau']
    if any(kw in target_lower for kw in credit_keywords):
        return "Credit Reporting Agency"

    # Money transfer
    money_keywords = ['western union', 'moneygram', 'money transfer']
    if any(kw in target_lower for kw in money_keywords):
        return "Money Transfer Service"

    # Government / Institutional
    gov_keywords = ['mcc', 'metropolitan correctional', 'white house', 'department',
                    'bureau', 'federal', 'state of', 'county', 'city of',
                    'customs', 'border', 'immigration']
    if any(kw in target_lower for kw in gov_keywords):
        return "Government / Corrections"

    # Law firms
    law_keywords = ['esq', 'llp', 'law firm', 'attorney', 'counsel', 'boies', 'schiller']
    if any(kw in target_lower for kw in law_keywords):
        return "Law Firm / Attorney"

    # Educational
    edu_keywords = ['school', 'university', 'college', 'interlochen', 'academy', 'arts']
    if any(kw in target_lower for kw in edu_keywords):
        return "Educational Institution"

    # Crypto / Fintech
    crypto_keywords = ['iterative', 'otc', 'crypto', 'bitcoin', 'blockchain']
    if any(kw in target_lower for kw in crypto_keywords):
        return "Cryptocurrency / Fintech"

    # Auto / business
    auto_keywords = ['auto', 'motor', 'car ', 'vehicle']
    if any(kw in target_lower for kw in auto_keywords):
        return "Business Entity"

    # Check if it looks like a person name (no inc, llc, corp, etc.)
    corp_indicators = ['inc', 'llc', 'corp', 'ltd', 'company', 'co.', 'group',
                       'holdings', 'services', 'associates']
    if not any(ci in target_lower for ci in corp_indicators):
        # Likely a person name if short and no corp indicators
        words = target.split()
        if 1 < len(words) <= 5:
            return "Individual"

    return "Other Entity"

def classify_requested_docs(categories):
    """Classify requested document categories into broad types."""
    doc_types = set()

    for cat in categories:
        cat_lower = cat.lower()

        if any(kw in cat_lower for kw in ['account', 'financial', 'bank', 'deposit', 'withdrawal',
                                            'transaction', 'wire', 'transfer', 'balance', 'statement',
                                            'check', 'money', 'payment', 'fund']):
            doc_types.add("Financial Records")

        if any(kw in cat_lower for kw in ['email', 'communication', 'correspondence', 'message',
                                            'text', 'chat', 'call', 'phone', 'voicemail', 'letter']):
            doc_types.add("Communications")

        if any(kw in cat_lower for kw in ['travel', 'flight', 'passenger', 'itinerary', 'boarding',
                                            'reservation', 'ticket', 'pnr', 'manifest']):
            doc_types.add("Travel Records")

        if any(kw in cat_lower for kw in ['employ', 'personnel', 'hr ', 'human resource', 'hire',
                                            'salary', 'wage', 'payroll', 'job', 'position', 'staff']):
            doc_types.add("Employment Records")

        if any(kw in cat_lower for kw in ['subscriber', 'user', 'account holder', 'registration',
                                            'sign-up', 'profile', 'ip address', 'login']):
            doc_types.add("Subscriber / Account Info")

        if any(kw in cat_lower for kw in ['video', 'surveillance', 'camera', 'cctv', 'footage',
                                            'recording', 'audio']):
            doc_types.add("Surveillance / Media")

        if any(kw in cat_lower for kw in ['report', 'investigation', 'incident', 'complaint',
                                            'log', 'record of', 'memo', 'note']):
            doc_types.add("Reports / Logs")

        if any(kw in cat_lower for kw in ['credit', 'score', 'inquiry', 'bureau']):
            doc_types.add("Credit Reports")

        if any(kw in cat_lower for kw in ['document', 'record', 'file', 'paper', 'material']):
            doc_types.add("General Records")

        if any(kw in cat_lower for kw in ['photo', 'image', 'picture', 'identification', 'id ',
                                            'passport', 'license', 'credential']):
            doc_types.add("Identification / Photos")

        if any(kw in cat_lower for kw in ['contract', 'agreement', 'lease', 'deed', 'property',
                                            'real estate', 'mortgage']):
            doc_types.add("Contracts / Property")

        if any(kw in cat_lower for kw in ['inmate', 'prisoner', 'custody', 'cell', 'housing',
                                            'visitation', 'commissary', 'booking']):
            doc_types.add("Correctional Records")

    return sorted(doc_types) if doc_types else ["Unclassified"]


def get_doc_page_count(conn, efta_number):
    """Get total page count for a document."""
    cur = conn.cursor()
    cur.execute("SELECT total_pages FROM documents WHERE efta_number = ?", (efta_number,))
    row = cur.fetchone()
    return row[0] if row else 0


def main():
    conn = sqlite3.connect(DB_PATH)

    print("=" * 80)
    print("GRAND JURY SUBPOENA RIDER EXTRACTION")
    print("=" * 80)

    # Step 1: Find all RIDER documents
    rider_docs = extract_rider_documents(conn)

    # Step 2: Also get total subpoena-mentioning documents for context
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(DISTINCT efta_number) FROM pages
        WHERE text_content LIKE '%SUBPOENA%' OR text_content LIKE '%subpoena%'
    """)
    total_subpoena_docs = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM documents")
    total_docs = cur.fetchone()[0]

    print(f"\nCorpus: {total_docs:,} total documents")
    print(f"Documents mentioning 'subpoena': {total_subpoena_docs:,} ({total_subpoena_docs/total_docs*100:.2f}%)")
    print(f"Documents with formal RIDER pages: {len(rider_docs)}")

    # Step 3: Parse each RIDER document
    records = []
    for efta_id, pages in sorted(rider_docs.items()):
        # Combine all RIDER pages for this document
        all_rider_text = "\n\n".join(text for _, text in pages)
        rider_page_nums = [p for p, _ in pages]

        # Parse the rider
        parsed = parse_rider_text(all_rider_text)

        # Get page count
        total_pages = get_doc_page_count(conn, efta_id)

        # Post-parse normalization of target
        target = parsed["target"]
        # Fix "dated August 15" etc. that slipped through as targets
        if target and re.match(r'^dated\s', target, re.IGNORECASE):
            target = "[REDACTED]"
        # Fix "the following corporate entity:" — keep but normalize
        if target and target.lower().startswith('the following corporate entit'):
            target = "[Multiple Corporate Entities — see detail]"
        # Clean trailing periods
        if target:
            target = target.rstrip('.')
        parsed["target"] = target

        # Classify
        target_category = classify_target(parsed["target"]) if parsed["target"] and parsed["target"] != "[REDACTED]" else "Redacted"
        doc_type_list = classify_requested_docs(parsed["requested_categories"])

        # Dataset
        efta_num = int(efta_id.replace("EFTA", ""))
        ds = efta_to_dataset(efta_num)

        records.append({
            "efta_number": efta_id,
            "dataset": ds,
            "total_pages": total_pages,
            "rider_pages": ";".join(str(p) for p in rider_page_nums),
            "target": parsed["target"],
            "target_category": target_category,
            "date": parsed["date"],
            "statutes": "; ".join(parsed["statutes"]),
            "requested_doc_types": "; ".join(doc_type_list),
            "requested_items_count": len(parsed["requested_categories"]),
            "requested_items_detail": " | ".join(parsed["requested_categories"]),  # All items
            "doj_url": doj_url(efta_id),
        })

    # Step 4: Write CSV
    fieldnames = [
        "efta_number", "dataset", "total_pages", "rider_pages",
        "target", "target_category", "date", "statutes",
        "requested_doc_types", "requested_items_count", "requested_items_detail",
        "doj_url"
    ]

    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rec in records:
            writer.writerow(rec)

    print(f"\nWrote {len(records)} records to {OUTPUT_CSV}")

    # Step 5: Summary statistics
    print("\n" + "=" * 80)
    print("SUMMARY STATISTICS")
    print("=" * 80)

    # By target category
    cat_counts = Counter(r["target_category"] for r in records)
    print("\n--- Subpoenas by Target Category ---")
    for cat, count in cat_counts.most_common():
        print(f"  {cat}: {count}")

    # By dataset
    ds_counts = Counter(r["dataset"] for r in records)
    print("\n--- Subpoenas by Dataset ---")
    for ds, count in sorted(ds_counts.items()):
        print(f"  Dataset {ds}: {count}")

    # By document type requested
    all_doc_types = Counter()
    for r in records:
        for dt in r["requested_doc_types"].split("; "):
            if dt:
                all_doc_types[dt] += 1

    print("\n--- Types of Records Requested ---")
    for dt, count in all_doc_types.most_common():
        print(f"  {dt}: {count}")

    # Unique targets
    targets = set()
    for r in records:
        t = r["target"].strip()
        if t and len(t) > 2:
            targets.add(t)

    print(f"\n--- Unique Named Targets: {len(targets)} ---")

    # Top targets by number of subpoenas
    target_counts = Counter(r["target"] for r in records if r["target"])
    print("\n--- Most Frequently Subpoenaed ---")
    for target, count in target_counts.most_common(25):
        if count > 1:
            print(f"  {target}: {count} subpoenas")

    # Largest response documents (by page count)
    print("\n--- Largest Subpoena Response Documents ---")
    by_pages = sorted(records, key=lambda r: r["total_pages"], reverse=True)
    for r in by_pages[:15]:
        print(f"  {r['efta_number']} ({r['total_pages']:,} pages) → {r['target'] or 'UNKNOWN'}")

    # Statutes cited
    all_statutes = Counter()
    for r in records:
        for s in r["statutes"].split("; "):
            s = s.strip()
            if s:
                all_statutes[s] += 1

    if all_statutes:
        print("\n--- Statutes Cited ---")
        for s, count in all_statutes.most_common(10):
            print(f"  {s}: {count}")

    # Date range
    dates = [r["date"] for r in records if r["date"]]
    if dates:
        print(f"\n--- Date Range ---")
        print(f"  Earliest: {min(dates, key=lambda d: d)}")
        print(f"  Latest: {max(dates, key=lambda d: d)}")
        print(f"  Dated subpoenas: {len(dates)} / {len(records)}")

    # Records with no target parsed
    no_target = [r for r in records if not r["target"]]
    print(f"\n--- Parse Quality ---")
    print(f"  Target extracted: {len(records) - len(no_target)} / {len(records)}")
    print(f"  Missing target: {len(no_target)}")
    if no_target:
        print(f"  Missing target EFTAs: {', '.join(r['efta_number'] for r in no_target[:10])}")

    conn.close()

    print(f"\n{'=' * 80}")
    print(f"CSV output: {OUTPUT_CSV}")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
