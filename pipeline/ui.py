"""Shared presentation for setup, recording dashboards and scene controls."""
from __future__ import annotations
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
from urllib.parse import quote

LABELS={'scan':'Scene reconstruction','review':'Capture review','models':'Scene models','hands':'Hand tracking','objects':'Object tracking'}
STATE={'complete':('Complete','good'),'needs_setup':('Setup needed','warn'),'pending':('Not started',''),'planned':('Ready to process',''),'queued':('Queued',''),'running':('Processing','running'),'failed':('Needs attention','bad')}
ICON='<svg width="27" height="29" viewBox="0 0 32 34" fill="none" aria-hidden="true"><path d="m16 2 13 7.5v15L16 32 3 24.5v-15L16 2Z" stroke="currentColor" stroke-width="1.7"/><path d="m3 9.5 13 7.4 13-7.4M16 17v15M9.5 5.8l13 7.4v7.5" stroke="currentColor" stroke-width="1.7"/></svg>'
e=lambda value:html.escape(str(value),quote=True)
url=lambda value:e(quote(str(value),safe='/'))


def badge(status,label=None):
    text,tone=STATE.get(status,(status.replace('_',' ').capitalize(),''))
    return f'<span class="badge {tone}" data-status-badge>{e(label or text)}</span>'


def title_for(root,state):
    candidates=[root/'presentation.json']
    if root.name=='processing':candidates.append(root.parent/'presentation.json')
    for p in candidates:
        try:
            label=json.loads(p.read_text()).get('name','').strip()
            if label:return label
        except (OSError,ValueError,AttributeError):pass
    name=state.get('recording_name','Recording')
    return'Room capture'if re.match(r'^\d{4}-\d\d-\d\dT\d\d-\d\d-\d\dZ-',name)else name


