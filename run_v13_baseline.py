"""
[v13.0 VCP & Deep Dip Baseline Sweep]
완전히 개조된 선취매/낙주 로직이 제대로 작동하고, 10분 타임컷이 해제된 상태에서 
어떤 익절/손절 비율이 가장 폭발적인 수익을 내는지 검증합니다.
"""
import sys, os, logging, time, json, importlib

base_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(base_dir)
from dotenv import load_dotenv
load_dotenv()
logging.disable(logging.CRITICAL)

BASE = {
    "STRATEGY_MODE": "BREAKOUT", "STRATEGY_BYPASS_MACRO": "True",
    "HFS_GOLDEN_RATIO": "0.0", "AI_THRESHOLD": "0.30",
    "INDICATOR_BB_STD": "2.0", "STRATEGY_VOL_RATIO": "1.5",
    "SMART_EXIT_MIN_PROFIT": "0.99", "DEADZONE_MINUTES": "99", # 둘다 비활성
}

CASES = [
    # 1. 스캘핑 (타이트 홀딩)
    {"label": "1_스캘핑_TP3_SL2", **BASE, "TRADE_TARGET_PROFIT": "0.03", "TRADE_STOP_LOSS": "-0.02", "TRAILING_STOP_CALLBACK": "0.005"},
    
    # 2. 데이트레이딩 (미디엄 홀딩)
    {"label": "2_미디엄_TP5_SL3", **BASE, "TRADE_TARGET_PROFIT": "0.05", "TRADE_STOP_LOSS": "-0.03", "TRAILING_STOP_CALLBACK": "0.010"},
    
    # 3. 홈런 스윙 (빅 홀딩)
    {"label": "3_빅홀딩_TP8_SL4", **BASE, "TRADE_TARGET_PROFIT": "0.08", "TRADE_STOP_LOSS": "-0.04", "TRAILING_STOP_CALLBACK": "0.020"},
    
    # 4. 무자비한 익스트림 홀딩
    {"label": "4_익스트림_TP15_SL5", **BASE, "TRADE_TARGET_PROFIT": "0.15", "TRADE_STOP_LOSS": "-0.05", "TRAILING_STOP_CALLBACK": "0.030"},
    
    # 5. 스마트익절 부활 (스캘핑 베이스)
    {"label": "5_스캘핑+스마트1.5", **BASE, "TRADE_TARGET_PROFIT": "0.03", "TRADE_STOP_LOSS": "-0.02", "SMART_EXIT_MIN_PROFIT": "0.015"}
]

def run_case(params, codes, data_cache):
    for k, v in params.items():
        if k == 'label': continue
        os.environ[k] = str(v)

    import strategy.stefano_strategy as strat_mod
    importlib.reload(strat_mod)
    import backtest.engine as eng_mod
    importlib.reload(eng_mod)
    import core.ai_engine as ai_mod
    importlib.reload(ai_mod)
    from backtest.virtual_broker import VirtualBroker

    friction = float(os.environ.get("TRADING_FRICTION", "0.0025"))
    tax_rate = 0.002; remain = max(0, friction - tax_rate)
    fee_rate = (remain * 0.7) / 2.0; slippage = (remain * 0.3) / 2.0
    PER_STOCK = 5000000

    total_initial = PER_STOCK * len(codes)
    total_final = 0; total_win = 0; total_loss = 0
    total_trades = 0; total_win_count = 0; total_orders = 0

    for code in codes:
        try:
            df = data_cache[code].copy()
            broker = VirtualBroker(initial_balance=PER_STOCK, fee_rate=fee_rate, tax_rate=tax_rate, slippage=slippage)
            strategy = strat_mod.StefanoStrategy()
            ai_engine = ai_mod.AIEngine()
            strategy.set_ai_modules(ai_engine, None)
            engine = eng_mod.BacktestEngine(broker, strategy)
            engine.run(code, df)
            s = broker.get_summary()
            total_final += broker.get_total_asset_value({})
            total_win += s['total_profit_sum']; total_loss += s['total_loss_sum']
            total_trades += s['completed_trades']; total_win_count += s['win_count']
            total_orders += s['order_count']
        except Exception as e:
            total_final += PER_STOCK

    # 기준 투자금 3천만원에 대한 ROI
    REALISTIC_ACC = 30000000.0
    net = total_final - total_initial
    roi = (net / REALISTIC_ACC) * 100.0
    pf = total_win / total_loss if total_loss > 0 else (999.0 if total_win > 0 else 0)
    wr = (total_win_count / total_trades * 100) if total_trades > 0 else 0
    return {"roi": roi, "net_profit": net, "trades": total_trades,
            "win_count": total_win_count, "win_rate": wr, "pf": pf}

def main():
    from backtest.historical_data_manager import HistoricalDataManager
    data_dir = os.path.join(base_dir, "data", "historical")
    codes = sorted([f.replace("_1m.csv", "") for f in os.listdir(data_dir) if f.endswith("_1m.csv")])
    print(f"[DATA] {len(codes)} stocks loading...")
    data_cache = {}
    manager = HistoricalDataManager()
    for code in codes:
        try: data_cache[code] = manager.load_code_data(code)
        except: pass
    codes = list(data_cache.keys())
    
    print(f"\n=======================================================")
    print(f" [v13.0] VCP & Deep Dip Baseline Check (Target: 5%)")
    print(f"=======================================================")

    results = []
    for idx, case in enumerate(CASES):
        t0 = time.time()
        res = run_case(case, codes, data_cache)
        dt = time.time() - t0
        print(f" [{idx+1}/{len(CASES)}] {case['label']:20s} | ROI={res['roi']:+.3f}% | Net={res['net_profit']:+,.0f} | T={res['trades']:3d} | W={res['win_rate']:5.1f}% | PF={res['pf']:.2f} ({dt:.1f}s)")

if __name__ == "__main__":
    main()
