"""Jinja2 模板环境与过滤器。"""
from __future__ import annotations

from fastapi.templating import Jinja2Templates

from .common import format_dt, human_size
from .config import settings

templates = Jinja2Templates(directory=str(settings.templates_dir))
templates.env.filters["filesize"] = human_size
templates.env.filters["dt"] = format_dt


def render(request, name: str, context: dict | None = None, status_code: int = 200):
    payload = {"request": request, "settings": settings}
    payload.update(context or {})
    return templates.TemplateResponse(request, name, payload, status_code=status_code)
