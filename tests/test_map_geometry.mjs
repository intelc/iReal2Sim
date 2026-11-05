import test from 'node:test';
import assert from 'node:assert/strict';
import {cameraCenter,cameraFrustum,trajectorySegments,portraitLookAt,mapFrame,mapCamera,dot,cross} from '../pipeline/map_geometry.mjs';

const identity=(x=0,y=0,z=0)=>[[1,0,0,x],[0,1,0,y],[0,0,1,z],[0,0,0,1]];
const close=(a,b,tol=1e-6)=>a.forEach((v,i)=>assert.ok(Math.abs(v-b[i])<tol,`${a} != ${b}`));

test('frustum points preserve calibrated camera edges and world translation',()=>{
  const K=[[400,0,319.5],[0,420,179.5],[0,0,1]],pose=identity(2,3,4),original=JSON.stringify(pose);
  const p=cameraFrustum(pose,K,640,360,.3);close(Array.from(p.subarray(0,3)),[2,3,4]);
  const corner=Array.from(p.subarray(3,6)).map((v,i)=>v-[2,3,4][i]);
  close([corner[0]/corner[2]*400+319.5,corner[1]/corner[2]*420+179.5],[-.5,-.5],1e-3);
  assert.equal(JSON.stringify(pose),original);
});

test('frustum heading follows camera rotation, not display orbit',()=>{
  const pose=[[0,0,1,5],[0,1,0,2],[-1,0,0,3],[0,0,0,1]];
  const p=cameraFrustum(pose,[[400,0,320],[0,400,180],[0,0,1]],640,360,.3);
  close(Array.from(p.slice(-3)),[5+.3*1.65,2,3]);close(cameraCenter(pose),[5,2,3]);
});

test('trajectory never bridges a missing pose or a timestamp gap',()=>{
  const frames=[{id:1,seconds:0,pose:identity()},{id:2,seconds:.03,pose:identity(1)},
    {id:3,seconds:.06,pose:null},{id:4,seconds:.09,pose:identity(3)},
    {id:5,seconds:1,pose:identity(4)},{id:6,seconds:1.03,pose:identity(5)}];
  const path=trajectorySegments(frames);assert.equal(path.positions.length,12);assert.equal(path.completed.get(2),2);assert.equal(path.completed.get(5),2);assert.equal(path.completed.get(6),4);
});

test('portrait orbit basis is right handed and display up matches world up',()=>{
  const {R}=portraitLookAt([0,3,-5],[0,0,0],[0,1,0]),cols=[0,1,2].map(c=>R.map(r=>r[c]));
  close(cross(cols[0],cols[1]),cols[2]);cols.forEach(v=>assert.ok(Math.abs(dot(v,v)-1)<1e-10));
  assert.ok(dot(cols[0],[0,1,0])<0);assert.ok(Math.abs(dot(cols[0],cols[2]))<1e-10);
});

test('top view stays finite for estimated up parallel to the initial camera axis',()=>{
  const positions=new Float32Array([0,0,0, 2,0,0, 0,3,0, 2,3,2]);
  const frame=mapFrame(positions,[0,0,1]);const pose=mapCamera(frame,[[400,0,320],[0,400,180],[0,0,1]],640,1.5,'top');
  assert.ok(pose.R.flat().every(Number.isFinite));assert.ok(pose.t.every(Number.isFinite));
  assert.ok(dot(pose.R.map(r=>r[2]),frame.up)<-.999);
});
