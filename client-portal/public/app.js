import { linkedRequests, recordingState, recordingURL, durationLabel, callMatches } from './calls.mjs';
import { createHTTPCall } from './http.mjs';
import { createAccountClient, parseAuthCallback } from './account.mjs';
import { createSettingsClient } from './settings.mjs';
import { createSettingsUI } from './settings-ui.mjs';
import { createSessionAPI } from './session-api.mjs';
import { csv } from './csv.mjs';
import { disableControls } from './action-controls.mjs';
import { Selection, moveSelected } from './bulk-selection.mjs';
import { CAPTURED, STATUSES, validateInquiry, changedFields, corrections, formatPhone } from './validate.mjs';
import { serviceMenu, serviceLabel } from './services.mjs';
import { groupByDay, monthSummary, summaryText, defaultFilter, transcriptTurns, filterByReceivedDate, inquiryScope } from './inbox.mjs?v=20261102-trash';

const cfg = window.PORTAL_CONFIG;
const $ = id => document.getElementById(id);
function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children.filter(c => c != null && c !== false));
  return node;
}

const SESSION_KEY = 'sintax-portal-session';
const BUSINESS_KEY = 'sintax-portal-business';
const COLUMNS = 'id,business_id,source_id,created_at,updated_at,name,phone,service,preferred_time,duration,preferences,status,owner_notes,original,transcript,is_trashed,call_id';
const CALL_COLUMNS = 'id,business_id,session_id,room_id,mode,started_at,ended_at,duration_seconds,recording_status,recording_path,recording_expires_at,is_trashed,updated_at';
const TITLE = document.title;
const FIELD_LABELS = { name: "Caller's name", phone: 'Phone', service: 'Service', preferred_time: 'Requested time', duration: 'Duration', preferences: 'Preferences' };
const statusLabel = code => STATUSES.find(([s]) => s === code)?.[1] ?? code;

const state = {
  section: 'calls', calls: [], callSelected: null, session: null, businesses: [], business: null, rows: [], filter: '', filterChosen: false, loading: false,
  selected: null, editing: false, saving: false, menu: [], view: 'inbox', bulkBusy: false,
  selection: new Selection(),
};
let authEpoch = 0;
const accountSession = () => state.session ? {...state.session,epoch:authEpoch} : null;
const authReturnOrigin = cfg?.demo ? window.location.origin : 'https://portal-alt.example.test';

/* ---------- Talking to Supabase ---------- */

const call = createHTTPCall({url:cfg?.url,publishableKey:cfg?.publishableKey,getSession:()=>state.session});

function storeSession(s) {
  try { s ? localStorage.setItem(SESSION_KEY, JSON.stringify(s)) : localStorage.removeItem(SESSION_KEY); } catch { /* storage blocked: session lasts this tab only */ }
}
function readSession() {
  try { return JSON.parse(localStorage.getItem(SESSION_KEY)); } catch { return null; }
}
function setSession(t) {
  if (!state.session || (state.session.id && t.user?.id && state.session.id !== t.user.id)) authEpoch++;
  state.session = {
    access_token: t.access_token,
    refresh_token: t.refresh_token,
    expires_at: t.expires_at ?? Math.floor(Date.now() / 1000) + (t.expires_in ?? 3600),
    email: t.user?.email ?? state.session?.email ?? '',
    id: t.user?.id ?? state.session?.id ?? null,
    display_name: t.user?.user_metadata?.display_name ?? state.session?.display_name ?? '',
    recovery: t.recovery ?? state.session?.recovery ?? false,
  };
  storeSession(state.session);
}

function applyUser(user) {
  if (!state.session || !user?.id || (state.session.id && state.session.id !== user.id)) return;
  state.session.id = user.id; state.session.email = user.email;
  state.session.display_name = typeof user.user_metadata?.display_name === 'string' ? user.user_metadata.display_name : '';
  storeSession(state.session);
  $('accountEmail').textContent = state.session.email;
  $('accountName').textContent = state.session.display_name;
}

const sessionAPI = createSessionAPI({call,getSession:accountSession,setSession,onExpired:endSession});
const api = sessionAPI.api;

/* ---------- Screens ---------- */

const accountClient = createAccountClient({call,api,getSession:accountSession,setSession});
const settingsClient = createSettingsClient({api});
const settingsUI = createSettingsUI({account:accountClient,settingsClient,
  getContext:()=>({business:state.business,userId:state.session?.id,session:accountSession()}),
  onAccountUpdated:applyUser,onBack:()=>{show('inbox');$('openSettings').focus();},returnOrigin:authReturnOrigin,
});

function show(view) {
  for (const id of ['login', 'starting', 'inbox','settings','forgot','recovery']) $(id).hidden = id !== view;
  $('account').hidden = !['inbox','settings'].includes(view);
}

function loginNotice(text, kind = 'error') {
  $('loginNotice').hidden = !text;
  $('loginNotice').textContent = text || '';
  $('loginNotice').className = kind === 'info' ? 'notice info' : 'notice';
}

function endSession(notice, keepStoredSession = false) {
  authEpoch++;
  settingsUI.reset();
  for (const id of ['recoveryPassword','recoveryConfirm','password']) $(id).value = '';
  stopAudio();
  if ($('callDetail').open) $('callDetail').close();
  state.calls = []; state.callSelected = null;
  state.session = null; state.rows = []; state.businesses = []; state.business = null; state.selected = null;
  $('accountName').textContent = ''; $('accountEmail').textContent = '';
  $('showCalls').textContent = 'Call history (0)'; $('showRequests').textContent = 'Customer requests (0)';
  $('summary').hidden = true;
  state.filter = ''; state.filterChosen = false;
  state.view = 'inbox';
  state.selection.clear(); $('bulkActions').hidden = true;
  $('mailboxViews').hidden = true; $('trashHint').hidden = true; $('actionNotice').hidden = true;
  resetDateFilter();
  $('inquiryType').value = '';
  document.title = TITLE;
  if (!keepStoredSession) storeSession(null);
  if ($('detail').open) $('detail').close();
  $('records').replaceChildren(); $('listStatus').textContent = '';
  show('login');
  loginNotice(notice, 'info');
  $('email').focus();
}

$('signin').addEventListener('submit', async e => {
  e.preventDefault();
  loginNotice('');
  const email = $('email').value.trim(), password = $('password').value;
  if (!email || !password) return loginNotice('Enter your email and password.');
  if (!cfg?.publishableKey) return loginNotice('This portal is not connected yet. No customer data is loaded.');
  const button = $('signinButton');
  button.disabled = true; button.setAttribute('aria-busy', 'true'); button.textContent = 'Signing in…';
  try {
    setSession(await call('/auth/v1/token?grant_type=password', { method: 'POST', auth: false, body: { email, password } }));
    $('password').value = '';
    await start();
  } catch (err) {
    state.session = null; storeSession(null);
    loginNotice(err.kind === 'credentials' ? "That email and password didn't match. Check them and try again." : err.message);
  } finally {
    button.disabled = false; button.removeAttribute('aria-busy'); button.textContent = 'Sign in';
  }
});

