import {add,sub,mul,dot,cross,normalize,portraitLookAt} from './map_geometry.mjs';

export const clamp=(x,lo,hi)=>Math.max(lo,Math.min(hi,x));
const column=(R,i)=>R.map(row=>row[i]);
const PITCH_LIMIT=Math.PI/2-.035;

export function viewAngles(forward,frame) {
  const f=normalize(forward),horizontal=sub(f,mul(frame.up,dot(f,frame.up)));
  const h=Math.hypot(...horizontal)>1e-7?normalize(horizontal):frame.forward;
  return {yaw:Math.atan2(dot(h,frame.right),dot(h,frame.forward)),pitch:clamp(Math.asin(clamp(dot(f,frame.up),-1,1)),-PITCH_LIMIT,PITCH_LIMIT)};
}

export function lookDirection(frame,yaw,pitch) {
  return add(mul(add(mul(frame.forward,Math.cos(yaw)),mul(frame.right,Math.sin(yaw))),Math.cos(pitch)),mul(frame.up,Math.sin(pitch)));
}

export function orbitUpright(camera,frame,dx,dy) {
  const angles=viewAngles(sub(camera.target,camera.t),frame),distance=Math.max(.25,Math.hypot(...sub(camera.target,camera.t)));
  const forward=lookDirection(frame,angles.yaw-dx*.005,clamp(angles.pitch-dy*.005,-PITCH_LIMIT,PITCH_LIMIT));
  return portraitLookAt(sub(camera.target,mul(forward,distance)),camera.target,frame.up);
}

export function panUpright(camera,frame,dx,dy,pixelsHigh,tanV) {
  const leveled=orbitUpright(camera,frame,0,0),distance=Math.hypot(...sub(leveled.t,leveled.target));
  const scale=2*distance*tanV/Math.max(1,pixelsHigh),pan=add(mul(column(leveled.R,1),dx*scale),mul(column(leveled.R,0),-dy*scale));
  return portraitLookAt(add(leveled.t,pan),add(leveled.target,pan),frame.up);
}

export function collisionField(data) {
  const cells=new Map(data.columns.map(([x,z,spans])=>[z*data.width+x,spans]));
  const cell=data.cellSize_m,[ox,oz]=data.origin_m;
  function intervals(x,z,radius) {
    const result=[];
    for(let iz=Math.max(0,Math.floor((z-radius-oz)/cell));iz<=Math.min(data.depth-1,Math.floor((z+radius-oz)/cell));iz++)
      for(let ix=Math.max(0,Math.floor((x-radius-ox)/cell));ix<=Math.min(data.width-1,Math.floor((x+radius-ox)/cell));ix++) {
        const a=ox+ix*cell,b=oz+iz*cell,dx=x-clamp(x,a,a+cell),dz=z-clamp(z,b,b+cell);
        if(dx*dx+dz*dz<radius*radius-1e-10)result.push(...(cells.get(iz*data.width+ix)||[]));
      }
    return result;
  }
  function blocked(x,z,feet,radius=.22,height=1.75) {
    if(x-radius<ox||z-radius<oz||x+radius>ox+data.width*cell||z+radius>oz+data.depth*cell)return true;
    return intervals(x,z,radius).some(([lo,hi])=>lo<feet+height-.005&&hi>feet+.055);
  }
  function spawn(x,z,radius=.22,height=1.75) {
    if(!blocked(x,z,0,radius,height))return [x,z];
    const candidates=[];
    for(let iz=0;iz<data.depth;iz++)for(let ix=0;ix<data.width;ix++) {
      const px=ox+(ix+.5)*cell,pz=oz+(iz+.5)*cell,d=(px-x)**2+(pz-z)**2;
      candidates.push([d,px,pz]);
    }
    candidates.sort((a,b)=>a[0]-b[0]);
    for(const [,px,pz]of candidates)if(!blocked(px,pz,0,radius,height))return [px,pz];
    return null;
  }
  return {...data,intervals,blocked,spawn};
}

