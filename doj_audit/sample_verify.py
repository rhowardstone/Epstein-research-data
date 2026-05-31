#!/usr/bin/env python3
"""
Statistical sample verification of 67,784 flagged-as-404 documents.

Uses Playwright to pass BOTH the Akamai bot check and DOJ age gate,
then checks each URL ONE AT A TIME with delays to avoid rate limiting.

Reads EFTAs and datasets from RESCAN_FINAL_RESULTS.csv (DB-sourced datasets).
Does NOT recompute datasets from the boundary table.

Canary checks every 50 requests to ensure session is still valid.
"""

import csv
import random
import time
import sys
import math
from pathlib import Path
from playwright.sync_api import sync_playwright

# --- Config ---
SAMPLE_SIZE = 500
DELAY_BETWEEN_REQUESTS = 0.3  # seconds
CANARY_INTERVAL = 50  # re-check canary every N requests
RANDOM_SEED = 42

RESCAN_CSV = Path("/atb-data/rye/dump/epstein_files/doj_audit/RESCAN_FINAL_RESULTS.csv")
OUTPUT_CSV = Path("/atb-data/rye/dump/epstein_files/doj_audit/sample_verification_results.csv")

DOJ_BASE = "https://www.justice.gov/epstein/files/DataSet%20{ds}/{efta}.pdf"
CANARY_GOOD = ("EFTA00000001", "1")  # Known live document
CANARY_BAD = ("EFTA99999999", "9")   # Known non-existent

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def load_404s():
    """Load all documents with status=404 from the rescan CSV."""
    docs = []
    with open(RESCAN_CSV) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["status"] == "404":
                docs.append({
                    "efta": row["efta"],
                    "dataset": row["dataset"],
                    "original_cl": row.get("content_length", ""),
                    "original_lm": row.get("last_modified", ""),
                })
    return docs


def build_url(efta, ds):
    """Build justice.gov URL from EFTA and dataset."""
    return DOJ_BASE.format(ds=ds, efta=efta)


def check_one(page, url):
    """Check a single URL via fetch() inside the browser. Returns dict."""
    result = page.evaluate("""async (url) => {
        try {
            const r = await fetch(url, {method: 'HEAD', redirect: 'follow'});
            return {
                status: r.status,
                content_type: r.headers.get('content-type') || '',
                content_length: r.headers.get('content-length') || '',
            };
        } catch(e) {
            return {status: -1, content_type: 'error:' + e.message, content_length: ''};
        }
    }""", url)
    return result


def classify(result):
    """Classify a fetch result as LIVE, REMOVED, or OTHER."""
    s = result["status"]
    ct = result["content_type"]
    if s == 200 and "pdf" in ct.lower():
        return "LIVE"
    elif s == 404:
        return "REMOVED"
    elif s == 200 and "html" in ct.lower():
        return "AGE_GATE"
    elif s == 401:
        return "RATE_LIMITED"
    elif s == 403:
        return "FORBIDDEN"
    else:
        return f"OTHER_{s}"


def check_canary(page, label=""):
    """Validate session with known-good and known-bad URLs. Returns True if OK."""
    good_url = build_url(*CANARY_GOOD)
    bad_url = build_url(*CANARY_BAD)

    good = check_one(page, good_url)
    bad = check_one(page, bad_url)

    good_ok = good["status"] == 200 and "pdf" in good["content_type"].lower()
    bad_ok = bad["status"] == 404

    if good_ok and bad_ok:
        print(f"  Canary OK {label}: good=200+pdf, bad=404", flush=True)
        return True
    else:
        print(f"  CANARY FAILED {label}: good={good['status']} ({good['content_type']}), "
              f"bad={bad['status']}", flush=True)
        return False


