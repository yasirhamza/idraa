"""The one builder for download ``Content-Disposition`` headers (advisory C9).

Every attachment response in ``src/idraa`` builds its header with
``attachment_disposition``; ``tests/unit/test_csv_export_helper.py`` fails on
any other ``"Content-Disposition"`` key in the source tree. The filename is
quoted, and a quote, semicolon, backslash or any control character is replaced
with ``_`` so no caller — present or future, literal or request-derived — can
inject header syntax.
"""

from __future__ import annotations

import re

_FILENAME_UNSAFE = re.compile(r'[";\\\x00-\x1f\x7f]')


def sanitize_disposition_filename(filename: str) -> str:
    """Replace characters that could break out of the Content-Disposition value."""
    return _FILENAME_UNSAFE.sub("_", filename)


def attachment_disposition(filename: str) -> str:
    """``Content-Disposition`` value for a download: sanitised, quoted filename."""
    return f'attachment; filename="{sanitize_disposition_filename(filename)}"'
