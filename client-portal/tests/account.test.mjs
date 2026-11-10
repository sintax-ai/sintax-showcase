import test from 'node:test';
import assert from 'node:assert/strict';
import { createAccountClient, parseAuthCallback, validateAccountField } from '../public/account.mjs';

const user = (extra = {}) => ({ id:'user-a', email:'alex@example.test', user_metadata:{display_name:'Alex'}, ...extra });
const token = () => ({ access_token:'new-access', refresh_token:'new-refresh', expires_in:3600, user:user() });
test('invitation links become verified password-setup callbacks and scrub credentials',async()=>{
  const result=parseAuthCallback('https://portal-alt.example.test/#access_token=invite-access&refresh_token=invite-refresh&expires_in=3600&type=invite');
  assert.equal(result.error,null);
  assert.equal(result.type,'recovery');
  assert.equal(result.cleanURL,'https://portal-alt.example.test/');
  const f=fixture({raw:async(path,options)=>{
    assert.equal(path,'/auth/v1/user');
    assert.equal(options.headers.Authorization,'Bearer invite-access');
    return user();
  }});
  const verified=await f.client.validateCallback(result);
  assert.equal(verified.user.id,'user-a');
});
test('invitation links without valid credentials cannot start password setup',()=>{
  for(const fragment of ['type=invite','type=invite&access_token=x','type=invite&access_token=x&refresh_token=y&expires_at=1']) {
    assert.equal(parseAuthCallback('https://portal-alt.example.test/#'+fragment).session,null);
  }
});
function fixture({ raw, auth } = {}) {
  let session={id:'user-a',email:'alex@example.test',access_token:'old-access',refresh_token:'old-refresh',epoch:1};
  const requests=[],adopted=[];
  const invoke=async(kind,path,options={})=>{
    requests.push({kind,path,options});
    return (kind==='call'?raw:auth)?.(path,options) ?? (kind==='call'?token():user());
  };
  const client=createAccountClient({call:(p,o)=>invoke('call',p,o),api:(p,o)=>invoke('api',p,o),getSession:()=>session,
    setSession:t=>{adopted.push(t);session={...session,...t,id:t.user.id,email:t.user.email};}});
  return {client,requests,adopted,get session(){return session;},replace:s=>{session=s;}};
}

