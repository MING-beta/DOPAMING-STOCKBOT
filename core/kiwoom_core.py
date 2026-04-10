import sys
import logging
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QEventLoop, QTimer
from PyQt5.QtWidgets import QMessageBox

from core.api_throttler import ApiThrottler
from core.event_handlers import EventHandler

class KiwoomCore(QAxWidget):
    """
    키움증권 Open API+ 통신 세션을 담당하는 싱글톤 클래스.
    본 클래스는 네트워크 통신 및 콜백 바인딩만 전담하며,
    실질적인 메시지 큐(스로틀링)와 데이터 수신 및 파싱은 각각 
    ApiThrottler, EventHandler 모듈로 위임(Delegation)하여 복잡도를 낮춥니다.
    """
    _instance = None

    @classmethod
    def get_instance(cls):
        """싱글톤 인스턴스 반환"""
        if cls._instance is None:
            cls._instance = KiwoomCore()
        return cls._instance

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger("DopamingBot.KiwoomCore")
        self.logger.info("KiwoomCore 싱글톤 인스턴스 초기화")
        
        # 외부 시스템 위임 객체들
        self.throttler = ApiThrottler(self)
        self.event_handler = EventHandler(self)
        
        # 데이터 파이프라인 및 실행 매니저 참조 변수
        self.data_pipeline = None
        self.execution_manager = None
        self.available_cash = 0
        self.reserved_cash = 0  # [시스템 보호] 주문 중인 가상 예약금 (중복 매수 방지)
        self._condition_loaded = False
        self.login_event_loop = None
        # 현재 실시간 감시 중인 종목 데이터: { 종목코드: 감시시작타임스탬프 }
        self.monitored_codes = {}
        # 초기 총 자산 (리스크 가드 기준점)
        self.initial_total_assets = 0
        
        # Kiwoom OpenAPI+ 제어기 (COM 오브젝트) 생성
        success = self.setControl("KHOPENAPI.KHOpenAPICtrl.1")
        
        # [Win32 핸들 확보] 화면이 뜨기 전 강제로 OS 핸들을 할당받아 '핸들값 없음' 오류 방지
        _ = self.winId()
        
        # 64비트 문제나 API 미설치 시 예외(Mock) 렌더링
        if not success:
            self.logger.error("🔥 키움증권 OpenAPI+ 활성화 실패! (64비트 파이썬이거나 설치되지 않음)")
            self.logger.warning("가상(Mock) 우회 모드로 구동합니다. 실제 통신은 불가합니다.")
            self.is_mock = True
            self._setup_mock_events()
        else:
            self.is_mock = False
            self._setup_events()

    def set_data_pipeline(self, pipeline):
        """파이프라인 인스턴스 주입"""
        self.data_pipeline = pipeline
        
    def set_execution_manager(self, manager):
        """실행(주문) 매니저 주입"""
        self.execution_manager = manager

    def _setup_events(self):
        """
        API 이벤트와 콜백 메서드(슬롯)를 연결합니다.
        PyQt 시그널이 발생하면 EventHandler 클래스로 이벤트를 넘겨줍니다.
        """
        self.logger.debug("API 이벤트 슬롯 등록 시작")
        self.OnEventConnect.connect(self._on_event_connect)
        self.OnReceiveConditionVer.connect(self.event_handler.on_receive_condition_ver)
        self.OnReceiveTrCondition.connect(self.event_handler.on_receive_tr_condition)
        self.OnReceiveRealCondition.connect(self.event_handler.on_receive_real_condition)  # 실시간 편입/이탈
        self.OnReceiveRealData.connect(self.event_handler.on_receive_real_data)
        self.OnReceiveChejanData.connect(self.event_handler.on_receive_chejan_data)
        self.OnReceiveTrData.connect(self.event_handler.on_receive_tr_data)
        self.logger.debug("API 이벤트 슬롯 등록 완료")

    def _setup_mock_events(self):
        """API 로드 실패 시 디버깅을 위해 응답을 조작하는 가상(Mock) 함수 맵핑"""
        self.comm_connect = lambda: self._on_event_connect(0)
        self.get_account_list = lambda: ["80000000000"]
        self.get_condition_load = lambda: self.event_handler.on_receive_condition_ver(1, "")
        
        def mock_dynamicCall(func_name, *args):
            if "GetCommData" in func_name and "주문가능금액" in args: return "50000000"
            if "GetCommData" in func_name and "추정평가자산" in args: return "50000000"
            if "GetRepeatCnt" in func_name: return 0
            if "GetConditionNameList" in func_name: return "000^가상시뮬레이션조건;"
            if "SendCondition" in func_name: return 1
            if "CommRqData" in func_name:
                rqname = args[0]
                trcode = args[1]
                if rqname == "opw00018_req":
                    QTimer.singleShot(500, lambda: self.event_handler.on_receive_tr_data("1000", rqname, trcode, "", "", 0, "", "", ""))
                elif rqname == "opw00001_req":
                    QTimer.singleShot(300, lambda: self.event_handler.on_receive_tr_data("1000", rqname, trcode, "", "", 0, "", "", ""))
                return 0
            return 0
            
        self.dynamicCall = mock_dynamicCall
        self.reconnect = lambda: self._on_event_connect(0)

    def get_master_last_price(self, code):
        """특정 종목의 전일 종가(기준가)를 반환합니다."""
        if getattr(self, 'is_mock', False): return 10000
        res = self.dynamicCall("GetMasterLastPrice(QString)", code)
        return float(res) if res else 0

    def comm_connect(self):
        """서버 로그인 요청 (비동기 처리 대기)"""
        self.logger.info("CommConnect() 호출: 로그인 요청")
        self.dynamicCall("CommConnect()")
        self.login_event_loop = QEventLoop()
        self.login_event_loop.exec_()
        self.logger.debug("CommConnect 로그인 이벤트 루프 종료")

    def reconnect(self):
        """서버와의 통신 재연결을 시도합니다."""
        self.logger.warning("🔄 [재연결] 서버와의 재연결을 시도합니다...")
        # 기존 로직 및 상태 초기화 (필요시)
        self._condition_loaded = False
        self.monitored_codes = set()
        # 재로그인 시도
        self.comm_connect()

    def _on_event_connect(self, err_code):
        """로그인 콜백"""
        if err_code == 0:
            self.logger.info("로그인 성공! (err_code=0)")
        else:
            self.logger.error(f"로그인 실패 (err_code={err_code})")
        if self.login_event_loop:
            self.login_event_loop.exit()

    def get_account_list(self):
        """계좌번호 반환"""
        if hasattr(self, '_cached_acc_list') and self._cached_acc_list:
            return self._cached_acc_list
        acc_list = self.dynamicCall("GetLoginInfo(QString)", "ACCNO")
        self._cached_acc_list = acc_list.rstrip(';').split(';')
        return self._cached_acc_list

    def get_connect_state(self):
        """현재 서버와 접속 상태를 반환합니다. (0: 미연결, 1: 연결됨)"""
        if getattr(self, 'is_mock', False):
            return 1
        return self.dynamicCall("GetConnectState()")

    def request_account_info(self, password):
        """계좌 예수금/잔고 동기화를 Throttler를 통해 지시합니다."""
        acc_list = self.get_account_list()
        if not acc_list:
            self.logger.error("계좌 리스트 없음")
            return
        acc_no = acc_list[0]
        self.throttler.put({"type": "opw00001", "account": acc_no, "password": password})
        self.throttler.put({"type": "opw00018", "account": acc_no, "password": password})
        self.logger.debug(f"[스로틀링 큐 적재] 계좌 동기화 요청 (acc={acc_no})")

    def request_opt10080(self, code):
        """1분봉 차트 조회를 Throttler를 통해 지시하며, 기준가(전일종가)를 동기화합니다."""
        # 전일 종가 동기화 (대시보드 등락률용 정밀 보정)
        prev_close = self.get_master_last_price(code)
        if self.data_pipeline:
            self.data_pipeline.reference_prices[code] = prev_close
            
        self.throttler.put({"type": "opt10080", "code": code})
        self.logger.debug(f"[스로틀링 큐 적재] 과거 차트(opt10080) 및 기준가({prev_close}) 요청: {code}")

    def send_order(self, rqname, screen_no, order_type, code, qty, price, hoga_gb, org_order_no):
        """메인 주문 전송"""
        acc_list = self.get_account_list()
        if not acc_list: return -1
        self.throttler.put({
            "type": "send_order",
            "args": (rqname, screen_no, acc_list[0], order_type, code, qty, price, hoga_gb, org_order_no)
        })
        self.logger.debug(f"[스로틀링 큐 적재] 주문 대기: {rqname} ({code})")
        return 0

    def get_condition_load(self):
        """서버조건식 로드"""
        self.logger.info("조건식 목록 요청(GetConditionLoad)")
        ret = self.dynamicCall("GetConditionLoad()")
        if not ret: self.logger.error("GetConditionLoad 실패")

    def send_condition(self, screen_no, cond_name, cond_index, is_realtime):
        """조건식 시스템 가동"""
        self.logger.info(f"조건검색 실행: {cond_name} (index: {cond_index})")
        ret = self.dynamicCall("SendCondition(QString, QString, int, int)", screen_no, cond_name, cond_index, is_realtime)
        if not ret: self.logger.error("SendCondition 실패")

    def set_real_reg(self, screen_no, code_list, fid_list, opt_type):
        """실시간(Real) 주식 체결 수신기 등록"""
        code_str = ";".join(code_list) if isinstance(code_list, list) else code_list
        self.logger.info(f"SetRealReg 요청: Codes={code_str}")
        self.dynamicCall("SetRealReg(QString, QString, QString, QString)", screen_no, code_str, fid_list, opt_type)

    def set_real_remove(self, screen_no, code):
        """실시간(Real) 수신 등록 해제 (조건 이탈 종목)"""
        self.logger.info(f"SetRealRemove 요청: {code}")
        self.dynamicCall("SetRealRemove(QString, QString)", screen_no, code)
