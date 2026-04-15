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

# AI 모듈 (선택적 의존성 — 주입(Injection) 방식으로 사용)
try:
    from core.data_collector import DataCollector
    from core.ai_engine import AIEngine
except ImportError:
    DataCollector = None
    AIEngine = None

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
        
        # [v4.2] 익스트림 테스트 모드 연동 (OR 조건 상시 매수)
        self.is_extreme_test = os.getenv("EXTREME_TEST_MODE", "False").lower() == 'true'
        
        # 거시 신호(5분봉) 무시 플래그 (타점 폭주용)
        self.bypass_macro = os.getenv("STRATEGY_BYPASS_MACRO", "False").lower() == 'true'
        if self.bypass_macro:
            self.logger.warning("[전략 설정] 거시(5분봉) 필터 우회 모드 활성화 - 1분봉 신호로 즉시 매수합니다.")

        # [v3.6] 거래량 폭증 기준 정합성 (기본 1.1)
        self.vol_spike_threshold = float(os.getenv("STRATEGY_VOL_RATIO", "1.1"))
        
        # [v4.5] 동일 종목 재진입 제한 시간 (초)
        self.signal_cooldown_limit = int(os.getenv("STRATEGY_SIGNAL_COOLDOWN", "1800"))

        # [v4.5] V자 반등(Nitro) 전용 환경 변수 로드
        self.nitro_rsi_limit    = float(os.getenv("STRATEGY_NITRO_RSI", "33.0"))
        self.nitro_bb_gap       = float(os.getenv("STRATEGY_NITRO_BB_GAP", "1.002"))
        self.min_recovery_rate  = float(os.getenv("STRATEGY_MIN_RECOVERY", "0.005"))
        self.buy_end_time       = os.getenv("STRATEGY_BUY_END_TIME", "14:30")
        self.bb_width_limit     = float(os.getenv("STRATEGY_BB_WIDTH_LIMIT", "0.03"))

        # [v5.1] 최적화: 루프 내 os.getenv 호출 제거 및 지표 파라미터 캐싱
        self.rsi_period = int(os.getenv("INDICATOR_RSI_PERIOD", "9" if self.is_aggressive else "14"))
        self.bb_period = int(os.getenv("INDICATOR_BB_PERIOD", "20"))
        self.bb_std = float(os.getenv("INDICATOR_BB_STD", "1.5" if self.is_aggressive else "2.0"))
        self.bb_gap_aggressive  = float(os.getenv("STRATEGY_BB_GAP", "1.05"))
        self.bb_gap_normal      = float(os.getenv("STRATEGY_BB_GAP", "1.01"))
        self.macro_rsi_exit     = int(os.getenv("STRATEGY_MACRO_RSI_EXIT", "50"))
        self.rsi_limit          = int(os.getenv("STRATEGY_RSI_LIMIT", "75" if self.is_aggressive else "45"))

        # [v4.8] 하이퍼 공격형 필터 토글 로드
        self.require_uptrend    = os.getenv("STRATEGY_REQUIRE_UPTREND", "True").lower() == 'true'
        self.require_vol_spike  = os.getenv("STRATEGY_REQUIRE_VOL_SPIKE", "True").lower() == 'true'
        self.scanner_soft_mode  = os.getenv("STRATEGY_SCANNER_SOFT_MODE", "False").lower() == 'true'

        # AI 모듈 (main.py에서 set_ai_modules()로 주입)
        self.ai_engine:       "AIEngine"      = None
        self.data_collector:  "DataCollector" = None
        
        # [v4.4] 매매 무한 도배 방지용 신호 기록 {code: timestamp}
        self.signal_cooldowns = {}

    def _find_valleys(self, arr, distance=2):
        """
        [v4.2] 저점 탐색 간격을 3->2로 단축하여 미세한 파동도 포착합니다.
        """
        valleys = []
        n = len(arr)
        for i in range(distance, n - distance):
            window_left = arr[i - distance : i]
            window_right = arr[i + 1 : i + distance + 1]
            
            if all(arr[i] <= x for x in window_left) and all(arr[i] < x for x in window_right):
                if not valleys or (i - valleys[-1]) >= distance:
                    valleys.append(i)
        return valleys

    def set_ai_modules(self, ai_engine, data_collector):
        """main.py에서 AI 엔진과 데이터 수집기를 주입합니다."""
        self.ai_engine      = ai_engine
        self.data_collector = data_collector
        self.logger.info("[OK] AI 모듈 연결 완료 (AIEngine + DataCollector)")

    # ------------------------------------------------------------------
    # AI Feature 추출 헬퍼
    # ------------------------------------------------------------------
    def _extract_ai_features(self, code, df_1m: pd.DataFrame, df_5m: pd.DataFrame,
                              macro_div: bool, micro_div: bool) -> dict:
        """AI 모델에 전달할 Feature 딕셔너리를 계산합니다."""
        try:
            last_price = df_1m['close'].iloc[-1]

            # RSI
            rsi_1m = float(df_1m['RSI'].iloc[-1])   if 'RSI'      in df_1m.columns else 50.0
            rsi_5m = float(df_5m['RSI'].iloc[-1])   if 'RSI'      in df_5m.columns else 50.0

            # BB 하단 이격도 (현재가 - BB하단) / BB하단
            if 'BB_Lower' in df_1m.columns and df_1m['BB_Lower'].iloc[-1] > 0:
                bb_pct_1m = (last_price - df_1m['BB_Lower'].iloc[-1]) / df_1m['BB_Lower'].iloc[-1]
            else:
                bb_pct_1m = 0.0

            if 'BB_Lower' in df_5m.columns and df_5m['BB_Lower'].iloc[-1] > 0:
                bb_pct_5m = (df_5m['close'].iloc[-1] - df_5m['BB_Lower'].iloc[-1]) / df_5m['BB_Lower'].iloc[-1]
            else:
                bb_pct_5m = 0.0

            # 거래량 비율 (현재봉 / 20봉 평균)
            vol_mean = df_1m['volume'].iloc[-20:].mean()
            vol_ratio = float(df_1m['volume'].iloc[-1] / vol_mean) if vol_mean > 0 else 1.0

            # 가격 변화율
            price_chg_5  = float((last_price - df_1m['close'].iloc[-6])  / df_1m['close'].iloc[-6])  if len(df_1m) >= 6  else 0.0
            price_chg_20 = float((last_price - df_1m['close'].iloc[-21]) / df_1m['close'].iloc[-21]) if len(df_1m) >= 21 else 0.0

            return {
                "rsi_1m":       rsi_1m,
                "rsi_5m":       rsi_5m,
                "bb_pct_1m":    round(bb_pct_1m, 6),
                "bb_pct_5m":    round(bb_pct_5m, 6),
                "vol_ratio":    round(vol_ratio, 4),
                "price_chg_5":  round(price_chg_5,  6),
                "price_chg_20": round(price_chg_20, 6),
                "macro_div":    int(macro_div),
                "micro_div":    int(micro_div),
                "is_aggressive": int(self.is_aggressive),
            }
        except Exception as e:
            self.logger.debug("[%s] Feature 추출 오류 (기본값 사용): %s", code, e)
            return {col: 0.0 for col in [
                "rsi_1m","rsi_5m","bb_pct_1m","bb_pct_5m",
                "vol_ratio","price_chg_5","price_chg_20",
                "macro_div","micro_div","is_aggressive"
            ]}

    # ------------------------------------------------------------------
    # 핵심 분석 엔진
    # ------------------------------------------------------------------
    def analyze(self, code, df_1m: pd.DataFrame, df_5m: pd.DataFrame):
        if df_1m.empty or df_5m.empty:
            return False
            
        # [v3.9] 1차 관문: 가상 스캐너 (수급 및 변동성 정밀 필터링)
        # RVOL (최근 20분 평균 대비 현재 거래량) 및 당일 진폭(High-Low) 체크
        vol_mean = df_1m['volume'].iloc[-20:-1].mean()
        vol_ratio_scanner = df_1m['volume'].iloc[-1] / vol_mean if vol_mean > 0 else 1.0
        
        day_high = df_1m['high'].max()
        day_low = df_1m['low'].min()
        day_range_pct = (day_high - day_low) / day_low if day_low > 0 else 0
        
        # 공격모드: RVOL 0.7, 진폭 0.3% (매수 기회 확대) / 일반모드: RVOL 1.3, 진폭 1.8%
        vol_threshold = 0.7 if self.is_aggressive else 1.3
        range_threshold = 0.003 if self.is_aggressive else 0.018
        
        if self.scanner_soft_mode:
            # 소프트 모드: 최소한의 거래량(1.01)과 진폭(0.1%)만 있어도 통과
            vol_threshold = 1.01
            range_threshold = 0.001
        
        # [v5.0] 논리 보정: 현재 분(minute) 내 경과 시간에 따른 거래량 문턱값 동적 조절
        # 1분봉 누적 거래량은 시간이 지날수록 늘어나므로, 현재 초(second)에 비례하여 비교해야 공정함
        now_dt = df_1m.index[-1]
        seconds_passed = now_dt.second if hasattr(now_dt, 'second') else 30
        time_weight = max(seconds_passed, 5) / 60.0 # 최소 5초 가중치 부여
        
        dynamic_vol_threshold = vol_threshold * time_weight
        
        if vol_ratio_scanner < dynamic_vol_threshold or day_range_pct < range_threshold:
            self.logger.debug(f"[{code}] [PASS] 스캐너 조건 미달 (RVOL:{vol_ratio_scanner:.2f}/{dynamic_vol_threshold:.2f}, Range:{day_range_pct*100:.2f}%/{range_threshold*100:.2f}%)")
            return False

        # 5분봉 데이터가 부족해도 분석 가능하도록 완화 (거시 필터 우회 시)
        min_5m_len = 5 if self.bypass_macro else 20
        if len(df_1m) < 20 or len(df_5m) < min_5m_len:
            self.logger.debug(f"[{code}] 데이터 수집 중... (1m: {len(df_1m)}개, 5m: {len(df_5m)}개)")
            return False

        # 1. 보조지표 계산 (캐싱 적용된 고속 버전)
        df_1m = self._get_cached_indicators(code, "1m", df_1m)
        df_5m = self._get_cached_indicators(code, "5m", df_5m)

        # 2. 5분봉(거시적) 다이버전스 감지 (거시는 안정성을 위해 distance=2 유지)
        macro_div = self._check_bullish_divergence(df_5m, window=self.check_window, distance=2)
        current_time = df_5m.index[-1]
        last_price   = df_1m['close'].iloc[-1]

        # [AI 라벨 업데이트] 실시간 틱마다 미결 신호의 결과를 추적
        if self.data_collector:
            self.data_collector.update_labels(code, last_price)

        # [최적화] 거시 신호가 감지되면 진입 대기 상태로 전환
        if macro_div:
            if not self.macro_states.get(code, False):
                self.logger.info(f"[{code}] [MACRO] 5분봉 거시 신호 포착! 1분봉 타점 대기...")
                self.macro_states[code] = True
                self._indicator_cache[f"{code}_macro_time"] = current_time
        else:
            # [전체 검수 보강] 매수 대기 상태(macro_states) 자동 만료(Reset) 로직
            if self.macro_states.get(code, False):
                entry_time = self._indicator_cache.get(f"{code}_macro_time")
                
                # A. 시간 만료: 5분봉 기준 12봉(60분)이 지났는데도 1분봉 타점이 안오면 신호 소멸로 간주
                rsi_5m = df_5m['RSI'].iloc[-1]
                time_diff = (current_time - entry_time).total_seconds() / 60 if entry_time else 0
                
                if time_diff > 60 or rsi_5m > self.macro_rsi_exit:
                    self.logger.debug(f"[{code}] [MACRO] 거시 신호 만료 또는 추세 회복으로 상태 초기화 (RSI:{rsi_5m:.1f}, Time:{time_diff:.1f}m)")
                    self.macro_states[code] = False
                    reason = "시간 경과(60분)" if time_diff > 60 else f"추세 회복(RSI > {self.macro_rsi_exit})"
                    self.logger.info(f"[{code}] 거시 신호가 유효하지 않아 {reason}로 대기 상태를 해제(Reset)합니다.")

        # [v2.5 Hybrid] 가시성 확보 모드 (BYPASS_MACRO=True 이면 거시 신호가 없어도 1분봉 스캔 수행)
        is_visible = self.bypass_macro or self.macro_states.get(code, False)

        # 3. 1분봉(미시적) 이중 다이버전스 및 BB 하단 필터 확인 (미시는 빠른 포착을 위해 distance=1)
        if is_visible:
            micro_div = self._check_bullish_divergence(df_1m, window=self.check_window, strict=False, distance=1)
            last_price = df_1m['close'].iloc[-1]
            bb_lower = df_1m['BB_Lower'].iloc[-1]
            rsi_1m = df_1m['RSI'].iloc[-1]
            
            # 볼린저 밴드 하단 대비 진입 허용폭 (공격모드 1.05, 일반 1.01)
            bb_multiplier = self.bb_gap_aggressive if self.is_aggressive else self.bb_gap_normal
            
            # [필터 강화 v3.3] EMA 120 추세 필터 및 RSI 반등 확인
            ema120 = df_1m['EMA120'].iloc[-1]
            vol_mean = df_1m['volume'].iloc[-20:-1].mean()
            vol_ratio = df_1m['volume'].iloc[-1] / vol_mean if vol_mean > 0 else 1.0
            
            # 1. 주가가 장기 이평선(EMA120) 위에 있는가? (하락 대세 종목 배제) - 옵션에 따라 우회
            # 2. RSI가 30.0을 넘었는가? (v2.6: 데드존 제거를 위해 30.0으로 하향)
            # 3. 거래량이 평균 대비 폭증했는가? - 옵션에 따라 우회
            # [v5.4] 캔들 몸통 확인 (반등 의지 확인을 위해 당사 분봉 양봉 필수)
            is_green_candle = last_price > df_1m['open'].iloc[-1]
            is_recovering = rsi_1m >= 30.0
            is_uptrend = last_price > ema120 if self.require_uptrend else True
            is_vol_spike = vol_ratio >= self.vol_spike_threshold if self.require_vol_spike else True
            
            # [v3.5 추가] RSI 반등(Hook) 확인 (평탄한 경우(>=)도 인정하도록 완화)
            is_rsi_hook = rsi_1m >= df_1m['RSI'].iloc[-2] if len(df_1m) >= 2 else True
            
            # [버그 수정] 백테스트 시뮬레이션 시간과 실제 시간 정합성을 위해 df_1m.index[-1] 사용
            now_ts = df_1m.index[-1].timestamp()
            last_signal_time = self.signal_cooldowns.get(code, 0)
            if now_ts - last_signal_time < self.signal_cooldown_limit: 
                self.logger.debug(f"[{code}] [PASS] 쿨타임 대기 중 (경과: {int(now_ts - last_signal_time)}s)")
                return False
            
            # [v4.7] 변동성 폭발 필터 (BB Width)
            bb_upper = df_1m['BB_Upper'].iloc[-1]
            middle = (bb_upper + bb_lower) / 2
            bb_width = (bb_upper - bb_lower) / middle if middle != 0 else 0
            
            if bb_width > self.bb_width_limit:
                self.logger.debug(f"[{code}] [PASS] 변동성 과폭 (BB Width:{bb_width*100:.2f}%/{self.bb_width_limit*100}%)")
                return False

            # [v4.6 Balanced Reversion] 단순 Hook이 아닌 '가격 반등의 질' 검증
            min_price_10m = df_1m['low'].iloc[-10:].min()
            actual_recovery = (last_price / min_price_10m) - 1
            
            # RSI가 nitro_rsi_limit 이하이면서, 주가가 BB 하단 근격에 있고, 최소 반등폭을 달성했을 때만 인정
            adj_min_recovery = self.min_recovery_rate * 0.5 if self.is_aggressive else self.min_recovery_rate
            is_v_bottom = (rsi_1m <= self.nitro_rsi_limit and is_rsi_hook and 
                           (last_price <= bb_lower * self.nitro_bb_gap) and
                           (actual_recovery >= adj_min_recovery))
            
            # [v5.4] 최종 진입 시그널 결합 (양봉 확인 추가)
            is_entry_signal = (is_v_bottom or (micro_div and is_recovering)) and is_green_candle

            if not is_entry_signal:
                return False
            
            # 타점 인정을 위한 최종 보조 조건 검증
            if not (is_rsi_hook and (is_uptrend or is_vol_spike)):
                return False

            if is_entry_signal:
                if last_price <= bb_lower * bb_multiplier:  
                    # ── v2.5 하이브리드 최종 매수 승인 게이트 ───────────────
                    signal_type = "다이버전스" if micro_div else "V자반등"
                    is_macro_confirmed = self.macro_states.get(code, False) # 실제 거시 신호 유무

                    if self.ai_engine:
                        features = self._extract_ai_features(
                            code, df_1m, df_5m,
                            macro_div=is_macro_confirmed, # 실제 필터 상태 전달
                            micro_div=micro_div
                        )
                        approved, prob = self.ai_engine.approve(features)
                        prob_str = f"{prob*100:.1f}%" if prob >= 0 else "N/A"

                        if not approved:
                            self.logger.info(f"[{code}] [DENIED] AI 기각 ({prob_str}) -> 화면 감시는 유지합니다.")
                            return False
                        
                        # [핵심] 화면에는 감지 로그를 띄우되, 거시 신호가 없으면 매수하지 않음 (단, bypass_macro=True 라면 진행)
                        if not is_macro_confirmed and not self.bypass_macro:
                            self.logger.info(f"[{code}] [SCAN] 1분봉 {signal_type} 감지! (5분봉 하락 추세 진정 대기 중... AI:{prob_str})")
                            return False # 실제 매수는 발생시키지 않음
                        
                        suffix = " + 거시 컨펌 우회" if not is_macro_confirmed else " + 5분봉 거시 컨펌 완료"
                        self.logger.warning(
                            f"[{code}] [SIGNAL] 1분봉 {signal_type}{suffix}! "
                            f"AI 최종 승인 ({prob_str}) -> 매수 실행!"
                        )
                    else:
                        if not is_macro_confirmed and not self.bypass_macro:
                            self.logger.info(f"[{code}] [SCAN] 1분봉 {signal_type} 감지! (5분봉 대기 중...)")
                            return False
                        
                        suffix = " + 거시 우회" if not is_macro_confirmed else " + 5분봉 거시 컨펌"
                        self.logger.warning(f"[{code}] [SIGNAL] 1분봉 이중 다이버전스{suffix}! 매수 실행!")
                    
                    self.macro_states[code] = False
                    self.signal_cooldowns[code] = now_ts
                    return True
                else:
                    self.logger.info(f"[{code}] [LIMIT] 1분봉 다이버전스는 형성되었으나, 주가({last_price:,})가 BB하단({bb_lower:,.1f}) 대비 너무 많이 반등했습니다.")
            else:
                self.logger.debug(f"[{code}] [WAIT] 아직 1분봉상 '이중 바닥(Divergence)' 신호가 완성되지 않았습니다.")
        return False

    def _get_cached_indicators(self, code, timeframe, df):
        """[v5.1] 고성능 캐싱: 백테스트 시에는 이미 계산된 컬럼이 있으면 즉시 반환"""
        if 'RSI' in df.columns and 'BB_Lower' in df.columns:
            return df
            
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
        rsi_period = int(os.getenv("INDICATOR_RSI_PERIOD", "12"))
        
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
        df['EMA120'] = df['close'].ewm(span=120, adjust=False).mean() # 장기 추세 필터
        df['STD20'] = df['close'].rolling(window=bb_period).std()
        df['BB_Upper'] = df['MA20'] + (df['STD20'] * bb_std)
        df['BB_Lower'] = df['MA20'] - (df['STD20'] * bb_std)
            
        return df.fillna(0)

    def _check_bullish_divergence(self, df: pd.DataFrame, window=60, strict=True, distance=2):
        """
        [v5.0 개편] 멀티 포인트 다이버전스 탐지 알고리즘
        가장 최근 저점을 기준으로 이전 윈도우 내 모든 저점들과 비교하여 하나라도 다이버전스가 맞으면 승인
        """
        df_recent = df.iloc[-window:]
        if len(df_recent) < 20: return False

        prices = df_recent['close'].values
        rsis = df_recent['RSI'].values
        valleys = self._find_valleys(prices, distance=distance)

        if len(valleys) < 2: return False

        # 최신 저점 정보
        curr_idx = valleys[-1]
        curr_price = prices[curr_idx]
        curr_rsi = rsis[curr_idx]
        
        # 다이버전스 타점 인정 RSI 한도 (캐싱된 변수 사용)
        rsi_limit = self.rsi_limit

        # 이전 저점들을 순회하며 다이버전스 탐색 (최근 것부터 역순)
        for i in range(len(valleys)-2, -1, -1):
            prev_idx = valleys[i]
            prev_price = prices[prev_idx]
            prev_rsi = rsis[prev_idx]
            
            # 1. 가격 하락 & RSI 상승 여부
            price_falling = curr_price < prev_price
            rsi_rising = curr_rsi > prev_rsi
            
            if price_falling and rsi_rising:
                rsi_slope = curr_rsi - prev_rsi
                price_drop_pct = (prev_price - curr_price) / prev_price
                
                # Class B+ 필터 (실전형 보정)
                is_valid_signal = rsi_slope >= 0.5 and price_drop_pct >= 0.001
                
                if not getattr(self, 'is_aggressive', False) and not is_valid_signal:
                    continue # 다음 이전 저점으로 넘어가 탐색 계속
                
                if strict and prev_rsi > rsi_limit:
                    continue

                # 하나라도 맞으면 즉시 성공 반환
                return True
                
        return False
