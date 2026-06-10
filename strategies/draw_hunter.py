"""平局猎人：小组赛中两队实力接近时买平局。"""
from .base import BaseStrategy, MatchContext, BetDecision
from typing import Optional


class DrawHunterStrategy(BaseStrategy):
    name = "draw_hunter"
    description = "小组赛中实力接近的比赛买平局"

    def __init__(self, max_diff: float = 1.2, min_draw_odds: float = 2.8, max_draw_odds: float = 4.2, stake_per_bet: float = 100):
        """
        max_diff: 主客赔率差上限（< 此值说明两队接近）
        min/max_draw_odds: 平局赔率范围
        """
        self.max_diff = max_diff
        self.min_draw_odds = min_draw_odds
        self.max_draw_odds = max_draw_odds
        self.stake_per_bet = stake_per_bet

    def decide(self, ctx: MatchContext, bankroll: float) -> Optional[BetDecision]:
        if bankroll < self.stake_per_bet:
            return None
        # 仅小组赛
        if ctx.stage != "小组赛":
            return None
        # 两队赔率接近 → 实力相当
        diff = abs(ctx.odds_home - ctx.odds_away)
        if diff > self.max_diff:
            return None
        # 平局赔率在合理范围
        if not (self.min_draw_odds <= ctx.odds_draw <= self.max_draw_odds):
            return None

        return BetDecision(
            bet_on="draw",
            stake=self.stake_per_bet,
            odds=ctx.odds_draw,
            confidence=0.3,
        )
