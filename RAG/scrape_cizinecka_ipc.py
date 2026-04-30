#!/usr/bin/env python3
"""Collect official immigration knowledge pages for Jasne RAG.

The script is intentionally dependency-free. It can crawl IPC/MVCR/FRS pages,
extract readable Markdown, preserve important links, and write manifests for
HTML pages, discovered links, skipped pages, and downloadable documents.

Recommended usage:
    python3 RAG/scrape_cizinecka_ipc.py --language cs --scope all --max-pages 0
    python3 RAG/scrape_cizinecka_ipc.py --language en --scope all --max-pages 0

Use --scope important if you want a smaller immigration-only corpus.
By default, existing pages from manifest.json are reused and not downloaded again.
Use --fresh only when you intentionally want to rebuild the output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import posixpath
import re
import shutil
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree


DEFAULT_SEEDS = {
    "cs": [
        "https://ipc.gov.cz/",
        "https://ipc.gov.cz/typy-viz-a-pobytu/",
        "https://ipc.gov.cz/formulare-a-dokumenty/",
        "https://ipc.gov.cz/rezervace/",
        "https://ipc.gov.cz/spravni-rizeni/",
        "https://ipc.gov.cz/povinnosti-cizincu/",
        "https://ipc.gov.cz/poradna/",
        "https://ipc.gov.cz/kontakty/",
        "https://ipc.gov.cz/spravni-poplatky/",
        "https://ipc.gov.cz/zvlastni-dlouhodoby-pobyt/",
        "https://kurzy.frs.gov.cz/cs/uvodni-informace",
    ],
    "en": [
        "https://ipc.gov.cz/en/visa-and-residence-permit-types/",
        "https://ipc.gov.cz/en/forms-and-documents/",
        "https://ipc.gov.cz/en/new-reservation/",
        "https://ipc.gov.cz/en/administrative-proceedings/",
        "https://ipc.gov.cz/en/obligations-for-foreigners/",
        "https://ipc.gov.cz/en/need-advice/",
        "https://ipc.gov.cz/en/contacts/",
        "https://ipc.gov.cz/en/administrative-fees/",
        "https://ipc.gov.cz/en/special-long-term-residence-permit/",
        "https://kurzy.frs.gov.cz/en/introductory-information",
    ],
}

ALLOWED_DOMAINS = {
    "ipc.gov.cz",
    "www.ipc.gov.cz",
    "mv.gov.cz",
    "www.mv.gov.cz",
    "kurzy.frs.gov.cz",
}

PRIMARY_SITEMAP_DOMAINS = [
    "ipc.gov.cz",
    "kurzy.frs.gov.cz",
]

IMPORTANT_PATTERNS = [
    "advice",
    "appointment",
    "bezviz",
    "biometr",
    "biometric",
    "cislo-jednaci",
    "client-center",
    "client-centre",
    "complete-application",
    "contacts",
    "cizinc",
    "dolozen",
    "employee-card",
    "faq",
    "family",
    "formulare",
    "forms-and-documents",
    "guide",
    "hlaseni",
    "klientske-centrum",
    "kontakty",
    "kratkodob",
    "long-term",
    "navody",
    "need-advice",
    "obcane-tretich-zemi",
    "odstraneni-vad",
    "otazky",
    "permanent",
    "pobyt",
    "poradna",
    "prechodny",
    "preklenovaci",
    "question",
    "reporting",
    "reservation",
    "residence",
    "rezerv",
    "short-term",
    "slouceni",
    "stav-rizeni",
    "stay",
    "stays",
    "student",
    "studium",
    "study",
    "temporary",
    "third-country",
    "trvaly",
    "visa",
    "viza",
    "vizum",
    "zamestnanecka-karta",
]

DOCUMENT_EXTENSIONS = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".odt",
    ".ods",
    ".rtf",
)

ASSET_EXTENSIONS = (
    ".avif",
    ".css",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".map",
    ".mp3",
    ".mp4",
    ".png",
    ".svg",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".xml",
    ".zip",
)

SKIP_PATH_PARTS = (
    "/wp-admin/",
    "/wp-content/cache/",
    "/wp-content/plugins/",
    "/wp-content/themes/",
    "/wp-includes/",
    "/wp-json/",
    "/feed/",
    "/author/",
    "/tag/",
)

NOISY_EXACT_LINES = {
    "cookies",
    "cookie settings",
    "menu",
    "privacy policy",
    "search",
    "skip to content",
    "vyhledavani",
}

NOISY_PREFIXES = (
    "javascript:",
    "data:",
)


@dataclass
class FetchResult:
    url: str
    content_type: str
    body: bytes


@dataclass
class PageRecord:
    title: str
    url: str
    output_file: str
    sha256: str
    char_count: int
    link_count: int


@dataclass
class LinkRecord:
    source_url: str
    target_url: str
    link_type: str
    crawlable: bool
    text: str = ""


@dataclass
class SkipRecord:
    url: str
    reason: str


@dataclass
class DocumentRecord:
    source_url: str
    document_url: str
    output_file: str | None = None
    status: str = "discovered"
    error: str | None = None


class ReadableHTMLParser(HTMLParser):
    """Extract Markdown-ish text and links from HTML without dependencies."""

    SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas"}
    SKIP_CONTENT_TAGS = {"header", "footer", "nav", "aside"}
    HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    BLOCK_TAGS = {
        "address",
        "article",
        "blockquote",
        "br",
        "dd",
        "details",
        "div",
        "dl",
        "dt",
        "figcaption",
        "hr",
        "main",
        "p",
        "pre",
        "section",
        "summary",
        "table",
    }

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: dict[str, str] = {}
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.heading_parts: list[str] = []
        self._skip_depth = 0
        self._skip_content_depth = 0
        self._in_title = False
        self._in_h1 = False
        self._list_stack: list[str] = []
        self._link_stack: list[dict[str, list[str] | str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value for key, value in attrs if value is not None}

        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        href = ""
        if tag == "a" and attrs_dict.get("href"):
            href = normalize_url(urljoin(self.base_url, attrs_dict["href"]))
            self.links.setdefault(href, "")

        if tag in self.SKIP_CONTENT_TAGS:
            self._skip_content_depth += 1
            return
        if self._skip_content_depth:
            return

        if tag == "a" and href:
            self._link_stack.append({"href": href, "text": []})
            return

        if tag == "title":
            self._in_title = True
            return

        if tag == "h1":
            self._in_h1 = True

        if tag in self.HEADING_TAGS:
            level = min(int(tag[1]), 4)
            self._append_block(f"{'#' * level} ")
        elif tag in {"ul", "ol"}:
            self._list_stack.append(tag)
            self._append_block("")
        elif tag == "li":
            marker = "1. " if self._list_stack and self._list_stack[-1] == "ol" else "- "
            self._append_block(marker)
        elif tag in {"tr"}:
            self._append_block("")
        elif tag in {"td", "th"}:
            self._append_inline(" | ")
        elif tag in self.BLOCK_TAGS:
            self._append_block("")

        alt_text = attrs_dict.get("alt")
        if tag == "img" and alt_text:
            self._append_inline(f"[image: {clean_inline_text(alt_text)}]")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return

        if tag in self.SKIP_CONTENT_TAGS and self._skip_content_depth:
            self._skip_content_depth -= 1
            return
        if self._skip_content_depth:
            return

        if tag == "a" and self._link_stack:
            link = self._link_stack.pop()
            href = str(link["href"])
            text = clean_inline_text(" ".join(str(part) for part in link["text"]))
            if text:
                self.links[href] = text
                if is_content_link(href) and not looks_like_url(text):
                    self._append_inline(f"[{escape_markdown_link_text(text)}]({href})")
                else:
                    self._append_inline(text)
            return

        if tag == "title":
            self._in_title = False
            return
        if tag == "h1":
            self._in_h1 = False

        if tag in self.HEADING_TAGS:
            self._append_block("")
        elif tag in {"ul", "ol"} and self._list_stack:
            self._list_stack.pop()
            self._append_block("")
        elif tag in {"li", "p", "div", "section", "article", "details", "summary", "tr"}:
            self._append_block("")
        elif tag == "br":
            self._append_block("")

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._skip_content_depth:
            return

        text = clean_inline_text(data)
        if not text:
            return

        if self._in_title:
            self.title_parts.append(text)
        if self._in_h1:
            self.heading_parts.append(text)
        if self._link_stack:
            self._link_stack[-1]["text"].append(text)  # type: ignore[index, union-attr]
            return

        self._append_inline(text)

    def _append_inline(self, text: str) -> None:
        if not text:
            return
        self.parts.append(text)
        self.parts.append(" ")

    def _append_block(self, text: str) -> None:
        self.parts.append("\n\n")
        if text:
            self.parts.append(text)

    @property
    def title(self) -> str:
        heading = clean_inline_text(" ".join(self.heading_parts))
        if heading:
            return heading
        return clean_inline_text(" ".join(self.title_parts))

    @property
    def text(self) -> str:
        return clean_page_text("".join(self.parts))


def normalize_url(url: str) -> str:
    url = html.unescape(url.strip())
    url, _fragment = urldefrag(url)
    parsed = urlparse(url)
    if not parsed.scheme and parsed.netloc:
        parsed = urlparse(f"https:{url}")

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = quote(unquote(parsed.path or "/"), safe="/:@%+-._~")
    normalized = parsed._replace(scheme=scheme, netloc=netloc, path=path, query="", fragment="").geturl()

    path_name = posixpath.basename(path)
    if path_name and "." in path_name:
        return normalized
    if not normalized.endswith("/"):
        return normalized + "/"
    return normalized


def clean_inline_text(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_page_text(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    lines: list[str] = []
    previous_line = ""
    for raw_line in text.splitlines():
        line = clean_inline_text(raw_line)
        if len(line) < 2:
            continue
        lowered = line.lower()
        if lowered in NOISY_EXACT_LINES:
            continue
        if lowered.startswith(NOISY_PREFIXES):
            continue
        line = re.sub(r"\s+([.,;:!?])", r"\1", line)
        if line == previous_line:
            continue
        previous_line = line
        lines.append(line)
    return "\n\n".join(lines).strip()


def escape_markdown_link_text(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


def looks_like_url(text: str) -> bool:
    lowered = text.lower()
    return lowered.startswith(("http://", "https://", "mailto:", "tel:"))


def slugify(text: str, fallback: str) -> str:
    text = text.lower()
    replacements = {
        "á": "a",
        "č": "c",
        "ď": "d",
        "é": "e",
        "ě": "e",
        "í": "i",
        "ň": "n",
        "ó": "o",
        "ř": "r",
        "š": "s",
        "ť": "t",
        "ú": "u",
        "ů": "u",
        "ý": "y",
        "ž": "z",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text or fallback)[:90].strip("-")


def is_allowed_domain(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() in ALLOWED_DOMAINS


def path_has_extension(url: str, extensions: tuple[str, ...]) -> bool:
    return urlparse(url).path.lower().endswith(extensions)


def is_document_url(url: str) -> bool:
    return path_has_extension(url, DOCUMENT_EXTENSIONS)


def is_asset_url(url: str) -> bool:
    return path_has_extension(url, ASSET_EXTENSIONS)


def has_skipped_path(url: str) -> bool:
    lowered_path = urlparse(url).path.lower()
    return any(part in lowered_path for part in SKIP_PATH_PARTS)


def language_matches(url: str, language: str) -> bool:
    if language == "all":
        return True

    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    path = parsed.path or "/"

    if "kurzy.frs.gov.cz" in domain:
        if language == "cs":
            return path == "/" or path.startswith("/cs/")
        if language == "en":
            return path == "/" or path.startswith("/en/")

    if "ipc.gov.cz" in domain:
        if language == "cs":
            return not path.startswith(("/en/", "/uk/", "/ua/", "/ru/"))
        if language == "en":
            return path == "/" or path.startswith("/en/")

    if "mv.gov.cz" in domain:
        if language == "en":
            return "/en/" in path or path.startswith("/clanek/")
        return True

    return True


def is_important_url(url: str) -> bool:
    lowered = unquote(url.lower())
    return any(pattern in lowered for pattern in IMPORTANT_PATTERNS)


def is_content_link(url: str) -> bool:
    if not is_allowed_domain(url):
        return False
    if has_skipped_path(url) or is_asset_url(url):
        return False
    return True


def is_crawlable_html_url(url: str, language: str, scope: str, seed_urls: set[str]) -> bool:
    if url in seed_urls:
        return True
    if not is_allowed_domain(url):
        return False
    if has_skipped_path(url) or is_asset_url(url) or is_document_url(url):
        return False
    if not language_matches(url, language):
        return False
    if scope == "important" and not is_important_url(url):
        return False
    return True


def classify_link(url: str, language: str, scope: str, seed_urls: set[str]) -> str:
    if is_document_url(url):
        return "document"
    if is_asset_url(url):
        return "asset"
    if is_crawlable_html_url(url, language, scope, seed_urls):
        return "html"
    if is_allowed_domain(url):
        return "same-domain"
    return "external"


def fetch_url(url: str, timeout: int) -> FetchResult:
    request = Request(
        url,
        headers={
            "User-Agent": "Jasne-RAG-Knowledge-Builder/0.2 (+https://jasne.app)",
            "Accept": "text/html,application/xhtml+xml,application/xml,text/xml,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        final_url = normalize_url(response.geturl())
        content_type = response.headers.get("content-type", "")
        return FetchResult(
            url=final_url,
            content_type=content_type,
            body=response.read(),
        )


def decode_body(fetch: FetchResult) -> str:
    charset_match = re.search(r"charset=([\w.-]+)", fetch.content_type, flags=re.IGNORECASE)
    charset = charset_match.group(1) if charset_match else "utf-8"
    return fetch.body.decode(charset, errors="replace")


def extract_sitemap_urls(xml_text: str) -> list[str]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    urls: list[str] = []
    for loc in root.iter():
        if loc.tag.endswith("loc") and loc.text:
            urls.append(normalize_url(loc.text))
    return urls


def discover_sitemap_urls(
    domains: Iterable[str],
    language: str,
    scope: str,
    seed_urls: set[str],
    timeout: int,
) -> tuple[list[str], list[SkipRecord]]:
    discovered: list[str] = []
    skips: list[SkipRecord] = []
    queue = deque(normalize_url(f"https://{domain}/sitemap.xml") for domain in domains)
    visited: set[str] = set()

    while queue:
        sitemap_url = queue.popleft()
        if sitemap_url in visited:
            continue
        visited.add(sitemap_url)

        try:
            fetched = fetch_url(sitemap_url, timeout=timeout)
            text = decode_body(fetched)
        except (HTTPError, URLError, TimeoutError, UnicodeError) as exc:
            skips.append(SkipRecord(sitemap_url, f"sitemap fetch failed: {exc}"))
            continue

        for found_url in extract_sitemap_urls(text):
            if found_url.endswith(".xml") or "sitemap" in posixpath.basename(urlparse(found_url).path).lower():
                if found_url not in visited:
                    queue.append(found_url)
                continue
            if is_crawlable_html_url(found_url, language, scope, seed_urls):
                discovered.append(found_url)

    return discovered, skips


def write_markdown(
    output_dir: Path,
    url: str,
    title: str,
    text: str,
    links: dict[str, str],
    category: str,
    language: str,
) -> PageRecord:
    source_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    slug = slugify(title, fallback=source_hash)
    path = output_dir / f"{slug}-{source_hash}.md"
    body_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    source_links = [
        f"- [{escape_markdown_link_text(label or link)}]({link})"
        for link, label in sorted(links.items())
        if is_content_link(link)
    ]

    markdown_parts = [
        "---",
        f"category: {category}",
        f"language: {language}",
        f"source_url: {url}",
        "source_name: IPC/MVCR/FRS",
        f"content_sha256: {body_hash}",
        "---",
        "",
        f"# {title}",
        "",
        f"Source: {url}",
        "",
        text,
        "",
    ]
    if source_links:
        markdown_parts.extend(["## Source Links", "", *source_links, ""])

    path.write_text("\n".join(markdown_parts), encoding="utf-8")
    return PageRecord(
        title=title,
        url=url,
        output_file=str(path),
        sha256=body_hash,
        char_count=len(text),
        link_count=len(source_links),
    )


def safe_document_filename(url: str) -> str:
    parsed = urlparse(url)
    name = posixpath.basename(unquote(parsed.path)) or hashlib.sha256(url.encode("utf-8")).hexdigest()
    slug = slugify(posixpath.splitext(name)[0], fallback=hashlib.sha256(url.encode("utf-8")).hexdigest()[:10])
    suffix = posixpath.splitext(name)[1].lower() or ".bin"
    return f"{slug}-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:10]}{suffix}"


def download_document(document_url: str, output_dir: Path, timeout: int) -> tuple[str | None, str | None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        fetched = fetch_url(document_url, timeout=timeout)
    except (HTTPError, URLError, TimeoutError, UnicodeError) as exc:
        return None, str(exc)

    path = output_dir / safe_document_filename(fetched.url)
    path.write_bytes(fetched.body)
    return str(path), None


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> object:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def existing_path(path_text: str, output_dir: Path) -> Path | None:
    if not path_text:
        return None
    path = Path(path_text)
    if path.exists():
        return path
    if not path.is_absolute():
        candidate = output_dir / path.name
        if candidate.exists():
            return candidate
    return None


def load_existing_page_records(output_dir: Path) -> dict[str, PageRecord]:
    data = read_json(output_dir / "manifest.json")
    if not isinstance(data, list):
        return {}

    records: dict[str, PageRecord] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        url = row.get("url")
        output_file = row.get("output_file")
        if not isinstance(url, str) or not isinstance(output_file, str):
            continue
        output_path = existing_path(output_file, output_dir)
        if output_path is None:
            continue
        records[normalize_url(url)] = PageRecord(
            title=str(row.get("title", "")),
            url=normalize_url(url),
            output_file=str(output_path),
            sha256=str(row.get("sha256", "")),
            char_count=int(row.get("char_count", 0) or 0),
            link_count=int(row.get("link_count", 0) or 0),
        )
    return records


def load_existing_link_records(
    output_dir: Path,
    language: str,
    scope: str,
    seed_urls: set[str],
) -> tuple[list[LinkRecord], dict[str, list[LinkRecord]], set[tuple[str, str]]]:
    data = read_json(output_dir / "link_tree.json")
    if not isinstance(data, list):
        return [], {}, set()

    records: list[LinkRecord] = []
    by_source: dict[str, list[LinkRecord]] = {}
    known: set[tuple[str, str]] = set()

    for row in data:
        if not isinstance(row, dict):
            continue
        source_url = row.get("source_url")
        target_url = row.get("target_url")
        if not isinstance(source_url, str) or not isinstance(target_url, str):
            continue

        source = normalize_url(source_url)
        target = normalize_url(target_url)
        crawlable = bool(row.get("crawlable", row.get("allowed", is_crawlable_html_url(target, language, scope, seed_urls))))
        record = LinkRecord(
            source_url=source,
            target_url=target,
            link_type=str(row.get("link_type", classify_link(target, language, scope, seed_urls))),
            crawlable=crawlable,
            text=str(row.get("text", "")),
        )
        key = (source, target)
        if key in known:
            continue
        known.add(key)
        records.append(record)
        by_source.setdefault(source, []).append(record)

    return records, by_source, known


def load_existing_document_records(output_dir: Path) -> dict[str, DocumentRecord]:
    data = read_json(output_dir / "document_links.json")
    if not isinstance(data, list):
        return {}

    records: dict[str, DocumentRecord] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        document_url = row.get("document_url")
        if not isinstance(document_url, str):
            continue
        output_file = row.get("output_file")
        output_path = existing_path(output_file, output_dir / "_documents") if isinstance(output_file, str) else None
        normalized_document_url = normalize_url(document_url)
        records[normalized_document_url] = DocumentRecord(
            source_url=normalize_url(str(row.get("source_url", ""))),
            document_url=normalized_document_url,
            output_file=str(output_path) if output_path else None,
            status=str(row.get("status", "discovered")),
            error=str(row["error"]) if row.get("error") else None,
        )
    return records


def prepare_output_dir(output_dir: Path, fresh: bool) -> None:
    if fresh and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def crawl(
    seeds: Iterable[str],
    output_dir: Path,
    language: str,
    scope: str,
    max_pages: int,
    delay: float,
    timeout: int,
    min_chars: int,
    use_sitemap: bool,
    download_documents: bool,
    resume: bool,
) -> tuple[list[PageRecord], list[LinkRecord], list[SkipRecord], list[DocumentRecord]]:
    seed_urls = {normalize_url(seed) for seed in seeds}
    queue = deque(seed_urls)
    queued = set(queue)
    visited: set[str] = set()
    existing_records = load_existing_page_records(output_dir) if resume else {}
    existing_links, existing_links_by_source, known_links = load_existing_link_records(output_dir, language, scope, seed_urls) if resume else ([], {}, set())
    records: list[PageRecord] = list(existing_records.values())
    link_records: list[LinkRecord] = existing_links
    skip_records: list[SkipRecord] = []
    document_records: dict[str, DocumentRecord] = load_existing_document_records(output_dir) if resume else {}
    saved_this_run = 0

    if use_sitemap:
        sitemap_urls, sitemap_skips = discover_sitemap_urls(
            domains=PRIMARY_SITEMAP_DOMAINS,
            language=language,
            scope=scope,
            seed_urls=seed_urls,
            timeout=timeout,
        )
        skip_records.extend(sitemap_skips)
        for sitemap_url in sitemap_urls:
            if sitemap_url not in queued:
                queue.append(sitemap_url)
                queued.add(sitemap_url)

    while queue and (max_pages == 0 or saved_this_run < max_pages):
        url = queue.popleft()
        queued.discard(url)
        if url in visited:
            continue
        if not is_crawlable_html_url(url, language, scope, seed_urls):
            skip_records.append(SkipRecord(url, "not crawlable by domain/language/scope rules"))
            continue
        visited.add(url)

        cached_record = existing_records.get(url)
        if cached_record is not None:
            print(f"cached {cached_record.output_file}")
            for cached_link in existing_links_by_source.get(url, []):
                if is_document_url(cached_link.target_url):
                    document_records.setdefault(
                        cached_link.target_url,
                        DocumentRecord(source_url=url, document_url=cached_link.target_url),
                    )
                if cached_link.crawlable and cached_link.target_url not in visited and cached_link.target_url not in queued:
                    queue.append(cached_link.target_url)
                    queued.add(cached_link.target_url)
            continue

        try:
            fetched = fetch_url(url, timeout=timeout)
            if "text/html" not in fetched.content_type and "application/xhtml+xml" not in fetched.content_type:
                skip_records.append(SkipRecord(url, f"unsupported content type: {fetched.content_type}"))
                continue
            if fetched.url in existing_records:
                cached_record = existing_records[fetched.url]
                print(f"cached {cached_record.output_file}")
                for cached_link in existing_links_by_source.get(fetched.url, []):
                    if cached_link.crawlable and cached_link.target_url not in visited and cached_link.target_url not in queued:
                        queue.append(cached_link.target_url)
                        queued.add(cached_link.target_url)
                continue
            raw_html = decode_body(fetched)
            parser = ReadableHTMLParser(fetched.url)
            parser.feed(raw_html)
            parser.close()
            visited.add(fetched.url)
        except (HTTPError, URLError, TimeoutError, ValueError, UnicodeError) as exc:
            skip_records.append(SkipRecord(url, f"fetch/parse failed: {exc}"))
            print(f"skip {url}: {exc}")
            continue

        for link, label in parser.links.items():
            normalized_link = normalize_url(link)
            crawlable = is_crawlable_html_url(normalized_link, language, scope, seed_urls)
            link_key = (fetched.url, normalized_link)
            if link_key not in known_links:
                known_links.add(link_key)
                link_records.append(
                    LinkRecord(
                        source_url=fetched.url,
                        target_url=normalized_link,
                        link_type=classify_link(normalized_link, language, scope, seed_urls),
                        crawlable=crawlable,
                        text=label,
                    )
                )

            if is_document_url(normalized_link):
                document_records.setdefault(
                    normalized_link,
                    DocumentRecord(source_url=fetched.url, document_url=normalized_link),
                )
                continue

            if crawlable and normalized_link not in visited and normalized_link not in queued:
                queue.append(normalized_link)
                queued.add(normalized_link)

        title = parser.title or urlparse(fetched.url).path.strip("/") or "Untitled page"
        text = parser.text
        if len(text) >= min_chars:
            record = write_markdown(
                output_dir=output_dir,
                url=fetched.url,
                title=title,
                text=text,
                links=parser.links,
                category="cizinecka",
                language=language,
            )
            records.append(record)
            saved_this_run += 1
            print(f"saved {record.output_file}")
        else:
            skip_records.append(SkipRecord(fetched.url, f"only {len(text)} chars"))
            print(f"skip {fetched.url}: only {len(text)} chars")

        if delay:
            time.sleep(delay)

    documents = list(document_records.values())
    if download_documents:
        documents_dir = output_dir / "_documents"
        for document in documents:
            if document.output_file and existing_path(document.output_file, documents_dir):
                document.status = "downloaded"
                print(f"cached {document.output_file}")
                continue
            output_file, error = download_document(document.document_url, documents_dir, timeout)
            document.output_file = output_file
            document.error = error
            document.status = "downloaded" if output_file else "failed"
            if delay:
                time.sleep(delay)

    return records, link_records, skip_records, documents


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape official immigration pages for Jasne RAG.")
    parser.add_argument("--language", choices=["cs", "en", "all"], default="cs")
    parser.add_argument(
        "--scope",
        choices=["all", "important"],
        default="all",
        help="all crawls same-language site pages; important keeps only immigration-keyword URLs.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=1000,
        help="Maximum saved HTML pages. Use 0 for no explicit limit.",
    )
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--min-chars", type=int, default=300)
    parser.add_argument(
        "--output-dir",
        "--output_dir",
        type=Path,
        default=Path("knowledge/cizinecka"),
        help="Directory where Markdown pages and manifests will be saved.",
    )
    parser.add_argument(
        "--seed",
        action="append",
        default=[],
        help="Extra seed URL. Can be passed multiple times.",
    )
    parser.add_argument(
        "--no-sitemap",
        action="store_true",
        help="Do not read sitemap.xml. Useful for debugging pure link crawling.",
    )
    parser.add_argument(
        "--download-documents",
        action="store_true",
        help="Download discovered PDF/DOC/XLS files into output_dir/_documents. They are not parsed.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Delete output directory before scraping.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore existing manifest/link_tree and fetch pages again without deleting output first.",
    )
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    prepare_output_dir(args.output_dir, fresh=args.fresh)

    if args.language == "all":
        seeds = [*DEFAULT_SEEDS["cs"], *DEFAULT_SEEDS["en"], *args.seed]
    else:
        seeds = [*DEFAULT_SEEDS[args.language], *args.seed]

    records, links, skips, documents = crawl(
        seeds=seeds,
        output_dir=args.output_dir,
        language=args.language,
        scope=args.scope,
        max_pages=args.max_pages,
        delay=args.delay,
        timeout=args.timeout,
        min_chars=args.min_chars,
        use_sitemap=not args.no_sitemap,
        download_documents=args.download_documents,
        resume=not args.no_resume,
    )

    write_json(args.output_dir / "manifest.json", [asdict(record) for record in records])
    write_json(args.output_dir / "link_tree.json", [asdict(link) for link in links])
    write_json(args.output_dir / "skipped.json", [asdict(skip) for skip in skips])
    write_json(args.output_dir / "document_links.json", [asdict(document) for document in documents])

    print(
        "done: "
        f"{len(records)} pages, "
        f"{len(links)} links, "
        f"{len(documents)} documents, "
        f"{len(skips)} skipped; "
        f"output {args.output_dir}"
    )


if __name__ == "__main__":
    main()
