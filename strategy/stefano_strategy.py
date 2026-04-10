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
    # distance 윈도우 필터 적용 (Flat Bottom 처리 위해 양측 비교 분리)
    for i in range(distance, n - distance):
        window_left = arr[i - distance : i]
        window_right = arr[i + 1 : i + distance + 1]
        
        # 좌측보다는 작거나 같고(Flat 바텀의 우측 끝단), 우측보다는 작을 때
        if all(arr[i] <= x for x in window_left) and all(arr[i] < x for x in window_right):
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
        
        # 공격적 매매 플래그 (타점 완화용)
        self.is_aggressive = os.getenv("AGGRESSIVE_MODE", "False").lower() == 'true'
        
        # 거래 체결 테스트 모드 플래그 (AND -> OR 조건으로 매수 빈도 폭증)
        self.is_extreme_test = os.getenv("EXTREME_TEST_MODE", "False").lower() == 'true'

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
        current_time = df_5m.index[-1]

        # [최적화] 거시 신호가 감지되면 진입 대기 상태로 전환
        if macro_div:
            if not self.macro_states.get(code, False):
                self.macro_states[code] = True
                self._indicator_cache[f"{code}_macro_time"] = current_time
                self.logger.info(f"[{code}] 5분봉 상승 다이버전스 감지! 진입 State 활성화.")
        else:
            # [전체 검수 보강] 매수 대기 상태(macro_states) 자동 만료(Reset) 로직
            if self.macro_states.get(code, False):
                entry_time = self._indicator_cache.get(f"{code}_macro_time")
                
                # A. 시간 만료: 5분봉 기준 6봉(30분)이 지났는데도 1분봉 타점이 안오면 신호 소멸로 간주
                # B. 추세 회복: 5분봉 RSI가 50(균형점)을 넘어서면 이미 반등이 끝난 것으로 보고 리셋
                rsi_5m = df_5m['RSI'].iloc[-1]
                time_diff = (current_time - entry_time).total_seconds() / 60 if entry_time else 0
                
                if time_diff > 30 or rsi_5m > 50:
                    self.macro_states[code] = False
                    reason = "시간 경과(30분)" if time_diff > 30 else "추세 회복(RSI > 50)"
                    self.logger.info(f"[{code}] 거시 신호가 유효하지 않아 {reason}로 대기 상태를 해제(Reset)합니다.")

        # [테스트 모드: 모든 지표 상시 연산 및 OR 조건 매수]
        if getattr(self, 'is_extreme_test', False):
            micro_div = self._check_bullish_divergence(df_1m, window=self.check_window, strict=False)
            last_price = df_1m['close'].iloc[-1]
            bb_lower = df_1m['BB_Lower'].iloc[-1]
            bb_multiplier = 1.05 if getattr(self, 'is_aggressive', False) else 1.02
            bb_touch = (last_price <= bb_lower * bb_multiplier)
            
            if macro_div or micro_div or bb_touch:
                self.logger.warning(f"[{code}] 🧪 [체결 테스트 모드] OR 조건 충족 (5m={macro_div}, 1m={micro_div}, bb={bb_touch}) -> 조건 무시 매수 발동!")
                self.macro_states[code] = False
                return True
                
        # 3. 1분봉(미시적) 이중 다이버전스 및 BB 하단 필터 확인
        elif self.macro_states.get(code, False):
            micro_div = self._check_bullish_divergence(df_1m, window=self.check_window, strict=False)
            last_price = df_1m['close'].iloc[-1]
            bb_lower = df_1m['BB_Lower'].iloc[-1]
            rsi_1m = df_1m['RSI'].iloc[-1]
            
            # [진단 로깅] 매수 대기 중인 종목의 상세 지표를 INFO 레벨로 출력하여 대기 원인 분석
            self.logger.info(f"[{code}] 🔎 대기중... RSI(1m)={rsi_1m:.1f}, 현재가={last_price:,}, BB하단={bb_lower:.1f}")
            self.logger.info(f"[{code}] ❓ 매수조건: 1분 다이버전스={micro_div}, BB하단 근접={last_price <= bb_lower * 1.005}")

            # 공격적 모드일 경우 BB 하단에서 5% 상위까지 타점 허용, 일반은 2%
            bb_multiplier = 1.05 if self.is_aggressive else 1.02
            
            if micro_div:
                if last_price <= bb_lower * bb_multiplier:  
                    self.logger.warning(f"[{code}] 💥 1분봉 이중 다이버전스 + BB 하단 통과! 매수 실행!")
                    self.macro_states[code] = False
                    return True
                else:
                    self.logger.info(f"[{code}] ⚠️ 1분봉 다이버전스는 형성되었으나, 주가({last_price:,})가 BB하단({bb_lower:,.1f}) 대비 너무 많이 반등했습니다.")
            else:
                self.logger.info(f"[{code}] ⏳ 아직 1분봉상 '이중 바닥(Divergence)' 신호가 완성되지 않았습니다.")
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
        
        if getattr(self, 'is_aggressive', False):
            rsi_period = 9  # 공격적 모드: RSI 기간 축소로 민감도 극대화
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
        
        if getattr(self, 'is_aggressive', False):
            bb_std = 1.5  # 공격적 모드: 밴드 너비를 좁혀 하단 터치(매수 기회) 확률 증폭
        
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

        # 저점(Trough) 탐지 - 가격 배열에서만 추출 (RSI는 가격의 저점 시점을 기준으로 동기화 비교)
        price_valleys = _find_valleys(prices, distance=3)

        # 찾은 저점이 2개 이상 있어야 다이버전스 비교 가능
        if len(price_valleys) >= 2:
            # 최근 두 개의 저점 인덱스 추출
            curr_p_idx = price_valleys[-1]
            prev_p_idx = price_valleys[-2]

            price_falling = prices[curr_p_idx] < prices[prev_p_idx]
            
            # 주가가 바닥을 친 정확히 '동일한 시점' 의 RSI 지표값을 비교해야 진정한 다이버전스 성립
            rsi_rising = rsis[curr_p_idx] > rsis[prev_p_idx]
            
            # 공격적 모드일 경우 RSI 과매도 필터 대폭 완화(65), 일반은 45
            rsi_strict_limit = 65 if getattr(self, 'is_aggressive', False) else 45
            
            if price_falling and rsi_rising:
                # 추가 필터 (선택적): 이전 저점 형성 시 RSI가 기준점 이하에 있었는가
                if strict and rsis[prev_p_idx] > rsi_strict_limit:
                    return False
                    
                return True
        return False
