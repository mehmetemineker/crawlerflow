"""Crawlerflow plugin for the CapSolver API."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from crawlerflow.engine.context import WorkflowContext
from crawlerflow.engine.registry import BaseStep
from crawlerflow.plugins import PluginRegistrationContext


class CapSolverError(RuntimeError):
    """Raised when CapSolver returns an API or task error."""

    def __init__(
        self,
        message: str,
        *,
        error_id: int | None = None,
        error_code: str | None = None,
        status_code: int | None = None,
        response: Mapping[str, Any] | None = None,
    ) -> None:
        self.error_id = error_id
        self.error_code = error_code
        self.status_code = status_code
        self.response = dict(response or {})
        super().__init__(message)


class CapSolverConfigurationError(ValueError):
    """Raised when the plugin or client configuration is invalid."""


class CapSolverSettings(BaseModel):
    """Workflow-level defaults for the CapSolver plugin."""

    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr | None = None
    base_url: str = "https://api.capsolver.com"
    app_id: str | None = None
    timeout: float = Field(default=30, gt=0)
    poll_interval: float = Field(default=3, ge=0)
    max_poll_attempts: int = Field(default=120, ge=1, le=120)


class CapSolverClient:
    """Generic async client for CapSolver's task API.

    The task payload is intentionally passed through unchanged. This supports
    current and future CapSolver task types without adding a hard-coded model
    for every CAPTCHA family.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = "https://api.capsolver.com",
        timeout: float = 30,
        poll_interval: float = 3,
        max_poll_attempts: int = 120,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        resolved_key = api_key or os.getenv("CAPSOLVER_API_KEY")
        if not resolved_key or not resolved_key.strip():
            raise CapSolverConfigurationError(
                "CapSolver API key is required; pass api_key or set CAPSOLVER_API_KEY"
            )
        parsed_base_url = urlparse(base_url)
        if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
            raise CapSolverConfigurationError("CapSolver base_url must be an HTTP(S) URL")
        if timeout <= 0:
            raise CapSolverConfigurationError("CapSolver timeout must be positive")
        if poll_interval < 0:
            raise CapSolverConfigurationError("CapSolver poll_interval cannot be negative")
        if not 1 <= max_poll_attempts <= 120:
            raise CapSolverConfigurationError(
                "CapSolver max_poll_attempts must be between 1 and 120"
            )

        self.api_key = resolved_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_poll_attempts = max_poll_attempts
        self._http_client = http_client
        self._owns_client = http_client is None

    async def __aenter__(self) -> CapSolverClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def create_task(
        self,
        task: Mapping[str, Any],
        *,
        app_id: str | None = None,
        callback_url: str | None = None,
    ) -> dict[str, Any]:
        """Call ``createTask`` with an arbitrary CapSolver task object."""

        body: dict[str, Any] = {"clientKey": self.api_key, "task": dict(task)}
        if app_id is not None:
            body["appId"] = app_id
        if callback_url is not None:
            body["callbackUrl"] = callback_url
        return await self._request("createTask", body)

    async def get_task_result(self, task_id: str) -> dict[str, Any]:
        """Call ``getTaskResult`` for an asynchronous task."""

        return await self._request(
            "getTaskResult",
            {"clientKey": self.api_key, "taskId": task_id},
        )

    async def get_token(
        self,
        task: Mapping[str, Any],
        *,
        app_id: str | None = None,
        callback_url: str | None = None,
    ) -> dict[str, Any]:
        """Call CapSolver's direct ``getToken`` endpoint."""

        body: dict[str, Any] = {"clientKey": self.api_key, "task": dict(task)}
        if app_id is not None:
            body["appId"] = app_id
        if callback_url is not None:
            body["callbackUrl"] = callback_url
        return await self._request("getToken", body)

    async def solve(
        self,
        task: Mapping[str, Any],
        *,
        app_id: str | None = None,
        callback_url: str | None = None,
        poll_interval: float | None = None,
        max_poll_attempts: int | None = None,
    ) -> dict[str, Any]:
        """Create a task and handle both synchronous and asynchronous results."""

        result = await self.create_task(
            task,
            app_id=app_id,
            callback_url=callback_url,
        )
        if result.get("status") == "ready" and result.get("solution") is not None:
            return result

        task_id = result.get("taskId")
        if not isinstance(task_id, str) or not task_id:
            raise CapSolverError(
                "CapSolver createTask response did not contain taskId or a ready solution",
                response=result,
            )
        interval = self.poll_interval if poll_interval is None else poll_interval
        attempts = self.max_poll_attempts if max_poll_attempts is None else max_poll_attempts
        if interval < 0 or not 1 <= attempts <= 120:
            raise CapSolverConfigurationError(
                "poll_interval must be non-negative and max_poll_attempts must be 1..120"
            )

        for _ in range(attempts):
            if interval:
                await asyncio.sleep(interval)
            result = await self.get_task_result(task_id)
            if result.get("status") == "ready":
                return result

        raise CapSolverError(
            f"CapSolver task did not become ready after {attempts} polling attempts",
            response={"taskId": task_id},
        )

    async def get_balance(self) -> dict[str, Any]:
        """Call ``getBalance``."""

        return await self._request("getBalance", {"clientKey": self.api_key})

    async def request(
        self,
        endpoint: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call a CapSolver API endpoint while injecting the configured client key.

        This escape hatch keeps the plugin forward-compatible with new CapSolver
        endpoints while retaining the same response and error handling.
        """

        body = dict(payload or {})
        body["clientKey"] = self.api_key
        return await self._request(endpoint.strip("/"), body)

    async def get_state(self) -> dict[str, Any]:
        """Return the service state documented by CapSolver's getState page.

        CapSolver currently documents this operation using the ``getBalance``
        endpoint and response shape, so this method intentionally follows that
        documented wire contract.
        """

        return await self.get_balance()

    async def _request(self, endpoint: str, body: Mapping[str, Any]) -> dict[str, Any]:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await self._http_client.post(
                f"{self.base_url}/{endpoint}",
                json=body,
            )
        except httpx.HTTPError as error:
            raise CapSolverError(f"CapSolver request failed: {error}") from error
        try:
            payload: Any = response.json()
        except ValueError:
            payload = {"body": response.text}
        if not isinstance(payload, dict):
            payload = {"body": payload}

        error_id = payload.get("errorId")
        if response.is_error or (isinstance(error_id, int) and error_id != 0):
            description = payload.get("errorDescription") or payload.get(
                "errorCode", response.reason_phrase
            )
            raise CapSolverError(
                f"CapSolver request failed: {description}",
                error_id=error_id if isinstance(error_id, int) else None,
                error_code=payload.get("errorCode"),
                status_code=response.status_code,
                response=payload,
            )
        return payload


def _settings(context: WorkflowContext) -> CapSolverSettings:
    configured = context.plugin_settings.get("capsolver", CapSolverSettings())
    if not isinstance(configured, CapSolverSettings):
        raise CapSolverConfigurationError("CapSolver plugin settings are not initialized")
    return configured


def _client(context: WorkflowContext) -> CapSolverClient:
    client = context.storage.get("capsolver_client")
    if isinstance(client, CapSolverClient):
        return client
    settings = _settings(context)
    api_key = settings.api_key.get_secret_value() if settings.api_key else None
    client = CapSolverClient(
        api_key,
        base_url=settings.base_url,
        timeout=settings.timeout,
        poll_interval=settings.poll_interval,
        max_poll_attempts=settings.max_poll_attempts,
    )
    context.storage["capsolver_client"] = client
    return client


class _TaskStepConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: dict[str, Any]
    app_id: str | None = None
    callback_url: str | None = None
    save_as: str = Field(pattern=r"^[A-Za-z_]\w*$")


class _TaskResultStepConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    save_as: str = Field(pattern=r"^[A-Za-z_]\w*$")


class CapSolverCreateTaskStep(BaseStep[_TaskStepConfig]):
    config_model = _TaskStepConfig

    async def execute(self, context: WorkflowContext) -> dict[str, Any]:
        settings = _settings(context)
        result = await _client(context).create_task(
            self.config.task,
            app_id=self.config.app_id or settings.app_id,
            callback_url=self.config.callback_url,
        )
        context.outputs[self.config.save_as] = result
        return result


class CapSolverGetTaskResultStep(BaseStep[_TaskResultStepConfig]):
    config_model = _TaskResultStepConfig

    async def execute(self, context: WorkflowContext) -> dict[str, Any]:
        result = await _client(context).get_task_result(self.config.task_id)
        context.outputs[self.config.save_as] = result
        return result


class CapSolverSolveStep(BaseStep[_TaskStepConfig]):
    config_model = _TaskStepConfig

    async def execute(self, context: WorkflowContext) -> dict[str, Any]:
        settings = _settings(context)
        result = await _client(context).solve(
            self.config.task,
            app_id=self.config.app_id or settings.app_id,
            callback_url=self.config.callback_url,
        )
        context.outputs[self.config.save_as] = result
        return result


class CapSolverGetTokenStep(BaseStep[_TaskStepConfig]):
    config_model = _TaskStepConfig

    async def execute(self, context: WorkflowContext) -> dict[str, Any]:
        settings = _settings(context)
        result = await _client(context).get_token(
            self.config.task,
            app_id=self.config.app_id or settings.app_id,
            callback_url=self.config.callback_url,
        )
        context.outputs[self.config.save_as] = result
        return result


class _RawRequestStepConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9]*$")
    payload: dict[str, Any] = Field(default_factory=dict)
    save_as: str = Field(pattern=r"^[A-Za-z_]\w*$")


class CapSolverRequestStep(BaseStep[_RawRequestStepConfig]):
    config_model = _RawRequestStepConfig

    async def execute(self, context: WorkflowContext) -> dict[str, Any]:
        result = await _client(context).request(self.config.endpoint, self.config.payload)
        context.outputs[self.config.save_as] = result
        return result


class _SaveOnlyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    save_as: str = Field(pattern=r"^[A-Za-z_]\w*$")


class CapSolverGetBalanceStep(BaseStep[_SaveOnlyConfig]):
    config_model = _SaveOnlyConfig

    async def execute(self, context: WorkflowContext) -> dict[str, Any]:
        result = await _client(context).get_balance()
        context.outputs[self.config.save_as] = result
        return result


class CapSolverGetStateStep(BaseStep[_SaveOnlyConfig]):
    config_model = _SaveOnlyConfig

    async def execute(self, context: WorkflowContext) -> dict[str, Any]:
        result = await _client(context).get_state()
        context.outputs[self.config.save_as] = result
        return result


class CapSolverPlugin:
    """Crawlerflow plugin exposing CapSolver's generic task API."""

    name = "capsolver"
    settings_model = CapSolverSettings

    def register(self, context: PluginRegistrationContext) -> None:
        context.registry.register("capsolver_create_task", CapSolverCreateTaskStep)
        context.registry.register("capsolver_get_task_result", CapSolverGetTaskResultStep)
        context.registry.register("capsolver_solve", CapSolverSolveStep)
        context.registry.register("capsolver_get_token", CapSolverGetTokenStep)
        context.registry.register("capsolver_request", CapSolverRequestStep)
        context.registry.register("capsolver_get_balance", CapSolverGetBalanceStep)
        context.registry.register("capsolver_get_state", CapSolverGetStateStep)

    async def startup(self, context: WorkflowContext) -> None:
        settings = _settings(context)
        api_key = settings.api_key.get_secret_value() if settings.api_key else None
        context.storage["capsolver_client"] = CapSolverClient(
            api_key,
            base_url=settings.base_url,
            timeout=settings.timeout,
            poll_interval=settings.poll_interval,
            max_poll_attempts=settings.max_poll_attempts,
        )

    async def shutdown(self, context: WorkflowContext) -> None:
        client = context.storage.pop("capsolver_client", None)
        if isinstance(client, CapSolverClient):
            await client.close()


__all__ = [
    "CapSolverClient",
    "CapSolverConfigurationError",
    "CapSolverError",
    "CapSolverPlugin",
    "CapSolverSettings",
]
