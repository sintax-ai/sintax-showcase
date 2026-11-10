import test from 'node:test';
import assert from 'node:assert/strict';
import {createSettingsUI} from '../public/settings-ui.mjs';
import {createSettingsClient} from '../public/settings.mjs';
import {createHTTPCall} from '../public/http.mjs';
const B='11111111-1111-4111-8111-111111111111',U='22222222-2222-4222-8222-222222222222';
function fixture({submitClient}={}){
 const nodes=new Map();
 function node(id=''){
  if(nodes.has(id))return nodes.get(id);
  const n={value:'',hidden:false,checked:false,textContent:'',dataset:{},listeners:{},children:[],
   setAttribute(){},focus(){},replaceChildren(...items){this.children=items;},append(...items){this.children.push(...items);},
   querySelectorAll(){return [];},querySelector(){return node('tablist');},addEventListener(event,handler){this.listeners[event]=handler;}};
  nodes.set(id,n);return n;
 }
 const tabs=['Account','Alerts','Changes'].map(name=>{const n=node('tab'+name);n.dataset.settingsPanel='panel'+name;return n;});
 const previous={document:globalThis.document,Option:globalThis.Option};
 globalThis.document={getElementById:node,querySelectorAll:()=>tabs,createElement:()=>node('dynamic'+nodes.size)};
 globalThis.Option=function(label,value){return {label,value};};
 const user={id:U,email:'old@example.test',user_metadata:{display_name:'Saved Name'}};
 const settingsClient={load:async()=>({role:'member',preferences:{notify_email:'alerts@example.test',request_alerts_enabled:true},requests:[]}),...submitClient};
 const ui=createSettingsUI({account:{getUser:async()=>user,changeEmail:async()=>({...user,new_email:'new@example.test'})},settingsClient,
  getContext:()=>({userId:U,business:{id:B,name:'Fictional'},session:{epoch:1}}),onAccountUpdated(){},onBack(){},returnOrigin:'https://portal-alt.example.test'});
 return {ui,node,restore(){globalThis.document=previous.document;globalThis.Option=previous.Option;}};
}
const settled=()=>new Promise(resolve=>setImmediate(resolve));
test('pending email change preserves an unsaved display-name draft and its dirty state',async()=>{
 const f=fixture();try{
  await f.ui.open();f.node('profileName').value='Unsaved Name';f.node('newEmail').value='new@example.test';f.node('emailCurrentPassword').value='fictional';
  f.node('emailChangeForm').listeners.submit({preventDefault(){}});await settled();
  assert.equal(f.node('profileName').value,'Unsaved Name');assert.equal(f.ui.hasUnsavedChanges(),true);
  assert.match(f.node('pendingEmail').textContent,/new@example.test/);
 }finally{f.restore();}
});
test('interrupted committed submission keeps its ID through UI retry and confirms only one saved request',async()=>{
 const posts=[],saved=new Map();let readingFails=true;
 const api=createHTTPCall({url:'https://project.example',publishableKey:'fake-public',getSession:()=>({access_token:'fake'}),fetchImpl:async(url,options)=>{
  if(options.method==='POST'){
   const row=JSON.parse(options.body);posts.push(row.id);
   if(saved.has(row.id))return {ok:false,status:409,text:async()=>JSON.stringify({code:'23505'})};
   saved.set(row.id,{...row,requested_by:U,status:'submitted',created_at:'2026-11-04T05:00:00Z'});
   return {ok:true,status:201,text:async()=>{throw new TypeError('Interrupted body');}};
  }
  if(readingFails)throw new TypeError('Offline');
  return {ok:true,status:200,text:async()=>JSON.stringify([...saved.values()])};
 }});
 const client=createSettingsClient({api});
 const g=fixture({submitClient:{submitChange:client.submitChange}});
 try{
  await g.ui.open();g.node('changeCategory').value='hours';g.node('changeDescription').value='Close at five.';
  g.node('receptionistChangeForm').listeners.submit({preventDefault(){}});await settled();
  assert.equal(g.node('submitReceptionistChange').textContent,'Retry this submission');assert.equal(g.node('changeDescription').readOnly,true);
  assert.equal(g.ui.hasUnsavedChanges(),true);assert.equal(saved.size,1);
  readingFails=false;g.node('receptionistChangeForm').listeners.submit({preventDefault(){}});await settled();
  assert.equal(posts.length,2);assert.equal(posts[0],posts[1]);assert.equal(saved.size,1);
  assert.equal(g.node('changeDescription').value,'');assert.match(g.node('changeNotice').textContent,/saved for SINTAX review/);
 }finally{g.restore();}
});
