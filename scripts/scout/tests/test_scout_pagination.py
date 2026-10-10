import sys
from contextlib import redirect_stdout
from dataclasses import asdict
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scout as s  # noqa: E402
from scout_crawl import Crawl


FIXTURES = Path(__file__).parent / 'fixtures'
CONTAINER_URL = s.DOMAIN + '/REGDOCS/Item/LoadResult/10'
PAGE_URL = '/REGDOCS/Item/SearchResult?loc=10&srt=11&isc=False&iscd=False'


def pagination(offset=1, size=2, next_page=True, url=PAGE_URL):
    anchor = '<a href="#" rel="next" class="next-page">Next 2 Results</a>' if next_page else ''
    return (anchor + f'<input id="nextPageUrl" value="{url}">'
            f'<input id="currentIndex" value="{offset}">'
            f'<input id="numberResultsPerPage" value="{size}">')


def member_page(ids, offset, next_page):
    rows = ''.join(f'<tr><td><i title="PDF Document"></i>'
                   f'<a href="/REGDOCS/File/Download/{doc_id}">Document {doc_id}</a>'
                   '</td><td>2012-01-01</td><td>Company</td></tr>' for doc_id in ids)
    return (f'Item(s) - {offset} to {offset + len(ids) - 1} out of about 3'
            f'<table><tbody>{rows}</tbody></table>' + pagination(offset, next_page=next_page))


class Http:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = 0
        self.calls = []

    def get(self, url, **kwargs):
        self.requests += 1
        self.calls.append(url)
        return True, next(self.responses), url, None


class PaginationTests(unittest.TestCase):
    def test_real_next_control_uses_declared_offset(self):
        html = (FIXTURES / 'search_2002-02_offset1801.html').read_text()
        target = httpx.URL(s.next_container_page(html, CONTAINER_URL))
        self.assertEqual(target.path, '/REGDOCS/Search/SearchAdvancedResults')
        self.assertEqual(dict(target.params), {
            'sd': '2002-02-01', 'ed': '2002-02-28', 'srt': '21', 'sr': '2001'})

    def test_real_final_container_page_has_hidden_inputs_but_no_next(self):
        html = (FIXTURES / 'container_4633939.html').read_text()
        self.assertIn('id="nextPageUrl"', html)
        self.assertIsNone(s.next_container_page(html, CONTAINER_URL))

    def test_next_offset_replaces_existing_offset_and_preserves_filters(self):
        html = pagination(offset=201, size=200,
                          url=PAGE_URL + '&sr=201&text=A%2BB%26C&empty=')
        target = httpx.URL(s.next_container_page(html, CONTAINER_URL))
        self.assertEqual(target.params.get_list('sr'), ['401'])
        self.assertEqual(dict(target.params), {
            'loc': '10', 'srt': '11', 'isc': 'False', 'iscd': 'False',
            'sr': '401', 'text': 'A+B&C', 'empty': ''})

    def test_unresolved_next_is_rejected(self):
        pages = [
            '<a href="#" rel="next">Next</a>',
            pagination(offset=0),
            pagination(size=0),
            pagination(offset='broken'),
            pagination(url=''),
            pagination(url='#'),
            pagination(url='javascript:void(0)'),
        ]
        for html in pages:
            with self.subTest(html=html), self.assertRaises(s.SearchRejected):
                s.next_container_page(html, CONTAINER_URL)


