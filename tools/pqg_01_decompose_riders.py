#!/usr/bin/env python3
"""
PQG Phase 1: Rider Clause Decomposition

Re-extracts all ~257 Grand Jury subpoena riders from full_text_corpus.db
WITHOUT the [:10] truncation, decomposes each into individual demand clauses
with data_class classification.

Input: full_text_corpus.db
Output: prosecutorial_query_graph.db tables: subpoenas, rider_clauses
"""

import os
import sqlite3
import re
import json
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
CORPUS_DB = f"{BASE_DIR}/epstein_files/full_text_corpus.db"
PQG_DB = f"{BASE_DIR}/epstein_files/prosecutorial_query_graph.db"

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

# Data class keyword mappings
DATA_CLASS_KEYWORDS = {
    'bank_records': [
        'account', 'transaction', 'deposit', 'wire', 'statement', 'bank',
        'balance', 'withdrawal', 'check', 'draft', 'money order', 'cashier',
        'safe deposit', 'signature card', 'loan', 'credit', 'debit',
        'savings', 'checking', 'certificate of deposit', 'investment',
        'brokerage', 'securities', 'stock', 'bond', 'mutual fund',
        'financial', 'payment', 'transfer', 'remittance', 'ach',
        'kyc', 'know your customer', 'sar', 'suspicious activity',
        'ctr', 'currency transaction',
    ],
    'phone_records': [
        'call detail', 'cdr', 'subscriber', 'cell site', 'tower',
        'telephone', 'phone', 'cellular', 'mobile', 'voicemail',
        'text message', 'sms', 'mms', 'pen register', 'trap and trace',
        'incoming call', 'outgoing call', 'toll record',
    ],
    'email': [
        'email', 'electronic communication', 'inbox', 'e-mail',
        'electronic mail', 'correspondence', 'message',
    ],
    'travel': [
        'passenger', 'flight', 'pnr', 'manifest', 'itinerary',
        'boarding pass', 'reservation', 'ticket', 'travel',
        'airline', 'charter', 'aviation', 'aircraft', 'helicopter',
        'passport', 'customs', 'immigration',
    ],
    'personnel': [
        'employee', 'roster', 'payroll', 'hr', 'human resource',
        'personnel', 'staff', 'hire', 'salary', 'wage', 'compensation',
        'contractor', 'worker', 'job', 'position', 'employment',
    ],
    'video': [
        'surveillance', 'camera', 'footage', 'recording', 'video',
        'cctv', 'security camera', 'monitor',
    ],
    'medical': [
        'medical', 'autopsy', 'toxicology', 'health', 'hospital',
        'physician', 'doctor', 'prescription', 'pharmacy', 'drug',
        'treatment', 'diagnosis',
    ],
    'corporate': [
        'incorporation', 'beneficial owner', 'llc', 'trust',
        'corporate', 'company', 'entity', 'formation', 'articles',
        'operating agreement', 'partnership', 'business',
        'registered agent', 'officer', 'director', 'shareholder',
    ],
    'identification': [
        'identification', 'id card', 'driver license', 'passport',
        'photo', 'image', 'picture', 'credential', 'social security',
    ],
    'property': [
        'property', 'real estate', 'deed', 'mortgage', 'lease',
        'rental', 'title', 'lien', 'address', 'residence',
    ],
    'correctional': [
        'inmate', 'prisoner', 'custody', 'cell', 'housing',
        'visitation', 'commissary', 'booking', 'detention',
        'correctional', 'jail', 'prison', 'incarcerat',
    ],
}


def efta_to_dataset(efta_num):
    for start, end, ds in DATASET_RANGES:
        if start <= efta_num <= end:
            return ds
    for start, end, ds in reversed(DATASET_RANGES):
        if efta_num >= start:
            return ds
    return None


