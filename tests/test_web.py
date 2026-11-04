import http.client
import json
from pathlib import Path
import queue
import re
import tempfile
import threading
import unittest
from unittest.mock import patch

from moh_ai.web import serve


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();ready=queue.Queue()
        cls.thread=threading.Thread(target=serve,args=(Path(cls.temp.name)/'web.db',0,ready.put),daemon=True)
        cls.thread.start();cls.server=ready.get(timeout=5)
        cls.port=cls.server.server_port;cls.origin=f'http://127.0.0.1:{cls.port}'
        status,html=cls.request('GET','/')
        assert status==200
        cls.token=re.search(r'name="csrf-token" content="([^"]+)"',html.decode()).group(1)
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.thread.join(timeout=5);cls.temp.cleanup()
    @classmethod
    def request(cls,method,path,body=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',cls.port,timeout=5)
        conn.request(method,path,body=body,headers=headers or {})
        response=conn.getresponse();data=response.read();status=response.status;conn.close()
        return status,data
    def post(self,payload,headers=None,path='/api/parse'):
        h={'Origin':self.origin,'X-CSRF-Token':self.token,'Content-Type':'application/json'}
        if headers:h.update(headers)
        return self.request('POST',path,json.dumps(payload).encode(),h)
    def test_invalid_host_blocks_reads_and_writes(self):
        self.assertEqual(self.request('GET','/api/workspace',headers={'Host':'attacker.example'})[0],403)
        self.assertEqual(self.post({'content':'x'},{'Host':'attacker.example'})[0],403)
    def test_origin_and_token_required(self):
        for headers in ({'Origin':'https://evil.example'},{'X-CSRF-Token':'bad'},{'Origin':''}):
            with self.subTest(headers=headers):self.assertEqual(self.post({'content':'x'},headers)[0],403)
        self.assertEqual(self.request('POST','/api/parse','{}',{'Content-Type':'application/json'})[0],403)
    def test_same_origin_parse_and_workspace(self):
        status,body=self.post({'content':'שלום','kind':'text','title':'בדיקה'})
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['content'],'שלום')
        status,body=self.request('GET','/api/workspace')
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['generation_status'],'awaiting_model')
    def test_bad_json_nonobject_and_wrong_content_type(self):
        headers={'Origin':self.origin,'X-CSRF-Token':self.token,'Content-Type':'application/json'}
        self.assertEqual(self.request('POST','/api/parse','{bad',headers)[0],400)
        self.assertEqual(self.post([])[0],400)
        self.assertEqual(self.post({}, {'Content-Type':'text/plain'})[0],400)
    def test_bad_input_is_explicit_and_no_path_traversal(self):
        self.assertEqual(self.post({'content':{},'kind':'text'})[0],400)
        self.assertEqual(self.post({'content':'text','title':{}})[0],400)
        self.assertEqual(self.request('GET','/../../etc/passwd')[0],404)
        self.assertEqual(self.request('GET','/api/export?id=not-found&final=true')[0],400)

    def test_daily_registry_run_and_evidence_http(self):
        from moh_ai.sources import parse_source
        config={'name':'Synthetic HTTP integration','url':'https://example.org/article','kind':'link','enabled':True,'authority':'primary'}
        self.assertEqual(self.post({'sources':[config]}, {'Origin':'https://evil.example'},path='/api/daily/sources')[0],403)
        self.assertEqual(self.post({'sources':[config]},path='/api/daily/sources')[0],200)
        record=parse_source('AI prompt tutorial for a useful tool. '*6,'article',title='AI tutorial',url=config['url'])
        with patch('moh_ai.daily.fetch_source',return_value=record):
            status,body=self.post({},path='/api/daily/run')
        self.assertEqual(status,200)
        report=json.loads(body);self.assertEqual(len(report['ideas']),1)
        status,body=self.request('GET','/api/daily')
        self.assertEqual(status,200);ws=json.loads(body)
        self.assertEqual(ws['latest']['id'],report['id']);self.assertEqual(ws['scheduler'],'disabled')
        self.assertEqual(self.post({'force':'yes'},path='/api/daily/run')[0],400)
        status,html=self.request('GET','/')
        self.assertIn('id="daily-run"',html.decode())
        self.assertEqual(self.post({'sources':[]},path='/api/daily/sources')[0],200)

if __name__=='__main__':unittest.main()
