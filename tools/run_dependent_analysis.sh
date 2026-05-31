#!/bin/bash
# Wait for OCR to process enough documents, then run dependent analysis

# Auto-detect data directory
if [ -n "$EPSTEIN_DATA_DIR" ]; then
    D="$EPSTEIN_DATA_DIR"
elif [ -f "$(dirname "$(dirname "$(readlink -f "$0")")")/ocr_database.db" ]; then
    D="$(dirname "$(dirname "$(readlink -f "$0")")")"
elif [ -f "./ocr_database.db" ]; then
    D="$(pwd)"
else
    echo "Error: Cannot find data directory. Set EPSTEIN_DATA_DIR or run from the data directory."
    exit 1
fi

TOOLS_DIR="$(dirname "$(readlink -f "$0")")"
OCR_DB="$D/ocr_database.db"
MIN_DOCS=5000

echo "Waiting for OCR to process at least $MIN_DOCS documents..."

while true; do
    if [ -f "$OCR_DB" ]; then
        COUNT=$(sqlite3 "$OCR_DB" "SELECT COUNT(*) FROM ocr_results WHERE ocr_text IS NOT NULL" 2>/dev/null || echo "0")
        echo "$(date): OCR has processed $COUNT documents"

        if [ "$COUNT" -ge "$MIN_DOCS" ]; then
            echo "Threshold reached! Running dependent analysis..."
            break
        fi
    fi
    sleep 60
done

echo "Running name search..."
python3 "$TOOLS_DIR/name_search.py"

echo "Running document classifier..."
python3 "$TOOLS_DIR/document_classifier.py"

echo "Building knowledge graph..."
python3 "$TOOLS_DIR/knowledge_graph.py"

echo "All analysis complete!"
