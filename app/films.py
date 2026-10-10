import io
import re

import imagehash
from PIL import Image, UnidentifiedImageError
from sqlalchemy import or_, select

from app.database import SessionLocal
from app.models import Film


def normalize_title(title: str) -> str:
    """Normalize a film title for Pashto, Dari, Arabic, and English search."""
    title = (title or "").lower().strip()
    title = re.sub(r"[^\w\s\u0600-\u06ff]", " ", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip()


def _safe_limit(limit: int, maximum: int = 100) -> int:
    try:
        return max(1, min(int(limit), maximum))
    except (TypeError, ValueError):
        return 30


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
    """Create and save a film without deleting or resetting existing records."""
    title = (title or "").strip()
    video_file_id = (video_file_id or "").strip()

    if not title:
        raise ValueError("Film title cannot be empty.")

    if not video_file_id:
        raise ValueError("Video file ID cannot be empty.")

    async with SessionLocal() as session:
        film = Film(
            title=title,
            normalized_title=normalize_title(title),
            video_file_id=video_file_id,
            video_file_unique_id=video_file_unique_id,
            uploader_id=int(uploader_id),
            year=year,
            quality=quality,
            genre=genre,
            language=language,
            description=description,
            category=(category or "general").strip(),
            poster_file_id=poster_file_id,
            poster_file_unique_id=poster_file_unique_id,
            poster_hash=poster_hash,
            approved=bool(approved),
            official=bool(official),
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
        query = select(Film).where(Film.id == int(film_id))

        if not include_unapproved:
            query = query.where(Film.approved.is_(True))

        result = await session.execute(query)
        return result.scalar_one_or_none()


async def search_films(query: str, limit: int = 30):
    """
    Search approved films by title.
    An empty query returns the latest approved films.
    """
    limit = _safe_limit(limit)
    normalized = normalize_title(query)

    async with SessionLocal() as session:
        statement = select(Film).where(Film.approved.is_(True))

        if normalized:
            words = normalized.split()
            conditions = [
                Film.normalized_title.ilike(f"%{word}%")
                for word in words
            ]
            statement = statement.where(or_(*conditions))

        statement = statement.order_by(
            Film.created_at.desc(),
            Film.id.desc(),
        ).limit(limit)

        result = await session.execute(statement)
        return result.scalars().all()


async def get_latest_films(limit: int = 30):
    limit = _safe_limit(limit)

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film)
            .where(Film.approved.is_(True))
            .order_by(Film.created_at.desc(), Film.id.desc())
            .limit(limit)
        )
        return result.scalars().all()


async def get_category_films(category: str, limit: int = 50):
    limit = _safe_limit(limit)
    category = (category or "").strip()

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film)
            .where(
                Film.approved.is_(True),
                Film.category == category,
            )
            .order_by(Film.created_at.desc(), Film.id.desc())
            .limit(limit)
        )
        return result.scalars().all()


async def get_official_films(limit: int = 50):
    limit = _safe_limit(limit)

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film)
            .where(
                Film.approved.is_(True),
                Film.official.is_(True),
            )
            .order_by(Film.created_at.desc(), Film.id.desc())
            .limit(limit)
        )
        return result.scalars().all()


def calculate_image_hash(image_bytes: bytes) -> str:
    """Return a perceptual hash for a valid image."""
    if not image_bytes:
        raise ValueError("Image data is empty.")

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()
            image = image.convert("RGB")
            return str(imagehash.phash(image))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("The uploaded file is not a valid image.") from exc


async def search_by_image_hash(
    image_hash: str,
    max_distance: int = 12,
    limit: int = 20,
):
    """Find approved films with visually similar posters."""
    limit = _safe_limit(limit)

    try:
        target = imagehash.hex_to_hash((image_hash or "").strip())
    except (TypeError, ValueError):
        return []

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film).where(
                Film.approved.is_(True),
                Film.poster_hash.is_not(None),
            )
        )
        films = result.scalars().all()

    matches = []

    for film in films:
        if not film.poster_hash:
            continue

        try:
            stored = imagehash.hex_to_hash(film.poster_hash)
            distance = target - stored

            if distance <= max(0, int(max_distance)):
                matches.append((distance, film))
        except (TypeError, ValueError):
            continue

    matches.sort(key=lambda item: (item[0], item[1].id))
    return [film for _, film in matches[:limit]]


async def get_pending_films(limit: int = 100):
    limit = _safe_limit(limit, maximum=200)

    async with SessionLocal() as session:
        result = await session.execute(
            select(Film)
            .where(Film.approved.is_(False))
            .order_by(Film.created_at.desc(), Film.id.desc())
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
    """Update supplied film fields; leave unspecified fields unchanged."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(Film).where(Film.id == int(film_id))
        )
        film = result.scalar_one_or_none()

        if film is None:
            return None

        if title is not None:
            title = title.strip()
            if not title:
                raise ValueError("Film title cannot be empty.")
            film.title = title
            film.normalized_title = normalize_title(title)

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
            film.category = category.strip() or "general"

        if official is not None:
            film.official = bool(official)

        await session.commit()
        await session.refresh(film)
        return film


async def delete_film(film_id: int) -> bool:
    """Delete one film record only."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(Film).where(Film.id == int(film_id))
        )
        film = result.scalar_one_or_none()

        if film is None:
            return False

        await session.delete(film)
        await session.commit()
        return True


async def approve_film(film_id: int) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Film).where(Film.id == int(film_id))
        )
        film = result.scalar_one_or_none()

        if film is None:
            return False

        film.approved = True
        await session.commit()
        return True


async def reject_film(film_id: int) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Film).where(Film.id == int(film_id))
        )
        film = result.scalar_one_or_none()

        if film is None:
            return False

        film.approved = False
        await session.commit()
        return True
