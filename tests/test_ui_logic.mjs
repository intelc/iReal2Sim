import test from 'node:test';
import assert from 'node:assert/strict';
import{stateMeta,filterJobs,startState,recordingTitle,captureDate}from '../pipeline/ui_logic.mjs';
test('available scenes remain usable while optional fitting needs setup',()=>{
 assert.equal(stateMeta('needs_setup',true).action,'Open scene');assert.equal(stateMeta('needs_setup',true).label,'Scene ready');assert.equal(stateMeta('needs_setup',false).action,'Continue setup');assert.equal(stateMeta('failed',true).action,'View details');
});
test('recording search matches names and IDs and respects scene availability',()=>{
 const jobs=[{id:'abc',name:'export',label:'Kitchen',status:'needs_setup',viewer_url:'/scene'},{id:'def',name:'Bedroom',status:'failed'}];
 assert.deepEqual(filterJobs(jobs,' KITCHEN ','ready'),[jobs[0]]);assert.deepEqual(filterJobs(jobs,'def','attention'),[jobs[1]]);assert.deepEqual(filterJobs(jobs,'Bedroom','ready'),[]);assert.deepEqual(filterJobs(jobs,'missing'),[]);
});
test('start requires source, dependencies, no running job, and a key only for review',()=>{
 const ready={healthReady:true,busy:false,submitting:false,session:'/capture',review:false,key:'',configuredKey:false};assert.equal(startState(ready).disabled,false);
 for(const change of[{healthReady:false},{busy:true},{submitting:true},{session:' '},{review:true}])assert.equal(startState({...ready,...change}).disabled,true);
 assert.equal(startState({...ready,review:true,key:'key'}).disabled,false);assert.equal(startState({...ready,review:true,configuredKey:true}).disabled,false);
});
test('export timestamp is readable without discarding a custom name',()=>{
 const exportName='2026-11-04T09-34-56Z-id';assert.equal(recordingTitle(exportName),'Room capture');assert.equal(captureDate(exportName),'2026-11-04T09:34:56Z');assert.equal(recordingTitle('Kitchen'),'Kitchen');assert.equal(captureDate('Kitchen'),null);
});
