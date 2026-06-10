# -*- coding: utf-8 -*-

"""
认证服务 - 独立 Flask 应用

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
from flask import Flask
from flask_cors import CORS

# 自动加载项目根目录的 .env 文件（python-dotenv 不存在时跳过）
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
    load_dotenv(_env_path)
except ImportError:
    pass

# ============== 导入 Blueprint ==============
try:
    from .api import auth_api_bp
    from .Oauth import sso_bp, sso_umt_bp
    from ..config_api import config_api_bp
except ImportError:
    import sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from backend.auth.api import auth_api_bp
    from backend.auth.Oauth import sso_bp, sso_umt_bp
    from backend.config_api import config_api_bp

# ============== 日志配置 ==============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)

    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
    app.config['SESSION_COOKIE_SECURE'] = False
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    CORS(app, supports_credentials=True)

    app.register_blueprint(auth_api_bp)
    app.register_blueprint(sso_bp)
    app.register_blueprint(sso_umt_bp)
    app.register_blueprint(config_api_bp)

    @app.route('/health', methods=['GET'])
    def health_check():
        return {'status': 'ok'}, 200

    logger.info('认证服务已启动')
    return app


if __name__ == '__main__':
    app = create_app()

    try:
        from backend.get_config import get_auth_service_port
        port = get_auth_service_port(env_var='AUTH_SERVICE_PORT', default_port=5024)
    except Exception:
        port = int(os.getenv('AUTH_SERVICE_PORT', '5024'))

    logger.info(f'=== 认证服务启动，监听 http://0.0.0.0:{port} ===')
    app.run(
        host='0.0.0.0',
        port=port,
        debug=os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    )
