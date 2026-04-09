import logging
import unittest
from unittest.mock import MagicMock
import pandas as pd

from core.execution_manager import ExecutionManager

logging.basicConfig(level=logging.DEBUG)

class TestExecutionSimulation(unittest.TestCase):

    def _make_manager(self, initial_positions=None):
        """테스트용 ExecutionManager 팩토리"""
        kiwoom_mock = MagicMock()
        kiwoom_mock.initial_total_assets = 0 # int 비교를 위해 0으로 초기화
        kiwoom_mock.available_cash = 10000000 # 기본 1,000만원 설정 (qty 계산용)
        db_mock = MagicMock()
        slack_mock = MagicMock()
        # ★ MagicMock이 아닌 실제 dict 반환으로 고정해야 함
        db_mock.load_todays_open_positions.return_value = initial_positions or {}
        manager = ExecutionManager(kiwoom_mock, db_mock, slack_mock, is_dry_run=True)
        return manager, slack_mock, db_mock

    def _make_pipeline(self, price):
        pipeline_mock = MagicMock()
        df = pd.DataFrame({'close': [price]})
        pipeline_mock.get_data.return_value = (df, df)
        return pipeline_mock

    # ─────────────────────────────────────────────────────────────────
    # CASE 1: 정상 매수 흐름
    # ─────────────────────────────────────────────────────────────────
    def test_buy_creates_position(self):
        print("\n[CASE 1] 매수 체결 포지션 생성 검증")
        manager, slack, db = self._make_manager()
        pipeline = self._make_pipeline(80000)

        manager.execute_buy("005930", pipeline)

        self.assertIn("005930", manager.positions, "매수 후 positions에 종목이 있어야 함")
        self.assertEqual(manager.positions["005930"]["buy_price"], 80000)
        # 1,000만원의 5% = 50만원 / 8만원 = 6.25 -> 6주
        self.assertEqual(manager.positions["005930"]["qty"], 6)
        print(f"  → positions: {manager.positions}")

    # ─────────────────────────────────────────────────────────────────
    # CASE 2: 중복 매수 방지
    # ─────────────────────────────────────────────────────────────────
    def test_no_duplicate_buy(self):
        print("\n[CASE 2] 중복 매수 방지 검증")
        manager, _, _ = self._make_manager()
        pipeline = self._make_pipeline(80000)

        manager.execute_buy("005930", pipeline)
        manager.execute_buy("005930", pipeline)  # 2번째는 무시돼야 함

        self.assertEqual(manager.positions["005930"]["qty"], 6, "중복 매수 차단 실패!")
        print(f"  → qty: {manager.positions['005930']['qty']} (6이어야 함)")

    # ─────────────────────────────────────────────────────────────────
    # CASE 3: 익절 (+4% 도달 시 매도)
    # ─────────────────────────────────────────────────────────────────
    def test_profit_take_at_plus4(self):
        print("\n[CASE 3] +4% 익절 자동 청산 검증")
        manager, slack, _ = self._make_manager()

        manager.execute_buy("005930", self._make_pipeline(80000))
        self.assertIn("005930", manager.positions)

        # 현재가 83,200 → +4% 초과
        manager.monitor_positions(self._make_pipeline(83200))

        self.assertNotIn("005930", manager.positions, "익절 후 포지션이 남아있으면 안 됨!")
        print(f"  → 익절 청산 완료. 현재 positions: {manager.positions}")

    # ─────────────────────────────────────────────────────────────────
    # CASE 4: 손절 (-2% 도달 시 매도)
    # ─────────────────────────────────────────────────────────────────
    def test_stop_loss_at_minus2(self):
        print("\n[CASE 4] -2% 손절 자동 청산 검증")
        manager, _, _ = self._make_manager()

        manager.execute_buy("005930", self._make_pipeline(80000))
        self.assertIn("005930", manager.positions)

        # 현재가 78,400 → -2% 초과
        manager.monitor_positions(self._make_pipeline(78400))

        self.assertNotIn("005930", manager.positions, "손절 후 포지션이 남아있으면 안 됨!")
        print(f"  → 손절 청산 완료. 현재 positions: {manager.positions}")

    # ─────────────────────────────────────────────────────────────────
    # CASE 5: 미달 → 청산 없음 (경계값 테스트)
    # ─────────────────────────────────────────────────────────────────
    def test_no_exit_within_bounds(self):
        print("\n[CASE 5] 손익 미달 시 포지션 유지 검증")
        manager, _, _ = self._make_manager()

        manager.execute_buy("005930", self._make_pipeline(80000))
        # 현재가 81,000 → +1.25%, 청산 기준 미달
        manager.monitor_positions(self._make_pipeline(81000))

        self.assertIn("005930", manager.positions, "청산 기준 미달인데 포지션이 사라짐!")
        print(f"  → 포지션 유지 확인: {manager.positions}")

    # ─────────────────────────────────────────────────────────────────
    # CASE 6: Slack 알림 발송 검증
    # ─────────────────────────────────────────────────────────────────
    def test_slack_notification_on_execution(self):
        print("\n[CASE 6] 체결 시 Slack 알림 발송 검증")
        manager, slack, _ = self._make_manager()

        manager.execute_buy("005930", self._make_pipeline(80000))
        manager.monitor_positions(self._make_pipeline(83200))  # 익절 유도

        self.assertTrue(slack.send_message.called, "Slack 메시지가 한 번도 호출되지 않음!")
        print(f"  → Slack send_message 총 호출 횟수: {slack.send_message.call_count}")
        for call in slack.send_message.call_args_list:
            print(f"     메시지: {call.args[0]}")



    # ─────────────────────────────────────────────────────────────────
    # CASE 7: 미체결 타임아웃 → Slack 경고 발송 검증
    # ─────────────────────────────────────────────────────────────────
    def test_pending_order_timeout_triggers_slack(self):
        print("\n[CASE 7] 미체결 타임아웃 시 Slack 경고 발송 검증")
        import time
        manager, slack, _ = self._make_manager()

        # DRY-RUN 매수는 즉시 체결되므로, 실전 모드로 pending 등록만 테스트
        manager.is_dry_run = False  # 실전 모드로 전환
        # pending_orders에 직접 60초 초과된 주문 삽입
        manager.pending_orders["BUY_005930"] = {
            'code': '005930', 'qty': 1, 'order_type': '매수',
            'sent_at': time.time() - 65,  # 65초 전 주문 → 타임아웃 조건 충족
            'screen_no': '1001'
        }

        manager.monitor_pending_orders()

        # pending_orders에서 제거됐는지 확인
        self.assertNotIn("BUY_005930", manager.pending_orders, "타임아웃된 주문이 pending에 남아있으면 안 됨!")
        # Slack 경고 메시지가 발송됐는지 확인
        self.assertTrue(slack.send_message.called, "Slack 타임아웃 경고가 발송되지 않음!")
        warning_msgs = [c.args[0] for c in slack.send_message.call_args_list]
        self.assertTrue(any("타임아웃" in m for m in warning_msgs), "타임아웃 경고 메시지 내용 확인 실패!")
        safe_msg = warning_msgs[-1].encode('cp949', errors='replace').decode('cp949')
        print(f"  -> 타임아웃 경고 발송 확인 (내용은 로그 참조)")

    # ─────────────────────────────────────────────────────────────────
    # CASE 9: 트레일링 스톱 (고점 대비 하락 시 수익 확정)
    # ─────────────────────────────────────────────────────────────────
    def test_trailing_stop_logic(self):
        print("\n[CASE 9] 트레일링 스톱 로직 검증")
        manager, _, _ = self._make_manager()
        pipeline = self._make_pipeline(80000)

        # 80,000원에 매수
        manager.execute_buy("005930", pipeline)
        
        # 주가 82,000원 (+2.5%) 도달 -> high_price 갱신
        manager.monitor_positions(self._make_pipeline(82000))
        self.assertEqual(manager.positions["005930"]["high_price"], 82000)
        
        # 주가 81,000원 (-1.2% drop from high, but still +1.25% from buy)
        # 트레일링 스톱(1% drop) 조건 충족 유도
        manager.monitor_positions(self._make_pipeline(81100)) # 82000 * 0.99 = 81180
        
        self.assertNotIn("005930", manager.positions, "트레일링 스톱 발동 후 포지션이 청산되어야 함")
        print(f"  -> 트레일링 스톱 발동 및 청산 확인 완료")

    # ─────────────────────────────────────────────────────────────────
    # CASE 10: 리스크 가드 (당일 누적 손실 한도 초과 시 매매 중단)
    # ─────────────────────────────────────────────────────────────────
    def test_risk_guard_daily_limit(self):
        print("\n[CASE 10] 리스크 가드(Daily Loss Limit - 20%) 검증")
        manager, slack, _ = self._make_manager()
        
        # 1. 초기 자산 및 한도 설정 (100만원 예시)
        manager.kiwoom.initial_total_assets = 1000000 
        manager.LOSS_LIMIT_RATE = 0.20 # 20% (20만원)
        
        # 2. 15만원 손실 상황 발생 유도 (아직 세이프)
        # 1,000,000원에 사서 850,000원에 판 상황
        manager.positions["100000"] = {'buy_price': 1000000, 'qty': 1, 'high_price': 1000000}
        manager.record_execution("S1", "100000", 850000, 1, "체결", "-매도")
        self.assertEqual(manager.daily_pnl, -150000)
        self.assertFalse(manager.is_risk_halt, "15% 손실에서는 중단되지 않아야 함")
        
        # 3. 25만원 손실 상황 발생 유도 (한도 20만원 초과)
        # 2,000,000원에 사서 1,750,000원에 판 상황 (-25만)
        manager.positions["200000"] = {'buy_price': 2000000, 'qty': 1, 'high_price': 2000000}
        manager.record_execution("S2", "200000", 1900000, 1, "체결", "-매도") # 15만 + 10만 = 25만
        self.assertTrue(manager.is_risk_halt, "20% 초과 손실(25만) 시 중단되어야 함")
        
        # 4. 새로운 매수 시도 -> 리스크 가드에 의해 차단되어야 함
        manager.execute_buy("005930", self._make_pipeline(70000))
        self.assertNotIn("005930", manager.positions, "손실 한도 초과 후 신규 매수가 차단되어야 함")
        
        print(f"  -> 리스크 가드(20% 비율 기준) 발동 및 거래 중단 확인 완료")
        
    # ─────────────────────────────────────────────────────────────────
    # CASE 12: 자금 관리 (종목당 5% 비중 수량 산출)
    # ─────────────────────────────────────────────────────────────────
    def test_money_management_qty_calculation(self):
        print("\n[CASE 12] 자금 관리 (5% 비중 수량 산출) 검증")
        manager, _, _ = self._make_manager()
        
        # 1. 초기 자산 설정 (1억원) -> 5%는 500만원
        manager.kiwoom.initial_total_assets = 100000000
        manager.kiwoom.available_cash = 200000000 # 충분한 현금
        
        # 2. 10만원짜리 종목 매수 시 -> 50주 산출되어야 함
        pipeline = self._make_pipeline(100000)
        manager.execute_buy("TEST01", pipeline)
        
        self.assertEqual(manager.positions["TEST01"]["qty"], 50, "1억의 5%인 500만원만큼(50주) 매수되어야 함")
        
        # 3. 예수금이 부족한 상황 테스트 (현금 15만원만 있음)
        # 10만원짜리 종목 5% 비중은 50주(500만원)이지만, 현금이 15만원뿐이면 1주만 사야 함
        manager.kiwoom.available_cash = 150000
        manager.execute_buy("TEST02", pipeline)
        self.assertEqual(manager.positions["TEST02"]["qty"], 1, "예수금 부족 시 살 수 있는 최대치(1주)만 매수되어야 함")
        
        # 4. 현금이 아예 없는 상황 -> 매수 포기
        manager.kiwoom.available_cash = 50000
        manager.execute_buy("TEST03", pipeline)
        self.assertNotIn("TEST03", manager.positions, "현금이 1주 가격보다 적으면 매수를 포기해야 함")
        
        print(f"  -> 자금 관리(5% 비중 및 예수금 방어) 로직 확인 완료")

    # ─────────────────────────────────────────────────────────────────
    # CASE 11: 일일 요약 리포트 산출 검증
    # ─────────────────────────────────────────────────────────────────
    def test_daily_summary_calculation(self):
        print("\n[CASE 11] 일일 요약 리포트 산출 검증")
        import os
        test_db = "test_executions.db"
        if os.path.exists(test_db): os.remove(test_db)
        
        from core.persistence import DatabaseManager
        db = DatabaseManager(test_db)
        
        # 매수 2회, 매도 1회 (익절) 데이터 삽입
        db.insert_execution("B1", "005930", 80000, 10, "매수")
        db.insert_execution("B2", "000660", 100000, 5, "매수")
        db.insert_execution("S1", "005930", 84000, 10, "익절") # 4000원 * 10 = 4만원 수익
        
        # 워커 스레드 작업 시간 대기
        import time
        time.sleep(1)
        
        summary = db.get_daily_summary()
        self.assertEqual(summary['buy_count'], 2)
        self.assertEqual(summary['sell_count'], 1)
        self.assertEqual(summary['realized_pnl'], 40000)
        
        db.stop()
        if os.path.exists(test_db): os.remove(test_db)
        print(f"  -> 일일 리포트 요약(수익 40,000원) 산출 확인 완료")


    # ─────────────────────────────────────────────────────────────────
    # CASE 13: 볼린저 밴드 필터링 검증
    # ─────────────────────────────────────────────────────────────────
    def test_bollinger_band_filter(self):
        print("\n[CASE 13] 볼린저 밴드 필터링(하단선 근접 여부) 검증")
        from strategy.stefano_strategy import StefanoStrategy
        from unittest.mock import MagicMock
        import pandas as pd
        strategy = StefanoStrategy()
        
        # 1. 볼린저 밴드 하단에 위치한 데이터 생성 (가격: 90,000 / 하단선: 약 95,000)
        # 억지로 하락 추세 데이터를 만들어 BB 하단 산출 유도
        prices = [120000 - (i * 1000) for i in range(30)] # 12만에서 9만까지 하락
        df_1m = pd.DataFrame({'close': prices, 'volume': [1000]*30})
        df_5m = df_1m.copy() # 5분봉도 동일하게 설정
        
        # 하이브리드 분석 (내부 지표 계산 포함)
        df_1m_processed = strategy._calculate_indicators(df_1m)
        bb_lower = df_1m_processed['BB_Lower'].iloc[-1]
        last_price = df_1m_processed['close'].iloc[-1]
        
        print(f"  -> 현재가: {last_price}, BB하단: {bb_lower:.0f}")
        
        # 2. 다이버전스 강제 발생 모사 (다이버전스 함수가 True를 반환하도록 Mocking 하거나 데이터 정밀 설계)
        # 여기서는 로직 흐름상 BB 필터 단계까지 도달하는지 확인하기 위해 analyze 핵심 로직 확인
        strategy.macro_states["TEST_BB"] = True # 거시 상태 강제 활성화
        
        # (A) 가격이 BB 하단보다 낮을 때 (통과 케이스)
        # 하단선보다 확실히 낮은 가격(80,000)을 마지막에 추가
        prices_low = prices + [80000]
        df_1m_low = pd.DataFrame({'close': prices_low, 'volume': [1000]*31})
        
        with MagicMock() as mock_div:
            # _check_bullish_divergence가 True를 반환한다고 가정
            strategy._check_bullish_divergence = MagicMock(return_value=True)
            result = strategy.analyze("TEST_BB", df_1m_low, df_5m)
            self.assertTrue(result, "가격이 BB 하단(약 8.8만)보다 낮은 8만 원이므로 매수 시그널이 발생해야 함")
            
        # (B) 가격이 BB 상단에 있을 때 (차단 케이스)
        # 가격을 급등시켜 밴드 상단으로 보냄
        high_prices = [100000 + (i * 5000) for i in range(30)]
        df_high = pd.DataFrame({'close': high_prices, 'volume': [1000]*30})
        strategy.macro_states["TEST_BB"] = True
        result_high = strategy.analyze("TEST_BB", df_high, df_5m)
        self.assertFalse(result_high, "BB 하단에서 멀어지면 매수가 차단되어야 함")
        
        print(f"  -> BB 하단 필터링 동작 확인 완료")

if __name__ == "__main__":
    unittest.main(verbosity=2)