def create_pqg_database(db_path):
    """Create the prosecutorial_query_graph.db schema."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS subpoenas (
            id INTEGER PRIMARY KEY,
            efta_number TEXT UNIQUE,
            target TEXT,
            target_category TEXT,
            date_issued TEXT,
            statutes TEXT,
            total_pages INTEGER,
            rider_page_numbers TEXT,
            full_rider_text TEXT,
            clause_count INTEGER
        );

        CREATE TABLE IF NOT EXISTS rider_clauses (
            id INTEGER PRIMARY KEY,
            subpoena_id INTEGER REFERENCES subpoenas(id),
            clause_number INTEGER,
            clause_text TEXT,
            data_class TEXT,
            date_range_start TEXT,
            date_range_end TEXT,
            target_accounts TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_clauses_subpoena ON rider_clauses(subpoena_id);
        CREATE INDEX IF NOT EXISTS idx_clauses_class ON rider_clauses(data_class);
        CREATE INDEX IF NOT EXISTS idx_subpoenas_target ON subpoenas(target);
        CREATE INDEX IF NOT EXISTS idx_subpoenas_efta ON subpoenas(efta_number);
    """)
    conn.commit()
    return conn


def extract_riders(corpus_conn):
    """Find all documents with RIDER pages from full_text_corpus.db."""
    cur = corpus_conn.cursor()

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
    print(f"Found {len(rider_pages)} RIDER pages")

    docs = {}
    for efta, page_num, text in rider_pages:
        if 'Case 9:08-cv-80119' in text:
            continue
        if 'Privilege Against Self-Incrimination' in text and 'RIDER' not in text[:100]:
            continue
        if efta not in docs:
            docs[efta] = []
        docs[efta].append((page_num, text))

    print(f"Found {len(docs)} unique RIDER documents")
    return docs


def parse_rider_header(text):
    """Extract target, date, and statutes from a RIDER page."""
    result = {"target": "", "date": "", "statutes": []}

    header_patterns = [
        r'RIDER\s*\(?(?:Grand Jury )?Subpoena\s+(?:to|for)\s+(.+?),?\s+dated\s+(\w+\s+\d{1,2},?\s+\d{4})\)?',
        r'RIDER\s*\(?(?:Grand Jury )?Subpoena\s+(?:to|for)\s+(.+?)\s+dated\s+(\w+\s+\d{1,2},?\s+\d{4})\)?',
        r'RIDER\s*\(.*?(?:to|for)\s+(.+?),?\s+dated\s+(.+?)\)',
    ]

    for pattern in header_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            raw_target = match.group(1).strip()
            raw_date = match.group(2).strip()

            raw_date = re.sub(r'\)\s*$', '', raw_date).strip()
            date_match = re.match(r'(\w+\s+\d{1,2},?\s+\d{4})', raw_date)
            if date_match:
                result["date"] = date_match.group(1)
            else:
                result["date"] = raw_date

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
                re.match(r'^dated\s', raw_target, re.IGNORECASE)
            )
            if is_junk:
                result["target"] = "[REDACTED]"
            else:
                raw_target = raw_target.split('\n')[0].strip()
                raw_target = re.sub(r'[,.\s]+$', '', raw_target)
                result["target"] = raw_target
            break

    if not result["target"]:
        m = re.search(r'RIDER.*?(?:to|for)\s+(.+?)(?:\n|,|dated)', text, re.IGNORECASE | re.DOTALL)
        if m:
            raw = m.group(1).strip()[:200]
            if raw and len(raw) > 3 and 'answer any question' not in raw.lower():
                result["target"] = raw
            else:
                result["target"] = "[REDACTED]"
        else:
            m2 = re.search(r'SUBPOENA RIDER\s*\n\s*(.+?)(?:\n|$)', text, re.IGNORECASE)
            if m2:
                result["target"] = m2.group(1).strip()[:200]

    # Post-parse normalization
    if result["target"] and re.match(r'^dated\s', result["target"], re.IGNORECASE):
        result["target"] = "[REDACTED]"
    if result["target"] and result["target"].lower().startswith('the following corporate entit'):
        result["target"] = "[Multiple Corporate Entities — see detail]"
    if result["target"]:
        result["target"] = result["target"].rstrip('.')

    # Extract statutes
    statute_matches = re.findall(
        r'(\d+\s+U\.?S\.?C\.?\s*§+\s*\d+[a-z]?(?:\([a-z0-9]+\))?)',
        text, re.IGNORECASE
    )
    result["statutes"] = list(set(statute_matches))

    return result


