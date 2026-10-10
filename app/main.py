
import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import parse_qsl

import httpx
from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Query,
    UploadFile,
    File,
    Depends,
)
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import SessionLocal, init_db, close_db
from app.models import User, Film, Channel, Setting, Referral
from app import films as film_service
from app import admin as admin_service
from app import settings_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("all_production_films")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

BOT_TOKEN = settings.BOT_TOKEN
ADMIN_IDS = {int(x) for x in settings.ADMIN_IDS}
BOT_USERNAME = os.getenv(
    "BOT_USERNAME", "ALL_PRODUCTION_FILMBOT"
).lstrip("@").strip()


# --------------------------------------------------
# Request models
# --------------------------------------------------

class FilmUpdate(BaseModel):
    approved: Optional[bool] = None
    official: Optional[bool] = None
    title: Optional[str] = None
    year: Optional[str] = None
    quality: Optional[str] = None
    genre: Optional[str] = None
    language: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None


class ChannelCreate(BaseModel):
    title: str
    username: str
    category: str = "pashto"
    active: bool = True


class ChannelUpdate(BaseModel):
    title: Optional[str] = None
    username: Optional[str] = None
    category: Optional[str] = None
    active: Optional[bool] = None


class SettingsUpdate(BaseModel):
    settings: Optional[dict] = None
    referral_target: Optional[int] = None
    main_channel: Optional[str] = None
    access_channel: Optional[str] = None
    auto_approve_films: Optional[bool] = None


class UserUpdate(BaseModel):
    blocked: Optional[bool] = None
    can_publish: Optional[bool] = None


# --------------------------------------------------
# Telegram authentication
# --------------------------------------------------

def validate_telegram_init_data(init_data: str) -> dict:
    if not init_data or not BOT_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Telegram authentication required",
        )

    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = data.pop("hash", None)

        if not received_hash:
            raise ValueError("Missing hash")

        auth_date = int(data.get("auth_date", "0"))

        # Reject expired data and timestamps too far in the future.
        now = int(time.time())
        if not auth_date or now - auth_date > 86400 or auth_date - now > 60:
            raise ValueError("Expired authentication data")

        data_check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(data.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            raise ValueError("Invalid signature")

        user_data = json.loads(data.get("user", "{}"))
        if not user_data.get("id"):
            raise ValueError("Missing user")

        return user_data

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Telegram authentication failed: %s", exc)
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired Telegram authentication",
        )


async def get_current_user(
    x_telegram_init_data: Optional[str] = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):
    if not x_telegram_init_data:
        raise HTTPException(
            status_code=401,
            detail="Telegram authentication required",
        )

    tg_user = validate_telegram_init_data(x_telegram_init_data)
    telegram_id = int(tg_user["id"])

    async with SessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()

        if user is None:
            user = User(
                telegram_id=telegram_id,
                username=tg_user.get("username"),
                first_name=tg_user.get("first_name", ""),
                referral_count=0,
                can_publish=False,
                is_blocked=False,
            )
            session.add(user)

            try:
                await session.commit()
                await session.refresh(user)
            except IntegrityError:
                await session.rollback()
                result = await session.execute(
                    select(User).where(User.telegram_id == telegram_id)
                )
                user = result.scalar_one_or_none()

        if user is None:
            raise HTTPException(
                status_code=500,
                detail="Unable to load user",
            )

        if user.is_blocked:
            raise HTTPException(status_code=403, detail="Account blocked")

        return {
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "referral_count": int(user.referral_count or 0),
            "can_publish": bool(user.can_publish),
            "referral_link": user.referral_link,
            "is_blocked": bool(user.is_blocked),
        }


async def require_admin(user=Depends(get_current_user)):
    if int(user["telegram_id"]) not in ADMIN_IDS:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_channel_access(user=Depends(get_current_user)):
    channel = await settings_db.get_access_channel()

    if not channel:
        return user

    username = str(channel).strip()
    if username.startswith("https://t.me/"):
        username = username.rstrip("/").rsplit("/", 1)[-1]
    username = username.lstrip("@")

    if not BOT_TOKEN:
        raise HTTPException(status_code=503, detail="Bot is not configured")

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember",
                params={
                    "chat_id": f"@{username}",
                    "user_id": int(user["telegram_id"]),
                },
            )
            response.raise_for_status()
            data = response.json()

        if not data.get("ok"):
            logger.warning("getChatMember failed: %s", data)
            raise HTTPException(
                status_code=503,
                detail="Unable to verify channel membership",
            )

        status = data["result"].get("status")
        if status not in ("creator", "administrator", "member"):
            raise HTTPException(status_code=403, detail="JOIN_REQUIRED")

        return user

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Channel membership check failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Unable to verify channel membership",
        )


