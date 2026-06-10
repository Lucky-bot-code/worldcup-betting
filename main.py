from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
import os

from models import init_db, get_session, Match, Odds, Bet, BacktestRun
from models import ManualMatch, ManualBet, ManualBankroll
from config import DEFAULT_BANKROLL, DEFAULT_STAKE
from scraper.worldcup import scrape_all
from backtest import run_backtest, BacktestResult
from smart_betting import get_recommendations, get_context_info
from sqlalchemy import func, desc


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # 初始化手动投注资金记录（单行）
    session = get_session()
    try:
        if session.query(ManualBankroll).count() == 0:
            session.add(ManualBankroll(initial_bankroll=10000.0, current_bankroll=10000.0))
            session.commit()
    finally:
        session.close()
    yield


app = FastAPI(title="世界杯彩票盈利系统", lifespan=lifespan)


# ─── API Schemas ──────────────────────────────────────────────

STAGE_GROUPS = {
    "全部": None,
    "小组赛": ["小组赛"],
    "淘汰赛": ["16强", "8强", "半决赛", "季军赛", "决赛"],
}


class BacktestRequest(BaseModel):
    strategy_name: str
    start_year: int
    end_year: int
    stage: str = "全部"
    initial_bankroll: float = DEFAULT_BANKROLL
    stake_per_bet: float = DEFAULT_STAKE


class ScrapeRequest(BaseModel):
    years: Optional[list[int]] = None


# ─── 数据接口 ──────────────────────────────────────────────────