def classify_target(target):
    """Classify a subpoena target into a category."""
    target_lower = target.lower()

    categories = [
        ("Financial Institution", ['bank', 'chase', 'wells fargo', 'citibank', 'citi ', 'ubs',
            'credit union', 'ameritrade', 'santander', 'capital one', 'firstbank', 'fifth third',
            'navy federal', 'usaa', 'td bank', 'umb bank', 'gold coast', 'mcu', 'credit one',
            'first premier', 'jpmorgan', 'deutsche', 'barclays', 'morgan stanley', 'goldman']),
        ("Technology Company", ['google', 'facebook', 'meta', 'amazon', 'apple', 'microsoft',
            'oath', 'yahoo', 'lyft', 'uber', '4chan', 'paypal', 'venmo', 'square', 'twitter']),
        ("Airline / Travel", ['airlines', 'airline', 'airways', 'air ', 'jetblue', 'delta',
            'united', 'southwest', 'alaska air', 'american air', 'expedia', 'travelport', 'sabre']),
        ("Telecommunications", ['at&t', 'verizon', 't-mobile', 'sprint', 'centurylink', 'comcast',
            'telephone', 'telecom', 'wireless', 'cellular']),
        ("Credit Reporting Agency", ['experian', 'transunion', 'equifax', 'credit bureau']),
        ("Money Transfer Service", ['western union', 'moneygram', 'money transfer']),
        ("Government / Corrections", ['mcc', 'metropolitan correctional', 'white house', 'department',
            'bureau', 'federal', 'state of', 'county', 'customs', 'border', 'immigration']),
        ("Law Firm / Attorney", ['esq', 'llp', 'law firm', 'attorney', 'counsel', 'boies', 'schiller']),
        ("Educational Institution", ['school', 'university', 'college', 'interlochen', 'academy', 'arts']),
        ("Cryptocurrency / Fintech", ['iterative', 'otc', 'crypto', 'bitcoin', 'blockchain']),
    ]

    for cat_name, keywords in categories:
        if any(kw in target_lower for kw in keywords):
            return cat_name

    corp_indicators = ['inc', 'llc', 'corp', 'ltd', 'company', 'co.', 'group', 'holdings', 'services']
    if not any(ci in target_lower for ci in corp_indicators):
        words = target.split()
        if 1 < len(words) <= 5:
            return "Individual"

    return "Other Entity"


