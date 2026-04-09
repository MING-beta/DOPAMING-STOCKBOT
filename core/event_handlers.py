import pandas as pd
from datetime import datetime

class EventHandler:
    """
    키움 서버로부터 수신되는 각종 콜백 이벤트(TR 응답, 실시간 체결, 조건검색 결과 등)를
    전담하여 파싱하고 적절한 파이프라인이나 매니저에게 데이터를 라우팅하는 핸들러.
    """
    def __init__(self, kiwoom_core):
        """
        초기화 메서드
        Args:
            kiwoom_core: 데이터를 보낼 부모 KiwoomCore 인스턴스
        """
        self.kc = kiwoom_core
        self.logger = self.kc.logger

    def on_receive_condition_ver(self, lRet, sMsg):
        """서버로부터 조건검색식 목록을 성공적으로 받아왔을 때의 처리"""
        if lRet == 1:
            self.kc._condition_loaded = True
            self.logger.info("조건검색식 로드 성공")
            cond_str = self.kc.dynamicCall("GetConditionNameList()")
            self.logger.info(f"보유 조건검색식 목록: {cond_str}")
            
            # 검색식 목록 중 가장 첫 번째 조건식을 가져와 실시간 감시 시작
            if cond_str:
                first_cond = cond_str.split(';')[0]
                if '^' in first_cond:
                    c_index, c_name = first_cond.split('^')
                    self.kc.send_condition("0156", c_name, int(c_index), 1)
        else:
            self.logger.error(f"조건검색식 로드 실패 ({sMsg})")

    def on_receive_tr_condition(self, sScrNo, strCodeList, strConditionName, nIndex, nNext):
        """조건검색 등록 후 현재 조건에 맞는 종목 초기 목록 수신 처리"""
        self.logger.info(f"[{strConditionName}] 검색 결과 수신")
        code_list = strCodeList.rstrip(';').split(';')
        if code_list == ['']: code_list = []
        self.logger.info(f"검색된 종목 수: {len(code_list)}건 -> {code_list}")
        
        # 감시 종목 집합 초기화 및 등록
        self.kc.monitored_codes = set(code_list)
        
        # 검색된 종목들에 대해 실시간 틱 데이터 수신 등록 및 과거 데이터 프리페치
        if code_list:
            self.kc.set_real_reg("1000", code_list, "10;15;20", "0") # FID: 10(현재가), 15(거래량), 20(체결시간)
            for code in code_list:
                self.kc.request_opt10080(code)

    def on_receive_real_condition(self, strCode, strType, strConditionName, strConditionIndex):
        """
        실시간 조건검색 편입(I)/이탈(D) 이벤트 처리
        - strType == 'I': 조건 편입 → 감시 목록 추가 및 실시간 등록
        - strType == 'D': 조건 이탈 → 감시 목록 제거 및 실시간 해제
        """
        if strType == 'I':  # 편입
            if strCode not in self.kc.monitored_codes:
                self.kc.monitored_codes.add(strCode)
                self.logger.info(f"[실시간 조건 편입] {strCode} ← {strConditionName} | 총 감시 종목: {len(self.kc.monitored_codes)}개")
                # 실시간 틱 수신 추가 등록 ("1"은 기존 등록에 추가)
                self.kc.set_real_reg("1000", [strCode], "10;15;20", "1")
                # 해당 종목 과거 분봉 차트 프리페치
                self.kc.request_opt10080(strCode)
            else:
                self.logger.debug(f"[실시간 조건 편입] {strCode} - 이미 감시 중, 무시")
                
        elif strType == 'D':  # 이탈
            if strCode in self.kc.monitored_codes:
                self.kc.monitored_codes.discard(strCode)
                self.logger.info(f"[실시간 조건 이탈] {strCode} ← {strConditionName} | 총 감시 종목: {len(self.kc.monitored_codes)}개")
                # 해당 종목 실시간 수신 해제
                self.kc.set_real_remove("1000", strCode)
                # 파이프라인에서도 해당 종목 데이터 제거
                if self.kc.data_pipeline:
                    self.kc.data_pipeline.remove_code(strCode)
            else:
                self.logger.debug(f"[실시간 조건 이탈] {strCode} - 감시 목록에 없음, 무시")

    def on_receive_real_data(self, sCode, sRealType, sRealData):
        """주식 체결(실시간) 틱 데이터 수신 처리"""
        if sRealType == "주식체결":
            dt_time = self.kc.dynamicCall("GetCommRealData(QString, int)", sCode, 20).strip()
            price_str = self.kc.dynamicCall("GetCommRealData(QString, int)", sCode, 10).strip()
            volume_str = self.kc.dynamicCall("GetCommRealData(QString, int)", sCode, 15).strip()
            
            if not price_str or not volume_str: return
                
            try:
                # 하락일 경우 음수가 올 수 있어 절댓값 처리
                price = abs(int(price_str))
                volume = abs(int(volume_str))
                if self.kc.data_pipeline:
                    self.kc.data_pipeline.add_tick_data(sCode, dt_time, price, volume)
            except ValueError:
                pass

    def on_receive_chejan_data(self, sGubun, nItemCnt, sFidList):
        """실제 매수/매도 주문 이후 체결 잔고 통보 수신 처리"""
        if sGubun == "0": # 0: 주문 체결
            order_no = self.kc.dynamicCall("GetChejanData(int)", 9203).strip()
            code = self.kc.dynamicCall("GetChejanData(int)", 9001).strip("A") # 'A005930' 등 형태 파싱
            order_status = self.kc.dynamicCall("GetChejanData(int)", 913).strip()
            
            exec_price_str = self.kc.dynamicCall("GetChejanData(int)", 910).strip()
            exec_qty_str = self.kc.dynamicCall("GetChejanData(int)", 911).strip()
            order_type_str = self.kc.dynamicCall("GetChejanData(int)", 905).strip() # '+매수', '-매도'
            
            if exec_price_str and exec_qty_str:
                try:
                    price = int(exec_price_str)
                    qty = int(exec_qty_str)
                    if price > 0 and qty > 0 and self.kc.execution_manager:
                        # 매니저에게 실제 DB 기록 및 포지션 업데이트 지시
                        self.kc.execution_manager.record_execution(order_no, code, price, qty, order_status, order_type_str)
                except ValueError:
                    pass

    def on_receive_tr_data(self, sScrNo, sRQName, sTrCode, sRecordName, sPrevNext, nDataLength, sErrorCode, sMessage, sSplmMsg):
        """특정 TR 단위 데이터(계좌, 과거 분봉 차트 등) 수신 및 파싱 처리"""
        
        # 1. 예수금 조회 TR 응답 처리
        if sRQName == "opw00001_req":
            cash_str = self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, 0, "주문가능금액").strip()
            if cash_str:
                self.kc.available_cash = int(cash_str)
                self.logger.info(f"💰 [예수금 동기화] 주문 가능 현금: {self.kc.available_cash:,} 원")
            return
            
        # 2. 계좌 잔고 조회 TR 응답 처리
        elif sRQName == "opw00018_req":
            data_cnt = self.kc.dynamicCall("GetRepeatCnt(QString, QString)", sTrCode, sRQName)
            server_pos = {}
            for i in range(data_cnt):
                code_str = self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "종목번호").strip()
                if code_str.startswith("A"): code_str = code_str[1:]
                qty_str = self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "보유수량").strip()
                price_str = self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "매입가").strip()
                
                if qty_str and price_str:
                    qty = int(qty_str)
                    price = int(price_str)
                    if qty > 0:
                        server_pos[code_str] = {'buy_price': price, 'qty': qty}
            
            # 서버에서 받은 실제 실시간 잔고를 매니저에 덮어씌움
            if self.kc.execution_manager:
                self.kc.execution_manager.sync_server_positions(server_pos)
                
            # 계좌 연동이 모두 끝났으므로 조건식 로드 킥오프
            self.kc.get_condition_load()
            return
            
        # 3. 주식 분봉 차트 조회 TR 응답 처리
        elif sRQName.startswith("opt10080_req_"):
            code = sRQName.replace("opt10080_req_", "")
            data_cnt = self.kc.dynamicCall("GetRepeatCnt(QString, QString)", sTrCode, sRQName)
            if data_cnt == 0:
                self.logger.warning(f"[{code}] 분봉 데이터 응답이 없습니다.")
                return
                
            self.logger.info(f"[{code}] 분봉 과거 데이터 수신: {data_cnt} rows 파싱 시작...")
            records = []
            
            for i in range(data_cnt):
                dt_str = self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "체결시간").strip()
                open_p = abs(int(self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "시가").strip() or 0))
                high_p = abs(int(self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "고가").strip() or 0))
                low_p = abs(int(self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "저가").strip() or 0))
                close_p = abs(int(self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "현재가").strip() or 0))
                vol = abs(int(self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "거래량").strip() or 0))
                
                if len(dt_str) == 14: # YYYYMMDDHHMMSS 포맷 변환
                    dt = datetime(
                        int(dt_str[:4]), int(dt_str[4:6]), int(dt_str[6:8]),
                        int(dt_str[8:10]), int(dt_str[10:12]), int(dt_str[12:14])
                    )
                    records.append({
                        'date_idx': dt,
                        'open': open_p, 'high': high_p, 'low': low_p, 'close': close_p, 'volume': vol
                    })

            # DataFrame으로 묶어서 데이터 파이프라인으로 전송 (가장 빠른 시간이 위로 가도록 정렬)
            if records and self.kc.data_pipeline:
                df = pd.DataFrame(records).set_index('date_idx').sort_index(ascending=True)
                self.kc.data_pipeline.add_historical_data(code, df)
