"""Tool modules - each registers its tools onto the shared FastMCP instance at import time."""

from . import (  # noqa: F401
    v1_rest, v2_search, v2_content, v2_interactions, v2_misc, oauth, v2_account, v2_admin,
)
