"""Generate the README rule table from the registry.

Run ``uv run python -m pss_seo_inspector.docs`` after adding or changing a rule.
"""

from __future__ import annotations

from pathlib import Path

from .rules.base import ALL, all_rules
from .storage import PROJECT_ROOT

START = "<!-- rules:start -->"
END = "<!-- rules:end -->"


def rule_table() -> str:
    lines = [
        "| ID | Severity | Applies to | Scope | Description |",
        "|---|---|---|---|---|",
    ]
    for rule in all_rules():
        applies = ALL if ALL in rule.applies_to else ", ".join(sorted(rule.applies_to))
        description = rule.description.replace("|", "\\|")
        lines.append(
            f"| {rule.id} | {rule.severity.value} | {applies} | {rule.scope} | "
            f"{description} |"
        )
    return "\n".join(lines)


def update_readme(path: Path = PROJECT_ROOT / "README.md") -> None:
    text = path.read_text(encoding="utf-8")
    head, _, rest = text.partition(START)
    _, _, tail = rest.partition(END)
    path.write_text(f"{head}{START}\n{rule_table()}\n{END}{tail}", encoding="utf-8")


if __name__ == "__main__":
    update_readme()
