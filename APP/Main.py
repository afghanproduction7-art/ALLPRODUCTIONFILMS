import asyncio
import hashlib
import hmac
import json
import logging
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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from app.admin import is_admin
from app.bot import bot, start_bot
from app.config import settings
from app.database import SessionLocal, init_db
from app.films import (
    approve_film,
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
    get_or_create_user,
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


logging.basicConfig(
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


bot_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task

    logger.info(
        "Initializing database..."
    )

    await init_db()
    await seed_default_settings()

    logger.info(
        "Starting Telegram bot..."
    )

    bot_task = asyncio.create_task(
        start_bot()
    )

    yield

    logger.info(
        "Stopping application..."
    )

    if bot_task:
        bot_task.cancel()

        try:
            await bot_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception(
                "Bot task stopped with error."
            )

    try:
        await bot.session.close()
    except Exception:
        logger.exception(
            "Could not close Telegram session."
        )


app = FastAPI(
    title="ALL PRODUCTION FILMS",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------
# STATIC FILES
# ---------------------------------------------------------


app.mount(
    "/static",
    StaticFiles(
        directory="static"
    ),
    name="static",
)


@app.get(
    "/",
    include_in_schema=False,
)
async def home():
    return FileResponse(
        "static/index.html"
    )


@app.get(
    "/admin",
    include_in_schema=False,
)
async def admin_page():
    return FileResponse(
        "static/admin.html"
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "project": "ALL PRODUCTION FILMS",
        "bot_username": settings.BOT_USERNAME,
    }


# ---------------------------------------------------------
# TELEGRAM WEB APP INIT DATA
# ---------------------------------------------------------


def validate_telegram_init_data(
    init_data: str,
) -> dict:
    """
    Validate Telegram Mini App initData.

    The secret key is derived from the bot token.
    """

    if not init_data:
        raise HTTPException(
            status_code=401,
            detail="Missing Telegram initData.",
        )

    try:
        pairs = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True,
            )
        )

        received_hash = pairs.pop(
            "hash",
            None,
        )

        if not received_hash:
            raise HTTPException(
                status_code=401,
                detail="Missing initData hash.",
            )

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(
                pairs.items()
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
                detail="Invalid Telegram initData.",
            )

        auth_date = pairs.get(
            "auth_date"
        )

        if auth_date:
            try:
                auth_timestamp = int(
                    auth_date
                )
            except ValueError:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid auth_date.",
                )

            now = int(
                time.time()
            )

            # Reject data older than 24 hours
            # and data from the future.
            if auth_timestamp > now + 300:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid auth_date.",
                )

            if now - auth_timestamp > 86400:
                raise HTTPException(
                    status_code=401,
                    detail="Expired Telegram initData.",
                )

        return pairs

    except HTTPException:
        raise

    except Exception:
        logger.exception(
            "Telegram initData validation failed."
        )

        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram initData.",
        )


async def get_current_user(
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

    data = validate_telegram_init_data(
        x_telegram_init_data
    )

    user_json = data.get(
        "user"
    )

    if not user_json:
        raise HTTPException(
            status_code=401,
            detail="Telegram user data missing.",
        )

    try:
        telegram_user = json.loads(
            user_json
        )
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram user data.",
        )

    telegram_id = telegram_user.get(
        "id"
    )

    if not telegram_id:
        raise HTTPException(
            status_code=401,
            detail="Telegram user ID missing.",
        )

    user = await get_or_create_user(
        telegram_id=int(
            telegram_id
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
            detail="User is blocked.",
        )

    return user


async def require_admin(
    current_user=Depends(
        get_current_user
    ),
):
    if not is_admin(
        current_user.telegram_id
    ):
        raise HTTPException(
            status_code=403,
            detail="Admin access required.",
        )

    return current_user


async def user_has_access(
    telegram_id: int,
) -> bool:
    channel = await get_access_channel()

    try:
        member = await bot.get_chat_member(
            chat_id=channel,
            user_id=telegram_id,
        )

        status = str(
            member.status
        )

        return status in {
            "member",
            "administrator",
            "creator",
        }

    except Exception:
        logger.exception(
            "Could not check channel access."
        )

        return False


async def require_channel_access(
    current_user=Depends(
        get_current_user
    ),
):
    allowed = await user_has_access(
        current_user.telegram_id
    )

    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="Join the required channel first.",
        )

    return current_user


