import asyncio
import hashlib
import hmac
import json
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal, init_db
from app.models import User, Film, Channel
from app.referrals import (
    get_user,
    get_or_create_user,
    get_referral_count,
    can_publish,
    get_referral_leaders,
)
from app.films import (
    get_film,
    search_films,
    get_category_films,
    get_official_films,
    delete_film,
    approve_film,
    reject_film,
)
from app.admin import (
    is_admin,
    get_statistics,
)
from app.settings_db import (
    seed_default_settings,
    get_referral_target,
    get_main_channel,
    get_access_channel,
    get_auto_approve,
    get_channels,
    add_channel,
    update_channel,
    delete_channel,
    get_setting,
    set_setting,
)

from app.bot import bot, start_bot


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(
    title="ALL PRODUCTION FILMS",
    version="1.0.0",
)


# =========================================================
# STATIC FILES
# =========================================================

app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static",
)


# =========================================================
# FRONTEND
# =========================================================

@app.get("/")
async def home():

    return FileResponse(
        "static/index.html"
    )


@app.get("/admin")
async def admin_page():

    return FileResponse(
        "static/admin.html"
    )


# =========================================================
# TELEGRAM INIT DATA VALIDATION
# =========================================================

def validate_telegram_init_data(
    init_data: str,
) -> dict:

    if not init_data:

        raise HTTPException(
            status_code=401,
            detail="Missing Telegram initData",
        )

    try:

        parsed = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True,
            )
        )

        received_hash = parsed.pop(
            "hash",
            None,
        )

        if not received_hash:

            raise HTTPException(
                status_code=401,
                detail="Invalid Telegram initData",
            )

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(
                parsed.items()
            )
        )

        secret_key = hmac.new(
            b"WebAppData",
            settings.BOT_TOKEN.encode(),
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash,
        ):

            raise HTTPException(
                status_code=401,
                detail="Invalid Telegram signature",
            )

        return parsed

    except HTTPException:
        raise

    except Exception:

        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram initData",
        )


# =========================================================
# USER FROM INIT DATA
# =========================================================

def get_telegram_user(
    init_data: str,
):

    data = validate_telegram_init_data(
        init_data
    )

    user_json = data.get(
        "user"
    )

    if not user_json:

        raise HTTPException(
            status_code=401,
            detail="Telegram user not found",
        )

    try:

        return json.loads(
            user_json
        )

    except Exception:

        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram user",
        )


# =========================================================
# TELEGRAM ACCESS CHECK
# =========================================================

async def user_has_access(
    telegram_id: int,
):

    try:

        member = await bot.get_chat_member(
            chat_id=await get_access_channel(),
            user_id=telegram_id,
        )

        status = str(
            member.status
        )

        return status in {
            "creator",
            "administrator",
            "member",
            "restricted",
        }

    except Exception:

        return False


# =========================================================
# REQUIRE USER
# =========================================================

async def require_user(
    init_data: str,
):

    tg_user = get_telegram_user(
        init_data
    )

    telegram_id = int(
        tg_user["id"]
    )

    if not await user_has_access(
        telegram_id
    ):

        raise HTTPException(
            status_code=403,
            detail="ACCESS_REQUIRED",
        )

    await get_or_create_user(
        telegram_id=telegram_id,
        username=tg_user.get(
            "username"
        ),
        first_name=tg_user.get(
            "first_name"
        ),
    )

    return telegram_id


# =========================================================
# ADMIN AUTH
# =========================================================

async def require_admin(
    init_data: str,
):

    telegram_id = await require_user(
        init_data
    )

    if not is_admin(
        telegram_id
    ):

        raise HTTPException(
            status_code=403,
            detail="ADMIN_REQUIRED",
        )

    return telegram_id


# =========================================================
# FILM SERIALIZER
# =========================================================

def film_to_dict(
    film: Film,
):

    return {
        "id": film.id,
        "title": film.title,
        "year": film.year,
        "quality": film.quality,
        "genre": film.genre,
        "language": film.language,
        "description": film.description,
        "category": film.category,
        "poster_url": (
            f"/api/films/{film.id}/poster"
            if film.poster_file_id
            else None
        ),
        "official": film.official,
        "approved": film.approved,
        "created_at": (
            film.created_at.isoformat()
            if film.created_at
            else None
        ),
    }


