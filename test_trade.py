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
        self.assertEqual(manager.positions["005930"]["qty"], 1)
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

        self.assertEqual(manager.positions["005930"]["qty"], 1, "중복 매수 차단 실패!")
        print(f"  → qty: {manager.positions['005930']['qty']} (1이어야 함)")

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
    # CASE 8: 체결 완료 시 pending에서 정상 제거 검증
    # ─────────────────────────────────────────────────────────────────
    def test_record_execution_removes_pending(self):
        print("\n[CASE 8] 체결 완료 시 pending_orders 정상 제거 검증")
        import time
        manager, _, _ = self._make_manager()

        # pending에 매수 주문 등록
        manager.pending_orders["BUY_005930"] = {
            'code': '005930', 'qty': 1, 'order_type': '매수',
            'sent_at': time.time(), 'screen_no': '1001'
        }
        self.assertIn("BUY_005930", manager.pending_orders)

        # 체결 이벤트 발생
        manager.record_execution("ORD001", "005930", 80000, 1, "체결완료", "+매수")

        # pending에서 제거됐는지 확인
        self.assertNotIn("BUY_005930", manager.pending_orders, "체결 후에도 pending에 남아있으면 안 됨!")
        print(f"  → 체결 확인 후 pending 정상 제거 완료. pending: {manager.pending_orders}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
