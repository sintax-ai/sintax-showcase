"""LOCAL TEST ONLY: a stand-in for Supabase so the portal can be clicked through
without touching real data. Every record and account here is FICTIONAL.

    python3 tests/mock/server.py        # then open http://127.0.0.1:4174

Fictional accounts (password for both: fictional-password):
    member@example.test  -> "Example Studios (FICTIONAL TEST DATA)"
    other@example.test   -> "Other Business (FICTIONAL)"
    nobody@example.test  -> signed in, but no business membership
    pickleball@example.test -> Pickleball, nine fictional examples
    sampleclub@example.test    -> same generic example (legacy shortcut)

Presentation shortcut: http://127.0.0.1:4174/?demo=pickleball (fictional auto-login).
Keep this development server local; do not deploy it as a public demo service.

It imitates the rules in supabase/migrations: membership-scoped reads, the
column-level update grant, format checks, the frozen `original`, and the
updated_at guard. Test controls live under /__mock/ (see handle_control).
"""
import copy
import io
import math
import struct
import wave
import json
import re
import secrets
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from uuid import UUID, uuid4, uuid5, NAMESPACE_DNS
from sampleclub_fixture import seed_sampleclub
from property_fixture import seed_property

PORT = 4174
PUBLIC = Path(__file__).resolve().parents[2] / 'public'
PASSWORD = 'fictional-password'
MV = 'd00c0c1d-7687-5992-bfbd-b69a214c566e'
OTHER = '11111111-2222-3333-4444-555555555555'
SAMPLECLUB = '22222222-3333-4444-5555-666666666666'  # Fictional; not its reserved production ID.
PROPERTY = '33333333-4444-5555-6666-777777777777'  # Fictional industry example.
USERS = {
    'owner@example.test': {'id': 'u-owner', 'businesses': [MV], 'roles': {MV: 'owner'}},
    'member@example.test': {'id': 'u-member', 'businesses': [MV]},
    'other@example.test': {'id': 'u-other', 'businesses': [OTHER]},
    'nobody@example.test': {'id': 'u-nobody', 'businesses': []},
    'sampleclub@example.test': {'id': 'u-sampleclub', 'businesses': [SAMPLECLUB]},
    'pickleball@example.test': {'id': 'u-pickleball', 'businesses': [SAMPLECLUB]},
    'property@example.test': {'id': 'u-property', 'businesses': [PROPERTY]},
}
for _email, _user in USERS.items():
    _user['id'] = str(uuid5(NAMESPACE_DNS, 'sintax-fictional-' + _email))
BUSINESSES = [
    {'id': MV, 'name': 'Example Studios (FICTIONAL TEST DATA)', 'slug': 'sample-studio'},
    {'id': OTHER, 'name': 'Other Business (FICTIONAL)', 'slug': 'other-fictional'},
    {'id': SAMPLECLUB, 'name': 'Pickleball', 'slug': 'sampleclub'},
    {'id': PROPERTY, 'name': 'Property Services', 'slug': 'property-services'},
]
UPDATABLE = {'status', 'owner_notes', 'name', 'phone', 'service', 'preferred_time', 'duration', 'preferences', 'is_trashed'}
CAPTURED = ('name', 'phone', 'service', 'preferred_time', 'duration', 'preferences')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def inquiry(i, business, name, phone, service, when, duration, prefs, created, status='new', notes='', transcript=None):
    row = {'id': f'00000000-0000-0000-0000-{i:012d}', 'business_id': business, 'source_id': f'fict-{i}',
           'created_at': created, 'updated_at': created, 'name': name, 'phone': phone, 'service': service,
           'preferred_time': when, 'duration': duration, 'preferences': prefs, 'status': status, 'owner_notes': notes}
    row['original'] = {k: row[k] for k in CAPTURED}
    row['transcript'] = transcript
    row['is_trashed'] = False
    return row


