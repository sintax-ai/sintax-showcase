import { validateAccountField } from './account.mjs';

export const CHANGE_CATEGORIES = [
  ['hours','Business hours'],['services','Services'],['pricing','Pricing'],
  ['voice','Voice'],['instructions','Instructions'],['other','Something else'],
];
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const PREF_COLUMNS='id,notify_email,request_alerts_enabled,settings_updated_at';
const REQUEST_COLUMNS='id,business_id,requested_by,category,description,status,created_at';
const failure=(kind,message)=>Object.assign(new Error(message),{kind});
const ambiguous=error=>error.kind==='offline'||error.status>=500;
const same=(a,b)=>a.id===b.id&&a.notify_email===b.notify_email&&a.request_alerts_enabled===b.request_alerts_enabled;
const query=(table,params)=>'/rest/v1/'+table+'?'+new URLSearchParams(params).toString();
function uuid(value) {
  if(typeof value!=='string'||!UUID.test(value)) throw failure('invalid','The business or account reference is not valid.');
  return value;
}
function onePreference(rows,businessId) {
  const row=Array.isArray(rows)&&rows.length===1?rows[0]:null;
  if(!row||row.id!==businessId||typeof row.request_alerts_enabled!=='boolean'||!row.settings_updated_at) {
    throw failure('denied','These business settings are unavailable. Return to your dashboard and try again.');
  }
  return row;
}

export function createSettingsClient({api}) {
  const readPreference=async businessId=>onePreference(await api(query('businesses',{select:PREF_COLUMNS,id:'eq.'+businessId})),businessId);
  const unconfirmed=()=>failure('unconfirmed','Could not confirm the save. Reload to check the current values before trying again.');
  function confirmedRequest(rows,expected) {
    const row=Array.isArray(rows)&&rows.length===1?rows[0]:null;
    if(!row||row.id!==expected.id||row.business_id!==expected.businessId||row.requested_by!==expected.userId
      ||row.category!==expected.category||row.description!==expected.description||!row.created_at
      ||!['submitted','reviewing','completed','declined'].includes(row.status)) throw unconfirmed();
    return row;
  }
  return {
    async load(businessId,userId) {
      uuid(businessId);uuid(userId);
      const [preferences,memberships,requests]=await Promise.all([
        readPreference(businessId),
        api(query('memberships',{select:'role',business_id:'eq.'+businessId,user_id:'eq.'+userId})),
        api(query('receptionist_change_requests',{select:REQUEST_COLUMNS,business_id:'eq.'+businessId,order:'created_at.desc',limit:'50'})),
      ]);
      const role=Array.isArray(memberships)&&memberships.length===1?memberships[0].role:null;
      if(!['owner','member'].includes(role)||!Array.isArray(requests)||requests.some(r=>r.business_id!==businessId)) {
        throw failure('denied','Your account is not connected to these business settings.');
      }
      return {preferences,role,requests};
    },
    async saveAlerts({businessId,settingsUpdatedAt,email,enabled}) {
      uuid(businessId);
      if(typeof enabled!=='boolean'||typeof email!=='string'||typeof settingsUpdatedAt!=='string'||!Number.isFinite(Date.parse(settingsUpdatedAt))) {
        throw failure('invalid','Reload the alert settings and check your entries.');
      }
      const recipient=email.trim();
      if((enabled||recipient)&&validateAccountField('email',recipient)) throw failure('invalid','Enter a valid alert recipient email.');
      const expected={id:businessId,notify_email:recipient||null,request_alerts_enabled:enabled};
      let written;
      try {
        const rows=await api(query('businesses',{id:'eq.'+businessId,settings_updated_at:'eq.'+settingsUpdatedAt,select:PREF_COLUMNS}),{
          method:'PATCH',headers:{Prefer:'return=representation'},
          body:{notify_email:expected.notify_email,request_alerts_enabled:enabled},
        });
        if(Array.isArray(rows)&&rows.length===0) throw failure('conflict','These alert settings changed elsewhere. Reload them before saving.');
        written=onePreference(rows,businessId);
        if(!same(written,expected)) throw unconfirmed();
      }catch(error){
        if(!ambiguous(error)) throw error;
        try {
          const current=await readPreference(businessId);
          if(same(current,expected)) return current;
        }catch{ /* An unknown outcome must stay visible instead of being retried. */ }
        throw unconfirmed();
      }
      try {
        const current=await readPreference(businessId);
        if(same(current,expected)&&current.settings_updated_at===written.settings_updated_at) return current;
      }catch{ /* Saved response alone does not confirm the current preferences. */ }
      throw unconfirmed();
    },
    async submitChange({businessId,userId,id,category,description}) {
      uuid(businessId);uuid(userId);uuid(id);
      if(!CHANGE_CATEGORIES.some(([key])=>key===category)||typeof description!=='string') {
        throw failure('invalid','Choose a request category and describe the change.');
      }
      const text=description.trim();
      if(!text||Array.from(text).length>4000) throw failure('invalid','Describe the change in 1–4,000 characters.');
      const expected={businessId,userId,id,category,description:text};
      let rows;
      try {
        rows=await api('/rest/v1/receptionist_change_requests',{
          method:'POST',headers:{Prefer:'return=representation'},
          body:{id,business_id:businessId,category,description:text},
        });
      }catch(error){
        if(!ambiguous(error)&&error.status!==409&&error.code!=='23505') throw error;
        try {
          rows=await api(query('receptionist_change_requests',{
            select:REQUEST_COLUMNS,business_id:'eq.'+businessId,id:'eq.'+id,requested_by:'eq.'+userId,
          }));
          return confirmedRequest(rows,expected);
        }catch{throw unconfirmed();}
      }
      return confirmedRequest(rows,expected);
    },
  };
}
