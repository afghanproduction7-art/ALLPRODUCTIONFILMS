import io
import re

import imagehash
from PIL import Image
from sqlalchemy import or_, select

from app.database import SessionLocal
from app.models import Film


def normalize_title(title: str) -> str:
    title = title.lower().strip()

    title = re.sub(
        r"[^\w\s\u0600-\u06ff]",
        " ",
        title,
    )

    title = re.sub(
        r"\s+",
        " ",
        title,
    )

    return title.strip()


async def create_film(
    title: str,
    video_file_id: str,
    uploader_id: int,
    year: str | None = None,
    quality: str | None = None,
    genre: str | None = None,
    language: str | None = None,
    description: str | None = None,
    category: str = "general",
    poster_file_id: str | None = None,
    poster_file_unique_id: str | None = None,
    poster_hash: str | None = None,
    approved: bool = True,
    official: bool = False,
    video_file_unique_id: str | None = None,
):
    async with SessionLocal() as session:
        film = Film(
            title=title.strip(),
            normalized_title=normalize_title(title),
            video_file_id=video_file_id,
            video_file_unique_id=video_file_unique_id,
            uploader_id=uploader_id,
            year=year,
            quality=quality,
            genre=genre,
            language=language,
            description=description,
            category=category,
            poster_file_id=poster_file_id,
            poster_file_unique_id=poster_file_unique_id,
            poster_hash=poster_hash,
            approved=approved,
            official=official,
        )

        session.add(film)

        await session.commit()
        await session.refresh(film)

        return film


async def get_film(
    film_id: int,
    include_unapproved: bool = False,
):
    async with SessionLocal() as session:

        query = select(Film).where(
            Film.id == film_id
        )

        if not include_unapproved:
            query = query.where(
                Film.approved.is_(True)
            )

        result = await session.execute(query)

        return result.scalar_one_or_none()


async def search_films(
    query: str,
    limit: int = 30,
):
    """
    Search approved films by title.

    Empty query returns latest approved films.
    """

    limit = max(1, min(int(limit), 100))

    normalized = normalize_title(query or "")

    async with SessionLocal() as session:

        base_query = select(Film).where(
            Film.approved.is_(True)
        )

        if not normalized:
            result = await session.execute(
                base_query
                .order_by(Film.created_at.desc())
                .limit(limit)
            )

            return result.scalars().all()

        words = normalized.split()

        conditions = []

        for word in words:
            conditions.append(
                Film.normalized_title.ilike(
                    f"%{word}%"
                )
            )

        result = await session.execute(
            base_query
            .where(or_(*conditions))
            .order_by(Film.created_at.desc())
            .limit(limit)
        )

        return result.scalars().all()


async def get_latest_films(limit: int = 30):
    limit = max(1, min(int(limit), 100))

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

        return result.scalars().all()


async def get_category_films(
    category: str,
    limit: int = 50,
):
    limit = max(1, min(int(limit), 100))

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film)
            .where(
                Film.approved.is_(True),
                Film.category == category,
            )
            .order_by(
                Film.created_at.desc()
            )
            .limit(limit)
        )

        return result.scalars().all()


async def get_official_films(
    limit: int = 50,
):
    limit = max(1, min(int(limit), 100))

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film)
            .where(
                Film.approved.is_(True),
                Film.official.is_(True),
            )
            .order_by(
                Film.created_at.desc()
            )
            .limit(limit)
        )

        return result.scalars().all()


def calculate_image_hash(
    image_bytes: bytes,
) -> str:
    image = Image.open(
        io.BytesIO(image_bytes)
    ).convert("RGB")

    return str(
        imagehash.phash(image)
    )


async def search_by_image_hash(
    image_hash: str,
    max_distance: int = 12,
    limit: int = 20,
):
    limit = max(1, min(int(limit), 100))

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film).where(
                Film.approved.is_(True),
                Film.poster_hash.is_not(None),
            )
        )

        films = result.scalars().all()

    try:
        target = imagehash.hex_to_hash(
            image_hash
        )
    except Exception:
        return []

    matches = []

    for film in films:
        if not film.poster_hash:
            continue

        try:
            stored = imagehash.hex_to_hash(
                film.poster_hash
            )

            distance = target - stored

            if distance <= max_distance:
                matches.append(
                    (distance, film)
                )

        except Exception:
            continue

    matches.sort(
        key=lambda item: item[0]
    )

    return [
        film
        for _, film in matches[:limit]
    ]


async def get_pending_films(
    limit: int = 100,
):
    limit = max(1, min(int(limit), 200))

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film)
            .where(
                Film.approved.is_(False)
            )
            .order_by(
                Film.created_at.desc()
            )
            .limit(limit)
        )

        return result.scalars().all()


async def update_film(
    film_id: int,
    title: str | None = None,
    year: str | None = None,
    quality: str | None = None,
    genre: str | None = None,
    language: str | None = None,
    description: str | None = None,
    category: str | None = None,
    official: bool | None = None,
):
    async with SessionLocal() as session:

        result = await session.execute(
            select(Film).where(
                Film.id == film_id
            )
        )

        film = result.scalar_one_or_none()

        if film is None:
            return None

        if title is not None:
            film.title = title.strip()
            film.normalized_title = (
                normalize_title(title)
            )

        if year is not None:
            film.year = year

        if quality is not None:
            film.quality = quality

        if genre is not None:
            film.genre = genre

        if language is not None:
            film.language = language

        if description is not None:
            film.description = description

        if category is not None:
            film.category = category

        if official is not None:
            film.official = official

        await session.commit()
        await session.refresh(film)

        return film


async def delete_film(
    film_id: int,
):
    async with SessionLocal() as session:

        result = await session.execute(
            select(Film).where(
                Film.id == film_id
            )
        )

        film = result.scalar_one_or_none()

        if film is None:
            return False

        await session.delete(film)
        await session.commit()

        return True


async def approve_film(
    film_id: int,
):
    async with SessionLocal() as session:

        result = await session.execute(
            select(Film).where(
                Film.id == film_id
            )
        )

        film = result.scalar_one_or_none()

        if film is None:
            return False

        film.approved = True

        await session.commit()

        return True


async def reject_film(
    film_id: int,
):
    async with SessionLocal() as session:

        result = await session.execute(
            select(Film).where(
                Film.id == film_id
            )
        )

        film = result.scalar_one_or_none()

        if film is None:
            return False

        film.approved = False

        await session.commit()

        return True
