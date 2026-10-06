import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qsl

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.bot import start_bot
from app.config import settings
from app.database import SessionLocal, init_db
from app.models import Film, User
from app.films import (
    get_category_films,
    get_film,
    get_official_films,
    search_films,
)
from app.referrals import (
    create_referral_link,
    get_referral_count,
    get_referral_leaders,
    get_user,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="ALL PRODUCTION FILMS",
    version="1.0.0",
)

app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static",
)


# ---------------------------------------------------------
# TELEGRAM MINI APP SECURITY
# ---------------------------------------------------------

def validate_telegram_init_data(
    init_data: str,
) -> dict:

    if not init_data:
        raise HTTPException(
            status_code=401,
            detail="Telegram authentication required",
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

        auth_date = parsed.get(
            "auth_date"
        )

        if not received_hash or not auth_date:
            raise ValueError(
                "Invalid Telegram data"
            )

        if (
            time.time()
            - int(auth_date)
            > 86400
        ):
            raise ValueError(
                "Telegram data expired"
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
            raise ValueError(
                "Invalid Telegram signature"
            )

        return parsed

    except Exception as exc:
        logger.warning(
            "Telegram initData validation failed: %s",
            exc,
        )

        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram authentication",
        )


def telegram_user_id(
    init_data: str | None,
) -> int:

    if not init_data:
        raise HTTPException(
            status_code=401,
            detail="Telegram authentication required",
        )

    data = validate_telegram_init_data(
        init_data
    )

    user_raw = data.get("user")

    if not user_raw:
        raise HTTPException(
            status_code=401,
            detail="Telegram user missing",
        )

    user = json.loads(user_raw)

    return int(user["id"])


# ---------------------------------------------------------
# STARTUP
# ---------------------------------------------------------

@app.on_event("startup")
async def startup():

    await init_db()

    logger.info(
        "Database initialized"
    )


# ---------------------------------------------------------
# WEB
# ---------------------------------------------------------

@app.get("/")
async def home():

    return FileResponse(
        "static/index.html"
    )


@app.get("/health")
async def health():

    return JSONResponse(
        {
            "status": "ok",
            "project": "ALL PRODUCTION FILMS",
            "version": "1.0.0",
        }
    )


@app.get("/api")
async def api_info():

    return {
        "project": "ALL PRODUCTION FILMS",
        "status": "online",
        "version": "1.0.0",
    }


# ---------------------------------------------------------
# FILMS
# ---------------------------------------------------------

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
        "poster_file_id": film.poster_file_id,
        "official": film.official,
    }


@app.get("/api/films/latest")
async def latest_films(
    init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    telegram_user_id(init_data)

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


@app.get("/api/films/search")
async def api_search_films(
    q: str = Query(
        min_length=2,
        max_length=200,
    ),
    init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    telegram_user_id(init_data)

    films = await search_films(q)

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
    init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    telegram_user_id(init_data)

    films = await get_category_films(
        category,
        50,
    )

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


@app.get("/api/films/official")
async def api_official_films(
    init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    telegram_user_id(init_data)

    films = await get_official_films(50)

    return {
        "films": [
            film_to_dict(film)
            for film in films
        ]
    }


@app.get("/api/films/{film_id}")
async def api_get_film(
    film_id: int,
    init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    telegram_user_id(init_data)

    film = await get_film(film_id)

    if not film:
        raise HTTPException(
            status_code=404,
            detail="Film not found",
        )

    return {
        "film": film_to_dict(film)
    }


@app.get(
    "/api/films/{film_id}/download"
)
async def api_download_film(
    film_id: int,
    init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    telegram_user_id(init_data)

    film = await get_film(film_id)

    if not film:
        raise HTTPException(
            status_code=404,
            detail="Film not found",
        )

    return {
        "telegram_url": (
            f"https://t.me/"
            f"{settings.BOT_USERNAME}"
            f"?start=film_{film.id}"
        )
    }


# ---------------------------------------------------------
# REFERRAL API
# ---------------------------------------------------------

@app.get("/api/referral")
async def api_referral(
    init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    user_id = telegram_user_id(
        init_data
    )

    count = await get_referral_count(
        user_id
    )

    link = await create_referral_link(
        __import__(
            "app.bot",
            fromlist=["bot"]
        ).bot,
        user_id,
    )

    remaining = max(
        settings.REFERRAL_TARGET - count,
        0,
    )

    if remaining == 0:
        status = (
            "✅ تاسو د فلم نشرولو اجازه لرئ."
        )
    else:
        status = (
            f"⏳ لا {remaining} کسان "
            "راوستل پکار دي."
        )

    return {
        "count": count,
        "target": settings.REFERRAL_TARGET,
        "remaining": remaining,
        "link": link,
        "status": status,
    }


@app.get("/api/referral/leaders")
async def api_referral_leaders(
    init_data: str | None = Header(
        default=None,
        alias="X-Telegram-Init-Data",
    ),
):

    telegram_user_id(init_data)

    leaders = await get_referral_leaders(
        20
    )

    return {
        "leaders": [
            {
                "name": user.first_name
                or user.username
                or "User",
                "count": user.referral_count,
            }
            for user in leaders
        ]
    }


# ---------------------------------------------------------
# RUN BOT
# ---------------------------------------------------------

@app.get("/bot-status")
async def bot_status():

    return {
        "bot": settings.BOT_USERNAME,
        "status": "configured",
    }
