"""Crawlerflow plugin for selecting and using a Webshare proxy."""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from crawlerflow.engine.context import WorkflowContext
from crawlerflow.engine.registry import BaseStep
from crawlerflow.plugins import PluginRegistrationContext


class WebshareError(RuntimeError):
    """Raised when Webshare cannot provide or configure a proxy."""


class WebshareConfigurationError(ValueError):
    """Raised when the Webshare plugin configuration is invalid."""


@dataclass(slots=True, frozen=True)
class WebshareProxy:
    """A proxy returned by Webshare, including credentials for internal use."""

    proxy_id: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None
    valid: bool | None = None
    country_code: str | None = None
    city_name: str | None = None

    @property
    def url(self) -> str:
        credentials = ""
        if self.username is not None or self.password is not None:
            if self.username is None or self.password is None:
                raise WebshareError("Webshare proxy has incomplete credentials")
            credentials = f"{quote(self.username, safe='')}:{quote(self.password, safe='')}@"
        return f"http://{credentials}{self.host}:{self.port}"

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.proxy_id,
            "host": self.host,
            "port": self.port,
            "valid": self.valid,
            "country_code": self.country_code,
            "city_name": self.city_name,
        }

    @classmethod
    def from_api(
        cls,
        value: Mapping[str, Any],
        *,
        mode: str,
        with_credentials: bool,
    ) -> WebshareProxy:
        address = value.get("proxy_address")
        host = "p.webshare.io" if mode == "backbone" else address
        port = value.get("port")
        if not isinstance(host, str) or not host:
            raise WebshareError("Webshare proxy response did not contain a proxy address")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise WebshareError("Webshare proxy response did not contain a valid port")
        username = value.get("username") if with_credentials else None
        password = value.get("password") if with_credentials else None
        if with_credentials and (not isinstance(username, str) or not isinstance(password, str)):
            raise WebshareError("Webshare proxy response did not contain credentials")
        return cls(
            proxy_id=str(value.get("id", f"{host}:{port}")),
            host=host,
            port=port,
            username=username,
            password=password,
            valid=value.get("valid") if isinstance(value.get("valid"), bool) else None,
            country_code=(
                str(value["country_code"]) if value.get("country_code") is not None else None
            ),
            city_name=str(value["city_name"]) if value.get("city_name") is not None else None,
        )


