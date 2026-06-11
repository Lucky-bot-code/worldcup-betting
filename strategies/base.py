from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MatchContext:
    """策略决策时使用的比赛上下文"""
    match_id: int
    year: int
    stage: str
    team_home: str
    team_away: str
    odds_home: float
    odds_draw: float
    odds_away: float
    score_home: int | None = None   # 回测中已知，仅 on_result 使用
    score_away: int | None = None   # decide() 不应依赖此字段（前瞻偏差）
    # 多维度赔率（竞彩支持）
    goals_odds: dict[str, float] = field(default_factory=dict)   # {"0": 8.5, "1": 4.2, ... "5": 12.0}
    score_odds: dict[str, float] = field(default_factory=dict)   # {"1:0": 6.5, "2:1": 8.0, ...}


@dataclass
class BetDecision:
    bet_type: str = "spf"  # spf / goals / score
    bet_on: str = ""       # home/draw/away / "0"~"5" / "1:0"等
    stake: float = 0.0     # 投注金额
    odds: float = 0.0      # 所取赔率
    confidence: float = 0.0  # 信心度 0~1
    reason: str = ""       # 触发原因简述


class BaseStrategy:
    """投注策略基类"""

    name: str = "base"
    description: str = ""

    def decide(self, ctx: MatchContext, bankroll: float) -> Optional[BetDecision]:
        """
        根据比赛上下文和当前资金，决定是否投注及如何投注。
        返回 None 表示不下注。
        """
        raise NotImplementedError

    def on_result(self, ctx: MatchContext):
        """
        赛后回调：每场比赛出结果后调用，ctx 含比分。
        子类重写以累积锦标赛统计数据（如冷门率、平局率）。
        """
        pass
