import asyncio
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl

from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.admin import is_admin
from app.bot import bot, start_bot, stop_bot
from app.database import SessionLocal, init_db
from app.films import (
    approve_film,
    calculate_image_hash,
    delete_film,
    get_category_films,
    get_film,
    get_latest_films,
    get_official_films,
    get_pending_films,
    search_by_image_hash,
    search_films,
    update_film,
)
from app.models import Channel, Film, User
from app.referrals import (
    ensure_referral_link,
    get_referral_leaders,
    get_referral_status,
    get_or_create_user,
)
from app.settings_db import (
    get_access_channel,
    get_auto_approve,
    get_channels,
    get_main_channel,
    get_referral_target,
    seed_default_settings,
    set_setting,
    update_channel,
    add_channel,
    delete_channel,
)


# =========================================================
# TELEGRAM WEB APP AUTH
# =========================================================

def validate_telegram_init_data(
    init_data: str,
) -> dict:
    if not init_data:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INIT_DATA_REQUIRED",
                "message": "Telegram initData is required",
            },
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
            detail={
                "code": "INVALID_INIT_DATA",
                "message": "Invalid Telegram initData",
            },
        )

    received_hash = parsed.pop(
        "hash",
        None,
    )

    if not received_hash:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_INIT_DATA",
                "message": "Telegram hash is missing",
            },
        )

    from app.config import settings

    secret_key = hmac.new(
        b"WebAppData",
        settings.BOT_TOKEN.encode(),
        hashlib.sha256,
    ).digest()

    data_check_string = "\n".join(
        f"{key}={value}"
        for key, value in sorted(
            parsed.items()
        )
    )

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
            detail={
                "code": "INVALID_INIT_DATA",
                "message": "Telegram initData validation failed",
            },
        )

    auth_date = parsed.get("auth_date")

    if auth_date:
        try:
            auth_time = int(auth_date)
        except ValueError:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "INVALID_AUTH_DATE",
                    "message": "Invalid auth date",
                },
            )

        current_time = int(
            time.time()
        )

        if current_time - auth_time > 86400:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "INIT_DATA_EXPIRED",
                    "message": "Telegram initData expired",
                },
            )

        if auth_time > current_time + 300:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "INVALID_AUTH_DATE",
                    "message": "Invalid future auth date",
                },
            )

    user_data = parsed.get("user")

    if not user_data:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "TELEGRAM_USER_MISSING",
                "message": "Telegram user is missing",
            },
        )

    try:
        telegram_user = json.loads(
            user_data
        )
    except Exception:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_TELEGRAM_USER",
                "message": "Invalid Telegram user data",
            },
        )

    if not telegram_user.get("id"):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "INVALID_TELEGRAM_USER",
                "message": "Telegram user ID is missing",
            },
        )

    return telegram_user


# =========================================================
# CURRENT USER
# =========================================================

async def get_current_user(
    x_telegram_init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):
    telegram_user = validate_telegram_init_data(
        x_telegram_init_data or ""
    )

    user = await get_or_create_user(
        telegram_id=int(
            telegram_user["id"]
        ),
        username=telegram_user.get(
            "username"
        ),
        first_name=telegram_user.get(
            "first_name"
        ),
    )

    if user.is_blocked:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "USER_BLOCKED",
                "message": "User is blocked",
            },
        )

    return user


# =========================================================
# CHANNEL ACCESS
# =========================================================

async def check_user_channel_access(
    telegram_id: int,
    channel: str,
) -> bool:
    try:
        member = await bot.get_chat_member(
            chat_id=channel,
            user_id=telegram_id,
        )

        return member.status in {
            "creator",
            "administrator",
            "member",
        }

    except Exception:
        return False


async def require_channel_access(
    user: User = Depends(
        get_current_user
    ),
):
    access_channel = (
        await get_access_channel()
    )

    has_access = (
        await check_user_channel_access(
            user.telegram_id,
            access_channel,
        )
    )

    if not has_access:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ACCESS_REQUIRED",
                "channel": access_channel,
                "message": "Please join the required channel",
            },
        )

    return user


# =========================================================
# ADMIN
# =========================================================

async def require_admin(
    user: User = Depends(
        get_current_user
    ),
):
    if not is_admin(
        user.telegram_id
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ADMIN_REQUIRED",
                "message": "Admin access required",
            },
        )

    return user


# =========================================================
# APP LIFESPAN
# =========================================================

