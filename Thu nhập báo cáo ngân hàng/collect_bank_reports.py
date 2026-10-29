from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import (
    parse_qsl,
    quote,
    urlencode,
    unquote,
    urljoin,
    urlsplit,
    urlunsplit,
)
from urllib.request import Request, urlopen


USER_AGENT = "BankReportResearchBot/1.0 (academic research; respects site access limits)"
DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}
FINANCIAL_TERMS = (
    "bao cao tai chinh",
    "bctc",
    "financial statement",
    "financial statements",
    "financial report",
)
SUSTAINABILITY_TERMS = (
    "phat trien ben vung",
    "ptbv",
    "sustainability",
    "sustainable development",
    "esg",
)
ANNUAL_REPORT_TERMS = (
    "bao cao thuong nien",
    "annual report",
    "annualreports",
)
REPORT_CATEGORIES = {
    "financial_statements",
    "sustainability",
    "annual_reports",
}
PAGE_TERMS = (
    *FINANCIAL_TERMS,
    *SUSTAINABILITY_TERMS,
    "bao cao",
    "bao-cao",
    "annual report",
    "bao cao thuong nien",
    "nha dau tu",
    "nha-dau-tu",
    "investor",
    "tai lieu",
)
PAGINATION_KEYS = {
    "page",
    "p",
    "cur",
    "delta",
    "start",
    "offset",
    "pageindex",
    "pagenumber",
}
MANIFEST_FIELDS = (
    "bank",
    "category",
    "year",
    "title",
    "filename",
    "source_url",
    "downloaded_at",
    "sha256",
)
ERROR_FIELDS = ("bank", "url", "error")
MAX_PAGE_BYTES = 20 * 1024 * 1024
MAX_DOCUMENT_BYTES = 200 * 1024 * 1024
EMBEDDED_DOCUMENT_PATTERN = re.compile(
    r"""(?P<quote>['"])(?P<url>[^'"<>\s]*\.(?:pdf|docx?|xlsx?)(?:\?[^'"<>\s]*)?)(?P=quote)""",
    re.IGNORECASE,
)
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/octet-stream",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip",
    "application/force-download",
    "application/x-download",
    "binary/octet-stream",
}


@dataclass(frozen=True)
class Link:
    href: str
    text: str


class ApiSource(TypedDict, total=False):
    url: str
    category: str
    response_format: str
    page_parameter: str
    year_start: int
    year_end: int | str
    year_parameter: str
    year_prefix: str


class BankSource(TypedDict):
    name: str
    slug: str
    seed_urls: list[str]
    api_sources: list[ApiSource]
    extra_documents: list[dict[str, str]]
    extra_pages: list[dict[str, str]]


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[Link] = []
        self._anchors: list[tuple[str, list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attributes = dict(attrs)
        href = attributes.get("href")
        if href:
            self._anchors.append(
                (
                    href.strip(),
                    [attributes.get("aria-label") or attributes.get("title") or ""],
                )
            )

    def handle_data(self, data: str) -> None:
        for _, text in self._anchors:
            text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._anchors:
            href, text = self._anchors.pop()
            self.links.append(Link(href, " ".join(" ".join(text).split())))


class DownloadAttributeParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[Link] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        href = dict(attrs).get("data-download-url")
        if href:
            self._href = href.strip()
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "li" and self._href is not None:
            self.links.append(
                Link(
                    quote(
                        urljoin(self.base_url, self._href),
                        safe=":/?#[]@!$&'()*+,;=%",
                    ),
                    " ".join(" ".join(self._text).split()),
                )
            )
            self._href = None
            self._text = []


def normalize(value: str) -> str:
    value = value.replace("đ", "d").replace("Đ", "D")
    value = value.replace("+", " ")
    value = unicodedata.normalize("NFKD", value)
    return "".join(char for char in value if not unicodedata.combining(char)).lower()


def classify_report(value: str) -> str | None:
    normalized = normalize(value)
    if any(term in normalized for term in SUSTAINABILITY_TERMS):
        return "sustainability"
    if any(term in normalized for term in FINANCIAL_TERMS):
        return "financial_statements"
    if any(term in normalized for term in ANNUAL_REPORT_TERMS):
        return "annual_reports"
    return None


def is_document_url(url: str) -> bool:
    return bool(document_filename(url))


def document_filename(url: str) -> str:
    segments = [unquote(segment) for segment in urlsplit(url).path.split("/") if segment]
    return next(
        (
            segment
            for segment in reversed(segments)
            if Path(segment).suffix.lower() in DOCUMENT_EXTENSIONS
        ),
        "",
    )


def is_pagination(link: Link, url: str) -> bool:
    if link.text.strip().isdigit():
        return True
    return any(
        key.lower() in PAGINATION_KEYS
        or key.lower().endswith(("_cur", "_delta", "_page", "_offset"))
        for key, _ in parse_qsl(urlsplit(url).query)
    )


def is_relevant_page(link: Link, url: str) -> bool:
    parts = urlsplit(url)
    candidate = normalize(f"{link.text} {parts.path} {parts.query}")
    return any(term in candidate for term in PAGE_TERMS) or is_pagination(link, url)


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    kept_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in {"download", "utm_source", "utm_medium", "utm_campaign"}
    ]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(kept_query), ""))


