#!/usr/bin/env python3
"""Search full_text_corpus.db for all judicial branch officials."""

import json
import sqlite3
import time

import os

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
OUTPUT_PATH = os.path.join(_DATA_DIR, "judicial_search_results.json")

# Current SCOTUS (2025-2026)
SCOTUS_CURRENT = [
    {"name": "John Roberts", "role": "Chief Justice", "appointed_by": "George W. Bush", "year": 2005},
    {"name": "Clarence Thomas", "role": "Associate Justice", "appointed_by": "George H.W. Bush", "year": 1991},
    {"name": "Samuel Alito", "role": "Associate Justice", "appointed_by": "George W. Bush", "year": 2006},
    {"name": "Sonia Sotomayor", "role": "Associate Justice", "appointed_by": "Barack Obama", "year": 2009},
    {"name": "Elena Kagan", "role": "Associate Justice", "appointed_by": "Barack Obama", "year": 2010},
    {"name": "Neil Gorsuch", "role": "Associate Justice", "appointed_by": "Donald Trump", "year": 2017},
    {"name": "Brett Kavanaugh", "role": "Associate Justice", "appointed_by": "Donald Trump", "year": 2018},
    {"name": "Amy Coney Barrett", "role": "Associate Justice", "appointed_by": "Donald Trump", "year": 2020},
    {"name": "Ketanji Brown Jackson", "role": "Associate Justice", "appointed_by": "Joe Biden", "year": 2022},
]

# Recent former SCOTUS
SCOTUS_FORMER = [
    {"name": "Ruth Bader Ginsburg", "role": "Former Associate Justice (died 2020)", "appointed_by": "Bill Clinton", "year": 1993},
    {"name": "Anthony Kennedy", "role": "Former Associate Justice (retired 2018)", "appointed_by": "Ronald Reagan", "year": 1988},
    {"name": "Stephen Breyer", "role": "Former Associate Justice (retired 2022)", "appointed_by": "Bill Clinton", "year": 1994},
    {"name": "Antonin Scalia", "role": "Former Associate Justice (died 2016)", "appointed_by": "Ronald Reagan", "year": 1986},
    {"name": "David Souter", "role": "Former Associate Justice (retired 2009)", "appointed_by": "George H.W. Bush", "year": 1990},
    {"name": "Sandra Day O'Connor", "role": "Former Associate Justice (died 2023)", "appointed_by": "Ronald Reagan", "year": 1981},
    {"name": "John Paul Stevens", "role": "Former Associate Justice (died 2019)", "appointed_by": "Gerald Ford", "year": 1975},
]

# Judges who handled Epstein/Maxwell cases
EPSTEIN_CASE_JUDGES = [
    {"name": "Kenneth Marra", "role": "SDFL Judge — CVRA ruling on Epstein NPA", "court": "S.D. Florida"},
    {"name": "Richard Berman", "role": "SDNY Judge — Epstein 2019 case, denied bail", "court": "S.D. New York"},
    {"name": "Alison Nathan", "role": "SDNY Judge — Maxwell trial", "court": "S.D. New York / 2nd Circuit"},
    {"name": "Loretta Preska", "role": "SDNY Judge — unsealed Epstein documents", "court": "S.D. New York"},
    {"name": "Robert Sweet", "role": "SDNY Judge — Giuffre v. Maxwell (died 2019)", "court": "S.D. New York"},
    {"name": "Denise Cote", "role": "SDNY Judge — related proceedings", "court": "S.D. New York"},
    {"name": "Deborah Batts", "role": "SDNY Judge — early Epstein matters (died 2020)", "court": "S.D. New York"},
]

# Key Circuit Court chief judges and notable appellate judges
CIRCUIT_JUDGES = [
    # 1st Circuit (Boston)
    {"name": "David Barron", "role": "Chief Judge, 1st Circuit", "court": "1st Circuit"},
    # 2nd Circuit (New York) — covers SDNY
    {"name": "Debra Ann Livingston", "role": "Chief Judge, 2nd Circuit", "court": "2nd Circuit"},
    {"name": "Denny Chin", "role": "2nd Circuit (former SDNY judge in Madoff case)", "court": "2nd Circuit"},
    {"name": "Robert Katzmann", "role": "Former Chief Judge, 2nd Circuit (died 2021)", "court": "2nd Circuit"},
    # 3rd Circuit (Philadelphia)
    {"name": "Michael Chagares", "role": "Chief Judge, 3rd Circuit", "court": "3rd Circuit"},
    # 4th Circuit (Richmond)
    {"name": "Albert Diaz", "role": "Chief Judge, 4th Circuit", "court": "4th Circuit"},
    # 5th Circuit (New Orleans)
    {"name": "Priscilla Richman", "role": "Chief Judge, 5th Circuit", "court": "5th Circuit"},
    # 6th Circuit (Cincinnati)
    {"name": "Jeffrey Sutton", "role": "Chief Judge, 6th Circuit", "court": "6th Circuit"},
    # 7th Circuit (Chicago)
    {"name": "Diane Sykes", "role": "Chief Judge, 7th Circuit", "court": "7th Circuit"},
    # 8th Circuit (St. Louis)
    {"name": "Steven Colloton", "role": "Chief Judge, 8th Circuit", "court": "8th Circuit"},
    # 9th Circuit (San Francisco)
    {"name": "Mary Murguia", "role": "Chief Judge, 9th Circuit", "court": "9th Circuit"},
    # 10th Circuit (Denver)
    {"name": "Jerome Holmes", "role": "Chief Judge, 10th Circuit", "court": "10th Circuit"},
    # 11th Circuit (Atlanta) — covers S.D. Florida
    {"name": "William Pryor", "role": "Chief Judge, 11th Circuit", "court": "11th Circuit"},
    # D.C. Circuit
    {"name": "Sri Srinivasan", "role": "Chief Judge, D.C. Circuit", "court": "D.C. Circuit"},
    {"name": "Merrick Garland", "role": "Former D.C. Circuit (now AG, already covered)", "court": "D.C. Circuit"},
    # Federal Circuit
    {"name": "Kimberly Moore", "role": "Chief Judge, Federal Circuit", "court": "Federal Circuit"},
    # Notable 2nd Circuit judges (Maxwell appeal)
    {"name": "Jose Cabranes", "role": "2nd Circuit Senior Judge", "court": "2nd Circuit"},
]

