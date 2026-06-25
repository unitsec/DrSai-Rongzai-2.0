# -*- coding: utf-8 -*-
from __future__ import annotations

"""
权限提供者抽象层

定义统一的权限查询接口，屏蔽底层实现差异。
当前支持的实现：
  - IcatPermissionProvider  — 通过 ICAT 服务查询（已上线）
  - DomasPermissionProvider — 通过 DoMAS 服务查询（开发中，占位）

切换实现方式：修改环境变量 PERMISSION_PROVIDER=icat|domas，
或在 instruments.json 的 auth_service 中添加 "permission_provider" 字段。
"""

import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


# ============== 数据结构 ==============

class RunInfo:
    """单次实验 Run 信息"""

    def __init__(self, run_id: str, run_number: str, run_title: str = '', start_time: str = ''):
        self.run_id = run_id
        self.run_number = run_number
        self.run_title = run_title
        self.start_time = start_time

    def to_dict(self) -> dict:
        return {
            'run_id': self.run_id,
            'run_number': self.run_number,
            'run_title': self.run_title,
            'start_time': self.start_time,
        }


class ProposalInfo:
    """课题（Proposal / Investigation）信息"""

    def __init__(self, proposal_id: str, proposal_name: str, title: str = '',
                 beamline: str = '', runs: Optional[list] = None):
        self.proposal_id = proposal_id
        self.proposal_name = proposal_name
        self.title = title
        self.beamline = beamline
        self.runs: list[RunInfo] = runs or []

    def to_dict(self) -> dict:
        return {
            'proposal_id': self.proposal_id,
            'proposal_name': self.proposal_name,
            'title': self.title,
            'beamline': self.beamline,
            'runs': [r.to_dict() for r in self.runs],
        }


class UserPermissions:
    """用户全部权限汇总"""

    def __init__(self, username: str, beamlines: list[str],
                 proposals: list[ProposalInfo], provider: str,
                 role: str = 'user'):
        self.username = username
        self.beamlines = beamlines        # 可访问的谱仪列表，如 ['BL16', 'BL01']
        self.proposals = proposals        # 可访问的课题列表
        self.provider = provider          # 数据来源标识，用于调试
        self.role = role                  # 登录角色：'user' 或 'scientist'

    def to_dict(self) -> dict:
        return {
            'username': self.username,
            'role': self.role,
            'beamlines': self.beamlines,
            'proposals': [p.to_dict() for p in self.proposals],
            'provider': self.provider,
        }


# ============== 抽象基类 ==============

class PermissionProvider(ABC):
    """
    权限提供者抽象基类

    所有实现必须在用户登录后提供统一的权限查询能力。
    """

    @abstractmethod
    def get_user_permissions(self, username: str, password: str,
                             role: str = 'user') -> UserPermissions:
        """
        用用户凭证查询该用户的全部权限信息。

        参数:
            username: 用户名
            password: 密码（需要向下游系统重新验证时使用）
            role: 登录角色，'user' 只查本人课题，'scientist' 查询谱仪所有课题

        返回:
            UserPermissions 对象，含 beamlines / proposals / runs

        异常:
            PermissionProviderError: 查询失败（网络错误、认证失败等）
        """

    @abstractmethod
    def check_beamline_access(self, username: str, password: str, beamline: str) -> bool:
        """
        快速检查用户是否有某条谱仪的访问权限。

        参数:
            username: 用户名
            password: 密码
            beamline: 谱仪名称，如 'BL16'

        返回:
            True 表示有权限
        """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """返回提供者名称字符串，用于日志和调试"""


class PermissionProviderError(Exception):
    """权限提供者异常基类"""

    def __init__(self, message: str, code: str = 'UNKNOWN'):
        self.message = message
        self.code = code
        super().__init__(message)

    def __str__(self) -> str:
        return f'[{self.code}] {self.message}'


# ============== 工厂函数 ==============

def get_permission_provider(login_type: str = 'local') -> PermissionProvider:
    """
    根据登录方式返回对应的权限提供者实例。

    规则：
      - login_type='local'（本地账号登录）→ 强制使用 ICAT
      - login_type='sso'（统一认证登录）  → 读环境变量 SSO_PERMISSION_PROVIDER=icat|domas

    环境变量：
      ICAT_URI                — ICAT 服务地址（local 或 sso+icat 时必填）
      DOMAS_URI               — DoMAS 服务地址（sso+domas 时必填）
      SSO_PERMISSION_PROVIDER — SSO 登录时选择的权限后端，默认 icat
    """
    if login_type == 'sso':
        provider_name = os.getenv('SSO_PERMISSION_PROVIDER', 'icat').lower()
    else:
        # 本地登录强制走 ICAT
        provider_name = 'icat'

    if provider_name == 'domas':
        from .domas_permission_provider import DomasPermissionProvider
        logger.info('使用 DoMAS 权限提供者（SSO 登录）')
        return DomasPermissionProvider()

    # ICAT：本地登录用 ldap，统一认证登录用 umt
    from .icat_permission_provider import IcatPermissionProvider
    icat_uri = os.getenv('ICAT_URI', '')
    if not icat_uri:
        raise PermissionProviderError(
            '环境变量 ICAT_URI 未配置，无法初始化 ICAT 权限提供者',
            code='CONFIG_ERROR'
        )
    icat_plugin = 'umt' if login_type == 'sso' else 'ldap'
    logger.info(f'使用 ICAT 权限提供者，服务地址: {icat_uri}，认证插件: {icat_plugin}')
    return IcatPermissionProvider(icat_uri, plugin=icat_plugin)
