"""Evidence-preserving source intake. Never executes source text or infers video content."""
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit, urljoin
import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import time

MAX_BYTES = 2 * 1024 * 1024


class SourceError(ValueError):
    """Explicit intake failure that callers can display without inventing content."""


class _ArticleParser(HTMLParser):
    SKIP = {'script', 'style', 'nav', 'footer', 'header', 'noscript', 'svg', 'aside', 'form'}
    BLOCKS = {'p', 'div', 'br', 'article', 'section', 'li', 'h1', 'h2', 'h3'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = []
        self.in_title = False
        self.in_article = False
        self.saw_article = False
        self.title_parts = []
        self.body_parts = []
        self.article_parts = []
        self.published = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.skip:
            if tag in self.SKIP:
                self.skip.append(tag)
            return
        if tag in self.SKIP:
            self.skip.append(tag)
            return
        if tag == 'meta':
            key = attrs.get('property', attrs.get('name', '')).lower()
            if key in {'article:published_time', 'datepublished', 'date'}:
                self.published = attrs.get('content') or self.published
        if tag == 'title':
            self.in_title = True
        if tag == 'article':
            self.in_article = True
            self.saw_article = True
        if tag in self.BLOCKS:
            self.body_parts.append('\n')
            if self.in_article:
                self.article_parts.append('\n')

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
            return
        if tag == 'title':
            self.in_title = False
        if tag in self.BLOCKS:
            self.body_parts.append('\n')
            if self.in_article:
                self.article_parts.append('\n')
        if tag == 'article':
            self.in_article = False

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title_parts.append(data)
            return
        self.body_parts.append(data)
        if self.in_article:
            self.article_parts.append(data)


def _clean(text):
    return '\n'.join(line for line in (re.sub(r'[ \t\r\f\v]+', ' ', x).strip()
                                      for x in text.split('\n')) if line)


def canonicalize_url(url):
    """Syntax validation only; no DNS request and no claim of network safety."""
    if not isinstance(url, str) or any(ord(c) < 33 for c in url) or '\\' in url:
        raise SourceError('URL must be a whitespace-free public HTTPS URL')
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise SourceError('Invalid URL') from exc
    if (parts.scheme.lower() != 'https' or not parts.hostname or parts.username is not None
            or parts.password is not None or port not in {None, 443}):
        raise SourceError('Only HTTPS URLs without credentials on port 443 are allowed')
    try:
        hostname = parts.hostname.encode('idna').decode('ascii').lower().rstrip('.')
    except UnicodeError as exc:
        raise SourceError('Invalid hostname') from exc
    if not hostname or '%' in hostname:
        raise SourceError('Invalid hostname')
    host = '[' + hostname + ']' if ':' in hostname else hostname
    return urlunsplit(('https', host, parts.path or '/', parts.query, ''))


def validate_public_url(url):
    """Resolve once and reject the entire set if any answer is not public."""
    canonical = canonicalize_url(url)
    host = urlsplit(canonical).hostname
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SourceError('Cannot resolve source host') from exc
    addresses = []
    for family, socktype, proto, _, address in records:
        ip = ipaddress.ip_address(address[0])
        if not ip.is_global or ip.is_multicast:
            raise SourceError('Source resolves to a non-public address')
        if family not in {socket.AF_INET, socket.AF_INET6}:
            raise SourceError('Unsupported address family')
        if (family, socktype, proto, address) not in addresses:
            addresses.append((family, socktype, proto, address))
    if not addresses:
        raise SourceError('Source host has no public addresses')
    return canonical, host, addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a validated numeric address; preserve host for SNI/certificate checks."""
    def __init__(self, host, address, timeout):
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self._pinned_address = address

    def connect(self):
        family, socktype, proto, sockaddr = self._pinned_address
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(self.timeout)
            sock.connect(sockaddr)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def parse_source(text, kind, title='', url=None, published_at=None):
    """Normalize supplied text/HTML/transcript, keeping provenance and timestamps.

    Kinds: text/article/html/transcript/vtt/srt/description. Dates are not invented.
    HTML extraction is heuristic, not a paywall or login bypass. A caller must
    not promote this record to an independently verified claim automatically.
    """
    if not isinstance(title, str):
        raise SourceError('Source title must be text')
    if url is not None and not isinstance(url, str):
        raise SourceError('Source URL must be text or null')
    if published_at is not None and not isinstance(published_at, str):
        raise SourceError('Publication date must be text or null')
    if not isinstance(kind, str):
        raise SourceError('Source kind must be text')
    if kind not in {'text', 'article', 'html', 'transcript', 'vtt', 'srt', 'description'}:
        raise SourceError('Unsupported source kind')
    if not isinstance(text, str):
        raise SourceError('Source content must be text')
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise SourceError('Source content exceeds 2 MiB')
    original_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
    canonical = canonicalize_url(url) if url else None
    segments = []
    detected_title = ''
    basis = 'user_supplied'
    if kind == 'html':
        parser = _ArticleParser()
        parser.feed(text)
        detected_title = _clean(''.join(parser.title_parts))
        text = _clean(''.join(parser.article_parts if parser.saw_article else parser.body_parts))
        published_at = published_at or parser.published
        basis = 'full_text'
    elif kind in {'transcript', 'vtt', 'srt'}:
        basis = 'transcript'
        cue_time = None
        cue_lines = []
        metadata_block = False
        def flush():
            if cue_lines:
                segments.append({'timestamp': cue_time, 'text': _clean(' '.join(cue_lines))})
                cue_lines.clear()
        for line in text.splitlines():
            line = line.strip()
            if not line:
                metadata_block = False
            if metadata_block:
                continue
            if '-->' in line:
                flush()
                cue_time = line.split('-->', 1)[0].strip()
            elif not line:
                flush()
                cue_time = None
            elif line == 'WEBVTT' or line.isdigit():
                continue
            elif line.startswith(('NOTE', 'STYLE', 'REGION')):
                metadata_block = True
                continue
            else:
                # Caption markup is text, never executable.
                cue_lines.append(re.sub(r'<[^>]*>', '', line))
        flush()
        text = '\n'.join(x['text'] for x in segments)
    elif kind == 'article':
        basis = 'full_text'
    elif kind == 'description':
        basis = 'description_only'
    # A video landing page is not the video's spoken or visual content.
    host = urlsplit(canonical).hostname if canonical else ''
    landing_host = any(host == domain or host.endswith('.' + domain) for domain in
                       ('youtube.com', 'youtu.be', 'instagram.com', 'facebook.com', 'tiktok.com',
                        'x.com', 'twitter.com', 'linkedin.com', 'vimeo.com'))
    if kind == 'html' and landing_host:
        basis = 'description_only'
    content = _clean(text)
    status = 'accessible' if content else ('needs_transcript' if basis == 'transcript' else 'blocked')
    if basis == 'description_only' and content:
        status = 'partial'
    content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
    return {'title': title.strip() or detected_title or 'Untitled source',
            'canonical_url': canonical, 'published_at': published_at or None,
            'retrieved_at': datetime.now(timezone.utc).isoformat(),
            'kind': kind, 'content': content, 'content_hash': content_hash,
            'raw_hash': original_hash, 'analysis_basis': basis,
            'access_status': status, 'segments': segments,
            'verification_status': 'unknown',
            'limitations': ['Transcript only; visual content was not inspected.'] if basis == 'transcript'
                          else (['Description alone does not establish full source content.']
                                if basis == 'description_only' else [])}


def fetch_document(url, timeout=10, max_bytes=MAX_BYTES):
    """Fetch a public HTTPS source; redirects/DNS are revalidated and IP-pinned.

    No proxies, credentials, cookies, paid APIs or automatic video downloads.
    Unsupported/binary content and HTTP failures are explicit errors. Time is
    bounded by a monotonic deadline across redirects and body reads, excluding
    OS DNS resolution (stdlib getaddrinfo has no portable hard timeout).
    """
    if not (0 < timeout <= 60) or not (0 < max_bytes <= MAX_BYTES):
        raise SourceError('Invalid fetch limits')
    deadline = time.monotonic() + timeout
    current = url
    for hop in range(4):
        canonical, host, addresses = validate_public_url(current)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceError('Source fetch timed out')
        conn = _PinnedHTTPSConnection(host, addresses[0], remaining)
        try:
            parts = urlsplit(canonical)
            target = parts.path + ('?' + parts.query if parts.query else '')
            conn.request('GET', target, headers={'Host': parts.netloc, 'User-Agent': 'MohAI/0.1 (+public-source-reader)',
                                               'Accept-Encoding': 'identity', 'Accept': 'text/html,text/plain,text/vtt,application/rss+xml,application/atom+xml,application/xml,text/xml'})
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader('Location')
                if not location or hop == 3:
                    raise SourceError('Invalid or excessive source redirects')
                current = urljoin(canonical, location)
                continue
            if response.status != 200:
                raise SourceError('Source returned HTTP ' + str(response.status))
            if response.getheader('Content-Encoding', 'identity').lower() not in {'', 'identity'}:
                raise SourceError('Compressed source responses are not supported')
            mime = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
            if mime not in {'text/html', 'application/xhtml+xml', 'text/plain', 'text/vtt', 'application/x-subrip', 'application/rss+xml', 'application/atom+xml', 'application/xml', 'text/xml'}:
                raise SourceError('Unsupported source content type')
            declared = response.getheader('Content-Length')
            if declared:
                try:
                    if int(declared) < 0 or int(declared) > max_bytes:
                        raise SourceError('Source response exceeds size limit')
                except ValueError as exc:
                    raise SourceError('Invalid source content length') from exc
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SourceError('Source fetch timed out')
                if conn.sock:
                    conn.sock.settimeout(remaining)
                chunk = response.read1(min(65536, max_bytes + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    raise SourceError('Source response exceeds size limit')
            encoding_match = re.search(r'charset=["\']?([\w-]+)', response.getheader('Content-Type', ''), re.I)
            encoding = encoding_match.group(1) if encoding_match else 'utf-8'
            try:
                text = b''.join(chunks).decode(encoding)
            except (UnicodeError, LookupError) as exc:
                raise SourceError('Source response is not decodable text') from exc
            return {'text': text, 'mime': mime, 'canonical_url': canonical, 'requested_url': url}
        except (OSError, http.client.HTTPException) as exc:
            raise SourceError('Source network request failed') from exc
        finally:
            conn.close()
    raise SourceError('Excessive source redirects')


def fetch_source(url, timeout=10, max_bytes=MAX_BYTES):
    """Parse a fetched document as evidence; XML feeds use fetch_document directly."""
    document = fetch_document(url, timeout, max_bytes)
    mime = document['mime']
    if mime in {'application/rss+xml', 'application/atom+xml', 'application/xml', 'text/xml'}:
        raise SourceError('Feed XML is not article evidence; use daily collection')
    kind = ('html' if mime in {'text/html', 'application/xhtml+xml'} else
            'vtt' if mime == 'text/vtt' else 'srt' if mime == 'application/x-subrip' else 'article')
    result = parse_source(document['text'], kind, url=document['canonical_url'])
    result['requested_url'] = url
    return result
