import asyncio
import logging
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.deep_linking import create_start_link
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import User, Film
from app.referrals import (
    get_or_create_user,
    get_user,
    get_referral_count,
    can_publish,
    get_referral_leaders,
    create_referral_link,
    process_channel_join,
)
from app.films import (
    create_film,
    search_films,
    get_film,
)
from app.admin import is_admin, get_statistics
from app.settings_db import (
    get_referral_target,
    get_main_channel,
    get_access_channel,
    get_auto_approve,
)

logging.basicConfig(
    level=logging.INFO
)

logger = logging.getLogger(
    "all-production-bot"
)


# =========================================================
# BOT
# =========================================================

bot = Bot(
    token=settings.BOT_TOKEN,
    parse_mode=ParseMode.HTML,
)

dp = Dispatcher()

router = Router()

dp.include_router(router)


# =========================================================
# TEMPORARY PUBLISHING SESSIONS
# =========================================================

pending_submissions: dict[int, dict[str, Any]] = {}


# =========================================================
# HELPERS
# =========================================================

def normalize_channel(value: str) -> str:

    value = str(value or "").strip()

    if value and not value.startswith("@"):
        value = "@" + value

    return value.lower()


def main_menu_keyboard() -> InlineKeyboardMarkup:

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔍 د فلم لټون",
                    callback_data="search_help",
                ),
                InlineKeyboardButton(
                    text="🆕 نوي فلمونه",
                    callback_data="latest_films",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎙️ پښتو فلمونه",
                    callback_data="pashto_films",
                ),
                InlineKeyboardButton(
                    text="⭐ زموږ فلمونه",
                    callback_data="official_films",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👥 دعوتونه",
                    callback_data="referrals",
                ),
                InlineKeyboardButton(
                    text="🏆 مشران",
                    callback_data="leaders",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎬 فلم خپرول",
                    callback_data="publish",
                ),
            ],
        ]
    )


async def access_keyboard() -> InlineKeyboardMarkup:

    channel = await get_access_channel()

    username = channel.lstrip("@")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 چینل Join کړئ",
                    url=f"https://t.me/{username}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ ما Join کړی",
                    callback_data="check_access",
                )
            ],
        ]
    )


async def user_has_access(
    user_id: int,
) -> bool:

    channel = await get_access_channel()

    try:

        member = await bot.get_chat_member(
            chat_id=channel,
            user_id=user_id,
        )

        return member.status in {
            ChatMemberStatus.CREATOR,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.RESTRICTED,
        }

    except Exception as exc:

        logger.warning(
            "Access check failed: %s",
            exc,
        )

        return False


async def ensure_access(
    message: Message,
) -> bool:

    if not message.from_user:
        return False

    allowed = await user_has_access(
        message.from_user.id
    )

    if allowed:
        return True

    await message.answer(
        "🔐 <b>لومړی چینل Join کړئ</b>\n\n"
        "د روباټ د کارولو لپاره باید لومړی زموږ اړین "
        "چینل Join کړئ.\n\n"
        "له Join وروسته لاندې تڼۍ کېکاږئ:",
        reply_markup=await access_keyboard(),
    )

    return False


async def ensure_callback_access(
    callback: CallbackQuery,
) -> bool:

    if not callback.from_user:
        return False

    allowed = await user_has_access(
        callback.from_user.id
    )

    if allowed:
        return True

    await callback.answer(
        "لومړی اړین چینل Join کړئ.",
        show_alert=True,
    )

    try:
        await callback.message.edit_text(
            "🔐 <b>لومړی چینل Join کړئ</b>\n\n"
            "لومړی زموږ اړین چینل Join کړئ، "
            "بیا لاندې تڼۍ کېکاږئ:",
            reply_markup=await access_keyboard(),
        )

    except Exception:
        pass

    return False


async def get_or_register(
    user_id: int,
    username: str | None,
    first_name: str | None,
):

    return await get_or_create_user(
        telegram_id=user_id,
        username=username,
        first_name=first_name,
    )