@asynccontextmanager
async def lifespan(
    app: FastAPI,
):
    await init_db()
    await seed_default_settings()

    bot_task = asyncio.create_task(
        start_bot()
    )

    try:
        yield

    finally:
        bot_task.cancel()

        try:
            await bot_task
        except asyncio.CancelledError:
            pass

        await stop_bot()


app = FastAPI(
    title="ALL PRODUCTION FILMS",
    version="1.0.0",
    lifespan=lifespan,
)


# =========================================================
# STATIC FILES
# =========================================================

app.mount(
    "/static",
    StaticFiles(
        directory="static"
    ),
    name="static",
)


# =========================================================
# BASIC ROUTES
# =========================================================

@app.get("/")
async def index():
    return FileResponse(
        "static/index.html"
    )


@app.get("/admin")
async def admin_page():
    return FileResponse(
        "static/admin.html"
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "project": "ALL PRODUCTION FILMS",
    }


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(
        content={}
    )


# =========================================================
# USER API
# =========================================================

@app.get("/api/me")
async def api_me(
    user: User = Depends(
        require_channel_access
    ),
):
    referral = await get_referral_status(
        user.telegram_id
    )

    referral_link = (
        referral.get(
            "referral_link"
        )
    )

    if not referral_link:
        referral_link = (
            await ensure_referral_link(
                user.telegram_id
            )
        )

        referral[
            "referral_link"
        ] = referral_link

    return {
        "user": {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "can_publish": bool(
                user.can_publish
            ),
        },
        "referral": referral,
    }


# =========================================================
# REFERRALS
# =========================================================

@app.get("/api/referrals")
async def api_referrals(
    user: User = Depends(
        require_channel_access
    ),
):
    referral = await get_referral_status(
        user.telegram_id
    )

    if not referral.get(
        "referral_link"
    ):
        referral[
            "referral_link"
        ] = await ensure_referral_link(
            user.telegram_id
        )

    return referral


@app.get("/api/referrals/leaders")
async def api_referral_leaders(
    user: User = Depends(
        require_channel_access
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
):
    leaders = await get_referral_leaders(
        limit
    )

    result = []

    for index, leader in enumerate(
        leaders,
        start=1,
    ):
        result.append(
            {
                "rank": index,
                "telegram_id": leader.telegram_id,
                "username": leader.username,
                "first_name": leader.first_name,
                "referral_count": int(
                    leader.referral_count or 0
                ),
            }
        )

    return {
        "leaders": result
    }


# Compatibility endpoint
@app.get("/api/leaders")
async def api_leaders_compatibility(
    user: User = Depends(
        require_channel_access
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
):
    leaders = await get_referral_leaders(
        limit
    )

    result = []

    for index, leader in enumerate(
        leaders,
        start=1,
    ):
        result.append(
            {
                "rank": index,
                "telegram_id": leader.telegram_id,
                "username": leader.username,
                "first_name": leader.first_name,
                "referral_count": int(
                    leader.referral_count or 0
                ),
            }
        )

    return {
        "leaders": result
    }


# =========================================================
# CHANNELS
# =========================================================

@app.get("/api/channels")
async def api_channels(
    user: User = Depends(
        require_channel_access
    ),
):
    channels = await get_channels(
        active_only=True
    )

    return {
        "channels": [
            {
                "id": channel.id,
                "title": channel.title,
                "username": channel.username,
                "category": channel.category,
            }
            for channel in channels
        ]
    }


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
        "official": bool(
            film.official
        ),
        "poster_file_id": film.poster_file_id,
        "poster_file_unique_id": film.poster_file_unique_id,
        "poster_url": (
            f"/api/films/{film.id}/poster"
            if film.poster_file_id
            else None
        ),
        "created_at": (
            film.created_at.isoformat()
            if film.created_at
            else None
        ),
    }


# =========================================================
# FILM LIST
# =========================================================

@app.get("/api/films/latest")
async def api_latest_films(
    user: User = Depends(
        require_channel_access
    ),
    limit: int = Query(
        default=40,
        ge=1,
        le=100,
    ),
):
    films = await get_latest_films(
        limit
    )

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


@app.get("/api/films/search")
async def api_search_films(
    q: str = "",
    user: User = Depends(
        require_channel_access
    ),
    limit: int = Query(
        default=40,
        ge=1,
        le=100,
    ),
):
    films = await search_films(
        q,
        limit
    )

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


@app.get(
    "/api/films/category/{category}"
)
async def api_category_films(
    category: str,
    user: User = Depends(
        require_channel_access
    ),
    limit: int = Query(
        default=40,
        ge=1,
        le=100,
    ),
):
    films = await get_category_films(
        category,
        limit
    )

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


@app.get("/api/films/official")
async def api_official_films(
    user: User = Depends(
        require_channel_access
    ),
    limit: int = Query(
        default=40,
        ge=1,
        le=100,
    ),
):
    films = await get_official_films(
        limit
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
async def api_film_details(
    film_id: int,
    user: User = Depends(
        require_channel_access
    ),
):
    film = await get_film(
        film_id
    )

    if not film:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FILM_NOT_FOUND",
                "message": "Film not found",
            },
        )

    return film_to_dict(
        film
    )


