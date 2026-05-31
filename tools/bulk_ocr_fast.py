#!/usr/bin/env python3
"""Bulk OCR with maximum parallelism"""
import os
import sqlite3
from pathlib import Path
from PIL import Image
import pytesseract
from concurrent.futures import ProcessPoolExecutor, as_completed

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
MAX_WORKERS = 128

def process_image(img_path):
    try:
        img = Image.open(img_path)
        text = pytesseract.image_to_string(img)
        fname = os.path.basename(img_path)
        efta = fname.split('_')[0] if 'EFTA' in fname else fname
        return {'path': img_path, 'efta': efta, 'text': text.strip(), 'orientation': 0}
    except Exception as e:
        return {'path': img_path, 'error': str(e)}

def main():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT image_path FROM ocr_results")
    processed = set(row[0] for row in c.fetchall())
    
    all_images = list(Path(IMAGES_DIR).glob("*.png"))
    to_process = [str(p) for p in all_images if str(p) not in processed]
    
    print(f"To process: {len(to_process)} (already done: {len(processed)})")
    
    if not to_process:
        print("All done!")
        return
    
    completed = 0
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_image, img): img for img in to_process}
        
        for future in as_completed(futures):
            result = future.result()
            completed += 1
            
            if 'error' not in result:
                c.execute("""INSERT OR REPLACE INTO ocr_results 
                            (image_path, efta_number, ocr_text, orientation) 
                            VALUES (?, ?, ?, ?)""",
                         (result['path'], result['efta'], result['text'], result['orientation']))
            
            if completed % 500 == 0:
                conn.commit()
                print(f"Processed {completed}/{len(to_process)}")
    
    conn.commit()
    print(f"DONE! Processed {completed}")
    conn.close()

if __name__ == "__main__":
    main()
