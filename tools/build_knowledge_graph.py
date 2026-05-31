#!/usr/bin/env python3
"""
Build Knowledge Graph from Epstein Files Evidence Database.

Reads from evidence.db and builds a proper knowledge_graph.db with:
- Entity nodes (persons, organizations, locations, properties, aircraft, shell companies)
- Weighted relationship edges with metadata
- Flight co-occurrence analysis
- Financial transaction links
- Communication links
- Victim/perpetrator links
- Known relationships

Also exports a JSON version for visualization.
"""

import sqlite3
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

# Paths
def _find_data_dir():
    """Find the directory containing the database files."""
    if os.environ.get("EPSTEIN_DATA_DIR"):
        return os.environ["EPSTEIN_DATA_DIR"]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(repo_root, "knowledge_graph.db")):
        return repo_root
    if os.path.exists(os.path.join(os.getcwd(), "knowledge_graph.db")):
        return os.getcwd()
    parent = os.path.dirname(os.getcwd())
    for name in os.listdir(parent):
        candidate = os.path.join(parent, name, "knowledge_graph.db")
        if os.path.exists(candidate):
            return os.path.join(parent, name)
    return os.getcwd()

BASE_DIR = _find_data_dir()
EVIDENCE_DB = os.path.join(BASE_DIR, "evidence_db", "evidence.db")
KG_DB = os.path.join(BASE_DIR, "knowledge_graph.db")
KG_JSON = os.path.join(BASE_DIR, "knowledge_graph_v2.json")

# Relationship type mapping from evidence.db to knowledge graph
RELATIONSHIP_TYPE_MAP = {
    "co_conspirator": "associated_with",
    "business_partner": "associated_with",
    "friend": "associated_with",
    "employer_employee": "employed_by",
    "attorney_client": "represented_by",
    "recruiter_victim": "recruited_by",
    "family": "related_to",
    "romantic": "related_to",
    "introduced_by": "associated_with",
    "other": "associated_with",
}

# Additional shell companies to add (from EVIDENCE_COMPILATION.md)
ADDITIONAL_SHELL_COMPANIES = [
    ("Financial Strategy Group, Ltd.", "shell_company", "Unknown"),
    ("Financial Trust, Inc.", "shell_company", "Unknown"),  # may already exist
    ("Hyperion Air, Inc.", "shell_company", "Unknown"),
    ("JSC Interiors, LLC", "shell_company", "Unknown"),  # may already exist
    ("Southern Trust Company, Inc.", "shell_company", "Virgin Islands"),  # may already exist
    ("The 1953 Trust", "foundation", "Virgin Islands"),  # may already exist
    ("Plan D, LLC", "shell_company", "Virgin Islands"),  # may already exist
    ("Great St. Jim, LLC", "shell_company", "Virgin Islands"),  # may already exist
]

# Additional locations to ensure exist
ADDITIONAL_LOCATIONS = [
    ("9 East 71st Street, NYC", "residence", "New York, NY", True),
    ("358 El Brillo Way, Palm Beach", "residence", "Palm Beach, FL", True),
    ("Little St. James Island, USVI", "island", "U.S. Virgin Islands", True),
    ("Zorro Ranch, Stanley, NM", "ranch", "Stanley, NM", True),
    ("Paris apartment, 16th Arrondissement", "residence", "Paris, France", False),
]


