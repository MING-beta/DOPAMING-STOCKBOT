import sys
import os
import random
import logging
import time
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_dir)

from backtest.historical_data_manager import HistoricalDataManager
from backtest.virtual_broker import VirtualBroker
from backtest.engine import BacktestEngine
from strategy.stefano_strategy import StefanoStrategy
from core.ai_engine import AIEngine
from dotenv import set_key, load_dotenv

# [초기화] 환경 변수 로드
env_path = os.path.join(base_dir, ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)

logging.getLogger("DopamingBot").setLevel(logging.CRITICAL)

def run_single_backtest_with_hyper_params(code, init_balance, friction, params):
    """지정된 하이퍼 파라미터로 단일 종목 백테스트 수행"""
    try:
        # 자식 프로세스 내 환경변수 격리 및 주입
        local_env = os.environ.copy()
        for key, val in params.items():
            local_env[key] = str(val)
        
        # StefanoStrategy 및 Engine이 os.environ을 참조하므로 일시적 패치
        for key, val in params.items():
            os.environ[key] = str(val)

        manager = HistoricalDataManager()
        tax_rate = 0.002 
        remain = max(0, friction - tax_rate)
        fee_rate = (remain * 0.7) / 2.0
        slippage = (remain * 0.3) / 2.0

        broker = VirtualBroker(initial_balance=init_balance, fee_rate=fee_rate, tax_rate=tax_rate, slippage=slippage)
        strategy = StefanoStrategy()
        
        ai_engine = AIEngine()
        strategy.set_ai_modules(ai_engine, None)
        
        engine = BacktestEngine(broker, strategy)
        df = manager.load_code_data(code)
        
        if df is not None and not df.empty:
            engine.run(code, df)
            summary = broker.get_summary()
            summary['code'] = code
            summary['final_value'] = broker.get_total_asset_value({})
            return summary
    except Exception as e:
        return {"code": code, "error": str(e)}
    return None

