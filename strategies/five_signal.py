"""
五信号动态统计策略 —— 12场滚动窗口 + 五级阈值 + 三个独立维度均值回归。

信号体系:
  信号 1 (爆冷率): 赔率差 >= 2x 时弱队胜出 → 太高押热门, 太低押下盘
  信号 2 (小球率): 总进球 < 3           → 太高押大球, 太低押小球 (需大小球赔率)
  信号 3 (平局率): 双方打平              → 太高押分胜负, 太低押平局
  信号 4 (进球分布): 0~5+ 球概率分布    → 辅助信号, 调整置信度
  信号 5 (比分概率): <=3 球各比分概率   → 辅助信号, 调整置信度

阈值体系 (基于 2014-2022 三届小组赛 12 场滚动窗口 P10/P25/P75/P90):
  强信号 (超 P90/低于 P10): 2x 注额
  弱信号 (超 P75/低于 P25): 1x 注额
  中性 (P25~P75): 不下注

策略核心: 每场比赛独立评估三个维度, 均值回归方向押注。
"""

from .base import BaseStrategy, MatchContext, BetDecision
from typing import Optional


# ─── 12 场滚动窗口基准 (来自 2014/2018/2022 三届小组赛滚动统计) ───

class Thresholds:
    # 爆冷率: mean=18%, SD=10%
    UPSET_P10 = 0.10   # 极低冷门 → 强押下盘
    UPSET_P25 = 0.11   # 较低冷门 → 弱押下盘
    UPSET_P75 = 0.25   # 较高冷门 → 弱押热门
    UPSET_P90 = 0.29   # 极高冷门 → 强押热门

    # 小球率: mean=48%, SD=17%  (仅作辅助, 当前无大小球赔率可投)
    UNDER_P10 = 0.25
    UNDER_P25 = 0.33
    UNDER_P75 = 0.58
    UNDER_P90 = 0.67

    # 平局率: mean=20%, SD=10%
    DRAW_P10 = 0.08   # 极低平局 → 强押平局
    # P25 与 P10 重叠 (同为 8%), 弱无平局与强无平局合并
    DRAW_P75 = 0.25   # 较高平局 → 弱押分胜负
    DRAW_P90 = 0.33   # 极高平局 → 强押分胜负

    # 进球分布 (辅助) - 0 球异常高 (>15%) → 进攻乏力型小球 → 大球回归更可期
    ZERO_GOAL_ALERT = 0.15

    # 窗口
    WINDOW = 12