async def make_user_referral_link(
    user_id: int,
) -> str:

    user = await get_user(
        user_id
    )

    if user and user.referral_link:
        return user.referral_link

    try:

        link = await create_referral_link(
            user_id
        )

        return link

    except Exception as exc:

        logger.exception(
            "Referral link creation failed: %s",
            exc,
        )

        # Fallback to Telegram bot deep link.
        return await create_start_link(
            bot,
            f"ref_{user_id}",
            encode=False,
        )


async def send_main_menu(
    message: Message,
):

    user = message.from_user

    if not user:
        return

    db_user = await get_or_register(
        user.id,
        user.username,
        user.first_name,
    )

    target = await get_referral_target()

    count = await get_referral_count(
        user.id
    )

    publish = await can_publish(
        user.id
    )

    status_text = (
        "✅ <b>تاسو د فلم خپرولو اجازه لرئ.</b>"
        if publish
        else (
            f"🔒 <b>فلم خپرول بند دي.</b>\n"
            f"👥 دعوتونه: <b>{count}/{target}</b>"
        )
    )

    await message.answer(
        f"🎬 <b>ALL PRODUCTION FILMS</b>\n\n"
        f"سلام <b>{user.first_name or 'ملګري'}</b>! 👋\n\n"
        f"دلته کولای شئ پښتو فلمونه ولټوئ، "
        f"فلمونه ترلاسه کړئ او د شرط پوره کولو وروسته "
        f"خپل فلمونه هم خپاره کړئ.\n\n"
        f"{status_text}",
        reply_markup=main_menu_keyboard(),
    )


# =========================================================
# START
# =========================================================

@router.message(CommandStart())
async def start_handler(
    message: Message,
):

    if not message.from_user:
        return

    user = message.from_user

    await get_or_register(
        user.id,
        user.username,
        user.first_name,
    )

    if not await ensure_access(
        message
    ):
        return

    args = ""

    if message.text:

        parts = message.text.split(
            maxsplit=1
        )

        if len(parts) == 2:
            args = parts[1].strip()

    # ---------------------------------------------
    # FILM DEEP LINK
    # ---------------------------------------------

    if args.startswith("film_"):

        try:

            film_id = int(
                args.replace(
                    "film_",
                    "",
                    1,
                )
            )

        except ValueError:

            film_id = 0

        if film_id:

            film = await get_film(
                film_id
            )

            if film:

                await send_film(
                    message,
                    film,
                )

                return

    # ---------------------------------------------
    # REFERRAL DEEP LINK
    # ---------------------------------------------

    if args.startswith("ref_"):

        try:

            inviter_id = int(
                args.replace(
                    "ref_",
                    "",
                    1,
                )
            )

            if inviter_id != user.id:

                await register_referral_deep_link(
                    inviter_id,
                    user.id,
                )

        except Exception as exc:

            logger.warning(
                "Referral deep-link error: %s",
                exc,
            )

    await send_main_menu(
        message
    )


# =========================================================
# REFERRAL DEEP LINK FALLBACK
# =========================================================

async def register_referral_deep_link(
    inviter_id: int,
    invited_id: int,
):

    async with SessionLocal() as session:

        inviter_result = await session.execute(
            select(User).where(
                User.telegram_id == inviter_id
            )
        )

        inviter = (
            inviter_result.scalar_one_or_none()
        )

        invited_result = await session.execute(
            select(User).where(
                User.telegram_id == invited_id
            )
        )

        invited = (
            invited_result.scalar_one_or_none()
        )

        if not inviter or not invited:
            return

        # The real referral is counted when the
        # invited user joins the main channel.
        #
        # This function intentionally does NOT
        # increment referral_count.


# =========================================================
# ACCESS CHECK CALLBACK
# =========================================================

@router.callback_query(
    F.data == "check_access"
)
async def check_access_callback(
    callback: CallbackQuery,
):

    if not callback.from_user:
        return

    allowed = await user_has_access(
        callback.from_user.id
    )

    if not allowed:

        await callback.answer(
            "❌ لا هم چینل Join شوی نه یاست.",
            show_alert=True,
        )

        return

    await callback.answer(
        "✅ Access فعال شو!",
        show_alert=False,
    )

    try:

        await callback.message.delete()

    except Exception:
        pass

    await send_main_menu(
        callback.message
    )


