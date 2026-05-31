#!/bin/bash
echo "=== EPSTEIN FILES ANALYSIS STATUS ==="
echo ""

# Auto-detect data directory
if [ -n "$EPSTEIN_DATA_DIR" ]; then
    D="$EPSTEIN_DATA_DIR"
elif [ -f "$(dirname "$(dirname "$(readlink -f "$0")")")/full_text_corpus.db" ]; then
    D="$(dirname "$(dirname "$(readlink -f "$0")")")"
elif [ -f "./full_text_corpus.db" ]; then
    D="$(pwd)"
else
    echo "Error: Cannot find data directory. Set EPSTEIN_DATA_DIR or run from the data directory."
    exit 1
fi

# OCR Status
if [ -f "$D/ocr_database.db" ]; then
    OCR_COUNT=$(sqlite3 "$D/ocr_database.db" "SELECT COUNT(*) FROM ocr_results WHERE ocr_text IS NOT NULL" 2>/dev/null || echo "0")
    echo "OCR: $OCR_COUNT images processed"
else
    echo "OCR: Database not yet created"
fi

# Redaction Status
if [ -f "$D/redaction_analysis.db" ]; then
    REDACT_COUNT=$(sqlite3 "$D/redaction_analysis.db" "SELECT COUNT(*) FROM redactions" 2>/dev/null || echo "0")
    REDACTED=$(sqlite3 "$D/redaction_analysis.db" "SELECT COUNT(*) FROM redactions WHERE has_redaction=1" 2>/dev/null || echo "0")
    echo "Redaction detector: $REDACT_COUNT scanned, $REDACTED with redactions found"
else
    echo "Redaction detector: Database not yet created"
fi

# Qwen2-VL Status
if [ -f "$D/image_analysis.db" ]; then
    QWEN_COUNT=$(sqlite3 "$D/image_analysis.db" "SELECT COUNT(*) FROM images WHERE analysis_text IS NOT NULL AND length(analysis_text) > 0" 2>/dev/null || echo "0")
    echo "Image analysis: $QWEN_COUNT images analyzed"
else
    echo "Image analysis: Database not found"
fi

# Evidence findings
FINDINGS=$(wc -l < "$D/evidence_findings.jsonl" 2>/dev/null || echo "0")
echo "Manual findings logged: $FINDINGS"

# Cross-references
if [ -f "$D/name_crossref.jsonl" ]; then
    XREF=$(wc -l < "$D/name_crossref.jsonl")
    echo "Name cross-references: $XREF"
fi

# Priority documents
if [ -f "$D/priority_documents.jsonl" ]; then
    PRIORITY=$(wc -l < "$D/priority_documents.jsonl")
    echo "Priority documents flagged: $PRIORITY"
fi

echo ""
echo "Running processes:"
ps aux | grep -E "(bulk_ocr|redaction_detector|comprehensive_image|dependent_analysis)" | grep -v grep | awk '{print "  " $11 " " $12}'
