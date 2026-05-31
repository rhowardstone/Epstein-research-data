#!/usr/bin/env python3
"""
Congressional Reading Room Priority Scorer

Scores EFTA documents by "Predator Name Reveal" potential:
  REVEAL_SCORE = estimated_redacted_names × crime_severity × novelty_factor

Layers:
  1. Automated DB scoring (crime keywords + redaction density)
  2. Report enrichment (manual EFTA context from investigation reports)
  3. Final merged ranking

Databases:
  - full_text_corpus.db: 1.38M docs, FTS5 searchable
  - redaction_analysis_v2.db: 2.58M redactions, extracted_entities

Output: congressional_scored_eftas.json
"""

import sqlite3
import json
import re
import os
import sys
from collections import defaultdict
from pathlib import Path

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

BASE_DIR = Path(_find_data_dir())
CORPUS_DB = BASE_DIR / "full_text_corpus.db"
REDACTION_DB = BASE_DIR / "redaction_analysis_v2.db"
ENRICHMENT_FILE = BASE_DIR / "efta_enrichment.json"
OUTPUT_FILE = BASE_DIR / "congressional_scored_eftas.json"

# Crime keywords grouped by severity
CRIME_KEYWORDS_SEVERE = [
    "rape", "raped", "raping",
    "CSAM", "child sexual abuse",
    "child trafficking", "sex trafficking",
    "minor", "minors", "underage",
    "molest", "molested", "molestation",
    "14-year-old", "15-year-old", "13-year-old", "12-year-old",
    "fourteen", "fifteen", "thirteen", "twelve",
]

CRIME_KEYWORDS_MODERATE = [
    "assault", "assaulted",
    "trafficking", "trafficked",
    "prostitut",  # matches prostitution, prostitute, etc.
    "forced", "coerced",
    "abuse", "abused", "abusive",
    "victim", "victims",
]

CRIME_KEYWORDS_FINANCIAL = [
    "wire transfer", "shell company", "money laundering",
    "compliance failure", "KYC", "AML",
    "obstruction", "witness tampering",
    "settlement", "NPA", "non-prosecution",
]

# Name-proximity patterns: redactions near these phrases suggest redacted person names
NAME_PROXIMITY_PATTERNS = [
    r"Mr\.\s*\[",
    r"Ms\.\s*\[",
    r"Dr\.\s*\[",
    r"trafficked\s+to\b",
    r"sexual\s+contact\s+with\b",
    r"identified\s+as\b",
    r"raped\s+by\b",
    r"assaulted\s+by\b",
    r"perpetrator",
    r"co-conspirator",
    r"alleged\s+abuser",
    r"accused",
    r"subject\s+of",
    r"person\s+of\s+interest",
    r"prominent\s+name",
    r"redacted\s+name",
    r"unnamed",
    r"\bVIP\b",
    r"powerful\s+",
    r"foreign\s+president",
    r"prime\s+minister",
    r"senator",
    r"governor",
    r"billionaire",
]


