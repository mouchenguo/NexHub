"""API 请求模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class RegisterIn(BaseModel):
    username: str = Field(
        min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$"
    )
    password: str = Field(min_length=6, max_length=128)
    display_name: str = Field(default="", max_length=32)

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: str) -> str:
        return value.strip()


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class ShareFileIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    size: int = Field(ge=0)


class ShareCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=500)
    storage_type: Literal["oss", "temp"] = "temp"
    files: list[ShareFileIn] = Field(min_length=1)
    total_size: int = Field(default=0, ge=0)
    file_count: int = Field(default=0, ge=0)
    local_url: str = Field(default="", max_length=512)


class DeviceHeartbeatIn(BaseModel):
    device_id: str = Field(min_length=8, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    tunnel_address: str = Field(default="", max_length=512)


class NpsTunnelCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    local_host: str = Field(min_length=1, max_length=128)
    local_port: int = Field(ge=1, le=65535)
    rdp_task: bool = False

    @field_validator("name", "local_host")
    @classmethod
    def strip_values(cls, value: str) -> str:
        return value.strip()


class NpsTunnelUpdateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    local_host: str = Field(min_length=1, max_length=128)
    local_port: int = Field(ge=1, le=65535)

    @field_validator("name", "local_host")
    @classmethod
    def strip_values(cls, value: str) -> str:
        return value.strip()
