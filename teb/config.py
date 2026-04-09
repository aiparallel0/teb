import os
from typing import List, Optional

OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL: str = os.getenv("TEB_MODEL", "gpt-4o-mini")
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///teb.db")
MAX_TASKS_PER_GOAL: int = int(os.getenv("MAX_TASKS_PER_GOAL", "20"))

# Anthropic / Claude settings
ANTHROPIC_API_KEY: Optional[str] = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL: str = os.getenv("TEB_ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

# AI provider selection: "anthropic", "openai", or "auto" (prefers Anthropic if key set)
AI_PROVIDER: str = os.getenv("TEB_AI_PROVIDER", "auto")

# Executor settings
EXECUTOR_TIMEOUT: int = int(os.getenv("TEB_EXECUTOR_TIMEOUT", "30"))  # HTTP timeout in seconds
EXECUTOR_MAX_RETRIES: int = int(os.getenv("TEB_EXECUTOR_MAX_RETRIES", "2"))

# JWT / auth settings
JWT_SECRET: str = os.getenv("TEB_JWT_SECRET", "change-me-in-production-not-safe")
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRE_HOURS: int = int(os.getenv("TEB_JWT_EXPIRE_HOURS", "168"))  # 7 days

# Credential encryption key (Fernet, base64-encoded 32 bytes)
SECRET_KEY: Optional[str] = os.getenv("TEB_SECRET_KEY")

# CORS — comma-separated origins, or "*" (default: allow all for dev)
CORS_ORIGINS: List[str] = [
    o.strip()
    for o in os.getenv("TEB_CORS_ORIGINS", "*").split(",")
    if o.strip()
]

# Logging
LOG_LEVEL: str = os.getenv("TEB_LOG_LEVEL", "INFO")

# Payment providers
MERCURY_API_KEY: Optional[str] = os.getenv("TEB_MERCURY_API_KEY")
MERCURY_BASE_URL: str = os.getenv("TEB_MERCURY_BASE_URL", "https://api.mercury.com/api/v1")
STRIPE_API_KEY: Optional[str] = os.getenv("TEB_STRIPE_API_KEY")
STRIPE_BASE_URL: str = os.getenv("TEB_STRIPE_BASE_URL", "https://api.stripe.com/v1")

# Autonomous execution
AUTONOMOUS_EXECUTION_ENABLED: bool = os.getenv("TEB_AUTONOMOUS_EXECUTION", "true").lower() == "true"
AUTONOMOUS_EXECUTION_INTERVAL: int = int(os.getenv("TEB_AUTONOMOUS_EXECUTION_INTERVAL", "30"))

# Autopilot default threshold (max $ auto-approved per transaction)
AUTOPILOT_DEFAULT_THRESHOLD: float = float(os.getenv("TEB_AUTOPILOT_DEFAULT_THRESHOLD", "50.0"))


def get_ai_provider() -> Optional[str]:
    """Resolve which AI provider to use. Returns 'anthropic', 'openai', or None."""
    if AI_PROVIDER == "anthropic" and ANTHROPIC_API_KEY:
        return "anthropic"
    if AI_PROVIDER == "openai" and OPENAI_API_KEY:
        return "openai"
    if AI_PROVIDER == "auto":
        if ANTHROPIC_API_KEY:
            return "anthropic"
        if OPENAI_API_KEY:
            return "openai"
    return None


def has_ai() -> bool:
    """Return True if any AI provider is configured."""
    return get_ai_provider() is not None


# Derive the SQLite file path from DATABASE_URL
def get_db_path() -> str:
    url = DATABASE_URL
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///"):]
    if url.startswith("sqlite://"):
        return url[len("sqlite://"):]
    return "teb.db"
