import pandas as pd
import logging
from strategy.stefano_strategy import StefanoStrategy
from backtest.virtual_broker import VirtualBroker

class BacktestEngine:
    """백테스팅 엔진 핵심 클래스"""
    def __init__(self, broker: VirtualBroker, strategy: StefanoStrategy):
        self.logger = logging.getLogger("DopamingBot.Backtest.Engine")
        self.broker = broker
        self.strategy = strategy
        
        # 전략 설정 (v3.3 최적화: 익절폭 확대 및 손익비 개선)
        self.target_profit = 0.035  # 3.5% 익절
        self.stop_loss = -0.020     # 2.0% 손절

    def run(self, code, df_1m):
        """단일 종목 백테스트 실행"""
        self.logger.info(f"--- [{code}] 백테스트 시작 (데이터: {len(df_1m)}개) ---")
        
        # 1. 5분봉 사전 리샘플링
        df_5m_full = df_1m.resample('5T').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()

        # 2. 본 루프 (1분 단위 시뮬레이션)
        # 윈도우 확보를 위해 60봉 이후부터 시작
        for i in range(60, len(df_1m) - 1):
            current_time = df_1m.index[i]
            current_price = df_1m['close'].iloc[i]
            
            # 현재 시점까지의 데이터 공급 (슬라이싱)
            sliced_1m = df_1m.iloc[:i+1]
            # 5분봉은 현재 시각보다 작거나 같은 것들만 공급
            sliced_5m = df_5m_full[df_5m_full.index <= current_time]
            
            # 2.1 포지션 모니터링 (이미 보유 중인 경우)
            if code in self.broker.positions:
                pos = self.broker.positions[code]
                buy_price = pos['buy_price']
                
                # 실질 수익률 (수수료/슬리피지 미포함 raw 가격 기준이지만 Broker 반영 시 계산됨)
                # 여기서는 단순 손익절 판별
                profit_rate = (current_price - buy_price) / buy_price
                
                if profit_rate >= self.target_profit:
                    # 익절 (다음 봉 시가 체결)
                    next_open = df_1m['open'].iloc[i+1]
                    self.broker.sell(code, next_open, pos['qty'], df_1m.index[i+1])
                    continue
                elif profit_rate <= self.stop_loss:
                    # 손절 (다음 봉 시가 체결)
                    next_open = df_1m['open'].iloc[i+1]
                    self.broker.sell(code, next_open, pos['qty'], df_1m.index[i+1])
                    continue

            # 2.2 전략 분석 (매수 신호 탐색)
            # StefanoStrategy.analyze()는 내부적으로 지표 연산을 수행함
            buy_signal = self.strategy.analyze(code, sliced_1m, sliced_5m)
            
            if buy_signal:
                # 매수 신호 발생 -> 다음 봉 시가로 매수
                next_open = df_1m['open'].iloc[i+1]
                # 투자 비중 10% 가정 (가상 자산의 10%)
                target_budget = self.broker.get_total_asset_value({code: current_price}) * 0.1
                qty = int(target_budget / next_open)
                
                if qty > 0:
                    self.broker.buy(code, next_open, qty, df_1m.index[i+1])

        self.logger.info(f"--- [{code}] 백테스트 종료 ---")
        return self.broker.get_summary()

def calculate_total_profit(initial, final):
    return ((final - initial) / initial) * 100
