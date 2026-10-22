"""Actual dashboard assets in Chromium, with the authority and all HTTP fully mocked."""
import json
import os
import unittest
from pathlib import Path
from urllib.parse import urlparse

import test_dashboard_ui as dashboard


class LicenseUiTests(unittest.TestCase):
    def setUp(self):
        dashboard.DashboardUiTests.setUp(self)
        self.page.wait_for_load_state('networkidle')
        self.is_manager=False
        self.status={'valid':False,'state':'not_activated','required':True,'installation_id':'fixture-installation-123456'}
        self.licenses=[]
        self.license_requests=[]
        def auth(route):
            route.fulfill(json={'is_admin':self.is_manager,'edition':'ADMIN' if self.is_manager else 'CLIENT'})
        def route(request_route):
            request=request_route.request;path=urlparse(request.url).path
            if request.method!='GET':self.license_requests.append((request.method,path,request.post_data_json if request.post_data else None))
            if path.endswith('/status'):return request_route.fulfill(json=self.status)
            if path.endswith('/activate'):
                self.status.update(valid=True,state='active',customer='مشتری آزمایشی',expires_at=1900000000)
                return request_route.fulfill(json=self.status)
            if path.endswith('/admin/licenses'):
                if request.method=='POST':
                    item=dict(id='a'*32,customer=request.post_data_json['customer'],key_hint='MHN-MARR-…test',expires_at=1900000000,active=True,max_devices=2,devices_used=0)
                    self.licenses.append(item)
                    return request_route.fulfill(json={'license_id':item['id'],'license_key':'MHN-MARR-fixture-key-shown-only-once'})
                return request_route.fulfill(json=self.licenses)
            if path.endswith('/state'):
                self.licenses[0]['active']=request.post_data_json['active']
            request_route.fulfill(json={'status':'ok'})
        self.context.route('**/auth/me',auth)
        self.context.route('**/api/license/**',route)
        self.page.locator('.sidebar-nav').get_by_role('button',name='لایسنس ماهان',exact=True).click()
        self.page.evaluate('MahanLicense.refresh();MahanLicense.loadManager()')
        self.page.wait_for_function("MahanLicense.getState().state==='not_activated'")

    def tearDown(self):
        dashboard.DashboardUiTests.tearDown(self)

    def test_client_activation_and_browser_storage_cannot_unlock(self):
        self.page.evaluate("localStorage.setItem('mahan_license_data',JSON.stringify({isUnlocked:true}));MahanLicense.refresh()")
        self.assertFalse(self.page.evaluate('MahanLicense.getState().valid'))
        self.assertTrue(self.page.locator('#centralLicenseManager').is_hidden())
        self.page.locator('#licenseActivationKey').fill('MHN-MARR-fixture-key')
        self.page.locator('#activateLicenseButton').click()
        self.page.wait_for_function("MahanLicense.getState().state==='active'")
        self.assertEqual(self.page.locator('#licenseActivationKey').input_value(),'')
        self.assertEqual(self.license_requests[0],('POST','/api/license/activate',{'license_key':'MHN-MARR-fixture-key'}))
        self.assertFalse(any('/admin/' in path for _,path,_ in self.license_requests))

    def test_manager_issue_renew_revoke_and_customer_text_is_not_html(self):
        self.is_manager=True
        self.page.evaluate('MahanLicense.loadManager()')
        self.page.wait_for_function("!document.getElementById('centralLicenseManager').hidden")
        self.page.on('dialog',lambda dialog:dialog.accept())
        name='<img src=x onerror="window.injected=true"> مشتری آزمایشی'
        self.page.locator('#licenseIssueCustomer').fill(name)
        self.page.locator('#licenseIssueDays').fill('15')
        self.page.locator('#licenseIssueDevices').fill('2')
        self.page.locator('#issueLicenseButton').click()
        self.page.wait_for_function("document.querySelectorAll('#licenseRows tr').length===1")
        self.assertEqual(self.page.locator('#licenseRows tr td').first.inner_text(),name)
        self.assertFalse(self.page.evaluate('Boolean(window.injected)'))
        self.assertIn('MHN-MARR-',self.page.locator('#licenseIssuedKey').input_value())
        self.page.locator('#licenseRows').get_by_role('button',name='تمدید',exact=True).click()
        self.page.wait_for_function("document.querySelector('#licenseMessage').textContent.includes('ذخیره شد')")
        self.page.locator('#licenseRows').get_by_role('button',name='غیرفعال‌سازی',exact=True).click()
        self.page.wait_for_function("document.querySelector('#licenseRows').textContent.includes('غیرفعال')")
        methods=[method for method,_,_ in self.license_requests]
        self.assertEqual(methods,['POST','POST','PUT'])

    def test_mobile_layout_and_connection_error_are_visible(self):
        for width in (390,720,1440):
            self.page.set_viewport_size({'width':width,'height':900})
            button=self.page.locator('#activateLicenseButton')
            button.click(trial=True)
            box=button.bounding_box()
            self.assertGreaterEqual(box['x'],0);self.assertLessEqual(box['x']+box['width'],width)
        self.status.update(valid=False,state='network_error',message='ارتباط سرور برقرار نیست.')
        self.page.evaluate('MahanLicense.refresh()')
        self.page.wait_for_function("MahanLicense.getState().state==='network_error'")
        self.assertIn('خطای اتصال',self.page.locator('#licenseState').inner_text())
        self.page.set_viewport_size({'width':390,'height':900})
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),390)
        evidence=os.getenv('MAHANBOT_LICENSE_UI_EVIDENCE')
        if evidence:
            Path(evidence).mkdir(parents=True,exist_ok=True)
            self.page.set_viewport_size({'width':390,'height':900})
            self.page.screenshot(path=str(Path(evidence)/'license-client-mobile.png'),full_page=True)
            self.is_manager=True
            self.status.update(required=False,state='administrator',valid=True)
            self.page.evaluate('loadSubscriptionManager();MahanLicense.refresh();MahanLicense.loadManager()')
            self.page.wait_for_function("!document.getElementById('centralLicenseManager').hidden")
            self.page.wait_for_function("MahanLicense.getState().state==='administrator'")
            self.page.wait_for_function("document.getElementById('editionTitle').textContent==='MAHANBOT ADMIN'")
            self.page.set_viewport_size({'width':1440,'height':1000})
            self.page.screenshot(path=str(Path(evidence)/'license-manager-desktop.png'),full_page=True)
