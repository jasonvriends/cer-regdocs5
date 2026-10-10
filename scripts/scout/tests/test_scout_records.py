import sys
import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scout as s  # noqa: E402
from scout_crawl import ensure_records


def record(doc_id, title):
    return {"cer_schema": s.CER_SCHEMA, "doc_id": doc_id, "title": title, "kind": "PDF Document",
            "date": "2026-07-02", "download": None, "facets_complete": {}}


class RecordFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = patch.object(s, 'OUT', Path(self.tmp.name) / 'output')
        self.out.start()

    def tearDown(self):
        self.out.stop()
        self.tmp.cleanup()

    def packed(self, doc_id, rec):
        s.record_path(doc_id).parent.mkdir(parents=True, exist_ok=True)
        s.write_json(s.record_path(doc_id), rec)

    def plain(self, doc_id, rec):
        path = s.OUT / doc_id / f'{doc_id}.{s.PLAIN_RECORD}'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec), encoding='utf-8')
        return path

    def test_records_are_gzipped_and_read_back(self):
        self.packed('1', record('1', 'Receipt 1'))
        path = s.record_path('1')
        self.assertTrue(path.name.endswith('.cer.meta.json.gz'))
        self.assertEqual(json.loads(gzip.decompress(path.read_bytes()))['title'], 'Receipt 1')
        self.assertEqual(s.read_record('1')['title'], 'Receipt 1')

    def test_same_record_compresses_to_same_bytes(self):
        self.packed('1', record('1', 'Receipt 1'))
        first = s.record_path('1').read_bytes()
        self.packed('1', record('1', 'Receipt 1'))
        self.assertEqual(s.record_path('1').read_bytes(), first)

    def test_compress_replaces_plain_records(self):
        plain = self.plain('1', record('1', 'Receipt 1'))
        self.assertEqual(s.compress_records(), 1)
        self.assertFalse(plain.exists())
        self.assertEqual(s.read_record('1')['title'], 'Receipt 1')
        self.assertEqual(s.compress_records(), 0)

    def test_newer_file_wins_when_both_exist(self):
        self.packed('1', record('1', 'Packed'))
        plain = self.plain('1', record('1', 'Written later by an old process'))
        later = s.record_path('1').stat().st_mtime + 10
        os.utime(plain, (later, later))
        s.compress_records()
        self.assertEqual(s.read_record('1')['title'], 'Written later by an old process')

        self.plain('1', record('1', 'Stale'))
        earlier = s.record_path('1').stat().st_mtime - 10
        os.utime(s.OUT / '1' / f'1.{s.PLAIN_RECORD}', (earlier, earlier))
        s.compress_records()
        self.assertEqual(s.read_record('1')['title'], 'Written later by an old process')
        self.assertFalse((s.OUT / '1' / f'1.{s.PLAIN_RECORD}').exists())

    def test_queue_reads_both_layouts(self):
        self.packed('1', record('1', 'Packed'))
        self.plain('2', record('2', 'Plain'))
        connection = duckdb.connect()
        ensure_records(connection)
        rows = connection.execute('SELECT doc_id FROM records ORDER BY doc_id').fetchall()
        self.assertEqual(rows, [('1',), ('2',)])


class StandInTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = patch.object(s, 'OUT', Path(self.tmp.name) / 'output')
        self.out.start()

    def tearDown(self):
        self.out.stop()
        self.tmp.cleanup()

    def sighting(self, rec, title, day, filing, container):
        obs = {"title": title, "date": day, "kind": "PDF Document",
               "filing": {"number": filing, "id": 1}, "company": {"name": "NEB", "id": 2}}
        return s.merge(rec, '41059', obs, {}, {}, True,
                       [{"id": container, "kind": "Compound Document", "title": filing}], set(),
                       f'{day}T00:00:00+00:00')

    def test_stand_in_keeps_listings_not_history(self):
        rec, _ = self.sighting(None, 'A1 Lettre (Pending Translation)', '2017-01-02', 'A1', '10')
        rec, changes = self.sighting(rec, 'A2 Ordonnance (Pending Translation)', '2018-03-04', 'A2', '20')
        rec, _ = self.sighting(rec, 'A1 Lettre (Pending Translation)', '2017-01-02', 'A1', '10')
        self.assertEqual(changes, [])
        self.assertEqual(rec['changes'], [])
        self.assertEqual(rec['title'], 'TranslationPending e.pdf')
        self.assertEqual(rec['stand_in'], 'translation pending')
        self.assertEqual(rec['date'], '2017-01-02')
        self.assertEqual([l['filing'] for l in rec['listings']], ['A1', 'A2'])
        self.assertEqual({c['id'] for c in rec['containers']}, {'10', '20'})

    def test_ordinary_documents_still_keep_history(self):
        obs = {"title": "One", "date": "2017-01-02", "kind": "PDF Document"}
        rec, _ = s.merge(None, '1', obs, {}, {}, True, [], set(), '2026-01-01T00:00:00+00:00')
        rec, changes = s.merge(rec, '1', {**obs, "title": "Two"}, {}, {}, True, [], set(),
                               '2026-01-02T00:00:00+00:00')
        self.assertEqual([c['field'] for c in changes], ['title'])
        self.assertNotIn('listings', rec)


if __name__ == '__main__':
    unittest.main()
