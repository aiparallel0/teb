"""
teb storage package — backward-compatible re-exports.

This package was split from the monolithic storage.py. The base infrastructure
lives in storage.base; domain functions are organized into submodules.
For backward compatibility, all public symbols are re-exported here.
"""
# Re-export base infrastructure
from teb.storage.base import (  # noqa: F401
    _BUSY_TIMEOUT_MS,
    _DB_PATH,
    _MAX_RETRIES,
    _REVENUE_UNITS,
    _conn,
    _db_path,
    _decrypt_value,
    _encrypt_value,
    _get_fernet,
    _run_migrations,
    _safe_add_column,
    _with_retry,
    init_db,
    register_reset_callback,
    set_db_path,
)

# Re-export all domain functions from the monolith
# These will be gradually migrated to their own submodules
from teb.storage._monolith import *  # noqa: F401,F403
