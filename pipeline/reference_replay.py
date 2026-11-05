"""Package synchronized estimated actors and selectable room representations."""
import argparse
import base64
import copy
import io
import json
import os
from pathlib import Path
import shutil
import numpy as np
from PIL import Image
from .fuse_map import read_poses
from .rgbd import write_json,read_rows,output_directory
from .world_tracks import packed


def relative(path,root):
    return os.path.relpath(path.resolve(),root.resolve())


def geometry_scene(source,root):
    scene=json.loads(source.read_text())
    for key in ['positions','colors','uv','indices']:
        if isinstance(scene[key],dict):scene[key]={'url':relative(source.parent/scene[key]['url'],root)}
    if scene.get('atlas') and not scene['atlas'].startswith('data:'):scene['atlas']=relative(source.parent/scene['atlas'],root)
    return scene


def write_html(output):
    template=Path(__file__).with_name('viewer.html').read_text().replace('__PAYLOAD__',"await (await fetch('scene.json')).json()")
    template=template.replace('Reconstruction inspection','Scene + hands replay').replace('Compare the captured image with the measured mesh from the same estimated camera. Drag the mesh to orbit; scroll to zoom.','Recorded RGB, room geometry and estimated hand–object motion share the capture timeline. Select the room representation, orbit freely, or inspect the hand.').replace('<span>Fused mesh</span>','<span>Estimated 3D scene</span>')
    (output/'viewer.html').write_text(template)


