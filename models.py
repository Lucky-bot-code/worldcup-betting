import sys
# Fallback for Python builds without _sqlite3 C extension
try:
    import sqlite3  # noqa
except ModuleNotFoundError:
    import pysqlite3  # noqa
    sys.modules["sqlite3"] = pysqlite3

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship, Session
from config import DATABASE_URL


class Base(DeclarativeBase):
    pass


# ─── 手动投注系统模型 ──────────────────────────────────────────

class ManualMatch(Base):
    __tablename__ = "manual_matches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_day = Column(String, nullable=False)
    stage = Column(String, nullable=False, default="小组赛")
    team_home = Column(String, nullable=False)
    team_away = Column(String, nullable=False)
    score_home = Column(Integer, nullable=True)
    score_away = Column(Integer, nullable=True)
    odds_home = Column(Float, nullable=True)
    odds_draw = Column(Float, nullable=True)
    odds_away = Column(Float, nullable=True)
    goals_odds_json = Column(String, nullable=True)
    score_odds_json = Column(String, nullable=True)
    is_settled = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    bets = relationship("ManualBet", back_populates="match", cascade="all, delete-orphan")


class ManualBet(Base):
    __tablename__ = "manual_bets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("manual_matches.id", ondelete="CASCADE"), nullable=False)
    odds_type = Column(String, nullable=False)
    bet_direction = Column(String, nullable=False)
    stake = Column(Float, nullable=False)
    odds_taken = Column(Float, nullable=False)
    threshold = Column(Float, nullable=True)
    result = Column(String, nullable=False, default="待定")
    profit = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    match = relationship("ManualMatch", back_populates="bets")


class ManualBankroll(Base):
    __tablename__ = "manual_bankroll"

    id = Column(Integer, primary_key=True, autoincrement=True)
    initial_bankroll = Column(Float, nullable=False, default=3000.0)
    current_bankroll = Column(Float, nullable=False, default=3000.0)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


engine = create_engine(DATABASE_URL, echo=False)


def init_db():
    Base.metadata.create_all(engine)
    _migrate_db()


def _migrate_db():
    """为已有数据库添加新列"""
    if "sqlite" not in str(engine.url):
        return
    try:
        db_path = str(engine.url).replace("sqlite:///", "")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(manual_matches)")
        cols = {row[1] for row in cur.fetchall()}
        if "goals_odds_json" not in cols:
            cur.execute("ALTER TABLE manual_matches ADD COLUMN goals_odds_json TEXT")
        if "score_odds_json" not in cols:
            cur.execute("ALTER TABLE manual_matches ADD COLUMN score_odds_json TEXT")
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_session():
    return Session(engine)
