import sys
import os
import logging
from datetime import datetime

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backtest.historical_data_manager import HistoricalDataManager
from backtest.virtual_broker import VirtualBroker
from backtest.engine import BacktestEngine, calculate_total_profit
from strategy.stefano_strategy import StefanoStrategy

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s : %(message)s'
    )

def main():
    setup_logging()
    
    # 1. 환경 설정
    initial_balance = 10000000
    target_stocks = ["005930", "000660"] # 삼성전자, SK하이닉스 (기본 예시)
    
    # data/historical 디렉토리에 있는 실제 파일들로 대체 가능
    historical_files = [f for f in os.listdir("data/historical") if f.endswith("_1m.csv")]
    if not historical_files:
        print("❌ [오류] data/historical 디렉토리에 CSV 파일이 없습니다.")
        print("💡 tools/data_fetcher.py를 먼저 실행하여 데이터를 수집하세요.")
        return
        
    codes = [f.split('_')[0] for f in historical_files]
    
    # 2. 시스템 초기화
    manager = HistoricalDataManager()
    broker = VirtualBroker(initial_balance=initial_balance)
    strategy = StefanoStrategy()
    engine = BacktestEngine(broker, strategy)
    
    print("=" * 50)
    print(f"🚀 [내장 백테스팅 엔진] 시뮬레이션 시작")
    print(f"📅 시작 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"💰 초기 자산: {initial_balance:,} 원")
    print(f"📈 대상 종목: {', '.join(codes)}")
    print("=" * 50)
    
    # 3. 종목별 루프
    for code in codes:
        try:
            df = manager.load_code_data(code)
            engine.run(code, df)
        except Exception as e:
            print(f"❌ [{code}] 백테스트 중 에러 발생: {e}")
            
    # 4. 최종 결과 출력 (상세 지표 추가)
    summary = broker.get_summary()
    final_value = broker.get_total_asset_value({}) # 모든 포지션 청산 가정
    
    total_profit_rate = calculate_total_profit(initial_balance, final_value)
    
    print("\n" + "=" * 50)
    print(f"🏁 [백테스트 상세 리포트]")
    print(f"📊 총 수익률: {total_profit_rate:+.2f}%")
    print(f"💵 최종 자산: {final_value:,.0f} 원 (손익: {final_value - initial_balance:+,} 원)")
    print(f"🎯 승률: {summary['win_rate']:.1f}% ({summary['order_count']//2} 거래 중 {summary['win_count']}회 익절)")
    print(f"📉 최대 낙폭 (MDD): {summary['max_drawdown']:.2f}%")
    print(f"⚖️ 손익비 (Profit Factor): {summary['profit_factor']:.2f}")
    print(f"🧾 총 주문 횟수: {summary['order_count']} 회 (체결 비용: {summary['total_fees']+summary['total_taxes']:,.0f} 원)")
    print("=" * 50)

if __name__ == "__main__":
    main()
