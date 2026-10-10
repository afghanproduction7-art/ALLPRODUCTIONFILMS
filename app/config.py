import os
from dataclasses import dataclass
from urllib.parse import urlparse


def _get_env(name, default=""):
    return os.getenv(name, default).strip()


def _get_int_env(name, default=0):
    value = _get_env(name, str(default))
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_bool_env(name, default=False):
    value = _get_env(name, str(default)).lower()
    return value in ("1", "true", "yes", "on")


def _normalize_channel(value):
    value = (value or "").strip()

    if not value:
        return ""

    if value.startswith("https://t.me/"):
        value = value.removeprefix("https://t.me/")

    if value.startswith("http://t.me/"):
        value = value.removeprefix("http://t.me/")

    if value.startswith("@"):
        value = value[1:]

    return value.strip("/")


@dataclass(frozen=True)
class Settings:
    BOT_TOKEN: str
    DATABASE_URL: str
    APP_URL: str
    WEBHOOK_SECRET: str
    ADMIN_IDS: tuple
    MAIN_CHANNEL: str
    ACCESS_CHANNEL: str
    REFERRAL_TARGET: int
    AUTO_APPROVE_FILMS: bool


def load_settings():
    bot_token = _get_env("BOT_TOKEN")
    database_url = _get_env("DATABASE_URL")
    app_url = _get_env(
        "APP_URL",
        "https://all-production-films.onrender.com",
    ).rstrip("/")

    admin_ids_raw = _get_env("ADMIN_IDS")
    admin_ids = []

    for item in admin_ids_raw.split(","):
        item = item.strip()
        if item:
            try:
                admin_ids.append(int(item))
            except ValueError:
                raise ValueError(
                    "ADMIN_IDS must contain Telegram numeric IDs "
                    "separated by commas."
                )

    referral_target = max(
        1,
        _get_int_env("REFERRAL_TARGET", 50),
    )

    if database_url.startswith("postgres://"):
        database_url = database_url.replace(
            "postgres://",
            "postgresql+asyncpg://",
            1,
        )
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )

    if database_url and database_url.startswith(
        "postgresql+asyncpg://"
    ):
        parsed = urlparse(database_url)
        if not parsed.hostname or not parsed.path.strip("/"):
            raise ValueError(
                "DATABASE_URL is not a valid PostgreSQL connection URL."
            )

    return Settings(
        BOT_TOKEN=bot_token,
        DATABASE_URL=database_url,
        APP_URL=app_url,
        WEBHOOK_SECRET=_get_env("WEBHOOK_SECRET"),
        ADMIN_IDS=tuple(admin_ids),
        MAIN_CHANNEL=_normalize_channel(
            _get_env("MAIN_CHANNEL", "afghanproduction")
        ),
        ACCESS_CHANNEL=_normalize_channel(
            _get_env("ACCESS_CHANNEL", "ALL_PASHTO")
        ),
        REFERRAL_TARGET=referral_target,
        AUTO_APPROVE_FILMS=_get_bool_env(
            "AUTO_APPROVE_FILMS",
            True,
        ),
    )


settings = load_settings()

# Compatibility aliases for older modules.
BOT_TOKEN = settings.BOT_TOKEN
DATABASE_URL = settings.DATABASE_URL
APP_URL = settings.APP_URL
WEBHOOK_SECRET = settings.WEBHOOK_SECRET
ADMIN_IDS = settings.ADMIN_IDS
MAIN_CHANNEL = settings.MAIN_CHANNEL
ACCESS_CHANNEL = settings.ACCESS_CHANNEL
REFERRAL_TARGET = settings.REFERRAL_TARGET
AUTO_APPROVE_FILMS = settings.AUTO_APPROVE_FILMS
