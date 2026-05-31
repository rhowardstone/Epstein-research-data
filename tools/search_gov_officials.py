#!/usr/bin/env python3
"""Search full_text_corpus.db for all government officials from CSV + executive branch."""

import csv
import json
import sqlite3
import sys
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
CSV_PATH = os.path.join(_DATA_DIR, "gov-officials-2-12-26.csv")
OUTPUT_PATH = os.path.join(_DATA_DIR, "gov_officials_search_results.json")

# Executive branch officials to search (Trump 2025 admin)
TRUMP_ADMIN = [
    {"name": "Pete Hegseth", "role": "Secretary of Defense", "party": "Republican"},
    {"name": "Kash Patel", "role": "FBI Director", "party": "Republican"},
    {"name": "Tulsi Gabbard", "role": "Director of National Intelligence", "party": "Republican"},
    {"name": "Russell Vought", "role": "OMB Director", "party": "Republican"},
    {"name": "Robert F. Kennedy Jr.", "role": "HHS Secretary", "party": "Republican"},
    {"name": "Robert Kennedy", "role": "HHS Secretary (alt search)", "party": "Republican"},
    {"name": "Elon Musk", "role": "DOGE", "party": "Republican"},
    {"name": "Vivek Ramaswamy", "role": "DOGE", "party": "Republican"},
    {"name": "Matt Gaetz", "role": "AG Nominee (withdrawn)", "party": "Republican"},
    {"name": "Tom Homan", "role": "Border Czar", "party": "Republican"},
    {"name": "Stephen Miller", "role": "Senior Advisor", "party": "Republican"},
    {"name": "John Ratcliffe", "role": "CIA Director", "party": "Republican"},
    {"name": "Mike Waltz", "role": "National Security Advisor", "party": "Republican"},
    {"name": "Linda McMahon", "role": "Education Secretary", "party": "Republican"},
    {"name": "Scott Bessent", "role": "Treasury Secretary", "party": "Republican"},
    {"name": "Lee Zeldin", "role": "EPA Administrator", "party": "Republican"},
    {"name": "Jim Jordan", "role": "House Judiciary Chairman", "party": "Republican"},
    {"name": "Andy Biggs", "role": "Former Rep. (AZ)", "party": "Republican"},
    {"name": "Daniel Feinberg", "role": "DOJ Official", "party": "Republican"},
    {"name": "James Mattis", "role": "Former SecDef", "party": "Republican"},
    {"name": "Marco Rubio", "role": "Secretary of State", "party": "Republican"},
    {"name": "Pam Bondi", "role": "Attorney General", "party": "Republican"},
    {"name": "Doug Burgum", "role": "Interior Secretary", "party": "Republican"},
    {"name": "Sean Duffy", "role": "Transportation Secretary", "party": "Republican"},
    {"name": "Chris Wright", "role": "Energy Secretary", "party": "Republican"},
    {"name": "Brooke Rollins", "role": "Agriculture Secretary", "party": "Republican"},
    {"name": "Howard Lutnick", "role": "Commerce Secretary", "party": "Republican"},
    {"name": "Lori Chavez-DeRemer", "role": "Labor Secretary", "party": "Republican"},
    {"name": "Doug Collins", "role": "VA Secretary", "party": "Republican"},
    {"name": "Kristi Noem", "role": "DHS Secretary", "party": "Republican"},
    {"name": "Pete Ricketts", "role": "Senator (NE)", "party": "Republican"},
    {"name": "J.D. Vance", "role": "Vice President", "party": "Republican"},
    {"name": "JD Vance", "role": "Vice President (alt)", "party": "Republican"},
    {"name": "Donald Trump", "role": "President", "party": "Republican"},
]

# Biden-era key officials
BIDEN_ADMIN = [
    {"name": "Joe Biden", "role": "Former President", "party": "Democratic"},
    {"name": "Kamala Harris", "role": "Former Vice President", "party": "Democratic"},
    {"name": "Merrick Garland", "role": "Former AG", "party": "Democratic"},
    {"name": "Antony Blinken", "role": "Former Secretary of State", "party": "Democratic"},
    {"name": "Lloyd Austin", "role": "Former SecDef", "party": "Democratic"},
    {"name": "Janet Yellen", "role": "Former Treasury", "party": "Democratic"},
    {"name": "Xavier Becerra", "role": "Former HHS", "party": "Democratic"},
    {"name": "Pete Buttigieg", "role": "Former Transportation", "party": "Democratic"},
    {"name": "Gina Raimondo", "role": "Former Commerce", "party": "Democratic"},
    {"name": "Deb Haaland", "role": "Former Interior", "party": "Democratic"},
    {"name": "Tom Vilsack", "role": "Former Agriculture", "party": "Democratic"},
    {"name": "Denis McDonough", "role": "Former VA", "party": "Democratic"},
    {"name": "Alejandro Mayorkas", "role": "Former DHS", "party": "Democratic"},
    {"name": "Jennifer Granholm", "role": "Former Energy", "party": "Democratic"},
    {"name": "Miguel Cardona", "role": "Former Education", "party": "Democratic"},
    {"name": "Marty Walsh", "role": "Former Labor", "party": "Democratic"},
    {"name": "Christopher Wray", "role": "Former FBI Director", "party": "Republican"},
    {"name": "Avril Haines", "role": "Former DNI", "party": "Democratic"},
    {"name": "William Burns", "role": "Former CIA Director", "party": "Democratic"},
    {"name": "Jake Sullivan", "role": "Former NSA", "party": "Democratic"},
    {"name": "Ron Klain", "role": "Former Chief of Staff", "party": "Democratic"},
    {"name": "Jeff Zients", "role": "Former Chief of Staff", "party": "Democratic"},
    {"name": "Lisa Monaco", "role": "Former Deputy AG", "party": "Democratic"},
    {"name": "Vanita Gupta", "role": "Former Associate AG", "party": "Democratic"},
    # Obama-era officials still relevant
    {"name": "Barack Obama", "role": "Former President", "party": "Democratic"},
    {"name": "Hillary Clinton", "role": "Former Sec. State", "party": "Democratic"},
    {"name": "Bill Clinton", "role": "Former President", "party": "Democratic"},
    {"name": "Eric Holder", "role": "Former AG", "party": "Democratic"},
    {"name": "Loretta Lynch", "role": "Former AG", "party": "Democratic"},
    {"name": "James Comey", "role": "Former FBI Director", "party": "Republican"},
    {"name": "Robert Mueller", "role": "Former FBI Director", "party": "Republican"},
    {"name": "John Kerry", "role": "Former Sec. State", "party": "Democratic"},
    {"name": "Susan Rice", "role": "Former NSA", "party": "Democratic"},
    {"name": "Samantha Power", "role": "Former USAID", "party": "Democratic"},
    # Key former officials referenced in documents
    {"name": "Alexander Acosta", "role": "Former Labor Secretary", "party": "Republican"},
    {"name": "William Barr", "role": "Former AG", "party": "Republican"},
    {"name": "Jeff Sessions", "role": "Former AG", "party": "Republican"},
    {"name": "Rex Tillerson", "role": "Former Sec. State", "party": "Republican"},
    {"name": "Mike Pompeo", "role": "Former Sec. State/CIA", "party": "Republican"},
    {"name": "Jared Kushner", "role": "Former Senior Advisor", "party": "Republican"},
    {"name": "Ivanka Trump", "role": "Former Senior Advisor", "party": "Republican"},
    {"name": "Steve Bannon", "role": "Former Chief Strategist", "party": "Republican"},
    {"name": "John Bolton", "role": "Former NSA", "party": "Republican"},
]

