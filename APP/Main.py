import asyncio
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qsl

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.admin import get_statistics, is_admin
from app.bot import (
    bot,
    get_or_create_user,
    start_bot,
)
from app.config import settings
from app.database import (
    SessionLocal,
    init_db,
)
from app.films import (
    approve_film,
    calculate_image_hash,
    delete_film,
    get_category_films,
    get_film,
    get_official_films,
    search_by_image_hash,
    search_films,
    reject_film,
)
from app.models import Channel, Film, User
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

logging.basicConfig(
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# APP
# =========================================================

app = FastAPI(
    title="ALL PRODUCTION FILMS",
    description="Telegram Movie Distribution System",
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
# HOME
# =========================================================

@app.get("/")
async def home():
    return FileResponse(
        "static/index.html"
    )


# =========================================================
# ADMIN PAGE
# =========================================================

@app.get("/admin")
async def admin_page():
    return FileResponse(
        "static/admin.html"
    )


# =========================================================
# HEALTH
# =========================================================

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "project": "ALL PRODUCTION FILMS",
        "bot": settings.BOT_USERNAME,
    }


# =========================================================
# TELEGRAM WEB APP VALIDATION
# =========================================================

def validate_telegram_init_data(
    init_data: str,
) -> dict:

    if not init_data:
        raise HTTPException(
            status_code=401,
            detail="Telegram initData is required.",
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
            detail="Invalid Telegram initData.",
        )

    received_hash = parsed.pop(
        "hash",
        None,
    )

    if not received_hash:

        raise HTTPException(
            status_code=401,
            detail="Telegram hash is missing.",
        )

    data_check_string = "\n".join(
        f"{key}={parsed[key]}"
        for key in sorted(parsed)
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
            detail="Invalid Telegram signature.",
        )

    # -----------------------------------------------------
    # AUTH DATE
    # -----------------------------------------------------

    auth_date = parsed.get(
        "auth_date"
    )

    if auth_date:

        try:

            auth_timestamp = int(
                auth_date
            )

            current_time = int(
                time.time()
            )

            # 24 hours
            if (
                current_time
                - auth_timestamp
                > 86400
            ):

                raise HTTPException(
                    status_code=401,
                    detail="Telegram initData expired.",
                )

        except ValueError:

            raise HTTPException(
                status_code=401,
                detail="Invalid auth_date.",
            )

    # -----------------------------------------------------
    # TELEGRAM USER
    # -----------------------------------------------------

    telegram_user = parsed.get(
        "user"
    )

    if not telegram_user:

        raise HTTPException(
            status_code=401,
            detail="Telegram user is missing.",
        )

    try:

        user_data = json.loads(
            telegram_user
        )

    except json.JSONDecodeError:

        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram user data.",
        )

    if not user_data.get("id"):

        raise HTTPException(
            status_code=401,
            detail="Telegram user ID is missing.",
        )

    return {
        "data": parsed,
        "user": user_data,
    }


# =========================================================
# GET INIT DATA
# =========================================================

def get_init_data_from_request(
    request: Request,
) -> str:

    init_data = request.headers.get(
        "X-Telegram-Init-Data"
    )

    if not init_data:

        init_data = request.headers.get(
            "X-Telegram-InitData"
        )

    if not init_data:

        init_data = request.query_params.get(
            "initData"
        )

    return init_data or ""


# =========================================================
# CURRENT USER
# =========================================================

async def get_current_user(
    request: Request,
) -> User:

    init_data = get_init_data_from_request(
        request
    )

    validated = validate_telegram_init_data(
        init_data
    )

    telegram_user = validated["user"]

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

    return user


# =========================================================
# ACCESS CHECK
# =========================================================

async def user_has_access(
    user: User,
) -> bool:

    channel = await get_access_channel()

    if not channel:
        return True

    try:

        member = await bot.get_chat_member(
            chat_id=channel,
            user_id=user.telegram_id,
        )

        status = str(
            member.status
        )

        return status in {
            "member",
            "administrator",
            "creator",
            "ChatMemberStatus.MEMBER",
            "ChatMemberStatus.ADMINISTRATOR",
            "ChatMemberStatus.CREATOR",
        }

    except Exception as exc:

        logger.warning(
            "Access check failed: %s",
            exc,
        )

        return False


# =========================================================
# REQUIRED USER
# =========================================================

async def require_user(
    request: Request,
) -> User:

    user = await get_current_user(
        request
    )

    allowed = await user_has_access(
        user
    )

    if not allowed:

        raise HTTPException(
            status_code=403,
            detail={
                "error": "ACCESS_REQUIRED",
                "channel": await get_access_channel(),
            },
        )

    return user


