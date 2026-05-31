#!/usr/bin/env python3
"""Search for known names/entities across all OCR'd documents"""
import sqlite3
import json
import re
from pathlib import Path

import os

def _find_data_dir():
    """Find the directory containing the database files."""
    if os.environ.get("EPSTEIN_DATA_DIR"):
        return os.environ["EPSTEIN_DATA_DIR"]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(repo_root, "ocr_database.db")):
        return repo_root
    if os.path.exists(os.path.join(os.getcwd(), "ocr_database.db")):
        return os.getcwd()
    parent = os.path.dirname(os.getcwd())
    for name in os.listdir(parent):
        candidate = os.path.join(parent, name, "ocr_database.db")
        if os.path.exists(candidate):
            return os.path.join(parent, name)
    return os.getcwd()

_DATA_DIR = _find_data_dir()
DB_PATH = os.path.join(_DATA_DIR, "ocr_database.db")
FINDINGS_PATH = os.path.join(_DATA_DIR, "evidence_findings.jsonl")
OUTPUT_PATH = os.path.join(_DATA_DIR, "name_crossref.jsonl")

def extract_names_from_findings():
    """Extract all names from our evidence findings"""
    names = set()
    
    with open(FINDINGS_PATH, 'r') as f:
        for line in f:
            try:
                finding = json.loads(line)
                # Extract from various fields
                text = json.dumps(finding).lower()
                
                # Add specific known names
                data = finding.get('data', {})
                if isinstance(data, dict):
                    for key, val in data.items():
                        if isinstance(val, str) and len(val) > 2:
                            # Check if it looks like a name
                            if any(word in key.lower() for word in ['name', 'manager', 'accountant', 'attorney', 'pilot', 'escort']):
                                names.add(val.strip())
                        elif isinstance(val, list):
                            for item in val:
                                if isinstance(item, str) and len(item) > 2:
                                    names.add(item.strip())
                                elif isinstance(item, dict):
                                    for k, v in item.items():
                                        if isinstance(v, str) and 'name' in k.lower():
                                            names.add(v.strip())
            except:
                continue
    
    # Add hardcoded important names
    important_names = [
        "Epstein", "Maxwell", "Ghislaine", "Jeffrey",
        "Bedminster", "John Bedminster", "Maurice Bedminster", "Hilian Bedminster",
        "Gaillard", "Sylvester Gaillard",
        "Leon Black", "Debra Black", "Melanie Spinella",
        "Darren Indyke", "Richard Kahn", "Mark Tollison",
        "Lesley Groff", "Leslie Groff",
        "Ann Rodriguez", "Carlos Rodriguez", "Monique Rodriguez",
        "Daphne Wallace", "Cecile deJongh", "Jeanne Brennan",
        "Larry Visoski", "Jermaine Ruan",
        "Clinton", "Trump", "Prince Andrew", "Alan Dershowitz",
        "Jean-Luc Brunel", "Les Wexner",
        "Basillia Morales", "Basila Morales",
        "Pierre Jules", "Myla Trestiza",
        "Southern Trust", "HBRK", "LSJE",
        "Zorro Ranch", "Little St. James", "Avenue Foch"
    ]
    names.update(important_names)
    
    return list(names)

def search_names():
    """Search for all names in OCR database"""
    names = extract_names_from_findings()
    print(f"Searching for {len(names)} names/entities...")
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    results = []
    
    for name in names:
        if len(name) < 3:
            continue
        
        # Search using FTS
        try:
            c.execute("""SELECT efta_number, ocr_text FROM ocr_results 
                        WHERE ocr_text LIKE ? COLLATE NOCASE""", (f'%{name}%',))
            matches = c.fetchall()
            
            if matches:
                for efta, text in matches:
                    # Find context around the match
                    idx = text.lower().find(name.lower())
                    start = max(0, idx - 50)
                    end = min(len(text), idx + len(name) + 50)
                    context = text[start:end]
                    
                    results.append({
                        'name': name,
                        'efta': efta,
                        'context': context,
                        'full_match': True
                    })
                print(f"  '{name}': {len(matches)} matches")
        except Exception as e:
            print(f"  Error searching '{name}': {e}")
    
    # Write results
    with open(OUTPUT_PATH, 'w') as f:
        for r in results:
            f.write(json.dumps(r) + '\n')
    
    print(f"\nWrote {len(results)} cross-references to {OUTPUT_PATH}")
    conn.close()

if __name__ == "__main__":
    search_names()
