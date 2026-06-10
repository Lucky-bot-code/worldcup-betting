"""
价值投注：利用 favorite-longshot bias（市场低估热门、高估冷门）。
对低赔率上调真实胜率，对高赔率下调，只在期望值 > 阈值时下注。
"""
from .base import BaseStrategy, MatchContext, BetDecision
from typing import Optional


class ValueBetStrategy(BaseStrategy):
    name = "value_bet"
    description = "利用冷热偏差：上调热门胜率、下调冷门，仅在+EV时下注"

    def __init__(self, min_edge: float = 0.015, max_odds: float = 3.0, stake_per_bet: float = 100):
        self.min_edge = min_edge
        self.max_odds = max_odds
        self.stake_per_bet = stake_per_bet

    def decide(self, ctx: MatchContext, bankroll: float) -> Optional[BetDecision]:
        if bankroll < self.stake_per_bet:
            return None

        odds_map = {"home": ctx.odds_home, "draw": ctx.odds_draw, "away": ctx.odds_away}

        # 1. 原始隐含概率
        raw_probs = {k: 1.0 / v for k, v in odds_map.items()}
        overround = sum(raw_probs.values())

        # 2. 乘法去水
        fair_probs = {k: v / overround for k, v in raw_probs.items()}

        # 3. Favorite-longshot bias 修正：
        #    热门(<2.0): 概率 * 1.08   (市场低估热门)
        #    中位(2.0~3.0): 概率 * 1.02
        #    冷门(>3.0): 概率 * 0.92   (市场高估冷门)
        adj_probs = {}
        for k, odds in odds_map.items():
            if odds < 2.0:
                adj_probs[k] = fair_probs[k] * 1.08
            elif odds > 3.0:
                adj_probs[k] = fair_probs[k] * 0.92
            else:
                adj_probs[k] = fair_probs[k] * 1.02

        # 归一化
        adj_sum = sum(adj_probs.values())
        adj_probs = {k: v / adj_sum for k, v in adj_probs.items()}

        # 4. 找最大 EV
        best = None
        best_edge = 0
        for bet_on, odds in odds_map.items():
            if odds > self.max_odds:
                continue
            ev = adj_probs[bet_on] * odds - 1.0
            if ev > best_edge:
                best_edge = ev
                best = (bet_on, odds, ev)

        if best and best_edge >= self.min_edge:
            bet_on, odds, ev = best
            return BetDecision(
                bet_on=bet_on,
                stake=round(self.stake_per_bet * (1 + ev * 2), 2),
                odds=odds,
                confidence=min(ev * 20, 1.0),
            )

        return None