def run(a):
    dataset=a.dataset.resolve();cfg=json.loads((dataset/'calibration.json').read_text());a.output=output_directory(dataset,a.output,Path(cfg['source']),a.run,a.timeline,a.tracks,a.base_scene.parent)
    poses={p['id']:p for p in read_poses(a.run/'mapping/optimized.tsv')};source=json.loads((a.timeline/'scene.json').read_text());report=json.loads((a.tracks/'tracks-report.json').read_text())
    if Path(report['dataset']).resolve()!=dataset or Path(report['run']).resolve()!=a.run.resolve():raise ValueError('Track dataset or trajectory mismatch')
    data=geometry_scene(a.base_scene,a.output)
    if isinstance(data['positions'],dict):
        positions=np.frombuffer((a.output/data['positions']['url']).read_bytes(),dtype='<f4').reshape(-1,3)
        data['overview_bounds']=np.percentile(positions[::max(1,len(positions)//100000)],[1,99],axis=0).tolist()
    data.update({k:source[k] for k in ['K','width','height','views','timeline']})
    data=copy.deepcopy(data)
    for frame in data['timeline']['frames']:
        p=poses.get(frame['id'])
        if p and abs(p['timestamp_s']-1-frame['seconds'])>1e-8:raise ValueError('Timeline timestamp identity mismatch')
        frame['pose']=p['world_from_camera'].tolist() if p else None
    for view in data['views']:
        view['pose']=poses[view['id']]['world_from_camera'].tolist();view['median_depth_error_m']=None
    rows=read_rows(dataset/'frames.jsonl')
    for requested in a.review_time or []:
        frame=min(data['timeline']['frames'],key=lambda f:abs(f['seconds']-requested));fid=frame['id']
        if any(v['id']==fid for v in data['views']):continue
        image=Image.open(dataset/rows[fid-1]['rgb']).transpose(Image.Transpose.ROTATE_270);buffer=io.BytesIO();image.save(buffer,format='JPEG',quality=90)
        data['views'].append({'id':fid,'seconds':frame['seconds'],'pose':frame['pose'],'focus':.7,'rgb':'data:image/jpeg;base64,'+base64.b64encode(buffer.getvalue()).decode(),'median_depth_error_m':None})
    data['views'].sort(key=lambda v:v['seconds'])
    data['timeline']['video']=relative(a.timeline/source['timeline']['video'],a.output)
    if source['timeline'].get('mask_root'):data['timeline']['mask_root']=relative(a.timeline/source['timeline']['mask_root'],a.output)+'/'
    data['scene_label']=a.base_label;data['scene_options']=[]
    data['provenance_note']='Camera motion includes an inferred translation bridge near 57s. Hand support filters are heuristics. Bottle colors sampled from the current view do not recover its hidden texture or axial rotation.'
    for choice in a.scene_option or []:
        label,path=choice.split('=',1);path=Path(path);dest=a.output/f'option-{len(data["scene_options"])}.json';write_json(dest,geometry_scene(path,a.output));data['scene_options'].append({'id':str(len(data['scene_options'])),'label':label,'url':dest.name})
    if a.structure_report:data['estimated_up']=json.loads(a.structure_report.read_text())['up_estimate_world']
    tracks=json.loads((a.tracks/'tracks.json').read_text())
    for track in tracks['tracks']:
        if not track['name'].endswith('_hand'):continue
        for frame in track['frames'].values():
            q=frame['quality'];reasons=[]
            if q['depth_compared_pixels']<100:reasons.append('little overlapping input depth')
            if q['median_depth_disagreement_m'] is None or q['median_depth_disagreement_m']>.05:reasons.append('weak depth agreement')
            if q.get('out_of_view_vertices',0)>544:reasons.append('most model vertices outside source view')
            if q.get('depth_supported_vertices',0)<20:reasons.append('little measured surface support')
            q['display_support_gate']={'passed':not reasons,'reasons':reasons,'kind':'heuristic preview filter; not calibrated confidence'}
    if a.bottle_track:
        bottle=np.load(a.bottle_track);meta=json.loads(a.bottle_track.with_suffix('.json').read_text());frames={};ids=bottle['frame_ids'];gate=bottle['observation_gate'];vertices=bottle['vertices_m'];records=meta['frames']
        colors=np.load(a.bottle_colors) if a.bottle_colors else None
        color_meta={r['frame_id']:r for r in json.loads(a.bottle_colors.with_name('provenance.json').read_text())['frames']} if a.bottle_colors else {}
        for fid,time,T,passed in zip(ids,bottle['timestamps_s'],bottle['T_camera_from_object'],gate):
            fid=int(fid)
            if fid not in poses:continue
            if abs(poses[fid]['timestamp_s']-time)>1e-8:raise ValueError('Bottle/source timestamp identity mismatch')
            world=poses[fid]['world_from_camera']@T;points=vertices@world[:3,:3].T+world[:3,3];q=records[str(fid)]['quality']
            frames[str(fid)]={'positions':packed(points,'<f4'),'quality':{**q,'method':'axisymmetric RGB-D proxy'+(' · weak observation' if not passed else ''),'yaw_observed':False}}
            if colors is not None and color_meta[fid]['frame_accepted']:
                frame_colors=colors[f'frame_{fid:06d}_rgb']
                if frame_colors.shape!=bottle['vertex_colors_rgb'].shape:raise ValueError('Bottle color topology mismatch')
                frames[str(fid)]['colors']=packed(frame_colors,'u1');frames[str(fid)]['quality']['appearance']='view-conditioned RGB on visibility/depth-supported vertices; hidden vertices retain band fallback'
        tracks['tracks']=[t for t in tracks['tracks'] if t['name']!='bottle_surface_centroid']
        tracks['tracks'].append({'name':'bottle_model','color':[.27,.55,.7],'colors':packed(bottle['vertex_colors_rgb'],'u1'),'indices':packed(bottle['faces'],'<u4'),'indexCount':int(bottle['faces'].size),'frames':frames})
    write_json(a.output/'interaction-tracks.json',tracks);data['tracks']='interaction-tracks.json'
    if a.ultra_preview:
        ultra=json.loads(a.ultra_preview.read_text())
        if Path(ultra['dataset']).resolve()!=dataset:raise ValueError('Ultra preview dataset mismatch')
        if len(ultra['frames'])!=len(data['timeline']['frames']):raise ValueError('Ultra timeline length mismatch')
        for u,f in zip(ultra['frames'],data['timeline']['frames']):
            if u['frame_id']!=f['id'] or abs(u['presentation_seconds']-f['seconds'])>1e-8:raise ValueError('Ultra presentation timestamp identity mismatch')
        data['ultra_context']={'video':relative(a.ultra_preview.parent/ultra['video'],a.output),'exposure_offsets_ms':[f['ultra_minus_main_ms'] for f in ultra['frames']],'limitations':ultra['limitations']}
    if a.paired_preview:
        pair=json.loads(a.paired_preview.read_text())
        if Path(pair['dataset']).resolve()!=dataset or pair['frames']!=len(data['timeline']['frames']) or pair['max_presentation_error_ms']>2:raise ValueError('Paired preview dataset/timeline mismatch')
        data['paired_preview']={'video':relative(a.paired_preview.parent/pair['video'],a.output)}
    write_json(a.output/'scene.json',data)
    write_html(a.output)
    write_json(a.output/'replay.json',{'schema':'iphone-rsa.reference-replay/1','dataset':str(dataset),'run':str(a.run.resolve()),'tracks':str(a.tracks.resolve()),'base_scene':str(a.base_scene.resolve()),'bottle_track':str(a.bottle_track.resolve()) if a.bottle_track else None,'timeline_frames':len(data['timeline']['frames']),'status':'complete','limitations':['Static-map anchored camera trajectory includes an explicitly inferred short translation bridge. Original poses and timestamps remain unchanged.','Fitted room surfaces and optional local completion are estimates, with provenance and raw alternatives retained.','Bottle proxy repeats measured color bands around an inferred symmetric circumference. Axial yaw and hidden geometry are not observed.','Full inferred hand includes occluded surfaces. Depth-supported mode is a noisy-input consistency filter, not ground truth.','No simulation or physical contact verification is implied.']})
    print(a.output/'viewer.html')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('dataset',type=Path);p.add_argument('run',type=Path);p.add_argument('--timeline',type=Path,required=True);p.add_argument('--tracks',type=Path,required=True);p.add_argument('--base-scene',type=Path,required=True);p.add_argument('--base-label',default='Measured static scan');p.add_argument('--scene-option',action='append');p.add_argument('--structure-report',type=Path);p.add_argument('--bottle-track',type=Path);p.add_argument('--bottle-colors',type=Path);p.add_argument('--ultra-preview',type=Path);p.add_argument('--paired-preview',type=Path);p.add_argument('--review-time',type=float,action='append');p.add_argument('--output',type=Path,required=True);run(p.parse_args())
