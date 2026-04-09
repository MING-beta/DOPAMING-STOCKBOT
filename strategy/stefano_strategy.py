"""
StefanoStrategy 모듈
----------------------
스테파노의 'RSI 다이버전스' 매매 기법을 시스템화한 로직입니다.
1. 거시적 타임프레임(5분봉)에서 하락 다이버전스가 발생하는지 (가격은 하락하나 RSI는 상승) 검증합니다.
2. 거시적 다이버전스가 감지된 상태에서 미시적 타임프레임(1분봉)의 다이버전스가 이중으로 감지되면 매수 시그널을 발생시킵니다.
"""
import pandas as pd
import numpy as np
import logging
import os

def _find_valleys(arr, distance=3):
    """
    scipy.signal.find_peaks를 대체하기 위한 가벼운 로컬 저점(Valley) 탐색 함수입니다.
    배열에서 좌우 값보다 작고 일정 간격(distance) 이상 떨어진 인덱스 목록을 반환합니다.
    
    Args:
        arr (list or np.array): RSI 배열 등 1차원 데이터
        distance (int): 저점 간의 최소 요구 간격(봉 개수)
        
    Returns:
        list: 저점이 발생한 인덱스 번호 목록
    """
    valleys = []
    n = len(arr)
    # 첫번째와 마지막 봉을 제외한 내부에서 지역 최저점 탐색
    for i in range(1, n - 1):
        if arr[i] < arr[i - 1] and arr[i] < arr[i + 1]:
            # distance 조건 필터링 (너무 가까운 저점은 노이즈로 보고 무시)
            if not valleys or (i - valleys[-1]) >= distance:
                valleys.append(i)
    return valleys