def get_crime_pages(corpus_conn):
    """Layer 1A: Find pages with crime keywords using FTS5."""
    print("  [Layer 1A] Searching for crime-keyword pages via FTS5...")

    # Build FTS5 query for all crime keywords
    fts_terms = (
        "rape OR raped OR assault OR assaulted OR trafficking OR trafficked "
        "OR minor OR minors OR underage OR molest OR molested OR victim OR victims "
        "OR prostitut OR abuse OR abused OR forced OR coerced "
        "OR CSAM"
    )

    cursor = corpus_conn.execute("""
        SELECT efta_number, page_number, text_content
        FROM pages_fts
        WHERE text_content MATCH ?
    """, (fts_terms,))

    crime_pages = {}
    count = 0
    for row in cursor:
        efta = row[0]
        page = row[1]
        text = row[2] or ""
        key = (efta, page)

        # Score the text
        text_lower = text.lower()
        severe_score = sum(1 for kw in CRIME_KEYWORDS_SEVERE if kw.lower() in text_lower)
        moderate_score = sum(1 for kw in CRIME_KEYWORDS_MODERATE if kw.lower() in text_lower)
        financial_score = sum(1 for kw in CRIME_KEYWORDS_FINANCIAL if kw.lower() in text_lower)

        # Check for name-proximity patterns
        name_proximity_hits = sum(
            1 for pat in NAME_PROXIMITY_PATTERNS
            if re.search(pat, text, re.IGNORECASE)
        )

        crime_pages[key] = {
            "efta_number": efta,
            "page_number": page,
            "severe_score": severe_score,
            "moderate_score": moderate_score,
            "financial_score": financial_score,
            "name_proximity": name_proximity_hits,
            "char_count": len(text),
        }
        count += 1

    print(f"    Found {count} crime-keyword pages across {len(set(k[0] for k in crime_pages))} documents")
    return crime_pages


def get_redaction_density(redaction_conn):
    """Layer 1A continued: Get redaction counts per page."""
    print("  [Layer 1A] Getting redaction density per page...")

    cursor = redaction_conn.execute("""
        SELECT efta_number, page_number, COUNT(*) as redaction_count
        FROM redactions
        GROUP BY efta_number, page_number
    """)

    redaction_counts = {}
    for row in cursor:
        redaction_counts[(row[0], row[1])] = row[2]

    print(f"    Got redaction counts for {len(redaction_counts)} page entries")
    return redaction_counts


def get_name_entity_density(redaction_conn):
    """Layer 1B: Get name-type entity counts per document from extracted_entities."""
    print("  [Layer 1B] Getting name entity density per document...")

    cursor = redaction_conn.execute("""
        SELECT efta_number, COUNT(*) as name_count
        FROM extracted_entities
        WHERE entity_type = 'name'
        GROUP BY efta_number
        ORDER BY name_count DESC
    """)

    name_counts = {}
    for row in cursor:
        name_counts[row[0]] = row[1]

    print(f"    Got name entity counts for {len(name_counts)} documents")
    return name_counts


def get_document_info(corpus_conn):
    """Get total pages and file size for each document."""
    print("  Getting document metadata...")

    cursor = corpus_conn.execute("""
        SELECT efta_number, total_pages, file_size FROM documents
    """)

    doc_info = {}
    for row in cursor:
        doc_info[row[0]] = {"total_pages": row[1], "file_size": row[2]}

    return doc_info


