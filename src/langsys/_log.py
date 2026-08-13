"""The library's logger.

Following the standard-library convention, the SDK never configures handlers; it just
emits to the ``langsys`` logger with a ``NullHandler`` attached, so nothing is printed
unless the application opts in::

    import logging
    logging.getLogger("langsys").setLevel(logging.DEBUG)
"""

from __future__ import annotations

import logging

logger = logging.getLogger("langsys")
logger.addHandler(logging.NullHandler())
