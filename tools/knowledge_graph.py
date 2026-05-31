#!/usr/bin/env python3
"""Build knowledge graph from extracted entities"""
import sqlite3
import json
from pathlib import Path
from collections import defaultdict

import os

def _find_data_dir():
    """Find the directory containing the database files."""
    if os.environ.get("EPSTEIN_DATA_DIR"):
        return os.environ["EPSTEIN_DATA_DIR"]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(repo_root, "evidence_findings.jsonl")):
        return repo_root
    if os.path.exists(os.path.join(os.getcwd(), "evidence_findings.jsonl")):
        return os.getcwd()
    parent = os.path.dirname(os.getcwd())
    for name in os.listdir(parent):
        candidate = os.path.join(parent, name, "evidence_findings.jsonl")
        if os.path.exists(candidate):
            return os.path.join(parent, name)
    return os.getcwd()

_DATA_DIR = _find_data_dir()
FINDINGS_PATH = os.path.join(_DATA_DIR, "evidence_findings.jsonl")
CROSSREF_PATH = os.path.join(_DATA_DIR, "name_crossref.jsonl")
OUTPUT_DB = os.path.join(_DATA_DIR, "knowledge_graph.db")
OUTPUT_JSON = os.path.join(_DATA_DIR, "knowledge_graph.json")

def init_db():
    conn = sqlite3.connect(OUTPUT_DB)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS entities (
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE,
        entity_type TEXT,
        aliases TEXT,
        metadata TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS relationships (
        id INTEGER PRIMARY KEY,
        source_entity TEXT,
        target_entity TEXT,
        relationship_type TEXT,
        document_source TEXT,
        metadata TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS document_mentions (
        id INTEGER PRIMARY KEY,
        entity_name TEXT,
        document_id TEXT,
        context TEXT
    )''')
    
    c.execute('CREATE INDEX IF NOT EXISTS idx_entity_name ON entities(name)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_entity)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_entity)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_mention_entity ON document_mentions(entity_name)')
    
    conn.commit()
    return conn

def extract_entities_from_findings():
    """Extract all entities from evidence findings"""
    entities = {}
    relationships = []
    
    with open(FINDINGS_PATH, 'r') as f:
        for line in f:
            try:
                finding = json.loads(line)
                
                # Extract entities based on entity_type
                etype = finding.get('entity_type', '')
                ename = finding.get('entity_name', '')
                data = finding.get('data', {})
                source = finding.get('source_image', '')
                
                # People
                if isinstance(data, dict):
                    for key, val in data.items():
                        if isinstance(val, str) and len(val) > 2:
                            if any(x in key.lower() for x in ['name', 'manager', 'attorney', 'accountant', 'pilot']):
                                entities[val] = {'type': 'person', 'role': key, 'source': source}
                        elif isinstance(val, list):
                            for item in val:
                                if isinstance(item, str):
                                    entities[item] = {'type': 'person', 'source': source}
                                elif isinstance(item, dict) and 'name' in item:
                                    entities[item['name']] = {'type': 'person', 'metadata': item, 'source': source}
                
                # Properties
                if 'properties' in str(data):
                    props = data.get('properties', [])
                    for prop in props:
                        if isinstance(prop, dict):
                            addr = prop.get('address', '')
                            name = prop.get('name', addr)
                            entities[name] = {'type': 'property', 'metadata': prop}
                
                # Organizations
                for org in ['Southern Trust', 'HBRK', 'LSJE', 'TWA']:
                    if org.lower() in json.dumps(finding).lower():
                        entities[org] = {'type': 'organization'}
                
            except Exception as e:
                continue
    
    return entities, relationships

def build_graph():
    print("Building knowledge graph...")
    conn = init_db()
    c = conn.cursor()
    
    entities, relationships = extract_entities_from_findings()
    print(f"Extracted {len(entities)} entities")
    
    # Insert entities
    for name, meta in entities.items():
        try:
            c.execute("""INSERT OR REPLACE INTO entities (name, entity_type, metadata)
                        VALUES (?, ?, ?)""",
                     (name, meta.get('type', 'unknown'), json.dumps(meta)))
        except:
            pass
    
    # Load cross-references if available
    if Path(CROSSREF_PATH).exists():
        print("Loading cross-references...")
        with open(CROSSREF_PATH, 'r') as f:
            for line in f:
                try:
                    ref = json.loads(line)
                    c.execute("""INSERT INTO document_mentions (entity_name, document_id, context)
                                VALUES (?, ?, ?)""",
                             (ref['name'], ref['efta'], ref.get('context', '')))
                except:
                    pass
    
    conn.commit()
    
    # Generate summary
    c.execute("SELECT entity_type, COUNT(*) FROM entities GROUP BY entity_type")
    print("\nEntity counts by type:")
    for etype, count in c.fetchall():
        print(f"  {etype}: {count}")
    
    c.execute("SELECT COUNT(*) FROM document_mentions")
    mentions = c.fetchone()[0]
    print(f"\nDocument mentions: {mentions}")
    
    # Export to JSON for visualization
    graph = {'nodes': [], 'edges': []}
    
    c.execute("SELECT name, entity_type, metadata FROM entities")
    for name, etype, meta in c.fetchall():
        graph['nodes'].append({
            'id': name,
            'type': etype,
            'metadata': json.loads(meta) if meta else {}
        })
    
    c.execute("""SELECT entity_name, document_id, COUNT(*) as mentions 
                FROM document_mentions GROUP BY entity_name, document_id""")
    for entity, doc, count in c.fetchall():
        graph['edges'].append({
            'source': entity,
            'target': doc,
            'type': 'mentioned_in',
            'weight': count
        })
    
    with open(OUTPUT_JSON, 'w') as f:
        json.dump(graph, f, indent=2)
    
    print(f"\nKnowledge graph saved to:")
    print(f"  {OUTPUT_DB}")
    print(f"  {OUTPUT_JSON}")
    
    conn.close()

if __name__ == "__main__":
    build_graph()
