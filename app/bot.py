
import asyncio
import logging
import re
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    ChatMemberUpdated,
)

from app.config import settings
from app import films as film_service
from app import referrals
from app import settings_db


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(router)

BOT_TOKEN = settings.BOT_TOKEN
APP_URL = settings.APP_URL.rstrip("/")
ADMIN_IDS = set(settings.ADMIN_IDS)

MAIN_CHANNEL = str(settings.MAIN_CHANNEL).strip().lstrip("@")
ACCESS_CHANNEL = str(settings.ACCESS_CHANNEL).strip().lstrip("@")

PENDING_FILMS: dict[int, dict[str, Any]] = {}


class FilmUpload(StatesGroup):
    video = State()
    title = State()
    year = State()
    quality = State()
    genre = State()
    language = State()
    description = State()
    poster = State()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def app_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎬 فلمونه وګورئ",
                    web_app=WebAppInfo(url=APP_URL),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔎 د فلمونو لټون",
                    web_app=WebAppInfo(url=APP_URL),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 د فلمونو چینلونه",
                    callback_data="show_channels",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 زما ریفرلونه",
                    callback_data="my_referrals",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎥 فلم خپرول",
                    callback_data="publish_film",
                )
            ],
        ]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎬 د فلمونو مدیریت",
                    web_app=WebAppInfo(url=f"{APP_URL}/admin"),
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ نوی فلم اضافه کول",
                    callback_data="admin_add_film",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏳ د تایید په تمه فلمونه",
                    callback_data="admin_pending",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 چینلونه",
                    callback_data="admin_channels",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ تنظیمات",
                    callback_data="admin_settings",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 اصلي مینو",
                    callback_data="back_home",
                )
            ],
        ]
    )


async def check_membership(bot: Bot, user_id: int) -> bool:
    channel = ACCESS_CHANNEL
    if not channel:
        return True

    try:
        member = await bot.get_chat_member(
            chat_id=f"@{channel}",
            user_id=user_id,
        )
        return member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        }
    except TelegramAPIError as exc:
        logger.warning("Membership check failed: %s", exc)
        return False


async def send_join_prompt(message: Message) -> None:
    channel = ACCESS_CHANNEL or "ALL_PASHTO"
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 چینل کې ګډون",
                    url=f"https://t.me/{channel}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ بیا کتنه",
                    callback_data="check_membership",
                )
            ],
        ]
    )
    await message.answer(
        "🔒 د روباټ د کارولو لپاره لومړی زموږ په اړین چینل کې ګډون وکړئ، "
        "بیا د «بیا کتنه» تڼۍ کېکاږئ.",
        reply_markup=markup,
    )


async def ensure_user(
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
    referrer_id: int | None = None,
):
    try:
        return await referrals.get_or_create_user(
            telegram_id=user_id,
            username=username,
            first_name=first_name,
            referrer_id=referrer_id,
        )
    except Exception:
        logger.exception("Could not create or load user %s", user_id)
        return None


async def start_message(message: Message, bot: Bot, state: FSMContext) -> None:
    await state.clear()

    user = message.from_user
    if not user:
        return

    referrer_id = None
    parts = (message.text or "").split(maxsplit=1)

    if len(parts) == 2:
        payload = parts[1].strip()
        if payload.startswith("ref_"):
            try:
                candidate = int(payload[4:])
                if candidate != user.id:
                    referrer_id = candidate
            except ValueError:
                pass

    await ensure_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        referrer_id=referrer_id,
    )

    if not await check_membership(bot, user.id):
        await send_join_prompt(message)
        return

    welcome = (
        f"سلام {user.first_name or 'ملګري'}! 👋\n\n"
        "🎬 د ALL PRODUCTION FILMS روباټ ته ښه راغلاست.\n\n"
        "دلته فلمونه کتل، لټول او د خپرولو شرایط معلومولای شئ."
    )
    await message.answer(welcome, reply_markup=app_keyboard())


@router.message(CommandStart())
async def start_handler(
    message: Message,
    bot: Bot,
    state: FSMContext,
):
    await start_message(message, bot, state)


