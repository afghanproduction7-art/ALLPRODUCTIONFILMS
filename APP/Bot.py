import asyncio
import logging
from io import BytesIO
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal
from app.models import Film, User
from app.admin import get_statistics, is_admin
from app.films import calculate_image_hash, create_film
from app.referrals import (
    ensure_referral_link,
    get_referral_status,
    process_channel_join,
)
from app.settings_db import (
    get_access_channel,
    get_auto_approve,
    get_main_channel,
    get_referral_target,
)

logging.basicConfig(
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

router = Router()

bot = Bot(
    token=settings.BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    ),
)

dp = Dispatcher()

dp.include_router(router)


# =========================================================
# TEMPORARY PUBLISHING STORAGE
# =========================================================

pending_submissions: dict[int, dict[str, Any]] = {}


# =========================================================
# KEYBOARDS
# =========================================================

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎬 وروستي فلمونه",
                    web_app=None,
                    callback_data="films_latest",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔎 د فلم لټون",
                    callback_data="search_info",
                ),
                InlineKeyboardButton(
                    text="👥 ریفرل",
                    callback_data="referral",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎥 فلم نشرول",
                    callback_data="publish",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏆 مشران",
                    callback_data="leaders",
                ),
            ],
        ]
    )


def access_keyboard(channel: str) -> InlineKeyboardMarkup:
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
                    text="✅ ما Join کړی، بیا وګوره",
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
                    text="🎥 فلم نشرول شروع کړئ",
                    callback_data="start_publish",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 شاته",
                    callback_data="back_menu",
                )
            ],
        ]
    )


# =========================================================
# DATABASE USER
# =========================================================

async def get_or_create_user(
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
) -> User:

    async with SessionLocal() as session:

        result = await session.execute(
            select(User).where(
                User.telegram_id == telegram_id
            )
        )

        user = result.scalar_one_or_none()

        if user is None:

            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
            )

            session.add(user)

        else:

            if username is not None:
                user.username = username

            if first_name is not None:
                user.first_name = first_name

        await session.commit()
        await session.refresh(user)

        return user


# =========================================================
# ACCESS CHECK
# =========================================================

async def check_channel_access(
    telegram_id: int,
) -> bool:

    channel = await get_access_channel()

    if not channel:
        return True

    try:

        member = await bot.get_chat_member(
            chat_id=channel,
            user_id=telegram_id,
        )

        allowed_statuses = {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        }

        return member.status in allowed_statuses

    except Exception as exc:

        logger.warning(
            "Access check failed for %s: %s",
            telegram_id,
            exc,
        )

        return False


# =========================================================
# SEND ACCESS REQUIRED
# =========================================================

async def send_access_required(
    message: Message,
) -> None:

    channel = await get_access_channel()

    await message.answer(
        "🔒 <b>د روباټ د استعمال لپاره</b>\n\n"
        "لومړی زموږ اړین چینل Join کړئ.\n\n"
        "له Join وروسته لاندې تڼۍ کې "
        "«ما Join کړی» ووهئ.",
        reply_markup=access_keyboard(channel),
    )


# =========================================================
# START
# =========================================================

@router.message(CommandStart())
async def start_handler(message: Message):

    if not message.from_user:
        return

    user = await get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
    )

    # -----------------------------------------------------
    # ACCESS
    # -----------------------------------------------------

    has_access = await check_channel_access(
        message.from_user.id
    )

    if not has_access:

        await send_access_required(message)
        return

    # -----------------------------------------------------
    # REFERRAL LINK
    # -----------------------------------------------------

    async with SessionLocal() as session:

        try:

            await ensure_referral_link(
                session,
                user,
            )

            await session.commit()

        except Exception:

            await session.rollback()

    # -----------------------------------------------------
    # DEEP LINK
    # -----------------------------------------------------

    text = message.text or ""

    if " " in text:

        start_parameter = text.split(
            " ",
            1,
        )[1].strip()

        if start_parameter.startswith("film_"):

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

    # -----------------------------------------------------
    # MAIN MENU
    # -----------------------------------------------------

    first_name = (
        message.from_user.first_name
        or "ملګري"
    )

    await message.answer(
        f"🎬 <b>ALL PRODUCTION FILMS</b>\n\n"
        f"سلام <b>{first_name}</b>! 👋\n\n"
        "د پښتو فلمونو نړۍ ته ښه راغلاست. 🍿",
        reply_markup=main_menu_keyboard(),
    )


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

    allowed = await check_channel_access(
        callback.from_user.id
    )

    if not allowed:

        await callback.answer(
            "❌ لا هم چینل Join شوی نه دی.",
            show_alert=True,
        )

        return

    await callback.answer(
        "✅ Access فعال شو!"
    )

    if callback.message:

        await callback.message.edit_text(
            "✅ <b>Access فعال شو!</b>\n\n"
            "اوس کولای شئ روباټ استعمال کړئ.",
            reply_markup=main_menu_keyboard(),
        )