# =========================================================
# REQUIRED ADMIN
# =========================================================

async def require_admin(
    request: Request,
) -> User:

    user = await get_current_user(
        request
    )

    if not is_admin(
        user.telegram_id
    ):

        raise HTTPException(
            status_code=403,
            detail="Admin access required.",
        )

    return user


# =========================================================
# ME
# =========================================================

@app.get("/api/me")
async def api_me(
    user: User = Depends(require_user),
):

    async with SessionLocal() as session:

        try:

            referral_link = (
                await ensure_referral_link(
                    session,
                    user,
                )
            )

            await session.commit()

        except Exception:

            await session.rollback()

            referral_link = (
                user.referral_link
            )

    target = await get_referral_target()

    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "referral_count": user.referral_count,
        "referral_target": target,
        "can_publish": user.can_publish,
        "referral_link": referral_link,
        "is_blocked": user.is_blocked,
    }


# =========================================================
# REFERRAL STATUS
# =========================================================

@app.get("/api/referrals")
async def api_referrals(
    user: User = Depends(require_user),
):

    status = await get_referral_status(
        user.telegram_id
    )

    if not status:

        return {
            "referral_count": 0,
            "target": await get_referral_target(),
            "remaining": await get_referral_target(),
            "can_publish": False,
            "referral_link": None,
        }

    count = status.get(
        "referral_count",
        0,
    )

    target = status.get(
        "target",
        await get_referral_target(),
    )

    return {
        **status,
        "remaining": max(
            target - count,
            0,
        ),
    }


# =========================================================
# REFERRAL LEADERS
# =========================================================

@app.get("/api/leaders")
async def api_leaders(
    user: User = Depends(require_user),
):

    leaders = await get_referral_leaders(
        limit=50
    )

    result = []

    for index, item in enumerate(
        leaders,
        start=1,
    ):

        if isinstance(
            item,
            dict,
        ):

            result.append(
                {
                    "rank": index,
                    **item,
                }
            )

        else:

            result.append(
                {
                    "rank": index,
                    "name": (
                        getattr(
                            item,
                            "first_name",
                            None,
                        )
                        or getattr(
                            item,
                            "username",
                            None,
                        )
                        or str(
                            getattr(
                                item,
                                "telegram_id",
                                "",
                            )
                        )
                    ),
                    "referral_count": getattr(
                        item,
                        "referral_count",
                        0,
                    ),
                }
            )

    return result


# =========================================================
# LATEST FILMS
# =========================================================

@app.get("/api/films/latest")
async def api_latest_films(
    user: User = Depends(require_user),
):

    async with SessionLocal() as session:

        films = await search_films(
            session,
            "",
            limit=30,
        )

    return [
        film_to_dict(
            film
        )
        for film in films
    ]


# =========================================================
# SEARCH FILMS
# =========================================================

@app.get("/api/films/search")
async def api_search_films(
    q: str = "",
    user: User = Depends(require_user),
):

    query = q.strip()

    if not query:

        return []

    async with SessionLocal() as session:

        films = await search_films(
            session,
            query,
            limit=50,
        )

    return [
        film_to_dict(
            film
        )
        for film in films
    ]


# =========================================================
# CATEGORY
# =========================================================

@app.get("/api/films/category/{category}")
async def api_category_films(
    category: str,
    user: User = Depends(require_user),
):

    async with SessionLocal() as session:

        films = await get_category_films(
            session,
            category,
            limit=50,
        )

    return [
        film_to_dict(
            film
        )
        for film in films
    ]


# =========================================================
# OFFICIAL
# =========================================================

@app.get("/api/films/official")
async def api_official_films(
    user: User = Depends(require_user),
):

    async with SessionLocal() as session:

        films = await get_official_films(
            session,
            limit=50,
        )

    return [
        film_to_dict(
            film
        )
        for film in films
    ]


# =========================================================
# IMAGE SEARCH
# =========================================================

@app.post("/api/films/search-image")
async def api_search_image(
    image: UploadFile = File(...),
    user: User = Depends(require_user),
):

    content_type = image.content_type or ""

    if not content_type.startswith(
        "image/"
    ):

        raise HTTPException(
            status_code=400,
            detail="Only image files are allowed.",
        )

    data = await image.read()

    if not data:

        raise HTTPException(
            status_code=400,
            detail="Empty image.",
        )

    # Prevent extremely large uploads
    if len(data) > 10 * 1024 * 1024:

        raise HTTPException(
            status_code=413,
            detail="Image is too large.",
        )

    try:

        image_hash = calculate_image_hash(
            data
        )

    except Exception as exc:

        logger.warning(
            "Image hash failed: %s",
            exc,
        )

        raise HTTPException(
            status_code=400,
            detail="Invalid image.",
        )

    async with SessionLocal() as session:

        films = await search_by_image_hash(
            session,
            str(image_hash),
        )

    return [
        film_to_dict(
            film
        )
        for film in films
    ]


