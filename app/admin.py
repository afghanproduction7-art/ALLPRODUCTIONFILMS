import logging

from sqlalchemy import select

from app.database import SessionLocal
from app.models import User, Film, Channel, Setting, AdminAction

logger = logging.getLogger(__name__)


async def record_admin_action(
    admin_id: int,
    action: str,
    target_id: str | None = None,
):
    async with SessionLocal() as session:
        session.add(
            AdminAction(
                admin_id=admin_id,
                action=action,
                target_id=target_id,
            )
        )
        await session.commit()


async def get_admin_stats():
    async with SessionLocal() as session:
        users_result = await session.execute(select(User))
        films_result = await session.execute(select(Film))
        channels_result = await session.execute(select(Channel))

        users = users_result.scalars().all()
        films = films_result.scalars().all()
        channels = channels_result.scalars().all()

        return {
            "users": len(users),
            "blocked_users": sum(1 for user in users if user.is_blocked),
            "publishers": sum(1 for user in users if user.can_publish),
            "films": len(films),
            "approved_films": sum(1 for film in films if film.approved),
            "pending_films": sum(1 for film in films if not film.approved),
            "official_films": sum(1 for film in films if film.official),
            "channels": len(channels),
            "active_channels": sum(1 for channel in channels if channel.active),
        }


async def get_users(limit: int = 100):
    limit = max(1, min(int(limit), 500))

    async with SessionLocal() as session:
        result = await session.execute(
            select(User)
            .order_by(User.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()


async def set_user_blocked(
    telegram_id: int,
    blocked: bool,
    admin_id: int | None = None,
):
    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == int(telegram_id))
        )
        user = result.scalar_one_or_none()

        if user is None:
            return False

        user.is_blocked = bool(blocked)
        await session.commit()

    if admin_id is not None:
        await record_admin_action(
            admin_id,
            "block_user" if blocked else "unblock_user",
            str(telegram_id),
        )

    return True


async def set_user_can_publish(
    telegram_id: int,
    allowed: bool,
    admin_id: int | None = None,
):
    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == int(telegram_id))
        )
        user = result.scalar_one_or_none()

        if user is None:
            return False

        user.can_publish = bool(allowed)
        await session.commit()

    if admin_id is not None:
        await record_admin_action(
            admin_id,
            "allow_publish" if allowed else "remove_publish_permission",
            str(telegram_id),
        )

    return True


async def get_channels(active_only: bool = False):
    async with SessionLocal() as session:
        query = select(Channel).order_by(Channel.id.desc())

        if active_only:
            query = query.where(Channel.active.is_(True))

        result = await session.execute(query)
        return result.scalars().all()


async def add_channel(
    title: str,
    username: str,
    category: str = "pashto",
    admin_id: int | None = None,
):
    username = username.strip()
    if username.startswith("https://t.me/"):
        username = "@" + username.rsplit("/", 1)[-1]
    elif not username.startswith("@"):
        username = "@" + username

    async with SessionLocal() as session:
        existing = await session.execute(
            select(Channel).where(Channel.username == username)
        )
        if existing.scalar_one_or_none():
            return None

        channel = Channel(
            title=title.strip(),
            username=username,
            category=category.strip() or "pashto",
            active=True,
        )
        session.add(channel)
        await session.commit()
        await session.refresh(channel)

        channel_id = channel.id

    if admin_id is not None:
        await record_admin_action(admin_id, "add_channel", str(channel_id))

    return channel_id


async def update_channel(
    channel_id: int,
    title: str | None = None,
    username: str | None = None,
    category: str | None = None,
    active: bool | None = None,
    admin_id: int | None = None,
):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Channel).where(Channel.id == int(channel_id))
        )
        channel = result.scalar_one_or_none()

        if channel is None:
            return False

        if title is not None:
            channel.title = title.strip()

        if username is not None:
            username = username.strip()
            if username.startswith("https://t.me/"):
                username = "@" + username.rsplit("/", 1)[-1]
            elif not username.startswith("@"):
                username = "@" + username
            channel.username = username

        if category is not None:
            channel.category = category.strip() or "pashto"

        if active is not None:
            channel.active = bool(active)

        await session.commit()

    if admin_id is not None:
        await record_admin_action(
            admin_id,
            "update_channel",
            str(channel_id),
        )

    return True


async def delete_channel(
    channel_id: int,
    admin_id: int | None = None,
):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Channel).where(Channel.id == int(channel_id))
        )
        channel = result.scalar_one_or_none()

        if channel is None:
            return False

        await session.delete(channel)
        await session.commit()

    if admin_id is not None:
        await record_admin_action(
            admin_id,
            "delete_channel",
            str(channel_id),
        )

    return True


async def get_settings():
    async with SessionLocal() as session:
        result = await session.execute(select(Setting))
        return {item.key: item.value for item in result.scalars().all()}


async def update_setting(
    key: str,
    value: str,
    admin_id: int | None = None,
):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Setting).where(Setting.key == key)
        )
        setting = result.scalar_one_or_none()

        if setting is None:
            setting = Setting(key=key, value=str(value))
            session.add(setting)
        else:
            setting.value = str(value)

        await session.commit()

    if admin_id is not None:
        await record_admin_action(
            admin_id,
            "update_setting",
            key,
        )

    return True


async def get_admin_actions(limit: int = 100):
    limit = max(1, min(int(limit), 500))

    async with SessionLocal() as session:
        result = await session.execute(
            select(AdminAction)
            .order_by(AdminAction.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()
