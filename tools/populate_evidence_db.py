#!/usr/bin/env python3
"""
populate_evidence_db.py - Populate empty tables in the Epstein evidence database.

Populates: flights, flight_passengers, financial_transactions, communications,
           communication_participants, relationships, victim_perpetrator_links

Data sources:
  - flight_logs_transcribed.pdf (4,002 passenger records)
  - EVIDENCE_COMPILATION.md (financial data, relationships)
  - evidence_findings.jsonl (322 entity findings)
  - Existing person_interactions table (abuse data)
"""

import sqlite3
import json
import re
import os
import sys
import logging
import shutil
from datetime import datetime

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
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
DB_PATH = os.path.join(BASE_DIR, "evidence_db", "evidence.db")
BACKUP_PATH = DB_PATH + ".backup"
LOG_PATH = os.path.join(BASE_DIR, "populate_evidence.log")
FLIGHT_PDF = os.path.join(BASE_DIR, "flight_logs_transcribed.pdf")
EVIDENCE_MD = os.path.join(BASE_DIR, "EVIDENCE_COMPILATION.md")
EVIDENCE_JSONL = os.path.join(BASE_DIR, "evidence_findings.jsonl")

# ------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ==================================================================
# Helper: person name matching
# ==================================================================

# Map of canonical person IDs (1-15) by well-known names.
# The DB has 108 persons, many are case-variant duplicates (ids 16-108).
# We build a lookup that resolves any variant to the canonical id.

CANONICAL_PERSONS = {
    1: ("Jeffrey Epstein", ["Jeff Epstein", "Epstein", "Jeffrey E. Epstein",
        "JEFFREY EPSTEIN", "EPSTEIN", "jeffrey epstein", "Jeffrey epstein",
        "JEFFREY Epstein", "Jeffrey EPSTEIN", "Jeffrey E. EPSTEIN",
        "JE", "epstein", "ePstein", "EpStein", "JEFFREY E. EPSTEIN",
        "Jeff Epstien"]),
    2: ("Ghislaine Maxwell", ["Maxwell", "Ms. Maxwell", "GHISLAINE MAXWELL",
        "MAXWELL", "ghislaine maxwell", "Ghislaine MAXWELL",
        "GhISLAINE MAXWELL", "GHiSLAINE MAXWELL", "MS. MAXWELL",
        "ms. Maxwell", "maxwell", "MaxwelL", "GM"]),
    3: ("Leon Black", ["Black", "BLACK", "black", "LEON BLACK", "LB"]),
    4: ("Jes Staley", ["Staley", "STALEY", "staley", "JES STALEY", "JS"]),
    5: ("Les Wexner", ["Wexner", "WEXNER", "wexner", "LW"]),
    6: ("Prince Andrew", ["Andrew", "ANDREW", "andrew", "Duke of York",
        "prince Andrew", "prince andrew", "PA"]),
    7: ("Alan Dershowitz", ["Dershowitz", "DERSHOWITZ", "dershowitz",
        "ALAN DERSHOWITZ", "AD"]),
    8: ("Glenn Dubin", ["Glen Dubin", "GD"]),
    9: ("Jean-Luc Brunel", ["Brunel", "brunel", "JLB"]),
    10: ("Sarah Kellen", ["Kellen", "SK"]),
    11: ("Nadia Marcinkova", ["Marcinkova", "NM"]),
    12: ("Lesley Groff", ["Groff", "GROFF", "Lesley K Groff", "LG"]),
    13: ("Bill Clinton", ["Clinton", "President Clinton", "CLINTON",
         "clinton", "ClInton", "BC"]),
    14: ("Donald Trump", ["Trump", "TRUMP", "trump", "truMp", "DT"]),
    15: ("Harvey Weinstein", ["Weinstein", "WEINSTEIN", "weinstein",
         "weInstein", "HW"]),
    77: ("Bill Gates", ["Gates", "GATES", "gates"]),
    50: ("Juan Alessi", ["Alessi"]),
    28: ("Darren Indyke", ["Indyke", "INDYKE", "Darren K. Indyke",
         "DARREN INDYKE", "DARREN K. INDYKE", "Darren K. INDYKE",
         "Darren INDYKE", "indyke"]),
    29: ("Richard Kahn", ["Kahn", "KAHN", "Richard D. Kahn",
         "RICHARD D. KAHN", "kahn"]),
    19: ("Ross", ["ROSS", "ross", "rOSS", "roSS"]),
    85: ("Giuffre", ["Virginia Roberts", "VIRGINIA ROBERTS", "giuffre",
         "GIUFFRE", "Virginia Giuffre"]),
    107: ("Eva Dubin", []),
    106: ("Maria Alessi", []),
}


def build_person_lookup(conn):
    """Build name -> person_id lookup from DB + CANONICAL_PERSONS."""
    cursor = conn.execute("SELECT id, canonical_name, aliases FROM persons")
    lookup = {}

    # First pass: all DB entries keyed by canonical_name (case-insensitive)
    db_persons = []
    for row in cursor:
        pid, cname, aliases_json = row
        db_persons.append((pid, cname, aliases_json))
        lookup[cname.strip().lower()] = pid
        if aliases_json:
            try:
                for alias in json.loads(aliases_json):
                    lookup[alias.strip().lower()] = pid
            except (json.JSONDecodeError, TypeError):
                pass

    # Second pass: canonical overrides (map variant IDs to canonical IDs)
    for canonical_id, (canonical_name, aliases) in CANONICAL_PERSONS.items():
        key = canonical_name.strip().lower()
        lookup[key] = canonical_id
        for alias in aliases:
            lookup[alias.strip().lower()] = canonical_id

    return lookup


def resolve_person(lookup, name):
    """Resolve a name string to a person_id, or None."""
    if not name or not name.strip():
        return None
    key = name.strip().lower()
    if key in lookup:
        return lookup[key]
    # Try partial matching for common patterns
    # "First Last" -> try last name alone
    parts = key.split()
    if len(parts) >= 2:
        last = parts[-1]
        if last in lookup:
            return lookup[last]
    return None


# ==================================================================
# 1. FLIGHT LOG PARSING
# ==================================================================

