import hashlib
import hmac
import json
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    Update,
    ChatMemberUpdated,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo
)
from aiogram.filters import CommandStart
from aiogram.enums import ChatMemberStatus

from .config import settings
from .db import (
    init_db,
    pool,
    upsert_user,
    get_user,
    set_referral_link,
    register_join,
    search_films,
    add_film,
    all_channels
)


bot = Bot(settings.bot_token)
dp = Dispatcher()


# =========================
# REFERRAL LINK
# =========================

async def ensure_referral_link(user_id: int) -> str:

    row = await get_user(user_id)

    if row and row["referral_invite_link"]:
        return row["referral_invite_link"]

    name = f"ref_{user_id}_{secrets.token_hex(3)}"

    link = await bot.create_chat_invite_link(
        chat_id=settings.main_channel,
        name=name,
        creates_join_request=False
    )

    await set_referral_link(
        user_id,
        link.invite_link,
        name
    )

    return link.invite_link


# =========================
# CHANNEL MEMBERSHIP CHECK
# =========================

async def channel_ok(user_id: int, channel: str) -> bool:

    try:

        member = await bot.get_chat_member(
            chat_id=channel,
            user_id=user_id
        )

        return member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR
        }

    except Exception:
        return False


async def access_channel_ok(user_id: int) -> bool:
    return await channel_ok(
        user_id,
        settings.access_channel
    )


# =========================
# REFERRAL PAYLOAD
# =========================

def referral_owner_from_start(text: str | None):

    parts = (text or "").split(maxsplit=1)

    if len(parts) == 2:

        payload = parts[1]

        if payload.startswith("ref_"):

            value = payload[4:]

            if value.isdigit():
                return int(value)

    return None


# =========================
# MAIN CHANNEL REFERRAL
# =========================

@dp.chat_member()
async def joined(event: ChatMemberUpdated):

    if not event.chat.username:
        return

    if (
        event.chat.username.lower()
        != settings.main_channel.lstrip("@").lower()
    ):
        return

    if not event.invite_link:
        return

    link = event.invite_link.invite_link

    async with pool.acquire() as con:

        inviter = await con.fetchval(
            """
            SELECT id
            FROM users
            WHERE referral_invite_link=$1
            """,
            link
        )

    if inviter:

        await register_join(
            inviter,
            event.from_user.id,
            link
        )


# =========================
# START
# =========================

@dp.message(CommandStart())
async def start(message: Message):

    await upsert_user(
        message.from_user,
        referral_owner_from_start(message.text)
    )

    access = await access_channel_ok(
        message.from_user.id
    )

    # =========================
    # NOT JOINED
    # =========================

    if not access:

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[

                [
                    InlineKeyboardButton(
                        text="📢 @ALL_PASHTO Join کړه",
                        url=f"https://t.me/{settings.access_channel.lstrip('@')}"
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="✅ Join مې وکړ، بیا وګوره",
                        callback_data="check_access"
                    )
                ]

            ]
        )

        await message.answer(
            "🎬 ALL PRODUCTION FILMS\n\n"
            "د Bot او Mini App د استعمال لپاره "
            "لومړی زموږ چینل Join کړه:\n\n"
            f"📢 {settings.access_channel}\n\n"
            "وروسته «Join مې وکړ، بیا وګوره» کېکاږه.",
            reply_markup=keyboard
        )

        return


    # =========================
    # JOINED
    # =========================

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="🔎 فلم ولټوه",
                    callback_data="search"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🎬 فلمونه نشر کړه",
                    callback_data="publish"
                )
            ],

            [
                InlineKeyboardButton(
                    text="👥 زما ریفرل",
                    callback_data="referral"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🏆 ریفرل لیډران",
                    callback_data="leaders"
                )
            ],

            [
                InlineKeyboardButton(
                    text="📢 پښتو ترجمه فلمونه",
                    callback_data="channels"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🌐 Mini App",
                    web_app=WebAppInfo(
                        url=settings.app_url
                    )
                )
            ]

        ]
    )

    await message.answer(
        "🎬 ALL PRODUCTION FILMS\n\n"
        "ښه راغلاست! اوس کولای شې فلمونه ولټوې "
        "او ترلاسه یې کړې.\n\n"
        f"🔒 د فلم نشرولو شرط: "
        f"{settings.referral_target} کسان "
        "زموږ اصلي چینل ته راوستل.",
        reply_markup=keyboard
    )