def safe_filename(value: str) -> str:
    value = value.replace("\\", "_").replace("/", "_")
    value = re.sub(r'[<>:"|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", "_", value).strip(" ._")
    return value[:160] or "report"


def extract_year(value: str) -> str:
    value = value.split("?", 1)[0]
    matches = re.findall(r"20\d{2}", value)
    year_ranges = re.findall(r"20\d{2}\s*[-–]\s*(20\d{2})", value)
    if year_ranges:
        return year_ranges[-1]
    return matches[0] if matches else ""


def get_links(page: bytes, base_url: str) -> list[Link]:
    parser = AnchorParser()
    parser.feed(page.decode("utf-8", errors="replace"))
    links: list[Link] = []
    for link in parser.links:
        absolute_url = urljoin(base_url, link.href)
        if urlsplit(absolute_url).scheme in {"http", "https"}:
            links.append(
                Link(
                    quote(absolute_url, safe=":/?#[]@!$&'()*+,;=%"),
                    link.text,
                )
            )
    return links


def get_embedded_document_links(page: bytes, base_url: str) -> list[Link]:
    text = page.decode("utf-8", errors="replace")
    links: list[Link] = []
    for match in EMBEDDED_DOCUMENT_PATTERN.finditer(text):
        raw_url = html.unescape(match.group("url"))
        absolute_url = urljoin(base_url, raw_url)
        links.append(
            Link(
                quote(absolute_url, safe=":/?#[]@!$&'()*+,;=%"),
                "",
            )
        )
    return links


def get_data_download_links(fragment: str, base_url: str) -> list[Link]:
    parser = DownloadAttributeParser(base_url)
    parser.feed(fragment)
    return parser.links


def host_is_allowed(url: str, allowed_hosts: set[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    host = host.removeprefix("www.")
    return any(
        host == allowed.removeprefix("www.")
        or host.endswith(f".{allowed.removeprefix('www.')}")
        for allowed in allowed_hosts
    )


def read_page(url: str, timeout: int) -> tuple[bytes, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        body = response.read(MAX_PAGE_BYTES + 1)
        if len(body) > MAX_PAGE_BYTES:
            raise ValueError(f"HTML page exceeded {MAX_PAGE_BYTES} bytes")
        return body, response.geturl()


def save_document(url: str, destination: Path, timeout: int) -> tuple[str, int]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    temporary = destination.with_name(f".{destination.name}.part")
    digest = hashlib.sha256()
    total = 0
    complete = False
    try:
        with urlopen(request, timeout=timeout) as response, temporary.open("wb") as output:
            content_type = response.headers.get_content_type()
            if content_type not in ALLOWED_CONTENT_TYPES:
                raise ValueError(f"Unexpected content type: {content_type}")
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_DOCUMENT_BYTES:
                raise ValueError(f"Document exceeded {MAX_DOCUMENT_BYTES} bytes")
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_DOCUMENT_BYTES:
                    raise ValueError(f"Document exceeded {MAX_DOCUMENT_BYTES} bytes")
                digest.update(chunk)
                output.write(chunk)
        if total == 0:
            raise ValueError("Downloaded document is empty")
        if destination.suffix.lower() == ".pdf":
            with temporary.open("rb") as downloaded:
                if not downloaded.read(5).startswith(b"%PDF-"):
                    raise ValueError("The response is not a valid PDF file")
        temporary.replace(destination)
        complete = True
    finally:
        if not complete:
            temporary.unlink(missing_ok=True)
    return digest.hexdigest(), total


def existing_document_digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as document:
            if path.suffix.lower() == ".pdf" and not document.read(5).startswith(b"%PDF-"):
                return None
            document.seek(0)
            while chunk := document.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def save_html_page(content: bytes, destination: Path) -> tuple[str, int]:
    temporary = destination.with_name(f".{destination.name}.part")
    digest = hashlib.sha256(content).hexdigest()
    if not content or b"<html" not in content[:8192].lower():
        raise ValueError("The response is not an HTML page")
    complete = False
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as output:
            output.write(content)
        temporary.replace(destination)
        complete = True
    finally:
        if not complete:
            temporary.unlink(missing_ok=True)
    return digest, len(content)


def read_sources(path: Path) -> list[BankSource]:
    with path.open(encoding="utf-8") as source_file:
        data = json.load(source_file)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    banks = data.get("banks")
    if not isinstance(banks, list) or not banks:
        raise ValueError(f"{path} must contain a non-empty 'banks' list")
    validated: list[BankSource] = []
    for bank in banks:
        if not isinstance(bank, dict) or not bank.get("name") or not bank.get("slug"):
            raise ValueError(f"Each bank in {path} must have a name and slug")
        slug = bank.get("slug")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9-]+", slug):
            raise ValueError(f"Bank slugs in {path} must use lowercase letters, digits, and hyphens")
        seed_urls = bank.get("seed_urls")
        if (
            not isinstance(seed_urls, list)
            or not seed_urls
            or not all(isinstance(url, str) and url.startswith(("http://", "https://")) for url in seed_urls)
        ):
            raise ValueError(f"Each bank in {path} must have at least one seed URL")
        api_sources = bank.get("api_sources", [])
        if not isinstance(api_sources, list):
            raise ValueError(f"Optional api_sources for each bank in {path} must be a list")
        validated_api_sources: list[ApiSource] = []
        for api in api_sources:
            if (
                not isinstance(api, dict)
                or not isinstance(api.get("url"), str)
                or not api["url"].startswith(("http://", "https://"))
                or api.get("category") not in REPORT_CATEGORIES
                or api.get("response_format", "acb_posts")
                not in {"acb_posts", "sitecore_html", "agribank_html"}
                or (
                    "page_parameter" in api
                    and not isinstance(api["page_parameter"], str)
                )
            ):
                raise ValueError(
                    f"Each API source in {path} must have a valid URL, category, and response format"
                )
            year_start = api.get("year_start")
            year_end = api.get("year_end")
            if (year_start is None) != (year_end is None) or (
                year_start is not None
                and (
                    not isinstance(year_start, int)
                    or not (
                        year_end == "current"
                        or isinstance(year_end, int) and year_start <= year_end
                    )
                )
            ):
                raise ValueError(f"Year ranges in API sources in {path} must be valid")
            validated_api_sources.append(dict(api))
        extra_documents = bank.get("extra_documents", [])
        if not isinstance(extra_documents, list) or not all(
            isinstance(document, dict)
            and isinstance(document.get("url"), str)
            and document["url"].startswith(("http://", "https://"))
            and document.get("category") in REPORT_CATEGORIES
            for document in extra_documents
        ):
            raise ValueError(
                f"Each extra document in {path} must have a valid URL and category"
            )
        extra_pages = bank.get("extra_pages", [])
        if not isinstance(extra_pages, list) or not all(
            isinstance(page, dict)
            and isinstance(page.get("url"), str)
            and page["url"].startswith(("http://", "https://"))
            and page.get("category") in REPORT_CATEGORIES
            and isinstance(page.get("title"), str)
            and page["title"].strip()
            for page in extra_pages
        ):
            raise ValueError(
                f"Each extra page in {path} must have a valid URL, category, and title"
            )
        validated.append(
            {
                "name": str(bank["name"]),
                "slug": slug,
                "seed_urls": seed_urls,
                "api_sources": validated_api_sources,
                "extra_documents": [
                    {
                        "url": str(document["url"]),
                        "category": str(document["category"]),
                        "title": str(document.get("title") or ""),
                    }
                    for document in extra_documents
                ],
                "extra_pages": [
                    {
                        "url": str(page["url"]),
                        "category": str(page["category"]),
                        "title": str(page["title"]),
                    }
                    for page in extra_pages
                ],
            }
        )
    return validated


def set_query_parameter(url: str, parameter: str, value: str) -> str:
    parameter_value = value
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    replaced = False
    updated: list[tuple[str, str]] = []
    for key, query_value in query:
        if key.lower() == parameter.lower():
            updated.append((key, parameter_value))
            replaced = True
        else:
            updated.append((key, query_value))
    if not replaced:
        updated.append((parameter, parameter_value))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(updated), parts.fragment)
    )


def set_page_parameter(url: str, page_number: int) -> str:
    return set_query_parameter(url, "page", str(page_number))


def api_documents(
    payload: object,
    api_source: ApiSource,
    base_url: str,
) -> tuple[list[Link], int]:
    response_format = api_source.get("response_format", "acb_posts")
    if response_format == "agribank_html":
        if not isinstance(payload, str):
            raise ValueError("Agribank API response must be an HTML string")
        links = [
            link
            for link in get_links(payload.encode("utf-8"), base_url)
            if is_document_url(link.href)
        ]
        return links, 0
    if not isinstance(payload, dict):
        raise ValueError("API response must be a JSON object")
    if response_format == "sitecore_html":
        items = payload.get("Results")
        total = payload.get("Count", 0)
        if not isinstance(items, list) or not isinstance(total, int):
            raise ValueError("Sitecore response must contain 'Results' and 'Count'")
        links: list[Link] = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("Html"), str):
                continue
            links.extend(
                link
                for link in get_data_download_links(item["Html"], base_url)
                if is_document_url(link.href)
            )
        return links, total

    items = payload.get("data")
    meta = payload.get("meta")
    if not isinstance(items, list) or not isinstance(meta, dict):
        raise ValueError("API response must contain 'data' and 'meta'")
    last_page = meta.get("last_page", 1)
    if not isinstance(last_page, int):
        raise ValueError("API metadata 'last_page' must be an integer")
    links = []
    for item in items:
        if not isinstance(item, dict):
            continue
        file_info = item.get("featured_image")
        if not isinstance(file_info, dict):
            continue
        file_url = file_info.get("path")
        if not isinstance(file_url, str) or not file_url.startswith(("http://", "https://")):
            continue
        title = str(item.get("title") or file_info.get("title") or "")
        if not extract_year(title):
            categories = item.get("categories")
            if isinstance(categories, list):
                category_year = next(
                    (
                        year
                        for item_category in categories
                        if isinstance(item_category, dict)
                        and isinstance(item_category.get("name"), str)
                        if (year := extract_year(item_category["name"]))
                    ),
                    "",
                )
                if category_year:
                    title = f"{title} - Báo cáo thường niên {category_year}"
        file_link = Link(
            quote(file_url, safe=":/?#[]@!$&'()*+,;=%"),
            title,
        )
        if is_document_url(file_link.href):
            links.append(file_link)
    return links, last_page


