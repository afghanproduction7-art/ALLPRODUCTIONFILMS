
import logging
from contextlib import suppress

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import SessionLocal
from app.models import User, Referral

logger = logging.getLogger(__name__)


async def get_or_create_user(
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    referrer_id: int | None = None,
):
    telegram_id = int(telegram_id)

    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()

        if user is None:
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                referral_count=0,
                can_publish=False,
                is_blocked=False,
            )
            session.add(user)

        else:
            if username is not None:
                user.username = username
            if first_name is not None:
                user.first_name = first_name

        await session.commit()
        await session.refresh(user)

    if (
        referrer_id
        and int(referrer_id) != telegram_id
    ):
        await record_referral(
            inviter_id=int(referrer_id),
            invited_id=telegram_id,
        )

    return user


async def record_referral(
    inviter_id: int,
    invited_id: int,
    invite_link: str | None = None,
) -> bool:
    inviter_id = int(inviter_id)
    invited_id = int(invited_id)

    if inviter_id == invited_id:
        return False

    async with SessionLocal() as session:
        existing = await session.execute(
            select(Referral).where(
                Referral.invited_id == invited_id
            )
        )
        if existing.scalar_one_or_none():
            return False

        inviter_result = await session.execute(
            select(User).where(
                User.telegram_id == inviter_id
            )
        )
        inviter = inviter_result.scalar_one_or_none()

        invited_result = await session.execute(
            select(User).where(
                User.telegram_id == invited_id
            )
        )
        invited = invited_result.scalar_one_or_none()

        if inviter is None or invited is None:
            return False

        referral = Referral(
            inviter_id=inviter_id,
            invited_id=invited_id,
            invite_link=invite_link,
        )
        session.add(referral)

        try:
            inviter.referral_count = int(
                inviter.referral_count or 0
            ) + 1

            target = max(
                1, int(settings.REFERRAL_TARGET)
            )
            if inviter.referral_count >= target:
                inviter.can_publish = True

            await session.commit()
            return True

        except IntegrityError:
            await session.rollback()
            return False


async def process_channel_join(
    joined_user_id: int,
    invite_link: str | None = None,
) -> bool:
    if not invite_link:
        return False

    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(
                User.referral_link == invite_link
            )
        )
        inviter = result.scalar_one_or_none()

        if inviter is None:
            return False

        inviter_id = int(inviter.telegram_id)

    return await record_referral(
        inviter_id=inviter_id,
        invited_id=int(joined_user_id),
        invite_link=invite_link,
    )


async def get_referral_count(telegram_id: int) -> int:
    async with SessionLocal() as session:
        result = await session.execute(
            select(User.referral_count).where(
                User.telegram_id == int(telegram_id)
            )
        )
        count = result.scalar_one_or_none()
        return int(count or 0)


async def get_referral_leaders(limit: int = 10):
    limit = max(1, min(int(limit), 100))

    async with SessionLocal() as session:
        result = await session.execute(
            select(User)
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
                "username": user.username or "",
                "first_name": user.first_name or "",
                "referral_count": int(
                    user.referral_count or 0
                ),
            }
            for user in users
        ]


async def create_referral_link(
    telegram_id: int,
    bot: Bot | None = None,
    channel: str | None = None,
) -> str | None:
    telegram_id = int(telegram_id)
    own_bot = None

    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(
                User.telegram_id == telegram_id
            )
        )
        user = result.scalar_one_or_none()

        if user is None:
            return None

        if user.referral_link:
            return user.referral_link

    if bot is None:
        if not settings.BOT_TOKEN:
            return None
        own_bot = Bot(token=settings.BOT_TOKEN)
        bot = own_bot

    target_channel = (
        channel
        or settings.MAIN_CHANNEL
    )

    if not target_channel.startswith("@"):
        target_channel = f"@{target_channel}"

    try:
        invite = await bot.create_chat_invite_link(
            chat_id=target_channel,
            name=f"Referral {telegram_id}",
            creates_join_request=False,
        )
        link = invite.invite_link

        async with SessionLocal() as session:
            result = await session.execute(
                select(User).where(
                    User.telegram_id == telegram_id
                )
            )
            user = result.scalar_one_or_none()

            if user is None:
                return None

            if not user.referral_link:
                user.referral_link = link
                await session.commit()
                return link

            return user.referral_link

    except Exception:
        logger.exception(
            "Failed to create referral link for user %s",
            telegram_id,
        )
        return None

    finally:
        if own_bot is not None:
            with suppress(Exception):
                await own_bot.session.close()


async def update_publishing_permission(
    telegram_id: int,
) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(
                User.telegram_id == int(telegram_id)
            )
        )
        user = result.scalar_one_or_none()

        if user is None:
            return False

        target = max(1, int(settings.REFERRAL_TARGET))
        user.can_publish = (
            int(user.referral_count or 0) >= target
        )
        await session.commit()
        return bool(user.can_publish)
