from datetime import datetime, timedelta
from functools import lru_cache

# 2025年及2026年美国联邦假期（arXiv 不更新的日期）
# 均为美国东部时间（UTC-4）
HOLIDAY_2025 = [
    "2025 1 January",    # 元旦
    "2025 20 January",   # 马丁·路德·金纪念日
    "2025 17 February",  # 总统日
    "2025 26 May",       # 阵亡将士纪念日
    "2025 19 June",      # 六月节
    "2025 4 July",       # 独立日
    "2025 1 September",  # 劳动节
    "2025 27 November",  # 感恩节
    "2025 28 November",  # 感恩节翌日
    "2025 25 December",  # 圣诞节
    "2025 26 December",  # 圣诞节翌日
    "2025 31 December",  # 新年前夜
]

HOLIDAY_2026 = [
    "2026 1 January",    # 元旦
    "2026 19 January",   # 马丁·路德·金纪念日
    "2026 16 February",  # 总统日
    "2026 25 May",       # 阵亡将士纪念日
    "2026 19 June",      # 六月节
    "2026 3 July",       # 独立日（7月4日是周六，提前放假）
    "2026 7 September",  # 劳动节
    "2026 26 November",  # 感恩节
    "2026 27 November",  # 感恩节翌日
    "2026 25 December",  # 圣诞节
]

# 2027年美国联邦假期（arXiv 不更新的日期），规则同上
# 注：假期表需每年维护；以下为 2027 年预估（独立日/圣诞节按「就近工作日」调整）。
HOLIDAY_2027 = [
    "2027 1 January",    # 元旦（周五）
    "2027 18 January",   # 马丁·路德·金纪念日（第3个周一）
    "2027 15 February",  # 总统日（第3个周一）
    "2027 31 May",       # 阵亡将士纪念日（最后一个周一）
    "2027 19 June",      # 六月节
    "2027 5 July",       # 独立日（7月4日是周日，顺延周一）
    "2027 6 September",  # 劳动节（第1个周一）
    "2027 25 November",  # 感恩节（第4个周四）
    "2027 26 November",  # 感恩节翌日
    "2027 24 December",  # 圣诞节（12月25日是周六，提前到周五）
    "2027 25 December",  # 圣诞节
]

# 合并所有假期
ALL_HOLIDAYS = HOLIDAY_2025 + HOLIDAY_2026 + HOLIDAY_2027
HOLIDAY_DATE = [datetime.strptime(d, "%Y %d %B").date() for d in ALL_HOLIDAYS]


@lru_cache()
def next_arxiv_update_day(time: datetime):
    # see https://info.arxiv.org/help/availability.html
    # arxiv update time is UTC+0 00:00:00

    time_date = time.replace(hour=0, minute=0, second=0, microsecond=0)
    if time > time_date:
        time = time_date + timedelta(days=1)

    # 上述假期均为美国东部时间（UTC-4），因此假期会导致次日UTC+0的更新推迟
    # arxiv的更新时间为周日-周四的美东20:00，对应周一到周五的UTC 00:00
    while time.date() - timedelta(days=1) in HOLIDAY_DATE or time.weekday() in [5, 6]:
        time = time + timedelta(days=1)
    return time


if __name__ == "__main__":
    print(HOLIDAY_DATE)
    print(next_arxiv_update_day(datetime.now()))
    print(next_arxiv_update_day(datetime.strptime("2026 9 8", "%Y %m %d")))