import sys
import os
import time

base_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(base_dir)

import logging
logging.disable(logging.CRITICAL)

from dotenv import load_dotenv
load_dotenv()

from backtest.historical_data_manager import HistoricalDataManager
from backtest.virtual_broker import VirtualBroker
from backtest.engine import BacktestEngine
from strategy.stefano_strategy import StefanoStrategy
from core.ai_engine import AIEngine

def main():
    friction = float(os.environ.get("TRADING_FRICTION", "0.0025"))
    tax_rate = 0.002
    remain = max(0, friction - tax_rate)
    fee_rate = (remain * 0.7) / 2.0
    slippage = (remain * 0.3) / 2.0
    
    PER_STOCK = 5000000
    manager = HistoricalDataManager()
    
    data_dir = os.path.join(base_dir, "data", "historical")
    codes = sorted([f.replace("_1m.csv", "") for f in os.listdir(data_dir) if f.endswith("_1m.csv")])
    
    print(f"============================================================")
    print(f" [v13.0] 1-Week Data Comprehensive Backtest (Settings from .env)")
    print(f"============================================================")
    print(f"[DATA] Loading {len(codes)} stocks (Target: ~2,000 candles per stock)")
    
    total_initial = PER_STOCK * len(codes)
    total_final = 0
    total_win = 0
    total_loss = 0
    total_trades = 0
    total_win_count = 0
    total_orders = 0
    
    start_time = time.time()
    
    for i, code in enumerate(codes):
        df = manager.load_code_data(code)
        if df is None or df.empty:
            total_final += PER_STOCK
            continue
            
        try:
            broker = VirtualBroker(initial_balance=PER_STOCK, fee_rate=fee_rate, tax_rate=tax_rate, slippage=slippage)
            strategy = StefanoStrategy()
            ai_engine = AIEngine()
            strategy.set_ai_modules(ai_engine, None)
            
            engine = BacktestEngine(broker, strategy)
            engine.run(code, df)
            
            s = broker.get_summary()
            total_final += broker.get_total_asset_value({})
            total_win += s['total_profit_sum']
            total_loss += s['total_loss_sum']
            total_trades += s['completed_trades']
            total_win_count += s['win_count']
            total_orders += s['order_count']
        except Exception as e:
            total_final += PER_STOCK

    net = total_final - total_initial
    roi = (net / total_initial) * 100.0  # 총 시드 대비 ROI (500만 x 종목수)
    
    pf = total_win / abs(total_loss) if total_loss != 0 else (999.0 if total_win > 0 else 0)
    wr = (total_win_count / total_trades * 100) if total_trades > 0 else 0
    duration = time.time() - start_time
    
    print(f"\n[결과 분석 완료! 소요시간: {duration:.1f}초]")
    print(f"--------------------------------------------------")
    print(f"총 거래 횟수 : {total_trades:5d}회 (시도: {total_orders})")
    print(f"총 승률      : {wr:5.1f}% ({total_win_count}승)")
    print(f"순수익(Net)  : {net:+,.0f} 원")
    print(f"수익 계수(PF): {pf:.2f}")
    print(f"최종 ROI     : {roi:+.3f}% (1주일/총시드 대비)")
    print(f"--------------------------------------------------")
    
if __name__ == "__main__":
    main()
