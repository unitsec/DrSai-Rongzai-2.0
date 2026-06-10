# -*- coding: utf-8 -*-
from __future__ import annotations

"""
ICAT 权限提供者

通过 ICAT 服务查询用户可访问的谱仪（beamline）、课题（proposal）和实验 run。

ICAT 数据模型映射：
  Instrument    → beamline（谱仪，如 BL16）
  Investigation → proposal（课题）
  Dataset       → run（实验 run，命名约定 __csns__<run_number>...）

端口和 URI 通过环境变量 ICAT_URI 注入，禁止在代码中硬编码。
"""

import logging
from typing import Optional

from .permission_provider import (
    PermissionProvider,
    PermissionProviderError,
    UserPermissions,
    ProposalInfo,
    RunInfo,
)
from .icat.icat_helper import IcatHelper
from .icat.icat import IcatException

logger = logging.getLogger(__name__)

# ICAT Dataset 命名前缀约定（见 icat_helper.py 中的查询条件）
_CSNS_DATASET_PREFIX = '__csns__'


class IcatPermissionProvider(PermissionProvider):
    """
    基于 ICAT 服务的权限提供者

    每次查询都向 ICAT 发起真实请求，不在本层做缓存。
    如需缓存，由调用方（api.py / session）负责。
    """

    def __init__(self, icat_uri: str):
        """
        参数:
            icat_uri: ICAT 服务根地址，如 https://data.csns.ihep.ac.cn
        """
        self._helper = IcatHelper(icat_uri, cert=False)
        logger.info(f'IcatPermissionProvider 初始化，ICAT 地址: {icat_uri}')

    @property
    def provider_name(self) -> str:
        return 'icat'

    # ============== 公开接口 ==============

    def get_user_permissions(self, username: str, password: str,
                             role: str = 'user') -> UserPermissions:
        """
        用用户凭证登录 ICAT，查询该用户可访问的谱仪、课题和 run。

        查询逻辑：
          user:      getOwnInstruments（investigationUsers）+ getOwnProposals + runs（课题少，完整加载）
          scientist: getScientistInstruments（instrumentScientists）+ getProposalsForInstrument，不加载 runs（避免超时）

        参数:
            username: 用户名（LDAP 账号）
            password: 密码
            role: 'user' 或 'scientist'

        返回:
            UserPermissions

        异常:
            PermissionProviderError: ICAT 登录失败或查询异常
        """
        icat_session = None
        try:
            try:
                icat_session = self._helper.loginIcat(username, password, plugin='ldap')
            except IcatException as ex:
                if ex.code == 'SESSION':
                    raise PermissionProviderError(
                        f'ICAT 认证失败，用户名或密码错误: {username}',
                        code='AUTH_FAILED'
                    )
                raise PermissionProviderError(
                    f'ICAT 登录异常: {ex}',
                    code=ex.code
                )

            if role == 'scientist':
                # 科学家模式：从 instrumentScientists 关系查询专属谱仪
                beamlines = self._fetch_scientist_beamlines(icat_session)
                proposals = self._fetch_proposals_for_scientist(icat_session, beamlines)
            else:
                # 用户模式：通过参与的课题关联查询谱仪
                beamlines = self._fetch_beamlines(icat_session)
                proposals = self._fetch_proposals_with_runs(icat_session)

            logger.info(f'权限查询完成 [{username}] role={role}，谱仪: {beamlines}，课题数: {len(proposals)}')
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
            logger.error(f'ICAT 权限查询未预期异常 [{username}]: {ex}', exc_info=True)
            raise PermissionProviderError(
                f'ICAT 权限查询失败: {ex}',
                code='INTERNAL'
            )
        finally:
            if icat_session is not None:
                self._helper.logoutIcat(icat_session)

    def check_beamline_access(self, username: str, password: str, beamline: str) -> bool:
        """
        快速检查用户是否有指定谱仪的访问权限。

        参数:
            username: 用户名
            password: 密码
            beamline: 谱仪名称，如 'BL16'（大小写不敏感）

        返回:
            True 表示有权限，False 表示无权限或查询失败
        """
        try:
            perms = self.get_user_permissions(username, password)
            return beamline.upper() in [b.upper() for b in perms.beamlines]
        except PermissionProviderError as ex:
            logger.warning(f'检查谱仪权限失败 [{username}/{beamline}]: {ex}')
            return False

    # ============== 私有查询方法 ==============

    def _fetch_beamlines(self, icat_session) -> list[str]:
        """用户模式：通过参与的课题关联查询可访问的谱仪列表"""
        try:
            raw = self._helper.getOwnInstruments(icat_session)
            beamlines = []
            for item in raw:
                name = item.get('Instrument', {}).get('name', '')
                if name:
                    beamlines.append(name)
            logger.debug(f'用户模式查询到谱仪: {beamlines}')
            return beamlines
        except IcatException as ex:
            logger.warning(f'查询谱仪列表失败: {ex}')
            return []

    def _fetch_scientist_beamlines(self, icat_session) -> list[str]:
        """谱仪科学家模式：通过 instrumentScientists 关系查询注册谱仪列表"""
        try:
            raw = self._helper.getScientistInstruments(icat_session)
            beamlines = []
            for item in raw:
                name = item.get('Instrument', {}).get('name', '')
                if name:
                    beamlines.append(name)
            logger.debug(f'科学家模式查询到谱仪: {beamlines}')
            return beamlines
        except IcatException as ex:
            logger.warning(f'查询科学家谱仪列表失败: {ex}')
            return []

    def _fetch_proposals_with_runs(self, icat_session) -> list[ProposalInfo]:
        """
        用户模式：查询本人参与的所有课题，并附带每个课题下的 run 列表。
        """
        proposals: list[ProposalInfo] = []
        try:
            raw_proposals = self._helper.getOwnProposals(icat_session, proposalName='', num=200)
        except IcatException as ex:
            logger.warning(f'查询课题列表失败: {ex}')
            return proposals

        for item in raw_proposals:
            proposal = self._parse_proposal(icat_session, item)
            if proposal:
                proposals.append(proposal)

        logger.debug(f'用户模式：查询到课题数: {len(proposals)}')
        return proposals

    def _fetch_proposals_for_scientist(self, icat_session,
                                       beamlines: list[str]) -> list[ProposalInfo]:
        """
        谱仪科学家模式：只拉课题基本信息（id/name/title/beamline），不查 runs。
        runs 数量可能达到几百条，逐课题查询会导致登录超时，按需加载即可。
        """
        proposals: list[ProposalInfo] = []
        seen_ids: set[str] = set()

        for beamline in beamlines:
            try:
                raw_proposals = self._helper.getProposalsForInstrument(
                    icat_session, beamlineName=beamline, proposalName='', num=500
                )
            except IcatException as ex:
                logger.warning(f'查询谱仪 {beamline} 课题列表失败: {ex}')
                continue

            for item in raw_proposals:
                proposal = self._parse_proposal_lightweight(item, beamline)
                if proposal and proposal.proposal_id not in seen_ids:
                    seen_ids.add(proposal.proposal_id)
                    proposals.append(proposal)

        logger.debug(f'谱仪科学家模式：查询到课题数: {len(proposals)}')
        return proposals

    def _parse_proposal_lightweight(self, item: dict, beamline: str) -> Optional[ProposalInfo]:
        """只解析课题基本字段，不发额外 ICAT 请求查谱仪和 runs。"""
        inv = item.get('Investigation', {})
        proposal_id = str(inv.get('id', ''))
        if not proposal_id:
            return None
        return ProposalInfo(
            proposal_id=proposal_id,
            proposal_name=inv.get('name', ''),
            title=inv.get('title', ''),
            beamline=beamline,
            runs=[],  # 科学家模式登录时不预加载 runs
        )

    def _parse_proposal(self, icat_session, item: dict) -> Optional[ProposalInfo]:
        """用户模式：解析课题并附带 runs（课题数量少，可接受）。"""
        inv = item.get('Investigation', {})
        proposal_id = str(inv.get('id', ''))
        proposal_name = inv.get('name', '')
        title = inv.get('title', '')

        if not proposal_id:
            return None

        beamline = self._fetch_beamline_for_proposal(icat_session, proposal_id)
        runs = self._fetch_runs_for_proposal(icat_session, proposal_id)

        return ProposalInfo(
            proposal_id=proposal_id,
            proposal_name=proposal_name,
            title=title,
            beamline=beamline,
            runs=runs,
        )

    def _fetch_beamline_for_proposal(self, icat_session, proposal_id: str) -> str:
        """查询课题对应的谱仪名称，失败返回空字符串"""
        try:
            raw = self._helper.getInstrumentsForProposal(icat_session, proposal_id)
            if raw:
                instrument = raw[0].get('Instrument', {})
                return instrument.get('name', '')
        except IcatException as ex:
            logger.debug(f'查询课题 {proposal_id} 对应谱仪失败: {ex}')
        return ''

    def _fetch_runs_for_proposal(self, icat_session, proposal_id: str,
                                 max_runs: int = 500) -> list[RunInfo]:
        """
        查询课题下的全部 run（Dataset，命名前缀 __csns__）。

        参数:
            icat_session: 当前 ICAT session
            proposal_id: 课题 ID
            max_runs: 最多返回条数，防止单个课题 run 数量过大

        返回:
            RunInfo 列表，按 run_number 升序排列
        """
        runs: list[RunInfo] = []
        try:
            raw_datasets = self._helper.getDatasetsForProposal(
                icat_session, proposal_id, name='', num=max_runs
            )
        except IcatException as ex:
            logger.debug(f'查询课题 {proposal_id} 的 run 失败: {ex}')
            return runs

        for item in raw_datasets:
            ds = item.get('Dataset', {})
            ds_id = str(ds.get('id', ''))
            ds_name = ds.get('name', '')

            # Dataset 名称格式：__csns__<run_number>_<description>
            if ds_name.startswith(_CSNS_DATASET_PREFIX):
                run_number = ds_name[len(_CSNS_DATASET_PREFIX):].split('_')[0]
            else:
                run_number = ds_name

            start_time = str(ds.get('startDate', ''))

            runs.append(RunInfo(
                run_id=ds_id,
                run_number=run_number,
                run_title=ds_name,
                start_time=start_time,
            ))

        runs.sort(key=lambda r: r.run_number)
        return runs


def _get_optional_str(d: dict, key: str, default: str = '') -> str:
    """安全取字典中的字符串值"""
    val = d.get(key)
    return str(val) if val is not None else default