class StefanoStrategy:
    def __init__(self, check_window=60):
        self.logger = logging.getLogger("DopamingBot.StefanoStrategy")
        self.check_window = check_window
        # 거시적(5분봉) 다이버전스 상태 캐싱 {code: bool}
        self.macro_states = {}
        # 지표 계산 결과 캐싱 { (code, timeframe): (last_index, df_result) }
        self._indicator_cache = {}

    def analyze(self, code, df_1m: pd.DataFrame, df_5m: pd.DataFrame):
        if df_1m.empty or df_5m.empty:
            return False
            
        if len(df_1m) < 20 or len(df_5m) < 20:
            return False

        # 1. 보조지표 계산 (캐싱 적용된 고속 버전)
        df_1m = self._get_cached_indicators(code, "1m", df_1m)
        df_5m = self._get_cached_indicators(code, "5m", df_5m)

        # 2. 5분봉(거시적) 다이버전스 감지
        macro_div = self._check_bullish_divergence(df_5m, window=self.check_window)
        if macro_div and not self.macro_states.get(code, False):
            self.macro_states[code] = True
            self.logger.info(f"[{code}] 5분봉 상승 다이버전스 감지! 진입 State 활성화.")
        elif not macro_div:
             # 다이버전스 상태가 해제되었을 때만 False로 변경 (필요 시)
             # 단, 진입대기 상태를 유지하고 싶다면 로직에 따라 조절 가능
             pass

        # 3. 1분봉(미시적) 이중 다이버전스 및 BB 하단 필터 확인
        if self.macro_states.get(code, False):
            micro_div = self._check_bullish_divergence(df_1m, window=self.check_window, strict=False)
            if micro_div:
                last_price = df_1m['close'].iloc[-1]
                bb_lower = df_1m['BB_Lower'].iloc[-1]
                
                if last_price <= bb_lower * 1.005:
                    self.logger.warning(f"[{code}] 💥 이중 다이버전스 + BB 하단 통과! 매수 시그널!")
                    self.macro_states[code] = False
                    return True
        return False

    def _get_cached_indicators(self, code, timeframe, df):
        """데이터 변경 시에만 지표를 재계산하는 고속 캐싱 메서드"""
        current_last_idx = df.index[-1]
        cache_key = (code, timeframe)
        
        # 캐시에 데이터가 있고, 마지막 캔들 시간이 동일하면(업데이트 없음) 캐시 반환
        if cache_key in self._indicator_cache:
            last_idx, cached_df = self._indicator_cache[cache_key]
            # 인덱스 길까지 체크하여 데이터 정합성 보장
            if last_idx == current_last_idx and len(cached_df) == len(df):
                return cached_df
                
        # 변경사항 발생 시 재계산
        calculated_df = self._calculate_indicators(df)
        self._indicator_cache[cache_key] = (current_last_idx, calculated_df)
        return calculated_df

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """핵심 지표 연산 (RSI, VO, BB)"""
        df = df.copy()
        rsi_period = int(os.getenv("INDICATOR_RSI_PERIOD", "14"))
        delta = df['close'].diff()
        up = delta.clip(lower=0)
        down = -1 * delta.clip(upper=0)
        
        alpha = 1 / rsi_period
        roll_up = up.ewm(alpha=alpha, min_periods=rsi_period, adjust=False).mean()
        roll_down = down.ewm(alpha=alpha, min_periods=rsi_period, adjust=False).mean()
        
        rs = roll_up / (roll_down + 1e-9) # ZeroDivision 방지
        df['RSI'] = 100.0 - (100.0 / (1.0 + rs))
        
        ema12 = df['volume'].ewm(span=12, adjust=False).mean()
        ema26 = df['volume'].ewm(span=26, adjust=False).mean()
        df['VO'] = np.where(ema26 != 0, ((ema12 - ema26) / ema26) * 100.0, 0.0)
        
        bb_period = int(os.getenv("INDICATOR_BB_PERIOD", "20"))
        bb_std = float(os.getenv("INDICATOR_BB_STD", "2.0"))
        
        df['MA20'] = df['close'].rolling(window=bb_period).mean()
        df['STD20'] = df['close'].rolling(window=bb_period).std()
        df['BB_Upper'] = df['MA20'] + (df['STD20'] * bb_std)
        df['BB_Lower'] = df['MA20'] - (df['STD20'] * bb_std)
            
        return df.fillna(0)

    def _check_bullish_divergence(self, df: pd.DataFrame, window=60, strict=True):
        """
        주가 하락 (새로운 저점 갱신) vs RSI 상승 (저점이 높아짐)
        scipy.signal.find_peaks를 사용해 저점을 판별합니다.
        """
        # 최근 윈도우 만큼 샘플링
        df_recent = df.iloc[-window:]
        if len(df_recent) < 20:
            return False

        prices = df_recent['close'].values
        rsis = df_recent['RSI'].values

        # 저점(Trough) 탐지 - 순수 Python 자체 구현체 사용
        price_valleys = _find_valleys(prices, distance=3)
        rsi_valleys = _find_valleys(rsis, distance=3)

        # 찾은 저점이 2개 이상 있어야 다이버전스 비교 가능
        if len(price_valleys) >= 2 and len(rsi_valleys) >= 2:
            # 최근 두 개의 저점 인덱스 추출
            curr_p_idx = price_valleys[-1]
            prev_p_idx = price_valleys[-2]
            
            curr_r_idx = rsi_valleys[-1]
            prev_r_idx = rsi_valleys[-2]

            price_falling = prices[curr_p_idx] < prices[prev_p_idx]
            rsi_rising = rsis[curr_r_idx] > rsis[prev_r_idx]
            
            # 엄격한 비교일 경우: 현재가가 이전 저점보다 낮고, RSI가 이전 저점보다 높을 때 다이버전스
            if price_falling and rsi_rising:
                
                # 추가 필터 (선택적): RSI가 30(과매도) 근처 또는 이하에서 발생했는가
                if strict and rsis[prev_r_idx] > 40:
                    return False
                    
                return True
        return False
