"""
合并均值回归策略 — 五信号 + 动量合并，多维度均值回归押注。

核心设计:
  1. 双窗口: 全赛累计 + 12场滚动
  2. 对比 2006-2022 小组赛历史基线 (192场)
  3. 前 12 场观察, 第 13 场起下注
  4. 等利润动态注额: Stake = 100 / (Odds - 1)
  5. 仅强信号触发 (弱信号忽略)
  6. 多维度: SPF (胜平负) / 进球数 (0~5+) / 比分 (≤5球)

维度分层:
  Tier A (>8% 基线): 冷门率/平局率/1/2/3球率/0-1/1-2/1-0/0-0/2-1
    → 弱信号 +50%, 强信号 +61% 相对偏离
  Tier B (5-8% 基线): 0/4/5+球率/1-1/2-0/0-2
    → 弱信号 +80%, 强信号 +100% 相对偏离
  Tier C (<5% 基线): 其余比分
    → 仅全赛 +100% 偏离时辅助参考, 不独立触发

非对称设计: 仅在偏离偏高方向出手 (偏高→均值回归), 低偏离不下注。
"""

from .base import BaseStrategy, MatchContext, BetDecision
from typing import Optional


# ═══════════════════════════════════════════════════════════════════
# 历史基线 (2006-2022, 192 场小组赛)
# ═══════════════════════════════════════════════════════════════════

UPSET_BASELINE = 0.180    # 赔率差 ≥2x 时弱队胜出
DRAW_BASELINE = 0.219     # 双方打平

# 进球数分布
GOAL_BASELINES = {
    0: 0.094,   # 18/192
    1: 0.255,   # 49/192
    2: 0.240,   # 46/192
    3: 0.182,   # 35/192
    4: 0.120,   # 23/192
    5: 0.109,   # 21/192 (5+)
}

# 比分分布 (仅 ≤5 球, Tier A+B)
SCORE_BASELINES = {
    "0:1": 0.125, "1:2": 0.104, "1:0": 0.099, "0:0": 0.094, "2:1": 0.089,  # Tier A
    "1:1": 0.078, "2:0": 0.068, "0:2": 0.062,                                # Tier B
}

# 分层定义
TIER_A_BASELINE_MIN = 0.10   # >10%
TIER_B_BASELINE_MIN = 0.05   # 5-8%

# 阈值: (weak_multiplier, strong_multiplier)
TIER_A_THRESHOLDS = (1.50, 1.61)   # +50% weak, +61% strong
TIER_B_THRESHOLDS = (1.80, 2.00)   # +80% weak, +100% strong
TIER_C_THRESHOLD = 2.00            # +100% (仅全赛)

# 注额参数
TARGET_PROFIT = 100.0       # 每次获胜目标盈利 ¥100
BUDGET = 3000.0             # 小组赛总预算
MAX_STAKE_RATIO = 0.02      # 单注最大占资金 2% (进球/比分)
SPF_MAX_STAKE_RATIO = 0.05  # SPF 单注最大占资金 5% (热门低赔率需更高上限)
MIN_STAKE_RATIO = 0.001     # 单注最小占资金 0.1%

OBSERVE_WINDOW = 12         # 观察场次
ROLLING_WINDOW = 12         # 滚动窗口


# ═══════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════

def _get_tier(baseline: float) -> str:
    if baseline >= TIER_A_BASELINE_MIN:
        return "A"
    elif baseline >= TIER_B_BASELINE_MIN:
        return "B"
    return "C"


def _thresholds(baseline: float):
    """返回 (weak_mult, strong_mult) 根据基线分层"""
    tier = _get_tier(baseline)
    if tier == "A":
        return TIER_A_THRESHOLDS
    elif tier == "B":
        return TIER_B_THRESHOLDS
    else:
        return (TIER_C_THRESHOLD, TIER_C_THRESHOLD)


