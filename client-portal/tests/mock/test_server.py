"""SAMPLECLUB preview fixtures and business isolation; no real Supabase calls."""
import io
import json
import wave
import urllib.error
import urllib.parse
import threading
import unittest
import urllib.request
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import server


class SAMPLECLUBPreviewTests(unittest.TestCase):
    def test_property_services_examples_are_valid_and_separate(self):
        business = next((b for b in server.BUSINESSES if b['slug'] == 'property-services'), None)
        self.assertIsNotNone(business)
        rows = [r for r in server.seed() if r['business_id'] == business['id']]
        self.assertGreaterEqual(len(rows), 6)
        self.assertEqual({r['status'] for r in rows}, {'new', 'contacted', 'closed'})
        self.assertTrue({'water_damage', 'sewage_cleanup', 'drain_cleaning', 'mold_inquiry'} <= {r['service'] for r in rows})
        self.assertEqual(server.USERS['property@example.test']['businesses'], [business['id']])
        for row in rows:
            self.assertTrue(server.check_row(row))
            self.assertIn('FICTIONAL', row['preferences'])
            self.assertTrue(row['transcript']['turns'])

    def test_sampleclub_examples_cover_its_categories_and_have_valid_details(self):
        rows = [r for r in server.seed() if r['business_id'] == server.SAMPLECLUB]
        self.assertEqual(len(rows), 9)
        self.assertEqual({r['service'] for r in rows}, {
            'court_rental', 'lesson', 'membership', 'open_play', 'private_event', 'general_inquiry'})
        self.assertEqual({r['status'] for r in rows}, {'new', 'contacted', 'closed'})
        for row in rows:
            UUID(row['id'])
            self.assertTrue(server.check_row(row))
            self.assertTrue(row['preferences'])
            self.assertTrue(row['transcript']['turns'])

    def test_examples_include_today_and_previous_month_for_date_filters(self):
        rows = [r for r in server.seed() if r['business_id'] == server.SAMPLECLUB]
        today = datetime.now(ZoneInfo('America/New_York')).date()
        days = [datetime.fromisoformat(r['created_at']).astimezone(ZoneInfo('America/New_York')).date() for r in rows]
        self.assertIn(today, days)
        self.assertTrue(any((d.year, d.month) != (today.year, today.month) for d in days))

    def test_sampleclub_login_cannot_read_or_update_sample_studio_records(self):
        http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{http.server_port}'
        def request(path, method='GET', body=None, token=None):
            headers = {'Content-Type': 'application/json'}
            if token:
                headers['Authorization'] = f'Bearer {token}'
            req = urllib.request.Request(base + path, method=method, headers=headers,
                data=json.dumps(body).encode() if body is not None else None)
            with urllib.request.urlopen(req) as response:
                return json.load(response)
        try:
            login = request('/auth/v1/token?grant_type=password', 'POST',
                {'email': 'sampleclub@example.test', 'password': server.PASSWORD})
            token = login['access_token']
            businesses = request('/rest/v1/businesses', token=token)
            self.assertEqual([b['slug'] for b in businesses], ['sampleclub'])
            rows = request('/rest/v1/inquiries', token=token)
            self.assertEqual(len(rows), 9)
            self.assertEqual({r['business_id'] for r in rows}, {server.SAMPLECLUB})
            self.assertEqual(request(f'/rest/v1/inquiries?business_id=eq.{server.MV}', token=token), [])
            self.assertEqual(request(f'/rest/v1/inquiries?business_id=eq.{server.MV}', 'PATCH',
                {'status': 'closed'}, token), [])
            updated = request(f'/rest/v1/inquiries?id=eq.{rows[0]["id"]}', 'PATCH',
                {'status': 'contacted', 'owner_notes': 'Fictional follow-up'}, token)
            self.assertEqual(updated[0]['owner_notes'], 'Fictional follow-up')
            self.assertEqual(updated[0]['original'], rows[0]['original'])
        finally:
            http.shutdown()
            http.server_close()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())


