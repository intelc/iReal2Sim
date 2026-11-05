"""Resumable, credential-aware workflow for an interactive coding agent.

This module's discovery/doctor/plan/status commands use only the standard library.
Scan/review execute the shipped pipeline; advanced fitting uses explicit workers.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from .release import save

SECRET_NAMES = ('OPENAI_API_KEY','FAL_KEY','FAL_API_KEY','RUNPOD_API_KEY',
                'MODAL_TOKEN_ID','MODAL_TOKEN_SECRET','HF_TOKEN','HUGGING_FACE_HUB_TOKEN')
PROFILES = {'scan':['scan'], 'review':['scan','review'],
            'full':['scan','review','models','hands','objects']}


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb')as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def settings(env_file=Path('.env')):
    """Parse simple dotenv assignments without shell expansion or execution."""
    values={}
    if env_file and Path(env_file).is_file():
        for line in Path(env_file).read_text().splitlines():
            line=line.strip()
            if not line or line.startswith('#')or '='not in line:continue
            key,value=line.removeprefix('export ').split('=',1);key=key.strip();value=value.strip()
            if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',key):continue
            if len(value)>1 and value[0]==value[-1]and value[0]in'\"\'':value=value[1:-1]
            else:value=value.split(' #',1)[0].rstrip()
            values[key]=value
    values.update({k:v for k,v in os.environ.items()if v})
    if not values.get('FAL_KEY')and values.get('FAL_API_KEY'):values['FAL_KEY']=values['FAL_API_KEY']
    return values


def redact(text,values):
    text=str(text)
    for name,value in values.items():
        if name not in SECRET_NAMES and not re.search(r'KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL',name,re.I):continue
        if value:text=text.replace(value,'[redacted]')
    return re.sub(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}','[redacted]',text)


def config(path=None):
    path=Path(path or 'ireal2sim.local.json')
    result=json.loads(path.read_text())if path.exists()else{}
    if not isinstance(result,dict)or not isinstance(result.get('backends',{}),dict):raise ValueError('Invalid workflow configuration.')
    def reject_secrets(node):
        if isinstance(node,dict):
            for k,v in node.items():
                if k in SECRET_NAMES:raise ValueError('Store credentials in environment variables or .env, not workflow configuration.')
                reject_secrets(v)
        elif isinstance(node,list):
            for v in node:reject_secrets(v)
    reject_secrets(result)
    return result


def command(argv,values):
    if not isinstance(argv,list)or not argv or any(not isinstance(x,str)or not x for x in argv):raise ValueError('Worker argv must be a nonempty string array.')
    for value in argv:
        if any(v and v in value for k,v in values.items()if k in SECRET_NAMES or re.search(r'KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL',k,re.I)):raise ValueError('Pass worker credentials through environment, never argv.')
        if re.search(r'(?i)(--?(?:api[-_]?key|token|password)|authorization:|https?://[^/\s]*@|\bsk-[\w-]{16,})',value):raise ValueError('Credential-bearing worker arguments are not supported.')
    return argv


def discover(directory):
    directory=Path(directory).expanduser().resolve();found=[]
    paths=[directory/'manifest.json',*directory.glob('*/manifest.json')]
    for p in sorted(paths):
        if not p.is_file():continue
        try:
            d=json.loads(p.read_text())
            if d.get('schema')=='iphone-rsa.session/1':found.append({'path':str(p.parent),'status':d.get('status'),'counts':d.get('counts',{})})
        except (ValueError,OSError):continue
    return found


def source_stamp(source):
    source=Path(source).expanduser().resolve()
    manifest=json.loads((source/'manifest.json').read_text())
    if manifest.get('schema')!='iphone-rsa.session/1'or manifest.get('status')!='complete':raise ValueError('Choose a complete exported iPhone session.')
    files={}
    for p in sorted(source.rglob('*')):
        if p.is_symlink():raise ValueError('Recording inputs must not contain symlinks.')
        if p.is_file():
            stat=p.stat();files[str(p.relative_to(source))]=[stat.st_size,stat.st_mtime_ns]
    return {'manifest_sha256':sha(source/'manifest.json'),'file_metadata_sha256':digest(files)}


def needed(stage,cfg,values):
    if stage=='review':return []if values.get('OPENAI_API_KEY')else['Set OPENAI_API_KEY in the environment or .env for requested OpenAI review.']
    if stage=='scan':
        missing=[n for n in ('ffmpeg','ffprobe','cmake','rtabmap')if not shutil.which(n)]
        return ['Install desktop tools: '+', '.join(missing)]if missing else[]
    worker=cfg.get('backends',{}).get(stage)
    if not isinstance(worker,dict)or not worker.get('argv'):return [f'Configure the {stage} worker in ireal2sim.local.json; this fitting stage is not built into the source preview.']
    command(worker['argv'],values)
    required=worker.get('requires_env',[])
    if not isinstance(required,list)or any(not isinstance(k,str)or not re.fullmatch(r'[A-Z][A-Z_0-9]*',k)for k in required):raise ValueError('requires_env must contain environment variable names.')
    missing=['Set '+k+' for the configured '+stage+' worker.'for k in required if not values.get(k)]
    paths=worker.get('requires_paths',[])
    if not isinstance(paths,list):raise ValueError('requires_paths must be a list.')
    for k in paths:
        if not isinstance(k,str)or not re.fullmatch(r'[A-Z][A-Z_0-9]*',k):raise ValueError('requires_paths must contain environment variable names.')
        if not values.get(k)or not Path(values[k]).expanduser().exists():missing.append('Configure an existing '+k+' path for '+stage+'.')
    return missing


def doctor(profile='scan',cfg=None,values=None):
    cfg=cfg or{};values=values if values is not None else settings()
    stages={name:{'implementation':'built_in'if name in('scan','review')else'configured_worker'if cfg.get('backends',{}).get(name,{}).get('argv')else'not_configured',
                  'needs':needed(name,cfg,values)}for name in PROFILES[profile]}
    return {'schema':'ireal2sim.agent-doctor/1','profile':profile,
            'credentials':{name:{'configured':bool(values.get(name)),'verified':False}for name in SECRET_NAMES},
            'tools':{name:bool(shutil.which(name))for name in ('uv','ffmpeg','ffprobe','cmake','rtabmap','xcodebuild','nvidia-smi')},
            'stages':stages,'ready_stages':[s for s,r in stages.items()if not r['needs']],
            'note':'Presence checks only: no credential values, API calls, model downloads or GPU provisioning. A configured worker is not proof of runtime availability.'}


def init(source,output,profile='scan',fps=15,width=640,texture=True):
    source=Path(source).expanduser().resolve();output=Path(output).expanduser().resolve()
    if output==source or output.is_relative_to(source)or source.is_relative_to(output):raise ValueError('Use separate, non-nested source and workspace folders.')
    if output.exists()and any(output.iterdir()):raise ValueError('Use a fresh workspace; resume an existing workflow explicitly.')
    if profile not in PROFILES or not 0<fps<=30 or not 160<=width<=1920:raise ValueError('Invalid profile or processing dimensions/rate.')
    stamp=source_stamp(source);output.mkdir(parents=True,exist_ok=True)
    state={'schema':'ireal2sim.agent-workflow/1','source':str(source),'source_stamp':stamp,'profile':profile,
           'options':{'fps':fps,'width':width,'texture':texture},'created_at':datetime.now(timezone.utc).isoformat(),
           'status':'planned','recording_name':source.name,'stages':{s:{'status':'pending','attempts':[]}for s in PROFILES[profile]}}
    write_state(output,state);return state


def write_state(root,state):
    save(root/'workflow.json',state)
    save(root/'run.json',{'schema':state['schema'],'status':state['status'],'recording_name':state['recording_name'],
                          'stage':', '.join(s+': '+r['status']for s,r in state['stages'].items())})
    from .ui import render_workflow
    (root/'index.html').write_text(render_workflow(root,state))


def fingerprints(root,paths):
    result={}
    for path in paths:
        path=Path(path).resolve()
        if not path.is_file()or not path.is_relative_to(root.resolve()):raise ValueError('Missing or escaping stage artifact.')
        result[str(path.relative_to(root.resolve()))]=sha(path)
    return result


def cache_valid(root,stage,fingerprint):
    files=stage.get('artifact_sha256',{})
    return bool(stage.get('status')=='complete'and stage.get('fingerprint')==fingerprint and files and
                all((root/p).is_file()and(root/p).resolve().is_relative_to(root.resolve())and sha(root/p)==h for p,h in files.items()))


def scan_context(root,stage,stamp):
    base=root/stage['output'];run=json.loads((base/'run.json').read_text())
    if run.get('status')!='complete'or run.get('source_manifest_sha256')!=stamp['manifest_sha256']:raise ValueError('Scan does not belong to this source recording.')
    return {'source_manifest_sha256':stamp['manifest_sha256'],'world_frame':'initial_main_optical',
            'dataset':str(base/'export'),'mapping':str(base/'mapping'),'viewer':str(base/'map'),
            'trajectory_sha256':sha(base/'mapping/mapping/optimized.tsv'),'calibration_sha256':sha(base/'export/calibration.json')}


def validate_worker(output,stage,context):
    result_path=output/'result.json'
    if result_path.stat().st_size>1024*1024:raise ValueError('Worker result is too large.')
    result=json.loads(result_path.read_text())
    for key,value in {'schema':'ireal2sim.worker-result/1','stage':stage,'status':'complete',
                      'source_manifest_sha256':context['source_manifest_sha256'],'context_sha256':digest(context),'world_frame':context['world_frame']}.items():
        if result.get(key)!=value:raise ValueError('Worker result has mismatched '+key+'.')
    items=result.get('artifacts')
    if not isinstance(items,list)or not 1<=len(items)<=100:raise ValueError('Worker must declare its actual artifacts.')
    artifacts=[]
    for item in items:
        rel=Path(item['path']);path=output/rel
        if rel.is_absolute()or '..'in rel.parts or path.is_symlink()or not path.resolve().is_relative_to(output.resolve())or not path.is_file():raise ValueError('Worker artifact must stay in its attempt folder.')
        if sha(path)!=item.get('sha256'):raise ValueError('Worker artifact hash mismatch.')
        artifacts.append({'path':str(path),'role':item['role']})
    role='scene_glb'if stage=='models'else'interaction_tracks'
    if not any(a['role']==role for a in artifacts):raise ValueError('Worker omitted the '+role+' artifact.')
    from .workflow_artifacts import checked_tracks,model_buffers
    for artifact in artifacts:
        if artifact['role']=='interaction_tracks':
            ids={f['id']for f in json.loads((Path(context['viewer'])/'scene.json').read_text())['timeline']['frames']}
            checked_tracks(json.loads(Path(artifact['path']).read_text()),stage,ids)
        elif artifact['role']=='scene_glb':
            validation=output/'model-validation';validation.mkdir(exist_ok=True)
            model_buffers(Path(artifact['path']),validation)
    return artifacts


@contextlib.contextmanager
def applied_environment(values):
    previous=os.environ.copy();os.environ.update(values)
    try:yield
    finally:os.environ.clear();os.environ.update(previous)


def run_worker(stage,worker,context,output,values):
    request={'schema':'ireal2sim.worker-request/1','stage':stage,'context':context,'context_sha256':digest(context),
             'output':str(output),'quality_policy':'Preserve exact source IDs and world frame; no missing-frame infill or approval/contact claims.'}
    save(output/'request.json',request)
    argv=command(worker['argv'],values)+['--request',str(output/'request.json'),'--output',str(output)]
    timeout=worker.get('timeout_seconds',1800)
    if type(timeout)not in(int,float)or not 1<=timeout<=7200:raise ValueError('Worker timeout must be 1..7200 seconds.')
    try:
        result=subprocess.run(argv,env={**os.environ,**values},capture_output=True,text=True,timeout=timeout)
        (output/'worker.log').write_text(redact((result.stdout+'\n'+result.stderr)[-4*1024*1024:],values))
    except subprocess.TimeoutExpired as exc:
        text=(exc.stdout or b'')+(exc.stderr or b'')
        if isinstance(text,bytes):text=text.decode(errors='replace')
        (output/'worker.log').write_text(redact(text[-4*1024*1024:],values));raise RuntimeError('Worker deadline exceeded; inspect redacted worker.log.')from None
    if result.returncode:raise RuntimeError('Worker failed; inspect redacted worker.log.')
    return validate_worker(output,stage,context)


def run(root,cfg=None,values=None):
    root=Path(root).expanduser().resolve();cfg=cfg or{};values=values if values is not None else settings()
    with(root/'.workflow.lock').open('a')as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another workflow process is already running.')from None
        state=json.loads((root/'workflow.json').read_text())
        if state.get('schema')!='ireal2sim.agent-workflow/1':raise ValueError('Unknown workflow schema.')
        if source_stamp(state['source'])!=state['source_stamp']:raise ValueError('Source recording changed. Create a new workflow; cached outputs were not reused.')
        state['status']='running';write_state(root,state)
        for name,stage in state['stages'].items():
            if name!='scan'and state['stages']['scan']['status']!='complete':stage.update(status='pending',needs=['Complete the scan stage first.']);continue
            context=None if name=='scan'else scan_context(root,state['stages']['scan'],state['source_stamp'])
            spec=state['options']if name=='scan'else{'model':values.get('OPENAI_MODEL','gpt-6-astra')}if name=='review'else cfg.get('backends',{}).get(name,{})
            implementation={'workflow':sha(__file__)}
            if name in('scan','review'):
                implementation[name]=sha(Path(__file__).with_name('release.py'if name=='scan'else'astra_review.py'))
            fingerprint=digest({'source':state['source_stamp'],'context':context,'spec':spec,'implementation':implementation})
            if cache_valid(root,stage,fingerprint):continue
            requirements=needed(name,cfg,values)
            if requirements:stage.update(status='needs_setup',needs=requirements);write_state(root,state);continue
            attempts=stage['attempts'];output=root/name/f'attempt-{len(attempts)+1:03d}';output.mkdir(parents=True)
            attempt={'output':str(output.relative_to(root)),'status':'running'};attempts.append(attempt)
            stage.update(status='running',needs=[],output=attempt['output']);write_state(root,state)
            try:
                if name=='scan':
                    from .release import run as scan
                    # Stage-owned attempt exists but must stay empty for the scan's source guard.
                    with applied_environment(values),(output/'pipeline.log').open('w')as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
                        scan(Path(state['source']),output/'data',fps=spec['fps'],width=spec['width'],texture=spec['texture'])
                    stage['output']=str((output/'data').relative_to(root));base=output/'data'
                    artifacts=[{'path':str(base/'run.json'),'role':'scan_run'}]
                    paths=[base/'run.json',base/'export/calibration.json',base/'mapping/mapping/optimized.tsv',*list((base/'map').rglob('*'))]
                    paths=[p for p in paths if p.is_file()]
                elif name=='review':
                    from .astra_review import review
                    base=Path(context['dataset']).parent
                    prompt='Review both original camera storyboards for capture gaps and persistent surfaces/objects. Return JSON with summary, surfaces, objects, capture_gaps, uncertainties. Identify visible chair support construction; do not invent hidden parts, metric dimensions, hand contact or approval of an unseen 3D model.'
                    with applied_environment(values),contextlib.redirect_stdout(sys.stderr):review([base/'parsed/storyboard-main.jpg',base/'parsed/storyboard-ultra.jpg'],prompt,output/'review',model=spec['model'])
                    artifacts=[{'path':str(output/'review/review.json'),'role':'vision_review'}];paths=list((output/'review').glob('*.json'))
                else:
                    artifacts=run_worker(name,cfg['backends'][name],context,output,values);paths=[Path(a['path'])for a in artifacts]+[output/'result.json']
                stage.update(status='complete',fingerprint=fingerprint,artifact_sha256=fingerprints(root,paths),
                             artifacts=[{**a,'path':str(Path(a['path']).relative_to(root))}for a in artifacts],quality='unreviewed_estimates'if name in('models','hands','objects')else'diagnostic')
                attempt['status']='complete'
            except Exception as exc:
                message=redact(f'{type(exc).__name__}: {exc}',values)
                stage.update(status='failed',needs=[message]);attempt.update(status='failed',error=message)
                save(output/'failure.json',{'stage':name,'error':message})
            write_state(root,state)
        states=[s['status']for s in state['stages'].values()]
        state['status']='complete'if all(s=='complete'for s in states)else'failed'if'failed'in states else'needs_setup'
        if state['stages']['scan']['status']=='complete':
            from .workflow_artifacts import compose
            try:compose(root,state)
            except Exception as exc:state['status']='failed';state['viewer_error']=redact(str(exc),values)
        write_state(root,state);return state


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    d=sub.add_parser('doctor');d.add_argument('--profile',choices=PROFILES,default='scan');d.add_argument('--config',type=Path);d.add_argument('--env-file',type=Path,default=Path('.env'))
    d=sub.add_parser('discover');d.add_argument('directory',type=Path,nargs='?',default=Path('recordings'))
    d=sub.add_parser('plan');d.add_argument('session',type=Path);d.add_argument('--output',type=Path,required=True);d.add_argument('--profile',choices=PROFILES,default='scan');d.add_argument('--fps',type=float,default=15);d.add_argument('--width',type=int,default=640);d.add_argument('--no-texture',action='store_true')
    d=sub.add_parser('run');d.add_argument('workspace',type=Path);d.add_argument('--config',type=Path);d.add_argument('--env-file',type=Path,default=Path('.env'))
    d=sub.add_parser('status');d.add_argument('workspace',type=Path)
    a=p.parse_args(argv)
    if a.command=='discover':result=discover(a.directory)
    elif a.command=='doctor':result=doctor(a.profile,config(a.config),settings(a.env_file))
    elif a.command=='plan':result=init(a.session,a.output,a.profile,a.fps,a.width,not a.no_texture)
    elif a.command=='status':result=json.loads((a.workspace/'workflow.json').read_text())
    else:result=run(a.workspace,config(a.config),settings(a.env_file))
    print(json.dumps(result,indent=2,allow_nan=False))
    return 0 if not isinstance(result,dict)or result.get('status')not in('failed','needs_setup')else 2


if __name__=='__main__':raise SystemExit(main())
