"""
ExecutionManager 모듈
----------------------
실제 주식 매수/매도 주문 발송, 현재 보유 중인 포지션 메모리 동기화,
그리고 손익절 비율(예: -2%, +4%)을 모니터링하여 자동 청산을 수행하는 전략 실행의 핵심체입니다.
"""
import logging

class ExecutionManager:
    """주문 실행 및 보유 잔고 모니터링 관리 (손절/익절)을 전담하는 시스템 객체"""
    def __init__(self, kiwoom_core, db_manager, slack_notifier, is_dry_run=False):
        self.logger = logging.getLogger("DopamingBot.ExecutionManager")
        self.kiwoom = kiwoom_core
        self.db = db_manager
        self.slack = slack_notifier
        self.is_dry_run = is_dry_run
        
        self.positions = {}
        
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
        """
        위험/전략 모듈에서 매수 시그널 발생 시 호출되어 1주(테스트분)를 시장가 매수합니다.
        
        Args:
            code (str): 매수할 종목코드
            pipeline (DataPipeline): 현재가 조회를 위한 파이프라인 인스턴스
        """
        if code in self.positions:
            self.logger.info(f"[{code}] 이미 보유중인 종목이므로 추가 매수(물타기) 제한합니다.")
            return

        qty = 1 # 테스트용으로 고정 1주 매수
        
        if self.is_dry_run:
            df_1m, _ = pipeline.get_data(code)
            if df_1m.empty: return
            current_price = df_1m['close'].iloc[-1]
            
            self.logger.warning(f"🤖 [DRY-RUN 가상 매수] {code} - 가격: {current_price}, 수량: {qty}")
            self.record_execution("VIRTUAL_B", code, current_price, qty, "체결완료", "+매수")
            return
            
        # Kiwoom 코어에 주문 위임
        # 주문유형: 1(매수), 호가구분: 03(시장가) -> 시장가 매수 시 주문가격(0)
        self.logger.warning(f"🚀 [{code}] 진입 매수 주문 발송 (수량: {qty})")
        self.kiwoom.send_order("BuyOrder", "1001", 1, code, qty, 0, "03", "")
        
        msg = f"[매수 주문 접수] 종목코드: {code}, 수량: {qty}"
        self.slack.send_message(msg)

    def execute_sell(self, code, pipeline, sell_type="익절"):
        """보유 종목 시장가 전량 청산 (sell_type: '익절' 또는 '손절')"""
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
        
        msg = f"[{sell_type} 주문 접수] 종목코드: {code}, 수량: {qty}"
        self.slack.send_message(msg)

    def monitor_positions(self, pipeline):
        """
        주기적으로(타이머) 호출되어 현재가의 익절(+4%), 손절(-2%) 여부를 판별합니다.
        - pipeline : DataPipeline 참조객체 (최신 가격 조회용)
        """
        if not self.positions:
            return
            
        # pipeline에서 안전하게 1분봉 Dataframe들의 최신 상태 복사본을 가져오려 하지만,
        # 그냥 단순히 각 종목의 마지막 close 가격만 확인하면 됩니다.
        for code, pos_data in list(self.positions.items()):
            df_1m, _ = pipeline.get_data(code)
            if df_1m.empty:
                continue
                
            current_price = df_1m['close'].iloc[-1]
            buy_price = pos_data['buy_price']
            
            if buy_price <= 0:
                continue
                
            profit_rate = ((current_price - buy_price) / buy_price) * 100.0
            
            # 손익비 1:2 (손절 -2%, 익절 +4%)
            if profit_rate >= 4.0:
                self.logger.info(f"[{code}] +4% 도달 ({profit_rate:.2f}%) -> 익절 청산 진행")
                self.execute_sell(code, pipeline, sell_type="익절")
                
            elif profit_rate <= -2.0:
                self.logger.warning(f"[{code}] -2% 도달 ({profit_rate:.2f}%) -> 손절 청산 진행")
                self.execute_sell(code, pipeline, sell_type="손절")

    def record_execution(self, order_no, code, price, qty, order_status, order_type_code):
        """
        Kiwoom OnReceiveChejanData에서 호출됨 (실제 체결 확정)
        - order_type_code : "+매수", "-매도"
        """
        self.logger.info(f"[실체결 통지] {code} | 가격:{price} | 수량:{qty} | 타입:{order_type_code}")

        # order_type_code 에 따른 내부 상태 정제
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
                self.positions[code] = {'buy_price': price, 'qty': qty}
                
        else: # 매도 체결
            # 단순화를 위해 익절인지 손절인지 여부는 체결창에서는 '알수 없음'으로 넘어옴.
            # 하지만 여기서 포지션 감소 처리만 하면 됨.
            order_type = "매도(청산)"
            if code in self.positions:
                self.positions[code]['qty'] -= qty
                if self.positions[code]['qty'] <= 0:
                    del self.positions[code]
        
        # Slack 알림
        self.slack.send_message(f"[{order_type} 체결완료] 종목:{code}, 가격:{price}, 수량:{qty}")
        # DB 영속화
        self.db.insert_execution(order_no, code, price, qty, order_type)
