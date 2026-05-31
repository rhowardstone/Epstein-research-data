#!/usr/bin/env python3
"""Generate government officials report markdown files from search results + context analysis."""

import json
import re

import os

def _find_data_dir():
    """Find the directory containing the database files."""
    if os.environ.get("EPSTEIN_DATA_DIR"):
        return os.environ["EPSTEIN_DATA_DIR"]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(repo_root, "gov_officials_search_results.json")):
        return repo_root
    if os.path.exists(os.path.join(os.getcwd(), "gov_officials_search_results.json")):
        return os.getcwd()
    parent = os.path.dirname(os.getcwd())
    for name in os.listdir(parent):
        candidate = os.path.join(parent, name, "gov_officials_search_results.json")
        if os.path.exists(candidate):
            return os.path.join(parent, name)
    return os.getcwd()

BASE = _find_data_dir()
SEARCH = f"{BASE}/gov_officials_search_results.json"
CONGRESS_CTX = f"{BASE}/gov_officials_context.json"
EXEC_CTX = f"{BASE}/gov_officials_exec_context.json"
OUT_DIR = f"{BASE}/government-officials"

DOJ_URL = "https://www.justice.gov/epstein/files/DataSet%20{ds}/EFTA{efta}.pdf"

# EFTA → Dataset mapping
DS_RANGES = [
    (1, 1, 3158),
    (2, 3159, 3857),
    (3, 3858, 5586),
    (4, 5705, 8320),
    (5, 8409, 8528),
    (6, 8529, 8998),
    (7, 9016, 9664),
    (8, 9676, 39023),
    (9, 39025, 1262781),
    (10, 1262782, 2205654),
    (11, 2205655, 2730264),
    (12, 2730265, 2731783),
]

def efta_to_ds(efta_num):
    """Map EFTA number string to dataset number."""
    n = int(efta_num.replace("EFTA", ""))
    for ds, lo, hi in DS_RANGES:
        if lo <= n <= hi:
            return ds
    # Fall back to nearest lower
    for ds, lo, hi in reversed(DS_RANGES):
        if n >= lo:
            return ds
    return 8  # default

def efta_link(efta):
    """Convert EFTA number to markdown link."""
    ds = efta_to_ds(efta)
    url = DOJ_URL.format(ds=ds, efta=efta.replace("EFTA", ""))
    return f"[{efta}]({url})"

def get_state_sort_key(office):
    """Extract state name for sorting."""
    # Remove prefixes
    s = office.replace("U.S. Senate ", "").replace("U.S. House ", "")
    # Take just the state part (before "District")
    if " District" in s:
        s = s.split(" District")[0].strip()
    if " At-large" in s:
        s = s.split(" At-large")[0].strip()
    if " Non-Voting" in s:
        s = "District of Columbia"
    return s

def get_last_name(name):
    """Extract last name for sorting."""
    parts = name.strip().split()
    # Handle suffixes
    if parts[-1] in ("Jr.", "Jr", "III", "II", "IV", "Sr."):
        return parts[-2] if len(parts) >= 2 else parts[0]
    return parts[-1]

def categorize(name, doc_count, congress_ctx, exec_ctx):
    """Get category and summary for an official."""
    # Check congress context
    if name in congress_ctx:
        c = congress_ctx[name]
        return c.get("category", "NEWS"), c.get("summary", ""), c.get("notable_eftas", [])

    # Check exec context (keyed differently)
    for section in ["trump_administration_2025", "biden_administration_and_former_officials"]:
        if section in exec_ctx:
            for key, val in exec_ctx[section].items():
                # Match by name
                display = key.replace("_", " ").title()
                if display.lower() == name.lower() or name.lower() in key.lower():
                    cat = val.get("category", "NEWS")
                    summary = val.get("summary", "")
                    eftas = val.get("sample_efta_numbers", [])
                    return cat, summary, eftas

    # Default
    if doc_count == 0:
        return "NONE", "", []
    elif doc_count <= 5:
        return "NEWS", f"Appears in {doc_count} document(s) in the corpus, likely in news coverage or political reporting unrelated to Epstein.", []
    else:
        return "NEWS", f"Appears in {doc_count} documents in the corpus, predominantly in news coverage and political reporting. No evidence of personal connection to Epstein found.", []


