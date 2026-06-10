"""
OddsPortal 世界杯赔率爬虫 — 使用 Playwright 抓取真实历史赔率。
支持 Bet365 作为默认赔率源。
"""
import asyncio
import re
from playwright.async_api import async_playwright

ODDS_PORTAL_URLS = {
    2022: "https://www.oddsportal.com/football/world/world-cup-2022/results/",
    2018: "https://www.oddsportal.com/football/world/world-cup-2018/results/",
    2014: "https://www.oddsportal.com/football/world/world-cup-2014/results/",
    2010: "https://www.oddsportal.com/football/world/world-cup-2010/results/",
}


async def scrape_year(year: int) -> list[dict]:
    """爬取指定年份世界杯的完整比赛+赔率数据"""
    url = ODDS_PORTAL_URLS.get(year)
    if not url:
        print(f"  [跳过] {year}: 无 OddsPortal URL")
        return []

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        page.set_default_timeout(30000)

        try:
            print(f"  正在加载: {url}")
            await page.goto(url, wait_until="domcontentloaded")

            # 等待表格加载
            await page.wait_for_selector("div[-set]", timeout=15000)
            await page.wait_for_timeout(3000)

            # 切换到 Bet365 赔率（如果可用）
            try:
                bet365_btn = page.locator("text=Bet365").first
                if await bet365_btn.is_visible():
                    await bet365_btn.click()
                    await page.wait_for_timeout(2000)
                    print("  [OK] 切换到 Bet365 赔率")
            except Exception:
                print("  [提示] 使用默认赔率源")

            # 提取数据行
            rows = page.locator("div[set].eventRow")
            count = await rows.count()
            print(f"  找到 {count} 行数据")

            for i in range(count):
                try:
                    row = rows.nth(i)

                    # 时间
                    time_el = row.locator("div.date")
                    time_text = ""
                    try:
                        time_text = await time_el.text_content() or ""
                    except:
                        pass

                    # 队伍名 + 比分
                    participants = row.locator("div.participant")
                    participant_count = await participants.count()
                    if participant_count < 2:
                        continue

                    home_text = (await participants.nth(0).text_content() or "").strip()
                    away_text = (await participants.nth(1).text_content() or "").strip()

                    # 比分
                    score_el = row.locator("div.score")
                    score_text = ""
                    try:
                        score_text = (await score_el.text_content() or "").strip()
                    except:
                        pass

                    # 赔率 — 三个 odds 列: Home, Draw, Away
                    odds_els = row.locator("div.odds")
                    odds_count = await odds_els.count()
                    if odds_count < 3:
                        continue

                    odds_home = _parse_odds(await odds_els.nth(0).text_content() or "")
                    odds_draw = _parse_odds(await odds_els.nth(1).text_content() or "")
                    odds_away = _parse_odds(await odds_els.nth(2).text_content() or "")

                    if odds_home is None or odds_draw is None or odds_away is None:
                        continue

                    # 解析比分
                    score_home, score_away = _parse_score(score_text)

                    # 推断阶段（基于日期顺序）
                    stage = _infer_stage(0, count, i)  # 简化逻辑

                    results.append({
                        "year": year,
                        "stage": stage,
                        "date": time_text.strip(),
                        "team_home": _clean_team_name(home_text),
                        "team_away": _clean_team_name(away_text),
                        "score_home": score_home,
                        "score_away": score_away,
                        "odds_home": odds_home,
                        "odds_draw": odds_draw,
                        "odds_away": odds_away,
                        "provider": "Bet365",
                    })
                except Exception as e:
                    continue

            print(f"  [OK] 成功解析 {len(results)} 场")

        except Exception as e:
            print(f"  [错误] {year}: {e}")
        finally:
            await browser.close()

    return results


def _parse_odds(text: str) -> float | None:
    """赔率文本转浮点数"""
    text = text.strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _parse_score(text: str) -> tuple[int | None, int | None]:
    """解析比分: '2:1' → (2, 1)"""
    text = text.strip().replace(":", "-")
    m = re.match(r"(\d+)\s*[-:]\s*(\d+)", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _clean_team_name(name: str) -> str:
    """清理队伍名称"""
    # 去掉年月日等后缀
    name = re.sub(r"\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}", "", name)
    name = name.strip()
    return name


def _infer_stage(total: int, count: int, index: int) -> str:
    """根据位置推断比赛阶段"""
    # 小组赛约48场，淘汰赛约16场
    # 简化: 前 ~80% 是小组赛，后面依次是淘汰赛
    ratio = index / max(count, 1)
    if ratio > 0.93:
        return "决赛"
    elif ratio > 0.89:
        return "季军赛"
    elif ratio > 0.82:
        return "半决赛"
    elif ratio > 0.72:
        return "8强"
    elif ratio > 0.60:
        return "16强"
    return "小组赛"


async def scrape_and_import(years: list[int] = None):
    """爬取并导入到数据库"""
    from models import get_session, Match, Odds

    target_years = years or list(ODDS_PORTAL_URLS.keys())
    session = get_session()

    total_inserted = 0

    try:
        for year in target_years:
            print(f"\n爬取 {year} 世界杯...")
            matches = await scrape_year(year)

            for m in matches:
                # 检查是否已存在
                existing = (
                    session.query(Match)
                    .filter(
                        Match.year == m["year"],
                        Match.team_home == m["team_home"],
                        Match.team_away == m["team_away"],
                    )
                    .first()
                )
                if existing:
                    # 更新比分 + 添加/更新赔率
                    if m["score_home"] is not None:
                        existing.score_home = m["score_home"]
                        existing.score_away = m["score_away"]

                    # 更新或添加 Bet365 赔率
                    existing_odds = (
                        session.query(Odds)
                        .filter(Odds.match_id == existing.id, Odds.provider == m["provider"])
                        .first()
                    )
                    if existing_odds:
                        existing_odds.odds_home = m["odds_home"]
                        existing_odds.odds_draw = m["odds_draw"]
                        existing_odds.odds_away = m["odds_away"]
                    else:
                        session.add(Odds(
                            match_id=existing.id,
                            provider=m["provider"],
                            odds_home=m["odds_home"],
                            odds_draw=m["odds_draw"],
                            odds_away=m["odds_away"],
                        ))
                    continue

                # 新增比赛
                match = Match(
                    year=m["year"],
                    stage=m["stage"],
                    team_home=m["team_home"],
                    team_away=m["team_away"],
                    score_home=m["score_home"],
                    score_away=m["score_away"],
                )
                session.add(match)
                session.flush()

                session.add(Odds(
                    match_id=match.id,
                    provider=m["provider"],
                    odds_home=m["odds_home"],
                    odds_draw=m["odds_draw"],
                    odds_away=m["odds_away"],
                ))
                total_inserted += 1

            session.commit()
            print(f"  [存档] {year} 年数据已入库")

        print(f"\n总计新增 {total_inserted} 场比赛（含真实赔率）")

    except Exception as e:
        session.rollback()
        print(f"导入失败: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    asyncio.run(scrape_and_import())
