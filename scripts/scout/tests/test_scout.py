import sys
from contextlib import redirect_stdout
from dataclasses import asdict
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scout as s  # noqa: E402

FIXTURES = Path(__file__).parent / 'fixtures'
DAY = '2026-07-02'
SHELL = '<section id="section-items" data-url="/REGDOCS/Item/LoadResult/10"></section>'


def fixture(name):
    html = (FIXTURES / name).read_text()
    return html, s.parse_html(html)


def page(ids, total=None, kind='PDF Document', day=DAY, next_url=None):
    rows = ''.join(f'<tr><td><a href="/REGDOCS/{"Item/View" if kind == "Folder" else "File/Download"}/{i}">'
                   f'Receipt {i}</a><i title="{kind}"></i></td><td>{day}</td><td>Company</td></tr>'
                   for i in ids)
    text = (f'Item(s) - 1 to {len(ids)} out of about {total if total is not None else len(ids)}'
            if ids else 'No results found')
    if next_url:
        text += f'<a rel="next" href="{next_url}">Next</a>'
    return f'{text}<table><tbody>{rows}</tbody></table>'


def folder(doc_id='10', day=DAY):
    return s.Row(doc_id, 'Folder', s.VIEW_URL.format(id=doc_id), False, day, 'Company', kind='Folder')


class Http:
    """Answers each get() with the next response; None is a failed request."""

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = 0
        self.calls = []

    def get(self, url, **kwargs):
        self.requests += 1
        self.calls.append((url, kwargs))
        response = next(self.responses)
        return (False, None, None, 'temporary error') if response is None else (True, response, url, None)

    def close(self):
        pass


class RealPageTests(unittest.TestCase):
    """Pages saved from REGDOCS: no real row may be rejected as unparsed."""

    def test_day_search(self):
        html, soup = fixture('search_2026-01-05.html')
        rows = s.parse_rows(soup)
        self.assertEqual((len(rows), s.parse_total(html)), (30, 30))
        self.assertEqual({r.kind for r in rows},
                         {'PDF Document', 'Html Document', 'Folder', 'Compound Document'})
        self.assertTrue(all(r.date == '2026-01-05' for r in rows))

    def test_container_of_folders(self):
        # Sub-folders are listed in a single cell, with no date or submitter.
        html, soup = fixture('container_4517705.html')
        rows = s.parse_rows(soup, container_id='4517705')
        self.assertEqual([r.document_id for r in rows], ['4644138', '4644139', '4644140', '4644141'])
        self.assertTrue(all(r.kind == 'Folder' and r.date is None and not r.is_file for r in rows))

    def test_hyperlink_items_are_skipped_and_next_page_found(self):
        html, soup = fixture('search_2002-02_offset1801.html')
        rows = s.parse_rows(soup)
        self.assertEqual(len(rows), 198)  # a full page of 200, less two web links
        target = httpx.URL(s.next_container_page(soup, s.MEMBERS_URL.format(id='10')))
        self.assertEqual(dict(target.params), {'sd': '2002-02-01', 'ed': '2002-02-28',
                                               'srt': '21', 'sr': '2001'})

    def test_final_container_page_has_no_next(self):
        html, soup = fixture('container_4633939.html')
        self.assertEqual((len(s.parse_rows(soup, container_id='4633939')), s.parse_total(html)), (2, 2))
        self.assertIsNone(s.next_container_page(soup, s.MEMBERS_URL.format(id='10')))

    def test_empty_day(self):
        html, soup = fixture('search_2026-01-04.html')
        self.assertEqual(s.parse_rows(soup), [])
        self.assertTrue(s.recognized(html, soup) and s.explicit_empty(soup))

    def test_unusable_next_link_is_rejected(self):
        with self.assertRaises(s.SearchRejected):
            s.next_container_page(s.parse_html('<a href="#" rel="next">Next</a>'), s.DOMAIN)


