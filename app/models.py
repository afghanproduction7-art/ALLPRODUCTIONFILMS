from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        index=True,
    )

    username: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    first_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    referral_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    can_publish: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    referral_link: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    is_blocked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class Referral(Base):
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    inviter_id: Mapped[int] = mapped_column(
        BigInteger,
        index=True,
        nullable=False,
    )

    invited_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        index=True,
        nullable=False,
    )

    invite_link: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    joined_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "inviter_id",
            "invited_id",
            name="uq_referral_pair",
        ),
    )


class Film(Base):
    __tablename__ = "films"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    title: Mapped[str] = mapped_column(
        String(500),
        index=True,
        nullable=False,
    )

    normalized_title: Mapped[str] = mapped_column(
        String(500),
        index=True,
        nullable=False,
    )

    year: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    quality: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    genre: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    language: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    category: Mapped[str] = mapped_column(
        String(100),
        default="general",
        index=True,
        nullable=False,
    )

    video_file_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    video_file_unique_id: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    poster_file_id: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    poster_file_unique_id: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    poster_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )

    uploader_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    approved: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        index=True,
        nullable=False,
    )

    official: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        index=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    username: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )

    category: Mapped[str] = mapped_column(
        String(100),
        default="pashto",
        index=True,
        nullable=False,
    )

    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    key: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    value: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )


class AdminAction(Base):
    __tablename__ = "admin_actions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    admin_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    action: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    target_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
