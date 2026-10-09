import asyncio
import io
import logging
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.config import settings
from app.films import (
    calculate_image_hash,
    create_film,
    get_film,
)
from app.referrals import (
    can_publish,
    ensure_referral_link,
    get_or_create_user,
    get_referral_status,
    process_channel_join,
)
from app.settings_db import (
    get_access_channel,
    get_auto_approve,
    get_channels,
    get_main_channel,
)


logging.basicConfig(
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


bot = Bot(
    token=settings.BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode="HTML"
    ),
)

dp = Dispatcher()
router = Router()

dp.include_router(router)


# ---------------------------------------------------------
# Temporary publishing state
# ---------------------------------------------------------
#
# This is intentionally kept simple for now.
# The film itself is stored permanently in PostgreSQL.
#
# A later version can move the multi-step conversation
# state into PostgreSQL if needed.
#

pending_submissions: dict[int, dict[str, Any]] = {}


# ---------------------------------------------------------
# Keyboards
# ---------------------------------------------------------


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎬 Films",
                    web_app=None,
                    callback_data="open_films",
                ),
                InlineKeyboardButton(
                    text="🔎 Search",
                    callback_data="search_films",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👥 Referrals",
                    callback_data="referrals",
                ),
                InlineKeyboardButton(
                    text="📤 Publish Film",
                    callback_data="publish",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🆕 Latest Films",
                    callback_data="latest_films",
                ),
            ],
        ]
    )


def access_keyboard(
    channel: str,
) -> InlineKeyboardMarkup:
    username = channel.lstrip("@")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 Join Channel",
                    url=f"https://t.me/{username}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Check Access",
                    callback_data="check_access",
                )
            ],
        ]
    )


def publish_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📤 Start Publishing",
                    callback_data="start_publish",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Referral Status",
                    callback_data="referrals",
                )
            ],
        ]
    )


def film_keyboard(
    film_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎬 Open Film",
                    callback_data=f"film_{film_id}",
                )
            ]
        ]
    )


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------


async def check_channel_access(
    user_id: int,
) -> bool:
    """
    Check whether the Telegram user has joined
    the mandatory access channel.
    """

    channel = await get_access_channel()

    try:
        member = await bot.get_chat_member(
            chat_id=channel,
            user_id=user_id,
        )

        return member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        }

    except Exception as exc:
        logger.warning(
            "Access check failed for %s: %s",
            user_id,
            exc,
        )

        return False


async def require_channel_access(
    message: Message,
) -> bool:
    if message.from_user is None:
        return False

    user_id = message.from_user.id

    has_access = await check_channel_access(
        user_id
    )

    if has_access:
        return True

    channel = await get_access_channel()

    await message.answer(
        "🔒 <b>Channel Access Required</b>\n\n"
        "د روباټ د استعمال لپاره لومړی زموږ "
        "لازمي چینل ته Join شئ.\n\n"
        "له Join وروسته د <b>Check Access</b> "
        "تڼۍ ووهئ.",
        reply_markup=access_keyboard(channel),
    )

    return False


async def send_film_to_user(
    message: Message,
    film_id: int,
):
    film = await get_film(film_id)

    if film is None:
        await message.answer(
            "❌ دغه فلم پیدا نه شو."
        )
        return

    if film.poster_file_id:
        try:
            await message.answer_photo(
                photo=film.poster_file_id,
                caption=(
                    f"🎬 <b>{film.title}</b>\n\n"
                    f"📅 کال: {film.year or '-'}\n"
                    f"⚙️ کیفیت: {film.quality or '-'}\n"
                    f"🎭 ژانر: {film.genre or '-'}\n"
                    f"🔊 ژبه: {film.language or '-'}\n\n"
                    f"{film.description or ''}"
                ),
            )
        except Exception:
            logger.exception(
                "Could not send film poster."
            )

    try:
        await message.answer_document(
            document=film.video_file_id,
            caption=(
                f"🎬 <b>{film.title}</b>\n\n"
                "⬇️ ستاسو فلم تیار دی."
            ),
        )

    except Exception:
        logger.exception(
            "Could not send film file."
        )

        await message.answer(
            "❌ فلم لېږل کېدای نه شول."
        )


async def notify_admins(
    text: str,
):
    """
    Send a notification to all configured admins.
    """

    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
            )
        except Exception:
            logger.exception(
                "Could not notify admin %s",
                admin_id,
            )


