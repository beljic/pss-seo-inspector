"""CONT-01..CONT-03: conflicting facts, typos and thin / near-duplicate copy."""

from __future__ import annotations

import re
from collections.abc import Iterable
from itertools import combinations

from ..models import EVIDENCE_SNIPPET_MAX, Page, Severity
from ..urlutil import snip
from .base import AuditContext, Issue, Manual, Outcome, Rule, unique_pages

THIN_WORDS = 150
NEAR_DUP_MIN_WORDS = 50
NEAR_DUP_JACCARD = 0.9
NEAR_DUP_MAX_PAGES = 500
SHINGLE = 5
CONTEXT_CHARS = 50
MAX_TYPO_FINDINGS_PER_PAGE = 10
WORD = re.compile(r"[\w'’-]+")
# Same word twice on one line ("the the"); lines are separate text nodes.
DUPLICATE_WORD = re.compile(r"\b([^\W\d_]+)[ \t]+\1\b", re.IGNORECASE)
DUPLICATE_OK = {"that", "had", "bye", "ha", "no", "so", "very", "knock"}

# Common English misspellings → correction (lower case, whole words).
MISSPELLINGS: dict[str, str] = {
    "abscence": "absence",
    "accomodate": "accommodate",
    "accomodation": "accommodation",
    "acheive": "achieve",
    "accross": "across",
    "acknowlege": "acknowledge",
    "adress": "address",
    "agressive": "aggressive",
    "allready": "already",
    "alot": "a lot",
    "amatuer": "amateur",
    "apparant": "apparent",
    "appearence": "appearance",
    "arguement": "argument",
    "assasination": "assassination",
    "basicly": "basically",
    "begining": "beginning",
    "beleive": "believe",
    "belive": "believe",
    "buisness": "business",
    "calender": "calendar",
    "catagory": "category",
    "cemetary": "cemetery",
    "changable": "changeable",
    "cheif": "chief",
    "collegue": "colleague",
    "comming": "coming",
    "commited": "committed",
    "commitee": "committee",
    "completly": "completely",
    "concious": "conscious",
    "conveniant": "convenient",
    "curiousity": "curiosity",
    "definately": "definitely",
    "definatly": "definitely",
    "desparate": "desperate",
    "developement": "development",
    "diffrent": "different",
    "dissapear": "disappear",
    "dissapoint": "disappoint",
    "embarass": "embarrass",
    "enviroment": "environment",
    "equiptment": "equipment",
    "exagerate": "exaggerate",
    "excellant": "excellent",
    "exersize": "exercise",
    "existance": "existence",
    "experiance": "experience",
    "familar": "familiar",
    "finaly": "finally",
    "flourescent": "fluorescent",
    "foriegn": "foreign",
    "freind": "friend",
    "garantee": "guarantee",
    "gaurantee": "guarantee",
    "guarentee": "guarantee",
    "goverment": "government",
    "grammer": "grammar",
    "harrass": "harass",
    "heighth": "height",
    "heirarchy": "hierarchy",
    "humourous": "humorous",
    "hygene": "hygiene",
    "idependent": "independent",
    "immediatly": "immediately",
    "incidently": "incidentally",
    "independant": "independent",
    "indispensible": "indispensable",
    "innoculate": "inoculate",
    "intelligance": "intelligence",
    "interupt": "interrupt",
    "irrelevent": "irrelevant",
    "knowlege": "knowledge",
    "lenght": "length",
    "liason": "liaison",
    "libary": "library",
    "maintainance": "maintenance",
    "maintenence": "maintenance",
    "managment": "management",
    "millenium": "millennium",
    "miniscule": "minuscule",
    "mischievious": "mischievous",
    "mispell": "misspell",
    "neccessary": "necessary",
    "necessery": "necessary",
    "neigbor": "neighbor",
    "noticable": "noticeable",
    "occassion": "occasion",
    "occassionally": "occasionally",
    "occured": "occurred",
    "occurence": "occurrence",
    "occurrance": "occurrence",
    "ommision": "omission",
    "oppurtunity": "opportunity",
    "orignal": "original",
    "paralel": "parallel",
    "parliment": "parliament",
    "particulary": "particularly",
    "pavillion": "pavilion",
    "peice": "piece",
    "perseverence": "perseverance",
    "persistant": "persistent",
    "personell": "personnel",
    "posession": "possession",
    "potatos": "potatoes",
    "preceeding": "preceding",
    "prefered": "preferred",
    "presense": "presence",
    "privelege": "privilege",
    "probaly": "probably",
    "proffesional": "professional",
    "profesional": "professional",
    "promiss": "promise",
    "pronounciation": "pronunciation",
    "publically": "publicly",
    "purchaseing": "purchasing",
    "questionaire": "questionnaire",
    "realy": "really",
    "reccomend": "recommend",
    "recomend": "recommend",
    "recieve": "receive",
    "recieved": "received",
    "reciept": "receipt",
    "refered": "referred",
    "relevent": "relevant",
    "religous": "religious",
    "remeber": "remember",
    "repitition": "repetition",
    "resistence": "resistance",
    "responsability": "responsibility",
    "rythm": "rhythm",
    "satisfacton": "satisfaction",
    "seperate": "separate",
    "seperately": "separately",
    "sieze": "seize",
    "shiping": "shipping",
    "sincerly": "sincerely",
    "speach": "speech",
    "succesful": "successful",
    "successfull": "successful",
    "supercede": "supersede",
    "suprise": "surprise",
    "tatoo": "tattoo",
    "tendancy": "tendency",
    "threshhold": "threshold",
    "tommorow": "tomorrow",
    "tommorrow": "tomorrow",
    "tounge": "tongue",
    "truely": "truly",
    "unforseen": "unforeseen",
    "unfortunatly": "unfortunately",
    "untill": "until",
    "usefull": "useful",
    "vaccuum": "vacuum",
    "vegatable": "vegetable",
    "visable": "visible",
    "warrenty": "warranty",
    "wierd": "weird",
    "wellcome": "welcome",
    "whereever": "wherever",
    "wich": "which",
    "withdrawl": "withdrawal",
    "writting": "writing",
}
MISSPELLING = re.compile(
    r"\b("
    + "|".join(sorted(map(re.escape, MISSPELLINGS), key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)


def _has_text(page: Page) -> bool:
    """False for pages loaded from a crawl.json written before text was captured."""
    return page.html is not None and "text" in page.html.model_fields_set


def _text_pages(ctx: AuditContext, pages: list[Page]) -> tuple[list[Page], int]:
    pages = unique_pages(pages)
    with_text = [p for p in pages if _has_text(p)]
    return with_text, len(pages) - len(with_text)


def _no_text_manual(missing: int) -> Manual:
    return Manual(
        f"{missing} page(s) have no captured text (old crawl.json); re-crawl to "
        "check them"
    )


def _context(text: str, start: int, end: int) -> str:
    lo = max(0, start - CONTEXT_CHARS)
    hi = min(len(text), end + CONTEXT_CHARS)
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(text) else ""
    return prefix + " ".join(text[lo:hi].split()) + suffix


def _norm(value: str) -> str:
    return " ".join(value.lower().split())


# -- CONT-01 -----------------------------------------------------------------


def _cont01(ctx: AuditContext) -> Iterable[Outcome]:
    facts = ctx.site_config.facts if ctx.site_config is not None else []
    if not facts:
        yield Manual("No facts configured in the site config; check by hand")
        return
    pages, missing = _text_pages(ctx, ctx.html_pages())
    if missing:
        yield _no_text_manual(missing)
    unique = list({p.final_url: p for p in pages}.values())
    for fact in facts:
        regex = re.compile(fact.pattern, re.IGNORECASE)
        # value -> [(url, quote)]
        found: dict[str, list[tuple[str, str]]] = {}
        for page in unique:
            assert page.html is not None
            text = page.html.text
            for match in regex.finditer(text):
                groups = [g for g in match.groups() if g]
                value = _norm(" ".join(groups) if groups else match.group(0))
                if not value:
                    continue
                quote = _context(text, match.start(), match.end())
                entries = found.setdefault(value, [])
                if all(url != page.final_url for url, _ in entries):
                    entries.append((page.final_url, quote))
        if not found:
            yield Manual(f"Fact '{fact.name}' not found on any crawled page")
            continue
        expected = _norm(fact.expected) if fact.expected else None
        if expected is not None:
            for value, entries in found.items():
                if value == expected:
                    continue
                for url, quote in entries:
                    yield Issue(
                        url,
                        f"{fact.name}: '{value}' but expected '{expected}': "
                        f'"{quote}"',
                    )
            continue
        if len(found) < 2:
            continue
        for value, entries in found.items():
            others = [(v, e[0]) for v, e in found.items() if v != value]
            other_text = "; ".join(f"'{v}' on {url}" for v, (url, _) in others[:2])
            for url, quote in entries:
                yield Issue(
                    url,
                    f"{fact.name}: '{value}' here (\"{snip(quote, 110)}\") vs "
                    f"{other_text}",
                )


# -- CONT-02 -----------------------------------------------------------------


def _typos(text: str, wrong_brands: list[str]) -> Iterable[str]:
    for line in text.split("\n"):
        for match in DUPLICATE_WORD.finditer(line):
            if match.group(1).lower() in DUPLICATE_OK:
                continue
            yield (
                f"Duplicated word '{match.group(0)}': "
                f'"{_context(line, match.start(), match.end())}"'
            )
    for match in MISSPELLING.finditer(text):
        word = match.group(1)
        yield (
            f"Misspelling '{word}' → '{MISSPELLINGS[word.lower()]}': "
            f'"{_context(text, match.start(), match.end())}"'
        )
    for wrong in wrong_brands:
        pattern = re.compile(r"(?<!\w)" + re.escape(wrong) + r"(?!\w)")
        for match in pattern.finditer(text):
            yield (
                f"Wrong brand spelling '{wrong}': "
                f'"{_context(text, match.start(), match.end())}"'
            )
            break


def _cont02(ctx: AuditContext) -> Iterable[Outcome]:
    config = ctx.site_config
    wrong_brands = [w for w in (config.brand_wrong_spellings if config else []) if w]
    pages, missing = _text_pages(ctx, ctx.canonical_pages())
    if missing:
        yield _no_text_manual(missing)
    # Identical evidence (typically header/footer copy) → one finding.
    found: dict[str, list[str]] = {}
    for page in pages:
        assert page.html is not None
        seen: set[str] = set()
        for evidence in _typos(page.html.text, wrong_brands):
            if evidence in seen:
                continue
            seen.add(evidence)
            if len(seen) > MAX_TYPO_FINDINGS_PER_PAGE:
                break
            found.setdefault(evidence, []).append(page.final_url)
    for evidence, urls in found.items():
        if len(urls) == 1:
            yield Issue(urls[0], evidence)
            continue
        suffix = f" (+{len(urls) - 1} more pages)"
        yield Issue(
            urls[0], snip(evidence, EVIDENCE_SNIPPET_MAX - len(suffix)) + suffix
        )


# -- CONT-03 -----------------------------------------------------------------


def _shingles(words: list[str]) -> set[int]:
    return {
        hash(tuple(words[i : i + SHINGLE])) for i in range(len(words) - SHINGLE + 1)
    }


def _cont03(ctx: AuditContext) -> Iterable[Outcome]:
    pages, missing = _text_pages(ctx, ctx.canonical_pages())
    if missing:
        yield _no_text_manual(missing)
    candidates: list[tuple[Page, int, set[int]]] = []
    for page in pages:
        assert page.html is not None
        words = [w.lower() for w in WORD.findall(page.html.main_text)]
        if len(words) < THIN_WORDS:
            yield Issue(
                page.final_url,
                f"Thin content: {len(words)} words in the main content "
                f'(<{THIN_WORDS}): "{snip(page.html.main_text, 120)}"',
            )
        if len(words) >= NEAR_DUP_MIN_WORDS:
            candidates.append((page, len(words), _shingles(words)))
    if len(candidates) > NEAR_DUP_MAX_PAGES:
        yield Manual(
            f"Near-duplicate check limited to {NEAR_DUP_MAX_PAGES} of "
            f"{len(candidates)} pages"
        )
        candidates = candidates[:NEAR_DUP_MAX_PAGES]
    for (a, a_words, a_set), (b, b_words, b_set) in combinations(candidates, 2):
        if not a_set or not b_set:
            continue
        smaller, larger = sorted((len(a_set), len(b_set)))
        if smaller / larger < NEAR_DUP_JACCARD:
            continue  # Jaccard can't reach the threshold
        jaccard = len(a_set & b_set) / len(a_set | b_set)
        if jaccard >= NEAR_DUP_JACCARD:
            yield Issue(
                b.final_url,
                f"Near-duplicate of {a.final_url} (Jaccard {jaccard:.2f} on "
                f"{SHINGLE}-word shingles; {b_words} vs {a_words} words)",
            )


RULES = [
    Rule(
        id="CONT-01",
        category="content",
        severity=Severity.medium,
        description="Facts from the site config (e.g. return window) read the same "
        "on every page",
        check=_cont01,
        scope="page",
        fix={
            "generic": "Make every page state the fact the same way; update the "
            "outdated page (policy, FAQ, product copy).",
            "shopify": "Check Settings > Policies, FAQ page/metaobjects and product "
            "descriptions for the old wording.",
            "magento2": "Check CMS pages/blocks and product descriptions for the old "
            "wording.",
            "woocommerce": "Check policy pages, FAQ blocks and product descriptions.",
        },
    ),
    Rule(
        id="CONT-02",
        category="content",
        severity=Severity.low,
        description="No duplicated words, common misspellings or wrong brand "
        "spellings in page copy",
        check=_cont02,
        scope="page",
        fix={
            "generic": "Correct the copy at its source (page, product or block "
            "content).",
            "shopify": "Edit the text in Admin (product/page/blog) or in the Theme "
            "Editor section settings; check locale files for theme strings.",
            "magento2": "Edit the CMS page/block or product attribute text.",
            "woocommerce": "Edit the page/product content or widget text.",
        },
    ),
    Rule(
        id="CONT-03",
        category="content",
        severity=Severity.low,
        description=f"Indexable pages have ≥{THIN_WORDS} words of main content and "
        "are not near-duplicates of each other",
        check=_cont03,
        scope="page",
        fix={
            "generic": "Expand thin pages with useful, unique copy, or noindex / "
            "consolidate them; rewrite or canonicalize near-duplicates.",
            "shopify": "Add unique product/collection descriptions; avoid "
            "copy-pasted descriptions between variants sold as separate products.",
            "magento2": "Add unique category/product descriptions; canonicalize "
            "near-identical products.",
            "woocommerce": "Add unique descriptions; noindex thin archive pages in "
            "the SEO plugin.",
        },
    ),
]
