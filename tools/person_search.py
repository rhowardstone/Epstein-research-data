#!/usr/bin/env python3
"""
Fast person cross-reference search against the full text corpus.

Uses FTS5 for speed, the unified persons_registry.json for names/aliases,
and supports co-occurrence detection (two people in the same document).

Usage:
    python3 tools/person_search.py                          # all persons, hit counts
    python3 tools/person_search.py --top 50                 # top 50 by hits
    python3 tools/person_search.py --name "Leon Black"      # single person deep search
    python3 tools/person_search.py --cooccur "Leon Black"   # who appears with Leon Black
    python3 tools/person_search.py --category business      # only business category
    python3 tools/person_search.py --min-hits 10            # only persons with 10+ doc hits
    python3 tools/person_search.py --output results.csv     # export to CSV
"""

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

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

BASE_DIR = _find_data_dir()
REGISTRY_PATH = os.path.join(BASE_DIR, "persons_registry.json")
CORPUS_DB = os.path.join(BASE_DIR, "full_text_corpus.db")
TRANSCRIPTS_DB = os.path.join(BASE_DIR, "transcripts.db")


def load_registry(category=None):
    """Load persons registry, optionally filtered by category."""
    with open(REGISTRY_PATH) as f:
        persons = json.load(f)
    if category:
        persons = [p for p in persons if p['category'] == category]
    return persons


def fts_escape(term):
    """Escape a term for FTS5 query."""
    # FTS5 uses double quotes for phrase matching
    # Remove any existing quotes and special chars
    term = term.replace('"', '').replace("'", "")
    return f'"{term}"'


def search_person_corpus(args):
    """Search for a single person across the corpus. Worker function."""
    person, db_path = args
    name = person['name']
    search_terms = person.get('search_terms', [name])

    results = {
        'name': name,
        'slug': person.get('slug', ''),
        'category': person.get('category', ''),
        'fts_hits': 0,
        'like_hits': 0,
        'total_docs': 0,
        'efta_numbers': set(),
        'search_term_hits': {},
    }

    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=30)
        cur = conn.cursor()

        all_eftas = set()

        for term in search_terms:
            if len(term) < 3:
                continue

            term_eftas = set()

            # Try FTS5 first (fast)
            try:
                escaped = fts_escape(term)
                cur.execute(
                    "SELECT DISTINCT efta_number FROM pages_fts WHERE pages_fts MATCH ?",
                    (escaped,)
                )
                for row in cur.fetchall():
                    term_eftas.add(row[0])
            except Exception:
                pass

            # If FTS found nothing and term is a full name, try LIKE as fallback
            # (FTS can miss OCR-mangled text that LIKE catches with substring matching)
            if not term_eftas and ' ' in term:
                try:
                    cur.execute(
                        "SELECT DISTINCT efta_number FROM pages "
                        "WHERE text_content LIKE ? LIMIT 500",
                        (f"%{term}%",)
                    )
                    for row in cur.fetchall():
                        term_eftas.add(row[0])
                    if term_eftas:
                        results['like_hits'] += len(term_eftas)
                except Exception:
                    pass

            results['search_term_hits'][term] = len(term_eftas)
            all_eftas.update(term_eftas)

        results['total_docs'] = len(all_eftas)
        results['efta_numbers'] = all_eftas
        results['fts_hits'] = results['total_docs'] - results['like_hits']

        conn.close()
    except Exception as e:
        results['error'] = str(e)

    return results


def search_person_transcripts(args):
    """Search for a person in transcripts DB."""
    person, db_path = args
    name = person['name']
    search_terms = person.get('search_terms', [name])

    hits = set()
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=30)
        cur = conn.cursor()

        for term in search_terms:
            if len(term) < 3:
                continue
            try:
                escaped = fts_escape(term)
                cur.execute(
                    "SELECT DISTINCT efta_number FROM transcripts_fts WHERE transcripts_fts MATCH ?",
                    (escaped,)
                )
                for row in cur.fetchall():
                    hits.add(row[0])
            except Exception:
                pass

        conn.close()
    except Exception:
        pass

    return person['name'], hits


