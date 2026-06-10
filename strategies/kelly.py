from .base import BaseStrategy, MatchContext, BetDecision
from typing import Optional


class KellyStrategy(BaseStrategy):
    """
    凯利公式策略：
    f* = (bp - q) / b
    b = 赔率 - 1
    p = 估计的真实胜率
    q = 1 - p
    只有当 f* > 0 时才下注。
    """

    name = "kelly"
    description = "凯利公式：期望值为正时才下注，动态调整注额"

    def __init__(self, fraction: float = 0.25, min_odds: float = 1.3, max_odds: float = 5.0, stake_per_bet: float = 100):
        """
        fraction: 凯利比例系数（0~1），保守起见默认用 1/4 凯利
        min_odds / max_odds: 赔率过滤范围
        stake_per_bet: 兼容参数，凯利策略不使用固定注额
        """
        self.fraction = fraction
        self.min_odds = min_odds
        self.max_odds = max_odds

    def decide(self, ctx: MatchContext, bankroll: float) -> Optional[BetDecision]:
        outcomes = {
            "home": ctx.odds_home,
            "draw": ctx.odds_draw,
            "away": ctx.odds_away,
        }

        best_bet = None
        best_edge = 0

        for bet_on, odds in outcomes.items():
            if odds < self.min_odds or odds > self.max_odds:
                continue

            # 用赔率反推隐含概率，再做一个简单调整作为估计概率
            implied_prob = 1.0 / odds
            # 假设市场略微高估冷门，对低赔率（强队）稍微上调
            estimated_prob = implied_prob * (1.0 + 0.05 * (3.0 - odds) / 3.0)

            b = odds - 1.0
            p = estimated_prob
            q = 1.0 - p

            edge = p * b - q  # bp - q

            if edge > best_edge:
                best_edge = edge
                # 全凯利比例
                kelly_fraction = (p * b - q) / b
                kelly_fraction = max(0, min(kelly_fraction, 0.1))  # 单场上限 10%
                stake = bankroll * kelly_fraction * self.fraction
                best_bet = BetDecision(
                    bet_on=bet_on,
                    stake=round(stake, 2),
                    odds=odds,
                    confidence=min(kelly_fraction * 10, 1.0),
                )

        if best_bet and best_bet.stake >= 10:
            return best_bet
        return None
