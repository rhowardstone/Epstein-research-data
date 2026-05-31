#!/usr/bin/env python3
"""
Update Knowledge Graph and Evidence Database with DS10 Findings.

Reads from redaction_analysis_ds10.db and integrates new entities,
relationships, and evidence into both knowledge_graph.db and evidence.db.

Key DS10 documents:
- EFTA01660622/EFTA01660636: FBI PROMINENT NAMES briefing
- EFTA02154241: Ehud Barak scheduling
- EFTA02176329: Tom Barrack communication
- Trust/financial documents with beneficiary info
- NTOC tip reports
"""

import sqlite3
import json
import os
import sys
import re
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
KG_DB = os.path.join(BASE_DIR, "knowledge_graph.db")
EVIDENCE_DB = os.path.join(BASE_DIR, "evidence_db", "evidence.db")
DS10_DB = os.path.join(BASE_DIR, "redaction_analysis_ds10.db")

# ============================================================
# DS10 FINDINGS: Structured data extracted from redaction analysis
# ============================================================

# FBI PROMINENT NAMES document (EFTA01660636) - allegations from victim statements
# These are allegations documented in FBI briefing slides, NOT proven facts
FBI_PROMINENT_NAMES = {
    "Donald Trump": {
        "person_type": "mentioned",
        "public_figure": True,
        "occupation": "Businessman/Politician",
        "allegations": [
            "Victim stated Epstein introduced her to Trump who subsequently forced her head down (date range 1983-1985)",
            "Another victim remembered Epstein introduced her to Trump saying 'This is a good one, huh'"
        ],
        "evidence_type": "single_testimony",
        "efta": "EFTA01660636",
        "kg_relationship": "associated_with",
        "relationship_detail": "Named in FBI PROMINENT NAMES briefing; victim allegations of sexual abuse"
    },
    "Harvey Weinstein": {
        "person_type": "associate",
        "public_figure": True,
        "occupation": "Film producer",
        "allegations": [
            "Victim stated she gave Weinstein a massage during which he fondled her and masturbated",
            "Another victim stated Weinstein came to her apartment, offered job, tried to follow her into shower",
            "Victim stated Epstein told her to give Weinstein massage, Weinstein threatened her"
        ],
        "evidence_type": "corroborated_testimony",
        "efta": "EFTA01660636",
        "kg_relationship": "associated_with",
        "relationship_detail": "Named in FBI PROMINENT NAMES briefing; multiple victim allegations of massage coercion and sexual assault"
    },
    "Glen Dubin": {
        "person_type": "associate",
        "public_figure": True,
        "occupation": "Hedge fund manager",
        "aliases": ["Glenn Dubin"],
        "allegations": [
            "Victim stated Maxwell instructed her to give Dubin a massage and do what she did for Epstein"
        ],
        "evidence_type": "single_testimony",
        "efta": "EFTA01660636",
        "kg_relationship": "associated_with",
        "relationship_detail": "Named in FBI PROMINENT NAMES briefing; victim allegation of massage directed by Maxwell; also trust beneficiary"
    },
    "Prince Andrew": {
        "person_type": "associate",
        "public_figure": True,
        "occupation": "British Royal, Duke of York",
        "aliases": ["Duke of York", "Andrew"],
        "allegations": [
            "Victim stated Maxwell told her to make Prince Andrew do exact same things for Epstein",
            "Witness Steve Scully witnessed Andrew on Epstein's Island grinding against young girl",
            "Victim stated Andrew and Epstein flew on Epstein's plane"
        ],
        "evidence_type": "corroborated_testimony",
        "efta": "EFTA01660636",
        "kg_relationship": "associated_with",
        "relationship_detail": "Named in FBI PROMINENT NAMES briefing; multiple allegations including witness corroboration"
    },
    "Jes Staley": {
        "person_type": "associate",
        "public_figure": True,
        "occupation": "Banker, former Barclays CEO",
        "aliases": ["JES STALEY", "Staley"],
        "allegations": [
            "Victim told to give Staley massage at Epstein's; Staley forced her to put hands on his penis and had rough sex with her"
        ],
        "evidence_type": "single_testimony",
        "efta": "EFTA01660636",
        "kg_relationship": "associated_with",
        "relationship_detail": "Named in FBI PROMINENT NAMES briefing; victim allegation of forced sexual assault during massage"
    },
    "Leon Black": {
        "person_type": "associate",
        "public_figure": True,
        "occupation": "Private equity, Apollo Global Management",
        "aliases": ["LEON BLACK", "Black"],
        "allegations": [
            "Victim told to give Black massage; another female gave massage and he made her perform oral sex; made jokes about penis size",
            "Victim stated Black trafficked her numerous times including at Epstein's; Black threatened her life and stated he had connections"
        ],
        "evidence_type": "corroborated_testimony",
        "efta": "EFTA01660636",
        "kg_relationship": "associated_with",
        "relationship_detail": "Named in FBI PROMINENT NAMES briefing; multiple victim allegations of sexual assault and trafficking; also alleged present during abuses with Barr"
    },
    "Les Wexner": {
        "person_type": "associate",
        "public_figure": True,
        "occupation": "Retail executive, L Brands",
        "aliases": ["WEXNER", "Wexner"],
        "allegations": [
            "Victim stated Epstein earned his money from having homosexual relationship with Wexner"
        ],
        "evidence_type": "single_testimony",
        "efta": "EFTA01660636",
        "kg_relationship": "associated_with",
        "relationship_detail": "Named in FBI PROMINENT NAMES briefing; victim allegation of financial/personal relationship"
    },
    "Alan Dershowitz": {
        "person_type": "associate",
        "public_figure": True,
        "occupation": "Attorney, Harvard Law Professor",
        "aliases": ["ALAN DERSHOWITZ", "DERSHOWITZ", "Dershowitz"],
        "allegations": [
            "Victim stated she gave Dershowitz a massage on Epstein's plane (noted: not a minor at the time)"
        ],
        "evidence_type": "single_testimony",
        "efta": "EFTA01660636",
        "kg_relationship": "associated_with",
        "relationship_detail": "Named in FBI PROMINENT NAMES briefing; victim allegation of massage on plane (victim noted as not a minor)"
    },
    "Bill Clinton": {
        "person_type": "associate",
        "public_figure": True,
        "occupation": "Former US President",
        "aliases": ["Clinton", "President Clinton"],
        "allegations": [
            "FBI noted: not a victim in Epstein case; one person claimed invited to orgy with Clinton but did not attend"
        ],
        "evidence_type": "single_testimony",
        "efta": "EFTA01660636",
        "kg_relationship": "associated_with",
        "relationship_detail": "Named in FBI PROMINENT NAMES briefing; FBI noted not a victim in case; allegation of orgy invitation (not attended)"
    },
    "Howard Lutnick": {
        "person_type": "mentioned",
        "public_figure": True,
        "occupation": "CEO Cantor Fitzgerald",
        "allegations": [
            "Simon Andriesz reported Lutnick made money through Ponzi schemes and money laundering",
            "Lutnick and Epstein were neighbors; Epstein sold Lutnick a home for $10 which was then sold for millions"
        ],
        "evidence_type": "single_testimony",
        "efta": "EFTA01660636",
        "kg_relationship": "associated_with",
        "relationship_detail": "Named in FBI PROMINENT NAMES briefing; alleged financial crimes connection; neighboring property transaction"
    },
    "William Barr": {
        "person_type": "mentioned",
        "public_figure": True,
        "occupation": "Former US Attorney General",
        "aliases": ["Barr"],
        "allegations": [
            "NTOC filed stating Barr and Black were present during abuses",
            "Victim stated she was at Epstein's for model event and ran into Barr who stated he wanted to see her next time",
            "At another point Epstein asked if she had ever met Barr"
        ],
        "evidence_type": "single_testimony",
        "efta": "EFTA01660636",
        "kg_relationship": "associated_with",
        "relationship_detail": "Named in FBI PROMINENT NAMES briefing; NTOC tip alleging present during abuses; victim encounter at model event"
    },
}

