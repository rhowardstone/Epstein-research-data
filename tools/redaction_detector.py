#!/usr/bin/env python3
"""Detect images with redaction boxes (black rectangles)"""
import os
import sqlite3
import json
from pathlib import Path
from PIL import Image
import numpy as np
from concurrent.futures import ProcessPoolExecutor, as_completed

def _find_data_dir():
    """Find the directory containing the database files."""
    if os.environ.get("EPSTEIN_DATA_DIR"):
        return os.environ["EPSTEIN_DATA_DIR"]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(repo_root, "extracted_images")):
        return repo_root
    if os.path.exists(os.path.join(os.getcwd(), "extracted_images")):
        return os.getcwd()
    parent = os.path.dirname(os.getcwd())
    for name in os.listdir(parent):
        candidate = os.path.join(parent, name, "extracted_images")
        if os.path.exists(candidate):
            return os.path.join(parent, name)
    return os.getcwd()

_DATA_DIR = _find_data_dir()
IMAGES_DIR = os.path.join(_DATA_DIR, "extracted_images")
OUTPUT_PATH = os.path.join(_DATA_DIR, "redacted_documents.jsonl")
DB_PATH = os.path.join(_DATA_DIR, "redaction_analysis.db")

def detect_redactions(img_path):
    """Detect black rectangular regions that could be redactions"""
    try:
        img = Image.open(img_path).convert('RGB')
        arr = np.array(img)
        
        # Find very dark pixels (potential redaction)
        dark_mask = (arr[:,:,0] < 30) & (arr[:,:,1] < 30) & (arr[:,:,2] < 30)
        
        # Calculate percentage of dark pixels
        dark_ratio = np.sum(dark_mask) / dark_mask.size
        
        # Look for horizontal black bars (common redaction pattern)
        row_darkness = np.mean(dark_mask, axis=1)
        dark_rows = np.sum(row_darkness > 0.5)  # Rows that are >50% dark
        
        # Extract EFTA number
        fname = os.path.basename(img_path)
        efta = fname.split('_')[0] if 'EFTA' in fname else fname
        
        has_redaction = dark_ratio > 0.01 and dark_rows > 5  # At least 1% dark with continuous bars
        
        return {
            'path': img_path,
            'efta': efta,
            'dark_ratio': float(dark_ratio),
            'dark_rows': int(dark_rows),
            'has_redaction': has_redaction,
            'confidence': min(1.0, dark_ratio * 10 + (dark_rows / 100))
        }
    except Exception as e:
        return {'path': img_path, 'error': str(e)}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS redactions (
        id INTEGER PRIMARY KEY,
        image_path TEXT UNIQUE,
        efta_number TEXT,
        dark_ratio REAL,
        dark_rows INTEGER,
        has_redaction BOOLEAN,
        confidence REAL
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_redact_efta ON redactions(efta_number)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_has_redaction ON redactions(has_redaction)')
    conn.commit()
    return conn

def main():
    print("Scanning for redacted documents...")
    conn = init_db()
    c = conn.cursor()
    
    # Get already processed
    c.execute("SELECT image_path FROM redactions")
    processed = set(row[0] for row in c.fetchall())
    
    all_images = list(Path(IMAGES_DIR).glob("*.png"))
    to_process = [str(p) for p in all_images if str(p) not in processed]
    
    print(f"Total images: {len(all_images)}")
    print(f"Already scanned: {len(processed)}")
    print(f"To scan: {len(to_process)}")
    
    redacted_docs = []
    completed = 0
    
    with ProcessPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(detect_redactions, img): img for img in to_process}
        
        for future in as_completed(futures):
            result = future.result()
            completed += 1
            
            if 'error' in result:
                continue
            
            c.execute("""INSERT OR REPLACE INTO redactions 
                        (image_path, efta_number, dark_ratio, dark_rows, has_redaction, confidence)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                     (result['path'], result['efta'], result['dark_ratio'], 
                      result['dark_rows'], result['has_redaction'], result['confidence']))
            
            if result['has_redaction']:
                redacted_docs.append(result)
            
            if completed % 500 == 0:
                conn.commit()
                print(f"Scanned {completed}/{len(to_process)}, found {len(redacted_docs)} with redactions")
    
    conn.commit()
    
    # Write high-confidence redactions
    with open(OUTPUT_PATH, 'w') as f:
        for doc in sorted(redacted_docs, key=lambda x: -x['confidence']):
            f.write(json.dumps(doc) + '\n')
    
    print(f"\nDONE! Found {len(redacted_docs)} documents with potential redactions")
    print(f"Results saved to {OUTPUT_PATH}")
    
    # Summary stats
    c.execute("SELECT COUNT(*) FROM redactions WHERE has_redaction = 1")
    total_redacted = c.fetchone()[0]
    print(f"Total redacted documents in database: {total_redacted}")
    
    conn.close()

if __name__ == "__main__":
    main()