class HelperTests(unittest.TestCase):
    def test_parse_day(self):
        self.assertEqual(s.parse_day('2026-02-30'), '2026-02-28')
        for bad in ('2026-13-01', '26-01-01', '2026-01'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                s.parse_day(bad)

    def test_windows_cover_leap_day(self):
        self.assertEqual(list(s.windows('2024-02-28', '2024-03-02')),
                         [('2024-02-28', '2024-02-29'), ('2024-03-01', '2024-03-02')])

    def test_http_retries_then_recovers_and_keeps_the_endpoint_query(self):
        statuses, received = iter([503, 200]), []

        def respond(request):
            received.append(request.url)
            return httpx.Response(next(statuses), text='ok')

        http = s.Http(min_delay=0, max_delay=0)
        http.client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch.object(s.time, 'sleep'):
            self.assertTrue(http.get(s.DOMAIN + '/x?loc=10&sr=1', params={'sr': 201})[0])
        self.assertEqual(http.requests, 2)
        self.assertTrue(1.0 < http.delay_multiplier < 2.0)
        self.assertEqual(dict(received[-1].params), {'loc': '10', 'sr': '201'})


class StandInTests(unittest.TestCase):
    def test_stand_in_keeps_listings_not_history(self):
        rec = None
        for title, day, filing, box in (('A1 Lettre', '2017-01-02', 'A1', '10'),
                                        ('A2 Ordonnance', '2018-03-04', 'A2', '20'),
                                        ('A1 Lettre', '2017-01-02', 'A1', '10')):
            rec, changes = s.merge(rec, '41059', {'title': title, 'date': day, 'filing': {'number': filing}},
                                   f'{day}T00:00:00+00:00', containers=[{'id': box, 'kind': 'Folder'}])
        self.assertEqual((changes, rec['changes']), ([], []))
        self.assertEqual((rec['title'], rec['date']), ('TranslationPending e.pdf', '2017-01-02'))
        self.assertEqual([l['filing'] for l in rec['listings']], ['A1', 'A2'])
        self.assertEqual({c['id'] for c in rec['containers']}, {'10', '20'})

    def test_ordinary_documents_keep_history(self):
        obs = {'title': 'One', 'date': '2017-01-02', 'company': {'name': 'X'}}
        rec, _ = s.merge(None, '1', obs, '2026-01-01T00:00:00+00:00')
        rec, changes = s.merge(rec, '1', {'title': 'Two', 'company': {'name': None}},
                               '2026-01-02T00:00:00+00:00')
        self.assertEqual([c['field'] for c in changes], ['title'])
        self.assertEqual(rec['company']['name'], 'X')  # a blank never erases


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in (('OUT', self.root / 'output'), ('SOURCE', self.root / 'source'),
                            ('STATE_ROOT', self.root / 'scout-state'), ('PAGE_SIZE', 2)):
            p = patch.object(s, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.a = self.open()

    def open(self, start=DAY, end=DAY, name='state', refresh=False):
        return s.Crawl.open(self.root / 'scout-state' / name, start, end, refresh)

    def quiet_run(self, crawl, responses, stage, limit=0):
        http = Http(responses)
        with redirect_stdout(StringIO()):
            crawl.run(http, stage, limit)
        return http


class CrawlTests(Base):
    def test_resume_from_next_page(self):
        self.a.run(Http([page(['1', '2'], 3)]), 'inventory', 1)
        self.assertEqual(s.read_record('1')['title'], 'Receipt 1')
        self.a = self.open()
        http = Http([page(['3'], 3)])
        self.a.run(http, 'inventory')
        self.assertEqual(http.calls[0][1]['params']['sr'], 3)
        self.assertEqual(self.a.observed(), {'1', '2', '3'})

    def test_failed_page_resumes_without_replaying_success(self):
        self.quiet_run(self.a, [page(['1', '2'], 3), None], 'inventory')
        self.assertEqual(self.a.listed()[0]['status'], 'failed')
        http = self.quiet_run(self.a, [page(['3'], 3)], 'inventory')
        self.assertEqual(http.calls[0][1]['params']['sr'], 3)

    def test_bad_pages_never_advance_the_cursor(self):
        broken = page(['1']).replace('</tbody>', '<tr><td>No link</td><td>x</td><td>y</td></tr></tbody>')
        for responses in ([page(['1'], day='2002-01-11')], [broken],
                          [page(['1', '2'], 4), page(['1', '2'], 4)]):
            with self.subTest(responses=responses):
                crawl = self.open(name=str(len(responses)) + responses[0][:20])
                self.quiet_run(crawl, responses, 'inventory')
                job = crawl.listed()[0]
                self.assertEqual(job['status'], 'failed')
                if len(responses) == 1:
                    self.assertFalse(s.record_path('1').exists())

    def test_crash_mid_page_replays_without_duplicate_history(self):
        write = s.write_json

        def fail_second(path, value):
            if path == s.record_path('2'):
                raise OSError('disk full')
            write(path, value)
        with patch.object(s, 'write_json', side_effect=fail_second), self.assertRaises(OSError):
            self.a.run(Http([page(['1', '2'])]), 'inventory')
        self.assertEqual(self.a.listed()[0]['offset'], 1)
        self.a.run(Http([page(['1', '2']), page([])]), 'inventory')
        self.assertEqual(s.read_record('1')['changes'], [])
        self.assertTrue(s.record_path('2').exists())

    def test_large_window_splits(self):
        crawl = self.open('2026-07-01', '2026-07-04', 'big')
        crawl.run(Http([page(['1', '2'], 12000)]), 'inventory', 1)
        self.assertEqual(sorted(j['status'] for j in crawl.listed()), ['pending', 'pending', 'split'])

    def test_regdocs_error_page_narrows_to_a_gap(self):
        error = 'REGDOCS has encountered an unexpected error'
        with patch.dict(s.NARROWER, {2: 1}, clear=True):
            self.quiet_run(self.a, [error, page([]), error, page(['2'])], 'inventory')
        jobs = self.a.listed()
        self.assertTrue(all(j['status'] == 'done' for j in jobs))
        self.assertEqual([g['result'] for j in jobs for g in j.get('gaps', [])], [1])
        self.assertTrue(s.record_path('2').exists())

    def test_nested_containers_record_membership(self):
        self.a.run(Http([page(['10'], kind='Folder')]), 'inventory')
        nxt = s.MEMBERS_URL.format(id='10') + '?page=2'
        self.a.run(Http([page(['20', '1'], 3, next_url=nxt).replace('Receipt 20</a><i title="PDF Document"',
                                                                     'Receipt 20</a><i title="Folder"')]),
                   'containers', 1)
        http = Http([page(['2'], 3), page(['10'], kind='Folder')])
        self.a.run(http, 'containers')
        self.assertEqual(http.calls[0][0], nxt)
        self.assertEqual(s.read_record('2')['containers'][0]['id'], '10')
        self.assertEqual({j['key']: j['status'] for j in self.a.listed('containers')},
                         {'container:10': 'done', 'container:20': 'done'})

    def test_container_falls_back_to_the_endpoint_its_page_declares(self):
        self.a.enqueue('container:10', 'containers', asdict(folder()))
        http = self.quiet_run(self.a, ['<html>Maintenance</html>', SHELL, page(['1'])], 'containers')
        self.assertEqual(http.requests, 3)
        self.assertEqual(self.a.listed('containers')[0]['status'], 'done')

    def test_container_pagination_loop_fails(self):
        url = s.MEMBERS_URL.format(id='10')
        self.a.enqueue('container:10', 'containers', asdict(folder()))
        self.quiet_run(self.a, [page(['1'], 2, next_url=url)], 'containers')
        self.assertEqual(self.a.listed('containers')[0]['status'], 'failed')

    def test_container_short_the_same_way_becomes_a_gap(self):
        self.a.enqueue('container:10', 'containers', asdict(folder()))
        for total in (3, 4, 3, 3):  # a different shortfall starts the count again
            self.quiet_run(self.a, [page(['1', '2'], total)], 'containers')
            self.assertEqual(self.a.listed('containers')[0]['status'], 'failed')
        self.quiet_run(self.a, [page(['1', '2'], 3)], 'containers')
        job = self.a.listed('containers')[0]
        self.assertEqual(job['status'], 'done')
        self.assertEqual((job['gaps'][0]['observed'], job['gaps'][0]['reported']), (2, 3))

    def test_unavailable_container_is_a_gap_but_a_vague_error_is_not(self):
        html = (FIXTURES / 'unavailable_2413807.html').read_text()
        self.a.enqueue('container:2413807', 'containers', asdict(folder('2413807')))
        self.quiet_run(self.a, [None, html], 'containers')
        self.assertEqual(self.a.listed('containers')[0]['gaps'][0]['reason'], 'unavailable')
        b = self.open(name='b')
        b.enqueue('container:10', 'containers', asdict(folder()))
        self.quiet_run(b, [None, '<div class="panel-warning">Looking for Document or Filing?</div>'],
                       'containers')
        self.assertEqual(b.listed('containers')[0]['status'], 'failed')

    def test_completed_containers_are_reused_by_other_ranges(self):
        other = self.open('2025-01-01', '2025-12-31', 'other')
        other.enqueue('container:10', 'containers', asdict(folder()))
        other.run(Http([page(['20'], kind='Folder'), page(['1'])]), 'containers')
        self.a.enqueue('container:10', 'containers', asdict(folder()))
        http = self.quiet_run(self.a, [], 'containers')
        self.assertEqual(http.requests, 0)
        self.assertEqual({j['key'] for j in self.a.listed('containers') if j.get('reused_at')},
                         {'container:10', 'container:20'})
        fresh = self.open(name='fresh', refresh=True)
        fresh.enqueue('container:10', 'containers', asdict(folder()))
        self.assertEqual(fresh.reuse_pending_containers(), 0)

    def test_facets_replace_only_when_every_search_finished(self):
        self.a.run(Http([page(['1'])]), 'inventory')
        unrelated = s.Row('99', 'Keep', s.VIEW_URL.format(id='99'), True, DAY, '', 'PDF Document')
        self.a.record(unrelated, facet=('Document Type', 'Letter'))
        rec = s.read_record('1')
        rec['facets'] = {'Document Type': ['Old']}
        s.write_json(s.record_path('1'), rec)
        self.a.config['catalog'] = {'Document Type': {'66': 'Receipt', '53': 'Letter'}}
        self.quiet_run(self.a, [page([]), None], 'facets')  # Letter finishes, Receipt fails
        self.assertEqual(s.read_record('1')['facets']['Document Type'], ['Old'])
        self.a.run(Http([page(['1'])]), 'facets')
        rec = s.read_record('1')
        self.assertEqual(rec['facets']['Document Type'], ['Receipt'])
        self.assertTrue(rec['facets_complete']['Document Type'])
        self.assertEqual(s.read_record('99')['facets']['Document Type'], ['Letter'])

    def test_older_facet_checkpoints_cannot_replace_newer_observations(self):
        with patch.object(s, 'now', return_value='2026-07-03T01:00:00+00:00'):
            self.a.run(Http([page(['1'])]), 'inventory')
            self.a.config['catalog'] = {'Document Type': {'66': 'Receipt'}}
            with patch.object(self.a, 'finish_facets'):
                self.a.run(Http([page(['1'])]), 'facets')
        rec, _ = s.merge(s.read_record('1'), '1', {}, '2026-09-24T01:00:00+00:00',
                         facets={'Document Type': ['Letter']}, final=True)
        s.write_json(s.record_path('1'), rec)
        self.a.finish_facets()
        self.assertEqual(s.read_record('1')['facets']['Document Type'], ['Letter'])

    def test_status_counts_only_this_range(self):
        self.a.run(Http([page(['1'])]), 'inventory')
        self.a.record(s.Row('99', 'Unrelated', '', True, DAY, '', 'PDF Document'))
        self.a.enqueue('container:10', 'containers', asdict(folder()))
        self.quiet_run(self.a, [page(['2'], day='2025-01-01')], 'containers')
        out = StringIO()
        with redirect_stdout(out):
            s.Crawl.load(self.a.path).status()
        self.assertIn('Discovered PDFs: 2 (1 dated in range; 1 linked outside/undated)', out.getvalue())
        self.assertIn('Missing downloads: 2; in range with incomplete facets: 1', out.getvalue())


class CommandTests(Base):
    """The command line, end to end, with REGDOCS faked."""

    def cli(self, *argv, http=None):
        out = StringIO()
        with patch.object(s, 'Http', return_value=http or Http([])), redirect_stdout(out):
            code = s.main(list(argv))
        return code, out.getvalue()

    def test_discover_prints_one_line_then_status_is_offline(self):
        code, out = self.cli('discover', '--from', DAY, '--to', DAY, http=Http([page(['1'])]))
        self.assertEqual((code, out), (0, f'discover {DAY}..{DAY}: 1 PDFs, 1 requests, done\n'))
        before = {p: p.read_bytes() for p in (self.root / 'scout-state').rglob('*.json')}
        with patch.object(s, 'Http', side_effect=AssertionError('status must be offline')), \
                redirect_stdout(StringIO()):
            self.assertEqual(s.main(['status']), 0)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_bad_date_is_refused(self):
        code, out = self.cli('discover', '--from', '2026-13-01', '--to', DAY)
        self.assertEqual(code, 2)
        self.assertIn('month must be 1-12', out)

    def test_refresh_fetches_again_keeps_downloads_and_drops_old_checkpoints(self):
        dates = ('--from', DAY, '--to', DAY)
        self.cli('discover', *dates, http=Http([page(['1'])]))
        state = self.root / 'scout-state' / f'{DAY}_{DAY}'
        old_jobs = s.Crawl.load(state).jobs_dir
        rec = s.read_record('1')
        rec['download'] = {'sha256': 'kept'}
        s.write_json(s.record_path('1'), rec)
        code, _ = self.cli('discover', *dates, '--refresh',
                           http=Http([page(['1']).replace('Receipt 1', 'Updated')]))
        self.assertEqual(code, 0)
        self.assertEqual((s.read_record('1')['title'], s.read_record('1')['download']['sha256']),
                         ('Updated', 'kept'))
        self.assertFalse(old_jobs.exists())

    def test_writers_share_one_lock(self):
        with s.exclusive(s.OUT / '.scout-writer.lock'):
            self.assertEqual(self.cli('download')[0], 1)
            self.assertEqual(self.cli('status')[0], 0)


class DownloadTests(Base):
    def http(self, answer):
        """An Http whose downloads answer answer(doc_id) -> (status, body)."""
        http = s.Http()
        http._pace = lambda: None
        http.client = MagicMock()

        def stream(method, url, **kwargs):
            status, body = answer(url.rsplit('/', 1)[-1])
            response = MagicMock(status_code=status, url=url, headers={'content-type': 'text/html'})
            response.iter_bytes.return_value = [body]
            context = MagicMock()
            context.__enter__.return_value = response
            return context
        http.client.stream.side_effect = stream
        return http

    def download(self, http, unavailable=s.MAX_UNAVAILABLE_IN_ROW):
        self.a.run(Http([page([str(i) for i in range(1, 8)], 7), page([])]), 'inventory')
        with patch.object(s, 'Http', return_value=http), patch.object(s.time, 'sleep'), \
                patch.object(s, 'MAX_UNAVAILABLE_IN_ROW', unavailable), redirect_stdout(StringIO()):
            return s.main(['download'])

    def test_files_answered_for_alone_do_not_stop_the_run(self):
        for status, body, calls in ((404, b'', 7), (200, b'<html>gone</html>', 7)):
            with self.subTest(status=status):
                http = self.http(lambda d: (200, b'%PDF-1.7 ' + d.encode()) if d == '7' else (status, body))
                self.assertEqual(self.download(http), 1)
                self.assertEqual(http.client.stream.call_count, calls)
                self.assertEqual(s.read_record('7')['download']['size_bytes'], 10)
                self.assertTrue((s.SOURCE / '7.pdf').exists())
                (s.SOURCE / '7.pdf').unlink()
                for d in map(str, range(1, 8)):  # the next subtest starts from unmarked records
                    rec = s.read_record(d)
                    rec['download'] = None
                    s.write_json(s.record_path(d), rec)

    def test_an_html_item_is_excluded_not_retried(self):
        http = self.http(lambda d: (200, b'<!DOCTYPE html><html>a receipt</html>'))
        self.download(http)
        self.assertEqual(http.client.stream.call_count, 7)       # once each, no retries
        self.assertEqual(s.read_record('1')['download']['excluded'], s.HTML_ITEM)
        again = self.http(lambda d: (200, b''))
        with patch.object(s, 'Http', return_value=again), redirect_stdout(StringIO()):
            s.main(['download', '--retry-unavailable'])
        self.assertEqual(again.client.stream.call_count, 0)

    def test_a_404_is_remembered_and_skipped_next_time(self):
        http = self.http(lambda d: (404, b''))
        self.download(http)
        self.assertEqual(s.read_record('1')['download']['unavailable'], 'HTTP 404')
        again = self.http(lambda d: (404, b''))
        with patch.object(s, 'Http', return_value=again), redirect_stdout(StringIO()):
            self.assertEqual(s.main(['download']), 0)
        self.assertEqual(again.client.stream.call_count, 0)

    def test_an_empty_file_is_remembered_not_retried_and_does_not_stop_the_run(self):
        http = self.http(lambda d: (200, b'%PDF-1.7 ' + d.encode()) if d == '7' else (200, b''))
        self.assertEqual(self.download(http), 1)
        self.assertEqual(http.client.stream.call_count, 7)       # once each, no retries
        self.assertEqual(s.read_record('1')['download']['unavailable'], s.EMPTY_FILE)
        self.assertTrue((s.SOURCE / '7.pdf').exists())
        self.assertFalse((s.SOURCE / '1.pdf').exists())
        again = self.http(lambda d: (200, b''))
        with patch.object(s, 'Http', return_value=again), redirect_stdout(StringIO()):
            self.assertEqual(s.main(['download']), 0)
        self.assertEqual(again.client.stream.call_count, 0)

    def test_stops_when_regdocs_keeps_failing(self):
        http = self.http(lambda d: (503, b''))
        self.assertEqual(self.download(http), 1)
        self.assertEqual(http.client.stream.call_count, s.MAX_FAILURES_IN_ROW * (s.MAX_RETRIES + 1))

    def test_stops_when_every_file_is_unavailable(self):
        http = self.http(lambda d: (404, b''))
        self.assertEqual(self.download(http, unavailable=3), 1)
        self.assertEqual(http.client.stream.call_count, 3)

    def named(self, http, argv, record=None):
        """Seven records, then download with argv; record stands in for record_documents."""
        self.a.run(Http([page([str(i) for i in range(1, 8)], 7), page([])]), 'inventory')
        out = StringIO()
        with patch.object(s, 'Http', return_value=http), patch.object(s.time, 'sleep'), \
                patch.object(s, 'record_documents', record or MagicMock()), redirect_stdout(out):
            code = s.main(['download', *argv])
        return code, out.getvalue()

    def pdf(self):
        return self.http(lambda d: (200, b'%PDF-1.7 ' + d.encode()))

    def test_named_ids_and_links_download_only_those_once_each(self):
        http = self.pdf()
        code, _ = self.named(http, ['2', f'{s.DOMAIN}/REGDOCS/File/Download/5',
                                    f'{s.DOMAIN}/REGDOCS/Item/View/2?lang=en'])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.name for p in s.SOURCE.iterdir()), ['2.pdf', '5.pdf'])
        self.assertEqual(http.client.stream.call_count, 2)

    def test_a_list_file_with_comments(self):
        ids = self.root / 'ids.txt'
        ids.write_text('# wanted\n3\n\n6   # the sixth\n')
        code, _ = self.named(self.pdf(), ['--list', str(ids)])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.name for p in s.SOURCE.iterdir()), ['3.pdf', '6.pdf'])

    def test_anything_but_regdocs_ids_and_links_is_refused_before_any_request(self):
        for item in ('https://example.com/REGDOCS/File/Download/4', 'Download/4', 'abc'):
            with self.subTest(item=item):
                http = self.pdf()
                code, out = self.named(http, ['1', item])
                self.assertEqual(code, 2)
                self.assertIn('refusing', out)
                self.assertEqual(http.client.stream.call_count, 0)

    def test_named_404_is_tried_again_but_html_and_files_in_source_are_not(self):
        self.a.run(Http([page([str(i) for i in range(1, 8)], 7), page([])]), 'inventory')
        for doc_id, mark in (('1', {'unavailable': 'HTTP 404'}), ('2', {'excluded': s.HTML_ITEM})):
            rec = s.read_record(doc_id)
            rec['download'] = mark
            s.write_json(s.record_path(doc_id), rec)
        s.SOURCE.mkdir(parents=True)
        (s.SOURCE / '3.pdf').write_bytes(b'%PDF-1.7 kept')
        http = self.pdf()
        code, out = self.named(http, ['1', '2', '3'])
        self.assertEqual(code, 1)                                 # 2 cannot be downloaded
        self.assertEqual(http.client.stream.call_count, 1)        # only 1
        self.assertTrue((s.SOURCE / '1.pdf').exists())
        self.assertEqual((s.SOURCE / '3.pdf').read_bytes(), b'%PDF-1.7 kept')
        self.assertIn('2: not downloaded', out)

    def test_a_named_document_without_a_record_is_recorded_first(self):
        record = MagicMock()
        code, out = self.named(self.pdf(), ['4', '99'], record=record)
        record.assert_called_once_with(['99'])
        self.assertTrue((s.SOURCE / '4.pdf').exists())
        self.assertIn('99: not downloaded -- no record', out)    # still none after recording
        self.assertEqual(code, 1)


if __name__ == '__main__':
    unittest.main()