# --------------------------------------------------
# Response helpers
# --------------------------------------------------

def film_public_dict(film):
    return {
        "id": film.id,
        "title": film.title,
        "year": film.year,
        "quality": film.quality,
        "genre": film.genre,
        "language": film.language,
        "description": film.description,
        "category": film.category,
        "approved": bool(film.approved),
        "official": bool(film.official),
        "poster_url": f"/api/films/{film.id}/poster",
        "created_at": (
            film.created_at.isoformat()
            if getattr(film, "created_at", None)
            else None
        ),
    }


def channel_public_dict(channel):
    username = str(channel.username or "").strip()
    url = (
        username
        if username.startswith("http")
        else f"https://t.me/{username.lstrip('@')}"
    )
    return {
        "id": channel.id,
        "title": channel.title,
        "username": username,
        "category": channel.category,
        "active": bool(channel.active),
        "url": url,
    }


async def get_active_channels():
    async with SessionLocal() as session:
        result = await session.execute(
            select(Channel)
            .where(Channel.active.is_(True))
            .order_by(Channel.id.desc())
        )
        return result.scalars().all()


async def save_setting(key: str, value):
    return await settings_db.set_setting(key, value)


async def read_all_settings():
    async with SessionLocal() as session:
        result = await session.execute(select(Setting))
        return {row.key: row.value for row in result.scalars().all()}


async def record_admin_action(admin_id: int, action: str, target_id=None):
    try:
        await admin_service.log_admin_action(
            admin_id=admin_id,
            action=action,
            target_id=target_id,
        )
    except Exception:
        logger.exception("Could not record admin action")


# --------------------------------------------------
# Application startup
# --------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    try:
        await settings_db.seed_default_settings()
    except Exception:
        logger.exception("Could not seed default settings")

    bot_task = None
    try:
        from app.bot import start_bot
        bot_task = asyncio.create_task(start_bot())
        app.state.bot_task = bot_task
    except Exception:
        logger.exception("Could not start Telegram bot")

    logger.info("ALL PRODUCTION FILMS started")

    try:
        yield
    finally:
        if bot_task and not bot_task.done():
            bot_task.cancel()
            try:
                await bot_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Error stopping Telegram bot")

        await close_db()


app = FastAPI(
    title="ALL PRODUCTION FILMS",
    version="1.0.1",
    lifespan=lifespan,
)


# --------------------------------------------------
# Pages and health
# --------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "project": "ALL PRODUCTION FILMS"}


@app.get("/")
async def index():
    path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.isfile(path):
        return JSONResponse(
            {"error": "Mini App index.html not found"},
            status_code=500,
        )
    return FileResponse(path)


@app.get("/admin")
async def admin_page():
    path = os.path.join(STATIC_DIR, "admin.html")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Admin page not found")
    return FileResponse(path)


if os.path.isdir(STATIC_DIR):
    app.mount(
        "/static",
        StaticFiles(directory=STATIC_DIR),
        name="static",
    )


# --------------------------------------------------
# User API
# --------------------------------------------------

@app.get("/api/me")
async def api_me(user=Depends(get_current_user)):
    await require_channel_access(user)
    return {
        **user,
        "referral_target": await settings_db.get_referral_target(),
        "main_channel": await settings_db.get_main_channel(),
        "access_channel": await settings_db.get_access_channel(),
    }


@app.get("/api/referrals")
async def api_referrals(user=Depends(get_current_user)):
    await require_channel_access(user)

    async with SessionLocal() as session:
        result = await session.execute(
            select(Referral).where(
                Referral.inviter_id == int(user["telegram_id"])
            )
        )
        rows = result.scalars().all()

    return {
        "count": len(rows),
        "referral_count": user["referral_count"],
        "target": await settings_db.get_referral_target(),
        "referral_link": user.get("referral_link"),
        "referrals": [
            {
                "invited_id": row.invited_id,
                "joined_at": (
                    row.joined_at.isoformat()
                    if getattr(row, "joined_at", None)
                    else None
                ),
            }
            for row in rows
        ],
    }


