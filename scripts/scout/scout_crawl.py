"""Resumable REGDOCS crawl using JSON records and an in-memory DuckDB queue.

CER metadata remains in output/<id>/<id>.cer.meta.json.gz. Search cursors and
container work live in scripts/scout/scout-state/<run>/jobs/*.json; no database
is persisted.
Each command loads the JSON into temporary DuckDB tables once and keeps them
current as it works, so neither the checkpoints nor catalog.duckdb are re-read.

A record is written before the page that found it is checkpointed, so a crash
replays the page and the merge makes the replay harmless. Facet values are
applied per category only once every filter in it has finished, in one write
per document, stamped with when each search ran rather than when it was
applied: an older checkpoint cannot overwrite a newer observation, and one
from before those stamps existed needs --refresh.
"""
from __future__ import annotations

import calendar
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

import duckdb

import scout as s


# REGDOCS pages beyond 10,000 results, but deep offsets are slow to answer and
# drift more while the index changes. Larger windows are halved instead.
SPLIT_ABOVE = 10000
# REGDOCS cannot render a results page holding certain records: it returns its
# own error page for every page that includes them, from either end, at any
# page size. 2020-12-16 had two among 145. Such a page is narrowed -- a window
# to single days, a day to pages of 20, a page of 20 to single results -- and a
# result that still fails is recorded as a gap instead of failing the range.
NARROWER = {s.PAGE_SIZE: 20, 20: 1}
# A container can report more members than it lists -- 2024 had two that show
# 2 of a reported 3 on every visit, presumably a member hidden from the public.
# The same shortfall seen on this many runs is recorded as a gap, like a result
# REGDOCS cannot list, instead of failing the range forever; a shortfall that
# changes starts the count again, since it may still be the index moving.
SHORT_CONTAINER_TRIES = 3
GAP_REUSE_DAYS = 30


class Incomplete(RuntimeError):
    def __init__(self, message, keep=None):
        super().__init__(message)
        self.keep = keep or {}  # saved on the failed job, which is otherwise reloaded


class UsageError(ValueError):
    """Bad arguments or state that does not match them: reported, not a bug."""


def stages_for(operation):
    if operation == 'discover':
        return ['inventory']
    if operation == 'enrich':
        # The last pass drains containers found by facet searches as well.
        return ['inventory', 'containers', 'facets', 'containers']
    return [operation]


def windows(start, end, yearly=False):
    current, stop = date.fromisoformat(start), date.fromisoformat(end)
    while current <= stop:
        last = date(current.year, 12, 31) if yearly else date(
            current.year, current.month, calendar.monthrange(current.year, current.month)[1])
        last = min(last, stop)
        yield current.isoformat(), last.isoformat()
        current = last + timedelta(days=1)


def regdocs_error(html):
    """REGDOCS's own error page, as opposed to a results page or a network fault."""
    return len(html) < 2000 and 'has encountered an unexpected error' in html


def regdocs_unavailable(html):
    """An explicit missing-item notice, never a generic or empty error page."""
    soup = s.BeautifulSoup(html or '', 'lxml')
    for panel in soup.select('.panel-warning'):
        text = s.clean(panel.get_text(' ', strip=True)).casefold()
        if ('looking for document or filing?' in text
                and 'the system was unable to locate them.' in text):
            return True
    return False