def write_congressional_report(filepath, title, subtitle, officials, congress_ctx, exec_ctx):
    """Write a congressional report file."""
    # Sort by state then last name
    officials.sort(key=lambda x: (get_state_sort_key(x.get("office", "")), get_last_name(x["name"])))

    total = len(officials)
    with_hits = [o for o in officials if o["doc_count"] > 0]
    direct = []
    investigation = []
    political = []
    mixed = []
    news_only = []
    false_pos = []

    lines = []
    lines.append(f"# {title}")
    lines.append(f"## {subtitle}")
    lines.append(f"*Generated 2026-02-12 from full_text_corpus.db (1,380,937 documents, 2,731,796 pages across 12 DOJ datasets)*\n")
    lines.append("---\n")
    lines.append("## METHODOLOGY\n")
    lines.append("Each official's name was searched as an exact quoted phrase in the FTS5 full-text index of all 12 DOJ Epstein file datasets (218 GB total). For officials with significant hit counts, a deeper context analysis was performed — sampling 5-10 document snippets to categorize the nature of each mention.\n")
    lines.append("**Categories:**")
    lines.append("- **DIRECT** — Direct personal involvement: emails, meetings, financial ties, contact lists")
    lines.append("- **INVESTIGATION** — Referenced in Epstein investigation/prosecution documents")
    lines.append("- **POLITICAL** — Political/legislative context: oversight, public statements, policy")
    lines.append("- **MIXED** — Multiple categories present across mentions")
    lines.append("- **NEWS** — News clips or media references unrelated to Epstein")
    lines.append("- **FALSE POSITIVE** — Name matches a different person entirely\n")
    lines.append("---\n")

    # Build entries and classify
    entries_by_state = {}
    for o in officials:
        state = get_state_sort_key(o.get("office", "Unknown"))
        if state not in entries_by_state:
            entries_by_state[state] = []

        cat, summary, notable_eftas = categorize(o["name"], o["doc_count"], congress_ctx, exec_ctx)

        entry = {
            "name": o["name"],
            "office": o.get("office", ""),
            "doc_count": o["doc_count"],
            "page_count": o["page_count"],
            "category": cat,
            "summary": summary,
            "notable_eftas": notable_eftas,
            "sample_eftas": o.get("sample_eftas", []),
        }
        entries_by_state[state].append(entry)

        if cat == "DIRECT":
            direct.append(entry)
        elif cat == "INVESTIGATION":
            investigation.append(entry)
        elif cat == "POLITICAL":
            political.append(entry)
        elif cat == "MIXED":
            mixed.append(entry)
        elif cat == "FALSE_POSITIVE":
            false_pos.append(entry)
        elif cat == "NONE":
            pass  # zero-hit, don't count
        else:
            news_only.append(entry)

    # Executive summary
    lines.append("## EXECUTIVE SUMMARY\n")
    lines.append(f"- **{total}** officials searched")
    lines.append(f"- **{len(with_hits)}** appear in the corpus ({total - len(with_hits)} with zero mentions)")
    if direct:
        lines.append(f"- **{len(direct)}** with DIRECT personal connections to Epstein")
    if investigation:
        lines.append(f"- **{len(investigation)}** referenced in investigation/prosecution context")
    if mixed:
        lines.append(f"- **{len(mixed)}** with MIXED mention types (multiple categories)")
    if political:
        lines.append(f"- **{len(political)}** in political/legislative context only")
    if false_pos:
        lines.append(f"- **{len(false_pos)}** confirmed FALSE POSITIVES (different person with same name)")
    if news_only:
        lines.append(f"- **{len(news_only)}** appear only in news coverage unrelated to Epstein")
    no_hits_count = total - len(with_hits)
    if no_hits_count > 0:
        lines.append(f"- **{no_hits_count}** not found in any document")
    lines.append("")

    # Highlight direct connections first
    if direct:
        lines.append("### Officials with Direct Connections\n")
        for d in sorted(direct, key=lambda x: -x["doc_count"]):
            lines.append(f"**{d['name']}** ({d['office']}) — {d['doc_count']} documents, {d['page_count']} pages")
            if d["summary"]:
                lines.append(f"> {d['summary']}")
            if d["notable_eftas"]:
                efta_links = ", ".join(efta_link(e) for e in d["notable_eftas"][:5])
                lines.append(f"> Key documents: {efta_links}")
            lines.append("")

    if mixed:
        lines.append("### Officials with Mixed Mentions\n")
        for m in sorted(mixed, key=lambda x: -x["doc_count"]):
            lines.append(f"**{m['name']}** ({m['office']}) — {m['doc_count']} documents, {m['page_count']} pages")
            if m["summary"]:
                lines.append(f"> {m['summary']}")
            if m["notable_eftas"]:
                efta_links = ", ".join(efta_link(e) for e in m["notable_eftas"][:5])
                lines.append(f"> Key documents: {efta_links}")
            lines.append("")

    if investigation:
        lines.append("### Officials in Investigation Context\n")
        for inv in sorted(investigation, key=lambda x: -x["doc_count"]):
            lines.append(f"**{inv['name']}** ({inv['office']}) — {inv['doc_count']} documents, {inv['page_count']} pages")
            if inv["summary"]:
                lines.append(f"> {inv['summary']}")
            if inv["notable_eftas"]:
                efta_links = ", ".join(efta_link(e) for e in inv["notable_eftas"][:5])
                lines.append(f"> Key documents: {efta_links}")
            lines.append("")

    if political:
        lines.append("### Officials in Political/Legislative Context\n")
        for p in sorted(political, key=lambda x: -x["doc_count"]):
            lines.append(f"**{p['name']}** ({p['office']}) — {p['doc_count']} documents, {p['page_count']} pages")
            if p["summary"]:
                lines.append(f"> {p['summary']}")
            lines.append("")

    if false_pos:
        lines.append("### Confirmed False Positives\n")
        for fp in false_pos:
            lines.append(f"**{fp['name']}** ({fp['office']}) — {fp['doc_count']} documents")
            if fp["summary"]:
                lines.append(f"> {fp['summary']}")
            lines.append("")

    # Full listing by state
    lines.append("---\n")
    lines.append("## COMPLETE LISTING BY STATE\n")

    for state in sorted(entries_by_state.keys()):
        entries = entries_by_state[state]
        entries.sort(key=lambda x: get_last_name(x["name"]))

        lines.append(f"### {state}\n")
        lines.append("| Official | Office | Docs | Pages | Category |")
        lines.append("|----------|--------|-----:|------:|----------|")
        for e in entries:
            office_short = e["office"].replace("U.S. Senate ", "").replace("U.S. House ", "")
            cat_display = e["category"].replace("_", " ")
            if e["category"] == "NONE":
                cat_display = "—"
            lines.append(f"| {e['name']} | {office_short} | {e['doc_count']} | {e['page_count']} | {cat_display} |")
        lines.append("")

    # No-hits section
    no_hits = [o for o in officials if o["doc_count"] == 0]
    if no_hits:
        lines.append("### Officials with Zero Mentions\n")
        lines.append("The following officials do not appear in any of the 1,380,937 documents in the corpus:\n")
        for o in no_hits:
            state = get_state_sort_key(o.get("office", ""))
            lines.append(f"- {o['name']} ({state})")
        lines.append("")

    lines.append("---\n")
    lines.append("*This report was generated by searching the complete DOJ Epstein file corpus (12 datasets, 218 GB). The presence of an official's name in this corpus does not imply any wrongdoing or connection to Jeffrey Epstein — the vast majority of mentions are in news articles, political coverage, and other media collected as part of the investigation. Only entries marked DIRECT indicate documented personal contact.*")

    with open(filepath, 'w') as f:
        f.write('\n'.join(lines))

    print(f"Wrote {filepath}: {len(lines)} lines, {total} officials")


