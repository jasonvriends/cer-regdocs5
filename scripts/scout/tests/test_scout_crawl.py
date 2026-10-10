import sys
import json
import hashlib
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scout as s  # noqa: E402
from scout_crawl import Crawl, pdf_queue, windows, exclusive


def page(ids, total=None, kind='PDF Document', day='2026-07-02', next_url=None):
    rows = ''.join(f'<tr><td><a href="/REGDOCS/{"Item/View" if kind == "Folder" else "File/Download"}/{i}">Receipt {i}</a>'
                   f'<i title="{kind}"></i></td><td>{day}</td><td>Company</td></tr>' for i in ids)
    text = f'Item(s) - 1 to {len(ids)} out of about {total if total is not None else len(ids)}'
    if not ids:
        text = 'No results found'
    if next_url:
        text += f'<a rel="next" href="{next_url}">Next</a>'
    return f'{text}<table><tbody>{rows}</tbody></table>'


class Http:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = 0
        self.calls = []

    def get(self, url, **kwargs):
        self.requests += 1
        self.calls.append((url, kwargs))
        response = next(self.responses)
        if response is None:
            return False, None, None, 'temporary error'
        return True, response, url, None

    def succeeded(self):
        pass

    def refused(self, response):
        return response.status_code in s.RETRYABLE

    def slow_down(self):
        pass

    def gave_up(self, error):
        pass

    def close(self):
        pass


class CrawlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.out = patch.object(s, 'OUT', self.root / 'output')
        self.out.start()
        self.repo = patch.object(s, 'ROOT', self.root)
        self.repo.start()
        self.state = patch.object(s, 'STATE_ROOT', self.root / 'scout-state')
        self.state.start()
        self.size = patch.object(s, 'PAGE_SIZE', 2)
        self.size.start()
        self.a = Crawl(self.root / 'state', '2026-07-02', '2026-07-02')

    def tearDown(self):
        self.a.close()
        self.size.stop()
        self.state.stop()
        self.repo.stop()
        self.out.stop()
        self.tmp.cleanup()

    def test_resume_from_next_page_and_keep_receipts(self):
        self.a.run(Http([page(['1', '2'], 3)]), 'inventory', 1)
        self.assertTrue(s.record_path('1').exists())
        self.a.close()
        self.a = Crawl(self.root / 'state', '2026-07-02', '2026-07-02')
        h = Http([page(['3'], 3)])
        self.a.run(h, 'inventory')
        self.assertEqual(h.calls[0][1]['params']['sr'], 3)
        self.assertEqual(len({i for j in self.a.jobs() for i in j['ids']}), 3)
        self.assertEqual(s.read_record('1')['title'], 'Receipt 1')
        self.a.run(Http([]), 'inventory')

    def test_failed_page_retries_without_replaying_success(self):
        self.a.run(Http([page(['1', '2'], 3), None]), 'inventory')
        self.assertEqual(self.a.jobs()[0]['status'], 'failed')
        h = Http([page(['3'], 3)])
        self.a.run(h, 'inventory')
        self.assertEqual(h.calls[0][1]['params']['sr'], 3)

    def test_repeated_page_is_not_success(self):
        self.a.run(Http([page(['1', '2'], 4), page(['1', '2'], 4)]), 'inventory')
        row = self.a.jobs()[0]
        self.assertEqual((row['status'], row['offset']), ('failed', 3))

    def test_bad_dates_cannot_write_records(self):
        self.a.run(Http([page(['1'], day='2002-01-11')]), 'inventory')
        self.assertFalse(s.record_path('1').exists())
        self.assertEqual(len({i for j in self.a.jobs() for i in j['ids']}), 0)

    def test_checkpoint_survives_json_write_failure(self):
        with patch.object(s, 'write_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.a.run(Http([page(['1'])]), 'inventory')
        self.assertEqual(self.a.jobs()[0]['offset'], 1)
        self.a.run(Http([page(['1'])]), 'inventory')
        self.assertTrue(s.record_path('1').exists())

    def test_container_nested_cycle_and_relationships(self):
        self.a.run(Http([page(['10'], kind='Folder')]), 'inventory')
        shell10 = '<section id="section-items" data-url="/REGDOCS/Item/LoadResult/10"></section>'
        shell20 = '<section id="section-items" data-url="/REGDOCS/Item/LoadResult/20"></section>'
        self.a.run(Http([shell10, page(['20'], kind='Folder'), shell20,
                         page(['10'], kind='Folder')]), 'containers')
        self.assertEqual(len(self.a.jobs('containers')), 2)
        self.assertEqual(sum(len(j['ids']) for j in self.a.jobs('containers')), 2)

    def test_completed_containers_are_reused_recursively_across_ranges(self):
        self.a.close()
        source = Crawl(self.root / 'scout-state' / '2025-01-01_2025-12-31',
                       '2025-01-01', '2025-12-31')
        parent = s.Row('10', 'Parent', s.VIEW_URL.format(id='10'), False,
                       '2025-01-01', 'Company', kind='Folder')
        source.enqueue('container:10', 'containers', asdict(parent))
        shell10 = '<section id="section-items" data-url="/REGDOCS/Item/LoadResult/10"></section>'
        shell20 = '<section id="section-items" data-url="/REGDOCS/Item/LoadResult/20"></section>'
        source.run(Http([shell10, page(['20'], kind='Folder'), shell20, page(['1'])]),
                   'containers')
        source.close()

        self.a = Crawl(self.root / 'scout-state' / '2012-01-01_2012-12-31',
                       '2012-01-01', '2012-12-31')
        self.a.enqueue('container:10', 'containers', asdict(parent))
        http = Http([])
        with redirect_stdout(StringIO()):
            self.a.run(http, 'containers')
        jobs = {j['key']: j for j in self.a.jobs('containers')}
        self.assertEqual(http.requests, 0)
        self.assertEqual(set(jobs), {'container:10', 'container:20'})
        self.assertTrue(all(j['status'] == 'done' and j.get('reused_at') for j in jobs.values()))
        self.assertEqual({i for j in jobs.values() for i in j['ids']}, {'1', '20'})

    def test_recent_gap_is_reused_but_refresh_bypasses_it(self):
        self.a.close()
        source = Crawl(self.root / 'scout-state' / 'source', '2025-01-01', '2025-12-31')
        parent = s.Row('10', 'Parent', s.VIEW_URL.format(id='10'), False,
                       '2025-01-01', 'Company', kind='Folder')
        source.enqueue('container:10', 'containers', asdict(parent))
        job = source.jobs('containers')[0]
        job.update(status='done', ids=['1'], gaps=[{
            'container': '10', 'observed': 1, 'reported': 2, 'found_at': s.now()}])
        source.save_job(job)
        source.close()

        self.a = Crawl(self.root / 'scout-state' / 'target', '2012-01-01', '2012-12-31')
        self.a.enqueue('container:10', 'containers', asdict(parent))
        self.assertEqual(self.a.reuse_pending_containers(), 1)
        self.assertEqual(self.a.jobs('containers')[0]['status'], 'done')
        self.assertTrue(self.a.jobs('containers')[0]['gaps'])
        self.a.close()
        self.a = Crawl(self.root / 'scout-state' / 'refresh',
                       '2012-01-01', '2012-12-31', refresh=True)
        self.a.enqueue('container:10', 'containers', asdict(parent))
        self.assertEqual(self.a.reuse_pending_containers(), 0)
        self.assertEqual(self.a.jobs('containers')[0]['status'], 'pending')

    def test_expired_gap_is_not_reused(self):
        self.a.close()
        source = Crawl(self.root / 'scout-state' / 'source', '2025-01-01', '2025-12-31')
        parent = s.Row('10', 'Parent', s.VIEW_URL.format(id='10'), False,
                       '2025-01-01', 'Company', kind='Folder')
        source.enqueue('container:10', 'containers', asdict(parent))
        job = source.jobs('containers')[0]
        job.update(status='done', ids=['1'], observed_at='2000-01-01T00:00:00+00:00', gaps=[{
            'container': '10', 'observed': 1, 'reported': 2,
            'found_at': '2000-01-01T00:00:00+00:00'}])
        source.save_job(job)
        source.close()
        self.a = Crawl(self.root / 'scout-state' / 'target', '2012-01-01', '2012-12-31')
        self.a.enqueue('container:10', 'containers', asdict(parent))
        self.assertEqual(self.a.reuse_pending_containers(), 0)
        self.assertEqual(self.a.jobs('containers')[0]['status'], 'pending')

    def test_direct_container_endpoint_avoids_shell_request(self):
        parent = s.Row('10', 'Parent', s.VIEW_URL.format(id='10'), False,
                       '2026-07-02', 'Company', kind='Folder')
        self.a.enqueue('container:10', 'containers', asdict(parent))
        http = Http([page(['1'])])
        http.direct_containers = True
        self.a.run(http, 'containers')
        self.assertEqual(http.requests, 1)
        self.assertEqual(http.calls[0][0], s.DOMAIN + '/REGDOCS/Item/LoadResult/10')
        self.assertEqual(self.a.jobs('containers')[0]['status'], 'done')

    def test_container_page_resume(self):
        self.a.run(Http([page(['10'], kind='Folder')]), 'inventory')
        shell = '<section id="section-items" data-url="/REGDOCS/Item/LoadResult/10"></section>'
        nxt = s.DOMAIN + '/REGDOCS/Item/LoadResult/10?page=2'
        self.a.run(Http([shell, page(['1'], 2, next_url=nxt)]), 'containers', 2)
        h = Http([page(['2'], 2)])
        self.a.run(h, 'containers')
        self.assertEqual(h.calls[0][0], nxt)
        self.assertEqual(s.read_record('2')['containers'][0]['id'], '10')

    def test_container_loop_is_incomplete(self):
        self.a.run(Http([page(['10'], kind='Folder')]), 'inventory')
        url = s.DOMAIN + '/REGDOCS/Item/LoadResult/10'
        shell = f'<section id="section-items" data-url="{url}"></section>'
        self.a.run(Http([shell, page(['1'], 2, next_url=url)]), 'containers')
        self.assertEqual(self.a.jobs('containers')[0]['status'], 'failed')

    def test_container_short_the_same_way_becomes_a_gap(self):
        self.a.run(Http([page(['10'], kind='Folder')]), 'inventory')
        shell = '<section id="section-items" data-url="/REGDOCS/Item/LoadResult/10"></section>'
        with redirect_stdout(StringIO()):
            self.a.run(Http([shell, page(['1', '2'], 3)]), 'containers')
            self.a.run(Http([page(['1', '2'], 4)]), 'containers')  # a different shortfall restarts
            self.a.run(Http([page(['1', '2'], 3)]), 'containers')
            self.a.run(Http([page(['1', '2'], 3)]), 'containers')
            self.assertEqual(self.a.jobs('containers')[0]['status'], 'failed')
            self.a.run(Http([page(['1', '2'], 3)]), 'containers')
        job = self.a.jobs('containers')[0]
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['gaps'][0]['container'], '10')
        self.assertEqual((job['gaps'][0]['observed'], job['gaps'][0]['reported']), (2, 3))

    def test_container_reporting_only_hidden_members_becomes_a_gap(self):
        self.a.run(Http([page(['10'], kind='Folder')]), 'inventory')
        shell = '<section id="section-items" data-url="/REGDOCS/Item/LoadResult/10"></section>'
        hidden = 'Item(s) - 1 to 0 out of about 3<table><tbody></tbody></table>'
        with redirect_stdout(StringIO()):
            self.a.run(Http([shell, hidden]), 'containers')
            self.a.run(Http([hidden]), 'containers')
            self.assertEqual(self.a.jobs('containers')[0]['status'], 'failed')
            self.a.run(Http([hidden]), 'containers')
        job = self.a.jobs('containers')[0]
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['gaps'][0]['container'], '10')
        self.assertEqual((job['gaps'][0]['observed'], job['gaps'][0]['reported']), (0, 3))

    def test_unavailable_container_is_a_visible_gap(self):
        self.a.run(Http([page(['2413807'], kind='Folder')]), 'inventory')
        html = (FIXTURES / 'unavailable_2413807.html').read_text()
        with redirect_stdout(StringIO()):
            self.a.run(Http([html]), 'containers')
        job = self.a.jobs('containers')[0]
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['gaps'][0]['reason'], 'unavailable')
        self.assertEqual(job['gaps'][0]['url'], s.VIEW_URL.format(id='2413807'))
        self.assertTrue(job['gaps'][0]['found_at'])
        self.a.run(Http([]), 'containers')  # a resume does not retry the missing item
        output = StringIO()
        with redirect_stdout(output):
            self.a.status()
        self.assertIn('container 2413807: REGDOCS cannot locate this item', output.getvalue())

    def test_missing_container_endpoint_is_not_assumed_unavailable(self):
        self.a.run(Http([page(['10'], kind='Folder')]), 'inventory')
        for response in [None, '<html>Maintenance</html>',
                         '<div class="panel-warning">Looking for Document or Filing?</div>']:
            with self.subTest(response=response), redirect_stdout(StringIO()):
                self.a.run(Http([response]), 'containers')
                job = self.a.jobs('containers')[0]
                self.assertEqual(job['status'], 'failed')
                self.assertFalse(job.get('gaps'))

    def test_partial_facets_preserve_prior_values(self):
        self.a.run(Http([page(['1'])]), 'inventory')
        rec = s.read_record('1')
        rec['facets'] = {'Document Type': ['Old']}
        s.write_json(s.record_path('1'), rec)
        self.a.config['catalog'] = {'Document Type': {'66': 'Receipt', '53': 'Letter'}}
        self.a.save_config()
        # Letter finishes, Receipt fails: no replacement or complete flag.
        self.a.run(Http([page([]), None]), 'facets')
        self.assertEqual(s.read_record('1')['facets']['Document Type'], ['Old'])
        self.a.run(Http([page(['1'])]), 'facets')
        rec = s.read_record('1')
        self.assertEqual(rec['facets']['Document Type'], ['Receipt'])
        self.assertTrue(rec['facets_complete']['Document Type'])

    def test_range_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            Crawl(self.root / 'state', '2026-07-01', '2026-07-02')

    def test_windows_cover_leap_day(self):
        self.assertEqual(list(windows('2024-02-28', '2024-03-02')),
                         [('2024-02-28', '2024-02-29'), ('2024-03-01', '2024-03-02')])

    def test_large_inventory_window_splits(self):
        self.a.close()
        self.a = Crawl(self.root / 'other', '2026-07-01', '2026-07-04')
        self.a.run(Http([page(['1', '2'], 12000)]), 'inventory', 1)
        self.assertEqual(sum(j['status'] == 'pending' for j in self.a.jobs()), 2)
        self.assertEqual(sum(j['status'] == 'split' for j in self.a.jobs()), 1)

    def test_large_facet_window_splits_and_still_finalizes(self):
        self.a.close()
        self.a = Crawl(self.root / 'other', '2026-07-01', '2026-07-04')
        self.a.run(Http([page(['1', '2'], day='2026-07-01'), page([])]), 'inventory')
        self.a.config['catalog'] = {'Document Type': {'66': 'Receipt'}}
        self.a.save_config()
        http = Http([page(['1'], 12000, day='2026-07-01'), page(['1'], day='2026-07-01'), page([])])
        self.a.run(http, 'facets')
        self.assertEqual([c[1]['params']['sd'] for c in http.calls], ['2026-07-01', '2026-07-01', '2026-07-03'])
        self.assertTrue(all(c[1]['params']['rds'] == '66' for c in http.calls))
        self.assertEqual(s.read_record('1')['facets']['Document Type'], ['Receipt'])
        self.assertEqual(s.read_record('2')['facets']['Document Type'], [])
        self.assertTrue(s.read_record('2')['facets_complete']['Document Type'])

    def test_duckdb_download_queue_reads_metadata_and_actual_files(self):
        self.a.run(Http([page(['1', '2'], 2), page([])]), 'inventory')
        source = self.root / 'source'
        source.mkdir()
        (source / '1.pdf').write_bytes(b'present')
        with patch.object(s, 'SOURCE', source):
            self.assertEqual([r[1] for r in pdf_queue(self.a.connection, missing_only=True)], ['2'])

    def test_partial_page_write_replays_without_duplicate_history(self):
        write = s.write_json
        def fail_second(path, value):
            if path == s.record_path('2'):
                raise OSError('disk full')
            write(path, value)
        with patch.object(s, 'write_json', side_effect=fail_second):
            with self.assertRaises(OSError):
                self.a.run(Http([page(['1', '2'])]), 'inventory')
        self.assertTrue(s.record_path('1').exists())
        self.assertEqual(self.a.jobs()[0]['offset'], 1)
        self.a.run(Http([page(['1', '2']), page([])]), 'inventory')
        self.assertEqual(s.read_record('1')['changes'], [])

    def test_facets_do_not_erase_unobserved_existing_documents(self):
        unrelated = s.Row('99', 'Keep', s.VIEW_URL.format(id='99'), True,
                          '2026-07-02', '', 'PDF Document')
        self.a.record(unrelated, facet=('Document Type', 'Letter'))
        self.a.run(Http([page(['1'])]), 'inventory')
        self.a.config['catalog'] = {'Document Type': {'66': 'Receipt'}}
        self.a.save_config()
        self.a.run(Http([page(['1'])]), 'facets')
        self.assertEqual(s.read_record('99')['facets']['Document Type'], ['Letter'])

    def cli(self, arguments, http):
        with patch.object(s, 'ROOT', self.root), patch.object(s, 'STATE_ROOT', self.root / 'scout-state'), patch.object(s, 'Http', return_value=http), \
                patch('sys.argv', ['scout.py', *arguments]), redirect_stdout(StringIO()):
            return s.main()

    def test_discover_and_enrich_share_automatic_range_state(self):
        dates = ['--from', '2026-07-02', '--to', '2026-07-02']
        self.assertEqual(self.cli(['discover', *dates], Http([page(['1'])])), 0)
        path = self.root / 'scout-state' / '2026-07-02_2026-07-02' / 'run.json'
        config = json.loads(path.read_text())
        config['catalog'] = {'Document Type': {'66': 'Receipt'}}
        s.write_json(path, config)
        # Discovery is already done. Facets find a folder; enrich must drain it.
        shell = '<section id="section-items" data-url="/REGDOCS/Item/LoadResult/10"></section>'
        http = Http([page(['10'], kind='Folder'), shell, page(['3'])])
        self.assertEqual(self.cli(['enrich', *dates], http), 0)
        self.assertEqual(http.calls[0][1]['params']['rds'], '66')
        self.assertEqual(s.read_record('3')['containers'][0]['id'], '10')
        self.assertEqual(self.cli(['enrich', *dates], Http([])), 0)

    def test_a_finished_month_prints_one_line(self):
        output = StringIO()
        with patch.object(s, 'ROOT', self.root), patch.object(s, 'STATE_ROOT', self.root / 'scout-state'), patch.object(s, 'Http', return_value=Http([page(['1'])])), \
                patch('sys.argv', ['scout.py', 'discover', '--from', '2026-07-02', '--to', '2026-07-02']), \
                redirect_stdout(output):
            self.assertEqual(s.main(), 0)
        self.assertEqual(output.getvalue(), 'discover 2026-07-02..2026-07-02: 1 PDFs, 1 requests, done\n')

    def test_status_is_read_only_and_does_not_contact_regdocs(self):
        dates = ['--from', '2026-07-02', '--to', '2026-07-02']
        self.cli(['discover', *dates], Http([page(['1'])]))
        before = {p: p.read_bytes() for p in (self.root / 'scout-state').rglob('*.json')}
        with patch.object(s, 'Http', side_effect=AssertionError('status must be offline')), \
                patch.object(s, 'ROOT', self.root), patch.object(s, 'STATE_ROOT', self.root / 'scout-state'), patch('sys.argv', ['scout.py', 'status']), \
                redirect_stdout(StringIO()):
            self.assertEqual(s.main(), 0)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_discover_requires_both_dates(self):
        with patch('sys.argv', ['scout.py', 'discover', '--from', '2026-07-02']), \
                redirect_stderr(StringIO()), self.assertRaises(SystemExit) as stopped:
            s.main()
        self.assertEqual(stopped.exception.code, 2)

    def test_unparsed_row_cannot_finish_a_search(self):
        broken = page(['1']).replace('</tbody>', '<tr><td>Missing link</td><td>2026-07-02</td><td>Company</td></tr></tbody>')
        self.a.run(Http([broken]), 'discover')
        job = self.a.jobs()[0]
        self.assertEqual((job['status'], job['offset']), ('failed', 1))
        self.assertIn('could not be parsed', job['error'])
        self.assertFalse(s.record_path('1').exists())

    def test_unparsed_container_row_keeps_its_cursor(self):
        self.a.run(Http([page(['10'], kind='Folder')]), 'discover')
        shell = '<section id="section-items" data-url="/REGDOCS/Item/LoadResult/10"></section>'
        broken = page(['1']).replace('</tbody>', '<tr><td>Broken row</td></tr></tbody>')
        self.a.run(Http([shell, broken]), 'containers')
        job = self.a.jobs('containers')[0]
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['pages'], 0)

    def test_shared_lock_blocks_all_metadata_writers(self):
        cases = [(['discover', '--from', '2026-07-02', '--to', '2026-07-02'], None),
                 (['enrich', '--from', '2026-07-02', '--to', '2026-07-02'], None),
                 (['download'], 'cmd_download'), (['doc', '1'], 'cmd_doc'),
                 (['missing'], 'cmd_missing'), (['seed', '/unused'], 'cmd_seed')]
        with exclusive(s.OUT / '.scout-writer.lock'):
            for args, handler in cases:
                with self.subTest(args=args):
                    if handler:
                        with patch.object(s, handler, side_effect=AssertionError('writer entered')):
                            self.assertEqual(self.cli(args, Http([])), 1)
                    else:
                        self.assertEqual(self.cli(args, Http([])), 1)
            self.assertEqual(self.cli(['status'], Http([])), 0)

    def test_download_repairs_existing_pdf_without_network(self):
        self.a.run(Http([page(['1'])]), 'discover')
        source = self.root / 'source'
        source.mkdir()
        data = b'%PDF-1.7\nrecovered file'
        (source / '1.pdf').write_bytes(data)
        with patch.object(s, 'SOURCE', source):
            self.assertEqual(self.cli(['download'], Http([])), 0)
            rec = s.read_record('1')
            self.assertEqual(rec['download']['sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(rec['download']['size_bytes'], len(data))
            self.assertIn('reconciled_at', rec['download'])
            self.assertNotIn('downloaded_at', rec['download'])
            # A new process must not select already-repaired files again.
            import duckdb
            with duckdb.connect(':memory:') as connection:
                self.assertEqual(pdf_queue(connection, repair_only=True), [])

    def test_reconciliation_rejects_non_pdf(self):
        self.a.run(Http([page(['1'])]), 'discover')
        source = self.root / 'source'
        source.mkdir()
        (source / '1.pdf').write_text('<html>error page</html>')
        with patch.object(s, 'SOURCE', source):
            self.assertEqual(self.cli(['download'], Http([])), 1)
        self.assertIsNone(s.read_record('1')['download'])

    def test_download_crash_after_rename_is_recovered_without_redownload(self):
        self.a.run(Http([page(['1'])]), 'discover')
        source = self.root / 'source'
        http = Http([])
        http._pace = lambda: None
        http.client = MagicMock()
        response = http.client.stream.return_value.__enter__.return_value
        response.status_code = 200
        response.iter_bytes.return_value = [b'%PDF-1.7\nactual bytes']
        response.url = 'https://example.invalid/file.pdf'
        response.headers = {'content-type': 'application/pdf'}
        with patch.object(s, 'SOURCE', source), patch.object(s, 'write_json', side_effect=OSError('disk full')):
            self.assertEqual(self.cli(['download'], http), 1)
        self.assertTrue((source / '1.pdf').exists())
        self.assertEqual(http.client.stream.call_count, 1)
        self.assertIsNone(s.read_record('1')['download'])
        with patch.object(s, 'SOURCE', source):
            self.assertEqual(self.cli(['download'], Http([])), 0)
        self.assertEqual(s.read_record('1')['download']['sha256'],
                         hashlib.sha256(b'%PDF-1.7\nactual bytes').hexdigest())

    def test_download_paces_like_enrich_and_stops_on_failures_in_a_row(self):
        self.a.run(Http([page([str(i) for i in range(1, 8)], 7), page([])]), 'inventory')
        http = s.Http()
        http._pace = lambda: None
        http.client = MagicMock()
        response = http.client.stream.return_value.__enter__.return_value
        response.status_code = 503
        response.headers = {}
        with patch.object(s, 'SOURCE', self.root / 'source'), patch.object(s.time, 'sleep'):
            self.assertEqual(self.cli(['download'], http), 1)
        # A 503 is retried, and five documents failing in a row stop the run.
        self.assertEqual(http.client.stream.call_count,
                         s.MAX_FAILURES_IN_ROW * (s.MAX_RETRIES + 1))

    def gone_then_found(self, found, missing=404):
        """An Http whose downloads answer `missing` (a status, or 'html' for a
        page instead of the PDF) except for the ids in found."""
        http = s.Http()
        http._pace = lambda: None
        http.client = MagicMock()

        def stream(method, url, **kwargs):
            response = MagicMock()
            doc_id = url.rsplit('/', 1)[-1]
            response.status_code = 200 if doc_id in found or missing == 'html' else missing
            response.headers = {'content-type': 'application/pdf'}
            response.iter_bytes.return_value = [
                b'%PDF-1.7\n' + doc_id.encode() if doc_id in found else b'<html>gone</html>']
            response.url = url
            context = MagicMock()
            context.__enter__.return_value = response
            return context

        http.client.stream.side_effect = stream
        return http

    def test_download_continues_past_files_that_are_unavailable(self):
        # Six in a row are answers about those files, not REGDOCS being down.
        for missing, calls in ((404, 7), (400, 7), ('html', 6 * (s.MAX_RETRIES + 1) + 1)):
            with self.subTest(missing=missing):
                self.a.run(Http([page([str(i) for i in range(1, 8)], 7), page([])]), 'inventory')
                http = self.gone_then_found({'7'}, missing)
                source = self.root / f'source-{missing}'
                with patch.object(s, 'SOURCE', source), patch.object(s.time, 'sleep'):
                    self.assertEqual(self.cli(['download'], http), 1)
                self.assertEqual(http.client.stream.call_count, calls)
                self.assertTrue((source / '7.pdf').exists())

    def test_download_stops_when_every_file_is_unavailable(self):
        self.a.run(Http([page([str(i) for i in range(1, 8)], 7), page([])]), 'inventory')
        http = self.gone_then_found(set())
        with patch.object(s, 'SOURCE', self.root / 'source'), patch.object(s, 'MAX_UNAVAILABLE_IN_ROW', 3):
            self.assertEqual(self.cli(['download'], http), 1)
        self.assertEqual(http.client.stream.call_count, 3)

    def test_finalization_preserves_fetch_time_and_combines_writes(self):
        fetched = '2026-07-03T01:00:00+00:00'
        later = '2026-09-24T01:00:00+00:00'
        with patch.object(s, 'now', return_value=fetched):
            self.a.run(Http([page(['1'])]), 'discover')
            self.a.config['catalog'] = {'Document Type': {'66': 'Receipt'}, 'Role': {'1': 'Applicant'}}
            self.a.save_config()
            with patch.object(self.a, 'finish_facets'):
                self.a.run(Http([page(['1']), page(['1'])]), 'facets')
        write = s.write_json
        written = []
        def track(path, value):
            written.append(path)
            write(path, value)
        with patch.object(s, 'now', return_value=later), patch.object(s, 'write_json', side_effect=track):
            self.a.finish_facets()
        self.assertEqual(written.count(s.record_path('1')), 1)
        rec = s.read_record('1')
        self.assertEqual(rec['last_seen_at'], fetched)
        self.assertEqual(rec['facet_observed_at']['Role'], fetched)
        self.assertTrue(rec['facets_complete']['Role'])
        self.assertTrue(rec['facets_complete']['Document Type'])

    def test_older_cached_facets_cannot_replace_newer_observations(self):
        with patch.object(s, 'now', return_value='2026-07-03T01:00:00+00:00'):
            self.a.run(Http([page(['1'])]), 'discover')
            self.a.config['catalog'] = {'Document Type': {'66': 'Receipt'}}
            self.a.save_config()
            with patch.object(self.a, 'finish_facets'):
                self.a.run(Http([page(['1'])]), 'facets')
        rec, _ = s.merge(s.read_record('1'), '1', {}, {'Document Type': ['Letter']},
                         {'Document Type': True}, True, [], set(), '2026-09-24T01:00:00+00:00')
        s.write_json(s.record_path('1'), rec)
        self.a.finish_facets()
        self.assertEqual(s.read_record('1')['facets']['Document Type'], ['Letter'])

    def test_queue_does_not_reread_checkpoint_files(self):
        self.a.run(Http([page(['1'])]), 'discover')
        with patch.object(Path, 'read_text', side_effect=AssertionError('extra file read')):
            self.assertEqual(self.a.jobs('inventory')[0]['status'], 'done')

    def test_refresh_fetches_again_then_resumes_and_preserves_download(self):
        dates = ['--from', '2026-07-02', '--to', '2026-07-02']
        self.cli(['discover', *dates], Http([page(['1'])]))
        rec = s.read_record('1')
        rec['download'] = {'sha256': 'preserved', 'size_bytes': 123}
        s.write_json(s.record_path('1'), rec)
        http = Http([page(['1']).replace('Receipt 1', 'Updated receipt')])
        self.assertEqual(self.cli(['discover', *dates, '--refresh'], http), 0)
        self.assertEqual(http.requests, 1)
        self.assertEqual(s.read_record('1')['title'], 'Updated receipt')
        self.assertEqual(s.read_record('1')['download']['sha256'], 'preserved')
        self.assertEqual(self.cli(['discover', *dates], Http([])), 0)
        config = json.loads((self.root / 'scout-state' / '2026-07-02_2026-07-02' / 'run.json').read_text())
        self.assertTrue(config['jobs_directory'].startswith('jobs-'))
        self.assertEqual(self.cli(['status'], Http([])), 0)

    def test_completed_refresh_removes_replaced_checkpoints(self):
        dates = ['--from', '2026-07-02', '--to', '2026-07-02']
        state = self.root / 'scout-state' / '2026-07-02_2026-07-02'
        self.cli(['discover', *dates], Http([page(['1'])]))
        self.assertEqual(self.cli(['discover', *dates, '--refresh', '--max-requests', '1'],
                                  Http([page(['1', '2'])])), 1)
        # Unfinished: the replaced checkpoints are kept.
        self.assertTrue((state / 'jobs').exists())
        self.assertEqual(self.cli(['discover', *dates], Http([page([])])), 0)
        self.assertFalse((state / 'jobs').exists())
        config = json.loads((state / 'run.json').read_text())
        self.assertNotIn('replaced_jobs_directories', config)
        self.assertTrue((state / config['jobs_directory']).exists())

    def test_bad_date_is_reported_not_raised(self):
        output = StringIO()
        with patch('sys.argv', ['scout.py', 'discover', '--from', '2026-13-01', '--to', '2026-07-02']), \
                patch.object(s, 'ROOT', self.root), patch.object(s, 'STATE_ROOT', self.root / 'scout-state'), redirect_stdout(output):
            self.assertEqual(s.main(), 2)
        self.assertIn('refusing:', output.getvalue())
        self.assertIn('month must be 1-12', output.getvalue())

    def test_status_leaves_outside_range_facets_to_their_own_crawl(self):
        self.a.run(Http([page(['1'])]), 'discover')
        shell = '<section id="section-items" data-url="/REGDOCS/Item/LoadResult/10"></section>'
        self.a.record(s.Row('10', 'Box', s.VIEW_URL.format(id='10'), False, '2026-07-02', '', 'Folder'))
        self.a.run(Http([shell, page(['2'], day='2025-01-01')]), 'containers')
        output = StringIO()
        with redirect_stdout(output):
            self.a.status()
        self.assertIn('(1 dated in range; 1 linked outside/undated)', output.getvalue())
        self.assertIn('in range with incomplete facets: 1;', output.getvalue())

    def test_status_counts_are_scoped_to_run(self):
        self.a.run(Http([page(['1'])]), 'discover')
        self.a.record(s.Row('99', 'Unrelated', s.VIEW_URL.format(id='99'), True, '2026-07-02', '', 'PDF Document'))
        output = StringIO()
        with redirect_stdout(output):
            self.a.status()
        self.assertIn('Discovered PDFs: 1 (1 dated in range;', output.getvalue())
        self.assertIn('Missing downloads: 1;', output.getvalue())
        self.assertIn('Last successful request:', output.getvalue())


FIXTURES = Path(__file__).parent / 'fixtures'


class RealPageTests(unittest.TestCase):
    """Pages saved from REGDOCS on 2026-09-24: no real row may be rejected as unparsed."""

    def test_day_search(self):
        html = (FIXTURES / 'search_2026-01-05.html').read_text()
        rows = s.parse_rows(html)
        self.assertEqual((len(rows), s.parse_total(html)), (30, 30))
        self.assertEqual({r.kind for r in rows},
                         {'PDF Document', 'Html Document', 'Folder', 'Compound Document'})
        self.assertTrue(all(r.date == '2026-01-05' for r in rows))

    def test_container_members(self):
        html = (FIXTURES / 'container_4633939.html').read_text()
        rows = s.parse_rows(html, container_id='4633939')
        self.assertEqual((len(rows), s.parse_total(html)), (2, 2))

    def test_container_of_folders(self):
        # Saved 2026-09-25: sub-folders are listed in a single cell, with no date or submitter.
        html = (FIXTURES / 'container_4517705.html').read_text()
        rows = s.parse_rows(html, container_id='4517705')
        self.assertEqual((len(rows), s.parse_total(html)), (4, 4))
        self.assertEqual([r.document_id for r in rows], ['4644138', '4644139', '4644140', '4644141'])
        self.assertTrue(all(r.kind == 'Folder' and r.date is None and not r.is_file for r in rows))

    def test_hyperlink_items_are_skipped(self):
        # Two "Hyperlink to Web Page" rows point outside REGDOCS: skipped, not rejected.
        html = (FIXTURES / 'search_2002-02_offset1801.html').read_text()
        rows = s.parse_rows(html)
        self.assertEqual(len(rows), 198)  # a full page of 200, less the two
        self.assertTrue(all(r.document_id.isdigit() for r in rows))

    def test_empty_day(self):
        html = (FIXTURES / 'search_2026-01-04.html').read_text()
        self.assertEqual(s.parse_rows(html), [])
        self.assertTrue(s.recognized(html) and s.explicit_empty(html))


if __name__ == '__main__':
    unittest.main()