$('logout').addEventListener('click', async () => {
  if (state.saving) return;
  if ($('detail').open && !confirmDiscard()) return;
  if (settingsUI.hasUnsavedChanges() && !window.confirm('Discard your unsaved settings and sign out?')) return;
  try { await call('/auth/v1/logout?scope=local', { method: 'POST' }); } catch { /* signing out locally is what matters */ }
  endSession('You have signed out.');
});

// Signing out in one tab signs out the others.
window.addEventListener('storage', e => {
  if (e.key === SESSION_KEY && !e.newValue && state.session) endSession('You have signed out.');
  if (e.key === SESSION_KEY && e.newValue && state.session?.id) {
    try {
      const next = JSON.parse(e.newValue);
      if (next?.id && next.id !== state.session.id) endSession('This browser signed into another account. Please sign in again.', true);
    } catch { /* malformed browser storage is never an authenticated identity */ }
  }
});

async function start() {
  const epoch = authEpoch;
  show('starting'); $('startingNotice').textContent = 'Checking your sign-in…'; $('startingRetry').hidden = true;
  let profile;
  try { profile = await api('/auth/v1/user'); }
  catch (err) {
    if (state.session && epoch === authEpoch) { $('startingNotice').textContent = err.message; $('startingRetry').hidden = false; }
    return;
  }
  if (!state.session || epoch !== authEpoch) return;
  if (!profile?.id || !profile.email || (state.session.id && state.session.id !== profile.id)) { endSession('Please sign in again.'); return; }
  applyUser(profile);
  if (state.session.recovery) { showRecovery(); return; }
  show('inbox');
  $('accountEmail').textContent = state.session.email;
  listMessage('loading');
  try {
    const businesses = await api('/rest/v1/businesses?select=id,name,slug&order=name');
    if (!state.session || epoch !== authEpoch) return;
    state.businesses = businesses;
  } catch (err) {
    if (state.session) listMessage('error', err.message, start);
    return;
  }
  $('portalSectionTabs').hidden = !state.businesses.length;
  $('sectionHint').hidden = !state.businesses.length;
  if (!state.businesses.length) {
    state.business = null; state.calls = []; state.rows = [];
    $('businessTitle').textContent = 'No business connected';
    $('toolbar').hidden = true;
    $('listStatus').textContent = '';
    return listMessage('empty', "Your account isn't connected to a business yet. Contact SINTAX and we'll set it up.");
  }
  $('toolbar').hidden = false;
  let saved = null;
  try { saved = localStorage.getItem(BUSINESS_KEY); } catch { /* ignore */ }
  state.business = state.businesses.find(b => b.id === saved) ?? state.businesses[0];
  $('business').replaceChildren(...state.businesses.map(b => new Option(b.name, b.id, false, b.id === state.business.id)));
  $('businessPicker').hidden = state.businesses.length < 2;
  await load();
}

$('business').addEventListener('change', () => {
  state.business = state.businesses.find(b => b.id === $('business').value);
  try { localStorage.setItem(BUSINESS_KEY, state.business.id); } catch { /* ignore */ }
  state.filterChosen = false;   // a different business opens on its own new requests
  state.view = 'inbox';
  $('actionNotice').hidden = true;
  resetDateFilter();
  $('inquiryType').value = '';
  load();
});

async function load(afterBulk = false) {
  if (state.bulkBusy && !afterBulk) return false;
  state.selection.clear();
  $('bulkActions').hidden = true;
  const business = state.business;
  $('businessTitle').textContent = business.name;
  state.loading = true; state.loadError = false; state.rows = []; state.calls = [];
  $('mailboxViews').hidden = true; $('summary').hidden = true; $('trashHint').hidden = true;
  $('filterField').hidden = true; $('export').disabled = true;   // nothing to filter or download until requests arrive
  $('refresh').disabled = true; $('refresh').setAttribute('aria-busy', 'true');
  listMessage('loading');
  try {
    const all = [];
    for (let offset = 0; ; offset += 500) {
      const page = await api(`/rest/v1/inquiries?business_id=eq.${encodeURIComponent(business.id)}&select=${COLUMNS}&order=created_at.desc,id.desc&limit=500&offset=${offset}`);
      all.push(...page);
      if (page.length < 500) break;
    }
    const allCalls = [];
    for (let offset = 0; ; offset += 500) {
      const page = await api(`/rest/v1/calls?business_id=eq.${encodeURIComponent(business.id)}&select=${CALL_COLUMNS}&order=started_at.desc,id.desc&limit=500&offset=${offset}`);
      allCalls.push(...page.map(c => ({ ...c, created_at: c.started_at })));
      if (page.length < 500) break;
    }
    if (!state.session || state.business !== business) return;   // switched business while loading
    state.calls = allCalls;
    state.rows = all;
    state.menu = serviceMenu(business.slug, all.map(r => r.service));
    const selectedType = $('inquiryType').value;
    $('inquiryType').replaceChildren(new Option('All inquiry types', ''),
      ...state.menu.map(([code, label]) => new Option(label, code)));
    $('inquiryType').value = state.menu.some(([code]) => code === selectedType) ? selectedType : '';
    if (!state.filterChosen) state.filter = state.section === 'calls' || state.view === 'trash' ? '' : defaultFilter(all);
    state.loading = false;
    render();
    return true;
  } catch (err) {
    state.loading = false; state.loadError = true;
    if (state.session) listMessage('error', err.message, () => load());
    return false;
  } finally {
    $('refresh').disabled = false; $('refresh').removeAttribute('aria-busy');
  }
}
$('refresh').addEventListener('click', () => load());

/* ---------- Request list ---------- */

const when = iso => {
  const d = new Date(iso);
  return isNaN(d) ? '' : d.toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' });
};

function sectionRows() { return state.section === 'calls' ? state.calls : state.rows; }
function recordNoun() { return state.section === 'calls' ? 'calls' : 'requests'; }
function dateSelection() {
  return filterByReceivedDate(inquiryScope(sectionRows(), state.view), {
    period: $('datePeriod').value, from: $('dateFrom').value, to: $('dateTo').value,
  });
}