# =========================================================
# BACK MENU
# =========================================================

@router.callback_query(
    F.data == "back_menu"
)
async def back_menu_callback(
    callback: CallbackQuery,
):

    await callback.answer()

    if callback.message:

        await callback.message.edit_text(
            "🏠 <b>اصلي مینو</b>\n\n"
            "مهرباني وکړئ یو انتخاب وکړئ.",
            reply_markup=main_menu_keyboard(),
        )


# =========================================================
# REFERRAL
# =========================================================

@router.callback_query(
    F.data == "referral"
)
async def referral_callback(
    callback: CallbackQuery,
):

    if not callback.from_user:
        return

    if not await check_channel_access(
        callback.from_user.id
    ):

        await callback.answer(
            "🔒 لومړی اړین چینل Join کړئ.",
            show_alert=True,
        )

        return

    status = await get_referral_status(
        callback.from_user.id
    )

    if not status:

        await callback.answer(
            "❌ د ریفرل معلومات پیدا نه شول.",
            show_alert=True,
        )

        return

    count = status.get(
        "referral_count",
        0,
    )

    target = status.get(
        "target",
        await get_referral_target(),
    )

    remaining = max(
        target - count,
        0,
    )

    referral_link = status.get(
        "referral_link"
    )

    publish_status = (
        "🟢 تاسو د فلم نشرولو اجازه لرئ."
        if count >= target
        else f"🔒 د نشر لپاره {remaining} ریفرل پاتې دي."
    )

    text = (
        "👥 <b>ستاسو ریفرل سیستم</b>\n\n"
        f"📊 ریفرل: <b>{count}</b> / <b>{target}</b>\n"
        f"⏳ پاتې: <b>{remaining}</b>\n\n"
        f"{publish_status}\n\n"
        f"🔗 <code>{referral_link or 'لا نه دی جوړ شوی'}</code>"
    )

    keyboard_rows = []

    if referral_link:

        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text="📤 لینک شریکول",
                    url=(
                        "https://t.me/share/url"
                        f"?url={referral_link}"
                    ),
                )
            ]
        )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🔄 تازه کول",
                callback_data="referral",
            )
        ]
    )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🔙 شاته",
                callback_data="back_menu",
            )
        ]
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=keyboard_rows
    )

    await callback.answer()

    if callback.message:

        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
        )


# =========================================================
# LEADERS
# =========================================================

@router.callback_query(
    F.data == "leaders"
)
async def leaders_callback(
    callback: CallbackQuery,
):

    await callback.answer()

    if not callback.message:
        return

    async with SessionLocal() as session:

        result = await session.execute(
            select(User)
            .where(
                User.referral_count > 0
            )
            .order_by(
                User.referral_count.desc()
            )
            .limit(10)
        )

        users = result.scalars().all()

    if not users:

        await callback.message.edit_text(
            "🏆 <b>د ریفرل مشران</b>\n\n"
            "تر اوسه کوم ریفرل نشته.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔙 شاته",
                            callback_data="back_menu",
                        )
                    ]
                ]
            ),
        )

        return

    lines = [
        "🏆 <b>د ریفرل ۱۰ مشران</b>\n"
    ]

    medals = [
        "🥇",
        "🥈",
        "🥉",
    ]

    for index, user in enumerate(users, start=1):

        medal = (
            medals[index - 1]
            if index <= 3
            else f"{index}."
        )

        name = (
            user.first_name
            or user.username
            or str(user.telegram_id)
        )

        lines.append(
            f"{medal} <b>{name}</b> — "
            f"{user.referral_count} ریفرل"
        )

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 شاته",
                        callback_data="back_menu",
                    )
                ]
            ]
        ),
    )