def find_cooccurrences(target_name, registry, db_path, target_eftas=None):
    """Find persons who appear in the same documents as target_name."""
    if target_eftas is None:
        # First find target's documents
        target = None
        for p in registry:
            if p['name'].lower() == target_name.lower():
                target = p
                break
        if not target:
            print(f"Person not found: {target_name}")
            return {}

        result = search_person_corpus((target, db_path))
        target_eftas = result['efta_numbers']

    if not target_eftas:
        print(f"No documents found for {target_name}")
        return {}

    print(f"\n{target_name}: {len(target_eftas)} documents")
    print(f"Searching for co-occurrences with {len(registry)} persons...")

    cooccur = {}
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=30)
    cur = conn.cursor()

    # Build a temp table of target's EFTAs for fast joins
    conn.execute("CREATE TEMP TABLE target_eftas (efta TEXT PRIMARY KEY)")
    conn.executemany("INSERT INTO target_eftas VALUES (?)", [(e,) for e in target_eftas])

    for person in registry:
        if person['name'].lower() == target_name.lower():
            continue

        person_eftas = set()
        for term in person.get('search_terms', [person['name']]):
            if len(term) < 3:
                continue
            try:
                escaped = fts_escape(term)
                cur.execute("""
                    SELECT DISTINCT p.efta_number
                    FROM pages_fts p
                    INNER JOIN target_eftas t ON p.efta_number = t.efta
                    WHERE pages_fts MATCH ?
                """, (escaped,))
                for row in cur.fetchall():
                    person_eftas.add(row[0])
            except Exception:
                pass

        if person_eftas:
            cooccur[person['name']] = {
                'count': len(person_eftas),
                'category': person.get('category', ''),
                'eftas': sorted(person_eftas)[:10],  # sample
            }

    conn.close()
    return cooccur


def deep_search_person(name, registry, db_path):
    """Deep search: find all documents mentioning a person, with context."""
    target = None
    for p in registry:
        if p['name'].lower() == name.lower():
            target = p
            break

    if not target:
        # Try partial match
        for p in registry:
            if name.lower() in p['name'].lower():
                target = p
                break

    if not target:
        print(f"Person not found in registry: {name}")
        return

    print(f"\n{'='*70}")
    print(f"DEEP SEARCH: {target['name']}")
    print(f"Category: {target['category']}")
    print(f"Aliases: {target['aliases']}")
    if target.get('description'):
        print(f"Description: {target['description'][:200]}")
    if target.get('involvement'):
        print(f"Involvement: {target['involvement'][:200]}")
    print(f"Search terms: {target['search_terms']}")
    print(f"{'='*70}")

    # Search corpus
    result = search_person_corpus((target, db_path))
    print(f"\nTotal documents: {result['total_docs']}")
    print(f"  FTS hits: {result['fts_hits']}")
    print(f"  LIKE fallback hits: {result['like_hits']}")

    print(f"\nHits by search term:")
    for term, count in sorted(result['search_term_hits'].items(), key=lambda x: -x[1]):
        print(f"  '{term}': {count} docs")

    # Show sample documents with context
    if result['efta_numbers']:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=30)
        cur = conn.cursor()

        print(f"\nSample documents (first 20):")
        for efta in sorted(result['efta_numbers'])[:20]:
            # Get a snippet
            for term in [target['name']] + target['aliases']:
                cur.execute("""
                    SELECT page_number, substr(text_content,
                        MAX(1, instr(lower(text_content), lower(?)) - 80), 200)
                    FROM pages
                    WHERE efta_number = ? AND text_content LIKE ?
                    LIMIT 1
                """, (term, efta, f"%{term}%"))
                row = cur.fetchone()
                if row:
                    snippet = row[1].replace('\n', ' ').strip()
                    print(f"  {efta} p{row[0]}: ...{snippet}...")
                    break
            else:
                print(f"  {efta}")

        conn.close()

    return result


