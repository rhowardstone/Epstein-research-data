#!/usr/bin/env python3
"""Classify documents by type based on OCR content"""
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
OCR_DB = os.path.join(_DATA_DIR, "ocr_database.db")
OUTPUT_PATH = os.path.join(_DATA_DIR, "document_classifications.jsonl")
PRIORITY_PATH = os.path.join(_DATA_DIR, "priority_documents.jsonl")

# Document type patterns
PATTERNS = {
    'employee_roster': [r'employee', r'staff', r'payroll', r'roster', r'personnel'],
    'financial': [r'invoice', r'payment', r'account', r'balance', r'transfer', r'bank', r'\$\d+', r'amount'],
    'legal': [r'attorney', r'contract', r'agreement', r'notary', r'witness', r'court', r'deposition'],
    'correspondence': [r'dear\s', r'sincerely', r'regards', r'letter', r'memo', r'from:', r'to:'],
    'flight_log': [r'flight', r'passenger', r'pilot', r'aircraft', r'departure', r'arrival', r'tail\s*number'],
    'phone_records': [r'phone', r'call', r'voicemail', r'\d{3}[-.\s]?\d{3}[-.\s]?\d{4}', r'dial'],
    'visitor_log': [r'visitor', r'guest', r'arrival', r'check.?in'],
    'property': [r'property', r'deed', r'real estate', r'mortgage', r'title'],
    'medical': [r'medical', r'prescription', r'doctor', r'patient', r'diagnosis', r'rx'],
    'thank_you_letter': [r'thank you', r'thanks', r'grateful', r'appreciate'],
    'contact_info': [r'emergency contact', r'address', r'phone number', r'email'],
    'corporate': [r'corporation', r'llc', r'inc\.', r'board', r'director', r'shareholder'],
    'schedule': [r'schedule', r'calendar', r'appointment', r'meeting', r'agenda'],
    'photo_metadata': [r'photo', r'image', r'picture', r'camera'],
}

# High priority indicators
HIGH_PRIORITY = [
    r'minor', r'underage', r'young', r'girl', r'teen',
    r'massage', r'recruit',
    r'prince', r'clinton', r'trump', r'president', r'senator', r'governor',
    r'settlement', r'nda', r'confidential',
    r'destroy', r'shred', r'delete', r'cover',
    r'victim', r'complaint', r'assault',
    r'passport', r'visa', r'immigration',
]

def classify_document(text):
    """Classify document based on content patterns"""
    text_lower = text.lower()
    
    classifications = []
    priority_score = 0
    priority_matches = []
    
    # Check document type patterns
    for doc_type, patterns in PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                classifications.append(doc_type)
                break
    
    # Check priority indicators
    for pattern in HIGH_PRIORITY:
        matches = re.findall(pattern, text_lower)
        if matches:
            priority_score += len(matches) * 10
            priority_matches.extend(matches)
    
    return {
        'types': list(set(classifications)),
        'priority_score': priority_score,
        'priority_matches': list(set(priority_matches))
    }

def main():
    print("Classifying documents...")
    
    conn = sqlite3.connect(OCR_DB)
    c = conn.cursor()
    
    c.execute("SELECT efta_number, ocr_text, image_path FROM ocr_results WHERE ocr_text IS NOT NULL AND ocr_text != ''")
    rows = c.fetchall()
    
    print(f"Processing {len(rows)} documents with OCR text...")
    
    classifications = []
    priority_docs = []
    
    type_counts = {}
    
    for efta, text, path in rows:
        result = classify_document(text)
        
        doc = {
            'efta': efta,
            'path': path,
            'types': result['types'],
            'priority_score': result['priority_score'],
            'priority_matches': result['priority_matches']
        }
        
        classifications.append(doc)
        
        if result['priority_score'] > 0:
            priority_docs.append(doc)
        
        for t in result['types']:
            type_counts[t] = type_counts.get(t, 0) + 1
    
    # Write all classifications
    with open(OUTPUT_PATH, 'w') as f:
        for doc in classifications:
            f.write(json.dumps(doc) + '\n')
    
    # Write priority documents sorted by score
    priority_docs.sort(key=lambda x: -x['priority_score'])
    with open(PRIORITY_PATH, 'w') as f:
        for doc in priority_docs:
            f.write(json.dumps(doc) + '\n')
    
    print(f"\nDocument type distribution:")
    for dtype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {dtype}: {count}")
    
    print(f"\nHigh priority documents: {len(priority_docs)}")
    print(f"Top 10 priority documents:")
    for doc in priority_docs[:10]:
        print(f"  {doc['efta']}: score={doc['priority_score']}, matches={doc['priority_matches']}")
    
    print(f"\nResults saved to:")
    print(f"  {OUTPUT_PATH}")
    print(f"  {PRIORITY_PATH}")
    
    conn.close()

if __name__ == "__main__":
    main()
