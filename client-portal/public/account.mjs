// Identity operations stay separate from business preferences and permissions.
const AUTH_KEYS = ['access_token','refresh_token','expires_in','expires_at','token_type','type','error','error_code','error_description','code','message','sb'];
const FIRST_EMAIL_CONFIRMATION = 'Confirmation link accepted. Please proceed to confirm link sent to the other email';
const RETURN_ORIGINS = new Set(['https://portal.example.test','https://portal-alt.example.test']);
const failure = (kind,message) => Object.assign(new Error(message),{kind});

export function parseAuthCallback(value) {
  const url=new URL(value);
  const hash=new URLSearchParams(url.hash.slice(1));
  const present=AUTH_KEYS.some(key=>hash.has(key)||url.searchParams.has(key)) || url.searchParams.has('auth');
  if(!present) return {cleanURL:url.href,session:null,type:null,error:null};
  // Email link credentials must leave the address bar even when the link is bad.
  const params=new URLSearchParams(url.search);
  for(const [key,item] of hash) params.set(key,item);
  for(const key of [...AUTH_KEYS,'auth']) url.searchParams.delete(key);
  url.hash='';
  const cleanURL=url.href;
  // An invitation also establishes a session for choosing the first password.
  // Both flows still validate the access token with Auth before adopting it.
  const type=params.get('type')==='invite'?'recovery':params.get('type');
  const access=params.get('access_token'), refresh=params.get('refresh_token');
  if(params.get('auth')==='email-change'&&params.get('message')===FIRST_EMAIL_CONFIRMATION&&params.has('sb')
    &&!access&&!refresh&&!params.has('error')&&!params.has('error_code')) {
    // Informational only: neither a new identity nor proof of a complete change.
    return {cleanURL,type:'email_change',session:null,error:null,pendingEmailConfirmation:true};
  }
  if(params.has('error')||params.has('error_code')||!['recovery','email_change'].includes(type)||!access||!refresh) {
    return {cleanURL,session:null,type:null,error:'invalid_callback'};
  }
  const lifetime=Number(params.get('expires_in')||3600);
  const expiresAt=Number(params.get('expires_at')||Math.floor(Date.now()/1000)+lifetime);
  if(!Number.isFinite(lifetime)||lifetime<=0||!Number.isFinite(expiresAt)||expiresAt<=Date.now()/1000) {
    return {cleanURL,session:null,type:null,error:'invalid_callback'};
  }
  return {cleanURL,type,error:null,session:{access_token:access,refresh_token:refresh,expires_at:expiresAt}};
}

export function validateAccountField(kind,value) {
  if(typeof value!=='string') return 'Enter a valid value.';
  const trimmed=value.trim();
  if(kind==='display_name') return trimmed && Array.from(trimmed).length<=200 ? null : 'Enter a display name of 1–200 characters.';
  if(kind==='email') return trimmed.length<=254 && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(trimmed) ? null : 'Enter a valid email address.';
  if(kind==='password') return value.length>=8 ? null : 'Use at least 8 characters for your new password.';
  return 'Unknown account field.';
}

function checkField(kind,value) {
  const message=validateAccountField(kind,value);
  if(message) throw failure('invalid',message);
  return kind==='password'?value:value.trim();
}
function returnURL(value) {
  let url;
  try{url=new URL(value);}catch{throw failure('invalid','The return address is not valid.');}
  const local=url.protocol==='http:' && ['localhost','127.0.0.1'].includes(url.hostname);
  if((!RETURN_ORIGINS.has(url.origin)&&!local)||url.username||url.password||url.hash) {
    throw failure('invalid','The return address is not allowed.');
  }
  return url.href;
}
function cleanToken(value) {
  if(!value?.access_token||!value?.refresh_token||!value?.user?.id||!value.user.email) {
    throw failure('expired','Please sign in again.');
  }
  return {access_token:value.access_token,refresh_token:value.refresh_token,
    expires_at:value.expires_at,expires_in:value.expires_in,
    user:{id:value.user.id,email:value.user.email,user_metadata:{display_name:value.user.user_metadata?.display_name||''}}};
}

export function createAccountClient({call,api,getSession,setSession}) {
  function current() {
    const session=getSession();
    if(!session?.id) throw failure('expired','Please sign in again.');
    return {id:session.id,email:session.email,epoch:session.epoch};
  }
  function guard(before,user) {
    const after=getSession();
    if(!after||after.id!==before.id||after.epoch!==before.epoch||(user&&user.id!==before.id)) {
      throw failure('session_changed','Your sign-in changed. Reopen Settings and try again.');
    }
  }
  async function reauthenticate(password) {
    const before=current();
    if(typeof password!=='string'||!password) throw failure('invalid','Enter your current password.');
    const response=await call('/auth/v1/token?grant_type=password',{
      method:'POST',auth:false,body:{email:before.email,password},
    });
    guard(before,response?.user);
    const safe=cleanToken(response);
    setSession(safe);
    return before;
  }
  async function update(body,before,path='/auth/v1/user') {
    const user=await api(path,{method:'PUT',body});
    guard(before,user);
    return user;
  }
  return {
    async getUser() {
      const before=current(), user=await api('/auth/v1/user');
      guard(before,user);return user;
    },
    async saveName(value) {
      const name=checkField('display_name',value), before=current();
      return update({data:{display_name:name}},before);
    },
    async changeEmail(value,password,redirectTo) {
      const email=checkField('email',value), redirect=returnURL(redirectTo);
      const before=await reauthenticate(password);
      return update({email},before,'/auth/v1/user?redirect_to='+encodeURIComponent(redirect));
    },
    async changePassword(value,currentPassword) {
      const password=checkField('password',value), before=await reauthenticate(currentPassword);
      return update({password},before);
    },
    async completeRecovery(value) {
      const before=current();
      if(!getSession().recovery) throw failure('invalid_callback','Open a valid password-reset link first.');
      return update({password:checkField('password',value)},before);
    },
    async requestRecovery(value,redirectTo) {
      const email=checkField('email',value), redirect=returnURL(redirectTo);
      try {
        await call('/auth/v1/recover?redirect_to='+encodeURIComponent(redirect),{method:'POST',auth:false,body:{email}});
      }catch(error){
        if(error.code!=='user_not_found') throw error;
      }
      // Intentionally identical whether or not the email belongs to an account.
      return {requested:true};
    },
    async signOutOthers() {
      const before=current();
      await api('/auth/v1/logout?scope=others',{method:'POST'});
      guard(before);return {signedOut:true};
    },
    async validateCallback(callback) {
      if(callback.error||!callback.session||!['recovery','email_change'].includes(callback.type)) {
        throw failure('invalid_callback','This link is invalid or expired. Request a new one.');
      }
      const user=await call('/auth/v1/user',{auth:false,headers:{Authorization:'Bearer '+callback.session.access_token}});
      return cleanToken({...callback.session,user});
    },
  };
}