# =========================================================
# CALLBACK MAIN MENU
# =========================================================

@router.callback_query(
    F.data == "search_help"
)
async def search_help_callback(
    callback: CallbackQuery,
):

    if not await ensure_callback_access(
        callback
    ):
        return

    await callback.answer()

    await callback.message.answer(
        "🔍 <b>د فلم لټون</b>\n\n"
        "د فلم نوم روباټ ته ولیکئ.\n\n"
        "مثال:\n"
        "<code>Avengers</code>\n"
        "<code>جب هېري ميټ سجل</code>"
    )


@router.callback_query(
    F.data == "latest_films"
)
async def latest_films_callback(
    callback: CallbackQuery,
):

    if not await ensure_callback_access(
        callback
    ):
        return

    await callback.answer()

    async with SessionLocal() as session:

        result = await session.execute(
            select(Film)
            .where(
                Film.approved.is_(True)
            )
            .order_by(
                Film.created_at.desc()
            )
            .limit(20)
        )

        films = result.scalars().all()

    if not films:

        await callback.message.answer(
            "🎬 تر اوسه کوم فلم نه دی اضافه شوی."
        )

        return

    await callback.message.answer(
        "🆕 <b>وروستي فلمونه</b>"
    )

    for film in films:

        await send_film(
            callback.message,
            film,
            compact=True,
        )


@router.callback_query(
    F.data == "pashto_films"
)
async def pashto_films_callback(
    callback: CallbackQuery,
):

    if not await ensure_callback_access(
        callback
    ):
        return

    await callback.answer()

    async with SessionLocal() as session:

        result = await session.execute(
            select(Film)
            .where(
                Film.approved.is_(True),
                Film.category == "pashto",
            )
            .order_by(
                Film.created_at.desc()
            )
            .limit(20)
        )

        films = result.scalars().all()

    if not films:

        await callback.message.answer(
            "🎙️ تر اوسه پښتو فلمونه نشته."
        )

        return

    await callback.message.answer(
        "🎙️ <b>پښتو فلمونه</b>"
    )

    for film in films:

        await send_film(
            callback.message,
            film,
            compact=True,
        )


@router.callback_query(
    F.data == "official_films"
)
async def official_films_callback(
    callback: CallbackQuery,
):

    if not await ensure_callback_access(
        callback
    ):
        return

    await callback.answer()

    async with SessionLocal() as session:

        result = await session.execute(
            select(Film)
            .where(
                Film.approved.is_(True),
                Film.official.is_(True),
            )
            .order_by(
                Film.created_at.desc()
            )
            .limit(20)
        )

        films = result.scalars().all()

    if not films:

        await callback.message.answer(
            "⭐ تر اوسه رسمي فلمونه نشته."
        )

        return

    await callback.message.answer(
        "⭐ <b>زموږ فلمونه</b>"
    )

    for film in films:

        await send_film(
            callback.message,
            film,
            compact=True,
        )


# =========================================================
# REFERRALS
# =========================================================

@router.callback_query(
    F.data == "referrals"
)
async def referrals_callback(
    callback: CallbackQuery,
):

    if not await ensure_callback_access(
        callback
    ):
        return

    await callback.answer()

    user = callback.from_user

    await get_or_register(
        user.id,
        user.username,
        user.first_name,
    )

    count = await get_referral_count(
        user.id
    )

    target = await get_referral_target()

    allowed = await can_publish(
        user.id
    )

    link = await make_user_referral_link(
        user.id
    )

    remaining = max(
        target - count,
        0,
    )

    status = (
        "🎉 <b>مبارک!</b> تاسو د فلم خپرولو اجازه لرئ."
        if allowed
        else (
            f"🔒 لا <b>{remaining}</b> دعوتونه پاتې دي."
        )
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 زما لینک",
                    callback_data="my_referral_link",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏆 د دعوتونو مشران",
                    callback_data="leaders",
                )
            ],
        ]
    )

    await callback.message.answer(
        f"👥 <b>د ملګرو دعوت</b>\n\n"
        f"📊 ستا دعوتونه: <b>{count}/{target}</b>\n"
        f"⏳ پاتې: <b>{remaining}</b>\n\n"
        f"{status}\n\n"
        f"🔗 <code>{link}</code>",
        reply_markup=keyboard,
    )


