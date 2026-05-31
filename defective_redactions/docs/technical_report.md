# Defective Redactions in USVI v. JPMorgan Court Filings: Technical Report

*Analysis of recoverable redacted text from Wayback-archived DOJ-published court filings*

**Date:** April 23, 2026
**Status:** Technical Report
**Scope:** 1,840 PDFs comprising the full public docket of *Government of the United States Virgin Islands v. JPMorgan Chase Bank, N.A.*, No. 1:22-cv-10904 (S.D.N.Y.)

---

## Executive Summary

The DOJ published court filings in the USVI v. JPMorgan case to `justice.gov/multimedia/` as text-based PDFs. In at least one pleading — the Second Amended Complaint, preserved on Wayback from December 2025 captures — black rectangles were rendered on top of sensitive text rather than replacing it. The underlying text remains in the PDF content stream and is recoverable via standard text-extraction tools.

### Key findings

- **Docket analyzed:** 1,840 PDFs (the full `Court Records/.../USVI v. JPMorgan...` subdirectory on Wayback)
- **Documents with recoverable hidden text:** **5**
- **Unique documents with recoverable hidden text:** **1** — the Second Amended Complaint, re-filed as an exhibit in four later motions (`001-01.pdf`, `016-01.pdf`, `047-02.pdf`, `109-01.pdf`, `119-01.pdf`)
- **Fragments recovered per copy:** 57 (285 total across the five filings)
- **DOJ remediation:** By Feb 25, 2026, live `justice.gov` URLs served image-based PDFs (invisible OCR overlays, file sizes ~10× larger). Text extraction from current versions yields no recoverable redacted content.
- **Archive:** Defective originals remain on Internet Archive Wayback Machine.

### What we are NOT claiming

- **Not** a case-wide vulnerability: 1,835 of 1,840 files in this docket either redacted properly, were image-based from the start, or had no redactions.
- **Not** a cross-case claim: we have not yet analyzed Giuffre v. Maxwell, US v. Maxwell criminal, or other dockets. Any claim about defect rates in those cases is future work.
- **Not** an OCR-derived transcription: recovered text is read from the PDF text layer, not re-OCR'd from pixels.

---

## The Vulnerability

**Defective method (the original publication):**
1. Text rendered in normal mode (PDF text-showing operators `Tj` / `TJ` with text rendering mode `Tr=0`)
2. Black rectangles drawn over the rendered text
3. **Result:** text remains in the content stream; copy/paste or text extraction recovers it

**Secure method (used for the replacement versions):**
1. Document rasterized to page-sized images
2. OCR re-applied (invisible overlay, `Tr=3`) for searchability
3. **Result:** redacted content is destroyed — it exists only inside the rasterized pixels, and even those have the black bars baked in

---

## Detection Methodology

Our detection is **pixel-verified**, not content-stream-heuristic. Earlier iterations using only content-stream analysis produced large numbers of false positives (visible body text flagged as redacted, phantom rectangles flagged as black fills when PDF specs default fill-color is black).

### Pipeline

For each page of each PDF:

1. **Render** the page at 100 DPI to an RGB bitmap using PyMuPDF.
2. **Extract words** with their bounding boxes via PyMuPDF's `get_text("words")`.
3. **Per-word pixel check:** for each word, compute the fraction of pixels inside its bbox where all three RGB channels are < 50 (i.e., near-black). If ≥75% of the bbox is near-black, the word is visually covered.
4. **Pre-filter: scanned pages.** Skip pages where a single embedded image covers ≥70% of page area. OCR word bboxes on scanned pages align with scan artifacts (smudges, rule lines), not real text.
5. **Pre-filter: image overlap.** Skip words whose bbox overlaps any embedded image on the page. Press-release banners and news-article photos contain white-on-dark design text that otherwise passes the pixel check as a false positive.
6. **Group** consecutive hidden words on the same line into fragments.
7. **Post-filter: court-header drop.** Drop fragments that are mostly a docket stamp (pattern: `Case 1:22-cv-10904 Document X-Y Filed MM/DD/YY Page N of M`) — these are overlaid on the top of every filed page and sometimes fall behind redaction bars without being substantive content.
8. **Post-filter: substantive-content check.** Drop fragments shorter than 4 characters, fragments with no 3+ letter run, or fragments that are majority-punctuation.

Source: [`tools/extract_hidden_text.py`](../tools/extract_hidden_text.py).

### Why RGB, not grayscale

Grayscale luminance of blue text (e.g., hyperlink blue RGB 34,121,190) is ~90, but each channel ≥50 — a grayscale threshold of 50 would flag colored body text in some court filings. The per-channel RGB test rejects these while still catching genuine near-black redaction fills (typical RGB channels 0–40).

---

## Case Study: The Second Amended Complaint

**File:** `001-01.pdf` (primary filing); also filed as `016-01.pdf`, `047-02.pdf`, `109-01.pdf`, `119-01.pdf`
**Original size:** 795 KB (text-based)
**Remediated size:** 7.8 MB (rasterized + invisible OCR)
**Wayback URL:** `https://web.archive.org/web/20251228132625/https://www.justice.gov/multimedia/Court%20Records/Government%20of%20the%20United%20States%20Virgin%20Islands%20v.%20JPMorgan%20Chase%20Bank%2C%20N.A.%2C%20No.%20122-cv-10904%20%28S.D.N.Y.%202022%29/001-01.pdf`

### Recovered content samples

**Page 18 (entity list):**
> "Financial Strategy Group, Ltd.; Financial Trust, Inc.; FT Real Estate Inc.; Gratitude America, Inc.; Hyperion Air, Inc."