# =========================================================
# PUBLISH INFO
# =========================================================

@router.callback_query(
    F.data == "publish"
)
async def publish_callback(
    callback: CallbackQuery,
):

    if not callback.from_user:
        return

    if not await check_channel_access(
        callback.from_user.id
    ):

        await callback.answer(
            "🔒 لومړی اړین چینل Join کړئ.",
            show_alert=True,
        )

        return

    status = await get_referral_status(
        callback.from_user.id
    )

    count = (
        status.get("referral_count", 0)
        if status
        else 0
    )

    target = await get_referral_target()

    if count < target:

        remaining = target - count

        await callback.answer(
            f"🔒 لا {remaining} ریفرل پاتې دي.",
            show_alert=True,
        )

        return

    await callback.answer()

    if callback.message:

        await callback.message.edit_text(
            "🎥 <b>فلم نشرول</b>\n\n"
            "تاسو د فلم نشرولو اجازه لرئ. ✅\n\n"
            "لومړی فلم Video یا Document "
            "په همدې چټ کې راولېږئ.",
            reply_markup=publish_keyboard(),
        )


# =========================================================
# START PUBLISH
# =========================================================

@router.callback_query(
    F.data == "start_publish"
)
async def start_publish_callback(
    callback: CallbackQuery,
):

    if not callback.from_user:
        return

    status = await get_referral_status(
        callback.from_user.id
    )

    count = (
        status.get("referral_count", 0)
        if status
        else 0
    )

    target = await get_referral_target()

    if count < target:

        await callback.answer(
            "❌ تاسو لا د نشر شرط نه دی بشپړ کړی.",
            show_alert=True,
        )

        return

    pending_submissions[
        callback.from_user.id
    ] = {
        "step": "video",
        "data": {},
    }

    await callback.answer()

    if callback.message:

        await callback.message.edit_text(
            "🎥 <b>لومړی ګام</b>\n\n"
            "اوس د فلم <b>Video</b> یا "
            "<b>Document</b> راولېږئ.\n\n"
            "وروسته به د فلم نور معلومات درڅخه وغواړل شي."
        )


# =========================================================
# RECEIVE VIDEO
# =========================================================

@router.message(
    F.video
)
async def receive_video(
    message: Message,
):

    if not message.from_user:
        return

    user_id = message.from_user.id

    if not await check_channel_access(
        user_id
    ):

        await send_access_required(message)
        return

    pending = pending_submissions.get(
        user_id
    )

    if not pending:
        return

    video = message.video

    pending["data"] = {
        "video_file_id": video.file_id,
        "video_file_unique_id": video.file_unique_id,
    }

    pending["step"] = "title"

    await message.answer(
        "✅ فلم ترلاسه شو.\n\n"
        "📝 <b>دوهم ګام:</b>\n"
        "د فلم نوم راولېږئ."
    )


# =========================================================
# RECEIVE DOCUMENT
# =========================================================

@router.message(
    F.document
)
async def receive_document(
    message: Message,
):

    if not message.from_user:
        return

    user_id = message.from_user.id

    if not await check_channel_access(
        user_id
    ):

        await send_access_required(message)
        return

    pending = pending_submissions.get(
        user_id
    )

    if not pending:
        return

    document = message.document

    pending["data"] = {
        "video_file_id": document.file_id,
        "video_file_unique_id": document.file_unique_id,
    }

    pending["step"] = "title"

    await message.answer(
        "✅ فلم ترلاسه شو.\n\n"
        "📝 <b>دوهم ګام:</b>\n"
        "د فلم نوم راولېږئ."
    )


