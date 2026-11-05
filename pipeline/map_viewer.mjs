import {cameraCenter,cameraFrustum,trajectorySegments,mapFrame,mapCamera,floorGrid,add,mul} from './map_geometry.mjs';
import {createNavigation,orbitUpright} from './map_navigation.mjs';

function overlayProgram(gl) {
  const compile=(type,source)=>{const shader=gl.createShader(type);gl.shaderSource(shader,source);gl.compileShader(shader);if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(shader));return shader;};
  const p=gl.createProgram();
  gl.attachShader(p,compile(gl.VERTEX_SHADER,`#version 300 es
    precision highp float;in vec3 position;uniform mat4 camera;uniform vec4 intrinsics;uniform vec2 size;uniform float pointSize;
    void main(){vec3 c=(camera*vec4(position,1.)).xyz;float n=.03,f=40.;
    gl_Position=vec4((1.-2.*intrinsics.w/size.y)*c.z-2.*intrinsics.y/size.y*c.y,(1.-2.*intrinsics.z/size.x)*c.z-2.*intrinsics.x/size.x*c.x,(f+n)/(f-n)*c.z-2.*f*n/(f-n),c.z);gl_PointSize=pointSize;}`));
  gl.attachShader(p,compile(gl.FRAGMENT_SHADER,`#version 300 es
    precision highp float;uniform vec3 color;uniform bool point;out vec4 pixel;
    void main(){if(point&&length(gl_PointCoord-vec2(.5))>.5)discard;pixel=vec4(color,1.);}`));
  gl.linkProgram(p);if(!gl.getProgramParameter(p,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(p));
  return {program:p,uniforms:Object.fromEntries(['camera','intrinsics','size','pointSize','color','point'].map(k=>[k,gl.getUniformLocation(p,k)]))};
}

