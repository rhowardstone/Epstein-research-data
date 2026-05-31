#!/usr/bin/env python3
"""
Batch PDF Processing Pipeline for Epstein Files

Features:
- Multiprocessing (uses all available cores)
- GPU-accelerated NER
- Pseudonymous victim IDs
- Structured JSON output for knowledge graph
- Progress tracking with resume capability
"""
import os
import sys
import json
import argparse
import multiprocessing as mp
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import traceback

import pdfplumber
import fitz  # PyMuPDF
import spacy
from tqdm import tqdm

# Local imports
from entity_registry import EntityRegistry, EntityType
from victim_classifier import classify_person, should_protect_name, get_context_window

# Global NLP model (loaded per-process)
_nlp = None

def get_nlp():
    """Lazy-load spaCy model (once per process)."""
    global _nlp
    if _nlp is None:
        _nlp = spacy.load("en_core_web_trf")
        if spacy.prefer_gpu():
            print(f"[PID {os.getpid()}] Using GPU for NER")
    return _nlp


@dataclass
class ExtractionResult:
    """Result from processing a single PDF."""
    pdf_path: str
    success: bool
    error: Optional[str]
    page_count: int
    total_words: int
    total_chars: int
    redaction_boxes_found: int
    words_under_redactions: int
    entities_found: Dict[str, int]  # entity_type -> count
    entity_ids: List[str]  # List of pseudonymous IDs found
    processing_time_sec: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DocumentOutput:
    """Structured output for a processed document."""
    source_path: str
    source_hash: str  # SHA-256 of original file
    processed_at: str
    page_count: int

    # Full text with protected names replaced by IDs
    protected_text: str

    # Structured entities
    entities: List[Dict]  # {id, type, mentions: [{page, context}]}

    # Redaction info
    redaction_stats: Dict

    # Metadata
    metadata: Dict


def compute_file_hash(filepath: str) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def detect_redaction_boxes(pdf_path: str) -> List[List[Tuple[float, float, float, float]]]:
    """Detect black filled rectangles (redaction boxes) in each page."""
    doc = fitz.open(pdf_path)
    all_boxes = []

    for page in doc:
        page_boxes = []

        # Check for redaction annotations
        for annot in page.annots() or []:
            if annot.type[0] == 12:  # Redact annotation type
                page_boxes.append(tuple(annot.rect))

        # Look for black filled rectangles
        drawings = page.get_drawings()
        for d in drawings:
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
                    if rect:
                        width = rect[2] - rect[0]
                        height = rect[3] - rect[1]
                        if width > 10 and height > 5:
                            page_boxes.append(tuple(rect))

        all_boxes.append(page_boxes)

    doc.close()
    return all_boxes


def word_overlaps_box(word_bbox, box, threshold=0.5) -> bool:
    """Check if word overlaps with redaction box."""
    wx0, wy0, wx1, wy1 = word_bbox
    bx0, by0, bx1, by1 = box

    ix0 = max(wx0, bx0)
    iy0 = max(wy0, by0)
    ix1 = min(wx1, bx1)
    iy1 = min(wy1, by1)

    if ix0 >= ix1 or iy0 >= iy1:
        return False

    intersection = (ix1 - ix0) * (iy1 - iy0)
    word_area = (wx1 - wx0) * (wy1 - wy0)

    return word_area > 0 and (intersection / word_area) >= threshold


def extract_text_with_positions(pdf_path: str) -> Tuple[str, List[Dict], Dict]:
    """
    Extract text from PDF with position info.
    Returns: (full_text, page_texts_with_positions, redaction_stats)
    """
    redaction_boxes = detect_redaction_boxes(pdf_path)
    total_boxes = sum(len(boxes) for boxes in redaction_boxes)

    full_text_parts = []
    pages_data = []
    words_under_redactions = 0
    total_words = 0

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            words = page.extract_words(
                keep_blank_chars=False,
                use_text_flow=True,
                extra_attrs=["size", "fontname"]
            )

            page_boxes = redaction_boxes[page_idx] if page_idx < len(redaction_boxes) else []
            page_text_parts = []
            page_words_data = []

            for w in words:
                text = w.get("text", "")
                if not text.strip():
                    continue

                total_words += 1
                word_bbox = (
                    float(w.get("x0", 0)),
                    float(w.get("top", 0)),
                    float(w.get("x1", 0)),
                    float(w.get("bottom", 0))
                )

                under_redaction = False
                for box in page_boxes:
                    if word_overlaps_box(word_bbox, box):
                        under_redaction = True
                        words_under_redactions += 1
                        break

                page_text_parts.append(text)
                page_words_data.append({
                    "text": text,
                    "bbox": word_bbox,
                    "under_redaction": under_redaction
                })

            page_text = " ".join(page_text_parts)
            full_text_parts.append(page_text)
            pages_data.append({
                "page_num": page_idx + 1,
                "text": page_text,
                "words": page_words_data,
                "redaction_boxes": len(page_boxes)
            })

    redaction_stats = {
        "total_boxes": total_boxes,
        "words_under_redactions": words_under_redactions,
        "total_words": total_words,
        "recovery_rate": (words_under_redactions / total_words * 100) if total_words > 0 else 0
    }

    return "\n\n".join(full_text_parts), pages_data, redaction_stats