function switchSection(section) {
  if (state.bulkBusy || state.loading) return;
  state.section = section;
  switchView('inbox');
}
$('showCalls').addEventListener('click', () => switchSection('calls'));
$('showRequests').addEventListener('click', () => switchSection('requests'));
function switchView(view) {
  if (state.bulkBusy) return;
  state.view = view;
  state.filter = ''; state.filterChosen = true;
  $('search').value = ''; $('inquiryType').value = '';
  resetDateFilter();
  $('actionNotice').hidden = true;
  render();
}
$('showInbox').addEventListener('click', () => switchView('inbox'));
$('showTrash').addEventListener('click', () => switchView('trash'));

function resetDateFilter() {
  $('datePeriod').value = 'all';
  $('dateFrom').value = ''; $('dateTo').value = '';
  $('customDates').hidden = true;
  $('dateError').hidden = true;
  $('dateFrom').removeAttribute('aria-invalid'); $('dateTo').removeAttribute('aria-invalid');
}

function visibleRows(rows = dateSelection().rows) {
  const q = $('search').value.trim().toLowerCase();
  if (state.section === 'calls') return rows.filter(c => callMatches(c, state.rows, q));
  const digits = q.replace(/\D/g, '');
  return rows.filter(r => {
    if ($('inquiryType').value && r.service !== $('inquiryType').value) return false;
    if (state.filter && r.status !== state.filter) return false;
    if (!q) return true;
    const haystack = [r.name, r.service, serviceLabel(r.service, state.menu), r.preferred_time, r.duration, r.preferences, r.owner_notes, r.original?.name]
      .map(v => String(v ?? '').toLowerCase());
    return haystack.some(v => v.includes(q)) || (digits.length >= 3 && String(r.phone).includes(digits));
  });
}

function renderFilters(rows) {
  rows = rows.filter(r => !$('inquiryType').value || r.service === $('inquiryType').value);
  const counts = Object.fromEntries(STATUSES.map(([s]) => [s, rows.filter(r => r.status === s).length]));
  const options = [['', 'All', rows.length], ...STATUSES.map(([s, label]) => [s, label, counts[s]])];
  $('filters').replaceChildren(...options.map(([value, label, n]) => {
    const b = el('button', { type: 'button' }, label, el('span', { className: 'count', textContent: `(${n})` }));
    b.setAttribute('aria-pressed', String(state.filter === value));
    b.disabled = state.bulkBusy;
    b.addEventListener('click', () => { if (state.bulkBusy) return; state.filter = value; state.filterChosen = true; render(); });
    return b;
  }));
}

function listMessage(kind, text, retry) {
  if (kind === 'loading' || kind === 'error') $('bulkActions').hidden = true;
  const box = $('records');
  if (kind === 'loading') {
    box.replaceChildren();
    $('listStatus').textContent = 'Loading calls and customer requests…';
    return;
  }
  $('listStatus').textContent = kind === 'error' ? '' : $('listStatus').textContent;
  const panel = el('div', { className: kind === 'error' ? 'empty error' : 'empty' });
  panel.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  panel.append(el('h2', { textContent: kind === 'error' ? "Records couldn't be loaded" : text.title ?? text }));
  if (kind === 'error') panel.append(el('p', { textContent: text }));
  if (text?.body) panel.append(el('p', { className: 'muted', textContent: text.body }));
  if (retry) {
    const b = el('button', { type: 'button', className: 'button', textContent: kind === 'error' ? 'Try again' : 'Clear search and filters' });
    b.addEventListener('click', retry);
    panel.append(b);
  }
  box.replaceChildren(panel);
}

function render() {
  if (state.loading || state.loadError) return;
  const inTrash = state.view === 'trash';
  const calls = state.section === 'calls';
  const noun = recordNoun();
  const active = inquiryScope(sectionRows());
  const trash = inquiryScope(sectionRows(), 'trash');
  $('showCalls').setAttribute('aria-pressed', String(calls));
  $('showRequests').setAttribute('aria-pressed', String(!calls));
  $('showCalls').textContent = `Call history (${inquiryScope(state.calls).length})`;
  $('showRequests').textContent = `Customer requests (${inquiryScope(state.rows).length})`;
  $('sectionHint').textContent = calls ? 'Every call appears here, with playback when a recording is available. Customer requests are linked separately.' : 'These are requests, not bookings. Nothing is confirmed until you contact the caller.';
  $('search').placeholder = calls ? 'Caller, phone or call reference' : 'Name, phone, service or notes';
  $('inquiryTypeField').hidden = calls;
  $('export').hidden = calls;
  $('trashHint').textContent = calls ? 'Trashed calls keep their recordings and connected requests. Restore calls at any time.' : 'Trashed requests keep their details and connected calls. Restore requests at any time.';
  $('mailboxViews').hidden = false;
  $('showInbox').textContent = `${calls ? 'All calls' : 'Inbox'} (${active.length})`;
  $('showTrash').textContent = `Trash (${trash.length})`;
  $('showInbox').setAttribute('aria-pressed', String(!inTrash));
  $('showTrash').setAttribute('aria-pressed', String(inTrash));
  $('trashHint').hidden = !inTrash;
  const dates = dateSelection();
  $('dateError').textContent = dates.error;
  $('dateError').hidden = !dates.error;
  for (const id of ['dateFrom', 'dateTo']) $(id).setAttribute('aria-invalid', String(Boolean(dates.error)));
  if (!calls) renderFilters(dates.rows);
  $('filterField').hidden = calls;
  const shown = visibleRows(dates.rows);
  state.selection.setScope(JSON.stringify([state.business?.id, state.section, state.view, state.filter, $('search').value, $('inquiryType').value, $('datePeriod').value, $('dateFrom').value, $('dateTo').value]));
  state.selection.prune(shown);
  renderBulk(shown);
  const total = inTrash ? trash.length : active.length;
  const waiting = inquiryScope(state.rows).filter(r => r.status === 'new').length;
  document.title = waiting ? `(${waiting}) ${TITLE}` : TITLE;
  $('summary').hidden = calls || inTrash || !total;
  $('summary').textContent = summaryText(monthSummary(state.rows, STATUSES), STATUSES);
  $('listStatus').textContent = total ? `${dates.label}: showing ${shown.length} of ${dates.rows.length} ${noun} in this period (${total} all time)` : '';
  $('export').disabled = inTrash || !shown.length;
  $('export').title = inTrash ? 'Restore requests to include them in a spreadsheet.' : '';
  if (dates.error) {
    return listMessage('empty', { title: 'Choose your date range', body: dates.error });
  }
  if (!total) {
    return listMessage('empty', inTrash
      ? { title: 'Trash is empty', body: `Trashed ${noun} will appear here. You can restore them at any time.` }
      : { title: calls ? 'No calls in your active list' : 'No customer requests in your inbox', body: calls ? 'New calls will appear here. You can also restore calls from Trash. Earlier requests stay in Customer requests; historical recordings are not automatically imported.' : 'New requests will appear here. You can also restore requests from Trash.' });
  }
  if (!shown.length) {
    return listMessage('empty', { title: `No ${noun} match`, body: 'Try a different date range, search or filter.' }, () => {
      $('search').value = ''; $('inquiryType').value = ''; state.filter = ''; state.filterChosen = true; resetDateFilter(); render(); $('search').focus();
    });
  }
  $('records').replaceChildren(...groupByDay(shown).flatMap(group => [
    el('h2', { className: 'day-heading', textContent: group.label }),
    ...group.rows.map(calls ? callCard : requestCard),
  ]));
}