def search_name(cursor, name, search_terms=None):
    """Search for a name in the FTS5 corpus. Returns doc count and sample EFTAs."""
    if search_terms is None:
        # Split name into parts for flexible searching
        parts = name.replace(".", "").replace(",", "").split()
        # Try quoted full name first
        search_terms = [f'"{name}"']
        # Also try last name alone for context
        if len(parts) >= 2:
            last = parts[-1]
            if last not in ("Jr", "Jr.", "III", "II", "IV"):
                search_terms.append(f'"{parts[0]} {last}"')
            else:
                # Handle suffixes
                if len(parts) >= 3:
                    search_terms.append(f'"{parts[0]} {parts[-2]}"')

    results = {"name": name, "doc_count": 0, "page_count": 0, "sample_eftas": [], "search_queries": []}

    for term in search_terms[:1]:  # Just use primary search term for count
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
                # Get sample EFTAs with snippets
                cursor.execute(
                    """SELECT DISTINCT efta_number, page_number,
                       snippet(pages_fts, 2, '>>>', '<<<', '...', 40) as snip
                       FROM pages_fts WHERE text_content MATCH ?
                       ORDER BY efta_number LIMIT 5""",
                    (term,)
                )
                samples = cursor.fetchall()
                results["doc_count"] = doc_count
                results["page_count"] = page_count
                results["search_queries"].append(term)
                results["sample_eftas"] = [
                    {"efta": r[0], "page": r[1], "snippet": r[2]} for r in samples
                ]
        except Exception as e:
            results["error"] = str(e)

    return results


def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check FTS5 table exists
    cursor.execute("SELECT COUNT(*) FROM pages LIMIT 1")
    print(f"Database connected. Total pages: {cursor.fetchone()[0]}")

    all_results = {
        "democratic_senate": [],
        "democratic_house": [],
        "republican_senate": [],
        "republican_house": [],
        "independent_senate": [],
        "trump_admin": [],
        "biden_admin": [],
    }

    # Read CSV
    officials = []
    with open(CSV_PATH, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            officials.append(row)

    print(f"CSV loaded: {len(officials)} officials")

    # Search CSV officials
    for i, official in enumerate(officials):
        name = official['Name'].strip()
        party = official['Party'].strip()
        office = official['Office'].strip()

        result = search_name(cursor, name)
        result["office"] = office
        result["party"] = party

        if 'Senate' in office:
            if party == 'Democratic':
                all_results["democratic_senate"].append(result)
            elif party == 'Republican':
                all_results["republican_senate"].append(result)
            else:
                all_results["independent_senate"].append(result)
        else:
            if party == 'Democratic':
                all_results["democratic_house"].append(result)
            else:
                all_results["republican_house"].append(result)

        if (i + 1) % 50 == 0:
            print(f"  Searched {i+1}/{len(officials)} CSV officials...")

    print(f"CSV search complete. Searching executive branch...")

    # Search Trump admin
    for official in TRUMP_ADMIN:
        result = search_name(cursor, official["name"])
        result["role"] = official["role"]
        result["party"] = official["party"]
        all_results["trump_admin"].append(result)

    # Search Biden admin
    for official in BIDEN_ADMIN:
        result = search_name(cursor, official["name"])
        result["role"] = official["role"]
        result["party"] = official["party"]
        all_results["biden_admin"].append(result)

    conn.close()

    # Write results
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(all_results, f, indent=2)

    # Print summary
    print(f"\n=== SEARCH SUMMARY ===")
    for cat, results in all_results.items():
        hits = [r for r in results if r["doc_count"] > 0]
        print(f"{cat}: {len(hits)}/{len(results)} officials found in corpus")
        for h in sorted(hits, key=lambda x: -x["doc_count"])[:10]:
            print(f"  {h['name']}: {h['doc_count']} docs, {h['page_count']} pages")

    print(f"\nResults written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
