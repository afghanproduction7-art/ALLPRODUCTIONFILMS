from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str
    DATABASE_URL: str

    APP_URL: str
    WEBHOOK_SECRET: str

    ADMIN_IDS: str = ""

    BOT_USERNAME: str = "ALL_PRODUCTION_FILMBOT"

    MAIN_CHANNEL: str = "@afghanproduction"
    ACCESS_CHANNEL: str = "@ALL_PASHTO"

    REFERRAL_TARGET: int = 50

    AUTO_APPROVE_FILMS: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore"
    )

    @property
    def admin_ids(self) -> set[int]:
        result = set()

        for value in self.ADMIN_IDS.split(","):
            value = value.strip()

            if value.isdigit():
                result.add(int(value))

        return result


settings = Settings()
