"""Loopback-only setup UI. API credentials stay in the processing child's environment."""
from functools import partial
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import re
import subprocess
import sys
import threading
from urllib.parse import urlsplit,unquote,quote
import webbrowser

from .release import check_paths, doctor, save, write_dashboard
from .serve_viewer import ViewerHandler

UI_ASSETS={'dashboard.css':'text/css','setup.mjs':'text/javascript','ui_logic.mjs':'text/javascript'}


def job_listing(workspace):
    jobs=[]
    for p in workspace.glob('*/run.json'):
        try:
            active=p.parent/'processing/run.json';active=active if active.exists()else p
            d=json.loads(active.read_text())
            if not isinstance(d,dict):continue
            presentation=p.parent/'presentation.json';label=json.loads(presentation.read_text()).get('name')if presentation.exists()else None
            workflow=p.parent/'workflow.json';w=json.loads(workflow.read_text())if workflow.exists()else{}
            stages=w.get('stages',{});pending=sum(s.get('status')in('needs_setup','pending')for s in stages.values())
            candidates=[p.parent/w['viewer_path']/'viewer.html']if w.get('viewer_path')else[active.parent/'map/viewer.html']
            viewer=next((v for v in candidates if v.is_file()and v.resolve().is_relative_to(workspace.resolve())),None)
            viewer_url='/'+quote(str(viewer.relative_to(workspace)),safe='/')if viewer else None
            path=str(active.parent.relative_to(workspace))+'/index.html'
            jobs.append({'id':p.parent.name,'name':d.get('recording_name',p.parent.name),'label':label,'status':d['status'],
                         'stage':d.get('stage'),'url':'/'+quote(path,safe='/'),'viewer_url':viewer_url,'pending_count':pending,
                         'updated_at':datetime.fromtimestamp(active.stat().st_mtime,timezone.utc).isoformat()})
        except (OSError,KeyError,ValueError,TypeError,AttributeError):continue
    return sorted(jobs,key=lambda j:j['updated_at'],reverse=True)


def recording_name(value):
    if not isinstance(value,str)or len(value.strip())>80 or any(ord(c)<32 for c in value):raise ValueError('Use a recording name of 80 characters or fewer.')
    return value.strip()


class ReleaseServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,workspace,port):
        self.workspace=Path(workspace).expanduser().resolve();self.workspace.mkdir(parents=True,exist_ok=True)
        self.token=secrets.token_urlsafe(32);self.children=[];self.job_lock=threading.Lock()
        super().__init__(('127.0.0.1',port),partial(ReleaseHandler,directory=str(self.workspace)))