def main():
    parser = argparse.ArgumentParser(description="Person cross-reference search")
    parser.add_argument('--name', type=str, help="Deep search for a specific person")
    parser.add_argument('--cooccur', type=str, help="Find co-occurrences with this person")
    parser.add_argument('--category', type=str, help="Filter by category")
    parser.add_argument('--top', type=int, default=0, help="Show top N by hits")
    parser.add_argument('--min-hits', type=int, default=0, help="Minimum document hits")
    parser.add_argument('--output', type=str, help="Export results to CSV")
    parser.add_argument('--workers', type=int, default=32, help="Parallel workers")
    parser.add_argument('--include-transcripts', action='store_true', help="Also search transcripts DB")
    args = parser.parse_args()

    # Load registry
    registry = load_registry(category=args.category)
    print(f"Loaded {len(registry)} persons from registry")
    if args.category:
        print(f"  (filtered to category: {args.category})")

    # Single person deep search
    if args.name:
        deep_search_person(args.name, registry, CORPUS_DB)
        return

    # Co-occurrence search
    if args.cooccur:
        cooccur = find_cooccurrences(args.cooccur, registry, CORPUS_DB)
        if cooccur:
            print(f"\nCo-occurrences with {args.cooccur} ({len(cooccur)} persons):\n")
            for name, data in sorted(cooccur.items(), key=lambda x: -x[1]['count']):
                efta_sample = ', '.join(data['eftas'][:3])
                print(f"  {name:<40} [{data['category']:<12}] {data['count']:>5} docs  ({efta_sample})")
        return

    # Full cross-reference scan
    print(f"\nSearching {len(registry)} persons across {CORPUS_DB}...")
    t0 = time.time()

    # Parallel search
    tasks = [(p, CORPUS_DB) for p in registry]
    all_results = []
    done = 0
    workers = min(args.workers, len(registry))

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(search_person_corpus, t): t for t in tasks}
        for future in as_completed(futures):
            result = future.result()
            all_results.append(result)
            done += 1
            if done % 100 == 0:
                elapsed = time.time() - t0
                print(f"  [{done}/{len(registry)}] {elapsed:.1f}s")

    elapsed = time.time() - t0
    print(f"\nSearch complete: {elapsed:.1f}s ({len(registry)/elapsed:.0f} persons/sec)")

    # Also search transcripts if requested
    transcript_hits = {}
    if args.include_transcripts and os.path.exists(TRANSCRIPTS_DB):
        print(f"\nAlso searching transcripts DB...")
        t_tasks = [(p, TRANSCRIPTS_DB) for p in registry]
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(search_person_transcripts, t): t for t in t_tasks}
            for future in as_completed(futures):
                name, hits = future.result()
                if hits:
                    transcript_hits[name] = hits

    # Sort by total hits
    all_results.sort(key=lambda x: -x['total_docs'])

    # Filter
    if args.min_hits:
        all_results = [r for r in all_results if r['total_docs'] >= args.min_hits]

    if args.top:
        all_results = all_results[:args.top]

    # Display
    print(f"\n{'='*100}")
    print(f"{'NAME':<40} {'CAT':<14} {'DOCS':>7} {'FTS':>7} {'LIKE':>7}  TOP TERMS")
    print(f"{'='*100}")

    for r in all_results:
        if r['total_docs'] == 0 and not args.min_hits:
            continue

        top_terms = sorted(r['search_term_hits'].items(), key=lambda x: -x[1])[:3]
        terms_str = ', '.join(f"{t}:{c}" for t, c in top_terms if c > 0)

        tr_str = ""
        if r['name'] in transcript_hits:
            tr_str = f" +{len(transcript_hits[r['name']])}tr"

        print(f"  {r['name']:<38} [{r['category']:<12}] {r['total_docs']:>6} {r['fts_hits']:>6} {r['like_hits']:>6}  {terms_str}{tr_str}")

    # Summary stats
    with_hits = [r for r in all_results if r['total_docs'] > 0]
    high = [r for r in all_results if r['total_docs'] >= 100]
    med = [r for r in all_results if 10 <= r['total_docs'] < 100]
    low = [r for r in all_results if 1 <= r['total_docs'] < 10]
    zero = [r for r in all_results if r['total_docs'] == 0]

    print(f"\n{'='*60}")
    print(f"  TOTAL persons searched:  {len(all_results)}")
    print(f"  HIGH (100+ docs):        {len(high)}")
    print(f"  MEDIUM (10-99 docs):     {len(med)}")
    print(f"  LOW (1-9 docs):          {len(low)}")
    print(f"  ZERO hits:               {len(zero)}")
    print(f"{'='*60}")

    # Export CSV
    if args.output:
        with open(args.output, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['name', 'slug', 'category', 'total_docs', 'fts_hits',
                           'like_hits', 'aliases', 'search_terms', 'sample_eftas'])
            for r in all_results:
                person = next((p for p in registry if p['name'] == r['name']), {})
                writer.writerow([
                    r['name'],
                    r['slug'],
                    r['category'],
                    r['total_docs'],
                    r['fts_hits'],
                    r['like_hits'],
                    '; '.join(person.get('aliases', [])),
                    '; '.join(person.get('search_terms', [])),
                    '; '.join(sorted(r['efta_numbers'])[:10]),
                ])
        print(f"\nExported to {args.output}")


if __name__ == '__main__':
    main()
