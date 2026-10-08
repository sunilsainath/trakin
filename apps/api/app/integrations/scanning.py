"""Malware scanning abstraction for uploaded documents.

Contract
  * `get_scanner()` returns None when no scanner is configured. Callers must
    treat that as "unknown", never as "clean": versions stay PENDING and the
    UI reports the scan as pending.
  * A configured scanner speaks the ClamAV INSTREAM protocol over TCP
    (`MALWARE_SCANNER_URL=clamav://host:3310`). Swapping in a vendor API later
    means adding one class that implements `Scanner.scan`.

INFECTED content is never served: `documents.download_url` refuses it.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ScanVerdict:
    clean: bool
    engine: str
    detail: str
    # Set when the scan itself failed (engine unreachable, timeout). The
    # caller records ERROR, which is distinct from INFECTED: an error must
    # never be presented as a finding, nor as a pass.
    error: str | None = None


class Scanner(Protocol):
    async def scan(self, content: bytes, *, filename: str) -> ScanVerdict: ...


class ClamAvScanner:
    """ClamAV over TCP (INSTREAM). Fails closed: any error is not a pass."""

    def __init__(self, host: str, port: int = 3310) -> None:
        self._host = host
        self._port = port

    async def scan(self, content: bytes, *, filename: str) -> ScanVerdict:
        import asyncio

        def _scan_sync() -> ScanVerdict:
            with socket.create_connection((self._host, self._port), timeout=30) as sock:
                sock.sendall(b"zINSTREAM\0")
                for offset in range(0, len(content), 8192):
                    chunk = content[offset : offset + 8192]
                    sock.sendall(struct.pack(">I", len(chunk)) + chunk)
                sock.sendall(struct.pack(">I", 0))
                response = b""
                while not response.endswith(b"\0"):
                    part = sock.recv(4096)
                    if not part:
                        break
                    response += part
                text = response.decode("utf-8", "replace").strip().rstrip("\0")
                if text.endswith("OK"):
                    return ScanVerdict(clean=True, engine="clamav", detail="OK")
                return ScanVerdict(clean=False, engine="clamav", detail=text[:500])

        try:
            return await asyncio.to_thread(_scan_sync)
        except Exception as exc:  # noqa: BLE001 - a dead scanner fails closed
            logger.warning("malware_scan_error", error=str(exc)[:200])
            return ScanVerdict(
                clean=False,
                engine="clamav",
                detail="scan engine unreachable",
                error=str(exc)[:500],
            )


def get_scanner(settings: Settings | None = None) -> Scanner | None:
    """None when unconfigured: the caller must leave the version PENDING."""
    settings = settings or get_settings()
    url = (settings.malware_scanner_url or "").strip()
    if not url:
        return None
    host_port = url.split("://", 1)[-1]
    host, _, port = host_port.partition(":")
    return ClamAvScanner(host.strip() or "localhost", int(port or 3310))
