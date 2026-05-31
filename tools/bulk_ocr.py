#!/usr/bin/env python3
"""Bulk OCR all extracted images into searchable SQLite database"""
import os
import sqlite3
import sys
from pathlib import Path
from PIL import Image
import pytesseract
from concurrent.futures import ProcessPoolExecutor, as_completed
import json

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
IMAGES_DIR = os.path.join(_DATA_DIR, "extracted_images")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS ocr_results (
        id INTEGER PRIMARY KEY,
        image_path TEXT UNIQUE,
        efta_number TEXT,
        ocr_text TEXT,
        orientation INTEGER,
        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_efta ON ocr_results(efta_number)')
    c.execute('CREATE VIRTUAL TABLE IF NOT EXISTS ocr_fts USING fts5(efta_number, ocr_text, content=ocr_results, content_rowid=id)')
    conn.commit()
    return conn

def process_image(img_path):
    """OCR a single image, trying multiple orientations"""
    try:
        img = Image.open(img_path)
        best_text = ""
        best_orientation = 0
        
        for angle in [0, 90, -90, 180]:
            rotated = img.rotate(angle, expand=True)
            text = pytesseract.image_to_string(rotated)
            if len(text.strip()) > len(best_text):
                best_text = text.strip()
                best_orientation = angle
        
        # Extract EFTA number from filename
        fname = os.path.basename(img_path)
        efta = fname.split('_')[0] if 'EFTA' in fname else fname
        
        return {
            'path': img_path,
            'efta': efta,
            'text': best_text,
            'orientation': best_orientation
        }
    except Exception as e:
        return {'path': img_path, 'error': str(e)}

def main():
    print("Initializing OCR database...")
    conn = init_db()
    c = conn.cursor()
    
    # Get already processed images
    c.execute("SELECT image_path FROM ocr_results")
    processed = set(row[0] for row in c.fetchall())
    
    # Get all images
    all_images = list(Path(IMAGES_DIR).glob("*.png"))
    to_process = [str(p) for p in all_images if str(p) not in processed]
    
    print(f"Total images: {len(all_images)}")
    print(f"Already processed: {len(processed)}")
    print(f"To process: {len(to_process)}")
    
    if not to_process:
        print("All images already processed!")
        return
    
    # Process in parallel
    completed = 0
    errors = 0
    
    with ProcessPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(process_image, img): img for img in to_process}
        
        for future in as_completed(futures):
            result = future.result()
            completed += 1
            
            if 'error' in result:
                errors += 1
                continue
            
            try:
                c.execute("""INSERT OR REPLACE INTO ocr_results 
                            (image_path, efta_number, ocr_text, orientation) 
                            VALUES (?, ?, ?, ?)""",
                         (result['path'], result['efta'], result['text'], result['orientation']))
                
                # Update FTS
                c.execute("""INSERT INTO ocr_fts(rowid, efta_number, ocr_text) 
                            VALUES (last_insert_rowid(), ?, ?)""",
                         (result['efta'], result['text']))
                
                if completed % 100 == 0:
                    conn.commit()
                    print(f"Processed {completed}/{len(to_process)} ({errors} errors)")
            except Exception as e:
                errors += 1
    
    conn.commit()
    print(f"DONE! Processed {completed} images ({errors} errors)")
    conn.close()

if __name__ == "__main__":
    main()