# =========================================================
# POSTER
# =========================================================

@app.get(
    "/api/films/{film_id}/poster"
)
async def api_film_poster(
    film_id: int,
    user: User = Depends(
        require_channel_access
    ),
):
    film = await get_film(
        film_id
    )

    if not film:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FILM_NOT_FOUND",
                "message": "Film not found",
            },
        )

    if not film.poster_file_id:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "POSTER_NOT_FOUND",
                "message": "Poster not found",
            },
        )

    try:
        file = await bot.get_file(
            film.poster_file_id
        )

        from io import BytesIO

        buffer = BytesIO()

        await bot.download(
            file,
            destination=buffer,
        )

        buffer.seek(0)

        return JSONResponse(
            status_code=200,
            content={
                "error": "POSTER_PROXY_NOT_READY"
            },
        )

    except Exception:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "POSTER_DOWNLOAD_FAILED",
                "message": "Poster could not be loaded",
            },
        )


# =========================================================
# DOWNLOAD
# =========================================================

@app.get(
    "/api/films/{film_id}/download"
)
async def api_download_film(
    film_id: int,
    user: User = Depends(
        require_channel_access
    ),
):
    film = await get_film(
        film_id
    )

    if not film:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FILM_NOT_FOUND",
                "message": "Film not found",
            },
        )

    from app.config import settings

    deep_link = (
        f"https://t.me/"
        f"{settings.BOT_USERNAME}"
        f"?start=film_{film.id}"
    )

    return {
        "film_id": film.id,
        "deep_link": deep_link,
    }


# =========================================================
# IMAGE SEARCH
# =========================================================

@app.post(
    "/api/films/search-image"
)
async def api_search_image(
    image: UploadFile = File(...),
    user: User = Depends(
        require_channel_access
    ),
):
    if not image.content_type:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_IMAGE",
                "message": "Image type is required",
            },
        )

    if not image.content_type.startswith(
        "image/"
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_IMAGE",
                "message": "Only image files are allowed",
            },
        )

    image_bytes = await image.read()

    if not image_bytes:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "EMPTY_IMAGE",
                "message": "Image is empty",
            },
        )

    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "IMAGE_TOO_LARGE",
                "message": "Image is too large",
            },
        )

    try:
        image_hash = calculate_image_hash(
            image_bytes
        )
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_IMAGE",
                "message": "Could not process image",
            },
        )

    films = await search_by_image_hash(
        image_hash
    )

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


# =========================================================
# ADMIN STATS
# =========================================================

@app.get("/api/admin/stats")
async def api_admin_stats(
    user: User = Depends(
        require_admin
    ),
):
    from sqlalchemy import func

    async with SessionLocal() as session:
        users = await session.scalar(
            select(
                func.count(User.id)
            )
        )

        publishers = await session.scalar(
            select(
                func.count(User.id)
            ).where(
                User.can_publish.is_(True)
            )
        )

        films = await session.scalar(
            select(
                func.count(Film.id)
            )
        )

        approved_films = await session.scalar(
            select(
                func.count(Film.id)
            ).where(
                Film.approved.is_(True)
            )
        )

        channels = await session.scalar(
            select(
                func.count(Channel.id)
            ).where(
                Channel.active.is_(True)
            )
        )

    return {
        "users": users or 0,
        "publishers": publishers or 0,
        "films": films or 0,
        "approved_films": approved_films or 0,
        "channels": channels or 0,
    }


# =========================================================
# ADMIN SETTINGS
# =========================================================

@app.get("/api/admin/settings")
async def api_admin_settings(
    user: User = Depends(
        require_admin
    ),
):
    return {
        "referral_target": (
            await get_referral_target()
        ),
        "main_channel": (
            await get_main_channel()
        ),
        "access_channel": (
            await get_access_channel()
        ),
        "auto_approve_films": (
            await get_auto_approve()
        ),
    }