def seed():
    rows = [
        inquiry(1, MV, 'FICTIONAL Alex Sample', '+15555550101', 'recording', 'Saturday at 2pm', '2 hours', 'Studio A, own beats', '2026-09-17T05:03:01.630402+00:00',
                transcript={'saved_at': '2026-09-17T05:04:00+00:00', 'turns': [
                    {'speaker': 'receptionist', 'text': 'Hi, this is Sophia from Example Studios. How can I help you today? (FICTIONAL)'},
                    {'speaker': 'caller', 'text': "Hey, I'd like to book two hours in Studio A on Saturday at 2."},
                    {'speaker': 'receptionist', 'text': 'Happy to take that request for the studio to confirm. Can I get your name?'},
                    {'speaker': 'caller', 'text': "It's Alex Example."},
                    {'speaker': 'receptionist', 'text': 'Thanks, Alex Sample. And the best number to reach you?'},
                    {'speaker': 'caller', 'text': '555-555-0101.'},
                    {'speaker': 'receptionist', 'text': 'Got it. Two hours in Studio A this Saturday at 2 PM for Alex Sample at 555-555-0101. Shall I save that for the studio to review?'},
                    {'speaker': 'caller', 'text': 'Yes please.'},
                ]}),
        inquiry(2, MV, 'FICTIONAL Alex', '+15555550102', 'recording', 'to discuss', 'to discuss', '', '2026-09-17T20:17:00.561108+00:00', 'contacted', 'Left voicemail'),
        inquiry(3, MV, 'FICTIONAL Priya Shah', '+15555550103', 'photoshoot', 'Next Friday morning', '1 hour', 'Pro package, white cyc wall', '2026-09-17T20:39:57.699086+00:00'),
        inquiry(4, MV, '=HYPERLINK("http://evil.example","FICTIONAL click me")', '+15555550104', 'mastering', 'Any weekday', 'to discuss', '3 tracks', '2026-09-17T20:47:34.809212+00:00', 'closed'),
        inquiry(5, OTHER, 'FICTIONAL Other-Business Caller', '+15555550105', 'consultation', 'Monday', '30 minutes', '', '2026-09-16T12:00:00+00:00'),
    ] + seed_sampleclub(inquiry, SAMPLECLUB) + seed_property(inquiry, PROPERTY)
    linked = set()
    for row in rows:
        business = row['business_id']
        row['call_id'] = None
        if business not in linked:
            index = [MV, OTHER, SAMPLECLUB, PROPERTY].index(business)
            row['call_id'] = f'10000000-0000-0000-0000-{index * 10 + 1:012d}'
            linked.add(business)
    return rows


def seed_calls():
    calls = []
    clock = datetime.now(timezone.utc)
    for index, business in enumerate([MV, OTHER, SAMPLECLUB, PROPERTY]):
        for offset, status in enumerate(['available', 'pending', 'unavailable', 'available']):
            call_id = f'10000000-0000-0000-0000-{index * 10 + offset + 1:012d}'
            started = clock - timedelta(hours=offset + index)
            calls.append({
                'id': call_id, 'business_id': business,
                'session_id': f'FICTIONAL-session-{index}-{offset}',
                'room_id': f'FICTIONAL-room-{index}-{offset}', 'mode': 'demo',
                'started_at': started.isoformat(), 'ended_at': (started + timedelta(seconds=2)).isoformat(),
                'duration_seconds': 2, 'recording_status': status,
                'recording_path': f'{business}/{call_id}/audio.ogg' if status == 'available' else None,
                'recording_expires_at': (clock - timedelta(days=1)).isoformat() if offset == 3 else None,
                'is_trashed': False, 'updated_at': started.isoformat(),
            })
    return calls


def fictional_tone():
    # Synthetic low-volume 440 Hz tone only; never load a real call recording.
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b''.join(struct.pack('<h', int(1500 * math.sin(2 * math.pi * 440 * sample / 16000)))
                                  for sample in range(32000)))
    return output.getvalue()


TONE = fictional_tone()


