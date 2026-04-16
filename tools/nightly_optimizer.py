import sys
import os
import random
import logging
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_dir)

from backtest.historical_data_manager import HistoricalDataManager
from backtest.virtual_broker import VirtualBroker
from backtest.engine import BacktestEngine, calculate_total_profit
from strategy.stefano_strategy import StefanoStrategy
from core.ai_engine import AIEngine
from core.data_collector import DataCollector
from dotenv import set_key, load_dotenv

def run_single_backtest_with_params(code, init_balance, friction, profit_target, stop_loss, ai_thresh):
    """워커 독립 프로세스이며 임시 파라미터를 강제 주입하여 백테스트를 수행합니다."""
    logger = logging.getLogger("DopamingBot")
    logger.setLevel(logging.ERROR) 

    # 최적화용 환경변수 강제 주입
    os.environ['TRADE_TARGET_PROFIT'] = str(profit_target)
    os.environ['TRADE_STOP_LOSS'] = str(stop_loss)
    os.environ['AI_THRESHOLD'] = str(ai_thresh)

    try:
        tax_rate = 0.002 
        remain = max(0, friction - tax_rate)
        fee_rate = (remain * 0.7) / 2.0
        slippage = (remain * 0.3) / 2.0
        
        manager = HistoricalDataManager()
        broker = VirtualBroker(initial_balance=init_balance, fee_rate=fee_rate, tax_rate=tax_rate, slippage=slippage)
        
        strategy = StefanoStrategy()
        
        ai_engine = AIEngine()
        data_collector = DataCollector()
        strategy.set_ai_modules(ai_engine, data_collector)
        
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


class NightlyOptimizer:
    """
    32비트 C++ 종속성 이슈를 우회하기 위해 Optuna를 배제하고 
    자체 Random/Grid Search를 멀티프로세스로 수행하는 Nightly Optimizer
    """
    def __init__(self):
        self.data_dir = os.path.join(base_dir, "data", "historical")
        self.target_codes = self._get_target_codes()
        self.friction = float(os.getenv("TRADING_FRICTION", "0.009"))
        self.per_stock_balance = 5000000 
        self.total_initial_balance = self.per_stock_balance * len(self.target_codes)

    def _get_target_codes(self):
        codes = []
        if os.path.exists(self.data_dir):
            for fname in os.listdir(self.data_dir):
                if fname.endswith("_1m.csv"):
                    codes.append(fname.replace("_1m.csv", ""))
        # 성능을 위해 대표 30종목만 샘플링
        return sorted(list(set(codes)))[:30]
        
    def evaluate_params(self, profit_target, stop_loss, ai_thresh):
        all_results = []
        # 코어를 전부 낭비하지 않도록 적절히 제한
        with ProcessPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(
                    run_single_backtest_with_params, 
                    code, self.per_stock_balance, self.friction, 
                    profit_target, stop_loss, ai_thresh
                ): code 
                for code in self.target_codes
            }
            for future in as_completed(futures):
                res = future.result()
                if res and "error" not in res:
                    all_results.append(res)
                    
        total_final = sum(r['final_value'] for r in all_results)
        return total_final

    def run_optimization(self, trials=5):
        if not self.target_codes:
            print("최적화용 히스토리컬 데이터가 없습니다. (data/historical 확인)")
            return None
            
        print(f"\n[Nightly Optimizer] 일일 정밀 시장 장세 스캔 시작 (Trials: {trials})")
        print(f"대상: 상위 {len(self.target_codes)}종목 샘플링 병렬 처리")
        
        best_profit = -99999999
        best_params = None
        
        for i in range(trials):
            pt = round(random.uniform(0.015, 0.035), 3)
            sl = round(random.uniform(-0.035, -0.015), 3)
            ai = round(random.uniform(0.30, 0.50), 3)
            
            print(f"  [{i+1:02d}/{trials}] 테스트 Param (익절: {pt*100:.1f}%, 손절: {sl*100:.1f}%, AI: {ai:.2f}) ...", end="", flush=True)
            
            final_val = self.evaluate_params(pt, sl, ai)
            profit_rate = calculate_total_profit(self.total_initial_balance, final_val)
            print(f" 완료 => 기대 수익률: {profit_rate:+.2f}%")
            
            if profit_rate > best_profit:
                best_profit = profit_rate
                best_params = (pt, sl, ai)
                
        print("\n" + "="*50)
        print(f"최적화 달성! (최고 수익률: {best_profit:+.2f}%)")
        print(f"발견된 베스트 설정: 익절 {best_params[0]*100:.1f}%, 손절 {best_params[1]*100:.1f}%, AI임계치 {best_params[2]:.2f}")
        print("="*50)
        
        self._update_env(best_params)
        return best_params, best_profit
        
    def _update_env(self, params):
        env_path = os.path.join(base_dir, ".env")
        if not os.path.exists(env_path):
            print("⚠️ .env 파일이 존재하지 않아 환경 변수 업데이트를 생략합니다.")
            return
            
        print("✅ [자동화] 탐색된 최적의 결과를 .env 파일에 주입하여 내일 매매에 적용합니다.")
        set_key(env_path, "TRADE_TARGET_PROFIT", str(params[0]))
        set_key(env_path, "TRADE_STOP_LOSS", str(params[1]))
        set_key(env_path, "AI_THRESHOLD", str(params[2]))

if __name__ == "__main__":
    opt = NightlyOptimizer()
    opt.run_optimization(trials=8)
