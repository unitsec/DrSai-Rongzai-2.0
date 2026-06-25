# -*- coding: utf-8 -*-

"""
认证服务 - FastAPI 应用

端口: 5024
功能:
  - /api/auth/login — 本地账号登录
  - /api/auth/logout — 登出
  - /api/auth/me — 获取当前用户信息
  - /api/config/instruments — 前端主题配置
"""
import os
import logging
from datetime import timedelta

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.wsgi import WSGIMiddleware
from flask import Flask
from flask_cors import CORS as FlaskCORS

# ============== 日志配置 ==============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 加载项目根目录的 .env 文件
try:
    from dotenv import load_dotenv
except ImportError:
    logger.error('缺少依赖 python-dotenv，请执行 pip install python-dotenv')
    raise

_env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))
if not os.path.isfile(_env_path):
    logger.error(f'.env 文件不存在: {_env_path}')
    raise FileNotFoundError(f'.env 文件不存在: {_env_path}')
load_dotenv(_env_path)

# ============== 导入 Blueprint ==============
try:
    from .router import router
    from .api import auth_api_bp
    from .Oauth import sso_bp, sso_umt_bp
    from ..config_api import config_api_bp
except ImportError:
    import sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from backend.auth.router import router
    from backend.auth.api import auth_api_bp
    from backend.auth.Oauth import sso_bp, sso_umt_bp
    from backend.config_api import config_api_bp


# ============== Flask 子应用（承载现有 Blueprint 路由）==============

def _create_flask_sub_app() -> Flask:
    """创建内部 Flask 子应用，挂载所有 Blueprint 路由"""
    flask_app = Flask(__name__)
    flask_app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-rongzai')
    flask_app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
    flask_app.config['SESSION_COOKIE_SECURE'] = False
    flask_app.config['SESSION_COOKIE_HTTPONLY'] = True
    flask_app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    FlaskCORS(flask_app, supports_credentials=True)
    flask_app.register_blueprint(auth_api_bp)
    flask_app.register_blueprint(sso_bp)
    flask_app.register_blueprint(sso_umt_bp)
    flask_app.register_blueprint(config_api_bp)
    return flask_app


# ============== FastAPI 应用工厂 ==============

def create_app() -> FastAPI:
    app = FastAPI(title='认证服务', docs_url=None, redoc_url=None)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=['*'],
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )

    app.include_router(router)

    # 将 Flask 子应用挂载到根路径，兼容现有 Blueprint 路由
    flask_sub = _create_flask_sub_app()
    app.mount('/', WSGIMiddleware(flask_sub))

    logger.info('认证服务已启动')
    return app


if __name__ == '__main__':
    try:
        from backend.get_config import get_auth_service_port
        port = get_auth_service_port(env_var='AUTH_SERVICE_PORT', default_port=5024)
    except Exception:
        port = int(os.getenv('AUTH_SERVICE_PORT', '5024'))

    logger.info(f'=== 认证服务启动，监听 http://0.0.0.0:{port} ===')
    uvicorn.run(
        create_app(),
        host='0.0.0.0',
        port=port,
        log_level='info',
    )
