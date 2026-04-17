"""
StefanoStrategy 모듈
----------------------
스테파노의 'RSI 다이버전스' 매매 기법을 시스템화한 로직입니다.
1. 거시적 타임프레임(5분봉)에서 하락 다이버전스가 발생하는지 (가격은 하락하나 RSI는 상승) 검증합니다.
2. 거시적 다이버전사가 감지된 상태에서 미시적 타임프레임(1분봉)의 다이버전스가 이중으로 감지되면 매수 시그널을 발생시킵니다.
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
        # [v11.4] HFS Golden Ratio 필터 (추세 강도 임계치)
        self.HFS_GOLDEN_RATIO = 0.618

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
        self.buy_start_time     = os.getenv("STRATEGY_BUY_START_TIME", "09:00")
        self.buy_end_time       = os.getenv("STRATEGY_BUY_END_TIME", "14:30")
        self.pullback_gap       = float(os.getenv("STRATEGY_PULLBACK_GAP", "0.005"))
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
        
        # [v5.9] 전략 모드 로드 (MEAN_REVERSION / BREAKOUT)
        self.strategy_mode = os.getenv("STRATEGY_MODE", "MEAN_REVERSION").upper()
        self.logger.info(f"[전략 모드] 현재 {self.strategy_mode} 모드로 작동 중입니다.")

    def _find_valleys(self, arr, distance=2):
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
        self.ai_engine      = ai_engine
        self.data_collector = data_collector
        self.logger.info("[OK] AI 모듈 연결 완료 (AIEngine + DataCollector)")

    def _extract_ai_features(self, code, df_1m: pd.DataFrame, df_5m: pd.DataFrame,
                              macro_div: bool, micro_div: bool) -> dict:
        try:
            last_price = df_1m['close'].iloc[-1]
            rsi_1m = float(df_1m['RSI'].iloc[-1]) if 'RSI' in df_1m.columns else 50.0
            rsi_5m = float(df_5m['RSI'].iloc[-1]) if 'RSI' in df_5m.columns else 50.0
            bb_pct_1m = (last_price - df_1m['BB_Lower'].iloc[-1]) / df_1m['BB_Lower'].iloc[-1] if 'BB_Lower' in df_1m.columns and df_1m['BB_Lower'].iloc[-1] > 0 else 0.0
            bb_pct_5m = (df_5m['close'].iloc[-1] - df_5m['BB_Lower'].iloc[-1]) / df_5m['BB_Lower'].iloc[-1] if 'BB_Lower' in df_5m.columns and df_5m['BB_Lower'].iloc[-1] > 0 else 0.0
            vol_mean = df_1m['volume'].iloc[-20:].mean()
            vol_ratio = float(df_1m['volume'].iloc[-1] / vol_mean) if vol_mean > 0 else 1.0
            price_chg_5 = float((last_price - df_1m['close'].iloc[-6]) / df_1m['close'].iloc[-6]) if len(df_1m) >= 6 else 0.0
            price_chg_20 = float((last_price - df_1m['close'].iloc[-21]) / df_1m['close'].iloc[-21]) if len(df_1m) >= 21 else 0.0
            return {
                "rsi_1m": rsi_1m, "rsi_5m": rsi_5m, "bb_pct_1m": round(bb_pct_1m, 6), "bb_pct_5m": round(bb_pct_5m, 6),
                "vol_ratio": round(vol_ratio, 4), "price_chg_5": round(price_chg_5, 6), "price_chg_20": round(price_chg_20, 6),
                "macro_div": int(macro_div), "micro_div": int(micro_div),
                "bb_width": round(df_1m['BB_Upper'].iloc[-1] - df_1m['BB_Lower'].iloc[-1], 2),
                "near_high": round(last_price / df_1m['high'].max(), 4) if df_1m['high'].max() > 0 else 1.0,
                "ema_slope": round(df_1m['EMA120_Slope'].iloc[-1], 6) if 'EMA120_Slope' in df_1m.columns else 0.0,
                "is_aggressive": int(self.is_aggressive),
            }
        except Exception as e:
            self.logger.debug("[%s] Feature 추출 오류 (기본값 사용): %s", code, e)
            return {col: 0.0 for col in ["rsi_1m","rsi_5m","bb_pct_1m","bb_pct_5m","vol_ratio","price_chg_5","price_chg_20","macro_div","micro_div","is_aggressive"]}

    def analyze(self, code, df_1m: pd.DataFrame, df_5m: pd.DataFrame):
        if df_1m.empty or df_5m.empty: return False, ""
        vol_mean = df_1m['volume'].iloc[-20:-1].mean()
        vol_ratio_scanner = df_1m['volume'].iloc[-1] / vol_mean if vol_mean > 0 else 1.0
        day_high, day_low = df_1m['high'].max(), df_1m['low'].min()
        day_range_pct = (day_high - day_low) / day_low if day_low > 0 else 0
        vol_threshold = 0.7 if self.is_aggressive else 1.3
        range_threshold = 0.003 if self.is_aggressive else 0.018
        if self.scanner_soft_mode: vol_threshold, range_threshold = 1.01, 0.001
        now_dt = df_1m.index[-1]
        
        # [v7.4 Golden Hour] 시장의 화력이 가장 강력한 오전 특정 시간에만 진입 허용
        current_time_str = now_dt.strftime("%H:%M")
        if not (self.buy_start_time <= current_time_str <= self.buy_end_time):
            self.logger.debug(f"[{code}] [PASS] 매매 가능 시간 아님 ({current_time_str})")
            return False, ""
            
        seconds_passed = now_dt.second if hasattr(now_dt, 'second') else 30
        time_weight = max(seconds_passed, 5) / 60.0
        dynamic_vol_threshold = vol_threshold * time_weight
        if vol_ratio_scanner < dynamic_vol_threshold or day_range_pct < range_threshold: return False, ""
        min_5m_len = 5 if self.bypass_macro else 20
        if len(df_1m) < 20 or len(df_5m) < min_5m_len: return False, ""
        df_1m = self._get_cached_indicators(code, "1m", df_1m)
        df_5m = self._get_cached_indicators(code, "5m", df_5m)
        macro_div = self._check_bullish_divergence(df_5m, window=self.check_window, distance=2)
        current_time = df_5m.index[-1]
        last_price = df_1m['close'].iloc[-1]
        if self.data_collector: self.data_collector.update_labels(code, last_price)
        if macro_div:
            if not self.macro_states.get(code, False):
                self.logger.info(f"[{code}] [MACRO] 5분봉 거시 신호 포착! 1분봉 타점 대기...")
                self.macro_states[code] = True
                self._indicator_cache[f"{code}_macro_time"] = current_time
        else:
            if self.macro_states.get(code, False):
                entry_time = self._indicator_cache.get(f"{code}_macro_time")
                rsi_5m = df_5m['RSI'].iloc[-1]
                time_diff = (current_time - entry_time).total_seconds() / 60 if entry_time else 0
                if time_diff > 60 or rsi_5m > self.macro_rsi_exit:
                    self.macro_states[code] = False
                    reason = "시간 경과(60분)" if time_diff > 60 else f"추세 회복(RSI > {self.macro_rsi_exit})"
                    self.logger.info(f"[{code}] 거시 신호 초기화: {reason}")
        is_visible = self.bypass_macro or self.macro_states.get(code, False)
        if is_visible:
            micro_div = self._check_bullish_divergence(df_1m, window=self.check_window, strict=False, distance=1)
            rsi_1m, bb_lower = df_1m['RSI'].iloc[-1], df_1m['BB_Lower'].iloc[-1]
            ema120 = df_1m['EMA120'].iloc[-1]
            vol_mean = df_1m['volume'].iloc[-20:-1].mean()
            vol_ratio = df_1m['volume'].iloc[-1] / vol_mean if vol_mean > 0 else 1.0
            is_green_candle = last_price > df_1m['open'].iloc[-1]
            is_recovering = rsi_1m >= 30.0
            is_uptrend = last_price > ema120 if self.require_uptrend else True
            is_vol_spike = vol_ratio >= self.vol_spike_threshold if self.require_vol_spike else True
            is_rsi_hook = rsi_1m >= df_1m['RSI'].iloc[-2] if len(df_1m) >= 2 else True
            now_ts = df_1m.index[-1].timestamp()
            if now_ts - self.signal_cooldowns.get(code, 0) < self.signal_cooldown_limit: return False, ""
            bb_upper = df_1m['BB_Upper'].iloc[-1]
            bb_width = (bb_upper - bb_lower) / ((bb_upper + bb_lower) / 2) if (bb_upper + bb_lower) > 0 else 0
            if bb_width > self.bb_width_limit: return False, ""
            min_price_10m = df_1m['low'].iloc[-10:].min()
            actual_recovery = (last_price / min_price_10m) - 1
            rsi_5m = df_5m['RSI'].iloc[-1]
            is_macro_oversold = rsi_5m <= 40.0
            adj_min_recovery = self.min_recovery_rate * 0.5 if self.is_aggressive else self.min_recovery_rate
            is_v_bottom = (rsi_1m <= self.nitro_rsi_limit and is_rsi_hook and (last_price <= bb_lower * self.nitro_bb_gap) and (actual_recovery >= adj_min_recovery))
            is_entry_signal = (is_v_bottom or (micro_div and is_recovering)) and is_green_candle and is_macro_oversold

            # [v11.4 HFS Golden Ratio] 변동성 강도(Strength) 검증
            # BB Width와 RSI를 조합한 상대 강도가 황금비(0.618)를 넘어야 진정한 분출로 인정
            relative_strength = (rsi_1m / 100.0) / (bb_width * 10.0 + 1e-9)
            if relative_strength < self.HFS_GOLDEN_RATIO:
                self.logger.debug(f"[{code}] [HFS] 강도 미달 ({relative_strength:.3f} < {self.HFS_GOLDEN_RATIO}) - 진입 차단")
                return False, ""

            if self.strategy_mode == "BREAKOUT":
                # [v9.2 Macro Precision] 5분봉 단기/중기 정배열 컨펌 (승률 향상)
                is_macro_aligned = df_5m['EMA20'].iloc[-1] > df_5m['EMA60'].iloc[-1]
                is_macro_up = df_5m['EMA120_Slope'].iloc[-1] > 0 if 'EMA120_Slope' in df_5m.columns else True
                
                is_breakout = self._check_trend_breakout(df_1m)
                
                # [v8.1] 눌림목 추가 타점 확보
                is_pullback = self._check_pullback_entry(df_1m)
                
                if not (is_breakout or is_pullback): return False, ""
                
                if is_breakout:
                    # 돌파 매매는 강력한 거시 정배열이 필수
                    if not (is_macro_up and is_macro_aligned): return False, ""
                    signal_type = "상향돌파"
                else:
                    signal_type = "눌림목반등"
                    if not is_macro_up: return False, ""
            else:
                if not is_entry_signal: return False, ""
                signal_type = "다이버전스" if micro_div else "V자반등"

            is_macro_confirmed = self.macro_states.get(code, False)
            features = self._extract_ai_features(code, df_1m, df_5m, is_macro_confirmed, micro_div)
            if self.ai_engine:
                approved, prob = self.ai_engine.approve(features)
                if not approved: return False, ""
                suffix = " + 추세 탑승" if self.strategy_mode == "BREAKOUT" else (" + 거시 우회" if not is_macro_confirmed else " + 5분봉 컨펌")
                self.logger.warning(f"[{code}] [SIGNAL] 1분봉 {signal_type}{suffix}! AI 승인({prob*100:.1f}%) -> 매수!")
            else:
                if self.strategy_mode != "BREAKOUT" and not is_macro_confirmed and not self.bypass_macro: return False, ""
                suffix = " + 추세 탑승" if self.strategy_mode == "BREAKOUT" else (" + 거시 우회" if not is_macro_confirmed else " + 5분봉 컨펌")
                self.logger.warning(f"[{code}] [SIGNAL] 1분봉 {signal_type}{suffix}! 매수 실행!")
            
            # [v10.0] 진입 시 Feature 스냅샷 저장
            if self.data_collector:
                self.data_collector.capture_signal(code, features, last_price)

            self.macro_states[code] = False
            self.signal_cooldowns[code] = now_ts
            return True, signal_type
        return False, ""

    def _get_cached_indicators(self, code, timeframe, df):
        if 'RSI' in df.columns and 'BB_Lower' in df.columns: return df
        current_last_idx = df.index[-1]
        if (code, timeframe) in self._indicator_cache:
            last_idx, cached_df = self._indicator_cache[(code, timeframe)]
            if last_idx == current_last_idx and len(cached_df) == len(df): return cached_df
        calculated_df = self._calculate_indicators(df)
        self._indicator_cache[(code, timeframe)] = (current_last_idx, calculated_df)
        return calculated_df

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        rsi_period = int(os.getenv("INDICATOR_RSI_PERIOD", "14"))
        delta = df['close'].diff()
        up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
        alpha = 1 / rsi_period
        roll_up = up.ewm(alpha=alpha, min_periods=rsi_period, adjust=False).mean()
        roll_down = down.ewm(alpha=alpha, min_periods=rsi_period, adjust=False).mean()
        df['RSI'] = 100.0 - (100.0 / (1.0 + roll_up / (roll_down + 1e-9)))
        ema12 = df['volume'].ewm(span=12, adjust=False).mean()
        ema26 = df['volume'].ewm(span=26, adjust=False).mean()
        df['VO'] = np.where(ema26 != 0, ((ema12 - ema26) / ema26) * 100.0, 0.0)
        bb_period, bb_std = int(os.getenv("INDICATOR_BB_PERIOD", "20")), float(os.getenv("INDICATOR_BB_STD", "2.0"))
        df['MA20'] = df['close'].rolling(window=bb_period).mean()
        df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['EMA20_Slope'] = df['EMA20'].diff(3) / df['EMA20'].shift(3).replace(0, np.nan)
        df['EMA60'] = df['close'].ewm(span=60, adjust=False).mean()
        df['EMA120'] = df['close'].ewm(span=120, adjust=False).mean()
        df['EMA120_Slope'] = df['EMA120'].diff(10) / df['EMA120'].shift(10).replace(0, np.nan)
        df['STD20'] = df['close'].rolling(window=bb_period).std()
        df['BB_Upper'] = df['MA20'] + (df['STD20'] * bb_std)
        df['BB_Lower'] = df['MA20'] - (df['STD20'] * bb_std)
        
        # [v7.6] BB Width 계산 (변동성 확장 확인용)
        middle = (df['BB_Upper'] + df['BB_Lower']) / 2
        df['BB_Width'] = (df['BB_Upper'] - df['BB_Lower']) / middle.replace(0, np.nan)
        
        return df.fillna(0)

    def _check_bullish_divergence(self, df: pd.DataFrame, window=60, strict=True, distance=2):
        df_recent = df.iloc[-window:]
        if len(df_recent) < 20: return False
        prices, rsis = df_recent['close'].values, df_recent['RSI'].values
        valleys = self._find_valleys(prices, distance=distance)
        if len(valleys) < 2: return False
        curr_price, curr_rsi = prices[valleys[-1]], rsis[valleys[-1]]
        for i in range(len(valleys)-2, -1, -1):
            prev_price, prev_rsi = prices[valleys[i]], rsis[valleys[i]]
            if curr_price < prev_price and curr_rsi > prev_rsi:
                if (curr_rsi - prev_rsi) >= 0.5 and (prev_price - curr_price) / prev_price >= 0.001:
                    if not strict or prev_rsi <= self.rsi_limit: return True
        return False

    def _check_trend_breakout(self, df: pd.DataFrame):
        if len(df) < 20: return False
        last = df.iloc[-1]
        prev = df.iloc[-2]  # [v7.4] 직전 봉 비교를 위해 추가
        
        price_break = last['close'] > last['BB_Upper']
        vol_mean = df['volume'].iloc[-20:-1].mean()
        vol_ratio = last['volume'] / vol_mean if vol_mean > 0 else 1.0
        
        # [v9.4.1 Optimal] 수급 문턱 미세 조정 + 가속도 확인 (하드코딩 해제하고 env 연동)
        is_accelerating = last['volume'] > prev['volume'] * 1.5
        is_vol_spike = vol_ratio >= self.vol_spike_threshold and is_accelerating
        
        # [v9.5] 완전 정배열 (Super Growth) & 가속도 증가 컨펌
        is_aligned = last['EMA20'] > last['EMA60'] > last['EMA120']
        prev_slope = df['EMA20_Slope'].iloc[-2]
        is_momentum = last['EMA20_Slope'] > 0.001 and last['EMA20_Slope'] > prev_slope
        
        # [v9.4.1] 변동성 확장 기준 진입장벽 완화 (1.05x)
        prev_width = df['BB_Width'].iloc[-2]
        is_exploding = last['BB_Width'] > prev_width * 1.05
        
        # [v9.5] 2연속 양봉 확인 (Strength Confirmation)
        is_double_green = last['close'] > last['open'] and prev['close'] > prev['open']
        
        # [v9.4.1] RSI 분출 구간 확장 (55~82)
        is_rsi_safe = 55.0 <= last['RSI'] <= 82.0
        
        # [v9.4 Crown] 이평선(MA20) 지격도 조건 완화
        is_supported = last['close'] > last['MA20'] * 1.002
        
        if price_break and is_vol_spike and is_aligned and is_rsi_safe and is_momentum and is_exploding and is_supported and is_double_green and last['close'] > last['open'] and last['VO'] > 25.0 and last['EMA120_Slope'] > 0:
            self.logger.debug(f"[BREAKOUT] v9.5 Victory 서지 감지! (RVOL:{vol_ratio:.1f}, RSI:{last['RSI']:.1f})")
            return True
        return False

    def _check_pullback_entry(self, df: pd.DataFrame):
        """[v8.3 Precision] 수익성이 검증된 정예 눌림목만 포착"""
        if len(df) < 10: return False
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 1. 완벽 정배열 (Super Uptrend)
        is_aligned = last['EMA20'] > last['EMA60'] > last['EMA120']
        if not is_aligned: return False
        
        # 2. 터치 확인 (최근 3개 봉 이내에 EMA20을 터치했는가)
        touched = any(df['low'].iloc[-3:] <= df['EMA20'].iloc[-3:] * 1.002)
        
        # 3. 과열 해소 확인 (RSI 바닥 컨펌) - [v8.3]
        # 직전 봉들 중 RSI가 45 미만으로 충분히 식었어야 함
        is_cooled_down = df['RSI'].iloc[-5:-1].min() < 45.0
        
        # 4. 바운스 확인 (양봉 몸통 0.3% + 거래량 가속 + RSI 회복) - [v8.3]
        body_pct = (last['close'] / last['open']) - 1
        is_strong_rebound = body_pct >= 0.003
        
        vol_mean_5m = df['volume'].iloc[-6:-1].mean()
        is_vol_surge = last['volume'] > vol_mean_5m * 1.2
        
        is_rebound_confirm = is_strong_rebound and is_vol_surge and last['RSI'] > 50
        
        # 5. 이평선과의 거리
        dist_to_ema20 = (last['close'] / last['EMA20']) - 1
        is_close_enough = 0 < dist_to_ema20 <= self.pullback_gap
        
        if touched and is_cooled_down and is_rebound_confirm and is_close_enough:
            self.logger.debug(f"[PULLBACK] Precision Dynamo! (Body:{body_pct*100:.2f}%, RSI:{last['RSI']:.1f})")
            return True
        return False
