import sys
import os
import json
import logging
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer

# 봇 경로 추가
base_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(base_dir)

from core.kiwoom_core import KiwoomCore
from core.data_pipeline import DataPipeline
from strategy.stefano_strategy import StefanoStrategy
from core.execution_manager import ExecutionManager
from core.persistence import DatabaseManager
from utils.notifier import SystemNotifier
from utils.logger import setup_logger, add_gui_logger
from ui.dashboard import Dashboard

def run_test():
    logger = setup_logger("DopamingBot_Test")
    app = QApplication(sys.argv)
    
    # 1. 의존성 셋업 (Mock/Dry Run)
    os.environ["DRY_RUN_MODE"] = "True"
    os.environ["ACCOUNT_PASSWORD"] = "0000"
    
    # 가상 모드로 코어 우회 (실제 로그인 X, UI만 테스트)
    kiwoom = KiwoomCore.get_instance()
    kiwoom.is_mock = True
    
    pipeline = DataPipeline()
    db = DatabaseManager()
    slack = SystemNotifier()
    
    slack.send_message = lambda x: logger.info(f"Mock Slack: {x}")
    
    execution_manager = ExecutionManager(kiwoom, db, slack, is_dry_run=True)
    strategy = StefanoStrategy()
    
    # 2. 대시보드 생성 및 연결
    dashboard = Dashboard(kiwoom, pipeline, execution_manager, strategy)
    gui_handler = add_gui_logger("DopamingBot")
    gui_handler.signal.new_log.connect(dashboard.append_log)
    
    dashboard.show()
    
    # 3. 테스트용 가짜 데이터 주입 및 매수/매도 시뮬레이션 이벤트
    def inject_mock_event():
        logger.info("====================================")
        logger.info("✅ UI 시스템 검증: 가상 매수 시그널 발생 테스트")
        logger.info("====================================")
        # 임의의 코드(예: 삼성전자)로 매수 실행 함수 직접 호출
        code = "005930"
        
        # 가짜 파이프라인 데이터 세팅 (에러 방지용)
        import pandas as pd
        pipeline.data_1m[code] = pd.DataFrame([{"close": 70000}])
        
        execution_manager.execute_buy(code, pipeline, signal_type="VCP선취매 테스트")
        
        # 3초 뒤 매도 테스트
        QTimer.singleShot(3000, lambda: execution_manager.execute_sell(code, pipeline, sell_type="익절 테스트"))
        
        # 10초 뒤 자동 종료
        QTimer.singleShot(10000, app.quit)
        
    # UI가 뜨고 2초 뒤에 이벤트 인젝션
    QTimer.singleShot(2000, inject_mock_event)
    
    sys.exit(app.exec_())

if __name__ == "__main__":
    run_test()
