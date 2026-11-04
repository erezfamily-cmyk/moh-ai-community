"""Bounded public-source collection and deterministic briefs; no model or scheduler."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import re
import sqlite3
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
import uuid
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from . import editorial
from .sources import SourceError, canonicalize_url, fetch_document, fetch_source, parse_source

MAX_SOURCES = 20
MAX_ITEMS = 20  # Total article requests per run, including all feeds and links.
DEFAULT_SOURCES = [
    {'name': 'OpenAI News', 'url': 'https://openai.com/news/rss.xml', 'kind': 'rss',
     'authority': 'primary', 'enabled': False},
    {'name': 'Google AI Blog', 'url': 'https://blog.google/technology/ai/rss/', 'kind': 'rss',
     'authority': 'primary', 'enabled': False},
    {'name': 'Hugging Face Blog', 'url': 'https://huggingface.co/blog/feed.xml', 'kind': 'rss',
     'authority': 'primary', 'enabled': False},
]
SCHEMA = '''
CREATE TABLE IF NOT EXISTS collection_config (id INTEGER PRIMARY KEY CHECK(id=1), record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collection_runs (
 id TEXT PRIMARY KEY, day TEXT NOT NULL, started_at TEXT NOT NULL,
 finished_at TEXT, status TEXT NOT NULL, report TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS collection_one_running ON collection_runs(status) WHERE status='running';
CREATE TABLE IF NOT EXISTS collection_items (
 id TEXT PRIMARY KEY, url TEXT NOT NULL, content_hash TEXT NOT NULL, topic TEXT NOT NULL,
 record TEXT NOT NULL, pack_id TEXT NOT NULL, UNIQUE(url,content_hash));
CREATE TABLE IF NOT EXISTS collection_suggestions (item_id TEXT PRIMARY KEY, run_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collection_run_items (
 run_id TEXT NOT NULL, item_id TEXT NOT NULL, PRIMARY KEY(run_id,item_id));
'''


def connect(db):
    c = editorial.connection(db)
    c.executescript(SCHEMA)
    return c


def identity_url(url):
    p = urlsplit(canonicalize_url(url))
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in {'fbclid', 'gclid'}]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(sorted(query)), ''))


def validate_sources(entries):
    if not isinstance(entries, list) or len(entries) > MAX_SOURCES:
        raise ValueError('יש להגדיר רשימה של עד 20 מקורות')
    result, seen = [], set()
    for item in entries:
        if not isinstance(item, dict): raise ValueError('כל מקור חייב להיות אובייקט')
        name = editorial.required_text(item.get('name'), 'source name', 200)
        url = identity_url(item.get('url'))
        if len(url) > 2000: raise ValueError('source URL too long')
        kind = item.get('kind')
        if not isinstance(kind, str) or kind not in {'rss', 'link', 'video', 'social', 'transcript'}:
            raise ValueError('kind must be rss, link, video, social or transcript')
        authority = item.get('authority', 'unknown')
        if not isinstance(authority, str) or authority not in {'primary', 'secondary', 'unknown'}: raise ValueError('invalid authority')
        enabled = item.get('enabled', True)
        if type(enabled) is not bool: raise ValueError('enabled must be boolean')
        if url in seen: raise ValueError('duplicate source URL')
        seen.add(url)
        result.append(dict(name=name, url=url, kind=kind, authority=authority, enabled=enabled))
    return result


def get_sources(db):
    c = connect(db)
    try: row = c.execute('SELECT record FROM collection_config WHERE id=1').fetchone()
    finally: c.close()
    return json.loads(row[0]) if row else validate_sources(DEFAULT_SOURCES)


def set_sources(db, entries):
    entries = validate_sources(entries)
    c = connect(db)
    try:
        with c: c.execute('INSERT OR REPLACE INTO collection_config VALUES(1,?)', (json.dumps(entries),))
    finally: c.close()
    return {'sources': entries, 'scheduler': 'disabled'}


def publication(value):
    if not value: return None
    try:
        try: dt = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        except ValueError: dt = parsedate_to_datetime(value)
        # Do not invent a timezone for ambiguous dates.
        if dt.tzinfo is None: return None
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError): return None


def parse_feed(text, base_url):
    if re.search(r'<!\s*(DOCTYPE|ENTITY)', text, re.I):
        raise SourceError('DTD/entities are not allowed in feeds')
    try: root = ET.fromstring(text)
    except ET.ParseError as e: raise SourceError('Invalid RSS/Atom XML') from e
    def local(tag): return tag.rsplit('}', 1)[-1]
    if local(root.tag) not in {'rss', 'feed', 'RDF'}: raise SourceError('Not RSS or Atom')
    entries = []
    for node in root.iter():
        if local(node.tag) not in {'item', 'entry'}: continue
        fields, link = {}, None
        for child in node:
            key = local(child.tag)
            fields.setdefault(key, ''.join(child.itertext()).strip())
            if key == 'link' and child.attrib.get('rel', 'alternate') == 'alternate':
                link = child.attrib.get('href') or fields[key]
        if not link: link = fields.get('link')
        if not link: continue
        try: url = identity_url(urljoin(base_url, link))
        except SourceError: continue
        entries.append({'url': url, 'title': fields.get('title', '')[:300],
                        'published_at': fields.get('published') or fields.get('pubDate') or fields.get('updated')})
        if len(entries) >= MAX_ITEMS: break
    return entries


def rank(record, authority, clock):
    text = (record['title'] + '\n' + record['content']).lower()
    groups = [('AI', r'\bai\b|artificial intelligence|בינה מלאכותית|chatgpt|claude|gemini|llm'),
              ('למידה ופעולה', r'tutorial|guide|how to|learn|מדריך|איך |ללמוד|prompt|פרומפט'),
              ('כלי מעשי', r'tool|assistant|app|כלי|עוזר|יישום')]
    matched = [label for label, pattern in groups if re.search(pattern, text)]
    score = min(60, 20 * len(matched))
    reasons = ['התאמת מילות מפתח: ' + ', '.join(matched)] if matched else ['לא נמצאה התאמת מילות מפתח']
    if 'AI' not in matched: score = min(score, 10)
    if authority == 'primary': score += 15; reasons.append('מקור ראשוני לפי הגדרת המשתמש; אינו אימות טענות')
    elif authority == 'secondary': score += 5
    dt = publication(record.get('published_at'))
    if dt and timedelta(0) <= clock - dt <= timedelta(days=2): score += 15; reasons.append('פורסם ביומיים האחרונים')
    if not dt: reasons.append('תאריך פרסום לא ידוע או ללא אזור זמן; אין הנחת עדכניות')
    if record['analysis_basis'] in {'description_only', 'feed_metadata'}: score = min(score, 25)
    if 'AI' not in matched: score = min(score, 25)
    return score, reasons


def make_ideas(items, clock=None, previously_suggested=()):
    clock = clock or datetime.now(timezone.utc)
    ideas, topics, hashes = [], set(), set()
    for item in sorted(items, key=lambda x: (-x['score'], x['id'])):
        r = item['source']
        dt = publication(r.get('published_at'))
        if any(key in previously_suggested for key in (item['id'], 'topic:' + item['topic'], 'hash:' + r['content_hash'])): continue
        if dt and (clock - dt > timedelta(hours=72) or dt > clock + timedelta(minutes=5)): continue
        if item['score'] < 35 or r['analysis_basis'] in {'description_only', 'feed_metadata'}: continue
        if item['topic'] in topics or r['content_hash'] in hashes: continue

        keywords = r['title'].lower()
        angle = ('מדריך קצר' if re.search(r'tutorial|how to|guide|מדריך|איך ', keywords) else
                 'כלי לבדיקה' if re.search(r'tool|assistant|app|כלי|עוזר', keywords) else
                 'מושג להבנה' if re.search(r'explain|concept|מושג|מה זה', keywords) else 'חדשה עם משמעות')
        if sum(idea['angle'] == angle for idea in ideas) >= 2: continue
        topics.add(item['topic']); hashes.add(r['content_hash'])
        ideas.append({'item_id': item['id'], 'pack_id': item['pack_id'], 'title': r['title'],
            'angle': angle, 'score': item['score'], 'reasons': item['reasons'],
            'analysis_basis': r['analysis_basis'], 'source_url': r['canonical_url'],
            'published_at': r.get('published_at'), 'retrieved_at': r['retrieved_at'],
            'evidence_excerpt': r['content'][:280],
            'verification_status': 'source_claim_only', 'generation_status': 'awaiting_model',
            'community_value': 'נקודת פתיחה ללמידת AI בשפה פשוטה; התועלת בפועל דורשת בדיקת המקור.',
            'suggested_action': 'בחרו רעיון אחד מהמקור ונסחו דוגמה בטוחה לבדיקה, לפני שיתוף בקהילה.',
            'visual_brief': 'המחשה מוצעת: כרטיס עם מושג אחד, דוגמה ומקור; ללא טענת צילום מסך אמיתי.',
            'brief': f'זווית מוצעת: {angle}. בדקו את המקור, בחרו טענה נתמכת והסבירו בעברית פשוטה מה אפשר ללמוד או לנסות. אין כאן טיוטת פוסט שנוצרה.',
            'recommended': len(ideas) == 0,
            'limitations': r['limitations'] + ['דירוג היוריסטי בלבד; אמינות הטענות דורשת בדיקה אנושית.']})
        if len(ideas) == 5: break
    return ideas


def collect(db, force=False, clock=None):
    """One manual run per Jerusalem day; force explicitly retries. Durable single-flight lock."""
    if type(force) is not bool: raise ValueError('force must be boolean')
    clock = clock or datetime.now(timezone.utc)
    day = clock.astimezone(ZoneInfo('Asia/Jerusalem')).date().isoformat()
    configs = get_sources(db)
    config_hash = hashlib.sha256(json.dumps(configs, sort_keys=True).encode()).hexdigest()
    ident = uuid.uuid4().hex
    c = connect(db)
    try:
        with c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT 1 FROM collection_runs WHERE status='running'").fetchone():
                raise ValueError('איסוף כבר פועל. ריצה שנקטעה דורשת daily-reset לאחר סגירת התהליך הישן.')
            prior = c.execute("SELECT report FROM collection_runs WHERE day=? AND status IN ('completed','partial') ORDER BY started_at DESC LIMIT 1", (day,)).fetchone()
            if prior and not force and json.loads(prior[0]).get('config_hash') == config_hash: return {**json.loads(prior[0]), 'reused': True}
            c.execute('INSERT INTO collection_runs VALUES(?,?,?,NULL,?,?)',
                      (ident, day, clock.isoformat(), 'running', '{}'))
    finally: c.close()
    report = {'id': ident, 'day': day, 'started_at': clock.isoformat(), 'config_hash': config_hash, 'errors': [], 'items': [],
              'discoveries': [], 'duplicates': 0, 'requests': 0, 'generation_status': 'awaiting_model', 'scheduler': 'disabled'}
    try:
        candidates, seen = [], set()
        if not any(config['enabled'] for config in configs):
            report['errors'].append({'stage': 'config', 'error': 'אין מקורות פעילים. בדקו גישה למקור במחשב שלכם והפעילו אותו בהגדרות.', 'analysis_basis': 'unread'})
        for config in configs:
            if not config['enabled']: continue
            try:
                if config['kind'] == 'rss':
                    doc = fetch_document(config['url'])
                    entries = parse_feed(doc['text'], doc['canonical_url'])
                    report['discoveries'].append({'url': config['url'], 'canonical_url': doc['canonical_url'],
                        'retrieved_at': editorial.now(), 'raw_hash': hashlib.sha256(doc['text'].encode()).hexdigest(),
                        'analysis_basis': 'feed_metadata', 'entries': entries})
                else: entries = [{'url': config['url'], 'title': '', 'published_at': None}]
                for entry in entries:
                    if entry['url'] in seen: report['duplicates'] += 1; continue
                    seen.add(entry['url']); candidates.append((config, entry))
            except (SourceError, OSError, ValueError) as e:
                report['errors'].append({'url': config['url'], 'stage': 'discovery', 'error': str(e), 'analysis_basis': 'unread'})
        # Sort recent dated entries first; unknown dates remain eligible but never called fresh.
        def candidate_time(pair):
            dt = publication(pair[1]['published_at'])
            if not dt or dt > clock + timedelta(minutes=5) or clock - dt > timedelta(hours=72):
                return datetime.min.replace(tzinfo=timezone.utc)
            return dt
        candidates.sort(key=candidate_time, reverse=True)
        report['deferred'] = max(0, len(candidates) - MAX_ITEMS)
        for config, entry in candidates[:MAX_ITEMS]:
            report['requests'] += 1
            try:
                r = fetch_source(entry['url'])
                if config['kind'] in {'video', 'social'} and r['analysis_basis'] != 'transcript':
                    r['analysis_basis'] = 'description_only'; r['access_status'] = 'partial'
                    r['limitations'].append('עמוד וידאו/רשת חברתית בלבד; אין צפייה או תמלול.')
                if config['kind'] == 'transcript' and r['analysis_basis'] != 'transcript':
                    raise SourceError('Transcript URL must return VTT/SRT; paste plain transcripts manually')
                lowered = r['content'].lower()
                if len(lowered) < 2000 and re.search(r'captcha|verify you are human|sign in to continue|log in to continue|subscribe to continue|enable javascript|access denied', lowered):
                    raise SourceError('Access/login/challenge page; article not read')
                if r['access_status'] in {'blocked', 'needs_transcript'} or len(r['content'].strip()) < 80:
                    raise SourceError('Insufficient accessible text; supply article or transcript manually')
                if len(r['content']) > editorial.MAX_TEXT:
                    raise SourceError('Extracted evidence exceeds workspace limit; no silent truncation')
                r['title'] = (entry['title'] if r['title'] == 'Untitled source' else r['title'])[:300] or config['name']
                r['published_at'] = r['published_at'] or entry['published_at']
                r['canonical_url'] = identity_url(r['canonical_url'])
                # Registry authority only applies to the same host, never third-party feed links/redirects.
                authority = config['authority'] if urlsplit(config['url']).hostname == urlsplit(r['canonical_url']).hostname else 'unknown'
                score, reasons = rank(r, authority, clock)
                topic = re.sub(r'[^\w]+', ' ', r['title'].casefold()).strip()
                item_id = hashlib.sha256((r['canonical_url'] + '\n' + r['content_hash']).encode()).hexdigest()[:32]
                excerpt = r['content'][:280]
                pack = editorial.save_pack(db, {'source': {'title': r['title'], 'content': r['content'],
                    'url': r['canonical_url'], 'published_at': r['published_at'], 'retrieved_at': r['retrieved_at'],
                    'analysis_basis': r['analysis_basis'], 'provenance': 'daily_public_fetch'},
                    'claims': [{'id': 'C1', 'statement': 'קטע שנאסף מהמקור; טרם אומת או נוסח כטענה לפוסט',
                                'evidence_excerpt': excerpt, 'locator': (r['segments'][0]['timestamp'] or 'תחילת התמלול') if r['segments'] else 'תחילת הטקסט המחולץ', 'verification_status': 'source_claim_only'}]})
                item = dict(id=item_id, topic=topic, source=r, pack_id=pack['id'], score=score, reasons=reasons, authority=authority)
                c = connect(db)
                try:
                    with c:
                        inserted = c.execute('INSERT OR IGNORE INTO collection_items VALUES(?,?,?,?,?,?)',
                            (item_id, r['canonical_url'], r['content_hash'], topic, json.dumps(item, ensure_ascii=False), pack['id'])).rowcount
                        c.execute('INSERT OR IGNORE INTO collection_run_items VALUES(?,?)', (ident, item_id))
                    if not inserted: report['duplicates'] += 1
                finally: c.close()
                report['items'].append(item)
            except (SourceError, ValueError, OSError) as e:
                report['errors'].append({'url': entry['url'], 'stage': 'read', 'error': str(e), 'analysis_basis': 'unread'})
        c = connect(db)
        try:
            previous = set()
            for row in c.execute('SELECT i.id,i.topic,i.content_hash FROM collection_suggestions s JOIN collection_items i ON s.item_id=i.id'):
                previous.update((row[0], 'topic:' + row[1], 'hash:' + row[2]))
            report['ideas'] = make_ideas(report['items'], clock, previous)
            with c:
                c.executemany('INSERT OR IGNORE INTO collection_suggestions VALUES(?,?)',
                              [(idea['item_id'], ident) for idea in report['ideas']])
        finally: c.close()
        report['status'] = 'partial' if report['errors'] else 'completed'
    except Exception:
        report['status'] = 'failed'
        report['errors'].append({'stage': 'run', 'error': 'Unexpected collection failure; inspect local data and retry explicitly', 'analysis_basis': 'unread'})
        report['ideas'] = []
        raise
    finally:
        report['finished_at'] = editorial.now()
        c = connect(db)
        try:
            with c: c.execute('UPDATE collection_runs SET status=?,finished_at=?,report=? WHERE id=?',
                              (report['status'], report['finished_at'], json.dumps(report, ensure_ascii=False), ident))
        finally: c.close()
    return report


def reset_interrupted(db):
    """Explicit operator recovery only, after stopping the old collector. No automatic takeover."""
    c = connect(db)
    try:
        with c:
            count = c.execute("UPDATE collection_runs SET status='interrupted',finished_at=? WHERE status='running'", (editorial.now(),)).rowcount
    finally: c.close()
    return {'interrupted': count}


def workspace(db):
    c = connect(db)
    try:
        row = c.execute('SELECT report,status FROM collection_runs ORDER BY started_at DESC,rowid DESC LIMIT 1').fetchone()
        recent = [dict(r) for r in c.execute('SELECT id,day,started_at,finished_at,status FROM collection_runs ORDER BY started_at DESC,rowid DESC LIMIT 10')]
    finally: c.close()
    return {'sources': get_sources(db), 'latest': ({**json.loads(row['report']), 'status': row['status']} if row else None),
            'runs': recent, 'scheduler': 'disabled', 'generation_status': 'awaiting_model'}
