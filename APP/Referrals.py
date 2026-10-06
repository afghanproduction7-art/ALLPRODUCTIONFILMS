from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import User, Referral


async def get_or_create_user(
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
):
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
            )

            session.add(user)
            await session.commit()
            await session.refresh(user)

        else:
            changed = False

            if username is not None and user.username != username:
                user.username = username
                changed = True

            if first_name is not None and user.first_name != first_name:
                user.first_name = first_name
                changed = True

            if changed:
                await session.commit()

        return user


async def get_user(telegram_id: int):
    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )

        return result.scalar_one_or_none()


async def create_referral_link(bot, user_id: int):
    """
    Creates one permanent private invite link for the main channel.
    """

    user = await get_user(user_id)

    if user is None:
        user = await get_or_create_user(user_id)

    if user.referral_link:
        return user.referral_link

    invite = await bot.create_chat_invite_link(
        chat_id=settings.MAIN_CHANNEL,
        name=f"ref_{user_id}",
        creates_join_request=False,
    )

    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user_id)
        )

        db_user = result.scalar_one_or_none()

        if db_user:
            db_user.referral_link = invite.invite_link
            await session.commit()

    return invite.invite_link


async def process_channel_join(
    inviter_id: int,
    invited_id: int,
    invite_link: str | None = None,
):
    """
    Counts a referral only once.

    The invited Telegram account must be new in the referral table.
    """

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

        if inviter is None:
            inviter = User(
                telegram_id=inviter_id,
            )
            session.add(inviter)
            await session.flush()

        invited_result = await session.execute(
            select(User).where(
                User.telegram_id == invited_id
            )
        )

        invited = invited_result.scalar_one_or_none()

        if invited is None:
            invited = User(
                telegram_id=invited_id,
            )
            session.add(invited)
            await session.flush()

        referral = Referral(
            inviter_id=inviter_id,
            invited_id=invited_id,
            invite_link=invite_link,
        )

        session.add(referral)

        inviter.referral_count += 1

        target = settings.REFERRAL_TARGET

        if inviter.referral_count >= target:
            inviter.can_publish = True

        await session.commit()

        return True


async def get_referral_count(user_id: int) -> int:
    user = await get_user(user_id)

    if user is None:
        return 0

    return user.referral_count


async def can_publish(user_id: int) -> bool:
    user = await get_user(user_id)

    if user is None:
        return False

    return user.can_publish


async def get_referral_leaders(limit: int = 20):
    async with SessionLocal() as session:

        result = await session.execute(
            select(User)
            .where(User.referral_count > 0)
            .order_by(User.referral_count.desc())
            .limit(limit)
        )

        return result.scalars().all()
