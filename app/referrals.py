import logging
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models import User, Referral

logger = logging.getLogger(__name__)


async def get_or_create_user(
    telegram_id,
    username=None,
    first_name=None,
    referrer_id=None,
):
    telegram_id = int(telegram_id)

    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()

        if user:
            user.username = username
            user.first_name = first_name
            await session.commit()
            await session.refresh(user)
            return user

        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
        )
        session.add(user)

        try:
            await session.flush()

            if referrer_id is not None:
                referrer_id = int(referrer_id)

                if referrer_id != telegram_id:
                    result = await session.execute(
                        select(User).where(
                            User.telegram_id == referrer_id
                        )
                    )
                    referrer = result.scalar_one_or_none()

                    if referrer:
                        result = await session.execute(
                            select(Referral).where(
                                Referral.invited_id == telegram_id
                            )
                        )
                        existing = result.scalar_one_or_none()

                        if not existing:
                            session.add(
                                Referral(
                                    inviter_id=referrer_id,
                                    invited_id=telegram_id,
                                )
                            )
                            referrer.referral_count = (
                                int(referrer.referral_count or 0) + 1
                            )

            await session.commit()
            await session.refresh(user)
            return user

        except IntegrityError:
            await session.rollback()

            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            existing_user = result.scalar_one_or_none()

            if existing_user:
                return existing_user

            raise


async def process_channel_join(
    joined_user_id,
    invite_link=None,
):
    """
    د اصلي چینل د نوي غړي د ګډون ثبتول.
    joined_user_id باید د نوي شامل شوي غړي Telegram ID وي.
    """

    if not invite_link:
        return False

    joined_user_id = int(joined_user_id)

    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == joined_user_id)
        )
        joined_user = result.scalar_one_or_none()

        if joined_user is None:
            return False

        result = await session.execute(
            select(Referral).where(
                Referral.invited_id == joined_user_id
            )
        )
        existing_referral = result.scalar_one_or_none()

        if existing_referral:
            return False

        result = await session.execute(
            select(User).where(User.referral_link == invite_link)
        )
        inviter = result.scalar_one_or_none()

        if inviter is None or inviter.telegram_id == joined_user_id:
            return False

        referral = Referral(
            inviter_id=inviter.telegram_id,
            invited_id=joined_user_id,
            invite_link=invite_link,
        )
        session.add(referral)

        inviter.referral_count = int(inviter.referral_count or 0) + 1

        await session.commit()
        return True


async def get_referral_count(telegram_id):
    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(
                User.telegram_id == int(telegram_id)
            )
        )
        user = result.scalar_one_or_none()

        return int(user.referral_count or 0) if user else 0


async def get_referral_leaders(limit=20):
    limit = max(1, min(int(limit), 100))

    async with SessionLocal() as session:
        result = await session.execute(
            select(User)
            .where(User.referral_count > 0)
            .order_by(
                User.referral_count.desc(),
                User.telegram_id.asc(),
            )
            .limit(limit)
        )
        users = result.scalars().all()

        return [
            {
                "telegram_id": user.telegram_id,
                "username": user.username,
                "first_name": user.first_name,
                "referral_count": int(user.referral_count or 0),
            }
            for user in users
        ]


async def create_referral_link(telegram_id, link):
    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(
                User.telegram_id == int(telegram_id)
            )
        )
        user = result.scalar_one_or_none()

        if user is None:
            return False

        user.referral_link = link
        await session.commit()
        return True


async def update_publishing_permission(
    telegram_id,
    target=50,
):
    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(
                User.telegram_id == int(telegram_id)
            )
        )
        user = result.scalar_one_or_none()

        if user is None:
            return False

        user.can_publish = (
            int(user.referral_count or 0) >= int(target)
        )
        await session.commit()
        return bool(user.can_publish)