def parse_flight_logs():
    """Parse the transcribed flight logs PDF into structured records."""
    import fitz

    log.info("Opening flight logs PDF: %s", FLIGHT_PDF)
    doc = fitz.open(FLIGHT_PDF)
    all_text = ""
    for page in doc:
        all_text += page.get_text()
    doc.close()

    lines = all_text.split("\n")
    total_lines = len(lines)
    log.info("Flight log PDF: %d lines of text extracted", total_lines)

    # Skip the header (first 21 lines are column names)
    # Each passenger record starts with a line matching: NNNN MM/DD/YYYY
    # Followed by structured fields. Some address fields span 2 lines.

    records = []
    i = 0
    # Skip header
    while i < total_lines:
        if re.match(r"^\d{4,5}\s+\d{1,2}/\d{1,2}/\d{4}$", lines[i].strip()):
            break
        i += 1

    while i < total_lines:
        line = lines[i].strip()
        m = re.match(r"^(\d{4,5})\s+(\d{1,2}/\d{1,2}/\d{4})$", line)
        if not m:
            i += 1
            continue

        rec_id = m.group(1)
        date_str = m.group(2)

        # We need to consume the following fields, handling multi-line addresses
        j = i + 1

        def next_field():
            nonlocal j
            if j < total_lines:
                val = lines[j].strip()
                j += 1
                return val
            return ""

        year_aircraft = next_field()  # e.g., "1995 G-1159B"
        tail_number = next_field()     # e.g., "N908JE"
        aircraft_type = next_field()   # e.g., "Jet"
        seats = next_field()           # e.g., "22"
        dep_code = next_field()        # ICAO/IATA code
        arr_code = next_field()        # ICAO/IATA code

        # DEP full name - may be multi-line
        dep_full = next_field()
        # Check if dep_full ends with comma (continuation)
        while dep_full.endswith(",") and j < total_lines:
            continuation = lines[j].strip()
            # Make sure we're not accidentally consuming the next record ID
            if re.match(r"^\d{4,5}\s+\d{1,2}/\d{1,2}/\d{4}$", continuation):
                break
            dep_full += " " + continuation
            j += 1

        # ARR full name - may be multi-line
        arr_full = next_field()
        # Check if it looks like a continuation (doesn't match flight_no pattern)
        while j < total_lines:
            peek = lines[j].strip()
            # Flight_No is typically a number, "No Records", or "FOIA-NNN"
            if re.match(r"^(\d+|No Records|FOIA.*)$", peek):
                break
            # Also check for next record start
            if re.match(r"^\d{4,5}\s+\d{1,2}/\d{1,2}/\d{4}$", peek):
                break
            arr_full += " " + peek
            j += 1

        flight_no = next_field()       # Flight number
        # Handle cases where flight_no got merged into something else
        # Some 'No Records' entries have different structure
        pass_num = next_field()        # e.g., "Pass 1", "No Records"

        if pass_num == "No Records" or "No Records" in flight_no:
            # Skip these - no actual passenger data
            i = j
            continue

        unique_id = next_field()
        first_name = next_field()
        last_name = next_field()
        last_first = next_field()      # "Last, First"

        # first_last may span 2 lines (e.g., "Ghislaine \n Maxwell")
        first_last = next_field()
        # Check if the next line is part of first_last (not initials-length)
        if j < total_lines:
            peek = lines[j].strip()
            # Initials are typically 1-3 chars
            if len(peek) > 3 and not re.match(r"^(Yes|No|Flight Log|\d)$", peek):
                first_last += " " + peek
                j += 1

        # Comment field (often empty but sometimes has text)
        # Then: Initials, Known (Yes/No), Data Source
        # We don't strictly need comment, initials, or data source

        # Parse the aircraft model from year_aircraft
        aircraft_match = re.match(r"(\d{4})\s+(.*)", year_aircraft)
        aircraft_model = aircraft_match.group(2) if aircraft_match else year_aircraft

        # Normalize the date to YYYY-MM-DD
        try:
            dt = datetime.strptime(date_str, "%m/%d/%Y")
            flight_date = dt.strftime("%Y-%m-%d")
        except ValueError:
            flight_date = date_str

        # Clean up passenger name
        passenger_name = first_last.strip()
        if not passenger_name or passenger_name == "? ?":
            passenger_name = f"{first_name} {last_name}".strip()
        if passenger_name in ("? ?", "?", ""):
            i = j
            continue

        records.append({
            "record_id": rec_id,
            "flight_date": flight_date,
            "aircraft_model": aircraft_model,
            "tail_number": tail_number,
            "dep_code": dep_code,
            "arr_code": arr_code,
            "dep_full": dep_full,
            "arr_full": arr_full,
            "flight_no": flight_no,
            "pass_num": pass_num,
            "first_name": first_name,
            "last_name": last_name,
            "passenger_name": passenger_name,
        })

        i = j

    log.info("Parsed %d passenger records from flight logs", len(records))
    return records


def group_flights(records):
    """Group passenger records into distinct flights.

    A flight is uniquely identified by (date, tail_number, dep_code, arr_code, flight_no).
    Multiple passengers share the same flight.
    """
    flights = {}
    for rec in records:
        key = (rec["flight_date"], rec["tail_number"], rec["dep_code"],
               rec["arr_code"], rec["flight_no"])
        if key not in flights:
            flights[key] = {
                "flight_date": rec["flight_date"],
                "aircraft": rec["aircraft_model"],
                "tail_number": rec["tail_number"],
                "origin": f"{rec['dep_full']} ({rec['dep_code']})",
                "destination": f"{rec['arr_full']} ({rec['arr_code']})",
                "passengers": [],
            }
        flights[key]["passengers"].append({
            "name": rec["passenger_name"],
            "name_as_written": f"{rec['first_name']} {rec['last_name']}",
            "pass_num": rec["pass_num"],
        })
    return list(flights.values())


def get_or_create_person(conn, person_lookup, name):
    """Resolve name to person_id, creating a new person record if needed."""
    pid = resolve_person(person_lookup, name)
    if pid:
        return pid

    # Skip initials-only or single-char names (e.g., "A S", "?")
    clean = name.strip()
    if not clean or clean == "?" or len(clean) <= 2:
        return None
    # Skip names that are just initials
    parts = clean.split()
    if all(len(p) <= 2 for p in parts):
        return None

    # Check if already exists (case-insensitive)
    row = conn.execute(
        "SELECT id FROM persons WHERE LOWER(canonical_name) = LOWER(?)", (clean,)
    ).fetchone()
    if row:
        person_lookup[clean.lower()] = row[0]
        return row[0]

    # Create new person
    cursor = conn.execute(
        """INSERT INTO persons (canonical_name, person_type, notes)
           VALUES (?, 'mentioned', ?)""",
        (clean, "Auto-created from flight logs"),
    )
    new_id = cursor.lastrowid
    person_lookup[clean.lower()] = new_id
    return new_id