class HttpQueryTests(unittest.TestCase):
    def test_retryable_error_slows_pacing_then_success_recovers(self):
        statuses = iter([503, 200])

        def respond(request):
            return httpx.Response(next(statuses), text='ok')

        http = s.Http(min_delay=0, max_delay=0)
        http.client.close()
        http.client = httpx.Client(transport=httpx.MockTransport(respond))
        try:
            with patch.object(s.time, 'sleep'):
                self.assertTrue(http.get('https://example.test/')[0])
        finally:
            http.close()
        self.assertEqual(http.requests, 2)
        self.assertGreater(http.delay_multiplier, 1.0)
        self.assertLess(http.delay_multiplier, 2.0)

    def test_encoded_endpoint_query_survives_without_extra_params(self):
        received = []

        def respond(request):
            received.append(request.url)
            return httpx.Response(200, text='ok')

        http = s.Http()
        http.client.close()
        http.client = httpx.Client(transport=httpx.MockTransport(respond))
        url = s.DOMAIN + PAGE_URL + '&sr=201&text=A%2BB%26C&empty='
        try:
            with patch.object(http, '_pace'):
                for params in (None, {}):
                    self.assertTrue(http.get(url, params=params, ajax=True)[0])
        finally:
            http.close()
        self.assertEqual([str(u) for u in received], [url, url])

    def test_supplied_params_merge_with_endpoint_query(self):
        received = []

        def respond(request):
            received.append(request.url)
            return httpx.Response(200, text='ok')

        http = s.Http()
        http.client.close()
        http.client = httpx.Client(transport=httpx.MockTransport(respond))
        try:
            with patch.object(http, '_pace'):
                self.assertTrue(http.get(s.DOMAIN + PAGE_URL + '&sr=1',
                                         params={'sr': 201}, ajax=True)[0])
        finally:
            http.close()
        self.assertEqual(dict(received[0].params), {
            'loc': '10', 'srt': '11', 'isc': 'False', 'iscd': 'False', 'sr': '201'})


class ContainerResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        output = patch.object(s, 'OUT', self.root / 'output')
        output.start()
        self.addCleanup(output.stop)
        self.crawl = self.open_crawl()
        self.addCleanup(lambda: self.crawl.close())
        parent = s.Row('10', 'Folder', s.VIEW_URL.format(id='10'), False,
                       '2012-01-01', 'Company', kind='Folder')
        self.crawl.enqueue('container:10', 'containers', asdict(parent))

    def open_crawl(self):
        return Crawl(self.root / 'state', '2012-01-01', '2012-12-31')

    def test_declared_next_page_survives_close_and_resume(self):
        shell = '<section id="section-items" data-url="/REGDOCS/Item/LoadResult/10"></section>'
        first = Http([shell, member_page(['1', '2'], offset=1, next_page=True)])
        self.crawl.run(first, 'containers', max_requests=2)
        job = self.crawl.jobs('containers')[0]
        next_url = s.DOMAIN + PAGE_URL + '&sr=3'
        self.assertEqual(job['status'], 'pending')
        self.assertEqual(job['url'], next_url)
        self.crawl.close()
        self.crawl = self.open_crawl()
        resumed = Http([member_page(['3'], offset=3, next_page=False)])
        self.crawl.run(resumed, 'containers')
        self.assertEqual(resumed.calls, [next_url])
        job = self.crawl.jobs('containers')[0]
        self.assertEqual((job['status'], job['ids'], job['pages']), ('done', ['1', '2', '3'], 2))
        self.assertFalse(job.get('gaps'))
        self.assertEqual(s.read_record('3')['containers'][0]['id'], '10')

    def test_broken_next_cannot_become_a_gap_after_retries(self):
        shell = '<section id="section-items" data-url="/REGDOCS/Item/LoadResult/10"></section>'
        html = member_page(['1', '2'], offset=1, next_page=True).replace('id="nextPageUrl"', 'id="missing"')
        with redirect_stdout(StringIO()):
            self.crawl.run(Http([shell, html]), 'containers')
            self.crawl.run(Http([html]), 'containers')
            self.crawl.run(Http([html]), 'containers')
        job = self.crawl.jobs('containers')[0]
        self.assertEqual(job['status'], 'failed')
        self.assertIn('Next link', job['error'])
        self.assertFalse(job.get('gaps'))
        self.assertFalse(job.get('short'))


if __name__ == '__main__':
    unittest.main()
