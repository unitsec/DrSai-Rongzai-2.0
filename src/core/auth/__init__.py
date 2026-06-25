# -*- coding: utf-8 -*-

"""
    :author: Ming Tang
    :copyright: © 2020 CSNS
    :license: GPL V3, see LICENSE for more details.
"""

from flask import Blueprint

auth_bp = Blueprint("auth", __name__, template_folder="templates")

# 导出 REST API Blueprint
# from .api import auth_api_bp

# 导出权限提供者
from .permission_provider import (
    get_permission_provider,
    PermissionProvider,
    PermissionProviderError,
    UserPermissions,
    ProposalInfo,
    RunInfo,
)

__all__ = [
    'auth_bp',
    'auth_api_bp',
    'get_permission_provider',
    'PermissionProvider',
    'PermissionProviderError',
    'UserPermissions',
    'ProposalInfo',
    'RunInfo',
]
