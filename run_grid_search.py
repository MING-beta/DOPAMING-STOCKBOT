"""
[v12.0 Aggressive Sweep] 진입 완화 + 출구 최적화 스윕
핵심: 스마트익절/데드존/진입조건을 공격적으로 변경하여 다거래 고수익를 탐색
"""
import sys, os, logging, time, json, importlib

base_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(base_dir)
from dotenv import load_dotenv
load_dotenv()
logging.disable(logging.CRITICAL)

# 기본값 (현재 설정)
BASE = {
    "STRATEGY_MODE": "BREAKOUT", "STRATEGY_BYPASS_MACRO": "False",
    "HFS_GOLDEN_RATIO": "0.350", "AI_THRESHOLD": "0.52",
    "INDICATOR_BB_STD": "1.8", "STRATEGY_VOL_RATIO": "4.1",
    "STRATEGY_BB_WIDTH_LIMIT": "0.21", "TRADE_STOP_LOSS": "-0.039",
    "TRADE_TARGET_PROFIT": "0.032", "TRAILING_STOP_CALLBACK": "0.006",
    # 새 v12.0 파라미터 (기본값 = 기존 하드코딩)
    "SMART_EXIT_MIN_PROFIT": "0.012",
    "DEADZONE_MINUTES": "5", "DEADZONE_MIN_PROFIT": "0.010",
    "BREAKOUT_RSI_MIN": "55", "BREAKOUT_RSI_MAX": "72",
    "BREAKOUT_VO_MIN": "25.0",
    "BREAKOUT_REQUIRE_DOUBLE_GREEN": "True",
    "BREAKOUT_REQUIRE_ACCEL": "True",
    "BREAKOUT_5M_RSI_LIMIT": "60.0",
    "PULLBACK_RSI_COOLING": "40.0",
    "PULLBACK_CLEAN_CHECK": "True",
}