@contextmanager
def exclusive(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Incomplete(f'Another scout process owns {path}') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def has_table(connection, name):
    return bool(connection.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name=?", [name]).fetchone())


QUEUE_COLUMNS = {'filename': 'VARCHAR', 'doc_id': 'VARCHAR', 'day': 'VARCHAR', 'kind': 'VARCHAR',
                 'facets_complete': 'BOOLEAN', 'download_complete': 'BOOLEAN'}


def queue_row(filename, rec):
    download = rec.get('download') or {}
    return (str(filename), rec['doc_id'], rec.get('date'), rec.get('kind'),
            all((rec.get('facets_complete') or {}).get(cat, False) for cat in s.FACET_CATEGORIES),
            bool(download.get('sha256') and download.get('size_bytes') is not None))


def record_files():
    """Each record's file; status takes no lock, so it may run before plain
    records are compressed, and reads whichever form a document has."""
    if not s.OUT.is_dir():
        return
    with os.scandir(s.OUT) as entries:
        for entry in entries:
            if not entry.is_dir() or not entry.name.isdigit():
                continue
            for name in (s.RECORD, s.PLAIN_RECORD):
                path = Path(entry.path) / f'{entry.name}.{name}'
                if path.exists():
                    yield path
                    break


def ensure_records(connection):
    """Load only queue fields once; full metadata stays in its original JSON.

    Python reads the records and DuckDB gets only the queue fields. DuckDB can
    read gzipped JSON itself, but it buffers twice its largest object per
    thread, and a REGDOCS placeholder listed under a thousand filings (41059,
    "Pending Translation") has grown to hundreds of megabytes of history."""
    if has_table(connection, 'records'):
        return
    connection.execute("""CREATE TEMP TABLE records (
        filename VARCHAR, doc_id VARCHAR PRIMARY KEY, day VARCHAR, kind VARCHAR,
        facets_complete BOOLEAN, download_complete BOOLEAN)""")
    with tempfile.TemporaryDirectory() as tmp:
        rows, written = Path(tmp) / 'records.ndjson', 0
        with rows.open('w', encoding='utf-8') as out:
            for path in record_files():
                out.write(json.dumps(dict(zip(QUEUE_COLUMNS, queue_row(path, s.read_json(path)))))
                          + '\n')
                written += 1
        columns = ', '.join(f"'{k}': '{v}'" for k, v in QUEUE_COLUMNS.items())
        if written:
            connection.execute(f"""INSERT INTO records SELECT * FROM read_json(
                ?, format='newline_delimited', columns={{{columns}}})""", [str(rows)])
    connection.execute("""CREATE TEMP TABLE source_files AS
        SELECT regexp_extract(file, '([0-9]+)[.]pdf$', 1) AS doc_id FROM glob(?)""",
        [str(s.SOURCE / '*.pdf')])


def index_record(connection, filename, rec):
    if not has_table(connection, 'records'):
        return
    connection.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?)',
                       list(queue_row(filename, rec)))


def pdf_queue(connection, missing_only=False, include_date=False, repair_only=False):
    """Query the original CER files once, never the derived catalog.duckdb."""
    ensure_records(connection)
    sql = 'SELECT filename, doc_id' + (', day' if include_date else '') + " FROM records WHERE contains(lower(coalesce(kind, '')), 'pdf')"
    if missing_only:
        sql += ' AND doc_id NOT IN (SELECT doc_id FROM source_files)'
    if repair_only:
        sql += ' AND doc_id IN (SELECT doc_id FROM source_files) AND NOT download_complete'
    return connection.execute(sql + ' ORDER BY doc_id').fetchall()


