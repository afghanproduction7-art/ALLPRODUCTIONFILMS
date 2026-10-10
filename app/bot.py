import asyncio
import logging
from io import BytesIO

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    ChatMemberUpdated,
)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import User, Film
from app.films import (
    create_film,
    get_latest_films,
    search_films,
    get_film,
    delete_film,
    approve_film,
    reject_film,
)
from app.referrals import (
    get_or_create_user,
    process_channel_join,
)
from app.settings_db import (
    get_main_channel,
    get_access_channel,
    get_referral_target,
    get_auto_approve,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()
dp = Dispatcher()
dp.include_router(router)

bot_instance: Bot | None = None
pending_uploads: dict[int, dict] = {}


def is_admin(user_id: int) -> bool:
    raw = getattr(settings, "ADMIN_IDS", "")
    if isinstance(raw, (list, tuple, set)):
        return user_id in {int(x) for x in raw}
    try:
        return user_id in {
            int(x.strip())
            for x in str(raw).split(",")
            if x.strip()
        }
    except (TypeError, ValueError):
        return False


def mini_app_keyboard():
    url = str(getattr(settings, "APP_URL", "")).rstrip("/")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎬 فلمونه پرانیزئ",
                    web_app=WebAppInfo(url=url),
                )
            ]
        ]
    )


def main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎬 فلمونه",
                    web_app=WebAppInfo(
                        url=str(settings.APP_URL).rstrip("/")
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔎 لټون",
                    callback_data="search_films",
                ),
                InlineKeyboardButton(
                    text="🆕 نوي فلمونه",
                    callback_data="latest_films",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👥 زما دعوتونه",
                    callback_data="my_referrals",
                ),
                InlineKeyboardButton(
                    text="📤 فلم خپرول",
                    callback_data="publish_film",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ اډمین",
                    callback_data="admin_menu",
                )
            ],
        ]
    )


def admin_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 انتظار فلمونه",
                    callback_data="admin_pending",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎬 وروستي فلمونه",
                    callback_data="admin_films",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 شمېرې",
                    callback_data="admin_stats",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌐 د اډمین پاڼه",
                    web_app=WebAppInfo(
                        url=str(settings.APP_URL).rstrip("/") + "/admin"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ بېرته",
                    callback_data="back_home",
                )
            ],
        ]
    )


async def user_has_access(user_id: int) -> bool:
    channel = await get_access_channel()
    if not channel:
        return True

    username = str(channel).strip()
    if not username.startswith("@"):
        username = "@" + username

    try:
        member = await bot_instance.get_chat_member(
            chat_id=username,
            user_id=user_id,
        )
        return member.status not in {"left", "kicked"}
    except Exception as exc:
        logger.warning("Access check failed for %s: %s", username, exc)
        return False


async def send_access_message(message: Message):
    channel = await get_access_channel()
    username = str(channel or "@ALL_PASHTO").strip()
    if not username.startswith("@"):
        username = "@" + username

    await message.answer(
        "🔒 د بوټ د کارولو لپاره لومړی زموږ اړین چینل کې ګډون وکړئ.\n"
        "له ګډون وروسته /start ولیکئ.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📢 چینل کې ګډون",
                        url=f"https://t.me/{username.lstrip('@')}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔄 بیا کتنه",
                        callback_data="check_access",
                    )
                ],
            ]
        ),
    )


async def send_film_card(message: Message, film: Film):
    caption = (
        f"🎬 <b>{film.title}</b>\n"
        f"📅 کال: {film.year or 'نامعلوم'}\n"
        f"🎞 کیفیت: {film.quality or 'نامعلوم'}\n"
        f"🎭 ژانر: {film.genre or 'نامعلوم'}\n"
        f"🔊 ژبه: {film.language or 'نامعلوم'}\n\n"
        f"{film.description or ''}"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ فلم ترلاسه کول",
                    callback_data=f"film:{film.id}",
                )
            ]
        ]
    )

    if film.poster_file_id:
        try:
            await message.answer_photo(
                photo=film.poster_file_id,
                caption=caption,
                reply_markup=keyboard,
            )
            return
        except Exception:
            logger.exception("Could not send poster for film %s", film.id)

    await message.answer(caption, reply_markup=keyboard)


