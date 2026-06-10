"""只买深盘强队：仅当赔率低于阈值时才下注，避免"假热门"。"""
from .base import BaseStrategy, MatchContext, BetDecision
from typing import Optional


class StrongFavoriteStrategy(BaseStrategy):
    name = "strong_favorite"
    description = "只买深盘强队（赔率<1.45），跳过模糊比赛"

    def __init__(self, max_odds: float = 1.45, stake_per_bet: float = 100):
        self.max_odds = max_odds
        self.stake_per_bet = stake_per_bet

    def decide(self, ctx: MatchContext, bankroll: float) -> Optional[BetDecision]:
        if bankroll < self.stake_per_bet:
            return None

        for bet_on, odds in [("home", ctx.odds_home), ("away", ctx.odds_away)]:
            if 1.0 < odds <= self.max_odds:
                return BetDecision(
                    bet_on=bet_on,
                    stake=self.stake_per_bet,
                    odds=odds,
                    confidence=1.0 / odds,
                )
        return None