**Page 19 (Foundation check flows):**
> "signed Foundation account checks for over $400,000 made payable to young female models and actresses, including a former Russian model who received over $380,000 through monthly payments of $8,333"

**Page 24 (entity loans):**
> "$16 million net $10 million net loans that are still outstanding to Indyke- and Kahn-related entities"

**Page 41 (obstruction allegations):**
> "Epstein also instructed one or more Epstein Enterprise participant-witnesses to destroy evidence relevant to ongoing court proceedings involving Defendants' criminal sex trafficking and abuse conduct."

### Verification

- **Original (`001-01.pdf`):** 795KB text-based PDF with recoverable redactions
- **Remediated:** current `justice.gov` URL serves 7.8MB image-based PDF — no recoverable text
- **Exhibit re-filings** (`016-01.pdf`, `047-02.pdf`, `109-01.pdf`, `119-01.pdf`): identical content, identical fragments recovered

---

## Systematic Results Across the Docket

| Classification | Files | Notes |
|----------------|-------|-------|
| **Defective — hidden text recovered** | 5 | All copies of the Second Amended Complaint |
| **Other (properly redacted, image-based, or no redactions)** | 1,835 | No recoverable hidden text |

Total PDFs analyzed: 1,840.

**Category breakdown of recovered fragments** (across all 5 copies combined):

| Category | Fragments |
|----------|-----------|
| Financial | 100 |
| Other | 100 |
| Names | 45 |
| Entity | 25 |
| Legal | 15 |
| **Total** | **285** |

Per unique document: ~57 fragments (285 ÷ 5 copies).

Full machine-readable catalog: [`COMPREHENSIVE_REDACTION_CATALOG.csv`](../COMPREHENSIVE_REDACTION_CATALOG.csv) (fields: document, page, category, text_fragment, char_count, wayback_url). Excel version with pre-built filters and freeze panes: [`DEFECTIVE_REDACTIONS_CATALOG.xlsx`](../DEFECTIVE_REDACTIONS_CATALOG.xlsx).

---

## Archive Access

### Wayback URL pattern

```
https://web.archive.org/web/TIMESTAMP/https://www.justice.gov/multimedia/Court%20Records/Government%20of%20the%20United%20States%20Virgin%20Islands%20v.%20JPMorgan%20Chase%20Bank%2C%20N.A.%2C%20No.%20122-cv-10904%20%28S.D.N.Y.%202022%29/FILE.pdf
```

The timestamp `20251228132625` reliably serves the defective originals for every file we tested.

### Example retrieval

```bash
curl -o 001-01.pdf "https://web.archive.org/web/20251228132625/https://www.justice.gov/multimedia/Court%20Records/Government%20of%20the%20United%20States%20Virgin%20Islands%20v.%20JPMorgan%20Chase%20Bank%2C%20N.A.%2C%20No.%20122-cv-10904%20%28S.D.N.Y.%202022%29/001-01.pdf"
```

---

## Tools

### Single-file recovery

```bash
pip install pymupdf
python3 tools/extract_hidden_text.py 001-01.pdf
python3 tools/extract_hidden_text.py 001-01.pdf --pages 15-25
python3 tools/extract_hidden_text.py 001-01.pdf --json
```

Output: per-page list of recovered text fragments.

### Bulk case downloader

```bash
python3 tools/download_case.py "usvi v. jpmorgan"
```

Downloads the full docket from Wayback using the case-specific URL prefix.

---

## Timeline

- **Dec 19, 2025:** Wayback first captures of the defective originals
- **Through Feb 2026:** Defective originals remain live on `justice.gov/multimedia/`
- **By Feb 25, 2026:** DOJ has replaced the originals with image-based PDFs; file sizes increase ~10× (795 KB → 7.8 MB for `001-01.pdf`)
- **Present:** Defective originals accessible only via Internet Archive

We have not independently verified the exact date DOJ began the replacement, only that the replacement was complete by the Feb 25 check.

---

## Effectiveness of DOJ Remediation

The remediation appears **technically complete** for currently-served files:
- Current `justice.gov` URLs serve rasterized PDFs with invisible OCR overlays
- Text extraction from current versions yields no recoverable redacted content
- File sizes are consistent with rasterization (~10× increase)

The remediation does **not**, however, affect archived copies. The defective originals persist on the Internet Archive and are therefore recoverable indefinitely.

---

## Limitations

- **Single case.** This report is limited to USVI v. JPMorgan. The same failure mode may or may not exist in other Epstein-related dockets. That analysis is future work.
- **Text-layer dependent.** The extractor relies on the PDF's text layer. If a document was published as a pure raster with no text layer, the extractor finds nothing — which is the correct behavior, but means we can't distinguish "properly redacted" from "has secrets but hidden by rasterization" in that class of files.
- **Conservative false-negative rate.** The 75% dark-fraction threshold and image-overlap filter are tuned to eliminate false positives we observed during development. Edge cases — e.g., a redaction bar that is slightly translucent, or a redaction that overlaps a figure — will be missed.

---

## Future Work

- Extend analysis to Giuffre v. Maxwell (2,978 files), US v. Maxwell criminal (1,318 files), and the broader civil-case set
- Cross-reference recovered passages against the SDNY public docket to identify the precise paragraphs redacted under seal
- Publish a reproducible pipeline notebook for independent verification

---

*All tools and techniques described here are standard document-forensics practice applied to public court records. The recoverable text was created by the filer, redacted defectively during government publication, and preserved by the Internet Archive.*
