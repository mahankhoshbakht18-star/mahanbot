"""Customer distributions cannot carry the license authority credential, activations or loan data."""
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('license_package_release',Path(__file__).resolve().parents[1]/'scripts/package_release.py')
package=importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class LicensePackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        files={'server.py':'print("fixture")','.env':'MANAGER_PRIVATE_VALUE=never-ship-this',
               '.licenses.private.env':'MAHANBOT_LICENSE_ADMIN_TOKEN=never-ship-this',
               'licensing.db':'private-license-db','cbi_ultimate.db':'private-applicants',
               'cbi_clean_template.db':'clean-template','.env.example':'# public template',
               'license_server/models.py':'# wire models','license_server/__init__.py':'',
               'license_server/store.py':'# authority','license_server/app.py':'# authority api',
               'chrome_profiles/default/session.txt':'private-session','saved_pages/private.html':'private-session',
               'static/live_browser/private.jpg':'private-applicant-preview','static/index.html':'<p>fixture</p>',
               'scripts/deploy_license_server.py':'# deployment helper','tests/test_fake.py':'# developer tests'}
        files.update({'deploy/private.env':'never-ship-this','android/release.jks':'never-ship-this',
                      'mahan':'never-ship-this','run_admin.bat':'set MAHANBOT_EDITION=ADMIN',
                      'scripts/test_otp_local.py':'# developer fixture',
                      'scripts/create_clean_distribution_db.py':'# clean installer',
                      'old-package.zip':'obsolete-archive'})
        for name,value in files.items():
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value)
        self.patch=patch.multiple(package,ROOT_DIR=self.root,DOWNLOADS_DIR=self.root/'downloads')
        self.patch.start()

    def tearDown(self):
        self.patch.stop();self.temp.cleanup()

    def test_customer_archive_is_license_enforced_and_contains_no_admin_secrets_or_data(self):
        path,_=package.package_release('test-license')
        with zipfile.ZipFile(path) as z:
            names=set(z.namelist())
            self.assertIn('MAHANBOT_EDITION=CLIENT',z.read('.env').decode())
            self.assertIn('MAHANBOT_REQUIRE_LICENSE=true',z.read('.env').decode())
            self.assertIn('license_server/models.py',names)
            self.assertIn('cbi_clean_template.db',names)
            self.assertIn('scripts/create_clean_distribution_db.py',names)
            self.assertNotIn('run_admin.bat',names)
            self.assertNotIn('scripts/test_otp_local.py',names)
            self.assertNotIn('mahan',names)
            for forbidden in ('.licenses.private.env','licensing.db','cbi_ultimate.db','license_server/app.py','license_server/store.py','chrome_profiles/default/session.txt','saved_pages/private.html','static/live_browser/private.jpg','scripts/deploy_license_server.py','tests/test_fake.py'):
                self.assertNotIn(forbidden,names)
            self.assertNotIn(b'never-ship-this',b''.join(z.read(name) for name in z.namelist()))
            self.assertNotIn('old-package.zip',names)
        self.assertEqual(json.loads((self.root/'downloads/release-metadata.json').read_text())['edition'],'CLIENT')

    def test_admin_archive_still_excludes_authority_secret_and_preserves_admin_mode(self):
        path,_=package.package_release('test-admin','ADMIN')
        with zipfile.ZipFile(path) as z:
            self.assertIn('MAHANBOT_EDITION=ADMIN',z.read('.env').decode())
            self.assertNotIn('.licenses.private.env',z.namelist())

    def test_invalid_edition_is_rejected_before_packaging(self):
        with self.assertRaises(ValueError):package.package_release('test','unknown')