# ---------------------------------------------------------
# START
# ---------------------------------------------------------


@router.message(CommandStart())
async def start_handler(
    message: Message,
):
    if message.from_user is None:
        return

    user = await get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
    )

    if user.is_blocked:
        await message.answer(
            "🚫 ستاسې حساب بند شوی دی."
        )
        return

    if not await require_channel_access(
        message
    ):
        return

    command = message.text or ""

    parts = command.split(
        maxsplit=1
    )

    start_parameter = (
        parts[1].strip()
        if len(parts) > 1
        else ""
    )

    # Film deep-link
    if start_parameter.startswith(
        "film_"
    ):
        try:
            film_id = int(
                start_parameter.replace(
                    "film_",
                    "",
                    1,
                )
            )

            await send_film_to_user(
                message,
                film_id,
            )

            return

        except ValueError:
            pass

    # Referral link generated on demand
    try:
        await ensure_referral_link(
            message.from_user.id
        )
    except Exception:
        logger.exception(
            "Could not create referral link."
        )

    await message.answer(
        "🎬 <b>ALL PRODUCTION FILMS</b>\n\n"
        "ښه راغلاست! 👋\n\n"
        "دلته کولای شئ فلمونه ولټوئ، "
        "فلمونه ترلاسه کړئ او د شرایطو له "
        "پوره کولو وروسته خپل فلمونه هم خپاره کړئ.",
        reply_markup=main_menu_keyboard(),
    )


# ---------------------------------------------------------
# ACCESS CHECK
# ---------------------------------------------------------


@router.callback_query(
    F.data == "check_access"
)
async def check_access_callback(
    callback: CallbackQuery,
):
    if callback.from_user is None:
        return

    has_access = await check_channel_access(
        callback.from_user.id
    )

    if has_access:
        await callback.answer(
            "✅ Access confirmed!",
            show_alert=False,
        )

        if callback.message:
            await callback.message.answer(
                "✅ Access تایید شو.\n\n"
                "اوس روباټ کارولی شئ.",
                reply_markup=main_menu_keyboard(),
            )

    else:
        await callback.answer(
            "❌ تاسو لا تراوسه چینل ته Join شوي نه یاست.",
            show_alert=True,
        )


# ---------------------------------------------------------
# REFERRALS
# ---------------------------------------------------------


@router.callback_query(
    F.data == "referrals"
)
async def referrals_callback(
    callback: CallbackQuery,
):
    if callback.from_user is None:
        return

    status = await get_referral_status(
        callback.from_user.id
    )

    link = status.get(
        "referral_link"
    )

    if not link:
        try:
            link = await ensure_referral_link(
                callback.from_user.id
            )
        except Exception:
            logger.exception(
                "Could not create referral link."
            )

    count = int(
        status.get(
            "referral_count",
            status.get("count", 0),
        )
    )

    target = int(
        status.get(
            "target",
            50,
        )
    )

    remaining = max(
        target - count,
        0,
    )

    can_publish_now = bool(
        status.get(
            "can_publish",
            False,
        )
    )

    text = (
        "👥 <b>Referral System</b>\n\n"
        f"👤 ستاسې دعوتونه: <b>{count}</b>\n"
        f"🎯 هدف: <b>{target}</b>\n"
        f"📊 پاتې: <b>{remaining}</b>\n\n"
    )

    if can_publish_now:
        text += (
            "✅ <b>تاسو د فلم خپرولو اجازه لرئ.</b>\n\n"
        )
    else:
        text += (
            "🔒 د فلم خپرولو لپاره لا "
            f"<b>{remaining}</b> دعوتونه پاتې دي.\n\n"
        )

    if link:
        text += (
            "🔗 <b>ستاسې شخصي Referral Link:</b>\n"
            f"<code>{link}</code>\n\n"
            "⚠️ یوازې هغه کسان حسابېږي چې "
            "ستاسې د همدې ځانګړي لینک له لارې "
            "چینل ته Join شي."
        )

    if callback.message:
        await callback.message.answer(
            text
        )

    await callback.answer()


# ---------------------------------------------------------
# PUBLISH
# ---------------------------------------------------------


