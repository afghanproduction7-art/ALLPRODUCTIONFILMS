import asyncio
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qsl

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select

from app.admin import get_statistics, is_admin
from app.bot import bot, start_bot
from app.config import settings
from app.database import SessionLocal, init_db
from app.films import (
    get_category_films,
    get_film,
    get_official_films,
    search_films,
)
from app.models import Channel, Film, User
from app.referrals import (
    create_referral_link,
    get_referral_count,
    get_referral_leaders,
    get_user,
)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO
)

logger = logging.getLogger(__name__)


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
# ROOT
# =========================================================

@app.get("/")
async def home():

    return FileResponse(
        "static/index.html"
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
            detail="Telegram initData missing.",
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
                detail="Telegram hash missing.",
            )

        auth_date = parsed.get(
            "auth_date"
        )

        if not auth_date:
            raise HTTPException(
                status_code=401,
                detail="Telegram auth_date missing.",
            )

        # -------------------------------------------------
        # EXPIRE AFTER 24 HOURS
        # -------------------------------------------------

        try:

            auth_timestamp = int(
                auth_date
            )

        except ValueError:

            raise HTTPException(
                status_code=401,
                detail="Invalid auth_date.",
            )

        if (
            time.time()
            - auth_timestamp
            > 86400
        ):

            raise HTTPException(
                status_code=401,
                detail="Telegram initData expired.",
            )

        # -------------------------------------------------
        # DATA CHECK STRING
        # -------------------------------------------------

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(
                parsed.items()
            )
        )

        # Telegram WebApp secret key
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
                detail="Invalid Telegram initData.",
            )

        return parsed

    except HTTPException:
        raise

    except Exception as error:

        logger.exception(
            "initData validation failed: %s",
            error,
        )

        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram initData.",
        )


# =========================================================
# GET TELEGRAM USER FROM INIT DATA
# =========================================================

def get_telegram_user_from_init_data(
    init_data: str,
) -> dict:

    data = validate_telegram_init_data(
        init_data
    )

    user_json = data.get(
        "user"
    )

    if not user_json:

        raise HTTPException(
            status_code=401,
            detail="Telegram user missing.",
        )

    try:

        return json.loads(
            user_json
        )

    except Exception:

        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram user data.",
        )


# =========================================================
# ACCESS CHECK
# =========================================================

async def user_has_access(
    telegram_id: int,
) -> bool:

    try:

        member = await bot.get_chat_member(
            chat_id=settings.ACCESS_CHANNEL,
            user_id=telegram_id,
        )

        status = member.status

        return status in {
            "member",
            "administrator",
            "creator",
        }

    except Exception as error:

        logger.warning(
            "Access check failed for %s: %s",
            telegram_id,
            error,
        )

        return False


# =========================================================
# REQUIRE MINI APP ACCESS
# =========================================================

async def require_user(
    x_telegram_init_data: str | None,
) -> dict:

    if not x_telegram_init_data:

        raise HTTPException(
            status_code=401,
            detail="Telegram authentication required.",
        )

    user = get_telegram_user_from_init_data(
        x_telegram_init_data
    )

    telegram_id = user.get(
        "id"
    )

    if not telegram_id:

        raise HTTPException(
            status_code=401,
            detail="Telegram user ID missing.",
        )

    # -----------------------------------------------------
    # ACCESS CHANNEL
    # -----------------------------------------------------

    if not await user_has_access(
        telegram_id
    ):

        raise HTTPException(
            status_code=403,
            detail="ACCESS_REQUIRED",
        )

    # -----------------------------------------------------
    # SAVE / UPDATE USER
    # -----------------------------------------------------

    await get_user(
        telegram_id
    )

    await ensure_user_exists(
        telegram_id=telegram_id,
        username=user.get("username"),
        first_name=user.get("first_name"),
    )

    return user


# =========================================================
# ENSURE USER EXISTS
# =========================================================

async def ensure_user_exists(
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
):

    from app.referrals import (
        get_or_create_user,
    )

    return await get_or_create_user(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
    )


# =========================================================
# ADMIN AUTH
# =========================================================

async def require_admin(
    x_telegram_init_data: str | None,
) -> dict:

    user = await require_user(
        x_telegram_init_data
    )

    telegram_id = user.get(
        "id"
    )

    if not is_admin(
        telegram_id
    ):

        raise HTTPException(
            status_code=403,
            detail="ADMIN_ONLY",
        )

    return user


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/health")
async def health():

    return {
        "status": "ok",
        "project": "ALL PRODUCTION FILMS",
        "bot": settings.BOT_USERNAME,
    }


# =========================================================
# ACCESS API
# =========================================================

