"""Install the isolated license authority on the approved Mahan host. Never print its admin token."""
import json
import os
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = '95.38.164.78'

REMOTE = r"""
import json,os,pwd,secrets,shutil,sqlite3,subprocess,time,urllib.request,zipfile
from pathlib import Path
os.umask(0o022)
root=Path('/opt/mahan-license')
data=Path('/var/lib/mahan-license')
config=Path('/etc/mahan-license.env')
unit=Path('/etc/systemd/system/mahan-license.service')
nginx=Path('/etc/nginx/sites-available/otp.mahanvip.ir.conf')
if not nginx.exists():
 nginx=Path('/etc/nginx/sites-enabled/otp.mahanvip.ir.conf').resolve()
original=nginx.read_text()
assert 'server_name otp.mahanvip.ir;' in original, 'Unexpected nginx server'
backup=Path('/var/backups/mahan-license')/time.strftime('%Y%m%d_%H%M%S',time.gmtime())
backup.mkdir(parents=True,exist_ok=False,mode=0o700)
for p in (nginx,config,unit,Path('/etc/nginx/conf.d/mahan-license-limits.conf')):
 if p.exists():shutil.copy2(p,backup/p.name)
if (data/'licenses.db').exists():
 with sqlite3.connect(str(data/'licenses.db')) as src,sqlite3.connect(str(backup/'licenses.db')) as dst:src.backup(dst)
if root.exists():
 with zipfile.ZipFile(backup/'source-before.zip','w',zipfile.ZIP_DEFLATED) as z:
  for p in (root/'license_server').glob('*.py'):z.write(p,p.relative_to(root))
try:account=pwd.getpwnam('mahanlicense')
except KeyError:
 subprocess.run(['useradd','--system','--home',str(data),'--no-create-home','--shell','/usr/sbin/nologin','mahanlicense'],check=True)
 account=pwd.getpwnam('mahanlicense')
root.mkdir(parents=True,exist_ok=True);data.mkdir(parents=True,exist_ok=True)
os.chmod(root,0o755)
os.chown(data,account.pw_uid,account.pw_gid);os.chmod(data,0o700)
with zipfile.ZipFile(UPLOAD) as z:
 for name in z.namelist():
  target=(root/name).resolve()
  assert target.is_relative_to(root) and name.startswith('license_server/'), 'Unsafe deployment member'
  target.parent.mkdir(parents=True,exist_ok=True)
  os.chmod(target.parent,0o755)
  target.write_bytes(z.read(name));os.chmod(target,0o644)
python=root/'venv/bin/python'
if not python.exists():subprocess.run(['python3','-m','venv',str(root/'venv')],check=True,capture_output=True)
probe=subprocess.run([str(python),'-c','import fastapi,uvicorn,pydantic'],capture_output=True)
if probe.returncode:
 # Copy only installed distribution files into the isolated environment. The existing
 # OTP interpreter is a read-only source; no pip upgrade or shared site-packages path.
 target=subprocess.run([str(python),'-c','import sysconfig;print(sysconfig.get_paths()["purelib"])'],check=True,capture_output=True,text=True).stdout.strip()
 copy_script='''import importlib.metadata as m,sys,sysconfig,shutil
from pathlib import Path
from pip._vendor.packaging.requirements import Requirement
source=Path(sysconfig.get_paths()["purelib"]).resolve()
destination=Path(sys.argv[1]).resolve()
pending=["fastapi","uvicorn"];seen=set()
while pending:
 name=pending.pop()
 if name.lower() in seen:continue
 seen.add(name.lower());dist=m.distribution(name)
 for filename in dist.files or []:
  origin=Path(dist.locate_file(filename)).resolve()
  if not origin.is_relative_to(source) or not origin.is_file() or origin.suffix==".pyc":continue
  target=destination/origin.relative_to(source)
  target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(origin,target)
 for requirement in dist.requires or []:
  dependency=Requirement(requirement)
  if dependency.marker is None or dependency.marker.evaluate({"extra":""}):pending.append(dependency.name)
'''
 copied=subprocess.run(['/opt/mahan-otp/venv/bin/python','-c',copy_script,target],capture_output=True,text=True)
 probe=subprocess.run([str(python),'-c','import fastapi,uvicorn,pydantic'],capture_output=True)
 if copied.returncode or probe.returncode:raise RuntimeError('Isolated license dependency preparation failed; existing OTP environment was not changed')
for p in (root/'venv').rglob('*'):
 if p.is_symlink():continue
 p.chmod(0o755 if p.is_dir() or p.stat().st_mode & 0o111 else 0o644)
os.chmod(root/'venv',0o755)
token=''
if config.exists():
 for line in config.read_text().splitlines():
  if line.startswith('MAHAN_LICENSE_ADMIN_TOKEN='):token=line.split('=',1)[1].strip()
token=token or secrets.token_urlsafe(48)
assert len(token)>=32
config.write_text('MAHAN_LICENSE_ADMIN_TOKEN='+token+'\nMAHAN_LICENSE_DB='+str(data/'licenses.db')+'\n')
os.chown(config,0,account.pw_gid);os.chmod(config,0o640)
unit.write_text('''[Unit]
Description=Mahan Marriage Bot Central Licensing
After=network.target
StartLimitIntervalSec=60
StartLimitBurst=3
[Service]
User=mahanlicense
Group=mahanlicense
WorkingDirectory=/opt/mahan-license
EnvironmentFile=/etc/mahan-license.env
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/mahan-license/venv/bin/python -m uvicorn license_server.app:create_app --factory --host 127.0.0.1 --port 8012 --workers 1 --no-access-log
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/mahan-license
MemoryMax=256M
[Install]
WantedBy=multi-user.target
''')
subprocess.run(['systemctl','daemon-reload'],check=True,capture_output=True)
subprocess.run(['systemctl','enable','mahan-license.service'],check=True,capture_output=True)
subprocess.run(['systemctl','restart','mahan-license.service'],check=True,capture_output=True)
healthy=False
for _ in range(50):
 try:
  with urllib.request.urlopen('http://127.0.0.1:8012/api/licensing/v1/health',timeout=1) as r:
   healthy=json.load(r).get('product')=='mahan-marriage-bot'
  if healthy:break
 except Exception:time.sleep(.2)
if not healthy:
 subprocess.run(['systemctl','stop','mahan-license.service'],capture_output=True)
 raise RuntimeError('New license service did not become healthy; it was stopped and nginx was not changed')
limits=Path('/etc/nginx/conf.d/mahan-license-limits.conf')
limits.write_text('limit_req_zone $binary_remote_addr zone=mahan_license_api:10m rate=30r/s;\n')
location='''
    location ^~ /api/licensing/ {
        limit_req zone=mahan_license_api burst=60 nodelay;
        limit_req_status 429;
        client_max_body_size 16k;
        proxy_pass http://127.0.0.1:8012;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_connect_timeout 3s;
        proxy_read_timeout 10s;
    }
'''
if 'location ^~ /api/licensing/' not in original:
 nginx.write_text(original.replace('server_name otp.mahanvip.ir;','server_name otp.mahanvip.ir;'+location,1))
test=subprocess.run(['nginx','-t'],capture_output=True)
if test.returncode:
 nginx.write_text(original)
 old_limits=backup/limits.name
 if old_limits.exists():shutil.copy2(old_limits,limits)
 else:limits.unlink()
 raise RuntimeError('nginx validation failed; original configuration restored')
reload=subprocess.run(['systemctl','reload','nginx'],capture_output=True)
if reload.returncode:
 nginx.write_text(original)
 subprocess.run(['systemctl','reload','nginx'],capture_output=True)
 raise RuntimeError('nginx reload failed; original configuration restored')
print(json.dumps({'admin_token':token,'backup':str(backup),'service':'mahan-license','healthy':healthy}))
"""


