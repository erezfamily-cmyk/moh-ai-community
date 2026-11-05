"""Free, metadata-only discovery from explicitly approved public RSS/Atom feeds.

Uses urllib's inherited proxy settings. This module never calls an AI provider,
fetches article bodies, watches a video, or claims that feed metadata is verified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import time
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, getproxies, proxy_bypass
import xml.etree.ElementTree as ET

from .sources import SourceError, canonicalize_url, validate_public_url

MAX_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 500
MAX_SOURCES = 20
APPROVED_FEEDS = {
    'openai-news': {'url': 'https://openai.com/news/rss.xml',
                    'hosts': {'openai.com', 'www.openai.com'},
                    'article_hosts': {'openai.com', 'www.openai.com'}},
    'google-ai': {'url': 'https://blog.google/innovation-and-ai/technology/ai/rss/',
                  'hosts': {'blog.google'}, 'article_hosts': {'blog.google'}},
    'huggingface-blog': {'url': 'https://huggingface.co/blog/feed.xml',
                        'hosts': {'huggingface.co'}, 'article_hosts': {'huggingface.co'}},
}
TRACKING = {'fbclid', 'gclid', 'dclid', 'trk', 'ref_src', 'mc_cid', 'mc_eid'}
AI_TERMS = ('artificial intelligence', ' ai ', 'chatgpt', 'gemini', 'notebooklm',
            'claude', 'language model', ' llm', 'model', 'agent', 'prompt',
            'transcription', 'speech', 'generative', 'multimodal', 'deep research')
PRACTICAL = ('how to', 'guide', 'tutorial', 'prompt', 'notebooklm', 'chatgpt',
             'gemini', 'document', 'pdf', 'presentation', 'deck', 'slides',
             'transcrib', 'speech', 'writing', 'learn', 'tool', 'search', 'extractor')
ADVANCED = ('benchmark', 'cwe', 'reinforcement learning', 'vllm', 'cuda',
            'trainium', 'govcloud', 'chief ', 'acquire', 'bug bount', 'infrastructure')


class DiscoveryError(ValueError):
    pass


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.skip += 1
        elif tag in {'p', 'br', 'li', 'div'}:
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'} and self.skip:
            self.skip -= 1

    def handle_data(self, value):
        if not self.skip:
            self.parts.append(value)


def plain_text(value: str) -> str:
    parser = _PlainText()
    parser.feed(value or '')
    return re.sub(r'\s+', ' ', ''.join(parser.parts)).strip()


def normalize_url(value: str) -> str:
    canonical = canonicalize_url(value)
    parts = urlsplit(canonical)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in TRACKING]
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/') or '/',
                       urlencode(sorted(query)), ''))


def publication_date(value: str | None) -> str | None:
    """Only timezone-aware publisher timestamps qualify as an exact time."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value.strip())
        except (ValueError, TypeError, OverflowError):
            return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def _now(value=None):
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise DiscoveryError('now must include a timezone')
    return value.astimezone(timezone.utc)


def read_report(path: str | Path) -> dict:
    """Read the local discovery snapshot without upgrading metadata to evidence."""
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data,dict) or data.get('schema_version')!=1 or not isinstance(data.get('items'),list):
        raise DiscoveryError('invalid discovery report')
    return data


def propose_angles(record: dict) -> list[dict]:
    """Offer transparent editorial directions from extracted text, without an AI model."""
    if not isinstance(record,dict) or record.get('analysis_basis')!='full_text' or len(record.get('content',''))<500:
        raise DiscoveryError('full article text is required before proposing angles')
    text=plain_text(record['content'])
    sentences=[re.sub(r'\s+',' ',s).strip() for s in re.split(r'(?<=[.!?])\s+',text) if len(s.split())>=8]
    evidence=[]
    for sentence in sentences:
        if len(sentence)>500:continue
        if any(sentence==x['excerpt'] for x in evidence):continue
        evidence.append({'excerpt':sentence,'locator':'קטע מהכתבה המלאה'})
        if len(evidence)==3:break
    if not evidence:raise DiscoveryError('could not identify short evidence excerpts; review the text manually')
    title=record.get('title') or 'הנושא שבכתבה'
    return [
        {'id':'explain','title':'להסביר רעיון אחד','angle':f'מה כדאי למתחילים להבין על {title}?','basis':'editorial_template','evidence':evidence[:2]},
        {'id':'try','title':'להציע התנסות קטנה','angle':'איזו פעולה פשוטה אפשר לנסות בעקבות הכתבה, ומה צריך לבדוק בתוצאה?','basis':'editorial_template','evidence':evidence[:2]},
        {'id':'question','title':'להעלות שאלה לקהילה','angle':'איזו שאלה פתוחה או מגבלה בכתבה כדאי לברר יחד?','basis':'editorial_template','evidence':evidence[-2:]},
    ]


