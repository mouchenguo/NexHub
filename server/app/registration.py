"""注册审核策略。

默认开启自动审核：格式校验通过的新账号立即标记为 ``approved``，登录后即可使用。
管理员关闭自动审核后，新账号进入 ``pending``，需要在管理后台手动通过或拒绝。
审核策略保存在 ``settings`` 表中，因此无需重启服务即可生效。
"""
from __future__ import annotations

from . import db
from .config import settings

AUTO_APPROVE_KEY = "registration_auto_approve"

STATUS_APPROVED = "approved"
STATUS_PENDING = "pending"
STATUS_REJECTED = "rejected"

STATUS_LABELS = {
    STATUS_APPROVED: "已通过",
    STATUS_PENDING: "待审核",
    STATUS_REJECTED: "已拒绝",
}

_TRUE_VALUES = {"1", "true", "yes", "on"}


def auto_approve_enabled() -> bool:
    """读取运行期自动审核开关，未设置时使用环境变量默认值。"""
    default = "1" if settings.registration_auto_approve else "0"
    raw = db.get_setting(AUTO_APPROVE_KEY, default)
    return raw.strip().lower() in _TRUE_VALUES


def set_auto_approve(enabled: bool) -> None:
    db.set_setting(AUTO_APPROVE_KEY, "1" if enabled else "0")


def evaluate_registration(username: str, display_name: str = "") -> tuple[str, str]:
    """自动审核新注册账号，返回 ``(状态, 审核说明)``。"""
    del username, display_name  # 保留参数，便于后续扩展风控规则
    if auto_approve_enabled():
        return STATUS_APPROVED, "自动审核通过"
    return STATUS_PENDING, "等待管理员审核"


def status_error(status: str) -> str:
    if status == STATUS_PENDING:
        return "账号正在等待管理员审核，暂时无法登录"
    if status == STATUS_REJECTED:
        return "账号未通过管理员审核，请联系管理员"
    return "账号审核状态异常，请联系管理员"
