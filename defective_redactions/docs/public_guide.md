# Hidden Text in Court Documents: A Simple Guide

*How to recover redacted information from a court filing in the USVI v. JPMorgan Epstein case*

**Updated:** April 23, 2026
**For:** Journalists, researchers, and the general public

---

## What We Found

**One pleading in a major Epstein-related lawsuit was published online with black "redaction" bars that don't actually remove the underlying text. The hidden text can be recovered by anyone with simple copy and paste.**

The document: the **Second Amended Complaint** in *Government of the United States Virgin Islands v. JPMorgan Chase Bank, N.A.*, No. 1:22-cv-10904 (S.D.N.Y.).

The DOJ published this filing on `justice.gov/multimedia/` as a text-based PDF. Someone drew black rectangles on top of sensitive text rather than removing the text from the file. **The text is still there underneath the black bars, and you can see it by copying the blacked-out sections and pasting them elsewhere.**

The same pleading appears **five times in the docket** — once as the primary complaint, and four more times as exhibits in later motions. All five copies have the same defective redactions.

### Real Example

Here's what we recovered from page 19 of the complaint:

> "signed Foundation account checks for over $400,000 made payable to young female models and actresses, including a former Russian model who received over $380,000 through monthly payments of $8,333"

That text was completely blacked out in the PDF viewer. Copy/paste revealed it.

---

## How This Happened

### Two ways to redact a PDF

**❌ The wrong way (what was done here):**
- Type the document normally
- Draw black rectangles on top of sensitive text
- **Result:** Text is hidden visually but still in the file

**✅ The right way:**
- Convert the page to an image ("flatten" it)
- Apply text recognition to the image so it remains searchable
- **Result:** Hidden text is permanently destroyed

### What happened after

- **December 2025:** DOJ publishes the complaint with defective redactions. The Internet Archive captures it.
- **February 2026:** DOJ replaces the originals on `justice.gov` with properly-flattened image-based versions. File sizes jump ~10×. No recoverable text in the new versions.
- **Today:** The defective originals are preserved on the Internet Archive (`web.archive.org`).

---

## What Documents Are Affected

We analyzed all **1,840 PDFs** in the USVI v. JPMorgan docket that the Internet Archive preserved. Of those:

- **Five files** had recoverable hidden text
- All five are copies of the **same complaint** — so there's one unique document's worth of recovered content
- The rest either redacted properly (text fully removed), were image-based from the start, or had no redactions at all

We have **not** yet analyzed the other major cases (Giuffre v. Maxwell, US v. Maxwell criminal). That's future work, and we can't currently say whether those dockets have the same problem.

---

## How to Access the Original

### Step 1: Open the Wayback Machine copy

`https://web.archive.org/web/20251228132625/https://www.justice.gov/multimedia/Court%20Records/Government%20of%20the%20United%20States%20Virgin%20Islands%20v.%20JPMorgan%20Chase%20Bank%2C%20N.A.%2C%20No.%20122-cv-10904%20%28S.D.N.Y.%202022%29/001-01.pdf`

### Step 2: Test for hidden text

1. **Find a page with black redaction bars** (pages 18–24 and 38–41 are good targets)
2. **Click and drag** to select the black area
3. **Copy** (Ctrl+C / Cmd+C)
4. **Paste** into a text editor (Ctrl+V / Cmd+V)

If you see text appear where the black bar was, that's a defective redaction.

---

## What Kind of Information Was Hidden

All recovered from the one complaint (and its four exhibit re-filings):

### Financial detail
- Specific dollar amounts of Foundation-account checks to named categories of recipients (models, actresses)
- Monthly payment schedules
- International wire transfer totals and dates
- Daily-withdrawal patterns
- Outstanding loan balances to named individuals' entities
- Property-tax payments across multiple residences and years

### Entity names
- Shell companies and trusts not previously disclosed in public narrative
- Relationships between entities (payroll processor, aviation, real estate)

### Allegations about obstruction
- Payments to participant-witnesses for attorney fees and litigation costs
- Instructions to destroy evidence relevant to ongoing proceedings
- Campaigns to discredit victims who attempted to go public

See [`UNCENSORED_FINDINGS_CATALOG.md`](../UNCENSORED_FINDINGS_CATALOG.md) for the full extracted text. A filterable Excel catalog is at [`DEFECTIVE_REDACTIONS_CATALOG.xlsx`](../DEFECTIVE_REDACTIONS_CATALOG.xlsx).

---

## Tools for Researchers

### Simple (no code)
Manual copy/paste on the Wayback URL above.

### Automated
```bash
pip install pymupdf
python tools/extract_hidden_text.py path/to/001-01.pdf
```

The extractor renders each page to a bitmap and, for every word in the PDF text layer, checks whether it's visually covered by a black rectangle. It skips scanned pages and image regions to avoid false positives.

---

## Legal and Ethical Notes

### This is legal research
- These are **public court documents** filed on the SDNY docket
- The **vulnerability was in the government's publication process**, not in the underlying pleading
- We use **standard document analysis techniques** — copy/paste is available in every PDF viewer

### Responsible use
- **Protect victim privacy.** The complaint refers to victims anonymously; we preserve that anonymity. Don't republish identifying details.
- **Focus on institutional accountability** — the bank, defendants' estate, shell entities, attorney coordination
- **Verify before repeating.** Pull the original Wayback PDF yourself rather than relying on second-hand summaries.

---

## Why This Matters

### Transparency
- A sworn pleading in a major federal case contained detailed financial and operational allegations that the filer chose to redact, and that the government published in a way that left those allegations recoverable.
- The recovered passages name specific dollar amounts, specific entities, and specific conduct that the parties litigated under seal.

### Historical record
The recovered text adds detail to the public record about:
- Foundation-account flows to named recipient categories
- Entity structures supporting Epstein's operations (Financial Strategy Group, Financial Trust, FT Real Estate, Gratitude America, Hyperion Air, JSC Interiors, Southern Trust)
- Evidence-handling allegations

---

## Frequently Asked Questions

**Q: Is recovering this text legal?**
A: Yes. These are public court documents. Copy/paste is standard PDF functionality.

**Q: How many documents are affected?**
A: Across the USVI v. JPMorgan case we analyzed, five — all copies of the same Second Amended Complaint. We have not yet analyzed the other Epstein-related cases, so we can't make claims about them.

**Q: Why didn't DOJ fix this earlier?**
A: PDF redaction is technically complex, and this failure mode is common across organizations. DOJ did fix it (replacing the originals with flattened versions) by February 25, 2026, but the Internet Archive preserved the originals.

**Q: Can I get in trouble for doing this?**
A: No — these are public records, and copy/paste is standard PDF functionality. Use common sense about republishing sensitive personal information.

**Q: Are other cases affected?**
A: We don't yet know. That's the next phase of work.

---

## Contact

- **Technical questions:** see [`technical_report.md`](technical_report.md)
- **Replication issues or bugs:** open a GitHub issue
- **Media inquiries:** standard research disclosure practices apply

---

*This guide demonstrates standard document analysis techniques for transparency research. All methods described use publicly available court records and standard software functionality.*
