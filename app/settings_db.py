
import logging

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Setting, Channel

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "referral_target": str(settings.REFERRAL_TARGET),
    "main_channel": str(settings.MAIN_CHANNEL),
    "access_channel": str(settings.ACCESS_CHANNEL),
    "auto_approve_films": str(
        settings.AUTO_APPROVE_FILMS
    ).lower(),
}


async def seed_default_settings():
    async with SessionLocal() as session:
        for key, value in DEFAULT_SETTINGS.items():
            result = await session.execute(
                select(Setting).where(
                    Setting.key == key
                )
            )
            if result.scalar_one_or_none() is None:
                session.add(
                    Setting(key=key, value=value)
                )

        await session.commit()


async def get_setting(key: str, default=None):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Setting).where(
                Setting.key == key
            )
        )
        item = result.scalar_one_or_none()

        if item is None:
            return default

        return item.value


async def set_setting(key: str, value) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Setting).where(
                Setting.key == key
            )
        )
        item = result.scalar_one_or_none()

        if item is None:
            item = Setting(
                key=key,
                value=str(value),
            )
            session.add(item)
        else:
            item.value = str(value)

        await session.commit()
        return True


async def get_referral_target() -> int:
    value = await get_setting(
        "referral_target",
        settings.REFERRAL_TARGET,
    )
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, int(settings.REFERRAL_TARGET))


async def get_main_channel() -> str:
    return str(
        await get_setting(
            "main_channel",
            settings.MAIN_CHANNEL,
        )
    )


async def get_access_channel() -> str:
    return str(
        await get_setting(
            "access_channel",
            settings.ACCESS_CHANNEL,
        )
    )


async def get_auto_approve() -> bool:
    value = await get_setting(
        "auto_approve_films",
        settings.AUTO_APPROVE_FILMS,
    )
    return str(value).strip().lower() in (
        "1", "true", "yes", "on"
    )


async def get_channels(
    category: str | None = None,
    active_only: bool = True,
):
    async with SessionLocal() as session:
        query = select(Channel).order_by(Channel.id.desc())

        if category:
            query = query.where(
                Channel.category == category
            )

        if active_only:
            query = query.where(
                Channel.active.is_(True)
            )

        result = await session.execute(query)
        return result.scalars().all()


async def add_channel(
    title: str,
    username: str,
    category: str = "pashto",
):
    username = username.strip()

    if username and not username.startswith("@"):
        username = "@" + username

    if not title.strip() or not username:
        raise ValueError(
            "Channel title and username are required."
        )

    async with SessionLocal() as session:
        result = await session.execute(
            select(Channel).where(
                Channel.username == username
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.title = title.strip()
            existing.category = category
            existing.active = True
            await session.commit()
            await session.refresh(existing)
            return existing

        channel = Channel(
            title=title.strip(),
            username=username,
            category=category,
            active=True,
        )
        session.add(channel)
        await session.commit()
        await session.refresh(channel)
        return channel


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
                Channel.id == int(channel_id)
            )
        )
        channel = result.scalar_one_or_none()

        if channel is None:
            return None

        if title is not None:
            channel.title = title.strip()

        if username is not None:
            username = username.strip()
            if username and not username.startswith("@"):
                username = "@" + username
            channel.username = username

        if category is not None:
            channel.category = category

        if active is not None:
            channel.active = bool(active)

        await session.commit()
        await session.refresh(channel)
        return channel


async def delete_channel(channel_id: int) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Channel).where(
                Channel.id == int(channel_id)
            )
        )
        channel = result.scalar_one_or_none()

        if channel is None:
            return False

        await session.delete(channel)
        await session.commit()
        return True