def main():
    task_id=uuid.uuid4().hex
    remote_zip='/tmp/mahan-license-'+task_id+'.zip'
    with tempfile.TemporaryDirectory(prefix='mahan-license-deploy-') as folder:
        bundle=Path(folder)/'license-server.zip'
        with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as archive:
            for name in ('__init__.py','models.py','store.py','app.py'):
                archive.write(ROOT/'license_server'/name,'license_server/'+name)
        subprocess.run(['scp','-q','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',str(bundle),HOST+':'+remote_zip],check=True,timeout=40)
        script='UPLOAD='+repr(remote_zip)+'\n'+REMOTE
        result=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=yes',HOST,'sudo -n python3 -'],
                              input=script,capture_output=True,text=True,timeout=240)
        if result.returncode:
            print('Deployment failed:',(result.stderr or 'remote command failed')[-1800:])
            raise SystemExit(1)
        settings=json.loads(result.stdout)
        private=ROOT/'.licenses.private.env'
        backup_pointer=ROOT.parent/'mahanbot_license_backup_path.txt'
        if private.exists() and backup_pointer.exists():
            old=Path(backup_pointer.read_text().strip())/'licenses-private-before.env'
            if not old.exists():old.write_bytes(private.read_bytes())
        private.write_text('MAHANBOT_LICENSE_ADMIN_TOKEN='+settings['admin_token']+'\n'
                           'MAHANBOT_LICENSE_SERVER=https://otp.mahanvip.ir/api/licensing/v1\n',encoding='utf-8')
        try:private.chmod(0o600)
        except OSError:pass
        public={key:value for key,value in settings.items() if key!='admin_token'}
        public['manager_config']='.licenses.private.env'
        if backup_pointer.exists():
            (Path(backup_pointer.read_text().strip())/'server-deployment.json').write_text(json.dumps(public,indent=2),encoding='utf-8')
        print(json.dumps(public,ensure_ascii=False))


if __name__=='__main__':
    main()
