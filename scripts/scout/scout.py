#!/usr/bin/env python3
"""Find REGDOCS filings, record what REGDOCS says about them, and fetch them.

    python scripts/scout/scout.py discover --from 2026-01-01 --to 2026-07-31
    python scripts/scout/scout.py enrich --from 2026-01-01 --to 2026-07-31
    python scripts/scout/scout.py status
    python scripts/scout/scout.py doc 4647200 [4692360 ...] [--dry-run]
    python scripts/scout/scout.py missing [--dry-run]
    python scripts/scout/scout.py download [--limit N]
    python scripts/scout/scout.py seed ../cer-regdocs2/workspace/2_download/files

State is files, not a database. For every PDF document:

    output/<id>/<id>.cer.meta.json.gz  what REGDOCS says about it, when it said it,
                                     and what changed since
    source/<id>.pdf                  the file itself, once downloaded

That record is the only one: the scout writes it, the download step adds the
file's hash to it, and nothing else holds a copy. A record with no PDF in
source/ is a document still to download; a document whose PDF exists is never
downloaded again. Repeating discover or enrich resumes where it stopped;
--refresh re-checks a range that has already finished.

Only PDF documents are recorded. REGDOCS also lists HTML documents, Folders
and Compound Documents; the containers are walked to learn which filings a PDF
belongs to, but none of them gets a record, because nothing downstream reads
them.

This is a port of the REGDOCS logic in cer-regdocs2's stage 1 and 2 scripts,
without their SQLite state. Three things were deliberately left behind:

- **Detail pages.** One request per document, about seven hours for eight
  thousand filings, and they yield a title the search row already gives and a
  `language` field that reads "en" on every filing, French ones included.
- **Raw HTML snapshots.** The records here say what was observed; the pages it
  was observed on are not kept.
- **Concurrency.** Requests are paced at one every 2-4 seconds regardless, the
  same courtesy the original extended to REGDOCS, so parallelism would only
  queue behind the pacer.

The record lives in output/<id>/ rather than beside the PDF so that source/
holds nothing but the documents themselves, and so ingest.py -- which reads a
`<pdf>.metadata.json` sidecar automatically -- never picks it up by accident.
"""
from __future__ import annotations

import argparse
import calendar
import gzip
import hashlib
import json
import random
import re
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag

CER_SCHEMA = 4
# Keep generated corpus data at the repository root even though the scout's
# implementation lives under scripts/scout/.
ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "source"
OUT = ROOT / "output"
STATE_ROOT = Path(__file__).resolve().parent / "scout-state"

DOMAIN = "https://apps.cer-rec.gc.ca"
ADVANCED_URL = f"{DOMAIN}/REGDOCS/Search/Advanced"
RESULTS_URL = f"{DOMAIN}/REGDOCS/Search/SearchAdvancedResults"
VIEW_URL = f"{DOMAIN}/REGDOCS/Item/View/{{id}}"
PAGE_SIZE = 200
SORT_OLDEST_FIRST = 21

# One polite sequential request at a time. Errors increase this delay
# automatically; successful responses gradually return it to the baseline.
MIN_DELAY, MAX_DELAY = 0.75, 1.25
MAX_DELAY_MULTIPLIER = 8.0
MAX_RETRIES, BACKOFF = 4, 2.0
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}
# Requests that failed even after their retries, in a row, before a run stops.
MAX_FAILURES_IN_ROW = 5
# A download REGDOCS answers about that one file -- a status not worth
# retrying (404, 400, ...) or a page where the PDF should be -- says nothing
# about whether REGDOCS is up, so it does not count toward MAX_FAILURES_IN_ROW.
# Earlier failures stay in the queue and come first on a resumed run, back to
# back. So many in a row that the download URLs themselves may have changed
# still stops the run.
MAX_UNAVAILABLE_IN_ROW = 50
CONTAINER_MAX_DEPTH = 20
CONTAINER_MAX_ITEMS = 10_000

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


# --------------------------------------------------------------------------
# small helpers

def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean(v) -> str:
    return "" if v is None else WS_RE.sub(" ", str(v)).strip()


def slug(v: str) -> str:
    return NON_WORD_RE.sub("-", clean(v).casefold()).strip("-")


def norm_label(label: str) -> str:
    t = clean(label).casefold().rstrip(":")
    t = re.sub(r"[\[\](){}]", " ", t)
    return clean(NON_WORD_RE.sub(" ", t))


def normalize_date(value: str) -> str | None:
    text = clean(value)
    if not text:
        return None
    cand = text.split("T", 1)[0]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%d/%m/%Y", "%m/%d/%Y",
                "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(cand, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    m = DATE_TOKEN_RE.search(cand)
    if m:
        try:
            return date(*map(int, m.groups())).isoformat()
        except ValueError:
            return None
    return None


def parse_day(value: str) -> str:
    """A YYYY-MM-DD date, with a day past the end of its month clamped to the last.

    REGDOCS does not reject an impossible date; it silently drops it. An end
    date of 2026-09-31 becomes "until today", and a start date of 2026-02-30
    becomes "since 2002" -- about 550,000 items. So a date is made valid here,
    before it can reach a request, and anything that cannot be made valid is
    refused rather than guessed at.
    """
    parts = clean(value).split("-")
    if (len(parts) != 3 or not all(p.isdigit() for p in parts)
            or len(parts[0]) != 4 or not 1 <= len(parts[1]) <= 2 or not 1 <= len(parts[2]) <= 2):
        raise ValueError(f"{value!r} is not a YYYY-MM-DD date")
    year, month, day = map(int, parts)
    # A short or mistyped year is still a date REGDOCS accepts, and every
    # filing falls after it, so the row-date check below cannot catch it.
    if not 1990 <= year <= date.today().year + 1:
        raise ValueError(f"{value!r}: year {year} is outside REGDOCS's range")
    if not 1 <= month <= 12:
        raise ValueError(f"{value!r}: month must be 1-12")
    if day < 1:
        raise ValueError(f"{value!r}: day must be at least 1")
    return f"{year:04d}-{month:02d}-{min(day, calendar.monthrange(year, month)[1]):02d}"


class RangeIgnored(RuntimeError):
    """REGDOCS returned rows outside the dates it was asked for."""


class SearchRejected(RuntimeError):
    """A search's first page shows it is not the search that was asked for."""


class TooManyFailures(RuntimeError):
    """Requests keep failing; carrying on would only add load to REGDOCS."""


def is_container_kind(v) -> bool:
    return clean(v).casefold() in {"compound document", "folder"}


def is_paper_only(v) -> bool:
    t = clean(v).casefold()
    return "paper only" in t or "papier seulement" in t


def write_json(path: Path, value) -> None:
    """Write atomically; gzipped when the name ends in .gz (with no timestamp in
    the header, so the same record always compresses to the same bytes)."""
    tmp = path.with_name(path.name + ".part")
    text = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if path.name.endswith(".gz"):
        tmp.write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))
    else:
        tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path):
    data = path.read_bytes()
    return json.loads(gzip.decompress(data) if path.name.endswith(".gz") else data)


