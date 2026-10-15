"""Extract the SEO-relevant parts of an HTML document."""

from __future__ import annotations

from bs4 import BeautifulSoup, Doctype, Tag
from bs4.element import NavigableString, PageElement

from .models import TEXT_MAX, Hreflang, Image, Link, ParsedHTML
from .urlutil import absolutize, snip

# Elements a browser keeps in <head>; anything else (or text) closes it.
HEAD_TAGS = {
    "title",
    "meta",
    "link",
    "script",
    "style",
    "base",
    "noscript",
    "template",
}
OPAQUE_TAGS = {"script", "style", "noscript", "template", "svg", "math", "title"}
INVISIBLE_TAGS = ["script", "style", "template", "noscript", "svg"]
LANDMARK_TAGS = ("header", "nav", "footer", "aside")
QUESTION_TAGS = ["h2", "h3", "h4", "h5", "h6", "summary", "dt"]


def _attr(tag: Tag, name: str) -> str | None:
    value = tag.get(name)
    if value is None:
        return None
    if isinstance(value, list):
        return " ".join(value)
    return str(value)


def _rel(tag: Tag) -> list[str]:
    value = tag.get("rel")
    if value is None:
        return []
    items = value if isinstance(value, list) else str(value).split()
    return [str(v).lower() for v in items]


class _HeadWalker:
    """Walk the tree in source order and record which elements sit in <head>."""

    def __init__(self, base_url: str, parsed: ParsedHTML) -> None:
        self.in_head = True
        self.base_url = base_url
        self.parsed = parsed
        self.head = parsed.head

    def walk(self, node: Tag) -> None:
        for child in node.children:
            self._visit(child)

    def _visit(self, child: PageElement) -> None:
        if isinstance(child, Doctype):
            return
        if isinstance(child, NavigableString):
            if type(child) is NavigableString and child.strip():
                self.in_head = False
            return
        if not isinstance(child, Tag):
            return
        name = child.name.lower()
        if name in ("html", "head"):
            self.walk(child)
            return
        if name == "body":
            self.in_head = False
            self.walk(child)
            return
        if name not in HEAD_TAGS:
            self.in_head = False
        self._classify(child, name)
        if name not in OPAQUE_TAGS:
            self.walk(child)

    def _classify(self, tag: Tag, name: str) -> None:
        head = self.head
        if name == "title":
            self.parsed.titles.append(tag.get_text(" ", strip=True))
            if not self.parsed.title_snippet:
                self.parsed.title_snippet = snip(str(tag))
            if self.in_head:
                head.title_in_head += 1
            else:
                head.title_outside_head += 1
        elif name == "link" and "canonical" in _rel(tag):
            raw = _attr(tag, "href") or ""
            self.parsed.canonicals_raw.append(raw)
            self.parsed.canonicals.append(absolutize(self.base_url, raw) or raw)
            self.parsed.canonical_snippets.append(snip(str(tag)))
            if self.in_head:
                head.canonical_in_head += 1
            else:
                head.canonical_outside_head += 1
        elif name == "meta":
            meta_name = (_attr(tag, "name") or "").lower()
            if meta_name in ("robots", "googlebot"):
                content = _attr(tag, "content") or ""
                self.parsed.meta_robots.append(f"{meta_name}: {content}")
                self.parsed.meta_robots_snippets.append(snip(str(tag)))
                if self.in_head:
                    head.robots_in_head += 1
                else:
                    head.robots_outside_head += 1


def parse_html(html: str, base_url: str) -> ParsedHTML:
    soup = BeautifulSoup(html, "html.parser")
    parsed = ParsedHTML()
    parsed.head.has_doctype = any(isinstance(item, Doctype) for item in soup.contents)
    base_tag = soup.find("base", href=True)
    if isinstance(base_tag, Tag):
        base_url = absolutize(base_url, _attr(base_tag, "href") or "") or base_url
    _HeadWalker(base_url, parsed).walk(soup)

    html_tag = soup.find("html")
    if isinstance(html_tag, Tag):
        lang = _attr(html_tag, "lang")
        parsed.lang = lang.strip() if lang and lang.strip() else None

    body = soup.find("body")
    if isinstance(body, Tag):
        parsed.body_classes = (_attr(body, "class") or "").split()

    for meta in soup.find_all("meta"):
        _meta(meta, parsed)

    for link in soup.find_all("link"):
        rel = _rel(link)
        href = _attr(link, "href")
        if not href:
            continue
        if "alternate" in rel and link.get("hreflang"):
            target = absolutize(base_url, href) or href
            parsed.hreflang.append(
                Hreflang(lang=(_attr(link, "hreflang") or "").strip(), href=target)
            )
        if "stylesheet" in rel:
            _add_resource(parsed, base_url, href)

    for h1 in soup.find_all("h1"):
        text = h1.get_text(" ", strip=True)
        imgs = h1.find_all("img")
        parsed.h1_snippets.append(snip(str(h1)))
        if not text and imgs:
            parsed.h1_logo_only += 1
            continue
        parsed.h1.append(text)

    for a in soup.find_all("a"):
        href = _attr(a, "href")
        if href is None:
            continue
        link_url = absolutize(base_url, href)
        if link_url is None:
            continue
        parsed.links.append(
            Link(
                href=link_url,
                raw=href,
                text=_anchor_text(a),
                rel=_rel(a),
                snippet=snip(str(a)),
            )
        )

    page_imgs = [
        i for i in soup.find_all("img") if not i.find_parent(["noscript", "template"])
    ]
    for index, img in enumerate(page_imgs):
        raw_src = _img_src(img)
        src = absolutize(base_url, raw_src) or raw_src
        srcset = _attr(img, "srcset") or ""
        anchor = img.find_parent("a")
        landmark = img.find_parent(list(LANDMARK_TAGS))
        parsed.images.append(
            Image(
                src=src,
                raw_src=raw_src,
                srcset=srcset,
                alt=_attr(img, "alt"),
                width=_attr(img, "width"),
                height=_attr(img, "height"),
                loading=_attr(img, "loading"),
                css_class=_attr(img, "class") or "",
                snippet=snip(str(img)),
                sizes=_attr(img, "sizes") or "",
                fetchpriority=_attr(img, "fetchpriority"),
                index=index,
                link_text=_anchor_text(anchor) if isinstance(anchor, Tag) else None,
                landmark=landmark.name.lower() if isinstance(landmark, Tag) else "",
                picture_modern=_picture_modern(img),
            )
        )
        if raw_src:
            _add_resource(parsed, base_url, raw_src)
        for candidate in srcset.split(","):
            url = candidate.strip().split(" ", 1)[0]
            if url:
                _add_resource(parsed, base_url, url)

    for tag_name, attr in (
        ("script", "src"),
        ("iframe", "src"),
        ("video", "src"),
        ("audio", "src"),
        ("source", "src"),
        ("embed", "src"),
    ):
        for tag in soup.find_all(tag_name):
            value = _attr(tag, attr)
            if value:
                _add_resource(parsed, base_url, value)

    for script in soup.find_all("script"):
        if (_attr(script, "type") or "").lower().strip() == "application/ld+json":
            parsed.jsonld.append(script.get_text().strip())

    if parsed.og.get("og:type", "").lower() == "product":
        parsed.product_name = parsed.og.get("og:title")
    _visible_text(soup, parsed)  # last: it removes elements from the tree
    return parsed