@router.message(Command("help"))
async def help_handler(message: Message):
    await message.answer(
        "📌 د روباټ لارښود\n\n"
        "/start — اصلي مینو\n"
        "/films — د فلمونو مینو\n"
        "/referrals — د ریفرل معلومات\n"
        "/publish — د فلم خپرولو غوښتنه\n"
        "/cancel — روان کار لغوه کول\n"
        "/admin — د اډمین مینو"
    )


@router.message(Command("films"))
async def films_command(message: Message, bot: Bot):
    if not message.from_user:
        return
    if not await check_membership(bot, message.from_user.id):
        await send_join_prompt(message)
        return
    await message.answer(
        "🎬 د فلمونو د لیدلو او لټون لپاره لاندې تڼۍ وکاروئ.",
        reply_markup=app_keyboard(),
    )


@router.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    await state.clear()
    PENDING_FILMS.pop(message.from_user.id, None)
    await message.answer("❌ روان کار لغوه شو.")


@router.callback_query(F.data == "back_home")
async def back_home_callback(callback: CallbackQuery, bot: Bot):
    if not callback.from_user:
        await callback.answer()
        return
    if not await check_membership(bot, callback.from_user.id):
        if callback.message:
            await callback.message.answer(
                "🔒 لومړی اړین چینل کې ګډون وکړئ."
            )
        await callback.answer()
        return

    if callback.message:
        await callback.message.answer(
            "🏠 اصلي مینو",
            reply_markup=app_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "check_membership")
async def check_membership_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    if await check_membership(bot, callback.from_user.id):
        await ensure_user(
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.first_name,
        )
        if callback.message:
            await callback.message.answer(
                "✅ ګډون مو تایید شو. اوس مینو کارولای شئ.",
                reply_markup=app_keyboard(),
            )
        await callback.answer("ګډون تایید شو")
    else:
        await callback.answer(
            "تر اوسه ګډون نه دی تایید شوی.",
            show_alert=True,
        )


@router.callback_query(F.data == "my_referrals")
async def referrals_callback(callback: CallbackQuery):
    try:
        count = await referrals.get_referral_count(
            callback.from_user.id
        )
        target = await settings_db.get_referral_target()

        try:
            link = await referrals.create_referral_link(
                callback.from_user.id,
                Bot(token=BOT_TOKEN),
                f"@{MAIN_CHANNEL}",
            )
        except Exception:
            link = ""

        progress = min(int(count), int(target)) if target else int(count)
        text = (
            "👥 ستاسو د ریفرل معلومات\n\n"
            f"✅ بریالي ریفرلونه: {count}\n"
            f"🎯 د فلم خپرولو هدف: {target}\n"
            f"📊 پرمختګ: {progress}/{target}\n\n"
        )

        if link:
            text += f"🔗 ستاسو ځانګړی لینک:\n{link}\n\n"

        if target and count >= target:
            text += "🎉 د فلم خپرولو هدف مو پوره کړی دی."
        else:
            text += (
                "📌 د هدف پوره کولو لپاره ملګري د خپل ځانګړي لینک "
                "له لارې اصلي چینل ته راوبلئ."
            )

        if callback.message:
            await callback.message.answer(text)
        await callback.answer()

    except Exception:
        logger.exception("Referral callback failed")
        await callback.answer(
            "د ریفرل معلومات اوس نه ترلاسه کېږي.",
            show_alert=True,
        )


@router.message(Command("referrals"))
async def referrals_command(message: Message):
    try:
        count = await referrals.get_referral_count(
            message.from_user.id
        )
        target = await settings_db.get_referral_target()
        await message.answer(
            f"👥 بریالي ریفرلونه: {count}\n"
            f"🎯 هدف: {target}\n\n"
            "د ځانګړي دعوت لینک لپاره د «زما ریفرلونه» تڼۍ وکاروئ.",
            reply_markup=app_keyboard(),
        )
    except Exception:
        logger.exception("Referral command failed")
        await message.answer("د ریفرل معلومات نه ترلاسه کېږي.")


