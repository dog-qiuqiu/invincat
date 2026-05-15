"""Compatibility patches for Textual runtime behavior."""

from __future__ import annotations


def patch_textual_utf8_decoder() -> None:
    """Patch Textual's Linux input driver to tolerate invalid UTF-8 bytes.

    Textual's LinuxDriver reads raw bytes from the terminal file descriptor and
    decodes them with a strict UTF-8 incremental decoder. Terminals that fall
    back to X10-style mouse tracking can emit raw coordinate bytes above 127,
    which can crash the input thread. Replacing the strict decoder with
    replacement mode keeps normal input intact and drops only invalid bytes.
    """
    try:
        from codecs import getincrementaldecoder as _orig_get

        import textual.drivers.linux_driver as _ld

        def _tolerant_getincrementaldecoder(encoding: str):  # type: ignore[return]
            decoder_cls = _orig_get(encoding)
            if encoding.lower().replace("-", "") == "utf8":

                class _TolerantDecoder(decoder_cls):  # type: ignore[misc, valid-type]
                    def __init__(self, errors: str = "replace") -> None:
                        super().__init__(errors)

                return _TolerantDecoder
            return decoder_cls

        _ld.getincrementaldecoder = _tolerant_getincrementaldecoder  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