export function createMapExperience({data,gl,canvas,getCamera,setCamera,requestDraw}) {
  const sourcePositions=new Float32Array(data.positions.buffer,data.positions.byteOffset,data.positions.byteLength/4);
  const frame=mapFrame(sourcePositions,data.estimated_up),trajectory=trajectorySegments(data.timeline?.frames||[]);
  frame.floorHeight=data.navigation?.floorHeight_m;frame.tanV=data.width/(2*data.K[0][0]);
  const {program,uniforms:u}=overlayProgram(gl);const makeBuffer=(positions,dynamic=false)=>{
    const vao=gl.createVertexArray();gl.bindVertexArray(vao);const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,positions,dynamic?gl.DYNAMIC_DRAW:gl.STATIC_DRAW);
    const loc=gl.getAttribLocation(program,'position');gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,3,gl.FLOAT,false,0,0);return {vao,buffer,count:positions.length/3};
  };
  const grid=makeBuffer(floorGrid(frame)),path=makeBuffer(trajectory.positions),device=makeBuffer(new Float32Array(),true),point=makeBuffer(new Float32Array(),true);
  let mode='map',source=null,markerFrame=null,markerUpdates=0,navigationControls=null,walkingOverlays=null;
  const follow=document.getElementById('follow'),cutaway=document.getElementById('cutaway');
  const state=document.getElementById('deviceState');
  function modeUI(next) {
    if(next==='walk'&&mode!=='walk') {
      walkingOverlays=['showPath','showFrustum'].map(id=>document.getElementById(id).checked);
      for(const id of ['showPath','showFrustum'])document.getElementById(id).checked=false;
    } else if(mode==='walk'&&next!=='walk'&&walkingOverlays) {
      ['showPath','showFrustum'].forEach((id,i)=>document.getElementById(id).checked=walkingOverlays[i]);walkingOverlays=null;
    }
    mode=next;document.body.dataset.mapView=next;document.body.classList.toggle('device-view',next==='device');document.body.classList.toggle('walk-view',next==='walk');
    follow.checked=next==='device';
    for(const [id,value]of [['overview','map'],['topView','top'],['reset','device'],['walk','walk']])document.getElementById(id).setAttribute('aria-pressed',String(next===value));
    document.getElementById('mode').textContent=next==='device'?'Device view · recorded tilt':next==='top'?'Top view · estimated up':next==='map'?'Room map':next==='walk'?'Walk · estimated floor':'Orbit map';
    document.getElementById('navigationHelp').textContent=next==='walk'?'WASD move · Drag to look · Shift faster · Space jump · R reset · Esc exit':'Drag to orbit · Shift-drag to pan · Scroll to zoom · Horizon stays level';
    document.getElementById('jump').hidden=next!=='walk';
  }
  function show(next,draw=true) {
    navigationControls?.stop();
    modeUI(next);cutaway.checked=next!=='device';
    if(next==='device'&&source?.pose) {
      const T=source.pose,R=T.slice(0,3).map(r=>r.slice(0,3)),t=cameraCenter(T);
      setCamera({R,t,target:add(t,mul(R.map(r=>r[2]),2))});
    } else {
      const rect=canvas.parentElement.getBoundingClientRect();
      setCamera(mapCamera(frame,data.K,data.width,rect.width/Math.max(rect.height,1),next));
    }
    if(draw)requestDraw();
  }
  document.getElementById('overview').onclick=()=>show('map');
  document.getElementById('topView').onclick=()=>show('top');
  document.getElementById('reset').onclick=()=>show('device');
  for(const id of ['showPath','showFrustum','showGrid'])document.getElementById(id).onchange=requestDraw;
  document.getElementById('focusLayout').onclick=()=>{const expanded=document.body.classList.toggle('map-expanded');document.getElementById('focusLayout').textContent=expanded?'Show previews':'Expand map';requestDraw();};
  window.addEventListener('resize',()=>{if(mode==='map'||mode==='top')show(mode);});
  navigationControls=createNavigation({canvas,frame,navigation:data.navigation,getCamera,setCamera,getMode:()=>mode,
    onMode:next=>{modeUI(next);if(next==='walk')cutaway.checked=false;},requestDraw,getSource:()=>source});
  const walk=document.getElementById('walk');walk.disabled=!navigationControls.available;
  walk.onclick=()=>{if(!navigationControls.start())state.textContent='No clear walking start was found. Choose another recording time.';};
  document.getElementById('jump').onclick=()=>navigationControls.jump();
  const inspect=document.getElementById('inspectHand'),inspectHand=inspect.onclick;
  inspect.onclick=()=>{navigationControls.stop();modeUI('orbit');inspectHand?.();setCamera(orbitUpright(getCamera(),frame,0,0));requestDraw();};
  show('map',false);
  return {
    get mode(){return mode;},
    showDevice(){show('device');},
    setSource(f) {
      source=f;
      if(markerFrame===f.id)return;
      markerFrame=f.id;markerUpdates++;
      const center=cameraCenter(f.pose),frustum=cameraFrustum(f.pose,data.K,data.width,data.height);
      gl.bindBuffer(gl.ARRAY_BUFFER,device.buffer);gl.bufferData(gl.ARRAY_BUFFER,frustum,gl.DYNAMIC_DRAW);device.count=frustum.length/3;
      gl.bindBuffer(gl.ARRAY_BUFFER,point.buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(center||[]),gl.DYNAMIC_DRAW);point.count=center?1:0;
      canvas.dataset.cameraFrame=String(f.id);canvas.dataset.cameraPosition=center?.join(',')||'';canvas.dataset.markerUpdates=String(markerUpdates);
      state.textContent=center?'Estimated device pose':'Camera pose unavailable';
    },
    cutPlane() {const h=(frame.floorHeight??frame.bounds[1][0])+(mode==='top'?1.25:1.95);return [...frame.up,-h];},
    draw(camera,projection) {
      const view=getCamera(),position=view.t.join(','),orientation=view.R.flat().join(',');
      if(canvas.dataset.mapPosition!==position)canvas.dataset.mapPosition=position;
      if(canvas.dataset.mapOrientation!==orientation)canvas.dataset.mapOrientation=orientation;
      gl.useProgram(program);gl.uniformMatrix4fv(u.camera,false,camera);gl.uniform4fv(u.intrinsics,projection.intrinsics);gl.uniform2fv(u.size,projection.size);gl.uniform1i(u.point,0);gl.uniform1f(u.pointSize,12*Math.min(devicePixelRatio||1,2));
      if(document.getElementById('showGrid').checked&&mode!=='device'){gl.enable(gl.DEPTH_TEST);gl.depthMask(false);gl.uniform3f(u.color,.73,.76,.72);gl.bindVertexArray(grid.vao);gl.drawArrays(gl.LINES,0,grid.count);gl.depthMask(true);}
      gl.disable(gl.DEPTH_TEST);
      if(document.getElementById('showPath').checked){gl.bindVertexArray(path.vao);gl.uniform3f(u.color,.60,.65,.66);gl.drawArrays(gl.LINES,0,path.count);gl.uniform3f(u.color,.15,.40,.58);gl.drawArrays(gl.LINES,0,trajectory.completed.get(source?.id)||0);}
      if(source?.pose&&mode!=='device') {
        gl.uniform3f(u.color,.91,.39,.13);
        if(document.getElementById('showFrustum').checked){gl.bindVertexArray(device.vao);gl.drawArrays(gl.LINES,0,device.count);}
        gl.bindVertexArray(point.vao);gl.uniform1i(u.point,1);gl.drawArrays(gl.POINTS,0,point.count);gl.uniform1i(u.point,0);
      }
      gl.enable(gl.DEPTH_TEST);
    }
  };
}