@router.callback_query(
    F.data == "my_referral_link"
)
async def my_referral_link_callback(
    callback: CallbackQuery,
):

    if not await ensure_callback_access(
        callback
    ):
        return

    await callback.answer()

    link = await make_user_referral_link(
        callback.from_user.id
    )

    await callback.message.answer(
        "🔗 <b>ستاسو ځانګړی دعوت لینک:</b>\n\n"
        f"<code>{link}</code>\n\n"
        "📢 دا لینک خپلو ملګرو ته واستوئ."
    )


@router.callback_query(
    F.data == "leaders"
)
async def leaders_callback(
    callback: CallbackQuery,
):

    if not await ensure_callback_access(
        callback
    ):
        return

    await callback.answer()

    leaders = await get_referral_leaders(
        20
    )

    if not leaders:

        await callback.message.answer(
            "🏆 تر اوسه کوم دعوت کوونکی نشته."
        )

        return

    lines = [
        "🏆 <b>د دعوتونو مشران</b>\n"
    ]

    for index, user in enumerate(
        leaders,
        start=1,
    ):

        name = (
            user.first_name
            or user.username
            or str(user.telegram_id)
        )

        lines.append(
            f"{index}. <b>{name}</b> — "
            f"{user.referral_count} 👥"
        )

    await callback.message.answer(
        "\n".join(lines)
    )


# =========================================================
# PUBLISH
# =========================================================

@router.callback_query(
    F.data == "publish"
)
async def publish_callback(
    callback: CallbackQuery,
):

    if not await ensure_callback_access(
        callback
    ):
        return

    await callback.answer()

    user_id = callback.from_user.id

    allowed = await can_publish(
        user_id
    )

    if not allowed:

        count = await get_referral_count(
            user_id
        )

        target = await get_referral_target()

        await callback.message.answer(
            "🔒 <b>فلم خپرول لا نه دي فعال.</b>\n\n"
            f"👥 ستا دعوتونه: <b>{count}/{target}</b>\n"
            f"📌 پاتې دعوتونه: "
            f"<b>{max(target - count, 0)}</b>"
        )

        return

    pending_submissions[user_id] = {
        "step": "video"
    }

    await callback.message.answer(
        "🎬 <b>د فلم خپرولو سیستم</b>\n\n"
        "لومړی خپل فلم د <b>Video</b> په توګه راولېږئ.\n\n"
        "وروسته به د فلم معلومات درڅخه وغواړم.\n\n"
        "❌ د لغوه کولو لپاره /cancel ولیکئ."
    )


@router.message(
    Command("cancel")
)
async def cancel_handler(
    message: Message,
):

    user_id = (
        message.from_user.id
        if message.from_user
        else 0
    )

    pending_submissions.pop(
        user_id,
        None,
    )

    await message.answer(
        "❌ د فلم خپرولو پروسه لغوه شوه."
    )


# =========================================================
# VIDEO SUBMISSION
# =========================================================

@router.message(
    F.video
)
async def video_handler(
    message: Message,
):

    if not message.from_user:
        return

    if not await ensure_access(
        message
    ):
        return

    user_id = message.from_user.id

    allowed = await can_publish(
        user_id
    )

    if not allowed:

        await message.answer(
            "🔒 تاسو لا د فلم خپرولو اجازه نه لرئ."
        )

        return

    session = pending_submissions.get(
        user_id
    )

    if not session:

        await message.answer(
            "ℹ️ د فلم خپرولو لپاره لومړی "
            "د <b>🎬 فلم خپرول</b> تڼۍ وکاروئ."
        )

        return

    if session.get("step") != "video":

        await message.answer(
            "ℹ️ اوس د فلم معلوماتو ته اړتیا ده."
        )

        return

    video = message.video

    session["video_file_id"] = (
        video.file_id
    )

    session["video_unique_id"] = (
        video.file_unique_id
    )

    session["step"] = "title"

    await message.answer(
        "✅ فلم ترلاسه شو.\n\n"
        "📝 اوس د فلم <b>نوم</b> راولېږئ."
    )


