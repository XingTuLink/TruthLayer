"""Text decoding for native text formats (txt/md/csv).

Enterprise files on Windows are often GB18030; UTF-8 (with BOM tolerance)
is tried first, GB18030 second, latin-1 as the lossless last resort.
"""

from __future__ import annotations


def decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    # latin-1 decodes every byte sequence; this is unreachable in practice.
    return data.decode("latin-1", errors="replace")