def _img_src(img: Tag) -> str:
    """src, or the lazy-load data-src / first data-srcset URL for placeholders."""
    src = (_attr(img, "src") or "").strip()
    if src and not src.lower().startswith("data:"):
        return src
    lazy = (_attr(img, "data-src") or "").strip()
    if lazy:
        return lazy
    first = (_attr(img, "data-srcset") or "").split(",", 1)[0].strip().split(" ")[0]
    return first or src


def _picture_modern(img: Tag) -> bool:
    """True if the img sits in a <picture> offering a WebP/AVIF <source>."""
    picture = img.find_parent("picture")
    if not isinstance(picture, Tag):
        return False
    return any(
        (_attr(source, "type") or "").lower().strip() in ("image/webp", "image/avif")
        for source in picture.find_all("source")
    )


def _visible_text(soup: BeautifulSoup, parsed: ParsedHTML) -> None:
    """Fill text, main_text and question_headings. Mutates ``soup``."""
    body = soup.find("body")
    root: Tag = body if isinstance(body, Tag) else soup
    for tag in root.find_all([*INVISIBLE_TAGS, "head", "title"]):
        tag.decompose()
    for tag in root.find_all(True, attrs={"hidden": True}):
        if not tag.decomposed:
            tag.decompose()
    for tag in root.find_all(QUESTION_TAGS):
        text = " ".join(tag.get_text(" ", strip=True).split())
        if text.endswith("?") and len(parsed.question_headings) < 50:
            parsed.question_headings.append(text)
    parsed.text = _lines(root)
    for tag in root.find_all(list(LANDMARK_TAGS)):
        tag.decompose()
    parsed.main_text = _lines(root)


def _lines(root: Tag) -> str:
    """One line per visible text node, whitespace collapsed, capped."""
    lines = (" ".join(s.split()) for s in root.stripped_strings)
    return "\n".join(line for line in lines if line)[:TEXT_MAX]


def _meta(meta: Tag, parsed: ParsedHTML) -> None:
    name = (_attr(meta, "name") or "").lower()
    prop = (_attr(meta, "property") or "").lower()
    content = _attr(meta, "content")
    if _attr(meta, "charset"):
        parsed.head.has_charset = True
    http_equiv = (_attr(meta, "http-equiv") or "").lower()
    if http_equiv == "content-type" and "charset" in (content or "").lower():
        parsed.head.has_charset = True
    if name == "viewport":
        parsed.head.has_viewport = True
    if content is None:
        return
    if name == "description":
        parsed.meta_descriptions.append(content.strip())
        if not parsed.meta_description_snippet:
            parsed.meta_description_snippet = snip(str(meta))
    key = prop if prop.startswith("og:") else name if name.startswith("og:") else ""
    if key:
        parsed.og.setdefault(key, content.strip())
    tkey = (
        name
        if name.startswith("twitter:")
        else (prop if prop.startswith("twitter:") else "")
    )
    if tkey:
        parsed.twitter.setdefault(tkey, content.strip())


def _anchor_text(a: Tag) -> str:
    text = a.get_text(" ", strip=True)
    if text:
        return text
    for img in a.find_all("img"):
        alt = _attr(img, "alt")
        if alt and alt.strip():
            return alt.strip()
    for attr in ("aria-label", "title"):
        value = _attr(a, attr)
        if value and value.strip():
            return value.strip()
    return ""


def _add_resource(parsed: ParsedHTML, base_url: str, value: str) -> None:
    target = absolutize(base_url, value)
    if target and target not in parsed.resources:
        parsed.resources.append(target)


def jsonld_types(blocks: list[str]) -> list[str]:
    """@type values of every parseable JSON-LD block (including @graph)."""
    import json

    types: list[str] = []

    def collect(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                collect(item)
        elif isinstance(node, dict):
            value = node.get("@type")
            if isinstance(value, str):
                types.append(value)
            elif isinstance(value, list):
                types.extend(str(v) for v in value)
            if "@graph" in node:
                collect(node["@graph"])

    for block in blocks:
        try:
            collect(json.loads(block))
        except ValueError:
            continue
    return list(dict.fromkeys(types))
