"""Real HTTPS smoke test, using only a temporary local DB and a clearly marked revoked QA license."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
import database
from core.config import load_dotenv
from core import licensing


def main():
    root=Path(__file__).resolve().parents[1]
    load_dotenv(root/'.licenses.private.env')
    original_db=database.DB_PATH
    original_edition=os.environ.get('MAHANBOT_EDITION')
    result={}
    license_id=None
    try:
        health=requests.get(licensing.server_url()+'/health',timeout=(5,8))
        assert health.status_code==200 and health.json().get('product')==licensing.PRODUCT,'Central HTTPS health check failed'
        result['https_health']='passed'
        with tempfile.TemporaryDirectory(prefix='mahan-license-live-check-') as temp:
            database.DB_PATH=str(Path(temp)/'client-only-test.db')
            os.environ['MAHANBOT_EDITION']='ADMIN'
            issued=licensing.manager_request('POST','',{'customer':'آزمایش اتصال '+time.strftime('%Y-%m-%d %H:%M',time.gmtime()),'days':1,'max_devices':1})
            license_id=issued['license_id']
            os.environ['MAHANBOT_EDITION']='CLIENT'
            public=licensing.activate_license(issued['license_key'])
            assert public['valid'] and 'activation_token' not in public,'Activation failed'
            assert licensing.check_license(force=True)['valid'],'Client verification failed'
            result['client_activation']='passed'
            try:
                licensing._request('POST','/activate',{'license_key':issued['license_key'],'installation_id':'live-qa-second-installation','machine_hash':'b'*64})
                raise AssertionError('Device limit was not enforced')
            except licensing.LicenseServiceError as exc:
                assert exc.state=='device_limit','Unexpected device-limit response'
            result['device_limit']='passed'
            os.environ['MAHANBOT_EDITION']='ADMIN'
            licensing.manager_request('PUT','/'+license_id+'/state',{'active':False})
            os.environ['MAHANBOT_EDITION']='CLIENT'
            assert licensing.check_license(force=True)['state']=='revoked','Remote revocation did not reach the client'
            result['remote_revocation']='passed'
            os.environ['MAHANBOT_EDITION']='ADMIN'
            licensing.manager_request('POST','/'+license_id+'/renew',{'days':1})
            licensing.manager_request('PUT','/'+license_id+'/state',{'active':True})
            os.environ['MAHANBOT_EDITION']='CLIENT'
            assert licensing.check_license(force=True)['valid'],'Renewed client did not recover'
            result['remote_renewal']='passed'
            os.environ['MAHANBOT_EDITION']='ADMIN'
            devices=licensing.manager_request('GET','/'+license_id+'/devices')
            assert len(devices)==1,'Unexpected device registration count'
            licensing.manager_request('DELETE','/'+license_id+'/devices/'+devices[0]['id'])
            os.environ['MAHANBOT_EDITION']='CLIENT'
            assert not licensing.check_license(force=True)['valid'],'Released device remained authorized'
            result['device_release']='passed'
        result['applicant_database']='not_used'
        response=requests.get('https://otp.mahanvip.ir/health',timeout=(5,8))
        assert response.status_code==200,'Existing OTP health failed'
        result['existing_otp_health']='passed'
    finally:
        if license_id:
            os.environ['MAHANBOT_EDITION']='ADMIN'
            licensing.manager_request('PUT','/'+license_id+'/state',{'active':False})
        database.DB_PATH=original_db
        licensing.clear_cache()
        if original_edition is None:os.environ.pop('MAHANBOT_EDITION',None)
        else:os.environ['MAHANBOT_EDITION']=original_edition
    print(json.dumps(result))
    pointer=root.parent/'mahanbot_license_backup_path.txt'
    if pointer.exists():(Path(pointer.read_text().strip())/'live-license-check.json').write_text(json.dumps(result,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
