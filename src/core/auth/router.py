# -*- coding: utf-8 -*-

"""
认证服务 FastAPI 路由

路由路径从 configure/instruments.json 的 auth_service.api_routes 读取。
"""

import json
import os
from fastapi import APIRouter

# ============== 读取路由配置 ==============

_CONFIG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'configure', 'instruments.json')
)

with open(_CONFIG_PATH, encoding='utf-8') as _f:
    _auth_routes = json.load(_f)['auth_service']['api_routes']

_HEALTH_PATH = _auth_routes['health']   # "/health"

# ============== 路由定义 ==============

router = APIRouter()


@router.get(_HEALTH_PATH)
def health_check():
    """服务健康检查"""
    return {'status': 'ok'}
