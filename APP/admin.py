from sqlalchemy import select, func

from app.config import settings
from app.database import SessionLocal
from app.models import User, Film, Channel


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


async def get_statistics():
    async with SessionLocal() as session:

        users = await session.scalar(
            select(func.count(User.id))
        )

        publishers = await session.scalar(
            select(func.count(User.id)).where(
                User.can_publish.is_(True)
            )
        )

        films = await session.scalar(
            select(func.count(Film.id))
        )

        approved_films = await session.scalar(
            select(func.count(Film.id)).where(
                Film.approved.is_(True)
            )
        )

        channels = await session.scalar(
            select(func.count(Channel.id)).where(
                Channel.active.is_(True)
            )
        )

        return {
            "users": users or 0,
            "publishers": publishers or 0,
            "films": films or 0,
            "approved_films": approved_films or 0,
            "channels": channels or 0,
        }
