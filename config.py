import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = f"sqlite:///{BASE_DIR}/worldcup.db"

# 历届世界杯年份
WORLDCUP_YEARS = list(range(1930, 2023, 4))
# 去掉因二战取消的 1942/1946
WORLDCUP_YEARS = [y for y in WORLDCUP_YEARS if y not in (1942, 1946)]

# 默认投注参数
DEFAULT_BANKROLL = 10000  # 初始资金
DEFAULT_STAKE = 100       # 每次固定投注额
