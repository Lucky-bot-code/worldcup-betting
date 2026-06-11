"""
智能下注推荐引擎
用已结算手动比赛建统计窗口 → 跑合并均值回归策略 → 输出多维度推荐
"""

from models import get_session, ManualMatch, ManualBet
from strategies.base import MatchContext
from strategies.merged_mean_reversion import (
    MergedMeanReversionStrategy,
    OBSERVE_WINDOW, TARGET_PROFIT, BUDGET, MAX_STAKE_RATIO, MIN_STAKE_RATIO,
)


def _merge_goals_5plus(goals_odds: dict[str, float]) -> dict[str, float]:
    """将 5球/6球/7+球 赔率取平均合并到 key "5"，供策略 5+ 桶使用"""
    if not goals_odds:
        return goals_odds
    vals = []
    for k in ["5", "6", "7"]:
        v = goals_odds.get(k)
        if v:
            vals.append(v)
    if len(vals) >= 2:
        goals_odds["5"] = round(sum(vals) / len(vals), 2)
    return goals_odds


def _build_context(m: ManualMatch) -> MatchContext:
    """将 ManualMatch 转为策略可用的 MatchContext，含多维度赔率"""
    import json

    goals_odds = {}
    score_odds = {}

    # 从 JSON 列加载总进球和比分赔率
    if m.goals_odds_json:
        try:
            goals_odds = json.loads(m.goals_odds_json)
        except (json.JSONDecodeError, TypeError):
            pass
    if m.score_odds_json:
        try:
            score_odds = json.loads(m.score_odds_json)
        except (json.JSONDecodeError, TypeError):
            pass

    goals_odds = _merge_goals_5plus(goals_odds)

    # 也从关联的 ManualBet 中补充赔率（兼容旧数据）
    if hasattr(m, 'bets') and m.bets:
        for bet in m.bets:
            if bet.odds_type == "over_under" and bet.odds_taken and bet.bet_direction not in goals_odds:
                goals_odds[bet.bet_direction] = bet.odds_taken
            elif bet.odds_type == "score" and bet.odds_taken and bet.bet_direction not in score_odds:
                score_odds[bet.bet_direction] = bet.odds_taken

    return MatchContext(
        match_id=m.id,
        year=2026,
        stage=m.stage,
        team_home=m.team_home,
        team_away=m.team_away,
        odds_home=m.odds_home or 1.0,
        odds_draw=m.odds_draw or 1.0,
        odds_away=m.odds_away or 1.0,
        score_home=m.score_home,
        score_away=m.score_away,
        goals_odds=goals_odds,
        score_odds=score_odds,
    )


def get_recommendations(
    match_id: int,
    odds_home: float,
    odds_draw: float,
    odds_away: float,
    goals_odds: dict[str, float] | None = None,
    score_odds: dict[str, float] | None = None,
    bankroll: float | None = None,
) -> dict:
    """
    返回合并策略的多维度推荐。

    参数:
    - match_id: 当前要分析的比赛 ID
    - odds_*: 当前比赛的即时 SPF 赔率
    - goals_odds: 当前比赛的进球数赔率 {"0": 8.5, "1": 4.2, ...}
    - score_odds: 当前比赛的比分赔率 {"1:0": 6.5, "2:1": 8.0, ...}
    - bankroll: 当前资金 (默认 ¥3000)
    """
    session = get_session()
    try:
        current_match = session.get(ManualMatch, match_id)
        if not current_match:
            return {"error": "比赛不存在"}
        if current_match.is_settled:
            return {"error": "比赛已结算，无法分析"}

        # 查询已结算的小组赛
        settled = (
            session.query(ManualMatch)
            .filter(
                ManualMatch.is_settled == 1,
                ManualMatch.stage == "小组赛",
                ManualMatch.score_home.isnot(None),
                ManualMatch.score_away.isnot(None),
                ManualMatch.odds_home.isnot(None),
            )
            .order_by(ManualMatch.match_day, ManualMatch.id)
            .all()
        )

        # 初始化合并策略
        strategy = MergedMeanReversionStrategy()

        # 用已结算比赛喂策略
        for m in settled:
            ctx = _build_context(m)
            strategy.on_result(ctx)

        # 合并 5/6/7+ → 5 均值
        goals_odds = _merge_goals_5plus(goals_odds or {})

        # 当前比赛 context
        cur_ctx = MatchContext(
            match_id=current_match.id,
            year=2026,
            stage=current_match.stage,
            team_home=current_match.team_home,
            team_away=current_match.team_away,
            odds_home=odds_home,
            odds_draw=odds_draw,
            odds_away=odds_away,
            score_home=None,
            score_away=None,
            goals_odds=goals_odds,
            score_odds=score_odds or {},
        )

        # 获取策略上下文
        context = strategy.context_info()

        # 多维度决策
        br = bankroll if bankroll is not None else BUDGET
        decisions = strategy.decide_multi(cur_ctx, br)

        return {
            "match_info": f"{current_match.match_day} {current_match.team_home} vs {current_match.team_away}",
            "match_stage": current_match.stage,
            "context": context,
            "recommendations": [_format_decision(d) for d in decisions],
            "stake_params": {
                "budget": BUDGET,
                "target_profit": TARGET_PROFIT,
                "max_stake_ratio": MAX_STAKE_RATIO,
                "min_stake_ratio": MIN_STAKE_RATIO,
                "current_bankroll": br,
            },
        }
    finally:
        session.close()


def _format_decision(d) -> dict:
    """格式化 BetDecision 为前端可用的 dict"""
    type_label = {"spf": "胜平负", "goals": "总进球数", "score": "比分"}
    spf_label = {"home": "主胜", "draw": "平局", "away": "客胜"}

    bet_label = d.bet_on
    if d.bet_type == "spf":
        bet_label = spf_label.get(d.bet_on, d.bet_on)

    return {
        "bet_type": d.bet_type,
        "bet_type_label": type_label.get(d.bet_type, d.bet_type),
        "bet_on": d.bet_on,
        "bet_label": bet_label,
        "odds": d.odds if d.odds else None,
        "stake": d.stake,
        "confidence": d.confidence,
        "reason": d.reason,
    }


def get_context_info() -> dict:
    """获取当前统计上下文（不针对特定比赛）"""
    session = get_session()
    try:
        settled = (
            session.query(ManualMatch)
            .filter(
                ManualMatch.is_settled == 1,
                ManualMatch.stage == "小组赛",
                ManualMatch.score_home.isnot(None),
                ManualMatch.score_away.isnot(None),
                ManualMatch.odds_home.isnot(None),
            )
            .order_by(ManualMatch.match_day, ManualMatch.id)
            .all()
        )

        strategy = MergedMeanReversionStrategy()
        for m in settled:
            ctx = _build_context(m)
            strategy.on_result(ctx)

        return strategy.context_info()
    finally:
        session.close()
