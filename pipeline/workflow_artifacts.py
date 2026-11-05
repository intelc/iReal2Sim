"""Compose available worker estimates with a preserved baseline replay."""
import base64
import io
import json
from pathlib import Path
import shutil
import struct

import numpy as np

from .agent_workflow import digest,sha
from .release import save


def model_buffers(path,output):
    import trimesh
    raw=Path(path).read_bytes()
    if raw[:4]!=b'glTF'or len(raw)<20:raise ValueError('scene_glb must be an embedded GLB.')
    size,kind=struct.unpack_from('<II',raw,12)
    if kind!=0x4e4f534a:raise ValueError('GLB JSON chunk is missing.')
    description=json.loads(raw[20:20+size])
    for item in description.get('buffers',[])+description.get('images',[]):
        if item.get('uri')and not item['uri'].startswith('data:'):raise ValueError('Embed model buffers/textures; external GLB references are not allowed.')
    scene=trimesh.load(io.BytesIO(raw),file_type='glb',force='scene',process=False)
    mesh=scene.to_mesh();vertices=np.asarray(mesh.vertices,'<f4');faces=np.asarray(mesh.faces,'<u4')
    if not len(vertices)or not len(faces)or not np.isfinite(vertices).all():raise ValueError('Model has no valid finite geometry.')
    if faces.max()>=len(vertices):raise ValueError('Model triangle index exceeds vertex count.')
    colors=np.asarray(mesh.visual.vertex_colors,dtype='u1')[:,:3]
    if len(colors)!=len(vertices):colors=np.full((len(vertices),3),175,dtype='u1')
    arrays={'positions':vertices,'indices':faces,'colors':colors,'uv':np.zeros((len(vertices),2),'<f4')}
    data={}
    for name,values in arrays.items():
        filename='modeled-'+name+'.bin';(output/filename).write_bytes(values.tobytes());data[name]={'url':filename}
    data.update(indexCount=int(faces.size),atlas=None,quality='Unreviewed worker geometry; hidden surfaces and dimensions are estimates.')
    save(output/'modeled-scene.json',data)


def checked_tracks(document,stage,source_ids):
    tracks=document.get('tracks')
    if not isinstance(tracks,list)or not tracks:raise ValueError('Worker interaction_tracks must contain actual actor tracks.')
    names=set()
    for track in tracks:
        name=track.get('name')
        if not isinstance(name,str)or name in names:raise ValueError('Actor names must be unique strings.')
        names.add(name)
        if (name in('left_hand','right_hand'))!=(stage=='hands'):raise ValueError('Actor identity does not match the worker stage.')
        indices=np.frombuffer(base64.b64decode(track['indices'],validate=True),'<u4')
        if not len(indices)or len(indices)%3 or track.get('indexCount')!=len(indices):raise ValueError('Invalid actor triangle indices.')
        frames=track.get('frames')
        if not isinstance(frames,dict)or not frames:raise ValueError('Actor frames are missing.')
        vertex_count=None
        for key,frame in frames.items():
            if not key.isdecimal()or int(key)not in source_ids:raise ValueError('Actor frame ID is absent from the original replay.')
            points=np.frombuffer(base64.b64decode(frame['positions'],validate=True),'<f4')
            if len(points)%3 or not len(points)or not np.isfinite(points).all()or indices.max()>=len(points)//3:raise ValueError('Invalid actor positions.')
            if vertex_count is not None and vertex_count!=len(points)//3:raise ValueError('Actor vertex counts must be stable over time.')
            vertex_count=len(points)//3
            quality=frame.get('quality',{})
            if not isinstance(quality,dict):raise ValueError('Actor quality metadata must be an object.')
            gate=quality.get('display_support_gate',{})
            passed=gate.get('passed')is True if stage=='hands'else quality.get('passes_observation_gate')is True
            quality['display_support_gate']={**gate,'passed':passed};frame['quality']=quality
    return tracks


def compose(root,state):
    from .ui import polish_viewer
    presentation={name:sha(Path(__file__).with_name(name))for name in('ui.py','viewer.css','viewer_ui.mjs')}
    fingerprint=digest({'presentation':presentation,'implementation':sha(__file__),'stages':{k:s.get('artifact_sha256',{})for k,s in state['stages'].items()if s['status']=='complete'}})
    if state.get('viewer_fingerprint')==fingerprint and(root/state.get('viewer_path','')/'viewer.html').is_file():return
    parent=root/'viewer';parent.mkdir(exist_ok=True)
    output=parent/f'v{len(list(parent.glob("v*")))+1:03d}'
    source=root/state['stages']['scan']['output']/'map';shutil.copytree(source,output)
    data=json.loads((output/'scene.json').read_text());source_ids={f['id']for f in data['timeline']['frames']};tracks=[]
    for stage,record in state['stages'].items():
        if record['status']!='complete':continue
        for item in record.get('artifacts',[]):
            if item['role']=='scene_glb':
                model_buffers(root/item['path'],output)
                data['scene_label']='Captured reconstruction'
                data['scene_options']=[{'id':'models','label':'Modeled scene · unreviewed','url':'modeled-scene.json'}]
            elif item['role']=='interaction_tracks':
                document=json.loads((root/item['path']).read_text())
                tracks.extend(checked_tracks(document,stage,source_ids))
    if tracks:
        if len({t['name']for t in tracks})!=len(tracks):raise ValueError('Worker actor identities overlap.')
        save(output/'interaction-tracks.json',{'schema':'iphone-rsa.viewer-tracks/1','tracks':tracks,'quality':'Estimated geometry, hidden shape inferred; contact not verified.'})
        data['tracks']='interaction-tracks.json'
        page=output/'viewer.html';text=page.read_text()
        old="if(mesh.name.endsWith('_hand')&&!document.getElementById('includeWeak').checked&&!document.getElementById('supportedHand').checked&&frame.quality.display_support_gate?.passed===false)continue;"
        new="if(!document.getElementById('includeWeak').checked&&frame.quality?.display_support_gate?.passed!==true)continue;"
        if text.count(old)!=1:raise ValueError('Actor display anchor changed; do not bypass the default quality gates.')
        page.write_text(text.replace(old,new))
    save(output/'scene.json',data)
    polish_viewer(output/'viewer.html',back_url='../../index.html')
    state['viewer_path']=str(output.relative_to(root));state['viewer_fingerprint']=fingerprint