# =========================
# FILM UPLOAD
# =========================

@dp.message(F.video | F.document)
async def receive_film(message: Message):

    # لومړی ALL_PASHTO
    if not await access_channel_ok(
        message.from_user.id
    ):

        await message.answer(
            f"🔒 لومړی {settings.access_channel} "
            "Join کړه، بیا فلم راولېږه."
        )

        return


    row = await get_user(
        message.from_user.id
    )


    # 50 REFERRAL شرط
    if not row or not row["upload_unlocked"]:

        link = await ensure_referral_link(
            message.from_user.id
        )

        count = row["referral_count"] if row else 0

        await message.answer(
            "🔒 د فلم نشرولو اجازه لا نه ده فعاله.\n\n"
            f"👥 ریفرل: {count}/{settings.referral_target}\n\n"
            f"🔗 ستا ځانګړی لینک:\n{link}\n\n"
            "50 کسان زموږ اصلي چینل ته راوله، "
            "بیا به د فلم نشرولو اجازه فعاله شي."
        )

        return


    file_id = (
        message.video.file_id
        if message.video
        else message.document.file_id
    )

    file_type = (
        "video"
        if message.video
        else "document"
    )

    default_title = (
        (message.caption or "").strip()
        or (
            message.video.file_name
            if message.video
            else message.document.file_name
        )
        or "بې نومه فلم"
    )


    await add_film(
        default_title,
        file_id,
        file_type,
        (
            message.video.file_name
            if message.video
            else message.document.file_name
        ),
        message.from_user.id
    )


    await message.answer(
        f"✅ فلم ثبت شو!\n\n"
        f"🎬 {default_title}\n\n"
        "اوس ټول کاروونکي یې Search کولی شي."
    )


# =========================
# SEARCH
# =========================

@dp.message(F.text)
async def text_search(message: Message):

    if (message.text or "").startswith("/"):
        return

    if not await access_channel_ok(
        message.from_user.id
    ):

        await message.answer(
            f"🔒 د استعمال لپاره لومړی "
            f"{settings.access_channel} Join کړه."
        )

        return


    rows = await search_films(
        message.text.strip()
    )


    if not rows:

        await message.answer(
            "🔎 فلم پیدا نه شو.\n"
            "د فلم نوم بیا ولیکه."
        )

        return


    for film in rows[:10]:

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[

                [
                    InlineKeyboardButton(
                        text="🎬 فلم ترلاسه کړه",
                        callback_data=f"film:{film['id']}"
                    )
                ]

            ]
        )


        await message.answer(

            f"🎬 {film['title']}\n"
            f"📅 {film['year'] or '-'}\n"
            f"⚙️ {film['quality'] or '-'}\n"
            f"🎭 {film['genre'] or '-'}",

            reply_markup=keyboard
        )


# =========================
# CHECK ACCESS
# =========================

@dp.callback_query(
    F.data == "check_access"
)
async def check_access(callback):

    if not await access_channel_ok(
        callback.from_user.id
    ):

        await callback.answer(
            "❌ لا هم @ALL_PASHTO Join شوی نه یې.",
            show_alert=True
        )

        return


    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="🌐 Mini App",
                    web_app=WebAppInfo(
                        url=settings.app_url
                    )
                )
            ],

            [
                InlineKeyboardButton(
                    text="🔎 فلم ولټوه",
                    callback_data="search"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🎬 فلمونه نشر کړه",
                    callback_data="publish"
                )
            ]

        ]
    )


    await callback.message.edit_text(
        "✅ تایید شو!\n\n"
        "اوس کولای شې Bot او Mini App استعمال کړې.",
        reply_markup=keyboard
    )

    await callback.answer("✅ تایید شو!")


# =========================
# SEARCH BUTTON
# =========================