# =========================================================
# DOCUMENT SUBMISSION
# =========================================================

@router.message(
    F.document
)
async def document_handler(
    message: Message,
):

    if not message.from_user:
        return

    if not await ensure_access(
        message
    ):
        return

    user_id = message.from_user.id

    allowed = await can_publish(
        user_id
    )

    if not allowed:

        await message.answer(
            "🔒 تاسو لا د فلم خپرولو اجازه نه لرئ."
        )

        return

    session = pending_submissions.get(
        user_id
    )

    if not session:

        await message.answer(
            "ℹ️ لومړی د فلم خپرولو سیستم فعال کړئ."
        )

        return

    if session.get("step") != "video":

        await message.answer(
            "ℹ️ اوس د فلم نوم ته اړتیا ده."
        )

        return

    document = message.document

    # This version accepts documents as well.
    # The Film model stores Telegram file_id.
    # Delivery will be handled as a Telegram file.

    session["video_file_id"] = (
        document.file_id
    )

    session["video_unique_id"] = (
        document.file_unique_id
    )

    session["media_type"] = "document"

    session["step"] = "title"

    await message.answer(
        "✅ فایل ترلاسه شو.\n\n"
        "📝 اوس د فلم <b>نوم</b> راولېږئ."
    )


# =========================================================
# PUBLISH TEXT FLOW
# =========================================================

@router.message(
    F.text
)
async def text_handler(
    message: Message,
):

    if not message.from_user:
        return

    user_id = message.from_user.id

    # Commands are handled by command filters.
    if message.text.startswith("/"):
        return

    if not await ensure_access(
        message
    ):
        return

    session = pending_submissions.get(
        user_id
    )

    # ---------------------------------------------
    # NO PUBLISHING SESSION = SEARCH
    # ---------------------------------------------

    if not session:

        query = message.text.strip()

        if not query:
            return

        films = await search_films(
            query
        )

        if not films:

            await message.answer(
                "🔍 <b>فلم پیدا نه شو.</b>\n\n"
                "د فلم نوم په بله بڼه هم وازمویئ."
            )

            return

        await message.answer(
            f"🔍 د <b>{query}</b> لپاره "
            f"{len(films)} فلمونه وموندل شول:"
        )

        for film in films:

            await send_film(
                message,
                film,
                compact=True,
            )

        return

    # ---------------------------------------------
    # TITLE
    # ---------------------------------------------

    step = session.get(
        "step"
    )

    if step == "title":

        session["title"] = (
            message.text.strip()
        )

        session["step"] = "year"

        await message.answer(
            "📅 د فلم د خپرېدو کال راولېږئ.\n\n"
            "مثال: <code>2025</code>\n"
            "که معلوم نه وي، <code>-</code> ولیکئ."
        )

        return

    # ---------------------------------------------
    # YEAR
    # ---------------------------------------------

    if step == "year":

        value = message.text.strip()

        session["year"] = (
            None
            if value == "-"
            else value
        )

        session["step"] = "quality"

        await message.answer(
            "⚙️ د فلم کیفیت راولېږئ.\n\n"
            "مثال: <code>720p</code>\n"
            "یا <code>1080p</code>"
        )

        return

    # ---------------------------------------------
    # QUALITY
    # ---------------------------------------------

    if step == "quality":

        value = message.text.strip()

        session["quality"] = (
            None
            if value == "-"
            else value
        )

        session["step"] = "genre"

        await message.answer(
            "🎭 د فلم ژانر راولېږئ.\n\n"
            "مثال:\n"
            "<code>عاشقانه، مسخراچي</code>\n\n"
            "که معلوم نه وي، <code>-</code> ولیکئ."
        )

        return

    # ---------------------------------------------
    # GENRE
    # ---------------------------------------------

    if step == "genre":

        value = message.text.strip()

        session["genre"] = (
            None
            if value == "-"
            else value
        )

        session["step"] = "language"

        await message.answer(
            "🔊 د فلم ژبه / ډول راولېږئ.\n\n"
            "مثال:\n"
            "<code>پښتو ژباړه</code>\n\n"
            "که معلومه نه وي، <code>-</code> ولیکئ."
        )

        return

    # ---------------------------------------------
    # LANGUAGE
    # ---------------------------------------------

    if step == "language":

        value = message.text.strip()

        session["language"] = (
            None
            if value == "-"
            else value
        )

        session["step"] = "description"

        await message.answer(
            "📝 د فلم لنډه تشریح راولېږئ.\n\n"
            "که تشریح نه غواړئ، <code>-</code> ولیکئ."
        )

        return

    # ---------------------------------------------
    # DESCRIPTION
    # ---------------------------------------------

    if step == "description":

        value = message.text.strip()

        session["description"] = (
            None
            if value == "-"
            else value
        )

        session["step"] = "poster"

        await message.answer(
            "🖼️ اوس د فلم <b>Poster</b> عکس راولېږئ.\n\n"
            "که Poster نه لرئ، <code>-</code> ولیکئ."
        )

        return

    # ---------------------------------------------
    # POSTER SKIP
    # ---------------------------------------------

    if step == "poster":

        if message.text.strip() == "-":

            await finalize_film_submission(
                message
            )

            return

        await message.answer(
            "🖼️ مهرباني وکړئ Poster د عکس "
            "په توګه راولېږئ، یا <code>-</code> ولیکئ."
        )