# ---------------------------------------------------------
# USER / MINI APP
# ---------------------------------------------------------


@app.get("/api/me")
async def api_me(
    current_user=Depends(
        require_channel_access
    ),
):
    referral = await ensure_referral_link(
        current_user.telegram_id
    )

    status = await get_referral_status(
        current_user.telegram_id
    )

    return {
        "id": current_user.id,
        "telegram_id": current_user.telegram_id,
        "username": current_user.username,
        "first_name": current_user.first_name,
        "referral_count": status.get(
            "referral_count",
            status.get("count", 0),
        ),
        "referral_target": status.get(
            "target",
            settings.REFERRAL_TARGET,
        ),
        "remaining": status.get(
            "remaining",
            settings.REFERRAL_TARGET,
        ),
        "can_publish": status.get(
            "can_publish",
            False,
        ),
        "referral_link": referral,
        "access": True,
    }


@app.get("/api/referrals")
async def api_referrals(
    current_user=Depends(
        require_channel_access
    ),
):
    status = await get_referral_status(
        current_user.telegram_id
    )

    return status


@app.get("/api/referrals/leaders")
async def api_referral_leaders(
    current_user=Depends(
        require_channel_access
    ),
):
    leaders = await get_referral_leaders(
        limit=50
    )

    return {
        "leaders": [
            {
                "telegram_id": user.telegram_id,
                "username": user.username,
                "first_name": user.first_name,
                "referral_count": int(
                    user.referral_count or 0
                ),
            }
            for user in leaders
        ]
    }


# ---------------------------------------------------------
# FILM APIs
# ---------------------------------------------------------


def film_to_dict(
    film: Film,
) -> dict:
    return {
        "id": film.id,
        "title": film.title,
        "year": film.year,
        "quality": film.quality,
        "genre": film.genre,
        "language": film.language,
        "description": film.description,
        "category": film.category,
        "poster_file_id": film.poster_file_id,
        "approved": film.approved,
        "official": film.official,
        "created_at": (
            film.created_at.isoformat()
            if film.created_at
            else None
        ),
    }


@app.get("/api/films/latest")
async def api_latest_films(
    current_user=Depends(
        require_channel_access
    ),
    limit: int = Query(
        default=30,
        ge=1,
        le=100,
    ),
):
    films = await get_latest_films(
        limit=limit
    )

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


@app.get("/api/films/search")
async def api_search_films(
    q: str = Query(
        default=""
    ),
    current_user=Depends(
        require_channel_access
    ),
    limit: int = Query(
        default=30,
        ge=1,
        le=100,
    ),
):
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
    current_user=Depends(
        require_channel_access
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
):
    films = await get_category_films(
        category,
        limit=limit,
    )

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


@app.get("/api/films/official")
async def api_official_films(
    current_user=Depends(
        require_channel_access
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
):
    films = await get_official_films(
        limit=limit
    )

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


@app.get("/api/films/{film_id}")
async def api_film_details(
    film_id: int,
    current_user=Depends(
        require_channel_access
    ),
):
    film = await get_film(
        film_id
    )

    if film is None:
        raise HTTPException(
            status_code=404,
            detail="Film not found.",
        )

    return film_to_dict(
        film
    )


@app.get("/api/films/{film_id}/download")
async def api_film_download(
    film_id: int,
    current_user=Depends(
        require_channel_access
    ),
):
    film = await get_film(
        film_id
    )

    if film is None:
        raise HTTPException(
            status_code=404,
            detail="Film not found.",
        )

    bot_username = (
        settings.BOT_USERNAME
        .lstrip("@")
    )

    return {
        "film_id": film.id,
        "bot_username": bot_username,
        "deep_link": (
            f"https://t.me/"
            f"{bot_username}"
            f"?start=film_{film.id}"
        ),
    }


# ---------------------------------------------------------
# IMAGE SEARCH
# ---------------------------------------------------------


@app.post("/api/films/search-image")
async def api_search_image(
    image: UploadFile = File(...),
    current_user=Depends(
        require_channel_access
    ),
):
    content_type = (
        image.content_type or ""
    ).lower()

    if not content_type.startswith(
        "image/"
    ):
        raise HTTPException(
            status_code=400,
            detail="Please upload an image.",
        )

    image_bytes = await image.read()

    if not image_bytes:
        raise HTTPException(
            status_code=400,
            detail="Empty image.",
        )

    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail="Image is too large.",
        )

    from app.films import calculate_image_hash

    try:
        image_hash = calculate_image_hash(
            image_bytes
        )
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid image.",
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


