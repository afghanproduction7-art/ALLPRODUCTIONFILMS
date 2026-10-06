from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    database_url: str
    app_url: str
    webhook_secret: str
    admin_ids: str = ""

    # اصلي چینل — د 50 کسانو Referral لپاره
    main_channel: str = "@afghanproduction"

    # د Bot او Mini App د استعمال اجباري چینل
    access_channel: str = "@ALL_PASHTO"

    # د فلم نشرولو شرط
    referral_target: int = 50

    bot_username: str = "ALL_PRODUCTION_FILMBOT"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )

    @property
    def admins(self) -> set[int]:
        return {
            int(x.strip())
            for x in self.admin_ids.split(",")
            if x.strip().isdigit()
        }


settings = Settings()
