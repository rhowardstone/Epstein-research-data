#!/usr/bin/env python3
"""
Extraction-Only Pipeline (No Auto-Classification)

Extracts text and entities, but ALL person names get pseudonymous IDs.
Classification happens through manual review.

Also includes pattern detection for:
- Diagonal/acrostic reading patterns
- Unusual spacing that might encode messages
- Letter frequency anomalies
"""
import os
import sys
import json
import argparse
import multiprocessing as mp
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict, field
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import traceback
import re

import pdfplumber
import fitz
import spacy
from tqdm import tqdm

from entity_registry import EntityRegistry, EntityType

_nlp = None

def get_nlp():
    global _nlp
    if _nlp is None:
        _nlp = spacy.load("en_core_web_trf")
        spacy.prefer_gpu()
    return _nlp


@dataclass
class PatternAnomaly:
    """Detected pattern that might encode hidden information."""
    pattern_type: str  # "diagonal", "acrostic", "spacing", "frequency"
    page_num: int
    description: str
    raw_data: str
    confidence: float


@dataclass
class ExtractedDocument:
    """Full extraction result with everything for manual review."""
    source_path: str
    source_hash: str
    extracted_at: str
    page_count: int

    # Raw text (no modifications)
    raw_text: str

    # Text with ALL person names replaced by temp IDs
    anonymized_text: str

    # All detected persons (for manual classification)
    persons: List[Dict]  # {temp_id, original_name, contexts: [], needs_classification: True}

    # Organizations and locations (usually safe to show)
    organizations: List[Dict]
    locations: List[Dict]

    # Pattern anomalies detected
    pattern_anomalies: List[Dict]

    # Redaction info
    redaction_stats: Dict

    # For manual review
    review_status: str = "pending"  # pending, in_progress, classified
    reviewer_notes: str = ""


