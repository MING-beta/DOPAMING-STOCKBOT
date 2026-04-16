"""
Dopaming-Stock-Bot 메인 엔트리포인트 (Main Entrypoint)
---------------------------------------------------
애플리케이션의 시작점입니다. 환경변수를 로드하고, 로깅 시스템을 초기화하며,
KiwoomCore, OpenAPI 통신, 데이터 파이프라인, 전략 매니저 및 GUI(대시보드)를 조립(DI)하여 구동합니다.
"""

import sys
import os
from datetime import datetime
from dotenv import load_dotenv
from PyQt5.QtWidgets import QApplication
from core.kiwoom_core import KiwoomCore
from core.data_pipeline import DataPipeline
from strategy.stefano_strategy import StefanoStrategy
from core.execution_manager import ExecutionManager
from core.persistence import DatabaseManager
from utils.notifier import SlackNotifier
from utils.logger import setup_logger, add_gui_logger
from ui.dashboard import Dashboard
from utils.report_generator import ReportGenerator
from PyQt5.QtCore import QTimer
from concurrent.futures import ThreadPoolExecutor
from core.data_collector import DataCollector
from core.ai_engine import AIEngine

def main():
    """
    메인 애플리케이션 초기화 및 이벤트 루프 진입 메서드
    """
    load_dotenv()
    
    # 1. 로깅 시스템 초기화
    logger = setup_logger("DopamingBot")
    logger.info("====================================")
    logger.info("시스템 시작: Dopaming-Stock-Bot")
    logger.info("====================================")

    # 전역 예외 처리기 (PyQt5 슬롯 내부 예외 추적 및 Fast Fail 방지)
    import traceback
    def unhandled_exception_hook(exc_type, exc_value, exc_traceback):
        logger.critical("❌ [치명적 오류] 처리되지 않은 예외 발생으로 종료 전 기록:")
        logger.critical("".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))
    sys.excepthook = unhandled_exception_hook

    # 2. QApplication 객체 생성 (PyQt5 GUI, 이벤트 루프 필수)
    app = QApplication(sys.argv)
    logger.debug("QApplication 인스턴스 생성 완료")
    
    try:
        # 3. KiwoomCore 싱글톤 인스턴스 가져오기
        kiwoom = KiwoomCore.get_instance()
        
        # 4. 데이터 파이프라인 생성 및 시작, Kiwoom 코어에 연결
        pipeline = DataPipeline()
        pipeline.start_pipeline()
        kiwoom.set_data_pipeline(pipeline)
        
        # 6. 알림 및 영속성(DB) 시스템 초기화
        slack = SlackNotifier()
        db = DatabaseManager()
        
        # 환경변수 로드 및 검증
        is_dry_run = os.getenv("DRY_RUN_MODE", "True").lower() in ("true", "1", "yes")
        account_password = os.getenv("ACCOUNT_PASSWORD", "").strip()
        
        if not account_password and not getattr(kiwoom, 'is_mock', False):
            error_msg = "🔥 [치명적 오류] 계좌 비밀번호(ACCOUNT_PASSWORD)가 누락되어 초기 동기화가 불가능합니다. 시스템을 강제 종료합니다."
            logger.error(error_msg)
            slack.send_message(error_msg)
            sys.exit(1)
        elif not account_password and getattr(kiwoom, 'is_mock', False):
            logger.warning("가상(Mock) 환경이므로 계좌 비밀번호 검사를 무시합니다.")
            account_password = "0000"
            
        # 7. 실행 제어 매니저 초기화 및 코어에 연동
        execution_manager = ExecutionManager(kiwoom, db, slack, is_dry_run=is_dry_run)
        kiwoom.set_execution_manager(execution_manager)
        
        # 8. 전략 엔진 초기화 및 지표 연산 등록 (백그라운드 최적화)
        strategy = StefanoStrategy()
        pipeline.register_indicator_callback(strategy._calculate_indicators)

        # 8-1. [AI 모듈] 데이터 수집기 + AI 추론 엔진 초기화 후 전략에 주입
        data_collector = DataCollector()
        ai_engine      = AIEngine()
        strategy.set_ai_modules(ai_engine, data_collector)
        # GC 방지를 위해 최상위 객체(kiwoom)에 앵커링
        kiwoom._ai_engine_anchor      = ai_engine
        kiwoom._data_collector_anchor = data_collector
        logger.info("🤖 AI 모듈 초기화 완료: DataCollector + AIEngine → Strategy 주입")

        # 9. GUI 대시보드 화면 생성 & 로깅 신호 연결
        dashboard = Dashboard(kiwoom, pipeline, execution_manager, strategy)
        gui_handler = add_gui_logger("DopamingBot")
        gui_handler.signal.new_log.connect(dashboard.append_log)
        
        # 10. 대시보드 화면 표출 (가시화 및 핸들 확보)
        dashboard.show()
        
        # 11. 로그인 요청 및 대기 (Dashboard 가시화 후 실행하여 핸들 오류 방지)
        logger.info("🔑 키움 OpenAPI+ 로그인 요청...")
        kiwoom.comm_connect()
        
        # 11. 계좌 동기화 통신 시작 (동기화 완료 응답 시 자동으로 get_condition_load 호출 연계됨)
        logger.info("내 계좌 예수금 및 잔고 동기화 절차 시작...")
        kiwoom.request_account_info(account_password)
        kiwoom.request_daily_pnl() # [추가] 오늘 전체 실현손익 서버 동기화
        
        # 🔔 [보유 종목 초기 동기화] DB에서 로드된 기존 보유 종목들에 대해 실시간 데이터 및 차트 등록
        existing_positions = list(execution_manager.positions.keys())
        if existing_positions:
            logger.info(f"기본 보유 종목({len(existing_positions)}건) 실시간 및 과거 데이터 등록 시작...")
            # 실시간 수신 등록 (FID: 10-현재가, 15-거래량, 20-체결시간)
            kiwoom.set_real_reg("1000", existing_positions, "10;15;20", "1")
            for code in existing_positions:
                kiwoom.get_master_code_name(code) # [성능 최적화] 보유 종목명 선제적 캐싱
                kiwoom.request_opt10080(code)
                logger.debug(f"[{code}] 보유 종목 실시간/차트 싱크 등록 완료")
        
        # 11-1. 폴백(Fallback) 타이머: 15초 후에도 조건검색 로드가 안 됐다면 강제 실행
        # (계좌 비밀번호 미등록 등으로 opw00018 응답이 없을 때를 대비)
        kiwoom._condition_loaded = False
        def _fallback_condition_load():
            if not kiwoom._condition_loaded:
                logger.warning("⚠️ [폴백] 계좌 동기화 응답 없음 → 조건검색식 직접 로드 시도")
                kiwoom.get_condition_load()
        fallback_timer = QTimer()
        fallback_timer.setSingleShot(True)
        fallback_timer.timeout.connect(_fallback_condition_load)
        fallback_timer.start(15000)  # 15초 후 1회 실행
        kiwoom._fallback_timer = fallback_timer  # 참조 유지 (GC 방지)
        
        # 병렬 연산을 위한 단일 전용 워커 스레드 (UI와 연산 분리)
        executor_pool = ThreadPoolExecutor(max_workers=1)
        is_analyzing = False # 분석 중복 실행 방지 플래그

        def evaluate_strategy_and_positions():
            nonlocal is_analyzing
            
            # [장 운영 시간 가드] 09:00 ~ 15:30 이외의 시간은 모든 매매 연산을 중단합니다.
            now = datetime.now()
            if now.hour < 9 or (now.hour >= 15 and now.minute > 30) or now.hour >= 16:
                return

            # 2. 매매 로직 집행 (백그라운드 스레드 위임)
            if not execution_manager.is_risk_halt and not is_analyzing:
                def background_worker():
                    nonlocal is_analyzing
                    is_analyzing = True
                    try:
                        # [P1] 포지션 모니터링 및 미체결 감시 (백그라운드 위임하여 UI 랙 방지)
                        execution_manager.monitor_positions(pipeline)
                        execution_manager.monitor_pending_orders()
                        
                        # [P2] 신규 진입 매수 시그널 탐색 (배치 데이터 활용)
                        with pipeline.lock:
                            codes = list(pipeline.data_1m.keys())
                        
                        # 일괄 데이터 획득 (락 점유 시간 최소화)
                        all_data = pipeline.batch_get_data(codes)
                        
                        for code, (df_1m, df_5m) in all_data.items():
                            is_signal, signal_type = strategy.analyze(code, df_1m, df_5m)
                            if is_signal:
                                # 매수 실행 (내부적으로 Throttler 큐를 사용하므로 스레드 안전)
                                execution_manager.execute_buy(code, pipeline, signal_type=signal_type)
                    except Exception as e:
                        logger.error(f"⚠️ [백그라운드 워커] 예외 발생: {e}")
                    finally:
                        is_analyzing = False

                # 백그라운드 워커 실행
                executor_pool.submit(background_worker)
                    
        timer = QTimer()
        timer.timeout.connect(evaluate_strategy_and_positions)
        timer.start(1000) # [성능 최적화] 1초 주기 (키움 틱 지연 200~300ms 감안 시 충분한 반응속도)

        # (4) [데이터 보정] 과거 차트 누락 종목 자동 재동기화 (2분 주기)
        def repair_data_continuity():
            if execution_manager.is_risk_halt: return
            
            with pipeline.lock:
                # 현재 파이프라인에 등록된 모든 종목 체크
                codes = list(pipeline.data_1m.keys())
            
            for code in codes:
                df_1m, _ = pipeline.get_data(code)
                # 데이터가 100개 미만이면 과거 차트(900개) 동기화가 실패한 것으로 간주하여 재요청
                # (1~2분 정도 흘러도 캔들이 100개가 될 수 없으므로 확실한 누락 징후)
                if not df_1m.empty and len(df_1m) < 100:
                    logger.warning(f"🔍 [데이터 복구] {code} 종목의 과거 차트 누락 또는 부족 감지 ({len(df_1m)}개) -> 재동기화 요청")
                    kiwoom.request_opt10080(code)
        
        repair_timer = QTimer()
        repair_timer.timeout.connect(repair_data_continuity)
        repair_timer.start(120000) # 2분(120,000ms)마다 스캔
        kiwoom._repair_timer = repair_timer


        # 13. 슬랙 푸시 알림: 시작, 종료 및 헬스체크 설정
        # (1) 시작 알림
        slack.send_message("🚀 *알림*: `Dopaming-Stock-Bot` 시스템 구동이 시작되었습니다. (정상 작동 중)")

        # (2) 앱 종료 알림 (동기 통신)
        def on_app_quit():
            msg = "🛑 *알림*: `Dopaming-Stock-Bot` 시스템이 종료되었습니다."
            logger.info(msg)
            slack.send_message_sync(msg)
        app.aboutToQuit.connect(on_app_quit)

        # (3) 1분 헬스체크 (통신 장애 확인 및 시간별 스케줄 알림)
        notification_flags = {"market_open": False, "market_close": False}
        reconnect_attempts = 0
        MAX_RECONNECT_ATTEMPTS = int(os.getenv("RECONNECT_LIMIT", "5"))
        
        def health_check():
            nonlocal reconnect_attempts
            # [A] 서버 접속 상태 체크
            state = kiwoom.get_connect_state()
            if state == 0:
                reconnect_attempts += 1
                error_msg = f"🚨 *[헬스체크 경고]* 키움 API 서버와 통신이 끊어졌습니다. (시도 {reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS})"
                logger.error(error_msg)
                slack.send_message(error_msg)
                
                if reconnect_attempts <= MAX_RECONNECT_ATTEMPTS:
                    # 재연결 시도
                    kiwoom.reconnect()
                    # 재연결 직후 상태 확인
                    if kiwoom.get_connect_state() == 1:
                        success_msg = "✅ *[재연결 성공]* 서버와 다시 연결되었습니다. 매매를 재개합니다."
                        logger.info(success_msg)
                        slack.send_message(success_msg)
                        reconnect_attempts = 0 # 횟수 초기화
                else:
                    fatal_msg = "🛑 *[치명적 에러]* 최대 재연결 시도 횟수를 초과했습니다. 시스템을 안전하게 종료합니다."
                    logger.critical(fatal_msg)
                    slack.send_message(fatal_msg)
                    app.quit()
            else:
                # 연결 상태 정상이면 횟수 초기화
                reconnect_attempts = 0
                
            # [B] 지정 시간(장시작/종료) 슬랙 알림
            now_str = datetime.now().strftime("%H:%M")
            if now_str == "09:00" and not notification_flags["market_open"]:
                open_msg = "🌅 *[장 시작 알림]* 09:00 정규장 매매를 시작합니다. 오늘도 성투하세요!"
                logger.info(open_msg)
                slack.send_message(open_msg)
                notification_flags["market_open"] = True
                
            elif now_str >= "15:30" and not notification_flags["market_close"]:
                # [장 종료 클리닝] 미체결 및 요청 큐 강제 정리
                logger.info("🏁 15:30 장 종료 감지. 일일 리포트 생성을 시작합니다.")
                kiwoom.throttler.clear_queue()
                execution_manager.clear_pending_orders()

                # [고도화된 일일 수익 리포트 생성]
                summary = db.get_daily_summary()
                
                # 종목코드 -> 이름 맵 생성
                code_map = {}
                for code in summary['stock_details'].keys():
                    name = kiwoom.dynamicCall("GetMasterCodeName(QString)", code)
                    code_map[code] = name.strip() if hasattr(name, 'strip') else code
                
                # [고도화] 서버 공식 실현손익 데이터 반영
                if kiwoom.official_daily_pnl != 0:
                    summary['realized_pnl'] = kiwoom.official_daily_pnl
                    logger.info(f"✅ 리포트 생성 시 서버 공식 수익금({kiwoom.official_daily_pnl:,}원)을 적용합니다.")
                else:
                    logger.warning("⚠️ 서버 공식 실현손익 데이터가 없어 DB 추정치를 사용합니다.")

                # 리포트 생성
                report_msg = ReportGenerator.generate_markdown_report(
                    summary, 
                    kiwoom.initial_total_assets, 
                    code_to_name_map=code_map
                )
                
                logger.info("장 종료 고도화 리포트 발송")
                slack.send_message(report_msg)
                notification_flags["market_close"] = True

                # [AI 재학습] 장 종료 후 당일 데이터를 학습하여 모델 자동 업데이트
                logger.info("🤖 AI 모델 일일 재학습 시작 (백그라운드)...")
                ai_engine.train_model()
                
            # 자정(00:00)에 다음날을 위해 플래그 초기화
            elif now_str == "00:00":
                notification_flags["market_open"] = False
                notification_flags["market_close"] = False
                
        health_timer = QTimer()
        health_timer.timeout.connect(health_check)
        health_timer.start(60000) # 1분(60,000ms)마다 실행
        # 가비지 컬렉션 방지 유지
        kiwoom._health_timer = health_timer

    except Exception as e:
        logger.error(f"시스템 실행 중 예외 발생: {e}", exc_info=True)

    # 13. 메인 이벤트 루프 실행 (프로그램 종료 방지 및 GUI, 통신 폴링 지원)
    logger.info("QApplication 메인 이벤트 루프 진입")
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
