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
        except PermissionProviderError as e:
            raise e
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
            logger.info(f"body={body}")
            import json as _json
            # 部分服务会把响应体（或 msg 字段）二次序列化为 JSON 字符串，统一解包
            if isinstance(body, str):
                body = _json.loads(body)
            msg = body.get('msg', {})
            if isinstance(msg, str):
                msg = _json.loads(msg)
            beamlines = msg.get('beamlines', [])
            logger.info(f'DoMAS 查询谱仪列表 [{email}] role={role}: {beamlines}')
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

                # DoMAS 字段：proposalname 对应 ICAT 的 title
                title = str(item.get('proposalname') or item.get('title') or '')
                proposals.append(ProposalInfo(
                    proposal_id=proposal_id,
                    proposal_name=proposal_id,
                    title=title,
                    beamline=beamline,
                    runs=runs,
                ))

        logger.info(f'DoMAS 查询课题总数 [{email}]: {len(proposals)}')
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
        另外，scientist 视角下可能存在跨页课题，必须把所有 page 都拉完。
        """
        url = f'{self._domas_uri}/beamtimeid/userBeamTimeByBeamline'
        result: list[dict] = []
        page_no = 1
        page_size = 50

        while True:
            params = {
                'isAdmin': role == 'scientist',
                'email': email,
                'beamline': beamline,
                'pageNo': str(page_no),
                'pageSize': str(page_size),
            }
            try:
                resp = requests.get(url, headers=self._get_headers(token),
                                    params=params, timeout=_REQUEST_TIMEOUT)
                resp.raise_for_status()
                body = resp.json()
                if isinstance(body, str):
                    import json as _json
                    body = _json.loads(body)
                msg = body.get('msg', {})
                if isinstance(msg, str):
                    import json as _json
                    msg = _json.loads(msg)

                raw_data = msg.get('data', [])
                for item in raw_data:
                    if isinstance(item, list):
                        result.extend(item)
                    elif isinstance(item, dict):
                        result.append(item)

                page_count = msg.get('PageCount') or msg.get('pageCount') or 0
                if not page_count:
                    break

                if page_no >= int(page_count):
                    break
                page_no += 1
            except requests.RequestException as ex:
                logger.warning(f'DoMAS 查询谱仪 {beamline} 课题分页失败 [page={page_no}]: {ex}')
                break
            except ValueError as ex:
                logger.warning(f'DoMAS 查询谱仪 {beamline} 课题响应解析失败 [page={page_no}]: {ex}')
                break

        return result

    def _fetch_runs_for_proposal(self, token: str, email: str,
                                 proposal_id: str, role: str = 'user') -> list[RunInfo]:
        """
        查询课题下的 run 列表。

        DoMAS 接口：GET /beamtimeid/runIdsByProposal
        参数：proposalid, email, isAdmin
        """
        url = f'{self._domas_uri}/beamtimeid/runIdsByProposal'
        params = {'proposalId': proposal_id, 'email': email, 'isAdmin': role == 'scientist'}
        try:
            resp = requests.get(url, headers=self._get_headers(token),
                                params=params, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            logger.info(f'DoMAS runIdsByProposal [{proposal_id}] 响应: {data}')
        except requests.RequestException as ex:
            logger.debug(f'DoMAS 查询 run 列表请求异常 [proposal={proposal_id}]: {ex}')
            return []

        # DoMAS runs 接口返回 {"msg": {"runIds": [...], ...}, ...}
        if isinstance(data, dict):
            import json as _json
            msg = data.get('msg', {})
            if isinstance(msg, str):
                msg = _json.loads(msg)
            if isinstance(msg, dict):
                data = msg.get('runIds', [])
            elif isinstance(msg, list):
                data = msg
            else:
                data = []

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
                              run_id: str,beamline: str) -> Optional[dict]:
        """
        科学家专用：用 run 号反查所属课题。

        DoMAS 接口：GET /beamtimeid/adminProposalByRunid
        返回格式：{"msg": {"proposalId": "xxx", "startData": "xxx", "endData": "xxx", "title": "..."}}
        """
        url = f'{self._domas_uri}/beamtimeid/adminProposalByRunid'
        params = {'runid': run_id, 'email': email, 'beamline': beamline}
        try:
            resp = requests.get(url, headers=self._get_headers(token),
                                params=params, timeout=_REQUEST_TIMEOUT)
            logger.info(f'DoMAS adminProposalByRunid [{email}] runid={run_id} status={resp.status_code}')
            resp.raise_for_status()
            import json as _json
            body = resp.json()
            logger.info(f'DoMAS adminProposalByRunid 响应: {body}')
            msg = body.get('msg')
            if isinstance(msg, str):
                msg = _json.loads(msg)
            # adminProposalByRunid 的数据在 msg.data[0]
            if isinstance(msg, dict):
                data = msg.get('data') or []
                if data:
                    return data[0]
            return None
        except requests.RequestException as ex:
            logger.warning(f'DoMAS run 反查课题失败 [runid={run_id}]: {ex}')
            return None
        except ValueError as ex:
            # resp.json() 解析失败（空 body 或非 JSON 响应）
            logger.warning(f'DoMAS run 反查课题响应解析失败 [runid={run_id}]: {ex}')
            return None

    def check_proposal_run_access(self, token: str, email: str,
                                  proposal_id: str, run_number: str) -> bool:
        """针对单个 proposal 快速判断 scientist 是否有权访问指定 run。"""
        if not proposal_id or not run_number:
            return False
        try:
            runs = self._fetch_runs_for_proposal(token, email, proposal_id, role='scientist')
        except Exception as ex:
            logger.debug(f'DoMAS 单课题访问检查失败 [proposal={proposal_id}, run={run_number}]: {ex}')
            return False

        for run in runs:
            try:
                run_data = run.to_dict()
            except Exception:
                continue
            if str(run_data.get('run_number') or '').strip() == str(run_number).strip():
                return True
        return False
