"""Portable capture-to-dashboard entry point. No sample-room parameters or weights."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import sys


def save(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');temp.replace(path)


def doctor():
    result={'python':sys.version.split()[0],'tools':{},'api_key_configured':bool(os.environ.get('OPENAI_API_KEY'))}
    for name in ('ffmpeg','ffprobe','cmake','rtabmap'):
        result['tools'][name]=shutil.which(name)
    result['ready']=all(result['tools'].values())
    result['install']='On macOS: brew install ffmpeg cmake rtabmap. Then uv sync --frozen --python 3.12.'
    return result


def check_paths(source, output):
    source=Path(source).expanduser().resolve();output=Path(output).expanduser().resolve()
    if not (source/'manifest.json').is_file():raise ValueError('Choose the exported session folder containing manifest.json.')
    if output==source or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('The output and source recording must be separate, non-nested folders.')
    if output.exists() and any(output.iterdir()):raise ValueError('Output already contains files. Use a new output folder.')
    return source,output


def write_dashboard(output, state):
    """Render only escaped local metadata; never serialize environment/API keys."""
    output=Path(output);e=html.escape
    links=[]
    for path,label in [('map/viewer.html','Open 3D scene + timeline'),('mesh/room-mesh.ply','Download reconstructed mesh'),
                       ('parsed/storyboard-main.jpg','Main camera storyboard'),('parsed/storyboard-ultra.jpg','Ultra Wide storyboard'),
                       ('validation.json','Capture validation'),('review/review.json','Vision review + inventory'),('run.json','Processing record')]:
        if (output/path).is_file():links.append(f'<a href="{path}">{e(label)}</a>')
    items=''.join(f'<li>{e(x)}</li>'for x in state.get('limitations',[]))
    page=f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scene Capture · recording dashboard</title><style>
body{{background:#eeeee7;color:#283d33;font:16px/1.6 system-ui;max-width:1080px;margin:48px auto;padding:0 24px}}h1{{font-size:36px;letter-spacing:-1px}}nav{{display:flex;flex-wrap:wrap;gap:12px}}a{{color:#116552}}nav a{{padding:10px 14px;border:1px solid #aebeb1;text-decoration:none}}iframe{{width:100%;height:680px;border:1px solid #bdc8bd;margin:24px 0}}small{{color:#617063}}li{{margin:8px 0}}
</style><h1>Your recording</h1><p>{e(state.get('recording_name','Session'))} · {e(state['status'])} · {e(state.get('stage',''))}</p><nav>{''.join(links)}</nav>'''
    if (output/'map/viewer.html').is_file():page+='<iframe title="Reconstructed scene and timed capture" src="map/viewer.html"></iframe>'
    review_file=output/'review/review.json'
    if review_file.is_file():
        result=json.loads(review_file.read_text()).get('review',{})
        if not isinstance(result,dict):result={}
        page+='<h2>Vision review</h2><p>'+e(str(result.get('summary','')))+'</p>'
        for group in ['surfaces','objects']:
            page+='<h3>'+group.capitalize()+' · proposals</h3><ul>'
            entries=result.get(group,[])
            for item in entries if isinstance(entries,list)else[]:
                if isinstance(item,dict):page+='<li><strong>'+e(str(item.get('label','Unlabeled')))+':</strong> '+e(str(item.get('evidence','')))+' <small>'+e(str(item.get('uncertainty','')))+'</small></li>'
            page+='</ul>'
    page+='<h2>What this run contains</h2><ul>'+items+'</ul>'
    if state.get('error'):page+='<p role="alert">'+e(state['error'])+'</p>'
    page+='<small>Local reconstruction estimates. Camera rates and original timestamps are retained in capture reports. Visual review does not establish measured geometry or contact.</small></html>'
    (output/'index.html').write_text(page)


def finish_viewer(output):
    """Add existing upright navigation with its explicit estimated-floor limits."""
    from .map_replay import write_map_ui
    from .navigation_surface import build
    output=Path(output)
    navigation=output/'navigation'
    if not navigation.exists():build(output/'map/scene.json',navigation)
    scene_path=output/'map/scene.json';scene=json.loads(scene_path.read_text())
    scene['navigation']=json.loads((navigation/'navigation.json').read_text())
    save(scene_path,scene);write_map_ui(output/'map')


def run(source,output,*,fps=15,width=640,review_enabled=False,model=None,texture=False,prefix=None):
    source,output=check_paths(source,output)
    # Preflight before making output; failures must not masquerade as completed jobs.
    health=doctor()
    if not health['ready']:raise RuntimeError('Missing mapping dependencies. '+health['install'])
    if review_enabled and not os.environ.get('OPENAI_API_KEY'):raise ValueError('Set OPENAI_API_KEY, or run without --review.')
    if not 0<fps<=30 or not 160<=width<=1920:raise ValueError('Require 0 < processing fps <= 30 and width 160..1920.')
    output.mkdir(parents=True,exist_ok=True)
    state={'schema':'scene-capture.run/1','status':'running','stage':'validation','recording_name':source.name,
           'started_at':datetime.now(timezone.utc).isoformat(),'source_manifest_sha256':hashlib.sha256((source/'manifest.json').read_bytes()).hexdigest(),
           'processing_fps':fps,'processing_width':width,'api_review_requested':review_enabled,
           'limitations':['RGB-D reconstruction uses Main camera + LiDAR and estimated offline poses.',
             'Ultra Wide imagery is indexed and shown as source evidence; automatic Ultra geometry fusion is not enabled.',
             'Moving hands/objects can leave artifacts in this baseline; no automatic dynamic masks or hand fitting run.',
             'Walking uses an estimated flat floor and approximate obstacles; it does not establish physical contact or gravity.',
             'Vision inventory is a proposal. Complete furnished CAD models require further modeling and review.',
             'No pretrained checkpoints, licensed MANO assets, demo-room models or private recordings are required.']}
    def progress(stage):
        state['stage']=stage;save(output/'run.json',state);write_dashboard(output,state);print('Stage: '+stage,flush=True)
    try:
        progress('validation')
        from .validate_session import Validator
        validation=Validator(source,check_video=True).run();save(output/'validation.json',validation)
        if validation['errors']:raise ValueError('Recording validation failed. See validation.json.')
        progress('indexing both cameras and depth')
        from .parse_recording import parse
        parse(source,output/'parsed')
        progress('calibrated RGB-D export')
        from .export_rgbd import export
        export(source,output/'export',fps,width)
        progress('camera tracking and loop closure')
        from .reconstruct import run as reconstruct
        reconstruct(output/'export',output/'mapping',output/'mapper-build',prefix)
        progress('depth fusion')
        from .fuse_map import fuse
        fuse(output/'export',output/'mapping',output/'mesh',stride=1,voxel=.025)
        atlas=None
        if texture:
            progress('texture atlas')
            from .texture_atlas import build_atlas
            atlas=output/'atlas';build_atlas(output/'export',output/'mapping',output/'mesh/room-mesh.ply',None,atlas,size=4096,interval=10)
        progress('3D replay dashboard')
        from .mesh_viewer import build_viewer
        build_viewer(output/'export',output/'mapping',output/'mesh/room-mesh.ply',output/'map',texture_bundle=atlas,timeline=True)
        progress('paired Main and Ultra Wide replay')
        from .release_previews import attach
        attach(output)
        finish_viewer(output)
        # Keep the delivered dashboard independent of atlas symlinks.
        atlas_link=output/'map/atlas.png'
        if atlas_link.is_symlink():data=atlas_link.read_bytes();atlas_link.unlink();atlas_link.write_bytes(data)
        if review_enabled:
            progress('OpenAI vision review and inventory')
            from .astra_review import review
            prompt='''Review this recording for reconstruction readiness and produce an object/surface inventory. Source Main and Ultra storyboards show repeated views over time; associate instances carefully and distinguish moving objects. Return JSON with summary, surfaces:[{label,evidence,uncertainty}], objects:[{label,visible_parts,base_type,evidence,uncertainty}], capture_gaps, reconstruction_risks, recommended_next_steps. Chair bases must follow visible central columns/casters versus legs; use unknown if occluded. Do not invent hidden geometry, metric dimensions, contact, model approval or 3D poses. This is capture review and recognition, not furnished model reconstruction.'''
            review([output/'parsed/storyboard-main.jpg',output/'parsed/storyboard-ultra.jpg'],prompt,output/'review',model=model)
        state.update(status='complete',stage='ready',completed_at=datetime.now(timezone.utc).isoformat())
        save(output/'run.json',state);write_dashboard(output,state)
        print('Dashboard: '+str(output/'index.html'),flush=True)
        return state
    except Exception as exc:
        state.update(status='failed',error=f'{type(exc).__name__}: Processing stopped; inspect pipeline.log and stage reports.')
        save(output/'run.json',state);write_dashboard(output,state)
        raise


def main():
    if len(sys.argv)>1 and sys.argv[1]=='workflow':
        from .agent_workflow import main as workflow_main
        raise SystemExit(workflow_main(sys.argv[2:]))
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('doctor',help='Check desktop dependencies without calling an API')
    s=sub.add_parser('serve',help='Open the local setup and processing dashboard');s.add_argument('--workspace',type=Path,default=Path('workspaces'));s.add_argument('--port',type=int,default=8766);s.add_argument('--no-open',action='store_true')
    r=sub.add_parser('run',help='Process one exported session');r.add_argument('session',type=Path);r.add_argument('--output',type=Path,required=True);r.add_argument('--fps',type=float,default=15);r.add_argument('--width',type=int,default=640);r.add_argument('--review',action='store_true',help='Send the two camera storyboards to OpenAI for recognition/review');r.add_argument('--model');r.add_argument('--texture',action='store_true');r.add_argument('--prefix')
    a=p.parse_args()
    if a.command=='doctor':print(json.dumps(doctor(),indent=2));return
    if a.command=='serve':
        from .release_server import serve
        serve(a.workspace,a.port,not a.no_open);return
    try:run(a.session,a.output,fps=a.fps,width=a.width,review_enabled=a.review,model=a.model,texture=a.texture,prefix=a.prefix)
    except Exception as exc:
        # No API response payload/credentials printed by this entry point.
        print(f'Processing failed ({type(exc).__name__}). See the output reports/logs, or run scene-capture doctor.',file=sys.stderr)
        raise SystemExit(1)


if __name__=='__main__':main()