def crawl_bank(
    bank: BankSource,
    output_dir: Path,
    max_pages: int,
    max_depth: int,
    delay: float,
    timeout: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]], int]:
    bank_name = str(bank["name"])
    slug = str(bank["slug"])
    seed_urls = bank["seed_urls"]
    allowed_hosts = {
        (urlsplit(url).hostname or "").lower()
        for url in seed_urls
    }
    queue = deque(
        (url, 0, bool(urlsplit(url).path.strip("/")))
        for url in seed_urls
    )
    visited: set[str] = set()
    found: dict[str, tuple[str, str, str]] = {}
    errors: list[dict[str, str]] = []
    pages_seen = 0
    bank_dir = output_dir / slug
    bank_dir.mkdir(parents=True, exist_ok=True)

    while queue and pages_seen < max_pages:
        url, depth, inherited_context = queue.popleft()
        key = canonical_url(url)
        if key in visited or not host_is_allowed(url, allowed_hosts):
            continue
        visited.add(key)
        if delay:
            time.sleep(delay)
        try:
            page, final_url = read_page(url, timeout)
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
            errors.append({"bank": bank_name, "url": url, "error": str(error)})
            print(f"[{bank_name}] ERROR page {url}: {error}", file=sys.stderr)
            continue

        pages_seen += 1
        page_context = inherited_context or any(
            term in normalize(urlsplit(final_url).path) for term in PAGE_TERMS
        )
        for embedded_link in get_embedded_document_links(page, final_url):
            embedded_category = classify_report(unquote(embedded_link.href))
            if embedded_category:
                found.setdefault(
                    canonical_url(embedded_link.href),
                    (
                        embedded_link.href,
                        embedded_category,
                        document_filename(embedded_link.href),
                    ),
                )
        page_links = get_links(page, final_url)
        document_hints: dict[str, tuple[str, str]] = {}
        download_urls: dict[str, str] = {}
        for link in page_links:
            key = canonical_url(link.href)
            category = classify_report(f"{link.text} {unquote(link.href)}")
            if category and "/documents/" in urlsplit(link.href).path.lower():
                document_hints[key] = (category, link.text)
            if any(
                query_key.lower() == "download" and query_value.lower() == "true"
                for query_key, query_value in parse_qsl(urlsplit(link.href).query)
            ):
                download_urls[key] = link.href

        for link in page_links:
            key = canonical_url(link.href)
            category = classify_report(f"{link.text} {unquote(link.href)}")
            hint = document_hints.get(key)
            if category is None and hint is not None:
                category = hint[0]
            label = link.text if classify_report(f"{link.text} {unquote(link.href)}") else (
                hint[1] if hint is not None else link.text
            )
            is_document = is_document_url(link.href) or (
                "/documents/" in urlsplit(link.href).path.lower()
                and (category is not None or page_context)
            )
            link_context = page_context or is_relevant_page(link, link.href)
            if is_document and (category or page_context):
                category = category or classify_report(final_url)
                if category:
                    existing = found.get(key)
                    document_url = download_urls.get(key, link.href)
                    if existing is None or document_url != existing[0]:
                        found[key] = (document_url, category, label or (hint[1] if hint else ""))
                continue
            if (
                depth < max_depth
                and host_is_allowed(link.href, allowed_hosts)
                and (is_relevant_page(link, link.href) or (page_context and is_pagination(link, link.href)))
            ):
                queue.append((link.href, depth + 1, link_context))

    api_pages_seen = 0
    for document in bank["extra_documents"]:
        url = quote(document["url"], safe=":/?#[]@!$&'()*+,;=%")
        key = canonical_url(url)
        existing = found.get(key)
        if existing is None:
            found[key] = (url, document["category"], document["title"])
        elif document["title"]:
            found[key] = (existing[0], document["category"], document["title"])

    page_records: list[dict[str, str]] = []
    for page in bank.get("extra_pages", []):
        url = quote(page["url"], safe=":/?#[]@!$&'()*+,;=%")
        file_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
        title_slug = safe_filename(normalize(page["title"]).replace(" ", "_"))
        destination = (
            bank_dir
            / page["category"]
            / safe_filename(f"{slug}_{page['category']}_{file_hash}_{title_slug}.html")
        )
        existing_digest = existing_document_digest(destination)
        if existing_digest is not None:
            downloaded_at = datetime.fromtimestamp(
                destination.stat().st_mtime,
                timezone.utc,
            ).isoformat()
            page_records.append(
                {
                    "bank": bank_name,
                    "category": page["category"],
                    "year": extract_year(page["title"]),
                    "title": page["title"],
                    "filename": str(destination.relative_to(output_dir)),
                    "source_url": url,
                    "downloaded_at": downloaded_at,
                    "sha256": existing_digest,
                }
            )
            continue
        if delay:
            time.sleep(delay)
        try:
            content, _ = read_page(url, timeout)
            digest, _ = save_html_page(content, destination)
            page_records.append(
                {
                    "bank": bank_name,
                    "category": page["category"],
                    "year": extract_year(page["title"]),
                    "title": page["title"],
                    "filename": str(destination.relative_to(output_dir)),
                    "source_url": url,
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                    "sha256": digest,
                }
            )
            print(f"[{bank_name}] Archived {destination}")
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
            errors.append({"bank": bank_name, "url": url, "error": str(error)})
            print(f"[{bank_name}] ERROR archive page {url}: {error}", file=sys.stderr)

    for api_source in bank["api_sources"]:
        endpoint = api_source["url"]
        category = api_source["category"]
        year_start = api_source.get("year_start")
        year_end = api_source.get("year_end")
        year_parameter = api_source.get("year_parameter", "year")
        year_prefix = api_source.get("year_prefix", "")
        page_parameter = api_source.get("page_parameter", "page")
        if year_start is not None:
            if not isinstance(year_end, int):
                year_end = datetime.now().year
            request_specs = [
                (set_query_parameter(endpoint, year_parameter, f"{year_prefix}{year}"), None)
                for year in range(year_start, year_end + 1)
            ]
        else:
            request_specs = [(endpoint, page_number) for page_number in range(1, max_pages + 1)]

        for api_endpoint, page_number in request_specs:
            request_url = (
                set_query_parameter(api_endpoint, page_parameter, str(page_number))
                if page_number is not None
                else api_endpoint
            )
            if delay:
                time.sleep(delay)
            try:
                response_body, final_url = read_page(request_url, timeout)
                payload: object
                if api_source.get("response_format") == "agribank_html":
                    payload = response_body.decode("utf-8", errors="replace")
                else:
                    payload = json.loads(response_body.decode("utf-8"))
                links, last_page = api_documents(payload, api_source, final_url)
                api_pages_seen += 1
                for link in links:
                    document_category = classify_report(link.text) or category
                    found.setdefault(
                        canonical_url(link.href),
                        (link.href, document_category, link.text),
                    )
                if (
                    page_number is not None
                    and api_source.get("response_format") == "agribank_html"
                    and not links
                ):
                    break
                if page_number is not None and last_page and page_number >= last_page:
                    break
            except (HTTPError, URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as error:
                errors.append({"bank": bank_name, "url": request_url, "error": str(error)})
                print(f"[{bank_name}] ERROR API {request_url}: {error}", file=sys.stderr)
                break

    records: list[dict[str, str]] = page_records
    for index, (url, category, label) in enumerate(found.values(), start=1):
        name = document_filename(url) or f"report_{index}.pdf"
        year = extract_year(label) or extract_year(name)
        file_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
        output_name = safe_filename(
            f"{slug}_{category}_{year + '_' if year else ''}{file_hash}_{name}"
        )
        category_dir = bank_dir / category
        category_dir.mkdir(parents=True, exist_ok=True)
        destination = category_dir / output_name
        existing_digest = existing_document_digest(destination)
        if existing_digest is None:
            previous_files = list(category_dir.glob(f"{slug}_{category}_*_{file_hash}_*"))
            previous_files.extend(bank_dir.glob(f"{slug}_{category}_*_{file_hash}_*"))
            for previous_file in previous_files:
                if previous_file != destination and f"_{file_hash}_" in previous_file.name:
                    existing_digest = existing_document_digest(previous_file)
                    if existing_digest is not None:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        previous_file.replace(destination)
                        break
        if existing_digest is not None:
            downloaded_at = datetime.fromtimestamp(
                destination.stat().st_mtime,
                timezone.utc,
            ).isoformat()
            records.append(
                {
                    "bank": bank_name,
                    "category": category,
                    "year": year,
                    "title": label or name,
                    "filename": str(destination.relative_to(output_dir)),
                    "source_url": url,
                    "downloaded_at": downloaded_at,
                    "sha256": existing_digest,
                }
            )
            print(f"[{bank_name}] Already present {destination}")
            continue
        if delay:
            time.sleep(delay)
        try:
            digest, _ = save_document(url, destination, timeout)
            records.append(
                {
                    "bank": bank_name,
                    "category": category,
                    "year": year,
                    "title": label or name,
                    "filename": str(destination.relative_to(output_dir)),
                    "source_url": url,
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                    "sha256": digest,
                }
            )
            print(f"[{bank_name}] Downloaded {destination}")
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
            errors.append({"bank": bank_name, "url": url, "error": str(error)})
            print(f"[{bank_name}] ERROR download {url}: {error}", file=sys.stderr)
    return records, errors, pages_seen + api_pages_seen


def write_csv(path: Path, fields: Iterable[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download official Vietnamese bank financial and sustainability reports."
    )
    parser.add_argument("--sources", type=Path, default=Path("report_sources.json"))
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument("--max-pages", type=int, default=250, help="Maximum pages to crawl per bank")
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between page requests")
    parser.add_argument("--timeout", type=int, default=25)
    args = parser.parse_args()
    if args.max_pages < 1 or args.max_depth < 0 or args.delay < 0 or args.timeout < 1:
        parser.error("max-pages and timeout must be positive; max-depth and delay cannot be negative")

    try:
        banks = read_sources(args.sources)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Cannot read source configuration: {error}", file=sys.stderr)
        return 2

    args.output.mkdir(parents=True, exist_ok=True)
    all_records: list[dict[str, str]] = []
    all_errors: list[dict[str, str]] = []
    total_pages = 0
    for bank in banks:
        records, errors, pages_seen = crawl_bank(
            bank,
            args.output,
            args.max_pages,
            args.max_depth,
            args.delay,
            args.timeout,
        )
        all_records.extend(records)
        all_errors.extend(errors)
        total_pages += pages_seen

    write_csv(args.output / "manifest.csv", MANIFEST_FIELDS, all_records)
    write_csv(args.output / "errors.csv", ERROR_FIELDS, all_errors)
    print(
        f"Finished: {len(all_records)} documents from {total_pages} pages; "
        f"{len(all_errors)} errors. See {args.output / 'manifest.csv'} and "
        f"{args.output / 'errors.csv'}."
    )
    if not all_records:
        print(
            "No matching documents were discovered. Check the seed URLs or add the "
            "banks' current report archive URLs to the source configuration.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