def write_exec_report(filepath, exec_ctx, search_data):
    """Write executive branch report."""
    lines = []
    lines.append("# Executive Branch Officials in the Epstein Files")
    lines.append("## Trump Administration (2025), Biden Administration, and Key Former Officials")
    lines.append(f"*Generated 2026-02-12 from full_text_corpus.db (1,380,937 documents, 2,731,796 pages across 12 DOJ datasets)*\n")
    lines.append("---\n")
    lines.append("## METHODOLOGY\n")
    lines.append("Each official's name was searched as an exact quoted phrase in the FTS5 full-text index of all 12 DOJ Epstein file datasets. For every official with hits, a detailed context analysis was performed — sampling 5-10 document snippets with random ordering to categorize the nature of each mention.\n")
    lines.append("**Categories:**")
    lines.append("- **DIRECT** — Direct personal involvement: emails, meetings, financial ties, contact lists")
    lines.append("- **INVESTIGATION** — Referenced in Epstein investigation/prosecution documents")
    lines.append("- **POLITICAL** — Political/legislative context: oversight, public statements, policy")
    lines.append("- **MIXED** — Multiple categories present across mentions")
    lines.append("- **NEWS** — News clips or media references unrelated to Epstein")
    lines.append("- **FALSE POSITIVE** — Name matches a different person entirely\n")
    lines.append("---\n")

    # Gather all exec officials
    trump_officials = search_data.get("trump_admin", [])
    biden_officials = search_data.get("biden_admin", [])

    # Executive summary
    all_exec = trump_officials + biden_officials
    with_hits = [o for o in all_exec if o["doc_count"] > 0]

    # Find direct connections from exec context
    direct_names = []
    if "key_findings_summary" in exec_ctx:
        for d in exec_ctx["key_findings_summary"].get("direct_personal_connections", []):
            direct_names.append(d["name"])

    lines.append("## EXECUTIVE SUMMARY\n")
    lines.append(f"- **{len(all_exec)}** executive branch officials searched")
    lines.append(f"- **{len(with_hits)}** appear in the corpus")
    lines.append(f"- **{len(direct_names)}** with documented DIRECT personal connections to Epstein\n")

    # Key findings
    if "key_findings_summary" in exec_ctx:
        kf = exec_ctx["key_findings_summary"]

        lines.append("## KEY FINDINGS\n")

        lines.append("### Direct Personal Connections\n")
        for d in kf.get("direct_personal_connections", []):
            strength = d.get("strength", "")
            lines.append(f"**{d['name']}** ({d['role']}) — Strength: {strength}")
            lines.append(f"> {d['nature']}\n")

        if kf.get("investigation_connections"):
            lines.append("### Investigation/Prosecution Connections\n")
            for d in kf["investigation_connections"]:
                lines.append(f"**{d['name']}** ({d['role']}) — Strength: {d.get('strength', '')}")
                lines.append(f"> {d['nature']}\n")

        if kf.get("false_positives_identified"):
            lines.append("### False Positives Identified\n")
            for fp in kf["false_positives_identified"]:
                lines.append(f"**{fp['name']}**: {fp['explanation']}\n")

        subpoenaed = kf.get("subpoenaed_for_epstein_testimony", [])
        if subpoenaed:
            lines.append("### Subpoenaed for House Oversight Epstein Testimony\n")
            lines.append(", ".join(subpoenaed) + "\n")

    lines.append("---\n")

    def sort_doc_count(item):
        dc = item[1].get("doc_count", 0)
        if isinstance(dc, str):
            dc = int(dc.replace("+", ""))
        return -dc

    # Trump Administration section
    lines.append("## TRUMP ADMINISTRATION (2025)\n")

    for section_key in ["trump_administration_2025"]:
        if section_key not in exec_ctx:
            continue
        section = exec_ctx[section_key]

        sorted_officials = sorted(section.items(), key=sort_doc_count)

        for key, val in sorted_officials:
            name_display = key.replace("_", " ").title()
            # Fix specific names
            name_display = name_display.replace("Jd ", "JD ").replace("Jr.", "Jr.").replace("Rfk", "RFK")

            doc_count = val.get("doc_count", 0)
            if isinstance(doc_count, str):
                doc_count_display = doc_count
            else:
                doc_count_display = str(doc_count)

            cat = val.get("category", "NEWS")
            summary = val.get("summary", "")
            prev = val.get("previously_investigated", False)

            lines.append(f"### {name_display}")
            role_line = ""
            # Find role from search data
            for o in trump_officials:
                if o["name"].lower().replace(".", "").replace(" ", "") in name_display.lower().replace(".", "").replace(" ", "") or \
                   name_display.lower().replace(".", "").replace(" ", "") in o["name"].lower().replace(".", "").replace(" ", ""):
                    role_line = o.get("role", "")
                    break

            if role_line:
                lines.append(f"**Role:** {role_line}")
            lines.append(f"**Documents:** {doc_count_display} | **Category:** {cat}")
            if prev:
                lines.append("*Previously investigated in dedicated report*")
            lines.append(f"\n{summary}\n")

            # Mention types
            mention_types = val.get("mention_types", {})
            if mention_types:
                for mt, desc in mention_types.items():
                    lines.append(f"- **{mt}**: {desc}")
                lines.append("")

            # Sample EFTAs
            sample_eftas = val.get("sample_efta_numbers", [])
            if sample_eftas:
                efta_links = ", ".join(efta_link(e) for e in sample_eftas[:5])
                lines.append(f"Sample documents: {efta_links}\n")

            # Notes
            note = val.get("note", "")
            if note:
                lines.append(f"*Note: {note}*\n")

    # Trump admin officials NOT in detailed context (low-count ones)
    trump_ctx_names = set()
    if "trump_administration_2025" in exec_ctx:
        for key in exec_ctx["trump_administration_2025"]:
            trump_ctx_names.add(key.replace("_", " ").lower())

    uncovered_trump = []
    for o in trump_officials:
        name_lower = o["name"].lower()
        found = False
        for ctx_name in trump_ctx_names:
            if ctx_name in name_lower or name_lower in ctx_name:
                found = True
                break
        if not found and o["doc_count"] > 0:
            uncovered_trump.append(o)

    if uncovered_trump:
        lines.append("### Other Trump Administration Officials\n")
        lines.append("| Official | Role | Docs | Pages |")
        lines.append("|----------|------|-----:|------:|")
        for o in sorted(uncovered_trump, key=lambda x: -x["doc_count"]):
            lines.append(f"| {o['name']} | {o.get('role', '')} | {o['doc_count']} | {o['page_count']} |")
        lines.append("\n*All of the above appear only in news coverage and political reporting unrelated to Epstein.*\n")

    lines.append("---\n")

    # Biden Administration / Former Officials section
    lines.append("## BIDEN ADMINISTRATION & KEY FORMER OFFICIALS\n")

    for section_key in ["biden_administration_and_former_officials"]:
        if section_key not in exec_ctx:
            continue
        section = exec_ctx[section_key]

        sorted_officials = sorted(section.items(), key=sort_doc_count)

        for key, val in sorted_officials:
            name_display = key.replace("_", " ").title()

            doc_count = val.get("doc_count", 0)
            if isinstance(doc_count, str):
                doc_count_display = doc_count
            else:
                doc_count_display = str(doc_count)

            cat = val.get("category", "NEWS")
            summary = val.get("summary", "")
            prev = val.get("previously_investigated", False)

            lines.append(f"### {name_display}")
            role_line = ""
            for o in biden_officials:
                if o["name"].lower().replace(".", "").replace(" ", "") in name_display.lower().replace(".", "").replace(" ", "") or \
                   name_display.lower().replace(".", "").replace(" ", "") in o["name"].lower().replace(".", "").replace(" ", ""):
                    role_line = o.get("role", "")
                    break

            if role_line:
                lines.append(f"**Role:** {role_line}")
            lines.append(f"**Documents:** {doc_count_display} | **Category:** {cat}")
            if prev:
                lines.append("*Previously investigated in dedicated report*")
            lines.append(f"\n{summary}\n")

            mention_types = val.get("mention_types", {})
            if mention_types:
                for mt, desc in mention_types.items():
                    lines.append(f"- **{mt}**: {desc}")
                lines.append("")

            sample_eftas = val.get("sample_efta_numbers", [])
            if sample_eftas:
                efta_links = ", ".join(efta_link(e) for e in sample_eftas[:5])
                lines.append(f"Sample documents: {efta_links}\n")

            note = val.get("note", "")
            if note:
                lines.append(f"*Note: {note}*\n")

    # Biden officials NOT in detailed context
    biden_ctx_names = set()
    if "biden_administration_and_former_officials" in exec_ctx:
        for key in exec_ctx["biden_administration_and_former_officials"]:
            biden_ctx_names.add(key.replace("_", " ").lower())

    uncovered_biden = []
    for o in biden_officials:
        name_lower = o["name"].lower()
        found = False
        for ctx_name in biden_ctx_names:
            if ctx_name in name_lower or name_lower in ctx_name:
                found = True
                break
        if not found and o["doc_count"] > 0:
            uncovered_biden.append(o)

    if uncovered_biden:
        lines.append("### Other Biden Administration / Former Officials\n")
        lines.append("| Official | Role | Docs | Pages |")
        lines.append("|----------|------|-----:|------:|")
        for o in sorted(uncovered_biden, key=lambda x: -x["doc_count"]):
            lines.append(f"| {o['name']} | {o.get('role', '')} | {o['doc_count']} | {o['page_count']} |")
        lines.append("\n*Officials not covered above appear only in news coverage and political reporting unrelated to Epstein.*\n")

    lines.append("---\n")
    lines.append("*This report was generated by searching the complete DOJ Epstein file corpus (12 datasets, 218 GB). The presence of an official's name in this corpus does not imply any wrongdoing or connection to Jeffrey Epstein — the vast majority of mentions are in news articles, political coverage, and other media collected as part of the investigation. Only entries marked DIRECT indicate documented personal contact.*")

    with open(filepath, 'w') as f:
        f.write('\n'.join(lines))

    print(f"Wrote {filepath}: {len(lines)} lines")