@router.callback_query(
    F.data == "publish"
)
async def publish_callback(
    callback: CallbackQuery,
):
    if callback.from_user is None:
        return

    allowed = await can_publish(
        callback.from_user.id
    )

    if not allowed:
        status = await get_referral_status(
            callback.from_user.id
        )

        count = int(
            status.get(
                "referral_count",
                status.get("count", 0),
            )
        )

        target = int(
            status.get(
                "target",
                50,
            )
        )

        remaining = max(
            target - count,
            0,
        )

        await callback.answer(
            f"❌ لا {remaining} referral ته اړتیا ده.",
            show_alert=True,
        )

        return

    if callback.message:
        await callback.message.answer(
            "📤 <b>د فلم خپرولو سیستم</b>\n\n"
            "تاسو د فلم خپرولو اجازه لرئ.\n\n"
            "لومړی د <b>Start Publishing</b> "
            "تڼۍ ووهئ.",
            reply_markup=publish_keyboard(),
        )

    await callback.answer()


@router.callback_query(
    F.data == "start_publish"
)
async def start_publish_callback(
    callback: CallbackQuery,
):
    if callback.from_user is None:
        return

    allowed = await can_publish(
        callback.from_user.id
    )

    if not allowed:
        await callback.answer(
            "❌ تاسو لا د فلم خپرولو شرایط نه دي پوره کړي.",
            show_alert=True,
        )
        return

    pending_submissions[
        callback.from_user.id
    ] = {
        "step": "video",
    }

    if callback.message:
        await callback.message.answer(
            "📤 <b>لومړی فلم راولېږئ.</b>\n\n"
            "ویډیو یا Document دلته Send کړئ."
        )

    await callback.answer()


# ---------------------------------------------------------
# FILM UPLOAD
# ---------------------------------------------------------


@router.message(
    F.video
)
async def receive_video(
    message: Message,
):
    if message.from_user is None:
        return

    user_id = message.from_user.id

    submission = pending_submissions.get(
        user_id
    )

    if not submission:
        return

    if submission.get("step") != "video":
        return

    video = message.video

    submission["video_file_id"] = (
        video.file_id
    )

    submission["video_file_unique_id"] = (
        video.file_unique_id
    )

    submission["step"] = "title"

    await message.answer(
        "✅ فلم ترلاسه شو.\n\n"
        "📝 اوس د فلم <b>نوم</b> راولېږئ."
    )


@router.message(
    F.document
)
async def receive_document(
    message: Message,
):
    if message.from_user is None:
        return

    user_id = message.from_user.id

    submission = pending_submissions.get(
        user_id
    )

    if not submission:
        return

    if submission.get("step") != "video":
        return

    document = message.document

    submission["video_file_id"] = (
        document.file_id
    )

    submission["video_file_unique_id"] = (
        document.file_unique_id
    )

    submission["step"] = "title"

    await message.answer(
        "✅ فلم ترلاسه شو.\n\n"
        "📝 اوس د فلم <b>نوم</b> راولېږئ."
    )


# ---------------------------------------------------------
# FILM INFORMATION STEPS
# ---------------------------------------------------------


@router.message(
    F.text
)
async def film_text_steps(
    message: Message,
):
    if message.from_user is None:
        return

    user_id = message.from_user.id

    submission = pending_submissions.get(
        user_id
    )

    if not submission:
        return

    step = submission.get(
        "step"
    )

    text = (
        message.text or ""
    ).strip()

    if not text:
        return

    if step == "title":
        submission["title"] = text
        submission["step"] = "year"

        await message.answer(
            "📅 د فلم کال ولیکئ.\n\n"
            "مثال: <code>2024</code>"
        )
        return

    if step == "year":
        submission["year"] = text
        submission["step"] = "quality"

        await message.answer(
            "⚙️ د فلم کیفیت ولیکئ.\n\n"
            "مثال: <code>720p</code>"
        )
        return

    if step == "quality":
        submission["quality"] = text
        submission["step"] = "genre"

        await message.answer(
            "🎭 د فلم ژانر ولیکئ.\n\n"
            "مثال: <code>Action, Comedy</code>"
        )
        return

    if step == "genre":
        submission["genre"] = text
        submission["step"] = "language"

        await message.answer(
            "🔊 د فلم ژبه ولیکئ.\n\n"
            "مثال: <code>Pashto</code>"
        )
        return

    if step == "language":
        submission["language"] = text
        submission["step"] = "description"

        await message.answer(
            "📝 د فلم لنډ Description راولېږئ.\n\n"
            "که Description نه لرئ، <code>-</code> ولیکئ."
        )
        return

    if step == "description":
        submission["description"] = (
            None
            if text == "-"
            else text
        )

        submission["step"] = "poster"

        await message.answer(
            "🖼️ اوس د فلم Poster/Image راولېږئ."
        )
        return


