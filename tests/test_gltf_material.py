import json
from pathlib import Path
import struct
import tempfile
import unittest
from pipeline.gltf_material import captured_rgb_unlit_glb


class CapturedMaterialTest(unittest.TestCase):
    def test_material_fix_preserves_binary_geometry_and_texture_bytes(self):
        source={'asset':{'version':'2.0'},'materials':[{'pbrMetallicRoughness':{'baseColorFactor':[.4,.4,.4,1],'baseColorTexture':{'index':0}}}]};text=json.dumps(source).encode();text+=b' '*((-len(text))%4);binary=b'1234GEOMETRYTEXTURE00';body=struct.pack('<II',len(text),0x4e4f534a)+text+struct.pack('<II',len(binary),0x004e4942)+binary;data=struct.pack('<III',0x46546c67,2,12+len(body))+body
        with tempfile.TemporaryDirectory() as d:
            p=Path(d,'scene.glb');p.write_bytes(data);captured_rgb_unlit_glb(p);result=p.read_bytes();size,kind=struct.unpack_from('<II',result,12);doc=json.loads(result[20:20+size]);self.assertEqual(doc['materials'][0]['pbrMetallicRoughness']['baseColorFactor'],[1,1,1,1]);self.assertIn('KHR_materials_unlit',doc['materials'][0]['extensions']);self.assertEqual(result[20+size+8:],binary)
