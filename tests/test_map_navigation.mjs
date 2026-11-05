import test from 'node:test';
import assert from 'node:assert/strict';
import {orbitUpright,panUpright,collisionField,createWalker,stepWalker,walkerCamera} from '../pipeline/map_navigation.mjs';
import {dot,cross,portraitLookAt} from '../pipeline/map_geometry.mjs';

const frame={right:[-1,0,0],up:[0,1,0],forward:[0,0,1]};
const body={radius_m:.22,height_m:1.75,eyeHeight_m:1.6,walkSpeed_m_s:1.6,runSpeed_m_s:3.2,jumpSpeed_m_s:3.2,gravity_m_s2:9.81};
const field=(columns=[])=>collisionField({basis:frame,floorHeight_m:0,cellSize_m:1,origin_m:[0,0],width:20,depth:20,columns,body});
const close=(a,b,tol=1e-8)=>assert.ok(Math.abs(a-b)<tol,`${a} != ${b}`);
function upright(R){const cols=[0,1,2].map(i=>R.map(row=>row[i]));close(dot(cols[1],frame.up),0);for(const v of cols)close(dot(v,v),1);cross(cols[0],cols[1]).forEach((v,i)=>close(v,cols[2][i]));}

test('repeated horizontal and vertical orbit never rolls or flips at a pole',()=>{
  let camera=portraitLookAt([5,3,-4],[0,1,0],[1,1,0]);const distance=Math.hypot(...camera.t.map((v,i)=>v-camera.target[i]));
  for(let i=0;i<1200;i++){camera=orbitUpright(camera,frame,18,Math.sin(i/50)*45);upright(camera.R);close(Math.hypot(...camera.t.map((v,k)=>v-camera.target[k])),distance,1e-6);}
});

test('pan removes inherited roll while preserving observer distance',()=>{
  const original=portraitLookAt([2,3,-4],[1,1,0],[1,1,0]),pan=panUpright(original,frame,70,-60,600,.8);upright(pan.R);
  close(Math.hypot(...pan.t.map((v,i)=>v-pan.target[i])),Math.hypot(...original.t.map((v,i)=>v-original.target[i])));
});

test('WASD motion slides along a wall without tunnelling or diagonal speed gain',()=>{
  const columns=Array.from({length:20},(_,z)=>[5,z,[[.18,2.8]]]),world=field(columns),state=createWalker(world,[-3,1.6,3],[0,0,1]);
  for(let i=0;i<300;i++)stepWalker(state,{forward:1,side:1},1/60,world);
  assert.ok(state.x<4.781);assert.ok(state.z>7);assert.ok(Math.hypot(state.vx,state.vz)<=body.walkSpeed_m_s+1e-6);upright(walkerCamera(state,world).R);
  stepWalker(state,{side:1,run:true},5,world);assert.ok(state.x<4.781);
});

test('jump rises about half a metre and lands on the virtual floor across scan holes',()=>{
  const world=field(),state=createWalker(world,[-3,1.6,3],[0,0,1]);let peak=0;
  for(let i=0;i<120;i++){stepWalker(state,{jump:i===0},1/60,world);peak=Math.max(peak,state.feet);assert.ok(state.feet>=0);}
  assert.ok(peak>.45&&peak<.55);close(state.feet,0);assert.equal(state.grounded,true);
});

test('head stops below low overhead geometry and descending feet land on a surface',()=>{
  const world=field([[3,3,[[1.9,2.1]]]]),state=createWalker(world,[-3.5,1.6,3.5],[0,0,1]);let peak=0;
  for(let i=0;i<100;i++){stepWalker(state,{jump:i===0},1/60,world);peak=Math.max(peak,state.feet);}
  assert.ok(peak<=.151);assert.equal(state.grounded,true);
  const chair=field([[3,3,[[.2,.4]]]]),landing={x:3.5,z:3.5,feet:.6,vy:-1,vx:0,vz:0,yaw:0,pitch:0,grounded:false};
  for(let i=0;i<40;i++)stepWalker(landing,{},1/60,chair);
  close(landing.feet,.4);assert.equal(landing.grounded,true);
});

test('spawn resolves estimated obstacles without modifying the original camera position',()=>{
  const world=field([[3,3,[[.2,2.5]]]]),original=[-3.5,1.6,3.5],copy=original.slice(),state=createWalker(world,original,[0,0,1]);assert.ok(state);assert.equal(world.blocked(state.x,state.z,0),false);assert.deepEqual(original,copy);
});