def decompose_clauses(full_text):
    """Decompose rider text into individual demand clauses.

    Returns list of (clause_number, clause_text) tuples.
    """
    # Find the rider content section (after the header)
    rider_start = 0
    for marker in ['RIDER', 'SUBPOENA RIDER']:
        idx = full_text.find(marker)
        if idx >= 0:
            rider_start = idx
            break

    rider_section = full_text[rider_start:]

    # Skip the header line(s) — find the first numbered/lettered item
    first_item = re.search(r'(?:^|\n)\s*(?:1[.)]|a[.)])', rider_section)
    if first_item:
        rider_section = rider_section[first_item.start():]

    clauses = []

    # Strategy 1: Numbered items (1. or 1) or a. or a))
    # Match items that start with number/letter + period/paren
    pattern = r'(?:^|\n)\s*(\d+|[a-zA-Z])\s*[.)]\s+(.*?)(?=\n\s*(?:\d+|[a-zA-Z])\s*[.)]|\n\s*(?:YOU ARE|ADVICE|PLEASE TAKE|DEFINITIONS|IT IS ORDERED)|\Z)'
    items = re.findall(pattern, rider_section, re.DOTALL)

    for num, text in items:
        cleaned = text.strip()
        cleaned = re.sub(r'\s+', ' ', cleaned)  # Normalize whitespace
        if len(cleaned) >= 10:
            # Skip boilerplate items
            boilerplate = [
                'advice of rights', 'you are advised', 'you have the right',
                'privilege against self-incrimination', 'your rights',
                'you are not a target', 'this subpoena requires',
                'definitions', 'the term', 'as used herein',
            ]
            is_boilerplate = any(bp in cleaned.lower()[:100] for bp in boilerplate)
            if not is_boilerplate:
                clauses.append(cleaned)

    # Strategy 2: If no numbered items found, try bullet points or paragraphs
    if not clauses:
        # Try dash/bullet items
        bullet_items = re.findall(
            r'(?:^|\n)\s*[-•]\s+(.+?)(?=\n\s*[-•]|\Z)',
            rider_section, re.DOTALL
        )
        for text in bullet_items:
            cleaned = re.sub(r'\s+', ' ', text.strip())
            if len(cleaned) >= 15:
                clauses.append(cleaned)

    # Strategy 3: If still nothing, split by significant paragraphs
    if not clauses:
        paragraphs = re.split(r'\n\s*\n', rider_section)
        for para in paragraphs:
            cleaned = re.sub(r'\s+', ' ', para.strip())
            if len(cleaned) >= 20 and not any(
                kw in cleaned.lower() for kw in ['rider', 'subpoena', 'grand jury', 'advice of rights']
            ):
                clauses.append(cleaned)

    return clauses


def classify_clause_data_class(clause_text):
    """Classify a clause into a data_class based on keywords."""
    text_lower = clause_text.lower()

    scores = {}
    for data_class, keywords in DATA_CLASS_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[data_class] = score

    if scores:
        return max(scores, key=scores.get)
    return 'other'


def extract_date_range(clause_text):
    """Extract date range from clause text if specified."""
    # Patterns like "from January 1, 2015 to December 31, 2019"
    # or "for the period 2015-2019"
    # or "from 01/01/2015 through 12/31/2019"
    patterns = [
        r'from\s+(\w+\s+\d{1,2},?\s+\d{4})\s+(?:to|through|until)\s+(\w+\s+\d{1,2},?\s+\d{4})',
        r'(?:period|time)\s+(?:of\s+)?(\d{4})\s*[-–]\s*(\d{4})',
        r'from\s+(\d{1,2}/\d{1,2}/\d{4})\s+(?:to|through)\s+(\d{1,2}/\d{1,2}/\d{4})',
        r'(?:beginning|starting)\s+(?:on\s+)?(\w+\s+\d{1,2},?\s+\d{4}).*?(?:ending|to|through)\s+(\w+\s+\d{1,2},?\s+\d{4})',
    ]

    for pattern in patterns:
        m = re.search(pattern, clause_text, re.IGNORECASE)
        if m:
            return m.group(1), m.group(2)

    return None, None


def extract_account_identifiers(clause_text):
    """Extract specific account numbers or identifiers from clause text."""
    accounts = []

    # Account number patterns
    acct_patterns = [
        r'account\s*(?:#|number|no\.?)\s*[:\s]*(\S+)',
        r'(?:acct|a/c)\s*[#:]\s*(\S+)',
    ]

    for pattern in acct_patterns:
        for m in re.finditer(pattern, clause_text, re.IGNORECASE):
            accounts.append(m.group(1))

    return '; '.join(accounts) if accounts else None


