#!/usr/bin/env python3
"""
Multi-Agent Document Analysis Coordinator

Manages parallel sub-agents that deeply read and analyze documents.
Each agent:
1. Reads full document content
2. Identifies persons and classifies (victim/perpetrator/associate)
3. Detects hidden patterns (steganographic, diagonal reading, etc.)
4. Extracts relationships and connections
5. Cross-references with other documents

This coordinator:
- Prioritizes documents by potential importance
- Distributes work across agents
- Aggregates findings into unified knowledge base
- Tracks progress and manages state
"""
import json
import sqlite3
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Optional, Set
from datetime import datetime
from enum import Enum
import hashlib

class AnalysisStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    ANALYZED = "analyzed"
    NEEDS_REVIEW = "needs_review"  # Agent found something requiring human attention
    ERROR = "error"

class Priority(Enum):
    CRITICAL = 1   # Known important documents (flight logs, contact books)
    HIGH = 2       # Documents with many redactions
    MEDIUM = 3     # Standard documents
    LOW = 4        # Likely administrative/routine

@dataclass
class DocumentTask:
    """A document queued for agent analysis."""
    pdf_path: str
    file_hash: str
    priority: Priority
    status: AnalysisStatus
    assigned_agent: Optional[str] = None
    assigned_at: Optional[str] = None
    completed_at: Optional[str] = None

    # Analysis results (populated by agent)
    persons_found: int = 0
    victims_identified: int = 0
    perpetrators_identified: int = 0
    patterns_detected: int = 0
    cross_references: int = 0
    requires_human_review: bool = False
    review_reason: str = ""

@dataclass
class PersonEntity:
    """A person identified across documents."""
    entity_id: str  # Pseudonymous ID like PERSON-0001
    classification: str  # victim, perpetrator, associate, unknown
    classification_confidence: float
    classification_reasons: List[str]

    # Document appearances
    document_count: int
    mention_count: int
    first_seen: str
    contexts: List[str]  # Sample contexts for review

    # Relationships
    co_occurs_with: List[str]  # Other entity IDs frequently mentioned together

    # For manual override
    manually_reviewed: bool = False
    reviewer_classification: Optional[str] = None
    reviewer_notes: str = ""

@dataclass
class AgentFinding:
    """A notable finding from agent analysis."""
    finding_type: str  # "hidden_pattern", "high_value_connection", "anomaly"
    document_path: str
    page_numbers: List[int]
    description: str
    evidence: str
    confidence: float
    entities_involved: List[str]
    requires_human_review: bool

