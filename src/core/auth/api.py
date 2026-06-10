# -*- coding: utf-8 -*-

"""
认证 API 模块

功能:
  - /api/auth/login       — 登录（本地账号 + ICAT 权限查询）
  - /api/auth/logout      — 登出
  - /api/auth/me          — 获取当前用户信息
  - /api/auth/permissions — 查询当前用户的 beamline / proposal / run 权限
"""

import logging
import os
from flask import Blueprint, request, jsonify, session

from .permission_provider import get_permission_provider, PermissionProviderError

logger = logging.getLogger(__name__)

auth_api_bp = Blueprint('auth_api', __name__, url_prefix='/api/auth')


# ============== 登录 ==============

@auth_api_bp.route('/login', methods=['POST'])
def api_login():
    """
    登录流程：
      1. 通过权限提供者（当前为 ICAT）查询用户的谱仪/课题/run 权限
      2. 将权限信息写入 session，返回给前端

    若 ICAT 查询失败（如 ICAT 服务不可用），仍允许登录但权限信息为空，
    避免 ICAT 故障导致整个平台无法使用。
    """
    try:
        data = request.get_json() or {}
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        role = data.get('role', 'user').strip()  # 'user' 或 'scientist'

        if not username:
            return jsonify({'success': False, 'error': '用户名不能为空'}), 400

        # ── 第一步：写入基础 session ──
        session['username'] = username
        session['user_id'] = username
        session['role'] = role
        # 存密码用于后续刷新 proposals（不影响安全：session 本身已加密，走服务端 session store）
        session['_pw'] = password
        session.permanent = True

        # ── 第二步：向 ICAT 查询权限（本地登录强制 ICAT，失败时降级不阻断登录）──
        permissions_dict = None
        permissions_warning = None

        try:
            provider = get_permission_provider(login_type='local')
            perms = provider.get_user_permissions(username, password, role=role)
            permissions_dict = perms.to_dict()
            # session 只存谱仪列表（beamlines），课题/run 不缓存，避免超出 cookie 4KB 限制
            # scientist 课题可达数百条，user 课题少但 runs 也可能较多
            session['permissions'] = {
                'username': permissions_dict['username'],
                'role': permissions_dict['role'],
                'beamlines': permissions_dict['beamlines'],
                'proposals': [],
                'provider': permissions_dict['provider'],
            }
            logger.info(
                f'登录成功: {username}，role={role}，'
                f'谱仪: {perms.beamlines}，课题数: {len(perms.proposals)}'
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

        return jsonify(resp), 200

    except Exception as ex:
        logger.error(f'登录异常: {ex}', exc_info=True)
        return jsonify({'success': False, 'error': '登录失败，请重试'}), 500


# ============== 登出 ==============

@auth_api_bp.route('/logout', methods=['POST'])
def api_logout():
    try:
        session.clear()
        logger.info('用户登出')
        return jsonify({'success': True, 'message': '登出成功'}), 200
    except Exception as ex:
        logger.error(f'登出异常: {ex}')
        return jsonify({'success': False, 'error': '登出失败'}), 500


# ============== 获取当前用户信息 ==============

@auth_api_bp.route('/me', methods=['GET'])
def api_get_current_user():
    try:
        username = session.get('username')
        if not username:
            return jsonify({'success': False, 'error': '未登录'}), 401
        return jsonify({
            'success': True,
            'user': {'id': username, 'name': username, 'isAuthenticated': True}
        }), 200
    except Exception as ex:
        logger.error(f'获取用户信息异常: {ex}')
        return jsonify({'success': False, 'error': '获取用户信息失败'}), 500


# ============== 查询权限信息 ==============

@auth_api_bp.route('/permissions', methods=['GET'])
def api_get_permissions():
    """
    返回当前登录用户的权限信息（beamlines / proposals / runs）。

    优先从 session 缓存读取（登录时已写入）。
    若 session 中无权限缓存（如 ICAT 登录时失败），则实时重查一次。
    前端可在用户进入谱仪功能页时调用此接口做访问控制。

    响应格式：
    {
        "success": true,
        "permissions": {
            "username": "durong",
            "provider": "icat",
            "beamlines": ["BL16", "BL01"],
            "proposals": [
                {
                    "proposal_id": "12345",
                    "proposal_name": "P2023-BL16-001",
                    "title": "...",
                    "beamline": "BL16",
                    "runs": [
                        {"run_id": "67890", "run_number": "0001", "run_title": "...", "start_time": "..."},
                        ...
                    ]
                }
            ]
        }
    }
    """
    try:
        username = session.get('username')
        if not username:
            return jsonify({'success': False, 'error': '未登录'}), 401

        role = session.get('role', 'user')
        sso_provider = session.get('sso_provider', '')

        # 凭证：本地登录用 _pw（LDAP 密码），SSO 登录用 sso_token
        if sso_provider == 'umt':
            credential = session.get('sso_token', '')
            login_type = 'sso'
        else:
            credential = session.get('_pw', '')
            login_type = 'local'

        if credential:
            try:
                provider = get_permission_provider(login_type=login_type)
                perms = provider.get_user_permissions(username, credential, role=role)
                permissions_dict = perms.to_dict()
                session['permissions'] = {
                    'username': permissions_dict['username'],
                    'role': permissions_dict['role'],
                    'beamlines': permissions_dict['beamlines'],
                    'proposals': [],
                    'provider': permissions_dict['provider'],
                }
                return jsonify({'success': True, 'permissions': permissions_dict}), 200
            except PermissionProviderError as ex:
                logger.warning(f'实时重查权限失败 [{username}]: {ex}')
            except Exception as ex:
                logger.error(f'实时重查权限异常 [{username}]: {ex}', exc_info=True)

        # 无密码或查询失败，从 session 缓存读取
        cached = session.get('permissions')
        if cached:
            return jsonify({'success': True, 'permissions': cached, 'from_cache': True}), 200

        logger.info(f'用户 {username} 的权限缓存不存在，返回空权限')
        return jsonify({
            'success': True,
            'permissions': {
                'username': username,
                'provider': 'none',
                'beamlines': [],
                'proposals': [],
                'role': role,
            },
            'from_cache': False,
            'message': '权限信息未缓存，请重新登录以加载完整权限',
        }), 200

    except Exception as ex:
        logger.error(f'查询权限异常: {ex}', exc_info=True)
        return jsonify({'success': False, 'error': '查询权限失败'}), 500


@auth_api_bp.route('/permissions/refresh', methods=['POST'])
def api_refresh_permissions():
    """
    用用户提供的凭证重新查询并刷新权限缓存。
    适用于权限缓存丢失或需要强制刷新的场景。

    请求体：{"password": "..."}（不重传 username，从 session 取）
    """
    try:
        username = session.get('username')
        if not username:
            return jsonify({'success': False, 'error': '未登录'}), 401

        data = request.get_json() or {}
        password = data.get('password', '').strip()
        role = data.get('role', session.get('role', 'user'))
        if not password:
            return jsonify({'success': False, 'error': '请提供密码以刷新权限'}), 400

        try:
            provider = get_permission_provider()
            perms = provider.get_user_permissions(username, password, role=role)
            permissions_dict = perms.to_dict()
            session['permissions'] = permissions_dict
            logger.info(f'权限缓存已刷新: {username}，谱仪: {perms.beamlines}')
            return jsonify({'success': True, 'permissions': permissions_dict}), 200
        except PermissionProviderError as ex:
            return jsonify({'success': False, 'error': str(ex)}), 403

    except Exception as ex:
        logger.error(f'刷新权限异常: {ex}', exc_info=True)
        return jsonify({'success': False, 'error': '刷新权限失败'}), 500


# ============== 权限检查（旧接口，保留向后兼容）==============

@auth_api_bp.route('/check-permission', methods=['POST'])
def api_check_permission():
    """
    快速检查用户是否有指定谱仪的访问权限。

    请求体：{"beamline": "BL16"}（可选，不传则只检查是否登录）
    """
    username = session.get('username')
    if not username:
        return jsonify({'success': False, 'error': '未登录'}), 401

    data = request.get_json() or {}
    beamline = data.get('beamline', '').strip().upper()

    if not beamline:
        # 未指定谱仪，只检查登录状态
        return jsonify({'success': True, 'permitted': True, 'message': '已登录'}), 200

    # 从 session 缓存中检查
    cached = session.get('permissions', {})
    beamlines = [b.upper() for b in cached.get('beamlines', [])]

    if beamlines:
        permitted = beamline in beamlines
        return jsonify({
            'success': True,
            'permitted': permitted,
            'message': f'有权访问 {beamline}' if permitted else f'无权访问 {beamline}',
        }), 200

    # 无缓存时，降级放行（ICAT 不可用时不阻断正常使用）
    logger.warning(f'用户 {username} 无权限缓存，检查 {beamline} 降级放行')
    return jsonify({
        'success': True,
        'permitted': True,
        'message': '权限缓存未加载，已降级放行',
    }), 200


# ============== scientist：run 号反查 proposalId ==============

@auth_api_bp.route('/lookup_proposal', methods=['POST'])
def api_lookup_proposal():
    """
    科学家专用：用 run 号反查 proposalId（通过 ICAT service account）。

    请求体：{"run_number": "RUN0021905", "beamline": "BL16"}
    响应：{
        "success": true,
        "proposal_id": "12345",
        "proposal_name": "P2023-BL16-001",
        "title": "...",
        "beamline": "BL16"
    }
    """
    username = session.get('username')
    if not username:
        return jsonify({'success': False, 'error': '未登录'}), 401

    # 只有 scientist 才能反查
    role = session.get('role', 'user')
    if role != 'scientist':
        return jsonify({'success': False, 'error': '仅谱仪科学家可使用此接口'}), 403

    data = request.get_json() or {}
    run_number = data.get('run_number', '').strip()
    if not run_number:
        return jsonify({'success': False, 'error': '请提供 run_number'}), 400

    sso_provider = session.get('sso_provider', '')

    # DoMAS 分支：统一认证登录 + domas 权限提供者
    if sso_provider == 'umt' and os.getenv('PERMISSION_PROVIDER', '').lower() == 'domas':
        try:
            token = session.get('sso_token', '')
            if not token:
                return jsonify({'success': False, 'error': '无法获取 SSO token，请重新登录'}), 401

            from .domas_permission_provider import DomasPermissionProvider
            domas = DomasPermissionProvider()
            result = domas.get_proposal_by_runid(token, username, run_number)

            if not result:
                return jsonify({
                    'success': False,
                    'error': f'未找到 run {run_number} 对应的课题，请确认 run 号正确'
                }), 404

            # DoMAS msg 层字段：proposalId / startData / endData / title
            proposal_id = str(result.get('proposalId') or '')
            return jsonify({
                'success': True,
                'proposal_id': proposal_id,
                'proposal_name': proposal_id,
                'title': str(result.get('title') or ''),
                'beamline': '',  # DoMAS 此接口不返回 beamline
            }), 200

        except Exception as ex:
            logger.error(f'DoMAS lookup_proposal 异常 [{username}] run={run_number}: {ex}', exc_info=True)
            return jsonify({'success': False, 'error': f'反查失败: {ex}'}), 500

    # ICAT 分支：本地登录或统一认证 + icat 权限提供者
    try:
        icat_uri = os.getenv('ICAT_URI', '').strip()
        if not icat_uri:
            return jsonify({'success': False, 'error': 'ICAT 服务未配置（ICAT_URI 未设置）'}), 503

        password = session.get('_pw', '')
        if not password:
            return jsonify({'success': False, 'error': '无法获取当前用户凭证，请重新登录'}), 401

        from .icat.icat_helper import IcatHelper
        helper = IcatHelper(icat_uri, cert=False)
        icat_session = helper.loginIcat(username, password)
        try:
            result = helper.getProposalByRunName(icat_session, run_number)
        finally:
            helper.logoutIcat(icat_session)

        if not result:
            return jsonify({
                'success': False,
                'error': f'未找到 run {run_number} 对应的课题，请确认 run 号正确'
            }), 404

        inv = result.get('Investigation', {})
        return jsonify({
            'success': True,
            'proposal_id': str(inv.get('id', '')),
            'proposal_name': inv.get('name', ''),
            'title': inv.get('title', ''),
            'beamline': inv.get('instrument', ''),
        }), 200

    except Exception as ex:
        logger.error(f'lookup_proposal 异常 [{username}] run={run_number}: {ex}', exc_info=True)
        return jsonify({'success': False, 'error': f'反查失败: {ex}'}), 500
