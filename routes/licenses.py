"""License activation and administrator proxy. The authority secret never reaches JavaScript."""
from fastapi import APIRouter, Depends, HTTPException, Path
from typing import Annotated
from pydantic import BaseModel, Field

from core.deps import _admin_user
from core.licensing import (LicenseServiceError, activate_license, check_license, deactivate_local, manager_request)
from license_server.models import Issue, Renew, ChangeState

router = APIRouter(prefix='/api/license', tags=['Licensing'])
LicenseId = Annotated[str, Path(pattern=r'^[a-f0-9]{32}$')]


class Activation(BaseModel):
    license_key: str = Field(min_length=10, max_length=160)


def call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except LicenseServiceError as exc:
        raise HTTPException(exc.status, {'state': exc.state, 'message': exc.message}) from None


@router.get('/status')
def status(force: bool = False):
    return call(check_license, force)


@router.post('/activate')
def activate(req: Activation):
    return call(activate_license, req.license_key)


@router.post('/deactivate')
def deactivate():
    deactivate_local()
    return {'status': 'ok'}


@router.get('/admin/licenses', dependencies=[Depends(_admin_user)])
def licenses():
    return call(manager_request, 'GET', '')


@router.post('/admin/licenses', dependencies=[Depends(_admin_user)])
def issue(req: Issue):
    return call(manager_request, 'POST', '', req.model_dump())


@router.post('/admin/licenses/{license_id}/renew', dependencies=[Depends(_admin_user)])
def renew(license_id: LicenseId, req: Renew):
    return call(manager_request, 'POST', f'/{license_id}/renew', req.model_dump())


@router.put('/admin/licenses/{license_id}/state', dependencies=[Depends(_admin_user)])
def state(license_id: LicenseId, req: ChangeState):
    return call(manager_request, 'PUT', f'/{license_id}/state', req.model_dump())


@router.get('/admin/licenses/{license_id}/devices', dependencies=[Depends(_admin_user)])
def devices(license_id: LicenseId):
    return call(manager_request, 'GET', f'/{license_id}/devices')


@router.delete('/admin/licenses/{license_id}/devices/{device_id}', dependencies=[Depends(_admin_user)])
def release(license_id: LicenseId, device_id: LicenseId):
    return call(manager_request, 'DELETE', f'/{license_id}/devices/{device_id}')