@dp.callback_query(
    F.data == "search"
)
async def cb_search(callback):

    if not await access_channel_ok(
        callback.from_user.id
    ):

        await callback.answer(
            "لومړی @ALL_PASHTO Join کړه.",
            show_alert=True
        )

        return


    await callback.message.answer(
        "🔎 د فلم نوم ولیکه؛ زه به یې درته ولټوم."
    )

    await callback.answer()


# =========================
# PUBLISH BUTTON
# =========================

@dp.callback_query(
    F.data == "publish"
)
async def cb_publish(callback):

    if not await access_channel_ok(
        callback.from_user.id
    ):

        await callback.answer(
            "لومړی @ALL_PASHTO Join کړه.",
            show_alert=True
        )

        return


    row = await get_user(
        callback.from_user.id
    )


    if row and row["upload_unlocked"]:

        await callback.message.answer(
            "🎬 اجازه فعاله ده!\n\n"
            "خپل فلم د Video یا Document "
            "په توګه Bot ته Send/Forward کړه."
        )

    else:

        link = await ensure_referral_link(
            callback.from_user.id
        )

        count = (
            row["referral_count"]
            if row
            else 0
        )


        await callback.message.answer(

            "🔒 د فلم نشرولو اجازه لا نه ده فعاله.\n\n"

            f"👥 ستا شمېر: "
            f"{count}/{settings.referral_target}\n\n"

            f"🔗 ستا ځانګړی لینک:\n"
            f"{link}\n\n"

            f"هر نوی کس باید زموږ اصلي چینل "
            f"{settings.main_channel} ته "
            "د همدې لینک له لارې Join شي."
        )


    await callback.answer()


# =========================
# REFERRAL
# =========================

@dp.callback_query(
    F.data == "referral"
)
async def cb_referral(callback):

    if not await access_channel_ok(
        callback.from_user.id
    ):

        await callback.answer(
            "لومړی @ALL_PASHTO Join کړه.",
            show_alert=True
        )

        return


    row = await get_user(
        callback.from_user.id
    )

    link = await ensure_referral_link(
        callback.from_user.id
    )

    count = (
        row["referral_count"]
        if row
        else 0
    )


    await callback.message.answer(

        "👥 زما Referral\n\n"

        f"📊 شمېر: "
        f"{count}/{settings.referral_target}\n\n"

        f"🔗 ستا ځانګړی لینک:\n"
        f"{link}"

    )

    await callback.answer()


# =========================
# LEADERS
# =========================

@dp.callback_query(
    F.data == "leaders"
)
async def cb_leaders(callback):

    if not await access_channel_ok(
        callback.from_user.id
    ):

        await callback.answer(
            "لومړی @ALL_PASHTO Join کړه.",
            show_alert=True
        )

        return


    async with pool.acquire() as con:

        rows = await con.fetch(
            """
            SELECT first_name,
                   username,
                   referral_count
            FROM users
            ORDER BY referral_count DESC
            LIMIT 10
            """
        )


    text = "🏆 ریفرل لیډران\n\n"


    for i, row in enumerate(
        rows,
        1
    ):

        text += (
            f"{i}. "
            f"{row['first_name'] or '-'} — "
            f"{row['referral_count']} 👥\n"
        )


    await callback.message.answer(text)

    await callback.answer()


# =========================
# CHANNELS
# =========================

@dp.callback_query(
    F.data == "channels"
)
async def cb_channels(callback):

    if not await access_channel_ok(
        callback.from_user.id
    ):

        await callback.answer(
            "لومړی @ALL_PASHTO Join کړه.",
            show_alert=True
        )

        return


    rows = await all_channels()


    text = "📢 پښتو ترجمه فلمونه\n\n"


    for row in rows:

        text += (
            f"• {row['title']}: "
            f"{row['username_or_id']}\n"
        )


    await callback.message.answer(text)

    await callback.answer()


# =========================
# GET FILM
# =========================

