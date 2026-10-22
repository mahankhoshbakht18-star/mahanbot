"""Run with: uvicorn license_server.app:create_app --factory --host 127.0.0.1 --port 8012."""
import hmac
import os
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Path
from fastapi.responses import JSONResponse
from .store import PRODUCT, LicenseError, LicenseStore
from .models import Issue, DeviceIdentity, Activate, Renew, ChangeState

PREFIX = '/api/licensing/v1'
LicenseId = Annotated[str, Path(pattern=r'^[a-f0-9]{32}$')]


def create_app(store=None, admin_token=None):
    token = admin_token if admin_token is not None else os.environ.get('MAHAN_LICENSE_ADMIN_TOKEN', '')
    if len(token) < 32:
        raise RuntimeError('MAHAN_LICENSE_ADMIN_TOKEN must be configured with at least 32 characters')
    store = store or LicenseStore(os.environ.get('MAHAN_LICENSE_DB', '/var/lib/mahan-license/licenses.db'))
    app = FastAPI(title='Mahan Marriage Bot License Authority', docs_url=None, redoc_url=None, openapi_url=None)

    def admin(authorization: Annotated[str | None, Header()] = None):
        supplied = authorization.removeprefix('Bearer ') if authorization else ''
        if not supplied or not hmac.compare_digest(supplied.encode('utf-8'), token.encode('utf-8')):
            raise HTTPException(401, 'دسترسی مدیریت معتبر نیست.')

    @app.middleware('http')
    async def no_cache(request: Request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.exception_handler(LicenseError)
    async def licensing_error(request: Request, exc: LicenseError):
        return JSONResponse({'state': exc.state, 'detail': exc.message, 'product': PRODUCT, 'valid': False}, status_code=exc.status)

    @app.get(PREFIX + '/health')
    def health():
        return {'status': 'ok', 'product': PRODUCT, 'version': 1}

    @app.post(PREFIX + '/activate')
    def activate(req: Activate):
        return store.activate(req.license_key.strip(), req.installation_id, req.machine_hash)

    @app.post(PREFIX + '/verify')
    def verify(req: DeviceIdentity, authorization: Annotated[str | None, Header()] = None):
        if not authorization or not authorization.startswith('Bearer '):
            raise HTTPException(401, 'دستگاه فعال نیست.')
        return store.verify(authorization[7:], req.installation_id, req.machine_hash)

    @app.get(PREFIX + '/admin/licenses', dependencies=[Depends(admin)])
    def licenses():
        return store.list_licenses()

    @app.post(PREFIX + '/admin/licenses', dependencies=[Depends(admin)])
    def issue(req: Issue):
        if not req.customer.strip():
            raise HTTPException(422, 'نام مشتری لازم است.')
        return store.issue(req.customer.strip(), req.days, req.max_devices)

    @app.post(PREFIX + '/admin/licenses/{license_id}/renew', dependencies=[Depends(admin)])
    def renew(license_id: LicenseId, req: Renew):
        return store.change(license_id, days=req.days)

    @app.put(PREFIX + '/admin/licenses/{license_id}/state', dependencies=[Depends(admin)])
    def change(license_id: LicenseId, req: ChangeState):
        return store.change(license_id, active=req.active)

    @app.get(PREFIX + '/admin/licenses/{license_id}/devices', dependencies=[Depends(admin)])
    def devices(license_id: LicenseId):
        return store.devices(license_id)

    @app.delete(PREFIX + '/admin/licenses/{license_id}/devices/{device_id}', dependencies=[Depends(admin)])
    def release(license_id: LicenseId, device_id: LicenseId):
        store.release_device(license_id, device_id)
        return {'status': 'ok'}

    return app
