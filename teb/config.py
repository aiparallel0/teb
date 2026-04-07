import os

OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL: str = os.getenv("TEB_MODEL", "gpt-4o-mini")
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///teb.db")
MAX_TASKS_PER_GOAL: int = int(os.getenv("MAX_TASKS_PER_GOAL", "20"))

# Derive the SQLite file path from DATABASE_URL
def get_db_path() -> str:
    url = DATABASE_URL
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///"):]
    if url.startswith("sqlite://"):
        return url[len("sqlite://"):]
    return "teb.db"
