from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from .errors import WeComConfigurationError


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Use .env first, then fall back to process environment variables."""

        return init_settings, dotenv_settings, env_settings, file_secret_settings

    wecom_corp_id: str = Field(
        validation_alias=AliasChoices("WECOM_CORP_ID", "CORPID"),
        description="WeCom corp ID.",
    )
    wecom_corp_secret: SecretStr = Field(
        validation_alias=AliasChoices("WECOM_CORP_SECRET", "CORPSECRET"),
        description="WeCom application secret.",
    )
    wecom_api_base: str = Field(
        default="https://qyapi.weixin.qq.com",
        validation_alias=AliasChoices("WECOM_API_BASE"),
    )
    wecom_request_timeout: float = Field(
        default=20.0,
        validation_alias=AliasChoices("WECOM_REQUEST_TIMEOUT"),
        ge=1.0,
        le=120.0,
    )
    wecom_mcp_transport: Literal["stdio", "sse", "streamable-http"] = Field(
        default="stdio",
        validation_alias=AliasChoices("WECOM_MCP_TRANSPORT"),
    )
    wecom_mcp_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("WECOM_MCP_HOST"),
    )
    wecom_mcp_port: int = Field(
        default=8000,
        validation_alias=AliasChoices("WECOM_MCP_PORT"),
        ge=1,
        le=65535,
    )
    wecom_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        validation_alias=AliasChoices("WECOM_LOG_LEVEL"),
    )

    @field_validator("wecom_corp_id", "wecom_corp_secret", "wecom_mcp_host", mode="before")
    @classmethod
    def _strip_required_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("wecom_corp_id")
    @classmethod
    def _validate_corp_id(cls, value: str) -> str:
        if not value:
            raise ValueError("WECOM_CORP_ID/CORPID cannot be empty")
        return value

    @field_validator("wecom_corp_secret")
    @classmethod
    def _validate_corp_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("WECOM_CORP_SECRET/CORPSECRET cannot be empty")
        return value

    @field_validator("wecom_api_base", mode="before")
    @classmethod
    def _normalize_api_base(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().rstrip("/")
        return value


def load_settings(**overrides: object) -> Settings:
    """Load settings from environment variables with helpful startup errors."""

    try:
        return Settings(**overrides)
    except ValidationError as exc:
        raise WeComConfigurationError(
            "Unable to start WeCom Mail MCP. "
            "Set WECOM_CORP_ID/WECOM_CORP_SECRET or CORPID/CORPSECRET."
        ) from exc
