import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from moh_ai import editorial as e
from moh_ai.media import render_svg
from moh_ai.pilot import load_pilot, pilot_dir


def pack(basis='full_text', status='primary_confirmed'):
    return {'source':{'title':'בדיקת מקור','content':'A clear request provides context.',
                      'url':'https://example.org/guide','published_at':None,'analysis_basis':basis},
            'claims':[{'id':'C1','statement':'A clear request provides context.',
                       'evidence_excerpt':'provides context','locator':'paragraph 1',
                       'verification_status':status,'verified_by':'test fixture researcher'}]}


def report(blocking=False, score=4):
    return {'reviewer':'test reviewer','review_type':'human','blocking':blocking,
            'findings':[],'scores':{k:score for k in ('accuracy','clarity','usefulness','voice')}}


class EditorialTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Path(self.temp.name)/'editorial.db'
    def tearDown(self): self.temp.cleanup()
    def draft(self,p=None,body='A clear request provides context.'):
        source=e.save_pack(self.db,p or pack())
        draft=e.save_draft(self.db,source['id'],body,[{'sentence':body,'claim_id':'C1'}])
        return draft
    def test_claim_quote_must_appear_in_content(self):
        p=pack();p['claims'][0]['evidence_excerpt']='fabricated evidence'
        with self.assertRaisesRegex(ValueError,'must occur'):e.save_pack(self.db,p)
    def test_pack_idempotence_and_changed_claim_versions(self):
        a=e.save_pack(self.db,pack());b=e.save_pack(self.db,pack())
        self.assertTrue(a['created']);self.assertFalse(b['created']);self.assertEqual(a['id'],b['id'])
        p=pack();p['claims'][0]['statement']='A revised claim statement.'
        c=e.save_pack(self.db,p)
        self.assertNotEqual(a['id'],c['id'])
        self.assertEqual(e.get_pack(self.db,a['id'])['claims'][0]['statement'],pack()['claims'][0]['statement'])
    def test_new_draft_version_does_not_inherit_review_or_approval(self):
        a=self.draft();e.record_review(self.db,a['id'],report());e.approve(self.db,a['id'],'עמית')
        b=e.save_draft(self.db,a['source_id'],'Revised contextual request.',[{'sentence':'Revised contextual request.','claim_id':'C1'}])
        self.assertEqual(b['version'],2);self.assertFalse(b['approved'])
        self.assertIsNone(e.get_draft(self.db,b['id'])['review'])
        with self.assertRaises(ValueError):e.export_bundle(self.db,b['id'],final=True)
    def test_missing_blocked_and_low_score_reviews_prevent_approval(self):
        d=self.draft()
        with self.assertRaises(ValueError):e.approve(self.db,d['id'],'עמית')
        for r in (report(blocking=True),report(score=3)):
            e.record_review(self.db,d['id'],r)
            with self.assertRaises(ValueError):e.approve(self.db,d['id'],'עמית')
    def test_current_explicit_approval_and_rereview_revocation(self):
        d=self.draft()
        with self.assertRaises(ValueError):e.export_bundle(self.db,d['id'],final=True)
        e.record_review(self.db,d['id'],report());e.approve(self.db,d['id'],'עמית')
        with zipfile.ZipFile(io.BytesIO(e.export_bundle(self.db,d['id'],final=True))) as z:
            self.assertTrue(json.loads(z.read('metadata.json'))['approved'])
            self.assertEqual(json.loads(z.read('metadata.json'))['image_approval_status'],'draft_requires_visual_approval')
            self.assertIn('תמונת טיוטה',z.read('image.svg').decode())
            self.assertNotIn('טרם אושרה',z.read('post.md').decode())
        e.record_review(self.db,d['id'],report())
        with self.assertRaises(ValueError):e.export_bundle(self.db,d['id'],final=True)
    def test_mapping_missing_sentence_and_unknown_claim_rejected(self):
        s=e.save_pack(self.db,pack())
        for mapping in ([{'sentence':'body','claim_id':'unknown'}],[{'sentence':'not present','claim_id':'C1'}],[]):
            with self.assertRaises(ValueError):e.save_draft(self.db,s['id'],'body',mapping)
    def test_description_only_cannot_support_source_summary(self):
        with self.assertRaisesRegex(ValueError,'description-only'):self.draft(pack(basis='description_only'))
    def test_unverified_claim_cannot_be_approved(self):
        d=self.draft(pack(status='source_claim_only'));e.record_review(self.db,d['id'],report())
        with self.assertRaisesRegex(ValueError,'verified'):e.approve(self.db,d['id'],'עמית')
    def test_svg_markup_is_escaped_and_bundle_is_safe(self):
        title='<script>alert("x")</script>'
        body='<image href="https://evil.example"> & text'
        svg=render_svg(title,body)
        self.assertNotIn('<script>',svg);self.assertNotIn('<image href=',svg)
        self.assertIn('&lt;script&gt;',svg);self.assertIn('&amp;',svg)
        p=pack();p['source']['title']=title
        d=self.draft(p,body)
        with zipfile.ZipFile(io.BytesIO(e.export_bundle(self.db,d['id']))) as z:
            self.assertNotIn('<script>',z.read('image.svg').decode())
            self.assertEqual(set(z.namelist()),{'post.md','evidence.json','review.json','image.svg','alt-text.txt','metadata.json'})
    def test_malformed_pack_is_rejected(self):
        for p in (None,[],{}, {'source':{}}, {'source':pack()['source'],'claims':['bad']}):
            with self.assertRaises((ValueError,TypeError)):e.save_pack(self.db,p)
    def test_review_required_scores_reject_bool_nan_string(self):
        d=self.draft()
        for bad in (True,float('nan'),'4',6):
            r=report();r['scores']['accuracy']=bad
            with self.assertRaises(ValueError):e.record_review(self.db,d['id'],r)
    def test_extra_review_score_keys_are_rejected(self):
        d=self.draft();r=report();r['scores']['unexpected']='bad value'
        with self.assertRaises(ValueError):e.record_review(self.db,d['id'],r)
        self.assertIsNone(e.get_draft(self.db,d['id'])['review'])

    def test_partial_pilot_import_resumes_missing_version_and_review(self):
        root=pilot_dir()
        source=e.save_pack(self.db,json.loads((root/'evidence.json').read_text()))
        first=e.save_draft(self.db,source['id'],(root/'post-v1.md').read_text(),
                           json.loads((root/'mappings-v1.json').read_text()),origin='assistant_session')
        result=load_pilot(self.db)
        drafts=e.list_workspace(self.db)['drafts']
        self.assertEqual(len(drafts),2)
        self.assertIn(first['id'],result['drafts'])
        self.assertTrue(all(d['review'] is not None for d in drafts))
        self.assertTrue(all(d['approved_by'] is None for d in drafts))
        self.assertFalse(result['already_loaded'])
        load_pilot(self.db)
        self.assertEqual(len(e.list_workspace(self.db)['drafts']),2)

    def test_existing_user_draft_does_not_skip_pilot_import(self):
        root=pilot_dir()
        source=e.save_pack(self.db,json.loads((root/'evidence.json').read_text()))
        body=(root/'post-v1.md').read_text()
        e.save_draft(self.db,source['id'],body,
                     json.loads((root/'mappings-v1.json').read_text()),origin='user_edit')
        result=load_pilot(self.db)
        drafts=e.list_workspace(self.db)['drafts']
        self.assertEqual(len(drafts),3)
        self.assertEqual(len(result['drafts']),2)
        self.assertEqual(sum(d['origin']=='assistant_session' for d in drafts),2)

    def test_pilot_has_two_assisted_unapproved_versions_and_no_duplicates(self):
        first=load_pilot(self.db);second=load_pilot(self.db)
        ws=e.list_workspace(self.db)
        self.assertEqual(len(ws['sources']),1);self.assertEqual(len(ws['drafts']),2)
        self.assertEqual({d['version'] for d in ws['drafts']},{1,2})
        self.assertTrue(all(d['origin']=='assistant_session' and d['approved_by'] is None for d in ws['drafts']))
        self.assertFalse(first['already_loaded']);self.assertTrue(second['already_loaded'])

if __name__=='__main__':unittest.main()
