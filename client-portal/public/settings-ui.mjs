import { CHANGE_CATEGORIES } from './settings.mjs';

export function createSettingsUI({account,settingsClient,getContext,onAccountUpdated,onBack,returnOrigin}) {
  const $=id=>document.getElementById(id);
  let serial=0,active=false,busy=false,context=null,loaded=null,user=null,attempt=null;
  const tabs=[...document.querySelectorAll('[data-settings-panel]')];
  const passwordIds=['emailCurrentPassword','currentPassword','newPassword','confirmPassword'];
  function snapshot(){const c=getContext();return {businessId:c.business?.id||null,userId:c.userId,epoch:c.session?.epoch};}
  function current(ref,version){const c=snapshot();return active&&version===serial&&ref.userId===c.userId&&ref.epoch===c.epoch&&ref.businessId===c.businessId;}
  function status(id,message,error=false){$(id).textContent=message;$(id).className='settings-status '+(error?'is-error':'is-saved');}
  function notice(message){$('settingsNotice').hidden=!message;$('settingsNotice').textContent=message||'';}
  function lock(){
    for(const field of $('settings').querySelectorAll('button,input,textarea,select')) field.disabled=busy;
    $('changeDescription').readOnly=Boolean(attempt);
    $('changeCategory').disabled=busy||Boolean(attempt)||!loaded;
    $('changeDescription').disabled=busy||!loaded;
    $('submitReceptionistChange').disabled=busy||!loaded;
  }
  function setBusy(value){busy=value;$('settings').setAttribute('aria-busy',String(value));lock();}
  function activate(tab){for(const item of tabs){const selected=item===tab;item.setAttribute('aria-selected',String(selected));item.tabIndex=selected?0:-1;$(item.dataset.settingsPanel).hidden=!selected;}}
  function clearPasswords(){for(const id of passwordIds) $(id).value='';}
  function renderUser(value,{preserveNameDraft=false}={}){
    const keepName=preserveNameDraft&&user&&$('profileName').value.trim()!==(user.user_metadata?.display_name||'');
    user=value;
    if(!keepName)$('profileName').value=typeof value.user_metadata?.display_name==='string'?value.user_metadata.display_name:'';
    $('settingsEmail').textContent=value.email;
    $('pendingEmail').hidden=!value.new_email;
    $('pendingEmail').textContent=value.new_email?'Verification pending for '+value.new_email+'. Follow the confirmation emails before signing in with this address.':'';
  }
  function alertHint(){
    const email=$('alertRecipient').value.trim();
    $('alertRecipient').required=$('requestAlertsEnabled').checked;
    $('alertDeliveryHint').textContent=$('requestAlertsEnabled').checked
      ?(email?'Future customer-request details will be emailed to '+email+'.':'Enter the email address that should receive customer-request details.')
      :'Email alerts are off. Your recipient address stays saved.';
  }
  function renderHistory(){
    const root=$('changeRequestHistory');root.replaceChildren();
    const rows=loaded?.requests||[];
    if(!rows.length){const p=document.createElement('p');p.className='muted small';p.textContent='No receptionist changes have been requested for this business yet.';root.append(p);return;}
    const labels={submitted:'Submitted',reviewing:'Under review',completed:'Completed',declined:'Declined'};
    for(const row of rows){
      const card=document.createElement('article');card.className='change-receipt';
      const line=document.createElement('div');line.className='settings-row';
      const title=document.createElement('h3');title.textContent=CHANGE_CATEGORIES.find(([key])=>key===row.category)?.[1]||'Receptionist change';
      const tag=document.createElement('span');tag.className='tag';tag.textContent=labels[row.status]||'Submitted';
      line.append(title,tag);
      const text=document.createElement('p');text.className='change-description';text.textContent=row.description;
      const meta=document.createElement('p');meta.className='hint';
      const date=new Date(row.created_at);meta.textContent=(Number.isFinite(date.getTime())?date.toLocaleString():'Date unavailable')+' · Reference '+row.id;
      card.append(line,text,meta);root.append(card);
    }
  }
  function renderBusiness(){
    const owner=loaded?.role==='owner';
    $('alertRole').textContent=owner?'You are a business owner.':'You are a team member.';
    $('alertMemberHint').hidden=owner;
    $('alertSettingsForm').hidden=!owner;
    if(loaded){$('alertRecipient').value=loaded.preferences.notify_email||'';$('requestAlertsEnabled').checked=loaded.preferences.request_alerts_enabled;}
    alertHint();renderHistory();lock();
  }
  async function loadBusiness(ref,version){
    if(!ref.businessId){
      notice('Your account is not connected to a business. You can manage your account; contact SINTAX for business access.');
      $('alertRole').textContent='No business connected.';$('alertMemberHint').hidden=false;$('alertSettingsForm').hidden=true;
      $('changeRequestHistory').replaceChildren();return;
    }
    const data=await settingsClient.load(ref.businessId,ref.userId);
    if(!current(ref,version))return;
    loaded=data;renderBusiness();
  }
  async function run(statusId,action){
    if(busy||!active)return;
    const ref=snapshot(),version=serial;status(statusId,'Saving…');setBusy(true);
    try{await action(ref,version);}
    catch(error){if(current(ref,version))status(statusId,error.kind==='credentials'&&(!error.code||error.code==='invalid_credentials')?'Your current sign-in details did not match. Reload Settings or try your current password again.':error.message,true);}
    finally{if(current(ref,version))setBusy(false);}
  }
  function alertDirty(){return loaded?.role==='owner'&&($('alertRecipient').value.trim()!==(loaded.preferences.notify_email||'')||$('requestAlertsEnabled').checked!==loaded.preferences.request_alerts_enabled);}
  function hasUnsavedChanges(){
    return active&&(
      (user&&$('profileName').value.trim()!==(user.user_metadata?.display_name||''))
      ||alertDirty()||Boolean($('changeDescription').value.trim())||Boolean(attempt)
      ||passwordIds.some(id=>Boolean($(id).value))||Boolean($('newEmail').value.trim())
    );
  }
  function reset(){
    serial++;active=false;busy=false;context=null;loaded=null;user=null;attempt=null;
    clearPasswords();$('profileName').value='';$('newEmail').value='';$('changeDescription').value='';$('changeCategory').value='hours';
    $('settingsEmail').textContent='';$('pendingEmail').textContent='';$('alertRecipient').value='';$('requestAlertsEnabled').checked=false;
    $('alertRole').textContent='';$('alertSettingsForm').hidden=true;$('alertMemberHint').hidden=false;$('changeRequestHistory').replaceChildren();
    $('alertDeliveryHint').textContent='';$('settingsRetry').hidden=true;
    $('emailChangeForm').hidden=true;$('passwordChangeForm').hidden=true;$('pendingEmail').hidden=true;
    $('submitReceptionistChange').textContent='Submit request to SINTAX';
    for(const id of ['profileNotice','emailNotice','passwordNotice','devicesNotice','alertNotice','changeNotice'])status(id,'');
    notice('');activate(tabs[0]);setBusy(false);
  }
  async function open(){
    reset();active=true;context=snapshot();const ref=context,version=serial;
    const name=getContext().business?.name||'Your account';
    $('settingsBusiness').textContent=name;$('alertBusiness').textContent=name;$('changeBusiness').textContent=name;
    $('settingsTitle').focus();notice('Loading your settings…');setBusy(true);
    try{
      const profile=await account.getUser();if(!current(ref,version))return;
      renderUser(profile);onAccountUpdated(profile);
      await loadBusiness(ref,version);
      if(current(ref,version)&&ref.businessId)notice('');
    }catch(error){if(current(ref,version)){notice(error.message);$('settingsRetry').hidden=false;}}
    finally{if(current(ref,version))setBusy(false);}
  }
  for(const tab of tabs)tab.addEventListener('click',()=>activate(tab));
  $('settings').querySelector('[role="tablist"]').addEventListener('keydown',event=>{
    if(!['ArrowRight','ArrowDown','ArrowLeft','ArrowUp','Home','End'].includes(event.key))return;
    const index=tabs.indexOf(document.activeElement);if(index<0)return;
    event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(['ArrowLeft','ArrowUp'].includes(event.key)?-1:1)+tabs.length)%tabs.length;
    activate(tabs[next]);tabs[next].focus();
  });
  $('settingsBack').addEventListener('click',()=>{if(busy)return;if(hasUnsavedChanges()&&!window.confirm('Discard your unsaved settings and return to the dashboard?'))return;reset();onBack();});
  $('settingsRetry').addEventListener('click',()=>{if(hasUnsavedChanges()&&!window.confirm('Reload settings and discard your unsaved entries?'))return;open();});
  $('profileForm').addEventListener('submit',event=>{event.preventDefault();run('profileNotice',async(ref,version)=>{
    const profile=await account.saveName($('profileName').value);if(!current(ref,version))return;
    renderUser(profile);onAccountUpdated(profile);status('profileNotice','Your display name has been saved.');
  });});
  $('showEmailChange').addEventListener('click',()=>{$('emailChangeForm').hidden=false;$('newEmail').focus();});
  $('cancelEmail').addEventListener('click',()=>{$('emailChangeForm').hidden=true;$('newEmail').value='';$('emailCurrentPassword').value='';});
  $('emailChangeForm').addEventListener('submit',event=>{
    event.preventDefault();const email=$('newEmail').value,password=$('emailCurrentPassword').value;$('emailCurrentPassword').value='';
    run('emailNotice',async(ref,version)=>{
      const profile=await account.changeEmail(email,password,new URL('/?auth=email-change',returnOrigin).href);
      if(!current(ref,version))return;renderUser(profile,{preserveNameDraft:true});onAccountUpdated(profile);
      $('emailChangeForm').hidden=true;$('newEmail').value='';
      status('emailNotice',profile.new_email?'Check the confirmation emails to complete your sign-in email change.':'Your sign-in email is up to date.');
    });
  });
  $('showPasswordChange').addEventListener('click',()=>{$('passwordChangeForm').hidden=false;$('currentPassword').focus();});
  $('cancelPassword').addEventListener('click',()=>{$('passwordChangeForm').hidden=true;clearPasswords();});
  $('passwordChangeForm').addEventListener('submit',event=>{
    event.preventDefault();const password=$('newPassword').value,currentPassword=$('currentPassword').value,confirmation=$('confirmPassword').value;
    clearPasswords();
    if(password!==confirmation){status('passwordNotice','The new passwords did not match. Enter them again.',true);return;}
    run('passwordNotice',async(ref,version)=>{await account.changePassword(password,currentPassword);if(!current(ref,version))return;$('passwordChangeForm').hidden=true;status('passwordNotice','Your password has been changed.');});
  });
  $('signOutOthers').addEventListener('click',()=>run('devicesNotice',async(ref,version)=>{
    await account.signOutOthers();if(current(ref,version))status('devicesNotice','Other devices will need to sign in again when their current access tokens expire. This device stays signed in.');
  }));
  $('requestAlertsEnabled').addEventListener('change',alertHint);$('alertRecipient').addEventListener('input',alertHint);
  $('alertSettingsForm').addEventListener('submit',event=>{
    event.preventDefault();if(!loaded||loaded.role!=='owner')return;
    const email=$('alertRecipient').value,enabled=$('requestAlertsEnabled').checked,stamp=loaded.preferences.settings_updated_at;
    run('alertNotice',async(ref,version)=>{
      const preferences=await settingsClient.saveAlerts({businessId:ref.businessId,settingsUpdatedAt:stamp,email,enabled});
      if(!current(ref,version))return;loaded.preferences=preferences;renderBusiness();
      status('alertNotice',preferences.request_alerts_enabled?'Alert settings saved. Future new-request details will go to '+preferences.notify_email+'.':'Email alerts are off. The recipient address remains saved.');
    });
  });
  $('reloadBusinessSettings').addEventListener('click',()=>{
    if(alertDirty()&&!window.confirm('Discard your unsaved alert preferences and load the latest settings?'))return;
    run('alertNotice',async(ref,version)=>{await loadBusiness(ref,version);if(current(ref,version)){notice('');status('alertNotice','Business settings reloaded.');}});
  });
  $('changeCategory').replaceChildren(...CHANGE_CATEGORIES.map(([code,label])=>new Option(label,code)));
  $('receptionistChangeForm').addEventListener('submit',event=>{
    event.preventDefault();if(!loaded||busy)return;
    attempt??={id:crypto.randomUUID(),category:$('changeCategory').value,description:$('changeDescription').value};
    const pending=attempt;
    run('changeNotice',async(ref,version)=>{
      try{
        const saved=await settingsClient.submitChange({businessId:ref.businessId,userId:ref.userId,...pending});
        if(!current(ref,version))return;
        loaded.requests=[saved,...loaded.requests.filter(row=>row.id!==saved.id)].slice(0,50);attempt=null;
        $('changeDescription').value='';$('changeCategory').value='hours';$('submitReceptionistChange').textContent='Submit request to SINTAX';renderHistory();
        status('changeNotice','Your request has been saved for SINTAX review. Your receptionist has not been changed.');
      }catch(error){
        if(current(ref,version)){
          if(error.kind==='unconfirmed')$('submitReceptionistChange').textContent='Retry this submission';else attempt=null;
        }
        throw error;
      }
    });
  });
  return {open,reset,hasUnsavedChanges};
}