@app.get("/api/referrals/leaders")
@app.get("/api/leaders")
async def api_referral_leaders(user=Depends(get_current_user)):
    await require_channel_access(user)

    async with SessionLocal() as session:
        result = await session.execute(
            select(User)
            .where(User.is_blocked.is_(False))
            .order_by(User.referral_count.desc())
            .limit(50)
        )
        rows = result.scalars().all()

    return {
        "leaders": [
            {
                "rank": i + 1,
                "telegram_id": row.telegram_id,
                "username": row.username,
                "first_name": row.first_name,
                "referral_count": int(row.referral_count or 0),
            }
            for i, row in enumerate(rows)
        ]
    }


# --------------------------------------------------
# Film search and listing
# --------------------------------------------------

@app.get("/api/films/latest")
async def api_latest_films(
    limit: int = Query(default=30, ge=1, le=100),
    user=Depends(get_current_user),
):
    await require_channel_access(user)

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film)
            .where(Film.approved.is_(True))
            .order_by(Film.created_at.desc())
            .limit(limit)
        )
        rows = result.scalars().all()

    return {"films": [film_public_dict(row) for row in rows]}


@app.get("/api/films/search")
async def api_search_films(
    q: str = Query(default="", max_length=200),
    query: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    user=Depends(get_current_user),
):
    await require_channel_access(user)
    search_text = (q or query).strip()

    if not search_text:
        return {"films": []}

    pattern = f"%{search_text}%"

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film)
            .where(
                Film.approved.is_(True),
                or_(
                    Film.title.ilike(pattern),
                    Film.normalized_title.ilike(pattern),
                    Film.genre.ilike(pattern),
                    Film.description.ilike(pattern),
                ),
            )
            .order_by(Film.created_at.desc())
            .limit(limit)
        )
        rows = result.scalars().all()

    return {"films": [film_public_dict(row) for row in rows]}


@app.get("/api/films/category/{category}")
async def api_category_films(
    category: str,
    limit: int = Query(default=50, ge=1, le=100),
    user=Depends(get_current_user),
):
    await require_channel_access(user)

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film)
            .where(
                Film.approved.is_(True),
                Film.category == category,
            )
            .order_by(Film.created_at.desc())
            .limit(limit)
        )
        rows = result.scalars().all()

    return {"films": [film_public_dict(row) for row in rows]}


@app.get("/api/films/official")
async def api_official_films(
    limit: int = Query(default=50, ge=1, le=100),
    user=Depends(get_current_user),
):
    await require_channel_access(user)

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film)
            .where(
                Film.approved.is_(True),
                Film.official.is_(True),
            )
            .order_by(Film.created_at.desc())
            .limit(limit)
        )
        rows = result.scalars().all()

    return {"films": [film_public_dict(row) for row in rows]}


@app.get("/api/films/{film_id}/download")
async def api_film_download(
    film_id: int,
    user=Depends(get_current_user),
):
    await require_channel_access(user)

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film).where(
                Film.id == film_id,
                Film.approved.is_(True),
            )
        )
        film = result.scalar_one_or_none()

    if film is None:
        raise HTTPException(status_code=404, detail="Film not found")

    link = f"https://t.me/{BOT_USERNAME}?start=film_{film.id}"
    return {"url": link, "telegram_url": link, "film_id": film.id}


@app.get("/api/films/{film_id}/poster")
async def api_film_poster(film_id: int):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Film).where(
                Film.id == film_id,
                Film.approved.is_(True),
            )
        )
        film = result.scalar_one_or_none()

    if film is None or not film.poster_file_id:
        raise HTTPException(status_code=404, detail="Poster not found")

    if not BOT_TOKEN:
        raise HTTPException(status_code=503, detail="Bot not configured")

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            file_response = await client.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                params={"file_id": film.poster_file_id},
            )
            file_response.raise_for_status()
            file_data = file_response.json()

            if not file_data.get("ok"):
                raise HTTPException(
                    status_code=502,
                    detail="Telegram could not retrieve the poster",
                )

            file_path = file_data["result"]["file_path"]
            image_response = await client.get(
                f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            )
            image_response.raise_for_status()

            return Response(
                content=image_response.content,
                media_type=image_response.headers.get(
                    "content-type", "image/jpeg"
                ),
                headers={"Cache-Control": "public, max-age=3600"},
            )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Poster retrieval failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not load poster")


