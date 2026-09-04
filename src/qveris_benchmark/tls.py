"""Verified TLS contexts for the direct QVeris HTTP boundaries."""

from __future__ import annotations

import os
from pathlib import Path
import ssl


def verified_ssl_context(
    *,
    ssl_context: ssl.SSLContext | None,
    ca_file: str | None,
    environment_ca_file: str,
) -> ssl.SSLContext:
    """Return a hostname-verifying context, or fail before any HTTP request."""
    if ssl_context is not None:
        if ca_file is not None:
            raise ValueError("ssl_context and ca_file are mutually exclusive")
        if not isinstance(ssl_context, ssl.SSLContext):
            raise ValueError("ssl_context must be an ssl.SSLContext")
        if ssl_context.verify_mode != ssl.CERT_REQUIRED or not ssl_context.check_hostname:
            raise ValueError("ssl_context must require certificate and hostname verification")
        return ssl_context
    selected = ca_file if ca_file is not None else os.environ.get(environment_ca_file) or os.environ.get("SSL_CERT_FILE")
    if selected is None:
        return ssl.create_default_context()
    if type(selected) is not str or not selected:
        raise ValueError("ca_file must be a non-empty certificate-bundle path")
    try:
        bundle = Path(selected).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("ca_file must resolve to a readable certificate bundle") from exc
    if not bundle.is_file():
        raise ValueError("ca_file must resolve to a regular certificate bundle")
    try:
        return ssl.create_default_context(cafile=str(bundle))
    except (OSError, ssl.SSLError) as exc:
        raise ValueError("ca_file is not a usable certificate bundle") from exc
