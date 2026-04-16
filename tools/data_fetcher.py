import sys
import os
import time
import pandas as pd
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer
from dotenv import load_dotenv

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 환경 변수 로드 (.env 설정값 반영을 위해 필수)
load_dotenv()

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
        print("키움증권 로그인 중...")
        self.kc.comm_connect()
        
        # 2. HTS 조건식 로딩 및 실행 지시 (우리가 .env에 적어놓은 v11조건식을 자동으로 찾아서 실행합니다)
        print("서버에 등록된 HTS 조건검색식 추출 요청 중...")
        QTimer.singleShot(1500, self.kc.get_condition_load)
        
        # 3. 5초 뒤면 이벤트 핸들러가 조건식 100개를 kc.monitored_codes에 담아둡니다. 그것을 빼옵니다!
        QTimer.singleShot(5000, self.merge_hts_condition_codes)
        
        # 4. 상태 감시 타이머
        self.monitor_timer = QTimer()
        self.monitor_timer.timeout.connect(self.check_progress)
        self.monitor_timer.start(1500)
        
        self.app.exec_()
        
    def merge_hts_condition_codes(self):
        hts_codes = list(self.kc.monitored_codes.keys())
        if hts_codes:
            print(f"🔥 [HTS API] 방금 키움 서버에서 실시간 조건식 통과 종목 {len(hts_codes)}개를 빼왔습니다!")
            for code in hts_codes:
                if code not in self.codes:
                    self.codes.append(code)
        else:
            print("⚠️ [HTS API] 조건검색식 오류이거나 현재 조건에 맞는 종목이 0개입니다. (기존 리스트만 수집)")
            
        print(f"🚀 최종 {len(self.codes)}개 종목 과거 1분봉 데이터 펌핑 시작!")
        # 수집 시작!
        QTimer.singleShot(1000, self.request_next_code)

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
                # 연속 조회일 때만 스로틀러와 겹치지 않게 미세 딜레이(0.25초)
                QTimer.singleShot(250, lambda: self.kc.request_opt10080(code, prev_next=2))
            else:
                # 수집 최종 완료
                print(f"✅ [{code}] 수집 최종 완료 ({current_len}개).")
                
                # 다음 종목으로 이동 (유예 시간 제거, 스로틀러가 알아서 분배함)
                self.current_code_idx += 1
                QTimer.singleShot(10, self.request_next_code)

if __name__ == "__main__":
    import sqlite3
    from datetime import datetime
    
    target_stocks = []
    
    # [특수 기능] 여기에 HTS에서 직접 방금 포착한 종목 코드 6자리를 콤마(,)로 묶어서 적어주시면 무조건 긁어옵니다!
    # (예시: ["005930", "000660", "012345"]) 
    manual_codes = [
        # 여기에 코드를 복사해서 붙여넣고 스크립트를 실행하세요. 비워두면 오늘 봇이 구동되면서 포착한 종목을 스캔합니다.
    ] 

    # [1] 오늘 감지된(AI Features DB에 기록된) 종목들 긁어오기
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai_features.db")
    detected_stocks = list(manual_codes) # 수동 추가 종목 병합
    if os.path.exists(db_path):
        try:
            with sqlite3.connect(db_path) as conn:
                today_str = datetime.now().strftime("%Y%m%d")
                rows = conn.execute("SELECT DISTINCT code FROM ai_signals WHERE date_key = ?", (today_str,)).fetchall()
                for r in rows:
                    if r[0] not in detected_stocks:
                        detected_stocks.append(r[0])
                print(f"✅ DB 및 수동 입력으로 총 {len(detected_stocks)}개 종목 발견.")
        except Exception as e:
            print(f"⚠️ DB 읽기 실패: {e}")
    else:
        print("ℹ️ ai_features.db를 찾을 수 없어 수동 입력 리스트만 활용합니다.")
        
    # [2] 이미 data/historical 에 존재하는 종목 제외하기
    save_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "historical")
    existing_stocks = []
    if os.path.exists(save_dir):
        for fname in os.listdir(save_dir):
            if fname.endswith("_1m.csv"):
                existing_stocks.append(fname.replace("_1m.csv", ""))
                
    # 순수하게 오늘 '새로' 추가된 종목만 필터링
    new_stocks = [code for code in detected_stocks if code not in existing_stocks]
    
    if not new_stocks:
        print("🎉 오늘 추가로 감지된(미수집된) 새로운 종목이 없습니다! 스크립트를 종료합니다.")
        sys.exit(0)
        
    print(f"🚀 총 {len(new_stocks)}개의 신규 감지 종목 데이터를 다운로드합니다: {new_stocks}")
    
    fetcher = HistoricalDataFetcher(new_stocks, target_count=6300)
    fetcher.run()
