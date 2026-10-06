from sqlalchemy import select

from aiogram import Bot
from aiogram.utils.deep_linking import create_start_link

from app.config import settings
from app.database import SessionLocal
from app.models import User, Referral
from app.settings_db import (
    get_referral_target,
    get_main_channel,
)


# =========================================================
# USER
# =========================================================

async def get_user(
    telegram_id: int,
):

    async with SessionLocal() as session:

        result = await session.execute(
            select(User).where(
                User.telegram_id == telegram_id
            )
        )

        return result.scalar_one_or_none()


async def get_or_create_user(
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
):

    async with SessionLocal() as session:

        result = await session.execute(
            select(User).where(
                User.telegram_id == telegram_id
            )
        )

        user = result.scalar_one_or_none()

        if user:

            changed = False

            if username is not None:
                if user.username != username:
                    user.username = username
                    changed = True

            if first_name is not None:
                if user.first_name != first_name:
                    user.first_name = first_name
                    changed = True

            if changed:
                await session.commit()

            return user

        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            referral_count=0,
            can_publish=False,
            is_blocked=False,
        )

        session.add(user)

        await session.commit()

        await session.refresh(user)

        return user


# =========================================================
# REFERRAL LINK
# =========================================================

async def create_referral_link(
    user_id: int,
):

    """
    Creates a permanent Telegram invite link
    for the currently configured main channel.

    IMPORTANT:
    The bot must have permission to invite users
    via invite links in the main channel.
    """

    main_channel = await get_main_channel()

    # Import here to avoid circular import.
    from app.bot import bot

    invite = await bot.create_chat_invite_link(
        chat_id=main_channel,
        name=f"ref_{user_id}",
        creates_join_request=False,
    )

    link = invite.invite_link

    async with SessionLocal() as session:

        result = await session.execute(
            select(User).where(
                User.telegram_id == user_id
            )
        )

        user = result.scalar_one_or_none()

        if user:

            user.referral_link = link

            await session.commit()

    return link


# =========================================================
# REFERRAL COUNT
# =========================================================

async def get_referral_count(
    telegram_id: int,
) -> int:

    async with SessionLocal() as session:

        result = await session.execute(
            select(User.referral_count).where(
                User.telegram_id == telegram_id
            )
        )

        count = result.scalar_one_or_none()

        return int(
            count or 0
        )


# =========================================================
# CAN PUBLISH
# =========================================================

async def can_publish(
    telegram_id: int,
) -> bool:

    user = await get_user(
        telegram_id
    )

    if not user:
        return False

    target = await get_referral_target()

    return (
        user.referral_count >= target
        or user.can_publish
    )


# =========================================================
# PROCESS CHANNEL JOIN
# =========================================================

async def process_channel_join(
    inviter_id: int | None,
    invited_id: int,
    invite_link: str | None = None,
):
    """
    Register a successful referral.

    Rules:
    - Each Telegram account can count only once.
    - Direct joins without invite_link do not count.
    - Self-referral is rejected.
    - Only an existing inviter can receive the referral.
    - Referral target is read dynamically from DB.
    """

    if not invite_link:

        return {
            "success": False,
            "reason": "no_invite_link",
        }

    async with SessionLocal() as session:

        # -------------------------------------------------
        # Check if invited user already counted
        # -------------------------------------------------

        existing = await session.execute(
            select(Referral).where(
                Referral.invited_id == invited_id
            )
        )

        already_referred = (
            existing.scalar_one_or_none()
        )

        if already_referred:

            return {
                "success": False,
                "reason": "already_counted",
                "inviter_id":
                    already_referred.inviter_id,
            }

        # -------------------------------------------------
        # Find inviter by stored referral link
        # -------------------------------------------------

        result = await session.execute(
            select(User).where(
                User.referral_link == invite_link
            )
        )

        inviter = (
            result.scalar_one_or_none()
        )

        if inviter is None:

            return {
                "success": False,
                "reason": "invite_link_not_found",
            }

        inviter_id = inviter.telegram_id

        # -------------------------------------------------
        # Self referral
        # -------------------------------------------------

        if inviter_id == invited_id:

            return {
                "success": False,
                "reason": "self_referral",
            }

        # -------------------------------------------------
        # Make sure invited user exists
        # -------------------------------------------------

        invited_result = await session.execute(
            select(User).where(
                User.telegram_id == invited_id
            )
        )

        invited_user = (
            invited_result.scalar_one_or_none()
        )

        if invited_user is None:

            invited_user = User(
                telegram_id=invited_id,
                referral_count=0,
                can_publish=False,
                is_blocked=False,
            )

            session.add(
                invited_user
            )

            await session.flush()

        # -------------------------------------------------
        # Create referral record
        # -------------------------------------------------

        referral = Referral(
            inviter_id=inviter_id,
            invited_id=invited_id,
            invite_link=invite_link,
        )

        session.add(
            referral
        )

        # -------------------------------------------------
        # Increase inviter count
        # -------------------------------------------------

        inviter.referral_count = (
            int(inviter.referral_count or 0)
            + 1
        )

        # -------------------------------------------------
        # Dynamic referral target
        # -------------------------------------------------

        target = await get_referral_target()

        if inviter.referral_count >= target:

            inviter.can_publish = True

        await session.commit()

        return {
            "success": True,
            "reason": "referral_counted",
            "inviter_id": inviter_id,
            "invited_id": invited_id,
            "referral_count":
                inviter.referral_count,
            "target": target,
            "can_publish":
                inviter.can_publish,
        }


# =========================================================
# REFERRAL LEADERS
# =========================================================

async def get_referral_leaders(
    limit: int = 50,
):

    limit = max(
        1,
        min(
            int(limit),
            100,
        ),
    )

    async with SessionLocal() as session:

        result = await session.execute(
            select(User)
            .where(
                User.referral_count > 0
            )
            .order_by(
                User.referral_count.desc(),
                User.created_at.asc(),
            )
            .limit(limit)
        )

        return result.scalars().all()


# =========================================================
# CREATE / ENSURE USER REFERRAL LINK
# =========================================================

async def ensure_referral_link(
    user_id: int,
):

    user = await get_user(
        user_id
    )

    if not user:
        return None

    if user.referral_link:

        return user.referral_link

    return await create_referral_link(
        user_id
    )


# =========================================================
# REFERRAL STATUS
# =========================================================

async def get_referral_status(
    telegram_id: int,
):

    user = await get_user(
        telegram_id
    )

    target = await get_referral_target()

    if not user:

        return {
            "count": 0,
            "target": target,
            "remaining": target,
            "can_publish": False,
            "referral_link": None,
        }

    count = int(
        user.referral_count or 0
    )

    return {
        "count": count,
        "target": target,
        "remaining": max(
            target - count,
            0,
        ),
        "can_publish": (
            count >= target
            or user.can_publish
        ),
        "referral_link":
            user.referral_link,
    }


# =========================================================
# ADMIN: RECALCULATE PUBLISH PERMISSIONS
# =========================================================

async def refresh_publish_permissions():

    """
    Re-check all users against the current
    referral target.

    Useful when Admin changes the target
    from 50 to another number.
    """

    target = await get_referral_target()

    async with SessionLocal() as session:

        result = await session.execute(
            select(User)
        )

        users = result.scalars().all()

        changed = 0

        for user in users:

            should_publish = (
                int(user.referral_count or 0)
                >= target
            )

            if user.can_publish != should_publish:

                user.can_publish = (
                    should_publish
                )

                changed += 1

        await session.commit()

        return {
            "target": target,
            "updated_users": changed,
            }