@app.put("/api/admin/settings")
async def api_update_admin_settings(
    payload: dict,
    user: User = Depends(
        require_admin
    ),
):
    if (
        "referral_target"
        in payload
    ):
        try:
            target = max(
                int(
                    payload[
                        "referral_target"
                    ]
                ),
                1,
            )
        except (
            TypeError,
            ValueError,
        ):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "INVALID_REFERRAL_TARGET",
                    "message": "Invalid referral target",
                },
            )

        await set_setting(
            "referral_target",
            str(target),
        )

    if (
        "main_channel"
        in payload
    ):
        await set_setting(
            "main_channel",
            str(
                payload[
                    "main_channel"
                ]
            ).strip(),
        )

    if (
        "access_channel"
        in payload
    ):
        await set_setting(
            "access_channel",
            str(
                payload[
                    "access_channel"
                ]
            ).strip(),
        )

    if (
        "auto_approve_films"
        in payload
    ):
        await set_setting(
            "auto_approve_films",
            str(
                bool(
                    payload[
                        "auto_approve_films"
                    ]
                )
            ).lower(),
        )

    return {
        "success": True
    }


# =========================================================
# ADMIN CHANNELS
# =========================================================

@app.get("/api/admin/channels")
async def api_admin_channels(
    user: User = Depends(
        require_admin
    ),
):
    channels = await get_channels(
        active_only=False
    )

    return {
        "channels": [
            {
                "id": channel.id,
                "title": channel.title,
                "username": channel.username,
                "category": channel.category,
                "active": bool(
                    channel.active
                ),
            }
            for channel in channels
        ]
    }


@app.post("/api/admin/channels")
async def api_admin_add_channel(
    payload: dict,
    user: User = Depends(
        require_admin
    ),
):
    title = str(
        payload.get(
            "title",
            ""
        )
    ).strip()

    username = str(
        payload.get(
            "username",
            ""
        )
    ).strip()

    category = str(
        payload.get(
            "category",
            "pashto"
        )
    ).strip()

    if not title or not username:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_CHANNEL",
                "message": "Title and username are required",
            },
        )

    channel = await add_channel(
        title=title,
        username=username,
        category=category,
    )

    return {
        "success": True,
        "channel": {
            "id": channel.id,
            "title": channel.title,
            "username": channel.username,
            "category": channel.category,
            "active": bool(
                channel.active
            ),
        },
    }


@app.put(
    "/api/admin/channels/{channel_id}"
)
async def api_admin_update_channel(
    channel_id: int,
    payload: dict,
    user: User = Depends(
        require_admin
    ),
):
    channel = await update_channel(
        channel_id=channel_id,
        title=payload.get(
            "title"
        ),
        username=payload.get(
            "username"
        ),
        category=payload.get(
            "category"
        ),
        active=payload.get(
            "active"
        ),
    )

    if channel is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CHANNEL_NOT_FOUND",
                "message": "Channel not found",
            },
        )

    return {
        "success": True
    }


@app.delete(
    "/api/admin/channels/{channel_id}"
)
async def api_admin_delete_channel(
    channel_id: int,
    user: User = Depends(
        require_admin
    ),
):
    deleted = await delete_channel(
        channel_id
    )

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CHANNEL_NOT_FOUND",
                "message": "Channel not found",
            },
        )

    return {
        "success": True
    }


# =========================================================
# ADMIN FILMS
# =========================================================

@app.get("/api/admin/films")
async def api_admin_films(
    user: User = Depends(
        require_admin
    ),
    pending_only: bool = False,
):
    if pending_only:
        films = await get_pending_films()
    else:
        async with SessionLocal() as session:
            result = await session.execute(
                select(Film).order_by(
                    Film.created_at.desc()
                )
            )
            films = result.scalars().all()

    return {
        "films": [
            {
                **film_to_dict(film),
                "approved": bool(
                    film.approved
                ),
                "uploader_id": film.uploader_id,
                "video_file_id": film.video_file_id,
            }
            for film in films
        ]
    }


@app.post(
    "/api/admin/films/{film_id}/approve"
)
async def api_admin_approve_film(
    film_id: int,
    user: User = Depends(
        require_admin
    ),
):
    success = await approve_film(
        film_id
    )

    if not success:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FILM_NOT_FOUND",
                "message": "Film not found",
            },
        )

    return {
        "success": True
    }


