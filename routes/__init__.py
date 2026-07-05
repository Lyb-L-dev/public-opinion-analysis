# routes/__init__.py
from .auth_routes import auth_bp
from .dashboard_routes import dashboard_bp
from .analysis_routes import analysis_bp

__all__ = ['auth_bp', 'dashboard_bp', 'analysis_bp']