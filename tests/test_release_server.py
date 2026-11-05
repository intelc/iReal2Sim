from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from pipeline.release_server import ReleaseServer


class SetupServerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.workspace=self.root/'jobs'
        self.server=ReleaseServer(self.workspace,0);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.port=self.server.server_port

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def request(self,path='/',method='GET',value=None,headers=None):
        connection=HTTPConnection('127.0.0.1',self.port);body=json.dumps(value)if value is not None else None
        connection.request(method,path,body,headers or {});r=connection.getresponse();result=(r.status,r.read());connection.close();return result

    def test_setup_loads_and_token_is_not_an_api_key(self):
        status,body=self.request();self.assertEqual(status,200);self.assertIn(self.server.token.encode(),body);self.assertNotIn(b'__SESSION_TOKEN__',body)
        status,body=self.request('/api/status');self.assertEqual(status,200);self.assertEqual(json.loads(body)['jobs'],[])

    def test_cross_origin_and_missing_token_cannot_launch_jobs(self):
        for headers in [{},{'Origin':'https://example.com','X-Scene-Token':self.server.token},{'Origin':f'http://127.0.0.1:{self.port}'}]:
            self.assertEqual(self.request('/api/jobs','POST',{},headers)[0],403)
        self.assertEqual(self.request('/',headers={'Host':'attacker.example'})[0],403)

    def test_generated_workspace_cannot_expose_parent_or_symlinked_secrets(self):
        secret=self.root/'.env';secret.write_text('private-test-value');(self.workspace/'secret.txt').symlink_to(secret)
        for path in ['/../.env','/%2e%2e/.env','/secret.txt','/.env']:
            status,body=self.request(path);self.assertNotEqual(status,200);self.assertNotIn(b'private-test-value',body)

    def test_key_is_passed_only_in_environment_not_saved_or_in_command(self):
        source=self.root/'capture';source.mkdir();(source/'manifest.json').write_text('{}');key='test-key-never-persist'
        class Child:
            def poll(self):return None
            def wait(self):return 1
        with patch('pipeline.release_server.doctor',return_value={'ready':True}),patch('pipeline.release_server.subprocess.Popen',return_value=Child())as popen:
            status,body=self.request('/api/jobs','POST',{'session':str(source),'api_key':key,'review':True},headers={'Origin':f'http://127.0.0.1:{self.port}','X-Scene-Token':self.server.token})
            self.assertEqual(status,202);self.assertNotIn(key.encode(),body)
            args,kwargs=popen.call_args;self.assertNotIn(key,' '.join(args[0]));self.assertEqual(kwargs['env']['OPENAI_API_KEY'],key)
        for path in self.workspace.rglob('*'):
            if path.is_file():self.assertNotIn(key.encode(),path.read_bytes())