def pass_age_gate(page):
    """Navigate through the DOJ age gate and bot check."""
    age_url = (
        "https://www.justice.gov/age-verify"
        "?destination=/epstein/files/DataSet%201/EFTA00000001.pdf"
    )
    print(f"Navigating to age gate...", flush=True)
    page.goto(age_url, wait_until="networkidle", timeout=60000)
    print(f"  URL: {page.url}", flush=True)

    # Click the Yes button via JS (avoids overlay interception issues)
    try:
        page.evaluate("document.querySelector('#age-button-yes').click()")
        page.wait_for_load_state("networkidle", timeout=30000)
        print(f"  Age gate passed. URL: {page.url}", flush=True)
    except Exception as e:
        print(f"  Age gate click failed: {e}", flush=True)
        print(f"  Trying direct PDF URL...", flush=True)
        page.goto(
            "https://www.justice.gov/epstein/files/DataSet%201/EFTA00000001.pdf",
            wait_until="networkidle", timeout=60000,
        )

    # Wait for cookies to settle
    page.wait_for_timeout(2000)

    # Verify cookies
    cookies = page.context.cookies()
    cookie_names = [c["name"] for c in cookies]
    has_age = "justiceGovAgeVerified" in cookie_names
    print(f"  Cookies ({len(cookies)}): {cookie_names}", flush=True)
    print(f"  Age cookie present: {has_age}", flush=True)

    if not has_age:
        print("  WARNING: No age verification cookie!", flush=True)

    return has_age


