"""NexHub Windows 客户端。

GUI（PyQt6）与本地服务（FastAPI）分层组织：
- ``views``      视图层
- ``services``   业务逻辑 / 系统能力
- ``api_client`` ECS API 通信
- ``theme``      主题管理
- ``config``     本地 JSON 配置
"""

__version__ = "0.1.6-beta"
APP_NAME = "NexHub"