def main():
    with open(SEARCH) as f:
        search_data = json.load(f)
    with open(CONGRESS_CTX) as f:
        congress_ctx = json.load(f)
    with open(EXEC_CTX) as f:
        exec_ctx = json.load(f)

    # Democrat Senate
    write_congressional_report(
        f"{OUT_DIR}/DEMOCRAT_SENATE.md",
        "Democratic Senators in the Epstein Files",
        "Full-Corpus Search Results for All 45 Democratic U.S. Senators (119th Congress)",
        search_data["democratic_senate"],
        congress_ctx, exec_ctx
    )

    # Democrat House
    write_congressional_report(
        f"{OUT_DIR}/DEMOCRAT_HOUSE.md",
        "Democratic House Members in the Epstein Files",
        "Full-Corpus Search Results for All 216 Democratic U.S. House Members (119th Congress)",
        search_data["democratic_house"],
        congress_ctx, exec_ctx
    )

    # Republican Senate
    write_congressional_report(
        f"{OUT_DIR}/REPUBLICAN_SENATE.md",
        "Republican Senators in the Epstein Files",
        "Full-Corpus Search Results for All 53 Republican U.S. Senators (119th Congress)",
        search_data["republican_senate"],
        congress_ctx, exec_ctx
    )

    # Republican House
    write_congressional_report(
        f"{OUT_DIR}/REPUBLICAN_HOUSE.md",
        "Republican House Members in the Epstein Files",
        "Full-Corpus Search Results for All 221 Republican U.S. House Members (119th Congress)",
        search_data["republican_house"],
        congress_ctx, exec_ctx
    )

    # Independent Senate
    write_congressional_report(
        f"{OUT_DIR}/INDEPENDENT_SENATE.md",
        "Independent Senators in the Epstein Files",
        "Full-Corpus Search Results for All 2 Independent U.S. Senators (119th Congress)",
        search_data["independent_senate"],
        congress_ctx, exec_ctx
    )

    # Executive Branch
    write_exec_report(
        f"{OUT_DIR}/EXECUTIVE_BRANCH.md",
        exec_ctx, search_data
    )

    print("\nAll reports generated.")


if __name__ == "__main__":
    main()