# =========================================================
# FILM DETAILS
# =========================================================

@app.get("/api/films/{film_id}")
async def api_film(
    film_id: int,
    user: User = Depends(require_user),
):

    async with SessionLocal() as session:

        film = await get_film(
            session,
            film_id,
        )

    if not film:

        raise HTTPException(
            status_code=404,
            detail="Film not found.",
        )

    return film_to_dict(
        film,
        detailed=True,
    )


# =========================================================
# FILM DOWNLOAD
# =========================================================

@app.get("/api/films/{film_id}/download")
async def api_film_download(
    film_id: int,
    user: User = Depends(require_user),
):

    async with SessionLocal() as session:

        film = await get_film(
            session,
            film_id,
        )

    if not film:

        raise HTTPException(
            status_code=404,
            detail="Film not found.",
        )

    return {
        "film_id": film.id,
        "title": film.title,
        "bot_username": settings.BOT_USERNAME,
        "url": (
            f"https://t.me/"
            f"{settings.BOT_USERNAME}"
            f"?start=film_{film.id}"
        ),
    }


# =========================================================
# POSTER
# =========================================================

@app.get("/api/films/{film_id}/poster")
async def api_film_poster(
    film_id: int,
):

    async with SessionLocal() as session:

        film = await get_film(
            session,
            film_id,
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
                detail="Telegram file path not found.",
            )

        file_url = (
            f"https://api.telegram.org/file/bot"
            f"{settings.BOT_TOKEN}/"
            f"{telegram_file.file_path}"
        )

        return RedirectResponse(
            url=file_url
        )

    except HTTPException:
        raise

    except Exception as exc:

        logger.warning(
            "Poster error: %s",
            exc,
        )

        raise HTTPException(
            status_code=404,
            detail="Poster unavailable.",
        )


# =========================================================
# CHANNELS
# =========================================================

@app.get("/api/channels")
async def api_channels(
    user: User = Depends(require_user),
):

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
            "url": (
                f"https://t.me/"
                f"{channel.username.lstrip('@')}"
            ),
        }
        for channel in channels
    ]


# =========================================================
# FILM SERIALIZER
# =========================================================

