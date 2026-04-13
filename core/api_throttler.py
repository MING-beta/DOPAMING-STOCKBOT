import queue
import logging
import itertools
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QMessageBox

class ApiThrottler:
    """
    키움 API의 요청 제한을 관리하며, 우선순위 큐를 통해 주문 성능을 극대화한 스로틀러.
    - P0: 주문 발송 (최우선)
    - P1: 잔고/계좌 조회
    - P2: 과거 차트 데이터 조회
    """
    def __init__(self, kiwoom_core):
        self.logger = logging.getLogger("DopamingBot.ApiThrottler")
        self.kiwoom_core = kiwoom_core
        self.request_queue = queue.PriorityQueue()
        self._counter = itertools.count() # 동일 우선순위 발생 시 선입선출 보장을 위한 타이브레이커
        
        # 초당 5회 제한(200ms)까지 가속하여 지연 시간 최소화
        # 초당 4회 제한(250ms)으로 조정하여 '시세 과부하(-200)' 방어 및 안정성 확보
        self.throttle_timer = QTimer()
        self.throttle_timer.timeout.connect(self._process_request_queue)
        self.throttle_timer.start(500) 
        
        self._is_paused = False # 과부하 보호를 위한 정지 플래그

    def put(self, req_dict):
        """
        요청 타입에 따라 우선순위를 부여하여 큐에 삽입합니다.
        (priority, counter, req_dict) 튜플을 사용하여 데이터 간 비교 에러를 방지합니다.
        """
        req_type = req_dict.get("type")
        
        if req_type == "send_order":
            priority = 0
        elif req_type in ["opw00001", "opw00018"]:
            priority = 1
        else:
            priority = 2
            
        self.request_queue.put((priority, next(self._counter), req_dict))

    def _process_request_queue(self):
        if self._is_paused or self.request_queue.empty():
            return
            
        # 가장 높은 우선순위의 요청을 가져옴 (순번을 통한 자동 정렬)
        priority, count, req = self.request_queue.get()
        req_type = req.get("type")
        
        if req_type == "send_order":
            args = req.get("args")
            ret = self.kiwoom_core.dynamicCall("SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)", list(args))
            if ret == 0:
                self.logger.info(f"🚀 [P{priority} 주문발송] 성공 - args={args}")
            else:
                self.logger.error(f"❌ [P{priority} 주문실패] 에러: {ret} - args={args}")
                if ret == -200:
                    self._retry_later(req)
                
        elif req_type == "opt10080": 
            code = req.get("code")
            rqname = req.get("rqname", f"opt10080_req_{code}")
            prev_next = req.get("prev_next", 0)
            
            self.kiwoom_core.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
            self.kiwoom_core.dynamicCall("SetInputValue(QString, QString)", "틱범위", "1")
            self.kiwoom_core.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
            
            ret = self.kiwoom_core.dynamicCall("CommRqData(QString, QString, int, QString)", rqname, "opt10080", prev_next, "2000")
            if ret == 0:
                self.logger.info(f"📊 [P{priority} 데이터수신] {code} 1분봉 요청 완료 (prev_next={prev_next})")
            else:
                self.logger.error(f"⚠️ [P{priority} 요청실패] {code} 데이터 (에러: {ret})")
                if ret in [-200, -300]:
                    self._retry_later(req)

        elif req_type in ["opw00001", "opw00018"]:
            rqname = f"{req_type}_req"
            self.kiwoom_core.dynamicCall("SetInputValue(QString, QString)", "계좌번호", req.get("account"))
            self.kiwoom_core.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
            self.kiwoom_core.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
            self.kiwoom_core.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")
            
            ret = self.kiwoom_core.dynamicCall("CommRqData(QString, QString, int, QString)", rqname, req_type, 0, "2000")
            if ret == 0:
                self.logger.info(f"💳 [P{priority} 계좌조회] {req_type} 발송")
            else:
                self.logger.error(f"⚠️ [P{priority} 조회실패] {req_type} (에러: {ret})")
                if ret in [-200, -300]:
                    self._retry_later(req)
                elif ret == -202:
                    QMessageBox.warning(None, "계좌 데이터 요청 실패 (-202)", 
                                        "계좌 비밀번호가 OpenAPI 시스템에 등록되지 않았습니다.")

        elif req_type == "opt10074":
            rqname = "opt10074_req"
            self.kiwoom_core.dynamicCall("SetInputValue(QString, QString)", "계좌번호", req.get("account"))
            self.kiwoom_core.dynamicCall("SetInputValue(QString, QString)", "시작일자", req.get("start_date"))
            self.kiwoom_core.dynamicCall("SetInputValue(QString, QString)", "종료일자", req.get("end_date"))
            
            ret = self.kiwoom_core.dynamicCall("CommRqData(QString, QString, int, QString)", rqname, "opt10074", 0, "2000")
            if ret == 0:
                self.logger.info(f"💰 [P{priority} 실현손익] opt10074 요청 발송 ({req.get('start_date')})")
            else:
                self.logger.error(f"⚠️ [P{priority} 요청실패] opt10074 (에러: {ret})")
                if ret in [-200, -300]:
                    self._retry_later(req)

    def _retry_later(self, req):
        """과부하 에러(-200) 발생 시 시스템을 5초간 일시 정지하고 3초 후 재시도 합니다."""
        if not self._is_paused:
            self._is_paused = True
            self.logger.warning("🚨 [시스템 보호] 과부하 감지로 인해 모든 API 요청을 5초간 일시 중단합니다.")
            QTimer.singleShot(5000, self._resume_throttler)
            
        self.logger.warning(f"⏳ [재시도 대기] {req.get('code') or req.get('type')} 요청을 3초 후 큐에 재인큐합니다.")
        QTimer.singleShot(3000, lambda: self.put(req))

    def _resume_throttler(self):
        """시스템 보호 정지를 해제합니다."""
        self._is_paused = False
        self.logger.info("✅ [시스템 재개] API 요청 중단이 해제되었습니다.")

    def clear_queue(self):
        """현재 대기 중인 모든 API 요청을 강제로 삭제합니다."""
        count = 0
        while not self.request_queue.empty():
            try:
                self.request_queue.get_nowait()
                count += 1
            except queue.Empty:
                break
        if count > 0:
            self.logger.warning(f"🧹 [큐 세척] 장 종료로 인해 대기 중이던 {count}개의 요청을 삭제했습니다.")