def main():
    # Load data
    print(f"Loading 404s from {RESCAN_CSV}...", flush=True)
    all_404s = load_404s()
    print(f"Loaded {len(all_404s):,} documents with 404 status", flush=True)

    # Random sample
    random.seed(RANDOM_SEED)
    sample = random.sample(all_404s, min(SAMPLE_SIZE, len(all_404s)))
    print(f"Selected {len(sample)} for verification (seed={RANDOM_SEED})", flush=True)

    # Sample composition
    has_lm = sum(1 for d in sample if d["original_lm"].strip())
    no_lm = len(sample) - has_lm
    print(f"  With Last-Modified: {has_lm}", flush=True)
    print(f"  Without Last-Modified: {no_lm}", flush=True)

    # Verify all datasets are present
    missing_ds = [d for d in sample if not d["dataset"].strip()]
    if missing_ds:
        print(f"ERROR: {len(missing_ds)} samples have no dataset!", flush=True)
        sys.exit(1)

    # Launch browser
    with sync_playwright() as p:
        print("\nLaunching browser...", flush=True)
        browser = p.firefox.launch(headless=True)
        context = browser.new_context(user_agent=UA)
        page = context.new_page()

        # Pass age gate
        if not pass_age_gate(page):
            print("Could not pass age gate. Continuing anyway...", flush=True)

        # Canary check
        print("\nInitial canary check...", flush=True)
        if not check_canary(page, "initial"):
            print("FATAL: Initial canary failed. Exiting.", flush=True)
            browser.close()
            sys.exit(1)

        # Open output CSV
        print(f"\nStarting scan of {len(sample)} documents...", flush=True)
        print(f"Delay between requests: {DELAY_BETWEEN_REQUESTS}s", flush=True)
        print(f"Canary check interval: every {CANARY_INTERVAL} requests\n", flush=True)

        results = []
        start_time = time.time()

        with open(OUTPUT_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "efta", "dataset", "url", "verify_status", "verify_content_type",
                "verify_content_length", "verdict", "original_cl", "original_lm",
            ])
            writer.writeheader()

            for i, doc in enumerate(sample):
                efta = doc["efta"]
                ds = doc["dataset"]
                url = build_url(efta, ds)

                result = check_one(page, url)
                verdict = classify(result)

                row = {
                    "efta": efta,
                    "dataset": ds,
                    "url": url,
                    "verify_status": result["status"],
                    "verify_content_type": result["content_type"],
                    "verify_content_length": result["content_length"],
                    "verdict": verdict,
                    "original_cl": doc["original_cl"],
                    "original_lm": doc["original_lm"],
                }
                writer.writerow(row)
                f.flush()
                results.append(verdict)

                # If we get rate-limited or blocked, stop immediately
                if verdict in ("RATE_LIMITED", "FORBIDDEN", "AGE_GATE"):
                    print(f"\n  BLOCKED at doc {i+1}: {efta} -> {verdict}", flush=True)
                    print(f"  Stopping scan. Results so far saved to {OUTPUT_CSV}", flush=True)
                    break

                # Progress every 25
                if (i + 1) % 25 == 0:
                    elapsed = time.time() - start_time
                    live = results.count("LIVE")
                    removed = results.count("REMOVED")
                    other = len(results) - live - removed
                    rate = (i + 1) / elapsed
                    eta = (len(sample) - i - 1) / rate if rate > 0 else 0
                    print(f"  [{i+1}/{len(sample)}] LIVE={live} REMOVED={removed} "
                          f"OTHER={other} ({rate:.1f}/s, ETA {eta:.0f}s)", flush=True)

                # Canary check
                if (i + 1) % CANARY_INTERVAL == 0 and (i + 1) < len(sample):
                    if not check_canary(page, f"@{i+1}"):
                        print(f"  Canary failed at doc {i+1}. Stopping.", flush=True)
                        break

                time.sleep(DELAY_BETWEEN_REQUESTS)

        browser.close()

    # Summary
    elapsed = time.time() - start_time
    total = len(results)
    print(f"\nDone in {elapsed:.0f}s. {total} documents checked.", flush=True)
    print(f"Results saved to {OUTPUT_CSV}", flush=True)

    from collections import Counter
    verdicts = Counter(results)
    print(f"\nVERDICTS ({total} sampled of 67,784):", flush=True)
    for v, count in verdicts.most_common():
        pct = count / total * 100
        print(f"  {v}: {count} ({pct:.1f}%)", flush=True)

    live_count = verdicts.get("LIVE", 0)
    removed_count = verdicts.get("REMOVED", 0)

    if total > 0:
        false_positive_rate = live_count / total
        genuine_rate = 1 - false_positive_rate
        estimated_genuine = int(67784 * genuine_rate)

        z = 1.96
        se = math.sqrt(false_positive_rate * (1 - false_positive_rate) / total)
        ci_low = int(67784 * max(0, genuine_rate - z * se))
        ci_high = int(67784 * min(1, genuine_rate + z * se))

        print(f"\nFalse positive rate: {false_positive_rate:.4f} ({live_count}/{total})", flush=True)
        print(f"Estimated genuinely removed: {estimated_genuine:,}", flush=True)
        print(f"95% CI: [{ci_low:,}, {ci_high:,}]", flush=True)

        # Breakdown by Last-Modified group
        lm_results = [(r, d) for r, d in zip(results, sample[:total]) if d["original_lm"].strip()]
        no_lm_results = [(r, d) for r, d in zip(results, sample[:total]) if not d["original_lm"].strip()]

        print(f"\nBREAKDOWN BY GROUP:", flush=True)
        if lm_results:
            lm_live = sum(1 for r, _ in lm_results if r == "LIVE")
            n = len(lm_results)
            print(f"  With Last-Modified ({n} sampled, ~47,430 total):", flush=True)
            print(f"    LIVE (false positive): {lm_live} ({lm_live/n*100:.1f}%)", flush=True)
            print(f"    REMOVED (genuine):     {n - lm_live} ({(n-lm_live)/n*100:.1f}%)", flush=True)

        if no_lm_results:
            no_lm_live = sum(1 for r, _ in no_lm_results if r == "LIVE")
            n = len(no_lm_results)
            print(f"  Without Last-Modified ({n} sampled, ~20,354 total):", flush=True)
            print(f"    LIVE (false positive): {no_lm_live} ({no_lm_live/n*100:.1f}%)", flush=True)
            print(f"    REMOVED (genuine):     {n - no_lm_live} ({(n-no_lm_live)/n*100:.1f}%)", flush=True)


if __name__ == "__main__":
    main()