function requestCard(r) {
    const fixed = corrections(r).length > 0;
    const card = el('button', { type: 'button', className: 'request' },
      el('div', { className: 'request-main' },
        el('div', { className: 'request-name' }, r.name, fixed && el('span', { className: 'tag', textContent: 'Corrected' })),
        el('div', { className: 'request-sub', textContent: formatPhone(r.phone) })),
      el('div', { className: 'request-body' },
        el('div', { className: 'request-what', textContent: serviceLabel(r.service, state.menu) }),
        el('div', { className: 'request-sub', textContent: `${r.preferred_time || 'Time not given'} · ${r.duration || 'Duration not given'}` })),
      el('div', { className: 'request-side' },
        el('span', { className: `badge badge-${r.status}`, textContent: statusLabel(r.status) }),
        el('span', { className: 'request-sub', textContent: `Received ${when(r.created_at)}` })));
    card.setAttribute('aria-label', `${r.name}, ${serviceLabel(r.service, state.menu)}, ${r.preferred_time}. Status: ${statusLabel(r.status)}. Received ${when(r.created_at)}.`);
    card.dataset.id = r.id;
    card.disabled = state.bulkBusy;
    card.addEventListener('click', () => openRequest(r));
    const checkbox = el('input', { type: 'checkbox', checked: state.selection.ids.has(r.id), disabled: state.bulkBusy });
    checkbox.setAttribute('aria-label', `Select request from ${r.name}, received ${when(r.created_at)}`);
    checkbox.addEventListener('change', () => {
      state.selection.toggle(r.id, checkbox.checked);
      wrapper.classList.toggle('is-selected', checkbox.checked);
      renderBulk();
    });
    const wrapper = el('div', { className: `request-row${checkbox.checked ? ' is-selected' : ''}` },
      el('label', { className: 'request-select' }, checkbox), card);
    return wrapper;
}

function renderBulk(rows = visibleRows()) {
  const count = state.selection.ids.size;
  $('bulkActions').hidden = !rows.length || Boolean(dateSelection().error);
  $('selectAll').checked = Boolean(rows.length) && count === rows.length;
  $('selectAll').indeterminate = count > 0 && count < rows.length;
  $('selectAll').disabled = state.bulkBusy || !rows.length;
  $('selectAllLabel').textContent = `Select all ${rows.length} matching ${recordNoun()}`;
  $('selectionCount').textContent = `${count} selected`;
  $('clearSelection').disabled = state.bulkBusy || !count;
  $('bulkMove').disabled = state.bulkBusy || !count;
  $('bulkMove').textContent = state.bulkBusy ? 'Updating selection…' : state.view === 'trash' ? 'Restore selected' : 'Move selected to trash';
}

$('selectAll').addEventListener('change', () => {
  if (state.bulkBusy) return;
  $('selectAll').checked ? state.selection.selectAll(visibleRows()) : state.selection.clear();
  render();
});
$('clearSelection').addEventListener('click', () => { if (!state.bulkBusy) { state.selection.clear(); render(); } });
$('bulkMove').addEventListener('click', async () => {
  if (state.bulkBusy || state.saving || state.loading) return;
  const rows = visibleRows().filter(row => state.selection.ids.has(row.id));
  if (!rows.length) return;
  const business = state.business;
  const noun = recordNoun();
  const removing = state.view !== 'trash';
  state.bulkBusy = true; state.saving = true;
  const unlock = disableControls(document.querySelectorAll('button, input, select, textarea'));
  $('bulkMove').setAttribute('aria-busy', 'true');
  renderBulk();
  $('actionNotice').textContent = `Updating ${rows.length} selected ${noun}…`;
  $('actionNotice').hidden = false;
  try {
    const result = await moveSelected(rows, { businessId: business.id, removing, request: api, columns: state.section === 'calls' ? CALL_COLUMNS : COLUMNS, table: state.section === 'calls' ? 'calls' : 'inquiries' });
    if (!state.session || state.business !== business) return;
    const refreshed = await load(true);
    if (!state.session || state.business !== business) return;
    const messages = [`${result.updated.length} of ${rows.length} ${noun} ${removing ? 'moved to Trash' : 'restored'}.`];
    if (result.conflicts.length) messages.push(`${result.conflicts.length} changed elsewhere or are no longer available; those records were not changed.`);
    if (result.failed.length) messages.push(`${result.failed.length} changes could not be confirmed. ${result.failed[0].error.message} Check their current location before retrying.`);
    messages.push(refreshed ? 'The list has been refreshed.' : 'The list could not be refreshed. Use Try again to check the current state before retrying.');
    $('actionNotice').textContent = messages.join(' ');
    $('actionNotice').hidden = false;
  } finally {
    state.bulkBusy = false; state.saving = false;
    state.selection.clear();
    unlock();
    $('bulkMove').removeAttribute('aria-busy');
    // New list controls created during reload also need to be enabled.
    if (state.session && !state.loading) render();
  }
});

$('search').addEventListener('input', render);
$('inquiryType').addEventListener('change', render);
for (const id of ['datePeriod', 'dateFrom', 'dateTo']) {
  $(id).addEventListener('change', () => {
    $('customDates').hidden = $('datePeriod').value !== 'custom';
    render();
  });
}