class FiveSignalStrategy(BaseStrategy):
    """五信号动态统计策略"""

    name = "five_signal"
    description = "五信号动态统计：12场滚动窗口，五级阈值，独立均值回归押注"

    def __init__(self, stake_per_bet: float = 100):
        self.stake_per_bet = stake_per_bet
        self._reset()

    def _reset(self):
        self._year = None
        self._history: list[dict] = []  # 最近 12 场

    # ── 赛后回调: 累积滚动窗口 ──────────────────────────────────

    def on_result(self, ctx: MatchContext):
        if self._year is not None and self._year != ctx.year:
            self._reset()
        self._year = ctx.year

        if ctx.stage != "小组赛":
            return
        if ctx.score_home is None or ctx.score_away is None:
            return

        h, a = ctx.odds_home, ctx.odds_away
        ratio = max(h, a) / min(h, a)
        has_fav = ratio >= 2.0

        is_upset = False
        if has_fav:
            weak_is_home = h > a
            if weak_is_home:
                weak_won = ctx.score_home > ctx.score_away
            else:
                weak_won = ctx.score_away > ctx.score_home
            is_upset = weak_won

        total = ctx.score_home + ctx.score_away
        is_under = total < 3
        is_draw = ctx.score_home == ctx.score_away

        self._history.append({
            "upset": is_upset,
            "has_fav": has_fav,
            "under": is_under,
            "draw": is_draw,
            "goals": total,
            "zero_goal": total == 0,
        })

        if len(self._history) > Thresholds.WINDOW:
            self._history.pop(0)

    # ── 信号分类 ─────────────────────────────────────────────────

    def _tier(self, rate: float, p10: float, p25: float, p75: float, p90: float) -> int:
        """返回信号强度: -2 -1 0 +1 +2 (负=偏低, 正=偏高)"""
        if rate > p90: return 2
        if rate > p75: return 1
        if rate < p10: return -2
        if rate < p25: return -1
        return 0

    def _running_rates(self):
        """计算三个维度的 12 场滚动率"""
        n = len(self._history)
        if n == 0:
            return 0, 0, 0, 0

        fav = [h for h in self._history if h["has_fav"]]
        upset_rate = sum(1 for h in fav if h["upset"]) / max(len(fav), 1)
        draw_rate = sum(1 for h in self._history if h["draw"]) / n
        under_rate = sum(1 for h in self._history if h["under"]) / n
        zero_rate = sum(1 for h in self._history if h["zero_goal"]) / n

        return upset_rate, draw_rate, under_rate, zero_rate

    # ── 决策 ─────────────────────────────────────────────────────

    def decide(self, ctx: MatchContext, bankroll: float) -> Optional[BetDecision]:
        if self._year is not None and self._year != ctx.year:
            self._reset()
            self._year = ctx.year

        if ctx.stage != "小组赛":
            return None
        if bankroll < self.stake_per_bet:
            return None
        if len(self._history) < Thresholds.WINDOW:
            return None

        upset_rate, draw_rate, under_rate, zero_rate = self._running_rates()

        # 信号分类
        upset_tier = self._tier(
            upset_rate, Thresholds.UPSET_P10, Thresholds.UPSET_P25,
            Thresholds.UPSET_P75, Thresholds.UPSET_P90)
        draw_tier = self._tier(
            draw_rate, Thresholds.DRAW_P10, Thresholds.DRAW_P10,
            Thresholds.DRAW_P75, Thresholds.DRAW_P90)
        under_tier = self._tier(
            under_rate, Thresholds.UNDER_P10, Thresholds.UNDER_P25,
            Thresholds.UNDER_P75, Thresholds.UNDER_P90)

        # 辅助信号: 0 球率异常高 → 进攻乏力型小球, 大球回归更可期
        zero_alert = zero_rate > Thresholds.ZERO_GOAL_ALERT

        # ── 决策逻辑 ──────────────────────────────────────────

        bet_on = None
        odds = 0.0
        confidence = 0.0
        stake_mult = 1.0

        # 非对称策略: 仅当某个维度偏离偏高时押注均值回归, 偏低时不下注
        # 因为"太冷→押热门回归"有正期望, "太平静→押冷门"持续亏损

        fav_side = "home" if ctx.odds_home <= ctx.odds_away else "away"
        fav_odds = min(ctx.odds_home, ctx.odds_away)

        # 爆冷率偏高 → 押热门回归
        if upset_tier > 0:
            bet_on = fav_side
            odds = fav_odds
            confidence = 0.25 + upset_tier * 0.15
            stake_mult = 2.0 if upset_tier == 2 else 1.0

        # 平局率偏高 → 押分胜负 (热门方向)
        elif draw_tier > 0:
            bet_on = fav_side
            odds = fav_odds
            confidence = 0.20 + draw_tier * 0.15
            stake_mult = 2.0 if draw_tier == 2 else 1.0

        # 两者都偏高 → 强共振, 加倍置信
        if upset_tier > 0 and draw_tier > 0:
            confidence += 0.10
            stake_mult += 0.5

        # 辅助: 0 球率异常高 → 沉闷后倾向热门反弹
        if zero_alert and bet_on in ("home", "away"):
            confidence += 0.05

        if bet_on is None:
            return None

        # 安全性检查
        if odds < 1.15 or odds > 4.5:
            return None

        stake = min(self.stake_per_bet * stake_mult, bankroll * 0.15)

        return BetDecision(
            bet_on=bet_on,
            stake=round(stake, 2),
            odds=odds,
            confidence=round(min(confidence, 0.65), 3),
        )
