"""
Victim vs Perpetrator Classification

Uses contextual clues to determine if a named person is likely a victim or perpetrator.
This is crucial for maintaining victim privacy while exposing perpetrator networks.
"""
import re
from typing import Tuple, List, Set, Optional
from dataclasses import dataclass
from entity_registry import EntityType

# Known perpetrators/associates (public record from trials and indictments)
KNOWN_PERPETRATORS: Set[str] = {
    "jeffrey epstein", "epstein",
    "ghislaine maxwell", "maxwell", "ghislaine",
    # Add more as identified from public court records
}

KNOWN_ASSOCIATES: Set[str] = {
    # These are people named in documents as associates but not charged
    # Will be populated during processing
}

# Contextual patterns indicating victim status
VICTIM_CONTEXT_PATTERNS = [
    r'\b(victim|survivor|complainant|accuser)\b',
    r'\b(minor|underage|juvenile|child|teen|teenage)\b',
    r'\b(jane\s+doe|john\s+doe)\b',
    r'\b(sexually\s+abuse[d]?|molest|assault|traffic)\b.{0,50}\b(victim|her|she|him|he)\b',
    r'\bage[d]?\s*(?:of\s*)?(1[0-7]|[1-9])\s*(?:years?|y/?o)?\b',  # ages under 18
    r'\b(recruited|groomed|lured|manipulated)\b',
    r'\b(massage|masseuse)\b.{0,30}\b(young|girl|minor)\b',
    r'\b(plaintiff|petitioner)\b.{0,100}\bv\.?\s*epstein\b',
]

# Contextual patterns indicating perpetrator/associate status
PERPETRATOR_CONTEXT_PATTERNS = [
    r'\b(defendant|accused|charged|indicted|convicted)\b',
    r'\b(conspir|facilitat|procur|recruit|transport)\b.{0,30}\b(minor|victim|girl)\b',
    r'\b(epstein|maxwell).{0,50}(associate|friend|employee|pilot|butler|assistant)\b',
    r'\b(paid|compensat|brib)\b.{0,30}\b(recruit|bring|find)\b',
]

# Titles/roles that suggest non-victim status
NON_VICTIM_TITLES = [
    r'\b(dr\.?|doctor|attorney|lawyer|judge|officer|detective|agent|pilot|captain)\s+',
    r'\b(ceo|cfo|president|director|manager|executive)\s+',
    r'\b(prince|duke|president|governor|senator|congressman)\s+',
]

@dataclass
class ClassificationResult:
    entity_type: EntityType
    confidence: float  # 0.0 to 1.0
    reason: str
    context_snippet: str

def normalize_name(name: str) -> str:
    """Normalize name for comparison."""
    return re.sub(r'\s+', ' ', name.strip().lower())

def classify_person(
    name: str,
    surrounding_text: str,
    full_document_text: str = ""
) -> ClassificationResult:
    """
    Classify a person as victim, perpetrator, associate, or unknown.

    Uses multiple signals:
    1. Known perpetrator list
    2. Contextual patterns around name mention
    3. Document-level context
    4. Title/role indicators
    """
    name_lower = normalize_name(name)
    context_lower = surrounding_text.lower()
    doc_lower = full_document_text.lower() if full_document_text else context_lower

    # Check known perpetrators first
    if name_lower in KNOWN_PERPETRATORS or any(kp in name_lower for kp in KNOWN_PERPETRATORS):
        return ClassificationResult(
            entity_type=EntityType.PERPETRATOR,
            confidence=0.99,
            reason="Known perpetrator from public court records",
            context_snippet=surrounding_text[:200]
        )

    # Check for victim context patterns
    victim_score = 0
    victim_reasons = []

    for pattern in VICTIM_CONTEXT_PATTERNS:
        if re.search(pattern, context_lower, re.IGNORECASE):
            victim_score += 1
            victim_reasons.append(pattern)

    # Check document-wide context if available
    if full_document_text:
        # If document mentions this name near victim-related terms
        name_pattern = re.escape(name_lower)
        for pattern in VICTIM_CONTEXT_PATTERNS[:4]:  # Use strongest patterns
            combined = f'{name_pattern}.{{0,100}}{pattern}|{pattern}.{{0,100}}{name_pattern}'
            if re.search(combined, doc_lower, re.IGNORECASE):
                victim_score += 0.5

    # Check for perpetrator context patterns
    perp_score = 0
    perp_reasons = []

    for pattern in PERPETRATOR_CONTEXT_PATTERNS:
        if re.search(pattern, context_lower, re.IGNORECASE):
            perp_score += 1
            perp_reasons.append(pattern)

    # Check for professional titles (usually not victims in this context)
    for pattern in NON_VICTIM_TITLES:
        if re.search(pattern + re.escape(name_lower), context_lower, re.IGNORECASE):
            perp_score += 0.5

    # Decision logic
    if victim_score >= 2 and victim_score > perp_score:
        confidence = min(0.95, 0.6 + (victim_score * 0.1))
        return ClassificationResult(
            entity_type=EntityType.VICTIM,
            confidence=confidence,
            reason=f"Victim context patterns: {len(victim_reasons)} matches",
            context_snippet=surrounding_text[:200]
        )

    if perp_score >= 2 and perp_score > victim_score:
        confidence = min(0.9, 0.5 + (perp_score * 0.1))
        return ClassificationResult(
            entity_type=EntityType.ASSOCIATE,  # Not PERPETRATOR unless confirmed
            confidence=confidence,
            reason=f"Associate/perpetrator context patterns: {len(perp_reasons)} matches",
            context_snippet=surrounding_text[:200]
        )

    if victim_score >= 1:
        return ClassificationResult(
            entity_type=EntityType.VICTIM,
            confidence=0.6,
            reason="Possible victim - treating as protected",
            context_snippet=surrounding_text[:200]
        )

    # Default: Unknown person, but err on side of caution
    # If in Epstein documents and no clear role, might be victim
    return ClassificationResult(
        entity_type=EntityType.UNKNOWN_PERSON,
        confidence=0.3,
        reason="Unclear role - will pseudonymize for safety",
        context_snippet=surrounding_text[:200]
    )


def should_protect_name(classification: ClassificationResult) -> bool:
    """
    Determine if a name should be pseudonymized (protected).

    Protects: VICTIM, UNKNOWN_PERSON (err on side of caution)
    Exposes: PERPETRATOR, ASSOCIATE, ORGANIZATION, LOCATION
    """
    protected_types = {EntityType.VICTIM, EntityType.UNKNOWN_PERSON, EntityType.WITNESS}
    return classification.entity_type in protected_types


def get_context_window(text: str, start: int, end: int, window: int = 200) -> str:
    """Extract context window around a span."""
    ctx_start = max(0, start - window)
    ctx_end = min(len(text), end + window)
    return text[ctx_start:ctx_end]
