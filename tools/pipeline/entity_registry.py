"""
Secure Entity Registry with Pseudonymous IDs

Maintains consistent victim/entity ID mappings across documents.
Names are never stored in plaintext - only salted hashes.
"""
import hashlib
import json
import sqlite3
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Set
from enum import Enum
import threading

class EntityType(Enum):
    VICTIM = "VICTIM"
    PERPETRATOR = "PERP"
    ASSOCIATE = "ASSOC"
    WITNESS = "WITNESS"
    ORGANIZATION = "ORG"
    LOCATION = "LOC"
    UNKNOWN_PERSON = "PERSON"

@dataclass
class EntityRecord:
    entity_id: str           # e.g., "VICTIM-001"
    entity_type: EntityType
    name_hash: str           # SHA-256 of salted name (never stores plaintext)
    document_count: int      # How many documents mention this entity
    first_seen_doc: str      # First document where entity appeared
    context_hints: str       # Non-identifying context (e.g., "mentioned as minor")

class EntityRegistry:
    """
    Thread-safe registry for pseudonymous entity tracking.

    The actual names are NEVER stored - only salted hashes.
    This allows consistent ID assignment without exposing identities.
    """

    def __init__(self, db_path: str, salt: Optional[str] = None):
        self.db_path = Path(db_path)
        # Salt should be kept secure - makes rainbow table attacks infeasible
        self._salt = salt or self._generate_salt()
        self._lock = threading.Lock()
        self._init_db()

        # In-memory cache for fast lookups
        self._hash_to_id: Dict[str, str] = {}
        self._type_counters: Dict[EntityType, int] = {}
        self._load_cache()

    def _generate_salt(self) -> str:
        """Generate a random salt for hashing."""
        import secrets
        return secrets.token_hex(32)

    def _hash_name(self, name: str) -> str:
        """Create salted hash of name - irreversible."""
        normalized = name.strip().lower()
        salted = f"{self._salt}:{normalized}"
        return hashlib.sha256(salted.encode()).hexdigest()

    def _init_db(self):
        """Initialize SQLite database."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entities (
                    entity_id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    name_hash TEXT UNIQUE NOT NULL,
                    document_count INTEGER DEFAULT 1,
                    first_seen_doc TEXT,
                    context_hints TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS document_entities (
                    doc_path TEXT,
                    entity_id TEXT,
                    mention_count INTEGER DEFAULT 1,
                    page_numbers TEXT,
                    PRIMARY KEY (doc_path, entity_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            # Store salt (encrypted in production, plaintext for now)
            conn.execute(
                "INSERT OR IGNORE INTO metadata (key, value) VALUES (?, ?)",
                ("salt", self._salt)
            )
            conn.commit()

    def _load_cache(self):
        """Load existing mappings into memory."""
        with sqlite3.connect(self.db_path) as conn:
            # Load hash -> ID mappings
            for row in conn.execute("SELECT name_hash, entity_id, entity_type FROM entities"):
                self._hash_to_id[row[0]] = row[1]

            # Load counters
            for etype in EntityType:
                result = conn.execute(
                    "SELECT COUNT(*) FROM entities WHERE entity_type = ?",
                    (etype.value,)
                ).fetchone()
                self._type_counters[etype] = result[0] if result else 0

    def get_or_create_id(
        self,
        name: str,
        entity_type: EntityType,
        doc_path: str = "",
        context: str = ""
    ) -> str:
        """
        Get existing ID for a name, or create new one.

        Returns pseudonymous ID like "VICTIM-001" or "PERP-003".
        The actual name is NEVER stored or returned.
        """
        name_hash = self._hash_name(name)

        with self._lock:
            # Check cache first
            if name_hash in self._hash_to_id:
                entity_id = self._hash_to_id[name_hash]
                self._increment_doc_count(entity_id, doc_path)
                return entity_id

            # Create new ID
            self._type_counters[entity_type] = self._type_counters.get(entity_type, 0) + 1
            count = self._type_counters[entity_type]
            entity_id = f"{entity_type.value}-{count:04d}"

            # Store in database
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO entities (entity_id, entity_type, name_hash, first_seen_doc, context_hints)
                    VALUES (?, ?, ?, ?, ?)
                """, (entity_id, entity_type.value, name_hash, doc_path, context))
                conn.commit()

            # Update cache
            self._hash_to_id[name_hash] = entity_id

            return entity_id

    def _increment_doc_count(self, entity_id: str, doc_path: str):
        """Increment document count for an entity."""
        if not doc_path:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE entities SET document_count = document_count + 1 WHERE entity_id = ?",
                (entity_id,)
            )
            conn.execute("""
                INSERT INTO document_entities (doc_path, entity_id, mention_count)
                VALUES (?, ?, 1)
                ON CONFLICT(doc_path, entity_id) DO UPDATE SET mention_count = mention_count + 1
            """, (doc_path, entity_id))
            conn.commit()

    def record_mention(self, entity_id: str, doc_path: str, page_num: int):
        """Record a specific mention of an entity in a document."""
        with sqlite3.connect(self.db_path) as conn:
            # Get existing page numbers
            result = conn.execute(
                "SELECT page_numbers FROM document_entities WHERE doc_path = ? AND entity_id = ?",
                (doc_path, entity_id)
            ).fetchone()

            if result and result[0]:
                pages = set(json.loads(result[0]))
                pages.add(page_num)
            else:
                pages = {page_num}

            conn.execute("""
                INSERT INTO document_entities (doc_path, entity_id, page_numbers, mention_count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(doc_path, entity_id) DO UPDATE SET
                    page_numbers = ?,
                    mention_count = mention_count + 1
            """, (doc_path, entity_id, json.dumps(sorted(pages)), json.dumps(sorted(pages))))
            conn.commit()

    def get_entity_stats(self) -> Dict:
        """Get statistics about registered entities."""
        stats = {}
        with sqlite3.connect(self.db_path) as conn:
            for etype in EntityType:
                result = conn.execute(
                    "SELECT COUNT(*), SUM(document_count) FROM entities WHERE entity_type = ?",
                    (etype.value,)
                ).fetchone()
                stats[etype.value] = {
                    "unique_entities": result[0] or 0,
                    "total_mentions": result[1] or 0
                }
        return stats

    def get_cross_references(self, entity_id: str) -> Dict:
        """Get all documents and co-occurring entities for an entity."""
        with sqlite3.connect(self.db_path) as conn:
            # Get all documents mentioning this entity
            docs = conn.execute(
                "SELECT doc_path, mention_count, page_numbers FROM document_entities WHERE entity_id = ?",
                (entity_id,)
            ).fetchall()

            # Get co-occurring entities
            co_entities = conn.execute("""
                SELECT de2.entity_id, COUNT(DISTINCT de2.doc_path) as shared_docs
                FROM document_entities de1
                JOIN document_entities de2 ON de1.doc_path = de2.doc_path
                WHERE de1.entity_id = ? AND de2.entity_id != ?
                GROUP BY de2.entity_id
                ORDER BY shared_docs DESC
                LIMIT 50
            """, (entity_id, entity_id)).fetchall()

            return {
                "entity_id": entity_id,
                "documents": [{"path": d[0], "mentions": d[1], "pages": d[2]} for d in docs],
                "co_occurring_entities": [{"id": c[0], "shared_docs": c[1]} for c in co_entities]
            }
