"""Synthetic integration fixtures; never mistaken for live-source evidence."""
from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from moh_ai import daily, editorial
from moh_ai.cli import main
from moh_ai.sources import SourceError, parse_source

CLOCK = datetime(2026, 11, 4, 8, tzinfo=timezone.utc)


def config(url='https://example.org/feed', kind='rss'):
    return dict(name='Synthetic fixture', url=url, kind=kind, authority='primary', enabled=True)


def article(url, title='AI tutorial for prompts', date='2026-11-04T07:00:00Z', suffix=''):
    r = parse_source(('AI tutorial guide: explain a useful prompt with context. ' + suffix) * 3,
                     'article', title=title, url=url, published_at=date)
    return r


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'test.sqlite3'
    def tearDown(self): self.temp.cleanup()
    def test_registry_validates_before_write_and_defaults_disabled(self):
        self.assertTrue(all(not x['enabled'] for x in daily.get_sources(self.db)))
        daily.set_sources(self.db, [config()])
        for bad in (None, {}, [config()] * 21, [config(), config()], [config(kind='paid-api')],
                    [{**config(), 'enabled': 'yes'}], [config(url='http://example.org')],
                    [config(url='https://name:secret@example.org')]):
            with self.subTest(bad=bad), self.assertRaises(ValueError): daily.set_sources(self.db, bad)
        self.assertEqual(len(daily.get_sources(self.db)), 1)
    def test_rss_atom_and_unsafe_xml(self):
        rss = '<rss><channel><item><title>AI &amp; prompts</title><link>/a?utm_source=x#p</link><pubDate>Wed, 04 Nov 2026 07:00:00 GMT</pubDate></item></channel></rss>'
        rows = daily.parse_feed(rss, 'https://example.org/feed')
        self.assertEqual(rows[0]['url'], 'https://example.org/a')
        self.assertEqual(rows[0]['title'], 'AI & prompts')
        atom = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>AI</title><link rel="self" href="/api"/><link href="/b"/><published>2026-11-04T07:00:00Z</published></entry></feed>'
        self.assertEqual(daily.parse_feed(atom, 'https://example.org/f')[0]['url'], 'https://example.org/b')
        for xml in ('<!DOCTYPE a [<!ENTITY x "boom">]><rss/>', '<html/>', '<rss>'):
            with self.assertRaises(SourceError): daily.parse_feed(xml, 'https://example.org')
    def test_feed_fetch_to_multiple_briefs_without_any_model_or_other_network(self):
        daily.set_sources(self.db, [config(), config(url='https://example.org/blocked', kind='link')])
        rss = '<rss><channel>' + ''.join(f'<item><link>https://example.org/{x}</link></item>' for x in ['a','a?utm_source=dup','b','c']) + '</channel></rss>'
        def read(url):
            if url.endswith('blocked'): raise SourceError('Source returned HTTP 403')
            titles = {'a': 'AI tutorial for prompts', 'b': 'AI assistant tool', 'c': 'AI concept explained'}
            return article(url, title=titles[url[-1]], suffix=url)
        with patch('moh_ai.daily.fetch_document', return_value={'text': rss, 'canonical_url': 'https://example.org/feed'}), \
             patch('moh_ai.daily.fetch_source', side_effect=read) as fetch, \
             patch('socket.socket', side_effect=AssertionError('unexpected network/model call')):
            result = daily.collect(self.db, clock=CLOCK)
            second = daily.collect(self.db, clock=CLOCK)
        self.assertEqual(len(result['ideas']), 3)
        self.assertTrue(second['reused']); self.assertEqual(fetch.call_count, 4)
        self.assertEqual(result['status'], 'partial'); self.assertEqual(len(result['errors']), 1)
        self.assertEqual(result['generation_status'], 'awaiting_model')
        self.assertEqual(result['duplicates'], 1)
        self.assertEqual(result['discoveries'][0]['analysis_basis'], 'feed_metadata')
        self.assertTrue(result['discoveries'][0]['raw_hash'])
        ws = editorial.list_workspace(self.db)
        self.assertEqual(len(ws['sources']), 3); self.assertFalse(ws['drafts'])
        for item in result['items']:
            pack = editorial.get_pack(self.db, item['pack_id'])
            self.assertIn(pack['claims'][0]['evidence_excerpt'], pack['source']['content'])
            self.assertEqual(pack['claims'][0]['verification_status'], 'source_claim_only')
    def test_changed_config_reruns_without_force_and_failures_recover(self):
        daily.set_sources(self.db, [])
        first = daily.collect(self.db, clock=CLOCK)
        self.assertEqual(first['errors'][0]['stage'], 'config')
        daily.set_sources(self.db, [config(kind='link')])
        with patch('moh_ai.daily.fetch_source', return_value=article('https://example.org/a')):
            second = daily.collect(self.db, clock=CLOCK + timedelta(seconds=1))
        self.assertNotEqual(first['id'], second['id']); self.assertEqual(len(second['ideas']), 1)
    def test_video_social_and_transcript_basis(self):
        configs = [config(url=f'https://example.org/{x}', kind=x) for x in ('video','social','transcript')]
        daily.set_sources(self.db, configs)
        def read(url):
            if url.endswith('transcript'):
                return parse_source('WEBVTT\n\n00:00:01.000 --> 00:00:09.000\n' + 'AI tutorial for a useful tool. ' * 6,
                                    'vtt', title='AI prompt tutorial', url=url)
            return article(url)
        with patch('moh_ai.daily.fetch_source', side_effect=read): r = daily.collect(self.db, clock=CLOCK)
        self.assertEqual(len(r['ideas']), 1); self.assertEqual(r['ideas'][0]['analysis_basis'], 'transcript')
        pack = editorial.get_pack(self.db, r['ideas'][0]['pack_id'])
        self.assertEqual(pack['source']['analysis_basis'], 'transcript')
        self.assertEqual(r['items'][2]['source']['segments'][0]['timestamp'], '00:00:01.000')
        self.assertEqual([x['source']['analysis_basis'] for x in r['items'][:2]], ['description_only'] * 2)
        with self.assertRaises(ValueError): editorial.save_draft(self.db, r['items'][0]['pack_id'], 'body', [])
    def test_social_landing_page_discovered_via_rss_is_never_full_text(self):
        r = parse_source('<article>' + 'AI tutorial tool ' * 8 + '</article>', 'html', url='https://www.instagram.com/p/abc')
        self.assertEqual(r['analysis_basis'], 'description_only')
    def test_budget_caps_articles_and_feed_entries(self):
        daily.set_sources(self.db, [config(), config(url='https://example.org/feed2')])
        def doc(url):
            return {'text': '<rss><channel>' + ''.join(f'<item><link>https://example.org/{url[-1]}-{i}</link></item>' for i in range(50)) + '</channel></rss>', 'canonical_url': url}
        with patch('moh_ai.daily.fetch_document', side_effect=doc), patch('moh_ai.daily.fetch_source', side_effect=lambda url: article(url, title='AI '+url, suffix=url)) as fetch:
            r = daily.collect(self.db, clock=CLOCK)
        self.assertEqual(fetch.call_count, 20); self.assertEqual(r['deferred'], 20)
        self.assertLessEqual(len(r['ideas']), 5)
    def test_run_lock_prevents_concurrent_duplicate_work(self):
        daily.set_sources(self.db, [config(kind='link')])
        entered, release = threading.Event(), threading.Event()
        results = []
        def read(url):
            entered.set(); self.assertTrue(release.wait(3)); return article(url)
        with patch('moh_ai.daily.fetch_source', side_effect=read):
            worker = threading.Thread(target=lambda: results.append(daily.collect(self.db, clock=CLOCK)))
            worker.start(); self.assertTrue(entered.wait(3))
            try:
                with self.assertRaisesRegex(ValueError, 'כבר פועל'): daily.collect(self.db, clock=CLOCK)
            finally: release.set(); worker.join(3)
        self.assertEqual(len(results), 1)
    def test_crashed_run_requires_explicit_recovery(self):
        c = daily.connect(self.db)
        with c: c.execute("INSERT INTO collection_runs VALUES('old','2026-11-03','2026-11-03T08:00:00Z',NULL,'running','{}')")
        c.close()
        with self.assertRaises(ValueError): daily.collect(self.db, clock=CLOCK)
        self.assertEqual(daily.reset_interrupted(self.db)['interrupted'], 1)
        self.assertEqual(daily.workspace(self.db)['latest']['status'], 'interrupted')
    def test_dates_unknown_old_future_and_no_ai(self):
        self.assertIsNone(daily.publication('2026-11-04'))
        self.assertIsNone(daily.publication('invalid'))
        for date in ('2010-01-01T00:00:00Z','2027-01-01T00:00:00Z'):
            r=article('https://example.org/a', date=date)
            item=dict(id='x',topic='topic',source=r,pack_id='p',score=80,reasons=[])
            self.assertFalse(daily.make_ideas([item], CLOCK))
        r=parse_source('How to use a tool for cooking soup. '*5,'article',title='Cooking tool guide')
        self.assertLess(daily.rank(r,'primary',CLOCK)[0],35)
    def test_cli_status_and_invalid_registry_are_offline(self):
        with patch('socket.socket', side_effect=AssertionError('network')), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(['--data-dir', self.temp.name, 'daily-status']), 0)
        self.assertEqual(json.loads(out.getvalue())['scheduler'], 'disabled')
    def test_jerusalem_day_boundary_and_no_model_fallback_on_failure(self):
        daily.set_sources(self.db, [config(kind='link')])
        with patch('moh_ai.daily.fetch_source', side_effect=SourceError('network blocked')), patch('socket.socket',side_effect=AssertionError('fallback')):
            r=daily.collect(self.db, clock=datetime(2026,11,4,23,30,tzinfo=timezone.utc))
        self.assertEqual(r['day'],'2026-11-05'); self.assertEqual(r['status'],'partial')
        self.assertFalse(r['items']);self.assertFalse(r['ideas']);self.assertEqual(r['errors'][0]['analysis_basis'],'unread')

if __name__ == '__main__': unittest.main()