def process_single_pdf(
    pdf_path: str,
    registry_path: str,
    output_dir: str
) -> ExtractionResult:
    """
    Process a single PDF file.

    1. Extract text
    2. Detect redactions
    3. Run NER
    4. Classify entities (victim vs perpetrator)
    5. Replace protected names with IDs
    6. Output structured JSON
    """
    start_time = datetime.now()
    pdf_path = str(pdf_path)

    try:
        nlp = get_nlp()
        registry = EntityRegistry(registry_path)

        # Extract text
        full_text, pages_data, redaction_stats = extract_text_with_positions(pdf_path)

        if not full_text.strip():
            return ExtractionResult(
                pdf_path=pdf_path,
                success=True,
                error=None,
                page_count=len(pages_data),
                total_words=0,
                total_chars=0,
                redaction_boxes_found=redaction_stats["total_boxes"],
                words_under_redactions=0,
                entities_found={},
                entity_ids=[],
                processing_time_sec=(datetime.now() - start_time).total_seconds()
            )

        # Run NER on full text (batch for efficiency)
        # Process in chunks to avoid memory issues
        max_chunk = 100000
        all_entities = []

        for i in range(0, len(full_text), max_chunk):
            chunk = full_text[i:i + max_chunk]
            doc = nlp(chunk)

            for ent in doc.ents:
                if ent.label_ == "PERSON":
                    context = get_context_window(chunk, ent.start_char, ent.end_char, 300)
                    all_entities.append({
                        "text": ent.text,
                        "start": i + ent.start_char,
                        "end": i + ent.end_char,
                        "context": context
                    })
                elif ent.label_ in ("ORG", "GPE", "LOC"):
                    all_entities.append({
                        "text": ent.text,
                        "start": i + ent.start_char,
                        "end": i + ent.end_char,
                        "label": ent.label_,
                        "context": ""
                    })

        # Classify persons and assign IDs
        protected_text = full_text
        entity_counts = {}
        entity_ids = []
        entity_records = []

        # Sort by position (reverse) to replace from end to start
        person_entities = [e for e in all_entities if "label" not in e]
        person_entities.sort(key=lambda x: x["start"], reverse=True)

        for ent in person_entities:
            classification = classify_person(
                ent["text"],
                ent["context"],
                full_text
            )

            # Get or create ID
            entity_id = registry.get_or_create_id(
                name=ent["text"],
                entity_type=classification.entity_type,
                doc_path=pdf_path,
                context=classification.reason
            )

            entity_ids.append(entity_id)
            entity_type_str = classification.entity_type.value
            entity_counts[entity_type_str] = entity_counts.get(entity_type_str, 0) + 1

            entity_records.append({
                "id": entity_id,
                "type": entity_type_str,
                "confidence": classification.confidence,
                "reason": classification.reason,
                "context": ent["context"][:200]
            })

            # Replace name with ID if protected
            if should_protect_name(classification):
                protected_text = (
                    protected_text[:ent["start"]] +
                    f"[{entity_id}]" +
                    protected_text[ent["end"]:]
                )

        # Handle non-person entities
        for ent in all_entities:
            if "label" in ent:
                etype = EntityType.ORGANIZATION if ent["label"] == "ORG" else EntityType.LOCATION
                entity_id = registry.get_or_create_id(
                    name=ent["text"],
                    entity_type=etype,
                    doc_path=pdf_path
                )
                entity_ids.append(entity_id)
                entity_type_str = etype.value
                entity_counts[entity_type_str] = entity_counts.get(entity_type_str, 0) + 1

        # Create output document
        output = DocumentOutput(
            source_path=pdf_path,
            source_hash=compute_file_hash(pdf_path),
            processed_at=datetime.now().isoformat(),
            page_count=len(pages_data),
            protected_text=protected_text,
            entities=entity_records,
            redaction_stats=redaction_stats,
            metadata={
                "filename": Path(pdf_path).name,
                "total_entities": len(entity_ids)
            }
        )

        # Save output
        output_path = Path(output_dir) / (Path(pdf_path).stem + ".json")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(asdict(output), f, indent=2)

        return ExtractionResult(
            pdf_path=pdf_path,
            success=True,
            error=None,
            page_count=len(pages_data),
            total_words=redaction_stats["total_words"],
            total_chars=len(full_text),
            redaction_boxes_found=redaction_stats["total_boxes"],
            words_under_redactions=redaction_stats["words_under_redactions"],
            entities_found=entity_counts,
            entity_ids=list(set(entity_ids)),
            processing_time_sec=(datetime.now() - start_time).total_seconds()
        )

    except Exception as e:
        return ExtractionResult(
            pdf_path=pdf_path,
            success=False,
            error=f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}",
            page_count=0,
            total_words=0,
            total_chars=0,
            redaction_boxes_found=0,
            words_under_redactions=0,
            entities_found={},
            entity_ids=[],
            processing_time_sec=(datetime.now() - start_time).total_seconds()
        )


