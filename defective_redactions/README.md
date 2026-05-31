# Defective Redactions in DOJ Court Filings

**Recovery of hidden text from Jeffrey Epstein case court documents**

## What This Is

The U.S. Department of Justice published court filings from the Epstein-related USVI v. JPMorgan case to `justice.gov/multimedia/` as text-based PDFs where black "redaction" bars were drawn on top of the underlying text rather than replacing it. The hidden text can be recovered with standard PDF tools.

**Key Finding:** In at least one filing — the Second Amended Complaint in *Government of the United States Virgin Islands v. JPMorgan Chase Bank, N.A.*, No. 1:22-cv-10904 (S.D.N.Y.) — black redaction bars cover text that remains in the document's content stream. The content is recoverable via copy/paste or text extraction.

## Scope of This Release

This release covers **USVI v. JPMorgan** only. Expansion to other cases (Giuffre v. Maxwell, US v. Maxwell criminal) is future work.

| Case | Docket PDFs on Wayback | Analyzed | Docs with recoverable hidden text |
|------|------------------------|----------|------------------------------------|
| **USVI v. JPMorgan** (1:22-cv-10904) | 1,840 | **1,840 (100%)** | **5** |

The 5 documents with recoveries are all the **same complaint**, filed once as the primary pleading (`001-01.pdf`) and re-filed four more times as exhibits in related motions (`016-01.pdf`, `047-02.pdf`, `109-01.pdf`, `119-01.pdf`). So there is **one unique document's worth of recovered content**, preserved across 5 filings. We extracted **285 text fragments** total (57 per copy).

All other documents in the docket either used proper redactions (text fully removed), used image-based PDFs from the start, or had no redactions.

## Quick Demo

**Try this with the live Wayback copy:**

1. Open the Second Amended Complaint in any PDF viewer:
   `https://web.archive.org/web/20251228132625/https://www.justice.gov/multimedia/Court%20Records/Government%20of%20the%20United%20States%20Virgin%20Islands%20v.%20JPMorgan%20Chase%20Bank%2C%20N.A.%2C%20No.%20122-cv-10904%20%28S.D.N.Y.%202022%29/001-01.pdf`
2. Go to page 19 (paragraph describing Foundation checks)
3. Select the black redaction bars, copy, paste into a text editor
4. You should see: *"signed Foundation account checks for over $400,000 made payable to young female models and actresses, including a former Russian model who received over $380,000 through monthly payments of $8,333"*

## What Was Recovered

From the Second Amended Complaint (5 identical copies, 57 fragments each):

**Financial flows (previously redacted):**
- "signed Foundation account checks for over $400,000 made payable to young female models and actresses"
- "a former Russian model who received over $380,000 through monthly payments of $8,333"
- "$60,000 were transferred by wire to young women mostly at foreign beneficiary banks in February and March 2016"
- "21 separate withdrawals each in the amount of $1,000 on every but one business day from April 9, 2019 to May 8, 2019"
- "$16 million net $10 million net loans that are still outstanding to Indyke- and Kahn-related entities"

**Entity names (previously redacted):**
- Financial Strategy Group, Ltd.
- Financial Trust, Inc.
- FT Real Estate Inc.
- Gratitude America, Inc.
- Hyperion Air, Inc.
- JSC Interiors, LLC

**Property-tax detail (previously redacted):**
- Santa Fe: $106,394.60 (Nov 2018); $55,770.41 + $113,679.56 (2017)
- New York City: $336,471.87 (2018); $327,497.48 + $6,487.04 (2017)
- Palm Beach: $196,673.56 (Nov 2018); $191,941.52 (Oct 2017)

**Witness tampering / evidence destruction allegations (previously redacted):**
- "paying large sums of money to participant-witnesses, including by paying for their attorneys' fees and case costs in litigation"
- "Epstein also instructed one or more Epstein Enterprise participant-witnesses to destroy evidence relevant to ongoing court proceedings"
- "release damaging stories about them to damage their credibility when they tried to go public"

See [`UNCENSORED_FINDINGS_CATALOG.md`](UNCENSORED_FINDINGS_CATALOG.md) for the full extracted text. The complete machine-readable catalog is in [`DEFECTIVE_REDACTIONS_CATALOG.xlsx`](DEFECTIVE_REDACTIONS_CATALOG.xlsx) and [`COMPREHENSIVE_REDACTION_CATALOG.csv`](COMPREHENSIVE_REDACTION_CATALOG.csv).

## How to Reproduce

### Manual (no code)

1. Go to `web.archive.org`, look up the URL above
2. Download the PDF
3. Open, navigate to a page with black bars, select the black area, copy, paste elsewhere

### Automated

```bash
pip install pymupdf

# Extract hidden text from one PDF
python tools/extract_hidden_text.py path/to/001-01.pdf

# Bulk download a case's docket from Wayback
python tools/download_case.py "usvi v. jpmorgan"
```

The detection method: render each page to a bitmap, then for each word reported by the PDF's text layer, check whether the word's bounding box overlaps a region of dark pixels in the render. A word that is in the text layer but visually covered by a black bar is a defective redaction. Words that overlap embedded images, or appear on fully scanned pages, are filtered out to avoid false positives.

## Timeline

- **Dec 19, 2025:** Wayback Machine captures the Second Amended Complaint with defective redactions intact
- **Through Feb 2026:** Defective originals remain on `justice.gov/multimedia/`
- **Feb 25, 2026:** DOJ had replaced the originals with image-based PDFs (invisible OCR overlays; file sizes ~10x larger). We have not independently verified the exact replacement date.
- **Today:** Defective originals are preserved on Internet Archive; live `justice.gov` URLs serve the remediated versions.

## Repository Contents

```
defective_redactions/
├── README.md                           # This file
├── UNCENSORED_FINDINGS_CATALOG.md      # Full extracted text, page by page
├── COMPREHENSIVE_REDACTION_CATALOG.csv # Machine-readable catalog (285 fragments)
├── DEFECTIVE_REDACTIONS_CATALOG.xlsx   # Same catalog, Excel format with filters
├── docs/
│   ├── public_guide.md                 # Non-technical explainer
│   └── technical_report.md             # Full technical methodology
├── tools/
│   ├── extract_hidden_text.py          # Single-file text recovery
│   ├── download_case.py                # Wayback downloader
│   └── requirements.txt
└── samples/                            # Example outputs
```

## Legal & Ethical Notes

### This Research is Legal
- These are **public court documents** filed on the SDNY docket, distributed by DOJ, and independently preserved by the Internet Archive.
- Recovery uses **standard PDF functionality** — copy/paste works in any PDF viewer.

### Responsible Use
- **Protect individual privacy.** Focus on institutional accountability — the bank, defendants' estate, shell entities, attorney fee arrangements — not victim identifiers.
- **Verify before republishing.** Cross-check recovered text against the original Wayback PDF.
- **Don't overclaim.** This release documents what was recoverable from one complaint's defective redactions, not a system-wide intelligence leak.

### What We Don't Do
- Republish victim names or identifying details
- Speculate beyond what the recovered text actually says
- Claim recoveries from documents we haven't independently verified

## Contact

- **GitHub Issues:** for questions, bug reports, or replication issues
- **Site feedback form:** for media inquiries (see main repository)

---

*This project demonstrates standard document forensics techniques applied to public court records. The recoverable text was created by the filer, redacted defectively during government publication, and preserved by the Internet Archive.*
