# -*- coding: utf-8 -*-
from __future__ import annotations

"""
DoMAS 权限提供者

DoMAS（Data Management System）是 CSNS 数据管理系统，
通过 token + email 方式查询用户可访问的谱仪、课题和 run。

认证方式：SSO 登录后将 access_token 作为请求头 token 传入，
         用户身份由 email（即 UMT uid）标识。

端口和 URI 通过环境变量 DOMAS_URI 注入，禁止在代码中硬编码。
"""

import logging
import os
from typing import Optional

import requests

from .permission_provider import (
    PermissionProvider,
    PermissionProviderError,
    UserPermissions,
    ProposalInfo,
    RunInfo,
)

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 10  # 秒


class DomasPermissionProvider(PermissionProvider):
    """
    基于 DoMAS 服务的权限提供者

    使用 SSO token（access_token）代替账号密码向 DoMAS 发起查询。
    与 IcatPermissionProvider 的区别：
      - 认证凭证是 token，不是 LDAP 密码
      - token 通过请求头 'token' 传入
      - 用户身份通过 email（UMT uid）标识
    """

    def __init__(self):
        self._domas_uri = os.getenv('DOMAS_URI', '').rstrip('/')
        if self._domas_uri:
            logger.info(f'DomasPermissionProvider 初始化，DoMAS 地址: {self._domas_uri}')
        else:
            logger.warning('DomasPermissionProvider: 环境变量 DOMAS_URI 未配置')

    @property
    def provider_name(self) -> str:
        return 'domas'

    # ============== 公开接口 ==============

    def get_user_permissions(self, username: str, password: str,
                             role: str = 'user') -> UserPermissions:
        """
        用 DoMAS token 查询用户的谱仪、课题和 run 权限。

        参数:
            username: SSO 登录后的 uid（即 DoMAS email 字段）
            password: SSO access_token（在 DoMAS 场景下复用此参数传 token）
            role: 'user' 或 'scientist'

        返回:
            UserPermissions

        异常:
            PermissionProviderError: DoMAS 查询失败
        """
        token = password  # SSO 场景下 password 字段存的是 access_token
        try:
            beamlines = self._fetch_beamlines(token, username, role)
            proposals = self._fetch_proposals(token, username, beamlines, role)

            logger.info(
                f'DoMAS 权限查询完成 [{username}] role={role}，'
                f'谱仪: {beamlines}，课题数: {len(proposals)}'
            )
            return UserPermissions(
                username=username,
                beamlines=beamlines,
                proposals=proposals,
                provider=self.provider_name,
                role=role,
            )
        except PermissionProviderError:
            raise
        except Exception as ex:
            logger.error(f'DoMAS 权限查询未预期异常 [{username}]: {ex}', exc_info=True)
            raise PermissionProviderError(
                f'DoMAS 权限查询失败: {ex}',
                code='INTERNAL'
            )

    def check_beamline_access(self, username: str, password: str, beamline: str) -> bool:
        """
        快速检查用户是否有指定谱仪的访问权限。

        参数:
            username: SSO uid
            password: SSO access_token
            beamline: 谱仪名称，如 'BL16'（大小写不敏感）

        返回:
            True 表示有权限
        """
        try:
            perms = self.get_user_permissions(username, password)
            return beamline.upper() in [b.upper() for b in perms.beamlines]
        except PermissionProviderError as ex:
            logger.warning(f'检查谱仪权限失败 [{username}/{beamline}]: {ex}')
            return False

    # ============== 私有查询方法 ==============

    def _get_headers(self, token: str) -> dict:
        """构造 DoMAS 请求头"""
        return {'token': token}

    def _fetch_beamlines(self, token: str, email: str, role: str) -> list[str]:
        """
        查询用户可访问的谱仪列表。

        DoMAS 接口：GET /beamtimeid/userBeamlines
        返回格式：{"msg": {"beamlines": ["BL16", "BL01"]}, "code": 200, "success": true}
        """
        url = f'{self._domas_uri}/beamtimeid/userBeamlines'
        params = {
            'isAdmin': role == 'scientist',
            'email': email,
        }
        try:
            resp = requests.get(url, headers=self._get_headers(token),
                                params=params, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            body = resp.json()
            # 部分服务会把响应体二次序列化为 JSON 字符串，统一解包
            if isinstance(body, str):
                import json as _json
                body = _json.loads(body)
            beamlines = body.get('msg', {}).get('beamlines', [])
            logger.debug(f'DoMAS 查询谱仪列表 [{email}] role={role}: {beamlines}')
            return [str(b) for b in beamlines if b]

        except requests.HTTPError as ex:
            logger.warning(f'DoMAS 查询谱仪列表 HTTP 错误 [{email}]: {ex}')
            raise PermissionProviderError(f'DoMAS 查询谱仪失败: {ex}', code='HTTP_ERROR')
        except requests.RequestException as ex:
            logger.warning(f'DoMAS 查询谱仪列表请求异常 [{email}]: {ex}')
            return []

    def _fetch_proposals(self, token: str, email: str,
                         beamlines: list[str], role: str) -> list[ProposalInfo]:
        """
        查询用户所有谱仪下的课题列表，并附带每个课题的 run 列表。

        user 模式：逐谱仪查询课题，再查每个课题的 runs
        scientist 模式：只查课题基本信息，不预加载 runs（避免超时）
        """
        proposals: list[ProposalInfo] = []
        seen_ids: set[str] = set()

        for beamline in beamlines:
            try:
                raw_proposals = self._fetch_proposals_for_beamline(token, email, beamline, role)
            except Exception as ex:
                logger.warning(f'DoMAS 查询谱仪 {beamline} 课题失败: {ex}')
                continue

            for item in raw_proposals:
                # DoMAS 课题字段：proposalId / startDate / endDate（无 title，用 proposalId 代替）
                proposal_id = str(item.get('proposalId') or '')
                if not proposal_id or proposal_id in seen_ids:
                    continue
                seen_ids.add(proposal_id)

                runs: list[RunInfo] = []
                if role == 'user':
                    try:
                        runs = self._fetch_runs_for_proposal(token, email, proposal_id)
                    except Exception as ex:
                        logger.debug(f'DoMAS 查询课题 {proposal_id} run 列表失败: {ex}')

                proposals.append(ProposalInfo(
                    proposal_id=proposal_id,
                    proposal_name=proposal_id,           # DoMAS 无 name 字段，用 proposalId 代替
                    title=str(item.get('title') or ''),  # 部分接口有 title，有则取用
                    beamline=beamline,
                    runs=runs,
                ))

        logger.debug(f'DoMAS 查询课题总数 [{email}]: {len(proposals)}')
        return proposals

    def _fetch_proposals_for_beamline(self, token: str, email: str,
                                      beamline: str, role: str) -> list[dict]:
        """
        查询指定谱仪下的课题列表。

        DoMAS 接口：GET /beamtimeid/userBeamTimeByBeamline
        返回格式：
        {
            "msg": {
                "beamline": "BL16",
                "PageCount": 1132,
                "data": [
                    {"proposalId": "P162025070900001", "startDate": "...", "endDate": "..."},
                    ...
                ]
            }
        }
        注：data 中的元素有时也会是嵌套列表（[{...}]），统一展开处理。
        """
        url = f'{self._domas_uri}/beamtimeid/userBeamTimeByBeamline'
        params = {
            'isAdmin': role == 'scientist',
            'email': email,
            'beamline': beamline,
        }
        resp = requests.get(url, headers=self._get_headers(token),
                            params=params, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        body = resp.json()
        raw_data = body.get('msg', {}).get('data', [])

        # DoMAS 的 data 中偶尔会出现嵌套列表，展开成平铺结构
        result = []
        for item in raw_data:
            if isinstance(item, list):
                result.extend(item)
            elif isinstance(item, dict):
                result.append(item)
        return result

    def _fetch_runs_for_proposal(self, token: str, email: str,
                                 proposal_id: str) -> list[RunInfo]:
        """
        查询课题下的 run 列表。

        DoMAS 接口：GET /beamtimeid/runIdsByProposal
        参数：proposalid, email
        """
        url = f'{self._domas_uri}/beamtimeid/runIdsByProposal'
        params = {'proposalid': proposal_id, 'email': email}
        try:
            resp = requests.get(url, headers=self._get_headers(token),
                                params=params, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as ex:
            logger.debug(f'DoMAS 查询 run 列表请求异常 [proposal={proposal_id}]: {ex}')
            return []

        runs: list[RunInfo] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    # 仅 run 号字符串
                    runs.append(RunInfo(run_id=item, run_number=item))
                elif isinstance(item, dict):
                    run_id = str(item.get('runId') or item.get('id') or '')
                    run_number = str(item.get('runNumber') or item.get('runid') or run_id)
                    run_title = str(item.get('title') or item.get('runTitle') or '')
                    start_time = str(item.get('startTime') or item.get('startDate') or '')
                    if run_id:
                        runs.append(RunInfo(
                            run_id=run_id,
                            run_number=run_number,
                            run_title=run_title,
                            start_time=start_time,
                        ))
        return runs

    def get_proposal_by_runid(self, token: str, email: str,
                              run_id: str) -> Optional[dict]:
        """
        科学家专用：用 run 号反查所属课题。

        DoMAS 接口：GET /beamtimeid/adminProposalByRunid
        返回格式：{"msg": {"proposalId": "xxx", "startData": "xxx", "endData": "xxx", "title": "..."}}
        """
        url = f'{self._domas_uri}/beamtimeid/adminProposalByRunid'
        params = {'runid': run_id, 'email': email}
        try:
            resp = requests.get(url, headers=self._get_headers(token),
                                params=params, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            body = resp.json()
            return body.get('msg')  # 返回 msg 层，字段：proposalId / startData / endData / title
        except requests.RequestException as ex:
            logger.warning(f'DoMAS run 反查课题失败 [runid={run_id}]: {ex}')
            return None