@router.message(CommandStart())
async def start_handler(message: Message):
    if not message.from_user:
        return

    if not await user_has_access(message.from_user.id):
        await send_access_message(message)
        return

    args = (message.text or "").split(maxsplit=1)
    referrer_id = None

    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer_id = int(args[1][4:])
        except ValueError:
            referrer_id = None

    try:
        await get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            referrer_id=referrer_id,
        )
    except TypeError:
        await get_or_create_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.first_name,
        )
    except Exception:
        logger.exception("User creation failed")

    await message.answer(
        f"سلام {message.from_user.first_name or 'ملګري'}! 👋\n"
        "🎬 ALL PRODUCTION FILMS ته ښه راغلاست.",
        reply_markup=main_keyboard(),
    )


@router.message(Command("admin"))
async def admin_command(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        await message.answer("⛔ تاسې د اډمین اجازه نه لرئ.")
        return

    await message.answer(
        "⚙️ د اډمین کنټرول پینل",
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "admin_menu")
async def admin_menu_callback(callback: CallbackQuery):
    if not callback.from_user or not is_admin(callback.from_user.id):
        await callback.answer("اجازه نشته.", show_alert=True)
        return

    await callback.message.answer(
        "⚙️ د اډمین کنټرول پینل",
        reply_markup=admin_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "back_home")
async def back_home_callback(callback: CallbackQuery):
    await callback.message.answer(
        "اصلي مینو:",
        reply_markup=main_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "check_access")
async def check_access_callback(callback: CallbackQuery):
    if await user_has_access(callback.from_user.id):
        await callback.message.answer(
            "✅ ګډون مو تایید شو.",
            reply_markup=main_keyboard(),
        )
        await callback.answer("تایید شو.")
    else:
        await callback.answer(
            "لا هم اړین چینل کې ګډون وکړئ.",
            show_alert=True,
        )


@router.callback_query(F.data == "latest_films")
async def latest_films_callback(callback: CallbackQuery):
    if not await user_has_access(callback.from_user.id):
        await callback.answer("لومړی چینل کې ګډون وکړئ.", show_alert=True)
        return

    films = await get_latest_films(10)
    if not films:
        await callback.message.answer("تر اوسه فلمونه نشته.")
    else:
        await callback.message.answer("🆕 وروستي فلمونه:")
        for film in films:
            await send_film_card(callback.message, film)

    await callback.answer()


@router.callback_query(F.data == "search_films")
async def search_callback(callback: CallbackQuery):
    pending_uploads[callback.from_user.id] = {"step": "search"}
    await callback.message.answer("🔎 د فلم نوم راولېږئ.")
    await callback.answer()


@router.message(F.text)
async def text_handler(message: Message):
    if not message.from_user:
        return

    user_id = message.from_user.id
    state = pending_uploads.get(user_id)

    if not state:
        return

    step = state.get("step")

    if step == "search":
        results = await search_films(message.text, limit=10)
        pending_uploads.pop(user_id, None)

        if not results:
            await message.answer("د دې نوم فلم ونه موندل شو.")
            return

        for film in results:
            await send_film_card(message, film)
        return

    if step == "film_title":
        state["title"] = message.text.strip()
        state["step"] = "year"
        await message.answer("📅 د فلم کال ولیکئ؛ که نه وي 0 ولیکئ.")
        return

    if step == "year":
        state["year"] = None if message.text.strip() == "0" else message.text.strip()
        state["step"] = "quality"
        await message.answer("🎞 کیفیت ولیکئ؛ که نه وي 0 ولیکئ.")
        return

    if step == "quality":
        state["quality"] = None if message.text.strip() == "0" else message.text.strip()
        state["step"] = "genre"
        await message.answer("🎭 ژانر ولیکئ؛ که نه وي 0 ولیکئ.")
        return

    if step == "genre":
        state["genre"] = None if message.text.strip() == "0" else message.text.strip()
        state["step"] = "language"
        await message.answer("🔊 ژبه ولیکئ؛ که نه وي 0 ولیکئ.")
        return

    if step == "language":
        state["language"] = None if message.text.strip() == "0" else message.text.strip()
        state["step"] = "description"
        await message.answer("📝 لنډه پېژندنه ولیکئ؛ که نه وي 0 ولیکئ.")
        return

    if step == "description":
        state["description"] = None if message.text.strip() == "0" else message.text.strip()
        state["step"] = "poster"
        await message.answer("🖼 پوسټر راولېږئ، یا 0 ولیکئ.")
        return

    if step == "poster":
        if message.text.strip() == "0":
            state["poster_file_id"] = None
            await save_pending_film(message, user_id)
        else:
            await message.answer("مهرباني وکړئ پوسټر عکس یا 0 راولېږئ.")


@router.message(F.photo)
async def photo_handler(message: Message):
    if not message.from_user:
        return

    state = pending_uploads.get(message.from_user.id)
    if not state:
        return

    if state.get("step") == "poster":
        state["poster_file_id"] = message.photo[-1].file_id
        state["step"] = "saving"
        await save_pending_film(message, message.from_user.id)


@router.message(F.video | F.document)
async def upload_handler(message: Message):
    if not message.from_user:
        return

    user_id = message.from_user.id

    if not await user_has_access(user_id):
        await send_access_message(message)
        return

    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user_id)
        )
        user = result.scalar_one_or_none()

    if not user:
        await message.answer("لومړی /start ولیکئ.")
        return

    if not user.can_publish and not is_admin(user_id):
        target = await get_referral_target()
        await message.answer(
            f"🔒 د فلم خپرولو لپاره {target} بریالي دعوتونه پکار دي."
        )
        return

    if message.video:
        file_id = message.video.file_id
        unique_id = message.video.file_unique_id
    elif message.document:
        mime = message.document.mime_type or ""
        if not mime.startswith("video/"):
            await message.answer("یوازې ویډیو یا ویډیويي فایل راولېږئ.")
            return
        file_id = message.document.file_id
        unique_id = message.document.file_unique_id
    else:
        return

    pending_uploads[user_id] = {
        "step": "film_title",
        "video_file_id": file_id,
        "video_file_unique_id": unique_id,
    }
    await message.answer("🎬 د فلم نوم ولیکئ.")