def insert_flights(conn, flights, person_lookup):
    """Insert flights and flight_passengers into the database."""
    log.info("Inserting %d flights into database...", len(flights))

    flight_count = 0
    passenger_count = 0
    linked_count = 0
    new_persons_count = 0
    skipped_count = 0

    for flight in flights:
        cursor = conn.execute(
            """INSERT INTO flights (flight_date, aircraft, tail_number, origin, destination, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                flight["flight_date"],
                flight["aircraft"],
                flight["tail_number"],
                flight["origin"],
                flight["destination"],
                "Parsed from flight_logs_transcribed.pdf",
            ),
        )
        flight_id = cursor.lastrowid
        flight_count += 1

        for pax in flight["passengers"]:
            person_id = resolve_person(person_lookup, pax["name"])
            if person_id is None:
                person_id = resolve_person(person_lookup, pax["name_as_written"])
            was_existing = person_id is not None

            if person_id is None:
                # Try to create a new person from the passenger name
                person_id = get_or_create_person(conn, person_lookup, pax["name"])
                if person_id is None:
                    person_id = get_or_create_person(conn, person_lookup, pax["name_as_written"])
                if person_id and not was_existing:
                    new_persons_count += 1

            if person_id is None:
                # Cannot link - skip this passenger (initials-only etc.)
                skipped_count += 1
                continue

            conn.execute(
                """INSERT INTO flight_passengers (flight_id, person_id, name_as_written, notes)
                   VALUES (?, ?, ?, ?)""",
                (
                    flight_id,
                    person_id,
                    pax["name_as_written"],
                    None,
                ),
            )
            passenger_count += 1
            if was_existing:
                linked_count += 1

    conn.commit()
    log.info(
        "Flights inserted: %d flights, %d passengers (%d linked to existing persons, "
        "%d new persons created, %d skipped/unresolvable)",
        flight_count, passenger_count, linked_count, new_persons_count, skipped_count,
    )
    return flight_count, passenger_count


# ==================================================================
# 2. FINANCIAL TRANSACTIONS
# ==================================================================

def insert_financial_transactions(conn, person_lookup):
    """Parse financial data from EVIDENCE_COMPILATION.md and evidence_findings.jsonl."""
    log.info("Parsing financial transactions...")

    epstein_id = 1  # Jeffrey Epstein

    transactions = []

    # ---- From EVIDENCE_COMPILATION.md ----
    # Leon Black -> Epstein: $158M (2012-2017)
    leon_id = resolve_person(person_lookup, "Leon Black")
    transactions.append({
        "payer_person_id": leon_id,
        "payee_person_id": epstein_id,
        "amount": 158000000.0,
        "currency": "USD",
        "transaction_date": "2012-01-01",
        "transaction_type": "fee",
        "purpose": "Payments from Leon Black to Epstein (2012-2017) - no written contract for over $100M",
        "memo_line": "Tax advisory services (claimed)",
        "suspicious": True,
        "notes": "Source: EFTA02731023 (Senate Finance Committee Letter). Tax savings achieved: $1B+ in gift/estate taxes. Black refused to answer questions.",
    })

    # Payments to victims/recruiters from VI Exhibit 1
    transactions.append({
        "payer_person_id": epstein_id,
        "payee_person_id": None,
        "amount": 400000.0,
        "currency": "USD",
        "transaction_date": None,
        "transaction_type": "check",
        "purpose": "Checks to young female models and actresses",
        "suspicious": True,
        "notes": "Source: VI Exhibit 1 (Hidden under redactions). $400,000+ in checks.",
    })
    transactions.append({
        "payer_person_id": epstein_id,
        "payee_person_id": None,
        "amount": 380000.0,
        "currency": "USD",
        "transaction_date": None,
        "transaction_type": "wire_transfer",
        "purpose": "Monthly payments ($8,333) to former Russian model",
        "suspicious": True,
        "notes": "Source: VI Exhibit 1. $380,000 via monthly payments of $8,333.",
    })
    transactions.append({
        "payer_person_id": epstein_id,
        "payee_person_id": None,
        "amount": 50000.0,
        "currency": "USD",
        "transaction_date": None,
        "transaction_type": "wire_transfer",
        "purpose": "Payments to women with Eastern European surnames including known recruiter",
        "suspicious": True,
        "notes": "Source: VI Exhibit 1. Includes one known to have recruited young women and girls for Epstein.",
    })
    transactions.append({
        "payer_person_id": epstein_id,
        "payee_person_id": None,
        "amount": 60000.0,
        "currency": "USD",
        "transaction_date": None,
        "transaction_type": "wire_transfer",
        "purpose": "Wire transfers to young women at foreign beneficiary banks",
        "suspicious": True,
        "notes": "Source: VI Exhibit 1.",
    })

    # Property expenses
    property_records = [
        ("2018-11-01", 106394.60, "Santa Fe property expenses (Nov 2018)"),
        ("2017-01-01", 169449.97, "Santa Fe property expenses (2017)"),
        ("2018-01-01", 336471.87, "NYC property expenses (2018)"),
        ("2017-01-01", 333984.52, "NYC property expenses (2017)"),
        ("2018-11-01", 196673.56, "Palm Beach property expenses (Nov 2018)"),
        ("2017-10-01", 191941.52, "Palm Beach property expenses (Oct 2017)"),
    ]
    for date, amount, purpose in property_records:
        transactions.append({
            "payer_person_id": epstein_id,
            "payee_person_id": None,
            "amount": amount,
            "currency": "USD",
            "transaction_date": date,
            "transaction_type": "other",
            "purpose": purpose,
            "suspicious": False,
            "notes": "Source: VI Exhibit 1. Property maintenance costs.",
        })

    # Prince Andrew settlement
    andrew_id = resolve_person(person_lookup, "Prince Andrew")
    transactions.append({
        "payer_person_id": andrew_id,
        "payee_person_id": None,
        "amount": 12000000.0,
        "currency": "USD",
        "transaction_date": "2022-02-15",
        "transaction_type": "settlement",
        "purpose": "Civil settlement with Virginia Giuffre",
        "suspicious": False,
        "notes": "Reported as $12M+ settlement. Civil case Giuffre v. Prince Andrew.",
    })

    # Witness tampering payments (from EVIDENCE_COMPILATION.md)
    transactions.append({
        "payer_person_id": epstein_id,
        "payee_person_id": None,
        "amount": None,
        "currency": "USD",
        "transaction_date": None,
        "transaction_type": "other",
        "purpose": "Large sums paid to participant-witnesses including attorneys fees",
        "suspicious": True,
        "notes": "Source: VI Exhibit 1 pg 41. Cover-up: paying participant-witnesses and instructing evidence destruction.",
    })

    # ---- From evidence_findings.jsonl ----
    with open(EVIDENCE_JSONL) as f:
        for line_str in f:
            entry = json.loads(line_str)
            data = entry.get("data", {})
            context = entry.get("context", "")
            notes_text = entry.get("notes", "")
            combined = (context + " " + notes_text + " " + str(data)).lower()

            # Look for entries with financial/payment keywords
            if any(w in combined for w in ["payment", "wire", "check ", "transaction",
                                           "account", "bank", "salary", "invoice"]):
                # Extract what we can
                entity_name = entry.get("entity_name", "")
                role = entry.get("role", "")
                source = entry.get("source_pdf", entry.get("source_image", ""))

                transactions.append({
                    "payer_person_id": None,
                    "payee_person_id": resolve_person(person_lookup, entity_name),
                    "amount": None,
                    "currency": "USD",
                    "transaction_date": None,
                    "transaction_type": "other",
                    "purpose": f"{role}: {context[:200]}" if context else role,
                    "suspicious": False,
                    "notes": f"Source: evidence_findings.jsonl id={entry.get('id')}. {source}",
                })

    # Insert all transactions
    count = 0
    for txn in transactions:
        conn.execute(
            """INSERT INTO financial_transactions
               (payer_person_id, payee_person_id, amount, currency,
                transaction_date, transaction_type, purpose, memo_line,
                bank_name, suspicious, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                txn.get("payer_person_id"),
                txn.get("payee_person_id"),
                txn.get("amount"),
                txn.get("currency", "USD"),
                txn.get("transaction_date"),
                txn.get("transaction_type", "other"),
                txn.get("purpose"),
                txn.get("memo_line"),
                txn.get("bank_name"),
                txn.get("suspicious", False),
                txn.get("notes"),
            ),
        )
        count += 1

    conn.commit()
    log.info("Financial transactions inserted: %d", count)
    return count


