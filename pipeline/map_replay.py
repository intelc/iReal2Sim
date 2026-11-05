"""Package a map-first replay without changing capture data or source viewers."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from .reference_replay import geometry_scene, relative
from .rgbd import output_directory, write_json


STYLE = '''
*{box-sizing:border-box}[hidden]{display:none!important}
body{margin:0;background:#e9e8e3;color:#252b27;font:14px/1.4 system-ui,sans-serif}
main{padding:18px 24px 12px;min-height:100dvh}header{display:flex;justify-content:space-between;align-items:center;gap:20px}
h1{font-size:23px;letter-spacing:-.6px;font-weight:580;margin:0}header p{margin:4px 0 0;font-size:12px;color:#626b62}.badge{font:12px ui-monospace,monospace;color:#667066}
nav{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:13px 0}button,select,summary{font:inherit;color:inherit;border:1px solid #b9c0b5;background:#f5f5ee;border-radius:4px;padding:8px 10px}button,summary{cursor:pointer}button:hover,summary:hover{background:#fff}button[aria-pressed="true"]{background:#d3dfd3;border-color:#6f8772}select{max-width:300px}.spacer{flex:1}
details{position:relative}summary{list-style:none}details>section{position:absolute;right:0;top:42px;z-index:5;background:#f7f7f0;border:1px solid #bac1b6;border-radius:6px;box-shadow:0 8px 30px #26372b20;width:290px;padding:14px;display:grid;gap:10px}label{display:flex;gap:7px;align-items:center}input[type="checkbox"]{accent-color:#507253}details select{max-width:240px}.jump{display:flex;gap:6px;align-items:center}.jump select{min-width:0;max-width:178px}
.views{display:grid;grid-template-columns:minmax(0,1fr) clamp(220px,23vw,360px);grid-template-rows:minmax(0,1fr) minmax(0,1fr);gap:14px;height:calc(100dvh - 224px);min-height:390px}
figure{margin:0;min-width:0;min-height:0;display:flex;flex-direction:column}figcaption{display:flex;justify-content:space-between;gap:6px;font-size:12px;color:#4c584d;padding:0 0 6px;min-height:23px}
.views>figure:first-child{grid-column:2;grid-row:1}.views>#ultraFigure{grid-column:2;grid-row:2}.views>#mapFigure{grid-column:1;grid-row:1/span 2}.views:not(.has-ultra)>figure:first-child{grid-row:1/span 2}
.viewport{width:100%;flex:1;min-height:0;position:relative;overflow:hidden;border:1px solid #c4cbc0;background:#dcdfd7}.map-viewport{background:#eee}.viewport>canvas,.viewport>video,.viewport>img{display:block;width:100%;height:100%;object-fit:contain}.viewport>#mesh{touch-action:none;cursor:grab}.viewport>#mesh:active{cursor:grabbing}#maskOverlay{position:absolute;inset:0;pointer-events:none}
.map-legend{position:absolute;bottom:12px;left:12px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;background:#f7f8f0e8;border:1px solid #d4dacd;border-radius:4px;padding:7px 10px;font-size:11px;pointer-events:none;color:#536052}.map-legend span{display:flex;align-items:center;gap:5px}.key{width:17px;height:2px;background:#27668f;display:inline-block}.key.device{width:8px;height:8px;border-radius:50%;background:#e86322}
#timeline{display:flex;gap:12px;align-items:center;margin:12px 0 6px}#scrub{flex:1;min-width:80px;accent-color:#4e7152}#status{font:11px ui-monospace,monospace;color:#5c685c}#trackStatus{font-size:11px;margin:5px 0;color:#5b6658}.note{font-size:11px;color:#667060;line-height:1.4;margin:6px 0 0}#error{color:#a32919;white-space:pre-wrap}#pathBlock{display:none!important}
.map-expanded .views{grid-template-columns:1fr;grid-template-rows:1fr}.map-expanded .views>figure:not(#mapFigure){display:none}.map-expanded .views>#mapFigure{grid-column:1;grid-row:1}
.device-view #mapFigure{align-items:center;background:#dde1d7}.device-view #mapFigure figcaption{width:100%}.device-view #mapFigure .viewport{flex:0 1 auto;height:calc(100% - 23px);width:auto;max-width:100%;aspect-ratio:9/16}.device-view .map-legend{display:none}
.walk-view .viewport>#mesh{cursor:crosshair}.walk-view .map-legend{display:none}.viewport>#mesh:focus-visible{outline:2px solid #547c60;outline-offset:-3px}
@media(max-width:850px){main{padding:12px}header .badge{display:none}h1{font-size:21px}nav{gap:6px}button,select,summary{padding:7px 8px;font-size:12px}.views{grid-template-columns:1fr 1fr;grid-template-rows:minmax(360px,57dvh) 220px;height:auto}.views>#mapFigure{grid-column:1/span 2;grid-row:1}.views>figure:first-child{grid-column:1;grid-row:2}.views>#ultraFigure{grid-column:2;grid-row:2}.views:not(.has-ultra)>figure:first-child{grid-column:1/span 2;grid-row:2}.map-expanded .views{grid-template-rows:calc(100dvh - 220px)}.map-legend{font-size:10px;gap:9px;bottom:8px;left:8px}#timeline{gap:8px}#status{max-width:150px;font-size:10px}}
'''

BODY = '''<body><main>
<header><div><h1>Scene + camera map</h1><p id="navigationHelp">Drag to orbit · Shift-drag to pan · Scroll to zoom · Horizon stays level</p></div><span class="badge" id="deviceState">Estimated device pose</span></header>
<nav><button id="overview" aria-pressed="true">Room map</button><button id="topView" aria-pressed="false">Top view</button><button id="walk" aria-pressed="false">Walk</button><button id="jump" hidden>Jump ␣</button><button id="reset" aria-pressed="false">Device view</button><label id="sceneControl" hidden><select id="sceneModel" aria-label="Scene representation"></select></label><span class="spacer"></span><button id="focusLayout">Expand map</button>
<details><summary>Display</summary><section>
<label><input type="checkbox" id="showPath" checked> Camera path</label><label><input type="checkbox" id="showFrustum" checked> Main camera frustum</label><label><input type="checkbox" id="showGrid" checked> Reference grid</label><label><input type="checkbox" id="cutaway" checked> Cutaway room</label>
<label id="textureControl" hidden><input type="checkbox" id="useTexture" checked> RGB texture atlas</label><label id="ultraControl" hidden><input id="showUltra" type="checkbox" checked> Ultra Wide preview</label>
<label id="maskControl" hidden><input id="showMask" type="checkbox"> Masks</label><label id="trackControl" hidden><input id="showTracks" type="checkbox" checked> Interaction estimates</label><label id="weakControl" hidden><input id="includeWeak" type="checkbox"> Include weak predictions</label><label id="supportControl" hidden><input id="supportedHand" type="checkbox"> Depth-supported hand only</label><button id="inspectHand" hidden>Inspect hand</button>
<div class="jump"><button id="prev" aria-label="Previous reviewed view">←</button><select id="views" aria-label="Recorded camera view"></select><button id="next" aria-label="Next reviewed view">→</button></div>
</section></details></nav>
<div class="views"><figure><figcaption><span>Main camera</span><span id="time"></span></figcaption><div class="viewport"><img id="rgb" alt="Original recorded RGB frame"><video id="sourceVideo" hidden playsinline muted preload="auto"></video><canvas id="mainPreview" hidden aria-label="Main camera preview"></canvas><canvas id="maskOverlay" hidden></canvas></div></figure>
<figure id="ultraFigure" hidden><figcaption><span>Ultra Wide</span><span id="ultraOffset"></span></figcaption><div class="viewport"><video id="ultraVideo" playsinline muted preload="auto"></video><canvas id="ultraPreview" hidden aria-label="Ultra Wide camera preview"></canvas></div></figure>
<figure id="mapFigure"><figcaption><span>Room reconstruction</span><span id="mode">Room map</span></figcaption><div class="viewport map-viewport"><canvas id="mesh" aria-label="Interactive reconstructed map"></canvas><div class="map-legend"><span><i class="key device"></i>Recorded camera</span><span><i class="key"></i>Estimated path</span><span>0.5 m grid · estimated frame</span></div></div></figure></div>
<div id="timeline" hidden><button id="play">Play</button><input id="scrub" aria-label="Recording time" type="range" step="0.001"><span id="status"></span><label hidden><input id="follow" type="checkbox"> Follow camera</label></div>
<div id="pathBlock" hidden><canvas id="trajectory" width="360" height="152"></canvas></div>
<p id="trackStatus" hidden></p><p class="note" id="note">Camera positions and room geometry are estimates.</p><div id="error" role="alert"></div>
</main>'''


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'Viewer source anchor changed: {old[:95]}')
    return text.replace(old, new, 1)


def map_template(source):
    script=source.split('<script type="module">',1)[1].split('</script>',1)[0]
    script=replace_once(script,'const data=__PAYLOAD__;','const data=__PAYLOAD__;\nconst {createMapExperience}=await import("./map_viewer.mjs");')
    script=replace_once(script,'let R,t,target,cutAnchor=null,index=0;','let R,t,target,cutAnchor=null,index=0;let mapExperience=null;')
    script=replace_once(script,'const rect=canvas.getBoundingClientRect(),dpr=', 'const rect=canvas.getBoundingClientRect();if(rect.width<1||rect.height<1)return;const dpr=')
    script=replace_once(script,"gl.uniform4f(cutPlaneLoc,...plane,-dot(plane,cutAnchor||target));", "gl.uniform4f(cutPlaneLoc,...(mapExperience?.cutPlane()||[...plane,-dot(plane,cutAnchor||target)]));")
    script=replace_once(script,' gl.bindVertexArray(staticVao);\n if(interactionMeshes.length){', ''' mapExperience?.draw(m,{intrinsics:[data.K[0][0],data.K[1][1],data.K[0][2],virtualHeight/2+data.K[1][2]-data.height/2],size:[data.width,virtualHeight]});
 gl.useProgram(program);gl.bindVertexArray(staticVao);
 if(interactionMeshes.length){''')
    begin=script.index('function choose(i){');end=script.index('\nselect.onchange=',begin)
    script=script[:begin]+'''function choose(i){index=(i+data.views.length)%data.views.length;select.value=index;const v=data.views[index];currentSourceFrame=v.id;mapExperience.setSource(v);if(mapExperience.mode==='device'){R=v.pose.slice(0,3).map(r=>r.slice(0,3));t=v.pose.slice(0,3).map(r=>r[3]);target=add(t,mul(column(R,2),v.focus));}document.getElementById('rgb').src=v.rgb;document.getElementById('time').textContent=v.seconds.toFixed(2)+'s';draw();}'''+script[end:]
    # Override the legacy view handlers after all of them have been installed.
    anchor='// Start from a representative tracked view; all views remain accessible, including failures.'
    script=replace_once(script,anchor,'''mapExperience=createMapExperience({data,gl,canvas,getCamera:()=>({R,t,target}),setCamera:camera=>{R=camera.R;t=camera.t;target=camera.target;},requestDraw:()=>draw()});
'''+anchor)
    script=replace_once(script,"follow.checked=true;const f=frames[nearest(data.views[index].seconds,'seconds')];", "follow.checked=mapExperience.mode==='device';const f=frames[nearest(data.views[index].seconds,'seconds')];")
    # The timeline installs another reset handler; keep the explicit device-mode action.
    script=replace_once(script,"document.getElementById('reset').onclick=()=>{follow.checked=true;document.getElementById('cutaway').checked=false;update(video.currentTime);};", "document.getElementById('reset').onclick=()=>{mapExperience.showDevice();update(video.currentTime);};")
    script=replace_once(script,"currentSourceFrame=f.id;scrub.value=f.seconds;", "mapExperience.setSource(f);currentSourceFrame=f.id;scrub.value=f.seconds;")
    script=replace_once(script,"if(!follow.checked&&interactionMeshes.length)draw();", "if(!follow.checked)draw();")
    # Map controls own all interactive camera motion; do not retain the rolling legacy orbit.
    begin=script.index('let dragging=null;')
    anchor="window.addEventListener('resize',draw);"
    end=script.index(anchor,begin)+len(anchor)
    script=script[:begin]+anchor+script[end:]
    # Keep the hand inspection button from being superseded by follow mode.
    script=replace_once(script,"if(data.provenance_note)document.getElementById('note').textContent+=' '+data.provenance_note;", "if(data.provenance_note)document.getElementById('note').textContent+=' '+data.provenance_note;document.getElementById('note').textContent+=' Walking uses an estimated floor and approximate obstacles. Device view preserves the recorded camera tilt. Camera path uses the original estimated poses, including any inferred motion bridge. The grid is a display guide, not surveyed ground.';")
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>iPhone RSA · scene and camera map</title><style>'+STYLE+'</style></head>'+BODY+'<script type="module">'+script+'</script></body></html>'


def write_map_ui(output):
    template_path=Path(__file__).with_name('viewer.html')
    template=map_template(template_path.read_text())
    (output/'viewer.html').write_text(template.replace('__PAYLOAD__',"await (await fetch('scene.json')).json()"))
    names=['map_viewer.mjs','map_geometry.mjs','map_navigation.mjs']
    for name in names:shutil.copyfile(Path(__file__).with_name(name),output/name)
    return dict(source_template_sha256=hashlib.sha256(template_path.read_bytes()).hexdigest(),implementation_sha256={name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in ['map_replay.py',*names]})


def read_navigation(path,source,scene):
    field=json.loads(path.read_text())
    if field['schema']!='iphone-rsa.walk-navigation/1':raise ValueError('Unknown navigation field')
    for name,key in [('positions','source_positions_sha256'),('indices','source_indices_sha256')]:
        sha=hashlib.sha256((source/scene[name]['url']).resolve().read_bytes()).hexdigest()
        if field[key]!=sha:raise ValueError('Navigation field belongs to different room geometry')
    return field


def build(source, output, navigation=None, refresh=False):
    source=source.resolve();original=json.loads((source/'scene.json').read_text())
    report=json.loads((source/'replay.json').read_text())
    cfg=json.loads((Path(report['dataset'])/'calibration.json').read_text())
    if refresh:
        output=output.resolve();record=json.loads((output/'map.json').read_text())
        if record['schema']!='iphone-rsa.map-replay/1' or Path(record['source_replay']).resolve()!=source:raise ValueError('Refresh requires the matching derived map replay')
        for protected in [source,Path(report['dataset']).resolve(),Path(cfg['source']).resolve()]:
            if output==protected or output.is_relative_to(protected):raise ValueError('Cannot refresh source data')
        data=json.loads((output/'scene.json').read_text())
        if data['timeline']['frames']!=original['timeline']['frames']:raise ValueError('Source trajectory/timestamps differ')
        if navigation:data['navigation']=read_navigation(navigation,source,original)
        write_json(output/'scene.json',data);record.update(write_map_ui(output));record['navigation']='Fixed-up orbit and virtual walking with estimated floor/obstacle columns';record['poses_and_timestamps_unchanged']=True;write_json(output/'map.json',record);print(output/'viewer.html');return
    output=output_directory(source,output,Path(report['dataset']),Path(report['run']),Path(cfg['source']))
    data=geometry_scene(source/'scene.json',output)
    for parent in ['timeline','paired_preview','ultra_context']:
        if data.get(parent,{}).get('video'):data[parent]['video']=relative(source/data[parent]['video'],output)
    if data['timeline'].get('mask_root'):data['timeline']['mask_root']=relative(source/data['timeline']['mask_root'],output)+'/'
    if data.get('tracks'):data['tracks']=relative(source/data['tracks'],output)
    for option in data.get('scene_options',[]):option['url']=relative(source/option['url'],output)
    if data['timeline']['frames']!=original['timeline']['frames']:
        raise ValueError('Map packaging changed source timing or camera poses')
    if navigation:data['navigation']=read_navigation(navigation,source,original)
    write_json(output/'scene.json',data)
    fingerprints=write_map_ui(output)
    write_json(output/'map.json',dict(schema='iphone-rsa.map-replay/1',source_replay=str(source),status='complete',
               default_view='room_map',source_frame_count=len(data['timeline']['frames']),
               poses_and_timestamps_unchanged=True,world_frame=data.get('world_frame','Initial Main optical frame'),
               camera_glyph='Recorded calibrated Main frustum at the original estimated camera pose',
               map_orientation='Estimated room up; not calibrated gravity or compass heading',
               **fingerprints))
    print(output/'viewer.html')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--navigation',type=Path);p.add_argument('--refresh',action='store_true')
    a=p.parse_args();build(a.source,a.output,a.navigation,a.refresh)


if __name__=='__main__':main()
