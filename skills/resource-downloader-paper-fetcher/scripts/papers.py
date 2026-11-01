#!/usr/bin/env python3
"""Search papers and save PDF files using only the Python standard library.

This is an original implementation for the resource-downloader skill suite.
It does not read or write the legacy paper-fetcher index or results files.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Optional


ARXIV_API = "https://export.arxiv.org/api/query"
SEMANTIC_API = "https://api.semanticscholar.org/graph/v1/paper/search"
OPENALEX_API = "https://api.openalex.org/works"
CROSSREF_API = "https://api.crossref.org/works"
UNPAYWALL_API = "https://api.unpaywall.org/v2"
PUBLIC_DNS_API = "https://dns.google/resolve"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
INDEX_NAME = "resource-downloader-index.json"
RESULTS_JSON = "resource-downloader-results.json"
RESULTS_MD = "resource-downloader-results.md"
LOCK_NAME = ".resource-downloader.lock"
PENDING_NAME = ".resource-downloader.pending.json"
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_SCIHUB_HTML_BYTES = 2 * 1024 * 1024
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PDF_SECONDS = 180
MAX_SCIHUB_HTML_SECONDS = 90
SYNTHETIC_PROXY_NET = ipaddress.ip_network("198.18.0.0/15")
DEFAULT_SCIHUB_BASES = (
    "https://sci-hub.se",
    "https://sci-hub.st",
    "https://sci-hub.ru",
)
DEFAULT_SCIHUB_HOSTS = {urllib.parse.urlsplit(base).hostname for base in DEFAULT_SCIHUB_BASES}
USER_AGENT = "resource-downloader-paper-fetcher/2026-11-01.2"
SKILL_ROOT = Path(__file__).resolve().parents[1]
PDF_TAIL_BYTES = 4096


class PaperError(Exception):
    """An expected input, network, or storage failure."""


class IdentityError(PaperError):
    """Metadata identifies a different work from the requested paper."""


def compact(value: object) -> str:
    return " ".join(str(value or "").split())


def topic_slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:63].strip("-")
    if result:
        return result
    return "topic-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def require_slug(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise PaperError("Topic must be a lowercase ASCII slug (letters, digits, hyphens).")
    return value


def output_root(raw: Path) -> Path:
    try:
        root = raw.expanduser().resolve()
        for ancestor in (root, *root.parents):
            if ancestor.exists() and not ancestor.is_dir():
                raise PaperError(f"Output path has a non-directory component: {ancestor}")
    except OSError as exc:
        raise PaperError(f"Cannot resolve output directory: {exc}") from exc
    if root == SKILL_ROOT or SKILL_ROOT in root.parents:
        raise PaperError("Choose an --out directory outside the installed skill.")
    return root


def _checked_output_path(root: Path, candidate: Path) -> Path:
    candidate = candidate.resolve()
    if candidate == root or root not in candidate.parents:
        raise PaperError(f"Output path escapes --out: {candidate}")
    if candidate == SKILL_ROOT or SKILL_ROOT in candidate.parents:
        raise PaperError("Output path reaches the installed skill.")
    return candidate


def safe_path(root: Path, *parts: str) -> Path:
    """Resolve existing symlinks/junctions and keep every output under --out."""
    return _checked_output_path(root, root.joinpath(*parts))


def topic_dir(root: Path, slug: str) -> Path:
    return safe_path(root, require_slug(slug))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PaperError(f"Cannot read {path}: {exc}") from exc


def atomic_text(path: Path, content: str, root: Optional[Path] = None) -> None:
    if root is not None:
        path = _checked_output_path(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if root is not None:
        path = _checked_output_path(root, path)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", suffix=".tmp",
            prefix=".resource-downloader-", dir=path.parent, delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(content)
        if root is not None:
            path = _checked_output_path(root, path)
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_json(path: Path, value: object, root: Optional[Path] = None) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n", root)


@contextmanager
def output_lock(root: Path):
    """Serialize all read/modify/write operations for one output root."""
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PaperError(f"Cannot create output directory: {exc}") from exc
    path = safe_path(root, LOCK_NAME)
    try:
        handle = os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+b")
    except OSError as exc:
        raise PaperError(f"Cannot open output lock: {exc}") from exc
    with handle:
        deadline = time.monotonic() + 300
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise PaperError(f"Timed out waiting for output lock: {exc}") from exc
                time.sleep(0.1)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _arxiv_rate_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    cache = Path(base).expanduser() if base else Path.home() / ".cache"
    directory = (cache / "resource-downloader").resolve()
    if directory == SKILL_ROOT or SKILL_ROOT in directory.parents:
        raise PaperError("arXiv rate state must stay outside the installed skill.")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "arxiv-rate.lock"


@contextmanager
def arxiv_rate_limit():
    """Allow one local arXiv API connection at a time, with >=3 s between starts."""
    path = _arxiv_rate_path()
    try:
        handle = os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+b")
    except OSError as exc:
        raise PaperError(f"Cannot open arXiv rate state: {exc}") from exc
    with handle:
        acquired = False
        started = time.monotonic()
        while not acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                if time.monotonic() - started >= 30:
                    raise PaperError(f"Timed out waiting for local arXiv rate lock: {exc}") from exc
                time.sleep(0.1)
        try:
            handle.seek(0)
            try:
                last_start = float(handle.read().decode("ascii").strip())
            except (ValueError, UnicodeError):
                last_start = 0.0
            elapsed = max(0.0, time.time() - last_start)
            delay = max(0.0, 3.0 - elapsed)
            if delay:
                time.sleep(delay)
            handle.seek(0)
            handle.truncate()
            handle.write(f"{time.time():.6f}\n".encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class _NoMetadataRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PaperError("Metadata API redirect refused; credentials were not forwarded.")


def _metadata_host(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in {
            "export.arxiv.org", "api.semanticscholar.org", "api.openalex.org",
            "api.crossref.org", "api.unpaywall.org",
        }
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
    ):
        raise PaperError("Metadata request must target the official HTTPS API host.")
    return parts.hostname


def _request_bytes(url: str, headers: dict[str, str], max_bytes: int) -> bytes:
    host = _metadata_host(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.build_opener(_NoMetadataRedirect()).open(request, timeout=25) as response:
            if _metadata_host(response.geturl()) != host:
                raise PaperError("Metadata response left its official API host.")
            body = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        retry = exc.headers.get("Retry-After") if exc.headers else None
        suffix = f"; retry after {retry}" if retry else ""
        raise PaperError(f"HTTP {exc.code} from {urllib.parse.urlsplit(url).hostname}{suffix}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PaperError(f"Network request failed: {exc}") from exc
    if len(body) > max_bytes:
        raise PaperError("Metadata response exceeded the size limit.")
    return body


def _https_arxiv_url(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise PaperError("Invalid arXiv URL in metadata.") from exc
    if parts.hostname in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
        return urllib.parse.urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))
    return url


def parse_arxiv(atom_bytes: bytes) -> list[dict[str, object]]:
    try:
        feed = ET.fromstring(atom_bytes)
    except ET.ParseError as exc:
        raise PaperError(f"Invalid arXiv Atom response: {exc}") from exc
    if feed.tag != ATOM + "feed":
        raise PaperError("arXiv response is not an Atom feed.")
    records: list[dict[str, object]] = []
    for entry in feed.findall(ATOM + "entry"):
        title = compact(entry.findtext(ATOM + "title"))
        try:
            source_url = _https_arxiv_url(compact(entry.findtext(ATOM + "id")))
        except PaperError:
            continue
        if not title or "/api/errors#" in source_url:
            continue
        identifier = source_url.split("/abs/", 1)[-1].rstrip("/")
        authors = [
            compact(author.findtext(ATOM + "name"))
            for author in entry.findall(ATOM + "author")
        ]
        doi = compact(entry.findtext(ARXIV + "doi"))
        pdf_url = ""
        for link in entry.findall(ATOM + "link"):
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                try:
                    pdf_url = _https_arxiv_url(compact(link.attrib.get("href")))
                except PaperError:
                    continue
            elif link.attrib.get("title") == "doi" and not doi:
                try:
                    doi = urllib.parse.urlsplit(compact(link.attrib.get("href"))).path.lstrip("/")
                except ValueError:
                    continue
        published = compact(entry.findtext(ATOM + "published"))
        records.append({
            "id": "", "title": title, "authors": [a for a in authors if a],
            "year": published[:4] if published[:4].isdigit() else "",
            "doi": doi, "arxiv_id": identifier, "semantic_id": "",
            "openalex_id": "",
            "abstract": compact(entry.findtext(ATOM + "summary")),
            "source_url": source_url, "pdf_url": pdf_url,
            "pdf_candidates": ([{"provider": "arxiv", "url": pdf_url, "license": "",
                                 "version": re.search(r"v\d+$", identifier).group(0)
                                 if re.search(r"v\d+$", identifier) else ""}]
                               if pdf_url else []),
            "sources": ["arxiv"], "downloaded_file": "",
            "download_source": "", "download_provider": "", "downloaded_url": "",
            "download_attempts": [],
        })
    return records


def parse_semantic(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise PaperError("Invalid Semantic Scholar search response.")
    records: list[dict[str, object]] = []
    for item in payload["data"]:
        if not isinstance(item, dict):
            continue
        title = compact(item.get("title"))
        if not title:
            continue
        external = item.get("externalIds") or {}
        if not isinstance(external, dict):
            external = {}
        oa = item.get("openAccessPdf") or {}
        if not isinstance(oa, dict):
            oa = {}
        authors = item.get("authors") or []
        if not isinstance(authors, list):
            authors = []
        year = item.get("year")
        records.append({
            "id": "", "title": title,
            "authors": [compact(a.get("name")) for a in authors if isinstance(a, dict) and compact(a.get("name"))],
            "year": str(year) if isinstance(year, int) and 1000 <= year <= 9999 else "",
            "doi": compact(external.get("DOI")), "arxiv_id": compact(external.get("ArXiv")),
            "semantic_id": compact(item.get("paperId")), "openalex_id": "",
            "abstract": compact(item.get("abstract")),
            "source_url": compact(item.get("url")), "pdf_url": compact(oa.get("url")),
            "pdf_candidates": ([{"provider": "semantic-scholar", "url": compact(oa.get("url")),
                                 "license": "", "version": ""}] if compact(oa.get("url")) else []),
            "sources": ["semantic-scholar"], "downloaded_file": "",
            "download_source": "", "download_provider": "", "downloaded_url": "",
            "download_attempts": [],
        })
    return records


def clean_doi(raw: object) -> str:
    doi = compact(raw)
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", "", doi, flags=re.IGNORECASE)
    if not re.fullmatch(r"10\.\d{4,9}/[^\s]+", doi, flags=re.IGNORECASE):
        raise PaperError("Expected a complete DOI such as 10.1234/example.")
    return doi


def maybe_doi(raw: object) -> str:
    try:
        return clean_doi(raw)
    except PaperError:
        return ""


def _oa_locations(payload: dict[str, object], key: str) -> list[dict[str, str]]:
    locations = [payload.get("best_oa_location")]
    other = payload.get(key)
    if isinstance(other, list):
        locations.extend(other)
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for location in locations:
        if not isinstance(location, dict) or location.get("is_oa") is not True:
            continue
        url = compact(location.get("pdf_url") if key == "locations" else location.get("url_for_pdf"))
        if not url or url in seen:
            continue
        seen.add(url)
        candidates.append({
            "provider": "openalex" if key == "locations" else "unpaywall",
            "url": url, "license": compact(location.get("license")),
            "version": compact(location.get("version")),
        })
    return candidates


def parse_openalex_work(work: object) -> dict[str, object]:
    if not isinstance(work, dict) or not compact(work.get("title")):
        raise PaperError("Invalid OpenAlex work response.")
    authorships = work.get("authorships")
    if not isinstance(authorships, list):
        authorships = []
    authors = [compact(item.get("author", {}).get("display_name")) for item in authorships
               if isinstance(item, dict) and isinstance(item.get("author"), dict)]
    year = work.get("publication_year")
    candidates = _oa_locations(work, "locations")
    openalex_id = compact(work.get("id"))
    return {
        "id": "", "title": compact(work.get("title")), "authors": [a for a in authors if a],
        "year": str(year) if isinstance(year, int) and 1000 <= year <= 9999 else "",
        "doi": maybe_doi(work.get("doi")), "arxiv_id": "", "semantic_id": "",
        "openalex_id": openalex_id, "abstract": "", "source_url": openalex_id,
        "pdf_url": candidates[0]["url"] if candidates else "", "pdf_candidates": candidates,
        "sources": ["openalex"], "downloaded_file": "", "download_source": "",
        "download_provider": "", "downloaded_url": "", "download_attempts": [],
    }


def _openalex_has_doi(work: dict[str, object], doi: str) -> bool:
    primary = maybe_doi(work.get("doi"))
    if primary:
        return primary.casefold() == doi.casefold()
    observed = set()
    locations = work.get("locations")
    if locations is not None and not isinstance(locations, list):
        raise PaperError("Invalid OpenAlex locations metadata.")
    for location in locations or []:
        if isinstance(location, dict):
            observed.add(maybe_doi(location.get("doi")).casefold())
    return doi.casefold() in observed


def lookup_openalex_doi(doi: str) -> dict[str, object]:
    requested = clean_doi(doi)
    url = OPENALEX_API + "/doi:" + urllib.parse.quote(requested, safe="/")
    body = _request_bytes(url, {"Accept": "application/json"}, MAX_METADATA_BYTES)
    try:
        work = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PaperError(f"Invalid OpenAlex JSON response: {exc}") from exc
    if not isinstance(work, dict) or not _openalex_has_doi(work, requested):
        raise IdentityError("OpenAlex work DOI does not match the requested DOI.")
    record = parse_openalex_work(work)
    if not maybe_doi(work.get("doi")):
        crossref = lookup_crossref_doi(requested)
        open_title, open_author, open_year = _title_author_year(record)
        cross_title, cross_author, cross_year = _title_author_year(crossref)
        if (not all((open_title, open_author, open_year, cross_title, cross_author, cross_year))
                or (open_title, open_author, open_year) != (cross_title, cross_author, cross_year)):
            raise IdentityError("OpenAlex location DOI title, first author or year does not match Crossref.")
    record["doi"] = requested
    return record


def search_openalex(query: str, limit: int) -> list[dict[str, object]]:
    doi = maybe_doi(query)
    if doi:
        return [lookup_openalex_doi(doi)]
    params = urllib.parse.urlencode({"search": query, "per_page": limit})
    body = _request_bytes(OPENALEX_API + "?" + params, {"Accept": "application/json"}, MAX_METADATA_BYTES)
    try:
        payload = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PaperError(f"Invalid OpenAlex JSON response: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise PaperError("Invalid OpenAlex search response.")
    records = []
    for work in payload["results"]:
        try:
            records.append(parse_openalex_work(work))
        except PaperError:
            continue
    return records


def parse_crossref_work(work: object) -> dict[str, object]:
    if not isinstance(work, dict) or not isinstance(work.get("title"), list) or not work["title"]:
        raise PaperError("Invalid Crossref work response.")
    title = compact(work["title"][0])
    doi = clean_doi(work.get("DOI"))
    if not title:
        raise PaperError("Crossref work has no title.")
    authors = []
    for author in work.get("author") or []:
        if isinstance(author, dict):
            name = compact(" ".join([compact(author.get("given")), compact(author.get("family"))]))
            if name:
                authors.append(name)
    published = work.get("published") or work.get("issued") or {}
    date_parts = published.get("date-parts") if isinstance(published, dict) else None
    year = ""
    if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list) and date_parts[0]:
        candidate_year = date_parts[0][0]
        year = str(candidate_year) if isinstance(candidate_year, int) and 1000 <= candidate_year <= 9999 else ""
    return {
        "id": "doi:" + doi, "title": title, "authors": authors, "year": year,
        "doi": doi, "arxiv_id": "", "semantic_id": "", "openalex_id": "",
        "abstract": "", "source_url": "https://doi.org/" + doi, "pdf_url": "",
        "pdf_candidates": [], "sources": ["crossref"], "downloaded_file": "",
        "download_source": "", "download_provider": "", "downloaded_url": "",
        "download_attempts": [],
    }


def lookup_crossref_doi(doi: str) -> dict[str, object]:
    requested = clean_doi(doi)
    url = CROSSREF_API + "/" + urllib.parse.quote(requested, safe="/")
    body = _request_bytes(url, {"Accept": "application/json"}, MAX_METADATA_BYTES)
    try:
        payload = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PaperError(f"Invalid Crossref JSON response: {exc}") from exc
    record = parse_crossref_work(payload.get("message") if isinstance(payload, dict) else None)
    if record["doi"].casefold() != requested.casefold():
        raise IdentityError("Crossref DOI does not match the requested DOI.")
    return record


def _title_key(value: object) -> str:
    return re.sub(r"[\W_]+", "", compact(value).casefold())


def crossref_doi_for_paper(paper: dict[str, object]) -> str:
    title = compact(paper.get("title"))
    if not title:
        raise PaperError("Cannot resolve DOI without a title.")
    params = urllib.parse.urlencode({
        "query.title": title, "rows": 5, "select": "DOI,title,author,published,issued",
    })
    body = _request_bytes(CROSSREF_API + "?" + params, {"Accept": "application/json"}, MAX_METADATA_BYTES)
    try:
        payload = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PaperError(f"Invalid Crossref JSON response: {exc}") from exc
    message = payload.get("message") if isinstance(payload, dict) else None
    items = message.get("items") if isinstance(message, dict) else None
    if not isinstance(items, list):
        raise PaperError("Invalid Crossref title search response.")
    wanted_title, wanted_author, wanted_year = _title_author_year(paper)
    if not wanted_author or not re.fullmatch(r"\d{4}", wanted_year):
        raise PaperError("Crossref title lookup needs a first author and year to confirm DOI identity.")
    matches: set[str] = set()
    for item in items:
        try:
            record = parse_crossref_work(item)
        except PaperError:
            continue
        found_title, found_author, found_year = _title_author_year(record)
        if ((found_title, found_author, found_year)
                != (wanted_title, wanted_author, wanted_year)):
            continue
        matches.add(str(record["doi"]))
    if len(matches) != 1:
        raise PaperError(f"Crossref title lookup found {len(matches)} unambiguous DOI matches.")
    return next(iter(matches))


def lookup_unpaywall_candidates(doi: str, email: str, expected_title: str = "") -> list[dict[str, str]]:
    requested = clean_doi(doi)
    if not email or "@" not in email or any(char.isspace() for char in email):
        raise PaperError("UNPAYWALL_EMAIL must be a valid contact email.")
    url = (UNPAYWALL_API + "/" + urllib.parse.quote(requested, safe="/") + "?"
           + urllib.parse.urlencode({"email": email}))
    body = _request_bytes(url, {"Accept": "application/json"}, MAX_METADATA_BYTES)
    try:
        payload = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PaperError(f"Invalid Unpaywall JSON response: {exc}") from exc
    if not isinstance(payload, dict) or maybe_doi(payload.get("doi")).casefold() != requested.casefold():
        raise IdentityError("Unpaywall DOI does not match the requested DOI.")
    reported_title = compact(payload.get("title"))
    if expected_title and reported_title and _title_key(reported_title) != _title_key(expected_title):
        raise IdentityError("Unpaywall DOI title does not match the saved paper title.")
    if payload.get("is_oa") is False:
        return []
    locations = [payload.get("best_oa_location")]
    if isinstance(payload.get("oa_locations"), list):
        locations.extend(payload["oa_locations"])
    candidates = []
    seen = set()
    for location in locations:
        if not isinstance(location, dict):
            continue
        pdf_url = compact(location.get("url_for_pdf"))
        if not pdf_url or pdf_url in seen:
            continue
        seen.add(pdf_url)
        candidates.append({
            "provider": "unpaywall", "url": pdf_url,
            "license": compact(location.get("license")),
            "version": compact(location.get("version")),
        })
    return candidates


def _norm_identifier(field: str, value: object) -> str:
    result = compact(value).casefold()
    if field == "doi":
        result = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", result)
    if field == "arxiv_id":
        result = re.sub(r"^arxiv:", "", result)
        result = re.sub(r"v\d+$", "", result)
    return result


def identity_tokens(paper: dict[str, object]) -> set[str]:
    tokens = set()
    for field, prefix in (("doi", "doi:"), ("arxiv_id", "arxiv:"),
                          ("semantic_id", "s2:"), ("openalex_id", "openalex:")):
        value = _norm_identifier(field, paper.get(field))
        if value:
            tokens.add(prefix + value)
    title, first_author, year = _title_author_year(paper)
    if title and first_author and re.fullmatch(r"\d{4}", year):
        tokens.add(f"title-author-year:{title}:{first_author}:{year}")
    return tokens


def _title_author_year(paper: dict[str, object]) -> tuple[str, str, str]:
    title = re.sub(r"[\W_]+", "", compact(paper.get("title")).casefold())
    authors = paper.get("authors") or []
    first_author = re.sub(r"[\W_]+", "", compact(authors[0]).casefold()) if isinstance(authors, list) and authors else ""
    return title, first_author, compact(paper.get("year"))


def same_paper(left: dict[str, object], right: dict[str, object]) -> bool:
    left_title, _, _ = _title_author_year(left)
    right_title, _, _ = _title_author_year(right)
    left_arxiv = re.sub(r"^arxiv:", "", compact(left.get("arxiv_id")).casefold())
    right_arxiv = re.sub(r"^arxiv:", "", compact(right.get("arxiv_id")).casefold())
    left_version = re.fullmatch(r"(.+)v(\d+)", left_arxiv)
    right_version = re.fullmatch(r"(.+)v(\d+)", right_arxiv)
    left_base = left_version.group(1) if left_version else left_arxiv
    right_base = right_version.group(1) if right_version else right_arxiv
    left_revision = left_version.group(2) if left_version else ""
    right_revision = right_version.group(2) if right_version else ""
    if (left_base and left_base == right_base and left_revision != right_revision):
        return False
    if (left_revision and not right_arxiv) or (right_revision and not left_arxiv):
        # A DOI-only work can refer to the journal version, not a particular
        # arXiv revision. Keep its PDF candidates on a neutral record.
        return False
    for field in ("doi", "arxiv_id", "semantic_id", "openalex_id"):
        a = _norm_identifier(field, left.get(field))
        b = _norm_identifier(field, right.get(field))
        if a and b:
            return a == b and (not left_title or not right_title or left_title == right_title)
    return bool(identity_tokens(left) & identity_tokens(right))


def stable_id(paper: dict[str, object]) -> str:
    for field, prefix in (("arxiv_id", "arxiv:"), ("doi", "doi:"),
                          ("semantic_id", "s2:"), ("openalex_id", "openalex:")):
        value = compact(paper.get(field))
        if value:
            return prefix + value
    title, first_author, year = _title_author_year(paper)
    identity = [title, first_author, year]
    if not first_author or not year:
        identity.extend([compact(paper.get("source_url")).casefold(), compact(paper.get("pdf_url")).casefold()])
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    return "title:" + digest


def merge_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    for record in records:
        target = next((item for item in merged if same_paper(record, item)), None)
        if target is None:
            merged.append(dict(record))
            continue
        for key in ("title", "year", "doi", "arxiv_id", "semantic_id", "openalex_id",
                    "abstract", "source_url", "pdf_url", "downloaded_file", "download_source",
                    "download_provider", "downloaded_url"):
            if not target.get(key) and record.get(key):
                target[key] = record[key]
        if len(record.get("authors") or []) > len(target.get("authors") or []):
            target["authors"] = record["authors"]
        target["sources"] = sorted(set(target.get("sources") or []) | set(record.get("sources") or []))
        known = {item.get("url") for item in target.get("pdf_candidates") or [] if isinstance(item, dict)}
        for candidate in record.get("pdf_candidates") or []:
            if isinstance(candidate, dict) and candidate.get("url") and candidate["url"] not in known:
                target.setdefault("pdf_candidates", []).append(candidate)
                known.add(candidate["url"])
        if not target.get("download_attempts") and record.get("download_attempts"):
            target["download_attempts"] = record["download_attempts"]
    used: set[str] = set()
    for item in merged:
        base = stable_id(item)
        proposed = compact(item.get("id")) or base
        if proposed in used:
            identity = [item.get(field) for field in ("title", "doi", "arxiv_id", "source_url", "pdf_url")]
            digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()[:10]
            proposed = base + "#" + digest
            if proposed in used:
                raise PaperError("Conflicting paper metadata produced duplicate IDs.")
        item["id"] = proposed
        used.add(proposed)
    return merged


def search_arxiv(query: str, limit: int) -> list[dict[str, object]]:
    safe_query = query.replace('"', "").strip()
    params = urllib.parse.urlencode({
        "search_query": f'all:"{safe_query}"', "start": 0, "max_results": limit,
    })
    with arxiv_rate_limit():
        body = _request_bytes(ARXIV_API + "?" + params, {"Accept": "application/atom+xml"}, MAX_METADATA_BYTES)
    return parse_arxiv(body)


def search_semantic(query: str, limit: int) -> list[dict[str, object]]:
    params = urllib.parse.urlencode({
        "query": query, "limit": limit,
        "fields": "title,authors,year,externalIds,openAccessPdf,url,abstract",
    })
    headers = {"Accept": "application/json"}
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if key:
        headers["x-api-key"] = key
    body = _request_bytes(SEMANTIC_API + "?" + params, headers, MAX_METADATA_BYTES)
    try:
        payload = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PaperError(f"Invalid Semantic Scholar JSON response: {exc}") from exc
    return parse_semantic(payload)


def _load_saved_document(root: Path, slug: str) -> dict[str, object]:
    path = safe_path(root, require_slug(slug), RESULTS_JSON)
    if not path.exists():
        raise PaperError(f"No saved topic '{slug}' at {path}.")
    data = read_json(path, None)
    if (not isinstance(data, dict)
            or data.get("format") != "resource-downloader-paper-fetcher-v1"
            or not isinstance(data.get("query"), str)
            or not isinstance(data.get("updated"), str)
            or not isinstance(data.get("papers"), list)):
        raise PaperError(f"Invalid saved results at {path}.")
    text_fields = (
        "id", "title", "year", "doi", "arxiv_id", "semantic_id", "abstract",
        "source_url", "pdf_url", "downloaded_file",
    )
    for position, paper in enumerate(data["papers"], 1):
        if (not isinstance(paper, dict)
                or any(not isinstance(paper.get(field), str) for field in text_fields)
                or not paper["id"].strip() or not paper["title"].strip()
                or not isinstance(paper.get("authors"), list)
                or any(not isinstance(author, str) for author in paper["authors"])
                or not isinstance(paper.get("sources"), list)
                or any(not isinstance(source, str) for source in paper["sources"])):
            raise PaperError(f"Invalid paper #{position} in saved results at {path}.")
        for optional in ("download_source", "download_provider", "downloaded_url", "openalex_id"):
            if optional not in paper:
                paper[optional] = ""
            elif not isinstance(paper[optional], str):
                raise PaperError(f"Invalid paper #{position} in saved results at {path}.")
        for optional in ("pdf_candidates", "download_attempts"):
            if optional not in paper:
                paper[optional] = []
            elif not isinstance(paper[optional], list):
                raise PaperError(f"Invalid paper #{position} in saved results at {path}.")
        for candidate in paper["pdf_candidates"]:
            if (not isinstance(candidate, dict)
                    or any(not isinstance(candidate.get(field), str)
                           for field in ("provider", "url", "license", "version"))):
                raise PaperError(f"Invalid PDF candidate in paper #{position} at {path}.")
        for attempt in paper["download_attempts"]:
            if (not isinstance(attempt, dict)
                    or any(not isinstance(attempt.get(field), str)
                           for field in ("provider", "status", "reason"))):
                raise PaperError(f"Invalid download attempt in paper #{position} at {path}.")
    return data


def _load_saved(root: Path, slug: str) -> list[dict[str, object]]:
    return _load_saved_document(root, slug)["papers"]


def _load_index(root: Path) -> dict[str, object]:
    path = safe_path(root, INDEX_NAME)
    index = read_json(path, {"format": "resource-downloader-paper-fetcher-v1", "topics": {}})
    if (not isinstance(index, dict)
            or index.get("format") != "resource-downloader-paper-fetcher-v1"
            or not isinstance(index.get("topics"), dict)):
        raise PaperError(f"Invalid index at {path}.")
    for slug, info in index["topics"].items():
        if not isinstance(slug, str):
            raise PaperError(f"Invalid topic key in index at {path}.")
        try:
            require_slug(slug)
        except PaperError as exc:
            raise PaperError(f"Invalid topic key '{slug}' in index at {path}.") from exc
        if (not isinstance(info, dict)
                or not isinstance(info.get("query"), str)
                or type(info.get("count")) is not int
                or info["count"] < 0
                or not isinstance(info.get("updated"), str)):
            raise PaperError(f"Invalid topic '{slug}' in index at {path}.")
    return index


def _markdown_cell(value: object) -> str:
    return compact(value).replace("\\", "\\\\").replace("|", "\\|")


def _results_markdown(query: str, papers: list[dict[str, object]]) -> str:
    lines = [
        f"# Paper results: {query}", "",
        "| ID | Title | Authors | Year | DOI | PDF | Download source |",
        "|---|---|---|---|---|---|---|",
    ]
    for paper in papers:
        pdf = "saved" if paper.get("downloaded_file") else ("available" if _open_candidates(paper) else "not listed")
        lines.append("| " + " | ".join([
            _markdown_cell(paper.get("id")),
            _markdown_cell(paper.get("title")),
            _markdown_cell(", ".join(paper.get("authors") or [])),
            _markdown_cell(paper.get("year")),
            _markdown_cell(paper.get("doi")),
            pdf,
            _markdown_cell(paper.get("download_source")),
        ]) + " |")
    source_names = {"arxiv": "arXiv API", "semantic-scholar": "Semantic Scholar Academic Graph API",
                    "openalex": "OpenAlex", "crossref": "Crossref"}
    used_sources = sorted({source_names.get(source, source)
                           for paper in papers for source in paper.get("sources") or []
                           if isinstance(source, str)})
    lines.extend(["", "Metadata sources in saved records: " + (", ".join(used_sources) or "none") + ".",
                  "PDF availability does not establish permission to redistribute.", ""])
    return "\n".join(lines)


def _persistable_url(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    if not url or parts.username or parts.password:
        return ""
    path = urllib.parse.unquote(parts.path).casefold()
    if "=" in path or ";" in path:
        return ""
    if re.search(r"(?:^|[/_-])(?:token|secret|auth|credential|signature|session|apikey|accesskey)(?:$|[/_-])", path):
        return ""
    if parts.query:
        # This publisher's DOI and rendering mode are public identifiers.
        # Other query strings may carry opaque access tokens, so do not save them.
        pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        values = dict(pairs)
        plos = (parts.hostname == "journals.plos.org" and parts.path.endswith("/article/file")
                and len(pairs) == 2 and set(values) == {"id", "type"}
                and re.fullmatch(r"10\.1371/journal\.[a-z]{4,8}\.\d+", values["id"])
                and values["type"] in {"printable", "manuscript"})
        simple_download = pairs in ([('download', '1')], [('download', 'true')], [('format', 'pdf')])
        if not (plos or simple_download):
            return ""
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _persisted_papers(papers: list[dict[str, object]]) -> list[dict[str, object]]:
    persisted = copy.deepcopy(papers)
    for paper in persisted:
        for field in ("pdf_url", "downloaded_url", "source_url"):
            paper[field] = _persistable_url(compact(paper.get(field)))
        paper["pdf_candidates"] = [
            dict(candidate, url=safe_url)
            for candidate in paper.get("pdf_candidates") or []
            if isinstance(candidate, dict)
            for safe_url in [_persistable_url(compact(candidate.get("url")))]
            if safe_url
        ]
    return persisted


def _write_topic(root: Path, slug: str, query: str, papers: list[dict[str, object]]) -> None:
    topic_dir(root, slug)
    index_path = safe_path(root, INDEX_NAME)
    json_path = safe_path(root, slug, RESULTS_JSON)
    md_path = safe_path(root, slug, RESULTS_MD)
    pending_path = safe_path(root, PENDING_NAME)
    index = _load_index(root)
    persisted = _persisted_papers(papers)
    updated = utc_now()
    document = {
        "format": "resource-downloader-paper-fetcher-v1",
        "query": query, "updated": updated, "papers": persisted,
    }
    try:
        atomic_json(pending_path, {"format": "resource-downloader-pending-v1",
                                   "slug": slug, "had_json": json_path.exists()}, root)
        atomic_json(json_path, document, root)
        atomic_text(md_path, _results_markdown(query, persisted), root)
        index["topics"][slug] = {"query": query, "count": len(persisted), "updated": updated}
        atomic_json(index_path, index, root)
        pending_path.unlink()
    except OSError as exc:
        raise PaperError(f"Could not finish writing topic '{slug}': {exc}") from exc


def _recover_pending(root: Path) -> None:
    pending_path = safe_path(root, PENDING_NAME)
    pending = read_json(pending_path, None)
    if pending is None:
        return
    if (not isinstance(pending, dict) or pending.get("format") != "resource-downloader-pending-v1"
            or not isinstance(pending.get("slug"), str)
            or type(pending.get("had_json")) is not bool):
        raise PaperError("Invalid pending topic marker; inspect it before continuing.")
    slug = require_slug(pending["slug"])
    json_path = safe_path(root, slug, RESULTS_JSON)
    if not json_path.exists():
        if pending["had_json"]:
            raise PaperError("Pending topic lost its existing results JSON; inspect before continuing.")
        pending_path.unlink()
        return
    document = _load_saved_document(root, slug)
    index = _load_index(root)
    try:
        atomic_text(safe_path(root, slug, RESULTS_MD),
                    _results_markdown(document["query"], document["papers"]), root)
        index["topics"][slug] = {"query": document["query"],
                                  "count": len(document["papers"]), "updated": document["updated"]}
        atomic_json(safe_path(root, INDEX_NAME), index, root)
        pending_path.unlink()
    except OSError as exc:
        raise PaperError(f"Could not recover pending topic '{slug}': {exc}") from exc


def command_search(args: argparse.Namespace) -> int:
    root = None if args.no_save else output_root(args.out)
    slug = None if args.no_save else (require_slug(args.topic) if args.topic else topic_slug(args.query))
    sources = [part.strip().lower() for part in args.sources.split(",") if part.strip()]
    if not sources or len(sources) != len(set(sources)) or set(sources) - {"arxiv", "semantic", "openalex"}:
        raise PaperError("--sources must contain arxiv, semantic, openalex, or a combination without duplicates.")
    found: list[dict[str, object]] = []
    failures: list[str] = []
    for source in sources:
        try:
            searcher = {"arxiv": search_arxiv, "semantic": search_semantic, "openalex": search_openalex}[source]
            found.extend(searcher(args.query, args.limit))
        except PaperError as exc:
            failures.append(f"{source}: {exc}")
    if len(failures) == len(sources):
        raise PaperError("All selected sources failed: " + "; ".join(failures))
    if args.no_save:
        papers = merge_records(found)
        print(json.dumps({
            "query": args.query,
            "papers": [{
                "id": item["id"], "title": item["title"], "authors": item["authors"],
                "year": item["year"], "doi": item["doi"],
                "source_url": _persistable_url(compact(item.get("source_url"))),
                "open_pdf_candidates": len(item.get("pdf_candidates") or []),
            } for item in papers],
            "warnings": failures,
        }, ensure_ascii=False, indent=2))
        return 0
    prior_path = safe_path(root, slug, RESULTS_JSON)
    if prior_path.exists():
        prior = _load_saved(root, slug)
        found = prior + found
    papers = merge_records(found)
    _write_topic(root, slug, args.query, papers)
    print(f"Saved {len(papers)} paper(s) to {topic_dir(root, slug)}")
    for paper in papers:
        print(f"{paper['id']}  {paper['title']}")
    for failure in failures:
        print(f"Warning: {failure}", file=sys.stderr)
    return 0


def command_list(args: argparse.Namespace) -> int:
    root = output_root(args.out)
    index = _load_index(root)
    pending = read_json(safe_path(root, PENDING_NAME), None)
    if isinstance(pending, dict) and isinstance(pending.get("slug"), str):
        slug = require_slug(pending["slug"])
        path = safe_path(root, slug, RESULTS_JSON)
        if path.exists():
            document = _load_saved_document(root, slug)
            index["topics"][slug] = {"query": document["query"],
                                      "count": len(document["papers"]), "updated": document["updated"]}
    topics = index["topics"]
    if not topics:
        print("No saved topics.")
    for slug, info in sorted(topics.items()):
        print(f"{slug}\t{info.get('count', 0)}\t{info.get('query', '')}")
    return 0


def _public_https_url(url: str) -> str:
    if any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise PaperError("PDF URL contains whitespace or control characters.")
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError as exc:
        raise PaperError("PDF URL is malformed.") from exc
    if parts.scheme != "https" or not host or parts.username or parts.password:
        raise PaperError("PDF URL must be public HTTPS without embedded credentials.")
    try:
        port = parts.port
    except ValueError as exc:
        raise PaperError("PDF URL has an invalid port.") from exc
    if port not in (None, 443):
        raise PaperError("PDF URL must use HTTPS port 443.")
    if not re.fullmatch(r"[a-z0-9.:-]+", host):
        raise PaperError("PDF URL has an unsupported host name.")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".test")):
        raise PaperError("PDF URL points to a local or internal host.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or any(not label or len(label) > 63 for label in host.split(".")):
            raise PaperError("PDF URL has an invalid DNS host name.")
        # arXiv's fixed official host can resolve to a local proxy's fake IP.
        # HTTPS certificate validation still authenticates arxiv.org; arbitrary
        # open-access hosts must pass the public-address DNS check below.
        if (host in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
                and parts.hostname.lower() == host
                and port in (None, 443)
                and parts.path.startswith("/pdf/")
                and parts.path != "/pdf/"
                and not any(segment in (".", "..") for segment in parts.path.split("/"))):
            return url
        # The fixed default Sci-Hub host may also be mapped to a proxy IP.
        # Limit this exception to a PDF file on that exact HTTPS origin.
        if (host in DEFAULT_SCIHUB_HOSTS
                and parts.hostname.lower() == host
                and port in (None, 443) and parts.path.lower().endswith(".pdf")
                and not any(segment in (".", "..") for segment in parts.path.split("/"))):
            return url
        try:
            answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except (socket.gaierror, OSError, UnicodeError, ValueError) as exc:
            raise PaperError(f"Cannot resolve PDF host {host}: {exc}") from exc
        if not answers:
            raise PaperError(f"PDF host {host} has no DNS addresses.")
        addresses = []
        for answer in answers:
            try:
                address = ipaddress.ip_address(answer[4][0].split("%", 1)[0])
            except ValueError as exc:
                raise PaperError(f"PDF host {host} returned an invalid DNS address.") from exc
            addresses.append(address)
        if all(address in SYNTHETIC_PROXY_NET for address in addresses):
            # Some local HTTPS proxies use RFC 2544 synthetic DNS addresses.
            # Check the public DNS answer independently before following the URL.
            addresses = _public_dns_answers(host)
        if any(not address.is_global for address in addresses):
            raise PaperError(f"PDF host {host} resolves to a non-public IP address.")
    else:
        if not address.is_global:
            raise PaperError("PDF URL points to a non-public IP address.")
    return url


def _public_dns_answers(host: str) -> tuple[object, ...]:
    return _public_dns_answers_cached(host, int(time.monotonic() // 30))


@lru_cache(maxsize=256)
def _public_dns_answers_cached(host: str, bucket: int) -> tuple[object, ...]:
    addresses = []
    for kind, number in (("A", 1), ("AAAA", 28)):
        url = PUBLIC_DNS_API + "?" + urllib.parse.urlencode({"name": host, "type": kind})
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.build_opener(_NoMetadataRedirect()).open(request, timeout=10) as response:
                if response.geturl() != url:
                    raise PaperError("Public DNS response redirected.")
                body = response.read(64 * 1024 + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PaperError(f"Cannot verify public DNS for {host}: {exc}") from exc
        if len(body) > 64 * 1024:
            raise PaperError(f"Public DNS response for {host} is too large.")
        try:
            payload = json.loads(body)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PaperError(f"Invalid public DNS response for {host}.") from exc
        if not isinstance(payload, dict) or payload.get("Status") != 0:
            raise PaperError(f"Public DNS cannot verify {host}.")
        for answer in payload.get("Answer") or []:
            if not isinstance(answer, dict) or answer.get("type") != number:
                continue
            try:
                addresses.append(ipaddress.ip_address(answer["data"]))
            except (KeyError, ValueError) as exc:
                raise PaperError(f"Public DNS returned an invalid address for {host}.") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise PaperError(f"Public DNS did not verify public addresses for {host}.")
    return tuple(addresses)


def _numeric_endpoint(address) -> tuple[int, tuple]:
    if address.version == 4:
        return socket.AF_INET, (str(address), 443)
    return socket.AF_INET6, (str(address), 443, 0, 0)


def _pin_pdf_url(url: str) -> tuple[tuple[int, tuple], ...]:
    _public_https_url(url)
    host = urllib.parse.urlsplit(url).hostname
    if host is None:
        raise PaperError("PDF URL has no host.")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return (_numeric_endpoint(literal),)
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError, UnicodeError, ValueError) as exc:
        raise PaperError(f"Cannot resolve PDF host {host}: {exc}") from exc
    if not answers:
        raise PaperError(f"PDF host {host} has no DNS addresses.")
    parsed = []
    for family, socktype, _proto, _canonical, sockaddr in answers:
        if family not in (socket.AF_INET, socket.AF_INET6) or socktype != socket.SOCK_STREAM:
            raise PaperError(f"PDF host {host} returned an unsupported DNS address.")
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except ValueError as exc:
            raise PaperError(f"PDF host {host} returned an invalid DNS address.") from exc
        if (family == socket.AF_INET and address.version != 4
                or family == socket.AF_INET6 and address.version != 6):
            raise PaperError(f"PDF host {host} returned a mismatched DNS address.")
        parsed.append(address)
    if all(address in SYNTHETIC_PROXY_NET for address in parsed):
        # Local proxy DNS uses synthetic addresses. Resolve through public DNS
        # and connect directly to the returned numeric public endpoints.
        parsed = list(_public_dns_answers(host))
    if any(not address.is_global for address in parsed):
        raise PaperError(f"PDF host {host} resolves to a non-public IP address.")
    return tuple(dict.fromkeys(_numeric_endpoint(address) for address in parsed))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, *, endpoints: tuple[tuple[int, tuple], ...], **kwargs):
        super().__init__(host, **kwargs)
        self._endpoints = endpoints

    def connect(self) -> None:
        if self._tunnel_host:
            raise OSError("PDF HTTPS proxy tunneling is disabled.")
        last_error = None
        for family, sockaddr in self._endpoints:
            raw = socket.socket(family, socket.SOCK_STREAM)
            try:
                if isinstance(self.timeout, (int, float)):
                    raw.settimeout(self.timeout)
                if self.source_address:
                    raw.bind(self.source_address)
                raw.connect(sockaddr)
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError as exc:
                last_error = exc
                raw.close()
        raise last_error or OSError("No public PDF endpoint was reachable.")


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        endpoints = _pin_pdf_url(req.full_url)

        def factory(host, **kwargs):
            return _PinnedHTTPSConnection(host, endpoints=endpoints, **kwargs)

        return self.do_open(factory, req, context=self._context)


def _scihub_base(raw: str) -> str:
    try:
        parts = urllib.parse.urlsplit(raw)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as exc:
        raise PaperError("Sci-Hub base URL is malformed.") from exc
    if (parts.scheme != "https" or not host or parts.username or parts.password
            or port not in (None, 443) or parts.query or parts.fragment
            or any(segment in (".", "..") for segment in parts.path.split("/"))):
        raise PaperError("Sci-Hub base must be an HTTPS URL on port 443 without credentials or query.")
    if host not in DEFAULT_SCIHUB_HOSTS:
        _public_https_url(raw)
    return raw.rstrip("/") + "/"


def _same_scihub_origin(url: str, host: str) -> None:
    try:
        parts = urllib.parse.urlsplit(url)
        valid = (parts.scheme == "https" and parts.hostname == host
                 and parts.port in (None, 443) and not parts.username and not parts.password)
    except ValueError:
        valid = False
    if not valid:
        raise PaperError("Sci-Hub redirected to a different or unsafe origin.")


class _SciHubRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, host: str):
        self.host = host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute = urllib.parse.urljoin(req.full_url, newurl)
        _same_scihub_origin(absolute, self.host)
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def _fetch_scihub_html(base: str, reference: str) -> tuple[str, str]:
    host = urllib.parse.urlsplit(base).hostname
    url = base + urllib.parse.quote(reference, safe="/:")
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}, method="GET",
    )
    context = ssl.create_default_context()
    context.set_alpn_protocols(["http/1.1"])
    deadline = time.monotonic() + MAX_SCIHUB_HTML_SECONDS
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _SciHubRedirect(host), _PinnedHTTPSHandler(context=context),
        )
        with opener.open(request, timeout=45) as response:
            final_url = response.geturl()
            _same_scihub_origin(final_url, host)
            chunks = []
            total = 0
            while True:
                block = response.read(64 * 1024)
                if not block:
                    break
                total += len(block)
                if total > MAX_SCIHUB_HTML_BYTES:
                    raise PaperError("Sci-Hub HTML exceeded the size limit.")
                if time.monotonic() > deadline:
                    raise PaperError("Sci-Hub HTML exceeded the time limit.")
                chunks.append(block)
            body = b"".join(chunks)
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise PaperError("Sci-Hub returned HTTP 403; access is blocked.") from exc
        if exc.code == 429:
            raise PaperError("Sci-Hub returned HTTP 429; try again later.") from exc
        raise PaperError(f"Sci-Hub returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException, ValueError) as exc:
        raise PaperError(f"Sci-Hub request failed: {exc}") from exc
    return body.decode("utf-8", errors="replace"), final_url


class _SciHubPdfParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.candidates: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attributes = dict(attrs)
        candidate = ""
        if tag in ("iframe", "embed"):
            candidate = attributes.get("src") or ""
        elif tag == "object":
            candidate = attributes.get("data") or ""
        elif tag == "a":
            href = attributes.get("href") or ""
            try:
                path = urllib.parse.urlsplit(href).path.lower()
            except ValueError:
                path = ""
            if path.endswith(".pdf") or attributes.get("type") == "application/pdf":
                candidate = href
        if candidate and tag != "a":
            try:
                path = urllib.parse.urlsplit(candidate).path.lower()
            except ValueError:
                path = ""
            if not (path.endswith(".pdf") or attributes.get("type") == "application/pdf"
                    or "pdf" in (attributes.get("id") or "").lower()):
                candidate = ""
        if candidate:
            self.candidates.append(candidate.strip())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)


def _scihub_pdf_candidates_from_html(html: str, page_url: str) -> list[str]:
    parser = _SciHubPdfParser()
    parser.feed(html)
    rejected: list[str] = []
    urls: list[str] = []
    seen: set[str] = set()
    def pdf_first(candidate: str) -> bool:
        try:
            return not urllib.parse.urlsplit(candidate).path.lower().endswith(".pdf")
        except ValueError:
            return True

    for candidate in sorted(parser.candidates[:256], key=pdf_first):
        try:
            url = urllib.parse.urljoin(page_url, candidate)
            _public_https_url(url)
            if url not in seen:
                urls.append(url)
                seen.add(url)
            if len(urls) >= 16:
                break
        except (PaperError, ValueError) as exc:
            rejected.append(str(exc))
    if urls:
        return urls
    lowered = html.casefold()
    if any(marker in lowered for marker in ("altcha", "captcha", "cf-challenge", "verify you are human")):
        raise PaperError("Mirror returned a robot-check page (CAPTCHA/ALTCHA), not a PDF; no account login is required by this skill.")
    if rejected:
        raise PaperError(f"Sci-Hub PDF link was rejected: {rejected[0]}")
    raise PaperError("Sci-Hub page contained no PDF link.")


def _scihub_pdf_from_html(html: str, page_url: str) -> str:
    return _scihub_pdf_candidates_from_html(html, page_url)[0]


def _scihub_pdf_urls(paper: dict[str, object], base: str) -> list[str]:
    doi = compact(paper.get("doi"))
    reference = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE) if doi else compact(paper.get("source_url"))
    if not reference:
        raise PaperError("Saved paper has no DOI or paper link for Sci-Hub lookup.")
    if not doi:
        try:
            parts = urllib.parse.urlsplit(reference)
            host = parts.hostname or ""
            port = parts.port
        except ValueError as exc:
            raise PaperError("Saved paper link is malformed.") from exc
        if (parts.scheme != "https" or parts.username or parts.password or port not in (None, 443)
                or host.lower() not in {
                    "arxiv.org", "www.arxiv.org", "export.arxiv.org",
                    "semanticscholar.org", "www.semanticscholar.org",
                }):
            raise PaperError("Saved paper link must use an official arXiv or Semantic Scholar HTTPS host.")
    html, page_url = _fetch_scihub_html(base, reference)
    return _scihub_pdf_candidates_from_html(html, page_url)


def _scihub_pdf_url(paper: dict[str, object], base: str) -> str:
    return _scihub_pdf_urls(paper, base)[0]


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute = urllib.parse.urljoin(req.full_url, newurl)
        _public_https_url(absolute)
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def _open_pdf(url: str):
    _public_https_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/pdf"})
    context = ssl.create_default_context()
    context.set_alpn_protocols(["http/1.1"])
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _SafeRedirect(), _PinnedHTTPSHandler(context=context),
    )
    return opener.open(request, timeout=45)


def _pdf_filename(paper: dict[str, object]) -> str:
    title = topic_slug(compact(paper.get("title")))[:70].rstrip("-") or "paper"
    digest = hashlib.sha256(compact(paper.get("id")).encode("utf-8")).hexdigest()[:10]
    return f"{title}-{digest}.pdf"


_PDF_XREF_HEADER = re.compile(rb"(\d+)[ \t]+(\d+)[ \t]*(?:\r\n|\r|\n)")
_PDF_XREF_ROW = re.compile(rb"(\d{10})[ \t]+(\d{5})[ \t]+([nf])[ \t]*(?:\r\n|\r|\n)")
_PDF_OBJECT_HEADER = re.compile(rb"(\d+)[ \t\r\n]+(\d+)[ \t\r\n]+obj\b")
_PDF_STREAM_MARKER = re.compile(rb"stream(?:\r\n|\r|\n)")


def _pdf_comment_end(data: bytes, pos: int, limit: int) -> int:
    endings = [point for point in (data.find(b"\r", pos, limit),
                                   data.find(b"\n", pos, limit)) if point >= 0]
    return min(endings) + 1 if endings else limit


def _pdf_space(data: bytes, pos: int) -> int:
    while pos < len(data):
        if data[pos] in b" \t\r\n\x00\x0c":
            pos += 1
        elif data[pos] == 37:  # PDF comment
            pos = _pdf_comment_end(data, pos, len(data))
        else:
            break
    return pos


def _pdf_dictionary(data: bytes, pos: int) -> Optional[tuple[bytes, int]]:
    pos = _pdf_space(data, pos)
    if not data.startswith(b"<<", pos):
        return None
    start = pos
    limit = min(len(data), pos + 1024 * 1024)
    depth = 0
    while pos < limit:
        char = data[pos]
        if char == 37:
            pos = _pdf_comment_end(data, pos, limit)
        elif char == 40:  # A literal string may contain dictionary delimiters.
            level = 1
            pos += 1
            while pos < limit and level:
                if data[pos] == 92:
                    pos += 2
                elif data[pos] == 40:
                    level += 1
                    pos += 1
                elif data[pos] == 41:
                    level -= 1
                    pos += 1
                else:
                    pos += 1
        elif data.startswith(b"<<", pos):
            depth += 1
            pos += 2
        elif data.startswith(b">>", pos):
            depth -= 1
            pos += 2
            if depth == 0:
                return data[start:pos], pos
        elif char == 60:  # Hex string.
            end = data.find(b">", pos + 1, limit)
            pos = limit if end < 0 else end + 1
        else:
            pos += 1
    return None


_PDF_DELIMITERS = b" \t\r\n\x00\x0c()<>[]{}/%"


def _pdf_name(token: bytes) -> bytes:
    return re.sub(rb"#([0-9A-Fa-f]{2})",
                  lambda match: bytes((int(match.group(1), 16),)), token[1:])


def _pdf_atom(data: bytes, pos: int) -> Optional[tuple[bytes, int]]:
    start = pos
    while pos < len(data) and data[pos] not in _PDF_DELIMITERS:
        pos += 1
    return (data[start:pos], pos) if pos > start else None


def _pdf_value(data: bytes, pos: int, depth: int = 0) -> Optional[tuple[str, bytes, int]]:
    pos = _pdf_space(data, pos)
    if pos >= len(data) or depth > 32:
        return None
    start = pos
    if data.startswith(b"<<", pos):
        found = _pdf_dictionary(data, pos)
        return ("dictionary", found[0], found[1]) if found else None
    if data[pos] == 91:  # Array.
        pos += 1
        while True:
            pos = _pdf_space(data, pos)
            if pos >= len(data):
                return None
            if data[pos] == 93:
                return "array", data[start:pos + 1], pos + 1
            item = _pdf_value(data, pos, depth + 1)
            if item is None:
                return None
            pos = item[2]
    if data[pos] == 40:  # Literal string.
        nesting = 1
        pos += 1
        while pos < len(data) and nesting:
            if data[pos] == 92:
                pos += 2
            elif data[pos] == 40:
                nesting += 1
                pos += 1
            elif data[pos] == 41:
                nesting -= 1
                pos += 1
            else:
                pos += 1
        return ("string", data[start:pos], pos) if nesting == 0 else None
    if data[pos] == 60:  # Hex string.
        end = data.find(b">", pos + 1)
        return ("string", data[start:end + 1], end + 1) if end >= 0 else None
    if data[pos] == 47:  # Name.
        pos += 1
        while pos < len(data) and data[pos] not in _PDF_DELIMITERS:
            pos += 1
        return "name", data[start:pos], pos
    first = _pdf_atom(data, pos)
    if first is None:
        return None
    token, pos = first
    if re.fullmatch(rb"[+-]?\d+", token):
        second = _pdf_atom(data, _pdf_space(data, pos))
        if second and re.fullmatch(rb"\d+", second[0]):
            third = _pdf_atom(data, _pdf_space(data, second[1]))
            if third and third[0] == b"R":
                return "reference", token + b" " + second[0] + b" R", third[1]
        return "integer", token, pos
    return "atom", token, pos


def _pdf_field(dictionary: bytes, name: bytes) -> Optional[tuple[str, bytes, int]]:
    if not dictionary.startswith(b"<<"):
        return None
    pos = 2
    result = None
    while True:
        pos = _pdf_space(dictionary, pos)
        if dictionary.startswith(b">>", pos):
            return result
        key = _pdf_value(dictionary, pos)
        if key is None or key[0] != "name":
            return None
        value = _pdf_value(dictionary, key[2])
        if value is None:
            return None
        if _pdf_name(key[1]) == name:
            result = value
        pos = value[2]


def _pdf_number(dictionary: bytes, name: bytes) -> Optional[int]:
    value = _pdf_field(dictionary, name)
    return int(value[1]) if value and value[0] == "integer" else None


def _pdf_reference(dictionary: bytes, name: bytes) -> Optional[tuple[int, int]]:
    value = _pdf_field(dictionary, name)
    if value is None or value[0] != "reference":
        return None
    first, second, _ = value[1].split()
    return int(first), int(second)


def _pdf_name_is(dictionary: bytes, name: bytes, expected: bytes) -> bool:
    value = _pdf_field(dictionary, name)
    return bool(value and value[0] == "name" and _pdf_name(value[1]) == expected)


def _pdf_array_values(value: tuple[str, bytes, int]) -> Optional[list[tuple[str, bytes, int]]]:
    if value[0] != "array":
        return None
    data = value[1]
    pos = 1
    items = []
    while True:
        pos = _pdf_space(data, pos)
        if pos >= len(data):
            return None
        if data[pos] == 93:
            return items
        item = _pdf_value(data, pos)
        if item is None:
            return None
        items.append(item)
        pos = item[2]


def _pdf_array_numbers(value: tuple[str, bytes, int]) -> Optional[list[int]]:
    items = _pdf_array_values(value)
    if items is None or any(item[0] != "integer" for item in items):
        return None
    numbers = [int(item[1]) for item in items]
    return numbers if all(number >= 0 for number in numbers) else None


def _pdf_stream_data(data: bytes, dictionary: bytes, pos: int) -> Optional[bytes]:
    pos = _pdf_space(data, pos)
    marker = _PDF_STREAM_MARKER.match(data, pos)
    if not marker:
        return None
    start = marker.end()
    length = _pdf_number(dictionary, b"Length")
    if length is not None:
        end = start + length
        if end > len(data) or not re.match(rb"[ \t\r\n]*endstream\b", data[end:end + 32]):
            return None
    else:  # Indirect lengths are legal; use the bounded end marker as a fallback.
        candidates = [point for point in (data.find(b"\nendstream", start),
                                          data.find(b"\rendstream", start)) if point >= 0]
        if not candidates:
            return None
        end = min(candidates)
    return data[start:end]


def _pdf_decoded_stream(data: bytes, dictionary: bytes, pos: int) -> Optional[bytes]:
    payload = _pdf_stream_data(data, dictionary, pos)
    if payload is None:
        return None
    field = _pdf_field(dictionary, b"Filter")
    if not field:
        return payload
    filters = [field] if field[0] == "name" else _pdf_array_values(field)
    if filters is None or len(filters) != 1 or filters[0][0] != "name" or not (
        _pdf_name(filters[0][1]) == b"FlateDecode"
    ):
        return None
    decoder = zlib.decompressobj()
    result = decoder.decompress(payload, MAX_PDF_BYTES + 1)
    if not decoder.eof or len(result) > MAX_PDF_BYTES:
        return None
    parameters = _pdf_field(dictionary, b"DecodeParms")
    if not parameters:
        return result
    if parameters[0] == "array":
        items = _pdf_array_values(parameters)
        parameters = items[0] if items and len(items) == 1 else None
    if parameters is None or parameters[0] != "dictionary":
        return None
    options = parameters[1]
    predictor = _pdf_number(options, b"Predictor") or 1
    if predictor == 1:
        return result
    columns = _pdf_number(options, b"Columns") or 1
    colors = _pdf_number(options, b"Colors") or 1
    bits = _pdf_number(options, b"BitsPerComponent") or 8
    if not 10 <= predictor <= 15 or bits not in (1, 2, 4, 8, 16):
        return None
    stride = (columns * colors * bits + 7) // 8
    pixel = max(1, (colors * bits + 7) // 8)
    if not 0 < stride <= MAX_PDF_BYTES or len(result) % (stride + 1):
        return None
    output = bytearray()
    previous = bytearray(stride)
    for start in range(0, len(result), stride + 1):
        method = result[start]
        row = bytearray(result[start + 1:start + stride + 1])
        if method > 4:
            return None
        for index in range(stride):
            left = row[index - pixel] if index >= pixel else 0
            up = previous[index]
            if method == 1:
                value = left
            elif method == 2:
                value = up
            elif method == 3:
                value = (left + up) // 2
            elif method == 4:
                upper_left = previous[index - pixel] if index >= pixel else 0
                estimate = left + up - upper_left
                distances = (abs(estimate - left), abs(estimate - up),
                             abs(estimate - upper_left))
                value = (left, up, upper_left)[distances.index(min(distances))]
            else:
                value = 0
            row[index] = (row[index] + value) & 255
        output.extend(row)
        previous = row
    return bytes(output)


def _pdf_xref_section(data: bytes, offset: int) -> Optional[dict]:
    if not 0 < offset < len(data):
        return None
    if data.startswith(b"xref", offset) and data[offset + 4:offset + 5] in b" \t\r\n":
        pos = _pdf_space(data, offset + 4)
        ranges = []
        while not data.startswith(b"trailer", pos):
            match = _PDF_XREF_HEADER.match(data, pos)
            if not match:
                return None
            first, count = int(match.group(1)), int(match.group(2))
            pos = match.end()
            row_start = pos
            if count > MAX_PDF_BYTES // 18:
                return None
            for _ in range(count):
                row = _PDF_XREF_ROW.match(data, pos)
                if not row:
                    return None
                pos = row.end()
            ranges.append((first, count, row_start))
            pos = _pdf_space(data, pos)
        found = _pdf_dictionary(data, pos + len(b"trailer"))
        return {"kind": "table", "dictionary": found[0], "ranges": ranges} if found else None
    header = _PDF_OBJECT_HEADER.match(data, offset)
    if not header:
        return None
    found = _pdf_dictionary(data, header.end())
    if not found:
        return None
    dictionary, end = found
    if not _pdf_name_is(dictionary, b"Type", b"XRef"):
        return None
    widths = _pdf_field(dictionary, b"W")
    size = _pdf_number(dictionary, b"Size")
    w = _pdf_array_numbers(widths) if widths else None
    if w is None or len(w) != 3 or size is None:
        return None
    if sum(w) == 0 or any(width > 8 for width in w):
        return None
    index = _pdf_field(dictionary, b"Index")
    numbers = _pdf_array_numbers(index) if index else [0, size]
    if numbers is None or len(numbers) % 2 or not numbers:
        return None
    decoded = _pdf_decoded_stream(data, dictionary, end)
    if decoded is None:
        return None
    ranges = []
    byte_pos = 0
    for first, count in zip(numbers[::2], numbers[1::2]):
        ranges.append((first, count, byte_pos))
        byte_pos += count * sum(w)
        if byte_pos > len(decoded):
            return None
    return {"kind": "stream", "dictionary": dictionary, "ranges": ranges,
            "decoded": decoded, "widths": w}


def _pdf_xref_entry(data: bytes, section: dict, number: int) -> Optional[tuple[int, int, int]]:
    for first, count, row_start in reversed(section["ranges"]):
        if not first <= number < first + count:
            continue
        if section["kind"] == "table":
            pos = row_start
            for _ in range(number - first + 1):
                row = _PDF_XREF_ROW.match(data, pos)
                if not row:
                    return (-1, 0, 0)
                pos = row.end()
            return (1 if row.group(3) == b"n" else 0,
                    int(row.group(1)), int(row.group(2)))
        w = section["widths"]
        pos = row_start + (number - first) * sum(w)
        raw = section["decoded"][pos:pos + sum(w)]
        fields = []
        for width in w:
            fields.append(int.from_bytes(raw[:width], "big"))
            raw = raw[width:]
        return (fields[0] if w[0] else 1, fields[1], fields[2])
    return None


def _pdf_find_entry(data: bytes, sections: list[dict], number: int) -> Optional[tuple[int, int, int]]:
    for section in sections:
        entry = _pdf_xref_entry(data, section, number)
        if entry is not None:
            return entry
    return None


def _pdf_indirect_dictionary(data: bytes, offset: int, number: int,
                             generation: int) -> Optional[tuple[bytes, int]]:
    match = _PDF_OBJECT_HEADER.match(data, offset)
    if not match or (int(match.group(1)), int(match.group(2))) != (number, generation):
        return None
    return _pdf_dictionary(data, match.end())


def _pdf_resolve_dictionary(data: bytes, sections: list[dict], number: int,
                            generation: int, cache: dict[int, tuple[bytes, bytes]]) -> Optional[bytes]:
    entry = _pdf_find_entry(data, sections, number)
    if entry is None:
        return None
    kind, field1, field2 = entry
    if kind == 1:
        if field2 != generation:
            return None
        found = _pdf_indirect_dictionary(data, field1, number, generation)
        return found[0] if found else None
    if kind != 2 or generation != 0:
        return None
    object_stream = field1
    stream_entry = _pdf_find_entry(data, sections, object_stream)
    if stream_entry is None or stream_entry[0] != 1:
        return None
    if object_stream not in cache:
        found = _pdf_indirect_dictionary(data, stream_entry[1], object_stream, stream_entry[2])
        if not found or not _pdf_name_is(found[0], b"Type", b"ObjStm"):
            return None
        decoded = _pdf_decoded_stream(data, found[0], found[1])
        if decoded is None:
            return None
        cache[object_stream] = (decoded, found[0])
    decoded, dictionary = cache[object_stream]
    count = _pdf_number(dictionary, b"N")
    first = _pdf_number(dictionary, b"First")
    if count is None or first is None or first > len(decoded) or count > first // 2:
        return None
    header = decoded[:first]
    if not re.fullmatch(rb"[0-9\s]*", header):
        return None
    words = [int(value) for value in header.split()]
    if len(words) != count * 2 or field2 >= count:
        return None
    pairs = list(zip(words[::2], words[1::2]))
    if pairs[field2][0] != number:
        return None
    offsets = [position for _, position in pairs]
    if any(left >= right for left, right in zip(offsets, offsets[1:])):
        return None
    start = first + offsets[field2]
    end = first + offsets[field2 + 1] if field2 + 1 < count else len(decoded)
    if not first <= start < end <= len(decoded):
        return None
    found = _pdf_dictionary(decoded[start:end], 0)
    return found[0] if found else None


def _pdf_has_page_leaf(data: bytes, sections: list[dict], pages_ref: tuple[int, int],
                       cache: dict[int, tuple[bytes, bytes]]) -> bool:
    visited = set()

    def count_leaves(reference: tuple[int, int], depth: int) -> Optional[int]:
        if depth > 32 or len(visited) >= 4096 or reference in visited:
            return None
        visited.add(reference)
        node = _pdf_resolve_dictionary(data, sections, reference[0], reference[1], cache)
        if node is None:
            return None
        if _pdf_name_is(node, b"Type", b"Page"):
            return 1 if depth > 0 else None
        if not _pdf_name_is(node, b"Type", b"Pages"):
            return None
        count = _pdf_number(node, b"Count")
        kids_field = _pdf_field(node, b"Kids")
        kids = _pdf_array_values(kids_field) if kids_field else None
        if count is None or count <= 0 or not kids:
            return None
        actual = 0
        for child in kids:
            if child[0] != "reference":
                return None
            number, generation, _ = child[1].split()
            leaves = count_leaves((int(number), int(generation)), depth + 1)
            if leaves is None:
                return None
            actual += leaves
            if actual > count:
                return None
        return actual if actual == count else None

    return count_leaves(pages_ref, 0) is not None


def _valid_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size < 32 or size > MAX_PDF_BYTES:
                return False
            handle.seek(0)
            data = handle.read(size + 1)
        if len(data) != size or not re.match(rb"%PDF-[12]\.\d", data[:16]):
            return False
        markers = list(re.finditer(rb"startxref\s+(\d+)\s+%%EOF", data[-PDF_TAIL_BYTES:]))
        if not markers:
            return False
        offset = int(markers[-1].group(1))
        sections = []
        seen = set()
        root = None
        for _ in range(64):
            if offset in seen:
                return False
            seen.add(offset)
            section = _pdf_xref_section(data, offset)
            if section is None:
                return False
            dictionary = section["dictionary"]
            if root is None:
                root = _pdf_reference(dictionary, b"Root")
            if section["kind"] == "table":
                hybrid = _pdf_number(dictionary, b"XRefStm")
                if hybrid is not None:
                    if hybrid in seen:
                        return False
                    seen.add(hybrid)
                    extra = _pdf_xref_section(data, hybrid)
                    if extra is None or extra["kind"] != "stream":
                        return False
                    if root is None:
                        root = _pdf_reference(extra["dictionary"], b"Root")
                    sections.append(extra)
            sections.append(section)
            previous = _pdf_number(dictionary, b"Prev")
            if previous is None:
                break
            offset = previous
        else:
            return False
        if root is None:
            return False
        cache = {}
        catalog = _pdf_resolve_dictionary(data, sections, root[0], root[1], cache)
        if catalog is None or not _pdf_name_is(catalog, b"Type", b"Catalog"):
            return False
        pages_ref = _pdf_reference(catalog, b"Pages")
        if pages_ref is None:
            return False
        return _pdf_has_page_leaf(data, sections, pages_ref, cache)
    except (OSError, ValueError, zlib.error):
        return False


def download_pdf(url: str, destination: Path, root: Optional[Path] = None) -> int:
    if root is not None:
        if destination.is_absolute() and destination != destination.resolve():
            raise PaperError(f"Refusing a PDF path through a symlink or junction: {destination}")
        destination = _checked_output_path(root, destination)
    if destination.is_symlink():
        raise PaperError(f"Refusing a symlink as a PDF destination: {destination}")
    if destination.exists():
        if _valid_pdf(destination):
            return 0
        raise PaperError(f"Existing file is incomplete or invalid; refusing to overwrite: {destination}")
    if root is not None:
        destination = _checked_output_path(root, destination)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PaperError(f"Cannot create PDF directory: {exc}") from exc
    if root is not None:
        destination = _checked_output_path(root, destination)
    temp_name = None
    deadline = time.monotonic() + MAX_PDF_SECONDS
    try:
        try:
            response = _open_pdf(url)
        except urllib.error.HTTPError as exc:
            raise PaperError(f"PDF server returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PaperError(f"PDF request failed: {exc}") from exc
        with response:
            _public_https_url(response.geturl())
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".partial", prefix=".resource-downloader-",
                dir=root if root is not None else destination.parent, delete=False,
            ) as handle:
                temp_name = handle.name
                total = 0
                while True:
                    block = response.read(64 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > MAX_PDF_BYTES:
                        raise PaperError(f"PDF exceeds {MAX_PDF_BYTES // (1024 * 1024)} MB limit.")
                    if time.monotonic() > deadline:
                        raise PaperError("PDF exceeded the time limit.")
                    handle.write(block)
                if not total:
                    raise PaperError("PDF response was empty.")
        if not _valid_pdf(Path(temp_name)):
            raise PaperError("Response failed PDF header and cross-reference validation.")
        if root is not None:
            destination = _checked_output_path(root, destination)
        try:
            os.link(temp_name, destination)
        except FileExistsError as exc:
            if _valid_pdf(destination):
                return 0
            raise PaperError(f"PDF destination appeared and is invalid: {destination}") from exc
        except OSError as exc:
            raise PaperError(f"Cannot create PDF without overwriting another file: {exc}") from exc
        return total
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise PaperError(f"PDF transfer failed: {exc}") from exc
    finally:
        if temp_name and os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def _select_papers(papers: list[dict[str, object]], wanted_id: Optional[str], all_papers: bool) -> list[dict[str, object]]:
    if all_papers:
        return papers
    if not wanted_id:
        raise PaperError("Choose one --id, or explicitly pass --all.")
    wanted = wanted_id.casefold()
    matches = [
        paper for paper in papers
        if wanted in {compact(paper.get(field)).casefold() for field in ("id", "doi", "arxiv_id", "semantic_id", "openalex_id")}
    ]
    if len(matches) != 1:
        raise PaperError(f"Expected one paper for --id '{wanted_id}', found {len(matches)}.")
    return matches


def _safe_reason(error: Exception) -> str:
    # A signed redirect URL can contain a short-lived credential. Never save it
    # in the result document or show it in a failure summary.
    reason = re.sub(r"https?://\S+", "[URL]", compact(error))
    reason = re.sub(r"\?[^\s'\"<>]+", "?[redacted]", reason)
    reason = re.sub(r"(?i)\b(token|secret|auth|credential|signature|session|apikey|accesskey)=[^/\s'\"&]+",
                    r"\1=[redacted]", reason)
    return reason[:300]


def _record_attempt(paper: dict[str, object], provider: str, status: str, reason: str = "") -> None:
    attempts = paper.setdefault("download_attempts", [])
    attempts.append({"provider": provider, "status": status, "reason": _safe_reason(Exception(reason)) if reason else ""})
    if len(attempts) > 100:
        del attempts[:-100]


def _open_candidates(paper: dict[str, object]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    arxiv_id = re.sub(r"^arxiv:", "", compact(paper.get("arxiv_id")), flags=re.IGNORECASE)
    revision = re.search(r"v\d+$", arxiv_id, flags=re.IGNORECASE)

    def matches_revision(url: str) -> bool:
        if not revision:
            return True
        try:
            parts = urllib.parse.urlsplit(url)
        except ValueError:
            return False
        path = urllib.parse.unquote(parts.path).rstrip("/").casefold()
        expected = "/pdf/" + arxiv_id.casefold()
        return (parts.scheme == "https"
                and (parts.hostname or "").casefold() in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
                and path in {expected, expected + ".pdf"})

    for candidate in paper.get("pdf_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        url = compact(candidate.get("url"))
        if url and url not in seen and matches_revision(url):
            result.append({
                "provider": compact(candidate.get("provider")) or "saved-metadata",
                "url": url, "license": compact(candidate.get("license")),
                "version": compact(candidate.get("version")),
            })
            seen.add(url)
    legacy_url = compact(paper.get("pdf_url"))
    if legacy_url and legacy_url not in seen and matches_revision(legacy_url):
        result.append({"provider": "saved-metadata", "url": legacy_url, "license": "", "version": ""})
    return result


def _refresh_semantic_candidates(paper: dict[str, object]) -> None:
    """Retrieve short-lived links again without storing their access query."""
    semantic_id = compact(paper.get("semantic_id"))
    if not semantic_id:
        return
    if not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", semantic_id):
        raise PaperError("Saved Semantic Scholar ID is malformed.")
    fields = "title,authors,year,externalIds,openAccessPdf,url,abstract"
    url = ("https://api.semanticscholar.org/graph/v1/paper/"
           + urllib.parse.quote(semantic_id, safe="")
           + "?" + urllib.parse.urlencode({"fields": fields}))
    headers = {"Accept": "application/json"}
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if key:
        headers["x-api-key"] = key
    body = _request_bytes(url, headers, MAX_METADATA_BYTES)
    try:
        payload = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PaperError(f"Invalid Semantic Scholar JSON response: {exc}") from exc
    matches = parse_semantic({"data": [payload]})
    if len(matches) != 1:
        raise IdentityError("Semantic Scholar ID returned no paper metadata.")
    refreshed = matches[0]
    if (compact(refreshed.get("semantic_id")).casefold() != semantic_id.casefold()
            or _title_key(refreshed["title"]) != _title_key(paper["title"])):
        raise IdentityError("Semantic Scholar ID or title does not match saved paper.")
    for field in ("doi", "arxiv_id", "year"):
        previous = compact(paper.get(field)).casefold()
        current = compact(refreshed.get(field)).casefold()
        if previous and current and previous != current:
            raise IdentityError(f"Semantic Scholar {field} does not match saved paper.")
    if (re.search(r"v\d+$", compact(paper.get("arxiv_id")), flags=re.IGNORECASE)
            and not compact(refreshed.get("arxiv_id"))):
        raise IdentityError("Semantic Scholar did not confirm the saved arXiv revision.")
    before_author = _title_author_year(paper)[1]
    current_author = _title_author_year(refreshed)[1]
    if before_author and current_author and before_author != current_author:
        raise IdentityError("Semantic Scholar author does not match saved paper.")
    known = {candidate["url"] for candidate in _open_candidates(paper)}
    for candidate in _open_candidates(refreshed):
        if candidate["url"] not in known:
            paper.setdefault("pdf_candidates", []).append(candidate)
            known.add(candidate["url"])


def _discover_open_candidates(paper: dict[str, object]) -> None:
    doi = maybe_doi(paper.get("doi"))
    try:
        _refresh_semantic_candidates(paper)
    except PaperError as exc:
        status = "metadata_conflict" if isinstance(exc, IdentityError) else "metadata_failed"
        _record_attempt(paper, "semantic-scholar", status, str(exc))
    arxiv_id = re.sub(r"^arxiv:", "", compact(paper.get("arxiv_id")), flags=re.IGNORECASE)
    if re.search(r"v\d+$", arxiv_id, flags=re.IGNORECASE):
        # DOI-level open-access locations do not identify an arXiv revision.
        if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})v\d+", arxiv_id, flags=re.IGNORECASE):
            official = "https://arxiv.org/pdf/" + arxiv_id
            if official not in {candidate["url"] for candidate in _open_candidates(paper)}:
                paper.setdefault("pdf_candidates", []).append({
                    "provider": "arxiv", "url": official, "license": "",
                    "version": re.search(r"v\d+$", arxiv_id, flags=re.IGNORECASE).group(0),
                })
        else:
            _record_attempt(paper, "arxiv", "metadata_failed", "Saved arXiv revision ID is malformed")
        return
    if not doi:
        try:
            doi = crossref_doi_for_paper(paper)
            paper["doi"] = doi
            paper["sources"] = sorted(set(paper.get("sources") or []) | {"crossref"})
            _record_attempt(paper, "crossref", "metadata", "DOI confirmed by title, author and year")
        except PaperError as exc:
            _record_attempt(paper, "crossref", "metadata_failed", str(exc))
            return
    try:
        enriched = lookup_openalex_doi(doi)
        original_identity = _title_author_year(paper)
        enriched_identity = _title_author_year(enriched)
        if (original_identity[0] != enriched_identity[0]
                or any(a and b and a != b for a, b in zip(original_identity[1:], enriched_identity[1:]))):
            raise IdentityError("OpenAlex DOI title, first author or year does not match saved paper.")
        combined = merge_records([paper, enriched])[0]
        combined["id"] = paper["id"]
        paper.update(combined)
    except IdentityError as exc:
        _record_attempt(paper, "openalex", "metadata_conflict", str(exc))
        return
    except PaperError as exc:
        _record_attempt(paper, "openalex", "metadata_failed", str(exc))
        try:
            crossref = lookup_crossref_doi(doi)
            original_identity = _title_author_year(paper)
            crossref_identity = _title_author_year(crossref)
            if (original_identity[0] != crossref_identity[0]
                    or any(a and b and a != b for a, b in zip(original_identity[1:], crossref_identity[1:]))):
                raise IdentityError("Crossref DOI title, first author or year does not match saved paper.")
            paper["sources"] = sorted(set(paper.get("sources") or []) | {"crossref"})
        except PaperError as crossref_error:
            _record_attempt(paper, "crossref", "metadata_failed", str(crossref_error))
            return
    email = compact(os.environ.get("UNPAYWALL_EMAIL"))
    if email:
        try:
            candidates = lookup_unpaywall_candidates(doi, email, compact(paper.get("title")))
            known = {candidate["url"] for candidate in _open_candidates(paper)}
            for candidate in candidates:
                if candidate["url"] not in known:
                    paper.setdefault("pdf_candidates", []).append(candidate)
                    known.add(candidate["url"])
        except PaperError as exc:
            _record_attempt(paper, "unpaywall", "metadata_failed", str(exc))


def _existing_download(paper: dict[str, object], destination: Path, source: str) -> bool:
    if not _valid_pdf(destination):
        return False
    relative = str(PurePosixPath("pdfs", *(("scihub",) if source == "scihub" else ()), destination.name))
    if paper.get("downloaded_file") != relative:
        paper["downloaded_file"] = relative
        paper["download_source"] = "existing"
        paper["download_provider"] = "local"
        paper["downloaded_url"] = ""
    _record_attempt(paper, "local", "existing", "Validated PDF already present")
    print(f"{paper.get('id')}\texisting\t{destination}\t{destination.stat().st_size} bytes")
    return True


def command_download(args: argparse.Namespace, *, prefetched: Optional[list[dict[str, object]]] = None,
                     query: Optional[str] = None, doi_verified: bool = False) -> int:
    if args.scihub_base and args.source != "scihub":
        raise PaperError("--scihub-base requires --source scihub.")
    root = output_root(args.out)
    slug = require_slug(args.topic)
    scihub_bases = (
        [_scihub_base(args.scihub_base)] if args.scihub_base else
        [_scihub_base(base) for base in DEFAULT_SCIHUB_BASES]
    ) if args.source == "scihub" else []
    document = {"query": query, "papers": prefetched} if prefetched is not None else _load_saved_document(root, slug)
    papers = document["papers"]
    selected = _select_papers(papers, args.id, args.all)
    saved = 0
    failures: list[str] = []
    for paper in selected:
        try:
            if (args.source == "scihub"
                    and re.search(r"v\d+$", compact(paper.get("arxiv_id")), flags=re.IGNORECASE)):
                raise PaperError("Sci-Hub cannot verify a specific arXiv revision; select the DOI record or use arXiv.")
            if args.source == "scihub" and not doi_verified and maybe_doi(paper.get("doi")):
                _verified_doi_record(clean_doi(paper["doi"]), compact(paper.get("title")))
            relative_parts = (slug, "pdfs", *(("scihub",) if args.source == "scihub" else ()), _pdf_filename(paper))
            logical_destination = root.joinpath(*relative_parts)
            if logical_destination != logical_destination.resolve():
                raise PaperError(f"Refusing a PDF path through a symlink or junction: {logical_destination}")
            destination = safe_path(root, *relative_parts)
            if _existing_download(paper, destination, args.source):
                saved += 1
                continue
            if args.source == "scihub":
                mirror_errors = []
                scihub_success = False
                for base in scihub_bases:
                    mirror = urllib.parse.urlsplit(base).hostname or "scihub"
                    try:
                        pdf_urls = _scihub_pdf_urls(paper, base)
                    except PaperError as exc:
                        reason = _safe_reason(exc)
                        _record_attempt(paper, mirror, "failed", reason)
                        mirror_errors.append(f"{mirror}: {reason}")
                        continue
                    for pdf_url in pdf_urls:
                        try:
                            size = download_pdf(pdf_url, destination, root)
                            if size:
                                _record_attempt(paper, mirror, "success")
                            scihub_success = True
                            break
                        except PaperError as exc:
                            reason = _safe_reason(exc)
                            _record_attempt(paper, mirror, "failed", reason)
                            mirror_errors.append(f"{mirror}: {reason}")
                    if scihub_success:
                        break
                if not scihub_success:
                    raise PaperError("All configured Sci-Hub mirrors failed: " + "; ".join(mirror_errors))
                provider = mirror
            else:
                attempted: set[str] = set()
                errors: list[str] = []
                provider = ""
                for discovery in (False, True):
                    if discovery:
                        _discover_open_candidates(paper)
                    for candidate in _open_candidates(paper):
                        pdf_url = candidate["url"]
                        if pdf_url in attempted:
                            continue
                        attempted.add(pdf_url)
                        provider = candidate["provider"]
                        try:
                            size = download_pdf(pdf_url, destination, root)
                            if size:
                                _record_attempt(paper, provider, "success")
                            break
                        except PaperError as exc:
                            reason = _safe_reason(exc)
                            _record_attempt(paper, provider, "failed", reason)
                            errors.append(f"{provider}: {reason}")
                    else:
                        continue
                    break
                else:
                    raise PaperError("All open PDF candidates failed: " + ("; ".join(errors) or "none found"))
        except PaperError as exc:
            reason = _safe_reason(exc)
            _record_attempt(paper, args.source, "failed", reason)
            failures.append(f"{paper.get('id')}: {reason}")
            continue
        if size == 0:
            _existing_download(paper, destination, args.source)
            saved += 1
            continue
        paper["downloaded_file"] = str(PurePosixPath("pdfs", *(("scihub",) if args.source == "scihub" else ()), destination.name))
        paper["download_source"] = args.source
        paper["download_provider"] = provider
        paper["downloaded_url"] = pdf_url
        saved += 1
        print(f"{paper.get('id')}\t{args.source}\t{destination}\t{size} bytes")
    _write_topic(root, slug, document["query"], papers)
    for failure in failures:
        print(f"Failed: {failure}", file=sys.stderr)
    print(f"Downloaded or already present: {saved}; failed: {len(failures)}")
    return 1 if failures else 0


def _verified_doi_record(doi: str, expected_title: str = "") -> dict[str, object]:
    try:
        paper = lookup_openalex_doi(doi)
    except PaperError as openalex_error:
        try:
            paper = lookup_crossref_doi(doi)
        except PaperError as crossref_error:
            raise PaperError(
                f"Could not verify DOI with OpenAlex ({_safe_reason(openalex_error)}) "
                f"or Crossref ({_safe_reason(crossref_error)})."
            ) from crossref_error
    if expected_title and _title_key(expected_title) != _title_key(paper["title"]):
        raise PaperError("The verified DOI title does not match --expect-title.")
    return paper


def command_verify(args: argparse.Namespace) -> int:
    doi = clean_doi(args.doi)
    paper = _verified_doi_record(doi, args.expect_title or "")
    print(json.dumps({
        "doi": doi, "title": paper["title"], "authors": paper["authors"],
        "year": paper["year"], "metadata_source": paper["sources"][0],
    }, ensure_ascii=False, indent=2))
    return 0


def command_fetch(args: argparse.Namespace) -> int:
    if args.scihub_base and args.source != "scihub":
        raise PaperError("--scihub-base requires --source scihub.")
    root = output_root(args.out)
    doi = clean_doi(args.doi)
    paper = _verified_doi_record(doi, args.expect_title or "")
    slug = require_slug(args.topic) if args.topic else topic_slug(paper["title"])
    path = safe_path(root, slug, RESULTS_JSON)
    prior_document = _load_saved_document(root, slug) if path.exists() else None
    prior = prior_document["papers"] if prior_document else []
    for old in prior:
        title_conflict = _title_key(old.get("title")) != _title_key(paper["title"])
        same_doi = maybe_doi(old.get("doi")).casefold() == doi.casefold()
        same_openalex = bool(compact(paper.get("openalex_id")) and
                             compact(old.get("openalex_id")).casefold() == compact(paper.get("openalex_id")).casefold())
        if title_conflict and (same_doi or same_openalex):
            raise PaperError("Existing topic has conflicting metadata for this DOI or OpenAlex ID; inspect it or choose another topic.")
    papers = merge_records(prior + [paper])
    selected = next((item for item in papers
                     if (maybe_doi(item.get("doi")).casefold() == doi.casefold()
                          and _title_key(item.get("title")) == _title_key(paper["title"])
                          and not re.search(r"v\d+$", compact(item.get("arxiv_id")), flags=re.IGNORECASE))), None)
    if selected is None:
        raise PaperError("Verified DOI was not retained in the saved topic.")
    print(f"Verified {doi}: {selected['title']}")
    return command_download(argparse.Namespace(
        out=args.out, topic=slug, source=args.source, scihub_base=args.scihub_base,
        id=selected["id"], all=False,
    ), prefetched=papers, query=prior_document["query"] if prior_document else doi,
       doi_verified=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search papers and download open or explicitly selected Sci-Hub PDFs.")
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search", help="Search OpenAlex, arXiv, and Semantic Scholar; save unless --no-save.")
    search.add_argument("--out", type=Path, help="Directory for saved topics (required unless --no-save).")
    search.add_argument("--query", required=True, help="Paper search query.")
    search.add_argument("--topic", help="Optional lowercase topic slug.")
    search.add_argument("--limit", type=int, default=10, help="Maximum hits per source, 1-100 (default: 10).")
    search.add_argument("--sources", default="openalex,arxiv,semantic", help="Comma-separated: openalex, arxiv, semantic.")
    search.add_argument("--no-save", action="store_true", help="Print results without saving a topic.")
    listing = sub.add_parser("list", help="List saved topics.")
    listing.add_argument("--out", type=Path, required=True)
    download = sub.add_parser("download", help="Download a saved paper from open links or Sci-Hub mirrors.")
    download.add_argument("--out", type=Path, required=True)
    download.add_argument("--topic", required=True, help="Saved topic slug.")
    download.add_argument("--source", choices=("open", "scihub"), default="open", help="PDF source (default: open; Sci-Hub requires explicit opt-in).")
    download.add_argument("--scihub-base", help="Use only this Sci-Hub HTTPS mirror; otherwise try .se, .st, then .ru.")
    selection = download.add_mutually_exclusive_group(required=True)
    selection.add_argument("--id", help="Exactly one saved paper ID, DOI, arXiv ID, Semantic Scholar ID, or OpenAlex ID.")
    selection.add_argument("--all", action="store_true", help="Explicitly download every available PDF in topic.")
    verify = sub.add_parser("verify", help="Verify one DOI and optionally its title without saving or downloading.")
    verify.add_argument("--doi", required=True, help="One complete DOI.")
    verify.add_argument("--expect-title", help="Reject the DOI if its verified title differs from this title.")
    fetch = sub.add_parser("fetch", help="Verify a DOI, save metadata, and try the selected PDF source.")
    fetch.add_argument("--out", type=Path, required=True, help="Directory for saved topics (outside this skill).")
    fetch.add_argument("--doi", required=True, help="One complete DOI.")
    fetch.add_argument("--topic", help="Optional lowercase topic slug; otherwise derive from verified title.")
    fetch.add_argument("--expect-title", help="Reject the DOI if its verified title differs from this title.")
    fetch.add_argument("--source", choices=("open", "scihub"), default="open",
                       help="PDF source (default: open; Sci-Hub requires explicit opt-in).")
    fetch.add_argument("--scihub-base", help="Use only this Sci-Hub HTTPS mirror with --source scihub.")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (OSError, ValueError, TypeError):
                pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "search" and (not compact(args.query) or not 1 <= args.limit <= 100):
        parser.error("--query must be nonempty and --limit must be between 1 and 100.")
    if args.command == "search" and not args.no_save and args.out is None:
        parser.error("search requires --out unless --no-save is set.")
    if args.command == "search" and args.no_save and args.topic:
        parser.error("--topic cannot be used with --no-save.")
    try:
        if args.command == "verify":
            return command_verify(args)
        if args.command == "search" and args.no_save:
            return command_search(args)
        if args.command == "list":
            return command_list(args)
        root = output_root(args.out)
        with output_lock(root):
            _recover_pending(root)
            if args.command == "search":
                return command_search(args)
            if args.command == "fetch":
                return command_fetch(args)
            return command_download(args)
    except PaperError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
