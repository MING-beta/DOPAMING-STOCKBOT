import os
import time
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
        
        # [Hysteresis] 이탈 유예 종목 관리: { 종목코드: 이탈시각 }
        self.pending_removals = {}
        
        # 유예 기간이 지난 종목들을 자동 정리하는 타이머 (10초 간격)
        from PyQt5.QtCore import QTimer
        self.cleanup_timer = QTimer()
        self.cleanup_timer.timeout.connect(self._cleanup_pending_removals)
        self.cleanup_timer.start(10000) 

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
        
        # 블랙리스트(상한가 등) 사전 필터링
        if hasattr(self.kc, 'blacklisted_codes'):
            code_list = [c for c in code_list if c not in self.kc.blacklisted_codes]
            
        self.logger.info(f"검색된 종목 수: {len(code_list)}건 -> {code_list}")
        
        # 감시 종목 상한제 적용 및 진입 시간 기록
        max_monitored = int(os.getenv("MAX_MONITORED_STOCKS", "20"))
        if len(code_list) > max_monitored:
            self.logger.warning(f"⚠️ 검색 결과({len(code_list)}건)가 상한선({max_monitored}개)을 초과하여 상위 {max_monitored}개만 감시합니다.")
            code_list = code_list[:max_monitored]
            
        # 감시 종목 데이터 초기화 { 코드: 진입시간 }
        now = time.time()
        self.kc.monitored_codes = {code: now for code in code_list}
        
        # 검색된 종목들에 대해 실시간 틱 데이터 수신 등록 및 과거 데이터 프리페치
        if code_list:
            self.kc.set_real_reg("1000", code_list, "10;15;20", "0") # FID: 10(현재가), 15(거래량), 20(체결시간)
            for code in code_list:
                self.kc.request_opt10080(code)

    def on_receive_real_condition(self, strCode, strType, strConditionName, strConditionIndex):
        """
        실시간 조건검색 편입(I)/이탈(D) 이벤트 처리
        - strType == 'I': 조건 편입 → 감시 목록 추가 및 실시간 등록
        - strType == 'D': 조건 이탈 → 유예 리스트 등록 (잠시 후 삭제)
        """
        if strType == 'I':  # 편입
            # 상한가 등으로 블랙리스트된 종목 원천 차단
            if hasattr(self.kc, 'blacklisted_codes') and strCode in self.kc.blacklisted_codes:
                return

            # [Hysteresis] 유예 목록에 있다면 즉시 복구 (삭제 예약 취소)
            if strCode in self.pending_removals:
                del self.pending_removals[strCode]
                self.logger.info(f"🔄 [유예 복구] {strCode} - 30초 이내 재편입되어 기존 데이터를 유지하고 분석을 계속합니다.")
                return

            if strCode in self.kc.monitored_codes:
                self.logger.debug(f"[실시간 조건 편입] {strCode} - 이미 감시 중, 무시")
                return

            max_monitored = int(os.getenv("MAX_MONITORED_STOCKS", "20"))
            
            # 1. 감시 한도 도달 시 다이내믹 교체 시도
            if len(self.kc.monitored_codes) >= max_monitored:
                # ExecutionManager가 아직 초기화되지 않았다면 교체 보류
                if not self.kc.execution_manager:
                    return

                # 보유 포지션이 없는 종목들 중 가장 오래된 종목 찾기 (교체 후보)
                with self.kc.execution_manager.lock:
                    active_positions = list(self.kc.execution_manager.positions.keys())
                
                # 감시 중인 종목 중 보유 중이지 않은 후보군 추출
                candidates = {code: t for code, t in self.kc.monitored_codes.items() if code not in active_positions}
                
                if candidates:
                    # 가장 오래된(타임스탬프가 가장 작은) 종목 선정
                    oldest_code = min(candidates, key=candidates.get)
                    self.logger.warning(f"🔄 [종목 교체] 한도 도달로 인해 유휴 종목 {oldest_code}를 신규 종목 {strCode}로 교체합니다.")
                    
                    # 기존 종목 제거
                    del self.kc.monitored_codes[oldest_code]
                    self.kc.set_real_remove("1000", oldest_code)
                    if self.kc.data_pipeline:
                        self.kc.data_pipeline.remove_code(oldest_code)
                else:
                    self.logger.error(f"🚫 [교체 실패] 현재 상한선({max_monitored})을 모두 보유 종목이 차지하고 있어 {strCode}를 편입할 수 없습니다.")
                    return

            # 2. 신규 종목 등록
            self.kc.monitored_codes[strCode] = time.time()
            self.logger.info(f"[실시간 조건 편입] {strCode} ← {strConditionName} | 감시 종목: {len(self.kc.monitored_codes)}/{max_monitored}")
            self.kc.set_real_reg("1000", [strCode], "10;15;20", "1")
            self.kc.request_opt10080(strCode)
            
        elif strType == 'D':  # 이탈
            if strCode in self.kc.monitored_codes:
                # [Hysteresis] 즉시 삭제하지 않고 유예 리스트에 등록
                self.pending_removals[strCode] = time.time()
                self.logger.info(f"⏳ [이탈 유예] {strCode} - 조건식에서 이탈했으나 30초간 데이터를 유지하며 추격합니다.")
            else:
                self.logger.debug(f"[실시간 조건 이탈] {strCode} - 감시 목록에 없음, 무시")

    def _cleanup_pending_removals(self):
        """30초 이상 조건식으로 돌아오지 않은 종목들을 시스템에서 완전히 제거합니다."""
        now = time.time()
        to_delete = [code for code, exit_time in self.pending_removals.items() if now - exit_time >= 30]
        
        for code in to_delete:
            del self.pending_removals[code]
            if code in self.kc.monitored_codes:
                del self.kc.monitored_codes[code]
                self.logger.warning(f"🗑️ [최종 제거] {code} - 유예 시간(30초) 초과로 감시를 종료합니다.")
                
                # 실시간 수신 해제 및 파이프라인 데이터 삭제
                self.kc.set_real_remove("1000", code)
                if self.kc.data_pipeline:
                    self.kc.data_pipeline.remove_code(code)

    def on_receive_real_data(self, sCode, sRealType, sRealData):
        """주식 체결(실시간) 틱 데이터 수신 처리"""
        if sRealType == "주식체결":
            dt_time = self.kc.dynamicCall("GetCommRealData(QString, int)", sCode, 20).strip()
            price_str = self.kc.dynamicCall("GetCommRealData(QString, int)", sCode, 10).strip()
            volume_str = self.kc.dynamicCall("GetCommRealData(QString, int)", sCode, 15).strip()
            fluctuation_str = self.kc.dynamicCall("GetCommRealData(QString, int)", sCode, 12).strip()
            
            if not price_str or not volume_str: return
                
            try:
                # 상한가(등락율 약 +29.8% 이상) 도달 종목 감시 영구 제외
                if fluctuation_str:
                    fluctuation_rate = float(fluctuation_str.replace('+', ''))
                    if fluctuation_rate >= 29.8:
                        if not hasattr(self.kc, 'blacklisted_codes'):
                            self.kc.blacklisted_codes = set()
                            
                        if sCode not in self.kc.blacklisted_codes:
                            self.logger.warning(f"🚫 [상한가 도달] {sCode} 종목이 상한가({fluctuation_rate}%)에 도달하여 감시를 영구 종료 및 제외합니다.")
                            self.kc.blacklisted_codes.add(sCode)
                            
                            if sCode in self.kc.monitored_codes:
                                del self.kc.monitored_codes[sCode]
                                self.kc.set_real_remove("1000", sCode)
                                if self.kc.data_pipeline:
                                    self.kc.data_pipeline.remove_code(sCode)
                        return # 상한가 종목은 틱 데이터 파이프라인 누적 컨텍스트에서 드롭

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
                self.kc.reserved_cash = 0 # [동기화] 서버에서 실제 잔고를 받았으므로 로컬 예약금 초기화
                self.logger.info(f"💰 [예수금 동기화] 주문 가능 현금: {self.kc.available_cash:,} 원")
                
                # 리스크 가드용 초기 자산이 아직 0이라면, 현재 현금을 기준으로 즉시 초기화 (계산 중... 방지)
                if self.kc.initial_total_assets == 0 and self.kc.available_cash > 0:
                    self.kc.initial_total_assets = self.kc.available_cash
                    self.logger.info(f"💎 [자산 초기화] 현금 기반 리스크 기준점 설정: {self.kc.initial_total_assets:,} 원")
            return
            
        # 2. 계좌 잔고 조회 TR 응답 처리
        elif sRQName == "opw00018_req":
            # [추가] 총 자산 파싱 (단일 데이터)
            # 모의투자/실전투자에 따라 필드명이 다를 수 있으므로 순차적 시도
            target_fields = ["추정평가자산", "자산현황", "총평가금액"]
            total_assets = 0
            
            for field in target_fields:
                val = self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, 0, field).strip()
                if val and int(val) > 0:
                    total_assets = int(val)
                    self.logger.debug(f"계좌 자산 파싱 성공: {field}={total_assets}")
                    break
                
            if total_assets > 0:
                # 더 정확한 자산 정보(주식 평가액 포함)가 오면 업데이트
                self.kc.initial_total_assets = total_assets
                self.kc.reserved_cash = 0 # [동기화] 자산 갱신 시 예약금 초기화
                self.logger.info(f"💎 [자산 동기화 완료] 실시간 총 자산: {total_assets:,} 원 (리스크 기준점)")

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
            
            # GetCommDataEx를 사용하여 한 번에 2차원 배열로 전체 데이터를 받아옴으로써
            # COM 객체 통신 부하 및 Qt Stack Buffer Overrun (0xc0000409) 에러 방지
            data_arr = self.kc.dynamicCall("GetCommDataEx(QString, QString)", sTrCode, "주식분봉차트조회")
            
            if not data_arr:
                self.logger.warning(f"[{code}] 분봉 데이터 응답이 없습니다 (GetCommDataEx 반환값 없음).")
                return
                
            data_cnt = len(data_arr)
            self.logger.info(f"[{code}] 분봉 과거 데이터 수신: {data_cnt} rows 파싱 시작 (GetCommDataEx)")
            records = []
            
            for row in data_arr:
                # opt10080 주식분봉차트조회 GetCommDataEx 반환 인덱스:
                # 0:현재가, 1:거래량, 2:체결시간, 3:시가, 4:고가, 5:저가
                try:
                    close_p = abs(int(row[0].strip() or 0))
                    vol = abs(int(row[1].strip() or 0))
                    dt_str = row[2].strip()
                    open_p = abs(int(row[3].strip() or 0))
                    high_p = abs(int(row[4].strip() or 0))
                    low_p = abs(int(row[5].strip() or 0))
                    
                    if len(dt_str) == 14: # YYYYMMDDHHMMSS
                        dt = datetime(
                            int(dt_str[:4]), int(dt_str[4:6]), int(dt_str[6:8]),
                            int(dt_str[8:10]), int(dt_str[10:12]), int(dt_str[12:14])
                        )
                        records.append({
                            'date_idx': dt,
                            'open': open_p, 'high': high_p, 'low': low_p, 'close': close_p, 'volume': vol
                        })
                except (ValueError, IndexError):
                    continue

            # DataFrame으로 묶어서 데이터 파이프라인으로 전송 (가장 빠른 시간이 위로 가도록 정렬)
            if records and self.kc.data_pipeline:
                df = pd.DataFrame(records).set_index('date_idx').sort_index(ascending=True)
                self.kc.data_pipeline.add_historical_data(code, df)