def compute_file_hash(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def detect_redaction_boxes(pdf_path: str) -> List[List[Tuple]]:
    """Detect black filled rectangles."""
    doc = fitz.open(pdf_path)
    all_boxes = []

    for page in doc:
        page_boxes = []
        for annot in page.annots() or []:
            if annot.type[0] == 12:
                page_boxes.append(tuple(annot.rect))

        for d in page.get_drawings():
            if d.get("fill") is not None:
                fill = d.get("fill")
                is_black = False
                if isinstance(fill, (list, tuple)) and len(fill) >= 3:
                    if all(c < 0.1 for c in fill[:3]):
                        is_black = True
                elif fill == 0 or fill == (0,) or fill == [0]:
                    is_black = True

                if is_black:
                    rect = d.get("rect")
                    if rect and (rect[2] - rect[0]) > 10 and (rect[3] - rect[1]) > 5:
                        page_boxes.append(tuple(rect))

        all_boxes.append(page_boxes)

    doc.close()
    return all_boxes


def detect_diagonal_patterns(lines: List[str]) -> List[PatternAnomaly]:
    """
    Detect potential diagonal reading patterns.

    Looks for:
    - First letters of lines spelling words
    - Diagonal patterns across lines
    - Patterns reading down columns
    """
    anomalies = []

    if len(lines) < 3:
        return anomalies

    # Check first letters (acrostic)
    first_letters = ''.join(line[0] if line else '' for line in lines if line.strip())
    if len(first_letters) >= 5:
        # Check if it forms words
        words = re.findall(r'[a-zA-Z]{3,}', first_letters)
        if words:
            anomalies.append(PatternAnomaly(
                pattern_type="acrostic",
                page_num=0,
                description=f"First letters may spell: {first_letters[:50]}",
                raw_data=first_letters,
                confidence=0.3
            ))

    # Check for diagonal patterns (every Nth character)
    for offset in [1, 2, 3]:
        diagonal = ''
        for i, line in enumerate(lines):
            if line and len(line) > (i * offset) % max(1, len(line)):
                idx = (i * offset) % len(line)
                diagonal += line[idx] if idx < len(line) else ''

        if len(diagonal) >= 5:
            words = re.findall(r'[a-zA-Z]{4,}', diagonal)
            if words:
                anomalies.append(PatternAnomaly(
                    pattern_type="diagonal",
                    page_num=0,
                    description=f"Diagonal offset {offset} may contain: {diagonal[:50]}",
                    raw_data=diagonal,
                    confidence=0.2
                ))

    return anomalies


def detect_spacing_anomalies(text: str) -> List[PatternAnomaly]:
    """Detect unusual spacing that might encode information."""
    anomalies = []

    # Check for Morse-code style spacing (dots and dashes via space count)
    space_runs = re.findall(r' +', text)
    if space_runs:
        lengths = [len(s) for s in space_runs]
        unique_lengths = set(lengths)

        if len(unique_lengths) <= 3 and len(unique_lengths) > 1:
            # Could be binary/ternary encoding
            anomalies.append(PatternAnomaly(
                pattern_type="spacing",
                page_num=0,
                description=f"Suspicious spacing pattern: {len(unique_lengths)} distinct space lengths",
                raw_data=str(lengths[:50]),
                confidence=0.4
            ))

    # Check for unusual tab patterns
    tab_pattern = re.findall(r'\t+', text)
    if tab_pattern:
        anomalies.append(PatternAnomaly(
            pattern_type="spacing",
            page_num=0,
            description=f"Tab characters found: {len(tab_pattern)} instances",
            raw_data=str([len(t) for t in tab_pattern[:20]]),
            confidence=0.3
        ))

    return anomalies


def detect_letter_frequency_anomalies(text: str) -> List[PatternAnomaly]:
    """Detect unusual letter frequencies that might indicate encoding."""
    if len(text) < 100:
        return []

    # Standard English letter frequencies
    expected = {
        'e': 12.7, 't': 9.1, 'a': 8.2, 'o': 7.5, 'i': 7.0,
        'n': 6.7, 's': 6.3, 'h': 6.1, 'r': 6.0, 'd': 4.3
    }

    # Calculate actual frequencies
    text_lower = text.lower()
    total_letters = sum(1 for c in text_lower if c.isalpha())
    if total_letters < 50:
        return []

    anomalies = []
    actual = {}
    for c in text_lower:
        if c.isalpha():
            actual[c] = actual.get(c, 0) + 1

    for letter, exp_freq in expected.items():
        actual_freq = (actual.get(letter, 0) / total_letters) * 100
        deviation = abs(actual_freq - exp_freq)

        if deviation > 5:  # More than 5% deviation
            anomalies.append(PatternAnomaly(
                pattern_type="frequency",
                page_num=0,
                description=f"Letter '{letter}' frequency anomaly: {actual_freq:.1f}% vs expected {exp_freq}%",
                raw_data=f"{letter}: {actual_freq:.1f}%",
                confidence=min(0.8, deviation / 10)
            ))

    return anomalies


def extract_and_anonymize(pdf_path: str, registry: EntityRegistry) -> ExtractedDocument:
    """
    Extract text and replace ALL person names with temporary IDs.
    No auto-classification - everything flagged for manual review.
    """
    nlp = get_nlp()

    # Extract text
    redaction_boxes = detect_redaction_boxes(pdf_path)
    total_boxes = sum(len(boxes) for boxes in redaction_boxes)

    full_text_parts = []
    page_lines = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            full_text_parts.append(text)
            page_lines.extend(text.split('\n'))

    raw_text = "\n\n".join(full_text_parts)

    # Run NER
    persons = []
    organizations = []
    locations = []

    # Process in chunks
    anonymized_text = raw_text
    max_chunk = 100000

    for chunk_start in range(0, len(raw_text), max_chunk):
        chunk = raw_text[chunk_start:chunk_start + max_chunk]
        doc = nlp(chunk)

        chunk_persons = []
        for ent in doc.ents:
            abs_start = chunk_start + ent.start_char
            abs_end = chunk_start + ent.end_char

            if ent.label_ == "PERSON":
                # Get context around the mention
                ctx_start = max(0, abs_start - 200)
                ctx_end = min(len(raw_text), abs_end + 200)
                context = raw_text[ctx_start:ctx_end]

                chunk_persons.append({
                    "text": ent.text,
                    "start": abs_start,
                    "end": abs_end,
                    "context": context
                })

            elif ent.label_ == "ORG":
                organizations.append({
                    "text": ent.text,
                    "start": abs_start,
                    "end": abs_end
                })

            elif ent.label_ in ("GPE", "LOC"):
                locations.append({
                    "text": ent.text,
                    "start": abs_start,
                    "end": abs_end
                })

        # Sort persons by position (reverse) for replacement
        chunk_persons.sort(key=lambda x: x["start"], reverse=True)

        for p in chunk_persons:
            # Create temporary ID (ALL persons get anonymized)
            temp_id = registry.get_or_create_id(
                name=p["text"],
                entity_type=EntityType.UNKNOWN_PERSON,  # All start as unknown
                doc_path=pdf_path,
                context="Pending manual classification"
            )

            persons.append({
                "temp_id": temp_id,
                "contexts": [p["context"]],
                "mentions": 1,
                "needs_classification": True,
                "classification": None,  # To be filled by reviewer
                "confidence": None
            })

            # Replace in text
            anonymized_text = (
                anonymized_text[:p["start"]] +
                f"[{temp_id}]" +
                anonymized_text[p["end"]:]
            )

    # Consolidate duplicate persons
    person_map = {}
    for p in persons:
        if p["temp_id"] in person_map:
            person_map[p["temp_id"]]["mentions"] += 1
            person_map[p["temp_id"]]["contexts"].extend(p["contexts"])
        else:
            person_map[p["temp_id"]] = p

    # Detect pattern anomalies
    anomalies = []
    anomalies.extend(detect_diagonal_patterns(page_lines))
    anomalies.extend(detect_spacing_anomalies(raw_text))
    anomalies.extend(detect_letter_frequency_anomalies(raw_text))

    # Filter to significant anomalies
    significant_anomalies = [a for a in anomalies if a.confidence > 0.25]

    return ExtractedDocument(
        source_path=pdf_path,
        source_hash=compute_file_hash(pdf_path),
        extracted_at=datetime.now().isoformat(),
        page_count=len(full_text_parts),
        raw_text=raw_text,
        anonymized_text=anonymized_text,
        persons=list(person_map.values()),
        organizations=[{"text": o["text"]} for o in organizations],
        locations=[{"text": l["text"]} for l in locations],
        pattern_anomalies=[asdict(a) for a in significant_anomalies],
        redaction_stats={
            "total_boxes": total_boxes,
            "pages_with_redactions": sum(1 for b in redaction_boxes if b)
        },
        review_status="pending"
    )


def process_single(pdf_path: str, registry_path: str, output_dir: str) -> Dict:
    """Process single PDF."""
    try:
        registry = EntityRegistry(registry_path)
        result = extract_and_anonymize(pdf_path, registry)

        # Save output
        output_path = Path(output_dir) / (Path(pdf_path).stem + ".json")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w') as f:
            json.dump(asdict(result), f, indent=2)

        return {
            "success": True,
            "path": pdf_path,
            "persons": len(result.persons),
            "anomalies": len(result.pattern_anomalies),
            "redactions": result.redaction_stats["total_boxes"]
        }

    except Exception as e:
        return {
            "success": False,
            "path": pdf_path,
            "error": str(e)
        }


def main():
    parser = argparse.ArgumentParser(
        description="Extract and anonymize PDFs (no auto-classification)"
    )
    parser.add_argument("input_dir", help="Directory containing PDFs")
    parser.add_argument("-o", "--output-dir", default="./extracted_json")
    parser.add_argument("-r", "--registry", default="./entity_registry.db")
    parser.add_argument("-w", "--workers", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)

    args = parser.parse_args()

    pdf_paths = list(Path(args.input_dir).rglob("*.pdf"))
    print(f"Found {len(pdf_paths)} PDFs")

    if args.limit:
        pdf_paths = pdf_paths[:args.limit]

    num_workers = args.workers or max(1, mp.cpu_count() - 2)
    print(f"Using {num_workers} workers")

    results = []
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(process_single, str(p), args.registry, args.output_dir): p
            for p in pdf_paths
        }

        with tqdm(total=len(pdf_paths), desc="Extracting") as pbar:
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                if result["success"]:
                    pbar.set_postfix({
                        "persons": result["persons"],
                        "anomalies": result.get("anomalies", 0)
                    })
                pbar.update(1)

    # Summary
    successful = [r for r in results if r["success"]]
    print(f"\nProcessed: {len(successful)}/{len(results)}")
    print(f"Total persons found: {sum(r['persons'] for r in successful)}")
    print(f"Documents with pattern anomalies: {sum(1 for r in successful if r.get('anomalies', 0) > 0)}")


if __name__ == "__main__":
    main()
