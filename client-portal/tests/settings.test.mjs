import test from 'node:test';
import assert from 'node:assert/strict';
import { createSettingsClient } from '../public/settings.mjs';

const B='11111111-1111-4111-8111-111111111111', U='22222222-2222-4222-8222-222222222222';
const R='33333333-3333-4333-8333-333333333333', OTHER='44444444-4444-4444-8444-444444444444';
const stamp='2026-11-04T05:00:00.000000+00:00', newer='2026-11-04T05:01:00.000000+00:00';
const preference=(extra={})=>({id:B,notify_email:'requests@example.test',request_alerts_enabled:true,settings_updated_at:stamp,...extra});
const request=(extra={})=>({id:R,business_id:B,requested_by:U,category:'hours',description:'Close at five.',status:'submitted',created_at:stamp,...extra});
const url=path=>new URL(path,'https://project.example');
const offline=()=>Object.assign(new Error('Network unavailable'),{kind:'offline'});

test('settings load scopes every read to its business and fetches the current user role',async()=>{
  const paths=[];
  const client=createSettingsClient({api:async path=>{
    const q=url(path);paths.push(q);
    if(q.pathname.endsWith('/businesses')){assert.equal(q.searchParams.get('id'),'eq.'+B);return [preference()];}
    assert.equal(q.searchParams.get('business_id'),'eq.'+B);
    if(q.pathname.endsWith('/memberships')){assert.equal(q.searchParams.get('user_id'),'eq.'+U);return [{role:'owner'}];}
    return [request()];
  }});
  const result=await client.load(B,U);
  assert.equal(result.role,'owner');assert.equal(result.preferences.notify_email,'requests@example.test');
  assert.equal(result.requests[0].id,R);assert.equal(paths.length,3);
});
test('an invalid or missing business identity fails before a network request',async()=>{
  let reads=0;const client=createSettingsClient({api:async()=>{reads++;return [];}});
  await assert.rejects(client.load('',U));await assert.rejects(client.load(B,'u&role=owner'));
  assert.equal(reads,0);
});
test('unexpected business rows cannot become settings for the selected business',async()=>{
  const client=createSettingsClient({api:async path=>url(path).pathname.endsWith('/businesses')?[preference({id:OTHER})]:url(path).pathname.endsWith('/memberships')?[{role:'owner'}]:[]});
  await assert.rejects(client.load(B,U));
});
test('turning alerts off preserves the address and touches no sign-in fields',async()=>{
  let current=preference();const client=createSettingsClient({api:async(path,options={})=>{
    const q=url(path);assert.ok(q.pathname.endsWith('/businesses'));assert.equal(q.searchParams.get('id'),'eq.'+B);
    if(options.method==='PATCH'){
      assert.equal(q.searchParams.get('settings_updated_at'),'eq.'+stamp);
      assert.deepEqual(options.body,{notify_email:'requests@example.test',request_alerts_enabled:false});
      current=preference({request_alerts_enabled:false,settings_updated_at:newer});
    }
    return [current];
  }});
  const saved=await client.saveAlerts({businessId:B,settingsUpdatedAt:stamp,email:' requests@example.test ',enabled:false});
  assert.equal(saved.notify_email,'requests@example.test');assert.equal(saved.request_alerts_enabled,false);
});
test('alert validation rejects invalid recipients, nonboolean switches and missing versions',async()=>{
  let writes=0;const client=createSettingsClient({api:async()=>{writes++;return [];}});
  for(const override of [{email:'not-an-email'},{email:'',enabled:true},{enabled:'false'},{settingsUpdatedAt:''}]){
    await assert.rejects(client.saveAlerts({businessId:B,settingsUpdatedAt:stamp,email:'requests@example.test',enabled:true,...override}));
  }
  assert.equal(writes,0);
});
test('a stale optimistic update reports a conflict instead of overwriting a newer preference',async()=>{
  let writes=0;const client=createSettingsClient({api:async(path,options)=>{assert.equal(options.method,'PATCH');writes++;return [];}});
  await assert.rejects(client.saveAlerts({businessId:B,settingsUpdatedAt:stamp,email:'requests@example.test',enabled:false}),e=>e.kind==='conflict');
  assert.equal(writes,1);
});
test('a successful write with failed readback is reported as unconfirmed',async()=>{
  const client=createSettingsClient({api:async(path,options={})=>{if(options.method==='PATCH')return [preference({request_alerts_enabled:false,settings_updated_at:newer})];throw offline();}});
  await assert.rejects(client.saveAlerts({businessId:B,settingsUpdatedAt:stamp,email:'requests@example.test',enabled:false}),e=>e.kind==='unconfirmed');
});
test('a lost preference response can be confirmed by its scoped current values without a second write',async()=>{
  let writes=0;const client=createSettingsClient({api:async(path,options={})=>{
    if(options.method==='PATCH'){writes++;throw offline();}
    assert.equal(url(path).searchParams.get('id'),'eq.'+B);
    return [preference({request_alerts_enabled:false,settings_updated_at:newer})];
  }});
  const saved=await client.saveAlerts({businessId:B,settingsUpdatedAt:stamp,email:'requests@example.test',enabled:false});
  assert.equal(saved.request_alerts_enabled,false);assert.equal(writes,1);
});
test('a permission denial cannot be mistaken for a saved matching preference',async()=>{
  const denied=Object.assign(new Error('Denied'),{kind:'denied',status:403});let reads=0;
  const client=createSettingsClient({api:async()=>{reads++;throw denied;}});
  await assert.rejects(client.saveAlerts({businessId:B,settingsUpdatedAt:stamp,email:'requests@example.test',enabled:false}),e=>e===denied);
  assert.equal(reads,1);
});
test('a change submission lets the server assign requester, status and timestamp',async()=>{
  const client=createSettingsClient({api:async(path,options)=>{
    assert.equal(path,'/rest/v1/receptionist_change_requests');assert.equal(options.method,'POST');
    assert.deepEqual(options.body,{id:R,business_id:B,category:'hours',description:'Close at five.'});return [request()];
  }});
  const saved=await client.submitChange({businessId:B,userId:U,id:R,category:'hours',description:' Close at five. '});
  assert.equal(saved.requested_by,U);assert.equal(saved.status,'submitted');
});
test('a lost change-submission response resolves the same ID without creating a duplicate',async()=>{
  let posts=0;const client=createSettingsClient({api:async(path,options={})=>{
    if(options.method==='POST'){posts++;throw offline();}
    const q=url(path);assert.equal(q.searchParams.get('business_id'),'eq.'+B);assert.equal(q.searchParams.get('id'),'eq.'+R);
    assert.equal(q.searchParams.get('requested_by'),'eq.'+U);return [request()];
  }});
  const saved=await client.submitChange({businessId:B,userId:U,id:R,category:'hours',description:'Close at five.'});
  assert.equal(saved.id,R);assert.equal(posts,1);
});
test('a duplicate ID for different text or another requester is not falsely accepted',async()=>{
  for(const mismatch of [{description:'A different change.'},{requested_by:OTHER}]){
    const client=createSettingsClient({api:async(path,options={})=>{
      if(options.method==='POST')throw Object.assign(new Error('Duplicate'),{status:409,code:'23505'});
      return [request(mismatch)];
    }});
    await assert.rejects(client.submitChange({businessId:B,userId:U,id:R,category:'hours',description:'Close at five.'}),e=>e.kind==='unconfirmed');
  }
});
test('invalid request categories, empty or oversized descriptions and IDs never reach the API',async()=>{
  let writes=0;const client=createSettingsClient({api:async()=>{writes++;}});
  for(const override of [{category:'unknown'},{description:' \t\n'},{description:'x'.repeat(4001)},{id:'not-a-uuid'}]){
    await assert.rejects(client.submitChange({businessId:B,userId:U,id:R,category:'hours',description:'Close at five.',...override}));
  }
  assert.equal(writes,0);
});
test('a missing saved request after a dropped response remains unconfirmed',async()=>{
  const client=createSettingsClient({api:async(path,options={})=>{if(options.method==='POST')throw offline();return [];}});
  await assert.rejects(client.submitChange({businessId:B,userId:U,id:R,category:'hours',description:'Close at five.'}),e=>e.kind==='unconfirmed');
});
