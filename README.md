# Epstein Files: Structured Data Exports

Structured data exports from the forensic analysis of the 218GB DOJ Jeffrey Epstein file release (all 12 datasets, 519,548 PDFs, 993,406 pages). Currently working with [r/EricKeller2](https://www.reddit.com/user/EricKeller2/) in order to (fully) integrate this data into https://www.epsteinexposed.com/.

Message board available at: https://board.epsteinexposed.com/

Note the latest release, v2.1, at: https://github.com/rhowardstone/Epstein-research-data/releases/tag/v2.1

**Parent project:** [Epstein-research](https://github.com/rhowardstone/Epstein-research) — 100+ forensic investigation reports with DOJ source citations.

## What's Here

### Knowledge Graph (Curated)

| File | Records | Description |
|------|---------|-------------|
| `knowledge_graph_entities.json` | 524 | Curated entities: 489 people, 12 shell companies, 9 organizations, 7 properties, 4 aircraft, 3 locations. Each entry includes aliases, metadata (occupation, legal status, mention counts), and entity type. |
| `knowledge_graph_relationships.json` | 2,096 | Relationships between entities with types (traveled_with, associated_with, owned_by, victim_of, etc.), weights, date ranges, and source/target entity names. |

**Note on knowledge graph:** The knowledge graph was curated during the initial investigation phases and does not include NER (Named Entity Recognition) run against the full OCR corpus. It covers the most frequently-referenced and manually-verified entities. For comprehensive name extraction from the full text, see `extracted_entities_filtered.json` below or query the full databases directly.

### Entity Extraction (Filtered from 107K raw)

| File | Records | Description |
|------|---------|-------------|
| `extracted_entities_filtered.json` | 8,081 | Filtered entity extractions: 3,881 names (appearing in 2+ documents), 2,238 phone numbers, 1,489 amounts, 357 emails, 116 organizations. Each entry includes the EFTA document numbers where it appears. |
| `extracted_names_multi_doc.csv` | 3,881 | Names appearing in multiple EFTA documents with document counts and sample EFTA references. CSV format for easy browsing. |

**Note on quality:** The raw extraction table contains 107,422 entities, many of which are OCR artifacts from redacted/degraded documents. The filtered exports remove garbled text and require multi-document co-occurrence for names.

### Image Catalog

| File | Records | Description |
|------|---------|-------------|
| `image_catalog.csv.gz` | 38,955 | Complete image catalog (gzipped). Fields: id, image_name, efta_number, page_number, people, text_content, objects, setting, activity, notable, analyzed_at. |
| `image_catalog_notable.json.gz` | 38,864 | Images with people or notable content identified (gzipped JSON). Truncated fields for manageable size. |

### Document Summaries

| File | Records | Description |
|------|---------|-------------|
| `document_summary.csv.gz` | 519,438 | Per-document redaction summary for every EFTA document (gzipped). Fields: efta_number, total_redactions, bad_redactions, proper_redactions, has_recoverable_text, dataset_source. |

### Reconstructed Pages (High Interest)

| File | Records | Description |
|------|---------|-------------|
| `reconstructed_pages_high_interest.json.gz` | 39,588 | Pages where hidden text was recovered from under redactions (gzipped JSON). Fields include efta_number, page_number, num_fragments, reconstructed_text, interest_score, and names_found. Higher interest scores indicate more substantive recovered content. |

### EFTA-to-DOJ URL Mapping

| File | Description |
|------|-------------|
| `efta_dataset_mapping.csv` | EFTA number ranges for each of the 12 DOJ datasets, with URL templates. |
| `efta_dataset_mapping.json` | Same mapping in JSON format for programmatic use. |

**URL Pattern:** `https://www.justice.gov/epstein/files/DataSet%20{N}/EFTA{XXXXXXXX}.pdf`

| Dataset | EFTA Start | EFTA End | Note |
|---------|-----------|----------|------|
| 1 | 00000001 | 00003158 | |
| 2 | 00003159 | 00003857 | |
| 3 | 00003858 | 00005586 | |
| 4 | 00005705 | 00008320 | Gap: 5587-5704 (files exist in adjacent datasets) |
| 5 | 00008409 | 00008528 | Gap: 8321-8408 (files exist in adjacent datasets) |
| 6 | 00008529 | 00008998 | |
| 7 | 00009016 | 00009664 | Gap: 8999-9015 (files exist in adjacent datasets) |
| 8 | 00009676 | 00039023 | Largest single dataset |
| 9 | 00039025 | 01262781 | Labeled "REMOVED" but returns 200 |
| 10 | 01262782 | 02205654 | |
| 11 | 02205655 | 02730264 | |
| 12 | 02730265 | 02731783 | |

## Full Database Downloads

All source databases are available as compressed SQLite files in the [v1.0 Release](https://github.com/rhowardstone/Epstein-research-data/releases/tag/v1.0):

| Database | Compressed | Uncompressed | Contents |
|----------|-----------|-------------|----------|
| [full_text_corpus.db.gz](https://github.com/rhowardstone/Epstein-research-data/releases/download/v1.0/full_text_corpus.db.gz) | 810MB | 2.2GB | 519,548 documents, 993,406 pages with full text, FTS5 search index |
| [redaction_analysis_v2.db.gz](https://github.com/rhowardstone/Epstein-research-data/releases/download/v1.0/redaction_analysis_v2.db.gz) | 112MB | 660MB | 1.8M redaction records, 519K document summaries, 39K reconstructed pages, 107K extracted entities |
| [redaction_analysis_ds10.db.gz](https://github.com/rhowardstone/Epstein-research-data/releases/download/v1.0/redaction_analysis_ds10.db.gz) | 87MB | 532MB | Dataset 10 deep analysis (EFTA01262782-02205654) |
| [image_analysis.db.gz](https://github.com/rhowardstone/Epstein-research-data/releases/download/v1.0/image_analysis.db.gz) | 64MB | 389MB | 38,955 images with AI-generated descriptions |
| [ocr_database.db.gz](https://github.com/rhowardstone/Epstein-research-data/releases/download/v1.0/ocr_database.db.gz) | 25MB | 68MB | OCR extraction data |

**Total:** ~1.1GB compressed / ~3.85GB uncompressed

```bash
# Download and decompress any database
wget https://github.com/rhowardstone/Epstein-research-data/releases/download/v1.0/full_text_corpus.db.gz
gunzip full_text_corpus.db.gz

# Search the full text corpus
sqlite3 full_text_corpus.db "SELECT efta_number, page_number, substr(text_content, 1, 200) FROM pages WHERE text_content LIKE '%Leon Black%' LIMIT 10;"

# Search redacted content
sqlite3 redaction_analysis_v2.db "SELECT efta_number, page_number, substr(hidden_text, 1, 300) FROM redactions WHERE hidden_text LIKE '%TERM%' AND length(hidden_text) > 20 LIMIT 20;"
```

## Integration Notes

For developers building tools on top of this data:

- EFTA numbers are the universal key. Every document in the DOJ release has one.
- The `efta_dataset_mapping` files let you resolve any EFTA number to a DOJ PDF URL.
- Entity `efta_numbers` arrays give you cross-references: "this person appears in these documents."
- Knowledge graph `weight` on relationships indicates strength of connection (higher = more documented).
- Image `image_name` format is `EFTA{number}_p{page}_i{index}_{hash}.png` — parse EFTA number and page from the filename.
- **Gap EFTAs:** The gaps between datasets (e.g., 5587-5704) are not missing — those files exist and resolve via DOJ URLs in adjacent datasets. When resolving an EFTA in a gap range, try the dataset on either side.

## License

This is analysis of public government records released under the Epstein Files Transparency Act (Public Law 118-299). The underlying documents are U.S. government works. This structured data is released into the public domain.

## Contact

Please open an issue if you find any problems! We will respond promptly.