def process_batch(
    pdf_paths: List[str],
    registry_path: str,
    output_dir: str,
    num_workers: int = None,
    progress_file: str = None
) -> List[ExtractionResult]:
    """
    Process multiple PDFs in parallel.

    Uses ProcessPoolExecutor for CPU parallelism.
    GPU NER is handled per-worker.
    """
    if num_workers is None:
        num_workers = max(1, mp.cpu_count() - 2)

    # Load progress if resuming
    completed = set()
    if progress_file and Path(progress_file).exists():
        with open(progress_file, 'r') as f:
            for line in f:
                completed.add(line.strip())
        print(f"Resuming: {len(completed)} already processed")

    # Filter out completed
    remaining = [p for p in pdf_paths if p not in completed]
    print(f"Processing {len(remaining)} PDFs with {num_workers} workers")

    results = []

    # Use ProcessPoolExecutor for parallel processing
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(process_single_pdf, pdf, registry_path, output_dir): pdf
            for pdf in remaining
        }

        with tqdm(total=len(remaining), desc="Processing PDFs") as pbar:
            for future in as_completed(futures):
                pdf_path = futures[future]
                try:
                    result = future.result()
                    results.append(result)

                    # Update progress file
                    if progress_file:
                        with open(progress_file, 'a') as f:
                            f.write(f"{pdf_path}\n")

                    # Update progress bar with stats
                    if result.success:
                        pbar.set_postfix({
                            "entities": sum(result.entities_found.values()),
                            "redactions": result.redaction_boxes_found
                        })

                except Exception as e:
                    print(f"Error processing {pdf_path}: {e}")

                pbar.update(1)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Batch process PDFs for entity extraction with victim protection"
    )
    parser.add_argument("input_dir", help="Directory containing PDFs (recursive)")
    parser.add_argument("-o", "--output-dir", default="./processed",
                        help="Output directory for JSON files")
    parser.add_argument("-r", "--registry", default="./entity_registry.db",
                        help="Path to entity registry database")
    parser.add_argument("-w", "--workers", type=int, default=None,
                        help="Number of worker processes")
    parser.add_argument("--progress", default="./progress.txt",
                        help="Progress file for resume capability")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit number of PDFs to process (for testing)")

    args = parser.parse_args()

    # Find all PDFs
    input_path = Path(args.input_dir)
    pdf_paths = list(input_path.rglob("*.pdf"))
    print(f"Found {len(pdf_paths)} PDFs in {input_path}")

    if args.limit:
        pdf_paths = pdf_paths[:args.limit]
        print(f"Limited to {args.limit} PDFs for testing")

    # Process
    results = process_batch(
        pdf_paths=[str(p) for p in pdf_paths],
        registry_path=args.registry,
        output_dir=args.output_dir,
        num_workers=args.workers,
        progress_file=args.progress
    )

    # Summary
    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    print(f"\n{'='*60}")
    print(f"PROCESSING COMPLETE")
    print(f"{'='*60}")
    print(f"Successful: {len(successful)}")
    print(f"Failed: {len(failed)}")

    if successful:
        total_entities = sum(sum(r.entities_found.values()) for r in successful)
        total_redactions = sum(r.redaction_boxes_found for r in successful)
        total_recovered = sum(r.words_under_redactions for r in successful)
        print(f"Total entities found: {total_entities}")
        print(f"Total redaction boxes: {total_redactions}")
        print(f"Words recovered from redactions: {total_recovered}")

    if failed:
        print(f"\nFailed files:")
        for r in failed[:10]:
            print(f"  {r.pdf_path}: {r.error[:100] if r.error else 'Unknown'}")


if __name__ == "__main__":
    main()
