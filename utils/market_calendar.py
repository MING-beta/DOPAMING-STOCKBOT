"""
MarketCalendar - 한국 주식시장(KRX) 영업일 판별 유틸리티
------------------------------------------------------
외부 라이브러리 의존성 없이 자체적으로 KRX 영업일을 판별합니다.

판별 방식 (3중 검증):
  1단계: 주말(토/일) → 즉시 휴장
  2단계: 한국 법정 공휴일 + KRX 특수 휴장일 → 휴장
  3단계: .env의 수동 휴장일/개장일 오버라이드 (임시대체공휴일 등 대응)
"""

import os
import logging
from datetime import date, timedelta

logger = logging.getLogger("DopamingBot.MarketCalendar")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [고정 공휴일] 매년 음력 변환 없이 양력으로 고정된 공휴일
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIXED_HOLIDAYS = {
    (1, 1),    # 신정
    (3, 1),    # 삼일절
    (5, 1),    # 근로자의 날 (KRX 휴장)
    (5, 5),    # 어린이날
    (6, 6),    # 현충일
    (8, 15),   # 광복절
    (10, 3),   # 개천절
    (10, 9),   # 한글날
    (12, 25),  # 성탄절
    (12, 31),  # KRX 연말 폐장일
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [음력 기반 공휴일] 연도별 양력 날짜 매핑
# 설날(음력 1/1 ±1일), 석가탄신일(음력 4/8), 추석(음력 8/15 ±1일)
# 매년 초에 한 번 업데이트 필요 (또는 3~5년치 선입력)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LUNAR_HOLIDAYS = {
    2025: [
        # 설날 연휴
        date(2025, 1, 28), date(2025, 1, 29), date(2025, 1, 30),
        # 석가탄신일
        date(2025, 5, 5),
        # 추석 연휴
        date(2025, 10, 5), date(2025, 10, 6), date(2025, 10, 7),
    ],
    2026: [
        # 설날 연휴
        date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
        # 삼일절 대체공휴일
        date(2026, 3, 2),
        # 석가탄신일
        date(2026, 5, 24),
        # 추석 연휴
        date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 26),
    ],
    2027: [
        # 설날 연휴
        date(2027, 2, 5), date(2027, 2, 6), date(2027, 2, 7),
        # 석가탄신일  
        date(2027, 5, 13),
        # 추석 연휴
        date(2027, 10, 14), date(2027, 10, 15), date(2027, 10, 16),
    ],
    2028: [
        # 설날 연휴
        date(2028, 1, 25), date(2028, 1, 26), date(2028, 1, 27),
        # 석가탄신일
        date(2028, 5, 2),
        # 추석 연휴
        date(2028, 10, 2), date(2028, 10, 3), date(2028, 10, 4),
    ],
}


def _get_env_override_dates(key: str) -> set:
    """
    .env에서 콤마로 구분된 날짜 목록을 파싱합니다.
    형식: YYYY-MM-DD,YYYY-MM-DD,...
    """
    raw = os.getenv(key, "").strip()
    if not raw:
        return set()
    dates = set()
    for d_str in raw.split(","):
        d_str = d_str.strip()
        if d_str:
            try:
                dates.add(date.fromisoformat(d_str))
            except ValueError:
                logger.warning(f"⚠️ [MarketCalendar] {key}에 잘못된 날짜 형식: '{d_str}' (YYYY-MM-DD 필요)")
    return dates