@app.get("/api/films/{film_id}")
async def api_film_detail(
    film_id: int,
    user=Depends(get_current_user),
):
    await require_channel_access(user)

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film).where(
                Film.id == film_id,
                Film.approved.is_(True),
            )
        )
        film = result.scalar_one_or_none()

    if film is None:
        raise HTTPException(status_code=404, detail="Film not found")

    return film_public_dict(film)


@app.get("/api/channels")
async def api_channels(user=Depends(get_current_user)):
    await require_channel_access(user)
    rows = await get_active_channels()
    return {"channels": [channel_public_dict(row) for row in rows]}


@app.post("/api/films/search-image")
async def api_search_image(
    image: UploadFile = File(...),
    user=Depends(get_current_user),
):
    await require_channel_access(user)

    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty image")
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large")

    try:
        image_hash = film_service.calculate_image_hash(content)
        if not image_hash:
            return {"films": []}

        rows = await film_service.search_by_image_hash(image_hash)
        return {
            "films": [
                film_public_dict(row)
                for row in rows
                if row.approved
            ]
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Image search failed: %s", exc)
        raise HTTPException(
            status_code=400,
            detail="Could not process this image",
        )


# --------------------------------------------------
# Admin API
# --------------------------------------------------

@app.get("/api/admin/stats")
async def api_admin_stats(user=Depends(require_admin)):
    return await admin_service.get_admin_stats()


@app.get("/api/admin/films/pending")
async def api_admin_pending_films(user=Depends(require_admin)):
    rows = await film_service.get_pending_films()
    return {"films": [film_public_dict(row) for row in rows]}


@app.put("/api/admin/films/{film_id}")
async def api_admin_update_film(
    film_id: int,
    payload: FilmUpdate,
    user=Depends(require_admin),
):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Film).where(Film.id == film_id)
        )
        film = result.scalar_one_or_none()

        if film is None:
            raise HTTPException(status_code=404, detail="Film not found")

        for key, value in payload.model_dump(exclude_unset=True).items():
            if value is not None:
                setattr(film, key, value)

        await session.commit()
        await session.refresh(film)
        output = film_public_dict(film)

    await record_admin_action(
        user["telegram_id"], "update_film", film_id
    )
    return {"success": True, "film": output}


@app.delete("/api/admin/films/{film_id}")
async def api_admin_delete_film(
    film_id: int,
    user=Depends(require_admin),
):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Film).where(Film.id == film_id)
        )
        film = result.scalar_one_or_none()

        if film is None:
            raise HTTPException(status_code=404, detail="Film not found")

        await session.delete(film)
        await session.commit()

    await record_admin_action(
        user["telegram_id"], "delete_film", film_id
    )
    return {"success": True, "deleted_id": film_id}


@app.get("/api/admin/users")
async def api_admin_users(
    limit: int = Query(default=100, ge=1, le=500),
    user=Depends(require_admin),
):
    return {"users": await admin_service.get_users(limit=limit)}


@app.put("/api/admin/users/{telegram_id}")
async def api_admin_update_user(
    telegram_id: int,
    payload: UserUpdate,
    user=Depends(require_admin),
):
    changed = False

    if payload.blocked is not None:
        success = await admin_service.set_user_blocked(
            telegram_id, payload.blocked
        )
        if not success:
            raise HTTPException(status_code=404, detail="User not found")
        await record_admin_action(
            user["telegram_id"], "block_user" if payload.blocked else "unblock_user",
            telegram_id,
        )
        changed = True

    if payload.can_publish is not None:
        success = await admin_service.set_user_publishing(
            telegram_id, payload.can_publish
        )
        if not success:
            raise HTTPException(status_code=404, detail="User not found")
        await record_admin_action(
            user["telegram_id"],
            "allow_publishing" if payload.can_publish else "disable_publishing",
            telegram_id,
        )
        changed = True

    if not changed:
        raise HTTPException(
            status_code=400,
            detail="No user changes provided",
        )

    return {"success": True}


@app.get("/api/admin/channels")
async def api_admin_channels(user=Depends(require_admin)):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Channel).order_by(Channel.id.desc())
        )
        rows = result.scalars().all()

    return {"channels": [channel_public_dict(row) for row in rows]}


