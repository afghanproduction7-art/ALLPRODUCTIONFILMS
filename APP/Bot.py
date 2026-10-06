import asyncio
import logging
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatMemberStatus, ContentType
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.admin import (
    get_statistics,
    is_admin,
)
from app.config import settings
from app.database import init_db
from app.films import (
    calculate_image_hash,
    create_film,
    get_category_films,
    get_film,
    get_official_films,
    search_by_image_hash,
    search_films,
)
from app.referrals import (
    can_publish,
    create_referral_link,
    get_or_create_user,
    get_referral_count,
    get_referral_leaders,
)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# BOT / DISPATCHER
# =========================================================

bot = Bot(
    token=settings.BOT_TOKEN
)

dp = Dispatcher()


# =========================================================
# TEMPORARY FILM SUBMISSION STATE
# =========================================================

pending_submissions: dict[int, dict[str, Any]] = {}


# =========================================================
# ACCESS CHECK
# =========================================================

async def check_access(user_id: int) -> bool:
    """
    Checks whether user joined @ALL_PASHTO.
    """

    try:
        member = await bot.get_chat_member(
            chat_id=settings.ACCESS_CHANNEL,
            user_id=user_id,
        )

        return member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        }

    except Exception as error:
        logger.warning(
            "Access check failed for %s: %s",
            user_id,
            error,
        )

        return False


# =========================================================
# ACCESS KEYBOARD
# =========================================================

def access_keyboard() -> InlineKeyboardMarkup:

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="📢 زموږ چینل ته Join شئ",
            url="https://t.me/ALL_PASHTO",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="✅ ما Join کړی",
            callback_data="check_access",
        )
    )

    return builder.as_markup()


# =========================================================
# MAIN MENU
# =========================================================

def main_menu() -> InlineKeyboardMarkup:

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="🔎 فلم ولټوئ",
            callback_data="search_help",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🎬 پښتو ترجمه فلمونه",
            callback_data="pashto_films",
        ),
        InlineKeyboardButton(
            text="📢 زموږ فلمونه",
            callback_data="official_films",
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="👥 Referral",
            callback_data="referral",
        ),
        InlineKeyboardButton(
            text="🏆 مشران",
            callback_data="leaders",
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="📤 فلم نشر کړئ",
            callback_data="publish",
        )
    )

    return builder.as_markup()


# =========================================================
# SEND MAIN MENU
# =========================================================

