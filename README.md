# Epstein Files: Structured Data Exports

Structured data exports from the forensic analysis of the 218GB DOJ Jeffrey Epstein file release (all 12 datasets, 519,548 PDFs, 993,406 pages).

**Parent project:** [Epstein-research](https://github.com/rhowardstone/Epstein-research) — 100+ forensic investigation reports with DOJ source citations.

## What's Here

### Knowledge Graph (Curated)

| File | Records | Description |
|------|---------|-------------|
| `knowledge_graph_entities.json` | 524 | Curated entities: 489 people, 12 shell companies, 9 organizations, 7 properties, 4 aircraft, 3 locations. Each entry includes aliases, metadata (occupation, legal status, mention counts), and entity type. |
| `knowledge_graph_relationships.json` | 2,096 | Relationships between entities with types (traveled_with, associated_with, owned_by, victim_of, etc.), weights, date ranges, and source/target entity names. |

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
| 4 | 00005705 | 00008320 | Gap: 5587-5704 |
| 5 | 00008409 | 00008528 | Gap: 8321-8408 |
| 6 | 00008529 | 00008998 | |
| 7 | 00009016 | 00009664 | Gap: 8999-9015 |
| 8 | 00009676 | 00039023 | Largest single dataset |
| 9 | 00039025 | 01262781 | Labeled "REMOVED" but returns 200 |
| 10 | 01262782 | 02205654 | |
| 11 | 02205655 | 02730264 | |
| 12 | 02730265 | 02731783 | |

## Source Databases

These exports were generated from the following databases built during the investigation:

- **redaction_analysis_v2.db** (660MB) — 1.8M redaction records, 519K document summaries, 107K entities
- **image_analysis.db** (389MB) — 38,955 analyzed images with AI-generated descriptions
- **knowledge_graph.db** (768KB) — 524 curated entities, 2,096 relationships
- **full_text_corpus.db** (2.2GB) — FTS5-indexed full text of 519,548 documents (not exported here due to size)

## Integration Notes

For developers building tools on top of this data:

- EFTA numbers are the universal key. Every document in the DOJ release has one.
- The `efta_dataset_mapping` files let you resolve any EFTA number to a DOJ PDF URL.
- Entity `efta_numbers` arrays give you cross-references: "this person appears in these documents."
- Knowledge graph `weight` on relationships indicates strength of connection (higher = more documented).
- Image `image_name` format is `EFTA{number}_p{page}_i{index}_{hash}.png` — parse EFTA number and page from the filename.

## License

This is analysis of public government records released under the Epstein Files Transparency Act (Public Law 118-299). The underlying documents are U.S. government works. This structured data is released into the public domain.

## Contact

Dr. Rye Howard-Stone — rhowardstone@gmail.com
