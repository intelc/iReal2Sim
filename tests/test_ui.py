import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pipeline.ui import polish_viewer,render_workflow


class PresentationTests(unittest.TestCase):
    def test_viewer_polish_preserves_geometry_timing_and_actor_gate(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);page=root/'viewer.html';original='<!doctype html><head></head><body><script type="module">const gate="display_support_gate";const poses=[1,2,3];</script></body>'
            page.write_text(original);scene=root/'scene.json';scene.write_text('{"timestamp":12.3,"poses":[1,2,3]}');before=hashlib.sha256(scene.read_bytes()).hexdigest()
            polish_viewer(page);once=page.read_text();polish_viewer(page)
            self.assertEqual(page.read_text(),once);self.assertIn('const gate="display_support_gate";const poses=[1,2,3];',once)
            self.assertEqual(hashlib.sha256(scene.read_bytes()).hexdigest(),before);self.assertTrue((root/'viewer_ui.mjs').is_file())

    def test_available_scene_and_incomplete_fitting_are_both_visible(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'viewer/v001').mkdir(parents=True);(root/'viewer/v001/viewer.html').write_text('scene')
            state={'status':'needs_setup','recording_name':'<capture>','viewer_path':'viewer/v001','stages':{'scan':{'status':'complete'},'hands':{'status':'needs_setup','needs':['<runner>']}}}
            page=render_workflow(root,state);self.assertIn('Scene ready',page);self.assertIn('Open scene',page);self.assertIn('Optional fitting',page);self.assertIn('&lt;capture&gt;',page);self.assertIn('&lt;runner&gt;',page)

    def test_recording_label_is_presentation_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'presentation.json').write_text(json.dumps({'name':'Kitchen <v2>'}));state={'status':'planned','recording_name':'original-capture','stages':{}}
            page=render_workflow(root,state);self.assertIn('Kitchen &lt;v2&gt;',page);self.assertIn('original-capture',page);self.assertEqual(state['recording_name'],'original-capture')
