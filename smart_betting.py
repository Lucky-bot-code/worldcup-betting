"""
智能下注推荐引擎
用已结算手动比赛建统计窗口 → 跑五信号 + 动量策略 → 输出推荐
"""

from models import get_session, ManualMatch
from strategies.base import MatchContext
from strategies.five_signal import FiveSignalStrategy, Thresholds
from strategies.momentum import MomentumStrategy


def _build_context(m: ManualMatch, odds_home: float, odds_draw: float, odds_away: float) -> MatchContext:
    """将 ManualMatch 转为策略可用的 MatchContext"""
    return MatchContext(
        match_id=m.id,
        year=2026,
        stage=m.stage,
        team_home=m.team_home,
        team_away=m.team_away,
        odds_home=odds_home,
        odds_draw=odds_draw,
        odds_away=odds_away,
        score_home=m.score_home,
        score_away=m.score_away,
    )


def get_recommendations(
    match_id: int,
    odds_home: float,
    odds_draw: float,
    odds_away: float,
) -> dict:
    """
    返回两个策略的推荐。
    - match_id: 当前要分析的比赛 ID
    - odds_*: 当前比赛的即时 SPF 赔率
    """
    session = get_session()
    try:
        current_match = session.get(ManualMatch, match_id)
        if not current_match:
            return {"error": "比赛不存在"}
        if current_match.is_settled:
            return {"error": "比赛已结算，无法分析"}

        # 查询已结算的小组赛（有赔率 + 有赛果），按日期排序
        settled = (
            session.query(ManualMatch)
            .filter(
                ManualMatch.is_settled == 1,
                ManualMatch.stage == "小组赛",
                ManualMatch.score_home.isnot(None),
                ManualMatch.score_away.isnot(None),
                ManualMatch.odds_home.isnot(None),
                ManualMatch.odds_draw.isnot(None),
                ManualMatch.odds_away.isnot(None),
            )
            .order_by(ManualMatch.match_day, ManualMatch.id)
            .all()
        )

        # 初始化两个策略
        five = FiveSignalStrategy(stake_per_bet=100)
        momentum = MomentumStrategy(stake_per_bet=100)

        # 用已结算比赛喂策略
        for m in settled:
            ctx = _build_context(m, m.odds_home, m.odds_draw, m.odds_away)
            five.on_result(ctx)
            momentum.on_result(ctx)

        # 当前比赛 context
        cur_ctx = _build_context(current_match, odds_home, odds_draw, odds_away)
        cur_ctx.score_home = None  # 未发生，不能有比分
        cur_ctx.score_away = None

        # 五信号决策
        fs_decision = five.decide(cur_ctx, bankroll=999999)

        # 动量决策
        mo_decision = momentum.decide(cur_ctx, bankroll=999999)

        # 统计上下文
        upset_rate, draw_rate, under_rate, zero_rate = five._running_rates()
        n_history = len(five._history)
        fs_reason = _five_signal_reason(upset_rate, draw_rate, under_rate, fs_decision, zero_rate)

        mo_reason = _momentum_reason(momentum, settled)

        return {
            "match_info": f"{current_match.match_day} {current_match.team_home} vs {current_match.team_away}",
            "match_stage": current_match.stage,
            "context": {
                "settled_count": len(settled),
                "window_size": n_history,
                "upset_rate": round(upset_rate, 3),
                "draw_rate": round(draw_rate, 3),
                "under_rate": round(under_rate, 3),
                "zero_goal_rate": round(zero_rate, 3),
                "momentum_phase": momentum._phase,
                "momentum_reason": getattr(momentum, '_reason', ''),
            },
            "five_signal": {
                "action": _decision_summary(fs_decision),
                "bet_on": fs_decision.bet_on if fs_decision else None,
                "odds": fs_decision.odds if fs_decision else None,
                "confidence": fs_decision.confidence if fs_decision else 0,
                "stake_mult": 2.0 if (fs_decision and fs_decision.confidence > 0.35) else 1.0,
                "reason": fs_reason,
            },
            "momentum": {
                "action": _decision_summary(mo_decision),
                "bet_on": mo_decision.bet_on if mo_decision else None,
                "odds": mo_decision.odds if mo_decision else None,
                "confidence": mo_decision.confidence if mo_decision else 0,
                "reason": mo_reason,
            },
        }
    finally:
        session.close()


def _decision_summary(decision) -> str:
    if decision is None:
        return "观望 — 无信号"
    dir_label = {"home": "主胜", "draw": "平局", "away": "客胜"}
    d = dir_label.get(decision.bet_on, decision.bet_on)
    return f"推荐下注: {d} @{decision.odds:.2f} 置信度{decision.confidence:.0%}"


def _five_signal_reason(upset_rate: float, draw_rate: float, under_rate: float, decision, zero_rate: float) -> str:
    w = Thresholds.WINDOW
    parts = [f"12场窗口: 冷门率{upset_rate:.0%}(P75={Thresholds.UPSET_P75:.0%} P90={Thresholds.UPSET_P90:.0%})",
             f"平局率{draw_rate:.0%}(P75={Thresholds.DRAW_P75:.0%} P90={Thresholds.DRAW_P90:.0%})"]

    if decision is None:
        parts.append("→ 无维度超阈值，观望")
        if upset_rate > 0 and upset_rate == 0:
            parts.append("(冷门率0%仅意味无爆冷型比赛)")
        return "；".join(parts)

    d = {"home": "主胜", "draw": "平局", "away": "客胜"}.get(decision.bet_on, decision.bet_on)

    if upset_rate > Thresholds.UPSET_P90:
        parts.append(f"冷门率{upset_rate:.0%}>P90→强押{d}回归")
    elif upset_rate > Thresholds.UPSET_P75:
        parts.append(f"冷门率{upset_rate:.0%}>P75→弱押{d}回归")
    elif draw_rate > Thresholds.DRAW_P90:
        parts.append(f"平局率{draw_rate:.0%}>P90→押{d}分胜负")
    elif draw_rate > Thresholds.DRAW_P75:
        parts.append(f"平局率{draw_rate:.0%}>P75→押{d}分胜负")

    if zero_rate > Thresholds.ZERO_GOAL_ALERT:
        parts.append(f"0球率{zero_rate:.0%}>15%→辅助增强")

    return "；".join(parts)


def _momentum_reason(strategy: MomentumStrategy, settled: list) -> str:
    phase = strategy._phase
    if phase == "observing":
        n = strategy._obs_total
        obs = strategy.observation_window
        return f"观察期 {n}/{obs} 场，尚需 {obs - n} 场后确定方向"
    if phase == "skip":
        return f"观察结果: {getattr(strategy, '_reason', '正常范围，观望')}"
    if phase in ("bet_fav", "bet_dog"):
        return f"方向已锁定: {getattr(strategy, '_reason', '')}"
    return "—"


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

        five = FiveSignalStrategy(stake_per_bet=100)
        momentum = MomentumStrategy(stake_per_bet=100)

        for m in settled:
            ctx = _build_context(m, m.odds_home, m.odds_draw, m.odds_away)
            five.on_result(ctx)
            momentum.on_result(ctx)

        upset_rate, draw_rate, under_rate, zero_rate = five._running_rates()

        return {
            "settled_count": len(settled),
            "window_size": len(five._history),
            "upset_rate": round(upset_rate, 3),
            "draw_rate": round(draw_rate, 3),
            "under_rate": round(under_rate, 3),
            "zero_goal_rate": round(zero_rate, 3),
            "momentum_phase": momentum._phase,
            "momentum_reason": getattr(momentum, '_reason', ''),
        }
    finally:
        session.close()