# =========================================================
# RECEIVE TEXT METADATA
# =========================================================

@router.message(
    F.text
)
async def receive_publish_text(
    message: Message,
):

    if not message.from_user:
        return

    user_id = message.from_user.id

    pending = pending_submissions.get(
        user_id
    )

    if not pending:
        return

    if message.text.startswith("/"):
        return

    text = message.text.strip()

    step = pending.get("step")

    if step == "title":

        pending["data"]["title"] = text
        pending["step"] = "year"

        await message.answer(
            "📅 <b>درېیم ګام:</b>\n"
            "د فلم د خپرېدو کال راولېږئ.\n\n"
            "مثال: <code>2017</code>"
        )

        return

    if step == "year":

        try:

            year = int(text)

        except ValueError:

            await message.answer(
                "❌ کال باید په عددونو وي.\n"
                "مثال: <code>2017</code>"
            )

            return

        pending["data"]["year"] = year
        pending["step"] = "quality"

        await message.answer(
            "⚙️ <b>څلورم ګام:</b>\n"
            "د فلم کیفیت راولېږئ.\n\n"
            "مثال: <code>720p</code>"
        )

        return

    if step == "quality":

        pending["data"]["quality"] = text
        pending["step"] = "genre"

        await message.answer(
            "🎭 <b>پنځم ګام:</b>\n"
            "د فلم ژانر راولېږئ.\n\n"
            "مثال: <code>عاشقانه، مسخراچي</code>"
        )

        return

    if step == "genre":

        pending["data"]["genre"] = text
        pending["step"] = "language"

        await message.answer(
            "🔊 <b>شپږم ګام:</b>\n"
            "د فلم ژبه راولېږئ.\n\n"
            "مثال: <code>پښتو</code>"
        )

        return

    if step == "language":

        pending["data"]["language"] = text
        pending["step"] = "description"

        await message.answer(
            "📝 <b>اووم ګام:</b>\n"
            "د فلم لنډه تشریح راولېږئ.\n\n"
            "که تشریح نه غواړئ، ولیکئ:\n"
            "<code>نه</code>"
        )

        return

    if step == "description":

        description = (
            ""
            if text.lower() in {
                "نه",
                "no",
                "none",
            }
            else text
        )

        pending["data"]["description"] = description
        pending["step"] = "poster"

        await message.answer(
            "🖼️ <b>اتم ګام:</b>\n"
            "اوس د فلم Poster عکس راولېږئ."
        )

        return


# =========================================================
# RECEIVE POSTER
# =========================================================

@router.message(
    F.photo
)
async def receive_poster(
    message: Message,
):

    if not message.from_user:
        return

    user_id = message.from_user.id

    pending = pending_submissions.get(
        user_id
    )

    if not pending:
        return

    if pending.get("step") != "poster":
        return

    photo = message.photo[-1]

    try:

        file = await bot.get_file(
            photo.file_id
        )

        buffer = BytesIO()

        await bot.download_file(
            file.file_path,
            buffer,
        )

        poster_bytes = buffer.getvalue()

        poster_hash = calculate_image_hash(
            poster_bytes
        )

    except Exception as exc:

        logger.exception(
            "Poster processing failed: %s",
            exc,
        )

        await message.answer(
            "❌ د Poster پروسس کې ستونزه راغله.\n"
            "مهرباني وکړئ بیا عکس راولېږئ."
        )

        return

    pending["data"]["poster_file_id"] = (
        photo.file_id
    )

    pending["data"]["poster_file_unique_id"] = (
        photo.file_unique_id
    )

    pending["data"]["poster_hash"] = (
        str(poster_hash)
    )

    await finalize_film_submission(
        message,
        user_id,
    )


# =========================================================
# FINALIZE FILM
# =========================================================