# Additional persons from DS10 findings
ADDITIONAL_DS10_PERSONS = {
    "Ehud Barak": {
        "person_type": "associate",
        "public_figure": True,
        "occupation": "Former Israeli Prime Minister",
        "allegations": [
            "Scheduling document shows '8:30am Breakfast w/Ehud Barak' (EFTA02154241, Wed Nov 28)"
        ],
        "evidence_type": "documentary_only",
        "efta": "EFTA02154241",
        "kg_relationship": "associated_with",
        "relationship_detail": "Breakfast meeting scheduled with Epstein per calendar (EFTA02154241)"
    },
    "Tom Barrack": {
        "person_type": "mentioned",
        "public_figure": True,
        "occupation": "Investor, Colony Capital founder",
        "allegations": [
            "Email communication: 'Tom Barrack time change' scheduling (EFTA02176329)",
            "Reference to 'lunch at 1pm w/Tom Barrack' and meeting with 'H.E. Sheikh'"
        ],
        "evidence_type": "documentary_only",
        "efta": "EFTA02176329",
        "kg_relationship": "communicated_with",
        "relationship_detail": "Scheduling communication for meetings (EFTA02176329)"
    },
    "Bob Shapiro": {
        "person_type": "mentioned",
        "public_figure": True,
        "occupation": "Attorney",
        "aliases": ["Robert Shapiro"],
        "allegations": [
            "Named in NTOC tip: 'Attorney Allan Dershowitz was also there with Attorney Bob Shapiro' - NOTE: This is from an anonymous, unverified NTOC tip"
        ],
        "evidence_type": "circumstantial",
        "efta": "EFTA01660636",
        "kg_relationship": "associated_with",
        "relationship_detail": "Named in unverified NTOC tip alongside Dershowitz; no contact info provided by tipster"
    },
    "Elon Musk": {
        "person_type": "mentioned",
        "public_figure": True,
        "occupation": "CEO Tesla/SpaceX",
        "allegations": [
            "Named in NTOC tip alleging presence at events - NOTE: Unverified anonymous tip with no contact information provided; FBI noted 'No contact information provided'"
        ],
        "evidence_type": "circumstantial",
        "efta": "EFTA01660636",
        "kg_relationship": "associated_with",
        "relationship_detail": "Named in single unverified NTOC anonymous tip only; no corroboration; FBI could not follow up (no contact info)"
    },
    "Andrew Cuomo": {
        "person_type": "mentioned",
        "public_figure": True,
        "occupation": "Former NY Governor",
        "aliases": ["Cuomo"],
        "allegations": [
            "Mentioned in NTOC tip context: caller reported school wanted her to testify against Andrew Cuomo",
            "Referenced in PROMINENT NAMES doc in context of Leon Black section"
        ],
        "evidence_type": "circumstantial",
        "efta": "EFTA01660636",
        "kg_relationship": "associated_with",
        "relationship_detail": "Mentioned in NTOC tip context and FBI PROMINENT NAMES briefing"
    },
    "Simon Andriesz": {
        "person_type": "mentioned",
        "public_figure": False,
        "occupation": "Unknown",
        "allegations": [
            "Reported allegations about Howard Lutnick's financial dealings with Epstein"
        ],
        "evidence_type": "single_testimony",
        "efta": "EFTA01660636",
        "kg_relationship": "associated_with",
        "relationship_detail": "Source of allegations about Lutnick in FBI PROMINENT NAMES briefing"
    },
    "Steve Scully": {
        "person_type": "mentioned",
        "public_figure": False,
        "occupation": "Unknown",
        "allegations": [
            "FBI briefing notes witness 'Steve Scully witnessed Andrew on Epstein's Island grinding against young girl' - noted with ** indicating criminal history"
        ],
        "evidence_type": "single_testimony",
        "efta": "EFTA01660636",
        "kg_relationship": "associated_with",
        "relationship_detail": "Witness named in FBI PROMINENT NAMES briefing re: Prince Andrew; noted as having criminal history"
    },
    "Alfredo Rodriguez": {
        "person_type": "mentioned",
        "public_figure": False,
        "occupation": "Former Epstein employee",
        "aliases": ["Rodriquez"],
        "allegations": [
            "Former employee who failed to comply with federal subpoena; attempted to sell documents for $50,000",
            "Arrested 12/8/2009 for obstruction; black book seized during undercover operation",
            "Convicted 2010, sentenced to 18 months"
        ],
        "evidence_type": "corroborated_documentary",
        "efta": "EFTA01660623",
        "kg_relationship": "employed_by",
        "relationship_detail": "Former Epstein employee; convicted of obstruction; source of black book"
    },
    "Nicholas Tartaglione": {
        "person_type": "mentioned",
        "public_figure": False,
        "occupation": "Inmate",
        "allegations": [
            "Placed with Epstein in SHU at MCC on 7/10/2019-7/23/2019"
        ],
        "evidence_type": "documentary_only",
        "efta": "EFTA01660622",
        "kg_relationship": "associated_with",
        "relationship_detail": "MCC cellmate with Epstein in SHU before first suicide attempt"
    },
    "Efrain Reyes": {
        "person_type": "mentioned",
        "public_figure": False,
        "occupation": "Inmate",
        "allegations": [
            "Cellmate of Epstein; new cellmate in SHU from 7/30/2019; released 8/9/2019 (day before Epstein's death)",
            "Interviewed on 8/16/2019 by AUSA Rebekah Donaleski"
        ],
        "evidence_type": "documentary_only",
        "efta": "EFTA01660625",
        "kg_relationship": "associated_with",
        "relationship_detail": "MCC cellmate released day before Epstein death; interviewed by AUSA"
    },
    "Michael Thomas": {
        "person_type": "mentioned",
        "public_figure": False,
        "occupation": "MCC Corrections Officer",
        "allegations": [
            "Was working during shift when Epstein committed suicide",
            "Charged by SDNY along with Tova Noel; entered deferred prosecution agreement 5/2021"
        ],
        "evidence_type": "corroborated_documentary",
        "efta": "EFTA01660625",
        "kg_relationship": "associated_with",
        "relationship_detail": "MCC CO on duty during Epstein death; charged and DPA"
    },
    "Tova Noel": {
        "person_type": "mentioned",
        "public_figure": False,
        "occupation": "MCC Corrections Officer",
        "allegations": [
            "Charged by SDNY with conspiracy along with Michael Thomas; deferred prosecution agreement 5/2021"
        ],
        "evidence_type": "corroborated_documentary",
        "efta": "EFTA01660625",
        "kg_relationship": "associated_with",
        "relationship_detail": "MCC CO charged with conspiracy re: Epstein death; DPA"
    },
}