class AnalysisCoordinator:
    """
    Coordinates multi-agent document analysis.

    Manages:
    - Work queue (prioritized)
    - Entity registry (consistent IDs)
    - Findings database
    - Cross-reference index
    """

    def __init__(self, db_path: str, docs_root: str):
        self.db_path = Path(db_path)
        self.docs_root = Path(docs_root)
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database for coordination."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            # Document queue
            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    pdf_path TEXT PRIMARY KEY,
                    file_hash TEXT,
                    priority INTEGER,
                    status TEXT,
                    assigned_agent TEXT,
                    assigned_at TEXT,
                    completed_at TEXT,
                    persons_found INTEGER DEFAULT 0,
                    victims_identified INTEGER DEFAULT 0,
                    perpetrators_identified INTEGER DEFAULT 0,
                    patterns_detected INTEGER DEFAULT 0,
                    cross_references INTEGER DEFAULT 0,
                    requires_human_review BOOLEAN DEFAULT FALSE,
                    review_reason TEXT DEFAULT ''
                )
            """)

            # Persons registry
            conn.execute("""
                CREATE TABLE IF NOT EXISTS persons (
                    entity_id TEXT PRIMARY KEY,
                    name_hash TEXT UNIQUE,
                    classification TEXT DEFAULT 'unknown',
                    classification_confidence REAL DEFAULT 0,
                    classification_reasons TEXT DEFAULT '[]',
                    document_count INTEGER DEFAULT 0,
                    mention_count INTEGER DEFAULT 0,
                    first_seen TEXT,
                    contexts TEXT DEFAULT '[]',
                    co_occurs_with TEXT DEFAULT '[]',
                    manually_reviewed BOOLEAN DEFAULT FALSE,
                    reviewer_classification TEXT,
                    reviewer_notes TEXT DEFAULT ''
                )
            """)

            # Person-document links
            conn.execute("""
                CREATE TABLE IF NOT EXISTS person_documents (
                    entity_id TEXT,
                    pdf_path TEXT,
                    mention_count INTEGER,
                    page_numbers TEXT,
                    PRIMARY KEY (entity_id, pdf_path)
                )
            """)

            # Findings
            conn.execute("""
                CREATE TABLE IF NOT EXISTS findings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    finding_type TEXT,
                    document_path TEXT,
                    page_numbers TEXT,
                    description TEXT,
                    evidence TEXT,
                    confidence REAL,
                    entities_involved TEXT,
                    requires_human_review BOOLEAN,
                    created_at TEXT,
                    reviewed BOOLEAN DEFAULT FALSE,
                    review_notes TEXT DEFAULT ''
                )
            """)

            # Cross-references (documents that should be analyzed together)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cross_references (
                    doc1_path TEXT,
                    doc2_path TEXT,
                    relationship_type TEXT,
                    shared_entities TEXT,
                    confidence REAL,
                    PRIMARY KEY (doc1_path, doc2_path)
                )
            """)

            conn.commit()

    def queue_documents(self, pdf_paths: List[str]):
        """Add documents to analysis queue with priority scoring."""
        with sqlite3.connect(self.db_path) as conn:
            for pdf_path in pdf_paths:
                # Calculate priority
                priority = self._calculate_priority(pdf_path)

                # Compute file hash
                file_hash = self._compute_hash(pdf_path)

                conn.execute("""
                    INSERT OR IGNORE INTO documents
                    (pdf_path, file_hash, priority, status)
                    VALUES (?, ?, ?, ?)
                """, (pdf_path, file_hash, priority.value, AnalysisStatus.PENDING.value))

            conn.commit()

    def _calculate_priority(self, pdf_path: str) -> Priority:
        """Calculate document priority based on filename and path."""
        path_lower = pdf_path.lower()

        # Critical documents
        critical_keywords = [
            'flight', 'log', 'contact', 'book', 'maxwell', 'proffer',
            'victim', 'witness', 'testimony', 'deposition'
        ]
        if any(kw in path_lower for kw in critical_keywords):
            return Priority.CRITICAL

        # High priority - specific datasets known to be important
        if 'dataset1' in path_lower or 'dataset2' in path_lower:
            return Priority.HIGH

        # Check file size (larger often more important)
        try:
            size = Path(pdf_path).stat().st_size
            if size > 5_000_000:  # > 5MB
                return Priority.HIGH
        except:
            pass

        return Priority.MEDIUM

    def _compute_hash(self, filepath: str) -> str:
        """Compute SHA-256 hash."""
        h = hashlib.sha256()
        try:
            with open(filepath, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    h.update(chunk)
            return h.hexdigest()
        except:
            return ""

    def get_next_batch(self, batch_size: int = 10, agent_id: str = None) -> List[str]:
        """Get next batch of documents for an agent to process."""
        with sqlite3.connect(self.db_path) as conn:
            # Get pending documents, prioritized
            rows = conn.execute("""
                SELECT pdf_path FROM documents
                WHERE status = ?
                ORDER BY priority ASC, pdf_path
                LIMIT ?
            """, (AnalysisStatus.PENDING.value, batch_size)).fetchall()

            paths = [r[0] for r in rows]

            # Mark as in progress
            if paths and agent_id:
                placeholders = ','.join('?' * len(paths))
                conn.execute(f"""
                    UPDATE documents
                    SET status = ?, assigned_agent = ?, assigned_at = ?
                    WHERE pdf_path IN ({placeholders})
                """, [AnalysisStatus.IN_PROGRESS.value, agent_id, datetime.now().isoformat()] + paths)
                conn.commit()

            return paths

    def record_analysis_result(
        self,
        pdf_path: str,
        persons: List[Dict],
        findings: List[Dict],
        cross_refs: List[Dict]
    ):
        """Record results from agent analysis."""
        with sqlite3.connect(self.db_path) as conn:
            # Count classifications
            victims = sum(1 for p in persons if p.get('classification') == 'victim')
            perps = sum(1 for p in persons if p.get('classification') == 'perpetrator')

            # Check if needs human review
            needs_review = any(f.get('requires_human_review') for f in findings)
            review_reasons = [f.get('description', '') for f in findings if f.get('requires_human_review')]

            # Update document status
            conn.execute("""
                UPDATE documents SET
                    status = ?,
                    completed_at = ?,
                    persons_found = ?,
                    victims_identified = ?,
                    perpetrators_identified = ?,
                    patterns_detected = ?,
                    cross_references = ?,
                    requires_human_review = ?,
                    review_reason = ?
                WHERE pdf_path = ?
            """, (
                AnalysisStatus.NEEDS_REVIEW.value if needs_review else AnalysisStatus.ANALYZED.value,
                datetime.now().isoformat(),
                len(persons),
                victims,
                perps,
                len([f for f in findings if f.get('finding_type') == 'hidden_pattern']),
                len(cross_refs),
                needs_review,
                '; '.join(review_reasons[:3]),
                pdf_path
            ))

            # Record findings
            for finding in findings:
                conn.execute("""
                    INSERT INTO findings
                    (finding_type, document_path, page_numbers, description,
                     evidence, confidence, entities_involved, requires_human_review, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    finding.get('finding_type'),
                    pdf_path,
                    json.dumps(finding.get('page_numbers', [])),
                    finding.get('description'),
                    finding.get('evidence', '')[:5000],
                    finding.get('confidence', 0),
                    json.dumps(finding.get('entities_involved', [])),
                    finding.get('requires_human_review', False),
                    datetime.now().isoformat()
                ))

            # Record cross-references
            for xref in cross_refs:
                conn.execute("""
                    INSERT OR REPLACE INTO cross_references
                    (doc1_path, doc2_path, relationship_type, shared_entities, confidence)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    pdf_path,
                    xref.get('related_doc'),
                    xref.get('relationship_type'),
                    json.dumps(xref.get('shared_entities', [])),
                    xref.get('confidence', 0)
                ))

            conn.commit()

    def get_progress(self) -> Dict:
        """Get analysis progress statistics."""
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            pending = conn.execute("SELECT COUNT(*) FROM documents WHERE status = ?",
                                   (AnalysisStatus.PENDING.value,)).fetchone()[0]
            in_progress = conn.execute("SELECT COUNT(*) FROM documents WHERE status = ?",
                                       (AnalysisStatus.IN_PROGRESS.value,)).fetchone()[0]
            analyzed = conn.execute("SELECT COUNT(*) FROM documents WHERE status = ?",
                                    (AnalysisStatus.ANALYZED.value,)).fetchone()[0]
            needs_review = conn.execute("SELECT COUNT(*) FROM documents WHERE status = ?",
                                        (AnalysisStatus.NEEDS_REVIEW.value,)).fetchone()[0]

            total_persons = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
            total_findings = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
            pending_review = conn.execute(
                "SELECT COUNT(*) FROM findings WHERE requires_human_review AND NOT reviewed"
            ).fetchone()[0]

            return {
                "documents": {
                    "total": total,
                    "pending": pending,
                    "in_progress": in_progress,
                    "analyzed": analyzed,
                    "needs_review": needs_review,
                    "percent_complete": ((analyzed + needs_review) / total * 100) if total > 0 else 0
                },
                "entities": {
                    "total_persons": total_persons
                },
                "findings": {
                    "total": total_findings,
                    "pending_review": pending_review
                }
            }

    def get_findings_for_review(self, limit: int = 50) -> List[Dict]:
        """Get findings that require human review."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT id, finding_type, document_path, page_numbers,
                       description, evidence, confidence, entities_involved
                FROM findings
                WHERE requires_human_review AND NOT reviewed
                ORDER BY confidence DESC
                LIMIT ?
            """, (limit,)).fetchall()

            return [{
                "id": r[0],
                "finding_type": r[1],
                "document_path": r[2],
                "page_numbers": json.loads(r[3]) if r[3] else [],
                "description": r[4],
                "evidence": r[5],
                "confidence": r[6],
                "entities_involved": json.loads(r[7]) if r[7] else []
            } for r in rows]


