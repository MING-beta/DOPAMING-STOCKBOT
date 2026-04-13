import sys
import os
import time
import pandas as pd
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.kiwoom_core import KiwoomCore
from core.data_pipeline import DataPipeline

class HistoricalDataFetcher:
    def __init__(self, codes, target_count=3000):
        self.app = QApplication(sys.argv)
        self.kc = KiwoomCore.get_instance()
        self.pipeline = DataPipeline()
        self.kc.set_data_pipeline(self.pipeline)
        
        self.codes = codes
        self.target_count = target_count
        self.current_code_idx = 0
        self.is_done = False
        
        # 데이터 저장 경로
        self.save_dir = os.path.join("data", "historical")
        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)

    def run(self):
        # 1. 로그인
        self.kc.comm_connect()
        
        # 2. 첫 번째 종목 요청
        QTimer.singleShot(1000, self.request_next_code)
        
        # 3. 상태 감시 타이머 (서버 과부하 방지를 위해 1.5초로 완화)
        self.monitor_timer = QTimer()
        self.monitor_timer.timeout.connect(self.check_progress)
        self.monitor_timer.start(1500)
        
        self.app.exec_()

    def request_next_code(self):
        if self.current_code_idx >= len(self.codes):
            print("모든 종목 수집 완료!")
            self.is_done = True
            self.app.quit()
            return
            
        code = self.codes[self.current_code_idx]
        print(f"\n>>> [{code}] 데이터 수집 시작...")
        self.kc.request_opt10080(code, prev_next=0)

    def check_progress(self):
        if self.current_code_idx >= len(self.codes):
            return
            
        code = self.codes[self.current_code_idx]
        
        # DataPipeline에 데이터가 들어오고, 다음 조회가 필요한지 확인
        with self.pipeline.lock:
            next_state = getattr(self.pipeline, 'next_state', {}).get(code)
            df = self.pipeline.data_1m.get(code, pd.DataFrame())
            
        if next_state is not None:
            # 현재까지 수집된 개수 및 저장 처리
            current_len = len(df)
            csv_path = os.path.join(self.save_dir, f"{code}_1m.csv")
            
            # 중간 저장 (Autosave): 데이터가 있을 경우 무조건 파일로 덤프하여 유실 방지
            if not df.empty:
                df.to_csv(csv_path)
            
            if next_state == "2" and current_len < self.target_count:
                # 다음 차례 요청 (중복 요청 방지를 위해 상태 초기화)
                with self.pipeline.lock:
                    self.pipeline.next_state[code] = None
                
                print(f"    - [{code}] 추가 데이터 요청 중... (현재: {current_len}개, 중간저장 완료)")
                self.kc.request_opt10080(code, prev_next=2)
            else:
                # 수집 최종 완료
                print(f"✅ [{code}] 수집 최종 완료 ({current_len}개).")
                
                # 다음 종목으로 이동 (충분한 유예 시간 2초 확보)
                self.current_code_idx += 1
                QTimer.singleShot(2000, self.request_next_code)

if __name__ == "__main__":
    # 오늘 API로 확인된 실제 보유 종목 리스트
    target_stocks = ["005290", "010120", "010130", "061040", "203650", "234340"]
    fetcher = HistoricalDataFetcher(target_stocks, target_count=9000)
    fetcher.run()
