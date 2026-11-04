"""Versioned, offline editorial workflow. No AI provider or hidden generation calls."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import uuid
import zipfile

MAX_TEXT = 200_000
BASES = {'full_text','transcript','audiovisual','description_only','user_supplied'}
VERIFIED = {'primary_confirmed','corroborated'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS editorial_sources (
 id TEXT PRIMARY KEY, record TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS editorial_drafts (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES editorial_sources(id),
 version INTEGER NOT NULL, body TEXT NOT NULL, claim_map TEXT NOT NULL,
 voice_status TEXT NOT NULL, origin TEXT NOT NULL, review TEXT,
 created_at TEXT NOT NULL, UNIQUE(source_id,version));
CREATE TABLE IF NOT EXISTS editorial_approvals (
 draft_id TEXT PRIMARY KEY REFERENCES editorial_drafts(id), actor TEXT NOT NULL,
 approved_at TEXT NOT NULL, body_hash TEXT NOT NULL);
'''


def now():
    return datetime.now(timezone.utc).isoformat()


def required_text(value, label, limit=MAX_TEXT):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        raise ValueError(f'{label}: expected non-empty text, maximum {limit} characters')
    return value.strip()


def connection(db):
    db = Path(db)
    db.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(db,timeout=15)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.executescript(SCHEMA)
    return c


def validate_pack(pack):
    if not isinstance(pack,dict): raise ValueError('evidence pack must be an object')
    source=pack.get('source')
    if not isinstance(source,dict): raise ValueError('source object is required')
    content=required_text(source.get('content'),'source.content')
    title=required_text(source.get('title'),'source.title',300)
    basis=source.get('analysis_basis')
    if not isinstance(basis,str) or basis not in BASES: raise ValueError('invalid analysis_basis')
    claims=pack.get('claims',[])
    if not isinstance(claims,list) or len(claims)>30: raise ValueError('claims must contain at most 30 objects')
    seen=set()
    cleaned=[]
    for claim in claims:
        if not isinstance(claim,dict): raise ValueError('claim must be object')
        ident=required_text(claim.get('id'),'claim.id',80)
        if ident in seen: raise ValueError('duplicate claim id')
        seen.add(ident)
        quote=required_text(claim.get('evidence_excerpt'),'claim.evidence_excerpt',500)
        if quote not in content: raise ValueError('claim evidence must occur in supplied source content')
        status=claim.get('verification_status','source_claim_only')
        if not isinstance(status,str) or status not in VERIFIED|{'source_claim_only','disputed','unknown'}:
            raise ValueError('invalid verification_status')
        if status in VERIFIED and not claim.get('verified_by'):
            raise ValueError('verified claims require explicit verified_by provenance')
        cleaned.append({'id':ident,'statement':required_text(claim.get('statement'),'claim.statement',1200),
            'evidence_excerpt':quote,'locator':required_text(claim.get('locator'),'claim.locator',500),
            'verification_status':status,'verified_by':claim.get('verified_by')})
    from urllib.parse import urlsplit
    url=source.get('url')
    if url is not None:
        if not isinstance(url,str) or len(url)>2000: raise ValueError('invalid source URL')
        parts=urlsplit(url)
        if parts.scheme!='https' or not parts.hostname or parts.username or parts.password:
            raise ValueError('source URL must be HTTPS without credentials')
    publication=source.get('published_at')
    if publication is not None and not isinstance(publication,str): raise ValueError('published_at must be string or null')
    return {'source':{'title':title,'content':content,'url':url,'published_at':publication,
        'retrieved_at':source.get('retrieved_at') or now(),'analysis_basis':basis,
        'coverage':source.get('coverage','supplied_content'),'provenance':source.get('provenance','user_import')},
        'claims':cleaned}


def save_pack(db,pack):
    pack=validate_pack(pack)
    identity={k:pack['source'].get(k) for k in ('content','url','analysis_basis')}
    ident=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
    # Evidence versions are immutable. Changed claims yield a new pack identity too.
    ident=hashlib.sha256((ident+json.dumps(pack['claims'],sort_keys=True,ensure_ascii=False)).encode()).hexdigest()[:24]
    c=connection(db)
    try:
        with c:
            inserted=c.execute('INSERT OR IGNORE INTO editorial_sources VALUES(?,?,?)',
                (ident,json.dumps(pack,ensure_ascii=False),now())).rowcount
    finally:c.close()
    return {'id':ident,'created':bool(inserted),'status':'collected','generation_status':'awaiting_model'}


def get_pack(db,source_id):
    c=connection(db)
    try: row=c.execute('SELECT record FROM editorial_sources WHERE id=?',(source_id,)).fetchone()
    finally:c.close()
    if row is None: raise ValueError('source not found')
    return json.loads(row['record'])


def list_workspace(db):
    c=connection(db)
    try:
        sources=[{'id':r['id'],**json.loads(r['record'])} for r in c.execute('SELECT * FROM editorial_sources ORDER BY created_at DESC')]
        drafts=[dict(r) for r in c.execute('SELECT d.*,a.actor AS approved_by FROM editorial_drafts d LEFT JOIN editorial_approvals a ON a.draft_id=d.id ORDER BY d.created_at DESC,d.version DESC')]
    finally:c.close()
    for d in drafts:
        d['claim_map']=json.loads(d['claim_map']);d['review']=json.loads(d['review']) if d['review'] else None
    return {'sources':sources,'drafts':drafts,'model_mode':'manual','generation_status':'awaiting_model'}


def make_brief(db,source_id):
    pack=get_pack(db,source_id)
    s=pack['source']
    return {'title':s['title'],'analysis_basis':s['analysis_basis'],'source_url':s['url'],
        'claims':pack['claims'],'voice_status':'provisional','generation_status':'awaiting_model',
        'prompt':'כתוב פוסט קצר וברור לקהילת מוח AI על בסיס הראיות בלבד. הסבר רעיון אחד, תן דוגמה מקורית ופעולה. אל תמציא ניסיון אישי. שמור מקור, אי־ודאות ומיפוי טענות. הקול זמני עד אישור עמית.'}


def save_draft(db,source_id,body,claim_map,origin='user_edit'):
    pack=get_pack(db,source_id)
    body=required_text(body,'draft body',12_000)
    if pack['source']['analysis_basis']=='description_only':
        raise ValueError('description-only source cannot support a source-summary draft; supply full text or transcript')
    if origin not in {'user_edit','assistant_session','local_model'}:raise ValueError('invalid draft origin')
    if origin=='local_model':raise ValueError('local model generation is not implemented')
    if not isinstance(claim_map,list) or len(claim_map)>50: raise ValueError('claim_map must be array')
    claims={x['id']:x for x in pack['claims']}
    if not claim_map: raise ValueError('at least one evidence mapping is required')
    for mapping in claim_map:
        if not isinstance(mapping,dict):raise ValueError('claim mapping must be object')
        if mapping.get('claim_id') not in claims:raise ValueError('unknown claim id')
        sentence=required_text(mapping.get('sentence'),'mapped sentence',2000)
        if sentence not in body:raise ValueError('mapped sentence is absent from draft')
    c=connection(db)
    try:
        with c:
            c.execute('BEGIN IMMEDIATE')
            version=c.execute('SELECT COALESCE(MAX(version),0)+1 FROM editorial_drafts WHERE source_id=?',(source_id,)).fetchone()[0]
            ident=uuid.uuid4().hex[:24]
            c.execute('INSERT INTO editorial_drafts VALUES(?,?,?,?,?,?,?,?,?)',
                (ident,source_id,version,body,json.dumps(claim_map,ensure_ascii=False),'provisional',origin,None,now()))
    finally:c.close()
    return {'id':ident,'source_id':source_id,'version':version,'status':'draft','approved':False}


def get_draft(db,draft_id):
    c=connection(db)
    try:row=c.execute('SELECT * FROM editorial_drafts WHERE id=?',(draft_id,)).fetchone()
    finally:c.close()
    if row is None:raise ValueError('draft not found')
    result=dict(row);result['claim_map']=json.loads(result['claim_map'])
    result['review']=json.loads(result['review']) if result['review'] else None
    return result


def record_review(db,draft_id,report):
    draft=get_draft(db,draft_id)
    if not isinstance(report,dict):raise ValueError('review must be object')
    required_text(report.get('reviewer'),'reviewer',200)
    if report.get('review_type') not in {'human','ai_session'}:raise ValueError('invalid review type')
    if not isinstance(report.get('blocking'),bool):raise ValueError('blocking must be boolean')
    if not isinstance(report.get('findings'),list):raise ValueError('findings must be array')
    scores=report.get('scores')
    if not isinstance(scores,dict) or set(scores)!={'accuracy','clarity','usefulness','voice'} or any(type(scores.get(k)) is not int or not 1<=scores[k]<=5 for k in ('accuracy','clarity','usefulness','voice')):
        raise ValueError('review scores must be integers 1..5 for accuracy, clarity, usefulness, voice')
    # New review invalidates any prior approval; approval must follow the current report.
    report={**report,'draft_body_hash':hashlib.sha256(draft['body'].encode()).hexdigest(),'reviewed_at':now()}
    c=connection(db)
    try:
        with c:
            c.execute('UPDATE editorial_drafts SET review=? WHERE id=?',(json.dumps(report,ensure_ascii=False),draft_id))
            c.execute('DELETE FROM editorial_approvals WHERE draft_id=?',(draft_id,))
    finally:c.close()
    return {'draft_id':draft_id,'status':'needs_revision' if report['blocking'] else 'reviewed'}


def approve(db,draft_id,actor):
    actor=required_text(actor,'approval actor',100)
    d=get_draft(db,draft_id);review=d['review']
    if not review or review['blocking'] or any(v<4 for v in review['scores'].values()):
        raise ValueError('a current non-blocking review scoring at least 4 is required')
    if review['draft_body_hash']!=hashlib.sha256(d['body'].encode()).hexdigest():raise ValueError('stale review')
    pack=get_pack(db,d['source_id']);claims={c['id']:c for c in pack['claims']}
    if any(claims[m['claim_id']]['verification_status'] not in VERIFIED for m in d['claim_map']):
        raise ValueError('mapped factual claims must be explicitly verified before approval')
    c=connection(db)
    try:
        with c:c.execute('INSERT OR REPLACE INTO editorial_approvals VALUES(?,?,?,?)',
            (draft_id,actor,now(),hashlib.sha256(d['body'].encode()).hexdigest()))
    finally:c.close()
    return {'draft_id':draft_id,'status':'approved','actor':actor}


def export_bundle(db,draft_id,final=False):
    d=get_draft(db,draft_id);pack=get_pack(db,d['source_id'])
    c=connection(db)
    try:approval=c.execute('SELECT * FROM editorial_approvals WHERE draft_id=?',(draft_id,)).fetchone()
    finally:c.close()
    if final and (not approval or approval['body_hash']!=hashlib.sha256(d['body'].encode()).hexdigest()):
        raise ValueError('explicit approval for this exact draft is required')
    # Assets are rendered from the current body during export, not taken from arbitrary paths.
    from .media import render_svg
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        prefix='' if final else '> טיוטה לבדיקה — טרם אושרה לפרסום.\n\n'
        z.writestr('post.md',prefix+d['body'])
        z.writestr('evidence.json',json.dumps(pack,ensure_ascii=False,indent=2))
        z.writestr('review.json',json.dumps(d['review'],ensure_ascii=False,indent=2))
        z.writestr('image.svg',render_svg(pack['source']['title'],d['body']))
        z.writestr('alt-text.txt','כרטיס הסבר בנושא '+pack['source']['title'])
        z.writestr('metadata.json',json.dumps({'version':d['version'],'voice_status':d['voice_status'],
            'origin':d['origin'],'approved':bool(approval),'final':final,'generation_status':'manual_assisted',
            'image_approval_status':'draft_requires_visual_approval'},ensure_ascii=False))
    return output.getvalue()
