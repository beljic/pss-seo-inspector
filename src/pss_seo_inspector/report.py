"""Write report.md, findings.json and urls.csv next to crawl.json."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from .models import AuditResult, Crawl, Finding, Severity
from .parse import jsonld_types
from .rules.base import Rule, all_rules, robots_directives
from .storage import crawl_dir

URLS_CSV_COLUMNS = [
    "url",
    "status",
    "canonical",
    "robots",
    "title",
    "title_len",
    "meta_desc_len",
    "h1_count",
    "imgs_missing_alt",
    "jsonld_types",
]
MAX_FINDINGS_PER_RULE_IN_MD = 50


def write_reports(crawl: Crawl, result: AuditResult) -> Path:
    directory = crawl_dir(crawl.crawl_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "findings.json").write_text(
        json.dumps([f.model_dump(mode="json") for f in result.findings], indent=1),
        encoding="utf-8",
    )
    _write_urls_csv(directory / "urls.csv", crawl)
    report = directory / "report.md"
    report.write_text(render_markdown(crawl, result), encoding="utf-8")
    return report


def _write_urls_csv(path: Path, crawl: Crawl) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(URLS_CSV_COLUMNS)
        for page in crawl.pages:
            html = page.html
            title = html.titles[0] if html and html.titles else ""
            desc = html.meta_descriptions[0] if html and html.meta_descriptions else ""
            writer.writerow(
                [
                    page.url,
                    page.status if page.status is not None else (page.error or ""),
                    html.canonicals[0] if html and html.canonicals else "",
                    ",".join(robots_directives(page)),
                    title,
                    len(title) if html else "",
                    len(desc) if html else "",
                    (len(html.h1) + html.h1_logo_only) if html else "",
                    sum(1 for i in html.images if not (i.alt or "").strip())
                    if html
                    else "",
                    "|".join(jsonld_types(html.jsonld)) if html else "",
                ]
            )


def summary_lines(crawl: Crawl, result: AuditResult) -> list[str]:
    """Executive summary, at most 10 lines."""
    counts = Counter(f.severity for f in result.findings)
    sev = ", ".join(f"{counts.get(s, 0)} {s.value}" for s in Severity)
    top = sorted(
        {
            f.rule_id
            for f in result.findings
            if f.severity in (Severity.critical, Severity.high)
        }
    )
    html_pages = sum(1 for p in crawl.pages if p.html is not None)
    lines = [
        f"- Site: {crawl.start_url} — platform **{crawl.platform}**, crawl "
        f"{crawl.crawl_id}",
        f"- Pages fetched: {len(crawl.pages)} ({html_pages} HTML)"
        + (f", capped at max_pages={crawl.max_pages}" if crawl.capped else "")
        + ("; incomplete: repeated HTTP 429" if crawl.rate_limited else "")
        + f"; robots-skipped: {len(crawl.skipped_robots)}; link checks: "
        f"{len(crawl.link_checks)}",
        f"- Findings: {len(result.findings)} ({sev})",
        f"- Critical/high rules: {', '.join(top) if top else 'none'}",
        f"- Rules passed: {len(result.passed)}; needs manual check: "
        f"{len({m.rule_id for m in result.manual})}; skipped (platform): "
        f"{len(result.skipped)}",
    ]
    if result.known_issues:
        still = sum(1 for k in result.known_issues if k.status == "still present")
        lines.append(
            f"- Known issues: {still} of {len(result.known_issues)} still present"
        )
    return lines[:10]


def render_markdown(crawl: Crawl, result: AuditResult) -> str:
    rules = {r.id: r for r in all_rules()}
    out: list[str] = [
        f"# SEO audit — {crawl.domain} ({crawl.crawl_id.split('/')[1]})",
        "",
        "## Summary",
        "",
        *summary_lines(crawl, result),
        "",
    ]

    by_rule: dict[str, list[Finding]] = defaultdict(list)
    for finding in result.findings:
        by_rule[finding.rule_id].append(finding)
    order = sorted(
        by_rule,
        key=lambda rid: (min(f.severity.rank for f in by_rule[rid]), rid),
    )
    out += ["## Findings", ""]
    if not order:
        out += ["None.", ""]
    for rid in order:
        findings = by_rule[rid]
        rule: Rule | None = rules.get(rid)
        worst = min(findings, key=lambda f: f.severity.rank).severity
        title = rule.description if rule else rid
        out += [
            f"### {rid} — {title}",
            "",
            f"Severity: **{worst.value}** · {len(findings)} finding(s)",
            "",
            f"Fix: {findings[0].fix}",
            "",
        ]
        for f in findings[:MAX_FINDINGS_PER_RULE_IN_MD]:
            out.append(f"- [{f.severity.value}] {f.url} — {_md(f.evidence)}")
        if len(findings) > MAX_FINDINGS_PER_RULE_IN_MD:
            out.append(
                f"- … {len(findings) - MAX_FINDINGS_PER_RULE_IN_MD} more in "
                "findings.json"
            )
        out.append("")

    out += ["", "## Performance (PageSpeed Insights)", ""]
    if crawl.pagespeed_results:
        for r in crawl.pagespeed_results:
            out.append(f"### {r.url} (strategy: {r.strategy})")
            out.append("")
            if r.error:
                out.append(f"Error: {r.error}")
            else:
                rows = [
                    (
                        "Performance score",
                        f"{r.score:.0f}/100" if r.score is not None else "n/a",
                    ),
                    ("LCP", f"{r.lcp_ms:.0f} ms" if r.lcp_ms is not None else "n/a"),
                    ("CLS", f"{r.cls:.3f}" if r.cls is not None else "n/a"),
                    ("INP", f"{r.inp_ms:.0f} ms" if r.inp_ms is not None else "n/a"),
                    ("FCP", f"{r.fcp_ms:.0f} ms" if r.fcp_ms is not None else "n/a"),
                    ("TBT", f"{r.tbt_ms:.0f} ms" if r.tbt_ms is not None else "n/a"),
                    (
                        "TTFB",
                        f"{r.ttfb_ms:.0f} ms" if r.ttfb_ms is not None else "n/a",
                    ),
                ]
                out.append("| Metric | Value |")
                out.append("| --- | --- |")
                for label, value in rows:
                    out.append(f"| {label} | {value} |")
            out.append("")
    else:
        out += ["No PageSpeed Insights data collected.", ""]

    out += ["## Passed", ""]
    out += [
        f"- {rid} — {rules[rid].description}" for rid in result.passed if rid in rules
    ] or ["None."]
    out += ["", "## Needs manual check", ""]
    out += [f"- {m.rule_id}: {_md(m.description)}" for m in result.manual] or ["None."]
    out += ["", "## Skipped (not applicable to this platform)", ""]
    out += [
        f"- {rid} — {rules[rid].description}" for rid in result.skipped if rid in rules
    ] or ["None."]
    out += ["", "## Known issues re-verified", ""]
    out += [
        f"- {k.description} — **{k.status}**"
        + (f" ({k.finding_count} finding(s))" if k.finding_count else "")
        for k in result.known_issues
    ] or ["None configured."]
    out.append("")
    return "\n".join(out)


def _md(text: str) -> str:
    return "`" + text.replace("`", "'") + "`" if "<" in text else text
