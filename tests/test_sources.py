import socket
import unittest
from unittest.mock import Mock, patch
from moh_ai.sources import SourceError, parse_source, validate_public_url, fetch_source

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
PRIVATE = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]


class SourcesTests(unittest.TestCase):
    def test_html_extracts_article_not_scripts_or_navigation(self):
        record = parse_source('<title>כותרת</title><nav>ignore</nav><article><p>שלום</p>'
                              '<script>run evil()</script><style>bad</style><p>עולם</p></article>'
                              '<p>outside</p>', 'html', url='https://example.org/a#top')
        self.assertEqual(record['content'], 'שלום\nעולם')
        self.assertEqual(record['title'], 'כותרת')
        self.assertEqual(record['canonical_url'], 'https://example.org/a')
        self.assertEqual(record['analysis_basis'], 'full_text')
        self.assertIsNone(record['published_at'])
        self.assertEqual(record['verification_status'], 'unknown')

    def test_meta_date_is_not_replaced_with_retrieval_date(self):
        record = parse_source('<meta property="article:published_time" content="2024-01-02">'
                              '<article><p>real content</p></article>', 'html')
        self.assertEqual(record['published_at'], '2024-01-02')
        self.assertNotEqual(record['published_at'], record['retrieved_at'])

    def test_transcript_retains_timestamps_without_claiming_visual_analysis(self):
        record = parse_source('WEBVTT\n\nNOTE editorial only\nnot spoken\n\n00:00:01.000 --> 00:00:03.000\nשלום <b>עולם</b>\n\n00:00:04.000 --> 00:00:05.000\nשוב שלום\n', 'vtt')
        self.assertEqual(record['content'], 'שלום עולם\nשוב שלום')
        self.assertEqual(record['segments'][0]['timestamp'], '00:00:01.000')
        self.assertEqual(record['analysis_basis'], 'transcript')
        self.assertIn('not inspected', record['limitations'][0])

    def test_video_landing_page_is_not_transcript_or_watched_video(self):
        r = parse_source('<title>Great video</title><p>Subscribe and watch</p>', 'html',
                         url='https://www.youtube.com/shorts/abc')
        self.assertEqual(r['analysis_basis'], 'description_only')
        self.assertEqual(r['access_status'], 'partial')

    def test_srt_and_plain_transcript(self):
        record = parse_source('1\n00:00:01,000 --> 00:00:02,000\nhello\n\n2\n00:00:03,000 --> 00:00:04,000\nworld', 'srt')
        self.assertEqual(record['content'], 'hello\nworld')
        self.assertEqual(record['segments'][1]['timestamp'], '00:00:03,000')
        self.assertEqual(parse_source('spoken text', 'transcript')['segments'][0]['timestamp'], None)

    def test_empty_and_description_have_explicit_limits(self):
        self.assertEqual(parse_source('  ', 'transcript')['access_status'], 'needs_transcript')
        self.assertEqual(parse_source('<script>bad</script>', 'html')['access_status'], 'blocked')
        record = parse_source('video description only', 'description')
        self.assertEqual(record['access_status'], 'partial')
        self.assertEqual(record['analysis_basis'], 'description_only')

    def test_bad_schemes_credentials_ports_and_whitespace_rejected_before_dns(self):
        with patch('moh_ai.sources.socket.getaddrinfo') as dns:
            for url in ['http://example.org', 'file:///etc/passwd', 'https://user:x@example.org',
                        'https://example.org:8443', 'https://example.org/\nsecret', 'https://[fe80::1%eth0]']:
                with self.subTest(url=url), self.assertRaises(SourceError):
                    validate_public_url(url)
            dns.assert_not_called()

    def test_any_private_dns_answer_blocks_host(self):
        with patch('moh_ai.sources.socket.getaddrinfo', return_value=PUBLIC+PRIVATE):
            with self.assertRaisesRegex(SourceError, 'non-public'):
                validate_public_url('https://example.org')
        with patch('moh_ai.sources.socket.getaddrinfo', return_value=[(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::1',443,0,0))]):
            with self.assertRaises(SourceError):
                validate_public_url('https://example.org')

    def connection(self, status=200, headers=None, body=b'hello'):
        response = Mock(status=status)
        response.getheader.side_effect = lambda k, default=None: (headers or {'Content-Type':'text/plain'}).get(k,default)
        response.read1.side_effect = [body,b'']
        conn = Mock()
        conn.getresponse.return_value = response
        return conn

    def test_fetch_pins_validated_address_and_keeps_original_hostname(self):
        conn = self.connection()
        with patch('moh_ai.sources.socket.getaddrinfo', return_value=PUBLIC), patch('moh_ai.sources._PinnedHTTPSConnection', return_value=conn) as factory:
            record = fetch_source('https://example.org/read')
        args = factory.call_args.args
        self.assertEqual(args[0], 'example.org')
        self.assertEqual(args[1][-1], ('93.184.216.34',443))
        self.assertEqual(record['content'], 'hello')
        self.assertEqual(record['requested_url'], 'https://example.org/read')
        conn.close.assert_called_once()

    def test_private_redirect_never_opens_second_connection(self):
        conn = self.connection(302, {'Location':'https://127.0.0.1/admin'})
        with patch('moh_ai.sources.socket.getaddrinfo', side_effect=[PUBLIC,PRIVATE]), patch('moh_ai.sources._PinnedHTTPSConnection', return_value=conn) as factory:
            with self.assertRaisesRegex(SourceError, 'non-public'):
                fetch_source('https://example.org')
            self.assertEqual(factory.call_count, 1)
            conn.close.assert_called_once()

    def test_oversize_unknown_length_response_rejected(self):
        conn = self.connection(body=b'123456')
        with patch('moh_ai.sources.socket.getaddrinfo', return_value=PUBLIC), patch('moh_ai.sources._PinnedHTTPSConnection', return_value=conn):
            with self.assertRaisesRegex(SourceError, 'size limit'):
                fetch_source('https://example.org',max_bytes=5)

    def test_feed_transport_uses_same_pinned_fetch_limits(self):
        from moh_ai.sources import fetch_document
        conn = self.connection(headers={'Content-Type':'application/rss+xml'}, body=b'<rss/>')
        with patch('moh_ai.sources.socket.getaddrinfo', return_value=PUBLIC), patch('moh_ai.sources._PinnedHTTPSConnection', return_value=conn):
            result = fetch_document('https://example.org/feed')
        self.assertEqual(result['text'], '<rss/>')
        self.assertEqual(result['mime'], 'application/rss+xml')
        self.assertEqual(result['canonical_url'], 'https://example.org/feed')
        conn.close.assert_called_once()
        conn = self.connection(headers={'Content-Type':'application/atom+xml'}, body=b'<feed/>')
        with patch('moh_ai.sources.socket.getaddrinfo', return_value=PUBLIC), patch('moh_ai.sources._PinnedHTTPSConnection', return_value=conn):
            with self.assertRaisesRegex(SourceError, 'not article evidence'):
                fetch_source('https://example.org/feed')

    def test_unsupported_content_and_http_failures_explicit(self):
        for status,headers in [(403,{}),(200,{'Content-Type':'video/mp4'}),(200,{'Content-Type':'text/plain','Content-Encoding':'gzip'})]:
            with self.subTest(status=status,headers=headers), patch('moh_ai.sources.socket.getaddrinfo', return_value=PUBLIC), patch('moh_ai.sources._PinnedHTTPSConnection',return_value=self.connection(status,headers)):
                with self.assertRaises(SourceError):
                    fetch_source('https://example.org')

    def test_source_metadata_types_fail_explicitly(self):
        for options in ({'title':{}},{'title':5},{'url':{}},{'url':[]},
                        {'published_at':[]},{'published_at':2026},{'kind':[]}):
            kwargs={'kind':'text',**options}
            with self.subTest(options=options), self.assertRaises(SourceError):
                parse_source('real source text',**kwargs)

    def test_large_supplied_source_rejected(self):
        with self.assertRaises(SourceError):
            parse_source('x'*(2*1024*1024+1),'text')


if __name__ == '__main__':
    unittest.main()
