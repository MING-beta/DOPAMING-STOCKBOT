import sys
import os
import logging
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

# 프로젝트 루트 경로 추가
base_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(base_dir)

from backtest.historical_data_manager import HistoricalDataManager
from backtest.virtual_broker import VirtualBroker
from backtest.engine import BacktestEngine, calculate_total_profit
from strategy.stefano_strategy import StefanoStrategy
from core.ai_engine import AIEngine
from core.data_collector import DataCollector
from dotenv import load_dotenv

def run_single_backtest(code, init_balance, friction):
    """개별 종목 백테스트 워커 함수 (프로세스 독립 실행)"""
    # 워커 내에서는 로깅을 콘솔에 찍지 않음 (로그 꼬임 방지)
    logger = logging.getLogger("DopamingBot")
    logger.setLevel(logging.ERROR) 
    
    # [BUG FIX] Windows 다중 프로세스 환경에서는 워커별로 환경변수를 다시 로드해야 함
    from dotenv import load_dotenv
    load_dotenv()

    try:
        tax_rate = 0.002 
        remain = max(0, friction - tax_rate)
        fee_rate = (remain * 0.7) / 2.0
        slippage = (remain * 0.3) / 2.0
        
        manager = HistoricalDataManager()
        broker = VirtualBroker(
            initial_balance=init_balance,
            fee_rate=fee_rate,
            tax_rate=tax_rate,
            slippage=slippage
        )
        strategy = StefanoStrategy()
        
        # [v10.0 AI Resurrection] AI 판독기(AIEngine) 및 데이터 수집기(DataCollector) 활성화
        ai_engine = AIEngine()
        data_collector = DataCollector()
        strategy.set_ai_modules(ai_engine, data_collector)
        
        engine = BacktestEngine(broker, strategy)
        
        df = manager.load_code_data(code)
        if df is not None and not df.empty:
            engine.run(code, df)
            # 결과 요약 반환
            summary = broker.get_summary()
            summary['code'] = code
            summary['final_value'] = broker.get_total_asset_value({})
            return summary
    except Exception as e:
        return {"code": code, "error": str(e)}
    return None

def main():
    load_dotenv()
    
    data_dir = os.path.join(base_dir, "data", "historical")
    TARGET_CODES = []
    if os.path.exists(data_dir):
        for fname in os.listdir(data_dir):
            if fname.endswith("_1m.csv"):
                code = fname.replace("_1m.csv", "")
                TARGET_CODES.append(code)
                
    TARGET_CODES = sorted(list(set(TARGET_CODES)))
    
    if not TARGET_CODES:
        print("에러: data/historical 폴더에 _1m.csv 데이터 파일이 없습니다!")
        return
    
    # 각 종목당 할당 자산 (포트폴리오 개념)
    PER_STOCK_BALANCE = 5000000 
    TOTAL_INITIAL_BALANCE = PER_STOCK_BALANCE * len(TARGET_CODES)
    friction = float(os.getenv("TRADING_FRICTION", "0.009"))
    
    print("\n" + "=" * 50)
    print(f" [병렬 처리] 대규모 {len(TARGET_CODES)}종목 고속 백테스트 시작")
    print(f"대상: {', '.join(TARGET_CODES)}")
    print(f"CPU 코어 활용 병렬 실행 중...")
    print("=" * 50)

    start_time = datetime.now()
    all_results = []
    
    # 병렬 실행 (ProcessPoolExecutor 활용)
    with ProcessPoolExecutor() as executor:
        futures = {executor.submit(run_single_backtest, code, PER_STOCK_BALANCE, friction): code for code in TARGET_CODES}
        
        for future in as_completed(futures):
            res = future.result()
            if res and "error" not in res:
                all_results.append(res)
                print(f" [OK] [{res['code']}] 테스트 완료")
            elif res:
                print(f" [ERROR] [{res['code']}] 에러: {res['error']}")

    # 결과 취합 및 합산
    total_final_value = 0
    total_win = 0
    total_loss = 0
    total_trades = 0
    total_win_count = 0
    total_orders = 0
    max_mdd = 0
    
    for r in all_results:
        total_final_value += r['final_value']
        total_win += r['total_profit_sum']
        total_loss += r['total_loss_sum']
        total_trades += r['completed_trades']
        total_win_count += r['win_count']
        total_orders += r['order_count']
        max_mdd = max(max_mdd, r['max_drawdown'])

    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    total_profit_rate = calculate_total_profit(TOTAL_INITIAL_BALANCE, total_final_value)
    pf = total_win / total_loss if total_loss > 0 else 999.0

    print("\n" + "REPORT " + "=" * 43)
    print(f"[백테스트 결과 리포트: 유니버스 {len(TARGET_CODES)}종목 (Total Victory)]")
    print(f"총 실행 시간: {duration:.2f}초")
    print(f"전체 수익률: {total_profit_rate:+.2f}%")
    print(f"최종 합산 자산: {total_final_value:,.0f} 원")
    print(f"평균 승률: {(total_win_count/total_trades*100):.1f}% ({total_trades}회 중 {total_win_count}회)")
    print(f"최대 낙폭(Max MDD): {max_mdd:.2f}%")
    print(f"포트폴리오 PF: {pf:.2f}")
    print(f"총 주문 횟수: {total_orders} 회")
    print("=" * 50)

if __name__ == "__main__":
    main()