# ==================================================================
# 3. COMMUNICATIONS
# ==================================================================

def insert_communications(conn, person_lookup):
    """Parse communications from evidence_findings.jsonl (email/phone references)."""
    log.info("Parsing communications from evidence findings...")

    comms_count = 0
    participants_count = 0

    with open(EVIDENCE_JSONL) as f:
        for line_str in f:
            entry = json.loads(line_str)
            context = entry.get("context", "")
            source = entry.get("source_pdf", "")
            entity_name = entry.get("entity_name", "")
            data = entry.get("data", {})
            notes_text = entry.get("notes", "")

            comm_type = None
            subject = None

            # Detect communication type from context
            if any(kw in context.lower() for kw in ["email", "wrote:", "@gmail", "@yahoo",
                                                     "subject:", "sent:", "from:"]):
                comm_type = "email"
                # Try to extract subject
                subj_match = re.search(r"Subject:\s*(.+?)(?:\n|$)", context)
                if subj_match:
                    subject = subj_match.group(1).strip()[:200]
            elif any(kw in context.lower() for kw in ["phone", "call", "voicemail", "mobile"]):
                comm_type = "phone"
            elif any(kw in context.lower() for kw in ["letter", "mail", "fax"]):
                comm_type = "letter"
            elif data.get("phone") or data.get("carrier"):
                comm_type = "phone"

            if not comm_type:
                continue

            # Extract date if available
            date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}|\w+ \d{1,2}, \d{4})", context)
            comm_date = None
            if date_match:
                raw_date = date_match.group(1)
                for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y"):
                    try:
                        comm_date = datetime.strptime(raw_date, fmt).strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        pass

            # Build content summary
            content_summary = context[:500] if context else notes_text[:500]

            cursor = conn.execute(
                """INSERT INTO communications
                   (communication_date, communication_type, subject, content_summary, notes)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    comm_date,
                    comm_type,
                    subject,
                    content_summary,
                    f"Source: evidence_findings.jsonl id={entry.get('id')}. {source}",
                ),
            )
            comm_id = cursor.lastrowid
            comms_count += 1

            # Add participants
            # The entity is typically involved
            person_id = resolve_person(person_lookup, entity_name)
            if person_id:
                conn.execute(
                    """INSERT INTO communication_participants
                       (communication_id, person_id, role)
                       VALUES (?, ?, ?)""",
                    (comm_id, person_id, "mentioned"),
                )
                participants_count += 1

            # Try to extract other names from context
            # Look for "From: Name" or "To: Name" patterns
            from_match = re.search(r"From:\s*[\"']?([A-Z][a-zA-Z]+ [A-Z][a-zA-Z]+)", context)
            to_match = re.search(r"To:\s*[\"']?([A-Z][a-zA-Z]+ [A-Z][a-zA-Z]+)", context)

            if from_match:
                sender_id = resolve_person(person_lookup, from_match.group(1))
                if sender_id and sender_id != person_id:
                    conn.execute(
                        """INSERT INTO communication_participants
                           (communication_id, person_id, role)
                           VALUES (?, ?, ?)""",
                        (comm_id, sender_id, "sender"),
                    )
                    participants_count += 1

            if to_match:
                recip_id = resolve_person(person_lookup, to_match.group(1))
                if recip_id and recip_id != person_id:
                    conn.execute(
                        """INSERT INTO communication_participants
                           (communication_id, person_id, role)
                           VALUES (?, ?, ?)""",
                        (comm_id, recip_id, "recipient"),
                    )
                    participants_count += 1

            # Check for Epstein/Maxwell in context if not already added
            epstein_id = 1
            maxwell_id = 2
            added_ids = {person_id}
            if from_match:
                sid = resolve_person(person_lookup, from_match.group(1))
                if sid:
                    added_ids.add(sid)
            if to_match:
                rid = resolve_person(person_lookup, to_match.group(1))
                if rid:
                    added_ids.add(rid)

            for kw, pid, role in [
                ("epstein", epstein_id, "mentioned"),
                ("jeffrey epstein", epstein_id, "mentioned"),
                ("maxwell", maxwell_id, "mentioned"),
                ("ghislaine", maxwell_id, "mentioned"),
            ]:
                if kw in context.lower() and pid not in added_ids:
                    conn.execute(
                        """INSERT INTO communication_participants
                           (communication_id, person_id, role)
                           VALUES (?, ?, ?)""",
                        (comm_id, pid, role),
                    )
                    participants_count += 1
                    added_ids.add(pid)

    # ---- Phase 2: Email references from name_crossref.jsonl ----
    NAME_CROSSREF = os.path.join(BASE_DIR, "name_crossref.jsonl")
    if os.path.exists(NAME_CROSSREF):
        log.info("Scanning name_crossref.jsonl for email communications...")
        seen_eftas = set()
        with open(NAME_CROSSREF) as f:
            for line_str in f:
                entry = json.loads(line_str)
                context = entry.get("context", "")
                name = entry.get("name", "")
                efta = entry.get("efta", "")

                # Look for email patterns in context
                if not any(kw in context.lower() for kw in
                           ["@gmail", "@yahoo", "wrote:", "subject:", "sent:",
                            "from:", "to:", "@hotmail", "@aol", "@email"]):
                    continue

                # Deduplicate by EFTA number (one communication per document)
                if efta in seen_eftas:
                    continue
                seen_eftas.add(efta)

                # Extract subject line if present
                subj_match = re.search(r"Subject:\s*(.+?)(?:\n|$)", context)
                subject = subj_match.group(1).strip()[:200] if subj_match else None

                # Extract date if available
                date_match = re.search(
                    r"(\w+day,\s+\w+\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})",
                    context,
                )
                comm_date = None
                if date_match:
                    raw_date = date_match.group(1)
                    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y",
                                "%A, %B %d, %Y", "%A, %b %d, %Y"):
                        try:
                            comm_date = datetime.strptime(raw_date.strip(), fmt).strftime("%Y-%m-%d")
                            break
                        except ValueError:
                            pass

                cursor = conn.execute(
                    """INSERT INTO communications
                       (communication_date, communication_type, subject, content_summary, notes)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        comm_date,
                        "email",
                        subject,
                        context[:500],
                        f"Source: name_crossref.jsonl, EFTA: {efta}",
                    ),
                )
                comm_id = cursor.lastrowid
                comms_count += 1

                # Add the named person as participant
                person_id = resolve_person(person_lookup, name)
                added_ids = set()
                if person_id:
                    conn.execute(
                        """INSERT INTO communication_participants
                           (communication_id, person_id, role)
                           VALUES (?, ?, ?)""",
                        (comm_id, person_id, "mentioned"),
                    )
                    participants_count += 1
                    added_ids.add(person_id)

                # Check for known persons in context
                for cid, (cname, aliases) in CANONICAL_PERSONS.items():
                    if cid in added_ids:
                        continue
                    found = False
                    if cname.lower() in context.lower():
                        found = True
                    else:
                        for alias in aliases:
                            if len(alias) > 3 and alias.lower() in context.lower():
                                found = True
                                break
                    if found:
                        conn.execute(
                            """INSERT INTO communication_participants
                               (communication_id, person_id, role)
                               VALUES (?, ?, ?)""",
                            (comm_id, cid, "mentioned"),
                        )
                        participants_count += 1
                        added_ids.add(cid)

    conn.commit()
    log.info("Communications inserted: %d communications, %d participants",
             comms_count, participants_count)
    return comms_count, participants_count


