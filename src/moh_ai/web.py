"""Loopback-only editorial workbench, with same-origin writes and no hosted AI calls."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
from urllib.parse import parse_qs,urlsplit

from . import editorial
from .sources import parse_source,fetch_source,SourceError

STATIC=Path(__file__).parent/'static'


def serve(db,port=8765,ready_callback=None):
    token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,body,kind='application/json; charset=utf-8',headers=None):
            if isinstance(body,(dict,list)):body=json.dumps(body,ensure_ascii=False).encode()
            elif isinstance(body,str):body=body.encode()
            self.send_response(status)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if headers:
                for key,value in headers.items():self.send_header(key,value)
            self.end_headers();self.wfile.write(body)
        def valid_host(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        def do_GET(self):
            if not self.valid_host():return self.send(403,{'error':'invalid host'})
            path=urlsplit(self.path)
            try:
                if path.path=='/':
                    html=(STATIC/'index.html').read_text().replace('CSRF_TOKEN',token)
                    return self.send(200,html,'text/html; charset=utf-8')
                if path.path in {'/app.js','/style.css'}:
                    kind='text/javascript; charset=utf-8' if path.path.endswith('.js') else 'text/css; charset=utf-8'
                    return self.send(200,(STATIC/path.path[1:]).read_bytes(),kind)
                if path.path=='/api/workspace':return self.send(200,editorial.list_workspace(db))
                if path.path=='/api/export':
                    q=parse_qs(path.query);ident=q.get('id',[''])[0]
                    data=editorial.export_bundle(db,ident,final=q.get('final',['false'])[0]=='true')
                    return self.send(200,data,'application/zip',{'Content-Disposition':'attachment; filename="moh-ai-post.zip"'})
                return self.send(404,{'error':'not found'})
            except (ValueError,OSError) as e:return self.send(400,{'error':str(e)})
        def do_POST(self):
            if not self.valid_host():return self.send(403,{'error':'invalid host'})
            expected={f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}
            if self.headers.get('Origin') not in expected or self.headers.get('X-CSRF-Token')!=token:
                return self.send(403,{'error':'same-origin token required'})
            try:
                if self.headers.get('Transfer-Encoding'):raise ValueError('chunked body is unsupported')
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=600_000:raise ValueError('request body missing or too large')
                if self.headers.get_content_type()!='application/json':raise ValueError('JSON content type required')
                payload=json.loads(self.rfile.read(size))
                if not isinstance(payload,dict):raise ValueError('request must be object')
                if self.path=='/api/parse':
                    result=parse_source(payload.get('content',''),payload.get('kind','text'),
                        title=payload.get('title',''),url=payload.get('url') or None)
                elif self.path=='/api/fetch':result=fetch_source(payload.get('url',''))
                elif self.path=='/api/packs':result=editorial.save_pack(db,payload)
                elif self.path=='/api/brief':result=editorial.make_brief(db,payload.get('source_id'))
                elif self.path=='/api/drafts':result=editorial.save_draft(db,payload.get('source_id'),payload.get('body'),payload.get('claim_map'),origin='user_edit')
                elif self.path=='/api/reviews':result=editorial.record_review(db,payload.get('draft_id'),payload.get('report'))
                elif self.path=='/api/approve':result=editorial.approve(db,payload.get('draft_id'),payload.get('actor'))
                elif self.path=='/api/pilot':
                    from .pilot import load_pilot
                    result=load_pilot(db)
                else:return self.send(404,{'error':'not found'})
                return self.send(200,result)
            except (ValueError,OSError,SourceError,TypeError,KeyError) as e:
                return self.send(400,{'error':str(e)})
            except Exception:
                return self.send(500,{'error':'פעולה נכשלה; הנתונים לא פורסמו. בדקו את תיק הנתונים והקלט.'})
    httpd=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    if ready_callback:
        ready_callback(httpd)
    else:print(f'מוח AI: http://127.0.0.1:{httpd.server_port} — מצב כתיבה ידני, ללא קריאות מודל',flush=True)
    try:httpd.serve_forever()
    finally:httpd.server_close()