async def finalize_film_submission(
    message: Message,
    user_id: int,
):

    pending = pending_submissions.get(
        user_id
    )

    if not pending:
        return

    data = pending.get(
        "data",
        {}
    )

    required_fields = [
        "title",
        "year",
        "quality",
        "genre",
        "language",
        "video_file_id",
        "poster_file_id",
    ]

    missing = [
        field
        for field in required_fields
        if not data.get(field)
    ]

    if missing:

        await message.answer(
            "❌ د فلم ځینې معلومات نیمګړي دي.\n"
            "مهرباني وکړئ بیا هڅه وکړئ."
        )

        return

    try:

        async with SessionLocal() as session:

            user_result = await session.execute(
                select(User).where(
                    User.telegram_id == user_id
                )
            )

            user = (
                user_result.scalar_one_or_none()
            )

            if not user:

                await message.answer(
                    "❌ کارن پیدا نه شو."
                )

                return

            auto_approve = (
                await get_auto_approve()
            )

            film = await create_film(
                session=session,
                title=data["title"],
                year=data["year"],
                quality=data["quality"],
                genre=data["genre"],
                language=data["language"],
                description=data.get(
                    "description",
                    "",
                ),
                category=data.get(
                    "genre",
                    "",
                ),
                video_file_id=data[
                    "video_file_id"
                ],
                video_file_unique_id=data.get(
                    "video_file_unique_id"
                ),
                poster_file_id=data[
                    "poster_file_id"
                ],
                poster_file_unique_id=data.get(
                    "poster_file_unique_id"
                ),
                poster_hash=data.get(
                    "poster_hash"
                ),
                uploader_id=user.id,
                approved=auto_approve,
            )

            await session.commit()

            film_id = film.id

    except Exception as exc:

        logger.exception(
            "Film creation failed: %s",
            exc,
        )

        await message.answer(
            "❌ فلم ثبت نه شو.\n"
            "مهرباني وکړئ وروسته بیا هڅه وکړئ."
        )

        return

    pending_submissions.pop(
        user_id,
        None,
    )

    if auto_approve:

        await message.answer(
            "🎉 <b>فلم په بریالیتوب سره ثبت او خپور شو!</b>\n\n"
            f"🎬 <b>{data['title']}</b>\n"
            f"🆔 ID: <code>{film_id}</code>\n\n"
            "کار مو مبارک شه. 🍿"
        )

    else:

        await message.answer(
            "✅ <b>فلم ترلاسه او ثبت شو.</b>\n\n"
            f"🎬 <b>{data['title']}</b>\n"
            f"🆔 ID: <code>{film_id}</code>\n\n"
            "⏳ اوس د Admin تایید ته انتظار وباسئ."
        )


# =========================================================
# LATEST FILMS
# =========================================================

@router.callback_query(
    F.data == "films_latest"
)
async def latest_films_callback(
    callback: CallbackQuery,
):

    await callback.answer()

    if callback.message:

        await callback.message.answer(
            "🎬 وروستي فلمونه د Mini App له لارې "
            "په اسانۍ موندلای شئ.",
        )


# =========================================================
# SEARCH INFO
# =========================================================

@router.callback_query(
    F.data == "search_info"
)
async def search_info_callback(
    callback: CallbackQuery,
):

    await callback.answer()

    if callback.message:

        await callback.message.answer(
            "🔎 <b>د فلم لټون</b>\n\n"
            "د فلم نوم په Mini App کې "
            "د Search برخه کې ولیکئ.\n\n"
            "🖼️ همدارنګه کولای شئ د Poster "
            "له عکس څخه فلم ولټوئ."
        )


# =========================================================
# SEND FILM
# =========================================================