async def save_pending_film(message: Message, user_id: int):
    state = pending_uploads.get(user_id)
    if not state:
        await message.answer("د فلم د ثبت معلومات ونه موندل شول. بیا هڅه وکړئ.")
        return

    try:
        approved = await get_auto_approve()
        film = await create_film(
            title=state["title"],
            video_file_id=state["video_file_id"],
            video_file_unique_id=state.get("video_file_unique_id"),
            uploader_id=user_id,
            year=state.get("year"),
            quality=state.get("quality"),
            genre=state.get("genre"),
            language=state.get("language"),
            description=state.get("description"),
            poster_file_id=state.get("poster_file_id"),
            approved=bool(approved) or is_admin(user_id),
        )
        pending_uploads.pop(user_id, None)

        await message.answer(
            f"✅ فلم ثبت شو.\nشمېره: {film.id}\n"
            + ("فلم خپرېدو ته چمتو دی." if film.approved else "د اډمین تایید ته انتظار وباسئ.")
        )
    except Exception:
        logger.exception("Saving film failed")
        await message.answer("❌ فلم ثبت نه شو. مهرباني وکړئ بیا هڅه وکړئ.")


@router.callback_query(F.data == "publish_film")
async def publish_callback(callback: CallbackQuery):
    user_id = callback.from_user.id

    if not await user_has_access(user_id):
        await callback.answer("لومړی اړین چینل کې ګډون وکړئ.", show_alert=True)
        return

    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user_id)
        )
        user = result.scalar_one_or_none()

    target = await get_referral_target()

    if not user or (not user.can_publish and not is_admin(user_id)):
        await callback.message.answer(
            f"🔒 د فلم خپرولو لپاره {target} بریالي دعوتونه پکار دي."
        )
    else:
        await callback.message.answer(
            "لومړی فلم د ویډیو یا ویډیويي فایل په توګه راولېږئ."
        )

    await callback.answer()


