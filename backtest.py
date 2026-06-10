from dataclasses import dataclass, field
from typing import Optional

from models import get_session, Match, Odds, Bet, BacktestRun
from strategies.base import MatchContext
from strategies.favorite import BuyFavoriteStrategy
from strategies.kelly import KellyStrategy
from strategies.strong_favorite import StrongFavoriteStrategy
from strategies.draw_hunter import DrawHunterStrategy
from strategies.knockout_underdog import KnockoutUnderdogStrategy
from strategies.odds_range import OddsRangeStrategy
from strategies.value_bet import ValueBetStrategy
from strategies.momentum import MomentumStrategy
from strategies.five_signal import FiveSignalStrategy


STRATEGY_REGISTRY = {
    "favorite": BuyFavoriteStrategy,
    "kelly": KellyStrategy,
    "strong_favorite": StrongFavoriteStrategy,
    "draw_hunter": DrawHunterStrategy,
    "knockout_underdog": KnockoutUnderdogStrategy,
    "odds_range": OddsRangeStrategy,
    "value_bet": ValueBetStrategy,
    "momentum": MomentumStrategy,
    "five_signal": FiveSignalStrategy,
}


@dataclass
class BacktestResult:
    run_id: int
    strategy_name: str
    initial_bankroll: float
    final_bankroll: float
    total_return: float
    total_bets: int
    wins: int
    pushes: int
    win_rate: float
    max_drawdown: float
    equity_curve: list


KNOCKOUT_STAGES = {"16强", "8强", "半决赛", "季军赛", "决赛"}


def _match_stage_group(stage: str) -> str:
    """分组赛 or 淘汰赛"""
    return "淘汰赛" if stage in KNOCKOUT_STAGES else "小组赛"


def run_backtest(
    strategy_name: str,
    start_year: int,
    end_year: int,
    stage: str = "全部",
    initial_bankroll: float = 10000,
    stake_per_bet: float = 100,
) -> BacktestResult:
    if strategy_name not in STRATEGY_REGISTRY:
        raise ValueError(f"未知策略: {strategy_name}，可用策略: {list(STRATEGY_REGISTRY.keys())}")

    strategy_cls = STRATEGY_REGISTRY[strategy_name]
    strategy = strategy_cls(stake_per_bet=stake_per_bet)

    session = get_session()
    try:
        q = (
            session.query(Match)
            .filter(Match.year >= start_year, Match.year <= end_year)
            .filter(Match.score_home.isnot(None))
        )
        # 阶段筛选
        if stage == "小组赛":
            q = q.filter(~Match.stage.in_(KNOCKOUT_STAGES))
        elif stage == "淘汰赛":
            q = q.filter(Match.stage.in_(KNOCKOUT_STAGES))

        matches = q.order_by(Match.year, Match.id).all()

        if not matches:
            raise ValueError(f"{start_year}-{end_year} 没有可用的比赛数据，请先爬取数据")

        bankroll = initial_bankroll
        peak = initial_bankroll
        max_drawdown = 0.0
        equity_curve = []

        # 先创建 Run 记录
        run = BacktestRun(
            strategy_name=strategy_name,
            start_year=start_year,
            end_year=end_year,
            stage=stage,
            initial_bankroll=initial_bankroll,
            final_bankroll=initial_bankroll,
            total_bets=0,
            wins=0,
            pushes=0,
            win_rate=0,
            max_drawdown=0,
        )
        session.add(run)
        session.flush()

        total_bets = 0
        wins = 0

        for match in matches:
            odds_list = match.odds
            if not odds_list:
                equity_curve.append({
                    "match_label": f"[{match.stage}] {match.year} {match.team_home} vs {match.team_away}",
                    "match_id": match.id,
                    "stage_group": _match_stage_group(match.stage),
                    "bankroll": round(bankroll, 2),
                    "bet": 0,
                    "bet_on": "",
                    "odds_taken": 0,
                    "result": "skip",
                    "profit": 0,
                })
                strategy.on_result(ctx)
                continue

            odds = odds_list[0]
            ctx = MatchContext(
                match_id=match.id,
                year=match.year,
                stage=match.stage,
                team_home=match.team_home,
                team_away=match.team_away,
                odds_home=odds.odds_home,
                odds_draw=odds.odds_draw,
                odds_away=odds.odds_away,
                score_home=match.score_home,
                score_away=match.score_away,
            )

            decision = strategy.decide(ctx, bankroll)

            if decision is None:
                equity_curve.append({
                    "match_label": f"[{match.stage}] {match.year} {match.team_home} vs {match.team_away}",
                    "match_id": match.id,
                    "stage_group": _match_stage_group(match.stage),
                    "bankroll": round(bankroll, 2),
                    "bet": 0,
                    "bet_on": "",
                    "odds_taken": 0,
                    "result": "skip",
                    "profit": 0,
                })
                strategy.on_result(ctx)
                continue

            bet_on = decision.bet_on
            stake = decision.stake
            odds_taken = decision.odds

            # 判定结果（1X2 欧赔，无走水）
            if bet_on == "home":
                won = match.score_home > match.score_away
            elif bet_on == "away":
                won = match.score_away > match.score_home
            else:  # draw
                won = match.score_home == match.score_away

            if won:
                result = "win"
                profit = stake * (odds_taken - 1)
            else:
                result = "lose"
                profit = -stake

            bankroll += profit
            total_bets += 1
            if result == "win":
                wins += 1

            # 持久化 Bet
            session.add(Bet(
                match_id=match.id,
                run_id=run.id,
                strategy_name=strategy_name,
                bet_on=bet_on,
                stake=round(stake, 2),
                odds_taken=odds_taken,
                result=result,
                profit=round(profit, 2),
            ))

            equity_curve.append({
                "match_label": f"[{match.stage}] {match.year} {match.team_home} vs {match.team_away}",
                "match_id": match.id,
                "stage_group": _match_stage_group(match.stage),
                "bankroll": round(bankroll, 2),
                "bet": round(stake, 2),
                "bet_on": bet_on,
                "odds_taken": odds_taken,
                "result": result,
                "profit": round(profit, 2),
                "score": f"{match.score_home}-{match.score_away}",
            })

            # 最大回撤
            if bankroll > peak:
                peak = bankroll
            drawdown = (peak - bankroll) / peak if peak > 0 else 0
            if drawdown > max_drawdown:
                max_drawdown = drawdown

            # 赛后回调：让策略累积锦标赛统计
            strategy.on_result(ctx)

        # 更新 Run 记录
        run.final_bankroll = round(bankroll, 2)
        run.total_bets = total_bets
        run.wins = wins
        run.pushes = 0
        run.win_rate = round(wins / total_bets, 4) if total_bets > 0 else 0
        run.max_drawdown = round(max_drawdown, 4)
        session.commit()

        return BacktestResult(
            run_id=run.id,
            strategy_name=strategy_name,
            initial_bankroll=initial_bankroll,
            final_bankroll=round(bankroll, 2),
            total_return=round((bankroll - initial_bankroll) / initial_bankroll, 4),
            total_bets=total_bets,
            wins=wins,
            pushes=0,
            win_rate=round(wins / total_bets, 4) if total_bets > 0 else 0,
            max_drawdown=round(max_drawdown, 4),
            equity_curve=equity_curve,
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
