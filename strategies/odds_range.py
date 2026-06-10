"""赔率区间策略：只在 1.5~2.2 区间内买胜平负，跳过极端赔率。"""
from .base import BaseStrategy, MatchContext, BetDecision
from typing import Optional


class OddsRangeStrategy(BaseStrategy):
    name = "odds_range"
    description = "只在赔率 1.5~2.2 区间买胜/负，避开深盘和超大冷门"

    def __init__(self, min_odds: float = 1.5, max_odds: float = 2.2, stake_per_bet: float = 100):
        self.min_odds = min_odds
        self.max_odds = max_odds
        self.stake_per_bet = stake_per_bet

    def decide(self, ctx: MatchContext, bankroll: float) -> Optional[BetDecision]:
        if bankroll < self.stake_per_bet:
            return None

        best = None
        best_odds = 999
        for bet_on, odds in [("home", ctx.odds_home), ("draw", ctx.odds_draw), ("away", ctx.odds_away)]:
            if self.min_odds <= odds <= self.max_odds and odds < best_odds:
                best_odds = odds
                best = bet_on

        if best:
            return BetDecision(
                bet_on=best,
                stake=self.stake_per_bet,
                odds=best_odds,
                confidence=1.0 / best_odds,
            )
        return None
