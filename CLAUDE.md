# Epstein Files — Data Repository

## What This Is

The DOJ released 2.91 million pages of Epstein case files on January 30, 2026, across 12 datasets at [justice.gov/epstein](https://www.justice.gov/epstein/). This repository contains **searchable databases** built from those files — 1,416,711 documents and 2,914,901 pages of extracted text, fully indexed for full-text search.

The databases are hosted as GitHub releases because they're too large for the repository itself (~17 GB uncompressed total).

## First-Time Setup

### 1. Check for sqlite3

```bash
sqlite3 --version
```

If missing: Mac/Linux usually have it pre-installed. On Ubuntu/Debian: `sudo apt install sqlite3`. On Windows: download from [sqlite.org/download.html](https://www.sqlite.org/download.html) and add to PATH, or use `winget install SQLite.SQLite`.

### 2. Download the databases

Use `gh release download` if available, otherwise `curl -LO`. There are two releases to download from.

#### Full text corpus (v5.0) — the main database

```bash
# Download both parts (~2.3 GB total compressed)
gh release download v5.0 --repo rhowardstone/Epstein-research-data --pattern "full_text_corpus.db.gz.*"

# Reassemble and decompress
cat full_text_corpus.db.gz.part_aa full_text_corpus.db.gz.part_ab > full_text_corpus.db.gz
gunzip full_text_corpus.db.gz
# Result: full_text_corpus.db (~6.3 GB)

# Clean up parts
rm full_text_corpus.db.gz.part_aa full_text_corpus.db.gz.part_ab
```

#### All other databases (v5.1)

```bash
gh release download v5.1 --repo rhowardstone/Epstein-research-data --pattern "*.db.gz"
gunzip concordance_complete.db.gz alteration_results.db.gz image_analysis.db.gz
```

#### Remaining databases (v4.0)

```bash
# Smaller databases not yet consolidated into v5.1
gh release download v4.0 --repo rhowardstone/Epstein-research-data --pattern "*.db.gz"
gh release download v4.0 --repo rhowardstone/Epstein-research-data --pattern "*.db"
gunzip *.db.gz
```

If `gh` is not available, download manually from:
- https://github.com/rhowardstone/Epstein-research-data/releases/tag/v5.0
- https://github.com/rhowardstone/Epstein-research-data/releases/tag/v5.1
- https://github.com/rhowardstone/Epstein-research-data/releases/tag/v4.0

### 3. Verify setup

```bash
sqlite3 full_text_corpus.db "SELECT COUNT(*) || ' documents, ' || (SELECT COUNT(*) FROM pages) || ' pages' FROM documents;"
```

Expected output: `1385916 documents, 2771231 pages`

---

## Database Reference

### `full_text_corpus.db` (6.3 GB) — Primary search database

The main database. Every page of every document, with full-text search.

**Tables:**

```sql
-- documents: one row per PDF/document
-- Columns: id, efta_number (unique), dataset (1-12, 98, 99), file_path, total_pages, file_size
SELECT * FROM documents WHERE efta_number = 'EFTA00074206';

-- pages: one row per page of each document
-- Columns: id, efta_number, page_number, text_content, char_count
SELECT * FROM pages WHERE efta_number = 'EFTA00074206' ORDER BY page_number;

-- pages_fts: FTS5 full-text search index on pages
-- Searchable columns: efta_number, text_content
SELECT * FROM pages_fts WHERE pages_fts MATCH 'search terms';
```

### `concordance_complete.db` (729 MB) — Cross-reference metadata

DOJ production metadata: original filenames, email headers, folder paths, dates, custodians, MD5 hashes.

**Key columns in `documents` table:** bates_begin, bates_end, original_filename, document_extension, original_folder_path, author, custodian, date_sent, email_from, email_to, email_cc, email_subject, efta_number

Also has: `email_threads`, `folder_inventory`, `extraction_stats`, `cross_references`

### `redaction_analysis_v2.db` (1.0 GB) — Redaction patterns

2.6 million detected redaction rectangles across 850K documents. Includes OCR of text under improperly applied redactions.

**Tables:** `redactions` (efta_number, page_number, redaction_type, ocr_text, confidence), `document_summary`, `reconstructed_pages`

### `alteration_results.db` (557 MB) — DOJ document alteration tracking

212,730 change units tracking differences between original and current versions of DOJ-hosted documents.

**Table:** `altered_files` (efta_number, dataset, diff_type, categories, removed_names_json, llm_classification, llm_sensitivity, llm_justification, anomaly_flag)

### `image_analysis.db` (762 MB) — Extracted images

92,095 images extracted from PDFs across all 14 datasets (DS1-12, DS98, DS99), analyzed with Qwen2-VL-7B vision model. FTS5 searchable by description, people, objects, and setting.

**Table:** `images` (image_name, efta_number, page_number, analysis_text, people, text_content, objects, setting, activity, notable, analyzed_at)

### `handwriting_transcriptions.db` (248 KB) — Handwritten document transcriptions

Page-level verbatim transcriptions of handwritten FBI/AUSA documents that OCR cannot read, starting with the 14 MCC inmate witness interviews from the Epstein death investigation (case 90A-NY-3151227). 54 pages across 14 witnesses, rendered at 300-400 DPI and transcribed via multimodal image analysis. FTS5 searchable.

**Table:** `transcriptions` (efta_number, page_number, document_type, case_number, serial, subject, subject_role, interview_date, location, transcript, page_of, redaction_count, unclear_count, render_dpi, render_path, source_pdf, transcribed_at, notes)

### `transcripts.db` (5 MB) — Audio/video transcriptions

1,530 media files, 375 with speech, 92K words total. Whisper large-v3 transcriptions.

**Tables:** `transcripts` (efta_number, transcript, duration_secs, word_count), `transcript_segments` (start_time, end_time, text)

### `prosecutorial_query_graph.db` (2.5 MB) — Subpoena analysis

Grand jury subpoena tracking: what was demanded vs. what was produced.

**Tables:** `subpoenas`, `rider_clauses`, `returns`, `subpoena_return_links`, `clause_fulfillment`, `investigative_gaps`

### Other databases

| Database | Size | Contents |
|----------|------|----------|
| `redaction_analysis_ds10.db` | 557 MB | DS10-specific redaction analysis |
| `knowledge_graph.db` | 782 KB | 606 entities, 2,302 relationships |
| `ocr_database.db` | 71 MB | Tesseract OCR results for scanned pages |
| `spreadsheet_corpus.db` | 4 MB | Native spreadsheet data from DS8 |
| `communications.db` | 24 MB | Extracted communications metadata |

---

## Search Cookbook

### Full-text search (fastest)

```sql
-- Search for a term using FTS5
SELECT p.efta_number, p.page_number, substr(p.text_content, 1, 500)
FROM pages_fts fts
JOIN pages p ON p.rowid = fts.rowid
WHERE pages_fts MATCH 'Leon Black'
AND p.char_count > 50
LIMIT 20;
```

FTS5 supports phrases (`"exact phrase"`), AND/OR (`term1 AND term2`), NOT (`term1 NOT term2`), prefix (`term*`), and column filters (`text_content:term`).

### LIKE search (for partial matches, wildcards)

```sql
-- Slower but catches OCR variations and partial words
SELECT efta_number, page_number, substr(text_content, 1, 500)
FROM pages
WHERE text_content LIKE '%Deutsche Bank%'
AND char_count > 50
LIMIT 20;
```

### Read a specific document

```sql
-- Get all pages of one document
SELECT page_number, text_content
FROM pages
WHERE efta_number = 'EFTA00074206'
ORDER BY page_number;
```

### Find which dataset an EFTA belongs to

```sql
SELECT efta_number, dataset, total_pages, file_path
FROM documents
WHERE efta_number = 'EFTA00074206';
```

### Person search with context

```sql
-- Find documents mentioning a person, with surrounding text
SELECT p.efta_number, p.page_number,
       substr(p.text_content,
              MAX(1, INSTR(LOWER(p.text_content), LOWER('Ghislaine Maxwell')) - 200),
              500) AS context
FROM pages p
WHERE p.text_content LIKE '%Ghislaine Maxwell%'
AND p.char_count > 50
LIMIT 30;
```

### Co-occurrence: two people in the same document

```sql
SELECT DISTINCT p1.efta_number
FROM pages p1
JOIN pages p2 ON p1.efta_number = p2.efta_number
WHERE p1.text_content LIKE '%Bill Clinton%'
AND p2.text_content LIKE '%Jeffrey Epstein%'
LIMIT 20;
```

### Search email metadata (concordance DB)

```sql
-- Find emails from a specific person
SELECT bates_begin, email_from, email_to, email_subject, date_sent, efta_number
FROM documents
WHERE email_from LIKE '%@gmail.com%'
AND email_subject IS NOT NULL
ORDER BY date_sent
LIMIT 20;
```

### Search images

```sql
-- Find images by description
SELECT image_name, efta_number, page_number, analysis_text
FROM images_fts
WHERE images_fts MATCH 'swimming pool'
LIMIT 10;
```

### Search audio transcripts

```sql
SELECT efta_number, substr(transcript, 1, 500), duration_secs, word_count
FROM transcripts
WHERE transcript LIKE '%lawyer%'
LIMIT 10;
```

### Look up subpoena gaps

```sql
-- Find investigative gaps
SELECT * FROM investigative_gaps ORDER BY gap_type;
```

### Cross-database: EFTA text + concordance metadata

```sql
-- Attach concordance to get both text content and file metadata
ATTACH 'concordance_complete.db' AS conc;

SELECT p.efta_number, p.page_number, substr(p.text_content, 1, 300),
       c.original_filename, c.email_from, c.email_to, c.date_sent
FROM pages p
JOIN conc.documents c ON c.efta_number = p.efta_number
WHERE p.text_content LIKE '%wire transfer%'
AND p.char_count > 50
LIMIT 20;
```

---

## EFTA Numbering System

EFTA (Electronic File Transfer Agreement) numbers are **per-page Bates stamps**, not per-document. A 10-page document starting at EFTA00000001 occupies EFTA00000001 through EFTA00000010.

### Dataset Boundaries

| Dataset | EFTA Range | Approximate Content |
|---------|-----------|-------------------|
| 1 | 00000001 – 00003158 | Prosecution case files |
| 2 | 00003159 – 00003857 | Grand jury materials |
| 3 | 00003858 – 00005586 | Court filings |
| 4 | 00005705 – 00008320 | NPA-era documents |
| 5 | 00008409 – 00008528 | Supplemental filings |
| 6 | 00008529 – 00008998 | Additional court records |
| 7 | 00009016 – 00009664 | Case correspondence |
| **8** | **00009676 – 00039023** | Native files (spreadsheets, media) |
| **9** | **00039025 – 01262781** | FBI investigation (531K docs) |
| **10** | **01262782 – 02205654** | SDNY prosecution (503K docs) |
| **11** | **02205655 – 02730264** | Defense materials (332K docs) |
| 12 | 02730265 – 02858497 | March 2026 expansion |
| 98 | — | FBI Vault (separate numbering) |
| 99 | — | House Oversight Committee |

### Accessing original PDFs

Every EFTA document can be viewed as a PDF from multiple sources. The DOJ is canonical but requires an age gate; two independent mirrors serve byte-identical raw PDFs with no gate.

| Source | URL Template | Notes |
|--------|-------------|-------|
| **DOJ** (canonical) | `https://www.justice.gov/epstein/files/DataSet%20{N}/EFTA{NUMBER}.pdf` | Requires age gate. Must know dataset number. |
| **RollCall** | `https://media-cdn.rollcall.com/epstein-files/EFTA{NUMBER}.pdf` | Raw PDF, no gate, no dataset needed. Full DS1-12 coverage. |
| **Kino/JDrive** | `https://assets.getkino.com/documents/EFTA{NUMBER}.pdf` | Raw PDF, no gate. DS1-7 + DS9-11. Missing DS12. |
| **Kino/JDrive (DS8)** | `https://assets.getkino.com/documents/vol00008-official-doj-latest-efta{number}.pdf` | DS8 uses a different naming convention. Note **lowercase** `efta`. |
| **JMail Viewer** | `https://jmail.world/drive/EFTA{NUMBER}.pdf` (or `vol00008-...` for DS8) | Formatted viewer with page navigation, comments. Uses Kino CDN. |

RollCall and Kino both serve files that are byte-identical to the DOJ originals (SHA-256 verified). For sourcing, use the DOJ URL as primary and RollCall as backup.

**To construct a DOJ URL**, look up the dataset number first:

```sql
SELECT dataset FROM documents WHERE efta_number = 'EFTA00074206';
-- Returns: 9
-- URL: https://www.justice.gov/epstein/files/DataSet%209/EFTA00074206.pdf
```

Do NOT guess the dataset from the EFTA number — the boundaries have gaps and irregularities.

**To construct a RollCall URL**, you only need the EFTA number:

```
https://media-cdn.rollcall.com/epstein-files/EFTA00074206.pdf
```

**To construct a Kino/JMail URL**, check the dataset first — DS8 uses a different pattern:

```python
if dataset == 8:
    url = f"https://assets.getkino.com/documents/vol00008-official-doj-latest-{efta.lower()}.pdf"
    viewer = f"https://jmail.world/drive/vol00008-official-doj-latest-{efta.lower()}.pdf"
else:
    url = f"https://assets.getkino.com/documents/{efta}.pdf"
    viewer = f"https://jmail.world/drive/{efta}.pdf"
```

The tool `tools/mirror_coverage.py` can incrementally build a coverage map verifying which mirrors host each document. It handles the DS8 naming convention automatically.

---

## Structured Data Files (in this repository)

| File | Contents |
|------|----------|
| `persons_registry.json` | 1,614 named persons with aliases and descriptions |
| `knowledge_graph_entities.json` | 606 entities (people, orgs, locations) |
| `knowledge_graph_relationships.json` | 2,302 entity relationships |
| `phone_numbers_enriched.csv` | Phone numbers found in documents |
| `extracted_entities_filtered.json` | Named entities extracted from corpus |
| `extracted_names_multi_doc.csv` | Names appearing across multiple documents |
| `image_catalog.csv.gz` | Catalog of all extracted images |
| `efta_dataset_mapping.csv` / `.json` | EFTA-to-dataset lookup table |
| `VERIFICATION_URLS.csv` | DOJ document URLs with verification status |
| `GRAND_JURY_SUBPOENAS.csv` | Identified grand jury subpoenas |

---

## Tools

Python scripts in `tools/` for working with the databases. All tools auto-detect the data directory — no path editing needed. Detection order:

1. `EPSTEIN_DATA_DIR` environment variable (if set)
2. Repository root (relative to the script's location)
3. Current working directory
4. Sibling directories of the current working directory's parent

If auto-detection fails, set the environment variable: `export EPSTEIN_DATA_DIR=/path/to/your/data`

### Investigation tools (most useful for research)

| Tool | What It Does |
|------|-------------|
| `person_search.py` | FTS5 cross-reference search with co-occurrence detection. Find who appears in docs, who appears *with* whom, export to CSV. |
| `find_missing_efta.py` | Find gaps in the EFTA production by exploiting the page-based numbering system. |
| `congressional_scorer.py` | Score EFTA documents for congressional relevance. |
| `extract_subpoena_riders.py` | Parse grand jury subpoena rider clauses from the corpus. |
| `mirror_coverage.py` | Incrementally build a coverage map of document mirrors (RollCall, Kino/JDrive). Resumable. |

### Pipeline tools (for building/updating databases)

| Tool | What It Does |
|------|-------------|
| `build_person_registry.py` | Merge person registries from 3 sources into unified JSON. |
| `build_knowledge_graph.py` | Build entity-relationship graph from corpus. |
| `redaction_detector_v2.py` | Detect redaction rectangles in PDF pages. |
| `transcribe_media.py` | Transcribe audio/video files using faster-whisper (GPU). |
| `bulk_ocr.py` / `bulk_ocr_fast.py` | Tesseract OCR on extracted images. |
| `pqg_00` through `pqg_05` | Six-phase prosecutorial query graph pipeline. |

---

## Critical Rules

1. **Never include real victim names or identifying details.** Use pseudonyms (Jane Doe, JD#1) or EFTA references. The goal is exposing the system, not retraumatizing victims.

2. **Corpus absence ≠ non-existence.** A missing document may be under seal, in a separate case, or outside the EFTA production. Do not assume DOJ non-compliance from a missing return.

3. **Present data, not conclusions.** Show what the documents say. Let readers draw their own inferences.

4. **Verify before citing.** Always confirm EFTA numbers and dataset assignments with actual database queries before constructing URLs or making claims about document contents.

---

## Companion Repository

Investigation reports analyzing these documents: [rhowardstone/epstein-research](https://github.com/rhowardstone/epstein-research) — 165+ forensic analysis reports organized by topic, each citing specific EFTA source documents.

For investigation methodology, writing standards, and fact-checking procedures, see [METHODOLOGY.md](https://github.com/rhowardstone/epstein-research/blob/main/METHODOLOGY.md) and [WRITING_GUIDE.md](https://github.com/rhowardstone/epstein-research/blob/main/WRITING_GUIDE.md) in the companion research repo.

---

## Release Manifest

Databases are hosted as GitHub releases, separate from git. After downloading, **cross-check this list** to confirm you have everything.

| Database | Release | Uncompressed | Contents |
|----------|---------|-------------|----------|
| `full_text_corpus.db` | v5.0 | 6.3 GB | Every page of every document, FTS5 indexed |
| `alteration_results.db` | v5.1 | 8.2 GB | DOJ document alteration tracking |
| `concordance_complete.db` | v5.1 | 729 MB | Production metadata, email headers |
| `image_analysis.db` | v5.1 | 762 MB | 92,095 images with vision analysis |
| `handwriting_transcriptions.db` | v5.1 | 248 KB | Handwritten document transcriptions |
| `redaction_analysis_v2.db` | v4.0 | 1.0 GB | 2.6M redaction rectangles |
| `redaction_analysis_ds10.db` | v4.0 | 557 MB | DS10-specific redaction analysis |
| `ocr_database.db` | v4.0 | 71 MB | Tesseract OCR results |
| `communications.db` | v4.0 | 24 MB | Communications metadata |
| `transcripts.db` | v4.0 | 5 MB | Audio/video transcriptions |
| `spreadsheet_corpus.db` | v4.0 | 4 MB | Native spreadsheet data |
| `prosecutorial_query_graph.db` | v4.0 | 2.5 MB | Subpoena analysis |
| `knowledge_graph.db` | v4.0 | 782 KB | Entity relationships |

**Total: 13 databases, ~17 GB uncompressed.**

If any database above is missing from your local directory, download it from the corresponding release.

---

## Updating

`git pull` only updates repo files (CSVs, JSON, tools). **Databases are in releases — you must check those separately.**

```bash
# 1. Pull repo file changes
git pull origin main

# 2. Check for new database releases
gh release list --repo rhowardstone/Epstein-research-data --limit 5
# (or check https://github.com/rhowardstone/Epstein-research-data/releases)

# 3. Compare release assets against the manifest above
# Download any new or updated databases, decompress, replace old versions

# 4. Verify
sqlite3 full_text_corpus.db "SELECT COUNT(*) FROM documents;"
# Expected: 1385916
```

---

## Read-Only Distribution

These repositories are distributed read-only. If you cloned them to investigate locally:

- **Do not push, create branches, or submit pull requests.** Your copy is for local research only.
- **Pull updates** with `git pull origin main` for repo files. Check releases separately for database updates (see Updating above).
- **All your work stays local.** Write findings to your own files outside the repo, or in a gitignored directory.
