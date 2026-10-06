import asyncio
import logging
from io import BytesIO

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Channel, Film, User
from app.referrals import (
    can_publish,
    create_referral_link,
    get_or_create_user,
    get_referral_count,
    process_channel_join,
)
from app.films import (
    calculate_image_hash,
    create_film,
    get_category_films,
    get_film,
    search_by_image_hash,
    search_films,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher()


# ---------------------------------------------------------
# USER ACCESS
# ---------------------------------------------------------

async def check_access(user_id: int) -> bool:
    """
    Checks whether the user joined the mandatory access channel.
    The bot should be admin in @ALL_PASHTO for reliable checking.
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

    except Exception as exc:
        logger.warning(
            "Access check failed for %s: %s",
            user_id,
            exc,
        )

        return False


def access_keyboard():
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="📢 چینل ته Join شئ",
            url=f"https://t.me/{settings.ACCESS_CHANNEL.lstrip('@')}",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="✅ ما Join کړی",
            callback_data="check_access",
        )
    )

    return builder.as_markup()


# ---------------------------------------------------------
# MAIN MENU
# ---------------------------------------------------------

def main_menu():
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="🔎 فلم ولټوه",
            callback_data="search",
        ),
        InlineKeyboardButton(
            text="🖼️ د عکس له لارې لټون",
            callback_data="image_search",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🎬 پښتو ترجمه فلمونه",
            callback_data="pashto_films",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="📢 زموږ فلمونه",
            callback_data="official_films",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="👥 Referral",
            callback_data="referral",
        ),
        InlineKeyboardButton(
            text="🏆 مشران",
            callback_data="leaders",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🎥 فلم نشر کړه",
            callback_data="publish",
        )
    )

    return builder.as_markup()


async def send_main_menu(message: Message):
    await message.answer(
        "🎬 <b>ALL PRODUCTION FILMS</b>\n\n"
        "خپل فلم پیدا کړئ، معلومات یې وګورئ او له Telegram څخه یې ترلاسه کړئ.\n\n"
        "👇 له لاندې انتخابونو څخه یو انتخاب کړئ:",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


# ---------------------------------------------------------
# START
# ---------------------------------------------------------

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

    if not await check_access(user.id):
        await message.answer(
            "🔐 <b>لومړی باید زموږ Access Channel ته Join شئ.</b>\n\n"
            "تر Join وروسته د «ما Join کړی» تڼۍ کېکاږئ.",
            reply_markup=access_keyboard(),
            parse_mode="HTML",
        )
        return

    await send_main_menu(message)


# ---------------------------------------------------------
# ACCESS CHECK
# ---------------------------------------------------------

@dp.callback_query(F.data == "check_access")
async def check_access_callback(callback: CallbackQuery):
    if callback.from_user is None:
        return

    if not await check_access(callback.from_user.id):
        await callback.answer(
            "❌ لا هم Channel ته نه یاست داخل شوي.",
            show_alert=True,
        )
        return

    await callback.answer("✅ Access فعال شو!")

    if callback.message:
        await callback.message.edit_text(
            "✅ <b>Access فعال شو!</b>\n\n"
            "اوس تاسو کولی شئ فلمونه ولټوئ او ترلاسه یې کړئ.",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )


# ---------------------------------------------------------
# REFERRAL
# ---------------------------------------------------------

@dp.callback_query(F.data == "referral")
async def referral_handler(callback: CallbackQuery):
    user = callback.from_user

    if not await check_access(user.id):
        await callback.answer(
            "لومړی @ALL_PASHTO ته Join شئ.",
            show_alert=True,
        )
        return

    link = await create_referral_link(
        bot,
        user.id,
    )

    count = await get_referral_count(user.id)
    target = settings.REFERRAL_TARGET

    remaining = max(target - count, 0)

    if remaining > 0:
        status = (
            f"⏳ د نشر اجازه ترلاسه کولو لپاره "
            f"<b>{remaining}</b> کسان نور راوبلئ."
        )
    else:
        status = "✅ تاسو د فلم نشرولو اجازه ترلاسه کړې ده."

    text = (
        "👥 <b>ستاسو Referral System</b>\n\n"
        f"👤 را بلل شوي کسان: <b>{count}</b>\n"
        f"🎯 هدف: <b>{target}</b>\n\n"
        f"{status}\n\n"
        "🔗 <b>ستاسو ځانګړی Invite Link:</b>\n"
        f"<code>{link}</code>\n\n"
        "📌 هر نوی کس باید ستاسو د همدې Link له لارې "
        f"<b>{settings.MAIN_CHANNEL}</b> ته Join شي."
    )

    await callback.message.answer(
        text,
        parse_mode="HTML",
    )

    await callback.answer()


# ---------------------------------------------------------
# PUBLISH PERMISSION
# ---------------------------------------------------------

@dp.callback_query(F.data == "publish")
async def publish_handler(callback: CallbackQuery):
    user = callback.from_user

    if not await check_access(user.id):
        await callback.answer(
            "لومړی @ALL_PASHTO ته Join شئ.",
            show_alert=True,
        )
        return

    if not await can_publish(user.id):
        count = await get_referral_count(user.id)
        target = settings.REFERRAL_TARGET

        await callback.answer(
            f"❌ د فلم نشرولو لپاره {target} Referral ته اړتیا ده.\n"
            f"ستاسو اوسنی شمېر: {count}",
            show_alert=True,
        )
        return

    await callback.message.answer(
        "🎬 <b>د فلم نشرولو برخه</b>\n\n"
        "اوس خپل فلم د Video یا Document په توګه دلته راولېږئ.\n\n"
        "⚠️ وروسته به د فلم معلومات هم درڅخه وغوښتل شي.",
        parse_mode="HTML",
    )

    await callback.answer()


# ---------------------------------------------------------
# SEARCH
# ---------------------------------------------------------

@dp.callback_query(F.data == "search")
async def search_handler(callback: CallbackQuery):
    await callback.message.answer(
        "🔎 د فلم نوم ولیکئ.\n\n"
        "مثال:\n"
        "<code>Avatar</code>\n"
        "<code>Avengers</code>",
        parse_mode="HTML",
    )

    await callback.answer()


@dp.message(F.text)
async def text_search_handler(message: Message):
    if message.text.startswith("/"):
        return

    user = message.from_user

    if user is None:
        return

    if not await check_access(user.id):
        await message.answer(
            "🔐 لومړی @ALL_PASHTO ته Join شئ.",
            reply_markup=access_keyboard(),
        )
        return

    query = message.text.strip()

    if len(query) < 2:
        return

    films = await search_films(query)

    if not films:
        await message.answer(
            "❌ د دې نوم سره فلم پیدا نه شو.\n\n"
            "بل نوم یا د فلم اصلي نوم ولیکئ."
        )
        return

    await message.answer(
        f"🔎 <b>{len(films)}</b> پایلې وموندل شوې:",
        parse_mode="HTML",
    )

    for film in films[:15]:
        await send_film_result(message, film)


async def send_film_result(message: Message, film: Film):
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="▶️ فلم ترلاسه کړه",
            callback_data=f"film:{film.id}",
        )
    )

    info = (
        f"🎬 <b>{film.title}</b>\n\n"
        f"📅 کال: {film.year or 'نامعلوم'}\n"
        f"⚙️ کیفیت: {film.quality or 'نامعلوم'}\n"
        f"🎭 ژانر: {film.genre or 'نامعلوم'}\n"
        f"🔊 ژبه: {film.language or 'نامعلوم'}"
    )

    if film.poster_file_id:
        try:
            await message.answer_photo(
                photo=film.poster_file_id,
                caption=info,
                reply_markup=builder.as_markup(),
                parse_mode="HTML",
            )
            return
        except Exception:
            pass

    await message.answer(
        info,
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


# ---------------------------------------------------------
# SEND FILM
# ---------------------------------------------------------

@dp.callback_query(F.data.startswith("film:"))
async def film_callback(callback: CallbackQuery):
    try:
        film_id = int(
            callback.data.split(":", 1)[1]
        )
    except Exception:
        await callback.answer(
            "❌ ناسم فلم.",
            show_alert=True,
        )
        return

    film = await get_film(film_id)

    if not film:
        await callback.answer(
            "❌ فلم موجود نه دی.",
            show_alert=True,
        )
        return

    await callback.answer(
        "📥 فلم درلېږل کېږي..."
    )

    try:
        await callback.message.answer_video(
            video=film.video_file_id,
            caption=(
                f"🎬 <b>{film.title}</b>\n\n"
                f"📅 {film.year or ''}\n"
                f"⚙️ {film.quality or ''}\n\n"
                "🎥 <b>ALL PRODUCTION FILMS</b>"
            ),
            parse_mode="HTML",
        )

    except Exception:
        try:
            await callback.message.answer_document(
                document=film.video_file_id,
                caption=f"🎬 {film.title}",
            )
        except Exception as exc:
            logger.error(
                "Film delivery failed: %s",
                exc,
            )


# ---------------------------------------------------------
# CATEGORY: PASHTO FILMS
# ---------------------------------------------------------

@dp.callback_query(F.data == "pashto_films")
async def pashto_films_handler(callback: CallbackQuery):
    films = await get_category_films(
        "pashto",
        limit=30,
    )

    if not films:
        await callback.answer(
            "اوس مهال فلمونه نشته.",
            show_alert=True,
        )
        return

    await callback.message.answer(
        "🎬 <b>پښتو ترجمه فلمونه</b>",
        parse_mode="HTML",
    )

    for film in films:
        await send_film_result(
            callback.message,
            film,
        )

    await callback.answer()


# ---------------------------------------------------------
# OFFICIAL FILMS
# ---------------------------------------------------------

@dp.callback_query(F.data == "official_films")
async def official_films_handler(callback: CallbackQuery):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Film)
            .where(
                Film.approved.is_(True),
                Film.official.is_(True),
            )
            .order_by(Film.created_at.desc())
            .limit(30)
        )

        films = result.scalars().all()

    if not films:
        await callback.answer(
            "اوس مهال رسمي فلمونه نشته.",
            show_alert=True,
        )
        return

    await callback.message.answer(
        "📢 <b>زموږ پښتو ترجمه فلمونه</b>",
        parse_mode="HTML",
    )

    for film in films:
        await send_film_result(
            callback.message,
            film,
        )

    await callback.answer()


# ---------------------------------------------------------
# LEADERS
# ---------------------------------------------------------

@dp.callback_query(F.data == "leaders")
async def leaders_handler(callback: CallbackQuery):
    from app.referrals import get_referral_leaders

    leaders = await get_referral_leaders(20)

    if not leaders:
        await callback.answer(
            "تر اوسه Referral Leader نشته.",
            show_alert=True,
        )
        return

    text = "🏆 <b>Referral Leaders</b>\n\n"

    for index, user in enumerate(
        leaders,
        start=1,
    ):
        name = user.first_name or "User"

        text += (
            f"{index}. {name} — "
            f"<b>{user.referral_count}</b>\n"
        )

    await callback.message.answer(
        text,
        parse_mode="HTML",
    )

    await callback.answer()


# ---------------------------------------------------------
# IMAGE SEARCH
# ---------------------------------------------------------

@dp.callback_query(F.data == "image_search")
async def image_search_handler(callback: CallbackQuery):
    await callback.message.answer(
        "🖼️ خپل د فلم Poster/عکس راولېږئ.\n\n"
        "زه به د موجودو فلمونو له Poster سره یې پرتله کړم."
    )

    await callback.answer()


@dp.message(F.photo)
async def photo_search_handler(message: Message):
    user = message.from_user

    if user is None:
        return

    if not await check_access(user.id):
        await message.answer(
            "🔐 لومړی @ALL_PASHTO ته Join شئ.",
            reply_markup=access_keyboard(),
        )
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

        image_bytes = buffer.getvalue()

        image_hash = calculate_image_hash(
            image_bytes
        )

        films = await search_by_image_hash(
            image_hash,
            max_distance=12,
            limit=10,
        )

        if not films:
            await message.answer(
                "❌ د دې عکس سره ورته فلم پیدا نه شو."
            )
            return

        await message.answer(
            f"🖼️ <b>{len(films)}</b> ورته فلمونه وموندل شول:",
            parse_mode="HTML",
        )

        for film in films:
            await send_film_result(
                message,
                film,
            )

    except Exception as exc:
        logger.error(
            "Image search error: %s",
            exc,
        )

        await message.answer(
            "❌ د عکس لټون کې ستونزه راغله."
        )


# ---------------------------------------------------------
# FILM UPLOAD
# ---------------------------------------------------------

@dp.message(F.video)
async def video_upload_handler(message: Message):
    user = message.from_user

    if user is None:
        return

    if not await check_access(user.id):
        await message.answer(
            "🔐 لومړی @ALL_PASHTO ته Join شئ.",
            reply_markup=access_keyboard(),
        )
        return

    if not await can_publish(user.id):
        count = await get_referral_count(user.id)

        await message.answer(
            "❌ تاسو لا د فلم نشرولو اجازه نه لرئ.\n\n"
            f"👥 اوسنی Referral: {count}\n"
            f"🎯 اړتیا: {settings.REFERRAL_TARGET}"
        )
        return

    video = message.video

    await get_or_create_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    # Save temporary submission data in Telegram user session.
    # The actual database film is created after metadata collection.
    await message.answer(
        "✅ فلم ترلاسه شو.\n\n"
        "اوس د فلم نوم ولیکئ.\n\n"
        "مثال:\n"
        "<code>Avatar 2009</code>",
        parse_mode="HTML",
    )

    await message.answer(
        "⚠️ د بشپړ Publish workflow لپاره به د Bot state/session برخه "
        "د راتلونکي handler سره فعاله شي."
    )


@dp.message(F.document)
async def document_upload_handler(message: Message):
    user = message.from_user

    if user is None:
        return

    if not await check_access(user.id):
        await message.answer(
            "🔐 لومړی @ALL_PASHTO ته Join شئ.",
            reply_markup=access_keyboard(),
        )
        return

    if not await can_publish(user.id):
        count = await get_referral_count(user.id)

        await message.answer(
            "❌ تاسو لا د فلم نشرولو اجازه نه لرئ.\n\n"
            f"👥 اوسنی Referral: {count}\n"
            f"🎯 اړتیا: {settings.REFERRAL_TARGET}"
        )
        return

    await message.answer(
        "✅ فایل ترلاسه شو.\n\n"
        "د فلم نوم ولیکئ."
    )


# ---------------------------------------------------------
# CHANNEL REFERRAL JOIN TRACKING
# ---------------------------------------------------------

@dp.chat_member()
async def channel_member_update(
    update: ChatMemberUpdated,
):
    """
    Telegram sends this update when a user joins/leaves a channel
    where the bot has sufficient rights.

    We use the private invite link to attribute the referral.
    """

    try:
        if update.chat.username:
            channel_username = (
                "@" + update.chat.username
            )
        else:
            channel_username = str(
                update.chat.id
            )

        if channel_username.lower() != settings.MAIN_CHANNEL.lower():
            return

        old_status = update.old_chat_member.status
        new_status = update.new_chat_member.status

        joined_statuses = {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        }

        if new_status not in joined_statuses:
            return

        if old_status in joined_statuses:
            return

        invite = update.invite_link

        if invite is None:
            return

        invite_url = invite.invite_link

        async with SessionLocal() as session:
            result = await session.execute(
                select(User).where(
                    User.referral_link == invite_url
                )
            )

            inviter = result.scalar_one_or_none()

        if inviter is None:
            logger.warning(
                "Unknown referral invite: %s",
                invite_url,
            )
            return

        invited_id = update.from_user.id

        counted = await process_channel_join(
            inviter_id=inviter.telegram_id,
            invited_id=invited_id,
            invite_link=invite_url,
        )

        if counted:
            logger.info(
                "Referral counted: %s -> %s",
                inviter.telegram_id,
                invited_id,
            )

    except Exception as exc:
        logger.error(
            "Referral update error: %s",
            exc,
        )


# ---------------------------------------------------------
# FALLBACK
# ---------------------------------------------------------

@dp.message()
async def fallback_handler(message: Message):
    if message.from_user is None:
        return

    if not await check_access(
        message.from_user.id
    ):
        await message.answer(
            "🔐 لومړی @ALL_PASHTO ته Join شئ.",
            reply_markup=access_keyboard(),
        )
        return

    await message.answer(
        "❓ ستاسې پیغام ونه پېژندل شو.\n\n"
        "له Menu څخه یو انتخاب وکړئ.",
        reply_markup=main_menu(),
    )


# ---------------------------------------------------------
# START BOT
# ---------------------------------------------------------

async def start_bot():
    logger.info(
        "ALL PRODUCTION FILMS bot starting..."
    )

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
    )


if __name__ == "__main__":
    asyncio.run(start_bot())
