import sys
import os
import time
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.kiwoom_core import KiwoomCore
from core.data_pipeline import DataPipeline

class PositionChecker:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.kc = KiwoomCore.get_instance()
        self.is_done = False
        
        # 계좌 비밀번호 (환경변수에서 로드, 없으면 기본값)
        self.account_password = os.getenv("ACCOUNT_PASSWORD", "0000")

    def run(self):
        print("🔍 키움 API 접속 중...")
        self.kc.comm_connect()
        
        # 로그인 및 계좌 정보 로드 대기
        QTimer.singleShot(2000, self.request_positions)
        
        # 상태 감시
        self.monitor_timer = QTimer()
        self.monitor_timer.timeout.connect(self.check_finish)
        self.monitor_timer.start(500)
        
        self.app.exec_()

    def request_positions(self):
        account_list = self.kc.get_account_list()
        if not account_list:
            print("❌ 계좌 정보를 찾을 수 없습니다. 로그인을 확인하세요.")
            self.app.quit()
            return
            
        # 첫 번째 계좌 사용
        account = account_list[0]
        print(f"💳 계좌번호 [{account}]의 보유 종목을 조회합니다...")
        
        # 계좌 잔고 요청 (opw00018 포함됨)
        self.kc.request_account_info(self.account_password)

    def check_finish(self):
        # KiwoomCore의 positions 데이터가 채워지는지 감시
        # EventHandler에서 opw00018 수신 시 kc.server_positions를 업데이트함
        if hasattr(self.kc, 'server_positions') and self.kc.server_positions:
            print("\n" + "="*40)
            print("📦 [현재 보유 종목 리스트]")
            codes = list(self.kc.server_positions.keys())
            
            for code in codes:
                name = self.kc.dynamicCall("GetMasterCodeName(QString)", code)
                print(f"- {name} ({code})")
                
            print("="*40)
            print(f"\n✅ 수집기에 복사할 코드: {', '.join(codes)}")
            print("="*40)
            
            self.is_done = True
            self.app.quit()
        elif time.time() % 5 < 1: # 주기적 진행 알림
             print("⏳ 데이터를 기다리는 중...")

if __name__ == "__main__":
    checker = PositionChecker()
    checker.run()