@app.get("/api/matches")
def get_matches(
    year: Optional[int] = Query(None),
    stage: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    session = get_session()
    try:
        q = session.query(Match)
        if year:
            q = q.filter(Match.year == year)
        if stage:
            q = q.filter(Match.stage == stage)
        total = q.count()
        matches = (
            q.order_by(Match.year.desc(), Match.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "data": [
                {
                    "id": m.id,
                    "year": m.year,
                    "stage": m.stage,
                    "team_home": m.team_home,
                    "team_away": m.team_away,
                    "score_home": m.score_home,
                    "score_away": m.score_away,
                }
                for m in matches
            ],
        }
    finally:
        session.close()


@app.get("/api/matches/count")
def get_match_count():
    session = get_session()
    try:
        total = session.query(Match).count()
        years = sorted(
            [r[0] for r in session.query(Match.year).distinct().all()]
        )
        return {"total": total, "years": years}
    finally:
        session.close()


@app.get("/api/odds/{match_id}")
def get_odds(match_id: int):
    session = get_session()
    try:
        odds = session.query(Odds).filter(Odds.match_id == match_id).all()
        return {
            "match_id": match_id,
            "data": [
                {
                    "provider": o.provider,
                    "odds_home": o.odds_home,
                    "odds_draw": o.odds_draw,
                    "odds_away": o.odds_away,
                }
                for o in odds
            ],
        }
    finally:
        session.close()


# ─── 爬虫触发 ──────────────────────────────────────────────────

@app.post("/api/scrape")
async def trigger_scrape(req: ScrapeRequest = None):
    years = req.years if req and req.years else None
    result = await scrape_all(years)
    return result


# ─── 回测接口 ──────────────────────────────────────────────────

@app.post("/api/backtest/run")
def run_backtest_api(req: BacktestRequest):
    try:
        result: BacktestResult = run_backtest(
            strategy_name=req.strategy_name,
            start_year=req.start_year,
            end_year=req.end_year,
            stage=req.stage,
            initial_bankroll=req.initial_bankroll,
            stake_per_bet=req.stake_per_bet,
        )
        return {
            "run_id": result.run_id,
            "summary": {
                "stage": req.stage,
                "initial_bankroll": result.initial_bankroll,
                "final_bankroll": result.final_bankroll,
                "total_return": result.total_return,
                "total_bets": result.total_bets,
                "wins": result.wins,
                "pushes": result.pushes,
                "win_rate": result.win_rate,
                "max_drawdown": result.max_drawdown,
            },
            "equity_curve": result.equity_curve,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/backtest/bets")
def get_backtest_bets(
    run_id: int = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    session = get_session()
    try:
        q = session.query(Bet).filter(Bet.run_id == run_id)
        total = q.count()
        bets = (
            q.order_by(Bet.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "data": [
                {
                    "id": b.id,
                    "match_id": b.match_id,
                    "strategy_name": b.strategy_name,
                    "bet_on": b.bet_on,
                    "stake": b.stake,
                    "odds_taken": b.odds_taken,
                    "result": b.result,
                    "profit": b.profit,
                    "match_info": _match_label(session, b.match_id),
                }
                for b in bets
            ],
        }
    finally:
        session.close()


def _match_label(session, match_id):
    m = session.get(Match, match_id)
    if m:
        return f"{m.year} {m.team_home} vs {m.team_away} ({m.score_home}-{m.score_away})"
    return ""


@app.get("/api/backtest/summary/{run_id}")
def get_backtest_summary(run_id: int):
    session = get_session()
    try:
        run = session.get(BacktestRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="回测记录不存在")
        return {
            "id": run.id,
            "strategy_name": run.strategy_name,
            "stage": run.stage,
            "start_year": run.start_year,
            "end_year": run.end_year,
            "initial_bankroll": run.initial_bankroll,
            "final_bankroll": run.final_bankroll,
            "total_return": run.total_return,
            "total_bets": run.total_bets,
            "wins": run.wins,
            "pushes": run.pushes,
            "win_rate": run.win_rate,
            "max_drawdown": run.max_drawdown,
            "created_at": run.created_at.isoformat(),
        }
    finally:
        session.close()


@app.get("/api/backtest/runs")
def get_backtest_runs():
    session = get_session()
    try:
        runs = session.query(BacktestRun).order_by(desc(BacktestRun.created_at)).limit(50).all()
        return {
            "data": [
                {
                    "id": r.id,
                    "strategy_name": r.strategy_name,
                    "total_return": r.total_return,
                    "total_bets": r.total_bets,
                    "win_rate": r.win_rate,
                    "created_at": r.created_at.isoformat(),
                }
                for r in runs
            ]
        }
    finally:
        session.close()


@app.get("/api/strategies")
def get_strategies():
    return {
        "strategies": [
            {"key": "five_signal", "name": "五信号动态统计", "description": "12场滚动窗口+五级阈值，爆冷率/平局率/小球率独立均值回归"},
            {"key": "momentum", "name": "锦标赛动量", "description": "统计前N场冷门率，与基线偏离时反向押注均值回归"},
            {"key": "favorite", "name": "买强队", "description": "每场买赔率最低的选项"},
            {"key": "strong_favorite", "name": "深盘强队", "description": "只买赔率<1.45的深盘强队，跳过模糊比赛"},
            {"key": "draw_hunter", "name": "平局猎人", "description": "小组赛双方实力接近时买平局"},
            {"key": "knockout_underdog", "name": "淘汰赛下盘", "description": "淘汰赛买赔率更高的下盘方"},
            {"key": "odds_range", "name": "赔率区间", "description": "只在赔率1.5~2.2区间内选最优"},
            {"key": "value_bet", "name": "价值投注", "description": "估算真实概率，只在期望值>5%时下注"},
            {"key": "kelly", "name": "凯利公式", "description": "凯利公式动态调整注额"},
        ]
    }


# ─── 手动投注系统 ──────────────────────────────────────────────

def _get_bankroll(session) -> ManualBankroll:
    br = session.query(ManualBankroll).first()
    if not br:
        br = ManualBankroll(initial_bankroll=10000.0, current_bankroll=10000.0)
        session.add(br)
        session.flush()
    return br


def _settle_spf(bet: ManualBet, score_home: int, score_away: int):
    if bet.bet_direction == "home":
        won = score_home > score_away
    elif bet.bet_direction == "away":
        won = score_away > score_home
    else:  # draw
        won = score_home == score_away
    bet.result = "win" if won else "lose"
    bet.profit = bet.stake * (bet.odds_taken - 1) if won else -bet.stake


def _settle_over_under(bet: ManualBet, score_home: int, score_away: int):
    total = score_home + score_away
    direction = bet.bet_direction  # "0", "1", "2", "3", "4", "5+"
    if direction == "5+":
        won = total >= 5
    else:
        try:
            won = total == int(direction)
        except ValueError:
            bet.result = "lose"
            bet.profit = -bet.stake
            return
    bet.result = "win" if won else "lose"
    bet.profit = bet.stake * (bet.odds_taken - 1) if won else -bet.stake


def _settle_score(bet: ManualBet, score_home: int, score_away: int):
    parts = bet.bet_direction.split("-")
    if len(parts) == 2:
        try:
            pred_h = int(parts[0])
            pred_a = int(parts[1])
            won = (score_home == pred_h and score_away == pred_a)
            bet.result = "win" if won else "lose"
            bet.profit = bet.stake * (bet.odds_taken - 1) if won else -bet.stake
        except ValueError:
            pass


def _settle_match(match: ManualMatch, session) -> list:
    """结算一场比赛的所有待定投注，返回结算结果列表"""
    if match.score_home is None or match.score_away is None:
        return []
    pending = session.query(ManualBet).filter(
        ManualBet.match_id == match.id, ManualBet.result == "待定"
    ).all()
    results = []
    br = _get_bankroll(session)
    for bet in pending:
        if bet.odds_type == "spf":
            _settle_spf(bet, match.score_home, match.score_away)
        elif bet.odds_type == "over_under":
            _settle_over_under(bet, match.score_home, match.score_away)
        elif bet.odds_type == "score":
            _settle_score(bet, match.score_home, match.score_away)
        br.current_bankroll += bet.profit
        results.append({"bet_id": bet.id, "result": bet.result, "profit": bet.profit})
    match.is_settled = 1
    session.commit()
    return results


# ── 比赛 CRUD ──

@app.get("/api/manual/matches")
def get_manual_matches(
    stage: Optional[str] = Query(None),
    is_settled: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
):
    session = get_session()
    try:
        q = session.query(ManualMatch)
        if stage and stage != "全部":
            q = q.filter(ManualMatch.stage == stage)
        if is_settled is not None:
            q = q.filter(ManualMatch.is_settled == is_settled)
        total = q.count()
        matches = q.order_by(ManualMatch.match_day, ManualMatch.id).offset((page - 1) * page_size).limit(page_size).all()
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "data": [
                {
                    "id": m.id, "match_day": m.match_day, "stage": m.stage,
                    "team_home": m.team_home, "team_away": m.team_away,
                    "score_home": m.score_home, "score_away": m.score_away,
                    "is_settled": m.is_settled,
                    "bet_count": len(m.bets),
                }
                for m in matches
            ],
        }
    finally:
        session.close()


@app.post("/api/manual/matches")
def create_manual_match(data: dict):
    session = get_session()
    try:
        m = ManualMatch(
            match_day=data.get("match_day", ""),
            stage=data.get("stage", "小组赛"),
            team_home=data["team_home"],
            team_away=data["team_away"],
        )
        session.add(m)
        session.commit()
        session.refresh(m)
        return {"id": m.id, "match_day": m.match_day, "stage": m.stage,
                "team_home": m.team_home, "team_away": m.team_away,
                "is_settled": m.is_settled}
    finally:
        session.close()


@app.put("/api/manual/matches/{match_id}")
def update_manual_match(match_id: int, data: dict):
    session = get_session()
    try:
        m = session.get(ManualMatch, match_id)
        if not m:
            raise HTTPException(status_code=404, detail="比赛不存在")
        for field in ["match_day", "stage", "team_home", "team_away"]:
            if field in data:
                setattr(m, field, data[field])
        session.commit()
        return {"id": m.id, "match_day": m.match_day, "stage": m.stage,
                "team_home": m.team_home, "team_away": m.team_away}
    finally:
        session.close()


@app.delete("/api/manual/matches/{match_id}")
def delete_manual_match(match_id: int):
    session = get_session()
    try:
        m = session.get(ManualMatch, match_id)
        if not m:
            raise HTTPException(status_code=404, detail="比赛不存在")
        if len(m.bets) > 0:
            raise HTTPException(status_code=400, detail="已有投注的比赛不可删除")
        session.delete(m)
        session.commit()
        return {"status": "deleted"}
    finally:
        session.close()


@app.put("/api/manual/matches/{match_id}/score")
def set_match_score(match_id: int, data: dict):
    session = get_session()
    try:
        m = session.get(ManualMatch, match_id)
        if not m:
            raise HTTPException(status_code=404, detail="比赛不存在")
        m.score_home = data.get("score_home", 0)
        m.score_away = data.get("score_away", 0)
        # 可选：同时录入赛前赔率（供智能下注使用）
        if data.get("odds_home"):
            m.odds_home = float(data["odds_home"])
        if data.get("odds_draw"):
            m.odds_draw = float(data["odds_draw"])
        if data.get("odds_away"):
            m.odds_away = float(data["odds_away"])
        session.flush()
        settled = _settle_match(m, session)
        return {
            "match_id": m.id, "score_home": m.score_home, "score_away": m.score_away,
            "is_settled": m.is_settled, "settled_bets": settled,
        }
    finally:
        session.close()


# ── 投注 CRUD ──

@app.get("/api/manual/bets")
def get_manual_bets(
    match_id: Optional[int] = Query(None),
    result: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
):
    session = get_session()
    try:
        q = session.query(ManualBet)
        if match_id:
            q = q.filter(ManualBet.match_id == match_id)
        if result:
            q = q.filter(ManualBet.result == result)
        total = q.count()
        bets = q.order_by(ManualBet.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return {
            "total": total, "page": page, "page_size": page_size,
            "data": [
                {
                    "id": b.id, "match_id": b.match_id, "odds_type": b.odds_type,
                    "bet_direction": b.bet_direction, "stake": b.stake,
                    "odds_taken": b.odds_taken, "threshold": b.threshold,
                    "result": b.result, "profit": b.profit,
                    "created_at": b.created_at.isoformat(),
                    "match_info": _manual_match_label(session, b.match_id),
                }
                for b in bets
            ],
        }
    finally:
        session.close()


def _manual_match_label(session, match_id):
    m = session.get(ManualMatch, match_id)
    if m:
        return f"{m.match_day} {m.team_home} vs {m.team_away}"
    return ""


@app.post("/api/manual/bets")
def create_manual_bet(data: dict):
    session = get_session()
    try:
        match = session.get(ManualMatch, int(data["match_id"]))
        if not match:
            raise HTTPException(status_code=404, detail="比赛不存在")
        if match.is_settled:
            raise HTTPException(status_code=400, detail="比赛已结算，不可下注")
        br = _get_bankroll(session)
        stake = float(data["stake"])
        if stake > br.current_bankroll:
            raise HTTPException(status_code=400, detail=f"资金不足，当前余额 ¥{br.current_bankroll:.0f}")

        b = ManualBet(
            match_id=match.id,
            odds_type=data["odds_type"],
            bet_direction=data["bet_direction"],
            stake=stake,
            odds_taken=float(data.get("odds_taken", 1.0)),
            threshold=float(data["threshold"]) if data.get("threshold") else None,
        )
        session.add(b)
        session.commit()
        session.refresh(b)
        return {
            "id": b.id, "match_id": b.match_id, "odds_type": b.odds_type,
            "bet_direction": b.bet_direction, "stake": b.stake,
            "odds_taken": b.odds_taken, "threshold": b.threshold,
            "result": b.result, "created_at": b.created_at.isoformat(),
        }
    finally:
        session.close()


@app.delete("/api/manual/bets/{bet_id}")
def delete_manual_bet(bet_id: int):
    session = get_session()
    try:
        b = session.get(ManualBet, bet_id)
        if not b:
            raise HTTPException(status_code=404, detail="投注不存在")
        if b.result != "待定":
            raise HTTPException(status_code=400, detail="已结算的投注不可删除")
        session.delete(b)
        session.commit()
        return {"status": "deleted"}
    finally:
        session.close()


@app.put("/api/manual/bets/{bet_id}/result")
def update_bet_result(bet_id: int, data: dict):
    session = get_session()
    try:
        b = session.get(ManualBet, bet_id)
        if not b:
            raise HTTPException(status_code=404, detail="投注不存在")
        new_result = data.get("result", b.result)
        new_profit = data.get("profit", b.profit)
        # 回滚旧盈亏
        br = _get_bankroll(session)
        br.current_bankroll -= b.profit
        b.result = new_result
        b.profit = float(new_profit)
        br.current_bankroll += b.profit
        session.commit()
        return {"id": b.id, "result": b.result, "profit": b.profit}
    finally:
        session.close()


# ── 资金管理 ──

@app.get("/api/manual/bankroll")
def get_manual_bankroll():
    session = get_session()
    try:
        br = _get_bankroll(session)
        bets = session.query(ManualBet).all()
        pending = sum(1 for b in bets if b.result == "待定")
        settled = [b for b in bets if b.result != "待定"]
        wins = sum(1 for b in settled if b.result == "win")
        pushes = sum(1 for b in settled if b.result == "push")
        losses = len(settled) - wins - pushes
        return {
            "initial_bankroll": br.initial_bankroll,
            "current_bankroll": br.current_bankroll,
            "total_profit": br.current_bankroll - br.initial_bankroll,
            "total_bets": len(settled),
            "pending_bets": pending,
            "wins": wins, "losses": losses, "pushes": pushes,
            "win_rate": round(wins / len(settled), 4) if settled else 0,
        }
    finally:
        session.close()


@app.put("/api/manual/bankroll")
def set_manual_bankroll(data: dict):
    session = get_session()
    try:
        br = _get_bankroll(session)
        new_initial = float(data.get("initial_bankroll", br.initial_bankroll))
        # 调整当前资金（按差值）
        diff = new_initial - br.initial_bankroll
        br.initial_bankroll = new_initial
        br.current_bankroll += diff
        session.commit()
        return {"initial_bankroll": br.initial_bankroll, "current_bankroll": br.current_bankroll}
    finally:
        session.close()


# ─── 智能下注 ──────────────────────────────────────────────────

@app.post("/api/smart/recommend")
def smart_recommend(data: dict):
    """根据已结算比赛统计，为当前比赛推荐下注方向"""
    try:
        result = get_recommendations(
            match_id=int(data["match_id"]),
            odds_home=float(data["odds_home"]),
            odds_draw=float(data["odds_draw"]),
            odds_away=float(data["odds_away"]),
        )
        return result
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"缺少参数: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/smart/context")
def smart_context():
    """返回当前统计窗口概览"""
    return get_context_info()


# ─── 静态资源 ──────────────────────────────────────────────────

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

@app.get("/")
async def root():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/manual")
async def manual_page():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(STATIC_DIR, "manual.html"))


@app.get("/smart")
async def smart_page():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(STATIC_DIR, "smart.html"))


if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