# Trust beneficiary relationships from DS10 financial documents
TRUST_BENEFICIARIES = {
    "Ghislaine Maxwell": {
        "trusts": ["The 1953 Trust", "Insurance Trust"],
        "banks": ["Deutsche Bank", "JPMorgan"],
        "efta_refs": ["multiple DS10 financial documents"],
        "detail": "Multiple trust accounts and bank relationships found in DS10 hidden text"
    },
    "Jean-Luc Brunel": {
        "trusts": [],
        "banks": [],
        "efta_refs": ["EFTA01660622"],
        "detail": "Interview records found in DS10; known associate/co-conspirator"
    },
    "Karyna Shuliak": {
        "trusts": ["Insurance Trust", "The Haze Trust"],
        "banks": ["Deutsche Bank"],
        "efta_refs": ["multiple DS10 financial documents"],
        "detail": "Named as beneficiary in Insurance Trust and suite connections with Epstein"
    },
    "Richard Kahn": {
        "trusts": ["Caterpillar Trust 2"],
        "banks": [],
        "efta_refs": ["multiple DS10 financial documents"],
        "detail": "Trust relationship found in DS10; attorney/trustee role"
    },
    "Larry Visoski": {
        "trusts": [],
        "banks": [],
        "efta_refs": ["multiple DS10 financial documents"],
        "detail": "Pilot; named in trust/financial documents in DS10"
    },
    "Glen Dubin": {
        "trusts": [],
        "banks": [],
        "efta_refs": ["EFTA01660636"],
        "detail": "Named in FBI PROMINENT NAMES as massage recipient; trust connections"
    },
    "Darren Indyke": {
        "trusts": ["Caterpillar Trust 2", "Insurance Trust"],
        "banks": ["Deutsche Bank"],
        "efta_refs": ["multiple DS10 financial documents"],
        "detail": "Attorney/trustee appearing extensively in DS10 financial/trust documents"
    },
    "Kevin Maxwell": {
        "trusts": [],
        "banks": [],
        "efta_refs": ["DS10 financial documents"],
        "detail": "Ghislaine Maxwell's brother; appears in financial documents"
    },
}

# Financial organizations from DS10
DS10_ORGANIZATIONS = [
    ("Deutsche Bank", "bank", "Financial institution with multiple Epstein accounts"),
    ("JPMorgan Chase", "bank", "Financial institution with Epstein accounts"),
    ("Deutsche Bank Securities Inc.", "bank", "Securities arm; offered securities through this entity"),
    ("Deutsche Bank Trust Co Americas", "bank", "Trust subsidiary at 60 Wall St"),
    ("SunTrust", "bank", "Bank with Epstein-related accounts"),
    ("The Haze Trust", "foundation", "Trust entity found in DS10 financial documents"),
    ("Insurance Trust", "foundation", "Epstein insurance trust; beneficiaries include Shuliak, Indyke"),
    ("Caterpillar Trust 2", "foundation", "Trust entity; Indyke/Kahn connected"),
    ("GSR Mortgage Loan Trust 2005-5F", "foundation", "Mortgage-backed security in Maxwell accounts"),
    ("FBI", "government", "Federal Bureau of Investigation"),
    ("MCC New York", "government", "Metropolitan Correctional Center, New York"),
    ("SDNY", "government", "Southern District of New York US Attorney's Office"),
]

# FBI Case numbers from DS10 timeline (EFTA01660622)
FBI_CASES = {
    "31E-MM-1080": "Initial Epstein investigation",
    "72-MM-113327": "Obstruction case (Alfredo Rodriguez / black book)",
    "50D-NY-30275": "Epstein-related NY investigation",
    "90A-NY-31512": "MCC death investigation (90A case)",
}

# Key document EFTA references found in DS10
DS10_KEY_DOCUMENTS = [
    ("EFTA01660622", "FBI Epstein Case Timeline Briefing", 10),
    ("EFTA01660623", "FBI Obstruction Case - Alfredo Rodriguez / Black Book", 10),
    ("EFTA01660625", "FBI MCC Death Investigation (90A case)", 10),
    ("EFTA01660634", "FBI Maxwell Trial Summary", 10),
    ("EFTA01660636", "FBI PROMINENT NAMES Briefing Document", 10),
    ("EFTA02154241", "Ehud Barak Breakfast Scheduling", 10),
    ("EFTA02176329", "Tom Barrack Communication / Scheduling", 10),
]


def get_or_create_entity(kg_cursor, name, entity_type, source_id=None,
                         source_table=None, aliases=None, metadata=None):
    """Get existing entity or create new one. Returns entity ID."""
    kg_cursor.execute(
        "SELECT id FROM entities WHERE name = ? AND entity_type = ?",
        (name, entity_type)
    )
    row = kg_cursor.fetchone()
    if row:
        # Update metadata if we have new info
        if metadata:
            kg_cursor.execute(
                "SELECT metadata FROM entities WHERE id = ?", (row[0],)
            )
            existing_meta_row = kg_cursor.fetchone()
            existing_meta = {}
            if existing_meta_row and existing_meta_row[0]:
                try:
                    existing_meta = json.loads(existing_meta_row[0])
                except (json.JSONDecodeError, TypeError):
                    pass
            # Merge metadata
            existing_meta.update(metadata)
            kg_cursor.execute(
                "UPDATE entities SET metadata = ? WHERE id = ?",
                (json.dumps(existing_meta), row[0])
            )
        return row[0], False  # (id, is_new)

    try:
        kg_cursor.execute("""
            INSERT INTO entities (name, entity_type, source_id, source_table, aliases, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            name, entity_type, source_id, source_table,
            json.dumps(aliases) if aliases else None,
            json.dumps(metadata) if metadata else None
        ))
        return kg_cursor.lastrowid, True
    except sqlite3.IntegrityError:
        kg_cursor.execute(
            "SELECT id FROM entities WHERE name = ? AND entity_type = ?",
            (name, entity_type)
        )
        row = kg_cursor.fetchone()
        return (row[0] if row else None), False


def get_or_create_evidence_person(ev_cursor, name, person_type="mentioned",
                                   public_figure=False, occupation=None,
                                   aliases=None, notes=None):
    """Get or create a person in evidence.db. Returns person ID."""
    ev_cursor.execute(
        "SELECT id, person_type FROM persons WHERE canonical_name = ?",
        (name,)
    )
    row = ev_cursor.fetchone()
    if row:
        pid = row[0]
        existing_type = row[1]
        # Upgrade type if we have stronger classification
        type_priority = {
            'perpetrator': 6, 'enabler': 5, 'recruiter': 4,
            'associate': 3, 'witness': 2, 'mentioned': 1,
            'unknown': 0, 'victim': 3, 'employee': 2
        }
        if type_priority.get(person_type, 0) > type_priority.get(existing_type, 0):
            ev_cursor.execute(
                "UPDATE persons SET person_type = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (person_type, pid)
            )
        # Update public_figure if needed
        if public_figure:
            ev_cursor.execute(
                "UPDATE persons SET public_figure = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (pid,)
            )
        # Update occupation if not set
        if occupation:
            ev_cursor.execute(
                "UPDATE persons SET occupation = COALESCE(occupation, ?), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (occupation, pid)
            )
        return pid, False

    try:
        ev_cursor.execute("""
            INSERT INTO persons (canonical_name, person_type, public_figure, occupation, aliases, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            name, person_type, public_figure, occupation,
            json.dumps(aliases) if aliases else None,
            notes
        ))
        return ev_cursor.lastrowid, True
    except sqlite3.IntegrityError:
        ev_cursor.execute(
            "SELECT id FROM persons WHERE canonical_name = ?", (name,)
        )
        row = ev_cursor.fetchone()
        return (row[0] if row else None), False