CASES = [
    # A. 기준선 (현재)
    {"label": "A_현재설정", **BASE},

    # B. 스마트 익절만 비활성화 (99% = 사실상 OFF)
    {"label": "B_스마트OFF", **{**BASE, "SMART_EXIT_MIN_PROFIT": "0.99"}},

    # C. 데드존만 비활성화 (99분 = 사실상 OFF)
    {"label": "C_데드존OFF", **{**BASE, "DEADZONE_MINUTES": "99"}},

    # D. 둘 다 비활성화
    {"label": "D_둘다OFF", **{**BASE, "SMART_EXIT_MIN_PROFIT": "0.99", "DEADZONE_MINUTES": "99"}},

    # E. 진입 완화 (RSI 확대 + VO 완화 + 2연속양봉 해제 + 가속도 해제)
    {"label": "E_진입완화", **{**BASE,
        "BREAKOUT_RSI_MIN": "45", "BREAKOUT_RSI_MAX": "80",
        "BREAKOUT_VO_MIN": "10.0",
        "BREAKOUT_REQUIRE_DOUBLE_GREEN": "False",
        "BREAKOUT_REQUIRE_ACCEL": "False",
        "BREAKOUT_5M_RSI_LIMIT": "75.0"}},

    # F. 진입완화 + 출구 비활성
    {"label": "F_완화+출구OFF", **{**BASE,
        "BREAKOUT_RSI_MIN": "45", "BREAKOUT_RSI_MAX": "80",
        "BREAKOUT_VO_MIN": "10.0",
        "BREAKOUT_REQUIRE_DOUBLE_GREEN": "False",
        "BREAKOUT_REQUIRE_ACCEL": "False",
        "BREAKOUT_5M_RSI_LIMIT": "75.0",
        "SMART_EXIT_MIN_PROFIT": "0.99", "DEADZONE_MINUTES": "99"}},

    # G. 진입완화 + 스마트 2.5% + 데드존 15분
    {"label": "G_밸런스1", **{**BASE,
        "BREAKOUT_RSI_MIN": "45", "BREAKOUT_RSI_MAX": "80",
        "BREAKOUT_VO_MIN": "10.0",
        "BREAKOUT_REQUIRE_DOUBLE_GREEN": "False",
        "BREAKOUT_REQUIRE_ACCEL": "False",
        "BREAKOUT_5M_RSI_LIMIT": "75.0",
        "SMART_EXIT_MIN_PROFIT": "0.025", "DEADZONE_MINUTES": "15", "DEADZONE_MIN_PROFIT": "0.003"}},

    # H. 진입완화 + HFS=0 + 거시우회 + 스마트OFF
    {"label": "H_울트라공격", **{**BASE,
        "STRATEGY_BYPASS_MACRO": "True", "HFS_GOLDEN_RATIO": "0.0",
        "AI_THRESHOLD": "0.35", "STRATEGY_VOL_RATIO": "1.5",
        "BREAKOUT_RSI_MIN": "45", "BREAKOUT_RSI_MAX": "80",
        "BREAKOUT_VO_MIN": "10.0",
        "BREAKOUT_REQUIRE_DOUBLE_GREEN": "False",
        "BREAKOUT_REQUIRE_ACCEL": "False",
        "BREAKOUT_5M_RSI_LIMIT": "80.0",
        "SMART_EXIT_MIN_PROFIT": "0.99", "DEADZONE_MINUTES": "99"}},

    # I. H + 넓은 SL/TP
    {"label": "I_울트라+넓은TP", **{**BASE,
        "STRATEGY_BYPASS_MACRO": "True", "HFS_GOLDEN_RATIO": "0.0",
        "AI_THRESHOLD": "0.35", "STRATEGY_VOL_RATIO": "1.5",
        "BREAKOUT_RSI_MIN": "45", "BREAKOUT_RSI_MAX": "80",
        "BREAKOUT_VO_MIN": "10.0",
        "BREAKOUT_REQUIRE_DOUBLE_GREEN": "False",
        "BREAKOUT_REQUIRE_ACCEL": "False",
        "BREAKOUT_5M_RSI_LIMIT": "80.0",
        "SMART_EXIT_MIN_PROFIT": "0.99", "DEADZONE_MINUTES": "99",
        "TRADE_TARGET_PROFIT": "0.05", "TRADE_STOP_LOSS": "-0.05",
        "TRAILING_STOP_CALLBACK": "0.01"}},

    # J. 완화 + 풀백 조건도 해제 + 거시우회
    {"label": "J_풀백해제", **{**BASE,
        "STRATEGY_BYPASS_MACRO": "True", "HFS_GOLDEN_RATIO": "0.0",
        "AI_THRESHOLD": "0.35", "STRATEGY_VOL_RATIO": "1.5",
        "BREAKOUT_RSI_MIN": "45", "BREAKOUT_RSI_MAX": "80",
        "BREAKOUT_VO_MIN": "10.0",
        "BREAKOUT_REQUIRE_DOUBLE_GREEN": "False",
        "BREAKOUT_REQUIRE_ACCEL": "False",
        "BREAKOUT_5M_RSI_LIMIT": "80.0",
        "PULLBACK_RSI_COOLING": "50.0", "PULLBACK_CLEAN_CHECK": "False",
        "SMART_EXIT_MIN_PROFIT": "0.025", "DEADZONE_MINUTES": "15", "DEADZONE_MIN_PROFIT": "0.003"}},

    # K. 밸런스2: 약간 완화 + 스마트 2% + 데드존 10분
    {"label": "K_밸런스2", **{**BASE,
        "BREAKOUT_RSI_MIN": "50", "BREAKOUT_RSI_MAX": "78",
        "BREAKOUT_VO_MIN": "15.0",
        "BREAKOUT_REQUIRE_DOUBLE_GREEN": "False",
        "BREAKOUT_5M_RSI_LIMIT": "70.0",
        "SMART_EXIT_MIN_PROFIT": "0.02", "DEADZONE_MINUTES": "10", "DEADZONE_MIN_PROFIT": "0.005"}},

    # L. 스마트만 높임 (3%)
    {"label": "L_스마트3%", **{**BASE, "SMART_EXIT_MIN_PROFIT": "0.03"}},
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

    net = total_final - total_initial
    roi = (net / 30000000) * 100.0
    pf = total_win / total_loss if total_loss > 0 else (999.0 if total_win > 0 else 0)
    wr = (total_win_count / total_trades * 100) if total_trades > 0 else 0
    return {"roi": roi, "net_profit": net, "trades": total_trades,
            "win_count": total_win_count, "win_rate": wr, "pf": pf, "orders": total_orders}


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
    print(f"[DATA] {len(codes)} loaded\n")

    total = len(CASES)
    print(f"{'='*85}")
    print(f" [v12.0 Aggressive Sweep] {total} cases (~{total*60//60}min)")
    print(f"{'='*85}\n")

    start = time.time()
    results = []
    for idx, case in enumerate(CASES):
        label = case.get('label', f'#{idx}')
        t0 = time.time()
        res = run_case(case, codes, data_cache)
        dt = time.time() - t0
        res['case_id'] = idx; res['params'] = {k:v for k,v in case.items() if k != 'label'}; res['label'] = label
        results.append(res)
        eta = (dt * (total - idx - 1)) / 60
        print(f" [{idx+1:2d}/{total}] {dt:5.1f}s | {label:16s} | ROI={res['roi']:+.3f}% | T={res['trades']:3d} W={res['win_rate']:5.1f}% PF={res['pf']:.2f} | ETA:{eta:.0f}m")

    elapsed = time.time() - start
    results.sort(key=lambda x: x['roi'], reverse=True)

    print(f"\n{'='*85}")
    print(f" [COMPLETE] {elapsed/60:.1f}min")
    print(f"{'='*85}")
    for i, r in enumerate(results):
        icon = "***" if i == 0 else "  *" if i < 3 else "   "
        p = r['params']
        print(f" {icon} #{i+1:2d} [{r['label']:16s}] ROI={r['roi']:+.4f}% Net={r['net_profit']:+,.0f} T={r['trades']} W={r['win_rate']:.1f}% PF={r['pf']:.2f}")

    best = results[0]
    print(f"\n *** BEST: [{best['label']}] ROI={best['roi']:+.4f}% ***")
    for k, v in best['params'].items():
        print(f"   {k}={v}")

    rp = os.path.join(base_dir, "grid_search_result.json")
    with open(rp, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n [SAVED] {rp}")


if __name__ == "__main__":
    main()
