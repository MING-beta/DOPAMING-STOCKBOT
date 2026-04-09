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

    def analyze(self, code, df_1m: pd.DataFrame, df_5m: pd.DataFrame):
        """
        [전략 핵심 진입점] 
        종목별 1분봉/5분봉 데이터를 받아 다이버전스 달성 여부를 분석하고
        최종 진입(매수) 시그널(True/False)을 반환합니다.
        
        Args:
            code (str): 종목코드
            df_1m (pd.DataFrame): 1분봉 OHLCV
            df_5m (pd.DataFrame): 5분봉 OHLCV
            
        Returns:
            bool: 매수 시그널 발생 여부
        """
        if df_1m.empty or df_5m.empty:
            return False
            
        # 캔들이 충분히 모여있는지 체크 (최소 20개 이상 권장)
        if len(df_1m) < 20 or len(df_5m) < 20:
            return False

        # 1. 보조지표 계산 (RSI, Volume Oscillator)
        df_1m = self._calculate_indicators(df_1m)
        df_5m = self._calculate_indicators(df_5m)

        # 2. 5분봉(거시적) 다이버전스 감지 -> State 활성화
        macro_div = self._check_bullish_divergence(df_5m, window=self.check_window)
        if macro_div:
            self.macro_states[code] = True
            self.logger.info(f"[{code}] 5분봉 거시적 상승 다이버전스 감지! 진입 State 활성화.")

        # 3. State가 활성화된 종목에 한하여 1분봉(미시적) 다이버전스 확인
        if self.macro_states.get(code, False):
            micro_div = self._check_bullish_divergence(df_1m, window=self.check_window, strict=False)
            if micro_div:
                self.logger.warning(f"[{code}] 💥 1분봉 미시적 상승 다이버전스 발동! 매수 시그널 반환!")
                # 시그널 무한 발생을 막기 위해 상태 초기화
                self.macro_states[code] = False
                return True

        return False

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        
        # 1. 수동 RSI 구현 (기본 Length=14)
        delta = df['close'].diff()
        up = delta.clip(lower=0)
        down = -1 * delta.clip(upper=0)
        
        # 트레이딩뷰/야후파이낸스 스타일 RMA(수정 이동 평균)
        alpha = 1 / 14
        roll_up = up.ewm(alpha=alpha, min_periods=14, adjust=False).mean()
        roll_down = down.ewm(alpha=alpha, min_periods=14, adjust=False).mean()
        
        # ZeroDivision 방지
        rs = roll_up / roll_down
        df['RSI'] = 100.0 - (100.0 / (1.0 + rs))
        
        # 2. 수동 PVO (단기 12, 장기 26 이평선의 Percentage Volume Oscillator)
        ema12 = df['volume'].ewm(span=12, adjust=False).mean()
        ema26 = df['volume'].ewm(span=26, adjust=False).mean()
        # ZeroDivision 방지 (ema26=0일 때 고려)
        df['VO'] = np.where(ema26 != 0, ((ema12 - ema26) / ema26) * 100.0, 0.0)
            
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
