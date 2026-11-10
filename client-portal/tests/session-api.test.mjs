import test from 'node:test';
import assert from 'node:assert/strict';
import { createSessionAPI } from '../public/session-api.mjs';

function fixture(call) {
  let state={id:'user-a',access_token:'old',refresh_token:'refresh-a',expires_at:Date.now()/1000+3600,epoch:1};
  let ended=0,adopted=0;
  const client=createSessionAPI({call,getSession:()=>state,setSession:value=>{adopted++;state={...state,...value};},onExpired:()=>{ended++;state=null;}});
  return {client,replace:s=>{state=s;},get state(){return state;},get ended(){return ended;},get adopted(){return adopted;}};
}
const expired=()=>Object.assign(new Error('Expired'),{kind:'expired'});
test('a 401 response retries with a renewed session of the same login',async()=>{
  let calls=0,refreshes=0;
  const f=fixture(async path=>{
    if(path.startsWith('/auth/v1/token')){refreshes++;return {access_token:'new',refresh_token:'new-refresh',expires_at:Date.now()/1000+3600};}
    if(++calls===1)throw expired();return {saved:true};
  });
  assert.deepEqual(await f.client.api('/rest/v1/businesses',{method:'PATCH'}),{saved:true});
  assert.equal(calls,2);assert.equal(refreshes,1);assert.equal(f.state.access_token,'new');
});
test('an old 401 cannot retry a write using a newer login',async()=>{
  let reject,calls=0;
  const pending=new Promise((resolve,fail)=>{reject=fail;});
  const f=fixture(async()=>{calls++;return pending;});
  const saving=f.client.api('/rest/v1/businesses',{method:'PATCH'});
  f.replace({id:'user-b',access_token:'other',refresh_token:'refresh-b',expires_at:Date.now()/1000+3600,epoch:3});
  reject(expired());
  await assert.rejects(saving,error=>error.kind==='session_changed');
  assert.equal(calls,1);assert.equal(f.state.id,'user-b');assert.equal(f.ended,0);
});
test('a late refresh cannot restore a signed-out account',async()=>{
  let complete;const pending=new Promise(resolve=>{complete=resolve;});
  const f=fixture(async()=>pending);
  const renewing=f.client.renew();f.replace(null);
  complete({access_token:'stale',refresh_token:'stale-refresh'});
  await assert.rejects(renewing,error=>error.kind==='session_changed');
  assert.equal(f.adopted,0);assert.equal(f.state,null);
});
test('a failed refresh from an old login does not sign out a newer login',async()=>{
  let reject;const pending=new Promise((resolve,fail)=>{reject=fail;});
  const f=fixture(async()=>pending);
  const renewing=f.client.renew();
  f.replace({id:'user-b',access_token:'other',refresh_token:'refresh-b',expires_at:Date.now()/1000+3600,epoch:3});
  reject(expired());await assert.rejects(renewing,error=>error.kind==='session_changed');
  assert.equal(f.ended,0);assert.equal(f.state.id,'user-b');
});
test('parallel renewals of one login share one refresh request',async()=>{
  let calls=0,complete;const pending=new Promise(resolve=>{complete=resolve;});
  const f=fixture(async()=>{calls++;return pending;});
  const first=f.client.renew(),second=f.client.renew();
  complete({access_token:'new',refresh_token:'new-refresh',expires_at:Date.now()/1000+3600});
  await Promise.all([first,second]);assert.equal(calls,1);assert.equal(f.adopted,1);
});
test('a new login can renew without waiting for an old login refresh',async()=>{
  let complete;const pending=new Promise(resolve=>{complete=resolve;});
  const f=fixture(async(path,options)=>options.body.refresh_token==='refresh-a'?pending:{access_token:'new-b',refresh_token:'new-refresh-b'});
  const first=f.client.renew();
  f.replace({id:'user-b',access_token:'b',refresh_token:'refresh-b',expires_at:0,epoch:3});
  await f.client.renew();assert.equal(f.state.access_token,'new-b');
  complete({access_token:'old-a'});await assert.rejects(first,error=>error.kind==='session_changed');
  assert.equal(f.state.access_token,'new-b');
});
test('network failures during refresh preserve the current session for retry',async()=>{
  const error=Object.assign(new Error('Offline'),{kind:'offline'});
  const f=fixture(async()=>{throw error;});
  await assert.rejects(f.client.renew(),e=>e===error);assert.equal(f.ended,0);assert.equal(f.state.id,'user-a');
});
test('a rejected current refresh ends the current sign-in',async()=>{
  const f=fixture(async()=>{throw expired();});
  await assert.rejects(f.client.renew(),error=>error.kind==='expired');assert.equal(f.ended,1);assert.equal(f.state,null);
});
test('late successful reads from an old session cannot be painted into a new session',async()=>{
  let complete;const pending=new Promise(resolve=>{complete=resolve;});
  const f=fixture(async()=>pending);
  const loading=f.client.api('/rest/v1/businesses');f.replace({...f.state,epoch:4});complete([{id:'old-business'}]);
  await assert.rejects(loading,error=>error.kind==='session_changed');
});