# =========================================================
# POSTER
# =========================================================

@router.message(
    F.photo
)
async def poster_handler(
    message: Message,
):

    if not message.from_user:
        return

    if not await ensure_access(
        message
    ):
        return

    user_id = message.from_user.id

    session = pending_submissions.get(
        user_id
    )

    if not session:
        return

    if session.get("step") != "poster":

        await message.answer(
            "ℹ️ اوس د Poster اړتیا نشته."
        )

        return

    photo = message.photo[-1]

    session["poster_file_id"] = (
        photo.file_id
    )

    session["poster_unique_id"] = (
        photo.file_unique_id
    )

    # Download poster temporarily only to
    # calculate pHash.
    try:

        telegram_file = await bot.get_file(
            photo.file_id
        )

        from io import BytesIO

        buffer = BytesIO()

        await bot.download_file(
            telegram_file.file_path,
            buffer,
        )

        buffer.seek(0)

        from app.films import (
            calculate_image_hash
        )

        session["poster_hash"] = (
            calculate_image_hash(
                buffer.getvalue()
            )
        )

    except Exception as exc:

        logger.warning(
            "Poster hash failed: %s",
            exc,
        )

    await finalize_film_submission(
        message
    )


# =========================================================
# FINALIZE FILM
# =========================================================

async def finalize_film_submission(
    message: Message,
):

    if not message.from_user:
        return

    user_id = message.from_user.id

    session = pending_submissions.get(
        user_id
    )

    if not session:
        return

    title = session.get(
        "title"
    )

    video_file_id = session.get(
        "video_file_id"
    )

    if not title or not video_file_id:

        await message.answer(
            "❌ د فلم معلومات نیمګړي دي."
        )

        pending_submissions.pop(
            user_id,
            None,
        )

        return

    auto_approve = await get_auto_approve()

    approved = auto_approve

    film = await create_film(
        title=title,
        video_file_id=video_file_id,
        video_file_unique_id=session.get(
            "video_unique_id"
        ),
        uploader_id=user_id,
        year=session.get(
            "year"
        ),
        quality=session.get(
            "quality"
        ),
        genre=session.get(
            "genre"
        ),
        language=session.get(
            "language"
        ),
        description=session.get(
            "description"
        ),
        category="pashto",
        poster_file_id=session.get(
            "poster_file_id"
        ),
        poster_file_unique_id=session.get(
            "poster_unique_id"
        ),
        poster_hash=session.get(
            "poster_hash"
        ),
        approved=approved,
        official=False,
    )

    pending_submissions.pop(
        user_id,
        None,
    )

    if approved:

        await message.answer(
            "🎉 <b>فلم په بریالیتوب سره ثبت شو!</b>\n\n"
            f"🎬 نوم: <b>{film.title}</b>\n"
            f"🆔 ID: <code>{film.id}</code>\n\n"
            "✅ فلم اوس د لټون او فلمونو په برخه کې ښکاره کېدای شي."
        )

    else:

        await message.answer(
            "✅ <b>فلم ترلاسه شو.</b>\n\n"
            f"🎬 نوم: <b>{film.title}</b>\n"
            f"🆔 ID: <code>{film.id}</code>\n\n"
            "⏳ فلم د Admin تایید ته واستول شو."
        )

        await notify_admins_about_film(
            film
        )