export function createWalker(field,worldStart,worldForward) {
  const frame={...field.basis},p=[dot(worldStart,frame.right),dot(worldStart,frame.up),dot(worldStart,frame.forward)],body=field.body;
  const position=field.spawn(p[0],p[2],body.radius_m,body.height_m);
  if(!position)return null;
  const angles=viewAngles(worldForward,frame);
  return {x:position[0],z:position[1],feet:0,vy:0,vx:0,vz:0,yaw:angles.yaw,pitch:-.08,grounded:true};
}

export function walkerCamera(state,field) {
  const basis=field.basis,up=basis.up;
  const eye=add(add(mul(basis.right,state.x),mul(up,field.floorHeight_m+state.feet+field.body.eyeHeight_m)),mul(basis.forward,state.z));
  return portraitLookAt(eye,add(eye,mul(lookDirection(basis,state.yaw,state.pitch),2)),up);
}

export function stepWalker(state,input,elapsed,field) {
  const body=field.body,dt=clamp(elapsed,0,.05);
  if(input.jump&&state.grounded){state.vy=body.jumpSpeed_m_s;state.grounded=false;}
  const forward=(input.forward||0),side=(input.side||0),length=Math.hypot(forward,side),speed=input.run?body.runSpeed_m_s:body.walkSpeed_m_s;
  const desiredX=length?(Math.sin(state.yaw)*forward+Math.cos(state.yaw)*side)/Math.max(1,length)*speed:0;
  const desiredZ=length?(Math.cos(state.yaw)*forward-Math.sin(state.yaw)*side)/Math.max(1,length)*speed:0;
  const acceleration=14;
  state.vx+=clamp(desiredX-state.vx,-acceleration*dt,acceleration*dt);state.vz+=clamp(desiredZ-state.vz,-acceleration*dt,acceleration*dt);
  const steps=Math.max(1,Math.ceil(dt/.01)),step=dt/steps;
  for(let i=0;i<steps;i++) {
    const previous=state.feet;state.vy-=body.gravity_m_s2*step;let next=previous+state.vy*step;state.grounded=false;
    const spans=field.intervals(state.x,state.z,body.radius_m);
    if(state.vy<=0) {
      let support=0;
      for(const [,top]of spans)if(previous>=top-.006&&next<=top)support=Math.max(support,top);
      if(next<=support){next=support;state.vy=0;state.grounded=true;}
    } else {
      for(const [bottom]of spans)if(previous+body.height_m<=bottom+.005&&next+body.height_m>bottom){next=Math.min(next,bottom-body.height_m);state.vy=0;}
    }
    state.feet=Math.max(0,next);
    const x=state.x+state.vx*step;
    if(!field.blocked(x,state.z,state.feet,body.radius_m,body.height_m))state.x=x;else state.vx=0;
    const z=state.z+state.vz*step;
    if(!field.blocked(state.x,z,state.feet,body.radius_m,body.height_m))state.z=z;else state.vz=0;
  }
  return state;
}

