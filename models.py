import sys
# Fallback for Python builds without _sqlite3 C extension
try:
    import sqlite3  # noqa
except ModuleNotFoundError:
    import pysqlite3  # noqa
    sys.modules["sqlite3"] = pysqlite3

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, create_engine, text,
)
from sqlalchemy.orm import DeclarativeBase, relationship, Session
from config import DATABASE_URL


class Base(DeclarativeBase):
    pass


class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    year = Column(Integer, nullable=False, index=True)
    stage = Column(String, nullable=False, default="小组赛")
    team_home = Column(String, nullable=False)
    team_away = Column(String, nullable=False)
    score_home = Column(Integer, nullable=True)
    score_away = Column(Integer, nullable=True)

    odds = relationship("Odds", back_populates="match", cascade="all, delete-orphan")
    bets = relationship("Bet", back_populates="match", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Match({self.year}) {self.team_home} vs {self.team_away}>"


class Odds(Base):
    __tablename__ = "odds"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String, nullable=False, default="未知")
    odds_home = Column(Float, nullable=False)
    odds_draw = Column(Float, nullable=False)
    odds_away = Column(Float, nullable=False)

    match = relationship("Match", back_populates="odds")

    def __repr__(self):
        return f"<Odds {self.provider}: {self.odds_home}/{self.odds_draw}/{self.odds_away}>"


class Bet(Base):
    __tablename__ = "bets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    run_id = Column(Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=True)
    strategy_name = Column(String, nullable=False)
    bet_on = Column(String, nullable=False)  # home / draw / away
    stake = Column(Float, nullable=False)
    odds_taken = Column(Float, nullable=False)
    result = Column(String, nullable=False, default="待定")  # win / lose / push / 待定
    profit = Column(Float, nullable=False, default=0.0)

    match = relationship("Match", back_populates="bets")
    run = relationship("BacktestRun", back_populates="bets")

    def __repr__(self):
        return f"<Bet {self.strategy_name} {self.bet_on} ¥{self.stake} → {self.result}>"


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_name = Column(String, nullable=False)
    start_year = Column(Integer, nullable=False)
    end_year = Column(Integer, nullable=False)
    initial_bankroll = Column(Float, nullable=False)
    final_bankroll = Column(Float, nullable=False)
    total_bets = Column(Integer, nullable=False, default=0)
    wins = Column(Integer, nullable=False, default=0)
    pushes = Column(Integer, nullable=False, default=0)
    win_rate = Column(Float, nullable=False, default=0.0)
    max_drawdown = Column(Float, nullable=False, default=0.0)
    stage = Column(String, nullable=False, default="全部")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    bets = relationship("Bet", back_populates="run", cascade="all, delete-orphan")

    @property
    def total_return(self):
        return (self.final_bankroll - self.initial_bankroll) / self.initial_bankroll

    def __repr__(self):
        return f"<Run #{self.id} {self.strategy_name} 回报:{self.total_return:.1%}>"


# ─── 手动投注系统模型 ──────────────────────────────────────────

class ManualMatch(Base):
    __tablename__ = "manual_matches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_day = Column(String, nullable=False)            # 比赛日期 "2026-06-14"
    stage = Column(String, nullable=False, default="小组赛")
    team_home = Column(String, nullable=False)
    team_away = Column(String, nullable=False)
    score_home = Column(Integer, nullable=True)
    score_away = Column(Integer, nullable=True)
    odds_home = Column(Float, nullable=True)              # 赛前主胜赔率
    odds_draw = Column(Float, nullable=True)              # 赛前平局赔率
    odds_away = Column(Float, nullable=True)              # 赛前客胜赔率
    is_settled = Column(Integer, nullable=False, default=0)  # 0=未结算 1=已结算
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    bets = relationship("ManualBet", back_populates="match", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<ManualMatch {self.match_day} {self.team_home} vs {self.team_away}>"


class ManualBet(Base):
    __tablename__ = "manual_bets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("manual_matches.id", ondelete="CASCADE"), nullable=False)
    odds_type = Column(String, nullable=False)             # "spf" / "over_under" / "score"
    bet_direction = Column(String, nullable=False)          # "home"/"draw"/"away" / "over"/"under" / "2-1"等
    stake = Column(Float, nullable=False)
    odds_taken = Column(Float, nullable=False)
    threshold = Column(Float, nullable=True)                # 仅 over_under 类型
    result = Column(String, nullable=False, default="待定")  # "待定" / "win" / "lose" / "push"
    profit = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    match = relationship("ManualMatch", back_populates="bets")

    def __repr__(self):
        return f"<ManualBet {self.odds_type} {self.bet_direction} ¥{self.stake} → {self.result}>"


class ManualBankroll(Base):
    __tablename__ = "manual_bankroll"

    id = Column(Integer, primary_key=True, autoincrement=True)
    initial_bankroll = Column(Float, nullable=False, default=10000.0)
    current_bankroll = Column(Float, nullable=False, default=10000.0)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


engine = create_engine(DATABASE_URL, echo=False)


def init_db():
    Base.metadata.create_all(engine)


def get_session():
    return Session(engine)