# ==================================================================
# 4. RELATIONSHIPS
# ==================================================================

def insert_relationships(conn, person_lookup, flights):
    """Build relationships from multiple data sources."""
    log.info("Building relationships...")

    relationships = set()  # (person_a_id, person_b_id, rel_type, notes)

    # ---- 4a. From prosecution memo / EVIDENCE_COMPILATION.md ----
    epstein_id = 1
    maxwell_id = 2

    known_relationships = [
        # (person_a, person_b, type, notes)
        (epstein_id, maxwell_id, "co_conspirator",
         "Maxwell was Epstein's primary co-conspirator, recruiter, and enabler"),
        (epstein_id, resolve_person(person_lookup, "Leon Black"), "business_partner",
         "Black paid Epstein $158M (2012-2017) for tax advisory. Source: EFTA02731023"),
        (epstein_id, resolve_person(person_lookup, "Jes Staley"), "friend",
         "Staley visited Epstein residence; messages exchanged. Source: EFTA02731082"),
        (epstein_id, resolve_person(person_lookup, "Les Wexner"), "business_partner",
         "Wexner was Epstein's primary benefactor and business relationship"),
        (epstein_id, resolve_person(person_lookup, "Prince Andrew"), "friend",
         "Andrew described as 'good friend of Maxwell's'. Source: EFTA02731082"),
        (epstein_id, resolve_person(person_lookup, "Alan Dershowitz"), "attorney_client",
         "Dershowitz served as Epstein's defense attorney"),
        (epstein_id, resolve_person(person_lookup, "Glenn Dubin"), "friend",
         "Dubin associated with Epstein; victim 'lent out' to Dubin. Source: EFTA02731082"),
        (epstein_id, resolve_person(person_lookup, "Harvey Weinstein"), "friend",
         "Victim sent to massage Weinstein at Epstein's direction. Source: EFTA02731082"),
        (epstein_id, resolve_person(person_lookup, "Jean-Luc Brunel"), "co_conspirator",
         "Brunel recruited young models for Epstein through MC2 modeling agency"),
        (epstein_id, resolve_person(person_lookup, "Sarah Kellen"), "employer_employee",
         "Kellen scheduled appointments, facilitated access. Non-prosecution agreement"),
        (epstein_id, resolve_person(person_lookup, "Nadia Marcinkova"), "employer_employee",
         "Marcinkova participated in abuse directed by Epstein. Non-prosecution agreement"),
        (epstein_id, resolve_person(person_lookup, "Lesley Groff"), "employer_employee",
         "Groff was executive assistant, facilitated travel. Non-prosecution agreement"),
        (epstein_id, resolve_person(person_lookup, "Darren Indyke"), "attorney_client",
         "Indyke was estate executor and signatory on suspicious accounts. Source: VI Exhibit 1"),
        (epstein_id, resolve_person(person_lookup, "Richard Kahn"), "attorney_client",
         "Kahn was estate executor and signatory on suspicious accounts. Source: VI Exhibit 1"),
        (epstein_id, resolve_person(person_lookup, "Bill Clinton"), "friend",
         "Clinton flew on Epstein's aircraft multiple times per flight logs"),
        (epstein_id, resolve_person(person_lookup, "Donald Trump"), "friend",
         "Trump mentioned in Epstein documents"),
        (epstein_id, resolve_person(person_lookup, "Bill Gates"), "friend",
         "Gates mentioned in Epstein documents"),
        (maxwell_id, resolve_person(person_lookup, "Prince Andrew"), "friend",
         "Maxwell told victim Andrew was 'a good friend of Maxwell's'. Source: EFTA02731082"),
        (maxwell_id, resolve_person(person_lookup, "Glenn Dubin"), "friend",
         "Maxwell directed victim to Dubin. Source: EFTA02731082"),
        (maxwell_id, resolve_person(person_lookup, "Jean-Luc Brunel"), "co_conspirator",
         "Brunel and Maxwell collaborated in recruitment"),
        (maxwell_id, resolve_person(person_lookup, "Sarah Kellen"), "co_conspirator",
         "Kellen worked under Maxwell's direction"),
        # Recruiter-victim links
        (maxwell_id, resolve_person(person_lookup, "Giuffre"), "recruiter_victim",
         "Maxwell recruited Virginia Giuffre. Source: victim testimony"),
        (resolve_person(person_lookup, "Jean-Luc Brunel"), resolve_person(person_lookup, "Giuffre"), "other",
         "Brunel's MC2 agency involved in recruitment operations"),
        (resolve_person(person_lookup, "Eva Dubin"), resolve_person(person_lookup, "Glenn Dubin"), "family",
         "Eva and Glenn Dubin are married"),
        (resolve_person(person_lookup, "Juan Alessi"), epstein_id, "employer_employee",
         "Alessi was Epstein's house manager at Palm Beach residence"),
    ]

    for a_id, b_id, rel_type, note in known_relationships:
        if a_id and b_id and a_id != b_id:
            # Normalize order to avoid duplicates
            key = (min(a_id, b_id), max(a_id, b_id), rel_type)
            if key not in relationships:
                relationships.add(key)
                conn.execute(
                    """INSERT INTO relationships
                       (person_a_id, person_b_id, relationship_type, notes)
                       VALUES (?, ?, ?, ?)""",
                    (a_id, b_id, rel_type, note),
                )

    # ---- 4b. From flight co-occurrence ----
    # Two people on the same flight implies a relationship
    co_flight_pairs = {}  # (person_a, person_b) -> count
    for flight in flights:
        pax_ids = set()
        for pax in flight["passengers"]:
            pid = resolve_person(person_lookup, pax["name"])
            if pid is None:
                pid = resolve_person(person_lookup, pax["name_as_written"])
            if pid and pid > 0:
                pax_ids.add(pid)

        pax_list = sorted(pax_ids)
        for idx_a in range(len(pax_list)):
            for idx_b in range(idx_a + 1, len(pax_list)):
                pair = (pax_list[idx_a], pax_list[idx_b])
                co_flight_pairs[pair] = co_flight_pairs.get(pair, 0) + 1

    # Only create relationships for pairs that flew together at least 2 times
    # (to reduce noise), and that aren't already in the known relationships
    flight_rel_count = 0
    for (a_id, b_id), count in co_flight_pairs.items():
        if count < 2:
            continue
        key = (min(a_id, b_id), max(a_id, b_id), "friend")
        if key in relationships:
            continue
        # Also skip if any relationship type exists for this pair
        existing_key_other = any(
            (min(a_id, b_id), max(a_id, b_id), rt) in relationships
            for rt in ("co_conspirator", "employer_employee", "attorney_client",
                       "business_partner", "friend", "romantic", "family",
                       "recruiter_victim", "other")
        )
        if existing_key_other:
            continue

        relationships.add(key)
        conn.execute(
            """INSERT INTO relationships
               (person_a_id, person_b_id, relationship_type, notes)
               VALUES (?, ?, ?, ?)""",
            (a_id, b_id, "friend",
             f"Co-appeared on {count} flights together (from flight logs)"),
        )
        flight_rel_count += 1

    # ---- 4c. From evidence_findings.jsonl co-mentions ----
    # Find entries where multiple known persons appear in the same context
    with open(EVIDENCE_JSONL) as f:
        for line_str in f:
            entry = json.loads(line_str)
            context = entry.get("context", "")
            entity_name = entry.get("entity_name", "")

            # Find all known persons mentioned in this context
            mentioned = set()
            entity_pid = resolve_person(person_lookup, entity_name)
            if entity_pid:
                mentioned.add(entity_pid)

            text = (context + " " + entry.get("notes", "")).lower()
            for cid, (cname, aliases) in CANONICAL_PERSONS.items():
                if cname.lower() in text:
                    mentioned.add(cid)
                for alias in aliases:
                    if alias.lower() in text and len(alias) > 3:
                        mentioned.add(cid)

            mentioned_list = sorted(mentioned)
            for idx_a in range(len(mentioned_list)):
                for idx_b in range(idx_a + 1, len(mentioned_list)):
                    a, b = mentioned_list[idx_a], mentioned_list[idx_b]
                    key = (min(a, b), max(a, b), "other")
                    if key not in relationships:
                        # Check no relationship of any type exists
                        has_any = any(
                            (min(a, b), max(a, b), rt) in relationships
                            for rt in ("co_conspirator", "employer_employee",
                                       "attorney_client", "business_partner",
                                       "friend", "romantic", "family",
                                       "recruiter_victim", "other",
                                       "introduced_by")
                        )
                        if not has_any:
                            relationships.add(key)
                            conn.execute(
                                """INSERT INTO relationships
                                   (person_a_id, person_b_id, relationship_type, notes)
                                   VALUES (?, ?, ?, ?)""",
                                (a, b, "other",
                                 f"Co-mentioned in evidence document. Source: evidence_findings.jsonl id={entry.get('id')}"),
                            )

    conn.commit()
    total = len(relationships)
    log.info("Relationships inserted: %d total (%d from known associations, %d from flight co-occurrence)",
             total, len(known_relationships), flight_rel_count)
    return total


