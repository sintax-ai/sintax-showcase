import test from 'node:test';
import assert from 'node:assert/strict';
import {createHTTPCall} from '../public/http.mjs';
import {createSettingsClient} from '../public/settings.mjs';
const B='11111111-1111-4111-8111-111111111111',U='22222222-2222-4222-8222-222222222222',R='33333333-3333-4333-8333-333333333333';
const row={id:R,business_id:B,requested_by:U,category:'hours',description:'Close at five.',status:'submitted',created_at:'2026-11-04T05:00:00Z'};
function fixture({readbackFails=false}={}){
  let writes=0;
  const api=createHTTPCall({url:'https://project.example',publishableKey:'public-fixture',getSession:()=>({access_token:'fake-access'}),fetchImpl:async(url,options)=>{
    if(options.method==='POST'){writes++;return {ok:true,status:201,text:async()=>{throw new TypeError('Body stream interrupted');}};}
    if(readbackFails)throw new TypeError('Offline');
    return {ok:true,status:200,text:async()=>JSON.stringify([row])};
  }});
  return {api,get writes(){return writes;}};
}
test('a committed request with an interrupted response body is confirmed by same-ID readback',async()=>{
  const f=fixture(),client=createSettingsClient({api:f.api});
  const saved=await client.submitChange({businessId:B,userId:U,id:R,category:'hours',description:'Close at five.'});
  assert.equal(saved.id,R);assert.equal(f.writes,1);
});
test('body interruption and unavailable readback remain unconfirmed for stable-ID retry',async()=>{
  const f=fixture({readbackFails:true}),client=createSettingsClient({api:f.api});
  await assert.rejects(client.submitChange({businessId:B,userId:U,id:R,category:'hours',description:'Close at five.'}),e=>e.kind==='unconfirmed');
  assert.equal(f.writes,1);
});
