"""SQLAlchemy ORM models and DatabaseManager."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterator, Optional, Sequence

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    and_,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from .config import DATABASE_URL
from .logger import log


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Fund(Base):
    __tablename__ = "funds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    short_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    manager: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    strategy: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_tracking: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_auto_registered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    nav_records: Mapped[list["NavHistory"]] = relationship(
        back_populates="fund", cascade="all, delete-orphan"
    )


class NavHistory(Base):
    __tablename__ = "nav_history"
    __table_args__ = (UniqueConstraint("fund_id", "nav_date", name="uq_nav_fund_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_id: Mapped[int] = mapped_column(ForeignKey("funds.id"), nullable=False, index=True)
    nav_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    unit_nav: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    cumulative_nav: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    source_email_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    source_filename: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    raw_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    fund: Mapped[Fund] = relationship(back_populates="nav_records")


class EmailLog(Base):
    __tablename__ = "email_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_uid: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sender: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    records_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    raw_snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class DatabaseManager:
    """Thin wrapper over SQLAlchemy. Holds engine + session factory."""

    def __init__(self, database_url: str = DATABASE_URL) -> None:
        self.database_url = database_url
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, future=True, connect_args=connect_args)
        self._Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._migrate()

    # ---------- lifecycle ----------
    def init_db(self) -> None:
        Base.metadata.create_all(self.engine)
        self._migrate()
        log.info(f"Database initialized at {self.database_url}")

    def _migrate(self) -> None:
        """Lightweight in-place migrations for existing DBs.

        Currently: add nav_history.cumulative_nav if absent.
        """
        from sqlalchemy import text, inspect

        insp = inspect(self.engine)
        if "nav_history" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("nav_history")}
        if "cumulative_nav" not in cols:
            log.info("Migrating: ALTER TABLE nav_history ADD COLUMN cumulative_nav")
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE nav_history ADD COLUMN cumulative_nav NUMERIC(10, 4)"))

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ---------- funds ----------
    def add_fund(
        self,
        fund_name: str,
        manager: Optional[str] = None,
        strategy: Optional[str] = None,
        category: Optional[str] = None,
        short_name: Optional[str] = None,
        is_tracking: bool = True,
        is_auto_registered: bool = False,
    ) -> int:
        with self.session() as s:
            existing = s.scalar(select(Fund).where(Fund.fund_name == fund_name))
            if existing:
                # Update non-null fields.
                if manager is not None:
                    existing.manager = manager
                if strategy is not None:
                    existing.strategy = strategy
                if category is not None:
                    existing.category = category
                if short_name is not None:
                    existing.short_name = short_name
                existing.is_tracking = is_tracking
                existing.updated_at = _utcnow()
                s.flush()
                return existing.id
            fund = Fund(
                fund_name=fund_name,
                manager=manager,
                strategy=strategy,
                category=category,
                short_name=short_name,
                is_tracking=is_tracking,
                is_auto_registered=is_auto_registered,
            )
            s.add(fund)
            s.flush()
            return fund.id

    def update_fund(self, fund_name: str, **fields: Any) -> bool:
        with self.session() as s:
            fund = s.scalar(select(Fund).where(Fund.fund_name == fund_name))
            if not fund:
                return False
            allowed = {"manager", "strategy", "category", "short_name", "is_tracking"}
            for k, v in fields.items():
                if k in allowed and v is not None:
                    setattr(fund, k, v)
            fund.updated_at = _utcnow()
            return True

    def get_fund_by_name(self, fund_name: str) -> Optional[dict[str, Any]]:
        with self.session() as s:
            fund = s.scalar(select(Fund).where(Fund.fund_name == fund_name))
            return _fund_to_dict(fund) if fund else None

    def get_all_tracking_funds(self) -> list[dict[str, Any]]:
        with self.session() as s:
            rows = s.scalars(select(Fund).where(Fund.is_tracking == True).order_by(Fund.id)).all()
            return [_fund_to_dict(r) for r in rows]

    def get_all_funds(self) -> list[dict[str, Any]]:
        with self.session() as s:
            rows = s.scalars(select(Fund).order_by(Fund.id)).all()
            return [_fund_to_dict(r) for r in rows]

    # ---------- nav ----------
    def upsert_nav(
        self,
        fund_id: int,
        nav_date: date,
        unit_nav: Decimal | float | str,
        cumulative_nav: Optional[Decimal | float | str] = None,
        source_email_id: Optional[str] = None,
        source_filename: Optional[str] = None,
        raw_data: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, bool]:
        """Insert or update a NAV row. Returns (inserted, updated).

        cumulative_nav is optional; when provided alongside an existing row, it
        is filled in if missing or refreshed if changed. updated=True only
        flags meaningful unit_nav changes to keep stats clean.
        """
        unit_nav = Decimal(str(unit_nav)).quantize(Decimal("0.0001"))
        cum = (
            Decimal(str(cumulative_nav)).quantize(Decimal("0.0001"))
            if cumulative_nav is not None
            else None
        )
        with self.session() as s:
            existing = s.scalar(
                select(NavHistory).where(
                    and_(NavHistory.fund_id == fund_id, NavHistory.nav_date == nav_date)
                )
            )
            raw_json = json.dumps(raw_data, ensure_ascii=False, default=str) if raw_data else None
            if existing:
                touched = False
                if existing.unit_nav != unit_nav:
                    existing.unit_nav = unit_nav
                    touched = True
                if cum is not None and existing.cumulative_nav != cum:
                    existing.cumulative_nav = cum
                    touched = True
                if touched:
                    existing.source_email_id = source_email_id or existing.source_email_id
                    existing.source_filename = source_filename or existing.source_filename
                    if raw_json:
                        existing.raw_data = raw_json
                    return (False, True)
                return (False, False)
            row = NavHistory(
                fund_id=fund_id,
                nav_date=nav_date,
                unit_nav=unit_nav,
                cumulative_nav=cum,
                source_email_id=source_email_id,
                source_filename=source_filename,
                raw_data=raw_json,
            )
            s.add(row)
            return (True, False)

    def get_nav_history(
        self,
        fund_name: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> list[dict[str, Any]]:
        with self.session() as s:
            stmt = select(NavHistory, Fund).join(Fund, NavHistory.fund_id == Fund.id)
            if fund_name:
                stmt = stmt.where(Fund.fund_name.like(f"%{fund_name}%"))
            if start_date:
                stmt = stmt.where(NavHistory.nav_date >= start_date)
            if end_date:
                stmt = stmt.where(NavHistory.nav_date <= end_date)
            stmt = stmt.order_by(Fund.fund_name, NavHistory.nav_date)
            return [
                {
                    "fund_name": fund.fund_name,
                    "manager": fund.manager,
                    "strategy": fund.strategy,
                    "category": fund.category,
                    "nav_date": nav.nav_date,
                    "unit_nav": float(nav.unit_nav),
                    "cumulative_nav": float(nav.cumulative_nav) if nav.cumulative_nav is not None else None,
                    "source_email_id": nav.source_email_id,
                    "source_filename": nav.source_filename,
                }
                for nav, fund in s.execute(stmt).all()
            ]

    def get_latest_nav(self) -> list[dict[str, Any]]:
        with self.session() as s:
            sub = (
                select(NavHistory.fund_id, func.max(NavHistory.nav_date).label("max_date"))
                .group_by(NavHistory.fund_id)
                .subquery()
            )
            stmt = (
                select(Fund, NavHistory)
                .join(NavHistory, NavHistory.fund_id == Fund.id)
                .join(
                    sub,
                    and_(NavHistory.fund_id == sub.c.fund_id, NavHistory.nav_date == sub.c.max_date),
                )
                .order_by(Fund.fund_name)
            )
            return [
                {
                    "fund_name": fund.fund_name,
                    "manager": fund.manager,
                    "strategy": fund.strategy,
                    "category": fund.category,
                    "nav_date": nav.nav_date,
                    "unit_nav": float(nav.unit_nav),
                    "cumulative_nav": float(nav.cumulative_nav) if nav.cumulative_nav is not None else None,
                }
                for fund, nav in s.execute(stmt).all()
            ]

    # ---------- email_log ----------
    def log_email(
        self,
        email_uid: str,
        subject: Optional[str] = None,
        sender: Optional[str] = None,
        received_at: Optional[datetime] = None,
        status: str = "pending",
        error_message: Optional[str] = None,
        records_count: int = 0,
        raw_snapshot: Optional[str] = None,
    ) -> None:
        with self.session() as s:
            existing = s.scalar(select(EmailLog).where(EmailLog.email_uid == email_uid))
            if existing:
                existing.status = status
                existing.error_message = error_message
                existing.records_count = records_count
                existing.processed_at = _utcnow()
                if subject is not None:
                    existing.subject = subject
                if sender is not None:
                    existing.sender = sender
                if received_at is not None:
                    existing.received_at = received_at
                if raw_snapshot is not None:
                    existing.raw_snapshot = raw_snapshot
                return
            row = EmailLog(
                email_uid=email_uid,
                subject=subject,
                sender=sender,
                received_at=received_at,
                status=status,
                error_message=error_message,
                records_count=records_count,
                raw_snapshot=raw_snapshot,
            )
            s.add(row)

    def is_email_processed(self, email_uid: str) -> bool:
        with self.session() as s:
            row = s.scalar(select(EmailLog).where(EmailLog.email_uid == email_uid))
            return bool(row and row.status in ("success", "ignored", "no_data"))

    def get_failed_emails(self) -> list[dict[str, Any]]:
        with self.session() as s:
            rows = s.scalars(
                select(EmailLog).where(EmailLog.status == "failed").order_by(EmailLog.processed_at.desc())
            ).all()
            return [
                {
                    "id": r.id,
                    "email_uid": r.email_uid,
                    "subject": r.subject,
                    "sender": r.sender,
                    "received_at": r.received_at,
                    "processed_at": r.processed_at,
                    "error_message": r.error_message,
                    "raw_snapshot": r.raw_snapshot,
                }
                for r in rows
            ]

    def get_status_summary(self) -> dict[str, Any]:
        with self.session() as s:
            fund_total = s.scalar(select(func.count(Fund.id))) or 0
            tracking_total = s.scalar(select(func.count(Fund.id)).where(Fund.is_tracking == True)) or 0
            nav_total = s.scalar(select(func.count(NavHistory.id))) or 0
            email_total = s.scalar(select(func.count(EmailLog.id))) or 0
            failed = s.scalar(select(func.count(EmailLog.id)).where(EmailLog.status == "failed")) or 0
            last_email = s.scalar(
                select(EmailLog).order_by(EmailLog.processed_at.desc()).limit(1)
            )
            return {
                "funds_total": fund_total,
                "funds_tracking": tracking_total,
                "nav_records": nav_total,
                "emails_processed": email_total,
                "emails_failed": failed,
                "last_email_subject": last_email.subject if last_email else None,
                "last_email_processed_at": last_email.processed_at if last_email else None,
            }


def _fund_to_dict(fund: Fund) -> dict[str, Any]:
    return {
        "id": fund.id,
        "fund_name": fund.fund_name,
        "short_name": fund.short_name,
        "manager": fund.manager,
        "strategy": fund.strategy,
        "category": fund.category,
        "is_tracking": fund.is_tracking,
        "is_auto_registered": fund.is_auto_registered,
        "created_at": fund.created_at,
        "updated_at": fund.updated_at,
    }
