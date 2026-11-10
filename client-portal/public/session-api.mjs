// Automatic retries must never change which signed-in account owns a request.
const failure=(kind,message)=>Object.assign(new Error(message),{kind});
const EXPIRED='Your sign-in has expired. Please sign in again.';
export function createSessionAPI({call,getSession,setSession,onExpired}) {
  let refreshing=null;
  function capture(){
    const session=getSession();
    if(!session)throw failure('expired',EXPIRED);
    return {id:session.id,epoch:session.epoch,refresh_token:session.refresh_token,expires_at:session.expires_at};
  }
  function guard(before){
    const current=getSession();
    if(!current||current.epoch!==before.epoch||(before.id&&current.id&&current.id!==before.id)) {
      throw failure('session_changed','Your sign-in changed. Please try again from your current account.');
    }
  }
  async function renew(){
    const before=capture();
    if(!refreshing||refreshing.epoch!==before.epoch){
      const flight={epoch:before.epoch,promise:null};
      flight.promise=call('/auth/v1/token?grant_type=refresh_token',{
        method:'POST',auth:false,body:{refresh_token:before.refresh_token},
      }).then(value=>{guard(before);setSession(value);}).finally(()=>{if(refreshing===flight)refreshing=null;});
      refreshing=flight;
    }
    const flight=refreshing;
    try{await flight.promise;guard(before);}
    catch(error){
      guard(before);
      if(error.kind==='offline'||error.kind==='session_changed')throw error;
      onExpired(EXPIRED);throw failure('expired',EXPIRED);
    }
  }
  async function api(path,options){
    const before=capture();
    if((before.expires_at||0)-60<Date.now()/1000)await renew();
    guard(before);
    const request=async()=>{guard(before);const value=await call(path,options);guard(before);return value;};
    try{return await request();}
    catch(error){
      guard(before);
      if(error.kind!=='expired')throw error;
      await renew();guard(before);
      try{return await request();}
      catch(again){guard(before);if(again.kind==='expired')onExpired(EXPIRED);throw again;}
    }
  }
  return {api,renew};
}
