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
            print(f"[HTS API] Extracted {len(hts_codes)} codes from Kiwoom server.")
            for code in hts_codes:
                if code not in self.codes:
                    self.codes.append(code)
        else:
            print("[HTS API] No codes found from search conditions.")
            
        print(f"[INFO] Starting data fetch for total {len(self.codes)} stocks.")
        # 수집 시작!
        QTimer.singleShot(1000, self.request_next_code)

    def request_next_code(self):
        if self.current_code_idx >= len(self.codes):
            print("All codes collection finished.")
            self.is_done = True
            self.app.quit()
            return
            
        code = self.codes[self.current_code_idx]
        print(f"\n>>> [{code}] Fetching...")
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
                
                print(f"    - [{code}] Requesting more... (Current: {current_len}, Save: OK)")
                # [Optimization] 스로틀러가 속도를 조절하므로 내부 지연은 최소화(10ms)
                QTimer.singleShot(10, lambda: self.kc.request_opt10080(code, prev_next=2))
            else:
                # 수집 최종 완료
                print(f"[OK] [{code}] Fetch Finished ({current_len} records).")
                
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
        "000250", "001810", "003280", "004410", "006340", "009200", "010820", "011930", "015260", 
        "018470", "019170", "027360", "032820", "036170", "039240", "043260", "047040", "049480", 
        "050890", "062040", "064260", "066970", "068330", "077360", "078130", "084650", "100790", 
        "101670", "115500", "118000", "131760", "150900", "152550", "158430", "184230", "185490", 
        "209640", "225190", "253840", "258790", "261780", "293580", "299660", "307870", "319400", 
        "321260", "332570", "348950", "375500", "430690", "452190", "456010", "476830", "900300"
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
                print(f"[OK] DB & Manual codes total: {len(detected_stocks)}")
        except Exception as e:
            print(f"[WARN] DB Read Failure: {e}")
    else:
        print("[INFO] ai_features.db not found. Using manual list.")
        
    # [2] 이미 data/historical 에 존재하는 종목 제외하기
    save_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "historical")
    existing_stocks = []
    if os.path.exists(save_dir):
        for fname in os.listdir(save_dir):
            if fname.endswith("_1m.csv"):
                existing_stocks.append(fname.replace("_1m.csv", ""))
                
    # 순수하게 오늘 '새로' 추가된 종목만 필터링 (1주일치 강제 업데이트를 위해 무력화)
    # new_stocks = [code for code in detected_stocks if code not in existing_stocks]
    new_stocks = detected_stocks
    
    if not new_stocks:
        print("[DONE] No signals detected today. Finishing.")
        sys.exit(0)
        
    # [3] 최적화: 수집 가속 및 상한가 필터 해제 강제 설정
    os.environ["API_THROTTLE_INTERVAL"] = "200"
    os.environ["STRATEGY_ALLOW_CEILING_DATA"] = "True"
    
    print(f"[RUN] Downloading data for {len(new_stocks)} new stocks: {new_stocks}")
    
    # [1주일치 데이터] 5영업일(약 1950분) 수집을 위해 target_count를 2000으로 설정
    fetcher = HistoricalDataFetcher(new_stocks, target_count=2000)
    fetcher.run()