def get_or_create_evidence_org(ev_cursor, name, org_type, notes=None):
    """Get or create an organization in evidence.db."""
    ev_cursor.execute(
        "SELECT id FROM organizations WHERE name = ?", (name,)
    )
    row = ev_cursor.fetchone()
    if row:
        return row[0], False

    try:
        ev_cursor.execute("""
            INSERT INTO organizations (name, org_type, notes)
            VALUES (?, ?, ?)
        """, (name, org_type, notes))
        return ev_cursor.lastrowid, True
    except sqlite3.IntegrityError:
        ev_cursor.execute(
            "SELECT id FROM organizations WHERE name = ?", (name,)
        )
        row = ev_cursor.fetchone()
        return (row[0] if row else None), False


def get_or_create_source_document(ev_cursor, efta_number, title, dataset_number=10):
    """Get or create a source document in evidence.db."""
    ev_cursor.execute(
        "SELECT id FROM source_documents WHERE efta_number = ?",
        (efta_number,)
    )
    row = ev_cursor.fetchone()
    if row:
        return row[0], False

    filename = f"ds10_{efta_number}.pdf"
    ev_cursor.execute("""
        INSERT INTO source_documents (filename, efta_number, document_type, title, dataset_number,
                                       notes)
        VALUES (?, ?, 'pdf', ?, ?, 'Added from DS10 redaction analysis')
    """, (filename, efta_number, title, dataset_number))
    return ev_cursor.lastrowid, True


def add_source_extract(ev_cursor, doc_id, content, extract_type="redaction_bypass",
                       confidence=0.85):
    """Add a source extract to evidence.db. Returns extract ID."""
    ev_cursor.execute("""
        INSERT INTO source_extracts (source_document_id, extract_type, content, confidence_score)
        VALUES (?, ?, ?, ?)
    """, (doc_id, extract_type, content, confidence))
    return ev_cursor.lastrowid


