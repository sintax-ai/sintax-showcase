import { test } from 'node:test';
import assert from 'node:assert/strict';
import { recordingState, recordingURL, linkedRequests, callMatches, durationLabel } from '../public/calls.mjs';
import { moveSelected } from '../public/bulk-selection.mjs';
test('recording availability is honest; no expiry means indefinite',()=>{
 assert.equal(recordingState({recording_status:'available',recording_path:'a',recording_expires_at:null}),'available');
 assert.equal(recordingState({recording_status:'available'}),'unavailable');
 assert.equal(recordingState({recording_status:'available',recording_path:'a',recording_expires_at:'2000-01-01'}),'expired');
 assert.equal(recordingState({recording_status:'pending',started_at:'2000-01-01'}),'unavailable');
 assert.equal(recordingState({recording_status:'pending',started_at:new Date().toISOString()}),'pending');
});
test('playback accepts only this project signed storage URLs',()=>{
 assert.equal(recordingURL('https://example.test','/object/sign/call-recordings/a?token=x'),'https://example.test/storage/v1/object/sign/call-recordings/a?token=x');
 for(const url of ['https://evil.test/object/sign/x','javascript:alert(1)','/object/public/call-recordings/a','//evil.test/x']) assert.throws(()=>recordingURL('https://example.test',url));
});
test('request linking and searching stay within business and preserve trash',()=>{
 const c={id:'c',business_id:'b',session_id:'room-123',mode:'demo'};
 const rows=[{id:'r',business_id:'b',call_id:'c',name:'Jamie',phone:'+15555550101',is_trashed:true},{id:'wrong',business_id:'other',call_id:'c',name:'Wrong'}];
 assert.deepEqual(linkedRequests(c,rows).map(r=>r.id),['r']);
 assert.ok(callMatches(c,rows,'Jamie')); assert.ok(callMatches(c,rows,'55550101')); assert.ok(callMatches(c,rows,'room-123'));
 assert.equal(callMatches(c,rows,'Wrong'),false);
 assert.equal(durationLabel(null),'Duration unavailable'); assert.equal(durationLabel(65),'1m 5s');
});
test('bulk calls route to calls table with the same version protection',async()=>{
 let path;
 const row={id:'c',business_id:'b',updated_at:'stamp'};
 const result=await moveSelected([row],{businessId:'b',removing:true,table:'calls',columns:'id',request:async p=>{path=p;return [{...row,is_trashed:true}];}});
 assert.ok(path.startsWith('/rest/v1/calls?')); assert.ok(path.includes('updated_at=eq.stamp')); assert.equal(result.updated.length,1);
});