@dp.callback_query(
    F.data.startswith("film:")
)
async def cb_film(callback):

    if not await access_channel_ok(
        callback.from_user.id
    ):

        await callback.answer(
            "لومړی @ALL_PASHTO Join کړه.",
            show_alert=True
        )

        return


    film_id = int(
        callback.data.split(":")[1]
    )


    async with pool.acquire() as con:

        film = await con.fetchrow(
            """
            SELECT *
            FROM films
            WHERE id=$1
            """,
            film_id
        )


    if not film:

        await callback.answer(
            "فلم پیدا نه شو.",
            show_alert=True
        )

        return


    if film["file_type"] == "video":

        await callback.message.answer_video(
            film["file_id"],
            caption=f"🎬 {film['title']}"
        )

    else:

        await callback.message.answer_document(
            film["file_id"],
            caption=f"🎬 {film['title']}"
        )


    await callback.answer()


# =========================
# TELEGRAM MINI APP SECURITY
# =========================

def validate_webapp_init_data(
    init_data: str
):

    pairs = dict(
        parse_qsl(
            init_data,
            keep_blank_values=True
        )
    )


    received_hash = pairs.pop(
        "hash",
        None
    )


    if not received_hash:
        raise ValueError(
            "Missing hash"
        )


    auth_date = int(
        pairs.get(
            "auth_date",
            "0"
        )
    )


    if (
        not auth_date
        or time.time() - auth_date > 86400
    ):

        raise ValueError(
            "Expired"
        )


    data_check_string = "\n".join(
        f"{key}={value}"
        for key, value
        in sorted(pairs.items())
    )


    secret_key = hmac.new(
        b"WebAppData",
        settings.bot_token.encode(),
        hashlib.sha256
    ).digest()


    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256
    ).hexdigest()


    if not hmac.compare_digest(
        calculated_hash,
        received_hash
    ):

        raise ValueError(
            "Invalid hash"
        )


    return pairs


# =========================
# FASTAPI
# =========================

@asynccontextmanager
async def lifespan(app):

    await init_db()


    await bot.set_webhook(

        f"{settings.app_url}"
        f"/telegram/webhook/"
        f"{settings.webhook_secret}",

        allowed_updates=
        dp.resolve_used_update_types()
    )


    yield


    await bot.delete_webhook()

    await bot.session.close()


app = FastAPI(
    title="ALL PRODUCTION FILMS",
    lifespan=lifespan
)


# =========================
# WEBHOOK
# =========================

@app.post(
    "/telegram/webhook/{secret}"
)
async def webhook(
    secret: str,
    request: Request
):

    if secret != settings.webhook_secret:
        raise HTTPException(403)


    update = Update.model_validate(
        await request.json(),
        context={"bot": bot}
    )


    await dp.feed_update(
        bot,
        update
    )


    return {
        "ok": True
    }


# =========================
# MINI APP ACCESS API
# =========================

@app.post(
    "/api/miniapp/access"
)
async def miniapp_access(
    request: Request
):

    body = await request.json()

    try:

        pairs = validate_webapp_init_data(
            body.get(
                "initData",
                ""
            )
        )


        raw_user = pairs.get(
            "user"
        )


        if not raw_user:
            raise ValueError(
                "User missing"
            )


        user = json.loads(
            raw_user
        )


        user_id = int(
            user["id"]
        )


        class TGUser:

            id = user_id
            username = user.get(
                "username"
            )
            first_name = user.get(
                "first_name"
            )


        await upsert_user(
            TGUser()
        )


        allowed = await access_channel_ok(
            user_id
        )


        return {

            "ok": True,

            "allowed": allowed,

            "channel":
                settings.access_channel,

            "channel_url":
                f"https://t.me/"
                f"{settings.access_channel.lstrip('@')}"
        }


    except Exception:

        raise HTTPException(
            401,
            "Mini App session invalid"
        )


# =========================
# HEALTH
# =========================

@app.get(
    "/health"
)
async def health():

    return {
        "ok": True,
        "service":
            "ALL PRODUCTION FILMS"
    }


# =========================
# MINI APP
# =========================

@app.get(
    "/",
    response_class=HTMLResponse
)
async def root():

    with open(
        "static/index.html",
        "r",
        encoding="utf-8"
    ) as file:

        return file.read()
