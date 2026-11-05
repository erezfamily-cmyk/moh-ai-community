import json
from pathlib import Path
import tempfile
import unittest

from moh_ai import editorial, production


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'db.sqlite3'
        self.old = production.model_name()
        production.configure_model('moh-test-model-not-installed-xyz:latest')
        self.source_text = ('A clear explanation of artificial intelligence helps beginners understand tools. ' * 12)
        self.source = editorial.save_pack(self.db, {
            'source': {'title': 'כתבה לדוגמה', 'content': self.source_text,
                       'url': 'https://example.org/article', 'published_at': None,
                       'analysis_basis': 'full_text'},
            'claims': [{'id': 'C1', 'statement': 'A clear explanation of artificial intelligence helps beginners understand tools.',
                        'evidence_excerpt': 'A clear explanation of artificial intelligence helps beginners understand tools.',
                        'locator': 'paragraph 1', 'verification_status': 'source_claim_only', 'verified_by': None}]
        })

    def tearDown(self):
        production.configure_model(self.old)
        self.tmp.cleanup()

    def test_run_waits_for_local_model_and_exposes_agent_status(self):
        run = production.start_run(self.db, self.source['id'])
        self.assertEqual(run['status'], 'awaiting_local_model')
        snapshot = production.get_run(self.db, run['id'])
        self.assertEqual(snapshot['status'], 'awaiting_local_model')
        self.assertGreaterEqual(len(snapshot['tasks']), 7)
        self.assertTrue(all(task['status'] == 'waiting' for task in snapshot['tasks']))
        self.assertNotIn('api_key', json.dumps(snapshot))

    def test_local_model_can_save_generated_draft(self):
        sentence = 'A clear explanation of artificial intelligence helps beginners understand tools.'
        draft = editorial.save_draft(self.db, self.source['id'], sentence,
                                     [{'claim_id': 'C1', 'sentence': sentence}],
                                     origin='local_model')
        self.assertEqual(draft['origin'] if 'origin' in draft else editorial.get_draft(self.db, draft['id'])['origin'],
                         'local_model')

    def test_short_article_is_not_sent_to_model(self):
        source = editorial.save_pack(self.db, {
            'source': {'title': 'קצר', 'content': 'short', 'analysis_basis': 'full_text'},
            'claims': [{'id': 'C1', 'statement': 'short', 'evidence_excerpt': 'short', 'locator': 'p1',
                        'verification_status': 'source_claim_only'}]
        })
        with self.assertRaisesRegex(ValueError, '500'):
            production.start_run(self.db, source['id'])


if __name__ == '__main__':
    unittest.main()
