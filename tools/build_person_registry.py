#!/usr/bin/env python3
"""
Build a unified person registry from three sources:
1. Epstein-Pipeline persons-registry.json (1,404 persons with aliases/categories)
2. la-rana-chicana-list CSV (241 names with descriptions/involvement)
3. knowledge_graph.db (489 persons with person_type, occupation, mention counts)

Output: persons_registry.json — single authoritative person registry
"""

import csv
import json
import os
import re
import sqlite3
from collections import defaultdict

def _find_data_dir():
    """Find the directory containing the database files."""
    if os.environ.get("EPSTEIN_DATA_DIR"):
        return os.environ["EPSTEIN_DATA_DIR"]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(repo_root, "knowledge_graph.db")):
        return repo_root
    if os.path.exists(os.path.join(os.getcwd(), "knowledge_graph.db")):
        return os.getcwd()
    parent = os.path.dirname(os.getcwd())
    for name in os.listdir(parent):
        candidate = os.path.join(parent, name, "knowledge_graph.db")
        if os.path.exists(candidate):
            return os.path.join(parent, name)
    return os.getcwd()

BASE_DIR = _find_data_dir()
PIPELINE_REGISTRY = os.path.join(BASE_DIR, "Epstein-Pipeline/data/persons-registry.json")
LRC_CSV = os.path.join(BASE_DIR, "la-rana-chicana-list_2-11-26_10am.csv")
KG_DB = os.path.join(BASE_DIR, "knowledge_graph.db")
OUTPUT = os.path.join(BASE_DIR, "persons_registry.json")


def normalize_name(name: str) -> str:
    """Normalize a name for dedup matching."""
    name = name.strip()
    # Handle "Last, First" format
    if ',' in name and name.count(',') == 1:
        parts = name.split(',')
        if len(parts[0].split()) == 1 and len(parts[1].strip().split()) <= 2:
            name = f"{parts[1].strip()} {parts[0].strip()}"
    return re.sub(r'\s+', ' ', name).strip()


def name_key(name: str) -> str:
    """Create a lowercase dedup key from a name."""
    n = normalize_name(name).lower()
    # Remove common suffixes/prefixes
    n = re.sub(r'\b(jr\.?|sr\.?|iii?|iv|esq\.?|md|phd|dr\.?)\b', '', n, flags=re.IGNORECASE)
    n = re.sub(r'[^a-z\s]', '', n)
    return re.sub(r'\s+', ' ', n).strip()


def slug_from_name(name: str) -> str:
    """Create a URL-friendly slug from a name."""
    s = name.lower().strip()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'\s+', '-', s)
    return s


def map_category(pipeline_cat: str = None, kg_type: str = None, lrc_desc: str = None) -> str:
    """Map various category labels to unified categories."""
    # Priority: most specific wins
    if kg_type in ('perpetrator', 'enabler', 'victim'):
        return kg_type
    if pipeline_cat:
        cat_map = {
            'associate': 'associate',
            'business': 'business',
            'celebrity': 'celebrity',
            'academic': 'academic',
            'politician': 'politician',
            'legal': 'legal',
            'socialite': 'socialite',
            'royalty': 'royalty',
            'military-intelligence': 'intelligence',
            'other': 'other',
        }
        return cat_map.get(pipeline_cat, pipeline_cat)
    if kg_type == 'associate':
        return 'associate'
    if lrc_desc:
        desc_lower = lrc_desc.lower()
        if any(w in desc_lower for w in ['attorney', 'lawyer', 'judge', 'solicitor']):
            return 'legal'
        if any(w in desc_lower for w in ['senator', 'representative', 'governor', 'president', 'minister', 'politician', 'delegate']):
            return 'politician'
        if any(w in desc_lower for w in ['professor', 'scientist', 'researcher', 'academic']):
            return 'academic'
        if any(w in desc_lower for w in ['actor', 'singer', 'musician', 'magician', 'comedian', 'director', 'rapper']):
            return 'celebrity'
        if any(w in desc_lower for w in ['housekeeper', 'butler', 'chauffeur', 'landscaper', 'pilot', 'captain', 'driver', 'painter', 'employee', 'staff', 'manager', 'assistant', 'operator']):
            return 'staff'
        if any(w in desc_lower for w in ['billionaire', 'businessman', 'investor', 'financier', 'ceo', 'founder', 'banker', 'executive']):
            return 'business'
    if kg_type == 'mentioned':
        return 'mentioned'
    return 'other'