# =========================================================
# BASIC API
# =========================================================

@app.get("/api/health")
async def health():

    return {
        "status": "ok",
        "project": "ALL PRODUCTION FILMS",
        "database": "PostgreSQL",
    }


# =========================================================
# CURRENT USER
# =========================================================

@app.get("/api/me")
async def api_me(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    telegram_id = await require_user(
        x_telegram_init_data
    )

    user = await get_user(
        telegram_id
    )

    target = await get_referral_target()

    return {
        "telegram_id": telegram_id,
        "username": (
            user.username
            if user
            else None
        ),
        "first_name": (
            user.first_name
            if user
            else None
        ),
        "referral_count": (
            user.referral_count
            if user
            else 0
        ),
        "referral_target": target,
        "can_publish": (
            user.can_publish
            if user
            else False
        ),
        "referral_link": (
            user.referral_link
            if user
            else None
        ),
    }


# =========================================================
# REFERRAL INFO
# =========================================================

@app.get("/api/referrals")
async def api_referrals(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    telegram_id = await require_user(
        x_telegram_init_data
    )

    user = await get_user(
        telegram_id
    )

    target = await get_referral_target()

    count = await get_referral_count(
        telegram_id
    )

    return {
        "count": count,
        "target": target,
        "remaining": max(
            target - count,
            0,
        ),
        "can_publish": await can_publish(
            telegram_id
        ),
        "referral_link": (
            user.referral_link
            if user
            else None
        ),
    }


# =========================================================
# REFERRAL LEADERS
# =========================================================

@app.get("/api/referrals/leaders")
async def api_referral_leaders(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    leaders = await get_referral_leaders(
        50
    )

    return [
        {
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "referral_count": user.referral_count,
        }
        for user in leaders
    ]


# =========================================================
# LATEST FILMS
# =========================================================

@app.get("/api/films/latest")
async def latest_films(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    async with SessionLocal() as session:

        result = await session.execute(
            select(Film)
            .where(
                Film.approved.is_(True)
            )
            .order_by(
                Film.created_at.desc()
            )
            .limit(50)
        )

        films = result.scalars().all()

    return [
        film_to_dict(film)
        for film in films
    ]


# =========================================================
# SEARCH FILMS
# =========================================================

@app.get("/api/films/search")
async def api_search_films(
    q: str,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    films = await search_films(
        q
    )

    return [
        film_to_dict(film)
        for film in films
    ]


# =========================================================
# CATEGORY
# =========================================================

@app.get("/api/films/category/{category}")
async def api_category_films(
    category: str,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    films = await get_category_films(
        category
    )

    return [
        film_to_dict(film)
        for film in films
    ]


# =========================================================
# OFFICIAL FILMS
# =========================================================

@app.get("/api/films/official")
async def api_official_films(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    films = await get_official_films()

    return [
        film_to_dict(film)
        for film in films
    ]


# =========================================================
# FILM DETAILS
# =========================================================

@app.get("/api/films/{film_id}")
async def api_film(
    film_id: int,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    film = await get_film(
        film_id
    )

    if film is None:

        raise HTTPException(
            status_code=404,
            detail="Film not found",
        )

    return film_to_dict(
        film
    )


# =========================================================
# FILM DOWNLOAD DEEP LINK
# =========================================================

@app.get("/api/films/{film_id}/download")
async def api_film_download(
    film_id: int,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    film = await get_film(
        film_id
    )

    if film is None:

        raise HTTPException(
            status_code=404,
            detail="Film not found",
        )

    bot_username = (
        settings.BOT_USERNAME
        .lstrip("@")
    )

    return {
        "url": (
            f"https://t.me/"
            f"{bot_username}"
            f"?start=film_{film.id}"
        )
    }


# =========================================================
# POSTER
# =========================================================

@app.get("/api/films/{film_id}/poster")
async def api_film_poster(
    film_id: int,
):

    film = await get_film(
        film_id
    )

    if film is None:

        raise HTTPException(
            status_code=404,
            detail="Film not found",
        )

    if not film.poster_file_id:

        raise HTTPException(
            status_code=404,
            detail="Poster not found",
        )

    try:

        telegram_file = await bot.get_file(
            film.poster_file_id
        )

        from io import BytesIO

        buffer = BytesIO()

        await bot.download_file(
            telegram_file.file_path,
            buffer,
        )

        buffer.seek(0)

        return StreamingResponse(
            buffer,
            media_type="image/jpeg",
        )

    except Exception:

        raise HTTPException(
            status_code=404,
            detail="Unable to load poster",
        )


# =========================================================
# CHANNELS
# =========================================================

@app.get("/api/channels")
async def api_channels(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    channels = await get_channels(
        active_only=True
    )

    return [
        {
            "id": channel.id,
            "title": channel.title,
            "username": channel.username,
            "category": channel.category,
            "active": channel.active,
        }
        for channel in channels
    ]


# =========================================================
# ADMIN STATISTICS
# =========================================================

@app.get("/api/admin/stats")
async def api_admin_stats(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    stats = await get_statistics()

    target = await get_referral_target()

    auto_approve = await get_auto_approve()

    return {
        **stats,
        "referral_target": target,
        "auto_approve_films": auto_approve,
    }


# =========================================================
# ADMIN SETTINGS MODEL
# =========================================================

class AdminSettingsUpdate(BaseModel):

    referral_target: int | None = None

    auto_approve_films: bool | None = None

    main_channel: str | None = None

    access_channel: str | None = None


# =========================================================
# GET ADMIN SETTINGS
# =========================================================

@app.get("/api/admin/settings")
async def api_admin_settings(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    return {
        "referral_target":
            await get_referral_target(),

        "auto_approve_films":
            await get_auto_approve(),

        "main_channel":
            await get_main_channel(),

        "access_channel":
            await get_access_channel(),
    }


# =========================================================
# UPDATE ADMIN SETTINGS
# =========================================================

@app.post("/api/admin/settings")
async def api_update_admin_settings(
    data: AdminSettingsUpdate,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    admin_id = await require_admin(
        x_telegram_init_data
    )

    if data.referral_target is not None:

        if data.referral_target < 1:

            raise HTTPException(
                status_code=400,
                detail="Referral target must be at least 1",
            )

        await set_setting(
            "referral_target",
            str(data.referral_target),
        )

    if data.auto_approve_films is not None:

        await set_setting(
            "auto_approve_films",
            str(
                data.auto_approve_films
            ).lower(),
        )

    if data.main_channel is not None:

        await set_setting(
            "main_channel",
            data.main_channel.strip(),
        )

    if data.access_channel is not None:

        await set_setting(
            "access_channel",
            data.access_channel.strip(),
        )

    return {
        "success": True,
        "admin_id": admin_id,
        "referral_target":
            await get_referral_target(),
        "auto_approve_films":
            await get_auto_approve(),
        "main_channel":
            await get_main_channel(),
        "access_channel":
            await get_access_channel(),
    }


# =========================================================
# ADMIN CHANNELS
# =========================================================

@app.get("/api/admin/channels")
async def api_admin_channels(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    channels = await get_channels(
        active_only=False
    )

    return [
        {
            "id": channel.id,
            "title": channel.title,
            "username": channel.username,
            "category": channel.category,
            "active": channel.active,
        }
        for channel in channels
    ]


# =========================================================
# ADD CHANNEL MODEL
# =========================================================

class ChannelCreate(BaseModel):

    title: str

    username: str

    category: str = "pashto"


# =========================================================
# ADD CHANNEL
# =========================================================

@app.post("/api/admin/channels")
async def api_add_channel(
    data: ChannelCreate,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    channel = await add_channel(
        title=data.title,
        username=data.username,
        category=data.category,
    )

    return {
        "success": True,
        "channel": {
            "id": channel.id,
            "title": channel.title,
            "username": channel.username,
            "category": channel.category,
            "active": channel.active,
        },
    }


# =========================================================
# UPDATE CHANNEL MODEL
# =========================================================

class ChannelUpdate(BaseModel):

    title: str | None = None

    username: str | None = None

    category: str | None = None

    active: bool | None = None


# =========================================================
# UPDATE CHANNEL
# =========================================================

@app.put("/api/admin/channels/{channel_id}")
async def api_update_channel(
    channel_id: int,
    data: ChannelUpdate,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    channel = await update_channel(
        channel_id=channel_id,
        title=data.title,
        username=data.username,
        category=data.category,
        active=data.active,
    )

    if channel is None:

        raise HTTPException(
            status_code=404,
            detail="Channel not found",
        )

    return {
        "success": True,
        "channel": {
            "id": channel.id,
            "title": channel.title,
            "username": channel.username,
            "category": channel.category,
            "active": channel.active,
        },
    }


# =========================================================
# DELETE CHANNEL
# =========================================================

@app.delete("/api/admin/channels/{channel_id}")
async def api_delete_channel(
    channel_id: int,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    deleted = await delete_channel(
        channel_id
    )

    if not deleted:

        raise HTTPException(
            status_code=404,
            detail="Channel not found",
        )

    return {
        "success": True
    }


# =========================================================
# ADMIN FILMS
# =========================================================

@app.get("/api/admin/films")
async def api_admin_films(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    async with SessionLocal() as session:

        result = await session.execute(
            select(Film)
            .order_by(
                Film.created_at.desc()
            )
            .limit(200)
        )

        films = result.scalars().all()

    return [
        film_to_dict(film)
        for film in films
    ]


# =========================================================
# APPROVE FILM
# =========================================================

@app.post("/api/admin/films/{film_id}/approve")
async def api_approve_film(
    film_id: int,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    success = await approve_film(
        film_id
    )

    if not success:

        raise HTTPException(
            status_code=404,
            detail="Film not found",
        )

    return {
        "success": True
    }


# =========================================================
# REJECT FILM
# =========================================================

@app.post("/api/admin/films/{film_id}/reject")
async def api_reject_film(
    film_id: int,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    success = await reject_film(
        film_id
    )

    if not success:

        raise HTTPException(
            status_code=404,
            detail="Film not found",
        )

    return {
        "success": True
    }


# =========================================================
# DELETE FILM
# =========================================================

@app.delete("/api/admin/films/{film_id}")
async def api_delete_film(
    film_id: int,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    success = await delete_film(
        film_id
    )

    if not success:

        raise HTTPException(
            status_code=404,
            detail="Film not found",
        )

    return {
        "success": True
    }


# =========================================================
# SET OFFICIAL FILM
# =========================================================

class OfficialFilmUpdate(BaseModel):

    official: bool


@app.post("/api/admin/films/{film_id}/official")
async def api_set_official(
    film_id: int,
    data: OfficialFilmUpdate,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    async with SessionLocal() as session:

        result = await session.execute(
            select(Film).where(
                Film.id == film_id
            )
        )

        film = result.scalar_one_or_none()

        if film is None:

            raise HTTPException(
                status_code=404,
                detail="Film not found",
            )

        film.official = data.official

        await session.commit()

    return {
        "success": True,
        "official": data.official,
    }


# =========================================================
# STARTUP
# =========================================================

@app.on_event("startup")
async def startup_event():

    # Create PostgreSQL tables
    await init_db()

    # Create default settings/channels
    await seed_default_settings()

    # Seed default channels
    async with SessionLocal() as session:

        default_channels = [
            {
                "title": "Afghan Production",
                "username": "@afghanproduction",
                "category": "pashto",
            },
            {
                "title": "ALL PASHTO DUBBED",
                "username": "@ALL_PASHTO_DUBBED",
                "category": "pashto",
            },
            {
                "title": "PASHTO SUB",
                "username": "@PASHTO_SUB",
                "category": "pashto",
            },
        ]

        for item in default_channels:

            result = await session.execute(
                select(Channel).where(
                    Channel.username
                    == item["username"]
                )
            )

            existing = (
                result.scalar_one_or_none()
            )

            if existing is None:

                session.add(
                    Channel(
                        title=item["title"],
                        username=item["username"],
                        category=item["category"],
                        active=True,
                    )
                )

        await session.commit()

    # Start Telegram bot
    asyncio.create_task(
        start_bot()
    )


# =========================================================
# SHUTDOWN
# =========================================================

@app.on_event("shutdown")
async def shutdown_event():

    try:

        await bot.session.close()

    except Exception:

        pass
