#!/usr/bin/env python3
"""Create a source-only portable release without mutating research artifacts."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile


ROOTS=['release','release_server','agent_workflow','workflow_artifacts','export_rgbd','reconstruct','fuse_map','mesh_viewer','parse_recording','serve_viewer','astra_review','texture_atlas','map_replay']
WEBSITE=['index.html','site.css','site.mjs','demo_defaults.mjs','demo_loading.html','demo_loading.css','demo_loading.js','mark.svg','README.md']
ASSETS=['dashboard.css','setup.mjs','ui_logic.mjs','dashboard_page.mjs','viewer.css','viewer_ui.mjs','viewer.html','release_setup.html','map_viewer.mjs','map_geometry.mjs','map_navigation.mjs']
TOP=['README.md','LICENSE','THIRD_PARTY.md','AGENTS.md','CLAUDE.md','.gitignore','.env.example','pyproject.toml','uv.lock','project.yml','start']
DOCS=['self-signing.md','release-workflow.md','session-format.md']
TESTS=['test_ui.py','test_ui_logic.mjs','test_validate_session.py','test_rgbd.py','test_reconstruction.py','test_parse_recording.py','test_gltf_material.py','test_viewer_server.py','test_release.py','test_release_server.py','test_release_previews.py','test_agent_workflow.py','test_workflow_artifacts.py','test_map_navigation.mjs','test_map_geometry.mjs']


def import_closure(root,roots):
    seen=set();pending=list(roots)
    while pending:
        name=pending.pop()
        if name in seen:continue
        path=root/'pipeline'/(name+'.py')
        if not path.is_file():raise ValueError('Missing portable module: '+name)
        seen.add(name)
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,ast.ImportFrom)and node.level==1 and node.module:
                pending.append(node.module.split('.')[0])
    return seen


def prepare(root,output):
    root=Path(root).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('Release staging already exists; choose a new output.')
    modules=import_closure(root,ROOTS)
    files=[Path(x)for x in TOP]+[Path('pipeline')/(x+'.py')for x in sorted(modules)]+[Path('pipeline/__init__.py')]
    files+=[Path('pipeline')/x for x in ASSETS]+[Path('pipeline/rtabmap/main.cpp'),Path('pipeline/rtabmap/CMakeLists.txt')]
    files+=[Path('website')/x for x in WEBSITE]
    files+=[Path('docs')/x for x in DOCS]+[Path('tests')/x for x in TESTS]
    files+=[Path('tests/ios/RecordingTests.swift')]
    files+=[Path('scripts/install_iphone.py'),Path('scripts/prepare_release.py'),Path('.github/workflows/ci.yml')]
    files +=[p.relative_to(root)for p in(root/'.agents/skills/ireal2sim').rglob('*')if p.is_file()and'__pycache__'not in p.parts]
    for folder in ['app','SceneCapture.xcodeproj']:
        files +=[p.relative_to(root)for p in (root/folder).rglob('*')if p.is_file()and'xcuserdata'not in p.parts and p.name!='.DS_Store']
    # Read/validate every file before creating a staging tree.
    payload={}
    for relative in sorted(set(files)):
        path=root/relative
        if path.is_symlink():raise ValueError('Symlink excluded: '+str(relative))
        data=path.read_bytes();text=data.decode('utf-8')
        if re.search(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}',text)or (str(Path.home())+'/')in text:
            raise ValueError('Private content in release source: '+str(relative))
        payload[relative]=data
    output.mkdir(parents=True)
    for relative,data in payload.items():
        target=output/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        if relative.as_posix()=='start' or relative.parts[0]=='scripts':target.chmod(0o755)
    hashes={str(p):hashlib.sha256(data).hexdigest()for p,data in payload.items()}
    manifest={'schema':'ireal2sim.source-release/1','version':'0.3.0','files':hashes,'portable_modules':sorted(modules),
              'excludes':['recordings','API keys/.env','weights and licensed MANO files','generated meshes/models','research outputs/configs','Xcode build/signing caches','Python environment','research-only modules'],
              'distribution':'source for user signing; no installable IPA or published TestFlight build'}
    (output/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    archive=output.parent/(output.name+'.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED)as z:
        for path in sorted(output.rglob('*')):
            if path.is_file():z.write(path,Path(output.name)/path.relative_to(output))
    print(json.dumps({'output':str(output),'source_files':len(hashes),'bytes':sum(len(v)for v in payload.values()),'archive':str(archive)},indent=2))
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args();prepare(Path(__file__).resolve().parents[1],a.output)
