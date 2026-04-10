"""
ExecutionManager 모듈
----------------------
실제 주식 매수/매도 주문 발송, 현재 보유 중인 포지션 메모리 동기화,
그리고 손익절 비율(예: -2%, +4%)을 모니터링하여 자동 청산을 수행하는 전략 실행의 핵심체입니다.
미체결 주문 타임아웃(기본 60초) 초과 시 자동 취소 처리를 포함합니다.
"""
import logging
import time
import os
import threading


class ExecutionManager:
    """주문 실행 및 보유 잔고 모니터링 관리 (손절/익절)을 전담하는 시스템 객체"""
    
    # 미체결 주문 타임아웃 기준 (초) - 이 시간 내에 체결 안 되면 자동 취소
    ORDER_TIMEOUT_SEC = 60
    
    def __init__(self, kiwoom_core, db_manager, slack_notifier, is_dry_run=False):
        self.logger = logging.getLogger("DopamingBot.ExecutionManager")
        self.kiwoom = kiwoom_core
        self.db = db_manager
        self.slack = slack_notifier
        self.is_dry_run = is_dry_run
        
        self.positions = {}
        
        # 리스크 및 자금 관리 설정 (환경변수 로드)
        self.LOSS_LIMIT_RATE = float(os.getenv("RISK_LOSS_LIMIT_RATE", "0.20"))
        self.INVEST_RATE_PER_STOCK = float(os.getenv("TRADE_INVEST_RATE", "0.05"))
        
        # 매매 목표 설정 (환경변수 로드)
        self.TARGET_PROFIT = float(os.getenv("TRADE_TARGET_PROFIT", "0.04"))
        self.STOP_LOSS = float(os.getenv("TRADE_STOP_LOSS", "-0.02"))
        self.TRAILING_STOP_ACTIVATION = float(os.getenv("TRAILING_STOP_ACTIVATION", "0.02"))
        self.TRAILING_STOP_CALLBACK = float(os.getenv("TRAILING_STOP_CALLBACK", "0.01"))
        
        # [공격적 투자 모드] 스캘핑 최적화를 위한 타겟 오버라이드
        is_aggressive = os.getenv("AGGRESSIVE_MODE", "False").lower() == 'true'
        if is_aggressive:
            self.logger.warning("🔥 [공격적 투자] 모드 활성화! 매매 빈도를 극대화하도록 스캘핑 타겟(익결/손절 1.5%, 트레일링 1.0% 활성)을 적용합니다.")
            self.INVEST_RATE_PER_STOCK = 0.10     # 1개 종목 비중 10% (기본 5%보다 2배 증가, 빠른 회전 목적)
            self.TARGET_PROFIT = 0.015          # 1.5% 익절
            self.STOP_LOSS = -0.015             # 1.5% 손절
            self.TRAILING_STOP_ACTIVATION = 0.010 # 1.0% 수익부터 이익 보존 스탑 켜짐
            self.TRAILING_STOP_CALLBACK = 0.005   # 0.5% 하락시 즉각 청산
        
        # 운영 설정 (환경변수 로드)
        self.ORDER_TIMEOUT_SEC = int(os.getenv("ORDER_PENDING_TIMEOUT", "60"))
        
        # 리스크 가드 관련 상태
        self.daily_pnl = 0
        self.is_risk_halt = False
        
        # 스레드 안전성 확보를 위한 락
        self.lock = threading.Lock()
        
        # 미체결 주문 추적: { order_no: {code, qty, order_type, sent_at(timestamp)} }
        self.pending_orders = {}
        
        if self.is_dry_run:
            self.logger.info("🤖 ** 시뮬레이션(Dry-Run) 모드 작동 중 ** (로컬 가상 포지션 복원)")
            self.positions = self.db.load_todays_open_positions()
            for code, data in self.positions.items():
                self.logger.info(f"가상 복원 - [{code}] 평단: {data['buy_price']}, 수량: {data['qty']}")
        else:
            self.logger.info("🔥 ** 실전 운영(Production) 모드 작동 중 ** (서버 잔고 동기화 대기)")

    def sync_server_positions(self, server_positions):
        """Production 모드 시 서버가 내려준 잔고표로 덮어씌움"""
        if self.is_dry_run:
            return
            
        self.positions = server_positions
        self.logger.info(f"서버 실제 포지션 동기화 완료: {len(self.positions)}종목")
        for code, data in self.positions.items():
            self.logger.info(f"실계좌 잔고 - [{code}] 평단: {data['buy_price']}, 수량: {data['qty']}")

    def execute_buy(self, code, pipeline):
        # [리스크 가드] 신규 매수 전 당일 손실 한도 상시 체크 (초기 자금 로드된 경우만)
        if not self.is_risk_halt and self.kiwoom.initial_total_assets > 0:
            loss_limit = self.kiwoom.initial_total_assets * self.LOSS_LIMIT_RATE
            if self.daily_pnl <= -loss_limit:
                self.logger.critical(f"🛑 [리스크 가드] 당일 손실액({self.daily_pnl:,.0f}원)이 자산 대비 20% 한도({loss_limit:,.0f}원)에 도달. 신규 매수 금지.")
                self.is_risk_halt = True

        if self.is_risk_halt:
            self.logger.error(f"⚠️ [리스크 가드] 당일 손실 한도 초과로 인해 [{code}] 매수를 차단합니다.")
            return

        with self.lock:
            if code in self.positions:
                self.logger.info(f"[{code}] 이미 보유중인 종목이므로 추가 매수(물타기) 제한합니다.")
                return

        # [자금 관리] 종목별 비중 결정 (전체 자산의 5%)
        # 1. 현재가 조회 (파이프라인 참조)
        df_1m, _ = pipeline.get_data(code)
        if df_1m is None or df_1m.empty:
            self.logger.error(f"⚠️ [{code}] 현재가 데이터를 가져올 수 없어 매수를 포기합니다.")
            return
        
        current_price = int(df_1m['close'].iloc[-1])
        if current_price <= 0: return

        # 2. 목표 투자금액 산출 (환경변수 비중 적용)
        invest_rate = self.INVEST_RATE_PER_STOCK
        # 초기 자산이 로드되지 않았을 경우, 현재 예수금을 임시 기준으로 사용
        base_assets = self.kiwoom.initial_total_assets if self.kiwoom.initial_total_assets > 0 else self.kiwoom.available_cash
        
        target_budget = base_assets * invest_rate
        qty = int(target_budget / current_price)
        
        # 최소 1주 보장
        if qty == 0:
            qty = 1
            
        # 3. 예수금 하한선 체크 및 최종 수량 확정
        # 실제 매수 소요액 (세금/수수료 고려 없이 보수적으로 원금만 체크)
        required_cash = qty * current_price
        
        if required_cash > self.kiwoom.available_cash:
            # 현금 부족 시 살 수 있는 만큼만 (Maximum affordable)
            qty = int(self.kiwoom.available_cash / current_price)
            if qty <= 0:
                self.logger.error(f"❌ [예수금 부족] {code} 1주를 살 현금도 부족합니다. (필요:{current_price:,}원 / 잔액:{self.kiwoom.available_cash:,}원)")
                return
            self.logger.warning(f"⚠️ [예수금 부족] 목표 비중({self.INVEST_RATE_PER_STOCK*100:.0f}%)보다 적은 {qty}주만 매수 시도합니다. (잔액에 맞춤)")

        if self.is_dry_run:
            self.logger.warning(f"🤖 [DRY-RUN 가상 매수] {code} - 가격: {current_price}, 수량: {qty} (비중: {((qty*current_price)/base_assets)*100:.1f}%)")
            self.record_execution("VIRTUAL_B", code, current_price, qty, "체결완료", "+매수")
            return
            
        # Kiwoom 코어에 주문 위임
        self.logger.warning(f"🚀 [{code}] 진입 매수 주문 발송 (가격: {current_price:,}원, 목표수량: {qty}주, 약 {qty*current_price:,}원)")
        self.kiwoom.send_order("BuyOrder", "1001", 1, code, qty, 0, "03", "")
        
        # 미체결 추적 등록
        pending_key = f"BUY_{code}"
        self.pending_orders[pending_key] = {
            'code': code, 'qty': qty, 'order_type': '매수',
            'sent_at': time.time(), 'screen_no': '1001'
        }
        msg = f"[매수 주문 접수] {code}, 수량: {qty} (약 {qty*current_price:,}원)"
        self.slack.send_message(msg)

    def execute_sell(self, code, pipeline, sell_type="익절"):
        """보유 종목 시장가 전량 청산 (sell_type: '익절' 또는 '손절')"""
        with self.lock:
            if code not in self.positions:
                return
                
            qty = self.positions[code]['qty']
        
        if self.is_dry_run:
            df_1m, _ = pipeline.get_data(code)
            if df_1m.empty: return
            current_price = df_1m['close'].iloc[-1]
            
            self.logger.warning(f"🤖 [DRY-RUN 가상 {sell_type}] {code} - 가격: {current_price}, 수량: {qty}")
            self.record_execution("VIRTUAL_S", code, current_price, qty, "체결완료", "-매도")
            return
            
        self.logger.warning(f"💥 [{code}] {sell_type} 매도 주문 발송 (수량: {qty})")
        # 주문유형: 2(매도), 호가구분: 03(시장가) -> 시장가 매도 
        self.kiwoom.send_order(f"{sell_type}Order", "1002", 2, code, qty, 0, "03", "")
        
        # 미체결 추적 등록
        pending_key = f"SELL_{code}"
        self.pending_orders[pending_key] = {
            'code': code, 'qty': qty, 'order_type': f'매도({sell_type})',
            'sent_at': time.time(), 'screen_no': '1002'
        }
        msg = f"[{sell_type} 주문 접수] 종목코드: {code}, 수량: {qty} (미체결 감시 시작)"
        self.slack.send_message(msg)

    def monitor_positions(self, pipeline):
        """
        주기적으로(타이머) 호출되어 현재가의 익절(+4%), 손절(-2%) 여부를 판별합니다.
        - pipeline : DataPipeline 참조객체 (최신 가격 조회용)
        """
        with self.lock:
            if not self.positions:
                return
                
            # 복사본을 만들어 순회 시 충돌 방지
            items = list(self.positions.items())
            
        for code, pos_data in items:
            df_1m, _ = pipeline.get_data(code)
            if df_1m.empty:
                continue
                
            current_price = df_1m['close'].iloc[-1]
            buy_price = pos_data['buy_price']
            
            # 1. 최고가 갱신 (Trailing Stop 준비)
            if 'high_price' not in pos_data:
                pos_data['high_price'] = current_price
            else:
                pos_data['high_price'] = max(pos_data['high_price'], current_price)
            
            high_price = pos_data['high_price']
            profit_rate = ((current_price - buy_price) / buy_price) * 100.0
            
            # 2. 리스크 가드: 당일 손실 한도 체크 (자산의 20%)
            if not self.is_risk_halt and self.kiwoom.initial_total_assets > 0:
                loss_limit = self.kiwoom.initial_total_assets * self.LOSS_LIMIT_RATE
                if self.daily_pnl <= -loss_limit:
                    self.logger.critical(f"🛑 [리스크 가드 발동] 당일 손실액({self.daily_pnl:,.0f}원)이 한도({loss_limit:,.0f}원)에 도달했습니다. 모든 포지션을 정리하고 매매를 중단합니다.")
                    self.slack.send_message(f"🛑 *[긴급]* 당일 손실 한도 도달! 모든 종목 청산 후 매매를 중단합니다. (손실: {self.daily_pnl:,.0f}원)")
                    self.is_risk_halt = True
            
            if self.is_risk_halt:
                self.execute_sell(code, pipeline, sell_type="리스크가드_전량청산")
                continue

            # 3. 매도 전략 판별 (손절 -> 트레일링 스톱 -> 익절 순)
            # [A] 하드 스톱 (환경변수 기준 손절)
            if profit_rate <= self.STOP_LOSS * 100.0:
                self.logger.warning(f"[{code}] 손절선({self.STOP_LOSS*100:.1f}%) 도달 ({profit_rate:.2f}%) -> 청산 진행")
                self.execute_sell(code, pipeline, sell_type="손절")
                
            # [B] 트레일링 스톱 (환경변수 기준: 수익 % 도달 후 고점 대비 % 하락 시 매도)
            high_profit_rate = ((high_price - buy_price) / buy_price) * 100.0
            if high_profit_rate >= self.TRAILING_STOP_ACTIVATION * 100.0 and current_price < high_price * (1.0 - self.TRAILING_STOP_CALLBACK):
                self.logger.info(f"[{code}] 트레일링 스톱 발동 (고점 {high_profit_rate:.2f}% -> 현재 {profit_rate:.2f}%, 하락폭 {self.TRAILING_STOP_CALLBACK*100:.1f}%)")
                self.execute_sell(code, pipeline, sell_type="트레일링스톱")
 
            # [C] 고정 익절 (환경변수 기준 익절)
            elif profit_rate >= self.TARGET_PROFIT * 100.0:
                self.logger.info(f"[{code}] 익절선({self.TARGET_PROFIT*100:.1f}%) 도달 ({profit_rate:.2f}%) -> 청산 진행")
                self.execute_sell(code, pipeline, sell_type="익절")

    def monitor_pending_orders(self):
        with self.lock:
            if not self.pending_orders:
                return
                
            now = time.time()
            timed_out_keys = [
                key for key, order in self.pending_orders.items()
                if (now - order['sent_at']) > self.ORDER_TIMEOUT_SEC
            ]
            
            # 타임아웃 대상 추출
            timed_out_orders = [self.pending_orders.pop(key) for key in timed_out_keys]
        
        # 실제 API 호출(주문 취소)은 락 밖에서 수행하여 성능 확보
        for order in timed_out_orders:
            code = order['code']
            elapsed = int(now - order['sent_at'])
            
            warn_msg = (
                f"⏰ *[미체결 타임아웃 경고]* `{code}` {order['order_type']} 주문이 "
                f"{elapsed}초 경과 후에도 미체결 상태입니다. → 자동 취소 시도"
            )
            self.logger.warning(warn_msg)
            self.slack.send_message(warn_msg)
            
            # 키움 API 주문 취소 (주문유형: 3=취소, 수량 0=전량취소)
            if not self.is_dry_run:
                try:
                    self.kiwoom.send_order(
                        f"CancelOrder_{code}", order['screen_no'],
                        3, code, 0, 0, "03", ""  # 주문유형 3=취소
                    )
                    self.logger.warning(f"[{code}] 미체결 주문 취소 요청 발송 완료")
                except Exception as e:
                    self.logger.error(f"[{code}] 주문 취소 요청 실패: {e}")

    def record_execution(self, order_no, code, price, qty, order_status, order_type_code):
        """
        Kiwoom OnReceiveChejanData에서 호출됨 (실제 체결 확정)
        - order_type_code : "+매수", "-매도"
        """
        self.logger.info(f"[실체결 통지] {code} | 가격:{price} | 수량:{qty} | 타입:{order_type_code}")

        with self.lock:
            # 체결 확정 시 미체결 추적 목록에서 제거 (매수/매도 둘 다)
            pending_key = f"BUY_{code}" if "매수" in order_type_code else f"SELL_{code}"
            if pending_key in self.pending_orders:
                self.pending_orders.pop(pending_key)
                self.logger.debug(f"[{code}] 체결 확인 → 미체결 추적 목록에서 제거")

        # order_type_code 에 따른 내부 상태 정제
        with self.lock:
            if "매수" in order_type_code:
                order_type = "매수"
                # 메모리 포지션 업데이트
                if code in self.positions:
                    old_qty = self.positions[code]['qty']
                    old_price = self.positions[code]['buy_price']
                    self.positions[code] = {
                        'buy_price': ((old_price * old_qty) + (price * qty)) / (old_qty + qty),
                        'qty': old_qty + qty
                    }
                else:
                    self.positions[code] = {'buy_price': price, 'qty': qty, 'high_price': price}
                    
            else: # 매도 체결
                order_type = "매도(청산)"
                # 실현 손익 정산 (리스크 가드용)
                if code in self.positions:
                    avg_buy_price = self.positions[code]['buy_price']
                    pnl = (price - avg_buy_price) * qty
                    self.daily_pnl += pnl
                    self.logger.debug(f"[{code}] 매도 체결로 인한 손익 발생: {pnl:,.0f}원 (당일 누적: {self.daily_pnl:,.0f}원)")
                    
                    # 매도 체결 즉시 리스크 가드 체크
                    if not self.is_risk_halt and self.kiwoom.initial_total_assets > 0:
                        loss_limit = self.kiwoom.initial_total_assets * self.LOSS_LIMIT_RATE
                        if self.daily_pnl <= -loss_limit:
                            self.logger.critical(f"🛑 [리스크 가드] 실시간 손실 한도 도달 ({self.daily_pnl:,.0f}원 / 한도: {loss_limit:,.0f}원)")
                            self.is_risk_halt = True

                    self.positions[code]['qty'] -= qty
                    if self.positions[code]['qty'] <= 0:
                        del self.positions[code]
        
        # Slack 알림
        self.slack.send_message(f"[{order_type} 체결완료] 종목:{code}, 가격:{price}, 수량:{qty}")
        # DB 영속화
        self.db.insert_execution(order_no, code, price, qty, order_type)
