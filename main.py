from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from typing import Optional
import os

from models import init_db, get_session, ManualMatch, ManualBet, ManualBankroll
from smart_betting import get_recommendations, get_context_info
from sqlalchemy import desc

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # 初始化手动投注资金记录（单行）
    session = get_session()
    try:
        if session.query(ManualBankroll).count() == 0:
            session.add(ManualBankroll(initial_bankroll=3000.0, current_bankroll=3000.0))
            session.commit()
    finally:
        session.close()
    yield


app = FastAPI(title="世界杯彩票盈利系统", lifespan=lifespan)


# ─── 手动投注系统 ──────────────────────────────────────────────

def _get_bankroll(session) -> ManualBankroll:
    br = session.query(ManualBankroll).first()
    if not br:
        br = ManualBankroll(initial_bankroll=3000.0, current_bankroll=3000.0)
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
    direction = bet.bet_direction  # "0"~"6", "7+", "5+"(legacy)
    if direction == "7+":
        won = total >= 7
    elif direction == "5+":
        won = total >= 5  # legacy
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


@app.get("/api/manual/matches/{match_id}")
def get_manual_match(match_id: int):
    session = get_session()
    try:
        m = session.get(ManualMatch, match_id)
        if not m:
            raise HTTPException(status_code=404, detail="比赛不存在")
        return {
            "id": m.id, "match_day": m.match_day, "stage": m.stage,
            "team_home": m.team_home, "team_away": m.team_away,
            "score_home": m.score_home, "score_away": m.score_away,
            "odds_home": m.odds_home, "odds_draw": m.odds_draw, "odds_away": m.odds_away,
            "goals_odds_json": m.goals_odds_json, "score_odds_json": m.score_odds_json,
            "is_settled": m.is_settled,
        }
    finally:
        session.close()


@app.put("/api/manual/matches/{match_id}")
def update_manual_match(match_id: int, data: dict):
    session = get_session()
    try:
        m = session.get(ManualMatch, match_id)
        if not m:
            raise HTTPException(status_code=404, detail="比赛不存在")
        for field in ["match_day", "stage", "team_home", "team_away",
                       "odds_home", "odds_draw", "odds_away",
                       "goals_odds_json", "score_odds_json"]:
            if field in data:
                setattr(m, field, data[field])
        session.commit()
        return {"id": m.id, "match_day": m.match_day, "stage": m.stage,
                "team_home": m.team_home, "team_away": m.team_away,
                "odds_home": m.odds_home, "odds_draw": m.odds_draw, "odds_away": m.odds_away,
                "goals_odds_json": m.goals_odds_json, "score_odds_json": m.score_odds_json}
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
    """根据已结算比赛统计，为当前比赛推荐多维度下注方向"""
    try:
        result = get_recommendations(
            match_id=int(data["match_id"]),
            odds_home=float(data["odds_home"]),
            odds_draw=float(data.get("odds_draw", 3.0)),
            odds_away=float(data["odds_away"]),
            goals_odds=data.get("goals_odds", {}),
            score_odds=data.get("score_odds", {}),
            bankroll=float(data.get("bankroll", 3000)),
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


# ─── 竞彩赔率刷新 ──────────────────────────────────────────────

@app.post("/api/odds/refresh")
def refresh_odds():
    """从 500.com 抓取竞彩实时赔率（SPF + 总进球 + 比分）"""
    from scraper.sporttery import update_manual_matches
    return update_manual_matches()


# ─── 静态资源 ──────────────────────────────────────────────────

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# 禁用浏览器缓存
_HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate"}

@app.get("/")
async def root():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers=_HEADERS)


@app.get("/manual")
async def manual_page():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(STATIC_DIR, "manual.html"), headers=_HEADERS)


@app.get("/smart")
async def smart_page():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(STATIC_DIR, "smart.html"), headers=_HEADERS)


if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