def _signal_strength(rate: float, baseline: float) -> int:
    """
    计算信号强度: 0=无信号, 1=弱信号, 2=强信号
    仅在 rate > baseline 时返回非零 (非对称设计)
    """
    if rate <= baseline:
        return 0
    weak_m, strong_m = _thresholds(baseline)
    if rate > baseline * strong_m:
        return 2
    if rate > baseline * weak_m:
        return 1
    return 0


def _calc_stake(odds: float, bankroll: float, max_ratio: float = MAX_STAKE_RATIO) -> float:
    """等利润注额: Stake = TARGET / (Odds - 1), 带上下限"""
    if odds <= 1.01:
        return 0.0
    raw = TARGET_PROFIT / (odds - 1)
    capped = min(raw, bankroll * max_ratio)
    capped = max(capped, bankroll * MIN_STAKE_RATIO)
    return round(capped, 2)


# ═══════════════════════════════════════════════════════════════════
# 策略
# ═══════════════════════════════════════════════════════════════════

class MergedMeanReversionStrategy(BaseStrategy):
    """合并均值回归策略"""

    name = "merged_mean_reversion"
    description = "合并均值回归: 双窗口 + 多维度 + 等利润注额 + 仅强信号"

    def __init__(self):
        self._reset()

    def _reset(self):
        self._year = None
        self._full: list[dict] = []       # 全赛历史
        self._rolling: list[dict] = []    # 最近 12 场

    # ── 赛后回调 ──────────────────────────────────────────────────

    def on_result(self, ctx: MatchContext):
        if self._year is not None and self._year != ctx.year:
            self._reset()
        self._year = ctx.year

        if ctx.stage != "小组赛":
            return
        if ctx.score_home is None or ctx.score_away is None:
            return

        h, a = ctx.odds_home, ctx.odds_away
        total = ctx.score_home + ctx.score_away
        is_draw = ctx.score_home == ctx.score_away
        score_key = f"{ctx.score_home}:{ctx.score_away}"

        # 冷门判定: 赔率差 ≥2x 时弱队胜出
        ratio = max(h, a) / min(h, a) if min(h, a) > 0 else 999
        has_fav = ratio >= 2.0
        is_upset = False
        if has_fav:
            weak_is_home = h > a
            if weak_is_home:
                is_upset = ctx.score_home > ctx.score_away
            else:
                is_upset = ctx.score_away > ctx.score_home

        # 进球数归入 0-5 (5 代表 5+)
        goals_bucket = min(total, 5)

        entry = {
            "upset": is_upset,
            "has_fav": has_fav,
            "draw": is_draw,
            "goals": goals_bucket,
            "score": score_key if total <= 5 else None,  # 仅 ≤5 球比分
        }
        self._full.append(entry)
        self._rolling.append(entry)
        if len(self._rolling) > ROLLING_WINDOW:
            self._rolling.pop(0)

    # ── 统计计算 ──────────────────────────────────────────────────

    def _compute_rates(self, history: list[dict]) -> dict:
        """从历史记录计算所有维度的比率"""
        n = len(history)
        if n == 0:
            return {}

        fav_matches = [h for h in history if h["has_fav"]]
        n_fav = max(len(fav_matches), 1)

        # SPF
        upset_rate = sum(1 for h in fav_matches if h["upset"]) / n_fav
        draw_rate = sum(1 for h in history if h["draw"]) / n

        # 进球分布
        goal_counts = {g: 0 for g in range(6)}
        for h in history:
            goal_counts[h["goals"]] += 1
        goal_rates = {g: goal_counts[g] / n for g in range(6)}

        # 比分分布 (仅统计有 ≤5 球比分的场次)
        score_entries = [h["score"] for h in history if h["score"] is not None]
        n_scores = max(len(score_entries), 1)
        score_rates = {}
        for sk in SCORE_BASELINES:
            score_rates[sk] = sum(1 for s in score_entries if s == sk) / n_scores

        return {
            "n": n,
            "upset_rate": upset_rate,
            "draw_rate": draw_rate,
            "goal_rates": goal_rates,
            "score_rates": score_rates,
        }

    def _rates(self):
        """返回 (full_rates, rolling_rates)"""
        return self._compute_rates(self._full), self._compute_rates(self._rolling)

    # ── 维度检查 ──────────────────────────────────────────────────

    def _check_spf_upset(self, full_r: dict, roll_r: dict) -> Optional[BetDecision]:
        """检查冷门率维度 → 押热门回归"""
        baseline = UPSET_BASELINE
        f_rate = full_r.get("upset_rate", 0)
        r_rate = roll_r.get("upset_rate", 0)
        if len(self._rolling) < ROLLING_WINDOW:
            r_rate = f_rate

        f_sig = _signal_strength(f_rate, baseline)
        r_sig = _signal_strength(r_rate, baseline)
        combined = _combine_signal(f_sig, r_sig)
        if combined == 0:
            return None

        confidence = 0.25 + combined * 0.15
        return BetDecision(
            bet_type="spf", bet_on="__fav__",  # 占位, 调用方填入方向
            stake=0, odds=0,
            confidence=round(confidence, 3),
            reason=f"冷门率偏高(全赛{f_rate:.0%} 滚动{r_rate:.0%} vs 基线{baseline:.0%})→押热门回归",
        )

    def _check_spf_draw(self, full_r: dict, roll_r: dict) -> Optional[BetDecision]:
        """检查平局率维度 → 押分胜负 (热门方向)"""
        baseline = DRAW_BASELINE
        f_rate = full_r.get("draw_rate", 0)
        r_rate = roll_r.get("draw_rate", 0)
        if len(self._rolling) < ROLLING_WINDOW:
            r_rate = f_rate

        f_sig = _signal_strength(f_rate, baseline)
        r_sig = _signal_strength(r_rate, baseline)
        combined = _combine_signal(f_sig, r_sig)
        if combined == 0:
            return None

        confidence = 0.20 + combined * 0.15
        return BetDecision(
            bet_type="spf", bet_on="__fav__",
            stake=0, odds=0,
            confidence=round(confidence, 3),
            reason=f"平局率偏高(全赛{f_rate:.0%} 滚动{r_rate:.0%} vs 基线{baseline:.0%})→押分胜负",
        )

    def _check_goals(self, full_r: dict, roll_r: dict) -> list[BetDecision]:
        """检查进球数各维度 → 押最欠代表的进球数回归"""
        decisions = []
        f_goals = full_r.get("goal_rates", {})
        r_goals = roll_r.get("goal_rates", {})
        if len(self._rolling) < ROLLING_WINDOW:
            r_goals = f_goals

        over_signals = {}  # goal -> combined_signal
        for g in range(6):
            baseline = GOAL_BASELINES[g]
            f_rate = f_goals.get(g, 0)
            r_rate = r_goals.get(g, 0)
            f_sig = _signal_strength(f_rate, baseline)
            r_sig = _signal_strength(r_rate, baseline)
            combined = _combine_signal(f_sig, r_sig)
            if combined >= 2:  # 仅强信号
                over_signals[g] = (combined, f_rate, r_rate)

        if not over_signals:
            return decisions

        # 找最欠代表的进球数 (负偏离最大) 作为下注目标
        under_targets = []
        for g in range(6):
            baseline = GOAL_BASELINES[g]
            f_rate = f_goals.get(g, 0)
            if g in over_signals:
                continue  # 已偏高, 不押
            deviation = (f_rate - baseline) / baseline  # 负值 = 欠代表
            under_targets.append((deviation, g, baseline, f_rate))

        under_targets.sort()  # 最负的排最前
        if not under_targets:
            return decisions

        # 为每个偏高信号生成一个下注推荐 (押最欠代表的进球数)
        for over_g, (sig, f_rate, r_rate) in over_signals.items():
            best_dev, best_g, best_base, best_f_rate = under_targets[0]
            confidence = 0.18 + sig * 0.12
            decisions.append(BetDecision(
                bet_type="goals", bet_on=str(best_g),
                stake=0, odds=0,
                confidence=round(confidence, 3),
                reason=f"进球{over_g}偏高(全赛{f_rate:.0%} 滚动{r_rate:.0%})→押进球{best_g}回归(欠代表{best_f_rate:.0%}vs{best_base:.0%})",
            ))
        return decisions

    def _check_scores(self, full_r: dict, roll_r: dict,
                      goal_signals: list[BetDecision]) -> list[BetDecision]:
        """检查比分维度 → 仅在与进球信号方向一致时触发"""
        decisions = []
        f_scores = full_r.get("score_rates", {})
        r_scores = roll_r.get("score_rates", {})
        if len(self._rolling) < ROLLING_WINDOW:
            r_scores = f_scores

        # 收集进球信号指向的总进球数
        goal_targets = set()
        for d in goal_signals:
            try:
                goal_targets.add(int(d.bet_on))
            except ValueError:
                pass

        for sk, baseline in SCORE_BASELINES.items():
            tier = _get_tier(baseline)
            if tier == "C":
                continue  # Tier C 不独立触发

            f_rate = f_scores.get(sk, 0)
            r_rate = r_scores.get(sk, 0)

            f_sig = _signal_strength(f_rate, baseline)
            r_sig = _signal_strength(r_rate, baseline)
            combined = _combine_signal(f_sig, r_sig)
            if combined < 2:
                continue

            # 至少需要实际出现 3 次才触发 (12 场窗口 1 场=8.3pp, 2 次可能是随机波动)
            full_n = full_r.get("n", 0)
            if f_rate * full_n < 3:
                continue

            # 该比分偏高, 找替代比分
            # 提取比分进球数
            try:
                parts = sk.split(":")
                goal_total = int(parts[0]) + int(parts[1])
            except (ValueError, IndexError):
                continue

            # 优先推荐与进球信号方向一致的比分
            candidates = []
            for cand_sk, cand_base in SCORE_BASELINES.items():
                if cand_sk == sk:
                    continue
                try:
                    cp = cand_sk.split(":")
                    cand_goals = int(cp[0]) + int(cp[1])
                except (ValueError, IndexError):
                    continue

                cand_f_rate = f_scores.get(cand_sk, 0)
                deviation = (cand_f_rate - cand_base) / cand_base  # 负值 = 欠代表

                # 如果与进球信号方向一致, 加权
                bonus = 1.5 if cand_goals in goal_targets else 1.0
                score = deviation * bonus
                candidates.append((score, cand_sk, cand_base, cand_f_rate, cand_goals))

            candidates.sort()
            if not candidates:
                continue

            best_score, best_sk, best_base, best_f_rate, _ = candidates[0]
            confidence = 0.15 + combined * 0.10
            decisions.append(BetDecision(
                bet_type="score", bet_on=best_sk,
                stake=0, odds=0,
                confidence=round(confidence, 3),
                reason=f"比分{sk}偏高(全赛{f_rate:.0%} 滚动{r_rate:.0%})→押{best_sk}回归",
            ))

        return decisions

    # ── 决策 ──────────────────────────────────────────────────────

    def decide(self, ctx: MatchContext, bankroll: float) -> Optional[BetDecision]:
        """返回最强信号 (向后兼容)"""
        decisions = self.decide_multi(ctx, bankroll)
        if not decisions:
            return None
        return max(decisions, key=lambda d: d.confidence)

    def decide_multi(self, ctx: MatchContext, bankroll: float) -> list[BetDecision]:
        """返回所有触发的下注推荐 (多维度)"""
        if self._year is not None and self._year != ctx.year:
            self._reset()
            self._year = ctx.year

        if ctx.stage != "小组赛":
            return []
        if len(self._full) < OBSERVE_WINDOW:
            return []
        if bankroll < TARGET_PROFIT * 0.3:
            return []

        full_r, roll_r = self._rates()
        decisions = []

        # 1. SPF: 冷门率
        d_upset = self._check_spf_upset(full_r, roll_r)
        if d_upset:
            decisions.append(d_upset)

        # 2. SPF: 平局率
        d_draw = self._check_spf_draw(full_r, roll_r)
        if d_draw:
            decisions.append(d_draw)

        # 3. 进球数 (最先检查, 为比分信号提供方向)
        goal_decisions = self._check_goals(full_r, roll_r)
        decisions.extend(goal_decisions)

        # 4. 比分 (依赖进球信号方向)
        score_decisions = self._check_scores(full_r, roll_r, goal_decisions)
        decisions.extend(score_decisions)

        # 填入实际赔率和注额
        for d in decisions:
            self._fill_odds_and_stake(d, ctx, bankroll)

        # 过滤无效注额，合并重复 SPF 推荐 (冷门率+平局率可能同时触发)
        seen_spf = {}
        merged = []
        for d in decisions:
            if d.stake <= 0 or d.odds <= 1.01:
                continue
            if d.bet_type == "spf":
                if d.bet_on in seen_spf:
                    prev = seen_spf[d.bet_on]
                    if d.confidence > prev.confidence:
                        prev.confidence = d.confidence
                    prev.reason += "；同时" + d.reason
                    continue
                seen_spf[d.bet_on] = d
            merged.append(d)
        return merged

    def _fill_odds_and_stake(self, d: BetDecision, ctx: MatchContext, bankroll: float):
        """根据 bet_type 和 bet_on 填入实际赔率和等利润注额"""
        if d.bet_type == "spf":
            fav_odds = min(ctx.odds_home, ctx.odds_away)
            fav_side = "home" if ctx.odds_home <= ctx.odds_away else "away"
            d.bet_on = fav_side
            d.odds = fav_odds
        elif d.bet_type == "goals":
            d.odds = ctx.goals_odds.get(d.bet_on, 0)
        elif d.bet_type == "score":
            d.odds = ctx.score_odds.get(d.bet_on, 0)

        if d.odds <= 1.01:
            d.stake = 0
            return

        max_r = SPF_MAX_STAKE_RATIO if d.bet_type == "spf" else MAX_STAKE_RATIO
        d.stake = _calc_stake(d.odds, bankroll, max_r)

    # ── 上下文查询 ────────────────────────────────────────────────

    def context_info(self) -> dict:
        """返回当前统计上下文，供前端展示"""
        full_r, roll_r = self._rates()
        return {
            "phase": "observing" if len(self._full) < OBSERVE_WINDOW else "betting",
            "observed_count": len(self._full),
            "observe_target": OBSERVE_WINDOW,
            "full_window": {
                "n": full_r.get("n", 0),
                "upset_rate": round(full_r.get("upset_rate", 0), 3),
                "draw_rate": round(full_r.get("draw_rate", 0), 3),
                "goal_rates": {str(k): round(v, 3) for k, v in full_r.get("goal_rates", {}).items()},
            },
            "rolling_window": {
                "n": roll_r.get("n", 0),
                "upset_rate": round(roll_r.get("upset_rate", 0), 3),
                "draw_rate": round(roll_r.get("draw_rate", 0), 3),
                "goal_rates": {str(k): round(v, 3) for k, v in roll_r.get("goal_rates", {}).items()},
            },
            "baselines": {
                "upset": UPSET_BASELINE,
                "draw": DRAW_BASELINE,
                "goals": {str(k): v for k, v in GOAL_BASELINES.items()},
            },
            "thresholds": {
                "tier_a": {"weak": TIER_A_THRESHOLDS[0], "strong": TIER_A_THRESHOLDS[1]},
                "tier_b": {"weak": TIER_B_THRESHOLDS[0], "strong": TIER_B_THRESHOLDS[1]},
            },
        }


# ═══════════════════════════════════════════════════════════════════
# 信号合并逻辑
# ═══════════════════════════════════════════════════════════════════

def _combine_signal(full_sig: int, roll_sig: int) -> int:
    """
    合并双窗口信号强度。
    返回: 0=不下注, 1=弱信号(不下注), 2=强信号(下注)

    触发规则:
      - 双强 → 2
      - 一强一弱 → 2
      - 双弱 → 0 (忽略)
      - 单一信号 → 0 (忽略)
    """
    if full_sig == 2 and roll_sig == 2:
        return 2
    if full_sig == 2 and roll_sig == 1:
        return 2
    if full_sig == 1 and roll_sig == 2:
        return 2
    return 0