async def send_film_to_user(
    message: Message,
    film_id: int,
):

    async with SessionLocal() as session:

        result = await session.execute(
            select(Film).where(
                Film.id == film_id,
                Film.approved.is_(True),
            )
        )

        film = result.scalar_one_or_none()

    if not film:

        await message.answer(
            "❌ دا فلم پیدا نه شو."
        )

        return

    caption = (
        f"🎬 <b>{film.title}</b>\n\n"
        f"📅 کال: {film.year or 'نامعلوم'}\n"
        f"⚙️ کیفیت: {film.quality or 'نامعلوم'}\n"
        f"🎭 ژانر: {film.genre or 'نامعلوم'}\n"
        f"🔊 ژبه: {film.language or 'نامعلوم'}"
    )

    try:

        await message.answer_photo(
            photo=film.poster_file_id,
            caption=caption,
        )

    except Exception:

        await message.answer(
            caption
        )

    await message.answer_document(
        document=film.video_file_id,
        caption=(
            f"🍿 <b>{film.title}</b>\n\n"
            "⬇️ د فلم ترلاسه کولو لپاره "
            "پورته فایل Download کړئ."
        ),
    )


# =========================================================
# ADMIN COMMAND
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
            "❌ تاسو Admin نه یاست."
        )

        return

    await message.answer(
        "🛠️ <b>ALL PRODUCTION ADMIN</b>\n\n"
        "Admin Panel:\n"
        f"{settings.APP_URL}/static/admin.html\n\n"
        "📊 /stats"
    )


# =========================================================
# STATS
# =========================================================

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

        await message.answer(
            "❌ تاسو Admin نه یاست."
        )

        return

    try:

        stats = await get_statistics()

        text = (
            "📊 <b>ALL PRODUCTION STATS</b>\n\n"
            f"👥 ټول Users: "
            f"<b>{stats.get('users', 0)}</b>\n"
            f"🎥 Publishers: "
            f"<b>{stats.get('publishers', 0)}</b>\n"
            f"🎬 ټول فلمونه: "
            f"<b>{stats.get('films', 0)}</b>\n"
            f"✅ Approved: "
            f"<b>{stats.get('approved_films', 0)}</b>\n"
            f"📢 Channels: "
            f"<b>{stats.get('channels', 0)}</b>"
        )

        await message.answer(text)

    except Exception as exc:

        logger.exception(
            "Stats error: %s",
            exc,
        )

        await message.answer(
            "❌ د Stats ترلاسه کولو کې ستونزه راغله."
        )


# =========================================================
# CHANNEL JOIN TRACKING
# =========================================================

@router.chat_member()
async def channel_member_handler(
    update,
):

    try:

        chat = update.chat

        main_channel = (
            await get_main_channel()
        )

        chat_username = (
            f"@{chat.username}"
            if getattr(chat, "username", None)
            else str(chat.id)
        )

        if (
            chat_username.lower()
            != main_channel.lower()
        ):
            return

        new_member = update.new_chat_member

        if not new_member:
            return

        if new_member.status not in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        }:
            return

        invited_user_id = (
            new_member.user.id
        )

        invite_link = None

        if update.invite_link:

            invite_link = (
                update.invite_link.invite_link
            )

        result = await process_channel_join(
            inviter_id=None,
            invited_id=invited_user_id,
            invite_link=invite_link,
        )

        if result:

            inviter_id = result.get(
                "inviter_id"
            )

            if inviter_id:

                try:

                    await bot.send_message(
                        inviter_id,
                        "🎉 <b>نوی ریفرل!</b>\n\n"
                        "یو نوی کارن ستاسو "
                        "د دعوت لینک له لارې "
                        "چینل ته داخل شو. 👥",
                    )

                except Exception:
                    pass

    except Exception as exc:

        logger.exception(
            "Channel member handler error: %s",
            exc,
        )


# =========================================================
# ERROR HANDLING
# =========================================================

@router.errors()
async def global_error_handler(
    event,
):

    logger.exception(
        "Unhandled Telegram error: %s",
        event.exception,
    )


# =========================================================
# BOT START
# =========================================================

async def start_bot():

    logger.info(
        "Starting ALL PRODUCTION FILMS bot..."
    )

    try:

        await bot.delete_webhook(
            drop_pending_updates=False
        )

    except Exception as exc:

        logger.warning(
            "Could not delete webhook: %s",
            exc,
        )

    try:

        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )

    finally:

        await bot.session.close()


# =========================================================
# DIRECT RUN
# =========================================================

if __name__ == "__main__":

    asyncio.run(
        start_bot()
)
