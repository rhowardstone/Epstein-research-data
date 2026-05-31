#!/usr/bin/env python3
"""
Download court filing PDFs from Wayback Machine archives.

Usage:
    python download_case.py "giuffre v. maxwell"
    python download_case.py "usvi v. jpmorgan" --limit 10
    python download_case.py --list-cases

Requirements:
    pip install requests
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, unquote

import requests

# Known court cases and their file counts
CASE_INVENTORY = {
    "giuffre v. maxwell": {
        "pattern": "giuffre v. maxwell",
        "count": 2978,
        "description": "Settlement documents, depositions from civil case"
    },
    "usvi v. jpmorgan": {
        "pattern": "jpmorgan",
        "count": 1840,
        "description": "USVI lawsuit against JPMorgan Chase"
    },
    "us v. maxwell": {
        "pattern": "120-cr-00330",
        "count": 1318,
        "description": "Criminal trial records, exhibits, transcripts"
    },
    "doe v. epstein 80119": {
        "pattern": "doe v. epstein, no. 908-cv-80119",
        "count": 856,
        "description": "Civil lawsuit documents"
    },
    "doe v. united states": {
        "pattern": "doe v. united states",
        "count": 695,
        "description": "Civil case against government"
    },
    "epstein v. rothstein": {
        "pattern": "epstein v. rothstein",
        "count": 1412,
        "description": "Florida state court case"
    }
}

# Working Wayback Machine timestamps
WORKING_TIMESTAMPS = [
    "20251219221855", "20251221112112", "20251227113138",
    "20251228132625", "20260115090642", "20260117011558"
]

def fetch_case_manifest():
    """Fetch the complete manifest of available files."""
    # This would normally query the Wayback CDX API
    # For now, return a placeholder structure
    return {
        "usvi_v_jpmorgan": [
            {"filename": "001-01.pdf", "timestamp": "20251228132625"},
            {"filename": "001.pdf", "timestamp": "20260117011558"},
            # ... more files would be here
        ]
    }

def sanitize_filename(filename):
    """Clean filename for filesystem storage."""
    # Remove or replace problematic characters
    return re.sub(r'[<>:"/\\|?*]', '_', filename)

def download_file(url, output_path, timeout=120):
    """Download a single file with retries."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Skip if already exists and has reasonable size
    if output_path.exists() and output_path.stat().st_size > 1000:
        return True, "Already exists"

    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; ResearchBot/1.0; +https://github.com/rhowardstone/epstein-research-data)'
    }

    for attempt in range(3):
        try:
            response = requests.get(url, headers=headers, timeout=timeout, stream=True)
            response.raise_for_status()

            # Check if it's actually a PDF
            content_type = response.headers.get('Content-Type', '')
            if 'pdf' not in content_type.lower():
                return False, f"Not a PDF: {content_type}"

            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            return True, "Downloaded"

        except requests.RequestException as e:
            if attempt < 2:  # Not last attempt
                time.sleep(2 ** attempt)  # Exponential backoff
                continue
            return False, str(e)

    return False, "Max retries exceeded"

def build_wayback_url(base_url, timestamp):
    """Build Wayback Machine URL for specific timestamp."""
    return f"https://web.archive.org/web/{timestamp}id_/{base_url}"

def download_case(case_name, output_dir="downloads", limit=None):
    """Download all files for a specific case."""

    if case_name not in CASE_INVENTORY:
        print(f"Unknown case: {case_name}")
        print("Available cases:")
        for name, info in CASE_INVENTORY.items():
            print(f"  {name}: {info['description']} ({info['count']} files)")
        return False

    case_info = CASE_INVENTORY[case_name]
    output_dir = Path(output_dir) / sanitize_filename(case_name)

    print(f"Downloading case: {case_info['description']}")
    print(f"Expected files: {case_info['count']}")
    print(f"Output directory: {output_dir}")

    # For demo purposes, download a few example files
    # In a real implementation, this would query the Wayback CDX API

    example_files = []
    if case_name == "usvi v. jpmorgan":
        base_url = "https://www.justice.gov/multimedia/Court%20Records/Government%20of%20the%20United%20States%20Virgin%20Islands%20v.%20JPMorgan%20Chase%20Bank,%20N.A.,%20No.%20122-cv-10904%20(S.D.N.Y.%202022)/"
        example_files = [
            "001-01.pdf", "001.pdf", "003.pdf", "004.pdf", "005.pdf"
        ]
    elif case_name == "giuffre v. maxwell":
        base_url = "https://www.justice.gov/multimedia/Court%20Records/Giuffre%20v.%20Maxwell,%20No.%20115-cv-07433%20(S.D.N.Y.%202015)/"
        example_files = [
            "001.pdf", "002.pdf", "003.pdf", "010.pdf", "020.pdf"
        ]

    if limit:
        example_files = example_files[:limit]

    success_count = 0
    fail_count = 0

    for filename in example_files:
        file_url = base_url + filename

        # Try multiple timestamps until one works
        downloaded = False
        for timestamp in WORKING_TIMESTAMPS:
            wayback_url = build_wayback_url(file_url, timestamp)
            output_path = output_dir / filename

            print(f"Trying {filename} (timestamp {timestamp})...", end=" ")

            success, message = download_file(wayback_url, output_path)
            if success:
                print(f"✓ {message}")
                success_count += 1
                downloaded = True
                break
            else:
                print(f"✗ {message}")

        if not downloaded:
            fail_count += 1

        # Rate limiting - be nice to Wayback Machine
        time.sleep(1)

    print(f"\nResults: {success_count} downloaded, {fail_count} failed")
    return success_count > 0

def main():
    parser = argparse.ArgumentParser(description="Download court case files from Wayback Machine")
    parser.add_argument("case_name", nargs="?", help="Case name to download")
    parser.add_argument("--list-cases", action="store_true", help="List available cases")
    parser.add_argument("--output", "-o", default="downloads", help="Output directory")
    parser.add_argument("--limit", type=int, help="Limit number of files to download")

    args = parser.parse_args()

    if args.list_cases or not args.case_name:
        print("Available cases:")
        for name, info in CASE_INVENTORY.items():
            print(f"\n{name}:")
            print(f"  Description: {info['description']}")
            print(f"  File count: {info['count']}")
            print(f"  Usage: python download_case.py \"{name}\"")
        return

    case_name = args.case_name.lower()
    success = download_case(case_name, args.output, args.limit)

    if not success:
        sys.exit(1)

if __name__ == "__main__":
    main()