export function createNavigation({canvas,frame,navigation,getCamera,setCamera,getMode,onMode,requestDraw,getSource,onJumpState}) {
  const field=navigation?collisionField(navigation):null,keys=new Set();let walking=null,drag=null,animation=null,lastTime=null,jump=false;
  canvas.tabIndex=0;canvas.setAttribute('aria-label','3D scene navigation');
  const editing=()=>['INPUT','SELECT','TEXTAREA','BUTTON','SUMMARY'].includes(document.activeElement?.tagName);
  function clear(){keys.clear();jump=false;}
  function stop(){clear();walking=null;lastTime=null;if(animation!==null)cancelAnimationFrame(animation);animation=null;onJumpState?.(false);}
  function updateCamera(){setCamera(walkerCamera(walking,field));canvas.dataset.walkPosition=[walking.x,walking.feet,walking.z].join(',');canvas.dataset.walkGrounded=String(walking.grounded);}
  function tick(now) {
    if(!walking||getMode()!=='walk'){stop();return;}
    const before=JSON.stringify(walking),dt=lastTime===null?0:Math.min(.05,(now-lastTime)/1000);lastTime=now;
    stepWalker(walking,{forward:Number(keys.has('KeyW'))-Number(keys.has('KeyS')),side:Number(keys.has('KeyD'))-Number(keys.has('KeyA')),run:keys.has('ShiftLeft')||keys.has('ShiftRight'),jump},dt,field);jump=false;
    if(JSON.stringify(walking)!==before){updateCamera();requestDraw();}
    animation=requestAnimationFrame(tick);
  }
  function start() {
    if(!field)return false;
    const source=getSource(),pose=source?.pose,camera=getCamera(),position=pose?pose.slice(0,3).map(row=>row[3]):camera.t,forward=pose?pose.slice(0,3).map(row=>row[2]):column(camera.R,2);
    walking=createWalker(field,position,forward);if(!walking)return false;
    clear();lastTime=null;onMode('walk');onJumpState?.(true);updateCamera();canvas.focus({preventScroll:true});requestDraw();if(animation===null)animation=requestAnimationFrame(tick);return true;
  }
  canvas.addEventListener('pointerdown',event=>{
    if(event.button>2)return;event.preventDefault();canvas.focus({preventScroll:true});canvas.setPointerCapture(event.pointerId);
    if(getMode()!=='walk'){stop();onMode('orbit');setCamera(orbitUpright(getCamera(),frame,0,0));}
    drag={x:event.clientX,y:event.clientY,pan:event.button!==0||event.shiftKey};requestDraw();
  });
  canvas.addEventListener('pointermove',event=>{
    if(!drag)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;drag.x=event.clientX;drag.y=event.clientY;
    if(walking&&getMode()==='walk'){walking.yaw+=dx*.0035;walking.pitch=clamp(walking.pitch-dy*.0035,-PITCH_LIMIT,PITCH_LIMIT);updateCamera();}
    else if(drag.pan)setCamera(panUpright(getCamera(),frame,dx,dy,canvas.clientHeight,frame.tanV));
    else setCamera(orbitUpright(getCamera(),frame,dx,dy));
    requestDraw();
  });
  for(const event of ['pointerup','pointercancel','lostpointercapture'])canvas.addEventListener(event,()=>drag=null);
  canvas.addEventListener('wheel',event=>{
    event.preventDefault();if(getMode()==='walk')return;stop();onMode('orbit');const camera=orbitUpright(getCamera(),frame,0,0),offset=sub(camera.t,camera.target),distance=clamp(Math.hypot(...offset)*Math.exp(clamp(event.deltaY*.001,-.3,.3)),.25,40);setCamera(portraitLookAt(add(camera.target,mul(normalize(offset),distance)),camera.target,frame.up));requestDraw();
  },{passive:false});
  canvas.addEventListener('contextmenu',event=>event.preventDefault());
  window.addEventListener('keydown',event=>{
    if(editing())return;
    if(getMode()==='walk') {
      if(['KeyW','KeyA','KeyS','KeyD','ShiftLeft','ShiftRight','Space'].includes(event.code)){event.preventDefault();if(event.code==='Space'&&!event.repeat&&!keys.has('Space'))jump=true;keys.add(event.code);}
      else if(event.code==='Escape'){event.preventDefault();stop();onMode('orbit');requestDraw();}
      else if(event.code==='KeyR'){event.preventDefault();start();}
    } else if(event.code==='Space'){event.preventDefault();document.getElementById('play').click();}
  });
  window.addEventListener('keyup',event=>keys.delete(event.code));window.addEventListener('blur',clear);canvas.addEventListener('blur',clear);document.addEventListener('visibilitychange',()=>{if(document.hidden){clear();lastTime=null;}});
  return {start,stop,jump:()=>{if(walking){jump=true;canvas.focus({preventScroll:true});}},get walking(){return walking;},available:!!field};
}
