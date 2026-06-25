# -*- coding: utf-8 -*-

"""
认证 API 模块（FastAPI 版）

路由:
  POST /api/auth/login
  POST /api/auth/logout
  GET  /api/auth/me
  GET  /api/auth/permissions
  POST /api/auth/permissions/refresh
  POST /api/auth/check-permission
  POST /api/auth/lookup_proposal
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .permission_provider import get_permission_provider, PermissionProviderError

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/auth')


# ============== 登录 ==============

@router.post('/login')
async def api_login(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}

    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    # role = data.get('role', 'user').strip()

    if not username:
        return JSONResponse({'success': False, 'error': '用户名不能为空'}, status_code=400)

    try:
        request.session['username']     = username
        request.session['user_id']      = username
        request.session['role']         = 'user'
        request.session['_pw']          = password
        request.session['sso_token']    = ''
        request.session['sso_provider'] = ''
        request.session['sso_email']    = ''

        permissions_dict = None
        permissions_warning = None

        try:
            provider = get_permission_provider(login_type='local')
            perms = provider.get_user_permissions(username, password, role='scientist')
            determined_role = 'scientist' if perms.beamlines else 'user'
            perms.role = determined_role
            request.session['role'] = determined_role
            permissions_dict = perms.to_dict()
            request.session['permissions'] = {
                'username':  permissions_dict['username'],
                'role':      permissions_dict['role'],
                'beamlines': permissions_dict['beamlines'],
                'proposals': [],
                'provider':  permissions_dict['provider'],
            }
            logger.info(
                f'登录成功: {username}，自动判定 role={determined_role}，'
                f'谱仪: {perms.beamlines}，课题数: {len(perms.proposals)}，来源: {perms.provider}'
            )
        except PermissionProviderError as ex:
            permissions_warning = str(ex)
            logger.warning(f'ICAT 权限查询失败 [{username}]: {ex}（仍允许登录）')
        except Exception as ex:
            permissions_warning = '权限服务暂时不可用，权限信息未加载'
            logger.error(f'权限查询异常 [{username}]: {ex}', exc_info=True)

        resp = {
            'success': True,
            'user': {'id': username, 'name': username, 'isAuthenticated': True},
        }
        if permissions_dict is not None:
            resp['permissions'] = permissions_dict
        if permissions_warning:
            resp['permissions_warning'] = permissions_warning

        return resp

    except Exception as ex:
        logger.error(f'登录异常: {ex}', exc_info=True)
        return JSONResponse({'success': False, 'error': '登录失败，请重试'}, status_code=500)


# ============== 登出 ==============

@router.post('/logout')
async def api_logout(request: Request):
    try:
        request.session.clear()
        logger.info('用户登出')
        return {'success': True, 'message': '登出成功'}
    except Exception as ex:
        logger.error(f'登出异常: {ex}')
        return JSONResponse({'success': False, 'error': '登出失败'}, status_code=500)


# ============== 获取当前用户信息 ==============

def _extract_jwt(request: Request) -> Optional[str]:
    """从 Authorization Bearer 或 refresh-token cookie 取 JWT。"""
    auth = request.headers.get('authorization', '')
    if auth.lower().startswith('bearer '):
        token = auth.split(' ', 1)[1].strip()
        if token:
            return token
    return request.cookies.get('refresh-token') or None


def _verify_user_in_drsai_db(token: str) -> Optional[str]:
    """解码 JWT 并在 drsai Userinfo 表中查询；存在返回 user_id，否则 None。"""
    try:
        from drsai_ui.drsai_adapter.sso.jwt import decode_jwt_token
        from drsai_ui.ui_backend.backend.web import deps as drsai_deps
        from drsai_ui.ui_backend.backend.datamodel.db import Userinfo
    except ImportError as ex:
        logger.error(f'drsai 模块未就绪: {ex}')
        return None

    try:
        data = decode_jwt_token(token)
    except Exception as ex:
        logger.info(f'JWT 校验失败: {ex}')
        return None

    user_id = getattr(data, 'user_id', None)
    if not user_id:
        return None

    db = drsai_deps._db_manager
    if db is None:
        logger.error('drsai DatabaseManager 未初始化')
        return None

    try:
        resp = db.get(Userinfo, filters={'user_id': user_id}, return_json=False)
    except Exception as ex:
        logger.error(f'查询 Userinfo 失败 [{user_id}]: {ex}')
        return None

    if not (resp.status and resp.data):
        logger.warning(f'Userinfo 表中无用户: {user_id}')
        return None
    return user_id


def _extract_sso_token(request: Request) -> Optional[str]:
    """从 session 或 sso-token cookie 取 IHEP access_token。"""
    return request.session.get('sso_token') or request.cookies.get('sso-token') or None


@router.get('/me')
async def api_get_current_user(request: Request):
    try:
        # 1) 本地登录：rongzai session 已写入
        username = request.session.get('username')
        if username:
            return {
                'success': True,
                'user': {'id': username, 'name': username, 'isAuthenticated': True},
            }

        # 2) SSO 登录：从 JWT 解出 user_id，并在 drsai Userinfo 表中校验
        token = _extract_jwt(request)
        if token:
            user_id = _verify_user_in_drsai_db(token)
            if user_id:
                # 回写父 session，后续请求走 session 即可，不必再次解码 JWT
                request.session['username']     = user_id
                request.session['user_id']      = user_id
                request.session['role']         = 'user'
                request.session['_pw']          = ''
                request.session['sso_token']    = request.cookies.get('sso-token', '')
                request.session['sso_provider'] = 'umt'
                request.session['sso_email']    = user_id
                logger.info(f'SSO 用户通过 JWT + 数据库校验: {user_id}')
                return {
                    'success': True,
                    'user': {'id': user_id, 'name': user_id, 'isAuthenticated': True},
                }

        return JSONResponse({'success': False, 'error': '未登录'}, status_code=401)
    except Exception as ex:
        logger.error(f'获取用户信息异常: {ex}', exc_info=True)
        return JSONResponse({'success': False, 'error': '获取用户信息失败'}, status_code=500)


# ============== 查询权限信息 ==============

@router.get('/permissions')
async def api_get_permissions(request: Request):
    try:
        username = request.session.get('username')
        if not username:
            return JSONResponse({'success': False, 'error': '未登录'}, status_code=401)

        role = request.session.get('role', 'user')
        sso_provider = request.session.get('sso_provider', '')

        if sso_provider == 'umt':
            # credential = request.session.get('sso_token', '')
            # credential = _extract_jwt(request)
            credential = _extract_sso_token(request)
            login_type = 'sso'
        else:
            credential = request.session.get('_pw', '')
            login_type = 'local'

        if credential:
            try:
                if sso_provider == 'umt':
                    sso_email = request.session.get('sso_email', username)
                    logger.info(f'SSO 权限查询 session: username={username!r}, sso_email={sso_email!r}')
                    provider = get_permission_provider(login_type=login_type)
                    query_identity = sso_email if provider.provider_name == 'domas' else f'{username.split('@')[0]} {sso_email}'
                else:
                    provider = get_permission_provider(login_type=login_type)
                    query_identity = username
                perms = provider.get_user_permissions(query_identity, credential, role=role)
                permissions_dict = perms.to_dict()
                request.session['permissions'] = {
                    'username':  permissions_dict['username'],
                    'role':      permissions_dict['role'],
                    'beamlines': permissions_dict['beamlines'],
                    'proposals': [],
                    'provider':  permissions_dict['provider'],
                }
                logger.info(
                    f'权限查询完成: {username}，role={permissions_dict["role"]}，'
                    f'谱仪: {perms.beamlines}，课题数: {len(perms.proposals)}，来源: {perms.provider}'
                )
                return {'success': True, 'permissions': permissions_dict}
            except PermissionProviderError as ex:
                logger.warning(f'实时重查权限失败 [{username}]: {ex}')
            except Exception as ex:
                logger.error(f'实时重查权限异常 [{username}]: {ex}', exc_info=True)

        cached = request.session.get('permissions')
        if cached:
            return {'success': True, 'permissions': cached, 'from_cache': True}

        return {
            'success': True,
            'permissions': {
                'username': username, 'provider': 'none',
                'beamlines': [], 'proposals': [], 'role': role,
            },
            'from_cache': False,
            'message': '权限信息未缓存，请重新登录以加载完整权限',
        }

    except Exception as ex:
        logger.error(f'查询权限异常: {ex}', exc_info=True)
        return JSONResponse({'success': False, 'error': '查询权限失败'}, status_code=500)


# ============== 刷新权限 ==============

@router.post('/permissions/refresh')
async def api_refresh_permissions(request: Request):
    try:
        username = request.session.get('username')
        if not username:
            return JSONResponse({'success': False, 'error': '未登录'}, status_code=401)

        try:
            data = await request.json()
        except Exception:
            data = {}
        password = data.get('password', '').strip()
        role = data.get('role', request.session.get('role', 'user'))
        if not password:
            return JSONResponse({'success': False, 'error': '请提供密码以刷新权限'}, status_code=400)

        try:
            provider = get_permission_provider()
            perms = provider.get_user_permissions(username, password, role=role)
            permissions_dict = perms.to_dict()
            request.session['permissions'] = permissions_dict
            logger.info(f'权限缓存已刷新: {username}')
            return {'success': True, 'permissions': permissions_dict}
        except PermissionProviderError as ex:
            return JSONResponse({'success': False, 'error': str(ex)}, status_code=403)

    except Exception as ex:
        logger.error(f'刷新权限异常: {ex}', exc_info=True)
        return JSONResponse({'success': False, 'error': '刷新权限失败'}, status_code=500)


# ============== 权限检查 ==============

@router.post('/check-permission')
async def api_check_permission(request: Request):
    username = request.session.get('username')
    if not username:
        return JSONResponse({'success': False, 'error': '未登录'}, status_code=401)

    try:
        data = await request.json()
    except Exception:
        data = {}
    beamline = data.get('beamline', '').strip().upper()

    if not beamline:
        return {'success': True, 'permitted': True, 'message': '已登录'}

    cached = request.session.get('permissions', {})
    beamlines = [b.upper() for b in cached.get('beamlines', [])]

    if beamlines:
        permitted = beamline in beamlines
        return {
            'success': True,
            'permitted': permitted,
            'message': f'有权访问 {beamline}' if permitted else f'无权访问 {beamline}',
        }

    logger.warning(f'用户 {username} 无权限缓存，检查 {beamline} 降级放行')
    return {'success': True, 'permitted': True, 'message': '权限缓存未加载，已降级放行'}


# ============== 科学家身份验证 ==============

@router.post('/is_scientist')
async def api_is_scientist(request: Request):
    """验证当前用户是否为谱仪科学家，通过后更新 session role 为 scientist。"""
    username = request.session.get('username')
    if not username:
        return JSONResponse({'success': False, 'error': '未登录'}, status_code=401)

    sso_token = request.session.get('sso_token', '')
    password  = request.session.get('_pw', '')
    cached    = request.session.get('permissions', {})
    beamlines = [b.upper() for b in cached.get('beamlines', [])]

    try:
        if sso_token:
            # SSO 用户：用 isAdmin=True 重新查 DoMAS，不信任缓存
            # 缓存是用 role=user 查的，scientist 身份需要 isAdmin=True 才能返回谱仪
            sso_email = request.session.get('sso_email', username)
            try:
                provider = get_permission_provider(login_type='sso')
                perms = provider.get_user_permissions(sso_email, sso_token, role='scientist')
                sci_beamlines = perms.beamlines
            except Exception as ex:
                logger.warning(f'is_scientist DoMAS 重查失败 [{username}]: {ex}，降级使用缓存')
                sci_beamlines = beamlines
            is_sci = len(sci_beamlines) > 0
            if is_sci:
                # 更新缓存里的 beamlines（用 scientist 视角的结果覆盖）
                if request.session.get('permissions') is None:
                    request.session['permissions'] = {}
                request.session['permissions']['beamlines'] = sci_beamlines
        else:
            # 本地 ICAT 用户：逐谱仪调用 isInstrumentScientist
            icat_uri = os.getenv('ICAT_URI', '').strip()
            if not icat_uri or not password or not beamlines:
                return {'success': True, 'is_scientist': False, 'message': 'ICAT 未配置或无谱仪权限记录'}
            from .icat.icat_helper import IcatHelper, PermissionChecker
            helper = IcatHelper(icat_uri, cert=False)
            icat_session = helper.loginIcat(username, password)
            try:
                is_sci = any(
                    PermissionChecker.isInstrumentScientist(icat_session, bl)
                    for bl in beamlines
                )
            finally:
                helper.logoutIcat(icat_session)

        if is_sci:
            request.session['role'] = 'scientist'
            if request.session.get('permissions'):
                request.session['permissions']['role'] = 'scientist'

        logger.info(f'is_scientist [{username}]: {is_sci}')
        return {'success': True, 'is_scientist': is_sci}

    except Exception as ex:
        logger.error(f'is_scientist 异常 [{username}]: {ex}', exc_info=True)
        return JSONResponse({'success': False, 'error': f'验证失败: {ex}'}, status_code=500)


# ============== 角色切换（降级为 user，无需验证）==============

@router.post('/switch_role')
async def api_switch_role(request: Request):
    """降级为 user 角色，不需要验证。禁止通过此接口升级为 scientist。"""
    username = request.session.get('username')
    if not username:
        return JSONResponse({'success': False, 'error': '未登录'}, status_code=401)

    try:
        data = await request.json()
    except Exception:
        data = {}
    role = data.get('role', '').strip()

    if role != 'user':
        return JSONResponse({'success': False, 'error': '此接口仅允许切换为 user 角色'}, status_code=400)

    request.session['role'] = 'user'
    if request.session.get('permissions'):
        request.session['permissions']['role'] = 'user'

    logger.info(f'switch_role [{username}] -> user')
    return {'success': True, 'role': 'user'}


# ============== scientist: run 号反查 ==============

def lookup_proposal_by_run_with_snapshot(run_number: str, beamline: str, session_snapshot: dict) -> dict:
    sso_token = session_snapshot.get('sso_token', '')
    password  = session_snapshot.get('_pw', '')

    if sso_token:
        from .domas_permission_provider import DomasPermissionProvider
        sso_email = session_snapshot.get('sso_email', session_snapshot.get('username', ''))
        result = DomasPermissionProvider().get_proposal_by_runid(sso_token, sso_email, run_number, beamline)
        if not result:
            raise ValueError(f'未找到 run {run_number} 对应的课题')
        proposal_id = str(result.get('proposalId') or '')
        return {
            'proposal_id':   proposal_id,
            'proposal_name': proposal_id,
            'title':         str(result.get('proposalname') or result.get('title') or ''),
        }

    icat_uri = os.getenv('ICAT_URI', '').strip()
    if not icat_uri:
        raise ValueError('ICAT 服务未配置（ICAT_URI 未设置）')
    username = session_snapshot.get('username', '')
    if not password:
        raise ValueError('无法获取当前用户凭证，请重新登录')
    from .icat.icat_helper import IcatHelper
    helper = IcatHelper(icat_uri, cert=False)
    icat_session = helper.loginIcat(username, password)
    try:
        result = helper.getProposalByRunName(icat_session, run_number, beamline)
    finally:
        helper.logoutIcat(icat_session)
    if not result:
        raise ValueError(f'未找到 run {run_number} 对应的课题')
    inv = result.get('Investigation', {})
    return {
        'proposal_id':   str(inv.get('id', '')),
        'proposal_name': inv.get('name', ''),
        'title':         inv.get('title', ''),
    }


@router.post('/lookup_proposal')
async def api_lookup_proposal(request: Request):
    username = request.session.get('username')
    if not username:
        return JSONResponse({'success': False, 'error': '未登录'}, status_code=401)

    role = request.session.get('role', 'user')
    if role != 'scientist':
        return JSONResponse({'success': False, 'error': '仅谱仪科学家可使用此接口'}, status_code=403)

    try:
        data = await request.json()
    except Exception:
        data = {}
    run_number = data.get('run_number', '').strip()
    beamline = data.get('beamline', '').strip()
    if not run_number:
        return JSONResponse({'success': False, 'error': '请提供 run_number'}, status_code=400)

    logger.info(f'lookup_proposal [{username}] role={role} run={run_number}')

    try:
        session_snapshot = dict(request.session)
        result = lookup_proposal_by_run_with_snapshot(run_number, beamline, session_snapshot)
        return {'success': True, 'beamline': beamline, **result}
    except ValueError as ex:
        return JSONResponse({'success': False, 'error': str(ex)}, status_code=404)
    except Exception as ex:
        logger.error(f'lookup_proposal 异常 [{username}] run={run_number}: {ex}', exc_info=True)
        return JSONResponse({'success': False, 'error': f'反查失败: {ex}'}, status_code=500)