def is_market_open(target_date: date = None) -> tuple:
    """
    주어진 날짜가 KRX 영업일인지 판별합니다.
    
    Args:
        target_date: 확인할 날짜 (None이면 오늘)
    
    Returns:
        (is_open: bool, reason: str)
        - (True, "영업일") 또는 (False, "사유")
    """
    if target_date is None:
        target_date = date.today()
    
    year = target_date.year
    month = target_date.month
    day = target_date.day
    
    # ─── [1단계] .env 수동 오버라이드 (최우선) ───
    # 강제 개장일 (임시공휴일이지만 거래소가 열리는 경우 등)
    force_open = _get_env_override_dates("KRX_FORCE_OPEN_DATES")
    if target_date in force_open:
        return True, "수동 강제 개장일 (.env 오버라이드)"
    
    # 강제 휴장일 (임시 대체공휴일, 임시 폐장일 등)
    force_close = _get_env_override_dates("KRX_FORCE_CLOSE_DATES")
    if target_date in force_close:
        return False, "수동 강제 휴장일 (.env 오버라이드)"
    
    # ─── [2단계] 주말 체크 ───
    weekday = target_date.weekday()  # 0=월, 6=일
    if weekday >= 5:
        day_name = "토요일" if weekday == 5 else "일요일"
        return False, f"주말 ({day_name})"
    
    # ─── [3단계] 고정 공휴일 체크 ───
    if (month, day) in FIXED_HOLIDAYS:
        holiday_names = {
            (1, 1): "신정", (3, 1): "삼일절", (5, 1): "근로자의 날",
            (5, 5): "어린이날", (6, 6): "현충일", (8, 15): "광복절",
            (10, 3): "개천절", (10, 9): "한글날", (12, 25): "성탄절",
            (12, 31): "KRX 연말 폐장일",
        }
        name = holiday_names.get((month, day), "공휴일")
        return False, f"법정 공휴일 ({name})"
    
    # ─── [4단계] 음력 기반 공휴일 체크 ───
    lunar_dates = LUNAR_HOLIDAYS.get(year, [])
    if target_date in lunar_dates:
        # 어떤 연휴인지 추정
        if month <= 3:
            name = "설날 연휴"
        elif month <= 6:
            name = "석가탄신일" if day > 10 else "대체공휴일"
        else:
            name = "추석 연휴"
        return False, f"음력 공휴일 ({name})"
    
    # ─── [5단계] 대체공휴일 체크 (공휴일이 주말과 겹칠 경우) ───
    # 고정 공휴일이 일요일이면 월요일이 대체공휴일
    for (m, d) in FIXED_HOLIDAYS:
        if m == 12 and d == 31:
            continue  # 연말 폐장은 대체공휴일 없음
        if m == 5 and d == 1:
            continue  # 근로자의 날은 대체공휴일 없음
        try:
            holiday_date = date(year, m, d)
            if holiday_date.weekday() == 6:  # 일요일
                substitute = holiday_date + timedelta(days=1)  # 월요일
                if target_date == substitute:
                    holiday_names = {
                        (1, 1): "신정", (3, 1): "삼일절", (5, 5): "어린이날",
                        (6, 6): "현충일", (8, 15): "광복절", (10, 3): "개천절",
                        (10, 9): "한글날", (12, 25): "성탄절",
                    }
                    name = holiday_names.get((m, d), "공휴일")
                    return False, f"대체공휴일 ({name} 일요일 → 월요일 대체)"
            elif holiday_date.weekday() == 5:  # 토요일
                substitute = holiday_date + timedelta(days=2)  # 월요일
                if target_date == substitute:
                    holiday_names = {
                        (1, 1): "신정", (3, 1): "삼일절", (5, 5): "어린이날",
                        (6, 6): "현충일", (8, 15): "광복절", (10, 3): "개천절",
                        (10, 9): "한글날", (12, 25): "성탄절",
                    }
                    name = holiday_names.get((m, d), "공휴일")
                    return False, f"대체공휴일 ({name} 토요일 → 월요일 대체)"
        except ValueError:
            pass
    
    # ─── 모든 체크 통과 = 영업일 ───
    return True, "영업일"


def get_next_market_day(from_date: date = None) -> date:
    """다음 영업일을 반환합니다."""
    if from_date is None:
        from_date = date.today()
    cursor = from_date + timedelta(days=1)
    for _ in range(30):  # 최대 30일 탐색
        is_open, _ = is_market_open(cursor)
        if is_open:
            return cursor
        cursor += timedelta(days=1)
    return cursor


def get_market_status_message(target_date: date = None) -> str:
    """오늘의 시장 상태를 슬랙/텔레그램용 메시지로 반환합니다."""
    if target_date is None:
        target_date = date.today()
    
    is_open, reason = is_market_open(target_date)
    day_names = ["월", "화", "수", "목", "금", "토", "일"]
    day_str = f"{target_date.strftime('%Y-%m-%d')} ({day_names[target_date.weekday()]})"
    
    if is_open:
        return f"📈 *[영업일 확인]* {day_str} - 오늘은 정규 거래일입니다. 매매 엔진을 가동합니다."
    else:
        next_day = get_next_market_day(target_date)
        next_str = f"{next_day.strftime('%Y-%m-%d')} ({day_names[next_day.weekday()]})"
        return (
            f"😴 *[휴장일 감지]* {day_str}\n"
            f"사유: {reason}\n"
            f"다음 거래일: {next_str}\n"
            f"시스템이 휴식 모드에 진입합니다. 다음 영업일 08:00에 자동 복귀됩니다."
        )