# ---------------------------------------------------------
# ADMIN - STATS
# ---------------------------------------------------------


@app.get("/api/admin/stats")
async def api_admin_stats(
    admin=Depends(
        require_admin
    ),
):
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

        pending_films = await session.scalar(
            select(
                func.count(Film.id)
            ).where(
                Film.approved.is_(False)
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
        "pending_films": pending_films or 0,
        "channels": channels or 0,
    }


# ---------------------------------------------------------
# ADMIN - SETTINGS
# ---------------------------------------------------------


@app.get("/api/admin/settings")
async def api_admin_settings(
    admin=Depends(
        require_admin
    ),
):
    return {
        "referral_target": await get_referral_target(),
        "main_channel": await get_main_channel(),
        "access_channel": await get_access_channel(),
        "auto_approve_films": await get_auto_approve(),
    }


@app.post("/api/admin/settings")
async def api_update_settings(
    data: dict,
    admin=Depends(
        require_admin
    ),
):
    if "referral_target" in data:
        try:
            target = int(
                data["referral_target"]
            )
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="Invalid referral target.",
            )

        if target < 1:
            raise HTTPException(
                status_code=400,
                detail="Referral target must be at least 1.",
            )

        await set_setting(
            "referral_target",
            str(target),
        )

    if "main_channel" in data:
        await set_setting(
            "main_channel",
            str(
                data["main_channel"]
            ),
        )

    if "access_channel" in data:
        await set_setting(
            "access_channel",
            str(
                data["access_channel"]
            ),
        )

    if "auto_approve_films" in data:
        value = bool(
            data["auto_approve_films"]
        )

        await set_setting(
            "auto_approve_films",
            str(value).lower(),
        )

    return {
        "success": True,
        "settings": {
            "referral_target": await get_referral_target(),
            "main_channel": await get_main_channel(),
            "access_channel": await get_access_channel(),
            "auto_approve_films": await get_auto_approve(),
        },
    }


# ---------------------------------------------------------
# ADMIN - CHANNELS
# ---------------------------------------------------------


@app.get("/api/admin/channels")
async def api_admin_channels(
    admin=Depends(
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
                "active": channel.active,
            }
            for channel in channels
        ]
    }


@app.post("/api/admin/channels")
async def api_add_channel(
    data: dict,
    admin=Depends(
        require_admin
    ),
):
    title = str(
        data.get(
            "title",
            ""
        )
    ).strip()

    username = str(
        data.get(
            "username",
            ""
        )
    ).strip()

    category = str(
        data.get(
            "category",
            "pashto"
        )
    ).strip()

    if not title or not username:
        raise HTTPException(
            status_code=400,
            detail="Title and username are required.",
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
            "active": channel.active,
        },
    }


@app.put("/api/admin/channels/{channel_id}")
async def api_update_channel(
    channel_id: int,
    data: dict,
    admin=Depends(
        require_admin
    ),
):
    channel = await update_channel(
        channel_id=channel_id,
        title=data.get(
            "title"
        ),
        username=data.get(
            "username"
        ),
        category=data.get(
            "category"
        ),
        active=data.get(
            "active"
        ),
    )

    if channel is None:
        raise HTTPException(
            status_code=404,
            detail="Channel not found.",
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


@app.delete(
    "/api/admin/channels/{channel_id}"
)
async def api_delete_channel(
    channel_id: int,
    admin=Depends(
        require_admin
    ),
):
    deleted = await delete_channel(
        channel_id
    )

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Channel not found.",
        )

    return {
        "success": True
    }


# ---------------------------------------------------------
# ADMIN - FILMS
# ---------------------------------------------------------


@app.get("/api/admin/films")
async def api_admin_films(
    admin=Depends(
        require_admin
    ),
    pending: bool = False,
    limit: int = Query(
        default=100,
        ge=1,
        le=200,
    ),
):
    if pending:
        films = await get_pending_films(
            limit=limit
        )
    else:
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


