"""
Dopaming-Stock-Bot 메인 엔트리포인트 (Main Entrypoint)
---------------------------------------------------
애플리케이션의 시작점입니다. 환경변수를 로드하고, 로깅 시스템을 초기화하며,
KiwoomCore, OpenAPI 통신, 데이터 파이프라인, 전략 매니저 및 GUI(대시보드)를 조립(DI)하여 구동합니다.
"""

import sys
import os
from dotenv import load_dotenv
from PyQt5.QtWidgets import QApplication
from core.kiwoom_core import KiwoomCore
from core.data_pipeline import DataPipeline
from strategy.stefano_strategy import StefanoStrategy
from core.execution_manager import ExecutionManager
from core.persistence import DatabaseManager
from utils.notifier import SlackNotifier
from utils.logger import setup_logger, add_gui_logger
from ui.dashboard import Dashboard
from PyQt5.QtCore import QTimer

def main():
    """
    메인 애플리케이션 초기화 및 이벤트 루프 진입 메서드
    """
    load_dotenv()
    
    # 1. 로깅 시스템 초기화
    logger = setup_logger("DopamingBot")
    logger.info("====================================")
    logger.info("시스템 시작: Dopaming-Stock-Bot")
    logger.info("====================================")

    # 2. QApplication 객체 생성 (PyQt5 GUI, 이벤트 루프 필수)
    app = QApplication(sys.argv)
    logger.debug("QApplication 인스턴스 생성 완료")
    
    try:
        # 3. KiwoomCore 싱글톤 인스턴스 가져오기
        kiwoom = KiwoomCore.get_instance()
        
        # 4. 로그인 요청 및 대기 (CommConnect > OnEventConnect 이벤트루프 활용)
        kiwoom.comm_connect()
        
        # 5. 데이터 파이프라인 생성 및 시작, Kiwoom 코어에 연결
        pipeline = DataPipeline()
        pipeline.start_pipeline()
        kiwoom.set_data_pipeline(pipeline)
        
        # 6. 알림 및 영속성(DB) 시스템 초기화
        slack = SlackNotifier()
        db = DatabaseManager()
        
        # 환경변수 로드 및 검증
        is_dry_run = os.getenv("DRY_RUN_MODE", "True").lower() in ("true", "1", "yes")
        account_password = os.getenv("ACCOUNT_PASSWORD", "").strip()
        
        if not account_password and not getattr(kiwoom, 'is_mock', False):
            error_msg = "🔥 [치명적 오류] 계좌 비밀번호(ACCOUNT_PASSWORD)가 누락되어 초기 동기화가 불가능합니다. 시스템을 강제 종료합니다."
            logger.error(error_msg)
            slack.send_message(error_msg)
            sys.exit(1)
        elif not account_password and getattr(kiwoom, 'is_mock', False):
            logger.warning("가상(Mock) 환경이므로 계좌 비밀번호 검사를 무시합니다.")
            account_password = "0000"
            
        # 7. 실행 제어 매니저 초기화 및 코어에 연동
        execution_manager = ExecutionManager(kiwoom, db, slack, is_dry_run=is_dry_run)
        kiwoom.set_execution_manager(execution_manager)
        
        # 8. 전략 엔진 초기화
        strategy = StefanoStrategy()

        # 9. GUI 대시보드 화면 생성 & 로깅 신호 연결
        dashboard = Dashboard(kiwoom, pipeline, execution_manager, strategy)
        gui_handler = add_gui_logger("DopamingBot")
        gui_handler.signal.new_log.connect(dashboard.append_log)
        
        # 10. 대시보드 화면 표출
        dashboard.show()
        
        # 11. 계좌 동기화 통신 시작 (동기화 완료 응답 시 자동으로 get_condition_load 호출 연계됨)
        logger.info("내 계좌 예수금 및 잔고 동기화 절차 시작...")
        kiwoom.request_account_info(account_password)
        
        # 11-1. 폴백(Fallback) 타이머: 15초 후에도 조건검색 로드가 안 됐다면 강제 실행
        # (계좌 비밀번호 미등록 등으로 opw00018 응답이 없을 때를 대비)
        kiwoom._condition_loaded = False
        def _fallback_condition_load():
            if not kiwoom._condition_loaded:
                logger.warning("⚠️ [폴백] 계좌 동기화 응답 없음 → 조건검색식 직접 로드 시도")
                kiwoom.get_condition_load()
        fallback_timer = QTimer()
        fallback_timer.setSingleShot(True)
        fallback_timer.timeout.connect(_fallback_condition_load)
        fallback_timer.start(15000)  # 15초 후 1회 실행
        kiwoom._fallback_timer = fallback_timer  # 참조 유지 (GC 방지)
        
        # 12. 타이머 기반 전략 평가 및 손익 관리 분배 (10초 주기)
        def evaluate_strategy_and_positions():
            with pipeline.lock:
                codes = list(pipeline.data_1m.keys())
            
            # 1. 오픈 포지션 손익 관리 (-2% 손절, +4% 익절)
            execution_manager.monitor_positions(pipeline)
            
            # 2. 신규 진입 매수 시그널 탐색
            for code in codes:
                df_1m, df_5m = pipeline.get_data(code)
                is_buy_signal = strategy.analyze(code, df_1m, df_5m)
                if is_buy_signal:
                    logger.warning(f"⭐⭐⭐ [{code}] 매수 시그널 발생! ExecutionManager 진입!")
                    execution_manager.execute_buy(code, pipeline)
                    
        timer = QTimer()
        timer.timeout.connect(evaluate_strategy_and_positions)
        timer.start(10000) # 10초마다 실행

    except Exception as e:
        logger.error(f"시스템 실행 중 예외 발생: {e}", exc_info=True)

    # 13. 메인 이벤트 루프 실행 (프로그램 종료 방지 및 GUI, 통신 폴링 지원)
    logger.info("QApplication 메인 이벤트 루프 진입")
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
