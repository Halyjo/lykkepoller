"""QR code rendering. Just a wrapper around the qrcode library."""

import io

import qrcode


def png_bytes(url: str) -> bytes:
    """Render `url` as a PNG QR code and return the raw bytes."""
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def png_local(url: str, path: str = "presentation/current_qr.png"):
    img = qrcode.make(url)
    img.save(path, format="PNG")