"""淘汰赛下盘：淘汰赛买赔率更高的一方（下盘狗）。淘汰赛中弱势方更可能死守逼平/爆冷。"""
from .base import BaseStrategy, MatchContext, BetDecision
from typing import Optional


class KnockoutUnderdogStrategy(BaseStrategy):
    name = "knockout_underdog"
    description = "淘汰赛买下盘（赔率更高的一方），利用淘汰赛保守特性"

    def __init__(self, max_fav_odds: float = 2.5, min_dog_odds: float = 2.2, max_dog_odds: float = 7.0, stake_per_bet: float = 100):
        self.max_fav_odds = max_fav_odds
        self.min_dog_odds = min_dog_odds
        self.max_dog_odds = max_dog_odds
        self.stake_per_bet = stake_per_bet

    KNOCKOUT_STAGES = {"16强", "8强", "半决赛", "季军赛", "决赛"}

    def decide(self, ctx: MatchContext, bankroll: float) -> Optional[BetDecision]:
        if bankroll < self.stake_per_bet:
            return None
        if ctx.stage not in self.KNOCKOUT_STAGES:
            return None

        # 必须有一个明显的热门（低赔）
        fav_odds = min(ctx.odds_home, ctx.odds_away)
        if fav_odds > self.max_fav_odds:
            return None  # 没有明显热门，不下注

        # 买下盘：赔率更高的那方
        if ctx.odds_home > ctx.odds_away:
            dog_odds, dog_side = ctx.odds_home, "home"
        else:
            dog_odds, dog_side = ctx.odds_away, "away"

        if not (self.min_dog_odds <= dog_odds <= self.max_dog_odds):
            return None

        return BetDecision(
            bet_on=dog_side,
            stake=self.stake_per_bet,
            odds=dog_odds,
            confidence=1.0 / dog_odds,
        )
