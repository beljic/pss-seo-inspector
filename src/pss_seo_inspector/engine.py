"""Run rules over a saved crawl (no network)."""

from __future__ import annotations

from collections.abc import Iterable

from .models import (
    AuditResult,
    Crawl,
    Finding,
    KnownIssueResult,
    KnownIssueStatus,
    ManualCheck,
)
from .rules.base import AuditContext, Issue, Rule, Scope, all_rules
from .site_config import SiteConfig
from .urlutil import normalize, snip


class UnknownRuleError(Exception):
    pass


def select_rules(rule_ids: Iterable[str] | None) -> list[Rule]:
    rules = all_rules()
    if rule_ids is None:
        return rules
    wanted = list(dict.fromkeys(rule_ids))
    known = {r.id for r in rules}
    unknown = [r for r in wanted if r not in known]
    if unknown:
        raise UnknownRuleError(f"Unknown rule ids: {', '.join(unknown)}")
    return [r for r in rules if r.id in wanted]


def run_audit(
    crawl: Crawl,
    site_config: SiteConfig | None,
    rule_ids: Iterable[str] | None = None,
    scope: Scope | None = None,
) -> AuditResult:
    ctx = AuditContext(crawl=crawl, site_config=site_config)
    result = AuditResult(crawl_id=crawl.crawl_id, platform=crawl.platform)
    has_html = bool(ctx.html_pages())
    for rule in select_rules(rule_ids):
        if scope is not None and rule.scope != scope:
            continue
        if not rule.applies(crawl.platform):
            result.skipped.append(rule.id)
            continue
        if rule.scope == "page" and not has_html:
            findings: list[Finding] = []
            manual = [ManualCheck(rule_id=rule.id, description="No HTML pages crawled")]
        else:
            try:
                findings, manual = _evaluate(rule, ctx)
            except Exception as exc:  # a broken rule must not hide the others
                findings = []
                manual = [
                    ManualCheck(
                        rule_id=rule.id,
                        description=f"Rule raised {type(exc).__name__}: {exc}",
                    )
                ]
        manual += [
            ManualCheck(rule_id=rule.id, description=f"Probes failed: {error}")
            for error in crawl.probe_errors
            if error.startswith(f"{rule.id}:")
        ]
        result.findings.extend(_dedupe(findings))
        if not findings and not manual:
            result.passed.append(rule.id)
        if rule.manual:
            manual.append(ManualCheck(rule_id=rule.id, description=rule.manual))
        result.manual.extend(manual)
    result.findings.sort(key=lambda f: (f.severity.rank, f.rule_id, f.url))
    result.known_issues = _known_issues(result, site_config)
    return result


def _evaluate(rule: Rule, ctx: AuditContext) -> tuple[list[Finding], list[ManualCheck]]:
    findings: list[Finding] = []
    manual: list[ManualCheck] = []
    platform = ctx.crawl.platform
    for outcome in rule.check(ctx):
        if isinstance(outcome, Issue):
            findings.append(
                Finding(
                    rule_id=rule.id,
                    severity=outcome.severity or rule.severity,
                    url=outcome.url or ctx.crawl.start_url,
                    evidence=outcome.evidence or "(no evidence)",
                    fix=rule.fix_for(platform),
                )
            )
        else:
            manual.append(ManualCheck(rule_id=rule.id, description=outcome.description))
    return findings, manual


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[Finding] = []
    for f in findings:
        key = (f.url, f.evidence, f.severity.value)
        if key not in seen:
            seen.add(key)
            unique.append(f.model_copy(update={"evidence": snip(f.evidence)}))
    return unique


def _known_issues(
    result: AuditResult, site_config: SiteConfig | None
) -> list[KnownIssueResult]:
    if site_config is None:
        return []
    out: list[KnownIssueResult] = []
    for issue in site_config.known_issues:
        if not issue.rule_ids:
            out.append(KnownIssueResult(description=issue.description, status="manual"))
            continue
        urls = {normalize(u) for u in issue.urls}
        matches = [
            f
            for f in result.findings
            if f.rule_id in issue.rule_ids
            and (not urls or normalize(f.url) in urls or f.url in issue.urls)
        ]
        ran = any(
            rid in result.passed or any(f.rule_id == rid for f in result.findings)
            for rid in issue.rule_ids
        )
        status: KnownIssueStatus = (
            "still present" if matches else ("not reproduced" if ran else "manual")
        )
        out.append(
            KnownIssueResult(
                description=issue.description, status=status, finding_count=len(matches)
            )
        )
    return out
