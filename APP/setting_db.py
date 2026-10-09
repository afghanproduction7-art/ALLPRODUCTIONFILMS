
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Channel, Setting


# =========================================================
# DEFAULT SETTINGS
# =========================================================

DEFAULT_SETTINGS = {
    "referral_target": str(settings.REFERRAL_TARGET),

    "main_channel": settings.MAIN_CHANNEL,

    "access_channel": settings.ACCESS_CHANNEL,

    "auto_approve_films": str(
        settings.AUTO_APPROVE_FILMS
    ).lower(),
}


# =========================================================
# GET SETTING
# =========================================================

async def get_setting(
    key: str,
    default: str | None = None,
) -> str | None:

    async with SessionLocal() as session:

        result = await session.execute(
            select(Setting).where(
                Setting.key == key
            )
        )

        setting = result.scalar_one_or_none()

        if setting is None:
            return default

        return setting.value


# =========================================================
# SET SETTING
# =========================================================

async def set_setting(
    key: str,
    value: str,
):

    async with SessionLocal() as session:

        result = await session.execute(
            select(Setting).where(
                Setting.key == key
            )
        )

        setting = result.scalar_one_or_none()

        if setting is None:

            setting = Setting(
                key=key,
                value=str(value),
            )

            session.add(setting)

        else:

            setting.value = str(value)

        await session.commit()

        return setting


# =========================================================
# SEED DEFAULT SETTINGS
# =========================================================

async def seed_default_settings():

    async with SessionLocal() as session:

        for key, value in DEFAULT_SETTINGS.items():

            result = await session.execute(
                select(Setting).where(
                    Setting.key == key
                )
            )

            existing = result.scalar_one_or_none()

            if existing is None:

                session.add(
                    Setting(
                        key=key,
                        value=value,
                    )
                )

        await session.commit()


# =========================================================
# REFERRAL TARGET
# =========================================================

async def get_referral_target() -> int:

    value = await get_setting(
        "referral_target",
        str(settings.REFERRAL_TARGET),
    )

    try:

        target = int(
            value or settings.REFERRAL_TARGET
        )

    except ValueError:

        target = settings.REFERRAL_TARGET

    return max(
        target,
        1,
    )


# =========================================================
# MAIN CHANNEL
# =========================================================

async def get_main_channel() -> str:

    return await get_setting(
        "main_channel",
        settings.MAIN_CHANNEL,
    ) or settings.MAIN_CHANNEL


# =========================================================
# ACCESS CHANNEL
# =========================================================

async def get_access_channel() -> str:

    return await get_setting(
        "access_channel",
        settings.ACCESS_CHANNEL,
    ) or settings.ACCESS_CHANNEL


# =========================================================
# AUTO APPROVE
# =========================================================

async def get_auto_approve() -> bool:

    value = await get_setting(
        "auto_approve_films",
        str(
            settings.AUTO_APPROVE_FILMS
        ).lower(),
    )

    return str(value).lower() in {
        "true",
        "1",
        "yes",
        "on",
    }


# =========================================================
# CHANNEL LIST
# =========================================================

async def get_channels(
    category: str | None = None,
    active_only: bool = True,
):

    async with SessionLocal() as session:

        query = select(Channel)

        if active_only:

            query = query.where(
                Channel.active.is_(True)
            )

        if category:

            query = query.where(
                Channel.category == category
            )

        query = query.order_by(
            Channel.id.asc()
        )

        result = await session.execute(
            query
        )

        return result.scalars().all()


# =========================================================
# ADD CHANNEL
# =========================================================

async def add_channel(
    title: str,
    username: str,
    category: str = "pashto",
):

    username = username.strip()

    if not username.startswith("@"):

        username = "@" + username

    async with SessionLocal() as session:

        result = await session.execute(
            select(Channel).where(
                Channel.username == username
            )
        )

        existing = result.scalar_one_or_none()

        if existing:

            existing.title = title.strip()
            existing.category = category.strip()
            existing.active = True

            await session.commit()
            await session.refresh(
                existing
            )

            return existing

        channel = Channel(
            title=title.strip(),
            username=username,
            category=category.strip(),
            active=True,
        )

        session.add(
            channel
        )

        await session.commit()
        await session.refresh(
            channel
        )

        return channel


# =========================================================
# UPDATE CHANNEL
# =========================================================

async def update_channel(
    channel_id: int,
    title: str | None = None,
    username: str | None = None,
    category: str | None = None,
    active: bool | None = None,
):

    async with SessionLocal() as session:

        result = await session.execute(
            select(Channel).where(
                Channel.id == channel_id
            )
        )

        channel = result.scalar_one_or_none()

        if channel is None:
            return None

        if title is not None:
            channel.title = title.strip()

        if username is not None:

            username = username.strip()

            if not username.startswith("@"):
                username = "@" + username

            channel.username = username

        if category is not None:
            channel.category = category.strip()

        if active is not None:
            channel.active = active

        await session.commit()
        await session.refresh(
            channel
        )

        return channel


# =========================================================
# DELETE CHANNEL
# =========================================================

async def delete_channel(
    channel_id: int,
):

    async with SessionLocal() as session:

        result = await session.execute(
            select(Channel).where(
                Channel.id == channel_id
            )
        )

        channel = result.scalar_one_or_none()

        if channel is None:
            return False

        await session.delete(
            channel
        )

        await session.commit()

        return True