@app.post(
    "/api/admin/films/{film_id}/reject"
)
async def api_admin_reject_film(
    film_id: int,
    user: User = Depends(
        require_admin
    ),
):
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
                detail={
                    "code": "FILM_NOT_FOUND",
                    "message": "Film not found",
                },
            )

        film.approved = False

        await session.commit()

    return {
        "success": True
    }


@app.delete(
    "/api/admin/films/{film_id}"
)
async def api_admin_delete_film(
    film_id: int,
    user: User = Depends(
        require_admin
    ),
):
    success = await delete_film(
        film_id
    )

    if not success:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FILM_NOT_FOUND",
                "message": "Film not found",
            },
        )

    return {
        "success": True
    }


@app.put(
    "/api/admin/films/{film_id}"
)
async def api_admin_edit_film(
    film_id: int,
    payload: dict,
    user: User = Depends(
        require_admin
    ),
):
    film = await update_film(
        film_id=film_id,
        title=payload.get(
            "title"
        ),
        year=payload.get(
            "year"
        ),
        quality=payload.get(
            "quality"
        ),
        genre=payload.get(
            "genre"
        ),
        language=payload.get(
            "language"
        ),
        description=payload.get(
            "description"
        ),
        category=payload.get(
            "category"
        ),
        official=payload.get(
            "official"
        ),
    )

    if film is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FILM_NOT_FOUND",
                "message": "Film not found",
            },
        )

    return {
        "success": True,
        "film": film_to_dict(
            film
        ),
    }


@app.post(
    "/api/admin/films/{film_id}/official"
)
async def api_admin_official_film(
    film_id: int,
    payload: dict,
    user: User = Depends(
        require_admin
    ),
):
    value = bool(
        payload.get(
            "official",
            True
        )
    )

    film = await update_film(
        film_id=film_id,
        official=value,
    )

    if film is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FILM_NOT_FOUND",
                "message": "Film not found",
            },
        )

    return {
        "success": True
    }


# =========================================================
# ADMIN USERS
# =========================================================

@app.get("/api/admin/users")
async def api_admin_users(
    user: User = Depends(
        require_admin
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
):
    async with SessionLocal() as session:
        result = await session.execute(
            select(User)
            .order_by(
                User.created_at.desc()
            )
            .limit(limit)
        )

        users = result.scalars().all()

    return {
        "users": [
            {
                "id": item.id,
                "telegram_id": item.telegram_id,
                "username": item.username,
                "first_name": item.first_name,
                "referral_count": int(
                    item.referral_count or 0
                ),
                "can_publish": bool(
                    item.can_publish
                ),
                "is_blocked": bool(
                    item.is_blocked
                ),
                "created_at": (
                    item.created_at.isoformat()
                    if item.created_at
                    else None
                ),
            }
            for item in users
        ]
    }


@app.post(
    "/api/admin/users/{telegram_id}/block"
)
async def api_admin_block_user(
    telegram_id: int,
    user: User = Depends(
        require_admin
    ),
):
    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(
                User.telegram_id
                == telegram_id
            )
        )

        target = (
            result.scalar_one_or_none()
        )

        if target is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "USER_NOT_FOUND",
                    "message": "User not found",
                },
            )

        target.is_blocked = True

        await session.commit()

    return {
        "success": True
    }


@app.post(
    "/api/admin/users/{telegram_id}/unblock"
)
async def api_admin_unblock_user(
    telegram_id: int,
    user: User = Depends(
        require_admin
    ),
):
    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(
                User.telegram_id
                == telegram_id
            )
        )

        target = (
            result.scalar_one_or_none()
        )

        if target is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "USER_NOT_FOUND",
                    "message": "User not found",
                },
            )

        target.is_blocked = False

        await session.commit()

    return {
        "success": True
    }


@app.post(
    "/api/admin/users/{telegram_id}/publisher"
)
async def api_admin_publisher(
    telegram_id: int,
    payload: dict,
    user: User = Depends(
        require_admin
    ),
):
    value = bool(
        payload.get(
            "can_publish",
            False,
        )
    )

    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(
                User.telegram_id
                == telegram_id
            )
        )

        target = (
            result.scalar_one_or_none()
        )

        if target is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "USER_NOT_FOUND",
                    "message": "User not found",
                },
            )

        target.can_publish = value

        await session.commit()

    return {
        "success": True
    }


# =========================================================
# ERROR HANDLER
# =========================================================

@app.exception_handler(
    Exception
)
async def global_exception_handler(
    request,
    exc,
):
    print(
        "GLOBAL ERROR:",
        repr(exc),
    )

    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "INTERNAL_ERROR",
                "message": "Internal server error",
            }
        },
        )
