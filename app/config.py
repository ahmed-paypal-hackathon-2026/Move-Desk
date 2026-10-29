"""Runtime settings, read from environment variables on every access so tests can override them."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def db_path() -> str:
    return env("DB_PATH", str(ROOT / "movedesk.db"))


def model_dir() -> Path:
    return Path(env("MODEL_DIR", str(ROOT / "models")))


def paypal_mode() -> str:
    """'sandbox' when credentials are set, otherwise 'mock' (in-process simulator).
    PAYPAL_MODE=mock forces the simulator even when credentials exist."""
    forced = env("PAYPAL_MODE").lower()
    if forced in ("mock", "sandbox"):
        return forced
    if env("PAYPAL_CLIENT_ID") and env("PAYPAL_CLIENT_SECRET"):
        return "sandbox"
    return "mock"


def paypal_base_url() -> str:
    return env("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")


def anthropic_key() -> str:
    return env("ANTHROPIC_API_KEY")


def claude_model() -> str:
    return env("CLAUDE_MODEL", "claude-sonnet-5-5")


def company() -> dict:
    return {
        "name": env("COMPANY_NAME", "Nile Movers"),
        "email": env("COMPANY_EMAIL", ""),
        "currency": env("CURRENCY", "USD"),
    }


def company_profile() -> dict:
    """Company traits the duration model was trained on."""
    return {
        "crew_experience_years": int(env("CREW_EXPERIENCE_YEARS", "6")),
        "has_dollies_and_straps": int(env("HAS_DOLLIES_AND_STRAPS", "1")),
        "region": env("COMPANY_REGION", "mixed"),
    }


def admin_key() -> str:
    return env("ADMIN_KEY")


def public_base_url() -> str:
    return env("PUBLIC_BASE_URL", "http://localhost:8000")


def scheduler_interval() -> int:
    """Seconds between automatic reminder runs. 0 disables the background scheduler."""
    return int(env("SCHEDULER_INTERVAL_SECONDS", "600"))
