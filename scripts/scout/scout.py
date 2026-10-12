#!/usr/bin/env python3
"""Find REGDOCS filings, record what REGDOCS says about them, and fetch them.

    ./scripts/scout/scout.sh discover --from 2026-01-01 --to 2026-07-31
    ./scripts/scout/scout.sh enrich   --from 2026-01-01 --to 2026-07-31
    ./scripts/scout/scout.sh status
    ./scripts/scout/scout.sh download [--limit N]
    ./scripts/scout/scout.sh doc 4647200 [4692360 ...]
    ./scripts/scout/scout.sh missing [--limit N]

One record per PDF document, and nothing else holds a copy:

    output/<id>/<id>.cer.meta.json.gz  what REGDOCS says about it, when it said
                                       it, and what changed since
    source/<id>.pdf                    the file itself, once downloaded

`discover` lists every item filed in a date range. `enrich` also walks the
filings (Compound Documents and Folders) those items sit in, and runs one
search per facet value to learn each document's Document Type, Commodity and
so on. Both checkpoint every page under scripts/scout/scout-state/<from>_<to>/:
running the same command again resumes, and --refresh starts the range over.
`download` fetches every recorded PDF not yet in source/.

Requests go one at a time, about a second apart, slowing down on errors; a run
stops once requests keep failing rather than add load to REGDOCS.

A record is written before the page that found it is checkpointed, so a crash
replays the page and the merge makes the replay harmless. A record is only
ever added to: a field is replaced only by a non-empty value, and a facet
category only once every search in it has finished.
"""
from __future__ import annotations

import argparse
import calendar
import copy
import fcntl
import gzip
import hashlib
import json
import os
import random
import re
import shutil
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup, Tag

CER_SCHEMA = 4
# Corpus data lives at the repository root, beside scripts/.
ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "source"
OUT = ROOT / "output"
STATE_ROOT = Path(__file__).resolve().parent / "scout-state"
RECORD = "cer.meta.json.gz"

DOMAIN = "https://apps.cer-rec.gc.ca"
ADVANCED_URL = f"{DOMAIN}/REGDOCS/Search/Advanced"
RESULTS_URL = f"{DOMAIN}/REGDOCS/Search/SearchAdvancedResults"
VIEW_URL = f"{DOMAIN}/REGDOCS/Item/View/{{id}}"
MEMBERS_URL = f"{DOMAIN}/REGDOCS/Item/LoadResult/{{id}}"
PAGE_SIZE = 200
SORT_OLDEST_FIRST = 21
FACET_CATEGORIES = ["Document Type", "Application Type", "Commodity", "Role", "File Type"]

# One request at a time. Errors double the delay; successes bring it back.
MIN_DELAY, MAX_DELAY = 0.75, 1.25
MAX_DELAY_MULTIPLIER = 8.0
MAX_RETRIES, BACKOFF = 4, 2.0
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}
# Requests that failed even after their retries, in a row, before a run stops.
MAX_FAILURES_IN_ROW = 5
# A download REGDOCS answers about that one file (a 404, or a page where the
# PDF should be) says nothing about whether REGDOCS is up, so it does not count
# toward MAX_FAILURES_IN_ROW -- unless so many in a row fail that the download
# URLs themselves may have changed.
MAX_UNAVAILABLE_IN_ROW = 50

# REGDOCS pages beyond 10,000 results, but deep offsets are slow and drift
# while the index changes. Larger windows are halved instead.
SPLIT_ABOVE = 10000
# REGDOCS cannot render a results page holding certain records: it returns its
# own error page for every page that includes them. Such a page is narrowed --
# a window to single days, a day to pages of 20, a page of 20 to single
# results -- and a result that still fails is recorded as a gap.
NARROWER = {PAGE_SIZE: 20, 20: 1}
# A container can report more members than it lists (presumably members hidden
# from the public). The same shortfall seen on this many runs is recorded as a
# gap instead of failing the range forever.
SHORT_CONTAINER_TRIES = 3
# A container recorded as a gap by another range is reused for this long.
GAP_REUSE_DAYS = 30

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9,fr-CA;q=0.7,fr;q=0.6",
}
AJAX_HEADERS = {**HEADERS, "X-Requested-With": "XMLHttpRequest"}

ITEM_HREF_RE = re.compile(r"/REGDOCS/(Item/View|File/Download)/(\d+)", re.I)
TOTAL_RE = re.compile(r"Item\(s\)\s*-\s*[\d,]+\s*to\s*[\d,]+\s*out of about\s*([\d,]+)", re.I)
FILING_RE = re.compile(r"Filing:\s*(\S+)", re.I)
WS_RE = re.compile(r"\s+")
NON_WORD_RE = re.compile(r"[^a-z0-9]+")
DATE_TOKEN_RE = re.compile(r"\b((?:19|20)\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b")
FILING_TITLE_RE = re.compile(r"\b(C\d{4,})(?:-(\d{1,4}))?\b", re.I)
EXHIBIT_RE = re.compile(r"\b(A(?=[0-9A-Z]{5,}\b)(?=[0-9A-Z]*\d)[0-9A-Z]{5,})\b", re.I)
ACTIVITY_RE = re.compile(r"\b([A-Z]{2,8}(?:-[A-Z]{2,8})*[- ]?\d{2,4}(?:-\d{1,4})+)\b", re.I)
REGULATORY_ID_RE = re.compile(
    r"\b((?:AO|GO|GPSO|EPR|MO|RO|XG|GC|GH|OH|MH|RH|OF|OM)[A-Z0-9]*(?:-[A-Z0-9]+)+)\b", re.I)
NO_RESULTS_RE = re.compile(
    r"(?:no\s+(?:items?|results?|records?|documents?)\s+(?:(?:were|are)\s+)?(?:found|available)|"
    r"there\s+(?:are|were)\s+no\s+(?:items?|results?|records?|documents?)|"
    r"no\s+(?:items?|results?|records?|documents?)\s+to\s+display|"
    r"aucun(?:e)?\s+(?:élément|résultat|document)(?:\s+n['’]a\s+été\s+trouvé)?)", re.I)


class SearchRejected(RuntimeError):
    """A page is not what was asked for; its cursor must not advance."""


class TooManyFailures(RuntimeError):
    """Requests keep failing; carrying on would only add load to REGDOCS."""


class Incomplete(RuntimeError):
    def __init__(self, message, keep=None):
        super().__init__(message)
        self.keep = keep or {}  # saved on the failed job, which is otherwise reloaded


class UsageError(ValueError):
    """Bad arguments, or state that does not match them: reported, not a bug."""


# --------------------------------------------------------------------------
# small helpers

def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean(v) -> str:
    return "" if v is None else WS_RE.sub(" ", str(v)).strip()


def norm_label(label: str) -> str:
    t = re.sub(r"[\[\](){}]", " ", clean(label).casefold().rstrip(":"))
    return clean(NON_WORD_RE.sub(" ", t))


def normalize_date(value: str) -> str | None:
    cand = clean(value).split("T", 1)[0]
    if not cand:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%d/%m/%Y", "%m/%d/%Y",
                "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(cand, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    m = DATE_TOKEN_RE.search(cand)
    try:
        return date(*map(int, m.groups())).isoformat() if m else None
    except ValueError:
        return None


def parse_day(value: str) -> str:
    """A YYYY-MM-DD date, with a day past the end of its month clamped to the last.

    REGDOCS does not reject an impossible date; it silently drops it. An end
    date of 2026-09-31 becomes "until today", and a start date of 2026-02-30
    becomes "since 2002". So a date is made valid here, before it can reach a
    request, and anything that cannot be made valid is refused.
    """
    parts = clean(value).split("-")
    if (len(parts) != 3 or not all(p.isdigit() for p in parts)
            or len(parts[0]) != 4 or not 1 <= len(parts[1]) <= 2 or not 1 <= len(parts[2]) <= 2):
        raise ValueError(f"{value!r} is not a YYYY-MM-DD date")
    year, month, day = map(int, parts)
    if not 1990 <= year <= date.today().year + 1:
        raise ValueError(f"{value!r}: year {year} is outside REGDOCS's range")
    if not 1 <= month <= 12:
        raise ValueError(f"{value!r}: month must be 1-12")
    if day < 1:
        raise ValueError(f"{value!r}: day must be at least 1")
    return f"{year:04d}-{month:02d}-{min(day, calendar.monthrange(year, month)[1]):02d}"