@router.callback_query(F.data == "show_channels")
async def show_channels_callback(callback: CallbackQuery):
    try:
        channels = await settings_db.get_channels(
            category=None,
            active_only=True,
        )
        rows = []

        for channel in channels or []:
            username = getattr(channel, "username", "") or ""
            title = getattr(channel, "title", "") or username
            username = username.strip().lstrip("@")
            if username:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"📢 {title}",
                            url=f"https://t.me/{username}",
                        )
                    ]
                )

        if not rows:
            for username in (
                MAIN_CHANNEL,
                "ALL_PASHTO_DUBBED",
                "PASHTO_SUB",
            ):
                if username:
                    rows.append(
                        [
                            InlineKeyboardButton(
                                text=f"📢 @{username}",
                                url=f"https://t.me/{username}",
                            )
                        ]
                    )

        markup = InlineKeyboardMarkup(inline_keyboard=rows)
        if callback.message:
            await callback.message.answer(
                "📢 زموږ د فلمونو چینلونه:",
                reply_markup=markup,
            )
        await callback.answer()
    except Exception:
        logger.exception("Channels display failed")
        await callback.answer(
            "د چینلونو معلومات نه ترلاسه کېږي.",
            show_alert=True,
        )


@router.callback_query(F.data == "publish_film")
async def publish_callback(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
):
    if not await check_membership(bot, callback.from_user.id):
        if callback.message:
            await callback.message.answer(
                "🔒 لومړی اړین چینل کې ګډون وکړئ."
            )
        await callback.answer()
        return

    try:
        count = await referrals.get_referral_count(
            callback.from_user.id
        )
        target = await settings_db.get_referral_target()
        if count < target and not is_admin(callback.from_user.id):
            await callback.answer(
                f"د فلم خپرولو لپاره {target} ریفرلونه پکار دي. "
                f"اوس {count} لرئ.",
                show_alert=True,
            )
            return
    except Exception:
        logger.exception("Could not check publishing permission")
        await callback.answer(
            "اوس د خپرولو شرایط نه شي تاییدېدای.",
            show_alert=True,
        )
        return

    await state.clear()
    await state.set_state(FilmUpload.video)
    PENDING_FILMS[callback.from_user.id] = {}

    if callback.message:
        await callback.message.answer(
            "🎬 د فلم د ثبتولو بهیر پیل شو.\n\n"
            "لومړی د فلم ویډیو یا فایل راولېږئ.\n"
            "د لغوه کولو لپاره /cancel ولیکئ."
        )
    await callback.answer()


@router.message(Command("publish"))
async def publish_command(
    message: Message,
    bot: Bot,
    state: FSMContext,
):
    if not message.from_user:
        return
    if not await check_membership(bot, message.from_user.id):
        await send_join_prompt(message)
        return

    count = await referrals.get_referral_count(message.from_user.id)
    target = await settings_db.get_referral_target()

    if count < target and not is_admin(message.from_user.id):
        await message.answer(
            f"🎯 د فلم خپرولو لپاره {target} بریالي ریفرلونه پکار دي.\n"
            f"ستاسو اوسنی شمېر: {count}"
        )
        return

    await state.clear()
    await state.set_state(FilmUpload.video)
    PENDING_FILMS[message.from_user.id] = {}
    await message.answer("🎬 لومړی د فلم ویډیو یا فایل راولېږئ.")


@router.message(FilmUpload.video, F.video)
async def receive_video(message: Message, state: FSMContext):
    video = message.video
    PENDING_FILMS.setdefault(message.from_user.id, {})
    PENDING_FILMS[message.from_user.id].update(
        {
            "video_file_id": video.file_id,
            "video_unique_id": video.file_unique_id,
        }
    )
    await state.set_state(FilmUpload.title)
    await message.answer("📝 د فلم نوم ولیکئ.")


@router.message(FilmUpload.video, F.document)
async def receive_document(message: Message, state: FSMContext):
    document = message.document
    PENDING_FILMS.setdefault(message.from_user.id, {})
    PENDING_FILMS[message.from_user.id].update(
        {
            "video_file_id": document.file_id,
            "video_unique_id": document.file_unique_id,
        }
    )
    await state.set_state(FilmUpload.title)
    await message.answer("📝 د فلم نوم ولیکئ.")


