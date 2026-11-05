"""Loopback-only editorial workbench, with same-origin writes and no hosted AI calls."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
from urllib.parse import parse_qs,urlsplit

from . import editorial
from .sources import parse_source,fetch_source,SourceError
from . import discovery

STATIC=Path(__file__).parent/'static'


def serve(db,port=8765,ready_callback=None,local_model=None):
    from . import production
    production.configure_model(local_model)
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
                if path.path=='/api/local-model':return self.send(200,production.local_model_status())
                if path.path.startswith('/api/production-runs/'):
                    tail=path.path.removeprefix('/api/production-runs/')
                    if tail.endswith('/visual.svg'):
                        ident=tail[:-len('/visual.svg')]
                        if len(ident)!=24 or any(ch not in '0123456789abcdef' for ch in ident):return self.send(404,{'error':'not found'})
                        return self.send(200,production.visual_asset(db,ident),'image/svg+xml; charset=utf-8')
                    ident=tail
                    if len(ident)!=24 or any(ch not in '0123456789abcdef' for ch in ident):return self.send(404,{'error':'not found'})
                    return self.send(200,production.get_run(db,ident))
                if path.path=='/api/production-export':
                    q=parse_qs(path.query);ident=q.get('id',[''])[0]
                    data=production.export_run(db,ident,final=q.get('final',['false'])[0]=='true')
                    return self.send(200,data,'application/zip',{'Content-Disposition':'attachment; filename="moh-ai-production.zip"'})
                if path.path=='/api/discovery':
                    report_path=Path(__file__).resolve().parents[2]/'research'/'discovery'/'latest-report.json'
                    return self.send(200,discovery.read_report(report_path))
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
                elif self.path=='/api/discovery/read':
                    report_path=Path(__file__).resolve().parents[2]/'research'/'discovery'/'latest-report.json'
                    report=discovery.read_report(report_path)
                    item=next((x for x in report['items'] if x.get('id')==payload.get('id')),None)
                    if item is None:raise ValueError('candidate is not in the current discovery report')
                    result=fetch_source(item['url'])
                    if result.get('analysis_basis')!='full_text' or len(result.get('content',''))<500:
                        raise ValueError('full article text was not available; use the source link or paste accessible text')
                    result['title']=result.get('title') or item['title']
                    result['published_at']=item.get('published_at')
                    result['discovery_id']=item['id']
                    result['angles']=discovery.propose_angles(result)
                elif self.path=='/api/packs':result=editorial.save_pack(db,payload)
                elif self.path=='/api/brief':result=editorial.make_brief(db,payload.get('source_id'))
                elif self.path=='/api/drafts':result=editorial.save_draft(db,payload.get('source_id'),payload.get('body'),payload.get('claim_map'),origin='user_edit')
                elif self.path=='/api/reviews':result=editorial.record_review(db,payload.get('draft_id'),payload.get('report'))
                elif self.path=='/api/approve':result=editorial.approve(db,payload.get('draft_id'),payload.get('actor'))
                elif self.path=='/api/production-runs':result=production.start_run(db,payload.get('source_id'))
                elif self.path.startswith('/api/production-runs/') and self.path.endswith('/approve'):
                    ident=self.path.removeprefix('/api/production-runs/')[:-len('/approve')].strip('/')
                    if len(ident)!=24 or any(ch not in '0123456789abcdef' for ch in ident):return self.send(404,{'error':'not found'})
                    if payload.get('checked') is not True:raise ValueError('סמנו שקראתם את הפוסט ובדקתם את תמונת הטיוטה.')
                    result=production.approve_run(db,ident,payload.get('actor'))
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