# ---------------------------------------------------------
# POSTER
# ---------------------------------------------------------


@router.message(
    F.photo
)
async def receive_poster(
    message: Message,
):
    if message.from_user is None:
        return

    user_id = message.from_user.id

    submission = pending_submissions.get(
        user_id
    )

    if not submission:
        return

    if submission.get("step") != "poster":
        return

    photo = message.photo[-1]

    try:
        file = await bot.get_file(
            photo.file_id
        )

        buffer = io.BytesIO()

        await bot.download_file(
            file.file_path,
            buffer,
        )

        image_bytes = buffer.getvalue()

        poster_hash = calculate_image_hash(
            image_bytes
        )

    except Exception:
        logger.exception(
            "Could not process poster."
        )

        await message.answer(
            "❌ Poster پروسس نه شو. "
            "مهرباني وکړئ بیا یې راولېږئ."
        )

        return

    submission["poster_file_id"] = (
        photo.file_id
    )

    submission["poster_file_unique_id"] = (
        photo.file_unique_id
    )

    submission["poster_hash"] = poster_hash

    await finalize_film_submission(
        message
    )


# ---------------------------------------------------------
# FINALIZE FILM
# ---------------------------------------------------------


async def finalize_film_submission(
    message: Message,
):
    if message.from_user is None:
        return

    user_id = message.from_user.id

    submission = pending_submissions.get(
        user_id
    )

    if not submission:
        return

    try:
        auto_approve = await get_auto_approve()

        film = await create_film(
            title=submission["title"],
            video_file_id=submission[
                "video_file_id"
            ],
            video_file_unique_id=submission.get(
                "video_file_unique_id"
            ),
            uploader_id=user_id,
            year=submission.get(
                "year"
            ),
            quality=submission.get(
                "quality"
            ),
            genre=submission.get(
                "genre"
            ),
            language=submission.get(
                "language"
            ),
            description=submission.get(
                "description"
            ),
            category="general",
            poster_file_id=submission.get(
                "poster_file_id"
            ),
            poster_file_unique_id=submission.get(
                "poster_file_unique_id"
            ),
            poster_hash=submission.get(
                "poster_hash"
            ),
            approved=auto_approve,
            official=False,
        )

        pending_submissions.pop(
            user_id,
            None,
        )

        if auto_approve:
            await message.answer(
                "🎉 <b>فلم په بریالیتوب ثبت او Published شو!</b>\n\n"
                f"🎬 <b>{film.title}</b>\n"
                f"🆔 Film ID: <code>{film.id}</code>"
            )

            await notify_admins(
                "🎬 <b>New Film Published</b>\n\n"
                f"Title: {film.title}\n"
                f"Film ID: {film.id}\n"
                f"Uploader ID: <code>{user_id}</code>"
            )

        else:
            await message.answer(
                "✅ فلم مو ترلاسه کړ.\n\n"
                "⏳ اوس د Admin د تایید په تمه دی.\n"
                f"🆔 Film ID: <code>{film.id}</code>"
            )

            await notify_admins(
                "📥 <b>New Film Pending Approval</b>\n\n"
                f"Title: {film.title}\n"
                f"Film ID: {film.id}\n"
                f"Uploader ID: <code>{user_id}</code>"
            )

    except Exception:
        logger.exception(
            "Could not save film."
        )

        await message.answer(
            "❌ فلم ثبت نه شو.\n\n"
            "مهرباني وکړئ بیا هڅه وکړئ."
        )


# ---------------------------------------------------------
# FILM CALLBACK
# ---------------------------------------------------------


@router.callback_query(
    F.data.startswith("film_")
)
async def film_callback(
    callback: CallbackQuery,
):
    if callback.from_user is None:
        return

    try:
        film_id = int(
            callback.data.split(
                "_",
                1,
            )[1]
        )

    except (ValueError, IndexError):
        await callback.answer(
            "❌ Invalid film ID.",
            show_alert=True,
        )
        return

    if callback.message:
        await send_film_to_user(
            callback.message,
            film_id,
        )

    await callback.answer()