@app.post("/api/admin/channels")
async def api_admin_add_channel(
    payload: ChannelCreate,
    user=Depends(require_admin),
):
    username = payload.username.strip()
    if username.startswith("https://t.me/"):
        username = username.rstrip("/").rsplit("/", 1)[-1]
    username = username.lstrip("@")

    if not username or not payload.title.strip():
        raise HTTPException(
            status_code=400,
            detail="Title and username required",
        )

    async with SessionLocal() as session:
        existing = await session.execute(
            select(Channel).where(Channel.username == username)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail="Channel already exists",
            )

        channel = Channel(
            title=payload.title.strip(),
            username=username,
            category=payload.category.strip() or "pashto",
            active=payload.active,
        )
        session.add(channel)

        try:
            await session.commit()
            await session.refresh(channel)
            output = channel_public_dict(channel)
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status_code=409,
                detail="Channel already exists",
            )

    await record_admin_action(
        user["telegram_id"], "add_channel", channel.id
    )
    return {"success": True, "channel": output}


@app.put("/api/admin/channels/{channel_id}")
async def api_admin_update_channel(
    channel_id: int,
    payload: ChannelUpdate,
    user=Depends(require_admin),
):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Channel).where(Channel.id == channel_id)
        )
        channel = result.scalar_one_or_none()

        if channel is None:
            raise HTTPException(status_code=404, detail="Channel not found")

        values = payload.model_dump(exclude_unset=True)
        if values.get("username") is not None:
            username = values["username"].strip()
            if username.startswith("https://t.me/"):
                username = username.rstrip("/").rsplit("/", 1)[-1]
            values["username"] = username.lstrip("@")

        for key, value in values.items():
            if value is not None:
                setattr(channel, key, value)

        try:
            await session.commit()
            await session.refresh(channel)
            output = channel_public_dict(channel)
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status_code=409,
                detail="Channel username already exists",
            )

    await record_admin_action(
        user["telegram_id"], "update_channel", channel_id
    )
    return {"success": True, "channel": output}


@app.delete("/api/admin/channels/{channel_id}")
async def api_admin_delete_channel(
    channel_id: int,
    user=Depends(require_admin),
):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Channel).where(Channel.id == channel_id)
        )
        channel = result.scalar_one_or_none()

        if channel is None:
            raise HTTPException(status_code=404, detail="Channel not found")

        await session.delete(channel)
        await session.commit()

    await record_admin_action(
        user["telegram_id"], "delete_channel", channel_id
    )
    return {"success": True, "deleted_id": channel_id}


@app.get("/api/admin/settings")
async def api_admin_get_settings(user=Depends(require_admin)):
    return {
        "settings": await read_all_settings(),
        "referral_target": await settings_db.get_referral_target(),
        "main_channel": await settings_db.get_main_channel(),
        "access_channel": await settings_db.get_access_channel(),
        "auto_approve_films": await settings_db.get_auto_approve(),
    }


@app.put("/api/admin/settings")
async def api_admin_update_settings(
    payload: SettingsUpdate,
    user=Depends(require_admin),
):
    values = payload.model_dump(exclude_unset=True)
    nested = values.pop("settings", None)

    if isinstance(nested, dict):
        values.update(nested)

    allowed = {
        "referral_target",
        "main_channel",
        "access_channel",
        "auto_approve_films",
    }

    for key, value in values.items():
        if key not in allowed or value is None:
            continue

        if key == "referral_target":
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail="Referral target must be a number",
                )
            if value < 1:
                raise HTTPException(
                    status_code=400,
                    detail="Referral target must be at least 1",
                )

        elif key in ("main_channel", "access_channel"):
            value = str(value).strip()
            if value.startswith("https://t.me/"):
                value = value.rstrip("/").rsplit("/", 1)[-1]
            value = value.lstrip("@")
            if not value:
                raise HTTPException(
                    status_code=400,
                    detail=f"{key} cannot be empty",
                )

        elif key == "auto_approve_films":
            value = str(value).strip().lower() in (
                "1", "true", "yes", "on"
            )

        await save_setting(key, value)

    await record_admin_action(
        user["telegram_id"], "update_settings"
    )

    return {
        "success": True,
        "settings": await read_all_settings(),
        "referral_target": await settings_db.get_referral_target(),
        "main_channel": await settings_db.get_main_channel(),
        "access_channel": await settings_db.get_access_channel(),
        "auto_approve_films": await settings_db.get_auto_approve(),
    }


# --------------------------------------------------
# Error handling
# --------------------------------------------------

@app.exception_handler(404)
async def not_found_handler(request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"detail": "API endpoint not found"},
            status_code=404,
        )

    return JSONResponse(
        {"detail": "Page not found"},
        status_code=404,
    )
