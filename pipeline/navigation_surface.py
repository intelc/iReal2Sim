"""Build an estimated flat walking floor and sparse obstacle columns for the map UI."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from .rgbd import output_directory, write_json


def room_basis(up):
    up=np.array(up,float,copy=True);up/=np.linalg.norm(up)
    forward=np.array([0.,0.,1.]);forward-=up*np.dot(up,forward)
    if np.linalg.norm(forward)<.1:
        forward=np.array([0.,1.,0.]);forward-=up*np.dot(up,forward)
    forward/=np.linalg.norm(forward)
    return np.stack([np.cross(forward,up),up,forward])


def obstacle_columns(vertices,faces,basis,floor,cell=.12,height_cell=.1):
    """Rasterize dense mesh surface samples without filling the space between floors and ceilings."""
    local=np.asarray(vertices,dtype=np.float32)@basis.T
    low,high=np.percentile(local[:,[0,2]],[.5,99.5],axis=0)
    origin=np.floor((low-.3)/cell)*cell;end=np.ceil((high+.3)/cell)*cell
    width,depth=np.ceil((end-origin)/cell).astype(int)
    a,b,c=(local[faces[:,k]] for k in range(3))
    normal=np.cross(b-a,c-a);twice_area=np.linalg.norm(normal,axis=1)
    center=(a+b+c)/3;relative=center[:,1]-floor
    horizontal=np.abs(normal[:,1])/np.maximum(twice_area,1e-12)>.8
    # Low horizontal fragments are noisy/uneven floor, rather than walls around the feet.
    keep=(relative>.18)&(relative<3.3)&(~(horizontal&(relative<.42)))&(twice_area>1e-8)
    points=center[keep];area=twice_area[keep]/2
    ix=np.floor((points[:,0]-origin[0])/cell).astype(int);iz=np.floor((points[:,2]-origin[1])/cell).astype(int);iy=np.floor((points[:,1]-floor)/height_cell).astype(int)
    valid=(ix>=0)&(ix<width)&(iz>=0)&(iz<depth)
    ix,iy,iz,area=ix[valid],iy[valid],iz[valid],area[valid]
    layers=int(np.ceil(3.3/height_cell))+1;keys=(iz*width+ix)*layers+iy
    unique,inverse,counts=np.unique(keys,return_inverse=True,return_counts=True);areas=np.bincount(inverse,weights=area)
    accepted=(counts>=2)&(areas>=.0008)
    columns={}
    for key in unique[accepted]:
        column=int(key)//layers;y=int(key)%layers;columns.setdefault(column,[]).append(y)
    result=[]
    for column,bins in sorted(columns.items()):
        spans=[]
        for y in sorted(bins):
            bottom=max(.16,y*height_cell-.025);top=(y+1)*height_cell+.025
            if spans and bottom<=spans[-1][1]+.025:spans[-1][1]=top
            else:spans.append([bottom,top])
        result.append([int(column%width),int(column//width),[[round(a,4),round(b,4)] for a,b in spans]])
    return {'cellSize_m':cell,'origin_m':origin.tolist(),'width':int(width),'depth':int(depth),'columns':result},local


def build(scene_path,output,floor_report=None):
    scene_path=scene_path.resolve();scene=json.loads(scene_path.read_text())
    # The scene's original source recording is recoverable through its retained timeline report.
    source_replay=scene_path.parent
    protected=[source_replay]
    report_path=source_replay/'map.json'
    if report_path.exists():
        original=Path(json.loads(report_path.read_text())['source_replay']);protected.append(original)
        replay=json.loads((original/'replay.json').read_text());dataset=Path(replay['dataset']);cfg=json.loads((dataset/'calibration.json').read_text());protected.extend([dataset,Path(cfg['source']),Path(replay['run'])])
    if floor_report:protected.append(floor_report.resolve().parent)
    output=output_directory(source_replay,output,*protected)
    positions_path=(source_replay/scene['positions']['url']).resolve();faces_path=(source_replay/scene['indices']['url']).resolve()
    vertices=np.fromfile(positions_path,'<f4').reshape(-1,3);faces=np.fromfile(faces_path,'<u4').reshape(-1,3);basis=room_basis(scene.get('estimated_up',[-1,0,0]))
    floor=None;provenance=None
    if floor_report:
        structural=json.loads(floor_report.read_text())
        candidates=[p for p in structural['planes'] if p['role']=='floor_candidate']
        if candidates:
            selected=max(candidates,key=lambda p:p['area_m2']);floor=float(basis[1]@np.array(selected['world_origin_m']));provenance={'method':'Existing fitted floor candidate, extended as a flat navigation support','report':str(floor_report.resolve()),'plane_id':selected['id']}
    if floor is None:
        # Fallback is intentionally labelled estimated; a fitted floor is preferred.
        heights=vertices@basis[1];floor=float(np.percentile(heights,8));provenance={'method':'Lower mesh height percentile; uncalibrated navigation fallback'}
    field,local=obstacle_columns(vertices,faces,basis,floor)
    report={'schema':'iphone-rsa.walk-navigation/1','basis':dict(zip(['right','up','forward'],basis.tolist())),'floorHeight_m':floor,**field,'body':{'radius_m':.22,'height_m':1.75,'eyeHeight_m':1.6,'walkSpeed_m_s':1.6,'runSpeed_m_s':3.2,'jumpSpeed_m_s':3.2,'gravity_m_s2':9.81},'floor_provenance':provenance,'source_scene':str(scene_path),'source_positions_sha256':hashlib.sha256(positions_path.read_bytes()).hexdigest(),'source_indices_sha256':hashlib.sha256(faces_path.read_bytes()).hexdigest(),'limitations':['Virtual walking uses an estimated flat support plane, extended across gaps in the scan.','Obstacle columns approximate the current inferred room mesh; missing surfaces can remain passable.','Navigation is a display interaction, not recovered physical contact or calibrated gravity.']}
    write_json(output/'navigation.json',report)
    poses=[f for f in scene['timeline']['frames'] if f.get('pose')]
    samples=[];columns={(x,z):spans for x,z,spans in field['columns']}
    for f in poses[::max(1,len(poses)//24)]:
        world=np.array(f['pose'])[:3,3];p=basis@world;x,z=np.floor((p[[0,2]]-field['origin_m'])/field['cellSize_m']).astype(int)
        samples.append({'frame_id':f['id'],'local_camera_m':p.tolist(),'center_cell_obstacle_at_body_height':any(lo<1.75 and hi>.1 for lo,hi in columns.get((int(x),int(z)),[]))})
    write_json(output/'diagnostics.json',{'floorHeight_m':floor,'grid':[field['width'],field['depth']],'occupied_columns':len(field['columns']),'source_camera_samples':samples,'raw_geometry_modified':False,'source_poses_modified':False})
    print(json.dumps({'floorHeight_m':floor,'grid':[field['width'],field['depth']],'occupied_columns':len(field['columns']),'camera_centers_blocked':sum(s['center_cell_obstacle_at_body_height'] for s in samples),'camera_samples':len(samples)}),flush=True)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('scene',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--floor-report',type=Path);a=p.parse_args();build(a.scene,a.output,a.floor_report)
