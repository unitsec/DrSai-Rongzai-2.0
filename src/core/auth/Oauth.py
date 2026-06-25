# -*- coding: utf-8 -*-
"""
统一身份认证 (SSO/OAuth2) 模块（FastAPI 版）

/umt 路由不再本地实现 OAuth 流程，而是转发到 drsai 子应用：
  /umt/login    → /drsai/umt/login
  /umt/callback → /drsai/umt/callback

路由:
  GET /sso/status
  GET /umt/login
  GET /umt/callback
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

sso_router = APIRouter(prefix='/sso')
umt_router = APIRouter(prefix='/umt')


# ============== /sso 路由 ==============

@sso_router.get('/status')
async def sso_status(request: Request):
    username = request.session.get('username')
    provider = request.session.get('sso_provider')
    if not username:
        return {'success': True, 'logged_in': False}
    return {
        'success': True,
        'logged_in': True,
        'username': username,
        'provider': provider or 'local',
    }


# ============== /umt 路由（转发到 drsai） ==============

def _forward_to_drsai(request: Request, target_path: str) -> RedirectResponse:
    query = request.url.query
    target = f'{target_path}?{query}' if query else target_path
    logger.info(f'转发到 drsai: {target}')
    return RedirectResponse(url=target)


@umt_router.get('/login')
async def umt_login(request: Request):
    return _forward_to_drsai(request, '/drsai/umt/login')


@umt_router.get('/callback')
async def umt_callback(request: Request):
    return _forward_to_drsai(request, '/drsai/umt/callback')