def compute_automated_scores(crime_pages, redaction_counts, name_entity_counts, doc_info):
    """Layer 1C: Aggregate page-level scores to document-level scores."""
    print("  [Layer 1C] Computing document-level automated scores...")

    doc_scores = defaultdict(lambda: {
        "crime_pages": 0,
        "total_severe": 0,
        "total_moderate": 0,
        "total_financial": 0,
        "total_redactions_on_crime_pages": 0,
        "total_name_proximity": 0,
        "max_page_score": 0,
        "name_entities": 0,
        "total_pages": 0,
        "pages_detail": [],
    })

    for (efta, page), scores in crime_pages.items():
        doc = doc_scores[efta]
        doc["crime_pages"] += 1
        doc["total_severe"] += scores["severe_score"]
        doc["total_moderate"] += scores["moderate_score"]
        doc["total_financial"] += scores["financial_score"]
        doc["total_name_proximity"] += scores["name_proximity"]

        # Get redaction count for this page
        redaction_count = redaction_counts.get((efta, page), 0)
        doc["total_redactions_on_crime_pages"] += redaction_count

        # Page-level score
        page_score = (
            scores["severe_score"] * 3 +
            scores["moderate_score"] * 2 +
            scores["financial_score"] * 1 +
            scores["name_proximity"] * 2 +
            min(redaction_count, 20) * 0.5  # cap redaction contribution
        )
        doc["max_page_score"] = max(doc["max_page_score"], page_score)

        doc["pages_detail"].append({
            "page": page,
            "severe": scores["severe_score"],
            "moderate": scores["moderate_score"],
            "redactions": redaction_count,
            "name_proximity": scores["name_proximity"],
            "page_score": page_score,
        })

    # Add name entity counts and doc info
    for efta in doc_scores:
        doc_scores[efta]["name_entities"] = name_entity_counts.get(efta, 0)
        info = doc_info.get(efta, {})
        doc_scores[efta]["total_pages"] = info.get("total_pages", 0)

    # Compute automated reveal score
    for efta, doc in doc_scores.items():
        # Estimated redacted names: use name entities (capped) + name proximity signals
        # Note: name entities are mostly garbled OCR, so use conservatively
        est_names = min(doc["name_entities"], 5) * 0.3 + doc["total_name_proximity"] * 0.5

        # Crime severity score
        if doc["total_severe"] > 0:
            crime_sev = 3
        elif doc["total_moderate"] > 0:
            crime_sev = 2
        else:
            crime_sev = 1

        # Redaction density bonus
        redaction_density = min(doc["total_redactions_on_crime_pages"], 50) / 10

        # Automated score (will be merged with enrichment data)
        doc["auto_score"] = (
            (est_names + 1) *  # +1 floor so docs with crime but no detected names still score
            crime_sev *
            (1 + redaction_density * 0.3) *
            min(doc["crime_pages"], 10)  # cap crime pages contribution
        )
        doc["estimated_names_auto"] = est_names
        doc["crime_severity_auto"] = crime_sev

    print(f"    Computed scores for {len(doc_scores)} documents")
    return dict(doc_scores)


def load_enrichment():
    """Layer 2: Load manual enrichment from report parsing."""
    if not ENRICHMENT_FILE.exists():
        print("  [Layer 2] No enrichment file found, skipping manual enrichment")
        return {}

    print("  [Layer 2] Loading enrichment data...")
    with open(ENRICHMENT_FILE) as f:
        enrichment = json.load(f)

    print(f"    Loaded enrichment for {len(enrichment)} EFTAs")
    return enrichment


def merge_scores(auto_scores, enrichment):
    """Layer 3: Merge automated + manual scores into final REVEAL_SCORE."""
    print("  [Layer 3] Merging scores...")

    all_eftas = set(auto_scores.keys()) | set(enrichment.keys())
    final_scores = {}

    for efta in all_eftas:
        auto = auto_scores.get(efta, {})
        enrich = enrichment.get(efta, {})

        # Use enrichment values when available, fall back to automated
        est_names = enrich.get("estimated_redacted_names",
                               auto.get("estimated_names_auto", 0))
        crime_severity = enrich.get("crime_severity",
                                     auto.get("crime_severity_auto", 1))
        novelty = enrich.get("novelty_factor", 1.0)
        has_redacted = enrich.get("has_redacted_names", est_names > 0)

        # Compute REVEAL_SCORE
        reveal_score = est_names * crime_severity * novelty

        # Boost from automated analysis
        auto_boost = auto.get("auto_score", 0) * 0.1  # 10% of auto score as boost
        reveal_score += auto_boost

        # Extra boost for documents with both high crime severity AND high redaction density
        if auto.get("total_redactions_on_crime_pages", 0) > 10 and crime_severity >= 2:
            reveal_score *= 1.2

        final_scores[efta] = {
            "efta_number": efta,
            "reveal_score": round(reveal_score, 2),
            "estimated_redacted_names": est_names,
            "crime_severity": crime_severity,
            "novelty_factor": novelty,
            "has_redacted_names": has_redacted,
            "description": enrich.get("description", ""),
            "crimes": enrich.get("crimes", []),
            "context": enrich.get("context", ""),
            "source_reports": enrich.get("source_reports", []),
            # Automated metrics
            "crime_pages": auto.get("crime_pages", 0),
            "total_severe_keywords": auto.get("total_severe", 0),
            "total_moderate_keywords": auto.get("total_moderate", 0),
            "redactions_on_crime_pages": auto.get("total_redactions_on_crime_pages", 0),
            "name_proximity_signals": auto.get("total_name_proximity", 0),
            "name_entities": auto.get("name_entities", 0),
            "total_pages": auto.get("total_pages", 0),
            "auto_score": round(auto.get("auto_score", 0), 2),
            "max_page_score": round(auto.get("max_page_score", 0), 2),
        }

    # Sort by reveal_score descending
    sorted_scores = dict(sorted(
        final_scores.items(),
        key=lambda x: x[1]["reveal_score"],
        reverse=True
    ))

    print(f"    Final scored list: {len(sorted_scores)} documents")
    return sorted_scores


