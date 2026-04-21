import pandas as pd
import logging
import os
from strategy.stefano_strategy import StefanoStrategy
from backtest.virtual_broker import VirtualBroker

class BacktestEngine:
    """백테스팅 엔진 핵심 클래스"""
    def __init__(self, broker: VirtualBroker, strategy: StefanoStrategy):
        self.logger = logging.getLogger("DopamingBot.Backtest.Engine")
        self.broker = broker
        self.strategy = strategy
        
        # [v4.5] 실거래와 동일한 거래 마찰 비용(Friction) 로드
        self.trading_friction = float(os.getenv("TRADING_FRICTION", "0.0025"))
        
        # [v3.6/v4.4] 환경 변수(.env)에서 정밀 파라미터 로드 (누락분 복구)
        self.target_profit = float(os.getenv("TRADE_TARGET_PROFIT", "0.015"))
        self.stop_loss     = float(os.getenv("TRADE_STOP_LOSS", "-0.018"))
        
        # [v3.6] 본절가 보호(Breakeven Shield) 설정 로드
        self.breakeven_trigger = float(os.getenv("TRADE_BREAKEVEN_TRIGGER", "0.01"))
        self.breakeven_stop    = float(os.getenv("TRADE_BREAKEVEN_PROTECT", "0.003"))

        # [v4.7] 스캘핑 전용 타임컷(Time-Cut) 삭제 (VCP 추세 무한 보유 허용)
        # self.exit_minutes = int(os.getenv("STRATEGY_EXIT_MINUTES", "10"))
        
        # [v12.0] 스마트 익절 / 데드존 파라미터화
        self.smart_exit_min_profit = float(os.getenv("SMART_EXIT_MIN_PROFIT", "0.012"))
        self.deadzone_minutes     = float(os.getenv("DEADZONE_MINUTES", "5"))
        self.deadzone_min_profit  = float(os.getenv("DEADZONE_MIN_PROFIT", "0.010"))
        
        # [v5.1] 최적화: 로깅 레벨 체크용 플래그
        self.debug_enabled = self.logger.isEnabledFor(logging.DEBUG)
        
        self.logger.info(f"[설정 로드] TP: {self.target_profit*100:.1f}%, SL: {self.stop_loss*100:.1f}%, Friction: {self.trading_friction*100:.2f}%")

    def run(self, code, df_1m, df_1m_pre=None, df_5m_pre=None):
        """단일 종목 백테스트 실행
        df_1m_pre/df_5m_pre: 사전 계산된 지표 DataFrame (그리드서치 고속 모드)
        """
        self.logger.info(f"--- [{code}] 백테스트 시작 (데이터: {len(df_1m)}개) ---")
        
        if df_1m_pre is not None and df_5m_pre is not None:
            # [고속 모드] 사전 계산된 지표 직접 사용 (재계산 생략)
            df_1m = df_1m_pre
            df_5m_full = df_5m_pre
        else:
            # 1. 5분봉 사전 리샘플링 (루프 밖으로 이동)
            df_5m_full = df_1m.resample('5T').agg({
                'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
            }).dropna()
            
            # 지표 선행 계산 (1분봉 & 5분봉 전체 기간에 대해 한 번만 수행)
            df_1m = self.strategy._calculate_indicators(df_1m)
            df_5m_full = self.strategy._calculate_indicators(df_5m_full)

        # [v3.8] 본절가 보호 및 트레일링 스탑 상태 추적
        reached_breakeven = False
        trailing_activated = False
        
        # [v4.5] 트레일링 스탑 설정도 .env에서 로드
        trailing_activation_rate = float(os.getenv("TRAILING_STOP_ACTIVATION", "0.02"))
        trailing_callback = float(os.getenv("TRAILING_STOP_CALLBACK", "0.004"))
        high_price = 0.0

        # 2. 본 루프 (1분 단위 시뮬레이션)
        for i in range(60, len(df_1m) - 1):
            current_time = df_1m.index[i]
            current_price = df_1m['close'].iloc[i]
            
            # [로깅 최적화] 매 분봉 데이터 기록 생략 (백테스트 가속)
            # self.logger.debug(f"[{current_time}] PRICE:{current_price:,.0f} | HOLD:{code in self.broker.positions}")
            
            # [v3.7] 당일 청산 규칙 (오후 3시 20분 이후 오버나잇 금지)
            is_closing_time = current_time.hour == 15 and current_time.minute >= 20
            
            # 2.1 포지션 모니터링
            if code in self.broker.positions:
                pos = self.broker.positions[code]
                buy_price = pos['buy_price']
                # [v6.0] 보유 시간 계산을 상단으로 이동하여 모든 청산 로직에서 사용 가능하게 함
                hold_duration = (current_time - pos['buy_time']).total_seconds() / 60
                
                # [Intra-Bar 3D 스캐닝] 분봉 내 고/저가를 활용한 엄격한 수익률 판별 (생존 편향/Look-ahead Bias 제거)
                current_low = df_1m['low'].iloc[i]
                current_high = df_1m['high'].iloc[i]
                
                profit_rate_close = (current_price - buy_price) / buy_price
                profit_rate_low = (current_low - buy_price) / buy_price
                profit_rate_high = (current_high - buy_price) / buy_price
                
                # 종가 기준 보조 지표
                profit_rate = profit_rate_close
                
                # [로깅 최적화] 보유 시 실시간 수익률 기록 생략
                # self.logger.debug(f"   >> ROI:{profit_rate*100:.2f}% | High:{high_price:,.0f} | BreakEven:{reached_breakeven}")

                # [v11.0] 시그널에 따른 동적 손익절 파라미터 적용
                signal = pos.get('signal_type', '상향돌파')
                if signal == '눌림목반등':
                    current_target = 0.016
                    current_stop_limit = -0.012
                    current_trailing_activation = 0.010
                    current_trailing_callback = 0.005
                else:
                    current_target = self.target_profit
                    current_stop_limit = self.stop_loss
                    current_trailing_activation = trailing_activation_rate
                    current_trailing_callback = trailing_callback

                # 최고가 갱신 (인트라바 고점 기준 트레일링)
                if current_high > high_price:
                    high_price = current_high
                
                # [0순위 방어] 최악(Worst-Case) 손절 판별 - 꼬리를 내리꽂아 손절선을 건드렸는가?
                applied_stop_limit = 0.010 if reached_breakeven else current_stop_limit
                if profit_rate_low <= applied_stop_limit:
                    stop_execution_price = buy_price * (1.0 + applied_stop_limit)
                    next_open = df_1m['open'].iloc[i+1]
                    # 시가가 이미 손절선을 뚫고 갭락했다면 시가 체결, 아니면 손절선 체결 (보수적)
                    execution_price = min(stop_execution_price, next_open)
                    
                    self.logger.info(f"[{code}] [SELL] 강제 손절선 터치 ({signal}) - 저가 마진: {profit_rate_low*100:.2f}% (Limit: {applied_stop_limit*100:.2f}%)")
                    self.broker.sell(code, execution_price, pos['qty'], df_1m.index[i+1])
                    reached_breakeven = False
                    trailing_activated = False
                    continue
                
                # A. 익절 판별 (인트라바 고점 터치 기준)
                if profit_rate_high >= current_target:
                    target_execution_price = buy_price * (1.0 + current_target)
                    next_open = df_1m['open'].iloc[i+1]
                    execution_price = max(target_execution_price, next_open)
                    self.logger.info(f"[{code}] [SELL] 목표 익절가 달성 ({signal}) - 고가 마진: {profit_rate_high*100:.2f}%")
                    self.broker.sell(code, execution_price, pos['qty'], df_1m.index[i+1])
                    reached_breakeven = False
                    trailing_activated = False
                    continue
                
                # [v12.0] 스마트 익절: 최소 수익률 기준을 env로 제어 (SMART_EXIT_MIN_PROFIT)
                bb_upper = df_1m['BB_Upper'].iloc[i]
                if current_price >= bb_upper and profit_rate > self.smart_exit_min_profit:
                    next_open = df_1m['open'].iloc[i+1]
                    self.logger.info(f"[{code}] [SELL] [SMART] BB 상단 터치 익절 ({signal}): {profit_rate*100:.2f}%")
                    self.broker.sell(code, next_open, pos['qty'], df_1m.index[i+1])
                    reached_breakeven = False
                    trailing_activated = False
                    continue
                
                # B. [v3.7] 당일 강제 청산
                if is_closing_time:
                    next_open = df_1m['open'].iloc[i+1]
                    self.logger.info(f"[{code}] [SELL] 장마감 강제 청산 (수익률: {profit_rate*100:.2f}%)")
                    self.broker.sell(code, next_open, pos['qty'], df_1m.index[i+1])
                    reached_breakeven = False
                    trailing_activated = False
                    continue

                # C. 본절 보호 및 트레일링 활성화 판별
                # [v6.0 AI-Sniper] 강화된 본절 보호: 1.5% 도달 시 확실한 순익(+0.1% Net) 확보 구역 진입
                if not reached_breakeven and profit_rate >= 0.015: 
                    reached_breakeven = True
                    self.logger.info(f"[{code}] [SHIELD] 수수료 극복 구간 진입(+1.5%), 실질 본절 매도 상향")

                if not reached_breakeven and profit_rate >= self.breakeven_trigger:
                    reached_breakeven = True
                    self.logger.debug(f"[{code}] [SHIELD] 본절가 보호 시작 (+{self.breakeven_trigger*100:.1f}% 도달)")
                
                # [트레일링 활성화] 고점(High)이 트리거를 건드렸는가?
                if not trailing_activated and profit_rate_high >= current_trailing_activation:
                    trailing_activated = True
                    self.logger.debug(f"[{code}] [TRAILING] 추적 익절 활성화 (+{current_trailing_activation*100:.1f}% 도달)")
                
                # D. 트레일링 스탑 청산 판별 (저가가 트레일링 방어선을 깼는가?)
                if trailing_activated:
                    trailing_stop_price = high_price * (1.0 - current_trailing_callback)
                    if current_low <= trailing_stop_price:
                        next_open = df_1m['open'].iloc[i+1]
                        execution_price = min(trailing_stop_price, next_open)
                        real_price_profit = (execution_price - buy_price) / buy_price
                        self.logger.info(f"[{code}] [SELL] [TRAILING] 트레일링 이탈 청산 ({signal}, 체결수익: {real_price_profit*100:.2f}%)")
                        self.broker.sell(code, execution_price, pos['qty'], df_1m.index[i+1])
                        reached_breakeven = False
                        trailing_activated = False
                        continue

                # [v12.0] 데드존 파라미터화: VCP 전략 도입으로 기본값 99분(비활성화) 권장
                if self.deadzone_minutes <= hold_duration < 999: # 상한선 해제
                    if profit_rate < self.deadzone_min_profit: 
                        next_open = df_1m['open'].iloc[i+1]
                        self.logger.info(f"[{code}] [SELL] [DEADZONE] {self.deadzone_minutes:.0f}분 내 수익권 미진출로 탈출 ({signal}, 수익률: {profit_rate*100:.2f}%)")
                        self.broker.sell(code, next_open, pos['qty'], df_1m.index[i+1])
                        reached_breakeven = False
                        trailing_activated = False
                        continue

                # G. 보유 시간 제약을 해제하고 오직 트레일링/손절/익절 선에서만 청산함
            else:
                reached_breakeven = False
                trailing_activated = False
                high_price = 0.0

            # 2.2 전략 분석 (매수 신호 탐색)
            # [최적화] 매분 슬라이싱 대신, 인덱스 기반으로 필요한 시점의 데이터만 전달
            if current_time.hour == 14 and current_time.minute >= 30:
                continue
            if current_time.hour >= 15:
                continue

            # [핵심 최적화] 이미 지표가 계산된 전체 DF에서 현재 시점까지의 뷰(View)만 전달
            # analyze 함수 내부에서 다시 지표를 계산하지 않도록 stefano_strategy 수정 필요
            sliced_1m = df_1m.iloc[:i+1]
            sliced_5m = df_5m_full[df_5m_full.index <= current_time]
            is_signal, signal_type = self.strategy.analyze(code, sliced_1m, sliced_5m)
            
            # [버그 픽스] 중복 매수 방지 및 시그널 튜플 파싱
            if is_signal and code not in self.broker.positions:
                next_open = df_1m['open'].iloc[i+1]
                # [백테스트] 투자금 풀매수 (종목당 할당된 500만원을 100% 한 번에 진입)
                target_budget = self.broker.balance * 0.99
                qty = int(target_budget / next_open)
                
                if qty > 0:
                    self.logger.info(f"[{code}] [BUY] 진입 완료 ({signal_type}): {next_open:,.0f} | 수량: {qty}주")
                    self.broker.buy(code, next_open, qty, df_1m.index[i+1], signal_type=signal_type)
                    high_price = next_open

        self.logger.info(f"--- [{code}] 백테스트 종료 ---")
        return self.broker.get_summary()

def calculate_total_profit(initial, final):
    return ((final - initial) / initial) * 100