def title_identifiers(title: str) -> dict[str, list[str]]:
    """Identifiers REGDOCS prints in a title, grouped by type.

    Same patterns and precedence as cer-regdocs2, so records seeded from it and
    records scouted here agree. These come from the title text, not from a
    structured field, and are only as reliable as the filer's naming.
    """
    text = clean(title)
    out: dict[str, list[str]] = defaultdict(list)

    def add(kind: str, value: str) -> None:
        if value not in out[kind]:
            out[kind].append(value)

    f = FILING_TITLE_RE.search(text)
    if f:
        add("filing_number", f.group(1).upper())
        if f.group(2):
            add("filing_sequence", f.group(2))
    for m in EXHIBIT_RE.finditer(text):
        add("exhibit_number", m.group(1).upper())
    seen = {v for vs in out.values() for v in vs}
    for m in REGULATORY_ID_RE.finditer(text):
        v = m.group(1).upper()
        kind = ("regulatory_instrument_number"
                if v.startswith(("AO-", "GO-", "GPSO-", "EPR-", "MO-", "RO-")) else "activity_number")
        if v not in seen:
            seen.add(v)
            add(kind, v)
    for m in ACTIVITY_RE.finditer(text):
        v = clean(m.group(1)).upper().replace(" ", "-")
        if v.startswith("C") and v[1:].replace("-", "").isdigit():
            continue
        kind = ("regulatory_instrument_number"
                if any(p in v for p in ("ORDER", "GPSO", "AO-", "GO-")) else "activity_number")
        if v not in seen:
            seen.add(v)
            add(kind, v)
    padded = f" {text} "
    if re.search(r"(?:^|\s|[-–—_/])(FR|FRENCH|FRANÇAIS)(?:\s|[-–—_/]|$)", padded, re.I):
        add("language_marker", "fr")
    elif re.search(r"(?:^|\s|[-–—_/])(EN|ENGLISH|ANGLAIS)(?:\s|[-–—_/]|$)", padded, re.I):
        add("language_marker", "en")
    return dict(out)


# --------------------------------------------------------------------------
# HTTP

class Http:
    """One adaptively paced request at a time, retried, honouring Retry-After.

    Stops the run (TooManyFailures) once MAX_FAILURES_IN_ROW requests in a row
    have failed after their retries: REGDOCS is down or refusing us, and
    retrying request after request for hours would only add to its load.
    """

    def __init__(self, min_delay=MIN_DELAY, max_delay=MAX_DELAY) -> None:
        if min_delay < 0 or max_delay < min_delay:
            raise ValueError('Invalid request delay range')
        self.client = httpx.Client(cookies={"RDI-NumberOfRecords": str(PAGE_SIZE)},
                                   follow_redirects=True,
                                   timeout=httpx.Timeout(60.0, connect=30.0))
        self.min_delay, self.max_delay = min_delay, max_delay
        self.delay_multiplier = 1.0
        self.direct_containers = True
        self.next_at = 0.0
        self.requests = 0
        self.failed_in_row = 0

    def _pace(self) -> None:
        wait = self.next_at - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.next_at = time.monotonic() + random.uniform(
            self.min_delay, self.max_delay) * self.delay_multiplier

    @staticmethod
    def _retry_after(headers) -> float | None:
        raw = clean(headers.get("retry-after"))
        return float(raw) if raw.isdigit() else None

    def get(self, url: str, *, params=None, ajax=False, referer=None, page_size=None):
        """-> (ok, text, final_url, error)

        page_size asks for that many results per page for this request only.
        """
        if page_size is None or page_size == PAGE_SIZE:
            return self._get(url, params=params, ajax=ajax, referer=referer)
        self.client.cookies.set("RDI-NumberOfRecords", str(page_size))
        try:
            return self._get(url, params=params, ajax=ajax, referer=referer)
        finally:
            self.client.cookies.set("RDI-NumberOfRecords", str(PAGE_SIZE))

    def _get(self, url: str, *, params, ajax, referer):
        headers = dict(AJAX_HEADERS if ajax else HEADERS)
        if referer:
            headers["Referer"] = referer
        # httpx replaces a URL's query when params is supplied, even with {}.
        # Container endpoints and pagination URLs already carry their filters.
        request_url = httpx.URL(url).copy_merge_params(params) if params else url
        error = "unknown"
        for attempt in range(1, MAX_RETRIES + 2):
            self._pace()
            self.requests += 1
            retryable = True
            try:
                r = self.client.get(request_url, headers=headers)
                if r.status_code == 200:
                    self.succeeded()
                    return True, r.text, str(r.url), None
                error = f"HTTP {r.status_code}"
                retryable = self.refused(r)
            except httpx.RequestError as exc:
                error = f"{type(exc).__name__}: {exc}"
            if retryable:
                self.slow_down()
            if not retryable or attempt > MAX_RETRIES:
                break
            time.sleep(BACKOFF ** attempt + random.uniform(0, 1))
        self.gave_up(error)
        return False, None, None, error

    # The bookkeeping above, shared with downloads so both pace alike.
    def succeeded(self) -> None:
        self.failed_in_row = 0
        self.delay_multiplier = max(1.0, self.delay_multiplier * 0.85)

    def refused(self, response) -> bool:
        """Honour Retry-After; -> whether the status is worth retrying."""
        ra = self._retry_after(response.headers)
        if ra:
            self.next_at = max(self.next_at, time.monotonic() + ra)
        return response.status_code in RETRYABLE

    def slow_down(self) -> None:
        self.delay_multiplier = min(MAX_DELAY_MULTIPLIER, self.delay_multiplier * 2.0)

    def gave_up(self, error: str) -> None:
        self.failed_in_row += 1
        if self.failed_in_row >= MAX_FAILURES_IN_ROW:
            raise TooManyFailures(f"{self.failed_in_row} requests in a row failed; last: {error}")

    def close(self) -> None:
        self.client.close()


# --------------------------------------------------------------------------
# REGDOCS page parsing (ported from cer-regdocs2 stage 1)

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


def parse_total(html: str) -> int | None:
    m = TOTAL_RE.search(html)
    return int(m.group(1).replace(",", "")) if m else None


def recognized(html: str) -> bool:
    """Reject HTTP-200 maintenance or error pages posing as results."""
    if not clean(html):
        return False
    soup = BeautifulSoup(html, "lxml")
    if soup.find("tbody") is not None or parse_total(html) is not None:
        return True
    return bool(NO_RESULTS_RE.search(clean(soup.get_text(" ", strip=True))))


def explicit_empty(html: str) -> bool:
    return bool(clean(html)) and bool(
        NO_RESULTS_RE.search(clean(BeautifulSoup(html, "lxml").get_text(" ", strip=True))))


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