# =========================================================
# SEND FILM
# =========================================================

async def send_film(
    message: Message,
    film: Film,
    compact: bool = False,
):

    caption = (
        f"🎬 <b>{film.title}</b>\n\n"
    )

    if film.year:
        caption += (
            f"📅 کال: <b>{film.year}</b>\n"
        )

    if film.quality:
        caption += (
            f"⚙️ کیفیت: <b>{film.quality}</b>\n"
        )

    if film.genre:
        caption += (
            f"🎭 ژانر: <b>{film.genre}</b>\n"
        )

    if film.language:
        caption += (
            f"🔊 ژبه: <b>{film.language}</b>\n"
        )

    if film.description:
        caption += (
            f"\n📝 {film.description}\n"
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬇️ فلم ترلاسه کړئ",
                    callback_data=f"getfilm_{film.id}",
                )
            ]
        ]
    )

    try:

        # If poster exists, send poster first.
        if film.poster_file_id:

            await message.answer_photo(
                photo=film.poster_file_id,
                caption=caption,
                reply_markup=keyboard,
            )

        else:

            await message.answer(
                caption,
                reply_markup=keyboard,
            )

    except Exception as exc:

        logger.warning(
            "Unable to send film preview: %s",
            exc,
        )


# =========================================================
# GET FILM
# =========================================================

@router.callback_query(
    F.data.startswith("getfilm_")
)
async def get_film_callback(
    callback: CallbackQuery,
):

    if not await ensure_callback_access(
        callback
    ):
        return

    await callback.answer(
        "فلم لېږل کېږي..."
    )

    try:

        film_id = int(
            callback.data.split(
                "_",
                1,
            )[1]
        )

    except Exception:

        await callback.message.answer(
            "❌ ناسم Film ID."
        )

        return

    film = await get_film(
        film_id
    )

    if not film:

        await callback.message.answer(
            "❌ فلم پیدا نه شو."
        )

        return

    try:

        await callback.message.answer_video(
            video=film.video_file_id,
            caption=(
                f"🎬 <b>{film.title}</b>\n\n"
                "📥 د ALL PRODUCTION FILMS له خوا"
            ),
        )

    except Exception:

        try:

            await callback.message.answer_document(
                document=film.video_file_id,
                caption=(
                    f"🎬 <b>{film.title}</b>\n\n"
                    "📥 د ALL PRODUCTION FILMS له خوا"
                ),
            )

        except Exception as exc:

            logger.exception(
                "Film delivery failed: %s",
                exc,
            )

            await callback.message.answer(
                "❌ فلم ونه لېږل شو. "
                "مهرباني وکړئ وروسته بیا هڅه وکړئ."
            )


# =========================================================
# CHANNEL REFERRALS
# =========================================================

@router.chat_member()
async def channel_member_update(
    event: ChatMemberUpdated,
):

    try:

        main_channel = await get_main_channel()

        event_username = (
            event.chat.username
            or ""
        )

        configured = (
            main_channel
            .lstrip("@")
            .lower()
        )

        if event_username.lower() != configured:

            return

        old_status = event.old_chat_member.status
        new_status = event.new_chat_member.status

        was_member = old_status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
            ChatMemberStatus.RESTRICTED,
        }

        is_member = new_status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
            ChatMemberStatus.RESTRICTED,
        }

        if was_member or not is_member:
            return

        invite_link = None

        if event.invite_link:

            invite_link = (
                event.invite_link.invite_link
            )

        if not invite_link:

            # Direct join does not count.
            logger.info(
                "Direct join detected; no referral counted."
            )

            return

        joined_user = (
            event.new_chat_member.user
        )

        result = await process_channel_join(
            inviter_id=None,
            invited_id=joined_user.id,
            invite_link=invite_link,
        )

        logger.info(
            "Referral join processed: %s",
            result,
        )

    except Exception as exc:

        logger.exception(
            "Channel member handler failed: %s",
            exc,
        )