async def send_main_menu(message: Message):

    user = message.from_user

    if user is None:
        return

    count = await get_referral_count(
        user.id
    )

    publish_status = await can_publish(
        user.id
    )

    if publish_status:

        publish_text = (
            "✅ تاسو د فلم نشرولو اجازه لرئ."
        )

    else:

        remaining = max(
            settings.REFERRAL_TARGET - count,
            0,
        )

        publish_text = (
            f"🔒 د فلم نشرولو لپاره "
            f"{remaining} Referral نور پکار دي."
        )

    text = (
        "🎬 <b>ALL PRODUCTION FILMS</b>\n\n"
        "👋 ښه راغلاست!\n\n"
        "🔎 دلته کولای شئ خپل خوښ فلم پیدا کړئ.\n"
        "📥 فلمونه ترلاسه کړئ.\n"
        "📤 او د شرایطو له پوره کولو وروسته "
        "خپل فلمونه هم نشر کړئ.\n\n"
        f"{publish_text}"
    )

    await message.answer(
        text,
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


# =========================================================
# /START
# =========================================================

@dp.message(CommandStart())
async def start_handler(message: Message):

    user = message.from_user

    if user is None:
        return

    await get_or_create_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    command_args = ""

    if message.text:

        parts = message.text.split(
            maxsplit=1
        )

        if len(parts) == 2:
            command_args = parts[1].strip()

    # -----------------------------------------------------
    # FILM DEEP LINK
    # -----------------------------------------------------

    if command_args.startswith("film_"):

        try:

            film_id = int(
                command_args.replace(
                    "film_",
                    "",
                    1,
                )
            )

        except ValueError:

            await message.answer(
                "❌ د فلم لینک ناسم دی."
            )

            return

        if not await check_access(
            user.id
        ):

            await message.answer(
                "🔐 <b>لومړی زموږ Access Channel ته Join شئ.</b>\n\n"
                "تر Join وروسته د «ما Join کړی» تڼۍ کېکاږئ.",
                reply_markup=access_keyboard(),
                parse_mode="HTML",
            )

            return

        film = await get_film(
            film_id
        )

        if not film:

            await message.answer(
                "❌ دا فلم نور موجود نه دی."
            )

            return

        await send_film(
            message,
            film,
        )

        return

    # -----------------------------------------------------
    # ACCESS
    # -----------------------------------------------------

    if not await check_access(
        user.id
    ):

        await message.answer(
            "🔐 <b>لومړی باید زموږ Access Channel ته Join شئ.</b>\n\n"
            "تر Join وروسته د «ما Join کړی» تڼۍ کېکاږئ.",
            reply_markup=access_keyboard(),
            parse_mode="HTML",
        )

        return

    # -----------------------------------------------------
    # NORMAL MENU
    # -----------------------------------------------------

    await send_main_menu(
        message
    )


# =========================================================
# ACCESS CALLBACK
# =========================================================

@dp.callback_query(
    F.data == "check_access"
)
async def check_access_callback(
    callback: CallbackQuery,
):

    user = callback.from_user

    if await check_access(
        user.id
    ):

        await callback.answer(
            "✅ Access تایید شو.",
            show_alert=True,
        )

        try:
            await callback.message.edit_text(
                "✅ Access تایید شو.\n\n"
                "🎬 اوس تاسو Bot استعمالولای شئ.",
                reply_markup=main_menu(),
            )

        except Exception:
            pass

    else:

        await callback.answer(
            "❌ تاسو لا تراوسه چینل ته Join نه یاست.",
            show_alert=True,
        )


# =========================================================
# GENERAL CALLBACK ACCESS CHECK
# =========================================================

async def callback_has_access(
    callback: CallbackQuery,
) -> bool:

    user = callback.from_user

    if await check_access(
        user.id
    ):

        return True

    await callback.answer(
        "🔐 لومړی @ALL_PASHTO ته Join شئ.",
        show_alert=True,
    )

    try:

        await callback.message.answer(
            "🔐 لومړی زموږ Access Channel ته Join شئ.",
            reply_markup=access_keyboard(),
        )

    except Exception:
        pass

    return False


# =========================================================
# REFERRAL
# =========================================================

@dp.callback_query(
    F.data == "referral"
)
async def referral_callback(
    callback: CallbackQuery,
):

    if not await callback_has_access(
        callback
    ):
        return

    user = callback.from_user

    link = await create_referral_link(
        bot,
        user.id,
    )

    count = await get_referral_count(
        user.id
    )

    target = settings.REFERRAL_TARGET

    if count >= target:

        status = (
            "🎉 مبارک!\n"
            "تاسو د فلم نشرولو اجازه ترلاسه کړې ده."
        )

    else:

        remaining = max(
            target - count,
            0,
        )

        status = (
            f"🔒 لا {remaining} کسان پکار دي "
            "تر څو د فلم نشرولو اجازه ترلاسه کړئ."
        )

    text = (
        "👥 <b>Referral System</b>\n\n"
        f"👤 ستا Referral: <b>{count}</b>\n"
        f"🎯 هدف: <b>{target}</b>\n\n"
        f"{status}\n\n"
        "🔗 <b>ستاسو شخصي لینک:</b>\n"
        f"<code>{link}</code>\n\n"
        "📢 هر نوی کس باید ستاسو د همدې لینک له لارې "
        "زموږ @afghanproduction چینل ته Join شي."
    )

    await callback.message.answer(
        text,
        parse_mode="HTML",
    )

    await callback.answer()


# =========================================================
# PUBLISH BUTTON
# =========================================================

@dp.callback_query(
    F.data == "publish"
)
async def publish_callback(
    callback: CallbackQuery,
):

    if not await callback_has_access(
        callback
    ):
        return

    user = callback.from_user

    allowed = await can_publish(
        user.id
    )

    if not allowed:

        count = await get_referral_count(
            user.id
        )

        remaining = max(
            settings.REFERRAL_TARGET - count,
            0,
        )

        await callback.answer(
            f"❌ لا {remaining} Referral پکار دي.",
            show_alert=True,
        )

        return

    pending_submissions[
        user.id
    ] = {
        "step": "video",
        "user_id": user.id,
    }

    await callback.message.answer(
        "📤 <b>د فلم نشرولو سیستم</b>\n\n"
        "لومړی خپل فلم د <b>Video</b> یا <b>Document</b> په توګه راولېږئ.\n\n"
        "📌 وروسته به له تاسو څخه:\n"
        "1️⃣ نوم\n"
        "2️⃣ کال\n"
        "3️⃣ کیفیت\n"
        "4️⃣ ژانر\n"
        "5️⃣ ژبه\n"
        "6️⃣ Poster\n"
        "7️⃣ Description\n"
        "وغوښتل شي.",
        parse_mode="HTML",
    )

    await callback.answer()


# =========================================================
# SEARCH HELP
# =========================================================

@dp.callback_query(
    F.data == "search_help"
)
async def search_help_callback(
    callback: CallbackQuery,
):

    if not await callback_has_access(
        callback
    ):
        return

    await callback.message.answer(
        "🔎 <b>د فلم لټون</b>\n\n"
        "د فلم نوم دلته راولېږئ.\n\n"
        "مثال:\n"
        "<code>جب هېري ميټ سجل</code>\n\n"
        "🖼️ تاسو کولای شئ د فلم Poster هم راولېږئ.",
        parse_mode="HTML",
    )

    await callback.answer()


# =========================================================
# PASHTO FILMS
# =========================================================

@dp.callback_query(
    F.data == "pashto_films"
)
async def pashto_films_callback(
    callback: CallbackQuery,
):

    if not await callback_has_access(
        callback
    ):
        return

    films = await get_category_films(
        "pashto",
        limit=20,
    )

    if not films:

        await callback.message.answer(
            "😔 تر اوسه پښتو ترجمه فلمونه نشته."
        )

        await callback.answer()

        return

    await callback.message.answer(
        "🎬 <b>پښتو ترجمه فلمونه</b>",
        parse_mode="HTML",
    )

    for film in films:

        await send_film_card(
            callback.message,
            film,
        )

    await callback.answer()


# =========================================================
# OFFICIAL FILMS
# =========================================================

@dp.callback_query(
    F.data == "official_films"
)
async def official_films_callback(
    callback: CallbackQuery,
):

    if not await callback_has_access(
        callback
    ):
        return

    films = await get_official_films(
        limit=20,
    )

    if not films:

        await callback.message.answer(
            "😔 تر اوسه زموږ فلمونه نشته."
        )

        await callback.answer()

        return

    await callback.message.answer(
        "📢 <b>زموږ فلمونه</b>",
        parse_mode="HTML",
    )

    for film in films:

        await send_film_card(
            callback.message,
            film,
        )

    await callback.answer()


# =========================================================
# REFERRAL LEADERS
# =========================================================

@dp.callback_query(
    F.data == "leaders"
)
async def leaders_callback(
    callback: CallbackQuery,
):

    if not await callback_has_access(
        callback
    ):
        return

    leaders = await get_referral_leaders(
        limit=20
    )

    if not leaders:

        await callback.message.answer(
            "🏆 تر اوسه Referral معلومات نشته."
        )

        await callback.answer()

        return

    text = (
        "🏆 <b>Referral Leaders</b>\n\n"
    )

    for index, user in enumerate(
        leaders,
        start=1,
    ):

        name = (
            user.first_name
            or user.username
            or str(user.telegram_id)
        )

        text += (
            f"{index}. "
            f"<b>{name}</b> — "
            f"{user.referral_count} 👥\n"
        )

    await callback.message.answer(
        text,
        parse_mode="HTML",
    )

    await callback.answer()


# =========================================================
# SEND FILM CARD
# =========================================================

async def send_film_card(
    message: Message,
    film,
):

    text = (
        f"🎬 <b>{film.title}</b>\n\n"
        f"📅 کال: {film.year or '—'}\n"
        f"⚙️ کیفیت: {film.quality or '—'}\n"
        f"🎭 ژانر: {film.genre or '—'}\n"
        f"🔊 ژبه: {film.language or '—'}\n"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 فلم ترلاسه کړه",
                    callback_data=f"getfilm:{film.id}",
                )
            ]
        ]
    )

    if film.poster_file_id:

        try:

            await message.answer_photo(
                photo=film.poster_file_id,
                caption=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )

            return

        except Exception as error:

            logger.warning(
                "Poster send failed: %s",
                error,
            )

    await message.answer(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


# =========================================================
# SEND FILM
# =========================================================

async def send_film(
    message: Message,
    film,
):

    await message.answer(
        "📥 فلم درلېږل کېږي..."
    )

    caption = (
        f"🎬 <b>{film.title}</b>\n\n"
        f"📅 کال: {film.year or '—'}\n"
        f"⚙️ کیفیت: {film.quality or '—'}\n"
        f"🎭 ژانر: {film.genre or '—'}\n"
        f"🔊 ژبه: {film.language or '—'}\n\n"
        "🎬 <b>ALL PRODUCTION FILMS</b>"
    )

    try:

        await message.answer_video(
            video=film.video_file_id,
            caption=caption,
            parse_mode="HTML",
        )

    except Exception:

        try:

            await message.answer_document(
                document=film.video_file_id,
                caption=caption,
                parse_mode="HTML",
            )

        except Exception as error:

            logger.error(
                "Film delivery failed: %s",
                error,
            )

            await message.answer(
                "❌ فلم ونه لېږل شو."
            )


# =========================================================
# GET FILM CALLBACK
# =========================================================

@dp.callback_query(
    F.data.startswith("getfilm:")
)
async def get_film_callback(
    callback: CallbackQuery,
):

    if not await callback_has_access(
        callback
    ):
        return

    try:

        film_id = int(
            callback.data.split(
                ":",
                1,
            )[1]
        )

    except Exception:

        await callback.answer(
            "❌ ناسم Film ID.",
            show_alert=True,
        )

        return

    film = await get_film(
        film_id
    )

    if not film:

        await callback.answer(
            "❌ فلم پیدا نه شو.",
            show_alert=True,
        )

        return

    await callback.answer()

    await send_film(
        callback.message,
        film,
    )


# =========================================================
# TEXT SEARCH
# =========================================================

@dp.message(
    F.text,
    ~F.text.startswith("/"),
)
async def text_search_handler(
    message: Message,
):

    user = message.from_user

    if user is None:
        return

    # -----------------------------------------------------
    # ACCESS
    # -----------------------------------------------------

    if not await check_access(
        user.id
    ):

        await message.answer(
            "🔐 لومړی @ALL_PASHTO ته Join شئ.",
            reply_markup=access_keyboard(),
        )

        return

    # -----------------------------------------------------
    # ACTIVE PUBLISH FLOW
    # -----------------------------------------------------

    state = pending_submissions.get(
        user.id
    )

    if state:

        await handle_submission_text(
            message,
            state,
        )

        return

    # -----------------------------------------------------
    # NORMAL FILM SEARCH
    # -----------------------------------------------------

    query = message.text.strip()

    if len(query) < 2:

        await message.answer(
            "🔎 لږ تر لږه ۲ حروف ولیکئ."
        )

        return

    films = await search_films(
        query,
        limit=20,
    )

    if not films:

        await message.answer(
            "😔 د دې نوم فلم پیدا نه شو."
        )

        return

    await message.answer(
        f"🔎 <b>د «{query}» لټون پایلې:</b>",
        parse_mode="HTML",
    )

    for film in films:

        await send_film_card(
            message,
            film,
        )


# =========================================================
# SUBMISSION TEXT FLOW
# =========================================================

async def handle_submission_text(
    message: Message,
    state: dict[str, Any],
):

    user = message.from_user

    if user is None:
        return

    text = message.text.strip()

    step = state.get(
        "step"
    )

    # -----------------------------------------------------
    # TITLE
    # -----------------------------------------------------

    if step == "title":

        state["title"] = text

        state["step"] = "year"

        await message.answer(
            "📅 اوس د فلم <b>کال</b> راولېږئ.\n\n"
            "مثال: <code>2017</code>\n"
            "که کال نه لرئ، <code>-</code> ولیکئ.",
            parse_mode="HTML",
        )

        return

    # -----------------------------------------------------
    # YEAR
    # -----------------------------------------------------

    if step == "year":

        state["year"] = (
            None if text == "-" else text
        )

        state["step"] = "quality"

        await message.answer(
            "⚙️ اوس د فلم <b>کیفیت</b> راولېږئ.\n\n"
            "مثال:\n"
            "<code>720p</code>\n"
            "<code>1080p</code>\n"
            "یا <code>-</code>",
            parse_mode="HTML",
        )

        return

    # -----------------------------------------------------
    # QUALITY
    # -----------------------------------------------------

    if step == "quality":

        state["quality"] = (
            None if text == "-" else text
        )

        state["step"] = "genre"

        await message.answer(
            "🎭 اوس د فلم <b>ژانر</b> ولیکئ.\n\n"
            "مثال:\n"
            "<code>عاشقانه، مسخراچي</code>\n"
            "یا <code>-</code>",
            parse_mode="HTML",
        )

        return

    # -----------------------------------------------------
    # GENRE
    # -----------------------------------------------------

    if step == "genre":

        state["genre"] = (
            None if text == "-" else text
        )

        state["step"] = "language"

        await message.answer(
            "🔊 اوس د فلم <b>ژبه</b> ولیکئ.\n\n"
            "مثال:\n"
            "<code>پښتو</code>\n"
            "یا <code>پښتو ژباړه</code>",
            parse_mode="HTML",
        )

        return

    # -----------------------------------------------------
    # LANGUAGE
    # -----------------------------------------------------

    if step == "language":

        state["language"] = (
            None if text == "-" else text
        )

        state["step"] = "poster"

        await message.answer(
            "🖼️ اوس د فلم <b>Poster</b> عکس راولېږئ.\n\n"
            "که Poster نه لرئ، <code>-</code> ولیکئ.",
            parse_mode="HTML",
        )

        return

    # -----------------------------------------------------
    # POSTER SKIP
    # -----------------------------------------------------

    if step == "poster":

        if text == "-":

            state["poster_file_id"] = None
            state["poster_file_unique_id"] = None
            state["poster_hash"] = None

            state["step"] = "description"

            await message.answer(
                "📝 اوس د فلم <b>Description</b> ولیکئ.\n\n"
                "که Description نه غواړئ، <code>-</code> ولیکئ.",
                parse_mode="HTML",
            )

            return

        await message.answer(
            "🖼️ دلته باید د فلم Poster عکس راولېږئ."
        )

        return

    # -----------------------------------------------------
    # DESCRIPTION
    # -----------------------------------------------------

    if step == "description":

        state["description"] = (
            None if text == "-" else text
        )

        await finish_submission(
            message,
            state,
        )

        return


# =========================================================
# VIDEO UPLOAD
# =========================================================

@dp.message(
    F.video
)
async def video_upload_handler(
    message: Message,
):

    user = message.from_user

    if user is None:
        return

    if not await check_access(
        user.id
    ):

        await message.answer(
            "🔐 لومړی @ALL_PASHTO ته Join شئ.",
            reply_markup=access_keyboard(),
        )

        return

    allowed = await can_publish(
        user.id
    )

    if not allowed:

        count = await get_referral_count(
            user.id
        )

        remaining = max(
            settings.REFERRAL_TARGET - count,
            0,
        )

        await message.answer(
            f"🔒 تاسو لا د فلم نشرولو اجازه نه لرئ.\n\n"
            f"👥 Referral: {count}/{settings.REFERRAL_TARGET}\n"
            f"🎯 پاتې: {remaining}"
        )

        return

    video = message.video

    pending_submissions[
        user.id
    ] = {
        "step": "title",
        "user_id": user.id,
        "video_file_id": video.file_id,
        "video_file_unique_id": video.file_unique_id,
    }

    await message.answer(
        "✅ فلم ترلاسه شو.\n\n"
        "🎬 اوس د فلم <b>نوم</b> راولېږئ.",
        parse_mode="HTML",
    )


# =========================================================
# DOCUMENT VIDEO UPLOAD
# =========================================================

@dp.message(
    F.document
)
async def document_upload_handler(
    message: Message,
):

    user = message.from_user

    if user is None:
        return

    if not await check_access(
        user.id
    ):

        await message.answer(
            "🔐 لومړی @ALL_PASHTO ته Join شئ.",
            reply_markup=access_keyboard(),
        )

        return

    allowed = await can_publish(
        user.id
    )

    if not allowed:

        count = await get_referral_count(
            user.id
        )

        await message.answer(
            f"🔒 د فلم نشرولو لپاره "
            f"{settings.REFERRAL_TARGET - count} "
            f"Referral نور پکار دي."
        )

        return

    document = message.document

    pending_submissions[
        user.id
    ] = {
        "step": "title",
        "user_id": user.id,
        "video_file_id": document.file_id,
        "video_file_unique_id": document.file_unique_id,
    }

    await message.answer(
        "✅ فلم ترلاسه شو.\n\n"
        "🎬 اوس د فلم <b>نوم</b> راولېږئ.",
        parse_mode="HTML",
    )


# =========================================================
# POSTER PHOTO
# =========================================================

@dp.message(
    F.photo
)
async def poster_photo_handler(
    message: Message,
):

    user = message.from_user

    if user is None:
        return

    state = pending_submissions.get(
        user.id
    )

    # -----------------------------------------------------
    # IF USER IS NOT PUBLISHING
    # THEN USE IMAGE SEARCH
    # -----------------------------------------------------

    if not state:

        if not await check_access(
            user.id
        ):

            await message.answer(
                "🔐 لومړی @ALL_PASHTO ته Join شئ.",
                reply_markup=access_keyboard(),
            )

            return

        try:

            photo = message.photo[-1]

            telegram_file = await bot.get_file(
                photo.file_id
            )

            buffer = bytearray()

            await bot.download_file(
                telegram_file.file_path,
                buffer,
            )

            image_hash = calculate_image_hash(
                bytes(buffer)
            )

            films = await search_by_image_hash(
                image_hash
            )

            if not films:

                await message.answer(
                    "😔 د دې Poster مطابق فلم پیدا نه شو."
                )

                return

            await message.answer(
                "🖼️ <b>د عکس له لارې پیدا شوي فلمونه:</b>",
                parse_mode="HTML",
            )

            for film in films:

                await send_film_card(
                    message,
                    film,
                )

        except Exception as error:

            logger.exception(
                "Image search failed: %s",
                error,
            )

            await message.answer(
                "❌ د عکس لټون کې ستونزه رامنځته شوه."
            )

        return

    # -----------------------------------------------------
    # POSTER FOR PUBLISHING
    # -----------------------------------------------------

    if state.get("step") != "poster":

        await message.answer(
            "📌 اوس د Poster مرحله نه ده."
        )

        return

    try:

        photo = message.photo[-1]

        telegram_file = await bot.get_file(
            photo.file_id
        )

        buffer = bytearray()

        await bot.download_file(
            telegram_file.file_path,
            buffer,
        )

        image_bytes = bytes(
            buffer
        )

        poster_hash = calculate_image_hash(
            image_bytes
        )

        state["poster_file_id"] = (
            photo.file_id
        )

        state["poster_file_unique_id"] = (
            photo.file_unique_id
        )

        state["poster_hash"] = (
            poster_hash
        )

        state["step"] = "description"

        await message.answer(
            "✅ Poster ثبت شو.\n\n"
            "📝 اوس د فلم <b>Description</b> راولېږئ.\n\n"
            "که Description نه غواړئ، <code>-</code> ولیکئ.",
            parse_mode="HTML",
        )

    except Exception as error:

        logger.exception(
            "Poster processing failed: %s",
            error,
        )

        await message.answer(
            "❌ د Poster پروسس کې ستونزه وشوه."
        )


# =========================================================
# FINISH FILM SUBMISSION
# =========================================================

async def finish_submission(
    message: Message,
    state: dict[str, Any],
):

    user = message.from_user

    if user is None:
        return

    title = state.get(
        "title"
    )

    if not title:

        await message.answer(
            "❌ د فلم نوم نشته."
        )

        return

    try:

        film = await create_film(
            title=title,
            video_file_id=state[
                "video_file_id"
            ],
            video_file_unique_id=state.get(
                "video_file_unique_id"
            ),
            uploader_id=user.id,
            year=state.get("year"),
            quality=state.get("quality"),
            genre=state.get("genre"),
            language=state.get("language"),
            description=state.get(
                "description"
            ),
            category="pashto",
            poster_file_id=state.get(
                "poster_file_id"
            ),
            poster_file_unique_id=state.get(
                "poster_file_unique_id"
            ),
            poster_hash=state.get(
                "poster_hash"
            ),
            approved=settings.AUTO_APPROVE_FILMS,
            official=False,
        )

        pending_submissions.pop(
            user.id,
            None,
        )

        if settings.AUTO_APPROVE_FILMS:

            await message.answer(
                "🎉 <b>فلم په بریالیتوب ثبت او نشر شو!</b>\n\n"
                f"🎬 {film.title}\n"
                f"🆔 Film ID: <code>{film.id}</code>\n\n"
                "✅ اوس کاروونکي کولای شي فلم پیدا او ترلاسه کړي.",
                parse_mode="HTML",
            )

        else:

            await message.answer(
                "✅ <b>فلم ثبت شو.</b>\n\n"
                "⏳ اوس د Admin تایید ته انتظار کوي.",
                parse_mode="HTML",
            )

        # -------------------------------------------------
        # ADMIN NOTIFICATION
        # -------------------------------------------------

        for admin_id in settings.admin_ids:

            try:

                await bot.send_message(
                    admin_id,
                    (
                        "🎬 <b>نوی فلم ثبت شو</b>\n\n"
                        f"🆔 ID: <code>{film.id}</code>\n"
                        f"🎬 نوم: <b>{film.title}</b>\n"
                        f"👤 Uploader: <code>{user.id}</code>\n"
                        f"📅 کال: {film.year or '—'}\n"
                        f"⚙️ کیفیت: {film.quality or '—'}\n"
                    ),
                    parse_mode="HTML",
                )

            except Exception as error:

                logger.warning(
                    "Admin notification failed: %s",
                    error,
                )

    except Exception as error:

        logger.exception(
            "Film creation failed: %s",
            error,
        )

        await message.answer(
            "❌ فلم ثبت نه شو.\n"
            "مهرباني وکړئ بیا هڅه وکړئ."
        )


# =========================================================
# CANCEL PUBLISHING
# =========================================================

@dp.message(
    Command("cancel")
)
async def cancel_handler(
    message: Message,
):

    user = message.from_user

    if user is None:
        return

    if user.id in pending_submissions:

        pending_submissions.pop(
            user.id,
            None,
        )

        await message.answer(
            "❌ د فلم نشرولو پروسه لغوه شوه."
        )

    else:

        await message.answer(
            "ℹ️ کومه فعاله پروسه نشته."
        )


# =========================================================
# ADMIN COMMAND
# =========================================================

@dp.message(
    Command("admin")
)
async def admin_handler(
    message: Message,
):

    user = message.from_user

    if user is None:
        return

    if not is_admin(
        user.id
    ):

        await message.answer(
            "❌ تاسو Admin نه یاست."
        )

        return

    stats = await get_statistics()

    text = (
        "👑 <b>ADMIN PANEL</b>\n\n"
        f"👥 Users: <b>{stats['users']}</b>\n"
        f"📤 Publishers: <b>{stats['publishers']}</b>\n"
        f"🎬 Films: <b>{stats['films']}</b>\n"
        f"✅ Approved: <b>{stats['approved_films']}</b>\n"
        f"📢 Channels: <b>{stats['channels']}</b>\n\n"
        "🌐 Web Admin Panel به د Mini App/Admin URL له لارې هم کارول کېدای شي."
    )

    await message.answer(
        text,
        parse_mode="HTML",
    )


# =========================================================
# ADMIN STATS
# =========================================================

@dp.message(
    Command("stats")
)
async def stats_handler(
    message: Message,
):

    user = message.from_user

    if user is None:
        return

    if not is_admin(
        user.id
    ):
        return

    stats = await get_statistics()

    await message.answer(
        "📊 <b>Statistics</b>\n\n"
        f"👥 Users: {stats['users']}\n"
        f"📤 Publishers: {stats['publishers']}\n"
        f"🎬 Films: {stats['films']}\n"
        f"✅ Approved Films: {stats['approved_films']}\n"
        f"📢 Active Channels: {stats['channels']}",
        parse_mode="HTML",
    )


# =========================================================
# ADMIN BROADCAST
# =========================================================

@dp.message(
    Command("broadcast")
)
async def broadcast_handler(
    message: Message,
):

    user = message.from_user

    if user is None:
        return

    if not is_admin(
        user.id
    ):
        return

    if not message.text:

        await message.answer(
            "استعمال:\n"
            "<code>/broadcast ستاسو پیغام</code>",
            parse_mode="HTML",
        )

        return

    parts = message.text.split(
        maxsplit=1
    )

    if len(parts) < 2:

        await message.answer(
            "❌ د Broadcast متن ولیکئ."
        )

        return

    broadcast_text = parts[1]

    # Import here to avoid unnecessary model import
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import User

    async with SessionLocal() as session:

        result = await session.execute(
            select(User.telegram_id).where(
                User.is_blocked.is_(False)
            )
        )

        user_ids = result.scalars().all()

    sent = 0
    failed = 0

    await message.answer(
        f"📢 Broadcast شروع شو.\n"
        f"👥 Users: {len(user_ids)}"
    )

    for telegram_id in user_ids:

        try:

            await bot.send_message(
                telegram_id,
                broadcast_text,
            )

            sent += 1

        except Exception:

            failed += 1

        await asyncio.sleep(
            0.05
        )

    await message.answer(
        "✅ Broadcast ختم شو.\n\n"
        f"📨 Sent: {sent}\n"
        f"❌ Failed: {failed}"
    )


# =========================================================
# CHANNEL MEMBER / REFERRAL TRACKING
# =========================================================

@dp.chat_member()
async def channel_member_handler(
    event: ChatMemberUpdated,
):

    try:

        if (
            event.chat.username
            and event.chat.username.lower()
            != settings.MAIN_CHANNEL.lstrip("@").lower()
        ):
            return

        old_status = event.old_chat_member.status
        new_status = event.new_chat_member.status

        # Only new join
        joined_statuses = {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        }

        if new_status not in joined_statuses:
            return

        if old_status in joined_statuses:
            return

        invited_user = event.new_chat_member.user

        if invited_user is None:
            return

        # Telegram gives the invite link used by the user
        invite_link = event.invite_link

        if not invite_link:
            logger.info(
                "Direct join/no invite link: %s",
                invited_user.id,
            )

            return

        # -------------------------------------------------
        # FIND INVITER FROM DATABASE
        # -------------------------------------------------

        from sqlalchemy import select

        from app.database import SessionLocal
        from app.models import User

        inviter_id = None

        async with SessionLocal() as session:

            result = await session.execute(
                select(User.telegram_id).where(
                    User.referral_link
                    == invite_link.invite_link
                )
            )

            inviter_id = result.scalar_one_or_none()

        if not inviter_id:

            logger.info(
                "Invite link not registered: %s",
                invite_link.invite_link,
            )

            return

        from app.referrals import (
            process_channel_join,
        )

        counted = await process_channel_join(
            inviter_id=inviter_id,
            invited_id=invited_user.id,
            invite_link=invite_link.invite_link,
        )

        if counted:

            logger.info(
                "Referral counted: %s -> %s",
                inviter_id,
                invited_user.id,
            )

            try:

                await bot.send_message(
                    inviter_id,
                    (
                        "🎉 <b>Referral نوی کس ثبت شو!</b>\n\n"
                        f"👥 اوس ستاسو Referral شمېر "
                        f"<b>{await get_referral_count(inviter_id)}</b> دی.\n"
                        f"🎯 هدف: <b>{settings.REFERRAL_TARGET}</b>"
                    ),
                    parse_mode="HTML",
                )

            except Exception:
                pass

    except Exception as error:

        logger.exception(
            "Channel member handler error: %s",
            error,
        )


# =========================================================
# FALLBACK
# =========================================================

@dp.message()
async def fallback_handler(
    message: Message,
):

    user = message.from_user

    if user is None:
        return

    if not await check_access(
        user.id
    ):

        await message.answer(
            "🔐 لومړی @ALL_PASHTO ته Join شئ.",
            reply_markup=access_keyboard(),
        )

        return

    await message.answer(
        "🎬 <b>ALL PRODUCTION FILMS</b>\n\n"
        "له لاندې Menu څخه انتخاب وکړئ:",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


# =========================================================
# START BOT
# =========================================================

async def start_bot():

    logger.info(
        "Starting ALL PRODUCTION FILMS bot..."
    )

    await bot.delete_webhook(
        drop_pending_updates=False
    )

    await dp.start_polling(
        bot,
        allowed_updates=[
            "message",
            "callback_query",
            "chat_member",
        ],
    )


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    async def main():

        await init_db()

        await start_bot()

    asyncio.run(
        main()
        )