def seed_state():
    businesses = copy.deepcopy(BUSINESSES)
    for business in businesses:
        business.update(notify_email='alerts@example.test', request_alerts_enabled=True, settings_updated_at=now())
    profiles = {email: {'id': user['id'], 'aud': 'authenticated', 'role': 'authenticated', 'email': email,
        'email_confirmed_at': now(), 'confirmed_at': now(), 'phone': '', 'created_at': now(), 'updated_at': now(),
        'user_metadata': {'display_name': 'FICTIONAL Alex' if email == 'owner@example.test' else ''},
        'app_metadata': {'provider': 'email', 'providers': ['email']}, 'identities': [], 'new_email': ''}
        for email, user in USERS.items()}
    return {'rows': seed(), 'calls': seed_calls(), 'businesses': businesses, 'profiles': profiles,
        'passwords': {email: PASSWORD for email in USERS}, 'changes': [], 'recovery_requests': [],
        'signed': {}, 'access': {}, 'refresh': {}, 'expire_all': False, 'revoke_refresh': False,
        'fail_next_patch': False, 'drop_next': False, 'log': []}


STATE = seed_state()
LOCK = threading.Lock()


def check_row(row):
    """Mirrors the CHECK constraints in 002_owner_corrections.sql."""
    return (1 <= len(row['name'].strip()) <= 200 and re.fullmatch(r'\+1[0-9]{10}', row['phone'])
            and re.fullmatch(r'[a-z][a-z0-9_]{0,49}', row['service'])
            and 1 <= len(row['preferred_time'].strip()) <= 500 and 1 <= len(row['duration'].strip()) <= 500
            and len(row['preferences'] or '') <= 2000 and len(row['owner_notes'] or '') <= 10000
            and row['status'] in ('new', 'contacted', 'closed') and isinstance(row['is_trashed'], bool))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(PUBLIC), **kw)

    def log_message(self, fmt, *args):
        STATE['log'].append(f'{self.command} {self.path} -> {args[1] if len(args) > 1 else ""}')

    # ---------- helpers
    def send_json(self, status, body, extra=None):
        data = b'' if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n) or b'null')

    def user(self):
        auth = self.headers.get('Authorization', '')
        token = auth.removeprefix('Bearer ').strip()
        entry = STATE['access'].get(token)
        if not entry:
            return None, 'none' if not auth else 'invalid'
        if STATE['expire_all'] or entry['exp'] < time.time():
            return None, 'expired'
        return entry['email'], 'ok'

    def maybe_drop(self):
        if STATE['drop_next']:
            STATE['drop_next'] = False
            self.connection.shutdown(socket.SHUT_RDWR)
            return True
        return False

    def issue(self, email, session_id=None):
        access, refresh = secrets.token_hex(16), secrets.token_hex(16)
        session_id = session_id or str(uuid4())
        STATE['access'][access] = {'email': email, 'exp': time.time() + 3600, 'session_id': session_id}
        STATE['refresh'][refresh] = {'email': email, 'session_id': session_id}
        return {'access_token': access, 'refresh_token': refresh, 'expires_in': 3600,
                'expires_at': int(time.time()) + 3600, 'token_type': 'bearer', 'user': copy.deepcopy(STATE['profiles'][email])}

    # ---------- routes
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == '/config.js':
            js = "window.PORTAL_CONFIG = { url: window.location.origin, publishableKey: 'FICTIONAL-mock-key', demo: true };\n"
            data = js.encode()
            self.send_response(200); self.send_header('Content-Type', 'text/javascript')
            self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(data)))
            self.end_headers(); self.wfile.write(data); return
        if url.path.startswith('/__mock/'):
            return self.handle_control(url)
        if url.path == '/auth/v1/user':
            with LOCK:
                email, status = self.user()
                return self.send_json(200, STATE['profiles'][email]) if email else self.send_json(401, {'code': 'bad_jwt', 'message': 'Sign in required'})
        if url.path.startswith(('/object/sign/call-recordings/', '/storage/v1/object/sign/call-recordings/')):
            with LOCK:
                return self.serve_audio(url)
        if url.path.startswith('/rest/v1/'):
            if self.maybe_drop():
                return
            with LOCK:
                return self.rest_get(url)
        return super().do_GET()

    def do_POST(self):
        url = urlparse(self.path)
        if url.path.startswith('/__mock/'):
            return self.handle_control(url)
        if self.maybe_drop():
            return
        with LOCK:
            if url.path.startswith('/storage/v1/object/sign/call-recordings/'):
                return self.sign_audio(url)
            if url.path == '/auth/v1/token':
                grant = parse_qs(url.query).get('grant_type', [''])[0]
                body = self.body() or {}
                if grant == 'password':
                    key = next((key for key, profile in STATE['profiles'].items() if profile['email'] == body.get('email')), None)
                    if key and body.get('password') == STATE['passwords'][key]:
                        return self.send_json(200, self.issue(key))
                    return self.send_json(400, {'code': 'invalid_credentials', 'msg': 'Invalid login credentials'})
                if grant == 'refresh_token':
                    entry = STATE['refresh'].pop(body.get('refresh_token'), None)
                    if entry and not STATE['revoke_refresh']:
                        STATE['expire_all'] = False
                        return self.send_json(200, self.issue(entry['email'], entry['session_id']))
                    return self.send_json(400, {'code': 'refresh_token_not_found', 'msg': 'Invalid Refresh Token'})
            if url.path == '/auth/v1/logout':
                email, status = self.user()
                if not email:
                    return self.send_json(401, {'code': 'bad_jwt', 'message': 'Sign in required'})
                entry = STATE['access'][self.headers.get('Authorization', '').removeprefix('Bearer ')]
                scope = parse_qs(url.query).get('scope', ['global'])[0]
                for refresh, other in list(STATE['refresh'].items()):
                    same_session = other['session_id'] == entry['session_id']
                    if other['email'] == email and (scope == 'global' or (scope == 'others' and not same_session) or (scope == 'local' and same_session)):
                        del STATE['refresh'][refresh]
                return self.send_json(204, None)
            if url.path == '/auth/v1/recover':
                body = self.body() or {}
                STATE['recovery_requests'].append(body.get('email'))
                return self.send_json(200, {})
            if url.path == '/rest/v1/receptionist_change_requests':
                return self.change_request_post(url)
            if url.path in ('/rest/v1/inquiries', '/rest/v1/calls'):
                email, status = self.user()
                return self.send_json(401 if not email else 403, {'code': '42501', 'message': 'permission denied for table inquiries'})
        self.send_json(404, {'message': 'not found'})

    def do_PUT(self):
        url = urlparse(self.path)
        if self.maybe_drop():
            return
        with LOCK:
            email, status = self.user()
            if not email:
                return self.send_json(401, {'code': 'bad_jwt', 'message': 'Sign in required'})
            if url.path != '/auth/v1/user':
                return self.send_json(404, {'message': 'not found'})
            body = self.body() or {}
            profile = STATE['profiles'][email]
            if 'data' in body:
                profile['user_metadata'].update(body['data'])
            if 'email' in body:
                if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', body['email']):
                    return self.send_json(400, {'code': 'email_address_invalid', 'message': 'Invalid email'})
                profile['new_email'] = body['email']
            if 'password' in body:
                if len(body['password']) < 8:
                    return self.send_json(400, {'code': 'weak_password', 'message': 'Use at least 8 characters'})
                STATE['passwords'][email] = body['password']
            profile['updated_at'] = now()
            return self.send_json(200, copy.deepcopy(profile))

    def do_PATCH(self):
        url = urlparse(self.path)
        if self.maybe_drop():
            return
        with LOCK:
            email, status = self.user()
            if status == 'expired':
                return self.send_json(401, {'code': 'PGRST303', 'message': 'JWT expired'})
            if not email:
                return self.send_json(401, {'code': '42501', 'message': 'permission denied for table inquiries'})
            if STATE['fail_next_patch']:
                STATE['fail_next_patch'] = False
                return self.send_json(500, {'message': 'FICTIONAL simulated server failure'})
            if url.path == '/rest/v1/businesses':
                return self.business_patch(url, email)
            if url.path == '/rest/v1/receptionist_change_requests':
                return self.send_json(403, {'code': '42501', 'message': 'Client request updates are not permitted'})
            if url.path not in ('/rest/v1/inquiries', '/rest/v1/calls'):
                return self.send_json(404, {'message': 'not found'})
            is_call = url.path == '/rest/v1/calls'
            if STATE.pop('fail_get_after_patch', False):
                STATE['fail_next_get'] = True
            changes = self.body() or {}
            forbidden = set(changes) - ({'is_trashed'} if is_call else UPDATABLE)
            if forbidden:
                return self.send_json(403, {'code': '42501', 'message': f'permission denied for table inquiries ({", ".join(sorted(forbidden))})'})
            filters = self.filters(url)
            allowed = set(USERS[email]['businesses'])
            matched = [r for r in STATE['calls' if is_call else 'rows'] if r['business_id'] in allowed and self.matches(r, filters)]
            out = []
            for r in matched:
                trial = {**r, **changes}
                if not (isinstance(trial['is_trashed'], bool) if is_call else check_row(trial)):
                    return self.send_json(400, {'code': '23514', 'message': 'new row violates check constraint'})
            for r in matched:
                r.update(changes)
                r['updated_at'] = now()        # trigger: bump; original stays frozen
                out.append(copy.deepcopy(r))
            STATE['log'].append(f'PATCH by {email}: {sorted(changes)} -> {len(out)} row(s)')
            return self.send_json(200, out)

    def do_DELETE(self):
        email, _ = self.user()
        self.send_json(401 if not email else 403, {'code': '42501', 'message': 'permission denied for table inquiries'})

    # ---------- REST
    def filters(self, url):
        out = {}
        for k, vs in parse_qs(url.query, keep_blank_values=True).items():
            v = vs[0]
            if k in ('select', 'order', 'limit', 'offset'):
                continue
            if v.startswith('eq.'):
                out[k] = unquote(v[3:])
        return out

    @staticmethod
    def matches(row, filters):
        return all((str(row.get(k)).lower() if isinstance(row.get(k), bool) else str(row.get(k))) == v for k, v in filters.items())

    def rest_get(self, url):
        if STATE.pop('fail_next_get', False):
            return self.send_json(503, {'message': 'FICTIONAL failed refresh'})
        email, status = self.user()
        table = url.path.removeprefix('/rest/v1/')
        if status == 'expired':
            return self.send_json(401, {'code': 'PGRST303', 'message': 'JWT expired'})
        if status == 'invalid':
            return self.send_json(401, {'code': 'PGRST301', 'message': 'JWT could not be decoded'})
        if not email:
            return self.send_json(401, {'code': '42501', 'message': f'permission denied for table {table}'})
        allowed = set(USERS[email]['businesses'])
        filters = self.filters(url)
        if table == 'businesses':
            return self.send_json(200, [b for b in STATE['businesses'] if b['id'] in allowed and self.matches(b, filters)])
        if table == 'memberships':
            rows = [{'business_id': business, 'user_id': USERS[email]['id'], 'role': USERS[email].get('roles', {}).get(business, 'member')} for business in allowed]
            return self.send_json(200, [row for row in rows if self.matches(row, filters)])
        if table == 'receptionist_change_requests':
            rows = [copy.deepcopy(row) for row in STATE['changes'] if row['business_id'] in allowed and self.matches(row, filters)]
            rows.sort(key=lambda row: (row['created_at'], row['id']), reverse=True)
            limit = min(500, max(0, int(parse_qs(url.query).get('limit', ['500'])[0])))
            return self.send_json(200, rows[:limit])
        if table in ('inquiries', 'calls'):
            rows = [copy.deepcopy(r) for r in STATE['calls' if table == 'calls' else 'rows'] if r['business_id'] in allowed and self.matches(r, filters)]
            rows.sort(key=lambda r: (r['started_at' if table == 'calls' else 'created_at'], r['id']), reverse=True)
            query = parse_qs(url.query)
            try:
                offset = max(0, int(query.get('offset', ['0'])[0]))
                limit = min(500, max(0, int(query.get('limit', ['500'])[0])))
            except ValueError:
                return self.send_json(400, {'message': 'invalid pagination'})
            return self.send_json(200, rows[offset:offset + limit])
        self.send_json(404, {'message': 'not found'})

    def business_patch(self, url, email):
        changes = self.body() or {}
        if set(changes) - {'notify_email', 'request_alerts_enabled'}:
            return self.send_json(403, {'code': '42501', 'message': 'Only alert preference columns are editable'})
        if ('request_alerts_enabled' in changes and not isinstance(changes['request_alerts_enabled'], bool)) or (
            changes.get('notify_email') is not None and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', changes['notify_email'])):
            return self.send_json(400, {'code': '23514', 'message': 'Invalid alert preferences'})
        filters = self.filters(url)
        owners = {business for business, role in USERS[email].get('roles', {}).items() if role == 'owner'}
        rows = [business for business in STATE['businesses'] if business['id'] in owners and self.matches(business, filters)]
        for row in rows:
            if any(row[key] != value for key, value in changes.items()):
                row.update(changes)
                row['settings_updated_at'] = now()
        return self.send_json(200, copy.deepcopy(rows))

    def change_request_post(self, url):
        email, status = self.user()
        if not email:
            return self.send_json(401, {'code': 'bad_jwt', 'message': 'Sign in required'})
        body = self.body() or {}
        if set(body) - {'id', 'business_id', 'category', 'description'} or body.get('business_id') not in USERS[email]['businesses']:
            return self.send_json(403, {'code': '42501', 'message': 'Request not permitted'})
        try:
            UUID(body.get('id', ''))
        except (ValueError, TypeError, AttributeError):
            return self.send_json(400, {'code': '22P02', 'message': 'Invalid request ID'})
        description = body.get('description', '')
        if body.get('category') not in ('hours', 'services', 'pricing', 'voice', 'instructions', 'other') or not isinstance(description, str) or not description.strip() or len(description) > 4000:
            return self.send_json(400, {'code': '23514', 'message': 'Invalid change request'})
        if any(row['id'] == body['id'] for row in STATE['changes']):
            return self.send_json(409, {'code': '23505', 'message': 'Request ID already exists'})
        saved = {**body, 'requested_by': USERS[email]['id'], 'status': 'submitted', 'created_at': now()}
        STATE['changes'].append(saved)
        return self.send_json(201, [copy.deepcopy(saved)])

    # ---------- FICTIONAL private recordings
    @staticmethod
    def available_recording(row):
        expiry = row.get('recording_expires_at')
        return (row['recording_status'] == 'available' and row.get('recording_path')
                and (not expiry or datetime.fromisoformat(expiry) > datetime.now(timezone.utc)))

    def sign_audio(self, url):
        email, status = self.user()
        if not email:
            return self.send_json(401, {'code': 'PGRST303' if status == 'expired' else '42501', 'message': 'Sign in required'})
        path = unquote(url.path.removeprefix('/storage/v1/object/sign/call-recordings/'))
        business = path.split('/')[0]
        if business not in USERS[email]['businesses']:
            return self.send_json(403, {'code': '42501', 'message': 'Recording is not available to this account'})
        row = next((r for r in STATE['calls'] if r['business_id'] == business and r['recording_path'] == path), None)
        if not row or not self.available_recording(row):
            return self.send_json(404, {'message': 'Recording is unavailable or expired'})
        body = self.body() or {}
        try:
            lifetime = min(3600, max(1, int(body.get('expiresIn', 60))))
        except (TypeError, ValueError):
            return self.send_json(400, {'message': 'Invalid expiration'})
        token = secrets.token_hex(24)
        STATE['signed'][token] = {'path': path, 'expires': time.time() + lifetime}
        return self.send_json(200, {'signedURL': f'/object/sign/call-recordings/{path}?token={token}'})

    def serve_audio(self, url):
        path = unquote(url.path.removeprefix('/storage/v1').removeprefix('/object/sign/call-recordings/'))
        token = parse_qs(url.query).get('token', [''])[0]
        grant = STATE['signed'].get(token)
        row = next((r for r in STATE['calls'] if r['recording_path'] == path), None)
        if not grant or grant['path'] != path or grant['expires'] <= time.time() or not row or not self.available_recording(row):
            return self.send_json(403, {'message': 'Recording link is invalid or expired'})
        data = TONE
        start, end = 0, len(data) - 1
        byte_range = self.headers.get('Range')
        if byte_range:
            match = re.fullmatch(r'bytes=(\d+)-(\d*)', byte_range)
            if not match:
                return self.send_json(416, {'message': 'Unsupported byte range'})
            start = int(match[1])
            end = min(end, int(match[2])) if match[2] else end
            if start > end:
                return self.send_json(416, {'message': 'Invalid byte range'})
        self.send_response(206 if byte_range else 200)
        self.send_header('Content-Type', 'audio/wav')
        self.send_header('Cache-Control', 'private, no-store')
        self.send_header('Accept-Ranges', 'bytes')
        if byte_range:
            self.send_header('Content-Range', f'bytes {start}-{end}/{len(data)}')
        self.send_header('Content-Length', str(end - start + 1))
        self.end_headers()
        self.wfile.write(data[start:end + 1])

    # ---------- test controls
    def handle_control(self, url):
        action = url.path.removeprefix('/__mock/')
        with LOCK:
            if action == 'reset':
                STATE.clear()
                STATE.update(seed_state())
            elif action == 'confirm-email':
                body = self.body() or {}
                key = next((key for key, profile in STATE['profiles'].items() if profile['email'] == body.get('email')), None)
                if key and STATE['profiles'][key].get('new_email'):
                    STATE['profiles'][key]['email'] = STATE['profiles'][key].pop('new_email')
                return self.send_json(200, {'confirmed': bool(key)})
            elif action == 'email-confirmation-link':
                from urllib.parse import urlencode
                fragment = urlencode({'message': 'Confirmation link accepted. Please proceed to confirm link sent to the other email', 'sb': ''})
                return self.send_json(200, {'url': f'http://127.0.0.1:{self.server.server_port}/?auth=email-change#{fragment}'})
            elif action == 'recovery-link':
                body = self.body() or {}
                key = next((key for key, profile in STATE['profiles'].items() if profile['email'] == body.get('email')), None)
                if not key:
                    return self.send_json(404, {'message': 'Unknown fictional account'})
                session = self.issue(key)
                from urllib.parse import urlencode
                fragment = urlencode({key: session[key] for key in ('access_token', 'refresh_token', 'expires_in', 'expires_at')}) + '&type=recovery'
                return self.send_json(200, {'url': f'http://127.0.0.1:{self.server.server_port}/?auth=recovery#{fragment}'})
            elif action == 'expire-access':
                STATE['expire_all'] = True            # next data request gets 401 JWT expired
            elif action == 'revoke-refresh':
                STATE['expire_all'] = True; STATE['revoke_refresh'] = True   # renewal fails too
            elif action == 'fail-refresh-after-patch':
                STATE['fail_get_after_patch'] = True
            elif action == 'fail-next-patch':
                STATE['fail_next_patch'] = True
            elif action == 'drop-next':
                STATE['drop_next'] = True             # next API call loses its connection
            elif action.startswith('touch/'):
                rid = action.removeprefix('touch/')   # simulate an edit from another device
                for r in STATE['rows'] + STATE['calls']:
                    if r['id'] == rid:
                        if 'owner_notes' in r:
                            r['owner_notes'] = (r['owner_notes'] + ' [edited on another device]').strip()
                        r['updated_at'] = now()
            elif action != 'state':
                return self.send_json(404, {'message': 'unknown control'})
            return self.send_json(200, {'rows': STATE['rows'], 'calls': STATE['calls'], 'log': STATE['log'][-30:]})


if __name__ == '__main__':
    print(f'FICTIONAL mock portal on http://127.0.0.1:{PORT}  (password: {PASSWORD})')
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
