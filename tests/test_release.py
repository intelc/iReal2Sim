import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pipeline.release import check_paths,run,write_dashboard


class ReleaseTests(unittest.TestCase):
    def test_source_and_output_are_never_nested_or_overwritten(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d);s=root/'source';s.mkdir();(s/'manifest.json').write_text('{}')
            for output in [s,s/'derived',root]:
                with self.subTest(output=output),self.assertRaises(ValueError):check_paths(s,output)
            out=root/'results';out.mkdir();(out/'old').write_text('retain')
            with self.assertRaises(ValueError):check_paths(s,out)
            self.assertEqual(check_paths(s,root/'fresh'),(s.resolve(),(root/'fresh').resolve()))

    def test_dashboard_escapes_names_and_never_invents_finished_assets(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d);write_dashboard(root,{'status':'failed','recording_name':'<script>alert(1)</script>','error':'<bad>'})
            page=(root/'index.html').read_text();self.assertNotIn('<script>',page);self.assertIn('&lt;script&gt;',page);self.assertNotIn('<iframe',page)

    def test_dependency_failure_leaves_source_and_destination_intact(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d);s=root/'source';s.mkdir();(s/'manifest.json').write_text('{}')
            with patch('pipeline.release.doctor',return_value={'ready':False,'install':'install tools'}),self.assertRaises(RuntimeError):run(s,root/'result')
            self.assertFalse((root/'result').exists());self.assertEqual((s/'manifest.json').read_text(),'{}')

    def test_vision_inventory_is_rendered_as_escaped_unverified_proposals(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d);(root/'review').mkdir();(root/'review/review.json').write_text(json.dumps({'review':{'summary':'<untrusted>','surfaces':None,'objects':[{'label':'Chair','evidence':'caster base visible','uncertainty':'<unknown>'}]}}))
            write_dashboard(root,{'status':'complete'})
            page=(root/'index.html').read_text();self.assertIn('caster base visible',page);self.assertIn('Objects · proposals',page);self.assertIn('&lt;unknown&gt;',page);self.assertNotIn('<untrusted>',page)

    def test_validation_failure_is_recorded_as_failed(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d);s=root/'source';s.mkdir();(s/'manifest.json').write_text('{}')
            with patch('pipeline.release.doctor',return_value={'ready':True}),patch('pipeline.validate_session.Validator')as validator:
                validator.return_value.run.return_value={'errors':['missing depth']}
                with self.assertRaises(ValueError):run(s,root/'result')
            state=json.loads((root/'result/run.json').read_text());self.assertEqual(state['status'],'failed');self.assertEqual(state['stage'],'validation')
            self.assertNotIn('Open 3D scene',(root/'result/index.html').read_text())