def parse_rows(html: str, *, container_id: str | None = None) -> list[Row]:
    """Result rows, keyed by REGDOCS item id.

    Inside a container, paper-only rows and rows sharing a placeholder id get a
    synthetic id instead, as in the original, so distinct rows cannot collapse
    onto one document. Synthetic rows are never files and are never written
    to source/.
    """
    soup = BeautifulSoup(html, "lxml")
    out: list[Row] = []
    skipped = 0
    for tr in (tr for tb in soup.find_all("tbody") for tr in tb.find_all("tr")):
        cells = tr.find_all("td")
        if not cells:
            continue
        if len(cells) == 1 and NO_RESULTS_RE.search(clean(cells[0].get_text(' ', strip=True))):
            continue
        summary = cells[0].find("summary")
        primary = summary if isinstance(summary, Tag) else cells[0]
        link = match = None
        for a in primary.find_all("a", href=True):
            m = ITEM_HREF_RE.search(a.get("href", ""))
            if m:
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
                if a is link:
                    continue
                ref = ITEM_HREF_RE.search(a["href"])
                if not ref:
                    continue
                text = clean(a.get_text(" ", strip=True))
                fm = FILING_RE.search(text)
                label = norm_label(_nearby_label(a))
                if fm:
                    row.filing_number, row.filing_id = fm.group(1), ref.group(2)
                elif label == "company":
                    row.company, row.company_id = text, ref.group(2)
                elif label == "project":
                    row.project, row.project_id = text, ref.group(2)
        out.append(row)

    if skipped:
        raise SearchRejected(f'{skipped} result row(s) could not be parsed; refusing to advance the page')
    if container_id and out:
        counts: dict[str, int] = defaultdict(int)
        for r in out:
            counts[r.document_id] += 1
        for r in out:
            if counts[r.document_id] > 1 or is_paper_only(f"{r.name} {r.kind or ''}"):
                r.synthetic, r.is_file = True, False
                r.document_id = f"row:{container_id}:{hashlib.sha256(r.name.encode()).hexdigest()[:12]}"
    return out


def container_endpoint(html: str, page_url: str, item_id: str) -> str | None:
    """The member-list URL a container page declares, and nothing guessed."""
    soup = BeautifulSoup(html, "lxml")
    cands = []
    sec = soup.find("section", id="section-items")
    if isinstance(sec, Tag):
        cands += [clean(sec.get(a)) for a in ("data-ajax-replace", "data-ajax-append", "data-url")
                  if clean(sec.get(a))]
    if not cands:
        cands += [clean(e.get("data-ajax-replace")) for e in soup.find_all(attrs={"data-ajax-replace": True})
                  if "/REGDOCS/Item/LoadResult/" in clean(e.get("data-ajax-replace"))]
    want = f"/REGDOCS/Item/LoadResult/{item_id}".casefold()
    for c in cands:
        u = urljoin(page_url, c)
        if want in u.casefold():
            return u
    return None


