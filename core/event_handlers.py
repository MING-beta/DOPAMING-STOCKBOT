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
            
            # 1. `.env`에서 타겟 조건식 이름들(콤마 구분) 불러오기
            target_env = os.getenv("TARGET_CONDITION_NAME", "").strip()
            target_names = [x.strip() for x in target_env.split(',')] if target_env else []
            
            # 검색식 목록 중 조건에 맞는 것을 모두 실행 (최대 10개까지 지원)
            if cond_str:
                cond_list = cond_str.rstrip(';').split(';')
                launched_count = 0
                
                for cond in cond_list:
                    if '^' in cond:
                        idx, name = cond.split('^')
                        
                        # 타겟 리스트가 비어있으면(미설정 시) 맨 첫 번째 것만 실행 (Fallback)
                        if not target_names and launched_count == 0:
                            self.logger.info(f"🎯 [기본모드] 타겟 조건검색식 발견: 인덱스 {int(idx):03d} / {name}")
                            self.kc.send_condition("0156", name, int(idx), 1)
                            launched_count += 1
                            break
                            
                        # 타겟 리스트 중 하나와 완전히 일치하거나 포함되면 실행
                        if target_names:
                            for target in target_names:
                                if target in name or target == idx:
                                    self.logger.info(f"🎯 [.env 매칭] 타겟 조건검색식 발견: 인덱스 {int(idx):03d} / {name}")
                                    self.kc.send_condition("0156", name, int(idx), 1)
                                    launched_count += 1
                                    time.sleep(0.3) # TR 전송 제한 보호
                                    break
                
                if launched_count == 0 and cond_list:
                    # 설정한 이름과 일치하는게 아무것도 없을 때의 최후의 수단
                    fallback_idx, fallback_name = int(cond_list[0].split('^')[0]), cond_list[0].split('^')[1]
                    self.logger.warning(f"⚠️ 일치하는 조건식이 없어 1번 조건식({fallback_name})을 강제 구동합니다.")
                    self.kc.send_condition("0156", fallback_name, fallback_idx, 1)
        else:
            self.logger.error(f"조건검색식 로드 실패 ({sMsg})")

    def on_receive_tr_condition(self, sScrNo, strCodeList, strConditionName, nIndex, nNext):
        """조건검색 등록 후 현재 조건에 맞는 종목 초기 목록 수신 처리"""
        self.logger.info(f"[{strConditionName}] 검색 결과 수신")
        code_list = strCodeList.rstrip(';').split(';')
        if code_list == ['']: code_list = []
        
        # 블랙리스트(상한가 등) 사전 필터링
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
                self.kc.get_master_code_name(code) # [성능 최적화] 종목명 선제적 캐싱
                self.kc.request_opt10080(code)

    def on_receive_real_condition(self, strCode, strType, strConditionName, strConditionIndex):
        """
        실시간 조건검색 편입(I)/이탈(D) 이벤트 처리
        """
        if strType == 'I':  # 편입
            # 상한가 등으로 블랙리스트된 종목 원천 차단
            if strCode in self.kc.blacklisted_codes:
                self.logger.debug(f"🚫 [차단] {strCode} - 블랙리스트(상한가 등) 종목으로 편입을 거절합니다.")
                return

            # [Hysteresis] 유예 목록에 있다면 즉시 복구 (삭제 예약 취소)
            if strCode in self.pending_removals:
                del self.pending_removals[strCode]
                grace_period = int(os.getenv("MONITORING_GRACE_PERIOD", "300"))
                self.logger.info(f"🔄 [유예 복구] {strCode} - {grace_period}초 이내 재편입되어 기존 데이터를 유지하고 분석을 계속합니다.")
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
            self.kc.get_master_code_name(strCode) # [성능 최적화] 종목명 선제적 캐싱
            self.logger.info(f"[실시간 조건 편입] {strCode} ← {strConditionName} | 감시 종목: {len(self.kc.monitored_codes)}/{max_monitored}")
            self.kc.set_real_reg("1000", [strCode], "10;15;20", "1")
            self.kc.request_opt10080(strCode)
            
        elif strType == 'D':  # 이탈
            if strCode in self.kc.monitored_codes:
                # [Hysteresis] 즉시 삭제하지 않고 유예 리스트에 등록
                self.pending_removals[strCode] = time.time()
                grace_period = int(os.getenv("MONITORING_GRACE_PERIOD", "300"))
                self.logger.info(f"⏳ [이탈 유예] {strCode} - 조건식에서 이탈했으나 {grace_period}초간 데이터를 유지하며 추격합니다.")
            else:
                self.logger.debug(f"[실시간 조건 이탈] {strCode} - 감시 목록에 없음, 무시")

    def _cleanup_pending_removals(self):
        """특정 시간 이상 조건식으로 돌아오지 않은 종목들을 시스템에서 완전히 제거합니다."""
        now = time.time()
        grace_period = int(os.getenv("MONITORING_GRACE_PERIOD", "300"))
        to_delete = [code for code, exit_time in self.pending_removals.items() if now - exit_time >= grace_period]
        
        for code in to_delete:
            del self.pending_removals[code]
            if code in self.kc.monitored_codes:
                del self.kc.monitored_codes[code]
                self.logger.warning(f"🗑️ [최종 제거] {code} - 유예 시간({grace_period}초) 초과로 감시를 종료합니다.")
                
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
                        # [BUG FIX] 보유 중인 종목이면 상한가라도 실시간 수신을 해제하면 안됨! (익/손절 추적을 위해 필수)
                        is_holding = False
                        if self.kc.execution_manager and sCode in self.kc.execution_manager.positions:
                            is_holding = True
                            
                        # 아직 매수 안한 남의 종목이 상한가 쳤을 때만 관심 끄기
                        if not is_holding:
                            if sCode not in self.kc.blacklisted_codes:
                                self.logger.warning(f"🚫 [상한가 도달] {sCode} 종목이 상한가({fluctuation_rate}%)에 도달하여 신규 진입 감시를 영구 제외합니다.")
                                self.kc.blacklisted_codes.add(sCode)
                                
                                if sCode in self.kc.monitored_codes:
                                    del self.kc.monitored_codes[sCode]
                                    self.kc.set_real_remove("1000", sCode)
                                    if self.kc.data_pipeline:
                                        self.kc.data_pipeline.remove_code(sCode)
                            return # 상한가 종목은 틱 데이터 무시
                        else:
                            # 보유 종목이면 틱 통과시킴
                            pass

                # 하락일 경우 음수가 올 수 있어 절댓값 처리
                price = abs(int(price_str))
                volume = abs(int(volume_str))
                if self.kc.data_pipeline:
                    self.kc.data_pipeline.add_tick_data(sCode, dt_time, price, volume)
            except ValueError:
                pass

    def on_receive_chejan_data(self, sGubun, nItemCnt, sFidList):
        """실제 매수/매도 주문 이후 체결 통보(0) 및 잔고 변경(1) 수신 처리"""
        try:
            # 상태 변수 모두 추출
            order_no = self.kc.dynamicCall("GetChejanData(int)", 9203).strip()
            code = self.kc.dynamicCall("GetChejanData(int)", 9001).replace("A", "").strip()
            order_status = self.kc.dynamicCall("GetChejanData(int)", 913).strip()
            order_type_str = self.kc.dynamicCall("GetChejanData(int)", 905).strip() # '+매수', '-매도'
            
            # 파싱 정규화 (콤마, 마이너스 기호 제거)
            exec_price_str = self.kc.dynamicCall("GetChejanData(int)", 910).replace(',', '').replace('+', '').replace('-', '').strip()
            exec_qty_str = self.kc.dynamicCall("GetChejanData(int)", 911).replace(',', '').replace('+', '').replace('-', '').strip()
            
            # [추가] 주문 전체 수량 및 누적 체결 수량 확보 (중복 알림 제어용)
            order_qty_str = self.kc.dynamicCall("GetChejanData(int)", 900).replace(',', '').strip()
            cumulative_qty_str = self.kc.dynamicCall("GetChejanData(int)", 912).replace(',', '').strip()

            if sGubun == "0":  # 0: 주문/체결
                # 체결 이벤트 로깅 (접수, 확인, 체결 등 모든 상태)
                self.logger.info(f"💌 [Chejan 체결 통보] 상태: {order_status} | 종목: {code} | {order_type_str} | 가격: {exec_price_str} | 수량: {exec_qty_str} (누적: {cumulative_qty_str}/{order_qty_str})")
                
                # 주문 상태가 '체결'에 도달했고 체결량이 발생했을 때만 기록
                if order_status == "체결" and exec_price_str and exec_qty_str:
                    try:
                        price = int(''.join(filter(str.isdigit, exec_price_str)) or 0)
                        qty = int(''.join(filter(str.isdigit, exec_qty_str)) or 0)
                        
                        o_qty_clean = ''.join(filter(str.isdigit, order_qty_str))
                        order_qty = int(o_qty_clean) if o_qty_clean else qty
                        
                        c_qty_clean = ''.join(filter(str.isdigit, cumulative_qty_str))
                        cum_qty = int(c_qty_clean) if c_qty_clean else qty
                        
                        if price > 0 and qty > 0 and self.kc.execution_manager:
                            self.kc.execution_manager.record_execution(
                                order_no, code, price, qty, order_status, order_type_str,
                                cum_qty, order_qty
                            )
                    except ValueError as e:
                        self.logger.error(f"❌ [Chejan 파싱 에러] 체결 데이터 변환 실패: {e}")
                        
            elif sGubun == "1": # 1: 잔고 변경
                # 잔고 변경의 경우 보유수량(930), 매입단가(931) 등의 정보를 받아 즉시 자산을 동기화
                pos_qty_str = self.kc.dynamicCall("GetChejanData(int)", 930).replace(',', '').strip()
                pos_price_str = self.kc.dynamicCall("GetChejanData(int)", 931).replace(',', '').strip()
                
                self.logger.info(f"💼 [Chejan 잔고 변경] 종목: {code} | 현재보유수량: {pos_qty_str} | 매입단가: {pos_price_str}")
                
                # API 공식 밸런스 데이터를 ExecutionManager에 즉시 동기화 (간결하고 정확함)
                if self.kc.execution_manager:
                    try:
                        qty = int(pos_qty_str) if pos_qty_str else 0
                        avg_price = float(pos_price_str) if pos_price_str else 0.0
                        self.kc.execution_manager.sync_single_position(code, qty, avg_price)
                    except ValueError:
                        self.logger.error(f"❌ [Chejan 동기화 오류] 수량/가각 변환 실패 ({code})")
                
        except Exception as e:
            self.logger.error(f"❌ [Chejan 수신 에러] 구조적 결함 발생: {e}")

    def on_receive_tr_data(self, sScrNo, sRQName, sTrCode, sRecordName, sPrevNext, nDataLength, sErrorCode, sMessage, sSplmMsg):
        """특정 TR 단위 데이터(계좌, 과거 분봉 차트 등) 수신 및 파싱 처리"""
        self.logger.critical(f"📥 TR 수신: {sRQName}")
        
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
            # [최종 해결] 불확실한 API 요약 필드 대신, 현금과 각 종목 평가액을 직접 합산함
            total_stock_eval = 0
            
            # (A) 싱글 데이터에서 MTS 요약 정보 파싱
            def _get_single(field_name):
                v = self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, 0, field_name).strip()
                try: return int(v) if v else 0
                except: return 0
            
            summary_eval_int = _get_single("평가금액")
            self.kc.mts_estimated_assets = _get_single("추정예탁자산")
            self.kc.mts_total_purchase = _get_single("총매입금액")
            self.kc.mts_total_eval = _get_single("총평가금액")

            data_cnt = self.kc.dynamicCall("GetRepeatCnt(QString, QString)", sTrCode, sRQName)
            server_pos = {}
            
            # [신규] 상장폐지/정리매매 허수 자산 차감용 변수
            bad_stock_purchase = 0
            bad_stock_eval = 0
            for i in range(data_cnt):
                code_str = self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "종목번호").strip()
                if code_str.startswith("A"): code_str = code_str[1:]
                code_str = code_str.strip()
                qty_str = self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "보유수량").strip()
                price_str = self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "매입가").strip()
                cur_price_str = self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "현재가").strip()
                profit_rate_str = self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "수익률(%)").strip()
                pnl_str = self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "평가손익").strip()
                
                # [핵심] 개별 종목의 평가금액 파싱 및 합산
                eval_amount_str = self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, i, "평가금액").strip()
                eval_amount = int(eval_amount_str) if eval_amount_str and eval_amount_str.isdigit() else 0
                total_stock_eval += eval_amount
                
                if qty_str and price_str:
                    qty = int(qty_str)
                    price = int(price_str)
                    cur_price = abs(int(cur_price_str)) if cur_price_str else price
                    
                    market_state = self.kc.dynamicCall("GetMasterStockState(QString)", code_str)
                    if market_state and ("상장폐지" in market_state or "정리매매" in market_state):
                        bad_stock_purchase += (price * qty)
                        bad_stock_eval += eval_amount
                    
                    try:
                        # [v11.5 ROOT CAUSE FIX] 키움 opw00018 수익률(%)은 실제 퍼센트의 100배 값으로 반환됨
                        # 예: -0.51% → API가 "-51" 반환 → /100 하여 -0.51로 변환
                        api_profit_rate = (float(profit_rate_str) / 100.0) if profit_rate_str else 0.0
                        api_pnl = int(pnl_str) if pnl_str else 0
                    except ValueError:
                        api_profit_rate = 0.0
                        api_pnl = 0

                    if qty > 0:
                        server_pos[code_str] = {
                            'buy_price': price, 'qty': qty, 'current_price': cur_price,
                            'api_profit_rate': api_profit_rate, 'api_pnl': api_pnl
                        }
            
            # (B-1) 허수 데이터 차감 보정
            if bad_stock_purchase > 0 or bad_stock_eval > 0:
                self.kc.mts_total_purchase -= bad_stock_purchase
                self.kc.mts_total_eval -= bad_stock_eval
                summary_eval_int -= bad_stock_eval
                self.logger.info(f"🗑️ [상장폐지 필터 적용] 쓰레기 매입금(-{bad_stock_purchase:,}원), 쓰레기 평가금(-{bad_stock_eval:,}원) 삭감 완료")

            # (B-2) 최종 자산 재구성: MTS "추정예탁자산"이 최우선
            if self.kc.mts_estimated_assets and self.kc.mts_estimated_assets > 0:
                total_assets = self.kc.mts_estimated_assets
            else:
                final_stock_val = max(summary_eval_int, total_stock_eval)
                total_assets = self.kc.available_cash + final_stock_val
            
            if total_assets > 10000: # 최소 1만원 이상일 때만 유효 조치
                self.kc.initial_total_assets = total_assets
                self.kc.reserved_cash = 0
                self.logger.info(f"💎 [자산 재구성 완료] 추정자산: {total_assets:,} 원 (매입:{self.kc.mts_total_purchase:,} | 평가:{self.kc.mts_total_eval:,})")
            
            # 서버에서 받은 실제 실시간 잔고를 매니저에 덮어씌움
            if self.kc.execution_manager:
                self.kc.execution_manager.sync_server_positions(server_pos)
                
            # 🔔 [실시간 보강] 서버에서 확인된 전 종목을 실시간 수신 대상으로 다시 등록
            if server_pos:
                codes = list(server_pos.keys())
                self.kc.set_real_reg("1000", codes, "10;15;20", "1")
                self.logger.info(f"💼 [보유 종목 실시간 연동] {len(codes)}개 종목 실시간 수신 등록 완료")
                
            # [도구 연동용] 서버 포지션 데이터를 코어에 노출
            self.kc.server_positions = server_pos
            
            # 계좌 연동이 모두 끝났으므로 조건식 로드 킥오프
            self.kc.get_condition_load()
            return

        # 2-1. 당일 실현손익 조회 TR 응답 처리 (서버 공식 데이터)
        elif sRQName == "opt10074_req":
            pnl_str = self.kc.dynamicCall("GetCommData(QString, QString, int, QString)", sTrCode, sRQName, 0, "실현손익").strip()
            if pnl_str:
                try:
                    official_pnl = int(pnl_str)
                    self.kc.official_daily_pnl = official_pnl # 공식 손익 저장
                    
                    if self.kc.execution_manager:
                        # [핵심] 키움 모의투자는 사고팔때 도합 0.7%~0.9%에 달하는 터무니없는 가짜 수수료를 차감합니다.
                        # 따라서 실전(0.25%) 기준으로 로컬에서 정밀 산출된 daily_pnl을 모의투자 서버 데이터가 덮어쓰지 못하도록 방어합니다.
                        is_mock = getattr(self.kc, 'is_mock', False)
                        if not is_mock:
                            self.kc.execution_manager.daily_pnl = official_pnl # 실전일 때만 동기화
                            self.logger.info(f"📊 [공식 수익 동기화] 오늘 실현 손익: {official_pnl:,} 원")
                        else:
                            self.logger.info(f"📊 [모의투자 PnL 무시] 서버상 손익: {official_pnl:,}원 (대시보드는 실전 0.25% 기준 자체 계산값 유지)")

                except ValueError:
                    self.logger.error("❌ [공식 수익 동기화 오류] 숫자 변환 실패")
            return
            
        # 3. 주식 분봉 차트 조회 TR 응답 처리
        elif sRQName.startswith("opt10080_req_"):
            code = sRQName.replace("opt10080_req_", "")
            
            self.logger.info(f"[{code}] 분봉 데이터 수신 중 (RQ: {sRQName}, Next: {sPrevNext})")
            
            # GetCommDataEx를 사용하여 한 번에 2차원 배열로 전체 데이터를 받아옴으로써
            # COM 객체 통신 부하 및 Qt Stack Buffer Overrun (0xc0000409) 에러 방지
            data_arr = self.kc.dynamicCall("GetCommDataEx(QString, QString)", sTrCode, "주식분봉차트조회")
            
            # [선제 방어] 차트 데이터 로드 시점에 이미 상한가면 즉시 제외
            if data_arr:
                try:
                    cur_price = abs(int(data_arr[0][0].strip() or 0))
                    ref_price = self.kc.data_pipeline.reference_prices.get(code, 0)
                    if ref_price > 0:
                        change_rate = (cur_price - ref_price) / ref_price * 100
                        if change_rate >= 29.8:
                            self.logger.warning(f"🚫 [상한가 선제 차단] {code} 종목이 이미 상한가({change_rate:.2f}%) 상태이므로 감시 대상에서 즉각 제외합니다.")
                            self.kc.blacklisted_codes.add(code)
                            if code in self.kc.monitored_codes:
                                del self.kc.monitored_codes[code]
                                self.kc.set_real_remove("1000", code)
                            return
                except (ValueError, IndexError):
                    pass

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
                self.kc.data_pipeline.add_historical_data(code, df, sPrevNext)