def print_top_n(scores, n=25):
    """Print top N documents."""
    items = list(scores.items())[:n]
    print(f"\n{'='*80}")
    print(f"TOP {n} DOCUMENTS BY REVEAL SCORE")
    print(f"{'='*80}")
    print(f"{'Rank':<5} {'EFTA':<16} {'Score':<8} {'Names':<6} {'Sev':<4} {'Nov':<4} {'CrPg':<5} {'Rdct':<5} Description")
    print(f"{'-'*5} {'-'*16} {'-'*8} {'-'*6} {'-'*4} {'-'*4} {'-'*5} {'-'*5} {'-'*40}")

    for i, (efta, data) in enumerate(items, 1):
        desc = data.get("description", "")[:50]
        print(
            f"{i:<5} {efta:<16} {data['reveal_score']:<8.1f} "
            f"{data['estimated_redacted_names']:<6} {data['crime_severity']:<4} "
            f"{data['novelty_factor']:<4} {data['crime_pages']:<5} "
            f"{data['redactions_on_crime_pages']:<5} {desc}"
        )


def main():
    print("=" * 60)
    print("Congressional Reading Room Priority Scorer")
    print("=" * 60)

    # Connect to databases
    print("\nConnecting to databases...")
    corpus_conn = sqlite3.connect(str(CORPUS_DB))
    redaction_conn = sqlite3.connect(str(REDACTION_DB))

    # Layer 1: Automated scoring
    print("\n[LAYER 1] Automated Database Scoring")
    crime_pages = get_crime_pages(corpus_conn)
    redaction_counts = get_redaction_density(redaction_conn)
    name_entity_counts = get_name_entity_density(redaction_conn)
    doc_info = get_document_info(corpus_conn)
    auto_scores = compute_automated_scores(crime_pages, redaction_counts, name_entity_counts, doc_info)

    # Layer 2: Report enrichment
    print("\n[LAYER 2] Report Enrichment")
    enrichment = load_enrichment()

    # Layer 3: Merge
    print("\n[LAYER 3] Final Score Merge")
    final_scores = merge_scores(auto_scores, enrichment)

    # Output
    print(f"\nWriting results to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w") as f:
        json.dump(final_scores, f, indent=2)

    print_top_n(final_scores, 30)

    # Summary stats
    scored = [v for v in final_scores.values() if v["reveal_score"] > 0]
    enriched = [v for v in final_scores.values() if v.get("description")]
    high = [v for v in scored if v["reveal_score"] >= 20]
    medium = [v for v in scored if 5 <= v["reveal_score"] < 20]

    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"  Total documents scored: {len(final_scores)}")
    print(f"  With enrichment data:   {len(enriched)}")
    print(f"  With reveal score > 0:  {len(scored)}")
    print(f"  HIGH priority (>=20):   {len(high)}")
    print(f"  MEDIUM priority (5-20): {len(medium)}")

    corpus_conn.close()
    redaction_conn.close()

    return final_scores


if __name__ == "__main__":
    main()