def page(title,body,state_src='run.json',viewer_path=None):
    css=Path(__file__).with_name('dashboard.css').read_text()
    script=Path(__file__).with_name('dashboard_page.mjs').read_text()
    favicon="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cpath fill='%23176f62' d='M16 2 29 9v14l-13 7L3 23V9z'/%3E%3C/svg%3E"
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} · iReal2Sim</title><link rel="icon" href="{favicon}"><style>{css}</style></head><body data-state-src="{e(state_src)}" data-viewer-path="{e(viewer_path or '')}"><a class="skip-link" href="#main">Skip to content</a><header class="app-nav"><a href="/" class="brand">{ICON}iReal2Sim</a><div class="nav-right"><span class="local-label">Local workspace</span><a href="/" class="nav-link">All recordings</a></div></header><main class="app-main" id="main">{body}<footer class="app-footer"><span>iReal2Sim · Local scene workspace</span><span>Geometry and contact are estimates.</span></footer></main><script type="module">{script}</script></body></html>'''


def heading(root,state,viewer_path=None):
    action=f'<a class="button primary" href="{url(viewer_path)}/viewer.html">Open scene <span aria-hidden="true">↗</span></a>'if viewer_path else''
    label='Scene ready'if viewer_path and state['status']=='needs_setup'else'Ready to explore'if state['status']=='complete'else None
    return f'''<div class="page-heading"><div><div class="eyebrow"><a href="/">Recordings</a><span>/</span><span>Scene workspace</span></div><h1>{e(title_for(root,state))}</h1><div class="recording-meta">{badge(state['status'],label)}<span>{e(state.get('recording_name',''))}</span></div></div>{action}</div><div class="message" id="newResults" hidden>New results are available. <button class="text-button" id="reloadResults" type="button">Refresh scene</button></div>'''


def scene_panel(viewer_path):
    if viewer_path:
        content=f'<iframe class="scene-frame" title="Reconstruction and camera replay" src="{url(viewer_path)}/viewer.html" allow="fullscreen"></iframe>'
        action=f'<a class="nav-link" href="{url(viewer_path)}/viewer.html">Full viewer ↗</a>'
    else:
        content=f'<div class="preview-pending"><div>{ICON}<h2>Your scene will appear here</h2><p>We are preparing the recording and reconstructing its geometry.<br>You can follow progress alongside.</p></div></div>';action=''
    return f'<section class="panel scene-panel"><header class="scene-panel-head"><h2>Scene preview</h2>{action}</header>{content}</section>'


def resources(items):
    links=''.join(f'<a href="{url(path)}"><span aria-hidden="true">↓</span>{e(label)}</a>'for path,label in items)
    return f'<section class="panel aside-panel"><h2>Files & evidence</h2><div class="resource-list">{links or "<span class=muted>Files appear as processing completes.</span>"}</div></section>'


def render_recording(root,state):
    root=Path(root);viewer='map'if(root/'map/viewer.html').is_file()else None
    body=heading(root,state,viewer)
    if state.get('error'):body+=f'<div class="error-summary" role="alert">{e(state["error"])}</div>'
    phase=state.get('stage','Preparing recording');phase='Ready'if state['status']=='complete'else phase.capitalize()
    inspector=f'<section class="panel aside-panel"><div class="inspector-title"><span>Processing status</span>{badge(state["status"])}</div><h2 class="section-space" id="activeStage">{e(phase)}</h2><p class="field-help">Your original recording is preserved. Camera footage and reconstructed geometry share the recorded timeline.</p>'
    if state.get('api_review_requested'):inspector+='<div class="callout">Includes <strong>vision review</strong> of visible objects and capture coverage.</div>'
    inspector+='<details class="compact-details"><summary>What this reconstruction includes</summary><ul>'+''.join(f'<li>{e(v)}</li>'for v in state.get('limitations',[]))+'</ul></details></section>'
    items=[]
    for path,label in [('mesh/room-mesh.ply','Reconstructed mesh'),('parsed/storyboard-main.jpg','Main camera storyboard'),('parsed/storyboard-ultra.jpg','Ultra Wide storyboard'),('validation.json','Capture checks'),('review/review.json','Vision review'),('run.json','Processing record')]:
        if(root/path).is_file():items.append((path,label))
    body+=f'<div class="overview-grid">{scene_panel(viewer)}<aside>{inspector}{resources(items)}</aside></div>'
    review=root/'review/review.json'
    if review.is_file():
        result=json.loads(review.read_text()).get('review',{});result=result if isinstance(result,dict)else{}
        body+='<section class="panel section-space"><header class="panel-head"><h2>Capture review</h2><p>Vision suggestions grounded in the original camera views.</p></header><p class="review-summary">'+e(result.get('summary',''))+'</p><div class="review-list">'
        for group in ['surfaces','objects']:
            entries=result.get(group,[]);entries=entries if isinstance(entries,list)else[]
            body+='<section><h3>'+group.capitalize()+' · proposals</h3><ul>'
            for item in entries:
                if isinstance(item,dict):body+='<li><strong>'+e(item.get('label','Unlabeled'))+'</strong> — '+e(item.get('evidence',''))+' <small>'+e(item.get('uncertainty',''))+'</small></li>'
            body+='</ul></section>'
        body+='</div></section>'
    return page(title_for(root,state),body,viewer_path=viewer)


def render_workflow(root,state):
    root=Path(root);viewer=state.get('viewer_path');viewer=viewer if viewer and(root/viewer/'viewer.html').is_file()else None
    body=heading(root,state,viewer)
    if state.get('viewer_error'):body+=f'<div class="error-summary" role="alert">{e(state["viewer_error"])}</div>'
    stages=state['stages'];complete=sum(s['status']=='complete'for s in stages.values());rows=[];items=[]
    for name,stage in stages.items():
        text,tone=STATE.get(stage['status'],(stage['status'],''));mark='✓'if tone=='good'else'!'if tone in('warn','bad')else'·'
        descriptions={'scan':'Calibrated RGB-D, camera poses and scene geometry.','review':'Original footage checked for objects and missing coverage.','models':'Complete scene geometry from a configured model runner.','hands':'Estimated hand meshes aligned to the source recording.','objects':'Handled-object geometry and estimated motion.'}
        message=descriptions.get(name,'')
        if stage['status']=='needs_setup':message='Optional fitting runner not configured. Continue setup with your agent.'
        if stage['status']=='failed':message='This stage stopped. Inspect its details before retrying.'
        detail=''
        if stage.get('needs'):detail='<details class="stage-detail"><summary>'+('Failure details'if stage['status']=='failed'else'Setup details')+'</summary><p data-stage-needs>'+e('; '.join(stage['needs']))+'</p></details>'
        rows.append(f'<li data-stage="{e(name)}"><span class="stage-mark {tone}" aria-hidden="true">{mark}</span><div><div class="stage-title"><span>{e(LABELS.get(name,name))}</span><span class="muted" data-stage-state>{e(text)}</span></div><p class="stage-description" data-stage-description>{e(message)}</p>{detail}</div></li>')
        for item in stage.get('artifacts',[]):
            label={'scan_run':'Processing record','vision_review':'Capture review','scene_glb':'Scene model','interaction_tracks':LABELS.get(name,name)+' estimates'}.get(item.get('role'),item.get('role','Artifact').replace('_',' ').capitalize())
            items.append((item['path'],label))
        if stage.get('output')and(root/stage['output']/'index.html').is_file():items.append((stage['output']+'/index.html','Reconstruction details'))
    inspector=f'<section class="panel aside-panel"><div class="inspector-title"><span>Workflow</span><span class="tabular" id="stageCount">{complete} of {len(stages)} complete</span></div><ol class="stage-list">{"".join(rows)}</ol>'
    if any(s['status']=='needs_setup'for s in stages.values()):
        prompt=f'Resume the iReal2Sim workflow at {root}. Configure the remaining fitting runners and continue ready stages.'
        lead='Your scan is available.'if viewer else'Continue workflow setup.'
        inspector+=f'<div class="callout"><strong>{lead}</strong> Optional fitting can continue with your interactive agent.</div><details class="compact-details"><summary>Continue in Codex or your agent</summary><p class="field-help">Open the project folder and give the agent this prompt:</p><code class="terminal-line">{e(prompt)}</code><button type="button" class="button secondary" data-copy="{e(prompt)}">Copy agent prompt</button></details>'
    inspector+='</section>'
    body+=f'<div class="overview-grid">{scene_panel(viewer)}<aside>{inspector}{resources(items)}</aside></div>'
    body+='<p class="callout">Fitting outputs remain estimates until visually reviewed. Worker completion does not establish metric accuracy or physical contact.</p>'
    return page(title_for(root,state),body,'workflow.json',viewer)


def polish_viewer(path,back_url='../index.html'):
    path=Path(path);original=path.read_text();css=Path(__file__).with_name('viewer.css').read_text()
    style=f'<style id="ireal2sim-presentation">{css}</style>'
    if 'id="ireal2sim-presentation"'in original:
        result=re.sub(r'<style id="ireal2sim-presentation">.*?</style>',lambda _:style,original,flags=re.S)
    else:
        result=original.replace('</head>',style+f'<meta name="ireal2sim-back" content="{e(back_url)}"></head>',1)
        result=result.replace('</body>','<script type="module" src="viewer_ui.mjs"></script></body>',1)if'</body>'in result else result+'<script type="module" src="viewer_ui.mjs"></script>'
    path.write_text(result);shutil.copyfile(Path(__file__).with_name('viewer_ui.mjs'),path.parent/'viewer_ui.mjs')
    return {'source_viewer_sha256':hashlib.sha256(original.encode()).hexdigest(),'viewer_sha256':hashlib.sha256(result.encode()).hexdigest(),'scope':'Presentation only. Geometry, camera poses, source timestamps and quality gates are unchanged.'}
