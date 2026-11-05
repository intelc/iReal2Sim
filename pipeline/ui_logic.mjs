export function stateMeta(status,hasScene=false){
 const values={complete:{label:'Ready to explore',tone:'good',action:'Open scene'},needs_setup:{label:hasScene?'Scene ready':'Setup needed',tone:hasScene?'good':'warn',action:hasScene?'Open scene':'Continue setup'},failed:{label:'Needs attention',tone:'bad',action:'View details'},running:{label:'Processing',tone:'running',action:'View progress'},queued:{label:'Queued',tone:'running',action:'View progress'},planned:{label:'Ready to process',tone:'',action:'View workflow'}};
 return values[status]||{label:'In progress',tone:'',action:'View details'};
}
export function recordingTitle(name){
 return /^\d{4}-\d\d-\d\dT\d\d-\d\d-\d\dZ-/.test(name||'')?'Room capture':String(name||'Untitled recording');
}
export function captureDate(name){
 const m=String(name||'').match(/^(\d{4}-\d\d-\d\d)T(\d\d)-(\d\d)-(\d\d)Z/);
 return m?`${m[1]}T${m[2]}:${m[3]}:${m[4]}Z`:null;
}
export function filterJobs(jobs,query='',filter='all'){
 const search=query.trim().toLocaleLowerCase();
 return jobs.filter(j=>{const text=[j.label,j.name,j.id].filter(Boolean).join(' ').toLocaleLowerCase();return(!search||text.includes(search))&&(filter==='all'||filter==='ready'&&!!j.viewer_url||filter==='attention'&&['failed','needs_setup'].includes(j.status));});
}
export function startState({healthReady,busy,submitting,session,review,key,configuredKey}){
 if(submitting)return{disabled:true,label:'Starting…',reason:''};
 if(busy)return{disabled:true,label:'Build scene',reason:'A recording is processing. You can start another when it finishes.'};
 if(!healthReady)return{disabled:true,label:'Build scene',reason:'Finish desktop setup to start processing.'};
 if(!session.trim())return{disabled:true,label:'Build scene',reason:'Choose an exported recording folder to continue.'};
 if(review&&!key.trim()&&!configuredKey)return{disabled:true,label:'Build scene',reason:'Add an API key or turn off vision review.'};
 return{disabled:false,label:'Build scene',reason:''};
}