# Agent task template for spawning sub-agents
AGENT_ANALYSIS_PROMPT = """
You are analyzing document: {pdf_path}

TASK: Deep document analysis for Epstein files investigation

1. READ the entire document carefully
2. IDENTIFY all persons mentioned:
   - For each person, determine if they are:
     * VICTIM: Minor, survivor, "Jane Doe", recruited, abused
     * PERPETRATOR: Epstein, Maxwell, charged individuals
     * ASSOCIATE: Employees, pilots, assistants, facilitators
     * WITNESS: Law enforcement, attorneys, journalists
     * UNKNOWN: Cannot determine (protect by default)

3. DETECT hidden patterns:
   - Read first letters of each line (acrostic)
   - Check diagonal reading patterns
   - Look for unusual spacing or formatting
   - Note any coded language or references

4. EXTRACT relationships:
   - Who is connected to whom?
   - What locations are mentioned?
   - What dates/timeframes?
   - Cross-reference with other documents

5. FLAG for human review:
   - Anything you're uncertain about
   - Potential steganographic messages
   - High-value connections
   - Victim testimony requiring sensitivity

OUTPUT FORMAT (JSON):
{{
  "persons": [
    {{
      "name": "...",
      "classification": "victim|perpetrator|associate|witness|unknown",
      "confidence": 0.0-1.0,
      "reasons": ["..."],
      "contexts": ["..."],
      "page_numbers": [1, 2, ...]
    }}
  ],
  "findings": [
    {{
      "finding_type": "hidden_pattern|connection|anomaly|testimony",
      "description": "...",
      "evidence": "...",
      "page_numbers": [...],
      "confidence": 0.0-1.0,
      "entities_involved": [...],
      "requires_human_review": true|false
    }}
  ],
  "cross_references": [
    {{
      "related_doc": "suggested document to compare",
      "relationship_type": "same_victim|same_date|same_location|...",
      "shared_entities": [...],
      "confidence": 0.0-1.0
    }}
  ],
  "summary": "Brief summary of document content and significance"
}}

IMPORTANT:
- NEVER output actual victim names - use placeholders like [VICTIM-PENDING-001]
- Err on the side of protecting identities
- Flag anything uncertain for human review
- Look for hidden messages and patterns carefully
"""


def generate_agent_tasks(coordinator: AnalysisCoordinator, batch_size: int = 20) -> List[Dict]:
    """Generate task definitions for spawning sub-agents."""
    paths = coordinator.get_next_batch(batch_size=batch_size, agent_id="batch_generator")

    tasks = []
    for pdf_path in paths:
        tasks.append({
            "pdf_path": pdf_path,
            "prompt": AGENT_ANALYSIS_PROMPT.format(pdf_path=pdf_path),
            "output_key": f"analysis_{Path(pdf_path).stem}"
        })

    return tasks
