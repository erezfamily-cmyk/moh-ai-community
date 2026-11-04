"""Load a real, session-assisted pilot, never claiming autonomous local generation."""
import json
from pathlib import Path
from .editorial import save_pack,save_draft,record_review,list_workspace


def pilot_dir():
    candidate=Path(__file__).resolve().parents[2]/'pilot'/'01-clear-request'
    if not candidate.is_dir():raise ValueError('pilot assets require running from the repository checkout')
    return candidate


def load_pilot(db):
    root=pilot_dir()
    pack=json.loads((root/'evidence.json').read_text())
    saved=save_pack(db,pack)
    existing=[d for d in list_workspace(db)['drafts'] if d['source_id']==saved['id'] and d['origin']=='assistant_session']
    drafts=[]
    created=False
    for version in (1,2):
        body=(root/f'post-v{version}.md').read_text().strip()
        found=next((d for d in existing if d['body']==body),None)
        if found:
            drafts.append(found['id'])
            if not found['review']:
                record_review(db,found['id'],json.loads((root/f'review-v{version}.json').read_text()))
            continue
        d=save_draft(db,saved['id'],body,
            json.loads((root/f'mappings-v{version}.json').read_text()),origin='assistant_session')
        record_review(db,d['id'],json.loads((root/f'review-v{version}.json').read_text()))
        drafts.append(d['id'])
        created=True
    return {'source_id':saved['id'],'drafts':drafts,'already_loaded':not created,'approved':False,
        'origin':'assistant_session','model_mode':'manual'}