@app.post(
    "/api/admin/films/{film_id}/approve"
)
async def api_approve_film(
    film_id: int,
    admin=Depends(
        require_admin
    ),
):
    success = await approve_film(
        film_id
    )

    if not success:
        raise HTTPException(
            status_code=404,
            detail="Film not found.",
        )

    return {
        "success": True
    }


@app.post(
    "/api/admin/films/{film_id}/reject"
)
async def api_reject_film(
    film_id: int,
    admin=Depends(
        require_admin
    ),
):
    from app.films import reject_film

    success = await reject_film(
        film_id
    )

    if not success:
        raise HTTPException(
            status_code=404,
            detail="Film not found.",
        )

    return {
        "success": True
    }


@app.delete(
    "/api/admin/films/{film_id}"
)
async def api_delete_film(
    film_id: int,
    admin=Depends(
        require_admin
    ),
):
    success = await delete_film(
        film_id
    )

    if not success:
        raise HTTPException(
            status_code=404,
            detail="Film not found.",
        )

    return {
        "success": True
    }


@app.put(
    "/api/admin/films/{film_id}"
)
async def api_edit_film(
    film_id: int,
    data: dict,
    admin=Depends(
        require_admin
    ),
):
    film = await update_film(
        film_id=film_id,
        title=data.get(
            "title"
        ),
        year=data.get(
            "year"
        ),
        quality=data.get(
            "quality"
        ),
        genre=data.get(
            "genre"
        ),
        language=data.get(
            "language"
        ),
        description=data.get(
            "description"
        ),
        category=data.get(
            "category"
        ),
        official=data.get(
            "official"
        ),
    )

    if film is None:
        raise HTTPException(
            status_code=404,
            detail="Film not found.",
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
async def api_set_official(
    film_id: int,
    data: dict,
    admin=Depends(
        require_admin
    ),
):
    official = bool(
        data.get(
            "official",
            True,
        )
    )

    film = await update_film(
        film_id=film_id,
        official=official,
    )

    if film is None:
        raise HTTPException(
            status_code=404,
            detail="Film not found.",
        )

    return {
        "success": True,
        "film": film_to_dict(
            film
        ),
    }


# ---------------------------------------------------------
# ADMIN - USERS
# ---------------------------------------------------------


@app.get("/api/admin/users")
async def api_admin_users(
    admin=Depends(
        require_admin
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=200,
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
                "id": user.id,
                "telegram_id": user.telegram_id,
                "username": user.username,
                "first_name": user.first_name,
                "referral_count": int(
                    user.referral_count or 0
                ),
                "can_publish": user.can_publish,
                "is_blocked": user.is_blocked,
                "created_at": (
                    user.created_at.isoformat()
                    if user.created_at
                    else None
                ),
            }
            for user in users
        ]
    }


@app.post(
    "/api/admin/users/{telegram_id}/block"
)
async def api_block_user(
    telegram_id: int,
    data: dict,
    admin=Depends(
        require_admin
    ),
):
    blocked = bool(
        data.get(
            "blocked",
            True,
        )
    )

    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(
                User.telegram_id
                == telegram_id
            )
        )

        user = result.scalar_one_or_none()

        if user is None:
            raise HTTPException(
                status_code=404,
                detail="User not found.",
            )

        user.is_blocked = blocked

        await session.commit()

    return {
        "success": True,
        "telegram_id": telegram_id,
        "blocked": blocked,
    }


# ---------------------------------------------------------
# ADMIN - MAKE USER PUBLISHER
# ---------------------------------------------------------


@app.post(
    "/api/admin/users/{telegram_id}/publisher"
)
async def api_set_publisher(
    telegram_id: int,
    data: dict,
    admin=Depends(
        require_admin
    ),
):
    can_publish = bool(
        data.get(
            "can_publish",
            True,
        )
    )

    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(
                User.telegram_id
                == telegram_id
            )
        )

        user = result.scalar_one_or_none()

        if user is None:
            raise HTTPException(
                status_code=404,
                detail="User not found.",
            )

        user.can_publish = can_publish

        await session.commit()

    return {
        "success": True,
        "telegram_id": telegram_id,
        "can_publish": can_publish,
    }


# ---------------------------------------------------------
# ROOT FALLBACK
# ---------------------------------------------------------


@app.get(
    "/favicon.ico",
    include_in_schema=False,
)
async def favicon():
    return {
        "status": "ok"
    }