test('recovery callback scrubs credentials while retaining non-auth query state',()=>{
  const result=parseAuthCallback('https://portal-alt.example.test/?view=account&auth=recovery#access_token=fake-access&refresh_token=fake-refresh&expires_in=3600&type=recovery&token_type=bearer');
  assert.equal(result.cleanURL,'https://portal-alt.example.test/?view=account');
  assert.equal(result.type,'recovery');
  assert.equal(result.session.access_token,'fake-access');
  assert.equal(result.error,null);
});
test('incomplete, expired and unknown callback types cannot become sessions',()=>{
  for(const fragment of ['access_token=x&type=recovery','access_token=x&refresh_token=y&type=signup','error=access_denied&error_code=otp_expired&error_description=private-detail']){
    const result=parseAuthCallback('https://portal.example.test/#'+fragment);
    assert.equal(result.session,null); assert.ok(result.error);
    assert.equal(result.cleanURL,'https://portal.example.test/');
  }
});
test('ordinary dashboard links remain ordinary links',()=>{
  assert.deepEqual(parseAuthCallback('https://portal.example.test/?view=calls#main'),{
    cleanURL:'https://portal.example.test/?view=calls#main',session:null,type:null,error:null,
  });
});
test('account fields reject whitespace-only names, malformed email and weak new passwords',()=>{
  assert.ok(validateAccountField('display_name','  '));
  assert.ok(validateAccountField('display_name','x'.repeat(201)));
  assert.equal(validateAccountField('display_name','  Alex Morgan  '),null);
  assert.ok(validateAccountField('email','alex@'));
  assert.equal(validateAccountField('email',' alex@example.test '),null);
  assert.ok(validateAccountField('password','short'));
  assert.equal(validateAccountField('password','strong-fictional-password'),null);
});
test('display-name save updates only display metadata and returns the verified current user',async()=>{
  const f=fixture({auth:async(path,options)=>{
    assert.equal(path,'/auth/v1/user');assert.equal(options.method,'PUT');
    assert.deepEqual(options.body,{data:{display_name:'Alex Morgan'}});
    return user({user_metadata:{display_name:'Alex Morgan',locale:'en'}});
  }});
  const result=await f.client.saveName(' Alex Morgan ');
  assert.equal(result.user_metadata.display_name,'Alex Morgan');assert.equal(result.user_metadata.locale,'en');
  assert.equal(f.session.email,'alex@example.test');
});
test('wrong current password leaves the existing session intact and never requests an account update',async()=>{
  const error=Object.assign(new Error('Wrong current password'),{kind:'credentials'});
  const f=fixture({raw:async()=>{throw error;}});
  await assert.rejects(f.client.changeEmail('new@example.test','wrong','https://portal-alt.example.test/?auth=email-change'),e=>e===error);
  assert.equal(f.adopted.length,0);assert.equal(f.session.access_token,'old-access');
  assert.equal(f.requests.some(r=>r.kind==='api'),false);
});
test('email change reauthenticates and preserves pending confirmation instead of changing alert settings',async()=>{
  const f=fixture({raw:async(path,options)=>{
    assert.equal(path,'/auth/v1/token?grant_type=password');assert.equal(options.auth,false);
    assert.deepEqual(options.body,{email:'alex@example.test',password:'fictional-current-password'});return token();
  },auth:async(path,options)=>{
    assert.equal(path,'/auth/v1/user?redirect_to=https%3A%2F%2Fportal-alt.example.test%2F%3Fauth%3Demail-change');
    assert.deepEqual(options.body,{email:'new@example.test'});
    return user({new_email:'new@example.test'});
  }});
  const result=await f.client.changeEmail(' new@example.test ','fictional-current-password','https://portal-alt.example.test/?auth=email-change');
  assert.equal(result.email,'alex@example.test');assert.equal(result.new_email,'new@example.test');
  assert.equal(f.session.email,'alex@example.test');
  assert.equal(JSON.stringify(f.adopted).includes('fictional-current-password'),false);
  assert.equal(f.requests.every(r=>r.path.startsWith('/auth/')),true);
});
test('password changes require reauthentication and send only the new password to Auth',async()=>{
  const f=fixture({auth:async(path,options)=>{
    assert.equal(path,'/auth/v1/user');assert.equal(options.method,'PUT');
    assert.deepEqual(options.body,{password:'fictional-new-password'});return user();
  }});
  await f.client.changePassword('fictional-new-password','fictional-current-password');
  assert.equal(f.requests[0].options.body.password,'fictional-current-password');
  assert.equal(JSON.stringify(f.adopted).includes('fictional-new-password'),false);
});
test('recovery password updates require a recovery session and do not ask for the forgotten password',async()=>{
  const f=fixture({auth:async(path,options)=>{
    assert.equal(path,'/auth/v1/user');assert.deepEqual(options.body,{password:'fictional-new-password'});return user();
  }});
  await assert.rejects(f.client.completeRecovery('fictional-new-password'));
  assert.equal(f.requests.length,0);
  f.replace({...f.session,recovery:true});
  await f.client.completeRecovery('fictional-new-password');
  assert.equal(f.requests.length,1);assert.equal(f.requests[0].kind,'api');
});
test('sign-out of other devices uses others scope and keeps this session',async()=>{
  const f=fixture({auth:async(path,options)=>{
    assert.equal(path,'/auth/v1/logout?scope=others');assert.equal(options.method,'POST');return null;
  }});
  await f.client.signOutOthers();assert.equal(f.session.access_token,'old-access');assert.equal(f.adopted.length,0);
});
test('recovery requests use an approved return URL and never adopt a session',async()=>{
  const f=fixture({raw:async(path,options)=>{
    assert.equal(path,'/auth/v1/recover?redirect_to=https%3A%2F%2Fportal-alt.example.test%2F%3Fauth%3Drecovery');
    assert.equal(options.auth,false);assert.deepEqual(options.body,{email:'alex@example.test'});return {};
  }});
  await f.client.requestRecovery(' alex@example.test ','https://portal-alt.example.test/?auth=recovery');
  assert.equal(f.adopted.length,0);
  await assert.rejects(f.client.requestRecovery('alex@example.test','https://evil.example/reset'));
  assert.equal(f.requests.length,1);
});
test('expired recovery tokens fail validation without persisting any callback credentials',async()=>{
  const f=fixture({raw:async()=>{throw Object.assign(new Error('Expired'),{kind:'expired'});}});
  const callback=parseAuthCallback('https://portal.example.test/#access_token=fake&refresh_token=refresh&type=recovery');
  await assert.rejects(f.client.validateCallback(callback));assert.equal(f.adopted.length,0);
});
test('valid recovery credentials are checked with Auth before returning a usable session',async()=>{
  const f=fixture({raw:async(path,options)=>{
    assert.equal(path,'/auth/v1/user');assert.equal(options.auth,false);
    assert.equal(options.headers.Authorization,'Bearer fake-access');return user();
  }});
  const callback=parseAuthCallback('https://portal.example.test/#access_token=fake-access&refresh_token=fake-refresh&type=recovery');
  const result=await f.client.validateCallback(callback);
  assert.equal(result.user.id,'user-a');assert.equal(result.access_token,'fake-access');assert.equal(f.adopted.length,0);
});
test('late reauthentication cannot restore a signed-out or replaced session',async()=>{
  let complete;const pending=new Promise(resolve=>{complete=resolve;});
  const f=fixture({raw:()=>pending});
  const changing=f.client.changePassword('fictional-new-password','fictional-current-password');
  f.replace({id:'user-a',email:'alex@example.test',epoch:3,access_token:'different-login'});
  complete(token());
  await assert.rejects(changing,e=>e.kind==='session_changed');assert.equal(f.adopted.length,0);
});
test('a reauthentication response for another user is rejected',async()=>{
  const f=fixture({raw:async()=>({...token(),user:user({id:'user-b'})})});
  await assert.rejects(f.client.changePassword('fictional-new-password','fictional-current-password'),e=>e.kind==='session_changed');
  assert.equal(f.adopted.length,0);
});


test('first secure-email confirmation is informational and never creates a session',()=>{
  const fragment=new URLSearchParams({message:'Confirmation link accepted. Please proceed to confirm link sent to the other email',sb:''});
  const result=parseAuthCallback('https://portal-alt.example.test/?auth=email-change#'+fragment);
  assert.equal(result.error,null);assert.equal(result.pendingEmailConfirmation,true);
  assert.equal(result.session,null);assert.equal(result.cleanURL,'https://portal-alt.example.test/');
});
test('arbitrary message callbacks and recovery messages cannot impersonate confirmation',()=>{
  for(const value of ['?auth=email-change#message=anything&sb=','?auth=recovery#message=Confirmation+link+accepted.&sb=']){
    assert.equal(parseAuthCallback('https://portal.example.test/'+value).error,'invalid_callback');
  }
});