def load_sources(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data.get('schema_version') != 1:
        raise DiscoveryError('sources config requires schema_version=1')
    sources = data.get('sources')
    if not isinstance(sources, list) or len(sources) > MAX_SOURCES:
        raise DiscoveryError('sources must be a list of at most 20 entries')
    seen = set()
    clean = []
    for source in sources:
        if not isinstance(source, dict):
            raise DiscoveryError('source must be an object')
        ident = source.get('id')
        if not isinstance(ident, str) or ident not in APPROVED_FEEDS or ident in seen:
            raise DiscoveryError('source id must be unique and explicitly approved')
        seen.add(ident)
        title = source.get('title')
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise DiscoveryError('source title must be short text')
        if source.get('url') != APPROVED_FEEDS[ident]['url']:
            raise DiscoveryError('source URL does not match the approved feed')
        if type(source.get('enabled', False)) is not bool:
            raise DiscoveryError('enabled must be boolean')
        clean.append({'id': ident, 'title': title.strip(), 'url': source['url'],
                      'enabled': source.get('enabled', False), 'source_type': 'article'})
    return clean


def _validate_feed_destination(url, source):
    canonical = canonicalize_url(url)
    if urlsplit(canonical).hostname not in APPROVED_FEEDS[source['id']]['hosts']:
        raise DiscoveryError('feed redirect left the approved publisher host')
    # In a managed proxy environment, the proxy resolves this fixed publisher
    # hostname. Local DNS may intentionally be unavailable. Do not bypass it.
    # NO_PROXY is respected: a direct request still needs public-DNS validation.
    host = urlsplit(canonical).hostname
    if not (getproxies().get('https') and not proxy_bypass(host)):
        validate_public_url(canonical)
    return canonical


class _FeedRedirects(HTTPRedirectHandler):
    def __init__(self, source):
        super().__init__()
        self.source = source

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_feed_destination(newurl, self.source)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_feed(source: dict, timeout=12, max_bytes=MAX_BYTES) -> bytes:
    if not isinstance(source, dict) or not isinstance(source.get('id'), str) or source.get('id') not in APPROVED_FEEDS or source.get('url') != APPROVED_FEEDS[source['id']]['url']:
        raise DiscoveryError('only registered feed URLs can be requested')
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 30:
        raise DiscoveryError('timeout must be in (0, 30] seconds')
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES:
        raise DiscoveryError('max_bytes must be between 1 and 2 MiB')
    url = _validate_feed_destination(source['url'], source)
    # build_opener adds default ProxyHandler; do not disable inherited proxies.
    opener = build_opener(_FeedRedirects(source))
    req = Request(url, headers={'User-Agent': 'MohAI-Discovery/0.1',
                               'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml'})
    started = time.monotonic()
    try:
        with opener.open(req, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {'text/xml', 'application/xml', 'application/rss+xml', 'application/atom+xml'}:
                raise DiscoveryError('publisher did not return an XML feed')
            _validate_feed_destination(response.geturl(), source)
            chunks = []
            size = 0
            while True:
                if time.monotonic() - started > timeout:
                    raise DiscoveryError('feed read exceeded time budget')
                block = response.read(min(65536, max_bytes + 1 - size))
                if not block:
                    break
                chunks.append(block)
                size += len(block)
                if size > max_bytes:
                    raise DiscoveryError('feed exceeds size limit')
            return b''.join(chunks)
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        raise DiscoveryError(f'feed unavailable: {type(exc).__name__}') from exc


def _local(tag):
    return tag.rsplit('}', 1)[-1]


def _child(node, names):
    for child in node:
        if _local(child.tag) in names:
            return ''.join(child.itertext()).strip()
    return ''


def parse_feed(payload: bytes | str, source: dict, *, now=None) -> list[dict]:
    retrieved = _now(now).isoformat()
    if not isinstance(payload, (str, bytes)):
        raise DiscoveryError('feed must be XML text or bytes')
    raw = payload.encode('utf-8') if isinstance(payload, str) else payload
    if len(raw) > MAX_BYTES:
        raise DiscoveryError('feed exceeds size limit')
    try:
        xml_text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise DiscoveryError('feed must use UTF-8') from exc
    if '\x00' in xml_text or re.search(r'<!\s*(?:DOCTYPE|ENTITY)', xml_text, re.I):
        raise DiscoveryError('DTD/entities and non-UTF-8 XML are not allowed')
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise DiscoveryError('invalid XML feed') from exc
    kind = _local(root.tag)
    if kind == 'rss':
        channel = next((c for c in root if _local(c.tag) == 'channel'), None)
        entries = [] if channel is None else [c for c in channel if _local(c.tag) == 'item']
    elif kind == 'feed':
        entries = [c for c in root if _local(c.tag) == 'entry']
    else:
        raise DiscoveryError('only RSS 2.0 and Atom feeds are supported')
    output = []
    for entry in entries[:MAX_ENTRIES]:
        title = plain_text(_child(entry, {'title'}))[:240]
        link = _child(entry, {'link'}) if kind == 'rss' else ''
        if kind == 'feed':
            link = next((c.attrib.get('href', '') for c in entry
                         if _local(c.tag) == 'link' and c.attrib.get('rel', 'alternate') == 'alternate'), '')
        if not title or not link:
            continue
        try:
            link = normalize_url(link)
        except (SourceError, ValueError):
            continue
        if urlsplit(link).hostname not in APPROVED_FEEDS[source['id']]['article_hosts']:
            continue
        raw_date = _child(entry, {'pubDate', 'published', 'date'})
        # Atom updated is not substituted for publication when published is absent.
        published = publication_date(raw_date)
        description = plain_text(_child(entry, {'description', 'summary'}))
        available_words = max(0, 25 - len(title.split()))
        summary = ' '.join(description.split()[:min(12, available_words)])
        output.append({'id': hashlib.sha256(link.encode()).hexdigest()[:24],
            'title': title, 'url': link, 'published_at': published,
            'publication_raw': raw_date or None, 'retrieved_at': retrieved,
            'source_id': source['id'], 'source_name': source['title'],
            'source_type': source.get('source_type', 'article'),
            'analysis_basis': 'metadata_only', 'content_status': 'needs_transcript' if source.get('source_type') == 'video' else 'needs_full_text',
            'summary': summary, 'verification_status': 'not_verified',
            'score': 0, 'score_reason': [], 'suggested_angle': '', 'suggested_format': 'post_image'})
    return output


def _angle(title):
    value = title.lower()
    if 'prompt' in value:
        return 'בקשה לפני ואחרי: איזה פרט קטן כדאי להוסיף לבקשה ל־AI?'
    if any(x in value for x in ('document', 'pdf', 'extractor')):
        return 'מה אפשר ללמוד על חילוץ מידע ממסמכים, ומה חייבים לבדוק בעצמנו?'
    if any(x in value for x in ('deck', 'slides', 'presentation')):
        return 'איך AI יכול לעזור בהכנת מצגת, ומה עדיין צריך לערוך ידנית?'
    if 'agent' in value:
        return 'מהו סוכן AI, ואיפה נגמרת האוטומציה ומתחילה הבקרה שלנו?'
    return 'מה הוכרז, למי זה עשוי לעזור ומה צריך לבדוק לפני שמנסים?'


def rank_items(items: list[dict], *, now=None, limit=5) -> tuple[list[dict], dict]:
    if type(limit) is not int or not 1 <= limit <= 5:
        raise DiscoveryError('limit must be an integer from 1 to 5')
    current = _now(now)
    counts = {'input_items': len(items), 'duplicate_items': 0, 'unknown_dates': 0,
              'future_dates': 0, 'older_than_72h': 0, 'low_relevance': 0}
    seen_url, seen_title = set(), set()
    ranked = []
    for raw in items:
        item = dict(raw)
        key = normalize_url(item['url'])
        title_key = re.sub(r'\W+', '', item['title'].casefold())
        if key in seen_url or title_key in seen_title:
            counts['duplicate_items'] += 1
            continue
        seen_url.add(key); seen_title.add(title_key)
        publication = publication_date(item.get('published_at'))
        if publication is None:
            counts['unknown_dates'] += 1
            continue
        published = datetime.fromisoformat(publication)
        age = (current - published).total_seconds() / 3600
        if age < 0:
            counts['future_dates'] += 1
            continue
        if age > 72:
            counts['older_than_72h'] += 1
            continue
        text = ' ' + item['title'].lower() + ' ' + item.get('summary', '').lower() + ' '
        ai = sum(term in text for term in AI_TERMS)
        practical = sum(term in text for term in PRACTICAL)
        advanced = sum(term in text for term in ADVANCED)
        score = min(35, ai * 12) + min(30, practical * 10) + (20 if age <= 24 else 12) + 10 - advanced * 15
        if ai == 0 or score < 35:
            counts['low_relevance'] += 1
            continue
        item.update(url=key, published_at=publication, score=max(0, min(100, score)),
                    score_reason=['מטא־דאטה ממקור שנבחר מראש',
                                  'פורסם ב־24 השעות האחרונות' if age <= 24 else 'הרחבת חלון עד 72 שעות',
                                  'התאמה למילות מפתח של AI ושימוש מעשי — דירוג כללים, לא ניתוח הכתבה'],
                    suggested_angle=_angle(item['title']), age_hours=round(age, 2))
        if advanced:
            item['score_reason'].append('הופחת דירוג בגלל מונחים טכניים או עסקיים מתקדמים')
        ranked.append(item)
    ranked.sort(key=lambda x: (x['age_hours'] > 24, -x['score'], x['age_hours'], x['url']))
    chosen, per_source = [], {}
    for item in ranked:
        if per_source.get(item['source_id'], 0) >= 2:
            continue
        chosen.append(item)
        per_source[item['source_id']] = per_source.get(item['source_id'], 0) + 1
        if len(chosen) == limit:
            break
    counts['selected_items'] = len(chosen)
    return chosen, counts


def collect(sources: list[dict], *, now=None, fetcher=fetch_feed, limit=5) -> dict:
    current = _now(now)
    all_items, results = [], []
    for source in sources:
        if not source.get('enabled', False):
            results.append({'id': source['id'], 'title': source['title'], 'url': source['url'], 'status': 'disabled'})
            continue
        result = {'id': source['id'], 'title': source['title'], 'url': source['url']}
        try:
            payload = fetcher(source)
            items = parse_feed(payload, source, now=current)
            all_items.extend(items)
            result.update(status='collected', parsed_items=len(items),
                          content_sha256=hashlib.sha256(payload.encode() if isinstance(payload, str) else payload).hexdigest())
        except (DiscoveryError, SourceError, OSError, ValueError) as exc:
            result.update(status='failed', error=str(exc))
        results.append(result)
    selected, counts = rank_items(all_items, now=current, limit=limit)
    successful = sum(s['status'] == 'collected' for s in results)
    return {'schema_version': 1, 'generated_at': current.isoformat(), 'collection_method': 'rss',
            'status': 'collected' if successful and all(s['status'] in {'collected','disabled'} for s in results)
                      else 'partial' if successful else 'failed',
            'sources': results, 'items': selected, 'counts': counts,
            'generation_status': 'awaiting_full_source_and_model',
            'limitations': ['כותרות ותקצירי RSS בלבד; לא נקרא גוף הכתבות ולא נצפה וידאו.',
                           'הזוויות הן הצעות כללים לעריכה, לא טיוטות שנוצרו ב־AI ולא אימות טענות.',
                           'תאריכים חסרים אינם מוחלפים בתאריך האיסוף.',
                           'מקורות ללא פריטים מתאימים עשויים להחזיר אפס הצעות.']}


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Collect public RSS/Atom metadata without an AI API')
    default = Path(__file__).resolve().parents[2] / 'config/discovery_sources.json'
    parser.add_argument('--sources', type=Path, default=default)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--now', help='Optional explicit timezone-aware timestamp for reproducible offline tests')
    args = parser.parse_args(argv)
    try:
        now = datetime.fromisoformat(args.now.replace('Z', '+00:00')) if args.now else None
        report = collect(load_sources(args.sources), now=now, limit=args.limit)
        write_report(args.output, report)
        print(json.dumps({'status': report['status'], 'items': len(report['items']),
                          'output': str(args.output), 'collection_method': 'rss'}, ensure_ascii=False))
        return 0 if report['status'] in {'collected', 'partial'} else 1
    except (DiscoveryError, OSError, ValueError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