@router.message(FilmUpload.video)
async def invalid_video(message: Message):
    await message.answer("⚠️ مهرباني وکړئ ویډیو یا فلم د فایل په بڼه راولېږئ.")


@router.message(FilmUpload.title)
async def receive_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if len(title) < 2 or len(title) > 200:
        await message.answer("⚠️ د فلم نوم باید له ۲ تر ۲۰۰ حروفو وي.")
        return
    PENDING_FILMS[message.from_user.id]["title"] = title
    await state.set_state(FilmUpload.year)
    await message.answer("📅 د فلم د خپرېدو کال ولیکئ، یا `-` ولیکئ.")


@router.message(FilmUpload.year)
async def receive_year(message: Message, state: FSMContext):
    year = (message.text or "").strip()
    if year != "-" and not re.fullmatch(r"\d{4}", year):
        await message.answer("⚠️ کال په څلورو عددونو ولیکئ، یا `-` ولیکئ.")
        return
    PENDING_FILMS[message.from_user.id]["year"] = (
        None if year == "-" else int(year)
    )
    await state.set_state(FilmUpload.quality)
    await message.answer("🎞 کیفیت ولیکئ، لکه 720p، 1080p یا `-`.")


@router.message(FilmUpload.quality)
async def receive_quality(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    PENDING_FILMS[message.from_user.id]["quality"] = (
        "" if value == "-" else value[:50]
    )
    await state.set_state(FilmUpload.genre)
    await message.answer("🎭 د فلم ژانر ولیکئ، لکه اکشن، کومیډي؛ یا `-`.")


@router.message(FilmUpload.genre)
async def receive_genre(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    PENDING_FILMS[message.from_user.id]["genre"] = (
        "" if value == "-" else value[:100]
    )
    await state.set_state(FilmUpload.language)
    await message.answer("🔊 د فلم ژبه ولیکئ، لکه پښتو؛ یا `-`.")


@router.message(FilmUpload.language)
async def receive_language(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    PENDING_FILMS[message.from_user.id]["language"] = (
        "" if value == "-" else value[:100]
    )
    await state.set_state(FilmUpload.description)
    await message.answer("📄 د فلم لنډ معلومات ولیکئ، یا `-`.")


@router.message(FilmUpload.description)
async def receive_description(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    PENDING_FILMS[message.from_user.id]["description"] = (
        "" if value == "-" else value[:3000]
    )
    await state.set_state(FilmUpload.poster)
    await message.answer(
        "🖼 د فلم پوسټر د عکس په بڼه راولېږئ، یا `-` ولیکئ."
    )


@router.message(FilmUpload.poster, F.photo)
async def receive_poster(
    message: Message,
    state: FSMContext,
    bot: Bot,
):
    photo = message.photo[-1]
    PENDING_FILMS[message.from_user.id].update(
        {
            "poster_file_id": photo.file_id,
            "poster_unique_id": photo.file_unique_id,
        }
    )
    await save_submitted_film(message, state, bot)


@router.message(FilmUpload.poster, F.text)
async def receive_no_poster(
    message: Message,
    state: FSMContext,
    bot: Bot,
):
    if (message.text or "").strip() != "-":
        await message.answer("🖼 عکس راولېږئ، یا د پوسټر د نه لرلو لپاره `-` ولیکئ.")
        return
    await save_submitted_film(message, state, bot)


async def save_submitted_film(
    message: Message,
    state: FSMContext,
    bot: Bot,
):
    user_id = message.from_user.id
    data = PENDING_FILMS.get(user_id, {})

    try:
        auto_approve = await settings_db.get_auto_approve()
        approved = bool(auto_approve) or is_admin(user_id)

        film = await film_service.create_film(
            title=data["title"],
            year=data.get("year"),
            quality=data.get("quality", ""),
            genre=data.get("genre", ""),
            language=data.get("language", ""),
            description=data.get("description", ""),
            video_file_id=data["video_file_id"],
            video_unique_id=data.get("video_unique_id"),
            poster_file_id=data.get("poster_file_id"),
            poster_unique_id=data.get("poster_unique_id"),
            uploader_id=user_id,
            approved=approved,
        )

        film_id = getattr(film, "id", None)
        title = getattr(film, "title", data["title"])

        if approved:
            await message.answer(
                f"✅ فلم ثبت شو او خپرېدو ته چمتو دی.\n"
                f"🎬 نوم: {title}\n"
                f"🆔 پېژندشمېره: {film_id}"
            )
        else:
            await message.answer(
                f"✅ د فلم غوښتنه ثبت شوه.\n"
                f"🎬 نوم: {title}\n"
                "⏳ د اډمین تایید ته منتظره ده."
            )
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"🆕 نوی فلم د تایید لپاره راغلی.\n"
                        f"🎬 {title}\n🆔 {film_id}",
                        reply_markup=InlineKeyboardMarkup(
                            inline_keyboard=[
                                [
                                    InlineKeyboardButton(
                                        text="✅ تایید",
                                        callback_data=f"film_approve:{film_id}",
                                    ),
                                    InlineKeyboardButton(
                                        text="❌ رد",
                                        callback_data=f"film_reject:{film_id}",
                                    ),
                                ]
                            ]
                        ),
                    )
                except TelegramAPIError:
                    logger.warning(
                        "Could not notify admin %s", admin_id
                    )

    except Exception:
        logger.exception("Film submission failed for user %s", user_id)
        await message.answer(
            "❌ فلم ثبت نه شو. معلومات خوندي نه شول؛ "
            "مهرباني وکړئ وروسته بیا هڅه وکړئ یا اډمین خبر کړئ."
        )
    finally:
        PENDING_FILMS.pop(user_id, None)
        await state.clear()


@router.callback_query(F.data.startswith("film_approve:"))
async def approve_film_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("یوازې اډمین اجازه لري.", show_alert=True)
        return

    try:
        film_id = int(callback.data.split(":", 1)[1])
        result = await film_service.approve_film(film_id)
        if result:
            if callback.message:
                await callback.message.edit_text(
                    f"✅ فلم تایید شو. ID: {film_id}"
                )
            await callback.answer("تایید شو")
        else:
            await callback.answer(
                "فلم ونه موندل شو یا تایید نه شو.",
                show_alert=True,
            )
    except Exception:
        logger.exception("Film approval failed")
        await callback.answer("تایید ناکام شو.", show_alert=True)


@router.callback_query(F.data.startswith("film_reject:"))
async def reject_film_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("یوازې اډمین اجازه لري.", show_alert=True)
        return

    try:
        film_id = int(callback.data.split(":", 1)[1])
        result = await film_service.reject_film(film_id)
        if result:
            if callback.message:
                await callback.message.edit_text(
                    f"❌ فلم رد شو. ID: {film_id}"
                )
            await callback.answer("رد شو")
        else:
            await callback.answer("فلم ونه موندل شو.", show_alert=True)
    except Exception:
        logger.exception("Film rejection failed")
        await callback.answer("رد کول ناکام شول.", show_alert=True)


@router.message(Command("admin"))
async def admin_command(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        await message.answer("⛔ دې برخې ته اجازه نه لرئ.")
        return

    await message.answer(
        "🛠 د ALL PRODUCTION FILMS اډمین پینل",
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "admin_add_film")
async def admin_add_film(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه نشته.", show_alert=True)
        return

    await state.clear()
    PENDING_FILMS[callback.from_user.id] = {}
    await state.set_state(FilmUpload.video)

    if callback.message:
        await callback.message.answer(
            "➕ د نوي فلم ثبتول پیل شول.\n"
            "لومړی ویډیو یا فایل راولېږئ. /cancel د لغوه کولو لپاره."
        )
    await callback.answer()


@router.callback_query(F.data == "admin_pending")
async def admin_pending(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه نشته.", show_alert=True)
        return

    try:
        pending = await film_service.get_pending_films()
        if not pending:
            if callback.message:
                await callback.message.answer("✅ د تایید لپاره فلم نشته.")
            await callback.answer()
            return

        for film in pending[:20]:
            film_id = getattr(film, "id", None)
            title = getattr(film, "title", "بې نومه")
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ تایید",
                            callback_data=f"film_approve:{film_id}",
                        ),
                        InlineKeyboardButton(
                            text="❌ رد",
                            callback_data=f"film_reject:{film_id}",
                        ),
                    ]
                ]
            )
            if callback.message:
                await callback.message.answer(
                    f"🎬 {title}\n🆔 {film_id}",
                    reply_markup=markup,
                )
        await callback.answer()
    except Exception:
        logger.exception("Could not list pending films")
        await callback.answer(
            "د فلمونو لیست ترلاسه نه شو.",
            show_alert=True,
        )


@router.callback_query(F.data == "admin_channels")
async def admin_channels(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه نشته.", show_alert=True)
        return

    try:
        channels = await settings_db.get_channels(
            category=None,
            active_only=False,
        )
        lines = ["📢 ثبت شوي چینلونه:\n"]
        for channel in channels or []:
            channel_id = getattr(channel, "id", "?")
            title = getattr(channel, "title", "")
            username = getattr(channel, "username", "")
            active = getattr(channel, "active", True)
            lines.append(
                f"🆔 {channel_id} | {title} | @{str(username).lstrip('@')} "
                f"| {'فعال' if active else 'غیرفعال'}"
            )

        lines.append(
            "\nد چینلونو د اضافه کولو، سمولو او حذفولو لپاره "
            "د اډمین ویب پینل وکاروئ."
        )
        if callback.message:
            await callback.message.answer(
                "\n".join(lines),
                reply_markup=admin_keyboard(),
            )
        await callback.answer()
    except Exception:
        logger.exception("Admin channel list failed")
        await callback.answer("چینلونه ونه لوستل شول.", show_alert=True)


@router.callback_query(F.data == "admin_settings")
async def admin_settings(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه نشته.", show_alert=True)
        return

    try:
        target = await settings_db.get_referral_target()
        main_channel = await settings_db.get_main_channel()
        access_channel = await settings_db.get_access_channel()
        auto_approve = await settings_db.get_auto_approve()

        text = (
            "⚙️ اوسني تنظیمات\n\n"
            f"🎯 د ریفرل هدف: {target}\n"
            f"📢 اصلي چینل: @{str(main_channel).lstrip('@')}\n"
            f"🔒 د لاسرسي چینل: @{str(access_channel).lstrip('@')}\n"
            f"🤖 اتومات تایید: {'فعال' if auto_approve else 'غیرفعال'}\n\n"
            "د تنظیماتو د بدلولو لپاره د اډمین ویب پینل وکاروئ."
        )
        if callback.message:
            await callback.message.answer(
                text,
                reply_markup=admin_keyboard(),
            )
        await callback.answer()
    except Exception:
        logger.exception("Admin settings read failed")
        await callback.answer(
            "تنظیمات ترلاسه نه شول.",
            show_alert=True,
        )


@router.chat_member()
async def track_main_channel_join(
    event: ChatMemberUpdated,
    bot: Bot,
):
    try:
        chat_username = getattr(event.chat, "username", None)
        if not chat_username:
            return
        if chat_username.lower() != MAIN_CHANNEL.lower():
            return

        old_status = event.old_chat_member.status
        new_status = event.new_chat_member.status
        joined_user_id = event.new_chat_member.user.id

        active_statuses = {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
            ChatMemberStatus.RESTRICTED,
        }

        if old_status in active_statuses or new_status not in active_statuses:
            return

        invite_link = event.invite_link
        if not invite_link or not invite_link.invite_link:
            return

        await referrals.process_channel_join(
            joined_user_id=joined_user_id,
            invite_link=invite_link.invite_link,
        )
        logger.info("Processed channel join for user %s", joined_user_id)

    except Exception:
        logger.exception("Could not process channel join")


async def start_bot():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing.")
    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS is empty; admin commands will be unavailable.")

    bot = Bot(token=BOT_TOKEN)

    try:
        await bot.delete_webhook(drop_pending_updates=False)
        logger.info("Starting ALL PRODUCTION FILMS bot polling.")
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(start_bot())