# ---------------------------------------------------------
# SEARCH
# ---------------------------------------------------------


@router.callback_query(
    F.data == "search_films"
)
async def search_callback(
    callback: CallbackQuery,
):
    await callback.answer(
        "🔎 Search د Mini App له لارې وکړئ.",
        show_alert=True,
    )


# ---------------------------------------------------------
# LATEST
# ---------------------------------------------------------


@router.callback_query(
    F.data == "latest_films"
)
async def latest_callback(
    callback: CallbackQuery,
):
    await callback.answer(
        "🆕 Latest Films د Mini App له لارې وګورئ.",
        show_alert=True,
    )


# ---------------------------------------------------------
# OPEN MINI APP
# ---------------------------------------------------------


@router.callback_query(
    F.data == "open_films"
)
async def open_films_callback(
    callback: CallbackQuery,
):
    await callback.answer(
        "🎬 Films د Mini App له لارې خلاص کړئ.",
        show_alert=True,
    )


# ---------------------------------------------------------
# CHANNEL JOIN TRACKING
# ---------------------------------------------------------


@router.chat_member()
async def channel_member_update(
    event,
):
    """
    Telegram sends chat-member updates when the bot
    has the required administrator permissions.

    Only joins containing a tracked invite link are
    counted as referrals.
    """

    try:
        chat = event.chat

        main_channel = await get_main_channel()

        if (
            chat.username
            and main_channel.startswith("@")
            and chat.username.lower()
            != main_channel.lstrip("@").lower()
        ):
            return

        new_member = event.new_chat_member

        if new_member.status not in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        }:
            return

        invited_id = new_member.user.id

        invite_link = None

        if event.invite_link:
            invite_link = (
                event.invite_link.invite_link
            )

        result = await process_channel_join(
            inviter_id=None,
            invited_id=invited_id,
            invite_link=invite_link,
        )

        if result.get("success"):
            inviter_id = result.get(
                "inviter_id"
            )

            count = result.get(
                "referral_count",
                0,
            )

            target = result.get(
                "target",
                50,
            )

            await bot.send_message(
                chat_id=inviter_id,
                text=(
                    "🎉 <b>New Referral!</b>\n\n"
                    "یو نوی کس ستاسې د لینک له لارې "
                    "چینل ته Join شو.\n\n"
                    f"👥 دعوتونه: <b>{count}/{target}</b>"
                ),
            )

            if result.get(
                "can_publish"
            ):
                await bot.send_message(
                    chat_id=inviter_id,
                    text=(
                        "🎉 <b>مبارک!</b>\n\n"
                        "تاسو د 50 referrals شرط "
                        "پوره کړ.\n\n"
                        "📤 اوس کولای شئ خپل فلمونه "
                        "خپاره کړئ."
                    ),
                )

    except Exception:
        logger.exception(
            "Channel member update failed."
        )


# ---------------------------------------------------------
# ADMIN COMMAND
# ---------------------------------------------------------


@router.message(
    Command("admin")
)
async def admin_command(
    message: Message,
):
    if message.from_user is None:
        return

    if message.from_user.id not in settings.admin_ids:
        await message.answer(
            "🚫 Access denied."
        )
        return

    await message.answer(
        "🛠️ <b>Admin Panel</b>\n\n"
        "د Admin Panel د خلاصولو لپاره "
        "Web App وکاروئ."
    )


# ---------------------------------------------------------
# ERROR HANDLER
# ---------------------------------------------------------


@router.errors()
async def global_error_handler(
    event,
):
    logger.exception(
        "Telegram update error: %s",
        event.exception,
    )


# ---------------------------------------------------------
# BOT START
# ---------------------------------------------------------


async def start_bot():
    """
    Start Telegram polling.

    Any previous webhook is removed because
    this deployment uses polling.
    """

    logger.info(
        "Starting Telegram bot..."
    )

    try:
        await bot.delete_webhook(
            drop_pending_updates=False
        )

        await dp.start_polling(
            bot
        )

    except asyncio.CancelledError:
        logger.info(
            "Bot polling cancelled."
        )
        raise

    except Exception:
        logger.exception(
            "Bot polling stopped."
        )
        raise


async def stop_bot():
    """
    Close the Telegram bot session.
    """

    try:
        await bot.session.close()

    except Exception:
        logger.exception(
            "Could not close bot session."
    )