def film_to_dict(
    film: Film,
    detailed: bool = False,
):

    result = {
        "id": film.id,
        "title": film.title,
        "year": film.year,
        "quality": film.quality,
        "genre": film.genre,
        "language": film.language,
        "category": film.category,
        "poster_url": (
            f"/api/films/"
            f"{film.id}/poster"
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

    if detailed:

        result.update(
            {
                "description": film.description,
                "download_url": (
                    f"/api/films/"
                    f"{film.id}/download"
                ),
            }
        )

    return result


# =========================================================
# ADMIN STATS
# =========================================================

@app.get("/api/admin/stats")
async def api_admin_stats(
    admin: User = Depends(require_admin),
):

    return await get_statistics()


# =========================================================
# ADMIN SETTINGS
# =========================================================

@app.get("/api/admin/settings")
async def api_admin_settings(
    admin: User = Depends(require_admin),
):

    return {
        "referral_target": await get_referral_target(),
        "main_channel": await get_main_channel(),
        "access_channel": await get_access_channel(),
        "auto_approve_films": await get_auto_approve(),
    }


@app.post("/api/admin/settings")
async def api_admin_save_settings(
    payload: dict,
    admin: User = Depends(require_admin),
):

    allowed = {
        "referral_target",
        "main_channel",
        "access_channel",
        "auto_approve_films",
    }

    for key, value in payload.items():

        if key not in allowed:
            continue

        await set_setting(
            key,
            str(value).lower()
            if isinstance(
                value,
                bool,
            )
            else str(value),
        )

    return {
        "success": True,
    }


# =========================================================
# ADMIN CHANNELS
# =========================================================

@app.get("/api/admin/channels")
async def api_admin_channels(
    admin: User = Depends(require_admin),
):

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


@app.post("/api/admin/channels")
async def api_admin_add_channel(
    payload: dict,
    admin: User = Depends(require_admin),
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
            "general",
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
        "id": channel.id,
    }


@app.put("/api/admin/channels/{channel_id}")
async def api_admin_update_channel(
    channel_id: int,
    payload: dict,
    admin: User = Depends(require_admin),
):

    channel = await update_channel(
        channel_id=channel_id,
        title=payload.get("title"),
        username=payload.get("username"),
        category=payload.get("category"),
        active=payload.get("active"),
    )

    if not channel:

        raise HTTPException(
            status_code=404,
            detail="Channel not found.",
        )

    return {
        "success": True,
    }


@app.delete("/api/admin/channels/{channel_id}")
async def api_admin_delete_channel(
    channel_id: int,
    admin: User = Depends(require_admin),
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
        "success": True,
    }


# =========================================================
# ADMIN FILMS
# =========================================================

@app.get("/api/admin/films")
async def api_admin_films(
    admin: User = Depends(require_admin),
):

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
        {
            **film_to_dict(
                film,
                detailed=True,
            ),
            "approved": film.approved,
            "official": film.official,
            "uploader_id": film.uploader_id,
        }
        for film in films
    ]


@app.post(
    "/api/admin/films/{film_id}/approve"
)
async def api_admin_approve_film(
    film_id: int,
    admin: User = Depends(require_admin),
):

    async with SessionLocal() as session:

        film = await approve_film(
            session,
            film_id,
        )

        if not film:

            raise HTTPException(
                status_code=404,
                detail="Film not found.",
            )

        await session.commit()

    return {
        "success": True,
    }


@app.post(
    "/api/admin/films/{film_id}/reject"
)
async def api_admin_reject_film(
    film_id: int,
    admin: User = Depends(require_admin),
):

    async with SessionLocal() as session:

        film = await reject_film(
            session,
            film_id,
        )

        if not film:

            raise HTTPException(
                status_code=404,
                detail="Film not found.",
            )

        await session.commit()

    return {
        "success": True,
    }


@app.delete(
    "/api/admin/films/{film_id}"
)
async def api_admin_delete_film(
    film_id: int,
    admin: User = Depends(require_admin),
):

    async with SessionLocal() as session:

        deleted = await delete_film(
            session,
            film_id,
        )

        if not deleted:

            raise HTTPException(
                status_code=404,
                detail="Film not found.",
            )

        await session.commit()

    return {
        "success": True,
    }


# =========================================================
# ADMIN OFFICIAL
# =========================================================

@app.post(
    "/api/admin/films/{film_id}/official"
)
async def api_admin_official_film(
    film_id: int,
    payload: dict,
    admin: User = Depends(require_admin),
):

    official = bool(
        payload.get(
            "official",
            True,
        )
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

        film.official = official

        await session.commit()

    return {
        "success": True,
        "official": official,
    }


# =========================================================
# STARTUP
# =========================================================

@app.on_event("startup")
async def startup_event():

    logger.info(
        "Starting ALL PRODUCTION FILMS..."
    )

    # -----------------------------------------------------
    # DATABASE
    # -----------------------------------------------------

    await init_db()

    # -----------------------------------------------------
    # DEFAULT SETTINGS
    # -----------------------------------------------------

    await seed_default_settings()

    # -----------------------------------------------------
    # DEFAULT CHANNELS
    # -----------------------------------------------------

    default_channels = [
        {
            "title": "Afghan Production",
            "username": "@afghanproduction",
            "category": "movies",
        },
        {
            "title": "ALL PASHTO DUBBED",
            "username": "@ALL_PASHTO_DUBBED",
            "category": "dubbed",
        },
        {
            "title": "PASHTO SUB",
            "username": "@PASHTO_SUB",
            "category": "subtitle",
        },
    ]

    async with SessionLocal() as session:

        for item in default_channels:

            result = await session.execute(
                select(Channel).where(
                    Channel.username
                    == item["username"]
                )
            )

            exists = (
                result.scalar_one_or_none()
            )

            if not exists:

                session.add(
                    Channel(
                        title=item["title"],
                        username=item["username"],
                        category=item["category"],
                        active=True,
                    )
                )

        await session.commit()

    # -----------------------------------------------------
    # BOT
    # -----------------------------------------------------

    asyncio.create_task(
        start_bot()
    )

    logger.info(
        "ALL PRODUCTION FILMS started."
    )


# =========================================================
# SHUTDOWN
# =========================================================

@app.on_event("shutdown")
async def shutdown_event():

    try:

        await bot.session.close()

    except Exception as exc:

        logger.warning(
            "Bot shutdown warning: %s",
            exc,
        )

    logger.info(
        "ALL PRODUCTION FILMS stopped."
        )
