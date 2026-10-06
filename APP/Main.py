import asyncio
import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import (
    FastAPI,
    File,
    Header,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse
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
    search_by_image_hash,
    search_films,
)
from app.models import Film, User
from app.referrals import (
    ensure_referral_link,
    get_referral_leaders,
    get_referral_status,
)
from app.settings_db import (
    add_channel,
    delete_channel,
    get_access_channel,
    get_auto_approve,
    get_channels,
    get_main_channel,
    get_referral_target,
    seed_default_settings,
    set_setting,
    update_channel,
)


# =========================================================
# APP
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent

app = FastAPI(
    title="ALL PRODUCTION FILMS",
    version="1.0.0",
)

app.mount(
    "/static",
    StaticFiles(
        directory=str(BASE_DIR / "static")
    ),
    name="static",
)


# =========================================================
# HOME / ADMIN
# =========================================================

@app.get("/")
async def home():
    return FileResponse(
        BASE_DIR / "static" / "index.html"
    )


@app.get("/admin")
async def admin_page():
    return FileResponse(
        BASE_DIR / "static" / "admin.html"
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "project": "ALL PRODUCTION FILMS",
    }


# =========================================================
# TELEGRAM WEB APP AUTH
# =========================================================

def validate_telegram_init_data(
    init_data: str,
) -> dict:

    if not init_data:
        raise HTTPException(
            status_code=401,
            detail="Telegram initData is required",
        )

    try:
        parsed = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True,
            )
        )
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Invalid initData",
        )

    received_hash = parsed.pop(
        "hash",
        None,
    )

    if not received_hash:
        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram hash",
        )

    auth_date = parsed.get(
        "auth_date"
    )

    if auth_date:

        try:
            auth_time = int(auth_date)

            # 24-hour validity window
            if (
                abs(
                    int(time.time())
                    - auth_time
                )
                > 86400
            ):
                raise HTTPException(
                    status_code=401,
                    detail="Telegram session expired",
                )

        except ValueError:
            raise HTTPException(
                status_code=401,
                detail="Invalid auth_date",
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
            detail="Telegram authentication failed",
        )

    user_data = parsed.get(
        "user"
    )

    if not user_data:
        raise HTTPException(
            status_code=401,
            detail="Telegram user missing",
        )

    try:
        return json.loads(
            user_data
        )
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram user data",
        )


# =========================================================
# CURRENT USER
# =========================================================

async def get_current_user(
    init_data: str | None,
):

    if not init_data:
        raise HTTPException(
            status_code=401,
            detail="Telegram authentication required",
        )

    telegram_user = (
        validate_telegram_init_data(
            init_data
        )
    )

    telegram_id = int(
        telegram_user["id"]
    )

    async with SessionLocal() as session:

        result = await session.execute(
            select(User).where(
                User.telegram_id
                == telegram_id
            )
        )

        user = (
            result.scalar_one_or_none()
        )

        if user is None:

            user = User(
                telegram_id=telegram_id,
                username=telegram_user.get(
                    "username"
                ),
                first_name=telegram_user.get(
                    "first_name"
                ),
                referral_count=0,
                can_publish=False,
                is_blocked=False,
            )

            session.add(user)

            await session.commit()

            await session.refresh(
                user
            )

        else:

            user.username = (
                telegram_user.get(
                    "username"
                )
            )

            user.first_name = (
                telegram_user.get(
                    "first_name"
                )
            )

            await session.commit()

        if user.is_blocked:

            raise HTTPException(
                status_code=403,
                detail="USER_BLOCKED",
            )

        return user


# =========================================================
# ACCESS CHECK
# =========================================================

async def user_has_access(
    telegram_id: int,
) -> bool:

    channel = (
        await get_access_channel()
    )

    try:

        member = (
            await bot.get_chat_member(
                chat_id=channel,
                user_id=telegram_id,
            )
        )

        return member.status in {
            "member",
            "administrator",
            "creator",
            "restricted",
        }

    except Exception:

        return False


async def require_user(
    x_telegram_init_data: str | None,
):

    user = await get_current_user(
        x_telegram_init_data
    )

    has_access = await user_has_access(
        user.telegram_id
    )

    if not has_access:

        access_channel = (
            await get_access_channel()
        )

        raise HTTPException(
            status_code=403,
            detail={
                "code": "ACCESS_REQUIRED",
                "channel": access_channel,
            },
        )

    return user


