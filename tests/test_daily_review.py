"""Independent collection acceptance regressions: evidence, freshness and repeats."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from moh_ai import daily


class CollectionReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'review.sqlite3'
        self.clock = datetime(2026, 11, 4, 10, tzinfo=timezone.utc)
        self.url = 'https://example.org/ai-guide'
        daily.set_sources(self.db, [{
            'name': 'Review source', 'url': self.url, 'kind': 'link',
            'authority': 'primary', 'enabled': True,
        }])

    def document(self, html):
        return {'text': html, 'mime': 'text/html',
                'canonical_url': self.url, 'requested_url': self.url}

    def article(self, date='2026-11-04T08:00:00Z'):
        return self.document(
            '<title>AI prompt tutorial assistant</title>'
            f'<meta property="article:published_time" content="{date}">'
            '<article><p>This AI tutorial explains how to write a prompt for an assistant. '
            'Learners should compare the answer against original evidence and check '
            'important facts before sharing the result with anyone.</p></article>'
        )

    def test_valid_public_article_is_collected_without_a_model(self):
        with patch('moh_ai.sources.fetch_document', return_value=self.article()):
            result = daily.collect(self.db, clock=self.clock)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(result['ideas']), 1)
        self.assertEqual(result['generation_status'], 'awaiting_model')
        self.assertEqual(result['ideas'][0]['analysis_basis'], 'full_text')
        self.assertEqual(result['ideas'][0]['verification_status'], 'source_claim_only')
        self.assertEqual(result['scheduler'], 'disabled')

    def test_next_day_does_not_recommend_an_unchanged_suggested_item(self):
        with patch('moh_ai.sources.fetch_document', return_value=self.article()):
            first = daily.collect(self.db, clock=self.clock)
            second = daily.collect(self.db, clock=self.clock + timedelta(days=1))
        self.assertEqual(len(first['ideas']), 1)
        self.assertEqual(second['ideas'], [])
        self.assertGreaterEqual(second['duplicates'], 1)

    def test_syndicated_content_at_new_url_is_not_recommended_next_day(self):
        with patch('moh_ai.sources.fetch_document', return_value=self.article()):
            first = daily.collect(self.db, clock=self.clock)
        new_url = 'https://example.org/syndicated-ai-guide'
        daily.set_sources(self.db, [{
            'name': 'Syndicated source', 'url': new_url, 'kind': 'link',
            'authority': 'primary', 'enabled': True,
        }])
        copy = {**self.article(), 'canonical_url': new_url, 'requested_url': new_url}
        with patch('moh_ai.sources.fetch_document', return_value=copy):
            second = daily.collect(self.db, clock=self.clock + timedelta(days=1))
        self.assertEqual(len(first['ideas']), 1)
        self.assertEqual(second['ideas'], [])

    def test_old_article_is_evidence_but_not_a_morning_recommendation(self):
        with patch('moh_ai.sources.fetch_document', return_value=self.article('2010-01-01T00:00:00Z')):
            result = daily.collect(self.db, clock=self.clock)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['ideas'], [])
        self.assertEqual(result['items'][0]['source']['published_at'], '2010-01-01T00:00:00Z')

    def test_login_wall_is_not_promoted_to_full_article_evidence(self):
        login = self.document(
            '<title>New AI tutorial assistant</title><p>Sign in to continue. '
            'This content is available for subscribers only. Please enter your email '
            'address to view the full article and manage your subscription.</p>'
        )
        with patch('moh_ai.sources.fetch_document', return_value=login):
            result = daily.collect(self.db, clock=self.clock)
        self.assertEqual(result['ideas'], [])
        self.assertTrue(result['errors'], 'An explicit access/content failure must be recorded')


if __name__ == '__main__':
    unittest.main()