class HyperOptimizer:
    def __init__(self, target_roi=5.0, max_trials=50):
        self.target_roi = target_roi
        self.max_trials = max_trials
        self.data_dir = os.path.join(base_dir, "data", "historical")
        self.target_codes = self._get_target_codes()
        self.friction = float(os.getenv("TRADING_FRICTION", "0.0025"))
        self.per_stock_balance = 5000000 
        self.realistic_account = 30000000.0

    def _get_target_codes(self):
        codes = []
        if os.path.exists(self.data_dir):
            for fname in os.listdir(self.data_dir):
                if fname.endswith("_1m.csv"):
                    codes.append(fname.replace("_1m.csv", ""))
        return sorted(list(set(codes)))

    def sample_params(self):
        """v13.0 VCP 및 Deep Dip 최적화 파라미터 스윕 범위"""
        tp = round(random.uniform(0.030, 0.120), 3) # 목표수익 3% ~ 12% 홈런 타겟
        return {
            "STRATEGY_MODE": "BREAKOUT",
            "STRATEGY_BYPASS_MACRO": random.choice(["True", "False"]),
            "TRADE_TARGET_PROFIT": tp,
            "TRADE_STOP_LOSS": round(random.uniform(-0.040, -0.015), 3),
            "TRADE_BREAKEVEN_TRIGGER": round(random.uniform(0.015, 0.030), 3),
            "TRADE_BREAKEVEN_PROTECT": round(random.uniform(0.003, 0.010), 3),
            "TRAILING_STOP_ACTIVATION": round(random.uniform(tp * 0.5, tp * 0.9), 3), # 목표가의 50~90% 도달 시 트레일링 활성
            "TRAILING_STOP_CALLBACK": round(random.uniform(0.005, 0.025), 4), # 떨어지는 폭 0.5% ~ 2.5%
            "STRATEGY_VOL_RATIO": round(random.uniform(1.2, 3.0), 1),
            "INDICATOR_BB_STD": round(random.uniform(1.5, 2.0), 1),
            "AI_THRESHOLD": round(random.uniform(0.10, 0.40), 3),
            
            # [v13.0] 데드존 및 스마트 익절 완전 비활성 (추세 최대한 끝까지 홀딩)
            "SMART_EXIT_MIN_PROFIT": 0.99,
            "DEADZONE_MINUTES": 99,
            "DEADZONE_MIN_PROFIT": 0.010
        }

    def evaluate(self, params):
        all_results = []
        # 코어를 최대한 활용
        with ProcessPoolExecutor(max_workers=os.cpu_count() or 6) as executor:
            futures = {
                executor.submit(run_single_backtest_with_hyper_params, code, self.per_stock_balance, self.friction, params): code 
                for code in self.target_codes
            }
            for future in as_completed(futures):
                res = future.result()
                if res and "error" not in res:
                    all_results.append(res)
        
        if not all_results:
            return -999, 0, 0, 0
            
        total_final = sum(r['final_value'] for r in all_results)
        success_count = len(all_results)
        actual_init = success_count * self.per_stock_balance
        net_profit = total_final - actual_init
        roi = (net_profit / self.realistic_account) * 100.0
        
        win_count = sum(r['win_count'] for r in all_results)
        total_trades = sum(r['completed_trades'] for r in all_results)
        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
        pf = sum(r['total_profit_sum'] for r in all_results) / abs(sum(r['total_loss_sum'] for r in all_results)) if sum(r['total_loss_sum'] for r in all_results) != 0 else 9.9
        
        return roi, win_rate, total_trades, pf

    def run(self, apply=False):
        print(f"\n[Extreme Hyper Optimizer] Target ROI {self.target_roi}% Search Started (Max {self.max_trials} trials)")
        print(f"Target Stocks: {len(self.target_codes)}")
        print(f"Apply Changes: {'YES' if apply else 'NO (Test Only Mode)'}")
        
        best_roi = -999.0
        best_params = None
        
        for i in range(self.max_trials):
            params = self.sample_params()
            # 간단 로깅
            print(f"[{i+1}/{self.max_trials}] Testing Trial... ", end="", flush=True)
            
            roi, win_rate, trades, pf = self.evaluate(params)
            
            # 다거래(최소 20회 이상) + 높은 타겟 ROI를 지향하는 필터
            if trades >= 20 and pf >= 1.0:
                print(f"VALID -> ROI: {roi:+.2f}%, WinRate: {win_rate:.1f}%, PF: {pf:.2f}, Trades: {trades}")
                if roi > best_roi:
                    best_roi = roi
                    best_params = params
                    print(f"  *** NEW RECORD! ROI: {best_roi:+.2f}% ***")
            else:
                print(f"SKIP (Low Quality) -> ROI: {roi:+.2f}%, Trades: {trades}, PF: {pf:.2f}")
            
            if roi >= self.target_roi and trades >= 80:
                print(f"\nTarget ROI {self.target_roi}% Achieved with High Quality!")
                break
        
        if best_params:
            self.apply_params(best_params, best_roi, apply=apply)
        else:
            print("\nOptimization Failed: No high-quality improvement found.")

    def apply_params(self, params, roi, apply=False):
        print("\n" + "="*60)
        print(f"  EXTREME OPTIMIZATION RESULT (ROI: {roi:+.2f}%)")
        for k, v in params.items():
            print(f"  {k:<25} : {v}")
            if apply:
                # .env에 저장 전 타입 변환
                set_key(env_path, k, str(v))
        print("="*60)
        
        if apply:
            print(" [OK] Best high-quality parameters applied to .env file.")
        else:
            print(" [INFO] TEST-ONLY 모드입니다. .env 파일이 수정되지 않았습니다.")
            print(" 위 파라미터를 적용하려면 실행 시 --apply 옵션을 사용하세요.")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Dopaming Hyper Optimizer (v11.9)")
    parser.add_argument("--trials", type=int, default=100, help="최대 탐색 횟수 (기본 100)")
    parser.add_argument("--target-roi", type=float, default=5.0, help="목표 수익률 ROI (기본 5.0)")
    parser.add_argument("--apply", action="store_true", help="최적화된 파라미터를 .env에 실제 적용")
    
    args = parser.parse_args()
    
    optimizer = HyperOptimizer(target_roi=args.target_roi, max_trials=args.trials)
    optimizer.run(apply=args.apply)
