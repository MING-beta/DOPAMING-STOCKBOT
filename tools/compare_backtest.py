"""
진입 파라미터 변경 전후 비교 백테스트
- 이전: NITRO_RSI=35, MIN_RECOVERY=0.005
- 이후: NITRO_RSI=28, MIN_RECOVERY=0.003
"""
import sys, os, logging
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, base_dir)

def run_single(code, init_balance, friction, nitro_rsi, min_recovery):
    import os
    from dotenv import load_dotenv
    load_dotenv()
    # 파라미터 오버라이드 (비교를 위해 모드를 MEAN_REVERSION으로 강제)
    os.environ["STRATEGY_MODE"]         = "MEAN_REVERSION"
    os.environ["STRATEGY_NITRO_RSI"]    = str(nitro_rsi)
    os.environ["STRATEGY_MIN_RECOVERY"] = str(min_recovery)

    logging.getLogger("DopamingBot").setLevel(logging.ERROR)
    try:
        from backtest.historical_data_manager import HistoricalDataManager
        from backtest.virtual_broker import VirtualBroker
        from backtest.engine import BacktestEngine
        from strategy.stefano_strategy import StefanoStrategy
        from core.ai_engine import AIEngine

        tax_rate = 0.002
        remain   = max(0, friction - tax_rate)
        fee_rate = (remain * 0.7) / 2.0
        slippage = (remain * 0.3) / 2.0

        manager  = HistoricalDataManager()
        broker   = VirtualBroker(initial_balance=init_balance, fee_rate=fee_rate,
                                 tax_rate=tax_rate, slippage=slippage)
        strategy = StefanoStrategy()
        ai       = AIEngine()
        strategy.set_ai_modules(ai, None)

        engine = BacktestEngine(broker, strategy)
        df = manager.load_code_data(code)
        if df is not None and not df.empty:
            engine.run(code, df)
            s = broker.get_summary()
            s['code'] = code
            s['final_value'] = broker.get_total_asset_value({})
            return s
    except Exception as e:
        return {"code": code, "error": str(e)}
    return None


def run_scenario(label, codes, per_balance, friction, nitro_rsi, min_recovery):
    print(f"\n{'='*55}")
def run_scenario(label, codes, per_balance, friction, nitro_rsi, min_recovery):
    print(f"\n{'='*55}")
    print(f"  {label}  (NITRO_RSI={nitro_rsi}, MIN_RECOVERY={min_recovery})")
    print(f"{'='*55}")

    all_results = []
    with ProcessPoolExecutor() as executor:
        futures = {
            executor.submit(run_single, c, per_balance, friction, nitro_rsi, min_recovery): c
            for c in codes
        }
        for future in as_completed(futures):
            res = future.result()
            if res and "error" not in res:
                all_results.append(res)
            elif res and "error" in res:
                print(f"  [ERROR] {res['code']}: {res['error']}")

    total_final   = sum(r['final_value']         for r in all_results)
    total_win_sum = sum(r['total_profit_sum']    for r in all_results)
    total_los_sum = sum(r['total_loss_sum']      for r in all_results)
    total_trades  = sum(r['completed_trades']    for r in all_results)
    total_wins    = sum(r['win_count']           for r in all_results)
    total_orders  = sum(r['order_count']         for r in all_results)
    max_mdd       = max((r['max_drawdown']       for r in all_results), default=0)

    total_init    = per_balance * len(codes)
    REALISTIC     = 30_000_000
    net_profit    = total_final - total_init
    roi           = (net_profit / REALISTIC) * 100.0
    win_rate      = (total_wins  / total_trades * 100) if total_trades > 0 else 0
    pf            = total_win_sum / total_los_sum if total_los_sum > 0 else 999.0

    print(f"  Stock Count    : {len(all_results)} / {len(codes)}")
    print(f"  Total Orders   : {total_orders}")
    print(f"  Trades         : {total_trades}")
    print(f"  Win Rate       : {win_rate:.1f}%  ({total_wins} wins / {total_trades} trades)")
    print(f"  Profit Sum     : {total_win_sum:>+15,.0f}")
    print(f"  Loss Sum       : {total_los_sum:>+15,.0f}")
    print(f"  Net Profit     : {net_profit:>+15,.0f}")
    print(f"  Realistic ROI  : {roi:+.2f}%")
    print(f"  Max MDD        : {max_mdd:.2f}%")
    print(f"  PF             : {pf:.2f}")

    return {
        "label": label, "roi": roi, "win_rate": win_rate,
        "net_profit": net_profit, "trades": total_trades,
        "orders": total_orders, "mdd": max_mdd, "pf": pf
    }


def main():
    from dotenv import load_dotenv
    load_dotenv()

    data_dir = os.path.join(base_dir, "data", "historical")
    codes = sorted([
        f.replace("_1m.csv", "")
        for f in os.listdir(data_dir) if f.endswith("_1m.csv")
    ]) if os.path.exists(data_dir) else []

    if not codes:
        print("Error: No CSV files in data/historical.")
        return

    # Use first 30 codes for faster testing if needed, or all codes
    # codes = codes[:30] 

    friction    = float(os.getenv("TRADING_FRICTION", "0.009"))
    per_balance = 5_000_000

    print(f"\nTarget Stocks: {len(codes)} | Asset per Stock: {per_balance:,}")
    t0 = datetime.now()

    r_before = run_scenario(
        "[BEFORE] (NITRO_RSI=35, MIN_RECOVERY=0.005)",
        codes, per_balance, friction,
        nitro_rsi=35.0, min_recovery=0.005
    )
    r_after = run_scenario(
        "[AFTER]  (NITRO_RSI=28, MIN_RECOVERY=0.003)",
        codes, per_balance, friction,
        nitro_rsi=28.0, min_recovery=0.003
    )

    duration = (datetime.now() - t0).total_seconds()

    print(f"\n{'='*55}")
    print("  COMPARISON SUMMARY")
    print(f"{'='*55}")
    print(f"  {'Metric':<15} {'Before':>14} {'After':>14} {'Diff':>12}")
    print(f"  {'-'*53}")

    def row(name, key, fmt=".2f", unit=""):
        b = r_before[key]; a = r_after[key]
        diff = a - b
        sign = "+" if diff >= 0 else ""
        print(f"  {name:<15} {b:>12{fmt}}{unit} {a:>12{fmt}}{unit} {sign}{diff:>{fmt}}{unit}")

    row("Win Rate(%)",      "win_rate",    ".1f", "%")
    row("ROI(%)",       "roi",         ".2f", "%")
    row("MDD(%)",       "mdd",         ".2f", "%")
    row("PF",           "pf",          ".2f", "")
    row("Trades",      "trades",      ".0f", " trades")
    row("Orders",      "orders",      ".0f", " orders")
    print(f"  {'Net Profit':<15} {r_before['net_profit']:>12,.0f}  {r_after['net_profit']:>12,.0f}")
    print(f"\n  Total Time: {duration:.1f}s")
    print(f"{'='*55}")

if __name__ == "__main__":
    main()