# ==================================================================
# 5. VICTIM-PERPETRATOR LINKS
# ==================================================================

def get_or_create_anon_victim(conn, person_lookup, label="Anonymous Victim"):
    """Get or create an anonymous victim placeholder in the persons table."""
    pid = resolve_person(person_lookup, label)
    if pid:
        return pid
    row = conn.execute(
        "SELECT id FROM persons WHERE canonical_name = ?", (label,)
    ).fetchone()
    if row:
        person_lookup[label.lower()] = row[0]
        return row[0]
    cursor = conn.execute(
        """INSERT INTO persons (canonical_name, person_type, notes)
           VALUES (?, 'victim', ?)""",
        (label, "Placeholder for unidentified victims in victim_perpetrator_links"),
    )
    new_id = cursor.lastrowid
    person_lookup[label.lower()] = new_id
    return new_id


def insert_victim_perpetrator_links(conn, person_lookup):
    """Build victim_perpetrator_links from existing person_interactions and EVIDENCE_COMPILATION.md."""
    log.info("Building victim-perpetrator links...")

    links = []

    # Create anonymous victim placeholder (victim_id is NOT NULL in schema)
    anon_victim_id = get_or_create_anon_victim(conn, person_lookup)
    log.info("Anonymous victim placeholder person_id = %d", anon_victim_id)

    # --- From EVIDENCE_COMPILATION.md (structured abuse evidence) ---
    epstein_id = 1
    maxwell_id = 2
    giuffre_id = resolve_person(person_lookup, "Giuffre")
    if not giuffre_id:
        giuffre_id = get_or_create_anon_victim(conn, person_lookup, "Virginia Giuffre")

    # Jeffrey Epstein -> victims (the primary perpetrator)
    # Multiple victim testimonies
    links.append({
        "victim_id": giuffre_id,
        "perpetrator_id": epstein_id,
        "abuse_type": "sexual_assault",
        "date_range": "2000-2002",
        "corroborated": True,
        "corroboration_details": "Multiple victim testimonies, flight logs, financial records. Source: EFTA02731082",
        "legal_outcome": "Charged in SDNY 2019; died in custody Aug 2019",
    })

    # Maxwell -> victims (recruiter / co-conspirator)
    links.append({
        "victim_id": giuffre_id,
        "perpetrator_id": maxwell_id,
        "abuse_type": "trafficking",
        "date_range": "2000-2002",
        "corroborated": True,
        "corroboration_details": "Multiple victim testimonies, proffer admission. Source: Maxwell Proffer, EFTA02731082",
        "legal_outcome": "Convicted Dec 2021 on sex trafficking charges",
    })
    links.append({
        "victim_id": giuffre_id,
        "perpetrator_id": maxwell_id,
        "abuse_type": "recruitment",
        "date_range": "2000-2002",
        "corroborated": True,
        "corroboration_details": "Maxwell recruited Giuffre at Mar-a-Lago. Source: victim testimony",
        "legal_outcome": "Convicted Dec 2021",
    })

    # Leon Black - sexual contact
    links.append({
        "victim_id": None,  # anonymous victim
        "perpetrator_id": resolve_person(person_lookup, "Leon Black"),
        "abuse_type": "sexual_contact",
        "date_range": None,
        "corroborated": False,
        "corroboration_details": "Single victim testimony. Source: EFTA02731082 pg 33",
        "notes": "Victim: 'Black began initiating sexual contact' during massage at Epstein's NYC residence. Another victim provided oral sex.",
        "public_denial": True,
        "denial_details": "Black has publicly denied allegations",
        "legal_outcome": "No criminal charges",
    })

    # Jes Staley - rape (CORROBORATED)
    links.append({
        "victim_id": None,
        "perpetrator_id": resolve_person(person_lookup, "Jes Staley"),
        "abuse_type": "rape",
        "date_range": None,
        "corroborated": True,
        "corroboration_details": "Victim testimony CORROBORATED BY MESSAGES between Staley and Epstein (fn 61). Source: EFTA02731082 pg 33",
        "notes": "Victim: 'he forced [her] to touch his genitals and then raped [her]'. Epstein said he left it to them to decide.",
        "public_denial": True,
        "denial_details": "Staley denied inappropriate relationship",
        "legal_outcome": "No criminal charges; dismissed from Barclays",
    })

    # Prince Andrew - sexual contact via 'lending out' system
    links.append({
        "victim_id": giuffre_id,
        "perpetrator_id": resolve_person(person_lookup, "Prince Andrew"),
        "abuse_type": "sexual_assault",
        "date_range": "2001",
        "corroborated": False,
        "corroboration_details": "Single victim testimony. Source: EFTA02731082 pg 58",
        "notes": "Maxwell told victim to 'make him happy and do the exact same things for him that she did for Epstein'",
        "public_denial": True,
        "denial_details": "Andrew denied all allegations; settled civil case for $12M+",
        "legal_outcome": "Civil settlement ($12M+); no criminal charges",
    })

    # Glenn Dubin - sexual contact via 'lending out' system
    links.append({
        "victim_id": giuffre_id,
        "perpetrator_id": resolve_person(person_lookup, "Glenn Dubin"),
        "abuse_type": "sexual_contact",
        "date_range": None,
        "corroborated": False,
        "corroboration_details": "Single victim testimony. Source: EFTA02731082 pg 58",
        "notes": "Maxwell told victim 'she had to do to Glen what [she] did for Epstein' meaning sex acts",
        "public_denial": True,
        "denial_details": "Dubin denied allegations",
        "legal_outcome": "No criminal charges",
    })

    # Harvey Weinstein - sexual contact
    links.append({
        "victim_id": None,
        "perpetrator_id": resolve_person(person_lookup, "Harvey Weinstein"),
        "abuse_type": "sexual_contact",
        "date_range": None,
        "corroborated": False,
        "corroboration_details": "Single victim testimony. Source: EFTA02731082 pg 59",
        "notes": "Victim asked to massage Weinstein; 'Weinstein directed [her] to remove her shirt'",
        "legal_outcome": "Convicted (separate cases in NY and LA)",
    })

    # Jean-Luc Brunel - trafficking/recruitment
    links.append({
        "victim_id": None,
        "perpetrator_id": resolve_person(person_lookup, "Jean-Luc Brunel"),
        "abuse_type": "trafficking",
        "date_range": "1990s-2000s",
        "corroborated": True,
        "corroboration_details": "Multiple sources. Model agent who recruited victims through MC2 agency.",
        "legal_outcome": "Charged in France 2020; died Feb 2022 (suicide in prison)",
    })

    # Sarah Kellen - facilitation
    links.append({
        "victim_id": None,
        "perpetrator_id": resolve_person(person_lookup, "Sarah Kellen"),
        "abuse_type": "coercion",
        "date_range": None,
        "corroborated": True,
        "corroboration_details": "Multiple victim testimonies. Source: EFTA02731082",
        "notes": "Scheduled appointments, facilitated access to victims for Epstein",
        "legal_outcome": "Non-prosecution agreement",
    })

    # Nadia Marcinkova - enabler/participant
    links.append({
        "victim_id": None,
        "perpetrator_id": resolve_person(person_lookup, "Nadia Marcinkova"),
        "abuse_type": "sexual_contact",
        "date_range": None,
        "corroborated": True,
        "corroboration_details": "Multiple victim testimonies. Source: EFTA02731082",
        "notes": "Participated in abuse as directed by Epstein; also described as a victim herself",
        "legal_outcome": "Non-prosecution agreement",
    })

    # Epstein trafficking operation (general)
    links.append({
        "victim_id": None,
        "perpetrator_id": epstein_id,
        "abuse_type": "trafficking",
        "date_range": "1990s-2019",
        "corroborated": True,
        "corroboration_details": "Dozens of victims, consistent testimony, documentary evidence. Source: EFTA02731082, VI Exhibit 1",
        "notes": "Systematic trafficking operation using 'lending out' system. Victims began using drugs due to frequency.",
        "legal_outcome": "Charged SDNY 2019; NPA in 2008 (Palm Beach); died Aug 2019",
    })

    # Epstein coercion/evidence destruction
    links.append({
        "victim_id": None,
        "perpetrator_id": epstein_id,
        "abuse_type": "coercion",
        "date_range": None,
        "corroborated": True,
        "corroboration_details": "Documentary evidence. Source: VI Exhibit 1 pg 41",
        "notes": "Epstein instructed participant-witnesses to destroy evidence and paid large sums to witnesses",
        "legal_outcome": "Part of overall criminal enterprise",
    })

    # --- From existing person_interactions data ---
    # Get distinct perpetrator/abuse combos from person_interactions
    cursor = conn.execute("""
        SELECT DISTINCT pi.person_id, p.canonical_name, p.person_type, pi.interaction_type
        FROM person_interactions pi
        JOIN persons p ON pi.person_id = p.id
        WHERE pi.interaction_type IN ('sexual_abuse', 'facilitated_abuse')
        AND p.person_type IN ('associate')
        AND p.id NOT IN (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15)
    """)
    for row in cursor:
        pid, pname, ptype, itype = row
        # These are associates with abuse interactions not already covered
        abuse_type = "sexual_assault" if itype == "sexual_abuse" else "coercion"
        links.append({
            "victim_id": None,
            "perpetrator_id": pid,
            "abuse_type": abuse_type,
            "corroborated": False,
            "corroboration_details": f"From person_interactions table. Type: {itype}",
            "notes": f"{pname} ({ptype}): {itype} interaction recorded",
        })

    # Insert all links
    count = 0
    for link in links:
        perp_id = link.get("perpetrator_id")
        if not perp_id:
            continue

        # victim_id is NOT NULL - use anonymous placeholder if not specified
        victim_id = link.get("victim_id") or anon_victim_id

        conn.execute(
            """INSERT INTO victim_perpetrator_links
               (victim_id, perpetrator_id, abuse_type, date_range,
                corroborated, corroboration_details,
                public_denial, denial_details, legal_outcome, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                victim_id,
                perp_id,
                link.get("abuse_type"),
                link.get("date_range"),
                link.get("corroborated", False),
                link.get("corroboration_details"),
                link.get("public_denial", False),
                link.get("denial_details"),
                link.get("legal_outcome"),
                link.get("notes"),
            ),
        )
        count += 1

    conn.commit()
    log.info("Victim-perpetrator links inserted: %d", count)
    return count


# ==================================================================
# MAIN
# ==================================================================

def main():
    log.info("=" * 70)
    log.info("EPSTEIN EVIDENCE DATABASE POPULATION SCRIPT")
    log.info("=" * 70)
    log.info("Database: %s", DB_PATH)
    log.info("Backup: %s", BACKUP_PATH)

    # Verify database exists
    if not os.path.exists(DB_PATH):
        log.error("Database not found: %s", DB_PATH)
        sys.exit(1)

    # Create backup
    log.info("Creating backup: %s", BACKUP_PATH)
    shutil.copy2(DB_PATH, BACKUP_PATH)
    log.info("Backup created successfully")

    # Verify tables are empty before populating
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    empty_tables = ["flights", "flight_passengers", "financial_transactions",
                    "communications", "communication_participants",
                    "relationships", "victim_perpetrator_links"]

    for table in empty_tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if count > 0:
            log.warning("Table %s is NOT empty (%d rows). Clearing before repopulating.", table, count)
            conn.execute(f"DELETE FROM {table}")
            conn.commit()

    # Build person lookup
    log.info("Building person name lookup...")
    person_lookup = build_person_lookup(conn)
    log.info("Person lookup built: %d name variants mapped", len(person_lookup))

    # ------------------------------------------------------------------
    # STEP 1: Parse and insert flight logs
    # ------------------------------------------------------------------
    log.info("-" * 50)
    log.info("STEP 1: Flight logs")
    log.info("-" * 50)

    records = parse_flight_logs()
    flights = group_flights(records)
    log.info("Grouped into %d distinct flights", len(flights))
    flight_count, pax_count = insert_flights(conn, flights, person_lookup)

    # ------------------------------------------------------------------
    # STEP 2: Financial transactions
    # ------------------------------------------------------------------
    log.info("-" * 50)
    log.info("STEP 2: Financial transactions")
    log.info("-" * 50)

    txn_count = insert_financial_transactions(conn, person_lookup)

    # ------------------------------------------------------------------
    # STEP 3: Communications
    # ------------------------------------------------------------------
    log.info("-" * 50)
    log.info("STEP 3: Communications")
    log.info("-" * 50)

    comm_count, part_count = insert_communications(conn, person_lookup)

    # ------------------------------------------------------------------
    # STEP 4: Relationships
    # ------------------------------------------------------------------
    log.info("-" * 50)
    log.info("STEP 4: Relationships")
    log.info("-" * 50)

    rel_count = insert_relationships(conn, person_lookup, flights)

    # ------------------------------------------------------------------
    # STEP 5: Victim-perpetrator links
    # ------------------------------------------------------------------
    log.info("-" * 50)
    log.info("STEP 5: Victim-perpetrator links")
    log.info("-" * 50)

    vpl_count = insert_victim_perpetrator_links(conn, person_lookup)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    log.info("=" * 70)
    log.info("POPULATION COMPLETE - SUMMARY")
    log.info("=" * 70)

    for table in empty_tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        log.info("  %-30s %6d rows", table, count)

    # Cross-check existing tables
    for table in ["persons", "person_interactions", "source_documents", "locations", "organizations"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        log.info("  %-30s %6d rows (pre-existing)", table, count)

    conn.close()
    log.info("Database closed. Done.")


if __name__ == "__main__":
    main()
