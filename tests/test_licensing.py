"""Central authority + actual MAHANBOT client integration; no loan site, SMS or real customers."""
import concurrent.futures
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

import database
from database import DBHandler
from core import licensing
from license_server.app import create_app
from license_server.store import LicenseError, LicenseStore, PRODUCT
from test_critical_repair import RepairTestCase

ADMIN_TOKEN = 'test-authority-secret-' + 'a' * 48
IDENTITY = {'installation_id': 'test-installation-123456', 'machine_hash': 'a' * 64}


class CentralAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = LicenseStore(Path(self.temp.name) / 'authority.db')
        self.client = TestClient(create_app(self.store, ADMIN_TOKEN))
        self.prefix = '/api/licensing/v1'
        self.headers = {'Authorization': 'Bearer ' + ADMIN_TOKEN}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def issue(self, **updates):
        body = dict(customer='مشتری آزمایشی', days=30, max_devices=1) | updates
        response = self.client.post(self.prefix + '/admin/licenses', json=body, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def activate(self, key, **identity):
        return self.client.post(self.prefix + '/activate', json=dict(IDENTITY, **identity, license_key=key))

    def test_issue_activate_verify_revoke_and_reenable(self):
        key = self.issue()
        activation = self.activate(key['license_key']).json()
        self.assertTrue(activation['valid'])
        auth = {'Authorization': 'Bearer ' + activation['activation_token']}
        self.assertTrue(self.client.post(self.prefix + '/verify', json=IDENTITY, headers=auth).json()['valid'])
        url = self.prefix + '/admin/licenses/' + key['license_id'] + '/state'
        self.client.put(url, json={'active': False}, headers=self.headers)
        self.assertEqual(self.client.post(self.prefix + '/verify', json=IDENTITY, headers=auth).json()['state'], 'revoked')
        self.client.put(url, json={'active': True}, headers=self.headers)
        self.assertTrue(self.client.post(self.prefix + '/verify', json=IDENTITY, headers=auth).json()['valid'])

    def test_credentials_are_hashed_and_not_returned_in_list(self):
        key = self.issue()
        activation = self.activate(key['license_key']).json()
        listing = self.client.get(self.prefix + '/admin/licenses', headers=self.headers).text
        self.assertNotIn(key['license_key'], listing)
        self.assertNotIn(activation['activation_token'], listing)
        with self.store.connect() as conn:
            raw = str([tuple(r) for r in conn.execute('SELECT * FROM licenses')]) + str([tuple(r) for r in conn.execute('SELECT * FROM devices')])
        self.assertNotIn(key['license_key'], raw)
        self.assertNotIn(activation['activation_token'], raw)

    def test_admin_auth_validation_and_cross_product_keys(self):
        for headers in ({}, {'Authorization': 'Bearer wrong'}):
            self.assertEqual(self.client.get(self.prefix + '/admin/licenses', headers=headers).status_code, 401)
        self.assertEqual(self.client.post(self.prefix + '/admin/licenses', headers=self.headers, json={'customer':'test','days':0}).status_code, 422)
        self.assertEqual(self.activate('MHN-SCREENING-NOT-A-MARRIAGE-LICENSE').status_code, 403)
        self.assertEqual(self.client.post(self.prefix + '/verify', json=IDENTITY).status_code, 401)

    def test_expired_license_can_be_renewed_without_losing_remaining_days(self):
        key = self.issue()
        with self.store.connect() as conn:
            conn.execute('UPDATE licenses SET expires_at=? WHERE id=?', (time.time() - 10, key['license_id']))
        self.assertEqual(self.activate(key['license_key']).json()['state'], 'expired')
        renewed = self.store.change(key['license_id'], days=2)
        self.assertTrue(renewed['valid'])
        before = renewed['expires_at']
        self.assertAlmostEqual(self.store.change(key['license_id'], days=3)['expires_at'] - before, 3 * 86400)

    def test_device_limit_duplicate_activation_binding_and_release(self):
        key = self.issue()
        first = self.activate(key['license_key']).json()
        second = self.activate(key['license_key']).json()
        self.assertNotEqual(first['activation_token'], second['activation_token'])
        self.assertEqual(self.activate(key['license_key'], installation_id='another-installation-123').json()['state'], 'device_limit')
        auth = {'Authorization': 'Bearer ' + second['activation_token']}
        self.assertEqual(self.client.post(self.prefix+'/verify', json=dict(IDENTITY,machine_hash='b'*64),headers=auth).json()['state'],'device_mismatch')
        device = self.store.devices(key['license_id'])[0]
        self.store.release_device(key['license_id'], device['id'])
        self.assertEqual(self.client.post(self.prefix+'/verify',json=IDENTITY,headers=auth).json()['state'],'not_activated')
        self.assertTrue(self.activate(key['license_key'], installation_id='another-installation-123').json()['valid'])

    def test_concurrent_activations_cannot_exceed_device_limit(self):
        key = self.issue()
        def activate(number):
            try:
                self.store.activate(key['license_key'], 'concurrent-installation-' + str(number), 'a' * 64)
                return 'active'
            except LicenseError as exc:
                return exc.state
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(activate, range(4)))
        self.assertEqual(results.count('active'), 1)
        self.assertEqual(results.count('device_limit'), 3)


