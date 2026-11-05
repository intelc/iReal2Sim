import base64
import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np
import trimesh

from pipeline.workflow_artifacts import checked_tracks,model_buffers


def track(name='right_hand',gate=None):
    enc=lambda values,dtype:base64.b64encode(np.asarray(values,dtype).tobytes()).decode()
    return {'name':name,'color':[.8,.6,.4],'indices':enc([0,1,2],'<u4'),'indexCount':3,
            'frames':{'7':{'positions':enc([0,0,1,.1,0,1,0,.1,1],'<f4'),'quality':gate or{}}}}


class WorkflowArtifactTests(unittest.TestCase):
    def test_unknown_hand_quality_fails_closed_without_filling_missing_frames(self):
        item=track();out=checked_tracks({'tracks':[item]},'hands',{3,7})
        self.assertEqual(set(out[0]['frames']),{'7'});self.assertIs(out[0]['frames']['7']['quality']['display_support_gate']['passed'],False)

    def test_object_gate_is_preserved_and_cannot_promote_unknown_samples(self):
        for passed in [True,False,None]:
            item=track('bottle_model',{'passes_observation_gate':passed})
            out=checked_tracks({'tracks':[item]},'objects',{7})
            self.assertEqual(out[0]['frames']['7']['quality']['display_support_gate']['passed'],passed is True)

    def test_foreign_frames_and_wrong_actor_stage_are_rejected(self):
        for stage,ids in [('hands',{1}),('objects',{7})]:
            with self.assertRaises(ValueError):checked_tracks({'tracks':[track()]},stage,ids)

    def test_nonfinite_actor_geometry_is_rejected(self):
        item=track();item['frames']['7']['positions']=base64.b64encode(np.full(9,np.nan,'<f4').tobytes()).decode()
        with self.assertRaises(ValueError):checked_tracks({'tracks':[item]},'hands',{7})

    def test_model_node_transforms_survive_conversion_to_viewer_buffers(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d);model=trimesh.Scene();T=np.eye(4);T[:3,3]=[2,3,4];model.add_geometry(trimesh.creation.box(),transform=T)
            path=root/'model.glb';path.write_bytes(model.export(file_type='glb'));model_buffers(path,root)
            points=np.fromfile(root/'modeled-positions.bin','<f4').reshape(-1,3)
            np.testing.assert_allclose(points.mean(axis=0),[2,3,4]);self.assertEqual(json.loads((root/'modeled-scene.json').read_text())['indexCount'],36)

    def test_external_glb_resources_are_rejected_without_reading_them(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d);description=json.dumps({'buffers':[{'uri':'../private.bin'}]}).encode();description+=b' '*((-len(description))%4)
            path=root/'unsafe.glb';path.write_bytes(struct.pack('<4sII',b'glTF',2,20+len(description))+struct.pack('<II',len(description),0x4e4f534a)+description)
            with self.assertRaisesRegex(ValueError,'external GLB'):model_buffers(path,root)
