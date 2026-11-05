"""Keep captured RGB appearance unlit and untinted in standard GLB exports."""
import json
from pathlib import Path
import struct


def captured_rgb_unlit_glb(path:Path):
    data=path.read_bytes();magic,version,total=struct.unpack_from('<III',data)
    if magic!=0x46546C67 or version!=2 or total!=len(data):raise ValueError('Invalid GLB v2')
    chunks=[];offset=12
    while offset<len(data):
        length,kind=struct.unpack_from('<II',data,offset);body=data[offset+8:offset+8+length];offset+=8+length
        if kind==0x4E4F534A:
            doc=json.loads(body)
            for material in doc.get('materials',[]):
                pbr=material.setdefault('pbrMetallicRoughness',{});pbr['baseColorFactor']=[1.,1.,1.,1.];pbr['metallicFactor']=0.;pbr['roughnessFactor']=1.;material['doubleSided']=True;material.setdefault('extensions',{})['KHR_materials_unlit']={}
            used=doc.setdefault('extensionsUsed',[])
            if 'KHR_materials_unlit' not in used:used.append('KHR_materials_unlit')
            body=json.dumps(doc,separators=(',',':')).encode();body+=b' '*((-len(body))%4)
        chunks.append(struct.pack('<II',len(body),kind)+body)
    payload=b''.join(chunks);path.write_bytes(struct.pack('<III',magic,version,12+len(payload))+payload)
