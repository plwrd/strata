"""Qt WebEngine host: the ``strata://`` scheme, navigation policy, and the page.

Why a custom scheme rather than ``file://`` or a localhost HTTP server:

* ``file://`` gives an opaque origin, unreliable CSP behaviour and (historically)
  broad local-file read access from the page;
* a localhost HTTP server opens a real port that any other local process can
  reach, which is an attack surface a *local-first* app has no reason to expose.

``strata://app/…`` is registered as a secure, local, CORS-enabled scheme and is
served straight from the bundled ``frontend/dist`` directory in memory. Nothing
listens on a socket.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import cast

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QUrl
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineUrlRequestJob,
    QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler,
)
from PySide6.QtWidgets import QMessageBox, QWidget

from app.infrastructure.logging.logger import get_logger
from app.infrastructure.storage.paths import resolve_within

logger = get_logger(__name__)

SCHEME = "strata"
HOST = "app"
APP_URL = f"{SCHEME}://{HOST}/index.html"

# Everything the page may do. `connect-src 'self'` means the frontend cannot
# reach the network at all: every outbound call goes through Python, where the
# per-layer AI policy is enforced. Note that Qt WebChannel communicates in-process
# and is unaffected by connect-src.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "worker-src 'self' blob:; "
    "media-src 'self' blob:; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)

_SAFE_SUFFIXES = frozenset(
    {
        ".html",
        ".js",
        ".mjs",
        ".css",
        ".json",
        ".svg",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".woff",
        ".woff2",
        ".ttf",
        ".map",
        ".wasm",
        ".glsl",
        ".ico",
    }
)


def register_scheme() -> None:
    """Register ``strata://``. Must run before the QApplication is constructed."""
    if QWebEngineUrlScheme.schemeByName(QByteArray(SCHEME.encode())).name():
        return
    scheme = QWebEngineUrlScheme(SCHEME.encode())
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.HostAndPort)
    # The default port is already "unspecified"; setting it explicitly is what a
    # network scheme would do, and strata:// never touches a socket.
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.SecureScheme
        | QWebEngineUrlScheme.Flag.LocalScheme
        | QWebEngineUrlScheme.Flag.LocalAccessAllowed
        | QWebEngineUrlScheme.Flag.CorsEnabled
    )
    QWebEngineUrlScheme.registerScheme(scheme)


class FrontendSchemeHandler(QWebEngineUrlSchemeHandler):
    """Serves the bundled frontend from disk. Read-only, and only from ``root``."""

    def __init__(self, root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._root = root

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:
        url = job.requestUrl()
        if url.host() != HOST:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return

        relative = url.path().lstrip("/") or "index.html"
        try:
            path = resolve_within(self._root, *relative.split("/"))
        except Exception:
            logger.warning("scheme.rejected_path")
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
            return

        if not path.is_file():
            # SPA fallback: unknown routes render the app shell, not a 404.
            path = self._root / "index.html"
            if not path.is_file():
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return

        if path.suffix.lower() not in _SAFE_SUFFIXES:
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
            return

        data = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix.lower() in (".js", ".mjs"):
            mime = "text/javascript"

        # Parented to the job: Qt owns the buffer and frees it when the request
        # finishes, so no per-request Python reference needs to be retained.
        buffer = QBuffer(job)
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(QByteArray(mime.encode()), buffer)


class StrataPage(QWebEnginePage):
    """A page that cannot navigate away and cannot open a window."""

    def __init__(
        self,
        profile: QWebEngineProfile,
        parent: QObject | None = None,
        *,
        allow_dev_server: str | None = None,
    ) -> None:
        super().__init__(profile, parent)
        self._allow_dev_server = allow_dev_server
        self.certificateError.connect(self._reject_certificate)

    def acceptNavigationRequest(
        self,
        url: QUrl | str,
        _type: QWebEnginePage.NavigationType,
        is_main_frame: bool,
    ) -> bool:
        target = QUrl(url) if isinstance(url, str) else url

        if target.scheme() == SCHEME:
            return True
        if self._allow_dev_server and target.toString().startswith(self._allow_dev_server):
            return True
        if not is_main_frame:
            return False

        # Anything else is an external link. Ask, then hand it to the OS browser.
        if target.scheme() in ("http", "https", "mailto"):
            self._confirm_external(target)
        else:
            logger.warning("navigation.blocked", scheme=target.scheme())
        return False

    def _confirm_external(self, url: QUrl) -> None:
        from PySide6.QtGui import QDesktopServices

        answer = QMessageBox.question(
            cast("QWidget", None),  # a top-level dialog: Qt accepts a null parent
            "Open an external link?",
            f"Strata wants to open this link in your browser:\n\n{url.toString()}\n\n"
            "Nothing from your workspace is sent by opening it.",
            QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Open:
            QDesktopServices.openUrl(url)

    def javaScriptConsoleMessage(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line: int,
        source: str,
    ) -> None:
        logger.info(
            "frontend.console",
            level=level.name,
            message=message[:500],
            line=line,
            source=source[:200],
        )

    def _reject_certificate(self, error: object) -> None:
        """Never accept a bad certificate, not even in development.

        In Qt 6 this is a *signal*, not a virtual method: overriding
        `certificateError` looks like it hardens the page and in fact does
        nothing. Qt's default is already to reject, and this makes the rejection
        explicit and logged.
        """
        logger.warning("certificate.rejected")
        reject = getattr(error, "rejectCertificate", None)
        if callable(reject):
            reject()


def build_profile(
    frontend_root: Path,
    *,
    persistent_path: Path,
    parent: QObject | None = None,
) -> tuple[QWebEngineProfile, FrontendSchemeHandler]:
    profile = QWebEngineProfile("strata", parent)
    profile.setPersistentStoragePath(str(persistent_path))
    profile.setCachePath(str(persistent_path / "cache"))
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
    )
    handler = FrontendSchemeHandler(frontend_root, profile)
    profile.installUrlSchemeHandler(QByteArray(SCHEME.encode()), handler)
    return profile, handler
