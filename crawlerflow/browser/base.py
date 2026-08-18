"""Browser abstraction used by workflow steps."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True, frozen=True)
class BrowserResponse:
    """Serializable response returned by browser-session requests."""

    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    body: str | bytes | None = None


class BrowserAdapter(ABC):
    """Backend-neutral asynchronous browser contract."""

    @abstractmethod
    async def goto(self, url: str) -> None: ...

    @abstractmethod
    async def click(self, selector: str) -> None: ...

    @abstractmethod
    async def fill(self, selector: str, value: str) -> None: ...

    @abstractmethod
    async def select(self, selector: str, value: str) -> None: ...

    @abstractmethod
    async def wait(self, selector: str, timeout_seconds: float | None = None) -> None: ...

    @abstractmethod
    async def wait_network(self, timeout_seconds: float | None = None) -> None: ...

    @abstractmethod
    async def html(self) -> str: ...

    @abstractmethod
    async def evaluate(self, script: str) -> Any: ...

    @abstractmethod
    async def cookies(self) -> dict[str, str]: ...

    @abstractmethod
    async def set_cookies(self, cookies: dict[str, str]) -> None: ...

    @abstractmethod
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: Any = None,
    ) -> BrowserResponse: ...

    @abstractmethod
    async def download(self, url: str, path: Path) -> Path: ...

    @abstractmethod
    async def screenshot(self, path: Path) -> Path: ...

    async def close(self) -> None:
        """Release browser resources when an adapter owns them."""

        return None