class WebshareSettings(BaseModel):
    """Workflow-level defaults for the Webshare plugin."""

    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr | None = None
    base_url: str = "https://proxy.webshare.io"
    mode: str = Field(default="direct", pattern=r"^(direct|backbone)$")
    authentication_method: str = Field(default="username", pattern=r"^(username|sourceip)$")
    country_codes: list[str] = Field(default_factory=list)
    plan_id: int | None = Field(default=None, gt=0)
    page_size: int = Field(default=100, gt=0, le=1000)
    valid_only: bool = True
    timeout: float = Field(default=30, gt=0)

    @field_validator("country_codes", mode="before")
    @classmethod
    def normalize_country_codes(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [value]
        return value


class WebshareClient:
    """Client for the Webshare proxy list API and random proxy selection."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = "https://proxy.webshare.io",
        timeout: float = 30,
        mode: str = "direct",
        authentication_method: str = "username",
        country_codes: list[str] | None = None,
        plan_id: int | None = None,
        page_size: int = 100,
        valid_only: bool = True,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        resolved_key = api_key or os.getenv("WEBSHARE_API_KEY")
        if not resolved_key or not resolved_key.strip():
            raise WebshareConfigurationError(
                "Webshare API key is required; pass api_key or set WEBSHARE_API_KEY"
            )
        if mode not in {"direct", "backbone"}:
            raise WebshareConfigurationError("Webshare mode must be direct or backbone")
        if authentication_method not in {"username", "sourceip"}:
            raise WebshareConfigurationError(
                "Webshare authentication_method must be username or sourceip"
            )
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise WebshareConfigurationError("Webshare base_url must be an HTTP(S) URL")
        if timeout <= 0 or not 1 <= page_size <= 1000:
            raise WebshareConfigurationError("Invalid Webshare timeout or page_size")

        self.api_key = resolved_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.mode = mode
        self.authentication_method = authentication_method
        self.country_codes = list(country_codes or [])
        self.plan_id = plan_id
        self.page_size = page_size
        self.valid_only = valid_only
        self._http_client = http_client
        self._owns_client = http_client is None

    async def __aenter__(self) -> WebshareClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def list_proxies(self) -> list[WebshareProxy]:
        """Retrieve every page of the configured usable proxy list."""

        if self.mode == "backbone" and self.valid_only:
            # Webshare does not support the valid filter in backbone mode.
            valid_only = False
        else:
            valid_only = self.valid_only
        params: dict[str, Any] = {
            "mode": self.mode,
            "page": 1,
            "page_size": self.page_size,
        }
        if self.country_codes:
            params["country_code__in"] = ",".join(self.country_codes)
        if self.plan_id is not None:
            params["plan_id"] = self.plan_id
        if valid_only:
            params["valid"] = "true"

        proxies: list[WebshareProxy] = []
        next_url: str | None = f"{self.base_url}/api/v2/proxy/list/"
        for _ in range(100):
            payload = await self._request("GET", next_url, params=params)
            results = payload.get("results")
            if not isinstance(results, list):
                raise WebshareError("Webshare response did not contain a results list")
            for result in results:
                if isinstance(result, Mapping):
                    proxy = WebshareProxy.from_api(
                        result,
                        mode=self.mode,
                        with_credentials=self.authentication_method == "username",
                    )
                    if not self.valid_only or proxy.valid is not False:
                        proxies.append(proxy)
            next_value = payload.get("next")
            next_url = str(next_value) if isinstance(next_value, str) and next_value else None
            if next_url is None:
                break
            params = {}
        else:
            raise WebshareError("Webshare proxy list exceeded the 100-page safety limit")
        return proxies

    async def random_proxy(self) -> WebshareProxy:
        """Select one usable proxy uniformly at random from the complete list."""

        proxies = await self.list_proxies()
        if not proxies:
            raise WebshareError("Webshare returned no usable proxies")
        return secrets.SystemRandom().choice(proxies)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await self._http_client.request(
                method,
                url,
                headers={"Authorization": f"Token {self.api_key}"},
                params=params,
            )
        except httpx.HTTPError as error:
            raise WebshareError(f"Webshare request failed: {error}") from error
        try:
            payload: Any = response.json()
        except ValueError:
            payload = {"body": response.text}
        if not isinstance(payload, dict):
            payload = {"body": payload}
        if response.is_error:
            raise WebshareError(
                f"Webshare API returned HTTP {response.status_code}: {payload}"
            )
        return payload


class WebshareProxyInfoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    save_as: str = Field(default="webshare_proxy", pattern=r"^[A-Za-z_]\w*$")


class WebshareProxyInfoStep(BaseStep[WebshareProxyInfoConfig]):
    config_model = WebshareProxyInfoConfig

    async def execute(self, context: WorkflowContext) -> dict[str, Any]:
        proxy = context.storage.get("webshare_proxy")
        if not isinstance(proxy, WebshareProxy):
            raise WebshareError("Webshare proxy has not been selected")
        result = proxy.public_dict()
        context.outputs[self.config.save_as] = result
        return result


class WebsharePlugin:
    """Select one Webshare proxy at workflow startup and keep it for the run."""

    name = "webshare"
    settings_model = WebshareSettings

    def register(self, context: PluginRegistrationContext) -> None:
        context.registry.register("webshare_proxy_info", WebshareProxyInfoStep)

    async def startup(self, context: WorkflowContext) -> None:
        settings = context.plugin_settings.get("webshare", WebshareSettings())
        if not isinstance(settings, WebshareSettings):
            raise WebshareConfigurationError("Webshare plugin settings are not initialized")
        api_key = settings.api_key.get_secret_value() if settings.api_key else None
        client = WebshareClient(
            api_key,
            base_url=settings.base_url,
            timeout=settings.timeout,
            mode=settings.mode,
            authentication_method=settings.authentication_method,
            country_codes=settings.country_codes,
            plan_id=settings.plan_id,
            page_size=settings.page_size,
            valid_only=settings.valid_only,
        )
        try:
            proxy = await client.random_proxy()
            context.proxy_url = proxy.url
            if context.browser is not None:
                configure_proxy = getattr(context.browser, "configure_proxy", None)
                if not callable(configure_proxy):
                    raise WebshareError(
                        "Browser adapter does not support proxy configuration: "
                        f"{type(context.browser).__name__}"
                    )
                result = configure_proxy(proxy.url)
                if isawaitable(result):
                    await result
        except Exception:
            await client.close()
            raise
        context.storage["webshare_client"] = client
        context.storage["webshare_proxy"] = proxy

    async def shutdown(self, context: WorkflowContext) -> None:
        client = context.storage.pop("webshare_client", None)
        context.storage.pop("webshare_proxy", None)
        context.proxy_url = None
        if isinstance(client, WebshareClient):
            await client.close()


__all__ = [
    "WebshareClient",
    "WebshareConfigurationError",
    "WebshareError",
    "WebsharePlugin",
    "WebshareProxy",
    "WebshareSettings",
]