# =========================================================
# ADMIN NOTIFICATION
# =========================================================

async def notify_admins_about_film(
    film: Film,
):

    for admin_id in settings.admin_ids:

        try:

            await bot.send_message(
                admin_id,
                (
                    "🎬 <b>نوی فلم د تایید لپاره راغلی</b>\n\n"
                    f"🆔 ID: <code>{film.id}</code>\n"
                    f"🎬 نوم: <b>{film.title}</b>\n"
                    f"👤 Uploader: <code>{film.uploader_id}</code>"
                ),
            )

        except Exception as exc:

            logger.warning(
                "Admin notification failed: %s",
                exc,
            )


# =========================================================
# ADMIN COMMANDS
# =========================================================

@router.message(
    Command("admin")
)
async def admin_handler(
    message: Message,
):

    if not message.from_user:
        return

    if not is_admin(
        message.from_user.id
    ):

        await message.answer(
            "⛔ تاسو Admin نه یاست."
        )

        return

    stats = await get_statistics()

    target = await get_referral_target()

    auto_approve = await get_auto_approve()

    main_channel = await get_main_channel()

    access_channel = await get_access_channel()

    await message.answer(
        "🛠 <b>ALL PRODUCTION ADMIN</b>\n\n"
        f"👥 Users: <b>{stats['users']}</b>\n"
        f"⭐ Publishers: <b>{stats['publishers']}</b>\n"
        f"🎬 Films: <b>{stats['films']}</b>\n"
        f"✅ Approved: <b>{stats['approved_films']}</b>\n"
        f"📢 Channels: <b>{stats['channels']}</b>\n\n"
        f"👥 Referral Target: <b>{target}</b>\n"
        f"🤖 Auto Approve: <b>{auto_approve}</b>\n"
        f"📢 Main: <code>{main_channel}</code>\n"
        f"🔐 Access: <code>{access_channel}</code>"
    )


@router.message(
    Command("stats")
)
async def stats_handler(
    message: Message,
):

    if not message.from_user:
        return

    if not is_admin(
        message.from_user.id
    ):
        return

    stats = await get_statistics()

    await message.answer(
        "📊 <b>Statistics</b>\n\n"
        f"👥 Users: <b>{stats['users']}</b>\n"
        f"⭐ Publishers: <b>{stats['publishers']}</b>\n"
        f"🎬 Films: <b>{stats['films']}</b>\n"
        f"✅ Approved: <b>{stats['approved_films']}</b>\n"
        f"📢 Channels: <b>{stats['channels']}</b>"
    )


# =========================================================
# ERROR HANDLER
# =========================================================

@router.errors()
async def error_handler(
    event,
):

    logger.exception(
        "Unhandled bot error: %s",
        event.exception,
    )


# =========================================================
# START BOT
# =========================================================

_bot_task = None


async def start_bot():

    global _bot_task

    if _bot_task is not None:
        return

    async def runner():

        while True:

            try:

                logger.info(
                    "ALL PRODUCTION FILMS bot starting..."
                )

                await bot.delete_webhook(
                    drop_pending_updates=False
                )

                await dp.start_polling(
                    bot,
                    allowed_updates=dp.resolve_used_update_types(),
                )

            except asyncio.CancelledError:

                raise

            except Exception as exc:

                logger.exception(
                    "Bot polling crashed: %s",
                    exc,
                )

                await asyncio.sleep(
                    5
                )

    _bot_task = asyncio.create_task(
        runner()
    )

    await asyncio.sleep(
        0
    )


# =========================================================
# END
# =========================================================
