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

from dotenv import load_dotenv

def setup_logging():
    # 1. 루트 로거 생성
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # 전체 레벨은 DEBUG로 설정
    
    # 2. 포맷터 정의
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s : %(message)s')
    
    # 3. 콘솔 핸들러 (INFO 레벨)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 4. 파일 핸들러 (타임스탬프 파일 생성)
    if not os.path.exists('logs'):
        os.makedirs('logs')
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"logs/backtest_{timestamp}.log"
    
    file_handler = logging.FileHandler(log_filename, mode='w', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    print(f"[알림] 상세 분석 로그 저장 중: {log_filename}")

def main():
    load_dotenv()
    setup_logging()
    
    # 1. 환경 설정 및 거래 마찰 비용(Friction) 계산
    initial_balance = 50000000
    # [v5.1] 최신 전략 기준 마찰 비용(0.9%) 반영 / .env 설정값 우선
    friction = float(os.getenv("TRADING_FRICTION", "0.009"))
    
    # 가상 브로커용 수수료/세금 배분 (0.2% 세금 고정, 나머지를 수수료/슬리피지에 배분)
    tax_rate = 0.002 
    remain = max(0, friction - tax_rate)
    fee_rate = (remain * 0.7) / 2.0  # 왕복 고려
    slippage = (remain * 0.3) / 2.0  # 왕복 고려
    
    # 2. 백테스트 대상 종목 확보 (절대 경로로 변경하여 어디서든 실행 가능하게 함)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    data_path = os.path.join(base_dir, "data", "historical")
    if not os.path.exists(data_path):
        print(f"[오류] {data_path} 디렉토리가 존재하지 않습니다.")
        return

    historical_files = [f for f in os.listdir(data_path) if f.endswith("_1m.csv")]
    available_codes = [f.split('_')[0] for f in historical_files]
    
    # [v5.6] 테스트 결과 일치화를 위해 병렬 엔진과 동일한 10개 핵심 종목으로 고정
    codes = [
        "000250", "005930", "000660", "247540", "298380",
        "010130", "005490", "042660", "035420", "000150"
    ]
    
    if not codes:
        print(f"[오류] {data_path} 디렉토리에 분석할 CSV 파일이 없습니다.")
        return

    # 3. 시스템 초기화
    manager = HistoricalDataManager()
    broker = VirtualBroker(
        initial_balance=initial_balance,
        fee_rate=fee_rate,
        tax_rate=tax_rate,
        slippage=slippage
    )
    strategy = StefanoStrategy()
    engine = BacktestEngine(broker, strategy)
    
    print("=" * 50)
    print(f"[내장 백테스팅 엔진] 시뮬레이션 시작")
    print(f"시작 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"초기 자산: {initial_balance:,} 원")
    print(f"대상 종목: {', '.join(codes)}")
    print("=" * 50)
    
    # 3. 종목별 루프
    for code in codes:
        try:
            df = manager.load_code_data(code)
            engine.run(code, df)
        except Exception as e:
            print(f"[{code}] 백테스트 중 에러 발생: {e}")
            
    # 4. 최종 결과 출력 (상세 지표 추가)
    summary = broker.get_summary()
    final_value = broker.get_total_asset_value({}) # 모든 포지션 청산 가정
    
    total_profit_rate = calculate_total_profit(initial_balance, final_value)
    
    print("\n" + "=" * 50)
    print(f"[백테스트 상세 리포트]")
    print(f"총 수익률: {total_profit_rate:+.2f}%")
    print(f"최종 자산: {final_value:,.0f} 원 (손익: {final_value - initial_balance:+,} 원)")
    print(f"승률: {summary['win_rate']:.1f}% ({summary['completed_trades']} 거래 중 {summary['win_count']}회 익절)")
    print(f"최대 낙폭 (MDD): {summary['max_drawdown']:.2f}%")
    print(f"손익비 (Profit Factor): {summary['profit_factor']:.2f}")
    print(f"총 주문 횟수: {summary['order_count']} 회 (체결 비용: {summary['total_fees']+summary['total_taxes']:,.0f} 원)")
    print("=" * 50)

if __name__ == "__main__":
    main()
