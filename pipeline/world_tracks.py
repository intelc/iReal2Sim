"""Place inferred hand meshes and measured bottle surface centroids in map coordinates."""
import argparse
import base64
import json
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree

from .fuse_map import read_poses
from .inspect_map import MeshRenderer
from .rgbd import backproject, output_directory, read_rows, write_json


def packed(values, dtype):
    return base64.b64encode(np.asarray(values,dtype=dtype).tobytes()).decode()


def portrait_to_world(points, portrait_from_landscape, world_from_landscape):
    landscape = points @ portrait_from_landscape
    return landscape @ world_from_landscape[:3,:3].T + world_from_landscape[:3,3]


def build(dataset:Path,run:Path,masks:Path,hand_result:Path,quality:Path,output:Path):
    dataset=dataset.resolve();cfg=json.loads((dataset/'calibration.json').read_text());output=output_directory(dataset,output,Path(cfg['source']),run,masks,hand_result)
    rows=read_rows(dataset/'frames.jsonl');poses={p['id']:p for p in read_poses(run/'mapping/optimized.tsv')}
    info=json.loads((hand_result/'run.json').read_text());C=np.array(info['source']['portrait_from_landscape']);raw=np.load(hand_result/'camera-hands.npz');K=np.array(cfg['intrinsics']);faces=raw['faces']
    side=info.get('hand','right')
    if Path(info['source']['dataset']).resolve()!=dataset:
        raise ValueError('Hand estimates belong to a different RGB-D export')
    hand_times={r['frame_id']:r['timestamp_s'] for r in info['source']['frames']}
    bad=set()
    for flag in json.loads(quality.read_text())['speed_above_2m_s']:
        bad.update(range(flag['frame_id']-3,flag['frame_id']+4))
    hand_frames={};hand_records=[];world_vertices=[];world_joints=[];visibility=[];ids=[]
    for frame_id,vertices,joints in zip(raw['frame_ids'],raw['vertices'],raw['joints']):
        frame_id=int(frame_id)
        if frame_id not in poses:continue
        row=rows[frame_id-1];pose=poses[frame_id]['world_from_camera'];landscape=vertices@C
        if abs(float(hand_times[frame_id])-float(row['timestamp_s']))>1e-8 or abs(poses[frame_id]['timestamp_s']-float(row['timestamp_s']))>1e-8:
            raise ValueError('Hand/camera/source timestamps disagree')
        world=portrait_to_world(vertices,C,pose);jworld=portrait_to_world(joints,C,pose)
        mesh=o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(landscape),o3d.utility.Vector3iVector(faces));mesh.paint_uniform_color([.85,.6,.25])
        _,predicted=MeshRenderer(mesh).render(K,np.eye(4),(cfg['width'],cfg['height']))
        measured=cv2.imread(str(dataset/row['depth']),-1).astype(np.float32)/1000
        labels=cv2.imread(str(masks/'labels'/f'{frame_id:06d}.png'),0)
        hand_mask=labels==1;valid=(measured>0)&hand_mask&np.isfinite(predicted)
        error=np.abs(measured[valid]-predicted[valid]);silhouette=np.isfinite(predicted)
        camera=landscape@K.T;pixels=np.rint(camera[:,:2]/camera[:,2:]).astype(int)
        inside=(pixels[:,0]>=0)&(pixels[:,0]<cfg['width'])&(pixels[:,1]>=0)&(pixels[:,1]<cfg['height'])&(landscape[:,2]>0)
        flags=np.full(len(vertices),4,np.uint8);flags[inside]=0;ind=np.flatnonzero(inside);z=measured[pixels[ind,1],pixels[ind,0]];have=z>0
        flags[ind[have]]=1
        flags[ind[have&(landscape[ind,2]>z+.03)]]=2
        flags[ind[have&(landscape[ind,2]<z-.03)]]=3
        record={'frame_id':frame_id,'timestamp_s':row['timestamp_s'],'camera_pose_quality':'motion_spike_nearby' if frame_id in bad else 'no_speed_flag',
                'depth_compared_pixels':int(valid.sum()),'median_depth_disagreement_m':float(np.median(error)) if len(error) else None,
                'p90_depth_disagreement_m':float(np.percentile(error,90)) if len(error) else None,
                'silhouette_agreement_with_mask':float(np.sum(silhouette&hand_mask)/max(1,np.sum(silhouette|hand_mask))),
                'inferred_occluded_vertices':int(np.sum(flags==2)),'unknown_depth_vertices':int(np.sum(flags==0)),'out_of_view_vertices':int(np.sum(flags==4)),
                'depth_supported_vertices':int(np.sum(flags==1)),'depth_disagreeing_front_vertices':int(np.sum(flags==3))}
        hand_records.append(record);ids.append(frame_id);world_vertices.append(world);world_joints.append(jworld);visibility.append(flags)
        supported_faces=faces[np.all(flags[faces]==1,axis=1)]
        hand_frames[str(frame_id)]={'positions':packed(world,'<f4'),'joints':packed(jworld,'<f4'),'quality':record,'visibility':packed(flags,'u1'),'supported_indices':packed(supported_faces,'<u4'),'supported_index_count':int(supported_faces.size)}
    # A marker denotes the observed surface centroid, never an inferred bottle mesh or 6DoF pose.
    sphere=o3d.geometry.TriangleMesh.create_sphere(radius=.012,resolution=4);sv=np.asarray(sphere.vertices);sf=np.asarray(sphere.triangles)
    object_frames={};object_records=[];observations={};by_hand={r['frame_id']:r for r in hand_records};by_vertices=dict(zip(ids,world_vertices))
    for i,row in enumerate(rows,1):
        if i not in poses:continue
        labels=cv2.imread(str(masks/'labels'/f'{i:06d}.png'),0);target=cv2.erode((labels==3).astype(np.uint8),np.ones((3,3),np.uint8))>0
        if target.sum()<80:continue
        depth=cv2.imread(str(dataset/row['depth']),-1).astype(np.float32)/1000;valid=target&(depth>.2)&(depth<2.)
        if valid.sum()<50:continue
        points=backproject(depth,K)[valid];points=points[::max(1,len(points)//2000)]
        cloud=o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points));clusters=np.asarray(cloud.cluster_dbscan(eps=.035,min_points=10,print_progress=False))
        candidates=[points[clusters==k] for k in set(clusters) if k>=0 and np.sum(clusters==k)>=max(20,.08*len(points))]
        if not candidates:continue
        points=min(candidates,key=lambda x:np.median(x[:,2]));pose=poses[i]['world_from_camera'];world=points@pose[:3,:3].T+pose[:3,3];center=np.median(world,axis=0)
        record={'frame_id':i,'timestamp_s':row['timestamp_s'],'world_surface_centroid_m':center.tolist(),'surface_points':len(world),'fraction_mask_with_depth':float(valid.sum()/target.sum()),
                'median_camera_depth_m':float(np.median(points[:,2])),'camera_pose_quality':'motion_spike_nearby' if i in bad else 'no_speed_flag','contact_label':'no_hand_estimate'}
        if i in by_vertices:
            distance=float(cKDTree(world).query(by_vertices[i],workers=1)[0].min());r=by_hand[i]
            reliable=i not in bad and r['depth_compared_pixels']>=100 and r['p90_depth_disagreement_m'] is not None and r['p90_depth_disagreement_m']<.05
            record.update({'minimum_hand_mesh_to_bottle_observation_m':distance,'contact_label':('proximity_candidate' if distance<.03 else 'no_proximity_evidence') if reliable else 'uncertain'})
        observations[f'frame_{i}']=world.astype(np.float32);object_records.append(record);object_frames[str(i)]={'positions':packed(sv+center,'<f4'),'quality':record}
    np.savez_compressed(output/'world-hands.npz',frame_ids=ids,vertices=np.array(world_vertices,dtype=np.float32),joints=np.array(world_joints,dtype=np.float32),faces=faces,visibility=np.array(visibility))
    np.savez_compressed(output/'bottle-observations.npz',**observations)
    write_json(output/'tracks.json',{'schema':'iphone-rsa.viewer-tracks/1','tracks':[
        {'name':side+'_hand','color':[.91,.62,.28] if side=='right' else [.66,.76,.89],'indices':packed(faces,'<u4'),'indexCount':int(faces.size),'visibility_codes':{'0':'in view, input depth unavailable','1':'within 3cm of measured depth','2':'behind measured surface by more than 3cm; inferred occluded','3':'in front of measured surface by more than 3cm','4':'outside recorded camera view'},'frames':hand_frames},
        {'name':'bottle_surface_centroid','color':[.15,.65,.85],'indices':packed(sf,'<u4'),'indexCount':int(sf.size),'frames':object_frames}]})
    refined=info.get('schema')=='iphone-rsa.refined-hawor/1'
    report={'schema':'iphone-rsa.world-tracks/1','status':'complete','dataset':str(dataset),'run':str(run),'hand_method':'HaWoR camera-space temporal model'+(' + CUDA MANO silhouette/depth refinement' if refined else '')+' + RGB-D map poses','hand_result':str(hand_result.resolve()),'hand_refinement_evaluation':info.get('evaluation'),'hand_frames':hand_records,'bottle_frames':object_records,
            'limitations':['All hand meshes infer complete shape, including hidden fingers. Visibility flags use noisy input depth, not ground truth.','No HaWoR monocular SLAM or occlusion infiller was run; missing hand detections remain missing.','No calibrated hand confidence is available; depth residual, mask agreement and camera-motion flags are diagnostics.',('Hand shape/pose is fitted to noisy observed silhouettes and depth; see separate withheld-frame evaluation and correlated-data limitations.' if refined else 'Hand depth is not fitted to the observations in this report.'),'Bottle markers are depth surface centroids, not object centers, orientations or FoundationPose estimates. Transparent glass can yield background returns.','Contact labels denote geometric proximity candidates or uncertainty, never verified physical contact.','Speed flags mark known fragile blank-background camera intervals; world tracks inherit camera pose uncertainty.']}
    write_json(output/'tracks-report.json',report);print('World tracks',len(hand_records),'hand frames,',len(object_records),'bottle observations')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('dataset',type=Path);p.add_argument('run',type=Path);p.add_argument('--masks',type=Path,required=True);p.add_argument('--hand-result',type=Path,required=True);p.add_argument('--quality',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();build(a.dataset,a.run,a.masks,a.hand_result,a.quality,a.output)