$('export').addEventListener('click', () => {
  if (state.loading || state.view === 'trash' || dateSelection().error) return;
  const rows = visibleRows();
  if (!rows.length) return;
  const blob = new Blob(['﻿' + csv(rows, { serviceLabel: s => serviceLabel(s, state.menu), statusLabel })], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const slug = state.business.slug || 'requests';
  const a = el('a', { href: url, download: `${slug}-requests-${new Date().toISOString().slice(0, 10)}.csv` });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  $('listStatus').textContent = `Downloaded ${rows.length} request${rows.length === 1 ? '' : 's'} as a spreadsheet file.`;
});

/* ---------- Calls and private playback ---------- */
const AUDIO_LABELS = { available: 'Recording available', pending: 'Call in progress or recording processing', unavailable: 'Recording unavailable', expired: 'Recording expired' };
function callCard(c) {
  const requests = linkedRequests(c, state.rows);
  const title = requests[0]?.name || 'Call without a saved request';
  const card = el('button', { type: 'button', className: 'request', disabled: state.bulkBusy },
    el('div', { className: 'request-main' }, el('div', { className: 'request-name', textContent: title }), el('div', { className: 'request-sub', textContent: when(c.started_at) })),
    el('div', { className: 'request-body' }, el('div', { className: 'request-what', textContent: AUDIO_LABELS[recordingState(c)] }), el('div', { className: 'request-sub', textContent: `${durationLabel(c.duration_seconds)} · ${c.mode === 'demo' ? 'Demo call' : 'Call'}` })),
    el('div', { className: 'request-side' }, el('span', { className: 'tag', textContent: `${requests.length} linked request${requests.length === 1 ? '' : 's'}` })));
  card.dataset.id = c.id;
  card.addEventListener('click', () => openCall(c));
  const checkbox = el('input', { type: 'checkbox', checked: state.selection.ids.has(c.id), disabled: state.bulkBusy });
  checkbox.setAttribute('aria-label', `Select call ${when(c.started_at)}, ${title}`);
  const wrapper = el('div', { className: `request-row${checkbox.checked ? ' is-selected' : ''}` }, el('label', { className: 'request-select' }, checkbox), card);
  checkbox.addEventListener('change', () => { state.selection.toggle(c.id, checkbox.checked); wrapper.classList.toggle('is-selected', checkbox.checked); renderBulk(); });
  return wrapper;
}
let audioRequest = 0;
function stopAudio() {
  audioRequest++;
  const player = $('callAudio');
  player.pause(); player.removeAttribute('src'); player.load(); player.hidden = true;
}
function openCall(c) {
  if (state.saving || state.bulkBusy) return;
  stopAudio();
  state.callSelected = c;
  $('callTitle').textContent = when(c.started_at);
  $('callMeta').textContent = `${durationLabel(c.duration_seconds)} · ${c.mode === 'demo' ? 'Demo call' : 'Call'}${c.is_trashed ? ' · In Trash' : ''}`;
  $('callReference').textContent = `Call reference: ${c.session_id}`;
  $('callTrash').textContent = c.is_trashed ? 'Restore call' : 'Move call to trash';
  $('callTrash').disabled = false;
  $('callNotice').textContent = '';
  const status = recordingState(c);
  $('audioStatus').textContent = AUDIO_LABELS[status] + (status === 'pending' ? '. Refresh the list after the call finishes.' : status === 'unavailable' ? '. This call can still be reviewed and moved to Trash.' : '.');
  $('loadAudio').hidden = status !== 'available';
  $('loadAudio').disabled = false;
  $('loadAudio').textContent = 'Load recording';
  const requests = linkedRequests(c, state.rows);
  $('linkedRequests').replaceChildren(...(requests.length ? requests.map(r => {
    const b = el('button', { type: 'button', className: 'button linked-request', textContent: 'Open customer request' });
    b.setAttribute('aria-label', `Open customer request for ${r.name}`);
    b.addEventListener('click', () => openRequest(r));
    return el('article', { className: 'call-request stack' },
      el('h4', { textContent: `${r.name}${r.is_trashed ? ' · In Trash' : ''}` }),
      el('p', { className: 'hint', textContent: `Request only, not a confirmed booking. Follow-up: ${statusLabel(r.status)}.` }),
      el('dl', { className: 'facts' }, ...requestFacts(r)),
      r.owner_notes ? el('p', { className: 'call-request-notes' }, el('strong', { textContent: 'Your notes: ' }), r.owner_notes) : null,
      b);
  }) : [el('p', { className: 'muted', textContent: 'No customer request was saved from this call.' })]));
  const transcripts = requests.map(row => ({ row, turns: transcriptTurns(row) })).filter(item => item.turns.length);
  $('callTranscriptMeta').textContent = transcripts.length
    ? 'Text as the receptionist heard it. Kept for 90 days after the call.'
    : 'No saved transcript is available. Text is currently stored with customer requests and kept for 90 days. A recording may still be available below.';
  $('callTranscript').replaceChildren(...transcripts.map(({ row, turns }) => el('div', {},
    requests.length > 1 ? el('h4', { textContent: `Transcript attached to ${row.name}'s request` }) : null,
    el('div', { className: 'turns' }, ...transcriptNodes(turns)))));
  if (!$('callDetail').open) $('callDetail').showModal();
  $('callTitle').focus();
}
function renderRequestCall(row) {
  const c = state.calls.find(c => c.id === row.call_id && c.business_id === row.business_id);
  const box = $('requestCall');
  box.replaceChildren(el('h3', { textContent: 'Connected call' }));
  if (!c) return box.append(el('p', { className: 'muted small', textContent: 'No call recording is linked to this request. Earlier requests were saved before call history was added.' }));
  const b = el('button', { type: 'button', className: 'button', textContent: `View call and recording${c.is_trashed ? ' (in Trash)' : ''}` });
  b.addEventListener('click', () => {
    if (state.saving) return;
    if (Object.keys(pendingChanges().changes).length || rawDiffers()) return saveStatus('error', 'Save or undo your changes before opening the call.');
    $('detail').close(); openCall(c);
  });
  box.append(b);
}
$('closeCall').addEventListener('click', () => { if (!state.saving) $('callDetail').close(); });
$('callDetail').addEventListener('cancel', e => { if (state.saving) e.preventDefault(); });
$('callDetail').addEventListener('close', stopAudio);
$('loadAudio').addEventListener('click', async () => {
  const c = state.callSelected;
  if (!c || recordingState(c) !== 'available') return;
  stopAudio();
  const serial = audioRequest;
  $('loadAudio').disabled = true;
  $('audioStatus').textContent = 'Preparing private playback…';
  try {
    const path = c.recording_path.split('/').map(encodeURIComponent).join('/');
    const data = await api(`/storage/v1/object/sign/call-recordings/${path}`, { method: 'POST', body: { expiresIn: 3600 } });
    if (serial !== audioRequest || !state.session || !$('callDetail').open) return;
    $('callAudio').src = recordingURL(cfg.url, data?.signedURL);
    $('callAudio').hidden = false;
    $('audioStatus').textContent = 'Press play to hear the conversation. If playback stops, reload the recording.';
    $('loadAudio').textContent = 'Reload recording';
  } catch (err) {
    if (serial === audioRequest) $('audioStatus').textContent = `Could not load recording. ${err.message}`;
  } finally { if (serial === audioRequest) $('loadAudio').disabled = false; }
});
$('callAudio').addEventListener('error', () => {
  if ($('callAudio').getAttribute('src')) $('audioStatus').textContent = 'Playback could not continue. Reload the recording and try again.';
});
$('callTrash').addEventListener('click', async () => {
  const c = state.callSelected;
  if (!c || state.saving) return;
  state.saving = true;
  const unlock = disableControls($('callDetail').querySelectorAll('button'));
  $('callNotice').textContent = 'Updating call…';
  try {
    const result = await moveSelected([c], { businessId: state.business.id, removing: !c.is_trashed, table: 'calls', columns: CALL_COLUMNS, request: api });
    if (!state.session) return;
    if (result.updated.length) {
      $('callDetail').close();
      await load();
      $('actionNotice').textContent = c.is_trashed ? 'Call restored. Its recording and customer requests were kept.' : 'Call moved to Trash. Its recording and customer requests were kept.';
      $('actionNotice').hidden = false;
    } else {
      $('callNotice').textContent = result.conflicts.length ? 'This call changed elsewhere. Close this window and refresh before trying again.' : 'The change could not be confirmed. Close this window and refresh to check before retrying.';
    }
  } finally { state.saving = false; unlock(); }
});

/* ---------- One request ---------- */

const field = name => $(`f-${name}`);
const heardText = (row, key) => key === 'phone' ? formatPhone(row.original?.[key]) : key === 'service' ? serviceLabel(row.original?.[key], state.menu) : row.original?.[key] || '(nothing)';

$('statusChoices').replaceChildren(...STATUSES.map(([value, title, help]) => el('label', { className: 'choice' },
  el('input', { type: 'radio', name: 'status', value }),
  el('span', { className: 'choice-title', textContent: title }),
  el('span', { className: 'choice-help', textContent: help }))));

function openRequest(row) {
  if ($('callDetail').open) $('callDetail').close();
  renderRequestCall(row);
  state.selected = row;
  state.editing = false;
  $('detailName').textContent = row.name;
  $('detailReceived').textContent = `Received ${when(row.created_at)}`;
  $('trashAction').textContent = row.is_trashed ? 'Restore to inbox' : 'Move to trash';
  $('trashAction').disabled = false;
  $('followStatus').disabled = Boolean(row.is_trashed);
  field('owner_notes').disabled = Boolean(row.is_trashed);
  $('save').hidden = Boolean(row.is_trashed);
  field('service').replaceChildren(...state.menu.map(([code, label]) => new Option(label, code)));
  fillForm(row);
  setEditing(false);
  clearErrors();
  $('reloadLatest').hidden = true;
  saveStatus('', row.is_trashed ? 'This request is in Trash. Restore it before making changes.' : '');
  updateDirty();
  if (!$('detail').open) $('detail').showModal();
  $('detailName').focus();
}

function fillForm(row) {
  for (const key of CAPTURED) field(key).value = key === 'phone' ? formatPhone(row.phone) : row[key] ?? '';
  field('owner_notes').value = row.owner_notes ?? '';
  for (const input of document.querySelectorAll('#statusChoices input')) input.checked = input.value === row.status;
  renderReadView(row);
  renderHeard(row);
  renderTranscript(row);
}

// What was said on the call, turn by turn. Text only; the owner can read it
// but not change it, and it is forgotten 90 days after the call.
function renderTranscript(row) {
  const turns = transcriptTurns(row);
  $('transcriptBox').open = true;
  $('transcriptMeta').textContent = turns.length ? `${turns.length} turns` : 'Not available';
  $('transcript').replaceChildren(...(turns.length
    ? transcriptNodes(turns)
    : [el('p', { className: 'muted small', textContent: 'No transcript for this call. Transcripts are kept for 90 days, and calls taken before this feature was added have none.' })]));
}

function transcriptNodes(turns) {
  return turns.map(t => el('div', { className: `turn turn-${t.speaker.toLowerCase()}` },
    el('span', { className: 'turn-who', textContent: t.speaker }),
    el('span', { className: 'turn-text', textContent: t.text })));
}

function requestFacts(row) {
  const fixed = corrections(row);
  return CAPTURED.flatMap(key => {
    let value = row[key] || 'Not given';
    if (key === 'service') value = serviceLabel(row.service, state.menu);
    const dd = el('dd');
    if (key === 'phone' && row.phone) dd.append(el('a', { href: `tel:${row.phone}`, textContent: formatPhone(row.phone) }));
    else dd.append(value);
    if (fixed.includes(key)) dd.append(el('span', { className: 'heard' }, 'AI heard: ', el('strong', { textContent: heardText(row, key) })));
    return [el('dt', { textContent: FIELD_LABELS[key] }), dd];
  });
}

function renderReadView(row) {
  $('readView').replaceChildren(...requestFacts(row));
}

// In correction mode, show what the AI heard under any field that now differs from it.
function renderHeard(row) {
  for (const key of CAPTURED) {
    const box = document.querySelector(`[data-field="${key}"]`);
    box.querySelector('.heard')?.remove();
    if (!row.original) continue;
    const current = key === 'phone' ? field(key).value.replace(/\D/g, '').slice(-10) : field(key).value.trim();
    const heard = key === 'phone' ? String(row.original.phone ?? '').replace(/\D/g, '').slice(-10) : String(row.original[key] ?? '').trim();
    if (current !== heard) {
      const p = el('p', { className: 'heard', id: `heard-${key}` }, 'AI heard: ', el('strong', { textContent: heardText(row, key) }));
      box.querySelector('.error-text') ? box.querySelector('.error-text').before(p) : box.append(p);
    }
  }
}

function setEditing(on) {
  state.editing = on;
  $('readView').hidden = on;
  $('correctView').hidden = !on;
  $('startCorrect').hidden = on || Boolean(state.selected?.is_trashed);
}

$('startCorrect').addEventListener('click', () => { setEditing(true); renderHeard(state.selected); field('name').focus(); });
$('cancelCorrect').addEventListener('click', () => {
  const row = state.selected;
  for (const key of CAPTURED) field(key).value = key === 'phone' ? formatPhone(row.phone) : row[key] ?? '';
  clearErrors(CAPTURED);
  setEditing(false);
  renderHeard(row);
  updateDirty();
  $('startCorrect').focus();
});

function formValues() {
  const row = state.selected;
  const values = Object.fromEntries(CAPTURED.map(k => [k, state.editing ? field(k).value : row[k]]));
  values.status = document.querySelector('#statusChoices input:checked')?.value ?? row.status;
  values.owner_notes = field('owner_notes').value;
  return values;
}

function pendingChanges() {
  const { clean, errors } = validateInquiry(formValues(), state.menu.map(([c]) => c));
  return { changes: changedFields(clean, state.selected), errors };
}

function updateDirty() {
  if (!state.selected || state.saving) return;
  if (state.selected.is_trashed) { $('save').disabled = true; return; }
  const { changes } = pendingChanges();
  const dirty = Object.keys(changes).length > 0 || rawDiffers();
  $('save').disabled = !dirty;
  if (dirty) saveStatus('dirty', 'You have unsaved changes.');
  else if ($('saveStatus').classList.contains('is-dirty')) saveStatus('', '');
}

// Catches edits that don't survive cleaning yet (for example, a half-typed phone number).
function rawDiffers() {
  if (!state.editing) return false;
  const row = state.selected;
  return CAPTURED.some(k => (k === 'phone' ? field(k).value !== formatPhone(row.phone) : field(k).value !== (row[k] ?? '')));
}

$('edit').addEventListener('input', e => {
  const name = e.target.name;
  if (name && e.target.getAttribute('aria-invalid')) clearErrors([name]);
  if (CAPTURED.includes(name)) renderHeard(state.selected);
  updateDirty();
});

function clearErrors(keys = [...CAPTURED, 'status', 'owner_notes']) {
  for (const key of keys) {
    const box = document.querySelector(`[data-field="${key}"]`);
    box?.querySelector('.error-text')?.remove();
    const input = key === 'status' ? null : field(key);
    input?.removeAttribute('aria-invalid');
    if (input) input.id === 'f-owner_notes' ? input.setAttribute('aria-describedby', 'notesHint') : input.removeAttribute('aria-describedby');
  }
}

function showErrors(errors) {
  clearErrors();
  for (const [key, message] of Object.entries(errors)) {
    const box = document.querySelector(`[data-field="${key}"]`);
    const p = el('p', { className: 'error-text', id: `error-${key}`, textContent: message });
    box.append(p);
    const input = key === 'status' ? null : field(key);
    if (input) { input.setAttribute('aria-invalid', 'true'); input.setAttribute('aria-describedby', key === 'owner_notes' ? `notesHint ${p.id}` : p.id); }
  }
  const first = [...CAPTURED, 'status', 'owner_notes'].find(k => errors[k]);
  if (CAPTURED.includes(first)) setEditing(true);
  (first === 'status' ? document.querySelector('#statusChoices input') : field(first))?.focus();
}

function saveStatus(kind, text) {
  $('saveStatus').className = `save-status${kind ? ` is-${kind}` : ''}`;
  $('saveStatus').textContent = text;
}

$('edit').addEventListener('submit', async e => {
  e.preventDefault();
  if (state.saving || !state.selected || state.selected.is_trashed) return;
  const row = state.selected;
  const { changes, errors } = pendingChanges();
  if (errors) {
    showErrors(errors);
    const n = Object.keys(errors).length;
    return saveStatus('error', `Not saved. Please fix the ${n === 1 ? 'highlighted field' : `${n} highlighted fields`}.`);
  }
  if (!Object.keys(changes).length) { setEditing(false); return saveStatus('', 'No changes to save.'); }

  state.saving = true;
  $('trashAction').disabled = true;
  $('save').disabled = true; $('save').setAttribute('aria-busy', 'true'); $('save').textContent = 'Saving…';
  $('close').disabled = true;
  saveStatus('saving', 'Saving your changes…');
  try {
    // Only saves if nobody else changed this request since it was opened.
    const updated = await api(
      `/rest/v1/inquiries?id=eq.${encodeURIComponent(row.id)}&updated_at=eq.${encodeURIComponent(row.updated_at)}&select=${COLUMNS}`,
      { method: 'PATCH', headers: { Prefer: 'return=representation' }, body: changes });
    if (updated?.length === 1) {
      Object.assign(row, updated[0]);
      $('detailName').textContent = row.name;
      fillForm(row);
      setEditing(false);
      render();
      saveStatus('saved', `Saved at ${new Date().toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}.`);
    } else {
      const latest = await api(`/rest/v1/inquiries?id=eq.${encodeURIComponent(row.id)}&select=${COLUMNS}`);
      if (latest.length) {
        saveStatus('error', 'Not saved. This request was changed somewhere else after you opened it. Load the latest version, then make your change again.');
        $('reloadLatest').hidden = false;
        $('reloadLatest').onclick = () => {
          Object.assign(row, latest[0]); render(); openRequest(row); $('reloadLatest').hidden = true;
        };
      } else {
        saveStatus('error', 'Not saved. This request is no longer available to your account.');
      }
    }
  } catch (err) {
    if (!state.session) return;   // signed out; the sign-in screen explains why
    saveStatus('error', `Not saved. ${err.message}${err.kind === 'invalid' ? ' Check the details and try again.' : ''}`);
  } finally {
    state.saving = false;
    $('trashAction').disabled = false;
    $('save').removeAttribute('aria-busy'); $('save').textContent = 'Save changes';
    $('close').disabled = false;
    if (state.selected) {
      const stillDirty = Object.keys(pendingChanges().changes).length > 0;
      $('save').disabled = !stillDirty;
    }
  }
});

$('trashAction').addEventListener('click', async () => {
  const row = state.selected;
  if (!row || state.saving) return;
  if (Object.keys(pendingChanges().changes).length || rawDiffers()) {
    saveStatus('error', 'Save or undo your changes before moving this request.');
    return;
  }
  const removing = !row.is_trashed;
  state.saving = true;
  const unlock = disableControls($('edit').elements);
  $('trashAction').setAttribute('aria-busy', 'true');
  $('reloadLatest').hidden = true;
  saveStatus('saving', removing ? 'Moving to trash…' : 'Restoring request…');
  try {
    const updated = await api(
      `/rest/v1/inquiries?id=eq.${encodeURIComponent(row.id)}&business_id=eq.${encodeURIComponent(row.business_id)}&updated_at=eq.${encodeURIComponent(row.updated_at)}&select=${COLUMNS}`,
      { method: 'PATCH', headers: { Prefer: 'return=representation' }, body: { is_trashed: removing } });
    if (!state.session || state.selected !== row) return;
    if (updated?.length === 1) {
      Object.assign(row, updated[0]);
      $('detail').close(); state.selected = null;
      render();
      $('actionNotice').textContent = removing ? 'Request moved to Trash. You can restore it from the Trash view.' : 'Request restored to your inbox with its previous follow-up status.';
      $('actionNotice').hidden = false;
      (removing ? $('showTrash') : $('showInbox')).focus();
    } else {
      const latest = await api(`/rest/v1/inquiries?id=eq.${encodeURIComponent(row.id)}&select=${COLUMNS}`);
      if (!state.session || state.selected !== row) return;
      saveStatus('error', latest.length
        ? 'Not changed. This request changed elsewhere. Load the latest version before trying again.'
        : 'Not changed. This request is no longer available to your account.');
      $('reloadLatest').hidden = !latest.length;
      $('reloadLatest').onclick = () => { Object.assign(row, latest[0]); render(); openRequest(row); };
    }
  } catch (err) {
    if (state.session && state.selected === row) saveStatus('error', `Could not confirm the change. ${err.message} Close this request and refresh to check its current location before retrying.`);
  } finally {
    state.saving = false;
    unlock();
    $('trashAction').removeAttribute('aria-busy');
    updateDirty();
  }
});

function confirmDiscard() {
  if (!state.selected || $('save').disabled) return true;
  return window.confirm('Discard your unsaved changes to this request?');
}
function closeRequest() {
  if (state.saving || !confirmDiscard()) return;
  const id = state.selected?.id;
  $('detail').close();
  state.selected = null;
  document.querySelector(`.request[data-id="${CSS.escape(id ?? '')}"]`)?.focus();
}
$('close').addEventListener('click', closeRequest);
$('detail').addEventListener('cancel', e => { e.preventDefault(); closeRequest(); });   // Escape key

/* ---------- Start ---------- */

$('startingRetry').addEventListener('click', start);
$('openSettings').addEventListener('click', () => {
  if (!state.session || !$('settings').hidden || state.saving || state.bulkBusy) return;
  if ($('detail').open && !confirmDiscard()) return;
  if ($('detail').open) { $('detail').close(); state.selected = null; }
  if ($('callDetail').open) $('callDetail').close();
  stopAudio();
  show('settings'); settingsUI.open();
});
window.addEventListener('beforeunload', event => {
  if (settingsUI.hasUnsavedChanges()) { event.preventDefault(); event.returnValue = ''; }
});

function authFormNotice(id, text, error = false) {
  $(id).textContent = text; $(id).hidden = !text; $(id).className = error ? 'notice' : 'notice info';
}
$('forgotPassword').addEventListener('click', () => {
  $('forgotEmail').value = $('email').value.trim(); authFormNotice('forgotNotice',''); show('forgot'); $('forgotTitle').focus();
});
$('forgotBack').addEventListener('click', () => { show('login'); $('email').focus(); });
$('forgotForm').addEventListener('submit', async event => {
  event.preventDefault(); const email = $('forgotEmail').value;
  const button = $('sendReset'); if (button.disabled) return;
  button.disabled = true; button.setAttribute('aria-busy','true'); authFormNotice('forgotNotice','');
  try {
    await accountClient.requestRecovery(email, new URL('/?auth=recovery',authReturnOrigin).href);
    authFormNotice('forgotNotice','If an account exists for that email, it will receive a password-reset link. Check your inbox and spam folder.');
  } catch (error) { authFormNotice('forgotNotice',error.message,true); }
  finally { button.disabled = false; button.removeAttribute('aria-busy'); }
});
function showRecovery() {
  $('recoveryEmail').textContent = state.session.email;
  $('recoveryPassword').value = ''; $('recoveryConfirm').value = '';
  authFormNotice('recoveryNotice',''); show('recovery'); $('recoveryTitle').focus();
}
$('recoveryBack').addEventListener('click', async () => {
  try { await call('/auth/v1/logout?scope=local',{method:'POST'}); } catch { /* local cleanup still applies */ }
  endSession('You can sign in or request a new password-reset link.');
});
$('recoveryForm').addEventListener('submit', async event => {
  event.preventDefault(); const button = $('saveRecovery'); if (button.disabled) return;
  const password = $('recoveryPassword').value, confirmation = $('recoveryConfirm').value;
  $('recoveryPassword').value = ''; $('recoveryConfirm').value = '';
  if (password !== confirmation) return authFormNotice('recoveryNotice','The new passwords did not match. Enter them again.',true);
  button.disabled = true; button.setAttribute('aria-busy','true'); const epoch = authEpoch;
  try {
    await accountClient.completeRecovery(password);
    if (!state.session || epoch !== authEpoch) return;
    try { await call('/auth/v1/logout?scope=local',{method:'POST'}); } catch { /* password success is already confirmed */ }
    if (epoch === authEpoch) endSession('Your password has been updated. Sign in with your new password.');
  } catch (error) { if (epoch === authEpoch && state.session) authFormNotice('recoveryNotice',error.message,true); }
  finally { button.disabled = false; button.removeAttribute('aria-busy'); }
});

(async function boot() {
  const callback = parseAuthCallback(window.location.href);
  if (callback.cleanURL !== window.location.href) window.history.replaceState(null,'',callback.cleanURL);
  $('demoNotice').hidden = !cfg?.demo;
  if (!cfg?.publishableKey) { show('login'); return loginNotice('This portal is not connected yet. No customer data is loaded.', 'info'); }
  if (callback.error) { endSession('That account link is invalid or expired. Sign in to request a new link.'); return; }
  if (callback.pendingEmailConfirmation) {
    const saved=readSession();
    if(saved?.refresh_token){state.session=saved;authEpoch++;await start();}
    else show('login');
    $('accountLinkNotice').textContent='One email confirmation was accepted. Open the confirmation link sent to your other email address to finish the change. Your sign-in email has not changed yet.';
    $('accountLinkNotice').hidden=false;
    return;
  }
  if (callback.session) {
    const epoch = authEpoch; show('starting');
    try {
      const verified = await accountClient.validateCallback(callback);
      if (epoch !== authEpoch) return;
      setSession({...verified,recovery:callback.type === 'recovery'});
      if (callback.type === 'recovery') showRecovery();
      else { await start(); if (state.session && !state.session.recovery) { show('settings'); await settingsUI.open(); } }
    } catch { if (epoch === authEpoch) endSession('That account link is invalid or expired. Request a new link and try again.'); }
    return;
  }
  // Only the local fictional server enables this shortcut. Production config does not.
  const demoAccounts = { sampleclub: 'pickleball@example.test', pickleball: 'pickleball@example.test', 'property-services': 'property@example.test' };
  const demoEmail = demoAccounts[new URLSearchParams(window.location.search).get('demo')];
  if (cfg.demo && typeof demoEmail === 'string') {
    show('starting');
    try {
      setSession(await call('/auth/v1/token?grant_type=password', {
        method: 'POST', auth: false, body: { email: demoEmail, password: 'fictional-password' },
      }));
      await start();
    } catch (err) { endSession(err.message); }
    return;
  }
  const saved = readSession();
  if (!saved?.refresh_token) { show('login'); return; }
  state.session = saved;
  authEpoch++;
  show('starting');
  await start().catch(() => {});
})();