def main():
    print("=" * 80)
    print("PQG Phase 1: Rider Clause Decomposition")
    print("=" * 80)
    start_time = datetime.now()

    # Connect to corpus
    corpus_conn = sqlite3.connect(CORPUS_DB)

    # Create PQG database
    import os
    if os.path.exists(PQG_DB):
        # Remove existing tables to re-create
        pqg_conn = sqlite3.connect(PQG_DB)
        pqg_conn.execute("DROP TABLE IF EXISTS rider_clauses")
        pqg_conn.execute("DROP TABLE IF EXISTS subpoenas")
        pqg_conn.close()

    pqg_conn = create_pqg_database(PQG_DB)
    pqg_cur = pqg_conn.cursor()

    # Extract riders
    rider_docs = extract_riders(corpus_conn)

    # Process each rider
    corpus_cur = corpus_conn.cursor()
    total_clauses = 0
    category_counts = Counter()
    class_counts = Counter()

    for efta_id, pages in sorted(rider_docs.items()):
        # Get ALL pages for this document (not just RIDER pages)
        efta_num = int(efta_id.replace("EFTA", ""))
        ds = efta_to_dataset(efta_num)

        corpus_cur.execute(
            "SELECT total_pages FROM documents WHERE efta_number = ?", (efta_id,))
        row = corpus_cur.fetchone()
        total_pages = row[0] if row else len(pages)

        # Combine ALL rider pages for complete text (NO truncation)
        rider_page_nums = [p for p, _ in pages]
        full_rider_text = "\n\n".join(text for _, text in pages)

        # Parse header
        parsed = parse_rider_header(full_rider_text)
        target = parsed["target"]
        target_category = classify_target(target) if target and target != "[REDACTED]" else "Redacted"
        category_counts[target_category] += 1

        # Decompose into clauses
        clauses = decompose_clauses(full_rider_text)

        # Insert subpoena
        pqg_cur.execute("""
            INSERT OR REPLACE INTO subpoenas (
                efta_number, target, target_category, date_issued, statutes,
                total_pages, rider_page_numbers, full_rider_text, clause_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            efta_id, target, target_category, parsed["date"],
            "; ".join(parsed["statutes"]), total_pages,
            json.dumps(rider_page_nums), full_rider_text, len(clauses),
        ))
        subpoena_id = pqg_cur.lastrowid

        # Insert clauses
        for clause_num, clause_text in enumerate(clauses, 1):
            data_class = classify_clause_data_class(clause_text)
            class_counts[data_class] += 1
            date_start, date_end = extract_date_range(clause_text)
            accounts = extract_account_identifiers(clause_text)

            pqg_cur.execute("""
                INSERT INTO rider_clauses (
                    subpoena_id, clause_number, clause_text, data_class,
                    date_range_start, date_range_end, target_accounts
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (subpoena_id, clause_num, clause_text, data_class, date_start, date_end, accounts))

        total_clauses += len(clauses)

    pqg_conn.commit()

    # Summary
    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print(f"{'=' * 80}")

    subpoena_count = pqg_cur.execute("SELECT COUNT(*) FROM subpoenas").fetchone()[0]
    clause_count = pqg_cur.execute("SELECT COUNT(*) FROM rider_clauses").fetchone()[0]
    print(f"Subpoenas: {subpoena_count}")
    print(f"Total clauses: {clause_count}")
    print(f"Avg clauses per subpoena: {clause_count / max(subpoena_count, 1):.1f}")

    print("\n--- Target Categories ---")
    for cat, count in category_counts.most_common():
        print(f"  {cat}: {count}")

    print("\n--- Data Classes ---")
    for cls, count in class_counts.most_common():
        print(f"  {cls}: {count}")

    # Clause distribution
    pqg_cur.execute("SELECT clause_count, COUNT(*) FROM subpoenas GROUP BY clause_count ORDER BY clause_count")
    print("\n--- Clauses per Subpoena Distribution ---")
    for count, num in pqg_cur.fetchall():
        print(f"  {count} clauses: {num} subpoenas")

    # Subpoenas with date ranges
    pqg_cur.execute("""
        SELECT COUNT(DISTINCT subpoena_id) FROM rider_clauses
        WHERE date_range_start IS NOT NULL
    """)
    dated = pqg_cur.fetchone()[0]
    print(f"\nSubpoenas with date-ranged clauses: {dated}")

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\nCompleted in {elapsed:.1f} seconds")

    corpus_conn.close()
    pqg_conn.close()


if __name__ == "__main__":
    main()
