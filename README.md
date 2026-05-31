# Epstein Files: Structured Data Exports

Structured data exports from the forensic analysis of the DOJ Jeffrey Epstein file release under the [Epstein Files Transparency Act](https://www.congress.gov/bill/119th-congress/house-bill/42) (Public Law 119-38). All 12 DOJ datasets plus House Oversight Estate and FBI Vault materials: **1.4M+ documents, 2.9M+ pages**.

**[epstein-data.com](https://epstein-data.com)** — searchable interface with full-text search, AI research assistant, document viewer, redaction analysis, deposition transcripts, geographic heatmap, and more.

**[Investigation Reports](https://epstein-data.com/reports/)** — 180+ forensic reports with DOJ source citations, organized by topic.

**[Desktop Install](https://epstein-data.com/investigate)** — run the full Claude-powered investigator locally with all databases. No tech skills required. ([CLI version](https://epstein-data.com/investigate_cli))

---

## Investigation Reports

180+ reports across 18 categories. Each report cites specific EFTA documents you can verify at [epstein-data.com](https://epstein-data.com). Browse all reports in the [grid view](https://epstein-data.com/reports/).

**[Executive Summaries](https://epstein-data.com/reports/#cat-overview)** — [Final Investigation Report](https://epstein-data.com/reports/overview/FINAL_INVESTIGATION_REPORT.html) | [Master Report](https://epstein-data.com/reports/overview/MASTER_REPORT.html) | [Phase 4 Briefing Kit](https://epstein-data.com/reports/overview/PHASE4_BRIEFING_KIT.html)

**[Individual Investigations](https://epstein-data.com/reports/#cat-individuals)** — [Trump](https://epstein-data.com/reports/individuals/DONALD_TRUMP_INVESTIGATION.html) | [Rothschild](https://epstein-data.com/reports/individuals/ROTHSCHILD_INVESTIGATION.html) | [Pseudonym & Codename Registry](https://epstein-data.com/reports/individuals/PSEUDONYM_CODENAME_REGISTRY.html)

**[Institutional Failures](https://epstein-data.com/reports/#cat-institutional)** — [DOJ Document Removal Audit](https://epstein-data.com/reports/institutional/DOJ_DOCUMENT_REMOVAL_AUDIT.html) | [DS12 Expansion Analysis](https://epstein-data.com/reports/institutional/DS12_EXPANSION_ANALYSIS.html) | [Alteration Forensics](https://epstein-data.com/reports/institutional/DOJ_DOCUMENT_ALTERATION_FORENSICS.html)

**[Congressional Briefings](https://epstein-data.com/reports/#cat-congressional)** — [Subpoena Guide](https://epstein-data.com/reports/congressional/CONGRESSIONAL_SUBPOENA_GUIDE.html) | [Witness Brief: Indyke](https://epstein-data.com/reports/congressional/WITNESS_BRIEF_INDYKE.html) | [Deposition Analysis: Indyke](https://epstein-data.com/reports/congressional/DEPOSITION_ANALYSIS_INDYKE.html)

**[Financial Forensics](https://epstein-data.com/reports/#cat-financial)** — [DiIorio / Apollo Whistleblower](https://epstein-data.com/reports/financial/DILORIO_APOLLO_WHISTLEBLOWER.html) | [Shell Entity Map](https://epstein-data.com/reports/financial/SHELL_ENTITY_MAP.html) | [Leon Black Prosecution Failure](https://epstein-data.com/reports/individuals/LEON_BLACK_PROSECUTION_FAILURE.html)

**[Evidence & Device Forensics](https://epstein-data.com/reports/#cat-evidence)** — [Evidence Compilation](https://epstein-data.com/reports/evidence/EVIDENCE_COMPILATION.html) | [Device Forensics](https://epstein-data.com/reports/evidence/DEVICE_FORENSICS_COMPLETE.html) | [MCC Inmate Witness Interviews](https://epstein-data.com/reports/evidence/MCC_INMATE_WITNESS_INTERVIEWS.html)

**[Intelligence Networks](https://epstein-data.com/reports/#cat-intelligence)** — [FBI Intelligence Investigations](https://epstein-data.com/reports/intelligence/FBI_INTELLIGENCE_INVESTIGATIONS.html) | [Israel Deep Dive](https://epstein-data.com/reports/intelligence/ISRAEL_DEEP_DIVE_V2.html)

**[Social Network Analysis](https://epstein-data.com/reports/#cat-social-networks)** — [Geffen](https://epstein-data.com/reports/social-networks/GEFFEN_INVESTIGATION.html) | [Kotick / Activision](https://epstein-data.com/reports/social-networks/KOTICK_ACTIVISION.html) | [Peggy Siegal Pipeline](https://epstein-data.com/reports/social-networks/PEGGY_SIEGAL_PIPELINE.html)

**[Victim Census & Routes](https://epstein-data.com/reports/#cat-victims)** | **[Art Market](https://epstein-data.com/reports/#cat-art)** | **[Science Network](https://epstein-data.com/reports/#cat-scientists)** | **[Government Officials](https://epstein-data.com/reports/#cat-government-officials)** | **[Dataset Analysis](https://epstein-data.com/reports/#cat-raw-dataset-analysis)** | **[Methodology](https://epstein-data.com/reports/#cat-methodology)** | **[Internet Theories](https://epstein-data.com/reports/#cat-internet-theories)** | **[Prosecutorial Query Graph](https://epstein-data.com/reports/#cat-pqg_lines_of_investigation)**

---

## Quick Start

All databases are in a single release: **[v5.2 — Complete Database Collection](https://github.com/rhowardstone/Epstein-research-data/releases/tag/v5.2)** (PII-redacted).

```bash
# Download the full text corpus (split into 2 parts, ~2.3 GB total)
wget https://github.com/rhowardstone/Epstein-research-data/releases/download/v5.2/ftc_final_part_aa
wget https://github.com/rhowardstone/Epstein-research-data/releases/download/v5.2/ftc_final_part_ab
cat ftc_final_part_* > full_text_corpus.db.gz
gunzip full_text_corpus.db.gz

# Search the full text corpus
sqlite3 full_text_corpus.db "SELECT efta_number, page_number, substr(text_content, 1, 200) FROM pages WHERE text_content LIKE '%Leon Black%' LIMIT 10;"

# FTS5 full-text search (faster)
sqlite3 full_text_corpus.db "SELECT p.efta_number, p.page_number, substr(p.text_content, 1, 200) FROM pages_fts fts JOIN pages p ON p.rowid = fts.rowid WHERE pages_fts MATCH 'shell company' LIMIT 10;"
```

---

## Databases

All databases available in the [v5.2 release](https://github.com/rhowardstone/Epstein-research-data/releases/tag/v5.2).

| Database | Compressed | Description |
|----------|-----------|-------------|
| `full_text_corpus.db` | 2.3 GB (split) | Master text database. 1.4M docs, 2.9M pages with full text and FTS5 search. DS1-12 + House Oversight (DS99) + FBI Vault (DS98) + native spreadsheets. PII-redacted. |
| `concordance_complete.db` | 137 MB | Cross-reference with email threads, folder inventory, production metadata. |
| `alteration_results.db` | 183 MB | 212K change units with diff text, pixel-diff, LLM classification of post-release DOJ document modifications. |
| `redaction_analysis_v2.db` | 166 MB | 2.6M redaction boxes, 850K doc summaries, 39K reconstructed pages, 107K extracted entities. |
| `redaction_analysis_ds10.db` | 87 MB | Dataset 10 deep redaction analysis. |
| `image_analysis.db` | 64 MB | 38K+ extracted images with AI-generated descriptions. |
| `secondary_stamps.db` | — | 835K documents mapped to parallel Bates numbering systems (R1, JPM-SDNY, DB-SDNY, SDNY-GM). |
| `deposition_transcripts.db` | — | Whisper-transcribed depositions with speaker diarization (Wexner, Bill Clinton, Hillary Clinton, and others). |
| `document_status.db` | — | Document availability tracking across DOJ website. |
| `handwriting_transcriptions.db` | — | Handwritten document transcriptions. |
| `native_files.db` | — | Metadata for 3,800+ non-PDF native files (XLSX, media, etc.). |
| `transcripts.db` | 1.7 MB | 1,600+ media files, 435 with speech, 190K words (faster-whisper large-v3). |
| `ocr_database.db` | 25 MB | Tesseract OCR results for degraded documents. |
| `spreadsheet_corpus.db` | — | Native spreadsheet data extracted from Dataset 8. |
| `prosecutorial_query_graph.db` | 2.5 MB | Subpoena analysis: riders, returns, clause fulfillment, investigative gaps. |
| `knowledge_graph.db` | 764 KB | 524 curated entities, 2,096 relationships. |
| `communications.db` | — | Email thread analysis. |
| `persons_registry.json` | — | 1,600+ persons merged from 9 sources with aliases and categories. |

---

## Data Exports

### Knowledge Graph

| File | Records | Description |
|------|---------|-------------|
| `knowledge_graph_entities.json` | 606 | Curated entities: people, shell companies, organizations, properties, aircraft, locations. |
| `knowledge_graph_relationships.json` | 2,302 | Typed relationships with weights, date ranges, and source/target entity names. |

### Person Registry

| File | Records | Description |
|------|---------|-------------|
| `persons_registry.json` | 1,614 | Unified person registry from 9 sources. Includes name, aliases, category, search terms. |

### Entity Extraction

| File | Records | Description |
|------|---------|-------------|
| `extracted_entities_filtered.json` | 8,085 | Filtered entities: 3,881 names (2+ documents), 2,238 phone numbers, 1,489 amounts, 357 emails, 116 organizations. |
| `extracted_names_multi_doc.csv` | 3,881 | Names appearing in multiple documents with counts and sample EFTA references. |

### Image Catalog

| File | Records | Description |
|------|---------|-------------|
| `image_catalog.csv.gz` | 38,955 | Complete image catalog with AI descriptions, people identified, objects, settings. |

### Document Summaries

| File | Records | Description |
|------|---------|-------------|
| `document_summary.csv.gz` | 519,438 | Per-document redaction summary for every EFTA document. |

### Reconstructed Pages

| File | Records | Description |
|------|---------|-------------|
| `reconstructed_pages_high_interest.json.gz` | 39,588 | Pages where hidden text was recovered from under redactions. |

---

## DOJ Document Audit

The [`doj_audit/`](doj_audit/) directory documents a comprehensive audit of the DOJ Epstein Library conducted in February-March 2026.

The audit identified ~64,000 documents that were temporarily removed from the DOJ website after the initial public release, as well as ~24,000 documents with post-release size changes suggesting modification. **As of March 2026, previously removed documents have been restored to justice.gov.** The audit data is preserved for the historical record.

| File | Records | Description |
|------|---------|-------------|
| `doj_audit/CONFIRMED_REMOVED.csv` | 67,784 | Documents confirmed removed at time of audit (since restored). |
| `doj_audit/FLAGGED_documents.csv` | 96,112 | All flagged documents with DOJ URLs and dataset info. |
| `doj_audit/FLAGGED_documents_details.csv` | 102,223 | Detailed metadata including status, category, document type, priority score. |
| `doj_audit/SIZE_MISMATCHES.csv` | 23,989 | Documents with file size changes between original and current DOJ versions. |

Full reports: [Document Removal Audit](https://epstein-data.com/reports/institutional/DOJ_DOCUMENT_REMOVAL_AUDIT.html) | [Alteration Forensics](https://epstein-data.com/reports/institutional/DOJ_DOCUMENT_ALTERATION_FORENSICS.html)

### Alteration Analysis

The [`alteration_analysis/`](alteration_analysis/) directory contains analysis of documents where content was modified between versions.

| File | Records | Description |
|------|---------|-------------|
| `alteration_analysis/classified_alterations.csv` | 21,803 | Classified alteration types with sensitivity ratings and LLM reasoning. |
| `alteration_analysis/removed_entities_export.csv` | 146,209 | Entities (names, accounts, phone numbers) removed between document versions. |

---

## EFTA Numbering and DOJ URLs

EFTA numbers are assigned **per page**, not per document. A 10-page document consumes 10 consecutive EFTA numbers.

**URL pattern:** `https://www.justice.gov/epstein/files/DataSet%20{N}/EFTA{XXXXXXXX}.pdf`

| Dataset | EFTA Start | EFTA End | Notes |
|---------|-----------|----------|-------|
| 1 | 00000001 | 00003158 | |
| 2 | 00003159 | 00003857 | |
| 3 | 00003858 | 00005586 | |
| 4 | 00005705 | 00008320 | |
| 5 | 00008409 | 00008528 | |
| 6 | 00008529 | 00008998 | |
| 7 | 00009016 | 00009664 | |
| 8 | 00009676 | 00039023 | |
| 9 | 00039025 | 01262781 | Largest (103 GB) |
| 10 | 01262782 | 02205654 | |
| 11 | 02205655 | 02730264 | |
| 12 | 02730265 | 02858497 | Post-release expansion |

No gaps between datasets 1-11 — every apparent gap is a multi-page document at a boundary. Dataset 12 has internal gaps (~100K unassigned numbers).

Also available as `efta_dataset_mapping.csv` and `efta_dataset_mapping.json`.

---

## Processing Tools

The `tools/` directory contains 34 Python scripts used to build all databases from raw PDFs. Use these to replicate or extend the analysis.

All tools auto-detect the data directory via `EPSTEIN_DATA_DIR` environment variable, or look relative to the script location.

### Core Pipeline

| Tool | Description |
|------|-------------|
| `ingest_house_estate.py` | Ingest House Oversight Estate documents (Concordance format) |
| `ingest_spreadsheets.py` | Ingest native XLS/XLSX/CSV into full_text_corpus.db |
| `transcribe_media.py` | GPU transcription with faster-whisper large-v3 |
| `redaction_detector_v2.py` | Spatial redaction analysis: find black bars, extract hidden text |
| `build_person_registry.py` | Build unified person registry from 9 sources |
| `build_knowledge_graph.py` | Construct entity relationship graph |
| `document_classifier.py` | Classify document types |
| `populate_evidence_db.py` | Build evidence/receipts database |

### Search & Analysis

| Tool | Description |
|------|-------------|
| `person_search.py` | FTS5 cross-reference search with co-occurrence and CSV export |
| `congressional_scorer.py` | Score documents by redacted-name density for reading room prioritization |
| `name_search.py` | Name-specific corpus search |
| `search_judicial.py` | Search corpus for federal judges |
| `search_gov_officials.py` | Search corpus for government officials |

### Prosecutorial Query Graph (PQG)

| Tool | Description |
|------|-------------|
| `pqg_00_extract_concordance.py` | Extract subpoena concordance data |
| `pqg_01_decompose_riders.py` | Decompose subpoena rider clauses |
| `pqg_02_match_returns.py` | Match returns to subpoenas |
| `pqg_03_score_fulfillment.py` | Score clause fulfillment |
| `pqg_04_build_graph.py` | Build prosecutorial query graph |
| `pqg_05_report.py` | Generate PQG investigation reports |

### Data Integrity

| Tool | Description |
|------|-------------|
| `find_missing_efta.py` | Gap detection across EFTA numbering |
| `recover_missing_efta.py` | Recover missing EFTAs from DOJ server or forensic carving |
| `run_post_ingestion_pipeline.sh` | Chain all post-ingestion steps |

### Recovered Corrupted PDFs

The [`recovered_corrupted_pdfs/`](recovered_corrupted_pdfs/) directory contains text forensically recovered from 5 corrupted PDFs through byte-level carving: `EFTA00593870`, `EFTA00597207`, `EFTA00645624`, `EFTA01175426`, `EFTA01220934`. See: [Corrupted PDF Forensics](https://epstein-data.com/reports/evidence/CORRUPTED_PDF_FORENSICS.html)

---

## Raw Data Sources

| Source | Contents |
|--------|----------|
| [DOJ Epstein Library](https://www.justice.gov/epstein) | Datasets 1-12 (individual PDFs) |
| [Archive.org DS9](https://archive.org/download/Epstein-Dataset-9-2026-01-30/full.tar.bz2) | 103.6 GB, largest single dataset |
| [Archive.org DS11](https://archive.org/download/Epstein-Data-Sets-So-Far/DataSet%2011.zip) | 25.6 GB, 267K PDFs |
| [Archive.org DS1-5](https://archive.org/details/combined-all-epstein-files/) | First 5 datasets combined |
| [House Oversight](https://oversight.house.gov/release/oversight-committee-releases-epstein-records-provided-by-the-department-of-justice/) | Estate documents, DOJ-provided records |

See [COMMUNITY_PLATFORMS.md](https://github.com/rhowardstone/epstein-research/blob/main/COMMUNITY_PLATFORMS.md) for a directory of 79+ community tools, mirrors, and analysis platforms.

---

## Integration Notes

- **EFTA numbers are the universal key.** Every document in the DOJ release has one.
- **Join on `efta_number`**, not `document_id`, when linking across databases.
- **FTS5 search**: `SELECT ... FROM pages_fts WHERE pages_fts MATCH 'term'` — join on `rowid`.
- Entity `efta_numbers` arrays give cross-references: "this entity appears in these documents."
- Image filename format: `EFTA{number}_p{page}_i{index}_{hash}.png`.

## License

Analysis of public government records released under the Epstein Files Transparency Act (Public Law 119-38). Underlying documents are U.S. government works. Structured data released under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).

## Contact

Questions or corrections? Use the feedback form at [epstein-data.com](https://epstein-data.com).


## Defective Redactions Analysis

**NEW:** [Defective Redactions in DOJ Court Filings](defective_redactions/) — Recovery of hidden text from 12,000+ court filing PDFs with faulty redactions (Dec 2025 - Feb 2026).

- **[Quick Start Guide](defective_redactions/README.md)** — Copy/paste technique, download tools, case inventory
- **[Technical Analysis](defective_redactions/docs/technical_report.md)** — Full vulnerability assessment and methodology  
- **[Public Guide](defective_redactions/docs/public_guide.md)** — Non-technical explanation for journalists and researchers

**Key Findings:** .3M+ property tax details, 6M+ in undisclosed loans, previously unknown financial entities, evidence destruction instructions — all recoverable from original Wayback Machine archives.