def create_kg_schema(kg_conn):
    """Drop old tables and create new knowledge graph schema."""
    cursor = kg_conn.cursor()

    # Drop old tables
    cursor.execute("DROP TABLE IF EXISTS document_mentions;")
    cursor.execute("DROP TABLE IF EXISTS relationships;")
    cursor.execute("DROP TABLE IF EXISTS entities;")
    cursor.execute("DROP TABLE IF EXISTS edge_sources;")
    cursor.execute("DROP TABLE IF EXISTS kg_metadata;")

    # Create new schema
    cursor.execute("""
        CREATE TABLE entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL CHECK(entity_type IN (
                'person', 'organization', 'location', 'property',
                'aircraft', 'shell_company'
            )),
            source_id INTEGER,
            source_table TEXT,
            aliases TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cursor.execute("""
        CREATE TABLE relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_entity_id INTEGER NOT NULL REFERENCES entities(id),
            target_entity_id INTEGER NOT NULL REFERENCES entities(id),
            relationship_type TEXT NOT NULL CHECK(relationship_type IN (
                'traveled_with', 'employed_by', 'victim_of', 'paid_by',
                'associated_with', 'communicated_with', 'visited',
                'recruited_by', 'represented_by', 'related_to',
                'owned_by', 'operated_at'
            )),
            weight INTEGER DEFAULT 1,
            date_first TEXT,
            date_last TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cursor.execute("""
        CREATE TABLE edge_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            relationship_id INTEGER NOT NULL REFERENCES relationships(id),
            source_type TEXT NOT NULL,
            source_id INTEGER,
            source_detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cursor.execute("""
        CREATE TABLE kg_metadata (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Create indexes
    cursor.execute("CREATE INDEX idx_entities_name ON entities(name);")
    cursor.execute("CREATE INDEX idx_entities_type ON entities(entity_type);")
    cursor.execute("CREATE INDEX idx_entities_source ON entities(source_table, source_id);")
    cursor.execute("CREATE UNIQUE INDEX idx_entities_unique ON entities(name, entity_type);")
    cursor.execute("CREATE INDEX idx_rel_source ON relationships(source_entity_id);")
    cursor.execute("CREATE INDEX idx_rel_target ON relationships(target_entity_id);")
    cursor.execute("CREATE INDEX idx_rel_type ON relationships(relationship_type);")
    cursor.execute("CREATE INDEX idx_rel_weight ON relationships(weight DESC);")
    cursor.execute("CREATE INDEX idx_rel_pair ON relationships(source_entity_id, target_entity_id, relationship_type);")
    cursor.execute("CREATE INDEX idx_edge_sources_rel ON edge_sources(relationship_id);")

    kg_conn.commit()
    print("[OK] Knowledge graph schema created.")


def insert_entity(cursor, name, entity_type, source_id=None, source_table=None,
                  aliases=None, metadata=None):
    """Insert an entity, returning its ID. Skip duplicates."""
    try:
        cursor.execute("""
            INSERT INTO entities (name, entity_type, source_id, source_table, aliases, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            name, entity_type, source_id, source_table,
            json.dumps(aliases) if aliases else None,
            json.dumps(metadata) if metadata else None
        ))
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        # Already exists - return existing ID
        cursor.execute(
            "SELECT id FROM entities WHERE name = ? AND entity_type = ?",
            (name, entity_type)
        )
        row = cursor.fetchone()
        return row[0] if row else None


def insert_relationship(cursor, source_id, target_id, rel_type, weight=1,
                        date_first=None, date_last=None, metadata=None):
    """Insert a relationship edge."""
    cursor.execute("""
        INSERT INTO relationships (source_entity_id, target_entity_id, relationship_type,
                                   weight, date_first, date_last, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        source_id, target_id, rel_type, weight,
        date_first, date_last,
        json.dumps(metadata) if metadata else None
    ))
    return cursor.lastrowid


def populate_persons(ev_cursor, kg_cursor):
    """Import all persons from evidence.db as person entities."""
    ev_cursor.execute("""
        SELECT id, canonical_name, aliases, person_type, occupation,
               public_figure, legal_status, notes
        FROM persons
    """)

    person_map = {}  # evidence_db person_id -> kg entity_id
    count = 0

    for row in ev_cursor.fetchall():
        pid, name, aliases_json, ptype, occupation, public_fig, legal, notes = row

        # Parse aliases
        aliases = None
        if aliases_json:
            try:
                aliases = json.loads(aliases_json)
            except (json.JSONDecodeError, TypeError):
                aliases = [aliases_json]

        metadata = {}
        if ptype:
            metadata["person_type"] = ptype
        if occupation:
            metadata["occupation"] = occupation
        if public_fig:
            metadata["public_figure"] = True
        if legal:
            metadata["legal_status"] = legal
        if notes:
            metadata["notes"] = notes

        eid = insert_entity(
            kg_cursor, name, "person",
            source_id=pid, source_table="persons",
            aliases=aliases,
            metadata=metadata if metadata else None
        )
        if eid:
            person_map[pid] = eid
            count += 1

    print(f"[OK] Imported {count} person entities.")
    return person_map


def populate_organizations(ev_cursor, kg_cursor):
    """Import organizations from evidence.db and add extras."""
    ev_cursor.execute("""
        SELECT id, name, org_type, registered_address, jurisdiction,
               registered_owner, purpose, notes
        FROM organizations
    """)

    org_map = {}  # evidence_db org_id -> kg entity_id
    count = 0

    for row in ev_cursor.fetchall():
        oid, name, org_type, addr, jurisdiction, owner, purpose, notes = row

        entity_type = "shell_company" if org_type == "shell_company" else "organization"

        metadata = {}
        if org_type:
            metadata["org_type"] = org_type
        if addr:
            metadata["registered_address"] = addr
        if jurisdiction:
            metadata["jurisdiction"] = jurisdiction
        if owner:
            metadata["registered_owner"] = owner
        if purpose:
            metadata["purpose"] = purpose
        if notes:
            metadata["notes"] = notes

        eid = insert_entity(
            kg_cursor, name, entity_type,
            source_id=oid, source_table="organizations",
            metadata=metadata if metadata else None
        )
        if eid:
            org_map[oid] = eid
            count += 1

    # Add additional shell companies that might not exist
    for name, otype, jurisdiction in ADDITIONAL_SHELL_COMPANIES:
        entity_type = "shell_company" if otype == "shell_company" else "organization"
        metadata = {"org_type": otype, "jurisdiction": jurisdiction,
                    "source": "EVIDENCE_COMPILATION.md"}
        eid = insert_entity(
            kg_cursor, name, entity_type,
            metadata=metadata
        )
        if eid:
            count += 1

    print(f"[OK] Imported {count} organization/shell_company entities.")
    return org_map


def populate_locations(ev_cursor, kg_cursor):
    """Import locations from evidence.db and add extras."""
    ev_cursor.execute("""
        SELECT id, name, address, location_type, known_abuse_location, notes
        FROM locations
    """)

    loc_map = {}  # evidence_db loc_id -> kg entity_id
    count = 0

    for row in ev_cursor.fetchall():
        lid, name, address, ltype, abuse_loc, notes = row

        entity_type = "property" if ltype in ("residence", "ranch") else "location"

        metadata = {}
        if address:
            metadata["address"] = address
        if ltype:
            metadata["location_type"] = ltype
        if abuse_loc:
            metadata["known_abuse_location"] = True
        if notes:
            metadata["notes"] = notes

        eid = insert_entity(
            kg_cursor, name, entity_type,
            source_id=lid, source_table="locations",
            metadata=metadata if metadata else None
        )
        if eid:
            loc_map[lid] = eid
            count += 1

    # Add additional locations
    for name, ltype, address, abuse in ADDITIONAL_LOCATIONS:
        entity_type = "property" if ltype in ("residence", "ranch") else "location"
        metadata = {
            "address": address,
            "location_type": ltype,
            "known_abuse_location": abuse,
            "source": "EVIDENCE_COMPILATION.md"
        }
        eid = insert_entity(
            kg_cursor, name, entity_type,
            metadata=metadata
        )
        if eid:
            count += 1

    print(f"[OK] Imported {count} location/property entities.")
    return loc_map


def populate_aircraft(kg_cursor):
    """Add known aircraft as entities."""
    aircraft = [
        ("N908JE (Boeing 727-31)", "aircraft", {
            "nickname": "Lolita Express",
            "tail_number": "N908JE",
            "aircraft_type": "Boeing 727-31",
            "owner": "Jeffrey Epstein"
        }),
        ("N909JE (Gulfstream II)", "aircraft", {
            "tail_number": "N909JE",
            "aircraft_type": "Gulfstream II/SP",
            "owner": "Jeffrey Epstein"
        }),
        ("N212JE (Cessna 421)", "aircraft", {
            "tail_number": "N212JE",
            "aircraft_type": "Cessna 421",
            "owner": "Jeffrey Epstein"
        }),
        ("N34JE (Helicopter)", "aircraft", {
            "tail_number": "N34JE",
            "aircraft_type": "Helicopter",
            "owner": "Jeffrey Epstein"
        }),
    ]

    count = 0
    for name, etype, meta in aircraft:
        eid = insert_entity(kg_cursor, name, etype, metadata=meta)
        if eid:
            count += 1

    print(f"[OK] Added {count} aircraft entities.")


def build_flight_cooccurrence(ev_cursor, kg_cursor, person_map):
    """
    Build traveled_with edges from flight co-occurrence.
    Two people on the same flight = traveled_with.
    Weight = number of shared flights.
    """
    print("[..] Computing flight co-occurrence (this may take a moment)...")

    # Get all flight_passenger pairs with dates
    ev_cursor.execute("""
        SELECT fp1.person_id AS p1, fp2.person_id AS p2,
               COUNT(DISTINCT fp1.flight_id) AS shared_flights,
               MIN(f.flight_date) AS first_flight,
               MAX(f.flight_date) AS last_flight,
               GROUP_CONCAT(DISTINCT f.flight_date) AS flight_dates
        FROM flight_passengers fp1
        JOIN flight_passengers fp2
            ON fp1.flight_id = fp2.flight_id
            AND fp1.person_id < fp2.person_id
        JOIN flights f ON fp1.flight_id = f.id
        GROUP BY fp1.person_id, fp2.person_id
    """)

    rows = ev_cursor.fetchall()
    count = 0
    skipped = 0

    for p1_id, p2_id, shared, first_date, last_date, dates_str in rows:
        eid1 = person_map.get(p1_id)
        eid2 = person_map.get(p2_id)

        if not eid1 or not eid2:
            skipped += 1
            continue

        # Collect sample flight dates for metadata
        all_dates = dates_str.split(",") if dates_str else []
        sample_dates = all_dates[:10]  # Keep first 10 for metadata

        metadata = {
            "shared_flight_count": shared,
            "sample_dates": sample_dates,
            "total_dates": len(all_dates)
        }

        insert_relationship(
            kg_cursor, eid1, eid2, "traveled_with",
            weight=shared,
            date_first=first_date,
            date_last=last_date,
            metadata=metadata
        )
        count += 1

    print(f"[OK] Created {count} traveled_with edges ({skipped} skipped for unmapped persons).")
    return count


def build_financial_edges(ev_cursor, kg_cursor, person_map, org_map):
    """Build paid_by edges from financial_transactions."""
    ev_cursor.execute("""
        SELECT id, payer_person_id, payer_org_id, payee_person_id, payee_org_id,
               amount, currency, transaction_date, transaction_type, purpose,
               memo_line, suspicious, notes
        FROM financial_transactions
    """)

    count = 0
    for row in ev_cursor.fetchall():
        (tid, payer_pid, payer_oid, payee_pid, payee_oid,
         amount, currency, tdate, ttype, purpose, memo, suspicious, notes) = row

        # Determine source entity (payer)
        source_eid = None
        if payer_pid and payer_pid in person_map:
            source_eid = person_map[payer_pid]
        elif payer_oid and payer_oid in org_map:
            source_eid = org_map[payer_oid]

        # Determine target entity (payee)
        target_eid = None
        if payee_pid and payee_pid in person_map:
            target_eid = person_map[payee_pid]
        elif payee_oid and payee_oid in org_map:
            target_eid = org_map[payee_oid]

        if not source_eid or not target_eid:
            continue

        metadata = {
            "transaction_id": tid,
            "amount": amount,
            "currency": currency or "USD",
            "transaction_type": ttype,
        }
        if purpose:
            metadata["purpose"] = purpose
        if memo:
            metadata["memo"] = memo
        if suspicious:
            metadata["suspicious"] = True
        if notes:
            metadata["notes"] = notes

        # Weight based on amount (log scale or just count)
        weight = 1

        insert_relationship(
            kg_cursor, target_eid, source_eid, "paid_by",
            weight=weight,
            date_first=tdate,
            date_last=tdate,
            metadata=metadata
        )
        count += 1

    print(f"[OK] Created {count} paid_by edges from financial transactions.")
    return count


def build_communication_edges(ev_cursor, kg_cursor, person_map):
    """Build communicated_with edges from communications."""
    # Get all communication participants grouped by communication
    ev_cursor.execute("""
        SELECT c.id, c.communication_date, c.communication_type, c.subject,
               cp.person_id, cp.role
        FROM communications c
        JOIN communication_participants cp ON c.id = cp.communication_id
        ORDER BY c.id
    """)

    # Group participants by communication
    comm_participants = defaultdict(list)
    comm_meta = {}

    for row in ev_cursor.fetchall():
        cid, cdate, ctype, subject, pid, role = row
        comm_participants[cid].append((pid, role))
        if cid not in comm_meta:
            comm_meta[cid] = {
                "communication_date": cdate,
                "communication_type": ctype,
                "subject": subject
            }

    # Build pair co-occurrence
    pair_data = defaultdict(lambda: {"count": 0, "dates": [], "types": set()})

    for cid, participants in comm_participants.items():
        pids = [p[0] for p in participants]
        meta = comm_meta[cid]

        for i in range(len(pids)):
            for j in range(i + 1, len(pids)):
                p1 = min(pids[i], pids[j])
                p2 = max(pids[i], pids[j])
                key = (p1, p2)
                pair_data[key]["count"] += 1
                if meta["communication_date"]:
                    pair_data[key]["dates"].append(meta["communication_date"])
                if meta["communication_type"]:
                    pair_data[key]["types"].add(meta["communication_type"])

    count = 0
    for (p1_id, p2_id), data in pair_data.items():
        eid1 = person_map.get(p1_id)
        eid2 = person_map.get(p2_id)
        if not eid1 or not eid2:
            continue

        dates = sorted(data["dates"])
        metadata = {
            "communication_count": data["count"],
            "communication_types": list(data["types"]),
        }
        if dates:
            metadata["sample_dates"] = dates[:10]

        insert_relationship(
            kg_cursor, eid1, eid2, "communicated_with",
            weight=data["count"],
            date_first=dates[0] if dates else None,
            date_last=dates[-1] if dates else None,
            metadata=metadata
        )
        count += 1

    print(f"[OK] Created {count} communicated_with edges from communications.")
    return count


def build_victim_edges(ev_cursor, kg_cursor, person_map):
    """Build victim_of edges from victim_perpetrator_links."""
    ev_cursor.execute("""
        SELECT id, victim_id, perpetrator_id, abuse_type, date_range,
               location_id, corroborated, corroboration_details,
               public_denial, denial_details, legal_outcome, notes
        FROM victim_perpetrator_links
    """)

    count = 0
    for row in ev_cursor.fetchall():
        (vid_id, victim_pid, perp_pid, abuse_type, date_range,
         loc_id, corroborated, corr_details, denial, denial_details,
         legal_outcome, notes) = row

        victim_eid = person_map.get(victim_pid)
        perp_eid = person_map.get(perp_pid)
        if not victim_eid or not perp_eid:
            continue

        metadata = {
            "vpl_id": vid_id,
            "abuse_type": abuse_type,
        }
        if date_range:
            metadata["date_range"] = date_range
        if corroborated:
            metadata["corroborated"] = True
        if corr_details:
            metadata["corroboration_details"] = corr_details
        if denial:
            metadata["public_denial"] = True
        if denial_details:
            metadata["denial_details"] = denial_details
        if legal_outcome:
            metadata["legal_outcome"] = legal_outcome
        if notes:
            metadata["notes"] = notes

        insert_relationship(
            kg_cursor, victim_eid, perp_eid, "victim_of",
            weight=1,
            metadata=metadata
        )
        count += 1

    print(f"[OK] Created {count} victim_of edges from victim_perpetrator_links.")
    return count


def build_known_relationships(ev_cursor, kg_cursor, person_map):
    """Build edges from the relationships table."""
    ev_cursor.execute("""
        SELECT id, person_a_id, person_b_id, relationship_type,
               date_start, date_end, notes
        FROM relationships
    """)

    count = 0
    for row in ev_cursor.fetchall():
        rid, pa_id, pb_id, rtype, date_start, date_end, notes = row

        eid_a = person_map.get(pa_id)
        eid_b = person_map.get(pb_id)
        if not eid_a or not eid_b:
            continue

        # Map to KG relationship type
        kg_rtype = RELATIONSHIP_TYPE_MAP.get(rtype, "associated_with")

        metadata = {
            "evidence_relationship_id": rid,
            "original_type": rtype,
        }
        if notes:
            metadata["notes"] = notes

        insert_relationship(
            kg_cursor, eid_a, eid_b, kg_rtype,
            weight=1,
            date_first=date_start,
            date_last=date_end,
            metadata=metadata
        )
        count += 1

    print(f"[OK] Created {count} edges from known relationships.")
    return count


def build_ownership_edges(ev_cursor, kg_cursor, person_map, org_map, loc_map):
    """Build owned_by edges for organizations and locations linked to persons."""
    # Organizations with actual_controller_id
    ev_cursor.execute("""
        SELECT id, actual_controller_id FROM organizations
        WHERE actual_controller_id IS NOT NULL
    """)

    count = 0
    for oid, controller_pid in ev_cursor.fetchall():
        org_eid = org_map.get(oid)
        person_eid = person_map.get(controller_pid)
        if org_eid and person_eid:
            insert_relationship(
                kg_cursor, org_eid, person_eid, "owned_by",
                weight=1,
                metadata={"source": "organizations.actual_controller_id"}
            )
            count += 1

    # Locations with owner_id
    ev_cursor.execute("""
        SELECT id, owner_id FROM locations
        WHERE owner_id IS NOT NULL
    """)

    for lid, owner_pid in ev_cursor.fetchall():
        loc_eid = loc_map.get(lid)
        person_eid = person_map.get(owner_pid)
        if loc_eid and person_eid:
            insert_relationship(
                kg_cursor, loc_eid, person_eid, "owned_by",
                weight=1,
                metadata={"source": "locations.owner_id"}
            )
            count += 1

    # Locations with shell_company_id
    ev_cursor.execute("""
        SELECT id, shell_company_id FROM locations
        WHERE shell_company_id IS NOT NULL
    """)

    for lid, sc_id in ev_cursor.fetchall():
        loc_eid = loc_map.get(lid)
        org_eid = org_map.get(sc_id)
        if loc_eid and org_eid:
            insert_relationship(
                kg_cursor, loc_eid, org_eid, "owned_by",
                weight=1,
                metadata={"source": "locations.shell_company_id"}
            )
            count += 1

    # Hardcoded ownership: Epstein owned/controlled all shell companies and properties
    # (evidence DB has NULLs for these FK fields, but ownership is well-established)
    epstein_eid = person_map.get(1)  # Jeffrey Epstein is person_id=1
    if epstein_eid:
        # All shell companies / organizations owned by Epstein
        kg_cursor.execute(
            "SELECT id, name FROM entities WHERE entity_type IN ('shell_company', 'organization')"
        )
        for org_eid, org_name in kg_cursor.fetchall():
            insert_relationship(
                kg_cursor, org_eid, epstein_eid, "owned_by",
                weight=1,
                metadata={"source": "known_ownership", "note": "Epstein controlled entity"}
            )
            count += 1

        # All properties and locations owned by Epstein
        kg_cursor.execute(
            "SELECT id, name FROM entities WHERE entity_type IN ('property', 'location')"
        )
        for loc_eid, loc_name in kg_cursor.fetchall():
            insert_relationship(
                kg_cursor, loc_eid, epstein_eid, "owned_by",
                weight=1,
                metadata={"source": "known_ownership", "note": "Epstein property"}
            )
            count += 1

        # All aircraft owned by Epstein
        kg_cursor.execute(
            "SELECT id, name FROM entities WHERE entity_type = 'aircraft'"
        )
        for ac_eid, ac_name in kg_cursor.fetchall():
            insert_relationship(
                kg_cursor, ac_eid, epstein_eid, "owned_by",
                weight=1,
                metadata={"source": "known_ownership", "note": "Epstein aircraft"}
            )
            count += 1

    print(f"[OK] Created {count} owned_by edges.")
    return count


def store_metadata(kg_cursor, stats):
    """Store build metadata."""
    now = datetime.now(timezone.utc).isoformat()
    entries = [
        ("build_timestamp", now),
        ("build_script", "build_knowledge_graph.py"),
        ("evidence_db_path", EVIDENCE_DB),
    ]
    for k, v in stats.items():
        entries.append((f"stat_{k}", str(v)))

    for key, value in entries:
        kg_cursor.execute("""
            INSERT OR REPLACE INTO kg_metadata (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (key, value, now))


def export_json(kg_conn):
    """Export knowledge graph to JSON for visualization."""
    cursor = kg_conn.cursor()

    # Export nodes
    cursor.execute("""
        SELECT id, name, entity_type, aliases, metadata
        FROM entities ORDER BY id
    """)

    nodes = []
    for row in cursor.fetchall():
        eid, name, etype, aliases_json, meta_json = row
        node = {
            "id": eid,
            "name": name,
            "type": etype,
        }
        if aliases_json:
            try:
                node["aliases"] = json.loads(aliases_json)
            except (json.JSONDecodeError, TypeError):
                node["aliases"] = []
        if meta_json:
            try:
                node["metadata"] = json.loads(meta_json)
            except (json.JSONDecodeError, TypeError):
                node["metadata"] = {}
        nodes.append(node)

    # Export edges
    cursor.execute("""
        SELECT id, source_entity_id, target_entity_id, relationship_type,
               weight, date_first, date_last, metadata
        FROM relationships ORDER BY id
    """)

    edges = []
    for row in cursor.fetchall():
        rid, src, tgt, rtype, weight, dfirst, dlast, meta_json = row
        edge = {
            "id": rid,
            "source": src,
            "target": tgt,
            "type": rtype,
            "weight": weight,
        }
        meta = {}
        if dfirst:
            meta["date_first"] = dfirst
        if dlast:
            meta["date_last"] = dlast
        if meta_json:
            try:
                meta.update(json.loads(meta_json))
            except (json.JSONDecodeError, TypeError):
                pass
        if meta:
            edge["metadata"] = meta
        edges.append(edge)

    graph = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }

    with open(KG_JSON, "w") as f:
        json.dump(graph, f, indent=2, default=str)

    print(f"[OK] Exported JSON: {len(nodes)} nodes, {len(edges)} edges -> {KG_JSON}")


def print_summary_statistics(kg_conn):
    """Print detailed summary statistics."""
    cursor = kg_conn.cursor()

    print("\n" + "=" * 80)
    print("KNOWLEDGE GRAPH SUMMARY STATISTICS")
    print("=" * 80)

    # Entity counts by type
    cursor.execute("""
        SELECT entity_type, COUNT(*) FROM entities GROUP BY entity_type ORDER BY COUNT(*) DESC
    """)
    print("\n--- Entity counts by type ---")
    total_entities = 0
    for etype, cnt in cursor.fetchall():
        print(f"  {etype:20s}: {cnt:>6d}")
        total_entities += cnt
    print(f"  {'TOTAL':20s}: {total_entities:>6d}")

    # Relationship counts by type
    cursor.execute("""
        SELECT relationship_type, COUNT(*), SUM(weight)
        FROM relationships GROUP BY relationship_type ORDER BY COUNT(*) DESC
    """)
    print("\n--- Relationship counts by type ---")
    total_rels = 0
    for rtype, cnt, total_w in cursor.fetchall():
        print(f"  {rtype:25s}: {cnt:>6d} edges (total weight: {int(total_w or 0):>8d})")
        total_rels += cnt
    print(f"  {'TOTAL':25s}: {total_rels:>6d}")

    # Top 20 most connected people (by degree = number of distinct edges)
    cursor.execute("""
        SELECT e.name, e.id,
               COUNT(DISTINCT CASE WHEN r.source_entity_id = e.id THEN r.target_entity_id
                                    WHEN r.target_entity_id = e.id THEN r.source_entity_id END) AS degree,
               SUM(r.weight) AS total_weight
        FROM entities e
        LEFT JOIN relationships r ON e.id = r.source_entity_id OR e.id = r.target_entity_id
        WHERE e.entity_type = 'person'
        GROUP BY e.id
        ORDER BY degree DESC
        LIMIT 20
    """)
    print("\n--- Top 20 most connected people (by unique connections) ---")
    print(f"  {'Rank':>4s}  {'Name':40s}  {'Connections':>11s}  {'Total Weight':>12s}")
    print(f"  {'----':>4s}  {'-'*40:40s}  {'-'*11:>11s}  {'-'*12:>12s}")
    for i, (name, eid, degree, tw) in enumerate(cursor.fetchall(), 1):
        print(f"  {i:4d}  {name:40s}  {degree:11d}  {int(tw or 0):12d}")

    # Top 20 flight pairs (traveled_with by weight)
    cursor.execute("""
        SELECT e1.name, e2.name, r.weight, r.date_first, r.date_last
        FROM relationships r
        JOIN entities e1 ON r.source_entity_id = e1.id
        JOIN entities e2 ON r.target_entity_id = e2.id
        WHERE r.relationship_type = 'traveled_with'
        ORDER BY r.weight DESC
        LIMIT 20
    """)
    print("\n--- Top 20 flight pairs (by shared flights) ---")
    print(f"  {'Rank':>4s}  {'Person A':30s}  {'Person B':30s}  {'Flights':>7s}  {'Date Range':20s}")
    print(f"  {'----':>4s}  {'-'*30:30s}  {'-'*30:30s}  {'-'*7:>7s}  {'-'*20:20s}")
    for i, (p1, p2, w, d1, d2) in enumerate(cursor.fetchall(), 1):
        dr = f"{d1 or '?'} - {d2 or '?'}"
        print(f"  {i:4d}  {p1:30s}  {p2:30s}  {w:7d}  {dr:20s}")

    # Network clustering: most interconnected clique (approximate via high-degree subgraph)
    # Compute average degree among connected persons
    cursor.execute("""
        SELECT AVG(deg) FROM (
            SELECT e.id AS eid,
                   COUNT(DISTINCT CASE WHEN r.source_entity_id = e.id THEN r.target_entity_id
                                        WHEN r.target_entity_id = e.id THEN r.source_entity_id END) AS deg
            FROM entities e
            JOIN relationships r ON e.id = r.source_entity_id OR e.id = r.target_entity_id
            WHERE e.entity_type = 'person'
            GROUP BY e.id
        )
    """)
    row = cursor.fetchone()
    avg_degree = row[0] if row and row[0] else 0
    print(f"\n--- Network statistics ---")
    print(f"  Average degree (connected persons): {avg_degree:.2f}")

    # Number of connected persons
    cursor.execute("""
        SELECT COUNT(DISTINCT e.id)
        FROM entities e
        JOIN relationships r ON e.id = r.source_entity_id OR e.id = r.target_entity_id
        WHERE e.entity_type = 'person'
    """)
    connected = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM entities WHERE entity_type = 'person'")
    total_persons = cursor.fetchone()[0]
    print(f"  Connected persons: {connected} / {total_persons} ({100*connected/total_persons:.1f}%)")

    # Number of isolated persons
    print(f"  Isolated persons (no edges): {total_persons - connected}")

    # Density
    if connected > 1:
        cursor.execute("SELECT COUNT(*) FROM relationships")
        total_edges = cursor.fetchone()[0]
        max_edges = connected * (connected - 1) / 2
        density = total_edges / max_edges if max_edges > 0 else 0
        print(f"  Graph density: {density:.6f}")
        print(f"  Total edges: {total_edges}")

    # Relationship type breakdown for top person (Jeffrey Epstein)
    cursor.execute("""
        SELECT id FROM entities WHERE name = 'Jeffrey Epstein' AND entity_type = 'person'
    """)
    epstein_row = cursor.fetchone()
    if epstein_row:
        epstein_id = epstein_row[0]
        cursor.execute("""
            SELECT r.relationship_type, COUNT(*), SUM(r.weight)
            FROM relationships r
            WHERE r.source_entity_id = ? OR r.target_entity_id = ?
            GROUP BY r.relationship_type
            ORDER BY COUNT(*) DESC
        """, (epstein_id, epstein_id))
        print(f"\n--- Jeffrey Epstein's relationship breakdown ---")
        for rtype, cnt, tw in cursor.fetchall():
            print(f"  {rtype:25s}: {cnt:>5d} edges (total weight: {int(tw or 0):>6d})")

    # Victim_of relationships detail
    cursor.execute("""
        SELECT e1.name AS victim, e2.name AS perpetrator, r.metadata
        FROM relationships r
        JOIN entities e1 ON r.source_entity_id = e1.id
        JOIN entities e2 ON r.target_entity_id = e2.id
        WHERE r.relationship_type = 'victim_of'
        ORDER BY e2.name, e1.name
    """)
    print(f"\n--- Victim-perpetrator links ---")
    for victim, perp, meta_json in cursor.fetchall():
        abuse = ""
        if meta_json:
            try:
                m = json.loads(meta_json)
                abuse = m.get("abuse_type", "")
            except:
                pass
        print(f"  {victim:30s}  -> victim_of ->  {perp:30s}  [{abuse}]")

    # Persons with most relationship types (diverse connections)
    cursor.execute("""
        SELECT e.name,
               COUNT(DISTINCT r.relationship_type) as type_count,
               GROUP_CONCAT(DISTINCT r.relationship_type) as types
        FROM entities e
        JOIN relationships r ON e.id = r.source_entity_id OR e.id = r.target_entity_id
        WHERE e.entity_type = 'person'
        GROUP BY e.id
        ORDER BY type_count DESC
        LIMIT 10
    """)
    print(f"\n--- Persons with most diverse connection types ---")
    for name, tc, types in cursor.fetchall():
        print(f"  {name:35s}: {tc} types ({types})")

    print("\n" + "=" * 80)


def main():
    print("=" * 80)
    print("BUILDING KNOWLEDGE GRAPH FROM EVIDENCE DATABASE")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 80)

    # Verify evidence DB exists
    if not os.path.exists(EVIDENCE_DB):
        print(f"[ERROR] Evidence database not found: {EVIDENCE_DB}")
        sys.exit(1)

    # Connect to databases
    ev_conn = sqlite3.connect(EVIDENCE_DB)
    ev_cursor = ev_conn.cursor()

    kg_conn = sqlite3.connect(KG_DB)
    kg_conn.execute("PRAGMA journal_mode=WAL;")
    kg_conn.execute("PRAGMA synchronous=NORMAL;")
    kg_cursor = kg_conn.cursor()

    stats = {}

    # Step 1: Create schema
    print("\n--- Step 1: Creating knowledge graph schema ---")
    create_kg_schema(kg_conn)

    # Step 2: Populate entities
    print("\n--- Step 2: Populating entities ---")
    person_map = populate_persons(ev_cursor, kg_cursor)
    org_map = populate_organizations(ev_cursor, kg_cursor)
    loc_map = populate_locations(ev_cursor, kg_cursor)
    populate_aircraft(kg_cursor)
    kg_conn.commit()

    kg_cursor.execute("SELECT COUNT(*) FROM entities")
    stats["total_entities"] = kg_cursor.fetchone()[0]
    print(f"[OK] Total entities: {stats['total_entities']}")

    # Step 3: Build relationships
    print("\n--- Step 3: Building relationship edges ---")

    stats["traveled_with"] = build_flight_cooccurrence(ev_cursor, kg_cursor, person_map)
    kg_conn.commit()

    stats["paid_by"] = build_financial_edges(ev_cursor, kg_cursor, person_map, org_map)
    kg_conn.commit()

    stats["communicated_with"] = build_communication_edges(ev_cursor, kg_cursor, person_map)
    kg_conn.commit()

    stats["victim_of"] = build_victim_edges(ev_cursor, kg_cursor, person_map)
    kg_conn.commit()

    stats["known_relationships"] = build_known_relationships(ev_cursor, kg_cursor, person_map)
    kg_conn.commit()

    stats["owned_by"] = build_ownership_edges(ev_cursor, kg_cursor, person_map, org_map, loc_map)
    kg_conn.commit()

    kg_cursor.execute("SELECT COUNT(*) FROM relationships")
    stats["total_relationships"] = kg_cursor.fetchone()[0]
    print(f"\n[OK] Total relationship edges: {stats['total_relationships']}")

    # Step 4: Store metadata
    print("\n--- Step 4: Storing build metadata ---")
    store_metadata(kg_cursor, stats)
    kg_conn.commit()
    print("[OK] Metadata stored.")

    # Step 5: Export JSON
    print("\n--- Step 5: Exporting JSON ---")
    export_json(kg_conn)

    # Step 6: Print statistics
    print_summary_statistics(kg_conn)

    # Cleanup
    ev_conn.close()
    kg_conn.close()

    print(f"\n[DONE] Knowledge graph built successfully.")
    print(f"  Database: {KG_DB}")
    print(f"  JSON: {KG_JSON}")
    print(f"  Entities: {stats['total_entities']}")
    print(f"  Relationships: {stats['total_relationships']}")


if __name__ == "__main__":
    main()