class ReleaseHandler(ViewerHandler):
    def local_request(self):
        return self.headers.get('Host')in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}

    def log_message(self,*args):pass  # Never log input paths or request bodies.

    def end_headers(self):
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
        super().end_headers()

    def respond(self,value,status=200):
        data=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

    def do_GET(self):
        if not self.local_request():self.send_error(403);return
        path=urlsplit(self.path).path
        if path=='/':
            data=Path(__file__).with_name('release_setup.html').read_text().replace('__SESSION_TOKEN__',self.server.token).encode()
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
        if path.startswith('/ui/')and path[4:]in UI_ASSETS:
            name=path[4:];data=Path(__file__).with_name(name).read_bytes()
            self.send_response(200);self.send_header('Content-Type',UI_ASSETS[name]+'; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
        if path=='/api/status':
            self.respond({'doctor':doctor(),'jobs':job_listing(self.server.workspace)});return
        # Serve only generated job artifacts, never the repository, .env or parent paths.
        target=Path(self.translate_path(path)).resolve()
        if not target.is_relative_to(self.server.workspace) or any(x.startswith('.')for x in Path(path).parts):self.send_error(403);return
        if target.is_dir():self.send_error(403);return
        super().do_GET()

    def do_POST(self):
        path=urlsplit(self.path).path
        rename=re.fullmatch(r'/api/jobs/([^/]+)/name',path)
        if not self.local_request() or(path!='/api/jobs'and not rename):self.send_error(403);return
        origin=self.headers.get('Origin')
        if origin not in {f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'} or not secrets.compare_digest(self.headers.get('X-Scene-Token',''),self.server.token):
            self.send_error(403);return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=16384:raise ValueError('Invalid request size')
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict):raise ValueError('Invalid request')
            if rename:
                identifier=unquote(rename[1]);job=self.server.workspace/identifier
                if not identifier or identifier.startswith('.')or '/'in identifier or '\\'in identifier or job.parent!=self.server.workspace or not job.resolve().is_relative_to(self.server.workspace)or not(job/'run.json').is_file():raise ValueError('Recording not found.')
                name=recording_name(data.get('name',''))
                save(job/'presentation.json',{'name':name})
                if(job/'workflow.json').is_file():
                    from .ui import render_workflow
                    state=json.loads((job/'workflow.json').read_text())
                    (job/'index.html').write_text(render_workflow(job,state))
                else:
                    run=job/'processing/run.json';run=run if run.is_file()else job/'run.json'
                    write_dashboard(run.parent,json.loads(run.read_text()))
                self.respond({'name':name});return
            source=Path(data.get('session','')).expanduser().resolve()
            name=recording_name(data.get('name',''))
            review=data.get('review')is True;key=data.get('api_key','')
            if not isinstance(key,str) or '\n'in key or '\r'in key:raise ValueError('Invalid API key')
            model=data.get('model')or'gpt-6-astra'
            if not isinstance(model,str)or len(model)>150:raise ValueError('Invalid model')
            if review and not(key or os.environ.get('OPENAI_API_KEY')):raise ValueError('Enter an OpenAI API key or turn off vision review.')
            if not doctor()['ready']:raise ValueError('Install the missing desktop dependencies listed above.')
            with self.server.job_lock:
                if any(p.poll()is None for p in self.server.children):self.respond({'error':'A recording is already processing. Wait for it to finish.'},409);return
                job=secrets.token_hex(8);output=self.server.workspace/job
                source,output=check_paths(source,output);output.mkdir()
                if name:save(output/'presentation.json',{'name':name})
                state={'status':'queued','stage':'starting','recording_name':source.name}
                save(output/'run.json',state);write_dashboard(output,state)
                env=os.environ.copy()
                if key:env['OPENAI_API_KEY']=key
                command=[sys.executable,'-m','pipeline.release','run',str(source),'--output',str(output/'processing'),'--model',model]
                # Processing writes a nested run; expose only the job's generated files.
                if review:command.append('--review')
                if data.get('texture')is True:command.append('--texture')
                log=(output/'pipeline.log').open('wb')
                try:child=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT)
                except Exception:log.close();raise
                log.close();self.server.children.append(child)
                def monitor():
                    code=child.wait();run=output/'processing/run.json'
                    if run.exists():
                        result=json.loads(run.read_text());save(output/'run.json',result)
                    else:
                        result={**state,'status':'failed','stage':'preflight','error':'Preflight failed; open pipeline.log or run scene-capture doctor.'};save(output/'run.json',result)
                    # Relative redirect leaves every asset below the generated job root.
                    if run.exists():(output/'index.html').write_text('<!doctype html><meta http-equiv="refresh" content="0;url=processing/index.html"><a href="processing/index.html">Open recording dashboard</a>')
                    else:write_dashboard(output,result)
                threading.Thread(target=monitor,daemon=True).start()
            self.respond({'id':job,'url':'/'+job+'/index.html'},202)
        except (OSError,ValueError,TypeError)as exc:self.respond({'error':str(exc)},400)


def serve(workspace,port=8766,open_browser=True):
    with ReleaseServer(workspace,port)as server:
        url=f'http://127.0.0.1:{server.server_port}'
        print('Setup dashboard: '+url,flush=True)
        if open_browser:webbrowser.open(url)
        try:server.serve_forever()
        except KeyboardInterrupt:pass