def windows(start: str, end: str, yearly=False):
    """Calendar months (or years) covering start..end, clipped to it."""
    current, stop = date.fromisoformat(start), date.fromisoformat(end)
    while current <= stop:
        last = date(current.year, 12, 31) if yearly else date(
            current.year, current.month, calendar.monthrange(current.year, current.month)[1])
        last = min(last, stop)
        yield current.isoformat(), last.isoformat()
        current = last + timedelta(days=1)


def is_container_kind(v) -> bool:
    return clean(v).casefold() in {"compound document", "folder"}


def is_pdf_kind(v) -> bool:
    return "pdf" in clean(v).casefold()


def is_paper_only(v) -> bool:
    t = clean(v).casefold()
    return "paper only" in t or "papier seulement" in t


def write_json(path: Path, value) -> None:
    """Write atomically; gzipped when the name ends in .gz (with no timestamp in
    the header, so the same record always compresses to the same bytes)."""
    tmp = path.with_name(path.name + ".part")
    data = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    tmp.write_bytes(gzip.compress(data, mtime=0) if path.name.endswith(".gz") else data)
    tmp.replace(path)


def read_json(path: Path):
    data = path.read_bytes()
    return json.loads(gzip.decompress(data) if path.name.endswith(".gz") else data)


@contextmanager
def exclusive(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Incomplete(f"another scout process holds {path}") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def title_identifiers(title: str) -> dict[str, list[str]]:
    """Identifiers REGDOCS prints in a title, grouped by type.

    Same patterns and precedence as cer-regdocs2, so records seeded from it and
    records scouted here agree. These come from the title text, not from a
    structured field, and are only as reliable as the filer's naming.
    """
    text = clean(title)
    out: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()

    def add(kind: str, value: str) -> None:
        if value not in seen:
            seen.add(value)
            out[kind].append(value)

    f = FILING_TITLE_RE.search(text)
    if f:
        add("filing_number", f.group(1).upper())
        if f.group(2):
            add("filing_sequence", f.group(2))
    for m in EXHIBIT_RE.finditer(text):
        add("exhibit_number", m.group(1).upper())
    for m in REGULATORY_ID_RE.finditer(text):
        v = m.group(1).upper()
        add("regulatory_instrument_number"
            if v.startswith(("AO-", "GO-", "GPSO-", "EPR-", "MO-", "RO-")) else "activity_number", v)
    for m in ACTIVITY_RE.finditer(text):
        v = clean(m.group(1)).upper().replace(" ", "-")
        if v.startswith("C") and v[1:].replace("-", "").isdigit():
            continue
        add("regulatory_instrument_number"
            if any(p in v for p in ("ORDER", "GPSO", "AO-", "GO-")) else "activity_number", v)
    padded = f" {text} "
    if re.search(r"(?:^|\s|[-–—_/])(FR|FRENCH|FRANÇAIS)(?:\s|[-–—_/]|$)", padded, re.I):
        out["language_marker"].append("fr")
    elif re.search(r"(?:^|\s|[-–—_/])(EN|ENGLISH|ANGLAIS)(?:\s|[-–—_/]|$)", padded, re.I):
        out["language_marker"].append("en")
    return dict(out)


# --------------------------------------------------------------------------
# HTTP

class Refused(Exception):
    """REGDOCS answered, but not with what was asked for."""

    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


def describe(exc: Exception) -> str:
    return str(exc) if isinstance(exc, Refused) else f"{type(exc).__name__}: {exc}"


class Http:
    """One adaptively paced request at a time, retried, honouring Retry-After."""

    def __init__(self, min_delay=MIN_DELAY, max_delay=MAX_DELAY) -> None:
        if min_delay < 0 or max_delay < min_delay:
            raise UsageError("invalid request delay range")
        self.client = httpx.Client(cookies={"RDI-NumberOfRecords": str(PAGE_SIZE)},
                                   follow_redirects=True,
                                   timeout=httpx.Timeout(60.0, connect=30.0))
        self.min_delay, self.max_delay = min_delay, max_delay
        self.delay_multiplier = 1.0
        self.next_at = 0.0
        self.requests = 0
        self.failed_in_row = 0

    def _pace(self) -> None:
        wait = self.next_at - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.next_at = time.monotonic() + random.uniform(
            self.min_delay, self.max_delay) * self.delay_multiplier

    def check(self, response) -> None:
        """Raise Refused for anything but a 200, honouring Retry-After."""
        if response.status_code == 200:
            return
        retry_after = clean(response.headers.get("retry-after"))
        if retry_after.isdigit():
            self.next_at = max(self.next_at, time.monotonic() + int(retry_after))
        raise Refused(f"HTTP {response.status_code}", response.status_code in RETRYABLE)

    def attempt(self, send):
        """Call send() paced, retrying what is worth retrying.
        -> (its result, None), or (None, the last exception)."""
        for n in range(1, MAX_RETRIES + 2):
            self._pace()
            self.requests += 1
            try:
                result = send()
            except (httpx.RequestError, Refused) as exc:
                retryable = getattr(exc, "retryable", True)
                if retryable:
                    self.delay_multiplier = min(MAX_DELAY_MULTIPLIER, self.delay_multiplier * 2.0)
                if not retryable or n > MAX_RETRIES:
                    return None, exc
                time.sleep(BACKOFF ** n + random.uniform(0, 1))
            else:
                self.failed_in_row = 0
                self.delay_multiplier = max(1.0, self.delay_multiplier * 0.85)
                return result, None

    def gave_up(self, error: str) -> None:
        self.failed_in_row += 1
        if self.failed_in_row >= MAX_FAILURES_IN_ROW:
            raise TooManyFailures(f"{self.failed_in_row} requests in a row failed; last: {error}")

    def get(self, url: str, *, params=None, ajax=False, referer=None, page_size=PAGE_SIZE):
        """-> (ok, text, final_url, error). page_size applies to this request only."""
        headers = {**(AJAX_HEADERS if ajax else HEADERS), **({"Referer": referer} if referer else {})}
        # httpx replaces a URL's query when params is supplied, even with {}.
        target = httpx.URL(url).copy_merge_params(params) if params else url

        def send():
            response = self.client.get(target, headers=headers)
            self.check(response)
            return response

        self.client.cookies.set("RDI-NumberOfRecords", str(page_size))
        try:
            response, exc = self.attempt(send)
        finally:
            self.client.cookies.set("RDI-NumberOfRecords", str(PAGE_SIZE))
        if exc:
            self.gave_up(describe(exc))
            return False, None, None, describe(exc)
        return True, response.text, str(response.url), None

    def close(self) -> None:
        self.client.close()


# --------------------------------------------------------------------------
# REGDOCS pages. Each is parsed once (parse_html) and the soup passed around.

def parse_html(html: str | None) -> BeautifulSoup:
    return BeautifulSoup(html or "", "lxml")


@dataclass
class Row:
    document_id: str
    name: str
    url: str
    is_file: bool
    date: str | None
    submitter: str
    kind: str | None = None
    filing_number: str | None = None
    filing_id: str | None = None
    company: str | None = None
    company_id: str | None = None
    project: str | None = None
    project_id: str | None = None
    synthetic: bool = False

    @property
    def recordable(self) -> bool:
        return (self.is_file and not self.synthetic and self.document_id.isdigit()
                and is_pdf_kind(self.kind))


def parse_total(html: str | None) -> int | None:
    m = TOTAL_RE.search(html or "")
    return int(m.group(1).replace(",", "")) if m else None


def explicit_empty(page: BeautifulSoup) -> bool:
    return bool(NO_RESULTS_RE.search(clean(page.get_text(" ", strip=True))))


def recognized(html: str | None, page: BeautifulSoup) -> bool:
    """Reject HTTP-200 maintenance or error pages posing as results."""
    return bool(clean(html)) and (page.find("tbody") is not None
                                  or parse_total(html) is not None or explicit_empty(page))


def regdocs_error(html: str) -> bool:
    """REGDOCS's own error page, as opposed to a results page or a network fault."""
    return len(html) < 2000 and "has encountered an unexpected error" in html


def regdocs_unavailable(page: BeautifulSoup) -> bool:
    """An explicit missing-item notice, never a generic or empty error page."""
    for panel in page.select(".panel-warning"):
        text = clean(panel.get_text(" ", strip=True)).casefold()
        if ("looking for document or filing?" in text
                and "the system was unable to locate them." in text):
            return True
    return False


def _nearby_label(anchor: Tag) -> str:
    parent = anchor.parent
    if not isinstance(parent, Tag):
        return ""
    prev = parent.find_previous_sibling()
    if isinstance(prev, Tag):
        t = clean(prev.get_text(" ", strip=True)).rstrip(":")
        if 0 < len(t) <= 80:
            return t
    row = parent.find_parent(class_=re.compile(r"\brow\b", re.I))
    if isinstance(row, Tag):
        for c in row.find_all(class_=re.compile(r"label|field|name|header", re.I), limit=3):
            t = clean(c.get_text(" ", strip=True)).rstrip(":")
            if t and anchor not in c.descendants and len(t) <= 80:
                return t
    return ""


def parse_rows(page: BeautifulSoup, *, container_id: str | None = None) -> list[Row]:
    """Result rows, keyed by REGDOCS item id.

    Inside a container, paper-only rows and rows sharing a placeholder id get a
    synthetic id instead, so distinct rows cannot collapse onto one document.
    Synthetic rows are never recorded. A row that cannot be parsed rejects the
    whole page rather than be silently lost.
    """
    out: list[Row] = []
    skipped = 0
    for tr in (tr for tb in page.find_all("tbody") for tr in tb.find_all("tr")):
        cells = tr.find_all("td")
        if not cells:
            continue
        if len(cells) == 1 and NO_RESULTS_RE.search(clean(cells[0].get_text(" ", strip=True))):
            continue
        summary = cells[0].find("summary")
        primary = summary if isinstance(summary, Tag) else cells[0]
        link = match = None
        for a in primary.find_all("a", href=True):
            if m := ITEM_HREF_RE.search(a["href"]):
                link, match = a, m
                break
        if link is None:
            # A "Hyperlink to Web Page" item points outside REGDOCS and has no
            # document id; anything else without one is markup we do not know.
            if not primary.find(title=re.compile(r"hyperlink", re.I)):
                skipped += 1
            continue
        kind = None
        for icon in primary.find_all(["i", "span", "img"]):
            t = clean(icon.get("title") or icon.get("alt"))
            if t and ("document" in t.casefold() or is_container_kind(t)):
                kind = t
                break
        # A sub-folder inside a container is listed in one cell, with no date
        # or submitter; any other short row is markup we do not know.
        full = len(cells) >= 3
        if not full and not is_container_kind(kind):
            skipped += 1
            continue
        row = Row(document_id=match.group(2),
                  name=clean(link.get_text(" ", strip=True)),
                  url=urljoin(DOMAIN, link["href"]),
                  is_file=match.group(1).casefold() == "file/download",
                  date=normalize_date(cells[1].get_text(" ", strip=True)) if full else None,
                  submitter=clean(cells[2].get_text(" ", strip=True)) if full else "",
                  kind=kind)
        if not row.kind and re.search(r"(?:^|[-–— ])HTML(?:[-–— ]|$)", row.name, re.I):
            row.kind = "Html Document"
        details = cells[0].find("details")
        if isinstance(details, Tag):
            for a in details.find_all("a", href=True):
                ref = ITEM_HREF_RE.search(a["href"])
                if a is link or not ref:
                    continue
                text = clean(a.get_text(" ", strip=True))
                label = norm_label(_nearby_label(a))
                if fm := FILING_RE.search(text):
                    row.filing_number, row.filing_id = fm.group(1), ref.group(2)
                elif label == "company":
                    row.company, row.company_id = text, ref.group(2)
                elif label == "project":
                    row.project, row.project_id = text, ref.group(2)
        out.append(row)

    if skipped:
        raise SearchRejected(f"{skipped} result row(s) could not be parsed; refusing to advance the page")
    if container_id:
        counts: dict[str, int] = defaultdict(int)
        for r in out:
            counts[r.document_id] += 1
        for r in out:
            if counts[r.document_id] > 1 or is_paper_only(f"{r.name} {r.kind or ''}"):
                r.synthetic, r.is_file = True, False
                r.document_id = f"row:{container_id}:{hashlib.sha256(r.name.encode()).hexdigest()[:12]}"
    return out


def container_endpoint(page: BeautifulSoup, page_url: str, item_id: str) -> str | None:
    """The member-list URL a container page declares, and nothing guessed."""
    cands = []
    sec = page.find("section", id="section-items")
    if isinstance(sec, Tag):
        cands += [clean(sec.get(a)) for a in ("data-ajax-replace", "data-ajax-append", "data-url")
                  if clean(sec.get(a))]
    if not cands:
        cands += [clean(e.get("data-ajax-replace")) for e in page.find_all(attrs={"data-ajax-replace": True})
                  if "/REGDOCS/Item/LoadResult/" in clean(e.get("data-ajax-replace"))]
    want = f"/REGDOCS/Item/LoadResult/{item_id}".casefold()
    for c in cands:
        u = urljoin(page_url, c)
        if want in u.casefold():
            return u
    return None


def next_container_page(page: BeautifulSoup, current: str) -> str | None:
    has_next = False
    for a in page.find_all("a"):
        rel = " ".join(a.get("rel") or []).casefold()
        text = clean(a.get_text(" ", strip=True)).casefold()
        title = clean(a.get("title") or a.get("aria-label")).casefold()
        cls = " ".join(a.get("class") or []).casefold()
        if not ("next" in rel or "next" in title or "next" in cls
                or text in {"next", "next page", ">", "»", "›"}):
            continue
        has_next = True
        for attr in ("href", "data-ajax-url", "data-url"):
            t = clean(a.get(attr))
            if t and not t.startswith("#"):
                target = urljoin(current, t)
                if urlsplit(target).scheme in {"http", "https"}:
                    return target
        m = re.search(r"['\"]([^'\"]*?/REGDOCS/Item/LoadResult/[^'\"]+)['\"]", clean(a.get("onclick")))
        if m:
            return urljoin(current, m.group(1))
    if not has_next:
        return None
    # REGDOCS's Next anchor has href="#". DocumentResults.js requests this
    # declared URL with sr=currentIndex+numberResultsPerPage. The hidden
    # inputs remain on the final page, so they only apply with a Next anchor.
    values = {}
    for name in ("nextPageUrl", "currentIndex", "numberResultsPerPage"):
        node = page.find("input", id=name)
        values[name] = clean(node.get("value")) if node else ""
    try:
        offset, size = int(values["currentIndex"]), int(values["numberResultsPerPage"])
        target = httpx.URL(urljoin(current, values["nextPageUrl"]))
        if (values["nextPageUrl"] and not values["nextPageUrl"].startswith("#")
                and target.scheme in {"http", "https"} and target.host
                and offset > 0 and size > 0):
            return str(target.copy_set_param("sr", offset + size))
    except (ValueError, httpx.InvalidURL):
        pass
    raise SearchRejected("container has a Next link without usable pagination data; "
                         "refusing to stop at this page")


def facet_catalog(page: BeautifulSoup) -> dict[str, dict[str, str]]:
    """Every facet category Advanced Search offers, with its values."""
    cat: dict[str, dict[str, str]] = {}
    for lab in page.find_all("label", attrs={"for": re.compile(r"^selectFilter\d+$")}):
        name = clean(lab.get_text(" ", strip=True))
        sel = page.find("select", id=lab.get("for"))
        if not name or not isinstance(sel, Tag):
            continue
        opts = {clean(o.get("value")): clean(o.get_text(" ", strip=True))
                for o in sel.find_all("option")
                if clean(o.get("value")) and clean(o.get_text(" ", strip=True))}
        if opts:
            cat[name] = opts
    return cat


def breadcrumb_parent(page: BeautifulSoup, doc_id: str) -> str | None:
    """The item a document's page names as its immediate parent: its filing."""
    crumb = page.select_one("ol.breadcrumb")
    if not isinstance(crumb, Tag):
        return None
    ids = [m.group(2) for a in crumb.find_all("a", href=True)
           if (m := ITEM_HREF_RE.search(a["href"])) and m.group(2) != doc_id]
    return ids[-1] if ids else None


# --------------------------------------------------------------------------
# records

def record_path(doc_id: str) -> Path:
    return OUT / doc_id / f"{doc_id}.{RECORD}"


def read_record(doc_id: str) -> dict | None:
    """The existing record, or None. A record in an older schema carries no
    history, so it is replaced rather than merged into."""
    path = record_path(doc_id)
    if not path.exists():
        return None
    rec = read_json(path)
    return rec if rec.get("cer_schema", 0) >= CER_SCHEMA else None


def record_from_row(row: Row) -> dict:
    def ref(name, ident):
        return {"name": name, "id": int(ident) if ident else None}
    return {
        "title": row.name,
        "date": row.date,
        "submitter": row.submitter or None,
        "kind": row.kind,
        "url": row.url,
        "view_url": VIEW_URL.format(id=row.document_id),
        "filing": {"number": row.filing_number, "id": int(row.filing_id) if row.filing_id else None},
        "company": ref(row.company, row.company_id),
        "project": ref(row.project, row.project_id),
        "identifiers": title_identifiers(row.name),
    }


# Documents REGDOCS links in place of the real one, under each filing that
# lacks it: a notice that the translation is pending, or that the document
# exists only on paper. One id, listed thousands of times, each listing with
# the title, date and filing of the document it stands in for. Recognised by
# id: the same markers also appear in the titles of ordinary documents.
STAND_INS = {
    "41059": ("TranslationPending e.pdf", "translation pending"),
    "41160": ("TranslationPending f.pdf", "translation pending"),
    "40940": ("Paper Only", "paper only"),
}
# What a stand-in keeps of each listing: the document it stands in for.
LISTING = (("title", "title"), ("date", "date"), ("filing", "filing.number"),
           ("company", "company.name"), ("submitter", "submitter"))


def listing_from(obs: dict) -> dict:
    out = {}
    for name, path in LISTING:
        value = obs
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value not in (None, ""):
            out[name] = value
    return out


def merge(old: dict | None, doc_id: str, obs: dict, when: str, *,
          facets: dict[str, list[str]] | None = None, final=False,
          containers: list[dict] = ()) -> tuple[dict, list[dict]]:
    """Fold one observation into a record. -> (record, changes)

    The rules exist so that a scrape that partly failed cannot erase anything:

    - A field is only ever replaced by a non-empty value.
    - A facet category's labels replace the old ones only when `final` -- every
      search in that category finished; otherwise values only grow. A category
      observed more recently than `when` is left alone.
    - Container memberships are only ever added.
    """
    rec = dict(old) if old else {
        "cer_schema": CER_SCHEMA, "doc_id": doc_id,
        "first_seen_at": when, "download": None, "changes": [],
    }
    changes: list[dict] = []
    stand_in = STAND_INS.get(doc_id)
    if stand_in:
        # Its fields are filled once, never replaced: each listing describes
        # another document, and is kept as a listing instead.
        listing = listing_from(obs)
        listings = rec.setdefault("listings", [])
        if listing and listing not in listings:
            listings.append(listing)
        obs = {k: v for k, v in obs.items() if not rec.get(k)}
        obs.update(title=stand_in[0])
        rec["stand_in"] = stand_in[1]

    def note(field: str, before, after) -> None:
        if before != after and not stand_in:
            changes.append({"field": field, "old": before, "new": after, "observed_at": when})

    for key, value in obs.items():
        if isinstance(value, dict) and key != "identifiers":
            cur = dict(rec.get(key) or {})
            for sub, v in value.items():
                if v not in (None, "", [], {}):
                    note(f"{key}.{sub}", cur.get(sub), v)
                    cur[sub] = v
            rec[key] = cur
        elif value not in (None, "", [], {}):
            note(key, rec.get(key), value)
            rec[key] = value

    if facets:
        cur_f = {k: list(v) for k, v in (rec.get("facets") or {}).items()}
        done_f = dict(rec.get("facets_complete") or {})
        observed_f = dict(rec.get("facet_observed_at") or {})
        for category, labels in facets.items():
            if observed_f.get(category, "") > when:
                continue
            before = cur_f.get(category, [])
            after = list(labels) if final else before + [v for v in labels if v not in before]
            note(f"facets.{category}", before, after)
            cur_f[category] = after
            done_f[category] = final or done_f.get(category, False)
            observed_f[category] = when
        rec["facets"], rec["facets_complete"], rec["facet_observed_at"] = cur_f, done_f, observed_f

    cur_c = list(rec.get("containers") or [])
    have = {c.get("id") for c in cur_c}
    new_c = cur_c + [c for c in containers if c["id"] not in have]
    note("containers", cur_c, new_c)
    rec["containers"] = new_c

    rec["last_seen_at"] = max(rec.get("last_seen_at") or "", when)
    if old is None:
        changes = []  # a first sighting is a creation, not a change
    rec["changes"] = (rec.get("changes") or []) + changes
    return rec, changes


def summarize(rec: dict | None):
    """What progress reports need from a record: (date, kind, facets complete)."""
    if rec is None:
        return None
    done = rec.get("facets_complete") or {}
    return rec.get("date"), rec.get("kind"), all(done.get(c, False) for c in FACET_CATEGORIES)


# --------------------------------------------------------------------------
# the resumable crawl

class Crawl:
    """One date range's checkpoints: run.json plus one JSON file per job.

    A job is one search (an inventory window, or one facet value over a year)
    or one container's member list, with the cursor it has reached.
    """

    def __init__(self, path: Path, config: dict) -> None:
        """Open saved state read-only; Crawl.open creates or resumes a run."""
        self.path, self.config = Path(path), config
        self.jobs_dir = self.path / config.get("jobs_directory", "jobs")
        self.jobs: dict[str, dict] = {}
        for p in self.jobs_dir.glob("*.json"):
            job = json.loads(p.read_text())
            self.jobs[job["key"]] = job
        self.reuse_containers = True
        self.container_cache = None
        self.summaries: dict[str, tuple | None] = {}

    @classmethod
    def load(cls, path: Path) -> "Crawl":
        return cls(path, json.loads((Path(path) / "run.json").read_text()))

    @classmethod
    def open(cls, path: Path, start: str, end: str, refresh=False) -> "Crawl":
        path = Path(path)
        old = json.loads((path / "run.json").read_text()) if (path / "run.json").exists() else None
        if old and old.get("range") != [start, end]:
            raise UsageError(f"{path} holds {old.get('range')}; use another --state directory")
        config = old
        if old is None or refresh:
            config = {"range": [start, end], "created_at": now(), "catalog": None,
                      "applied": {}, "requests": 0, "elapsed_seconds": 0,
                      "jobs_directory": "jobs-" + uuid4().hex[:12]}
            if old:
                # The old checkpoints stay until the fresh crawl completes.
                config["replaced_jobs_directories"] = (
                    old.get("replaced_jobs_directories", []) + [old.get("jobs_directory", "jobs")])
        crawl = cls(path, config)
        crawl.reuse_containers = not refresh
        crawl.jobs_dir.mkdir(parents=True, exist_ok=True)
        crawl.save_config()
        for lo, hi in windows(start, end):
            crawl.enqueue(f"inventory:{lo}:{hi}", "inventory", {"sd": lo, "ed": hi})
        return crawl

    def save_config(self) -> None:
        write_json(self.path / "run.json", self.config)

    def drop_replaced(self) -> None:
        """Delete the checkpoints a --refresh replaced, once it has completed."""
        replaced = self.config.pop("replaced_jobs_directories", [])
        if replaced:
            self.save_config()
            for name in replaced:
                if name.startswith("jobs") and "/" not in name:
                    shutil.rmtree(self.path / name, ignore_errors=True)

    # jobs ------------------------------------------------------------------

    def job_path(self, key: str) -> Path:
        return self.jobs_dir / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def save_job(self, job: dict) -> None:
        write_json(self.job_path(job["key"]), job)
        self.jobs[job["key"]] = copy.deepcopy(job)

    def listed(self, stage=None, pending=False) -> list[dict]:
        """Copies of the saved jobs, in key order; edit them, then save_job."""
        return [copy.deepcopy(j) for _, j in sorted(self.jobs.items())
                if (stage is None or j["stage"] == stage) and (not pending or j["status"] == "pending")]

    def enqueue(self, key: str, stage: str, payload: dict, offset=1) -> None:
        if key in self.jobs:
            return
        self.save_job({"key": key, "stage": stage, "payload": payload, "offset": offset,
                       "url": None, "status": "pending", "error": None,
                       "pages": 0, "ids": [], "visited": [], "observations": {}})
        if stage == "containers" and self.reuse_containers and self.container_cache is not None:
            self.reuse_container(self.jobs[key])

    def observed(self) -> set[str]:
        return {i for j in self.jobs.values() for i in j["ids"]}

    # reusing containers read by other ranges ------------------------------

    def load_container_cache(self) -> None:
        """Index other ranges' container checkpoints, once per command.

        Clean completed reads are reusable; recorded gaps for GAP_REUSE_DAYS.
        Every payload is kept, so a reused parent's children can be enqueued.
        """
        if self.container_cache is not None:
            return
        payloads, completed = {}, {}
        cutoff = (datetime.now(timezone.utc) - timedelta(days=GAP_REUSE_DAYS)).isoformat()
        for config_path in STATE_ROOT.glob("*/run.json"):
            if config_path.parent.resolve() == self.path.resolve():
                continue
            try:
                other = Crawl.load(config_path.parent)
            except (OSError, ValueError, KeyError):
                continue  # a state file being written, or obsolete: not a safe source
            for key, job in other.jobs.items():
                if job.get("stage") != "containers" or not isinstance(job.get("payload"), dict):
                    continue
                payloads.setdefault(key, job["payload"])
                gaps = job.get("gaps") or []
                clean_read = not gaps and not job.get("short")
                fresh_gap = bool(gaps) and all(g.get("container") for g in gaps) and \
                    max(g.get("found_at") or "" for g in gaps) >= cutoff
                if job.get("status") != "done" or not (clean_read or fresh_gap):
                    continue
                rank = (clean_read, job.get("observed_at") or job.get("last_successful_request_at") or "")
                if key not in completed or rank > completed[key][0]:
                    completed[key] = (rank, job)
        self.container_cache = {"payloads": payloads,
                                "completed": {k: job for k, (_, job) in completed.items()}}

    def reuse_container(self, job: dict) -> bool:
        """Replace one queued container with a cached read, children included."""
        cached = self.container_cache["completed"].get(job["key"])
        if cached is None:
            return False
        self.save_job({**cached, "payload": job["payload"], "error": None,
                       "reused_at": now(), "reused_from": "another saved range"})
        for doc_id in cached.get("ids", []):
            child = "container:" + doc_id
            if child in self.container_cache["payloads"]:
                self.enqueue(child, "containers", self.container_cache["payloads"][child])
        return True

    def reuse_pending_containers(self) -> int:
        if not self.reuse_containers:
            return 0
        self.load_container_cache()
        # A job may already have been reused as another one's child.
        return sum(self.reuse_container(job) for job in self.listed("containers", pending=True)
                   if self.jobs[job["key"]]["status"] == "pending")

    # records ---------------------------------------------------------------

    def summary(self, doc_id: str):
        if doc_id not in self.summaries:
            self.summaries[doc_id] = summarize(read_record(doc_id)) if doc_id.isdigit() else None
        return self.summaries[doc_id]

    def write_record(self, doc_id: str, rec: dict) -> None:
        path = record_path(doc_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, rec)
        self.summaries[doc_id] = summarize(rec)

    def record(self, row: Row, parent: Row | None = None, facet=None, observed_at=None) -> None:
        """Write metadata before advancing its page checkpoint; replay is safe."""
        if is_container_kind(row.kind) and not row.synthetic:
            self.enqueue("container:" + row.document_id, "containers", asdict(row))
        if not row.recordable:
            return
        refs = [{"id": parent.document_id, "kind": parent.kind, "title": parent.name}] if parent else []
        rec, _ = merge(read_record(row.document_id), row.document_id, record_from_row(row),
                       observed_at or now(), facets={facet[0]: [facet[1]]} if facet else None,
                       containers=refs)
        self.write_record(row.document_id, rec)

    # pages -----------------------------------------------------------------

    def prepare_facets(self, http: Http) -> None:
        catalog = self.config["catalog"]
        if catalog is None:
            ok, html, _, err = http.get(ADVANCED_URL)
            catalog = facet_catalog(parse_html(html)) if ok else {}
            if set(catalog) != set(FACET_CATEGORIES):
                raise Incomplete(f"incomplete facet catalog: {err or list(catalog)}")
            self.config["catalog"] = catalog
            self.save_config()
        lo, hi = self.config["range"]
        for start, end in windows(lo, hi, yearly=True):
            for category, options in catalog.items():
                for filter_id, label in options.items():
                    self.enqueue(f"facet:{start}:{end}:{filter_id}", "facets",
                                 {"sd": start, "ed": end, "rds": filter_id,
                                  "category": category, "label": label, "window": [start, end]})

    def split(self, job: dict) -> None:
        """Replace a search over several days with one per half of its window.
        A facet half keeps its original window, which finish_facets groups by."""
        p = job["payload"]
        lo, hi = date.fromisoformat(p["sd"]), date.fromisoformat(p["ed"])
        mid = lo + (hi - lo) // 2
        prefix = job["key"].split(":")[0]
        suffix = f":{p['rds']}" if "rds" in p else ""
        for start, end in ((lo, mid), (mid + timedelta(days=1), hi)):
            self.enqueue(f"{prefix}:{start}:{end}{suffix}", job["stage"],
                         {**p, "sd": start.isoformat(), "ed": end.isoformat()})
        job["status"] = "split"

    def search_page(self, http: Http, job: dict) -> None:
        p = job["payload"]
        size = p.get("page_size", PAGE_SIZE)
        params = {k: p[k] for k in ("sd", "ed", "rds") if k in p}
        params.update(sr=job["offset"], srt=SORT_OLDEST_FIRST)
        ok, html, _, err = http.get(RESULTS_URL, params=params, ajax=True, page_size=size)
        if ok and regdocs_error(html):
            self.narrow(job, size)
            return
        page = parse_html(html)
        if not ok or not recognized(html, page):
            raise Incomplete(err or "unrecognized search response")
        rows = parse_rows(page)
        total = parse_total(html)
        if job["offset"] == 1 and total is not None and total > SPLIT_ABOVE and p["sd"] < p["ed"]:
            self.split(job)
            return
        if not rows and total != 0 and not explicit_empty(page):
            raise Incomplete("empty search page without an explicit empty result")
        if any(r.date is None or not p["sd"] <= r.date <= p["ed"] for r in rows):
            # REGDOCS silently ignores a range it cannot parse.
            raise Incomplete("search returned a missing or out-of-range date; cursor retained")
        ids = set(job["ids"])
        if rows and all(row.document_id in ids for row in rows):
            raise Incomplete("repeated search page; cursor retained")
        observed_at = now()
        facet = (p["category"], p["label"]) if job["stage"] == "facets" else None
        for row in rows:
            self.record(row, facet=facet, observed_at=observed_at)
            if facet:
                job["observations"][row.document_id] = observed_at
            ids.add(row.document_id)
        job.update(observed_at=observed_at, ids=sorted(ids), offset=job["offset"] + size,
                   pages=job["pages"] + 1)
        # Totals are approximate; a short page ends pagination. A job narrowed
        # to a block of results also ends at that block's last one.
        last = p.get("last")
        job["status"] = "done" if len(rows) < size or (last and job["offset"] > last) else "pending"

    def narrow(self, job: dict, size: int) -> None:
        """Work around a page REGDOCS fails to render, without losing the rest.

        A window longer than a day is halved, down to single days. A day goes
        on from the same result in pages of 20; a page of 20 that fails is read
        one result at a time in a job of its own, while the day moves on; a
        single result that still fails is a gap -- recorded, printed, and not
        held against the range.
        """
        p = job["payload"]
        where = f"{p['sd']}..{p['ed']}{' ' + p['label'] if 'label' in p else ''}"
        if p["sd"] < p["ed"]:
            self.split(job)
            print(f"  REGDOCS error on {where} at result {job['offset']}; splitting the window", flush=True)
            return
        if size == 1:
            job.setdefault("gaps", []).append({"date": p["sd"], "result": job["offset"],
                                               "filter": p.get("label"), "found_at": now()})
            print(f"  GAP: REGDOCS cannot list result {job['offset']} of {where}; "
                  "recorded, and the range goes on", flush=True)
            job["offset"] += 1
            job["status"] = "done" if p.get("last") and job["offset"] > p["last"] else "pending"
            return
        smaller = NARROWER[size]
        if smaller == 1:
            first, last = job["offset"], job["offset"] + size - 1
            self.enqueue(f"{job['key']}@{first}-{last}", job["stage"],
                         {**p, "page_size": 1, "last": last}, offset=first)
            job["offset"] += size
            print(f"  REGDOCS error on {where}, results {first}-{last}; reading those one at a time",
                  flush=True)
        else:
            job["payload"] = {**p, "page_size": smaller}
            print(f"  REGDOCS error on {where} at result {job['offset']}; going on in pages of {smaller}",
                  flush=True)
        job["status"] = "pending"

    def container_page(self, http: Http, job: dict) -> None:
        parent = Row(**job["payload"])
        box_id = parent.document_id
        shell = VIEW_URL.format(id=box_id)
        url = job["url"] or MEMBERS_URL.format(id=box_id)
        ok, html, final, err = http.get(url, ajax=True, referer=shell)
        page = parse_html(html)
        if job["url"] is None and not (ok and recognized(html, page)):
            # The usual member-list URL did not answer: ask the container's
            # own page which one it declares.
            ok, shell_html, shell_final, shell_err = http.get(shell)
            shell_page = parse_html(shell_html)
            endpoint = container_endpoint(shell_page, shell_final or shell, box_id) if ok else None
            if not endpoint and ok and regdocs_unavailable(shell_page):
                observed_at = now()
                job.setdefault("gaps", []).append({"container": box_id, "reason": "unavailable",
                                                   "url": shell_final or shell, "found_at": observed_at})
                job.update(status="done", observed_at=observed_at)
                print(f"  GAP: container {box_id}: REGDOCS says it cannot locate this item; "
                      "recorded, and the range goes on", flush=True)
                return
            if not endpoint:
                raise Incomplete(shell_err or err or "no container member-list endpoint")
            job["url"] = endpoint
            return
        if not ok or not recognized(html, page):
            raise Incomplete(err or "unrecognized container response")
        rows = parse_rows(page, container_id=box_id)
        total = parse_total(html)
        if not rows and total is None and not explicit_empty(page):
            raise Incomplete("empty container page without an explicit empty result")
        visited = job["visited"] + [url]
        nxt = next_container_page(page, final or url)
        if nxt and nxt in visited:
            raise Incomplete("container pagination loop")
        ids = set(job["ids"])
        members = [r for r in rows if r.document_id != box_id]
        if members and all(r.document_id in ids for r in members):
            raise Incomplete("container page added no members")
        observed_at = now()
        for row in members:
            self.record(row, parent=parent, observed_at=observed_at)
            ids.add(row.document_id)
        # The members are recorded even if the last page proves the list short.
        if not nxt and ((total is None and rows) or (total is not None and len(ids) < total)):
            short = {"observed": len(ids), "reported": total}
            seen = job.get("short") or {}
            times = seen["times"] + 1 if {k: seen.get(k) for k in short} == short else 1
            if times < SHORT_CONTAINER_TRIES:
                raise Incomplete(f"container incomplete: observed {len(ids)}, reported {total} "
                                 f"({times} of {SHORT_CONTAINER_TRIES} runs)",
                                 keep={"short": {**short, "times": times}})
            job.setdefault("gaps", []).append({"container": box_id, **short, "found_at": observed_at})
            print(f"  GAP: container {box_id} lists {len(ids)} of a reported {total} on "
                  f"{times} runs; recorded, and the range goes on", flush=True)
        job.update(ids=sorted(ids), visited=visited, observed_at=observed_at, url=nxt or url,
                   status="pending" if nxt else "done", pages=job["pages"] + 1)

    def finish_facets(self) -> None:
        """Apply every finished facet category, in one write per document.

        A category's labels replace a document's old ones only once every
        search in it, over that window, is done. Each label is stamped with
        when its search ran, so an older checkpoint cannot overwrite a newer
        observation.
        """
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for job in self.jobs.values():
            if job["stage"] == "facets" and job["status"] != "split":
                p = job["payload"]
                groups[(*p["window"], p["category"])].append(job)
        observed = sorted(self.observed())
        updates: dict[str, list] = defaultdict(list)
        applied = {}
        for (lo, hi, category), jobs in groups.items():
            if any(j["status"] != "done" for j in jobs):
                continue
            eligible = [d for d in observed if (sm := self.summary(d))
                        and is_pdf_kind(sm[1]) and sm[0] and lo <= sm[0] <= hi]
            key = json.dumps([lo, hi, category])
            signature = hashlib.sha256(json.dumps(eligible).encode()).hexdigest()
            if self.config["applied"].get(key) == signature:
                continue
            labels, times = defaultdict(list), {}
            completed_at = max((j.get("observed_at") or "" for j in jobs), default="") or now()
            for job in jobs:
                for doc_id in job["ids"]:
                    labels[doc_id].append(job["payload"]["label"])
                    times[doc_id] = max(times.get(doc_id, ""),
                                        job["observations"].get(doc_id) or job.get("observed_at") or "")
            for doc_id in eligible:
                updates[doc_id].append((category, labels.get(doc_id, []),
                                        times.get(doc_id) or completed_at))
            applied[key] = signature
        for doc_id, categories in updates.items():
            old = rec = read_record(doc_id)
            for category, found, observed_at in categories:
                rec, _ = merge(rec, doc_id, {}, observed_at, facets={category: found}, final=True)
            if rec != old:
                self.write_record(doc_id, rec)
        # A crash during the writes replays the group; merges are idempotent.
        if applied:
            self.config["applied"].update(applied)
            self.save_config()

    # running ---------------------------------------------------------------

    def run(self, http: Http, operation: str, max_requests=0) -> None:
        """Work through the operation's stages until done, or max_requests."""
        initial, started = http.requests, time.monotonic()
        reported = initial
        stages = STAGES[operation]

        def spent():
            return max_requests and http.requests - initial >= max_requests

        for job in self.listed():
            if job["status"] == "failed" and job["stage"] in stages:
                job["status"] = "pending"
                self.save_job(job)
        try:
            for stage in stages:
                if spent():
                    break
                if stage == "containers" and (reused := self.reuse_pending_containers()):
                    print(f"  reused {reused} container(s) read by other saved ranges", flush=True)
                if stage == "facets":
                    self.prepare_facets(http)
                while not spent() and (batch := self.listed(stage, pending=True)):
                    for job in batch:
                        while job["status"] == "pending" and not spent():
                            try:
                                (self.container_page if stage == "containers" else self.search_page)(http, job)
                            except (Incomplete, SearchRejected, TooManyFailures) as exc:
                                # Back to the last saved cursor, not a half-changed job.
                                job = copy.deepcopy(self.jobs[job["key"]])
                                job.update(status="failed", error=str(exc), **getattr(exc, "keep", {}))
                                self.save_job(job)
                                print(f"FAILED {job['key']}: {exc}", flush=True)
                                if isinstance(exc, TooManyFailures):
                                    raise
                                break
                            job.update(error=None, last_successful_request_at=now())
                            self.config["last_successful_request_at"] = job["last_successful_request_at"]
                            self.save_job(job)
                            if http.requests - reported >= 100:
                                reported = http.requests
                                lo, hi = self.config["range"]
                                print(f"  {lo}..{hi} {stage}: {http.requests - initial} requests so far",
                                      flush=True)
                        if spent():
                            break
            self.finish_facets()
        finally:
            self.config["requests"] += http.requests - initial
            self.config["elapsed_seconds"] += time.monotonic() - started
            self.save_config()

    # reporting -------------------------------------------------------------

    def pdfs(self) -> dict[str, tuple]:
        """Every PDF this crawl has seen -> its summary."""
        return {d: sm for d in self.observed() if (sm := self.summary(d)) and is_pdf_kind(sm[1])}

    def status(self) -> None:
        tally = defaultdict(int)
        for job in self.jobs.values():
            tally[(job["stage"], job["status"])] += 1
        for (stage, state), count in sorted(tally.items()):
            print(f"{stage:12} {state:8} {count}")
        lo, hi = self.config["range"]
        pdfs = self.pdfs()
        in_range = {d: sm for d, sm in pdfs.items() if sm[0] and lo <= sm[0] <= hi}
        missing = sum(not (SOURCE / f"{d}.pdf").exists() for d in pdfs)
        print(f"Discovered PDFs: {len(pdfs)} ({len(in_range)} dated in range; "
              f"{len(pdfs) - len(in_range)} linked outside/undated)")
        # Outside-range PDFs get their facets from a crawl of their own dates.
        print(f"Missing downloads: {missing}; in range with incomplete facets: "
              f"{sum(not sm[2] for sm in in_range.values())}")
        failures = [j for _, j in sorted(self.jobs.items()) if j["status"] == "failed"]
        print(f"Failed jobs: {len(failures)}")
        gaps = [g for j in self.jobs.values() for g in j.get("gaps", [])]
        if gaps:
            print(f"Results REGDOCS cannot list (gaps): {len(gaps)}")
            for g in gaps[:20]:
                if g.get("reason") == "unavailable":
                    print(f"  container {g['container']}: REGDOCS cannot locate this item")
                elif "container" in g:
                    print(f"  container {g['container']}: {g['observed']} of {g['reported']} listed")
                else:
                    print(f"  {g['date']} result {g['result']}{' (' + g['filter'] + ')' if g['filter'] else ''}")
        last = max([self.config.get("last_successful_request_at") or ""]
                   + [j.get("last_successful_request_at") or j.get("observed_at") or ""
                      for j in self.jobs.values()])
        print(f"Last successful request: {last or 'none recorded'}")
        requests, elapsed = self.config["requests"], self.config["elapsed_seconds"]
        print(f"Recorded requests: {requests}; elapsed: {elapsed / 3600:.2f} hours"
              + (f"; {requests / elapsed * 3600:.0f} requests/hour" if requests and elapsed else ""))
        for job in failures[:10]:
            print(f"  {job['key']}: {job['error']}")


# enrich's last pass drains containers that the facet searches found.
STAGES = {"discover": ["inventory"], "enrich": ["inventory", "containers", "facets", "containers"],
          "inventory": ["inventory"], "containers": ["containers"], "facets": ["facets"]}


def crawl_range(http: Http, start: str, end: str, operation: str, *, state=None,
                refresh=False, max_requests=0) -> int:
    """Run (or resume) one date range, and print one line saying how it went."""
    crawl = Crawl.open(Path(state) if state else STATE_ROOT / f"{start}_{end}", start, end, refresh)
    crawl.run(http, operation, max_requests)
    stages = STAGES[operation]
    jobs = [j for j in crawl.jobs.values() if j["stage"] in stages]
    failed = sum(j["status"] == "failed" for j in jobs)
    left = sum(j["status"] == "pending" for j in jobs)
    if "facets" in stages and crawl.config["catalog"] is None:
        left += 1  # the facet searches have not been listed yet
    outcome = f"unfinished ({failed} failed, {left} left); rerun to resume" if failed or left else "done"
    print(f"{operation} {start}..{end}: {len(crawl.pdfs())} PDFs, {http.requests} requests, {outcome}",
          flush=True)
    if failed or left:
        return 1
    crawl.drop_replaced()
    return 0


# --------------------------------------------------------------------------
# commands

def cmd_crawl(args) -> int:
    try:
        start = parse_day(args.date_from)
        end = parse_day(args.date_to) if args.date_to else date.today().isoformat()
    except ValueError as exc:
        raise UsageError(exc) from None
    if start > end or args.max_requests < 0:
        raise UsageError("invalid date range or request limit")
    http = Http(min_delay=args.min_delay, max_delay=args.max_delay)
    try:
        return crawl_range(http, start, end, args.cmd, state=args.state,
                           refresh=args.refresh, max_requests=args.max_requests)
    finally:
        http.close()


def cmd_status(args) -> int:
    """Saved progress, read without writing anything or contacting REGDOCS."""
    paths = [Path(args.state)] if args.state else sorted(p.parent for p in STATE_ROOT.glob("*/run.json"))
    if not paths:
        print("No saved crawls yet. Start with: discover --from YYYY-MM-DD --to YYYY-MM-DD")
        return 0
    if any(not (p / "run.json").exists() for p in paths):
        print("No crawl state at the requested directory")
        return 1
    for path in paths:
        crawl = Crawl.load(path)
        print(f"{crawl.config['range'][0]} .. {crawl.config['range'][1]} ({path})")
        crawl.status()
    return 0


def member_rows(http: Http, box_id: str):
    """Every row of one container's member list, page by page."""
    url, seen = MEMBERS_URL.format(id=box_id), set()
    while url and url not in seen:
        seen.add(url)
        ok, html, final, err = http.get(url, ajax=True, referer=VIEW_URL.format(id=box_id))
        page = parse_html(html)
        if not ok or not recognized(html, page):
            raise Incomplete(err or "unrecognized member list")
        yield from parse_rows(page, container_id=box_id)
        url = next_container_page(page, final or url)


def find_date(http: Http, doc_id: str) -> tuple[str | None, str]:
    """A document's filing date. Its own page does not state it, so the date is
    read from its row in its filing's member list, found through the
    breadcrumb. -> (date or None, how it was found or why not)"""
    ok, html, _, err = http.get(VIEW_URL.format(id=doc_id))
    if not ok:
        return None, f"its page is unavailable ({err})"
    parent = breadcrumb_parent(parse_html(html), doc_id)
    if not parent:
        return None, "its page names no filing"
    try:
        row = next((r for r in member_rows(http, parent) if r.document_id == doc_id), None)
    except (Incomplete, SearchRejected) as exc:
        return None, f"its filing {parent}: {exc}"
    if row is None or not row.date:
        return None, f"its filing {parent} does not list it with a date"
    if not is_pdf_kind(row.kind):
        return None, f"it is {row.kind or 'of unknown kind'}, not a PDF"
    return row.date, f"from filing {parent}"


def record_documents(ids: list[str]) -> int:
    """Give specific documents a record: find each one's date, enrich those days."""
    if bad := [i for i in ids if not i.isdigit()]:
        raise UsageError(f"not REGDOCS document ids: {', '.join(bad)}")
    http = Http()
    days: dict[str, list[str]] = defaultdict(list)
    unplaced: dict[str, str] = {}
    code = 0
    try:
        for doc_id in ids:
            day, how = find_date(http, doc_id)
            print(f"  {doc_id}: {day or 'NOT PLACED'} -- {how}", flush=True)
            if day:
                days[day].append(doc_id)
            else:
                unplaced[doc_id] = how
        for day in sorted(days):
            code = max(code, crawl_range(http, day, day, "enrich"))
    finally:
        http.close()
    missed = [d for group in days.values() for d in group if read_record(d) is None]
    print(f"\nrecorded {len(ids) - len(unplaced) - len(missed)} of {len(ids)}")
    for doc_id, why in unplaced.items():
        print(f"  {doc_id}: not placed -- {why}; enrich a date range that includes it")
    for doc_id in missed:
        print(f"  {doc_id}: not in its day's search results")
    return max(code, 1 if unplaced or missed else 0)


def cmd_doc(args) -> int:
    return record_documents(args.ids)


def cmd_missing(args) -> int:
    """Every PDF in source/ that has no record, e.g. one fetched by hand."""
    recorded = {e.name for e in os.scandir(OUT) if e.is_dir()} if OUT.is_dir() else set()
    ids = sorted((p.stem for p in SOURCE.glob("*.pdf") if p.stem.isdigit() and p.stem not in recorded),
                 key=int)
    print(f"{len(ids)} PDF(s) in source/ without a record")
    return record_documents(ids[:args.limit] if args.limit else ids) if ids else 0


class NotPdf(Refused):
    """REGDOCS answered a file link with something else, usually an HTML page."""


HTML_ITEM = "served as HTML, not a PDF"


def download(http: Http, rec: dict) -> tuple[str | None, bool]:
    """Fetch one PDF into source/. -> (error or None, whether REGDOCS answered
    about this file alone -- a status not worth retrying, or not a PDF)."""
    doc_id = rec["doc_id"]
    tmp, dest = SOURCE / f"{doc_id}.pdf.part", SOURCE / f"{doc_id}.pdf"

    def send():
        digest, first = hashlib.sha256(), b""
        try:
            with http.client.stream("GET", rec["url"], headers=HEADERS) as r:
                http.check(r)
                with tmp.open("wb") as fh:
                    for chunk in r.iter_bytes(1 << 20):
                        if len(first) < 512:
                            first += chunk[:512 - len(first)]
                        digest.update(chunk)
                        fh.write(chunk)
                head = first.lstrip().lower()
                if head.startswith((b"<!doctype html", b"<html")):
                    # REGDOCS serves some "PDF Document" items (2023 filing receipts) as an
                    # HTML page: not a PDF, so excluded rather than retried.
                    raise NotPdf(HTML_ITEM, retryable=False)
                if not first.startswith(b"%PDF-"):
                    raise NotPdf(f"expected a PDF, got {r.headers.get('content-type') or 'something else'}",
                                 retryable=True)
                return {"sha256": digest.hexdigest(), "size_bytes": tmp.stat().st_size,
                        "content_type": "application/pdf", "extension": ".pdf",
                        "resolved_url": str(r.url), "downloaded_at": now(),
                        "sha256_matches_file": True}
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    SOURCE.mkdir(parents=True, exist_ok=True)
    result, exc = http.attempt(send)
    if exc:
        return describe(exc), isinstance(exc, NotPdf) or not getattr(exc, "retryable", True)
    # The record first: a crash before the rename leaves no PDF, so the next
    # run downloads it again and rewrites the record.
    rec["download"] = result
    write_json(record_path(doc_id), rec)
    tmp.replace(dest)
    return None, False


def cmd_download(args) -> int:
    """Fetch every recorded PDF not already in source/. Existing files are left alone."""
    have = {n[:-4] for n in os.listdir(SOURCE) if n.endswith(".pdf")} if SOURCE.is_dir() else set()
    # Newest first: an old record without its PDF is usually one REGDOCS no longer has.
    names = sorted((e.name for e in os.scandir(OUT) if e.is_dir() and e.name.isdigit()
                    and e.name not in have), key=int, reverse=True) if OUT.is_dir() else []
    todo = [rec for n in names if (rec := read_record(n)) and is_pdf_kind(rec.get("kind"))
            and "/File/Download/" in (rec.get("url") or "")
            and not (rec.get("download") or {}).get("excluded")
            and (args.retry_unavailable or not (rec.get("download") or {}).get("unavailable"))]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} file(s) to download", flush=True)
    http = Http(min_delay=args.min_delay, max_delay=args.max_delay)
    ok_n = fail_n = unavailable_in_row = 0
    try:
        for rec in todo:
            error, unavailable = download(http, rec)
            if error is None:
                ok_n += 1
                unavailable_in_row = 0
                print(f"  {rec['doc_id']}: {rec['download']['size_bytes']:,} bytes", flush=True)
                continue
            fail_n += 1
            print(f"  {rec['doc_id']}: FAILED {error}", flush=True)
            if error == HTML_ITEM:
                # An HTML item is excluded for good: later runs skip it, --retry-unavailable too.
                rec["download"] = {**(rec.get("download") or {}), "excluded": error, "checked_at": now()}
                write_json(record_path(rec["doc_id"]), rec)
            elif error.startswith("HTTP 4"):
                # REGDOCS says this file is not there: noted, so later runs skip it.
                rec["download"] = {**(rec.get("download") or {}), "unavailable": error, "checked_at": now()}
                write_json(record_path(rec["doc_id"]), rec)
            if unavailable:
                unavailable_in_row += 1
                if unavailable_in_row >= MAX_UNAVAILABLE_IN_ROW:
                    raise TooManyFailures(f"{unavailable_in_row} files in a row unavailable "
                                          f"({error}); have the download URLs changed?")
            else:
                unavailable_in_row = 0
                http.gave_up(error)
    except TooManyFailures as exc:
        print(f"\nSTOPPED: {exc}\nfiles downloaded before the stop are kept; re-run the same command")
        fail_n += 1
    finally:
        http.close()
    print(f"\ndownloaded {ok_n}, failed {fail_n}")
    return 1 if fail_n else 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split("\n\n", 1)[1])
    sub = p.add_subparsers(dest="cmd", required=True)

    def pacing(cmd):
        cmd.add_argument("--min-delay", type=float, default=MIN_DELAY,
                         help=f"minimum seconds between requests (default: {MIN_DELAY})")
        cmd.add_argument("--max-delay", type=float, default=MAX_DELAY,
                         help=f"maximum seconds between requests (default: {MAX_DELAY})")

    for name, help_text in (
            ("discover", "find PDFs in a date range and record them"),
            ("enrich", "discover, then add filing memberships and facets")):
        c = sub.add_parser(name, help=help_text)
        c.add_argument("--from", dest="date_from", required=True, help="inclusive YYYY-MM-DD")
        c.add_argument("--to", dest="date_to", help="inclusive YYYY-MM-DD (default: today)")
        c.add_argument("--refresh", action="store_true", help="start the range over instead of resuming")
        c.add_argument("--max-requests", type=int, default=0,
                       help="pause after about this many requests (default: no limit)")
        c.add_argument("--state", help="checkpoint directory (default: scout-state/<from>_<to>)")
        pacing(c)
    c = sub.add_parser("status", help="show saved crawl progress, offline")
    c.add_argument("--state", help="one checkpoint directory instead of every saved range")
    c = sub.add_parser("download", help="fetch recorded PDFs not already in source/")
    c.add_argument("--limit", type=int)
    c.add_argument("--retry-unavailable", action="store_true",
                   help="also try files REGDOCS answered 404 (or another 4xx) for before")
    pacing(c)
    c = sub.add_parser("doc", help="record specific documents by id")
    c.add_argument("ids", nargs="+")
    c = sub.add_parser("missing", help="record every PDF in source/ that has no record")
    c.add_argument("--limit", type=int)
    return p


COMMANDS = {"discover": cmd_crawl, "enrich": cmd_crawl, "download": cmd_download,
            "doc": cmd_doc, "missing": cmd_missing}


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.cmd == "status":
            return cmd_status(args)
        # Every command that writes records takes the same lock.
        with exclusive(OUT / ".scout-writer.lock"):
            return COMMANDS[args.cmd](args)
    except UsageError as exc:
        print(f"refusing: {exc}")
        return 2
    except (Incomplete, TooManyFailures, SearchRejected) as exc:
        print(f"STOPPED: {exc}; saved checkpoints are kept, rerun to resume")
        return 1
    except KeyboardInterrupt:
        print("\npaused; rerun the same command to resume")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