class LicenseClientIntegrationTests(RepairTestCase):
    def setUp(self):
        super().setUp()
        self.store = LicenseStore(Path(self.temp.name) / 'authority.db')
        self.authority = TestClient(create_app(self.store, ADMIN_TOKEN))
        self.env_license = patch.dict(os.environ, {'MAHANBOT_EDITION': 'CLIENT', 'MAHANBOT_LICENSE_SERVER':'http://license.invalid/api/licensing/v1'})
        self.env_license.start()
        # TLS rules are tested separately. This fake transport calls the real authority app in memory.
        self.url_patch = patch('core.licensing.server_url',return_value='http://license.invalid/api/licensing/v1')
        self.url_patch.start()
        self.transport = patch('core.licensing.requests.request',side_effect=lambda method,url,**kw:self.authority.request(method,url,headers=kw['headers'],json=kw['json']))
        self.transport.start()
        licensing.clear_cache()
        self.key = self.store.issue('مشتری یکپارچه آزمایشی',30,1)

    def tearDown(self):
        self.transport.stop();self.url_patch.stop();self.env_license.stop()
        licensing.clear_cache();self.authority.close()
        super().tearDown()

    def test_activation_renew_revoke_are_authoritative_and_tokens_stay_in_backend(self):
        self.assertFalse(licensing.can_run())
        result = licensing.activate_license(self.key['license_key'])
        self.assertTrue(result['valid'])
        self.assertNotIn('activation_token', result)
        self.assertTrue(licensing.can_run(force=True))
        self.store.change(self.key['license_id'],active=False)
        self.assertFalse(licensing.can_run(force=True))
        self.assertEqual(licensing.check_license()['state'],'revoked')
        self.store.change(self.key['license_id'],active=True,days=2)
        self.assertTrue(licensing.can_run(force=True))
        licensing.deactivate_local()
        self.assertFalse(licensing.can_run(force=True))

    def test_network_timeout_malformed_response_and_wrong_product_fail_closed(self):
        licensing.activate_license(self.key['license_key'])
        with patch('core.licensing.requests.request',side_effect=requests.Timeout('private transport details')):
            result=licensing.check_license(force=True)
            self.assertFalse(result['valid']);self.assertEqual(result['state'],'network_error')
            self.assertNotIn('private',json.dumps(result))
        from unittest.mock import Mock
        response=Mock(status_code=200)
        for data in ({'valid':True,'product':'screening'},None,{'valid':'true','product':PRODUCT}):
            response.json.return_value=data
            with patch('core.licensing.requests.request',return_value=response):
                self.assertEqual(licensing.check_license(force=True)['state'],'invalid_response')

    def test_expiry_is_checked_even_inside_valid_cache_and_state_survives_restart(self):
        licensing.activate_license(self.key['license_key'])
        licensing.clear_cache()
        self.assertTrue(licensing.can_run(force=True))
        with patch('core.licensing.time.time',return_value=self.key['expires_at']+1):
            self.assertEqual(licensing.check_license()['state'],'expired')
        with self.store.connect() as conn:
            conn.execute('UPDATE licenses SET expires_at=? WHERE id=?',(time.time()-1,self.key['license_id']))
        self.assertEqual(licensing.check_license(force=True)['state'],'expired')
        self.store.change(self.key['license_id'],days=2)
        licensing.clear_cache()
        self.assertTrue(licensing.can_run(force=True))

    def test_admin_manager_is_forbidden_for_client_even_with_local_admin_fallback(self):
        licensing.activate_license(self.key['license_key'])
        from server import app
        client=TestClient(app)
        for method,path,body in [('GET','/api/license/admin/licenses',None),('POST','/admin/users',{'username':'client-admin','password':'test-password','days':30})]:
            self.assertEqual(client.request(method,path,json=body).status_code,403)
        with self.assertRaises(licensing.LicenseServiceError):
            licensing.manager_request('GET','')

    def test_manager_proxy_issues_and_changes_license_without_exposing_admin_secret(self):
        from server import app
        client=TestClient(app)
        with patch.dict(os.environ,{'MAHANBOT_EDITION':'ADMIN','MAHANBOT_LICENSE_ADMIN_TOKEN':ADMIN_TOKEN}):
            response=client.post('/api/license/admin/licenses',json={'customer':'مشتری پنل آزمایشی','days':5,'max_devices':2})
            self.assertEqual(response.status_code,200)
            license_id=response.json()['license_id']
            response=client.put('/api/license/admin/licenses/'+license_id+'/state',json={'active':False})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['state'],'revoked')
            public=client.get('/api/license/status').text+client.get('/auth/me').text+client.get('/api/license/admin/licenses').text
            self.assertNotIn(ADMIN_TOKEN,public)

    def test_unlicensed_job_and_mutations_blocked_but_stop_and_activation_remain_available(self):
        from server import app
        client=TestClient(app)
        for path,body in [('/jobs/start',{'nid':self.nid,'bot_name':'select'}),('/api/capacity/start-all',{}),('/settings',{'auto_final_submit':True})]:
            self.assertEqual(client.post(path,json=body).status_code,402)
        self.assertEqual(client.get('/api/license/status').status_code,200)
        self.assertNotEqual(client.post('/bot/stop/'+self.nid).status_code,402)
        self.assertTrue(client.post('/api/license/activate',json={'license_key':self.key['license_key']}).json()['valid'])
        from unittest.mock import Mock
        queue=Mock()
        queue.find_active.return_value=None
        queue.enqueue.return_value.to_dict.return_value={'id':'test-only-job','status':'queued'}
        with patch('core.state.JOB_QUEUE',queue):
            self.assertEqual(client.post('/jobs/start',json={'nid':self.nid,'bot_name':'select'}).status_code,200)

    def test_license_revocation_pauses_page_three_without_closing_browser(self):
        self.owner.bank_page_active=True
        licensing.pause_unlicensed_sessions()
        self.assertEqual(self.owner.commands.get_nowait(),'pause')
        licensing.pause_unlicensed_sessions()
        self.assertTrue(self.owner.commands.empty())
        self.assertFalse(self.owner.cancellation_token.is_set())
        self.assertTrue(self.owner.alive)

    def test_license_gate_does_not_replace_sms_webhook_authentication(self):
        from server import app
        client=TestClient(app)
        with patch.dict(os.environ,{'MAHANBOT_OTP_SECRET':'test-only-webhook-secret'}):
            response=client.post('/receive_sms',json={'text':'fixture without an OTP'})
            self.assertEqual(response.status_code,401)

    def test_server_configuration_rejects_plain_http_external_and_embedded_credentials(self):
        self.url_patch.stop()
        try:
            for url in ('http://public.example/api','https://user:password@example/api','https://example/api?token=hidden'):
                with patch.dict(os.environ,{'MAHANBOT_LICENSE_SERVER':url}):
                    with self.assertRaises(licensing.LicenseServiceError):licensing.server_url()
        finally:
            self.url_patch.start()
