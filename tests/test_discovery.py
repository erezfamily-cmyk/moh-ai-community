from datetime import datetime, timezone
from email.message import Message
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from moh_ai.discovery import (APPROVED_FEEDS, DiscoveryError, collect, fetch_feed,
    load_sources, normalize_url, parse_feed, publication_date, rank_items, write_report)

NOW = datetime(2026, 11, 4, 16, tzinfo=timezone.utc)
SOURCE = {'id':'openai-news','title':'OpenAI','url':APPROVED_FEEDS['openai-news']['url'],
          'enabled':True,'source_type':'article'}


def rss(items):
    return ('<rss version="2.0"><channel>' + ''.join(
        f'<item><title>{title}</title><link>{url}</link><pubDate>{date}</pubDate>'
        f'<description>{desc}</description></item>' for title,url,date,desc in items
    ) + '</channel></rss>').encode()


def one(title='ChatGPT prompt guide',date='Wed, 04 Nov 2026 12:00:00 +0000',
        url='https://openai.com/index/guide/',desc='A clear guide for writing prompts'):
    return rss([(title,url,date,desc)])


class DiscoveryTests(unittest.TestCase):
    def test_rss_keeps_publication_retrieval_and_unverified_basis_separate(self):
        items=parse_feed(one(),SOURCE,now=NOW)
        self.assertEqual(items[0]['published_at'],'2026-11-04T12:00:00+00:00')
        self.assertEqual(items[0]['retrieved_at'],'2026-11-04T16:00:00+00:00')
        self.assertEqual(items[0]['analysis_basis'],'metadata_only')
        self.assertEqual(items[0]['verification_status'],'not_verified')
        self.assertEqual(items[0]['content_status'],'needs_full_text')

    def test_atom_published_is_not_invented_from_updated(self):
        atom='''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>AI model guide</title>
        <link rel="alternate" href="https://openai.com/guide"/>
        <updated>2026-11-04T12:00:00Z</updated><summary>Original summary</summary></entry></feed>'''
        item=parse_feed(atom,SOURCE,now=NOW)[0]
        self.assertIsNone(item['published_at'])
        selected,counts=rank_items([item],now=NOW)
        self.assertEqual(selected,[]);self.assertEqual(counts['unknown_dates'],1)

    def test_timezone_missing_and_date_only_are_unknown_not_retrieval_time(self):
        for value in (None,'not a date','2026-11-04','2026-11-04T12:00:00'):
            self.assertIsNone(publication_date(value))

    def test_url_tracking_normalization_deduplicates_entries(self):
        a='https://openai.com/index/a/?utm_source=x&x=2#section'
        b='https://openai.com/index/a?x=2'
        self.assertEqual(normalize_url(a),b)
        items=parse_feed(one(url=a.replace('&','&amp;')),SOURCE,now=NOW)
        other={**items[0],'url':b,'title':'Different description of same AI guide'}
        selected,counts=rank_items(items+[other],now=NOW)
        self.assertEqual(len(selected),1);self.assertEqual(counts['duplicate_items'],1)

    def test_future_old_and_unknown_items_do_not_fill_quota(self):
        entries=[('ChatGPT guide','https://openai.com/new','Thu, 05 Nov 2026 12:00:00 +0000','AI guide'),
                 ('Gemini guide','https://openai.com/old','Sun, 01 Nov 2026 00:00:00 +0000','AI guide'),
                 ('AI guide','https://openai.com/unknown','','AI guide')]
        items=parse_feed(rss(entries),SOURCE,now=NOW)
        selected,c=rank_items(items,now=NOW)
        self.assertFalse(selected)
        self.assertEqual((c['future_dates'],c['older_than_72h'],c['unknown_dates']),(1,1,1))

    def test_24h_precedes_72h_and_source_diversity_is_limited(self):
        entries=[(f'ChatGPT prompt guide {i}',f'https://openai.com/g{i}',
            'Wed, 04 Nov 2026 12:00:00 +0000','AI guide') for i in range(4)]
        items=parse_feed(rss(entries),SOURCE,now=NOW)
        old={**items[0],'id':'old','url':'https://blog.google/ai-guide','source_id':'google-ai',
             'title':'Gemini AI guide','published_at':'2026-11-02T18:00:00+00:00','score':100}
        chosen,_=rank_items(items+[old],now=NOW)
        self.assertEqual(len(chosen),3)
        self.assertEqual(sum(i['source_id']=='openai-news' for i in chosen),2)
        self.assertEqual(chosen[-1]['source_id'],'google-ai')

    def test_irrelevant_recent_item_is_excluded(self):
        selected,c=rank_items(parse_feed(one(title='Annual finance report',desc='Company results'),SOURCE,now=NOW),now=NOW)
        self.assertFalse(selected);self.assertEqual(c['low_relevance'],1)

    def test_xml_entity_utf16_and_oversize_inputs_are_rejected(self):
        for value in (b'<!DOCTYPE rss [<!ENTITY x "foo">]><rss/>',
                      '<rss/>'.encode('utf-16'), b'x'*(2*1024*1024+1),b'<html/>'):
            with self.assertRaises(DiscoveryError):parse_feed(value,SOURCE,now=NOW)

    def test_scripts_and_external_article_links_are_not_accepted(self):
        item=parse_feed(one(desc='&lt;script&gt;steal()&lt;/script&gt;A safe guide'),SOURCE,now=NOW)[0]
        self.assertNotIn('steal',item['summary'])
        self.assertEqual(parse_feed(one(url='https://evil.example/guide'),SOURCE,now=NOW),[])

    def test_video_metadata_requires_transcript_never_claims_watching(self):
        item=parse_feed(one(),{**SOURCE,'source_type':'video'},now=NOW)[0]
        self.assertEqual(item['content_status'],'needs_transcript')
        self.assertEqual(item['analysis_basis'],'metadata_only')

    def test_config_rejects_unknown_or_local_feed_and_nonboolean_enable(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'sources.json'
            for source in ({**SOURCE,'url':'https://127.0.0.1/feed'},
                           {**SOURCE,'id':'unknown'}, {**SOURCE,'id':[]}, {**SOURCE,'enabled':'yes'}):
                p.write_text(json.dumps({'schema_version':1,'sources':[source]}))
                with self.assertRaises(DiscoveryError):load_sources(p)
            p.write_text(json.dumps({'schema_version':1,'sources':[SOURCE]}))
            self.assertEqual(load_sources(p)[0]['url'],SOURCE['url'])

    def test_source_failure_is_explicit_and_other_sources_continue(self):
        other={**SOURCE,'id':'google-ai','title':'Google','url':APPROVED_FEEDS['google-ai']['url']}
        def fake(source):
            if source['id']=='google-ai':raise DiscoveryError('blocked by test')
            return one()
        report=collect([SOURCE,other],now=NOW,fetcher=fake)
        self.assertEqual(report['status'],'partial');self.assertEqual(len(report['items']),1)
        self.assertEqual(report['sources'][1]['status'],'failed')
        self.assertIn('blocked',report['sources'][1]['error'])
        self.assertEqual(report['generation_status'],'awaiting_full_source_and_model')

    def test_fetch_uses_default_proxy_path_and_rejects_unapproved_redirect(self):
        from moh_ai.discovery import _FeedRedirects
        with patch('moh_ai.discovery.validate_public_url'):
            with self.assertRaises(DiscoveryError):
                _FeedRedirects(SOURCE).redirect_request(None,None,302,'',{},'https://127.0.0.1/feed')
        with self.assertRaises(DiscoveryError):fetch_feed({**SOURCE,'url':'https://openai.com/not-approved'})

    def test_proxy_mode_uses_inherited_default_handler_without_local_dns(self):
        import io
        class Response:
            def __init__(self):
                self.body=io.BytesIO(one());self.headers=Message();self.headers['Content-Type']='application/rss+xml'
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def geturl(self):return SOURCE['url']
            def read(self,n):return self.body.read(n)
        class Opener:
            def open(self,request,timeout):
                self.request=request;self.timeout=timeout;return Response()
        opener=Opener()
        with patch('moh_ai.discovery.getproxies',return_value={'https':'http://trusted-proxy:8080'}), \
             patch('moh_ai.discovery.proxy_bypass',return_value=False), \
             patch('moh_ai.discovery.validate_public_url',side_effect=AssertionError('must not do local DNS')), \
             patch('moh_ai.discovery.build_opener',return_value=opener) as builder:
            self.assertTrue(fetch_feed(SOURCE).startswith(b'<rss'))
            self.assertEqual(len(builder.call_args.args),1)
            self.assertEqual(opener.request.full_url,SOURCE['url'])

    def test_no_proxy_bypass_still_requires_public_dns(self):
        from moh_ai.discovery import _validate_feed_destination
        from moh_ai.sources import SourceError
        with patch('moh_ai.discovery.getproxies',return_value={'https':'http://trusted-proxy:8080'}), \
             patch('moh_ai.discovery.proxy_bypass',return_value=True), \
             patch('moh_ai.discovery.validate_public_url',side_effect=SourceError('non-public')):
            with self.assertRaises(SourceError):_validate_feed_destination(SOURCE['url'],SOURCE)

    def test_report_write_is_json_and_limit_is_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'report.json'
            report=collect([SOURCE],now=NOW,fetcher=lambda _:one())
            write_report(p,report)
            self.assertEqual(json.loads(p.read_text())['collection_method'],'rss')
            self.assertFalse(p.with_suffix('.json.tmp').exists())
        with self.assertRaises(DiscoveryError):rank_items([],now=NOW,limit=6)


if __name__=='__main__':unittest.main()