def main():
    # Collect all persons keyed by name_key
    registry = {}  # key -> person dict
    key_to_names = defaultdict(set)  # track all original names per key

    # =========================================================================
    # Source 1: Epstein-Pipeline registry
    # =========================================================================
    with open(PIPELINE_REGISTRY) as f:
        pipeline = json.load(f)

    print(f"Pipeline registry: {len(pipeline)} persons")

    for p in pipeline:
        name = normalize_name(p['name'])
        key = name_key(name)
        if not key:
            continue

        key_to_names[key].add(name)

        if key not in registry:
            registry[key] = {
                'name': name,
                'slug': p.get('slug', slug_from_name(name)),
                'aliases': [],
                'category': '',
                'description': '',
                'involvement': '',
                'occupation': '',
                'ocr_variants': [],
                'search_terms': [],
                'sources': [],
                'metadata': {},
            }

        entry = registry[key]
        entry['sources'].append('epstein-pipeline')
        entry['category'] = entry['category'] or map_category(pipeline_cat=p.get('category'))

        # Add aliases
        for alias in p.get('aliases', []):
            alias = alias.strip()
            if alias and alias.lower() != name.lower() and alias not in entry['aliases']:
                entry['aliases'].append(alias)

    print(f"  After pipeline: {len(registry)} unique persons")

    # =========================================================================
    # Source 2: La Rana Chicana list
    # =========================================================================
    lrc_count = 0
    lrc_new = 0
    with open(LRC_CSV, 'r', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            last = (row.get('LAST') or '').strip()
            first = (row.get('FIRST') or '').strip()
            desc = (row.get('DESCRIPTION') or '').strip()
            involve = (row.get('INVOLVEMENT') or '').strip()

            if not last and not first:
                continue

            name = f"{first} {last}".strip()
            key = name_key(name)
            if not key:
                continue

            lrc_count += 1
            key_to_names[key].add(name)

            if key not in registry:
                lrc_new += 1
                registry[key] = {
                    'name': name,
                    'slug': slug_from_name(name),
                    'aliases': [],
                    'category': '',
                    'description': '',
                    'involvement': '',
                    'occupation': '',
                    'ocr_variants': [],
                    'search_terms': [],
                    'sources': [],
                    'metadata': {},
                }

            entry = registry[key]
            entry['sources'].append('la-rana-chicana')

            # LRC has richer descriptions — prefer them
            if desc and (not entry['description'] or len(desc) > len(entry['description'])):
                entry['description'] = desc
            if involve and (not entry['involvement'] or len(involve) > len(entry['involvement'])):
                entry['involvement'] = involve

            # Update category from description if we don't have one yet
            if not entry['category'] or entry['category'] in ('other', 'mentioned'):
                new_cat = map_category(lrc_desc=desc)
                if new_cat != 'other':
                    entry['category'] = new_cat

    print(f"  La Rana Chicana: {lrc_count} names, {lrc_new} new")
    print(f"  After LRC merge: {len(registry)} unique persons")

    # =========================================================================
    # Source 3: Knowledge Graph DB
    # =========================================================================
    kg_new = 0
    if os.path.exists(KG_DB):
        conn = sqlite3.connect(KG_DB)
        cur = conn.cursor()
        cur.execute('SELECT name, aliases, metadata FROM entities WHERE entity_type="person"')

        for name_raw, aliases_raw, meta_raw in cur.fetchall():
            name = normalize_name(name_raw)
            # Fix "Marcinkova, Nadia Nadia" type issues
            name = re.sub(r'(\w+),\s*(\w+)\s+\1', r'\2 \1', name)

            key = name_key(name)
            if not key:
                continue

            meta = json.loads(meta_raw) if meta_raw else {}
            kg_aliases = json.loads(aliases_raw) if aliases_raw else []

            key_to_names[key].add(name)

            if key not in registry:
                kg_new += 1
                registry[key] = {
                    'name': name,
                    'slug': slug_from_name(name),
                    'aliases': [],
                    'category': '',
                    'description': '',
                    'involvement': '',
                    'occupation': '',
                    'ocr_variants': [],
                    'search_terms': [],
                    'sources': [],
                    'metadata': {},
                }

            entry = registry[key]
            entry['sources'].append('knowledge-graph')

            # KG has occupation and person_type
            if meta.get('occupation') and not entry['occupation']:
                entry['occupation'] = meta['occupation']
            if not entry['category'] or entry['category'] in ('other', 'mentioned'):
                new_cat = map_category(kg_type=meta.get('person_type'))
                if new_cat not in ('other', 'mentioned'):
                    entry['category'] = new_cat
            elif meta.get('person_type') in ('perpetrator', 'enabler', 'victim'):
                entry['category'] = meta['person_type']

            # DS10 mention count
            if meta.get('ds10_mention_count'):
                entry['metadata']['ds10_mentions'] = meta['ds10_mention_count']
            if meta.get('ds10_detail'):
                entry['metadata']['ds10_detail'] = meta['ds10_detail']

            # KG aliases
            for alias in kg_aliases:
                alias = alias.strip()
                if alias and alias.lower() != name.lower() and alias not in entry['aliases']:
                    entry['aliases'].append(alias)

        conn.close()

    print(f"  Knowledge Graph: {kg_new} new persons")
    print(f"  After KG merge: {len(registry)} unique persons")

    # =========================================================================
    # Post-processing: cleanup junk entries, generate search terms
    # =========================================================================

    # Common words that are NOT person names (even if they appear as entity names)
    COMMON_WORDS = {
        'will', 'david', 'paul', 'john', 'mark', 'michael', 'tom', 'max',
        'sarah', 'amanda', 'anna', 'nicole', 'karen', 'mary', 'amy', 'eve',
        'sam', 'nick', 'dan', 'lee', 'eric', 'hall', 'bob', 'joe', 'tim',
        'kathy', 'natasha', 'james', 'daniel', 'patrick', 'friend', 'lt.',
        'mr.', 'ms.', 'mrs.', 'dr.', 'epstein', 'maxwell', 'black', 'groff',
        'kahn', 'dubin', 'staley', 'brunel', 'indyke', 'andrew', 'nathan',
        'dave', 'steve', 'alex', 'larry', 'charles', 'robert', 'steven',
        'lisa', 'stephen', 'ian', 'fran', 'darren', 'jean', 'government',
        'unknown', 'victim', 'witness', 'defendant', 'court',
    }

    # Last names too common to use as search terms (match thousands of unrelated docs)
    COMMON_LASTNAMES = {
        'ross', 'james', 'black', 'walker', 'francis', 'miller', 'thomas',
        'harris', 'lloyd', 'martin', 'klein', 'khan', 'lang', 'perry',
        'band', 'marsh', 'page', 'starr', 'stern', 'bush', 'carter',
        'leach', 'church', 'gates', 'allen', 'tucker', 'smith', 'brown',
        'jones', 'white', 'king', 'hill', 'scott', 'green', 'baker',
        'hall', 'young', 'lee', 'wright', 'lopez', 'moore', 'clark',
        'stone', 'stewart', 'cohen', 'mitchell', 'gordon', 'field',
        'richards', 'richard', 'george', 'andrew', 'david', 'jean',
        'love', 'long', 'cross', 'edge', 'english', 'back', 'rich',
        'roth', 'paul', 'morris', 'barrett', 'driver', 'cook', 'kelly',
        'ward', 'said', 'park', 'paris', 'york', 'bank', 'check',
        'case', 'office', 'company', 'states', 'united', 'holding',
        'manager', 'attorney', 'officer', 'director', 'counsel',
        'staff', 'assistant', 'flight', 'charge', 'count', 'members',
        'agents', 'judge', 'dead', 'breakfast', 'individual', 'owner',
        'brother', 'specified', 'mentioned',
    }

    to_remove = []
    for key, entry in registry.items():
        name = entry['name']
        name_lower = name.lower().strip()

        # Remove non-person entries
        remove = False

        # Single word that's a common first name or word
        if ' ' not in name.strip() and name_lower in COMMON_WORDS:
            remove = True
        # Single word less than 4 chars (initials, abbreviations)
        elif ' ' not in name.strip() and len(name.strip()) < 4:
            remove = True
        # Contains geographic indicators (flight routes from Pipeline)
        elif any(x in name for x in [', FL', ', NJ', ', NY', ', GA', ', CA', ', OH',
                                       ', IN', ', WA', ', NM', 'United States',
                                       'Charlotte Amalie', 'St. Thomas,']):
            remove = True
        # Starts with generic articles/descriptions
        elif any(name_lower.startswith(x) for x in [
            'the ', 'a ', 'various ', 'unknown', 'none ', 'not ',
            'unnamed ', 'undisclosed', 'parties ', 'attorneys ',
            'defendant', 'crew ', 'passengers', 'fbi ', 'officer ',
        ]):
            remove = True
        # Contains garbled characters (OCR artifacts)
        elif any(c in name for c in ['|', '<', '>', '[', ']', '5|']):
            remove = True
        # Role descriptions without real names
        elif any(x in name_lower for x in [
            'control center', 'staff attorney', 'staff member',
            'estate manager', 'unit manager', 'associate warden',
            'regional director', 'supervisory staff', 'correctional',
            'performing check', 'signing off', 'explicitly mentioned',
            'not specified', 'redacted', 'actively working',
            'involved in the', 'wake up the dead', 'eating emo',
        ]):
            remove = True
        # Entries that are just a question mark suffix (from Pipeline's "First ?" format)
        elif name.endswith(' ?') and len(name.split()) == 2:
            remove = True
        # Entries with parenthetical roles that aren't real people
        elif '(' in name and any(x in name for x in [
            '(Pilot)', '(Institutional)', '(Attorney General)',
            '(PA)', '(Judge)', '(Metropolitan',
        ]):
            remove = True

        if remove:
            to_remove.append(key)

    for key in to_remove:
        del registry[key]

    print(f"\n  Removed {len(to_remove)} junk entries")
    print(f"  After cleanup: {len(registry)} persons")

    # Now generate search terms for clean entries
    for key, entry in registry.items():
        if not entry['category']:
            entry['category'] = 'other'

        terms = set()

        # Full name is always a search term
        terms.add(entry['name'])

        # Last name only if distinctive
        parts = entry['name'].split()
        if len(parts) >= 2:
            last = parts[-1]
            if len(last) > 3 and last.lower() not in COMMON_LASTNAMES:
                terms.add(last)

        # Aliases
        for alias in entry['aliases']:
            if len(alias) > 2:
                terms.add(alias)

        entry['search_terms'] = sorted(terms)
        entry['sources'] = sorted(set(entry['sources']))

        # Remove empty fields
        for field in ['ocr_variants', 'involvement', 'description', 'occupation', 'metadata']:
            if field in entry and not entry[field]:
                del entry[field]

    # =========================================================================
    # Output
    # =========================================================================
    output = sorted(registry.values(), key=lambda x: x['name'].lower())

    with open(OUTPUT, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"UNIFIED REGISTRY: {len(output)} persons")
    print(f"Output: {OUTPUT}")
    print(f"{'='*60}")

    # Stats
    cats = defaultdict(int)
    has_alias = 0
    has_desc = 0
    total_aliases = 0
    multi_source = 0
    for p in output:
        cats[p['category']] += 1
        if p['aliases']:
            has_alias += 1
            total_aliases += len(p['aliases'])
        if p.get('description'):
            has_desc += 1
        if len(p['sources']) > 1:
            multi_source += 1

    print(f"\nCategories:")
    for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")
    print(f"\n{has_alias} persons have aliases ({total_aliases} total)")
    print(f"{has_desc} persons have descriptions")
    print(f"{multi_source} persons appear in 2+ sources")

    # Show some multi-source entries
    print(f"\nSample multi-source persons:")
    ms = [p for p in output if len(p['sources']) > 1]
    for p in ms[:10]:
        print(f"  {p['name']} [{p['category']}] — sources: {p['sources']}, aliases: {p['aliases']}")


if __name__ == '__main__':
    main()
