from .base import BaseStrategy, MatchContext, BetDecision
from typing import Optional


class BuyFavoriteStrategy(BaseStrategy):
    """每场都买赔率最低的选项（胜/平/负中赔率最小的）"""

    name = "favorite"
    description = "每场买赔率最低的选项"

    def __init__(self, stake_per_bet: float = 100):
        self.stake_per_bet = stake_per_bet

    def decide(self, ctx: MatchContext, bankroll: float) -> Optional[BetDecision]:
        if bankroll < self.stake_per_bet:
            return None

        odds_map = {
            "home": ctx.odds_home,
            "draw": ctx.odds_draw,
            "away": ctx.odds_away,
        }
        # 找赔率最低的（= 概率最高的）
        bet_on = min(odds_map, key=odds_map.get)
        odds = odds_map[bet_on]

        # 赔率太高（> 3.0）说明是冷门，跳过
        if odds > 3.0:
            return None

        return BetDecision(
            bet_on=bet_on,
            stake=self.stake_per_bet,
            odds=odds,
            confidence=1.0 / odds,
        )
