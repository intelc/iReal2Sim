import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from pipeline.agent_workflow import (command,config,doctor,init,redact,run,settings,sha,source_stamp)


WORKER = '''import argparse,base64,hashlib,json,os,struct
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--request');p.add_argument('--output');p.add_argument('--bad-context',action='store_true');a=p.parse_args()
out=Path(a.output);req=json.loads(Path(a.request).read_text())
print(os.environ.get('FAL_KEY','no secret'))
if os.environ.get('FAIL_WORKER'):raise SystemExit(3)
enc=lambda code,values:base64.b64encode(struct.pack('<'+code*len(values),*values)).decode()
track={'name':'right_hand','color':[.8,.6,.4],'indices':enc('I',[0,1,2]),'indexCount':3,'frames':{'1':{'positions':enc('f',[0,0,1,.1,0,1,0,.1,1]),'quality':{'display_support_gate':{'passed':True}}}}}
data={'schema':'iphone-rsa.viewer-tracks/1','tracks':[track]};path=out/'tracks.json';path.write_text(json.dumps(data))
result={'schema':'ireal2sim.worker-result/1','stage':req['stage'],'status':'complete','source_manifest_sha256':req['context']['source_manifest_sha256'],'context_sha256':'wrong'if a.bad_context else req['context_sha256'],'world_frame':req['context']['world_frame'],'artifacts':[{'path':'tracks.json','role':'interaction_tracks','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]}
(out/'result.json').write_text(json.dumps(result))
'''


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        self.source=self.root/'source';self.source.mkdir();(self.source/'manifest.json').write_text(json.dumps({'schema':'iphone-rsa.session/1','status':'complete'}))
        self.workspace=self.root/'workflow';self.worker=self.root/'worker.py';self.worker.write_text(WORKER)

    def fake_scan(self,source,output,**kwargs):
        output.mkdir(parents=True)
        for folder in ['export','mapping/mapping','map']:(output/folder).mkdir(parents=True)
        (output/'run.json').write_text(json.dumps({'status':'complete','source_manifest_sha256':sha(source/'manifest.json')}))
        (output/'export/calibration.json').write_text('{}');(output/'mapping/mapping/optimized.tsv').write_text('fixture trajectory')
        (output/'map/scene.json').write_text(json.dumps({'timeline':{'frames':[{'id':1,'seconds':0,'pts':0}]}}))
        from pipeline import release
        (output/'map/viewer.html').write_text(Path(release.__file__).with_name('viewer.html').read_text())
        (output/'index.html').write_text('fixture scan')

    def worker_config(self,*extra):
        return {'backends':{'hands':{'argv':[sys.executable,str(self.worker),*extra],'requires_env':['FAL_KEY']}}}

    def test_doctor_does_not_reveal_keys_or_request_unused_services(self):
        secret='unit-do-not-print-key';result=doctor('full',{}, {'OPENAI_API_KEY':secret,'FAL_KEY':secret})
        self.assertNotIn(secret,json.dumps(result));self.assertTrue(result['credentials']['OPENAI_API_KEY']['configured'])
        self.assertFalse(result['credentials']['OPENAI_API_KEY']['verified'])
        self.assertIn('not built into',result['stages']['hands']['needs'][0]);self.assertNotIn('FAL_KEY',result['stages']['models']['needs'][0])

    def test_dotenv_parsing_never_executes_shell_and_environment_wins(self):
        path=self.root/'env';sentinel=self.root/'executed';path.write_text(f'OPENAI_API_KEY=file-value\nFAL_API_KEY="alias-value"\nIGNORED=$(touch {sentinel})\n')
        with patch.dict(os.environ,{'OPENAI_API_KEY':'process-value'},clear=True):values=settings(path)
        self.assertEqual(values['OPENAI_API_KEY'],'process-value');self.assertEqual(values['FAL_KEY'],'alias-value');self.assertFalse(sentinel.exists())

    def test_secret_arguments_and_configured_secret_values_are_rejected(self):
        for argv in [['python','--api-key','abc'],['python','unit-secret']]:
            with self.assertRaises(ValueError):command(argv,{'OPENAI_API_KEY':'unit-secret'})
        path=self.root/'config.json';path.write_text('{"OPENAI_API_KEY":"forbidden"}')
        with self.assertRaises(ValueError):config(path)

    def test_custom_worker_credentials_are_redacted_too(self):
        values={'CUSTOM_WORKER_PASSWORD':'private-worker-value'}
        self.assertEqual(redact('Error: private-worker-value',values),'Error: [redacted]')
        with self.assertRaises(ValueError):command(['python','private-worker-value'],values)

    def test_missing_optional_stage_does_not_stop_scan_and_resume_reuses_it(self):
        init(self.source,self.workspace,'full')
        with patch('pipeline.release.run',side_effect=self.fake_scan)as scan:
            first=run(self.workspace,{},{});second=run(self.workspace,{}, {})
        self.assertEqual(scan.call_count,1);self.assertEqual(second['status'],'needs_setup');self.assertEqual(first['stages']['scan']['status'],'complete')
        self.assertEqual(second['stages']['review']['status'],'needs_setup');self.assertTrue((self.workspace/second['viewer_path']/'viewer.html').is_file())

    def test_changed_source_is_rejected_before_reusing_cache(self):
        init(self.source,self.workspace);(self.source/'new-file').write_text('new input')
        with self.assertRaisesRegex(ValueError,'Source recording changed'):run(self.workspace,{}, {})

    def test_corrupted_cached_output_creates_new_attempt_and_keeps_old(self):
        init(self.source,self.workspace)
        with patch('pipeline.release.run',side_effect=self.fake_scan)as scan:
            first=run(self.workspace,{},{});old=self.workspace/first['stages']['scan']['output'];(old/'map/scene.json').write_text('{}')
            second=run(self.workspace,{}, {})
        self.assertEqual(scan.call_count,2);self.assertEqual(len(second['stages']['scan']['attempts']),2);self.assertTrue(old.exists())

    def test_worker_failure_preserves_attempt_and_secret_redaction_then_resumes(self):
        init(self.source,self.workspace,'full');cfg=self.worker_config();secret='unit-worker-secret'
        with patch('pipeline.release.run',side_effect=self.fake_scan)as scan:
            failed=run(self.workspace,cfg,{'FAL_KEY':secret,'FAIL_WORKER':'1'})
            resumed=run(self.workspace,cfg,{'FAL_KEY':secret})
        self.assertEqual(scan.call_count,1);self.assertEqual(failed['stages']['hands']['status'],'failed');self.assertEqual(resumed['stages']['hands']['status'],'complete')
        self.assertEqual(len(resumed['stages']['hands']['attempts']),2)
        for p in self.workspace.rglob('*'):
            if p.is_file():self.assertNotIn(secret.encode(),p.read_bytes())
        page=(self.workspace/resumed['viewer_path']/'viewer.html').read_text();self.assertIn('frame.quality?.display_support_gate?.passed!==true',page)

    def test_worker_with_wrong_pose_context_is_failed_not_shown(self):
        init(self.source,self.workspace,'full')
        with patch('pipeline.release.run',side_effect=self.fake_scan):state=run(self.workspace,self.worker_config('--bad-context'),{'FAL_KEY':'unit-worker-secret'})
        self.assertEqual(state['stages']['hands']['status'],'failed')
        self.assertFalse((self.workspace/state['viewer_path']/'interaction-tracks.json').exists())

    def test_concurrent_execution_cannot_duplicate_a_paid_stage(self):
        init(self.source,self.workspace)
        with(self.workspace/'.workflow.lock').open('a')as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError,'already running'):run(self.workspace,{}, {})
