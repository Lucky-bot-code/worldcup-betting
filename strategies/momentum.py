"""
锦标赛动量策略：统计前 N 场小组赛的冷门/平局分布,
与历史基准对比后, 确定本届"情绪方向", 后续统一按均值回归押注。

核心逻辑:
  1. 前 N 场建立基线（观察期不下注）
  2. 观察期冷门率 > 历史均值 → "冷门潮" → 后续押热门（冷门率回归均值）
  3. 观察期冷门率 < 历史均值 → "过于平静" → 后续押下盘（冷门率回归均值）
  4. 观察期平局率异常 → 押分胜负方向

与现有策略的本质区别: 不是每场独立看赔率,
而是把前 N 场的分布作为一个"锦标赛情绪指标", 单向押注。
"""

from .base import BaseStrategy, MatchContext, BetDecision
from typing import Optional


class MomentumStrategy(BaseStrategy):
    """锦标赛动量策略 — 观察期定方向, 均值回归押注"""

    name = "momentum"
    description = "锦标赛动量：前N场冷门率偏离基线时，后续反向押注均值回归"

    def __init__(
        self,
        observation_window: int = 8,
        favorite_threshold: float = 2.5,
        upset_baseline: float = 0.42,
        draw_baseline: float = 0.22,
        upset_margin: float = 0.10,
        stake_per_bet: float = 100,
        max_stake_mult: float = 1.5,
    ):
        self.observation_window = observation_window
        self.favorite_threshold = favorite_threshold
        self.upset_baseline = upset_baseline
        self.draw_baseline = draw_baseline
        self.upset_margin = upset_margin
        self.stake_per_bet = stake_per_bet
        self.max_stake_mult = max_stake_mult
        self.reset_state()

    # ── 锦标赛状态 ──────────────────────────────────────────────

    def reset_state(self):
        self._year = None
        self._phase = "observing"       # observing | bet_fav | bet_dog | skip
        self._obs_upsets = 0
        self._obs_clear_fav = 0
        self._obs_draws = 0
        self._obs_total = 0
        self._matches_seen = 0

    def _detect_year_change(self, year: int) -> bool:
        return self._year is not None and self._year != year

    def _enter_betting_phase(self):
        """观察期结束, 根据统计确定下注方向"""
        upset_rate = self._obs_upsets / max(self._obs_clear_fav, 1)
        draw_rate = self._obs_draws / max(self._obs_total, 1)

        # 方向判断
        if upset_rate > self.upset_baseline + self.upset_margin:
            # 冷门太多 → 均值回归押热门
            self._phase = "bet_fav"
            self._reason = f"冷门潮(冷门率{upset_rate:.0%}>{self.upset_baseline:.0%}+{self.upset_margin:.0%})→押热门回归"
        elif upset_rate < self.upset_baseline - self.upset_margin:
            # 过于平静 → 均值回归押冷门
            self._phase = "bet_dog"
            self._reason = f"过于平静(冷门率{upset_rate:.0%}<{self.upset_baseline:.0%}-{self.upset_margin:.0%})→押下盘爆破"
        elif draw_rate > self.draw_baseline + 0.13:
            # 平局太多 → 押分胜负（热门方向）
            self._phase = "bet_fav"
            self._reason = f"平局潮(平局率{draw_rate:.0%}>{self.draw_baseline:.0%}+13%)→押分胜负"
        else:
            # 正常范围, 不下注
            self._phase = "skip"
            self._reason = f"正常范围(冷门率{upset_rate:.0%}vs{self.upset_baseline:.0%})→观望"

    # ── 赛后回调 ────────────────────────────────────────────────

    def on_result(self, ctx: MatchContext):
        if self._detect_year_change(ctx.year):
            self.reset_state()
        self._year = ctx.year

        if ctx.score_home is None or ctx.score_away is None:
            return

        self._matches_seen += 1

        is_draw = ctx.score_home == ctx.score_away

        # 热门从主客队中选
        fav_odds = min(ctx.odds_home, ctx.odds_away)
        fav_side = "home" if ctx.odds_home <= ctx.odds_away else "away"

        if self._phase == "observing":
            self._obs_total += 1
            if is_draw:
                self._obs_draws += 1
            if fav_odds <= self.favorite_threshold:
                self._obs_clear_fav += 1
                fav_won = (fav_side == "home" and ctx.score_home > ctx.score_away) or \
                          (fav_side == "away" and ctx.score_away > ctx.score_home)
                if not fav_won:
                    self._obs_upsets += 1

            if self._obs_total >= self.observation_window:
                self._enter_betting_phase()

    # ── 决策 ────────────────────────────────────────────────────

    def decide(self, ctx: MatchContext, bankroll: float) -> Optional[BetDecision]:
        if self._detect_year_change(ctx.year):
            self.reset_state()
            self._year = ctx.year

        if bankroll < self.stake_per_bet:
            return None

        # 观察期或 skip 模式 → 不下注
        if self._phase in ("observing", "skip"):
            return None

        # 仅小组赛下注
        if ctx.stage != "小组赛":
            return None

        # 热门方向（仅主客队）
        fav_odds = min(ctx.odds_home, ctx.odds_away)
        fav_side = "home" if ctx.odds_home <= ctx.odds_away else "away"

        if fav_odds > self.favorite_threshold:
            return None

        # 按方向下注
        if self._phase == "bet_fav":
            bet_on = fav_side
            odds = fav_odds
        elif self._phase == "bet_dog":
            # 押下盘（赔率更高的一方）
            if ctx.odds_home > ctx.odds_away:
                bet_on, odds = "home", ctx.odds_home
            else:
                bet_on, odds = "away", ctx.odds_away
            if odds > 5.0:
                return None
        else:
            return None

        # 安全性检查
        if odds < 1.15 or odds > 4.5:
            return None

        stake = min(self.stake_per_bet, bankroll * 0.15)

        return BetDecision(
            bet_on=bet_on,
            stake=round(stake, 2),
            odds=odds,
            confidence=0.35,
        )