@app.get("/api/access")
async def access_api(
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    if not x_telegram_init_data:

        raise HTTPException(
            status_code=401,
            detail="Telegram authentication required.",
        )

    user = get_telegram_user_from_init_data(
        x_telegram_init_data
    )

    telegram_id = user.get(
        "id"
    )

    access = await user_has_access(
        telegram_id
    )

    return {
        "access": access,
        "channel": settings.ACCESS_CHANNEL,
    }


# =========================================================
# LATEST FILMS
# =========================================================

@app.get("/api/films/latest")
async def latest_films(
    x_telegram_init_data: str | None = Header(
        default=None,
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
            .limit(30)
        )

        films = result.scalars().all()

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


# =========================================================
# SEARCH FILMS
# =========================================================

@app.get("/api/films/search")
async def search_films_api(
    q: str = Query(
        min_length=2
    ),
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    films = await search_films(
        q,
        limit=30,
    )

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


# =========================================================
# CATEGORY
# =========================================================

@app.get(
    "/api/films/category/{category}"
)
async def category_films_api(
    category: str,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    films = await get_category_films(
        category,
        limit=50,
    )

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


# =========================================================
# OFFICIAL FILMS
# =========================================================

@app.get("/api/films/official")
async def official_films_api(
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    films = await get_official_films(
        limit=50
    )

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


# =========================================================
# FILM DETAILS
# =========================================================

@app.get("/api/films/{film_id}")
async def film_details_api(
    film_id: int,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    film = await get_film(
        film_id
    )

    if not film:

        raise HTTPException(
            status_code=404,
            detail="Film not found.",
        )

    return {
        "film": film_to_dict(
            film
        )
    }


# =========================================================
# FILM DOWNLOAD
# =========================================================

@app.get(
    "/api/films/{film_id}/download"
)
async def download_film_api(
    film_id: int,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    user = await require_user(
        x_telegram_init_data
    )

    film = await get_film(
        film_id
    )

    if not film:

        raise HTTPException(
            status_code=404,
            detail="Film not found.",
        )

    # -----------------------------------------------------
    # BOT DEEP LINK
    # -----------------------------------------------------

    bot_username = (
        settings.BOT_USERNAME
        .lstrip("@")
    )

    telegram_url = (
        f"https://t.me/"
        f"{bot_username}"
        f"?start=film_{film.id}"
    )

    return {
        "film_id": film.id,
        "telegram_url": telegram_url,
        "user_id": user.get("id"),
    }


# =========================================================
# POSTER
# =========================================================

@app.get(
    "/api/films/{film_id}/poster"
)
async def film_poster(
    film_id: int,
):

    film = await get_film(
        film_id
    )

    if not film:
        raise HTTPException(
            status_code=404,
            detail="Film not found.",
        )

    if not film.poster_file_id:

        raise HTTPException(
            status_code=404,
            detail="Poster not found.",
        )

    try:

        telegram_file = await bot.get_file(
            film.poster_file_id
        )

        if not telegram_file.file_path:

            raise HTTPException(
                status_code=404,
                detail="Poster file unavailable.",
            )

        async def poster_stream():

            buffer = bytearray()

            await bot.download_file(
                telegram_file.file_path,
                buffer,
            )

            yield bytes(buffer)

        return StreamingResponse(
            poster_stream(),
            media_type="image/jpeg",
        )

    except HTTPException:
        raise

    except Exception as error:

        logger.exception(
            "Poster error: %s",
            error,
        )

        raise HTTPException(
            status_code=500,
            detail="Poster could not be loaded.",
        )


# =========================================================
# REFERRAL API
# =========================================================

@app.get("/api/referral")
async def referral_api(
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    user = await require_user(
        x_telegram_init_data
    )

    telegram_id = user.get(
        "id"
    )

    count = await get_referral_count(
        telegram_id
    )

    link = await create_referral_link(
        bot,
        telegram_id,
    )

    target = settings.REFERRAL_TARGET

    if count >= target:

        status = (
            "🎉 مبارک! تاسو د فلم نشرولو اجازه لرئ."
        )

    else:

        remaining = max(
            target - count,
            0,
        )

        status = (
            f"🔒 د فلم نشرولو لپاره "
            f"{remaining} Referral نور پکار دي."
        )

    return {
        "count": count,
        "target": target,
        "link": link,
        "status": status,
        "can_publish": count >= target,
    }


# =========================================================
# REFERRAL LEADERS API
# =========================================================

@app.get(
    "/api/referral/leaders"
)
async def referral_leaders_api(
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    leaders = await get_referral_leaders(
        limit=20
    )

    result = []

    for user in leaders:

        name = (
            user.first_name
            or user.username
            or str(user.telegram_id)
        )

        result.append(
            {
                "name": name,
                "count": user.referral_count,
            }
        )

    return {
        "leaders": result
    }


# =========================================================
# ADMIN STATISTICS
# =========================================================

@app.get("/api/admin/stats")
async def admin_stats_api(
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    return await get_statistics()


# =========================================================
# ADMIN FILMS
# =========================================================

@app.get("/api/admin/films")
async def admin_films_api(
    x_telegram_init_data: str | None = Header(
        default=None,
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

    return {
        "films": [
            film_to_dict(
                film,
                include_admin=True,
            )
            for film in films
        ]
    }


# =========================================================
# ADMIN APPROVE FILM
# =========================================================

@app.post(
    "/api/admin/films/{film_id}/approve"
)
async def admin_approve_film(
    film_id: int,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    admin_user = await require_admin(
        x_telegram_init_data
    )

    async with SessionLocal() as session:

        result = await session.execute(
            select(Film).where(
                Film.id == film_id
            )
        )

        film = result.scalar_one_or_none()

        if not film:

            raise HTTPException(
                status_code=404,
                detail="Film not found.",
            )

        film.approved = True

        await session.commit()

    return {
        "success": True,
        "film_id": film_id,
        "admin_id": admin_user.get("id"),
    }


# =========================================================
# ADMIN REJECT FILM
# =========================================================

@app.post(
    "/api/admin/films/{film_id}/reject"
)
async def admin_reject_film(
    film_id: int,
    x_telegram_init_data: str | None = Header(
        default=None,
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

        if not film:

            raise HTTPException(
                status_code=404,
                detail="Film not found.",
            )

        film.approved = False

        await session.commit()

    return {
        "success": True,
        "film_id": film_id,
    }


# =========================================================
# ADMIN DELETE FILM
# =========================================================

@app.delete(
    "/api/admin/films/{film_id}"
)
async def admin_delete_film(
    film_id: int,
    x_telegram_init_data: str | None = Header(
        default=None,
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

        if not film:

            raise HTTPException(
                status_code=404,
                detail="Film not found.",
            )

        await session.delete(
            film
        )

        await session.commit()

    return {
        "success": True,
        "film_id": film_id,
    }


# =========================================================
# ADMIN CHANNELS
# =========================================================

@app.get("/api/admin/channels")
async def admin_channels_api(
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    async with SessionLocal() as session:

        result = await session.execute(
            select(Channel)
            .order_by(
                Channel.id.asc()
            )
        )

        channels = result.scalars().all()

    return {
        "channels": [
            {
                "id": channel.id,
                "title": channel.title,
                "username": channel.username,
                "category": channel.category,
                "active": channel.active,
            }
            for channel in channels
        ]
    }


# =========================================================
# ADMIN ADD CHANNEL
# =========================================================

class ChannelCreate(BaseModel):

    title: str
    username: str
    category: str = "pashto"


@app.post("/api/admin/channels")
async def admin_add_channel(
    data: ChannelCreate,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    username = data.username.strip()

    if not username.startswith("@"):

        username = "@" + username

    async with SessionLocal() as session:

        existing = await session.execute(
            select(Channel).where(
                Channel.username == username
            )
        )

        if existing.scalar_one_or_none():

            raise HTTPException(
                status_code=400,
                detail="Channel already exists.",
            )

        channel = Channel(
            title=data.title.strip(),
            username=username,
            category=data.category.strip(),
            active=True,
        )

        session.add(
            channel
        )

        await session.commit()
        await session.refresh(
            channel
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
# ADMIN DELETE CHANNEL
# =========================================================

@app.delete(
    "/api/admin/channels/{channel_id}"
)
async def admin_delete_channel(
    channel_id: int,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    async with SessionLocal() as session:

        result = await session.execute(
            select(Channel).where(
                Channel.id == channel_id
            )
        )

        channel = result.scalar_one_or_none()

        if not channel:

            raise HTTPException(
                status_code=404,
                detail="Channel not found.",
            )

        await session.delete(
            channel
        )

        await session.commit()

    return {
        "success": True
    }


# =========================================================
# FILM TO JSON
# =========================================================

def film_to_dict(
    film,
    include_admin: bool = False,
):

    data = {
        "id": film.id,
        "title": film.title,
        "year": film.year,
        "quality": film.quality,
        "genre": film.genre,
        "language": film.language,
        "description": film.description,
        "category": film.category,
        "poster_file_id": film.poster_file_id,
        "poster_url": (
            f"/api/films/{film.id}/poster"
            if film.poster_file_id
            else None
        ),
        "official": film.official,
        "created_at": (
            film.created_at.isoformat()
            if film.created_at
            else None
        ),
    }

    if include_admin:

        data.update(
            {
                "approved": film.approved,
                "uploader_id": film.uploader_id,
                "video_file_unique_id": (
                    film.video_file_unique_id
                ),
            }
        )

    return data


# =========================================================
# STARTUP
# =========================================================

@app.on_event("startup")
async def startup_event():

    logger.info(
        "Initializing database..."
    )

    await init_db()

    logger.info(
        "Database initialized."
    )

    # -----------------------------------------------------
    # START TELEGRAM BOT
    # -----------------------------------------------------

    asyncio.create_task(
        start_bot()
    )

    logger.info(
        "Telegram bot startup task created."
    )


# =========================================================
# SHUTDOWN
# =========================================================

@app.on_event("shutdown")
async def shutdown_event():

    try:

        await bot.session.close()

    except Exception as error:

        logger.warning(
            "Bot shutdown error: %s",
            error,
    )
