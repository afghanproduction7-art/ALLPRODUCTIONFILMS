
import logging
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import User, Film, Referral, Channel, AdminAction

logger = logging.getLogger(__name__)


async def get_admin_stats():
    async with SessionLocal() as session:
        users = await session.scalar(
            select(func.count()).select_from(User)
        )
        films = await session.scalar(
            select(func.count()).select_from(Film)
        )
        approved = await session.scalar(
            select(func.count()).select_from(Film).where(
                Film.approved.is_(True)
            )
        )
        pending = await session.scalar(
            select(func.count()).select_from(Film).where(
                Film.approved.is_(False)
            )
        )
        referrals = await session.scalar(
            select(func.count()).select_from(Referral)
        )
        channels = await session.scalar(
            select(func.count()).select_from(Channel)
        )

        return {
            "users": int(users or 0),
            "films": int(films or 0),
            "approved_films": int(approved or 0),
            "pending_films": int(pending or 0),
            "referrals": int(referrals or 0),
            "channels": int(channels or 0),
        }


async def get_users(limit=100, offset=0):
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    async with SessionLocal() as session:
        result = await session.execute(
            select(User)
            .order_by(User.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        users = result.scalars().all()

        return [
            {
                "telegram_id": user.telegram_id,
                "username": user.username or "",
                "first_name": user.first_name or "",
                "referral_count": int(user.referral_count or 0),
                "can_publish": bool(user.can_publish),
                "is_blocked": bool(user.is_blocked),
                "created_at": (
                    user.created_at.isoformat()
                    if user.created_at
                    else None
                ),
            }
            for user in users
        ]


async def get_user(telegram_id: int):
    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == int(telegram_id))
        )
        return result.scalar_one_or_none()


async def set_user_blocked(telegram_id: int, blocked: bool):
    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == int(telegram_id))
        )
        user = result.scalar_one_or_none()

        if user is None:
            return False

        user.is_blocked = bool(blocked)
        await session.commit()
        return True


async def set_user_publishing(telegram_id: int, allowed: bool):
    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == int(telegram_id))
        )
        user = result.scalar_one_or_none()

        if user is None:
            return False

        user.can_publish = bool(allowed)
        await session.commit()
        return True


async def log_admin_action(
    admin_id: int,
    action: str,
    target_id=None,
):
    async with SessionLocal() as session:
        record = AdminAction(
            admin_id=int(admin_id),
            action=str(action)[:200],
            target_id=(
                int(target_id)
                if target_id is not None
                else None
            ),
        )
        session.add(record)
        await session.commit()
        return True


async def get_admin_actions(limit=50):
    limit = max(1, min(int(limit), 200))

    async with SessionLocal() as session:
        result = await session.execute(
            select(AdminAction)
            .order_by(AdminAction.created_at.desc())
            .limit(limit)
        )
        actions = result.scalars().all()

        return [
            {
                "id": item.id,
                "admin_id": item.admin_id,
                "action": item.action,
                "target_id": item.target_id,
                "created_at": (
                    item.created_at.isoformat()
                    if item.created_at
                    else None
                ),
            }
            for item in actions
        ]


async def get_pending_films(limit=100):
    limit = max(1, min(int(limit), 500))

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film)
            .where(Film.approved.is_(False))
            .order_by(Film.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()


async def get_film_list(limit=100, offset=0):
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film)
            .order_by(Film.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return result.scalars().all()


async def get_channel_list(active_only=False):
    async with SessionLocal() as session:
        query = select(Channel).order_by(Channel.id.desc())

        if active_only:
            query = query.where(Channel.active.is_(True))

        result = await session.execute(query)
        return result.scalars().all()
