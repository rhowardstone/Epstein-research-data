# Recovered Text Catalog — USVI v. JPMorgan Defective Redactions

**All text recovered from defectively-redacted PDFs in this case**

Machine-readable versions: [`COMPREHENSIVE_REDACTION_CATALOG.csv`](COMPREHENSIVE_REDACTION_CATALOG.csv) · [`DEFECTIVE_REDACTIONS_CATALOG.xlsx`](DEFECTIVE_REDACTIONS_CATALOG.xlsx)

---

## Scope

**Case:** *Government of the United States Virgin Islands v. JPMorgan Chase Bank, N.A.*, No. 1:22-cv-10904 (S.D.N.Y.)

**Source:** Internet Archive captures of `justice.gov/multimedia/Court Records/...`, December 2025 – February 2026

**Files processed:** 1,840 (full docket)

**Files with recoverable hidden text:** 5 — all copies of the same pleading (the Second Amended Complaint), filed once as the primary pleading and four more times as exhibits in subsequent motions:

| File | Filed as | Fragments recovered |
|------|----------|---------------------|
| `001-01.pdf` | Second Amended Complaint | 57 |
| `016-01.pdf` | Exhibit 1 to a later motion | 57 |
| `047-02.pdf` | Exhibit to a later motion | 57 |
| `109-01.pdf` | Exhibit to a later motion | 57 |
| `119-01.pdf` | Exhibit to a later motion | 57 |

All five files contain identical recoverable content — 285 fragments total, representing **one unique document**. The remainder of the 1,840-file docket either redacted properly (content truly removed), was published as image-based PDFs from the start, or had no redactions at all.

---

## Executive Findings (from the Second Amended Complaint)

### Financial flows previously redacted

- Signed Foundation-account checks totaling **over $400,000** made payable to young female models and actresses
- A former Russian model who received **over $380,000** through monthly payments of **$8,333**
- **$60,000** wired to young women at foreign beneficiary banks in February and March 2016
- **$50,000** to women with Eastern European surnames, including one known to have recruited young women and girls
- **21 separate $1,000 withdrawals** on nearly every business day from April 9, 2019 to May 8, 2019 (Indyke had signatory authority)
- **$16 million net + $10 million net** in loans still outstanding to Indyke- and Kahn-related entities
- A check signed to "the immigration lawyer in New York who was involved in one or more forced marriages arranged among Epstein's victims to secure a victim's immigration status. The check's memo line references the former Russian model's last name."

### Property-tax payments previously redacted

| Property | Amount | Date |
|----------|--------|------|
| Santa Fe | $106,394.60 | Nov 6, 2018 |
| Santa Fe | $55,770.41 + $113,679.56 | 2017 |
| New York City | $336,471.87 | 2018 |
| New York City | $327,497.48 + $6,487.04 | 2017 |
| Palm Beach | $196,673.56 | Nov 6, 2018 |
| Palm Beach | $191,941.52 | Oct 31, 2017 |

Sum across disclosed redactions: **$1,334,899.04**

### Entity names previously redacted

- Financial Strategy Group, Ltd.
- Financial Trust, Inc.
- Hyperion Air, Inc.
- JSC Interiors, LLC
- Southern Trust Company, Inc. (payroll)

### Operational allegations previously redacted

**JSC Interiors, LLC:**
> "JSC Interiors, LLC (JSC), who was forced [as interior designer]" … "paid employee of Southern Trust Company, which did not actually or even pretend to perform interior design or dentistry services"

**Witness tampering and evidence destruction:**
> "Defendants also attempted to conceal their criminal sex trafficking and abuse conduct by paying large sums of money to participant-witnesses, including by paying for their attorneys' fees and case costs in litigation related to this conduct. Epstein also threatened harm to victims and helped release damaging stories about them to damage their credibility when they tried to go public. Epstein also instructed one or more Epstein Enterprise participant-witnesses to destroy evidence relevant to ongoing court proceedings involving Defendants' criminal sex trafficking and abuse conduct."

---

## Category Breakdown (285 fragments across 5 copies)

| Category | Count |
|----------|-------|
| Financial | 100 |
| Other | 100 |
| Names | 45 |
| Entity | 25 |
| Legal | 15 |

These are the rollup totals across all five filings. Per unique document: 20 financial, 20 other, 9 names, 5 entity, 3 legal (≈57 fragments).

---

## Methodology

1. Render each page of each PDF at 100 DPI to an RGB bitmap.
2. Use PyMuPDF's text layer to get every word and its bounding box.
3. For each word, check whether ≥75% of pixels inside its bbox have all three RGB channels below 50 (i.e., visually covered by a near-black rectangle).
4. Filter: skip pages that are mostly a single embedded image (scanned exhibits), and skip words whose bbox overlaps any embedded image (press-release banners, news-article photos) — these produce false positives where white-on-dark design elements look like redactions.
5. Group hidden words into line-level fragments.
6. Post-filter: drop court-docket stamps ("Case 1:22-cv-10904 Document … Filed …") and fragments that are mostly punctuation or too short to be substantive.

The extractor source is `tools/extract_hidden_text.py` in this repo.

## Verification

- Original (001-01.pdf, 795KB, text-based): recoverable redactions present
- Corresponding file on current justice.gov: image-based PDF with invisible OCR overlay — no recoverable text
- Content verified against the public EFTA corpus: the un-redacted passages match the document structure and cross-reference to related filings in the same docket

## Limitations

- **One case only.** This release covers USVI v. JPMorgan. Other cases (Giuffre v. Maxwell, US v. Maxwell criminal) have not been analyzed yet.
- **Text-layer dependent.** If the underlying PDF had no text layer (pure image), the extractor finds nothing — correctly — but that doesn't prove those PDFs had no hidden content, only that they're not defectively redacted in the same way.
- **Confidence on amounts and dates.** The text layer is authoritative for characters (not OCR-derived). Dollar figures and dates above are transcribed from text-layer extraction, not OCR.

## Ethical framing

This catalog documents **institutional accountability** information — payment flows, entity structures, witness-tampering allegations — recovered from a public court filing. Victim names and identifying details are not extracted and not included. Where the complaint refers to victims it does so anonymously; we preserve that anonymity.

---

*Raw research dataset. All content recovered from publicly-filed court records preserved on the Internet Archive.*
