import queue
import logging
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QMessageBox

class ApiThrottler:
    """
    키움 API의 초당 요청 제한(초당 5회 등)을 우회하기 위한 요청 지연/큐 관리 클래스.
    모든 데이터 및 주문 TR 발송은 이 클래스를 거쳐 안전하게 서버로 전송됩니다.
    """
    def __init__(self, kiwoom_core):
        """
        초기화 메서드
        Args:
            kiwoom_core: KiwoomCore(QAxWidget) 인스턴스 (실질적인 OpenAPI 통신 수행 객체)
        """
        self.logger = logging.getLogger("DopamingBot.ApiThrottler")
        self.kiwoom_core = kiwoom_core
        self.request_queue = queue.Queue()
        
        # 0.3초마다 큐를 비우는 타이머 작동
        self.throttle_timer = QTimer()
        self.throttle_timer.timeout.connect(self._process_request_queue)
        self.throttle_timer.start(300)

    def put(self, req_dict):
        """
        발송할 API 요청을 안전한 스로틀링 큐에 추가합니다.
        
        Args:
            req_dict (dict): "type" 파라미터가 포함된 요청 Dictionary
        """
        self.request_queue.put(req_dict)

    def _process_request_queue(self):
        """
        타이머에 의해 주기적(0.3초)으로 호출되어 큐를 확인하고 서버로 실발송합니다.
        키움증권 서버 과부하 및 에러(1초 5회 제한 등)를 방지하는 핵심 메서드입니다.
        """
        if self.request_queue.empty():
            return
            
        req = self.request_queue.get()
        req_type = req.get("type")
        
        if req_type == "send_order":
            args = req.get("args")
            ret = self.kiwoom_core.dynamicCall("SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)", *args)
            if ret == 0:
                self.logger.info(f"SendOrder 발송 성공 - args={args}")
            else:
                self.logger.error(f"SendOrder 발송 실패 (에러: {ret}) - args={args}")
                
        elif req_type == "opt10080": # 주식분봉차트조회요청
            code = req.get("code")
            rqname = f"opt10080_req_{code}"
            
            # 1. SetInputValue 원자성 보장 (타이머 블록 안에서 묶음 실행)
            self.kiwoom_core.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
            self.kiwoom_core.dynamicCall("SetInputValue(QString, QString)", "틱범위", "1")  # 1분봉
            self.kiwoom_core.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
            
            # 2. CommRqData 발송
            ret = self.kiwoom_core.dynamicCall("CommRqData(QString, QString, int, QString)", rqname, "opt10080", 0, "2000")
            if ret == 0:
                self.logger.info(f"[스로틀링] {code} 과거 1분봉 데이터 요청 발송 완료")
            else:
                self.logger.error(f"[스로틀링] {code} 과거 차트 요청 실패 (에러: {ret})")

        elif req_type in ["opw00001", "opw00018"]:
            rqname = f"{req_type}_req"
            self.kiwoom_core.dynamicCall("SetInputValue(QString, QString)", "계좌번호", req.get("account"))
            self.kiwoom_core.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")  # 키움 권장 공백 처리
            self.kiwoom_core.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
            self.kiwoom_core.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")
            
            ret = self.kiwoom_core.dynamicCall("CommRqData(QString, QString, int, QString)", rqname, req_type, 0, "2000")
            if ret == 0:
                self.logger.info(f"[스로틀링] 계좌 {req_type} 데이터 요청 발송")
            else:
                self.logger.error(f"[스로틀링] 계좌 {req_type} 요청 실패 (에러: {ret})")
                if ret == -202:
                    # -202 에러 처리 가이드
                    QMessageBox.warning(None, "계좌 데이터 요청 실패 (-202)", 
                                        f"[{req_type}] 계좌 정보를 불러오지 못했습니다.\n\n"
                                        "원인: 계좌 비밀번호가 트레이의 OpenAPI 시스템에 등록되지 않았습니다!\n"
                                        "조치: 화면 우측 하단 시스템 트레이(시계 옆)에서 키움 OpenAPI 아이콘을 "
                                        "우클릭하여 [계좌비밀번호 저장] 메뉴에 모의투자 비밀번호(보통 0000)를 입력 및 등록해주세요.")