class CallsPreviewTests(unittest.TestCase):
    def setUp(self):
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.http.server_port}'
        self.request('/__mock/reset', 'POST')
        self.token = self.request('/auth/v1/token?grant_type=password', 'POST',
            {'email': 'member@example.test', 'password': server.PASSWORD})['access_token']

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, method='GET', body=None, token=None, raw=False):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        req = urllib.request.Request(self.base + path, method=method, headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req) as response:
                return response.read() if raw else json.load(response)
        except urllib.error.HTTPError as error:
            error.close()
            raise

    def calls(self):
        return self.request('/rest/v1/calls?limit=500&offset=0', token=self.token)

    def test_calls_include_linked_and_no_request_history_with_membership_and_pagination(self):
        calls = self.calls()
        self.assertGreaterEqual(len(calls), 4)
        self.assertEqual({r['business_id'] for r in calls}, {server.MV})
        self.assertTrue({'available', 'pending', 'unavailable'} <= {r['recording_status'] for r in calls})
        inquiries = self.request('/rest/v1/inquiries', token=self.token)
        linked = {r['call_id'] for r in inquiries if r['call_id']}
        self.assertTrue(linked & {r['id'] for r in calls})
        self.assertTrue({r['id'] for r in calls} - linked)
        self.assertEqual(self.request(f'/rest/v1/calls?business_id=eq.{server.OTHER}', token=self.token), [])
        page = self.request('/rest/v1/calls?limit=1&offset=1', token=self.token)
        self.assertEqual(page, calls[1:2])
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.request('/rest/v1/calls')
        self.assertEqual(err.exception.code, 401)

    def test_calls_only_allow_guarded_trash_updates_in_own_business(self):
        row = self.calls()[0]
        filters = urllib.parse.urlencode({'id': 'eq.' + row['id'], 'business_id': 'eq.' + server.MV, 'updated_at': 'eq.' + row['updated_at']})
        path = '/rest/v1/calls?' + filters
        changed = self.request(path, 'PATCH', {'is_trashed': True}, self.token)
        self.assertTrue(changed[0]['is_trashed'])
        self.assertNotEqual(changed[0]['updated_at'], row['updated_at'])
        self.assertEqual(self.request(path, 'PATCH', {'is_trashed': False}, self.token), [])
        self.assertEqual(self.request(f'/rest/v1/calls?business_id=eq.{server.OTHER}', 'PATCH', {'is_trashed': True}, self.token), [])
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.request('/rest/v1/calls', 'PATCH', {'recording_status': 'available'}, self.token)
        self.assertEqual(err.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.request('/rest/v1/calls', 'PATCH', {'is_trashed': 'true'}, self.token)
        self.assertEqual(err.exception.code, 400)
        self.request('/__mock/reset', 'POST')
        self.assertFalse(any(r['is_trashed'] for r in server.STATE['calls']))

    def test_signed_audio_is_synthetic_and_cannot_access_other_or_unavailable_calls(self):
        calls = self.calls()
        available = next(r for r in calls if r['recording_status'] == 'available' and not r['recording_expires_at'])
        sign_path = '/storage/v1/object/sign/call-recordings/' + available['recording_path']
        signed = self.request(sign_path, 'POST', {'expiresIn': 60}, self.token)['signedURL']
        content = self.request('/storage/v1' + signed, raw=True)
        with wave.open(io.BytesIO(content), 'rb') as wav:
            self.assertEqual(wav.getnchannels(), 1)
            self.assertGreater(wav.getnframes(), 0)
        tampered = signed.replace(available['id'], '00000000-0000-0000-0000-000000000000')
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.request('/storage/v1' + tampered, raw=True)
        self.assertEqual(err.exception.code, 403)
        for row in calls:
            if row['recording_status'] != 'available' or row['recording_expires_at']:
                path = f'/storage/v1/object/sign/call-recordings/{row["business_id"]}/{row["id"]}/audio.ogg'
                with self.assertRaises(urllib.error.HTTPError) as err:
                    self.request(path, 'POST', {'expiresIn': 60}, self.token)
                self.assertEqual(err.exception.code, 404)
        other = next(r for r in server.STATE['calls'] if r['business_id'] == server.OTHER and r['recording_status'] == 'available')
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.request('/storage/v1/object/sign/call-recordings/' + other['recording_path'], 'POST', {}, self.token)
        self.assertEqual(err.exception.code, 403)
        token = urllib.parse.parse_qs(urllib.parse.urlparse(signed).query)['token'][0]
        server.STATE['signed'][token]['expires'] = 0
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.request('/storage/v1' + signed, raw=True)
        self.assertEqual(err.exception.code, 403)


class SettingsPreviewTests(unittest.TestCase):
    setUp = CallsPreviewTests.setUp
    tearDown = CallsPreviewTests.tearDown
    request = CallsPreviewTests.request

    def login(self, email):
        return self.request('/auth/v1/token?grant_type=password', 'POST',
            {'email': email, 'password': server.PASSWORD})

    def test_profile_updates_keep_signin_email_separate_from_alert_recipient(self):
        owner = self.login('owner@example.test')['access_token']
        profile = self.request('/auth/v1/user', token=owner)
        UUID(profile['id'])
        changed = self.request('/auth/v1/user', 'PUT', {'data': {'display_name': 'FICTIONAL Alex'}}, owner)
        self.assertEqual(changed['user_metadata']['display_name'], 'FICTIONAL Alex')
        pending = self.request('/auth/v1/user', 'PUT', {'email': 'new-owner@example.test'}, owner)
        self.assertEqual(pending['email'], 'owner@example.test')
        self.assertEqual(pending['new_email'], 'new-owner@example.test')
        before = self.request(f'/rest/v1/businesses?id=eq.{server.MV}', token=owner)[0]
        self.request('/__mock/confirm-email', 'POST', {'email': 'owner@example.test'})
        self.assertEqual(self.request('/auth/v1/user', token=owner)['email'], 'new-owner@example.test')
        self.assertEqual(self.request(f'/rest/v1/businesses?id=eq.{server.MV}', token=owner)[0]['notify_email'], before['notify_email'])

    def test_only_owners_can_change_alert_preferences_and_old_versions_do_not_write(self):
        owner = self.login('owner@example.test')['access_token']
        before = self.request(f'/rest/v1/businesses?id=eq.{server.MV}', token=owner)[0]
        filters = urllib.parse.urlencode({'id': 'eq.' + server.MV, 'settings_updated_at': 'eq.' + before['settings_updated_at']})
        path = '/rest/v1/businesses?' + filters
        prefs = {'notify_email': 'requests@example.test', 'request_alerts_enabled': False}
        self.assertEqual(self.request(path, 'PATCH', prefs, self.token), [])
        saved = self.request(path, 'PATCH', prefs, owner)[0]
        self.assertEqual(saved['notify_email'], 'requests@example.test')
        self.assertFalse(saved['request_alerts_enabled'])
        self.assertEqual(self.request(path, 'PATCH', {'request_alerts_enabled': True}, owner), [])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(path, 'PATCH', {'name': 'Not permitted'}, owner)
        self.assertEqual(error.exception.code, 403)

    def test_first_secure_email_confirmation_does_not_complete_the_email_change(self):
        owner = self.login('owner@example.test')['access_token']
        self.request('/auth/v1/user', 'PUT', {'email': 'new-owner@example.test'}, owner)
        link = self.request('/__mock/email-confirmation-link', 'POST')['url']
        fragment = urllib.parse.parse_qs(urllib.parse.urlparse(link).fragment, keep_blank_values=True)
        self.assertIn('message', fragment)
        self.assertNotIn('access_token', fragment)
        profile = self.request('/auth/v1/user', token=owner)
        self.assertEqual(profile['email'], 'owner@example.test')
        self.assertEqual(profile['new_email'], 'new-owner@example.test')

    def test_change_requests_are_attributed_isolated_and_immutable(self):
        user = self.request('/auth/v1/user', token=self.token)
        payload = {'id': '99999999-1111-4111-8111-111111111111', 'business_id': server.MV,
                   'category': 'hours', 'description': 'FICTIONAL close at five.'}
        saved = self.request('/rest/v1/receptionist_change_requests', 'POST', payload, self.token)[0]
        self.assertEqual(saved['requested_by'], user['id'])
        self.assertEqual(saved['status'], 'submitted')
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/rest/v1/receptionist_change_requests', 'POST', payload, self.token)
        self.assertEqual(error.exception.code, 409)
        other = self.login('other@example.test')['access_token']
        self.assertEqual(self.request('/rest/v1/receptionist_change_requests', token=other), [])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/rest/v1/receptionist_change_requests', 'PATCH', {'status': 'completed'}, self.token)
        self.assertEqual(error.exception.code, 403)

    def test_others_logout_revokes_other_refresh_sessions_and_preserves_current_device(self):
        first = self.login('owner@example.test')
        second = self.login('owner@example.test')
        self.request('/auth/v1/logout?scope=others', 'POST', {}, second['access_token'], raw=True)
        # Issued access tokens remain usable until expiry, matching real Supabase.
        self.assertEqual(self.request('/auth/v1/user', token=first['access_token'])['email'], 'owner@example.test')
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/auth/v1/token?grant_type=refresh_token', 'POST', {'refresh_token': first['refresh_token']})
        self.assertEqual(error.exception.code, 400)
        refreshed = self.request('/auth/v1/token?grant_type=refresh_token', 'POST', {'refresh_token': second['refresh_token']})
        self.assertEqual(refreshed['user']['email'], 'owner@example.test')

    def test_recovery_requests_do_not_disclose_membership_and_fake_recovery_tokens_work(self):
        existing = self.request('/auth/v1/recover', 'POST', {'email': 'owner@example.test'})
        missing = self.request('/auth/v1/recover', 'POST', {'email': 'missing@example.test'})
        self.assertEqual(existing, missing)
        link = self.request('/__mock/recovery-link', 'POST', {'email': 'owner@example.test'})['url']
        values = urllib.parse.parse_qs(urllib.parse.urlparse(link).fragment)
        recovery = values['access_token'][0]
        self.assertEqual(self.request('/auth/v1/user', token=recovery)['email'], 'owner@example.test')
        self.request('/auth/v1/user', 'PUT', {'password': 'changed-fictional-password'}, recovery)
        with self.assertRaises(urllib.error.HTTPError):
            self.login('owner@example.test')
        login = self.request('/auth/v1/token?grant_type=password', 'POST',
            {'email': 'owner@example.test', 'password': 'changed-fictional-password'})
        self.assertEqual(login['user']['email'], 'owner@example.test')


if __name__ == '__main__':
    unittest.main()