# Other notable federal judges and judicial figures
OTHER_JUDICIAL = [
    {"name": "Alan Dershowitz", "role": "Harvard Law professor, Epstein defense attorney", "court": "N/A — attorney"},
    {"name": "Alexander Acosta", "role": "Former U.S. Attorney SDFL (already covered in exec)", "court": "S.D. Florida"},
    {"name": "Geoffrey Berman", "role": "Former U.S. Attorney SDNY (Epstein arrest)", "court": "S.D. New York"},
    {"name": "Audrey Strauss", "role": "Acting U.S. Attorney SDNY (Maxwell arrest)", "court": "S.D. New York"},
    {"name": "Maurene Comey", "role": "SDNY AUSA — Maxwell prosecution team", "court": "S.D. New York"},
    {"name": "Damian Williams", "role": "U.S. Attorney SDNY (2021-2024)", "court": "S.D. New York"},
    {"name": "Barry Krischer", "role": "Former Palm Beach State Attorney", "court": "Palm Beach County"},
    {"name": "Jack Smith", "role": "Former Special Counsel (Trump cases)", "court": "DOJ"},
    # FISA Court
    {"name": "James Boasberg", "role": "Chief Judge, FISA Court / D.C. District", "court": "FISC / D.D.C."},
]


def search_name(cursor, name):
    """Search for a name in the FTS5 corpus."""
    parts = name.replace(".", "").replace(",", "").split()
    term = f'"{name}"'

    result = {"name": name, "doc_count": 0, "page_count": 0, "sample_eftas": [], "search_query": term}

    try:
        cursor.execute(
            "SELECT COUNT(DISTINCT efta_number) FROM pages_fts WHERE text_content MATCH ?",
            (term,)
        )
        doc_count = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM pages_fts WHERE text_content MATCH ?",
            (term,)
        )
        page_count = cursor.fetchone()[0]

        if doc_count > 0:
            cursor.execute(
                """SELECT DISTINCT efta_number, page_number,
                   snippet(pages_fts, 2, '>>>', '<<<', '...', 40) as snip
                   FROM pages_fts WHERE text_content MATCH ?
                   ORDER BY efta_number LIMIT 5""",
                (term,)
            )
            samples = cursor.fetchall()
            result["doc_count"] = doc_count
            result["page_count"] = page_count
            result["sample_eftas"] = [
                {"efta": r[0], "page": r[1], "snippet": r[2]} for r in samples
            ]
    except Exception as e:
        result["error"] = str(e)

    return result


def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM pages LIMIT 1")
    print(f"Database connected. Total pages: {cursor.fetchone()[0]}")

    all_results = {
        "scotus_current": [],
        "scotus_former": [],
        "epstein_case_judges": [],
        "circuit_judges": [],
        "other_judicial": [],
    }

    # Search each category
    for category, officials in [
        ("scotus_current", SCOTUS_CURRENT),
        ("scotus_former", SCOTUS_FORMER),
        ("epstein_case_judges", EPSTEIN_CASE_JUDGES),
        ("circuit_judges", CIRCUIT_JUDGES),
        ("other_judicial", OTHER_JUDICIAL),
    ]:
        print(f"\nSearching {category} ({len(officials)} names)...")
        for official in officials:
            result = search_name(cursor, official["name"])
            result["role"] = official.get("role", "")
            result["court"] = official.get("court", "")
            result["appointed_by"] = official.get("appointed_by", "")
            result["year"] = official.get("year", "")
            all_results[category].append(result)
            if result["doc_count"] > 0:
                print(f"  {official['name']:30s} {result['doc_count']:5d} docs  {result['page_count']:6d} pages")

    conn.close()

    with open(OUTPUT_PATH, 'w') as f:
        json.dump(all_results, f, indent=2)

    # Summary
    print(f"\n=== SEARCH SUMMARY ===")
    for cat, results in all_results.items():
        hits = [r for r in results if r["doc_count"] > 0]
        print(f"\n{cat}: {len(hits)}/{len(results)} found")
        for h in sorted(hits, key=lambda x: -x["doc_count"]):
            print(f"  {h['name']:30s} {h['doc_count']:5d} docs  {h['page_count']:6d} pages  {h['role']}")

    print(f"\nResults written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