def add_person_interaction(ev_cursor, person_id, interaction_type, description,
                           source_extract_id, evidence_strength, victim_contact="unclear",
                           knowledge_level="unclear", verbatim_quote=None):
    """Add a person interaction to evidence.db."""
    ev_cursor.execute("""
        INSERT INTO person_interactions (person_id, interaction_type, victim_contact,
                                          knowledge_level, description, verbatim_quote,
                                          source_extract_id, evidence_strength)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (person_id, interaction_type, victim_contact, knowledge_level,
          description, verbatim_quote, source_extract_id, evidence_strength))
    return ev_cursor.lastrowid


def get_or_create_relationship(kg_cursor, source_id, target_id, rel_type,
                                weight=1, metadata=None):
    """Get existing or create new relationship. If exists, update weight."""
    kg_cursor.execute("""
        SELECT id, weight FROM relationships
        WHERE source_entity_id = ? AND target_entity_id = ? AND relationship_type = ?
    """, (source_id, target_id, rel_type))
    row = kg_cursor.fetchone()

    if row:
        # Update weight (add)
        new_weight = row[1] + weight
        kg_cursor.execute(
            "UPDATE relationships SET weight = ?, metadata = ? WHERE id = ?",
            (new_weight, json.dumps(metadata) if metadata else None, row[0])
        )
        return row[0], False

    # Also check reverse direction for symmetric relationships
    if rel_type in ('associated_with', 'communicated_with', 'traveled_with'):
        kg_cursor.execute("""
            SELECT id, weight FROM relationships
            WHERE source_entity_id = ? AND target_entity_id = ? AND relationship_type = ?
        """, (target_id, source_id, rel_type))
        row = kg_cursor.fetchone()
        if row:
            new_weight = row[1] + weight
            # Merge metadata
            if metadata:
                existing_meta = {}
                kg_cursor.execute("SELECT metadata FROM relationships WHERE id = ?", (row[0],))
                meta_row = kg_cursor.fetchone()
                if meta_row and meta_row[0]:
                    try:
                        existing_meta = json.loads(meta_row[0])
                    except:
                        pass
                existing_meta.update(metadata)
                metadata = existing_meta
            kg_cursor.execute(
                "UPDATE relationships SET weight = ?, metadata = ? WHERE id = ?",
                (new_weight, json.dumps(metadata) if metadata else None, row[0])
            )
            return row[0], False

    kg_cursor.execute("""
        INSERT INTO relationships (source_entity_id, target_entity_id, relationship_type,
                                   weight, metadata)
        VALUES (?, ?, ?, ?, ?)
    """, (source_id, target_id, rel_type, weight,
          json.dumps(metadata) if metadata else None))
    return kg_cursor.lastrowid, True


def add_edge_source(kg_cursor, relationship_id, source_type, source_id=None,
                    source_detail=None):
    """Add an edge source to knowledge_graph.db."""
    kg_cursor.execute("""
        INSERT INTO edge_sources (relationship_id, source_type, source_id, source_detail)
        VALUES (?, ?, ?, ?)
    """, (relationship_id, source_type, source_id, source_detail))
    return kg_cursor.lastrowid


def count_ds10_cooccurrences(ds10_cursor, name):
    """Count how many DS10 hidden text entries mention a name."""
    # Use simple LIKE matching
    ds10_cursor.execute("""
        SELECT COUNT(*) FROM redactions
        WHERE hidden_text IS NOT NULL AND length(hidden_text) > 2
        AND hidden_text LIKE ?
    """, (f"%{name}%",))
    return ds10_cursor.fetchone()[0]


def main():
    print("=" * 80)
    print("UPDATING KNOWLEDGE GRAPH AND EVIDENCE DB WITH DS10 FINDINGS")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 80)

    # Verify all DBs exist
    for path, label in [(KG_DB, "Knowledge Graph"), (EVIDENCE_DB, "Evidence DB"),
                        (DS10_DB, "DS10 Redaction Analysis")]:
        if not os.path.exists(path):
            print(f"[ERROR] {label} not found: {path}")
            sys.exit(1)

    # Connect
    kg_conn = sqlite3.connect(KG_DB)
    kg_conn.execute("PRAGMA journal_mode=WAL;")
    kg_conn.execute("PRAGMA synchronous=NORMAL;")
    kg_cursor = kg_conn.cursor()

    ev_conn = sqlite3.connect(EVIDENCE_DB)
    ev_cursor = ev_conn.cursor()

    ds10_conn = sqlite3.connect(DS10_DB)
    ds10_cursor = ds10_conn.cursor()

    # Track statistics
    stats = {
        "new_kg_entities": 0,
        "updated_kg_entities": 0,
        "new_kg_relationships": 0,
        "updated_kg_relationships": 0,
        "new_evidence_persons": 0,
        "updated_evidence_persons": 0,
        "new_evidence_orgs": 0,
        "new_source_documents": 0,
        "new_source_extracts": 0,
        "new_person_interactions": 0,
        "new_edge_sources": 0,
    }

    # ============================================================
    # STEP 1: Get Jeffrey Epstein entity IDs (central node)
    # ============================================================
    print("\n--- Step 1: Locating central Epstein entity ---")

    kg_cursor.execute(
        "SELECT id FROM entities WHERE name = 'Jeffrey Epstein' AND entity_type = 'person'"
    )
    epstein_kg_id = kg_cursor.fetchone()
    if epstein_kg_id:
        epstein_kg_id = epstein_kg_id[0]
    else:
        print("[WARN] Jeffrey Epstein not found in KG - creating")
        epstein_kg_id, _ = get_or_create_entity(kg_cursor, "Jeffrey Epstein", "person")

    ev_cursor.execute(
        "SELECT id FROM persons WHERE canonical_name = 'Jeffrey Epstein'"
    )
    epstein_ev_id = ev_cursor.fetchone()
    if epstein_ev_id:
        epstein_ev_id = epstein_ev_id[0]
    else:
        print("[WARN] Jeffrey Epstein not found in evidence DB - creating")
        epstein_ev_id, _ = get_or_create_evidence_person(
            ev_cursor, "Jeffrey Epstein", "perpetrator"
        )

    print(f"  Epstein KG entity ID: {epstein_kg_id}")
    print(f"  Epstein evidence person ID: {epstein_ev_id}")

    # ============================================================
    # STEP 2: Add DS10 source documents to evidence.db
    # ============================================================
    print("\n--- Step 2: Adding DS10 source documents ---")

    doc_id_map = {}  # efta -> source_document_id
    for efta, title, ds_num in DS10_KEY_DOCUMENTS:
        doc_id, is_new = get_or_create_source_document(ev_cursor, efta, title, ds_num)
        doc_id_map[efta] = doc_id
        if is_new:
            stats["new_source_documents"] += 1
            print(f"  [NEW] Document: {efta} - {title}")
        else:
            print(f"  [EXISTS] Document: {efta}")

    ev_conn.commit()

    # ============================================================
    # STEP 3: Add organizations from DS10
    # ============================================================
    print("\n--- Step 3: Adding DS10 organizations ---")

    org_kg_ids = {}  # name -> kg entity id
    org_ev_ids = {}  # name -> evidence org id

    for org_name, org_type, notes in DS10_ORGANIZATIONS:
        # Knowledge graph
        entity_type = "organization"
        if org_type in ("shell_company", "foundation"):
            entity_type = "shell_company"

        # Try to add to KG - need to handle the CHECK constraint
        # KG only allows: person, organization, location, property, aircraft, shell_company
        kg_id, is_new = get_or_create_entity(
            kg_cursor, org_name, entity_type,
            metadata={"org_type": org_type, "source": "ds10", "notes": notes}
        )
        org_kg_ids[org_name] = kg_id
        if is_new:
            stats["new_kg_entities"] += 1
            print(f"  [NEW KG] {org_name} ({entity_type})")
        else:
            stats["updated_kg_entities"] += 1

        # Evidence DB
        ev_id, is_new_ev = get_or_create_evidence_org(ev_cursor, org_name, org_type, notes)
        org_ev_ids[org_name] = ev_id
        if is_new_ev:
            stats["new_evidence_orgs"] += 1
            print(f"  [NEW EV] {org_name} ({org_type})")

    kg_conn.commit()
    ev_conn.commit()

    # ============================================================
    # STEP 4: Process FBI PROMINENT NAMES persons
    # ============================================================
    print("\n--- Step 4: Processing FBI PROMINENT NAMES persons ---")

    person_kg_ids = {}  # name -> kg entity id
    person_ev_ids = {}  # name -> evidence person id

    all_persons = {}
    all_persons.update(FBI_PROMINENT_NAMES)
    all_persons.update(ADDITIONAL_DS10_PERSONS)

    for name, info in all_persons.items():
        aliases = info.get("aliases", [])
        person_type = info.get("person_type", "mentioned")
        public_figure = info.get("public_figure", False)
        occupation = info.get("occupation", None)

        # Count DS10 co-occurrences for weight
        cooccurrence_count = count_ds10_cooccurrences(ds10_cursor, name)
        # Also count alias occurrences
        for alias in aliases:
            cooccurrence_count += count_ds10_cooccurrences(ds10_cursor, alias)

        # Knowledge graph entity
        kg_metadata = {
            "person_type": person_type,
            "source": "ds10_analysis",
            "ds10_mention_count": cooccurrence_count,
        }
        if public_figure:
            kg_metadata["public_figure"] = True
        if occupation:
            kg_metadata["occupation"] = occupation
        if info.get("relationship_detail"):
            kg_metadata["ds10_detail"] = info["relationship_detail"]

        kg_id, is_new_kg = get_or_create_entity(
            kg_cursor, name, "person",
            aliases=aliases if aliases else None,
            metadata=kg_metadata
        )
        person_kg_ids[name] = kg_id
        if is_new_kg:
            stats["new_kg_entities"] += 1
            print(f"  [NEW KG] {name} (ds10_mentions={cooccurrence_count})")
        else:
            stats["updated_kg_entities"] += 1
            print(f"  [UPD KG] {name} (ds10_mentions={cooccurrence_count})")

        # Evidence DB person
        ev_id, is_new_ev = get_or_create_evidence_person(
            ev_cursor, name, person_type, public_figure, occupation,
            aliases=aliases if aliases else None,
            notes=f"DS10 source: {info.get('efta', 'N/A')}"
        )
        person_ev_ids[name] = ev_id
        if is_new_ev:
            stats["new_evidence_persons"] += 1
            print(f"  [NEW EV] {name}")
        else:
            stats["updated_evidence_persons"] += 1

    kg_conn.commit()
    ev_conn.commit()

    # ============================================================
    # STEP 5: Add relationships to knowledge graph
    # ============================================================
    print("\n--- Step 5: Adding DS10 relationships to knowledge graph ---")

    # 5a. FBI PROMINENT NAMES -> associated_with Epstein
    print("  5a. FBI PROMINENT NAMES associations with Epstein...")
    for name, info in FBI_PROMINENT_NAMES.items():
        kg_id = person_kg_ids.get(name)
        if not kg_id:
            continue

        rel_type = info.get("kg_relationship", "associated_with")
        cooccurrence_count = count_ds10_cooccurrences(ds10_cursor, name)
        for alias in info.get("aliases", []):
            cooccurrence_count += count_ds10_cooccurrences(ds10_cursor, alias)
        weight = max(1, cooccurrence_count)

        metadata = {
            "source": "ds10_fbi_prominent_names",
            "efta": info.get("efta"),
            "evidence_type": info.get("evidence_type"),
            "detail": info.get("relationship_detail"),
            "ds10_mention_count": cooccurrence_count,
        }

        rel_id, is_new = get_or_create_relationship(
            kg_cursor, kg_id, epstein_kg_id, rel_type,
            weight=weight, metadata=metadata
        )
        if is_new:
            stats["new_kg_relationships"] += 1
        else:
            stats["updated_kg_relationships"] += 1

        # Add edge source
        add_edge_source(
            kg_cursor, rel_id, "ds10_redaction_analysis",
            source_detail=f"FBI PROMINENT NAMES ({info.get('efta', 'EFTA01660636')})"
        )
        stats["new_edge_sources"] += 1

    # 5b. Additional DS10 persons -> associated_with Epstein
    print("  5b. Additional DS10 person associations...")
    for name, info in ADDITIONAL_DS10_PERSONS.items():
        kg_id = person_kg_ids.get(name)
        if not kg_id:
            continue

        rel_type = info.get("kg_relationship", "associated_with")
        cooccurrence_count = count_ds10_cooccurrences(ds10_cursor, name)
        weight = max(1, cooccurrence_count)

        metadata = {
            "source": "ds10_analysis",
            "efta": info.get("efta"),
            "evidence_type": info.get("evidence_type"),
            "detail": info.get("relationship_detail"),
            "ds10_mention_count": cooccurrence_count,
        }

        rel_id, is_new = get_or_create_relationship(
            kg_cursor, kg_id, epstein_kg_id, rel_type,
            weight=weight, metadata=metadata
        )
        if is_new:
            stats["new_kg_relationships"] += 1
        else:
            stats["updated_kg_relationships"] += 1

        add_edge_source(
            kg_cursor, rel_id, "ds10_redaction_analysis",
            source_detail=f"DS10 document ({info.get('efta', 'N/A')})"
        )
        stats["new_edge_sources"] += 1

    # 5c. Trust beneficiary relationships
    print("  5c. Trust beneficiary relationships...")
    for name, info in TRUST_BENEFICIARIES.items():
        # Get or create the person KG entity
        kg_id = person_kg_ids.get(name)
        if not kg_id:
            kg_cursor.execute(
                "SELECT id FROM entities WHERE name = ? AND entity_type = 'person'",
                (name,)
            )
            row = kg_cursor.fetchone()
            if row:
                kg_id = row[0]
            else:
                kg_id, is_new = get_or_create_entity(
                    kg_cursor, name, "person",
                    metadata={"source": "ds10_trust_docs"}
                )
                if is_new:
                    stats["new_kg_entities"] += 1
            person_kg_ids[name] = kg_id

        # Link to trusts
        for trust_name in info.get("trusts", []):
            trust_kg_id = org_kg_ids.get(trust_name)
            if trust_kg_id:
                rel_id, is_new = get_or_create_relationship(
                    kg_cursor, kg_id, trust_kg_id, "associated_with",
                    weight=1,
                    metadata={
                        "source": "ds10_trust_beneficiary",
                        "relationship_subtype": "beneficiary",
                        "detail": info.get("detail")
                    }
                )
                if is_new:
                    stats["new_kg_relationships"] += 1
                    print(f"    [NEW] {name} -> beneficiary of -> {trust_name}")
                add_edge_source(kg_cursor, rel_id, "ds10_financial_docs",
                                source_detail=f"Trust beneficiary ({', '.join(info.get('efta_refs', []))})")
                stats["new_edge_sources"] += 1

        # Link to banks
        for bank_name in info.get("banks", []):
            bank_kg_id = org_kg_ids.get(bank_name)
            if bank_kg_id:
                rel_id, is_new = get_or_create_relationship(
                    kg_cursor, kg_id, bank_kg_id, "associated_with",
                    weight=1,
                    metadata={
                        "source": "ds10_financial",
                        "relationship_subtype": "banking_relationship",
                        "detail": f"DS10 bank records for {name}"
                    }
                )
                if is_new:
                    stats["new_kg_relationships"] += 1
                    print(f"    [NEW] {name} -> banking with -> {bank_name}")
                add_edge_source(kg_cursor, rel_id, "ds10_financial_docs",
                                source_detail=f"Banking relationship")
                stats["new_edge_sources"] += 1

    # 5d. Financial institution relationships with Epstein
    print("  5d. Financial institution links to Epstein...")
    for org_name in ["Deutsche Bank", "JPMorgan Chase", "SunTrust",
                     "Deutsche Bank Securities Inc.", "Deutsche Bank Trust Co Americas"]:
        org_kg_id = org_kg_ids.get(org_name)
        if org_kg_id:
            metadata = {
                "source": "ds10_financial",
                "detail": f"Epstein accounts at {org_name} found in DS10 documents"
            }
            rel_id, is_new = get_or_create_relationship(
                kg_cursor, epstein_kg_id, org_kg_id, "associated_with",
                weight=5, metadata=metadata
            )
            if is_new:
                stats["new_kg_relationships"] += 1
                print(f"    [NEW] Epstein -> banking -> {org_name}")
            add_edge_source(kg_cursor, rel_id, "ds10_financial_docs",
                            source_detail="DS10 financial records")
            stats["new_edge_sources"] += 1

    # 5e. FBI/government relationships
    print("  5e. FBI case relationships...")
    fbi_kg_id = org_kg_ids.get("FBI")
    if fbi_kg_id:
        # Epstein investigated by FBI
        rel_id, is_new = get_or_create_relationship(
            kg_cursor, epstein_kg_id, fbi_kg_id, "associated_with",
            weight=10,
            metadata={
                "source": "ds10_fbi_timeline",
                "relationship_subtype": "investigation_subject",
                "fbi_cases": FBI_CASES,
                "detail": "Multiple FBI investigations spanning 2006-2019"
            }
        )
        if is_new:
            stats["new_kg_relationships"] += 1
        add_edge_source(kg_cursor, rel_id, "ds10_fbi_briefing",
                        source_detail="EFTA01660622 FBI case timeline")
        stats["new_edge_sources"] += 1

        # Maxwell investigated by FBI
        maxwell_kg_id = person_kg_ids.get("Ghislaine Maxwell")
        if not maxwell_kg_id:
            kg_cursor.execute(
                "SELECT id FROM entities WHERE name = 'Ghislaine Maxwell' AND entity_type = 'person'"
            )
            row = kg_cursor.fetchone()
            if row:
                maxwell_kg_id = row[0]

        if maxwell_kg_id:
            rel_id, is_new = get_or_create_relationship(
                kg_cursor, maxwell_kg_id, fbi_kg_id, "associated_with",
                weight=8,
                metadata={
                    "source": "ds10_fbi_timeline",
                    "relationship_subtype": "investigation_subject",
                    "detail": "Indicted 6/29/2020, arrested 7/2/2020, convicted 12/30/2021, sentenced to 20 years"
                }
            )
            if is_new:
                stats["new_kg_relationships"] += 1
            add_edge_source(kg_cursor, rel_id, "ds10_fbi_briefing",
                            source_detail="EFTA01660634 Maxwell trial summary")
            stats["new_edge_sources"] += 1

    # 5f. MCC relationships
    print("  5f. MCC/death investigation relationships...")
    mcc_kg_id = org_kg_ids.get("MCC New York")
    if mcc_kg_id:
        # Epstein at MCC
        rel_id, _ = get_or_create_relationship(
            kg_cursor, epstein_kg_id, mcc_kg_id, "associated_with",
            weight=5,
            metadata={
                "source": "ds10_fbi_timeline",
                "relationship_subtype": "detained_at",
                "detail": "Detained at MCC 7/8/2019; suicide 8/10/2019"
            }
        )
        stats["new_kg_relationships"] += 1

        # Michael Thomas and Tova Noel at MCC
        for co_name in ["Michael Thomas", "Tova Noel"]:
            co_kg_id = person_kg_ids.get(co_name)
            if co_kg_id:
                rel_id, is_new = get_or_create_relationship(
                    kg_cursor, co_kg_id, mcc_kg_id, "employed_by",
                    weight=1,
                    metadata={
                        "source": "ds10_fbi_90a_case",
                        "detail": "Corrections officer charged re: Epstein death"
                    }
                )
                if is_new:
                    stats["new_kg_relationships"] += 1

        # Cellmates
        for inmate_name in ["Nicholas Tartaglione", "Efrain Reyes"]:
            inmate_kg_id = person_kg_ids.get(inmate_name)
            if inmate_kg_id:
                rel_id, is_new = get_or_create_relationship(
                    kg_cursor, inmate_kg_id, epstein_kg_id, "associated_with",
                    weight=1,
                    metadata={
                        "source": "ds10_fbi_90a_case",
                        "relationship_subtype": "mcc_cellmate",
                        "detail": f"MCC cellmate of Epstein"
                    }
                )
                if is_new:
                    stats["new_kg_relationships"] += 1

    # 5g. Cross-relationships between prominent names
    print("  5g. Cross-relationships between prominent names...")

    # William Barr <-> Leon Black (co-mentioned in NTOC tip about being present during abuses)
    barr_kg = person_kg_ids.get("William Barr")
    black_kg = person_kg_ids.get("Leon Black")
    if barr_kg and black_kg:
        rel_id, is_new = get_or_create_relationship(
            kg_cursor, barr_kg, black_kg, "associated_with",
            weight=2,
            metadata={
                "source": "ds10_fbi_prominent_names",
                "detail": "Co-named in NTOC tip: both alleged present during abuses (EFTA01660636)"
            }
        )
        if is_new:
            stats["new_kg_relationships"] += 1
            print(f"    [NEW] William Barr <-> Leon Black (co-mentioned in NTOC)")

    # Howard Lutnick property transaction with Epstein
    lutnick_kg = person_kg_ids.get("Howard Lutnick")
    if lutnick_kg:
        rel_id, is_new = get_or_create_relationship(
            kg_cursor, lutnick_kg, epstein_kg_id, "associated_with",
            weight=3,
            metadata={
                "source": "ds10_fbi_prominent_names",
                "relationship_subtype": "financial_property",
                "detail": "Epstein sold Lutnick home for $10, later sold for millions; neighbors"
            }
        )
        if is_new:
            stats["new_kg_relationships"] += 1

    # Dershowitz <-> Bob Shapiro (co-mentioned in NTOC tip)
    dersh_kg = person_kg_ids.get("Alan Dershowitz")
    shapiro_kg = person_kg_ids.get("Bob Shapiro")
    if dersh_kg and shapiro_kg:
        rel_id, is_new = get_or_create_relationship(
            kg_cursor, dersh_kg, shapiro_kg, "associated_with",
            weight=1,
            metadata={
                "source": "ds10_ntoc_tip",
                "detail": "Co-named in unverified NTOC tip as attorneys present at events"
            }
        )
        if is_new:
            stats["new_kg_relationships"] += 1

    # Tom Barrack communication
    barrack_kg = person_kg_ids.get("Tom Barrack")
    if barrack_kg:
        rel_id, is_new = get_or_create_relationship(
            kg_cursor, barrack_kg, epstein_kg_id, "communicated_with",
            weight=2,
            metadata={
                "source": "ds10_email",
                "efta": "EFTA02176329",
                "detail": "Scheduling email for lunch meeting; also reference to H.E. Sheikh"
            }
        )
        if is_new:
            stats["new_kg_relationships"] += 1

    # Ehud Barak meeting
    barak_kg = person_kg_ids.get("Ehud Barak")
    if barak_kg:
        rel_id, is_new = get_or_create_relationship(
            kg_cursor, barak_kg, epstein_kg_id, "communicated_with",
            weight=2,
            metadata={
                "source": "ds10_calendar",
                "efta": "EFTA02154241",
                "detail": "Breakfast meeting scheduled: '8:30am Breakfast w/Ehud Barak' (Wed Nov 28)"
            }
        )
        if is_new:
            stats["new_kg_relationships"] += 1

    # Alfredo Rodriguez employed by Epstein
    rodriguez_kg = person_kg_ids.get("Alfredo Rodriguez")
    if rodriguez_kg:
        rel_id, is_new = get_or_create_relationship(
            kg_cursor, rodriguez_kg, epstein_kg_id, "employed_by",
            weight=3,
            metadata={
                "source": "ds10_fbi_timeline",
                "efta": "EFTA01660623",
                "detail": "Former employee; source of black book; convicted of obstruction 2010"
            }
        )
        if is_new:
            stats["new_kg_relationships"] += 1

    kg_conn.commit()

    # ============================================================
    # STEP 6: Add source extracts and person interactions to evidence.db
    # ============================================================
    print("\n--- Step 6: Adding source extracts and person interactions ---")

    # FBI PROMINENT NAMES document extracts
    pn_doc_id = doc_id_map.get("EFTA01660636")
    if pn_doc_id:
        for name, info in FBI_PROMINENT_NAMES.items():
            ev_id = person_ev_ids.get(name)
            if not ev_id:
                continue

            # Add source extract with allegations
            allegations_text = "; ".join(info.get("allegations", []))
            extract_id = add_source_extract(
                ev_cursor, pn_doc_id,
                f"FBI PROMINENT NAMES - {name}: {allegations_text}",
                extract_type="redaction_bypass",
                confidence=0.90
            )
            stats["new_source_extracts"] += 1

            # Determine interaction type
            if any(kw in allegations_text.lower() for kw in ["massage", "sex", "assault", "rape", "fondl", "trafficking"]):
                interaction_type = "sexual_abuse" if "force" in allegations_text.lower() or "rape" in allegations_text.lower() else "facilitated_abuse"
            elif "financial" in allegations_text.lower() or "money" in allegations_text.lower() or "ponzi" in allegations_text.lower():
                interaction_type = "financial"
            elif "orgy" in allegations_text.lower() or "party" in allegations_text.lower():
                interaction_type = "social"
            else:
                interaction_type = "other"

            # Determine victim contact level
            if any(kw in allegations_text.lower() for kw in ["massage", "force", "rape", "fondl", "sex"]):
                victim_contact = "direct_abuse"
            elif "present" in allegations_text.lower():
                victim_contact = "direct_non_abuse"
            else:
                victim_contact = "unclear"

            add_person_interaction(
                ev_cursor, ev_id, interaction_type,
                f"DS10 FBI PROMINENT NAMES: {info.get('relationship_detail', '')}",
                extract_id,
                info.get("evidence_type", "single_testimony"),
                victim_contact=victim_contact,
                verbatim_quote=allegations_text[:500] if allegations_text else None
            )
            stats["new_person_interactions"] += 1

    # Additional persons interactions
    for name, info in ADDITIONAL_DS10_PERSONS.items():
        ev_id = person_ev_ids.get(name)
        efta = info.get("efta")
        doc_id = doc_id_map.get(efta) if efta else None

        if not ev_id or not doc_id:
            continue

        allegations_text = "; ".join(info.get("allegations", []))
        extract_id = add_source_extract(
            ev_cursor, doc_id,
            f"DS10 - {name}: {allegations_text}",
            extract_type="redaction_bypass",
            confidence=0.85
        )
        stats["new_source_extracts"] += 1

        # Classify interaction type
        if name in ("Ehud Barak", "Tom Barrack"):
            interaction_type = "communication"
        elif name in ("Michael Thomas", "Tova Noel"):
            interaction_type = "employed_by"
        elif name in ("Alfredo Rodriguez",):
            interaction_type = "employed_by"
        elif name in ("Nicholas Tartaglione", "Efrain Reyes"):
            interaction_type = "other"
        else:
            interaction_type = "other"

        add_person_interaction(
            ev_cursor, ev_id, interaction_type,
            f"DS10: {info.get('relationship_detail', '')}",
            extract_id,
            info.get("evidence_type", "circumstantial"),
            victim_contact="none" if interaction_type in ("communication", "employed_by") else "unclear"
        )
        stats["new_person_interactions"] += 1

    ev_conn.commit()

    # ============================================================
    # STEP 7: Update relationship weights based on DS10 co-occurrences
    # ============================================================
    print("\n--- Step 7: Updating relationship weights from DS10 co-occurrence counts ---")

    # Get all person entities in KG
    kg_cursor.execute("""
        SELECT id, name FROM entities WHERE entity_type = 'person'
    """)
    all_kg_persons = kg_cursor.fetchall()

    # Build a mapping of name -> ds10 mention count
    name_mention_counts = {}
    key_names_to_check = set()
    for pid, pname in all_kg_persons:
        # Only check "real" person names (skip noise entries)
        if len(pname) > 3 and not pname.startswith("36") and not pname.startswith("37") \
           and "?" not in pname and "United States" not in pname \
           and "FOIA" not in pname and "PBI" not in pname:
            key_names_to_check.add((pid, pname))

    update_count = 0
    for pid, pname in key_names_to_check:
        count = count_ds10_cooccurrences(ds10_cursor, pname)
        if count > 0:
            name_mention_counts[pname] = count
            # Update the entity metadata with DS10 mention count
            kg_cursor.execute("SELECT metadata FROM entities WHERE id = ?", (pid,))
            row = kg_cursor.fetchone()
            meta = {}
            if row and row[0]:
                try:
                    meta = json.loads(row[0])
                except:
                    pass
            meta["ds10_mention_count"] = count
            kg_cursor.execute(
                "UPDATE entities SET metadata = ? WHERE id = ?",
                (json.dumps(meta), pid)
            )
            update_count += 1

    print(f"  Updated DS10 mention counts for {update_count} entities")

    # Update relationship weights for Epstein connections
    # Find all relationships where one end is Epstein
    kg_cursor.execute("""
        SELECT r.id, r.source_entity_id, r.target_entity_id, r.weight,
               e1.name as source_name, e2.name as target_name
        FROM relationships r
        JOIN entities e1 ON r.source_entity_id = e1.id
        JOIN entities e2 ON r.target_entity_id = e2.id
        WHERE (r.source_entity_id = ? OR r.target_entity_id = ?)
        AND r.relationship_type = 'associated_with'
    """, (epstein_kg_id, epstein_kg_id))

    weight_updates = 0
    for rel_id, src_id, tgt_id, old_weight, src_name, tgt_name in kg_cursor.fetchall():
        other_name = src_name if tgt_id == epstein_kg_id else tgt_name
        ds10_count = name_mention_counts.get(other_name, 0)
        if ds10_count > 0:
            # Add DS10 mentions as additional weight
            new_weight = old_weight + ds10_count
            kg_cursor.execute(
                "UPDATE relationships SET weight = ? WHERE id = ?",
                (new_weight, rel_id)
            )
            weight_updates += 1

    print(f"  Updated weights for {weight_updates} Epstein-associated relationships")

    kg_conn.commit()

    # ============================================================
    # STEP 8: Add FBI case metadata to KG
    # ============================================================
    print("\n--- Step 8: Storing DS10 update metadata ---")

    now = datetime.now(timezone.utc).isoformat()
    kg_cursor.execute("""
        INSERT OR REPLACE INTO kg_metadata (key, value, updated_at)
        VALUES ('ds10_update_timestamp', ?, ?)
    """, (now, now))

    kg_cursor.execute("""
        INSERT OR REPLACE INTO kg_metadata (key, value, updated_at)
        VALUES ('ds10_update_script', 'update_kg_with_ds10.py', ?)
    """, (now,))

    kg_cursor.execute("""
        INSERT OR REPLACE INTO kg_metadata (key, value, updated_at)
        VALUES ('ds10_fbi_case_numbers', ?, ?)
    """, (json.dumps(FBI_CASES), now))

    kg_cursor.execute("""
        INSERT OR REPLACE INTO kg_metadata (key, value, updated_at)
        VALUES ('ds10_key_documents', ?, ?)
    """, (json.dumps([e[0] for e in DS10_KEY_DOCUMENTS]), now))

    for k, v in stats.items():
        kg_cursor.execute("""
            INSERT OR REPLACE INTO kg_metadata (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (f"ds10_stat_{k}", str(v), now))

    # Update total counts
    kg_cursor.execute("SELECT COUNT(*) FROM entities")
    total_entities = kg_cursor.fetchone()[0]
    kg_cursor.execute("SELECT COUNT(*) FROM relationships")
    total_relationships = kg_cursor.fetchone()[0]
    kg_cursor.execute("SELECT COUNT(*) FROM edge_sources")
    total_edge_sources = kg_cursor.fetchone()[0]

    kg_cursor.execute("""
        INSERT OR REPLACE INTO kg_metadata (key, value, updated_at)
        VALUES ('stat_total_entities', ?, ?)
    """, (str(total_entities), now))
    kg_cursor.execute("""
        INSERT OR REPLACE INTO kg_metadata (key, value, updated_at)
        VALUES ('stat_total_relationships', ?, ?)
    """, (str(total_relationships), now))

    kg_conn.commit()
    ev_conn.commit()

    # ============================================================
    # SUMMARY
    # ============================================================
    print("\n" + "=" * 80)
    print("DS10 UPDATE SUMMARY")
    print("=" * 80)

    print(f"\n--- Knowledge Graph Updates ---")
    print(f"  New entities added:          {stats['new_kg_entities']}")
    print(f"  Entities updated:            {stats['updated_kg_entities']}")
    print(f"  New relationships added:     {stats['new_kg_relationships']}")
    print(f"  Relationships updated:       {stats['updated_kg_relationships']}")
    print(f"  New edge sources added:      {stats['new_edge_sources']}")
    print(f"  Total entities now:          {total_entities}")
    print(f"  Total relationships now:     {total_relationships}")
    print(f"  Total edge sources now:      {total_edge_sources}")

    print(f"\n--- Evidence Database Updates ---")
    print(f"  New persons added:           {stats['new_evidence_persons']}")
    print(f"  Persons updated:             {stats['updated_evidence_persons']}")
    print(f"  New organizations added:     {stats['new_evidence_orgs']}")
    print(f"  New source documents added:  {stats['new_source_documents']}")
    print(f"  New source extracts added:   {stats['new_source_extracts']}")
    print(f"  New person interactions:     {stats['new_person_interactions']}")

    print(f"\n--- Key Persons Added/Updated ---")
    key_persons = [
        "Howard Lutnick", "William Barr", "Jes Staley", "Harvey Weinstein",
        "Andrew Cuomo", "Tom Barrack", "Ehud Barak", "Bob Shapiro",
        "Elon Musk", "Donald Trump", "Leon Black", "Les Wexner",
        "Alan Dershowitz", "Bill Clinton", "Prince Andrew", "Glen Dubin",
        "Alfredo Rodriguez", "Nicholas Tartaglione", "Efrain Reyes",
        "Michael Thomas", "Tova Noel", "Simon Andriesz", "Steve Scully"
    ]
    for name in key_persons:
        kg_id = person_kg_ids.get(name)
        ev_id = person_ev_ids.get(name)
        ds10_count = name_mention_counts.get(name, 0)
        print(f"  {name:30s}  KG_ID={str(kg_id):>5s}  EV_ID={str(ev_id):>5s}  DS10_mentions={ds10_count}")

    print(f"\n--- DS10 Top Mentioned Names (in hidden text) ---")
    sorted_mentions = sorted(name_mention_counts.items(), key=lambda x: -x[1])[:30]
    for name, count in sorted_mentions:
        print(f"  {name:40s}: {count:>6d} mentions")

    print(f"\n--- FBI Case Numbers Tracked ---")
    for case_num, desc in FBI_CASES.items():
        print(f"  {case_num:20s}: {desc}")

    print(f"\n--- Trust/Financial Relationships Added ---")
    for name, info in TRUST_BENEFICIARIES.items():
        trusts = ", ".join(info.get("trusts", [])) or "N/A"
        banks = ", ".join(info.get("banks", [])) or "N/A"
        print(f"  {name:25s}: Trusts=[{trusts}]  Banks=[{banks}]")

    print(f"\n--- DS10 Source Documents Registered ---")
    for efta, title, ds_num in DS10_KEY_DOCUMENTS:
        print(f"  {efta}: {title}")

    # Cleanup
    ds10_conn.close()
    ev_conn.close()
    kg_conn.close()

    print(f"\n" + "=" * 80)
    print(f"[DONE] DS10 update complete.")
    print(f"  Knowledge Graph: {KG_DB}")
    print(f"  Evidence DB:     {EVIDENCE_DB}")
    print(f"  DS10 Source:     {DS10_DB}")
    print("=" * 80)


if __name__ == "__main__":
    main()