class Crawl:
    def __init__(self, path, start, end, refresh=False):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.config_path = self.path / 'run.json'
        self.config = json.loads(self.config_path.read_text()) if self.config_path.exists() else {
            'schema': 1, 'range': [start, end], 'created_at': s.now(),
            'catalog': None, 'applied': {}, 'requests': 0, 'elapsed_seconds': 0,
        }
        if self.config.get('schema') != 1 or self.config['range'] != [start, end]:
            raise UsageError(f"State belongs to {self.config.get('range')}; use another --state directory")
        if refresh:
            from uuid import uuid4
            # The old checkpoints stay until the fresh crawl completes cleanly.
            replaced = (self.config.get('replaced_jobs_directories', [])
                        + [self.config.get('jobs_directory', 'jobs')])
            self.config = {'schema': 1, 'range': [start, end], 'created_at': s.now(),
                'catalog': None, 'applied': {}, 'requests': 0, 'elapsed_seconds': 0,
                'jobs_directory': 'jobs-' + uuid4().hex,
                'replaced_jobs_directories': replaced}
        self.jobs_dir = self.path / self.config.get('jobs_directory', 'jobs')
        self.jobs_dir.mkdir(exist_ok=True)
        # A normal resume may reuse a clean, completed read of the same
        # REGDOCS container from another date range. A refresh deliberately
        # contacts REGDOCS again.
        self.reuse_containers = not refresh
        self.container_cache = None
        self.connection = duckdb.connect(':memory:')
        self.load_jobs()
        self.save_config()
        for lo, hi in windows(start, end):
            self.enqueue(f'inventory:{lo}:{hi}', 'inventory', {'sd': lo, 'ed': hi})

    def close(self):
        self.connection.close()

    def save_config(self):
        s.write_json(self.config_path, self.config)

    def drop_replaced(self):
        """Delete the checkpoints a --refresh replaced, once it has completed."""
        replaced = self.config.pop('replaced_jobs_directories', [])
        if not replaced:
            return
        self.save_config()
        for name in replaced:
            if name == 'jobs' or (name.startswith('jobs-') and '/' not in name):
                shutil.rmtree(self.path / name, ignore_errors=True)

    def job_path(self, key):
        return self.jobs_dir / (hashlib.sha256(key.encode()).hexdigest() + '.json')

    def load_jobs(self):
        self.connection.execute('DROP TABLE IF EXISTS jobs')
        self.connection.execute('CREATE TEMP TABLE jobs (key VARCHAR PRIMARY KEY, stage VARCHAR, status VARCHAR, data VARCHAR)')
        if next(self.jobs_dir.glob('*.json'), None):
            self.connection.execute("""INSERT INTO jobs
                SELECT j->>'key', j->>'stage', j->>'status', content
                FROM (SELECT content, content::JSON j FROM read_text(?))""",
                [str(self.jobs_dir / '*.json')])

    def save_job(self, job):
        s.write_json(self.job_path(job['key']), job)
        self.connection.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?,?)',
                                [job['key'], job['stage'], job['status'], json.dumps(job)])

    def enqueue(self, key, stage, payload):
        if not self.connection.execute('SELECT 1 FROM jobs WHERE key=?', [key]).fetchone():
            job = {'key': key, 'stage': stage, 'payload': payload,
                'offset': 1, 'url': None, 'status': 'pending', 'error': None,
                'pages': 0, 'ids': [], 'visited': [], 'observations': {}}
            self.save_job(job)
            if stage == 'containers' and self.reuse_containers and self.container_cache is not None:
                self.reuse_container(job)

    def load_container_cache(self):
        """Index container checkpoints from other active ranges once per command.

        Every container payload is retained so a reused parent's child
        containers can still be enqueued. Clean completed jobs are reusable;
        confirmed container gaps are reused for 30 days before being checked
        again. In-progress shortfalls are never reused.
        """
        if self.container_cache is not None:
            return
        payloads, completed = {}, {}
        state_root = s.STATE_ROOT
        for config_path in state_root.glob('*/run.json'):
            run_path = config_path.parent
            if run_path.resolve() == self.path.resolve():
                continue
            try:
                config = json.loads(config_path.read_text())
                jobs_dir = run_path / config.get('jobs_directory', 'jobs')
                paths = jobs_dir.glob('*.json')
                for path in paths:
                    job = json.loads(path.read_text())
                    if job.get('stage') != 'containers':
                        continue
                    key = job.get('key')
                    payload = job.get('payload')
                    if not key or not isinstance(payload, dict):
                        continue
                    payloads.setdefault(key, payload)
                    gaps = job.get('gaps') or []
                    fresh_gap = False
                    if gaps and all(g.get('container') for g in gaps):
                        try:
                            newest = max(datetime.fromisoformat(g['found_at']) for g in gaps)
                            fresh_gap = (datetime.now(timezone.utc) - newest
                                         <= timedelta(days=GAP_REUSE_DAYS))
                        except (KeyError, TypeError, ValueError):
                            fresh_gap = False
                    clean = not gaps and not job.get('short')
                    if job.get('status') == 'done' and (clean or fresh_gap):
                        prior = completed.get(key)
                        quality = 2 if clean else 1
                        old_quality = (prior or {}).get('_cache_quality', 0)
                        stamp = job.get('observed_at') or job.get('last_successful_request_at') or ''
                        old_stamp = ((prior or {}).get('observed_at')
                                     or (prior or {}).get('last_successful_request_at') or '')
                        if prior is None or quality > old_quality or (
                                quality == old_quality and stamp > old_stamp):
                            completed[key] = {**job, '_cache_quality': quality}
            except (OSError, ValueError, TypeError):
                # A concurrently written or obsolete state file is not a safe
                # reuse source. The ordinary crawl remains available.
                continue
        self.container_cache = {'payloads': payloads, 'completed': completed}

    def reuse_container(self, job):
        """Replace one queued container with a clean cached read, recursively."""
        cached = self.container_cache['completed'].get(job['key'])
        if cached is None:
            return False
        source_payload = job['payload']
        reused = dict(cached)
        reused.pop('_cache_quality', None)
        reused.update(payload=source_payload, error=None,
                      reused_at=s.now(), reused_from='another saved range')
        self.save_job(reused)
        for doc_id in reused.get('ids', []):
            child_key = 'container:' + doc_id
            child_payload = self.container_cache['payloads'].get(child_key)
            if child_payload is not None:
                self.enqueue(child_key, 'containers', child_payload)
        return True

    def reuse_pending_containers(self):
        if not self.reuse_containers:
            return 0
        self.load_container_cache()
        reused = 0
        # Reuse may recursively enqueue children, so drain until every newly
        # added pending child has also had a chance to use the cache.
        while True:
            batch = self.jobs('containers', pending=True)
            changed = sum(self.reuse_container(job) for job in batch)
            reused += changed
            if not changed:
                return reused

    def jobs(self, stage=None, pending=False):
        query, params = 'SELECT data FROM jobs WHERE true', []
        if stage:
            query += ' AND stage=?'
            params.append(stage)
        if pending:
            query += " AND status='pending'"
        return [json.loads(row[0]) for row in self.connection.execute(query + ' ORDER BY key', params).fetchall()]

    def record(self, row, parent=None, facet=None, observed_at=None):
        """Write metadata before advancing its page checkpoint; replay is safe."""
        if s.is_container_kind(row.kind) and not row.synthetic:
            self.enqueue('container:' + row.document_id, 'containers', asdict(row))
        if not (row.is_file and not row.synthetic and row.document_id.isdigit()
                and s.is_pdf_kind(row.kind)):
            return
        refs = [{'id': parent.document_id, 'kind': parent.kind, 'title': parent.name}] if parent else []
        facets = {facet[0]: [facet[1]]} if facet else {}
        rec, _ = s.merge(s.read_record(row.document_id), row.document_id,
            s.record_from_row(row), facets, {cat: False for cat in facets},
            False, refs, set(), observed_at or s.now())
        path = s.record_path(row.document_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        s.write_json(path, rec)
        index_record(self.connection, path, rec)

    def prepare_facets(self, http):
        catalog = self.config['catalog']
        if catalog is None:
            ok, html, _, err = http.get(s.ADVANCED_URL)
            catalog = s.facet_catalog(html or '') if ok else {}
            if set(catalog) != set(s.FACET_CATEGORIES):
                raise Incomplete(f'Incomplete facet catalog: {err or list(catalog)}')
            self.config['catalog'] = catalog
            self.save_config()
        lo, hi = self.config['range']
        for start, end in windows(lo, hi, yearly=True):
            for category, options in catalog.items():
                for filter_id, label in options.items():
                    self.enqueue(f'facet:{start}:{end}:{filter_id}', 'facets',
                        {'sd': start, 'ed': end, 'rds': filter_id,
                         'category': category, 'label': label, 'window': [start, end]})

    def search_page(self, http, job):
        p = job['payload']
        size = p.get('page_size', s.PAGE_SIZE)
        params = {k: p[k] for k in ('sd', 'ed', 'rds') if k in p}
        params.update(sr=job['offset'], srt=s.SORT_OLDEST_FIRST)
        ok, html, _, err = http.get(s.RESULTS_URL, params=params, ajax=True, page_size=size)
        if ok and regdocs_error(html or ''):
            self.narrow(job, size)
            return
        if not ok or not s.recognized(html or ''):
            raise Incomplete(err or 'Unrecognized search response')
        rows = s.parse_rows(html)
        total = s.parse_total(html)
        if (job['offset'] == 1 and total is not None and total > SPLIT_ABOVE
                and p['sd'] < p['ed']):
            lo, hi = date.fromisoformat(p['sd']), date.fromisoformat(p['ed'])
            mid = lo + (hi - lo) // 2
            prefix = job['key'].split(':')[0]
            suffix = f":{p['rds']}" if 'rds' in p else ''
            for start, end in ((lo, mid), (mid + timedelta(days=1), hi)):
                # A facet half keeps its original window, which finish_facets groups by.
                self.enqueue(f'{prefix}:{start}:{end}{suffix}', job['stage'],
                             {**p, 'sd': start.isoformat(), 'ed': end.isoformat()})
            job['status'] = 'split'
            return
        if not rows and not s.explicit_empty(html) and total != 0:
            raise Incomplete('Empty search page without an explicit empty result')
        if any(r.date is None or not p['sd'] <= r.date <= p['ed'] for r in rows):
            raise Incomplete('Search returned a missing/out-of-range date; cursor retained')
        ids = set(job['ids'])
        if rows and all(row.document_id in ids for row in rows):
            raise Incomplete('Repeated search page; cursor retained')
        observed_at = s.now()
        for row in rows:
            self.record(row, facet=(p['category'], p['label']) if job['stage'] == 'facets' else None, observed_at=observed_at)
            if job['stage'] == 'facets':
                job.setdefault('observations', {})[row.document_id] = observed_at
            ids.add(row.document_id)
        job['observed_at'] = observed_at
        job['ids'] = sorted(ids)
        job['offset'] += size
        job['pages'] += 1
        # Totals are approximate; actual short pages end pagination. A job
        # narrowed to a block of results also ends at that block's last one.
        last = p.get('last')
        job['status'] = ('done' if len(rows) < size or (last and job['offset'] > last)
                         else 'pending')

    def narrow(self, job, size):
        """Work around a page REGDOCS fails to render, without losing the rest.

        A window longer than a day is halved, down to single days. A day goes
        on from the same result in pages of 20; a page of 20 that fails is read
        one result at a time in a job of its own, while the day moves on; a
        single result that still fails is a gap -- recorded, printed, and not
        held against the range. Facet jobs keep their window through every step.
        """
        p = job['payload']
        where = f"{p['sd']}..{p['ed']}{' ' + p['label'] if 'label' in p else ''}"
        if p['sd'] < p['ed']:
            lo, hi = date.fromisoformat(p['sd']), date.fromisoformat(p['ed'])
            mid = lo + (hi - lo) // 2
            prefix = job['key'].split(':')[0]
            suffix = f":{p['rds']}" if 'rds' in p else ''
            for start, end in ((lo, mid), (mid + timedelta(days=1), hi)):
                self.enqueue(f'{prefix}:{start}:{end}{suffix}', job['stage'],
                             {**p, 'sd': start.isoformat(), 'ed': end.isoformat()})
            job['status'] = 'split'
            print(f"  REGDOCS error on {where} at result {job['offset']}; splitting the window",
                  flush=True)
            return
        if size == 1:
            gap = {'date': p['sd'], 'result': job['offset'], 'filter': p.get('label'),
                   'found_at': s.now()}
            job.setdefault('gaps', []).append(gap)
            print(f"  GAP: REGDOCS cannot list result {job['offset']} of {where}; "
                  f"recorded, and the range goes on", flush=True)
            job['offset'] += 1
            job['status'] = 'done' if p.get('last') and job['offset'] > p['last'] else 'pending'
            return
        smaller = NARROWER[size]
        if smaller == 1:
            first, last = job['offset'], job['offset'] + size - 1
            self.enqueue(f"{job['key']}@{first}-{last}", job['stage'],
                         {**p, 'page_size': 1, 'last': last})
            # enqueue starts every job at result 1; this one starts at the block.
            sub = json.loads(self.job_path(f"{job['key']}@{first}-{last}").read_text())
            if sub['offset'] == 1 and first > 1:
                sub['offset'] = first
                self.save_job(sub)
            job['offset'] += size
            print(f"  REGDOCS error on {where}, results {first}-{last}; "
                  f"reading those one at a time", flush=True)
        else:
            job['payload'] = {**p, 'page_size': smaller}
            print(f"  REGDOCS error on {where} at result {job['offset']}; "
                  f"going on in pages of {smaller}", flush=True)
        job['status'] = 'pending'

    def container_page(self, http, job):
        parent = s.Row(**job['payload'])
        box_id = parent.document_id
        shell = s.VIEW_URL.format(id=box_id)
        if job['url'] is None:
            if getattr(http, 'direct_containers', False):
                direct = f'{s.DOMAIN}/REGDOCS/Item/LoadResult/{box_id}'
                ok, html, final, err = http.get(direct, ajax=True, referer=shell)
            else:
                direct, ok, html, final, err = None, False, None, None, None
            if not ok or not s.recognized(html or ''):
                ok, shell_html, shell_final, shell_err = http.get(shell)
                endpoint = s.container_endpoint(
                    shell_html or '', shell_final or shell, box_id) if ok else None
                if not endpoint and ok and regdocs_unavailable(shell_html):
                    observed_at = s.now()
                    job.setdefault('gaps', []).append({
                        'container': box_id, 'reason': 'unavailable',
                        'url': shell_final or shell, 'found_at': observed_at,
                    })
                    job.update(status='done', observed_at=observed_at)
                    print(f'  GAP: container {box_id}: REGDOCS says it cannot locate '
                          'this item; recorded, and the range goes on', flush=True)
                    return
                if not endpoint:
                    raise Incomplete(shell_err or err or 'No container member-list endpoint')
                job['url'] = endpoint
                return
            job['url'] = direct
        else:
            ok, html, final, err = http.get(job['url'], ajax=True, referer=shell)
        if not ok or not s.recognized(html or ''):
            raise Incomplete(err or 'Unrecognized container response')
        rows = s.parse_rows(html, container_id=box_id)
        total = s.parse_total(html)
        # A container may report members while publicly listing none. Treat it
        # like every other repeated shortfall below: retry it before recording
        # a visible gap. Only an empty page with no count is unrecognizable.
        if not rows and not s.explicit_empty(html) and total is None:
            raise Incomplete('Empty container page without an explicit empty result')
        visited = job['visited'] + [job['url']]
        nxt = s.next_container_page(html, final or job['url'])
        if nxt and nxt in visited:
            raise Incomplete('Container pagination loop')
        ids = set(job['ids'])
        members = [r for r in rows if r.document_id != box_id]
        if members and all(r.document_id in ids for r in members):
            raise Incomplete('Container page added no members')
        observed_at = s.now()
        for row in members:
            self.record(row, parent=parent, observed_at=observed_at)
            ids.add(row.document_id)
        # Save useful metadata even if the final page proves traversal incomplete.
        if not nxt and ((total is None and rows) or (total is not None and len(ids) < total)):
            short = {'observed': len(ids), 'reported': total}
            seen = job.get('short') or {}
            times = seen['times'] + 1 if {k: seen.get(k) for k in short} == short else 1
            if times < SHORT_CONTAINER_TRIES:
                raise Incomplete(f'Container incomplete: observed {len(ids)}, reported {total} '
                                 f'({times} of {SHORT_CONTAINER_TRIES} runs)',
                                 keep={'short': {**short, 'times': times}})
            job.setdefault('gaps', []).append({'container': box_id, **short, 'found_at': observed_at})
            print(f'  GAP: container {box_id} lists {len(ids)} of a reported {total} on '
                  f'{times} runs; recorded, and the range goes on', flush=True)
        job.update(ids=sorted(ids), visited=visited, observed_at=observed_at, url=nxt or job['url'],
                   status='pending' if nxt else 'done', pages=job['pages'] + 1)

    def finish_facets(self):
        """Apply all completed categories in one write per document, using fetch times."""
        jobs_snapshot = self.jobs()
        groups = {}
        for job in jobs_snapshot:
            if job['stage'] == 'facets' and job['status'] != 'split':
                # Checkpoints from before splitting have no window: their own dates are it.
                p = job['payload']
                lo, hi = p.get('window') or (p['sd'], p['ed'])
                groups.setdefault((lo, hi, p['category']), []).append(job)
        observed = {doc_id for job in jobs_snapshot for doc_id in job['ids']}
        records, updates, applied = None, {}, {}
        for (lo, hi, category), jobs in groups.items():
            if any(j['status'] != 'done' for j in jobs):
                continue
            if any(not j.get('observed_at') for j in jobs):
                print('Older facet checkpoints lack observation times; use --refresh to recheck them.')
                continue
            key = json.dumps([lo, hi, category])
            if records is None:
                records = pdf_queue(self.connection, include_date=True)
            eligible = [(filename, doc_id) for filename, doc_id, day in records
                        if doc_id in observed and day and lo <= day <= hi]
            signature = hashlib.sha256(json.dumps(eligible).encode()).hexdigest()
            if self.config['applied'].get(key) == signature:
                continue
            labels, times = {}, {}
            completed_at = max(j['observed_at'] for j in jobs)
            for job in jobs:
                for doc_id in job['ids']:
                    labels.setdefault(doc_id, []).append(job['payload']['label'])
                    times[doc_id] = max(times.get(doc_id, ''),
                        job.get('observations', {}).get(doc_id, job['observed_at']))
            for filename, doc_id in eligible:
                updates.setdefault((filename, doc_id), []).append(
                    (category, labels.get(doc_id, []), times.get(doc_id, completed_at)))
            applied[key] = signature
        for (filename, doc_id), categories in updates.items():
            old = s.read_json(Path(filename))
            rec = old
            for category, labels, observed_at in categories:
                rec, _ = s.merge(rec, doc_id, {}, {category: labels},
                                 {category: True}, True, [], set(), observed_at)
            if rec != old:
                s.write_json(Path(filename), rec)
                index_record(self.connection, filename, rec)
        # A crash during writes replays the group; merges are idempotent.
        if applied:
            self.config['applied'].update(applied)
            self.save_config()

    def run(self, http, stage, max_requests=0):
        initial, started = http.requests, time.monotonic()
        reported = initial
        stages = stages_for(stage)
        for job in self.jobs():
            if job['status'] == 'failed' and job['stage'] in stages:
                job['status'] = 'pending'
                self.save_job(job)
        try:
            for current in stages:
                if max_requests and http.requests - initial >= max_requests:
                    break
                if current == 'containers':
                    reused = self.reuse_pending_containers()
                    if reused:
                        print(f'  reused {reused} completed containers from other saved ranges',
                              flush=True)
                if current == 'facets':
                    self.prepare_facets(http)
                while not max_requests or http.requests - initial < max_requests:
                    batch = self.jobs(current, pending=True)
                    if not batch:
                        break
                    for job in batch:
                        while job['status'] == 'pending':
                            if max_requests and http.requests - initial >= max_requests:
                                break
                            try:
                                if current == 'containers':
                                    self.container_page(http, job)
                                else:
                                    self.search_page(http, job)
                            except (Incomplete, s.SearchRejected, s.TooManyFailures) as exc:
                                # Reload the last durable cursor, not a partially changed job.
                                job = json.loads(self.job_path(job['key']).read_text())
                                job.update(status='failed', error=str(exc), **getattr(exc, 'keep', {}))
                                self.save_job(job)
                                print(f"FAILED {job['key']}: {exc}", flush=True)
                                if isinstance(exc, s.TooManyFailures):
                                    raise
                                break
                            job['last_successful_request_at'] = s.now()
                            self.config['last_successful_request_at'] = job['last_successful_request_at']
                            job['error'] = None
                            self.save_job(job)
                            if http.requests - reported >= 100:
                                reported = http.requests
                                lo, hi = self.config['range']
                                print(f'  {lo}..{hi} {current}: {http.requests - initial} requests so far',
                                      flush=True)
                        if max_requests and http.requests - initial >= max_requests:
                            break
            self.finish_facets()
        finally:
            self.config['requests'] += http.requests - initial
            self.config['elapsed_seconds'] += time.monotonic() - started
            self.save_config()

    def counts(self):
        """PDFs this crawl has seen: (all, dated in range, not downloaded,
        in range with incomplete facets, download metadata to reconcile)."""
        observed = sorted({i for j in self.jobs() for i in j['ids']})
        ensure_records(self.connection)
        self.connection.execute('CREATE OR REPLACE TEMP TABLE scoped_ids AS SELECT unnest(?::VARCHAR[]) AS doc_id', [observed])
        counts = self.connection.execute("""SELECT count(*),
            count(*) FILTER (WHERE day BETWEEN ? AND ?),
            count(*) FILTER (WHERE r.doc_id NOT IN (SELECT doc_id FROM source_files)),
            count(*) FILTER (WHERE NOT facets_complete AND day BETWEEN ? AND ?),
            count(*) FILTER (WHERE NOT download_complete AND r.doc_id IN (SELECT doc_id FROM source_files))
            FROM records r JOIN scoped_ids USING(doc_id)
            WHERE contains(lower(coalesce(kind,'')), 'pdf')""", self.config['range'] * 2).fetchone()
        return counts

    def status(self):
        for stage, state, count in self.connection.execute(
                'SELECT stage,status,count(*) FROM jobs GROUP BY stage,status ORDER BY stage,status').fetchall():
            print(f'{stage:12} {state:8} {count}')
        jobs = self.jobs()
        counts = self.counts()
        print(f'Discovered PDFs: {counts[0]} ({counts[1]} dated in range; {counts[0] - counts[1]} linked outside/undated)')
        # Outside-range PDFs get their facets from a crawl of their own dates.
        print(f'Missing downloads: {counts[2]}; in range with incomplete facets: {counts[3]}; download metadata to reconcile: {counts[4]}')
        failures = [j for j in jobs if j['status'] == 'failed']
        print(f'Failed jobs: {len(failures)}')
        gaps = [(j['key'], g) for j in jobs for g in j.get('gaps', [])]
        if gaps:
            print(f'Results REGDOCS cannot list (gaps): {len(gaps)}')
            for key, g in gaps[:20]:
                if g.get('reason') == 'unavailable':
                    print(f"  container {g['container']}: REGDOCS cannot locate this item")
                elif 'container' in g:
                    print(f"  container {g['container']}: {g['observed']} of {g['reported']} listed")
                else:
                    print(f"  {g['date']} result {g['result']}{' (' + g['filter'] + ')' if g['filter'] else ''}")
        last = max([self.config.get('last_successful_request_at') or ''] +
                   [j.get('last_successful_request_at') or j.get('observed_at') or '' for j in jobs])
        print(f'Last successful request: {last or "none recorded"}')
        requests, elapsed = self.config['requests'], self.config['elapsed_seconds']
        print(f'Recorded requests: {requests}; elapsed: {elapsed / 3600:.2f} hours')
        if requests and elapsed:
            print(f'Observed rate: {requests / elapsed * 3600:.0f} requests/hour')
        for job in failures[:10]:
            print(f"  {job['key']}: {job['error']}")
        print('ETA unavailable until the remaining search/container pages are known.')


def command(args):
    if args.cmd == 'status':
        paths = [Path(args.state)] if args.state else sorted(
            p.parent for p in s.STATE_ROOT.glob('*/run.json'))
        if not paths:
            print('No saved crawls yet. Start with discover --from YYYY-MM-DD --to YYYY-MM-DD.')
            return 0
        if any(not (path / 'run.json').exists() for path in paths):
            print('No crawl state at the requested directory')
            return 1
        # Status must not create/rewrite jobs or config.
        with duckdb.connect(':memory:') as connection:
            for path in paths:
                crawl = Crawl.__new__(Crawl)
                crawl.path = path
                crawl.config = json.loads((path / 'run.json').read_text())
                crawl.jobs_dir = path / crawl.config.get('jobs_directory', 'jobs')
                crawl.connection = connection
                crawl.load_jobs()
                print(f"{crawl.config['range'][0]} .. {crawl.config['range'][1]} ({path})")
                crawl.status()
        return 0
    try:
        start, end = s.parse_day(args.date_from), s.parse_day(args.date_to)
    except ValueError as exc:
        raise UsageError(exc) from None
    if (start > end or args.max_requests < 0 or args.min_delay < 0
            or args.max_delay < args.min_delay):
        raise UsageError('Invalid date range, request limit, or delay range')
    path = Path(args.state) if args.state else s.STATE_ROOT / f'{start}_{end}'
    # Separate runs still write the same CER records: serialize scout writers.
    with exclusive(path / '.lock'):
        crawl = Crawl(path, start, end, refresh=args.refresh)
        http = s.Http(min_delay=args.min_delay, max_delay=args.max_delay)
        try:
            crawl.run(http, args.cmd, args.max_requests)
            stages = stages_for(args.cmd)
            jobs = [j for j in crawl.jobs() if j['stage'] in stages]
            failed = sum(j['status'] == 'failed' for j in jobs)
            left = sum(j['status'] not in ('done', 'split', 'failed') for j in jobs)
            if 'facets' in stages and crawl.config['catalog'] is None:
                left += 1  # the facet searches have not been listed yet
            outcome = (f'unfinished ({failed} failed, {left} left); rerun to resume'
                       if failed or left else 'done')
            # One line per command; `status` has the detail.
            print(f'{args.cmd} {start}..{end}: {crawl.counts()[0]} PDFs, '
                  f'{http.requests} requests, {outcome}', flush=True)
            if failed or left:
                return 1
            crawl.drop_replaced()
            return 0
        finally:
            http.close()
            crawl.close()
