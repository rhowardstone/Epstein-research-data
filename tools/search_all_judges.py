#!/usr/bin/env python3
"""Search full_text_corpus.db for ALL Article III federal judges from FJC database."""

import csv
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
FJC_CSV = os.path.join(_DATA_DIR, "fjc_judges.csv")
OUTPUT_PATH = os.path.join(_DATA_DIR, "judicial_search_all.json")

# Key Epstein-related districts
KEY_DISTRICTS = [
    "U.S. District Court for the Southern District of New York",
    "U.S. District Court for the Southern District of Florida",
    "U.S. District Court for the District of Columbia",
    "U.S. District Court for the District of New Mexico",
    "U.S. District Court for the District of the Virgin Islands",
    "U.S. District Court for the Eastern District of New York",
    "U.S. District Court for the Northern District of New York",
    "U.S. District Court for the District of Connecticut",
    "U.S. District Court for the District of Massachusetts",
]


def parse_fjc():
    """Parse FJC CSV into categorized judge lists."""
    judges = {
        "scotus": [],
        "circuit_active": [],
        "circuit_senior": [],
        "district_active": [],
        "district_senior": [],
    }

    with open(FJC_CSV) as f:
        reader = csv.DictReader(f)
        for row in reader:
            dy = row.get("Death Year", "").strip()
            if dy:
                continue

            for i in range(1, 7):
                ct = row.get(f"Court Type ({i})", "").strip()
                cn = row.get(f"Court Name ({i})", "").strip()
                term = row.get(f"Termination ({i})", "").strip()
                senior = row.get(f"Senior Status Date ({i})", "").strip()

                if term or not ct:
                    continue

                first = row["First Name"].strip()
                middle = row["Middle Name"].strip()
                last = row["Last Name"].strip()
                suffix = row.get("Suffix", "").strip()
                full_name = f"{first} {last}"  # Use first + last for search
                display_name = f"{first} {middle} {last}".replace("  ", " ").strip()
                if suffix:
                    display_name += f", {suffix}"

                appt_pres = row.get(f"Appointing President ({i})", "").strip()
                appt_title = row.get(f"Appointment Title ({i})", "").strip()

                entry = {
                    "name": full_name,
                    "display_name": display_name,
                    "court": cn,
                    "appointed_by": appt_pres,
                    "title": appt_title,
                    "is_senior": bool(senior),
                }

                if ct == "Supreme Court":
                    judges["scotus"].append(entry)
                elif ct == "U.S. Court of Appeals":
                    key = "circuit_senior" if senior else "circuit_active"
                    judges[key].append(entry)
                elif ct == "U.S. District Court" and cn in KEY_DISTRICTS:
                    key = "district_senior" if senior else "district_active"
                    judges[key].append(entry)

    return judges


def search_name(cursor, name):
    """Search for a name in the FTS5 corpus."""
    term = f'"{name}"'
    result = {"search_query": term, "doc_count": 0, "page_count": 0, "sample_eftas": []}

    try:
        cursor.execute(
            "SELECT COUNT(DISTINCT efta_number) FROM pages_fts WHERE text_content MATCH ?",
            (term,)
        )
        doc_count = cursor.fetchone()[0]

        if doc_count > 0:
            cursor.execute(
                "SELECT COUNT(*) FROM pages_fts WHERE text_content MATCH ?",
                (term,)
            )
            page_count = cursor.fetchone()[0]

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
    judges = parse_fjc()
    print(f"Parsed FJC database:")
    for cat, jlist in judges.items():
        print(f"  {cat}: {len(jlist)}")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM pages LIMIT 1")
    print(f"\nDatabase connected. Total pages: {cursor.fetchone()[0]}")

    all_results = {}
    total_searched = 0
    total_found = 0

    for category, judge_list in judges.items():
        print(f"\nSearching {category} ({len(judge_list)} judges)...")
        results = []
        for j in judge_list:
            r = search_name(cursor, j["name"])
            r.update(j)
            results.append(r)
            total_searched += 1
            if r["doc_count"] > 0:
                total_found += 1
                print(f"  {j['display_name']:35s} {r['doc_count']:5d} docs  {j['court'][:40]}")
            if total_searched % 50 == 0:
                print(f"  ... {total_searched} searched, {total_found} found so far ...")
        all_results[category] = results

    conn.close()

    with open(OUTPUT_PATH, "w") as f:
        json.dump(all_results, f, indent=2)

    # Summary
    print(f"\n{'='*60}")
    print(f"SEARCH COMPLETE: {total_searched} judges searched, {total_found} found")
    print(f"{'='*60}")
    for cat, results in all_results.items():
        hits = [r for r in results if r["doc_count"] > 0]
        if hits:
            print(f"\n{cat}: {len(hits)}/{len(results)} found")
            for h in sorted(hits, key=lambda x: -x["doc_count"])[:20]:
                print(f"  {h['display_name']:35s} {h['doc_count']:5d} docs  {h['court'][:40]}")
            if len(hits) > 20:
                print(f"  ... and {len(hits)-20} more")

    print(f"\nResults written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
