// Pure geometry for the map view. Source poses remain world_from_camera in metres.
export const add = (a,b) => a.map((v,i)=>v+b[i]);
export const sub = (a,b) => a.map((v,i)=>v-b[i]);
export const mul = (a,s) => a.map(v=>v*s);
export const dot = (a,b) => a.reduce((s,v,i)=>s+v*b[i],0);
export const cross = (a,b) => [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
export const normalize = a => mul(a,1/Math.max(Math.hypot(...a),1e-12));

export function cameraCenter(pose) {
  return pose ? [pose[0][3],pose[1][3],pose[2][3]] : null;
}

export function cameraFrustum(pose,K,width,height,depth=.3) {
  if(!pose)return new Float32Array();
  const origin=cameraCenter(pose);
  const world=p=>origin.map((v,i)=>v+pose[i][0]*p[0]+pose[i][1]*p[1]+pose[i][2]*p[2]);
  const corners=[[-.5,-.5],[width-.5,-.5],[width-.5,height-.5],[-.5,height-.5]]
    .map(([u,v])=>world([(u-K[0][2])/K[0][0]*depth,(v-K[1][2])/K[1][1]*depth,depth]));
  const lines=[];
  for(let i=0;i<4;i++)lines.push(...origin,...corners[i],...corners[i],...corners[(i+1)%4]);
  lines.push(...origin,...world([0,0,depth*1.65]));
  return new Float32Array(lines);
}

export function trajectorySegments(frames,maxGap=.2) {
  const vertices=[],completed=new Map();
  for(let i=0;i<frames.length;i++) {
    const f=frames[i],previous=frames[i-1];
    if(f.pose&&previous?.pose&&f.seconds>previous.seconds&&f.seconds-previous.seconds<=maxGap)
      vertices.push(...cameraCenter(previous.pose),...cameraCenter(f.pose));
    completed.set(f.id,vertices.length/3);
  }
  return {positions:new Float32Array(vertices),completed};
}

// The capture viewer displays a clockwise portrait image: screen up is -camera X.
export function portraitLookAt(eye,target,screenUp) {
  const forward=normalize(sub(target,eye));
  let up=sub(screenUp,mul(forward,dot(screenUp,forward)));
  if(Math.hypot(...up)<1e-8) {
    const hint=Math.abs(forward[1])<.9?[0,1,0]:[1,0,0];
    up=sub(hint,mul(forward,dot(hint,forward)));
  }
  const x=mul(normalize(up),-1),y=normalize(cross(forward,x));
  return {R:[0,1,2].map(i=>[x[i],y[i],forward[i]]),t:eye.slice(),target:target.slice()};
}

export function mapFrame(positions,estimatedUp=[-1,0,0]) {
  const up=normalize(estimatedUp);
  let forward=sub([0,0,1],mul(up,up[2]));
  if(Math.hypot(...forward)<.1)forward=sub([0,1,0],mul(up,up[1]));
  forward=normalize(forward);const right=normalize(cross(forward,up)),axes=[right,up,forward];
  const samples=[[],[],[]],points=[],stride=Math.max(1,Math.floor(positions.length/3/50000));
  for(let i=0;i<positions.length/3;i+=stride) {
    const p=Array.from(positions.subarray(i*3,i*3+3));
    if(!p.every(Number.isFinite))continue;
    points.push(p);
    axes.forEach((axis,j)=>samples[j].push(dot(axis,p)));
  }
  if(!samples[0].length)throw Error('No finite room positions for map framing');
  const bounds=samples.map(values=>{values.sort((a,b)=>a-b);return [values[Math.floor((values.length-1)*.01)],values[Math.ceil((values.length-1)*.99)]];});
  const toWorld=p=>axes[0].map((_,i)=>axes.reduce((v,a,j)=>v+a[i]*p[j],0));
  return {up,forward,right,axes,bounds,toWorld,points};
}

export function mapCamera(frame,K,width,aspect,mode='map') {
  const b=frame.bounds,mid=b.map(([lo,hi])=>(lo+hi)/2);
  const floor=frame.floorHeight??b[1][0];
  mid[1]=floor+Math.min(.9,(b[1][1]-floor)/2);
  const target=frame.toWorld(mid),top=mode==='top';
  const offset=top?frame.up:normalize(add(add(mul(frame.up,1.2),mul(frame.forward,-1)),mul(frame.right,.65)));
  const basis=portraitLookAt(add(target,offset),target,top?frame.forward:frame.up);
  const cols=[0,1,2].map(i=>basis.R.map(row=>row[i]));
  const tanV=width/(2*K[0][0]),tanH=tanV*Math.max(.2,aspect);
  const visible=frame.points.filter(p=>dot(p,frame.up)<=floor+(top?1.25:1.95));
  const selected=visible.length?visible:frame.points;
  const projections=selected.map(p=>{const q=sub(p,target);return [dot(q,cols[0]),dot(q,cols[1]),dot(q,cols[2])];});
  const quantile=(values,q)=>{values.sort((a,b)=>a-b);return values[Math.floor((values.length-1)*q)];};
  const requirements=projections.map(([x,y,z])=>Math.max(Math.abs(x)/tanV-z,Math.abs(y)/tanH-z));
  const distance=Math.max(.8,quantile(requirements,.985))*1.1;
  const screen=projections.filter(p=>p[2]+distance>.1).map(([x,y,z])=>[-y/((distance+z)*tanH),-x/((distance+z)*tanV)]);
  const center=screen.length?[0,1].map(i=>(quantile(screen.map(p=>p[i]),.015)+quantile(screen.map(p=>p[i]),.985))/2):[0,0];
  const pan=add(mul(cols[1],-center[0]*distance*tanH),mul(cols[0],-center[1]*distance*tanV));
  const centered=add(target,pan);
  return portraitLookAt(add(centered,mul(offset,distance)),centered,top?frame.forward:frame.up);
}

export function floorGrid(frame,spacing=.5) {
  const [x,y,z]=frame.bounds,height=(frame.floorHeight??y[0])-.025,padding=.5,lines=[];
  const loX=Math.floor((x[0]-padding)/spacing)*spacing,hiX=Math.ceil((x[1]+padding)/spacing)*spacing;
  const loZ=Math.floor((z[0]-padding)/spacing)*spacing,hiZ=Math.ceil((z[1]+padding)/spacing)*spacing;
  for(let a=loX;a<=hiX+1e-8;a+=spacing)lines.push(...frame.toWorld([a,height,loZ]),...frame.toWorld([a,height,hiZ]));
  for(let a=loZ;a<=hiZ+1e-8;a+=spacing)lines.push(...frame.toWorld([loX,height,a]),...frame.toWorld([hiX,height,a]));
  return new Float32Array(lines);
}