@router.callback_query(F.data == "my_referrals")
async def referrals_callback(callback: CallbackQuery):
    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = result.scalar_one_or_none()

    if not user:
        await callback.answer("لومړی /start ولیکئ.", show_alert=True)
        return

    target = await get_referral_target()
    bot_username = (await bot_instance.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{user.telegram_id}"

    await callback.message.answer(
        f"👥 ستا دعوتونه: {user.referral_count}\n"
        f"🎯 هدف: {target}\n\n"
        f"🔗 ستا د دعوت لینک:\n{link}"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("film:"))
async def film_callback(callback: CallbackQuery):
    try:
        film_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("د فلم شمېره ناسمه ده.", show_alert=True)
        return

    film = await get_film(film_id)
    if not film:
        await callback.answer("فلم ونه موندل شو.", show_alert=True)
        return

    try:
        await bot_instance.send_video(
            chat_id=callback.from_user.id,
            video=film.video_file_id,
            caption=film.title,
        )
    except Exception:
        try:
            await bot_instance.send_document(
                chat_id=callback.from_user.id,
                document=film.video_file_id,
                caption=film.title,
            )
        except Exception:
            logger.exception("Sending film failed")
            await callback.answer("فلم ونه لېږل شو.", show_alert=True)
            return

    await callback.answer("فلم شخصي پیغام ته ولېږل شو.")


@router.callback_query(F.data == "admin_pending")
async def admin_pending_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه نشته.", show_alert=True)
        return

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film)
            .where(Film.approved.is_(False))
            .order_by(Film.created_at.desc())
            .limit(20)
        )
        films = result.scalars().all()

    if not films:
        await callback.message.answer("د تایید لپاره فلم نشته.")
    else:
        for film in films:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ تایید",
                            callback_data=f"approve:{film.id}",
                        ),
                        InlineKeyboardButton(
                            text="❌ رد",
                            callback_data=f"reject:{film.id}",
                        ),
                    ]
                ]
            )
            await callback.message.answer(
                f"🎬 {film.title}\nشمېره: {film.id}",
                reply_markup=keyboard,
            )

    await callback.answer()


@router.callback_query(F.data.startswith("approve:"))
async def approve_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه نشته.", show_alert=True)
        return

    try:
        film_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("ناسم ID.", show_alert=True)
        return

    success = await approve_film(film_id)
    await callback.answer(
        "فلم تایید شو." if success else "فلم ونه موندل شو.",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("reject:"))
async def reject_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه نشته.", show_alert=True)
        return

    try:
        film_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("ناسم ID.", show_alert=True)
        return

    success = await reject_film(film_id)
    await callback.answer(
        "فلم رد شو." if success else "فلم ونه موندل شو.",
        show_alert=True,
    )


@router.callback_query(F.data == "admin_films")
async def admin_films_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه نشته.", show_alert=True)
        return

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film).order_by(Film.created_at.desc()).limit(20)
        )
        films = result.scalars().all()

    if not films:
        await callback.message.answer("فلمونه نشته.")
    else:
        for film in films:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🗑 حذف",
                            callback_data=f"delete:{film.id}",
                        )
                    ]
                ]
            )
            await callback.message.answer(
                f"🎬 {film.title}\nID: {film.id}\n"
                f"تایید: {'هو' if film.approved else 'نه'}",
                reply_markup=keyboard,
            )

    await callback.answer()


@router.callback_query(F.data.startswith("delete:"))
async def delete_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه نشته.", show_alert=True)
        return

    try:
        film_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("ناسم ID.", show_alert=True)
        return

    success = await delete_film(film_id)
    await callback.answer(
        "فلم حذف شو." if success else "فلم ونه موندل شو.",
        show_alert=True,
    )


@router.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه نشته.", show_alert=True)
        return

    async with SessionLocal() as session:
        users_result = await session.execute(select(User))
        films_result = await session.execute(select(Film))
        users = len(users_result.scalars().all())
        films = len(films_result.scalars().all())

    await callback.message.answer(
        f"📊 د سیستم شمېرې\n👥 کاروونکي: {users}\n🎬 فلمونه: {films}"
    )
    await callback.answer()


@router.chat_member()
async def chat_member_handler(event: ChatMemberUpdated):
    if event.chat.username:
        main_channel = str(await get_main_channel() or "").lstrip("@").lower()
        if event.chat.username.lower() != main_channel:
            return

    if event.new_chat_member.status not in {"member", "administrator", "creator"}:
        return

    invite_link = None
    if event.invite_link:
        invite_link = event.invite_link.invite_link

    try:
        await process_channel_join(
            event.from_user.id,
            invite_link,
        )
    except TypeError:
        try:
            await process_channel_join(event)
        except Exception:
            logger.exception("Referral join processing failed")
    except Exception:
        logger.exception("Referral join processing failed")


async def start_bot():
    global bot_instance

    bot_instance = Bot(token=settings.BOT_TOKEN)

    try:
        await bot_instance.delete_webhook(drop_pending_updates=False)
        logger.info("Starting Telegram bot polling")
        await dp.start_polling(bot_instance)
    finally:
        await bot_instance.session.close()