def next_container_page(html: str, current: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    has_next = False
    for a in soup.find_all("a"):
        rel = " ".join(a.get("rel", [])).casefold() if a.get("rel") else ""
        text = clean(a.get_text(" ", strip=True)).casefold()
        title = clean(a.get("title") or a.get("aria-label")).casefold()
        cls = " ".join(a.get("class", [])).casefold()
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
        node = soup.find("input", id=name)
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
    raise SearchRejected('Container has a Next link without usable pagination data; '
                         'refusing to stop at this page')


def facet_catalog(html: str) -> dict[str, dict[str, str]]:
    """Every facet category Advanced Search offers, with its values."""
    soup = BeautifulSoup(html, "lxml")
    cat: dict[str, dict[str, str]] = {}
    for lab in soup.find_all("label", attrs={"for": re.compile(r"^selectFilter\d+$")}):
        name = clean(lab.get_text(" ", strip=True))
        sel = soup.find("select", id=lab.get("for"))
        if not name or not isinstance(sel, Tag):
            continue
        opts = {clean(o.get("value")): clean(o.get_text(" ", strip=True))
                for o in sel.find_all("option")
                if clean(o.get("value")) and clean(o.get_text(" ", strip=True))}
        if opts:
            cat[name] = opts
    return cat


# --------------------------------------------------------------------------
# crawling

def crawl_search(http: Http, params: dict, check=None) -> tuple[dict[str, Row], bool]:
    """Every row for one search. Complete only if no page failed.

    `check(first_page_rows, total)` may return a reason to refuse the search
    before any further page is fetched; it is raised as SearchRejected.

    REGDOCS labels its total "about", so it is used to plan pages, not to judge
    completeness; a final probe past the last full page catches any overrun.

    Every row's date is checked against the range asked for. A row outside it
    means REGDOCS ignored the range -- which it does silently for a date it
    cannot parse -- and the search stops at once rather than walking the whole
    archive.
    """
    base = {**params, "srt": SORT_OLDEST_FIRST}
    lo, hi = params.get("sd"), params.get("ed")
    rows: dict[str, Row] = {}

    def page(offset: int):
        ok, html, _, _ = http.get(RESULTS_URL, params={**base, "sr": offset}, ajax=True)
        if ok and not recognized(html):
            ok = False
        got = parse_rows(html) if ok else []
        for r in got:
            if lo and hi and r.date and not lo <= r.date <= hi:
                raise RangeIgnored(f"asked for {lo}..{hi}, REGDOCS returned a row dated "
                                   f"{r.date} (total about {parse_total(html or '')}); "
                                   f"it did not apply the date range")
        return ok, got, html

    ok, first, html = page(1)
    if not ok:
        return {}, False
    for r in first:
        rows.setdefault(r.document_id, r)
    total = parse_total(html or "")
    if check and (why := check(first, total)):
        raise SearchRejected(why)
    if not first or len(first) < PAGE_SIZE:
        return rows, True
    last_count, offset, failed = len(first), 1, False
    for offset in range(1 + PAGE_SIZE, (total or 0) + 1, PAGE_SIZE):
        ok, got, _ = page(offset)
        if not ok:
            failed = True
            continue
        last_count = len(got)
        for r in got:
            rows.setdefault(r.document_id, r)
    nxt = offset + PAGE_SIZE
    while not failed and last_count == PAGE_SIZE:
        ok, got, _ = page(nxt)
        if not ok:
            failed = True
            break
        before = len(rows)
        for r in got:
            rows.setdefault(r.document_id, r)
        last_count = len(got)
        if not got or len(rows) == before:
            break
        nxt += PAGE_SIZE
    return rows, not failed


def read_container(http: Http, box_id: str) -> tuple[dict[str, Row], bool, str | None]:
    """One container's members. -> (members, complete, error)

    Membership counts as complete only when the parsed rows reach REGDOCS's own
    total or it explicitly says the container is empty; anything less is
    incomplete, so it cannot be used to remove a membership.
    """
    shell_url = VIEW_URL.format(id=box_id)
    ok, shell, final, err = http.get(shell_url)
    if not ok:
        return {}, False, f"shell failed ({err})"
    endpoint = container_endpoint(shell, final or shell_url, box_id)
    if not endpoint:
        return {}, False, "no member-list endpoint declared"
    members: dict[str, Row] = {}
    total, empty, failed, url, visited = None, False, False, endpoint, set()
    while url and url not in visited:
        visited.add(url)
        ok, frag, ffinal, err = http.get(url, ajax=True, referer=final or shell_url)
        if not ok or not recognized(frag):
            failed = True
            break
        t = parse_total(frag)
        if t is not None:
            total = max(total or 0, t)
        got = parse_rows(frag, container_id=box_id)
        if not got and explicit_empty(frag):
            empty = True
        for m in got:
            if m.document_id != box_id:
                members.setdefault(m.document_id, m)
        nxt = next_container_page(frag, ffinal or url)
        url = nxt if nxt and nxt not in visited else None
    if total is None and not members and empty:
        total = 0
    return members, (not failed and total is not None and len(members) >= total), None


def expand_containers(http: Http, rows: dict[str, Row], log) -> dict[str, tuple[Row, set[str], bool]]:
    """Walk Compound Documents and Folders to their members, nested ones included.

    Returns container id -> (container row, member ids, complete).
    """
    seeds = [r for r in rows.values() if is_container_kind(r.kind)]
    queue = deque((r, 0) for r in seeds)
    queued = {r.document_id for r in seeds}
    seen: set[str] = set()
    result: dict[str, tuple[Row, set[str], bool]] = {}
    while queue and len(seen) < CONTAINER_MAX_ITEMS:
        box, depth = queue.popleft()
        if box.document_id in seen:
            continue
        seen.add(box.document_id)
        members, complete, err = read_container(http, box.document_id)
        result[box.document_id] = (box, set(members), complete)
        if err:
            log(f"  container {box.document_id}: {err}")
            continue
        for m in members.values():
            rows.setdefault(m.document_id, m)
            if (is_container_kind(m.kind) and m.document_id not in queued
                    and depth + 1 <= CONTAINER_MAX_DEPTH):
                queue.append((m, depth + 1))
                queued.add(m.document_id)
        log(f"  container {box.document_id}: {len(members)} member(s), "
            f"{'complete' if complete else 'INCOMPLETE'}")
    return result


def breadcrumb_parent(html: str, doc_id: str) -> str | None:
    """The item a document's page names as its immediate parent -- its filing.

    A REGDOCS page's breadcrumb runs from the site root through commodity,
    company and project down to the filing the document sits in, each an
    Item/View link. The last of those that is not the document itself is the
    filing.
    """
    crumb = BeautifulSoup(html, "lxml").select_one("ol.breadcrumb")
    if not isinstance(crumb, Tag):
        return None
    ids = [m.group(2) for a in crumb.find_all("a", href=True)
           if (m := ITEM_HREF_RE.search(a["href"])) and m.group(2) != doc_id]
    return ids[-1] if ids else None


def find_date(http: Http, doc_id: str) -> tuple[str | None, str]:
    """A document's filing date, found without knowing it in advance.

    The scout searches by date, and a document's own page does not state its
    date -- the only date on it is the project's. So the date is read from the
    document's row in its filing's member list, found through the breadcrumb.
    -> (date or None, how it was found or why not)
    """
    ok, html, _, err = http.get(VIEW_URL.format(id=doc_id))
    if not ok:
        return None, f"its page is unavailable ({err})"
    parent = breadcrumb_parent(html, doc_id)
    if not parent:
        return None, "its page names no filing"
    members, _, err = read_container(http, parent)
    if err:
        return None, f"its filing {parent}: {err}"
    row = members.get(doc_id)
    if row is None or not row.date:
        return None, f"its filing {parent} does not list it with a date"
    if not is_pdf_kind(row.kind):
        return None, f"it is {row.kind or 'of unknown kind'}, not a PDF"
    return row.date, f"from filing {parent}"


def scout_facets(http: Http, start: str, end: str, log):
    """doc id -> {category: [labels]}, plus which categories finished cleanly."""
    ok, html, _, err = http.get(ADVANCED_URL)
    if not ok:
        log(f"facet catalog unavailable ({err}); facets left unchanged")
        return {}, {}
    catalog = facet_catalog(html)
    log(f"facets: {len(catalog)} categories, {sum(map(len, catalog.values()))} values")
    found: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    complete: dict[str, bool] = {}
    for category, options in catalog.items():
        complete[category] = True
        for filter_id, label in options.items():
            got, ok = crawl_search(http, {"sd": start, "ed": end, "rds": filter_id})
            if not ok:
                complete[category] = False
                log(f"  facet {category} = {label}: incomplete")
            for doc_id in got:
                if label not in found[doc_id][category]:
                    found[doc_id][category].append(label)
    return found, complete


# --------------------------------------------------------------------------
# records

def record_from_row(row: Row) -> dict:
    return {
        "title": row.name,
        "date": row.date,
        "submitter": row.submitter or None,
        "kind": row.kind,
        "url": row.url,
        "view_url": VIEW_URL.format(id=row.document_id),
        "filing": {"number": row.filing_number,
                   "id": int(row.filing_id) if row.filing_id else None},
        "company": {"name": row.company,
                    "id": int(row.company_id) if row.company_id else None},
        "project": {"name": row.project,
                    "id": int(row.project_id) if row.project_id else None},
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


def add_listing(rec: dict, listing: dict) -> None:
    listing = {k: v for k, v in listing.items() if v not in (None, "")}
    listings = rec.setdefault("listings", [])
    if listing and listing not in listings:
        listings.append(listing)


def listing_from(obs: dict) -> dict:
    def get(path):
        value = obs
        for part in path.split("."):
            value = (value or {}).get(part) if isinstance(value, dict) else None
        return value
    return {name: get(path) for name, path in LISTING}


def merge(old: dict | None, doc_id: str, obs: dict, facets: dict, facets_done: dict[str, bool],
          in_range: bool, containers_seen: list[dict], containers_cleared: set[str],
          when: str) -> tuple[dict, list[dict]]:
    """Fold one observation into a record. -> (record, changes)

    The rules exist so that a scrape that partly failed cannot erase anything:

    - A field is only ever replaced by a non-empty value. A row missing its
      company does not blank a company recorded earlier.
    - A facet category is replaced outright only if every search in it
      finished AND the document was inside the scouted date range -- facet
      searches are date-bounded, so a document reached only through a
      container would otherwise lose every facet. Otherwise values only grow.
    - A container membership is dropped only when that container was fully
      traversed this time and the document was not in it.
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
        add_listing(rec, listing_from(obs))
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

    cur_f = {k: list(v) for k, v in (rec.get("facets") or {}).items()}
    done_f = dict(rec.get("facets_complete") or {})
    observed_f = dict(rec.get("facet_observed_at") or {})
    for category, finished in facets_done.items():
        if observed_f.get(category, '') > when:
            continue
        new = list(facets.get(category, []))
        before = cur_f.get(category, [])
        if finished and in_range:
            after = new
        else:
            after = before + [v for v in new if v not in before]
        note(f"facets.{category}", before, after)
        cur_f[category] = after
        done_f[category] = bool(finished and in_range) or done_f.get(category, False)
        observed_f[category] = when
    if facets_done:
        rec["facets"], rec["facets_complete"] = cur_f, done_f
        rec['facet_observed_at'] = observed_f

    cur_c = [c for c in (rec.get("containers") or []) if c.get("id") not in containers_cleared]
    have = {c.get("id") for c in cur_c}
    cur_c += [c for c in containers_seen if c["id"] not in have]
    note("containers", rec.get("containers") or [], cur_c)
    rec["containers"] = cur_c

    rec["last_seen_at"] = max(rec.get('last_seen_at') or '', when)
    if old is None:
        # A first sighting is a creation, not a change.
        changes = []
    rec["changes"] = (rec.get("changes") or []) + changes
    return rec, changes


RECORD = "cer.meta.json.gz"
PLAIN_RECORD = "cer.meta.json"   # the layout before records were gzipped


def record_path(doc_id: str) -> Path:
    return OUT / doc_id / f"{doc_id}.{RECORD}"


def compress_records() -> int:
    """Gzip every record still in the plain layout; returns how many.

    Runs under the writer lock before any command that writes, so a process
    started before the change -- which still writes plain records -- has
    finished first. Where both forms exist, the newer file wins. Each record is
    read back before its plain file is removed."""
    done = 0
    for plain in OUT.glob(f"*/*.{PLAIN_RECORD}"):
        packed = plain.with_name(plain.name + ".gz")
        if not packed.exists() or plain.stat().st_mtime > packed.stat().st_mtime:
            rec = json.loads(plain.read_text(encoding="utf-8"))
            write_json(packed, rec)
            if read_json(packed) != rec:
                raise OSError(f"{packed}: does not read back as {plain}")
        plain.unlink()
        done += 1
    return done


def read_record(doc_id: str) -> dict | None:
    """The existing record, or None. A file in an older layout does not count:
    it carries no history, so it is replaced rather than merged into."""
    path = record_path(doc_id)
    if not path.exists():
        return None
    rec = read_json(path)
    return rec if rec.get("cer_schema", 0) >= CER_SCHEMA else None


def is_pdf_kind(kind) -> bool:
    return "pdf" in clean(kind).casefold()


# --------------------------------------------------------------------------
# commands

def scout_range(http: Http, start: str, end: str, *, only: set[str] | None,
                dry_run: bool, log) -> tuple[int, set[str]]:
    """Scout one date range and fold what it finds into the records.

    With `only`, the whole range is still read -- facets and containers can
    only be learned that way -- but records are written for those documents
    alone. -> (exit code, ids recorded)
    """
    when = now()
    written: set[str] = set()
    try:
        log(f"scouting {start} .. {end}")
        base, base_ok = crawl_search(http, {"sd": start, "ed": end})
        log(f"base search: {len(base)} item(s){'' if base_ok else ' -- INCOMPLETE'}")
        in_range = set(base)
        rows = dict(base)
        boxes = expand_containers(http, rows, log) if any(
            is_container_kind(r.kind) for r in base.values()) else {}
        facets, facets_done = scout_facets(http, start, end, log)
    except RangeIgnored as exc:
        # Nothing has been written yet; stopping here leaves every record as it was.
        log(f"STOPPED: {exc}")
        log("nothing written")
        return 2, written
    if not base_ok:
        # An incomplete base search is still worth recording, but it says less.
        log("base search incomplete: records are updated, nothing is removed")

    member_of: dict[str, list[dict]] = defaultdict(list)
    cleared_for: dict[str, set[str]] = defaultdict(set)
    for box_id, (box, members, complete) in boxes.items():
        ref = {"id": box_id, "kind": box.kind, "title": box.name}
        for m in members:
            member_of[m].append(ref)
        if complete:
            for doc_id in rows:
                if doc_id not in members:
                    cleared_for[doc_id].add(box_id)

    tally = defaultdict(int)
    changed_fields = defaultdict(int)
    no_facets: dict[str, dict] = {}
    for doc_id, row in sorted(rows.items()):
        if not row.is_file or row.synthetic or not doc_id.isdigit():
            tally["skipped: container or paper-only"] += 1
            continue
        if not is_pdf_kind(row.kind):
            tally[f"skipped: {row.kind or 'unknown kind'}"] += 1
            continue
        if only is not None and doc_id not in only:
            tally["skipped: not requested"] += 1
            continue
        path = record_path(doc_id)
        old = read_record(doc_id)
        rec, changes = merge(old, doc_id, record_from_row(row), facets.get(doc_id, {}),
                             facets_done, doc_id in in_range, member_of.get(doc_id, []),
                             cleared_for.get(doc_id, set()), when)
        if old is None:
            tally["new"] += 1
        elif changes:
            tally["updated"] += 1
            for c in changes:
                changed_fields[c["field"]] += 1
        else:
            tally["unchanged"] += 1
        written.add(doc_id)
        if unfaceted(rec):
            no_facets[doc_id] = rec
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json(path, rec)
    log(f"\n{http.requests} request(s) so far")
    for k, v in sorted(tally.items(), key=lambda kv: -kv[1]):
        log(f"  {k:>44}: {v}")
    if changed_fields:
        log("  fields that changed:")
        for k, v in sorted(changed_fields.items(), key=lambda kv: -kv[1]):
            log(f"    {k:>40}: {v}")
    code = 0 if base_ok else 1
    if no_facets:
        # Reached only through a container, filed outside the range: the
        # date-bounded facet searches never saw them. Found by filing instead.
        log(f"\n{len(no_facets)} record(s) reached only through containers have no facets")
        if dry_run:
            log("  a real run finds them by filing")
        else:
            code = max(code, facet_pass(http, no_facets, FACET_CATEGORIES, False, log))
    if dry_run:
        log("\ndry run; nothing written")
    return code, written


def record_documents(ids: list[str], dry_run: bool) -> int:
    """Give specific documents a record: find each one's date, scout those days.

    Documents filed on the same day share one scout. A document whose date
    cannot be found is reported, not guessed at.
    """
    bad = [i for i in ids if not i.isdigit()]
    if bad:
        print(f"refusing: not REGDOCS document ids: {', '.join(bad)}")
        return 2
    http = Http()
    by_date: dict[str, list[str]] = defaultdict(list)
    unplaced: dict[str, str] = {}
    recorded: set[str] = set()
    code = 0
    try:
        for doc_id in ids:
            day, how = find_date(http, doc_id)
            print(f"  {doc_id}: {day or 'NOT PLACED'} -- {how}")
            if day:
                by_date[day].append(doc_id)
            else:
                unplaced[doc_id] = how
        for day, group in sorted(by_date.items()):
            print(f"\n{len(group)} document(s) filed {day}")
            c, got = scout_range(http, day, day, only=set(group), dry_run=dry_run, log=print)
            recorded |= got
            code = max(code, c)
    except TooManyFailures as exc:
        print(f"\nSTOPPED: {exc}\nrecords written before the stop are kept; re-run the same command")
        code = 1
    finally:
        http.close()
    missed = [i for g in by_date.values() for i in g if i not in recorded]
    print(f"\nrecorded {len(recorded)} of {len(ids)}")
    for doc_id, why in unplaced.items():
        print(f"  {doc_id}: not placed -- {why}; scout a date range that includes it")
    for doc_id in missed:
        print(f"  {doc_id}: not in its day's search results")
    return max(code, 1 if (unplaced or missed) else 0)


def cmd_doc(args) -> int:
    return record_documents(args.ids, args.dry_run)


def cmd_missing(args) -> int:
    """Every PDF in source/ that has no record, e.g. one fetched with ingest.py <url>."""
    ids = sorted(p.stem for p in SOURCE.glob("*.pdf") if read_record(p.stem) is None)
    print(f"{len(ids)} PDF(s) in source/ without a record")
    if not ids:
        return 0
    if args.limit:
        ids = ids[:args.limit]
    return record_documents(ids, args.dry_run)


# --------------------------------------------------------------------------
# facets for records that have none

# Filing numbers per text search. 50, 100 and 200 all found the same share of
# their filings, so the query's length is not what limits it.
FACET_GROUP = 200
# A text search over FACET_GROUP filings returned at most ~2,100 results. An
# exhibit number, which REGDOCS cannot match, returned ~3,700 instead.
TEXT_SEARCH_CAP = 3000
FACET_WINDOW_DAYS = 31
FACET_CATEGORIES = ["Document Type", "Application Type", "Commodity", "Role", "File Type"]


def text_check(rows: list[Row], total: int | None) -> str | None:
    """Refuse a text search too broad to be the one asked for, after one page.

    This bounds cost, not correctness: a document a facet-filtered search
    returns carries that facet whatever the text matched. The first page
    cannot be judged by its filing numbers -- the text also matches other
    filings' documents that cite the numbers, and sorted oldest first those
    fill it.
    """
    if total and total > TEXT_SEARCH_CAP:
        return f"about {total} results, more than {TEXT_SEARCH_CAP}"
    return None


def sweep(http: Http, params: dict, check, catalog: dict, targets: set[str], log):
    """Which targets one search finds, and their facet labels.

    The search is run once unfiltered, to learn which targets it reaches, and
    then once per facet value. -> (present, {doc: {category: [labels]}},
    {category: every search finished}, {doc: its unfiltered row})
    """
    rows, ok = crawl_search(http, params, check)
    present = targets & {d for d, r in rows.items() if r.is_file}
    # A filtered search can only narrow the unfiltered one. `check` is not
    # reused: documents citing a filing number can fill a filtered page
    # without the search being wrong.
    ceiling = len(rows) + max(20, len(rows) // 10)

    def narrower(first: list[Row], total: int | None) -> str | None:
        if total and total > ceiling:
            return f"a filtered search reports about {total} results, the unfiltered one {len(rows)}"
        return None
    labels: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    finished: dict[str, bool] = {}
    if not ok:
        log("    unfiltered search incomplete; facets are added, none marked complete")
    if not present:
        return present, labels, finished, rows
    for category, options in catalog.items():
        finished[category] = ok
        for filter_id, label in options.items():
            got, done = crawl_search(http, {**params, "rds": filter_id}, narrower)
            if not done:
                finished[category] = False
                log(f"    {category} = {label}: incomplete")
            for doc_id in present & set(got):
                labels[doc_id][category].append(label)
    return present, labels, finished, rows


def add_facets(rec: dict, labels: dict, finished: dict, via: str, when: str) -> list[dict]:
    """Fold found labels into a record. Values are only ever added."""
    cur = {k: list(v) for k, v in (rec.get("facets") or {}).items()}
    done = dict(rec.get("facets_complete") or {})
    changes = []
    for category, ok in finished.items():
        before = cur.get(category, [])
        after = before + [v for v in labels.get(category, []) if v not in before]
        if after != before and rec.get("doc_id") not in STAND_INS:
            changes.append({"field": f"facets.{category}", "old": before, "new": after,
                            "observed_at": when, "via": via})
        cur[category] = after
        done[category] = ok or done.get(category, False)
    rec["facets"], rec["facets_complete"] = cur, done
    rec["last_seen_at"] = when
    rec["changes"] = (rec.get("changes") or []) + changes
    return changes


def fill_blanks(rec: dict, row: Row, via: str, when: str) -> list[dict]:
    """Fill a record's empty fields from its search row. Nothing set is changed.

    The searches already return each document's row, which names its
    submitter, filing, company and project. A record seeded from cer-regdocs2
    often lacks some of these; a field REGDOCS leaves blank stays blank.
    """
    obs = record_from_row(row)
    changes = []
    for key in ("submitter", "kind", "filing", "company", "project"):
        new = obs.get(key)
        if isinstance(new, dict):
            cur = dict(rec.get(key) or {})
            for sub, v in new.items():
                if v not in (None, "") and cur.get(sub) in (None, ""):
                    changes.append({"field": f"{key}.{sub}", "old": cur.get(sub), "new": v,
                                    "observed_at": when, "via": via})
                    cur[sub] = v
            rec[key] = cur
        elif new not in (None, "") and rec.get(key) in (None, ""):
            changes.append({"field": key, "old": rec.get(key), "new": new,
                            "observed_at": when, "via": via})
            rec[key] = new
    if rec.get("doc_id") in STAND_INS:
        changes = []
    rec["changes"] = (rec.get("changes") or []) + changes
    return changes


def unfaceted(rec: dict, categories=None) -> bool:
    """No value in any category: never faceted, as opposed to a scouted
    record that simply has no Commodity. Every scouted PDF has a File Type,
    so a record the date searches saw is never picked up by accident."""
    facets = rec.get("facets") or {}
    return not any(facets.get(c) for c in (categories or FACET_CATEGORIES))


def facet_pass(http: Http, recs: dict[str, dict], categories: list[str],
               dry_run: bool, log) -> int:
    """Find these records' facets by filing, then by date; fill blank fields.

    Values are only added, and each addition is logged in `changes` with
    `via` naming the search that found it. Records are written at the end.
    """
    when = now()
    found: dict[str, tuple] = {}   # doc -> (via, labels, finished, its search row)
    code = 0
    try:
        ok, html, _, err = http.get(ADVANCED_URL)
        if not ok:
            log(f"facet catalog unavailable ({err})")
            return 1
        full = facet_catalog(html)
        unknown = [c for c in categories if c not in full]
        if unknown:
            log(f"refusing: no facet category {', '.join(unknown)}; there are {', '.join(full)}")
            return 2
        catalog = {c: full[c] for c in categories}
        log(f"searching {', '.join(f'{c} ({len(v)} values)' for c, v in catalog.items())}")

        def take(present, labels, finished, rows, via):
            for doc_id in present:
                found[doc_id] = (via, labels.get(doc_id, {}), finished, rows[doc_id])

        by_filing: dict[str, set[str]] = defaultdict(set)
        for doc_id, rec in recs.items():
            number = clean((rec.get("filing") or {}).get("number"))
            if number:
                by_filing[number].add(doc_id)
        filings = sorted(by_filing, key=lambda f: min(recs[d].get("date") or "" for d in by_filing[f]))
        groups = [filings[i:i + FACET_GROUP] for i in range(0, len(filings), FACET_GROUP)]
        log(f"\nby filing: {len(filings)} filing(s) in {len(groups)} search(es)")
        for n, group in enumerate(groups, 1):
            targets = set().union(*(by_filing[f] for f in group))
            params = {"txthl": " ".join(group), "w": 2, "lf": 1, "m": 0}
            try:
                present, labels, finished, rows = sweep(http, params, text_check, catalog,
                                                        targets, log)
            except (SearchRejected, RangeIgnored) as exc:
                log(f"  {n}/{len(groups)} {group[0]}..{group[-1]}: REFUSED -- {exc}")
                code = 1
                continue
            take(present, labels, finished, rows, "filing search")
            log(f"  {n}/{len(groups)} {group[0]}..{group[-1]}: {len(present)} of {len(targets)} "
                  f"found, {sum(bool(labels.get(d)) for d in present)} with facets "
                  f"({http.requests} requests so far)")

        rest = {d for d in recs if d not in found and recs[d].get("date")}
        days = sorted({recs[d]["date"] for d in rest})
        windows: list[list[str]] = []
        for day in days:
            if windows and (date.fromisoformat(day) - date.fromisoformat(windows[-1][0])).days < FACET_WINDOW_DAYS:
                windows[-1].append(day)
            else:
                windows.append([day])
        log(f"\nby date: {len(rest)} not found by filing, on {len(days)} day(s) "
              f"in {len(windows)} window(s)")
        for n, window in enumerate(windows, 1):
            start, end = window[0], window[-1]
            targets = {d for d in rest if start <= recs[d]["date"] <= end}
            try:
                present, labels, finished, rows = sweep(http, {"sd": start, "ed": end}, None,
                                                  catalog, targets, log)
            except (SearchRejected, RangeIgnored) as exc:
                log(f"  {n}/{len(windows)} {start}..{end}: REFUSED -- {exc}")
                code = 1
                continue
            take(present, labels, finished, rows, "date search")
            log(f"  {n}/{len(windows)} {start}..{end}: {len(present)} of {len(targets)} "
                  f"found, {sum(bool(labels.get(d)) for d in present)} with facets "
                  f"({http.requests} requests so far)")
    except TooManyFailures as exc:
        log(f"\nSTOPPED: {exc}\nwhat was found so far is still recorded")
        code = 1

    tally = defaultdict(int)
    filled = defaultdict(int)
    for doc_id, (via, labels, finished, row) in sorted(found.items()):
        rec = recs[doc_id]
        added = add_facets(rec, labels, finished, via, when)
        blanks = fill_blanks(rec, row, via, when)
        tally[f"facets added ({via})" if added else "found, no new facets"] += 1
        for c in blanks:
            filled[c["field"]] += 1
        if not dry_run:
            write_json(record_path(doc_id), rec)
    missing = sorted(set(recs) - set(found))
    tally["not found"] = len(missing)
    log(f"\n{http.requests} request(s)")
    for k, v in sorted(tally.items(), key=lambda kv: -kv[1]):
        log(f"  {k:>32}: {v}")
    if filled:
        log("  blank fields filled from search rows:")
        for k, v in sorted(filled.items(), key=lambda kv: -kv[1]):
            log(f"    {k:>40}: {v}")
    typed = defaultdict(int)
    for via, labels, *_ in found.values():
        for label in labels.get("Document Type", []):
            typed[label] += 1
    if typed:
        log("  Document Type found:")
        for k, v in sorted(typed.items(), key=lambda kv: -kv[1]):
            log(f"    {k:>40}: {v}")
    for doc_id in missing[:20]:
        log(f"  not found: {doc_id} {recs[doc_id].get('date')} {recs[doc_id].get('title')}")
    if dry_run:
        log("\ndry run; nothing written")
    return code


def sniff(first: bytes) -> tuple[str, str]:
    if first.startswith(b"%PDF-"):
        return ".pdf", "application/pdf"
    if first.startswith(b"PK\x03\x04"):
        return ".zip", "application/zip"
    if first.startswith(b"\x89PNG"):
        return ".png", "image/png"
    if first.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if first.startswith((b"II*\x00", b"MM\x00*")):
        return ".tif", "image/tiff"
    low = first.lstrip()[:1024].lower()
    if low.startswith((b"<!doctype html", b"<html")) or b"<html" in low or b"<body" in low:
        return ".html", "text/html"
    return ".bin", "application/octet-stream"


def reconcile_download(path: Path, doc_id: str) -> None:
    """Recover the metadata half of an interrupted PDF/JSON write, locally."""
    rec = read_json(path)
    pdf = SOURCE / f'{doc_id}.pdf'
    with pdf.open('rb') as fh:
        first = fh.read(4096)
        if sniff(first)[0] != '.pdf':
            raise ValueError(f'{pdf}: existing file is not a PDF; refusing to mark downloaded')
        digest = hashlib.sha256(first)
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            digest.update(chunk)
    download = dict(rec.get('download') or {})
    download.update(sha256=digest.hexdigest(), size_bytes=pdf.stat().st_size,
                    content_type='application/pdf', extension='.pdf',
                    sha256_matches_file=True, reconciled_at=now())
    # Do not invent a download time or resolved URL for an existing file.
    rec['download'] = download
    write_json(path, rec)


def cmd_download(args) -> int:
    """Fetch every recorded PDF not already in source/. Existing files are left alone."""
    import duckdb
    from scout_crawl import pdf_queue

    todo = []
    repaired = repair_failed = 0
    with duckdb.connect(":memory:") as queue:
        for filename, doc_id in pdf_queue(queue, repair_only=True):
            try:
                reconcile_download(Path(filename), doc_id)
                repaired += 1
            except (OSError, ValueError) as exc:
                repair_failed += 1
                print(f'  {doc_id}: reconciliation FAILED: {exc}')
        candidates = pdf_queue(queue, missing_only=True)
    if repaired or repair_failed:
        print(f'Reconciled {repaired} existing PDF(s), failed {repair_failed}; no downloads needed for these')
    for filename, _ in candidates:
        path = Path(filename)
        rec = read_json(path)
        doc_id = rec.get("doc_id")
        if (rec.get("cer_schema", 0) >= CER_SCHEMA and is_pdf_kind(rec.get("kind"))
                and "/File/Download/" in (rec.get("url") or "")
                and not (SOURCE / f"{doc_id}.pdf").exists()):
            todo.append((path, rec))
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} file(s) to download")
    http = Http(min_delay=args.min_delay, max_delay=args.max_delay)
    ok_n = fail_n = unavailable_in_row = 0
    try:
        for path, rec in todo:
            doc_id = rec["doc_id"]
            status = None
            unavailable = False
            SOURCE.mkdir(exist_ok=True)
            tmp = SOURCE / f"{doc_id}.pdf.part"
            error = None
            installed = False
            for attempt in range(1, MAX_RETRIES + 2):
                http._pace()
                http.requests += 1
                retryable = True
                try:
                    h = hashlib.sha256()
                    first = b""
                    with http.client.stream("GET", rec["url"], headers=HEADERS) as r:
                        if r.status_code != 200:
                            status = r.status_code
                            retryable = http.refused(r)
                            raise httpx.HTTPStatusError(f"HTTP {r.status_code}",
                                                        request=r.request, response=r)
                        with open(tmp, "wb") as fh:
                            for chunk in r.iter_bytes(1 << 20):
                                if len(first) < 4096:
                                    first += chunk[:4096 - len(first)]
                                h.update(chunk)
                                fh.write(chunk)
                        final_url, ctype = str(r.url), r.headers.get("content-type", "")
                    ext, mime = sniff(first)
                    # REGDOCS sometimes answers a file link with an HTML page.
                    # For something catalogued as a PDF that is a failure to
                    # retry, not a document to keep.
                    if ext != ".pdf":
                        raise ValueError(f"expected a PDF, got {mime}")
                    http.succeeded()
                    SOURCE.mkdir(exist_ok=True)
                    dest = SOURCE / f"{doc_id}.pdf"
                    tmp.replace(dest)
                    installed = True
                    rec["download"] = {"sha256": h.hexdigest(), "size_bytes": dest.stat().st_size,
                                       "content_type": mime, "extension": ext,
                                       "resolved_url": final_url, "downloaded_at": now(),
                                       "sha256_matches_file": True}
                    write_json(path, rec)
                    error = None
                    break
                except (httpx.HTTPError, ValueError, OSError) as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    unavailable = isinstance(exc, ValueError) or (status is not None
                                                                  and not retryable)
                    tmp.unlink(missing_ok=True)
                    if installed:
                        # The file is safe. A later invocation repairs metadata
                        # locally instead of downloading the same PDF again.
                        break
                    if retryable:
                        http.slow_down()
                    if not retryable or attempt > MAX_RETRIES:
                        break
                    time.sleep(BACKOFF ** attempt + random.uniform(0, 1))
            if error:
                fail_n += 1
                print(f"  {doc_id}: FAILED {error}")
                if unavailable and not installed:
                    unavailable_in_row += 1
                    if unavailable_in_row >= MAX_UNAVAILABLE_IN_ROW:
                        raise TooManyFailures(f"{unavailable_in_row} files in a row unavailable "
                                              f"({error}); have the download URLs changed?")
                elif not installed:
                    unavailable_in_row = 0
                    http.gave_up(error)
            else:
                unavailable_in_row = 0
                ok_n += 1
                print(f"  {doc_id}: {rec['download']['size_bytes']:,} bytes")
    except TooManyFailures as exc:
        print(f"\nSTOPPED: {exc}\nfiles downloaded before the stop are kept; re-run the same command")
        fail_n += 1
    finally:
        http.close()
    print(f"\ndownloaded {ok_n}, failed {fail_n}")
    return 1 if fail_n or repair_failed else 0


def cmd_seed(args) -> int:
    """Build scout records from cer-regdocs2's download sidecars.

    A one-off, so documents already held have a record to compare the next
    scout against. Only documents whose PDF is in source/ are seeded, and
    only fields a scout here can also observe are carried over -- a field
    the scout cannot see would otherwise read as removed on the first run.
    The PDF is hashed against the sidecar's sha256 as it is seeded.
    """
    src = Path(args.sidecars)
    tally = defaultdict(int)
    for pdf in sorted(SOURCE.glob("*.pdf")):
        doc_id = pdf.stem
        side_path = src / f"{doc_id}.metadata.json"
        dest = record_path(doc_id)
        if read_record(doc_id) is not None and not args.force:
            tally["already seeded"] += 1
            continue
        if not side_path.exists():
            tally["no sidecar"] += 1
            continue
        d = json.loads(side_path.read_text())
        m = d.get("metadata") or {}
        col = (m.get("collection") or {}).get("facets") or {}
        dl = (m.get("download") or {})
        num = lambda v: int(v) if isinstance(v, (int, str)) and str(v).isdigit() else None
        h = hashlib.sha256()
        with open(pdf, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        rec = {
            "cer_schema": CER_SCHEMA,
            "doc_id": doc_id,
            # The search row's name, not the detail page's title: this scout
            # never fetches detail pages, and seeding from them would make
            # every title look changed on the first run.
            "title": m.get("name") or d.get("title"),
            "date": normalize_date(m.get("date") or d.get("filing_date") or ""),
            "submitter": m.get("submitter") or d.get("submitter"),
            "kind": m.get("kind"),
            "url": m.get("url") or d.get("source_url"),
            "view_url": VIEW_URL.format(id=doc_id),
            "filing": {"number": m.get("filing_number"), "id": num(m.get("filing_id"))},
            "company": {"name": m.get("company"), "id": num(m.get("company_id"))},
            "project": {"name": m.get("project"), "id": num(m.get("project_id"))},
            "identifiers": title_identifiers(m.get("name") or d.get("title") or ""),
            "facets": {k: list(v or []) for k, v in (m.get("facets") or {}).items()},
            "facets_complete": {k: (v or {}).get("status") == "SUCCEEDED" for k, v in col.items()},
            "containers": [{"id": c.get("container_id"), "kind": c.get("container_kind"),
                            "title": c.get("container_title")}
                           for c in (m.get("container_memberships") or [])],
            "first_seen_at": (d.get("pipeline") or {}).get("first_seen_at"),
            "last_seen_at": m.get("scraped_at"),
            "download": {"sha256": d.get("sha256"), "size_bytes": m.get("size_bytes"),
                         "content_type": d.get("content_type"), "extension": d.get("extension"),
                         "resolved_url": dl.get("resolved_url") or m.get("resolved_url"),
                         "downloaded_at": dl.get("downloaded_at") or m.get("downloaded_at"),
                         "sha256_matches_file": h.hexdigest() == d.get("sha256")},
            "changes": [],
            "seeded_from": f"cer-regdocs2 sidecar ({d.get('schema')} v{d.get('schema_version')})",
        }
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_json(dest, rec)
        tally["seeded" if rec["download"]["sha256_matches_file"] else "seeded (sha256 MISMATCH)"] += 1
    for k, v in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"  {k:>16}: {v}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, help_text in (
            ("discover", "find PDFs in a date range; save metadata and resume automatically"),
            ("enrich", "complete discovery, filing relationships, and facets for a date range")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--from", dest="date_from", required=True, help="inclusive YYYY-MM-DD")
        command.add_argument("--to", dest="date_to", required=True, help="inclusive YYYY-MM-DD")
        command.add_argument("--state", help="override the automatic date-range checkpoint directory")
        command.add_argument("--refresh", action="store_true",
                             help="start a fresh crawl of this range; omit on subsequent resumes")
        command.add_argument("--max-requests", type=int, default=0,
                             help="pause after this request budget; retries may exceed it (0: unlimited)")
        command.add_argument("--min-delay", type=float, default=MIN_DELAY,
                             help=f"minimum seconds between requests (default: {MIN_DELAY})")
        command.add_argument("--max-delay", type=float, default=MAX_DELAY,
                             help=f"maximum seconds between requests (default: {MAX_DELAY})")
    status = sub.add_parser("status", help="show saved crawl progress without contacting REGDOCS")
    status.add_argument("--state", help="show one checkpoint directory instead of all saved ranges")
    o = sub.add_parser("doc", help="record specific documents by id")
    o.add_argument("ids", nargs="+")
    o.add_argument("--dry-run", action="store_true")
    n = sub.add_parser("missing", help="record every PDF in source/ that has no record")
    n.add_argument("--limit", type=int)
    n.add_argument("--dry-run", action="store_true")
    d = sub.add_parser("download", help="fetch recorded PDFs not already in source/")
    d.add_argument("--limit", type=int)
    d.add_argument("--min-delay", type=float, default=MIN_DELAY,
                   help=f"minimum seconds between requests (default: {MIN_DELAY})")
    d.add_argument("--max-delay", type=float, default=MAX_DELAY,
                   help=f"maximum seconds between requests (default: {MAX_DELAY})")
    sub.add_parser("compress", help="gzip records left in the plain .cer.meta.json layout")
    e = sub.add_parser("seed", help="one-off: build records from cer-regdocs2 sidecars")
    e.add_argument("sidecars")
    e.add_argument("--force", action="store_true")
    args = p.parse_args()
    # Imported here: scout_crawl imports this module.
    from scout_crawl import command, Incomplete, UsageError, exclusive
    try:
        if args.cmd == 'status':
            return command(args)
        # Every metadata writer shares one lock, including download and repair.
        with exclusive(OUT / '.scout-writer.lock'):
            if compressed := compress_records():
                print(f"Compressed {compressed:,} record(s) to .{RECORD}")
            if args.cmd == 'compress':
                return 0
            if args.cmd in {'discover', 'enrich'}:
                return command(args)
            return {"doc": cmd_doc, "missing": cmd_missing,
                    "download": cmd_download, "seed": cmd_seed}[args.cmd](args)
    except UsageError as exc:
        print(f"refusing: {exc}")
        return 2
    except (Incomplete, TooManyFailures, SearchRejected) as exc:
        print(f"STOPPED: {exc}; saved checkpoints are retained")
        return 1
    except KeyboardInterrupt:
        print("\nPaused; rerun without --refresh to resume")
        return 130


if __name__ == "__main__":
    # Run as the module `scout`, the one scout_crawl imports, so both share
    # one copy of its settings instead of __main__ and scout each having one.
    import scout
    raise SystemExit(scout.main())