# =========================================================
# ADMIN AUTH
# =========================================================

async def require_admin(
    x_telegram_init_data: str | None,
):

    if not x_telegram_init_data:

        raise HTTPException(
            status_code=401,
            detail="Telegram authentication required",
        )

    user = await get_current_user(
        x_telegram_init_data
    )

    if not is_admin(
        user.telegram_id
    ):

        raise HTTPException(
            status_code=403,
            detail="ADMIN_ONLY",
        )

    return user


# =========================================================
# FILM SERIALIZER
# =========================================================

def film_to_dict(
    film: Film,
):

    poster_url = None

    if film.poster_file_id:

        poster_url = (
            f"/api/films/{film.id}/poster"
        )

    return {
        "id": film.id,
        "title": film.title,
        "year": film.year,
        "quality": film.quality,
        "genre": film.genre,
        "language": film.language,
        "description": film.description,
        "category": film.category,
        "poster_url": poster_url,
        "official": film.official,
        "approved": film.approved,
        "created_at": (
            film.created_at.isoformat()
            if film.created_at
            else None
        ),
    }


# =========================================================
# USER API
# =========================================================

@app.get("/api/me")
async def api_me(
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    user = await require_user(
        x_telegram_init_data
    )

    referral = (
        await get_referral_status(
            user.telegram_id
        )
    )

    referral_link = (
        await ensure_referral_link(
            user.telegram_id
        )
    )

    return {
        "user": {
            "telegram_id":
                user.telegram_id,
            "username":
                user.username,
            "first_name":
                user.first_name,
        },
        "referral": {
            **referral,
            "referral_link":
                referral_link,
        },
        "access": True,
    }


# =========================================================
# REFERRALS
# =========================================================

@app.get("/api/referrals")
async def api_referrals(
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    user = await require_user(
        x_telegram_init_data
    )

    status = (
        await get_referral_status(
            user.telegram_id
        )
    )

    link = (
        await ensure_referral_link(
            user.telegram_id
        )
    )

    return {
        **status,
        "referral_link": link,
    }


@app.get("/api/leaders")
async def api_leaders(
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    leaders = (
        await get_referral_leaders(
            limit=50
        )
    )

    return {
        "leaders": [
            {
                "rank": index + 1,
                "telegram_id":
                    user.telegram_id,
                "username":
                    user.username,
                "first_name":
                    user.first_name,
                "referral_count":
                    user.referral_count,
            }
            for index, user
            in enumerate(leaders)
        ]
    }


# =========================================================
# FILMS
# =========================================================

@app.get("/api/films/latest")
async def api_latest_films(
    limit: int = 30,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    limit = max(
        1,
        min(limit, 100),
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
            .limit(limit)
        )

        films = result.scalars().all()

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


@app.get("/api/films/search")
async def api_search_films(
    q: str,
    limit: int = 30,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    limit = max(
        1,
        min(limit, 100),
    )

    films = await search_films(
        q,
        limit=limit,
    )

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


@app.get("/api/films/category/{category}")
async def api_category_films(
    category: str,
    limit: int = 30,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    limit = max(
        1,
        min(limit, 100),
    )

    films = (
        await get_category_films(
            category,
            limit=limit,
        )
    )

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


@app.get("/api/films/official")
async def api_official_films(
    limit: int = 30,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    limit = max(
        1,
        min(limit, 100),
    )

    films = (
        await get_official_films(
            limit=limit
        )
    )

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


@app.get("/api/films/{film_id}")
async def api_film(
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
            detail="Film not found",
        )

    return film_to_dict(
        film
    )


# =========================================================
# FILM POSTER
# =========================================================

@app.get("/api/films/{film_id}/poster")
async def api_film_poster(
    film_id: int,
):

    async with SessionLocal() as session:

        result = await session.execute(
            select(Film).where(
                Film.id == film_id,
                Film.approved.is_(True),
            )
        )

        film = (
            result.scalar_one_or_none()
        )

    if not film:
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

        telegram_file = (
            await bot.get_file(
                film.poster_file_id
            )
        )

        if not telegram_file.file_path:
            raise HTTPException(
                status_code=404,
                detail="Poster unavailable",
            )

        file_url = (
            f"https://api.telegram.org/file/"
            f"bot{settings.BOT_TOKEN}/"
            f"{telegram_file.file_path}"
        )

        from fastapi.responses import RedirectResponse

        return RedirectResponse(
            file_url
        )

    except HTTPException:
        raise

    except Exception:

        raise HTTPException(
            status_code=404,
            detail="Poster unavailable",
        )


# =========================================================
# IMAGE SEARCH
# =========================================================

@app.post("/api/films/search-image")
async def api_search_by_image(
    image: UploadFile = File(...),
    limit: int = 20,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    if not image.content_type:
        raise HTTPException(
            status_code=400,
            detail="Invalid image",
        )

    if not image.content_type.startswith(
        "image/"
    ):

        raise HTTPException(
            status_code=400,
            detail="Only image files are allowed",
        )

    data = await image.read()

    if not data:

        raise HTTPException(
            status_code=400,
            detail="Empty image",
        )

    if len(data) > 10 * 1024 * 1024:

        raise HTTPException(
            status_code=413,
            detail="Image is too large",
        )

    try:

        from app.films import (
            calculate_image_hash,
        )

        image_hash = (
            calculate_image_hash(
                data
            )
        )

    except Exception:

        raise HTTPException(
            status_code=400,
            detail="Could not process image",
        )

    films = await search_by_image_hash(
        image_hash,
        limit=limit,
    )

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


# =========================================================
# DOWNLOAD / TELEGRAM DEEP LINK
# =========================================================

@app.get("/api/films/{film_id}/download")
async def api_film_download(
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
            detail="Film not found",
        )

    username = (
        settings.BOT_USERNAME
        .lstrip("@")
    )

    return {
        "film_id": film.id,
        "bot_username": username,
        "deep_link":
            f"https://t.me/{username}"
            f"?start=film_{film.id}",
    }


# =========================================================
# CHANNELS
# =========================================================

@app.get("/api/channels")
async def api_channels(
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_user(
        x_telegram_init_data
    )

    channels = await get_channels(
        active_only=True
    )

    return {
        "channels": [
            {
                "id": channel.id,
                "title": channel.title,
                "username":
                    channel.username,
                "category":
                    channel.category,
                "active":
                    channel.active,
            }
            for channel in channels
        ]
    }


# =========================================================
# ADMIN - STATS
# =========================================================

@app.get("/api/admin/stats")
async def api_admin_stats(
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
# ADMIN - SETTINGS
# =========================================================

@app.get("/api/admin/settings")
async def api_admin_settings(
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    return {
        "referral_target":
            await get_referral_target(),
        "main_channel":
            await get_main_channel(),
        "access_channel":
            await get_access_channel(),
        "auto_approve":
            await get_auto_approve(),
    }


class SettingsUpdate(BaseModel):

    referral_target: int | None = None
    main_channel: str | None = None
    access_channel: str | None = None
    auto_approve: bool | None = None


@app.put("/api/admin/settings")
async def api_update_settings(
    payload: SettingsUpdate,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    admin = await require_admin(
        x_telegram_init_data
    )

    if payload.referral_target is not None:

        if payload.referral_target < 1:

            raise HTTPException(
                status_code=400,
                detail="Referral target must be at least 1",
            )

        await set_setting(
            "referral_target",
            str(
                payload.referral_target
            ),
        )

    if payload.main_channel:

        await set_setting(
            "main_channel",
            payload.main_channel.strip(),
        )

    if payload.access_channel:

        await set_setting(
            "access_channel",
            payload.access_channel.strip(),
        )

    if payload.auto_approve is not None:

        await set_setting(
            "auto_approve_films",
            str(
                payload.auto_approve
            ).lower(),
        )

    return {
        "success": True,
        "admin_id":
            admin.telegram_id,
    }


# =========================================================
# ADMIN - CHANNELS
# =========================================================

class ChannelCreate(BaseModel):

    title: str
    username: str
    category: str = "general"


class ChannelUpdate(BaseModel):

    title: str | None = None
    username: str | None = None
    category: str | None = None
    active: bool | None = None


@app.get("/api/admin/channels")
async def api_admin_channels(
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    channels = await get_channels(
        active_only=False
    )

    return {
        "channels": [
            {
                "id": channel.id,
                "title":
                    channel.title,
                "username":
                    channel.username,
                "category":
                    channel.category,
                "active":
                    channel.active,
            }
            for channel in channels
        ]
    }


@app.post("/api/admin/channels")
async def api_add_channel(
    payload: ChannelCreate,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    username = payload.username.strip()

    if not username.startswith("@"):

        username = (
            "@"
            + username
        )

    channel = await add_channel(
        title=payload.title.strip(),
        username=username,
        category=payload.category.strip(),
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


@app.put("/api/admin/channels/{channel_id}")
async def api_update_channel(
    channel_id: int,
    payload: ChannelUpdate,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    channel = await update_channel(
        channel_id=channel_id,
        title=payload.title,
        username=payload.username,
        category=payload.category,
        active=payload.active,
    )

    if not channel:

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


@app.delete("/api/admin/channels/{channel_id}")
async def api_delete_channel(
    channel_id: int,
    x_telegram_init_data: str | None = Header(
        default=None,
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
# ADMIN - FILMS
# =========================================================

@app.get("/api/admin/films")
async def api_admin_films(
    limit: int = 100,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    limit = max(
        1,
        min(limit, 200),
    )

    async with SessionLocal() as session:

        result = await session.execute(
            select(Film)
            .order_by(
                Film.created_at.desc()
            )
            .limit(limit)
        )

        films = result.scalars().all()

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


# =========================================================
# ADMIN - FILM ACTIONS
# =========================================================

@app.post(
    "/api/admin/films/{film_id}/approve"
)
async def api_approve_film(
    film_id: int,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    from app.films import approve_film

    film = await approve_film(
        film_id
    )

    if not film:

        raise HTTPException(
            status_code=404,
            detail="Film not found",
        )

    return {
        "success": True,
        "film": film_to_dict(
            film
        ),
    }


@app.post(
    "/api/admin/films/{film_id}/reject"
)
async def api_reject_film(
    film_id: int,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    from app.films import reject_film

    film = await reject_film(
        film_id
    )

    if not film:

        raise HTTPException(
            status_code=404,
            detail="Film not found",
        )

    return {
        "success": True
    }


@app.delete(
    "/api/admin/films/{film_id}"
)
async def api_delete_film(
    film_id: int,
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    await require_admin(
        x_telegram_init_data
    )

    from app.films import delete_film

    deleted = await delete_film(
        film_id
    )

    if not deleted:

        raise HTTPException(
            status_code=404,
            detail="Film not found",
        )

    return {
        "success": True
    }


class OfficialUpdate(BaseModel):

    official: bool


@app.put(
    "/api/admin/films/{film_id}/official"
)
async def api_update_official(
    film_id: int,
    payload: OfficialUpdate,
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

        film = (
            result.scalar_one_or_none()
        )

        if not film:

            raise HTTPException(
                status_code=404,
                detail="Film not found",
            )

        film.official = (
            payload.official
        )

        await session.commit()

        await session.refresh(
            film
        )

    return {
        "success": True,
        "film": film_to_dict(
            film
        ),
    }


# =========================================================
# STARTUP
# =========================================================

@app.on_event("startup")
async def startup_event():

    await init_db()

    await seed_default_settings()

    # Default channels are inserted only if
    # they don't already exist.
    default_channels = [
        (
            "Afghan Production",
            "@afghanproduction",
            "general",
        ),
        (
            "ALL PASHTO DUBBED",
            "@ALL_PASHTO_DUBBED",
            "dubbed",
        ),
        (
            "PASHTO SUB",
            "@PASHTO_SUB",
            "subtitle",
        ),
    ]

    async with SessionLocal() as session:

        for title, username, category in default_channels:

            result = await session.execute(
                select(
                    __import__(
                        "app.models",
                        fromlist=[
                            "Channel"
                        ],
                    ).Channel
                ).where(
                    __import__(
                        "app.models",
                        fromlist=[
                            "Channel"
                        ],
                    ).Channel.username
                    == username
                )
            )

            existing = (
                result.scalar_one_or_none()
            )

            if existing is None:

                ChannelModel = __import__(
                    "app.models",
                    fromlist=[
                        "Channel"
                    ],
                ).Channel

                session.add(
                    ChannelModel(
                        title=title,
                        username=username,
                        category=category,
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
