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
import json
from datetime import datetime


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
        self.FIXED_LOSS_LIMIT = float(os.getenv("RISK_FIXED_LOSS_LIMIT", "1000000"))
        self.INVEST_RATE_PER_STOCK = float(os.getenv("TRADE_INVEST_RATE", "0.05"))
        # [성능 최적화] 매매 비용(수수료+세금) 선반영 비율
        self.TRADING_FRICTION = float(os.getenv("TRADING_FRICTION", "0.0025"))
        
        # 매매 목표 설정 (환경변수 로드)
        self.TARGET_PROFIT = float(os.getenv("TRADE_TARGET_PROFIT", "0.015"))
        self.STOP_LOSS = float(os.getenv("TRADE_STOP_LOSS", "-0.018"))
        self.TRAILING_STOP_ACTIVATION = float(os.getenv("TRAILING_STOP_ACTIVATION", "0.02"))
        self.TRAILING_STOP_CALLBACK = float(os.getenv("TRAILING_STOP_CALLBACK", "0.01"))
        
        # [v3.6] 본절가 보호(Breakeven Shield) 설정 로드
        self.BREAKEVEN_TRIGGER = float(os.getenv("TRADE_BREAKEVEN_TRIGGER", "0.01"))
        self.BREAKEVEN_PROTECT = float(os.getenv("TRADE_BREAKEVEN_PROTECT", "0.003"))
        
        # [v11.1] 오버나잇 (종가 홀딩) 조건 수익률 
        self.OVERNIGHT_HOLD_THRESHOLD = float(os.getenv("OVERNIGHT_HOLD_PROFIT", "3.0"))
        
        # [v11.1] 오버나잇 하단 방어선 (이 수치 밑으로 떨어지면 투매)
        self.OVERNIGHT_DROP_LIMIT = float(os.getenv("OVERNIGHT_DROP_LIMIT", "-1.0"))
        
        # [공격적 투자 모드] 스캘핑 최적화를 위한 가중치 조정
        is_aggressive = os.getenv("AGGRESSIVE_MODE", "False").lower() == 'true'
        if is_aggressive:
            self.logger.warning("🔥 [공격적 투자] 모드 활성화! 빠른 회전을 위해 종목별 투자 비중(10%)을 상향 적용합니다.")
            self.INVEST_RATE_PER_STOCK = 0.10     # 1개 종목 비중 10%
            # TP/SL/BREAKEVEN은 이제 .env에 설정된 v3.6 값을 그대로 사용합니다 (하드코딩 제거)
        
        # 운영 설정 (환경변수 로드)
        self.ORDER_TIMEOUT_SEC = int(os.getenv("ORDER_PENDING_TIMEOUT", "60"))
        
        # 리스크 가드 관련 상태
        self.daily_pnl = 0
        self.is_risk_halt = False
        self.account_password = os.getenv("ACCOUNT_PASSWORD", "0000")
        
        # 스레드 안전성 확보를 위한 락
        self.lock = threading.Lock()
        
        # 미체결 주문 추적: { order_no: {code, qty, order_type, sent_at(timestamp)} }
        self.pending_orders = {}
        self.snapshot_file = os.path.join("data", "dry_run_snapshot.json")
        
        if self.is_dry_run:
            self.logger.info("🤖 ** 시뮬레이션(Dry-Run) 모드 작동 중 ** (JSON 스냅샷 복구 진행)")
            self._load_virtual_snapshot()
        else:
            self.logger.info("🔥 ** 실전 운영(Production) 모드 작동 중 ** (서버 잔고 동기화 대기)")

    def _save_virtual_snapshot(self):
        """[Dry-Run 전용] 가상 매매 상태를 JSON으로 영구 저장"""
        if not self.is_dry_run: return
        os.makedirs(os.path.dirname(self.snapshot_file), exist_ok=True)
        data = {
            'date': datetime.now().strftime("%Y-%m-%d"),
            'positions': getattr(self, 'positions', {}),
            'daily_pnl': getattr(self, 'daily_pnl', 0),
            'available_cash': getattr(self.kiwoom, 'available_cash', getattr(self.kiwoom, 'initial_total_assets', 0))
        }
        try:
            with open(self.snapshot_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"가상 스냅샷 저장 실패: {e}")

    def _load_virtual_snapshot(self):
        """[Dry-Run 전용] 봇 재구동 시 스냅샷 데이터 복원"""
        if os.path.exists(self.snapshot_file):
            try:
                with open(self.snapshot_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                today_str = datetime.now().strftime("%Y-%m-%d")
                
                self.positions = data.get('positions', {})
                self.kiwoom.available_cash = data.get('available_cash', getattr(self.kiwoom, 'initial_total_assets', 0))
                
                if data.get('date') == today_str:
                    self.daily_pnl = data.get('daily_pnl', 0)
                    self.logger.info(f"✅ 오늘자 스냅샷 데이터 로드 (당일현황 100% 복구: 실현손익 {self.daily_pnl:,.0f}원)")
                else:
                    self.daily_pnl = 0
                    self.logger.info("✅ 어제자 스냅샷 로드 (어제 살아남은 종목 복원 및 당일 수익금 리셋)")
                    
                for code, pos in self.positions.items():
                    self.logger.info(f"💾 가상 복원완료 - [{code}] 평단: {pos.get('buy_price'):,.0f}, 수량: {pos.get('qty')}주")
            except Exception as e:
                self.logger.error(f"❌ 가상 스냅샷 로드 실패: {e}")
                self.positions = {}
        else:
            self.logger.info("ℹ️ 기존 가상 스냅샷이 없어 백지 상태로 시뮬레이션을 시작합니다.")
            self.positions = {}

    def sync_server_positions(self, server_positions):
        """Production 모드 시 서버가 내려준 잔고표로 덮어씌움"""
        if self.is_dry_run:
            return
            
        self.positions = server_positions
        self.logger.info(f"서버 실제 포지션 동기화 완료: {len(self.positions)}종목")
        for code, data in self.positions.items():
            self.logger.info(f"실계좌 잔고 - [{code}] 평단: {data['buy_price']}, 수량: {data['qty']}")

    def execute_buy(self, code, pipeline, signal_type="상향돌파"):
        """새로운 종목을 매수합니다.
        가용 현금과 비중(INVEST_RATE_PER_STOCK)을 체크하여 주문합니다."""
        # [리스크 가드] 신규 매수 전 당일 실현 손실 한도 상시 체크
        if not self.is_risk_halt and self.FIXED_LOSS_LIMIT > 0:
            if self.daily_pnl <= -self.FIXED_LOSS_LIMIT:
                self.logger.critical(f"🛑 [리스크 가드] 당일 실현 손실액({self.daily_pnl:,.0f}원)이 설정 한도({self.FIXED_LOSS_LIMIT:,.0f}원)에 도달. 신규 매수 금지.")
                self.is_risk_halt = True

        if self.is_risk_halt:
            self.logger.error(f"⚠️ [리스크 가드] 당일 손실 한도 초과로 인해 [{code}] 매수를 차단합니다.")
            return

        with self.lock:
            # 1. 이미 보유 중인 종목인지 체크
            if code in self.positions:
                self.logger.info(f"[{code}] 이미 보유중인 종목이므로 추가 매수(물타기) 제한합니다.")
                return
            
            # 2. 이미 주문이 발송되어 '체결 대기' 중인 종목인지 체크 (중복 주문 방지)
            if f"BUY_{code}" in self.pending_orders:
                self.logger.info(f"[{code}] 이미 매수 주문이 진행 중입니다. 중복 주문을 차단합니다.")
                return

        # [자금 관리] 종목별 비중 결정 (전체 자산의 5%)
        # 1. 현재가 조회 (파이프라인 참조)
        df_1m, _ = pipeline.get_data(code)
        if df_1m is None or df_1m.empty:
            self.logger.error(f"⚠️ [{code}] 현재가 데이터를 가져올 수 없어 매수를 포기합니다.")
            return
        
        current_price = int(df_1m['close'].iloc[-1])
        if current_price <= 0: return

        # 2. [복리 투자 엔진] 목표 투자금액 산출 (수익금 눈덩이 반영)
        invest_rate = self.INVEST_RATE_PER_STOCK
        
        # 기본 자산(아침 예수금)에 오늘 벌어들인 누적 수익금(실현손익)을 플러긴하여 스노우볼 확장
        official_pnl = getattr(self.kiwoom, 'official_daily_pnl', self.daily_pnl)
        base_assets = self.kiwoom.initial_total_assets + official_pnl
        
        # 자산이 비정상일 경우 하한 방어 로직
        if base_assets <= 0:
            base_assets = max(self.kiwoom.initial_total_assets, self.kiwoom.available_cash)
            
        target_budget = base_assets * invest_rate
        qty = int(target_budget / current_price)
        
        # 최소 1주 보장
        if qty == 0:
            qty = 1
            
        # 3. 예수금 하한선 체크 및 최종 수량 확정 (임계 구역 설정)
        with self.lock:
            # 실시간 가용 현금 계산: (서버 예수금) - (현재 주문 요청 중인 가상 예약금)
            effective_cash = self.kiwoom.available_cash - self.kiwoom.reserved_cash
            required_cash = qty * current_price
            
            if required_cash > effective_cash:
                # 현금 부족 시 살 수 있는 만큼만 (Maximum affordable)
                qty = int(effective_cash / current_price)
                if qty <= 0:
                    self.logger.error(f"❌ [예수금 부족] {code} 주문 차단 (가용:{effective_cash:,}원 / 필요:{current_price:,}원)")
                    return
                self.logger.warning(f"⚠️ [가용 현금 제한] 잔액에 맞춰 {qty}주로 수량을 조정합니다. (가용:{effective_cash:,}원)")

            if self.is_dry_run:
                self.logger.warning(f"🤖 [DRY-RUN 가상 매수] {code}({signal_type}) - 가격: {current_price}, 수량: {qty}")
                self.record_execution("VIRTUAL_B", code, current_price, qty, "체결완료", "+매수", cumulative_qty=qty, total_order_qty=qty)
                # 가상 체결 직후 포지션에 시그널 타입 심기
                if code in self.positions: self.positions[code]['signal_type'] = signal_type
                return
                
            # Kiwoom 코어에 주문 위임
            self.logger.warning(f"🚀 [{code}] 진입 매수 주문 발송 (가격: {current_price:,}원, 수량: {qty}주)")
            
            # [수정] 수동 예약금 관리를 제거하고 API 동기화에 의존 (시차 방지)
            self.kiwoom.send_order("BuyOrder", "1001", 1, code, qty, 0, "03", "")
            
            # 미체결 추적 등록
            pending_key = f"BUY_{code}"
            self.pending_orders[pending_key] = {
                'code': code, 'qty': qty, 'order_type': '매수',
                'sent_at': time.time(), 'screen_no': '1001',
                'signal_type': signal_type
            }
        msg = f"🚀 [매수 주문 접수] {code}, 수량: {qty}주 (약 {qty*current_price:,}원)"
        self.slack.send_message(msg)

    def execute_sell(self, code, pipeline, sell_type="익절"):
        """보유 종목 시장가 전량 청산 (sell_type: '익절' 또는 '손절')"""
        with self.lock:
            # 1. 보유 중인 종목인지 체크
            if code not in self.positions:
                return
            
            # 2. 이미 매도 주문이 진행 중인지 체크 (중복 매도 방지)
            if f"SELL_{code}" in self.pending_orders:
                self.logger.info(f"[{code}] 이미 매도 주문이 진행 중입니다. 중복 매도를 차단합니다.")
                return
                
            qty = self.positions[code]['qty']
        
        if self.is_dry_run:
            df_1m, _ = pipeline.get_data(code)
            if df_1m is not None and not df_1m.empty:
                current_price = df_1m['close'].iloc[-1]
            else:
                current_price = self.positions[code].get('current_price', self.positions[code]['buy_price'])
            
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
        msg = f"💥 [{sell_type} 매도 접수] 종목: {code}, 수량: {qty}주"
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
            
            is_mock = getattr(self.kiwoom, 'is_mock', False)
            
            # [v11.5] 매수 후 5초 Grace Period - 파이프라인 초기화 대기
            buy_time = pos_data.get('buy_time')
            if buy_time and (time.time() - buy_time) < 5:
                continue
            
            # [v11.6] 항상 자체계산(실시간 시세 기반)을 먼저 수행
            simple_rate = (((current_price - buy_price) / buy_price) * 100.0) - (self.TRADING_FRICTION * 100.0)
            
            # [v11.7 수익률 판단 로직 고도화]
            # 1. 손절(Stop Loss) 판단용: API 지연 시에도 조기 대응을 위해 '최악의 값(최소)' 선택
            # 2. 익절/보호막(Take Profit/Shield) 판단용: API 지연으로 인한 기회 상실 방지를 위해 '실시간(최신)' 선택
            
            api_rate = pos_data.get('api_profit_rate')
            loss_profit_rate = simple_rate  # 기본값은 자체계산
            target_profit_rate = simple_rate # 익절용은 항상 실시간 시세 우선
            
            if api_rate is not None and not is_mock:
                # 괴리가 10%p 이내일 때만 API 데이터 참고
                if abs(api_rate - simple_rate) <= 10.0:
                    loss_profit_rate = min(api_rate, simple_rate)
                    # 익절/보호막 판단용으로는 실시간 시세(simple_rate)가 더 높다면 실시간을 우선함
                    target_profit_rate = max(api_rate, simple_rate)
                else:
                    self.logger.warning(f"⚠️ [{code}] API 수익률({api_rate:.2f}%) vs 자체계산({simple_rate:.2f}%) 괴리 과다 -> 자체계산 강제 적용")
            
            profit_rate = loss_profit_rate # 기본 루프용 (나중에 개별 조건에서 target_profit_rate 사용 가능)
            
            # [v11.5] ±15% 이상치 절대 차단 (공식/추정 구분 없이)
            if abs(profit_rate) > 15.0:
                self.logger.error(f"🚫 [{code}] 수익률 이상치 차단: {profit_rate:.2f}% (±15% 초과, 데이터 오류 의심)")
                continue
            
            # [v11.7 디버그] 매도 판단 직전 핵심 변수 로깅
            self.logger.debug(f"[{code}] 수익률: buy={buy_price:,} cur={current_price:,} (손절용:{loss_profit_rate:+.2f}% / 익절용:{target_profit_rate:+.2f}%)")
            
            # 1. 최고가(High Price) 및 실질 최고 수익률(High Net Profit) 갱신
            if 'high_price' not in pos_data:
                pos_data['high_price'] = current_price
            else:
                pos_data['high_price'] = max(pos_data['high_price'], current_price)
            
            high_price = pos_data['high_price']
            # 트레일링 스톱 판단의 기준이 되는 '고점 수익률'도 실질 수익률(Net) 기준으로 산출
            friction_pct = self.TRADING_FRICTION * 100.0
            high_net_profit_rate = (((high_price - buy_price) / buy_price) * 100.0) - friction_pct
            
            # 2. 리스크 가드: 당일 실현 손실 한도 체크
            if not self.is_risk_halt and self.FIXED_LOSS_LIMIT > 0:
                if self.daily_pnl <= -self.FIXED_LOSS_LIMIT:
                    self.logger.critical(f"🛑 [리스크 가드 발동] 당일 실현 손실액({self.daily_pnl:,.0f}원)이 한도({self.FIXED_LOSS_LIMIT:,.0f}원)에 도달했습니다. 모든 포지션을 정리하고 매매를 중단합니다.")
                    self.slack.send_message(f"🛑 *[긴급]* 당일 실현 손실 한도 도달! 모든 종목 청산 후 매매를 중단합니다. (실현 손실: {self.daily_pnl:,.0f}원)")
                    self.is_risk_halt = True
            
            if self.is_risk_halt:
                self.execute_sell(code, pipeline, sell_type="리스크가드_전량청산")
                continue

            # 3. [v3.6] 본절가 보호(Breakeven Shield) 로직 판별
            if 'reached_breakeven' not in pos_data:
                pos_data['reached_breakeven'] = False
            
            # [v5.4] 쉴드 강화: ROI +1.5% 도달 시 확실한 본절(+0.1% 이상) 확보 (익절용 수익률 사용)
            if not pos_data['reached_breakeven'] and target_profit_rate >= 1.5: 
                pos_data['reached_breakeven'] = True
                self.logger.warning(f"🛡️ [{code}] [SHIELD] 강력한 수익 확보(+1.5%), 본절 보호 상향")
                self.slack.send_message(f"🛡️ *[SHIELD]* {code} 강력한 수익(1.5%) 확보로 원금 보호 라인을 가동합니다.")

            if not pos_data['reached_breakeven'] and target_profit_rate >= self.BREAKEVEN_TRIGGER * 100.0:
                pos_data['reached_breakeven'] = True
                self.logger.warning(f"🛡️ [{code}] [SHIELD] 수익률 {target_profit_rate:.2f}% 도달 -> 본전 보호(+{self.BREAKEVEN_PROTECT*100:.1f}%) 활성화")
                self.slack.send_message(f"🛡️ *[SHIELD]* {code} 수익률 {target_profit_rate:.2f}% 도달! 본전 보호 라인을 +{self.BREAKEVEN_PROTECT*100:.1f}%로 설정합니다.")

            # 4. 매도 전략 판별 (본절보호 -> 손절 -> 트레일링 스톱 -> 익절 순) 
            signal_type = pos_data.get('signal_type', '상향돌파')

            # [동적 매도 파라미터 적용]
            if signal_type == "눌림목반등":
                dyn_stop = -1.2
                dyn_trailing_activation = 1.0
                dyn_trailing_callback = 0.5
                dyn_target = 1.6
            else:
                dyn_stop = self.STOP_LOSS * 100.0
                dyn_trailing_activation = self.TRAILING_STOP_ACTIVATION * 100.0
                dyn_trailing_callback = self.TRAILING_STOP_CALLBACK * 100.0
                dyn_target = self.TARGET_PROFIT * 100.0

            # [A] 하드 스톱 또는 본절가 보호 청산 (손절용 수익률 기반)
            current_stop_limit = self.BREAKEVEN_PROTECT * 100.0 if pos_data['reached_breakeven'] else dyn_stop
            
            if loss_profit_rate <= current_stop_limit:
                s_type = "본절보호" if pos_data['reached_breakeven'] else "손절"
                self.logger.warning(f"[{code}] {s_type}선({current_stop_limit:.1f}%) 도달 (현재:{loss_profit_rate:.2f}%) -> 청산")
                self.execute_sell(code, pipeline, sell_type=s_type)
                
            # [B] 트레일링 스톱 (익절용 수익률 기반)
            elif high_net_profit_rate >= dyn_trailing_activation and target_profit_rate <= high_net_profit_rate - dyn_trailing_callback:
                self.logger.info(f"[{code}] 트레일링 스톱 발동 (실질고점 {high_net_profit_rate:.2f}% -> 현재 {target_profit_rate:.2f}%, 고점대비 하락폭 {dyn_trailing_callback:.1f}%)")
                self.execute_sell(code, pipeline, sell_type="트레일링스톱")
            
            # [v5.4] [B-2] 스마트 익절 (BB 상단 터치 & 순익권)
            elif 'BB_Upper' in df_1m.columns and current_price >= df_1m['BB_Upper'].iloc[-1] and target_profit_rate > 0.8:
                self.logger.info(f"[{code}] [SMART] BB 상단 터치 스마트 익절 발동 ({target_profit_rate:.2f}%)")
                self.execute_sell(code, pipeline, sell_type="스마트익절_BB상단")
 
            # [C] 고정 익절 (익절용 수익률 기반)
            elif target_profit_rate >= dyn_target:
                self.logger.info(f"[{code}] 실질 익절선({dyn_target:.1f}%) 도달 ({target_profit_rate:.2f}%) -> 청산")
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
            # self.slack.send_message(warn_msg)
            
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

    def record_execution(self, order_no, code, price, qty, order_status, order_type_code, cumulative_qty=0, total_order_qty=0):
        """
        Kiwoom OnReceiveChejanData에서 호출됨 (실제 체결 확정)
        가상 매매 시에도 직접 호출되어 상태를 동기화합니다.
        """
        try:
            self.logger.info(f"[체결 통지] {code} | 가격:{price} | 수량:{qty} | 누적:{cumulative_qty}/{total_order_qty} | 타입:{order_type_code}")

            with self.lock:
                # 체결 확정 시 미체결 추적 목록에서 제거
                pending_key = f"BUY_{code}" if "매수" in order_type_code else f"SELL_{code}"
                if pending_key in self.pending_orders:
                    if cumulative_qty >= total_order_qty:
                        self.pending_orders.pop(pending_key)
                        self.logger.debug(f"[{code}] 전량 체결 확인 → 미체결 추적 종료")

            pnl_info = ""
            with self.lock:
                if "매수" in order_type_code:
                    order_type = "매수"
                    if code in self.positions:
                        old_qty = self.positions[code]['qty']
                        old_price = self.positions[code]['buy_price']
                        self.positions[code]['buy_price'] = ((old_price * old_qty) + (price * qty)) / (old_qty + qty)
                        self.positions[code]['qty'] = old_qty + qty
                    else:
                        self.positions[code] = {'buy_price': price, 'qty': qty, 'high_price': price}
                        
                        if not self.is_dry_run:
                            self.kiwoom.set_real_reg("1000", [code], "10;15;20", "1")
                            self.logger.info(f"🚀 [{code}] 신규 매수 포지션 실시간 감시 시작")
                        
                else: # 매도 체결
                    order_type = "매도(청산)"
                    if code in self.positions:
                        avg_buy_price = self.positions[code]['buy_price']
                        total_friction_pct = self.TRADING_FRICTION * 100.0
                        raw_profit_rate = (price - avg_buy_price) / avg_buy_price * 100.0
                        net_profit_rate = raw_profit_rate - total_friction_pct
                        
                        net_pnl = (avg_buy_price * qty) * (net_profit_rate / 100.0)
                        pnl_info = f" | 실질수익: {int(net_pnl):+,}원 ({net_profit_rate:+.2f}%)"
                        self.positions[code]['qty'] -= qty
                        
                        # [즉시 반영] 체결 시 서버 opt10074의 지연시간 대기 없이 당일 실현손익 먼저 업데이트
                        if self.daily_pnl is None:
                            self.daily_pnl = 0
                        self.daily_pnl += int(net_pnl)
                        
                        if self.positions[code]['qty'] <= 0:
                            del self.positions[code]

            # 서버 동기화 (실매매인 경우)
            if not self.is_dry_run:
                self.kiwoom.request_account_info(self.account_password)
                self.kiwoom.request_daily_pnl()

            # [알림 통합] 드라이런의 경우 total_order_qty가 0일 수 있으므로 qty를 대신 활용
            is_full_fill = cumulative_qty >= total_order_qty or total_order_qty == 0
            if is_full_fill:
                display_qty = total_order_qty if total_order_qty > 0 else qty
                clean_code = code.replace("A", "").strip().zfill(6)
                name = self.kiwoom.get_master_code_name(clean_code) or self.kiwoom.code_names.get(code, code)
                
                status_label = "✅ [매수완료]" if "매수" in order_type else "💥 [매도완료]"
                current_total_qty = self.positions[code]['qty'] if code in self.positions else 0
                
                exec_msg = (
                    f"{status_label} *{name}({code})*\n"
                    f"• 체결가: {int(price):,}원\n"
                    f"• 체결량: {display_qty}주 (전량){pnl_info}\n"
                    f"• 현재 잔고: {current_total_qty}주"
                )
                self.slack.send_message(exec_msg)
                
            self.db.insert_execution("EXE_" + str(int(time.time())), code, price, qty, order_type)
            
            # [가상 스냅샷 업데이트] 체결이 완료될 때마다 파일에 상태 저장
            self._save_virtual_snapshot()

        except Exception as e:
            self.logger.error(f"❌ record_execution 처리 중 예외 발생: {e}")

    def sync_server_positions(self, server_pos_dict):
        """
        서버(Kiwoom)로부터 받은 공식 잔고 현황으로 로컬 포지션을 100% 동기화합니다.
        가장 정확한 'Ultimate Truth' 데이터를 기반으로 합니다.
        """
        with self.lock:
            # 1. 서버 정보로 덮어쓰기
            for code, data in server_pos_dict.items():
                if code not in self.positions:
                    self.positions[code] = {
                        'buy_price': data['buy_price'],
                        'qty': data['qty'],
                        'high_price': data['buy_price'], # 초기 고점은 매입가로 설정
                        'api_profit_rate': data.get('api_profit_rate'),
                        'api_pnl': data.get('api_pnl')
                    }
                else:
                    # 기존 보유 중이면 수량, 평단가, 공식 수익률 최신화 (고점 기록은 유지)
                    self.positions[code]['qty'] = data['qty']
                    self.positions[code]['buy_price'] = data['buy_price']
                    if 'api_profit_rate' in data:
                        self.positions[code]['api_profit_rate'] = data['api_profit_rate']
                    if 'api_pnl' in data:
                        self.positions[code]['api_pnl'] = data['api_pnl']
            
            # 2. 서버 잔고에 없는 종목은 로컬에서도 제거 (전량 매도 반영)
            # [v5.1] 드라이런 모드일 때는 가상 잔고가 서버에 업으므로 삭제 로직을 우회하여 보호함
            if not self.is_dry_run:
                local_codes = list(self.positions.keys())
                for code in local_codes:
                    if code not in server_pos_dict:
                        self.logger.info(f"🗑️ [{code}] 서버 잔고에 없음 -> 로컬 포지션 삭제 및 감시 종료")
                        del self.positions[code]
                        self.kiwoom.set_real_remove("1000", code)

    def sync_single_position(self, code, qty, avg_price):
        """
        실시간 잔고 통보(sGubun=1)를 기반으로 특정 종목의 상태만 즉시 업데이트합니다.
        매매 체결 직후 키움이 계산한 공식 데이터를 반영합니다.
        """
        with self.lock:
            if qty <= 0:
                    self.logger.info(f"🚫 [{code}] 잔고 0 확인 -> 포지션 제거 및 미체결 클린업")
                    del self.positions[code]
                    
                    # [v11.7] 잔고가 0이면 해당 종목의 모든 미체결 추적 강제 종료 (데드락 방지)
                    pending_keys = [k for k in self.pending_orders.keys() if code in k]
                    for k in pending_keys:
                        self.pending_orders.pop(k, None)
                    
                    if not self.is_dry_run:
                        self.kiwoom.set_real_remove("1000", code)
            else:
                if code not in self.positions:
                    self.positions[code] = {
                        'buy_price': avg_price,
                        'qty': qty,
                        'high_price': avg_price
                    }
                else:
                    self.positions[code]['qty'] = qty
                    self.positions[code]['buy_price'] = avg_price
                self.logger.info(f"✅ [{code}] 실시간 잔고 동기화 완료: {qty}주 @ {avg_price:,.0f}원")

    def clear_pending_orders(self):
        """현재 추적 중인 모든 미체결 주문 목록을 비웁니다."""
        with self.lock:
            count = len(self.pending_orders)
            self.pending_orders.clear()
            if count > 0:
                self.logger.info(f"🗑️ [미체결 정리] 장 종료로 인해 {count}개의 미체결 추적을 중단했습니다.")

    def smart_liquidate_positions(self, pipeline):
        """
        [15:19 오버나잇 방지] 현재 가지고 있는 포지션 중,
        수익률이 OVERNIGHT_HOLD_THRESHOLD (예: +3.0%) 미만인 종목만 강제 시장가 청산합니다.
        강력한 매수세(상한가 등)가 있는 종목은 내일 상승을 기대하며 예외적으로 홀딩합니다.
        """
        liquidated_count = 0
        held_count = 0
        
        with self.lock:
            # 안전한 순회를 위해 리스트 백업
            codes_to_check = list(self.positions.keys())
            
        for code in codes_to_check:
            # 1. 포지션 데이터 조회
            with self.lock:
                if code not in self.positions:
                    continue
                pos_data = self.positions[code]
                
            buy_price = pos_data['buy_price']
            
            # 2. 현재가 및 수익률 조회
            df_1m, _ = pipeline.get_data(code)
            if df_1m is not None and not df_1m.empty:
                current_price = df_1m['close'].iloc[-1]
            else:
                current_price = buy_price
                
            friction_pct = self.TRADING_FRICTION * 100.0
            profit_rate = (((current_price - buy_price) / buy_price) * 100.0) - friction_pct
            
            # [API 공식 수익률 덮어쓰기]
            if 'api_profit_rate' in pos_data and not getattr(self.kiwoom, 'is_mock', False):
                profit_rate = pos_data['api_profit_rate']
                
            # 3. 오버나잇 (마감 홀딩) 3단계 분류 판단
            if profit_rate >= self.OVERNIGHT_HOLD_THRESHOLD:
                self.logger.warning(f"💎 [초강세 홀딩] {code} - 현재 수익률 {profit_rate:+.2f}%(기대 상회). 강력한 모멘텀 감지로 내일까지 홀딩합니다!")
                held_count += 1
            elif profit_rate > self.OVERNIGHT_DROP_LIMIT:
                self.logger.info(f"💤 [보합권 연장] {code} - 현재 수익률 {profit_rate:+.2f}%. 세금(-0.25%) 절약 및 내일 슈팅 기대를 위해 하루 더 지켜봅니다.")
                held_count += 1
            else:
                self.logger.critical(f"💣 [위험군 투매] {code} - 현재 수익률 {profit_rate:+.2f}%. 하단 방어선({self.OVERNIGHT_DROP_LIMIT}%) 이탈! 추가 폭락 리스크 차단용 시장가 투매.")
                self.execute_sell(code, pipeline, sell_type="강제정리")
                liquidated_count += 1
                
        # 리포팅
        msg = f"🚨 *[15:19 오버나잇 3단계 검문 결과]*\n- 💣 위험군 도태 (시장가 컷): {liquidated_count}개 종목\n- 💎/💤 생존 승인 (오버나잇): {held_count}개 종목"
        self.slack.send_message(msg)

    def emergency_liquidate_all(self, pipeline):
        """[패닉 버튼 전용] 모든 종목을 즉시 시장가 투매하고, 신규 매수를 영구 차단합니다."""
        liquidated_count = 0
        with self.lock:
            codes_to_sell = list(self.positions.keys())
            
        for code in codes_to_sell:
            self.logger.critical(f"🚨 [긴급 강제 투매] {code} - 사용자의 비상 버튼 개입으로 즉시 시장가 투척!")
            self.execute_sell(code, pipeline, sell_type="패닉투매")
            liquidated_count += 1
            
        self.is_risk_halt = True
        msg = f"🚨 *[긴급 시스템 정지 & 투매 발동]*\n- 💣 완전히 모든 주식을 시장가로 내던졌습니다 ({liquidated_count}종목).\n- 신규 매수 시스템이 정지(Lock)되었습니다."
        self.slack.send_message(msg)
