// Presentation only. Original controls retain their event handlers and quality gates.
document.body.classList.add('viewer-shell');
const nav=document.querySelector('main>nav'),header=document.querySelector('main>header');
if(header&&!document.querySelector('.viewer-back')){
 const link=document.createElement('a');link.className='viewer-back';link.target='_top';link.href=document.querySelector('meta[name="ireal2sim-back"]')?.content||'../index.html';link.textContent='← Scene workspace';
 (header.querySelector('div')||header).prepend(link);
}
if(nav){
 nav.setAttribute('aria-label','Scene controls');
 const group=document.createElement('div');group.className='viewer-group';group.setAttribute('role','group');group.setAttribute('aria-label','Navigation');nav.prepend(group);
 for(const id of ['overview','topView','walk','jump','reset']){const button=document.getElementById(id);if(button)group.append(button);}
 const help=document.createElement('details');help.className='viewer-shortcuts';help.innerHTML='<summary>Controls</summary><section><h2>Explore the scene</h2><dl><dt>Orbit</dt><dd>Drag</dd><dt>Pan</dt><dd>Shift + drag</dd><dt>Zoom</dt><dd>Scroll</dd></dl><h2>In Walk mode</h2><dl><dt>Move</dt><dd><kbd>W</kbd> <kbd>A</kbd> <kbd>S</kbd> <kbd>D</kbd></dd><dt>Look</dt><dd>Drag in the scene</dd><dt>Move faster</dt><dd>Hold <kbd>Shift</kbd></dd><dt>Jump</dt><dd><kbd>Space</kbd></dd><dt>Reset / exit</dt><dd><kbd>R</kbd> / <kbd>Esc</kbd></dd></dl><p>Walking uses an estimated floor and approximate obstacles.</p></section>';nav.append(help);
 nav.querySelectorAll('details').forEach(details=>details.addEventListener('toggle',()=>{if(details.open)nav.querySelectorAll('details').forEach(other=>{if(other!==details)other.open=false;});}));
 document.addEventListener('pointerdown',event=>nav.querySelectorAll('details[open]').forEach(details=>{if(!details.contains(event.target))details.open=false;}));
 document.addEventListener('keydown',event=>{if(event.key==='Escape')nav.querySelectorAll('details[open]').forEach(details=>details.open=false);});
}
const layout=document.getElementById('focusLayout');
if(layout){const rename=()=>{const expanded=document.body.classList.contains('map-expanded'),label=expanded?'Show cameras':'Hide cameras';if(layout.textContent!==label)layout.textContent=label;layout.setAttribute('aria-pressed',String(!expanded));layout.setAttribute('aria-label',label+' alongside the scene');};new MutationObserver(rename).observe(layout,{childList:true,characterData:true,subtree:true});new MutationObserver(rename).observe(document.body,{attributes:true,attributeFilter:['class']});rename();}
const note=document.getElementById('note');if(note&&!note.closest('.viewer-estimates')){const details=document.createElement('details');details.className='viewer-estimates';const summary=document.createElement('summary');summary.textContent='Reconstruction estimates';note.before(details);details.append(summary,note);}
const trackStatus=document.getElementById('trackStatus');
if(trackStatus){const readable=()=>{const text=trackStatus.textContent,next=text.replace(/\bright_hand\b/g,'right hand').replace(/\bleft_hand\b/g,'left hand').replace(/\bbottle_model\b/g,'bottle');if(next!==text)trackStatus.textContent=next;};new MutationObserver(readable).observe(trackStatus,{childList:true,characterData:true,subtree:true});readable();}
const title=document.querySelector('title');if(title){const brandTitle=()=>{const next=(document.querySelector('h1')?.textContent||'Scene viewer')+' · iReal2Sim';if(document.title!==next)document.title=next;};new MutationObserver(brandTitle).observe(title,{childList:true,characterData:true,subtree:true});brandTitle();}
