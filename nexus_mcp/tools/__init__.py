"""Tool modules - each registers its tools onto the shared FastMCP instance at import time."""

from . import (  # noqa: F401
    oauth,
    v1_rest,
    v2_account,
    v2_admin,
    v2_content,
    v2_interactions,
    v2_misc,
    v2_search,
    web_scrape,
)
