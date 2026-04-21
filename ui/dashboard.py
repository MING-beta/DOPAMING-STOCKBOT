import sys
import os
import dotenv
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QTextEdit, QTableWidget, QTableWidgetItem, QLabel, QHeaderView,
    QGroupBox, QGridLayout, QFrame, QPushButton, QDoubleSpinBox, QMessageBox
)
from PyQt5.QtCore import QTimer, Qt, QSize
from PyQt5.QtGui import QColor, QFont, QPalette, QBrush

"""
Dashboard 2.0 Premium Edition
-------------------------------
차분하고 고급스러운 하이엔드 거래 위주 레이아웃입니다.
핵심 지표의 가시성을 극대화하고, 데이터 계층화를 통해 피로도를 줄였습니다.
"""

class Dashboard(QMainWindow):
    def __init__(self, kiwoom, pipeline, execution_manager, strategy):
        super().__init__()
        self.kiwoom = kiwoom
        self.pipeline = pipeline
        self.execution_manager = execution_manager
        self.strategy = strategy
        self.code_names = {} 
        self._delisted_cache = {}  # {code: bool} 상장폐지 종목 캐시 (API 호출 최소화)
        
        # [Hot-Reload] .env 파일 경로 및 초기 수정 시간 캐싱
        self.env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
        self.last_env_mtime = os.path.getmtime(self.env_file) if os.path.exists(self.env_file) else 0
        
        self.init_ui()
        self.apply_dark_theme()
        
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_dashboard)
        self.update_timer.start(2000) # [성능 최적화] 업데이트 주기 1초 -> 2초로 완화하여 UI 부하 감소

    def init_ui(self):
        self.setWindowTitle("DOPAMING STOCK BOT v2.0 - Premium Edition")
        self.setMinimumSize(1280, 900)

        # 메인 컨테이너
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        outer_layout = QVBoxLayout(central_widget)
        outer_layout.setContentsMargins(20, 20, 20, 20)
        outer_layout.setSpacing(16)

        # ─────────── [1] 상단: 메인 콘텐츠 영역 (중앙 & 사이드) ───────────
        content_layout = QHBoxLayout()
        content_layout.setSpacing(20)

        # [1-1] 좌측/중앙: 매매 메인 스택 (종목 리스트 & 포지션)
        main_stack = QVBoxLayout()
        main_stack.setSpacing(16)

        # (A) 감시 종목 센터
        watch_card = QFrame()
        watch_card.setObjectName("Card")
        watch_vbox = QVBoxLayout(watch_card)
        
        watch_header = QHBoxLayout()
        watch_title = QLabel("🔍 실시간 종목 감시")
        watch_title.setFont(QFont("Apple SD Gothic Neo", 14, QFont.Bold))
        watch_header.addWidget(watch_title)
        watch_vbox.addLayout(watch_header)

        self.watch_table = QTableWidget(0, 7)
        self.watch_table.setHorizontalHeaderLabels([
            "종목명", "현재가 (등락)", "RSI 신호", "수급(억)", "BB 위치", "당일 위치", "전략 상태"
        ])
        self.watch_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.watch_table.setColumnWidth(0, 180) # 종목명 컬럼 너비 확장
        self.watch_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.watch_table.verticalHeader().setVisible(False)
        self.watch_table.setShowGrid(False)
        watch_vbox.addWidget(self.watch_table)
        
        main_stack.addWidget(watch_card, stretch=3)

        # (B) 오픈 포지션 센터
        pos_card = QFrame()
        pos_card.setObjectName("Card")
        pos_vbox = QVBoxLayout(pos_card)
        
        pos_title = QLabel("📦 보유 종목 현황")
        pos_title.setFont(QFont("Apple SD Gothic Neo", 14, QFont.Bold))
        pos_vbox.addWidget(pos_title)

        self.pos_table = QTableWidget(0, 8)
        self.pos_table.setColumnCount(8)
        self.pos_table.setHorizontalHeaderLabels([
            "종목명", "매입가", "수익률", "평가손익", "매입금액", 
            "현재가", "등락률", "진입 전략"
        ])
        self.pos_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.pos_table.setColumnWidth(0, 220) # 종목명 컬럼 너비 조금 더 여유있게 확장
        self.pos_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.pos_table.verticalHeader().setVisible(False)
        self.pos_table.setShowGrid(False)
        pos_vbox.addWidget(self.pos_table)
        
        main_stack.addWidget(pos_card, stretch=2)

        content_layout.addLayout(main_stack, stretch=7)

        # [1-2] 우측: 요약 & 설정 패널 (Portfolio Summary)
        side_panel = QVBoxLayout()
        side_panel.setSpacing(16)

        # (C) 자산 통합 관리 카드 (Portfolio Summary)
        summary_card = QFrame()
        summary_card.setObjectName("SummaryCard")
        summary_vbox = QVBoxLayout(summary_card)
        summary_vbox.setContentsMargins(24, 24, 24, 24)
        summary_vbox.setSpacing(12)
        
        sum_title = QLabel("💰 총 평가 자산 현황")
        sum_title.setStyleSheet("color: #ADB5BD; font-size: 13px; font-weight: bold; letter-spacing: 1px;")
        
        self.cash_value_label = QLabel("로딩 중...")
        self.cash_value_label.setStyleSheet("font-size: 36px; font-weight: 900; color: #FFFFFF; font-family: 'Verdana', sans-serif;")
        
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: rgba(255, 255, 255, 0.1); border: none; height: 1px;")

        # 당일 실현 손익을 감싸는 반투명 둥근 박스 패널
        pnl_container = QFrame()
        pnl_container.setObjectName("PnlContainer")
        pnl_container.setStyleSheet("QFrame#PnlContainer { background-color: rgba(255, 255, 255, 0.05); border-radius: 12px; }")
        pnl_layout = QVBoxLayout(pnl_container)
        pnl_layout.setContentsMargins(16, 16, 16, 16)
        pnl_layout.setSpacing(6)
        
        self.daily_pnl_label = QLabel("당일 실현 손익: 0원")
        self.daily_pnl_label.setFont(QFont("Apple SD Gothic Neo", 16, QFont.Bold))
        self.daily_pnl_label.setAlignment(Qt.AlignCenter)
        self.daily_pnl_label.setStyleSheet("background-color: transparent;")
        
        self.risk_limit_label = QLabel("손실 제한 한도: -")
        self.risk_limit_label.setStyleSheet("color: #F28B82; font-size: 11px; background-color: transparent;")
        self.risk_limit_label.setAlignment(Qt.AlignCenter)
        
        pnl_layout.addWidget(self.daily_pnl_label)
        pnl_layout.addWidget(self.risk_limit_label)
        
        summary_vbox.addWidget(sum_title)
        summary_vbox.addWidget(self.cash_value_label)
        summary_vbox.addSpacing(8)
        summary_vbox.addWidget(line)
        summary_vbox.addSpacing(6)
        summary_vbox.addWidget(pnl_container)
        
        side_panel.addWidget(summary_card, stretch=10)

        # (D) 운영 설정 정보 카드
        config_card = QFrame()
        config_card.setObjectName("Card")
        config_vbox = QVBoxLayout(config_card)
        config_vbox.setSpacing(12)
        
        config_title = QLabel("⚙️ 운영 설정 센터")
        config_title.setFont(QFont("Apple SD Gothic Neo", 13, QFont.Bold))
        config_vbox.addWidget(config_title)
        
        # [수정] 중복 제거 및 중요 지표 추가된 정보 라벨 (읽기 전용)
        self.lbl_trade_mode = QLabel("매매 모드: -")
        self.lbl_risk_rate = QLabel("투자 비중: -")
        self.lbl_indicators = QLabel("보조지표: -")
        self.lbl_master_settings = QLabel("마스터: -")
        
        for lbl in [self.lbl_trade_mode, self.lbl_risk_rate, self.lbl_indicators, self.lbl_master_settings]:
            lbl.setStyleSheet("color: #ADB5BD; font-size: 12px;")
            config_vbox.addWidget(lbl)
            
        line2 = QFrame()
        line2.setFrameShape(QFrame.HLine)
        line2.setStyleSheet("background-color: rgba(255, 255, 255, 0.1); border: none; height: 1px;")
        config_vbox.addWidget(line2)

        control_label = QLabel("🎛️ 실시간 파라미터 제어")
        control_label.setStyleSheet("color: #FFFFFF; font-size: 11px; font-weight: bold;")
        config_vbox.addWidget(control_label)
        
        # [신규 수정] 인터랙티브 컨트롤 패널 (그리드 정렬)
        control_grid = QGridLayout()
        control_grid.setSpacing(8)
        
        def make_spinbox(val, min_v, max_v, step):
            sb = QDoubleSpinBox()
            sb.setRange(min_v, max_v)
            sb.setSingleStep(step)
            sb.setValue(val)
            sb.setStyleSheet("background-color: #202124; color: #E8EAED; border: 1px solid #3C4043; border-radius: 4px; padding: 4px; font-weight: bold;")
            return sb

        lbl_target = QLabel("익절(+)")
        lbl_target.setStyleSheet("color: #FF8A80; font-size: 11px;")
        self.spin_target = make_spinbox(self.execution_manager.TARGET_PROFIT * 100.0, 0.1, 30.0, 0.1)
        self.spin_target.valueChanged.connect(self.on_target_changed)
        
        lbl_stop = QLabel("손절(-)")
        lbl_stop.setStyleSheet("color: #92B9F9; font-size: 11px;")
        self.spin_stop = make_spinbox(self.execution_manager.STOP_LOSS * 100.0, -30.0, -0.1, 0.1)
        self.spin_stop.valueChanged.connect(self.on_stop_changed)

        lbl_trail = QLabel("트레일링 시작")
        lbl_trail.setStyleSheet("color: #A5D6A7; font-size: 11px;")
        self.spin_trail = make_spinbox(self.execution_manager.TRAILING_STOP_ACTIVATION * 100.0, 0.1, 10.0, 0.1)
        self.spin_trail.valueChanged.connect(self.on_trail_changed)
        
        lbl_ai = QLabel("AI 임계치")
        lbl_ai.setStyleSheet("color: #FADB14; font-size: 11px;")
        self.spin_ai = make_spinbox(float(os.getenv("AI_THRESHOLD", "0.38")), 0.0, 1.0, 0.01)
        self.spin_ai.valueChanged.connect(self.on_ai_changed)

        control_grid.addWidget(lbl_target, 0, 0)
        control_grid.addWidget(self.spin_target, 0, 1)
        control_grid.addWidget(lbl_stop, 0, 2)
        control_grid.addWidget(self.spin_stop, 0, 3)
        
        control_grid.addWidget(lbl_trail, 1, 0)
        control_grid.addWidget(self.spin_trail, 1, 1)
        control_grid.addWidget(lbl_ai, 1, 2)
        control_grid.addWidget(self.spin_ai, 1, 3)
        
        config_vbox.addLayout(control_grid)
        config_vbox.addSpacing(10)
        
        panic_vbox = QVBoxLayout()
        self.lbl_panic_pnl = QLabel("보유 종목 총 손익: 계산 중...")
        self.lbl_panic_pnl.setStyleSheet("color: #F28B82; font-size: 12px; font-weight: bold;")
        self.lbl_panic_pnl.setAlignment(Qt.AlignCenter)
        
        self.btn_panic = QPushButton("🚨 긴급 투매 & 매수 중단")
        self.btn_panic.setFixedHeight(35)
        self.btn_panic.setStyleSheet("QPushButton { background-color: #D93025; color: white; border-radius: 6px; font-weight: bold; font-size: 13px; } QPushButton:hover { background-color: #EA4335; }")
        self.btn_panic.setCursor(Qt.PointingHandCursor)
        self.btn_panic.clicked.connect(self.toggle_panic)
        
        panic_vbox.addWidget(self.lbl_panic_pnl)
        panic_vbox.addWidget(self.btn_panic)
        config_vbox.addLayout(panic_vbox)
        side_panel.addWidget(config_card, stretch=15)

        # (E) 데몬 워커 상태 컨트롤 센터
        daemon_card = QFrame()
        daemon_card.setObjectName("Card")
        daemon_vbox = QVBoxLayout(daemon_card)
        daemon_vbox.setContentsMargins(20, 16, 20, 16)
        daemon_vbox.setSpacing(6)
        
        daemon_title = QLabel("🤖 데몬 워커 센터")
        daemon_title.setFont(QFont("Apple SD Gothic Neo", 12, QFont.Bold))
        
        self.lbl_daemon_status = QLabel("상태: 확인 중...")
        self.lbl_daemon_status.setStyleSheet("color: #ADB5BD; font-size: 12px;")
        
        self.btn_daemon = QPushButton("▶ 데몬 런처 가동")
        self.btn_daemon.setFixedHeight(35)
        self.btn_daemon.setStyleSheet("QPushButton { background-color: #1E8E3E; color: white; border-radius: 6px; font-weight: bold; font-size: 13px; } QPushButton:hover { background-color: #188038; }")
        self.btn_daemon.setCursor(Qt.PointingHandCursor)
        self.btn_daemon.clicked.connect(self.start_daemon_worker)

        # [AUTO TUNE TOGGLE] #
        self.btn_auto_tune = QPushButton("🤖 AUTO 모드: ON (데몬 자동 세팅)")
        self.btn_auto_tune.setFixedHeight(35)
        self.btn_auto_tune.setCheckable(True)
        self.btn_auto_tune.setCursor(Qt.PointingHandCursor)
        is_auto = os.getenv("AUTO_OPTIMIZE_MODE", "True").lower() == 'true'
        self.btn_auto_tune.setChecked(is_auto)
        self._update_auto_tune_btn_style(is_auto)
        self.btn_auto_tune.toggled.connect(self.toggle_auto_tune)
        
        # [수정] 두 버튼을 HBox 로 묶어서 같은 줄에 절반씩 배치
        daemon_btns_layout = QHBoxLayout()
        daemon_btns_layout.addWidget(self.btn_daemon)
        daemon_btns_layout.addWidget(self.btn_auto_tune)
        
        daemon_vbox.addWidget(daemon_title)
        daemon_vbox.addWidget(self.lbl_daemon_status)
        daemon_vbox.addLayout(daemon_btns_layout)
        
        side_panel.addWidget(daemon_card, stretch=6)

        content_layout.addLayout(side_panel, stretch=3)
        outer_layout.addLayout(content_layout, stretch=8)

        # ─────────── [2] 하단: 시스템 콘솔 (Log) ───────────
        log_card = QFrame()
        log_card.setObjectName("Console")
        log_vbox = QVBoxLayout(log_card)
        log_vbox.setContentsMargins(12, 12, 12, 12)
        
        log_header = QHBoxLayout()
        log_title = QLabel("🖥️ 시스템 실시간 콘솔")
        log_title.setStyleSheet("color: #666666; font-size: 10px; font-weight: bold;")
        log_header.addWidget(log_title)
        log_vbox.addLayout(log_header)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 10))
        self.log_text.setStyleSheet("background-color: transparent; border: none; color: #888888;")
        log_vbox.addWidget(self.log_text)
        
        outer_layout.addWidget(log_card, stretch=2)

    def apply_dark_theme(self):
        """부드러운 파스텔 톤과 자연스러운 그라데이션이 적용된 하이엔드 다크 테마"""
        css = """
        QMainWindow, QWidget {
            background-color: #0F1012; 
            color: #E8EAED;
            font-family: 'Apple SD Gothic Neo', 'Pretendard', 'sans-serif';
        }
        QFrame#Card {
            background-color: #1A1C1E; 
            border-radius: 24px; /* 더 둥글고 부드러운 모서리 */
            border: 1px solid #282A2D;
        }
        QFrame#SummaryCard {
            background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1A1C1E, stop:1 #222529);
            border-radius: 28px;
            border: 1px solid #3C4043;
        }
        QFrame#Console {
            background-color: #0B0C0D;
            border-top: 1px solid #202124;
            border-radius: 0px;
        }
        QLabel {
            background-color: transparent; 
            color: #BDC1C6;
            border: none;
        }
        QTableWidget {
            background-color: transparent;
            color: #BDC1C6;
            gridline-color: transparent;
            border: none;
            outline: none;
            selection-background-color: rgba(138, 180, 248, 0.1);
        }
        /* [중요] 헤더 자체와 코너 버튼 배경을 투명하게 하여 짜투리 색상 제거 */
        QHeaderView, QTableCornerButton::section {
            background-color: transparent;
            border: none;
        }
        QHeaderView::section {
            background-color: #202124; 
            color: #80868B; 
            font-size: 11px;
            font-weight: bold;
            padding: 16px;
            border: none;
            border-bottom: 2px solid #1A1C1E;
        }
        /* 테이블 헤더의 양 끝을 부드럽게 깎음 */
        QHeaderView::section:horizontal:first {
            border-top-left-radius: 16px;
            border-bottom-left-radius: 4px;
        }
        QHeaderView::section:horizontal:last {
            border-top-right-radius: 16px;
            border-bottom-right-radius: 4px;
        }
        QTableWidget::item {
            padding: 16px;
            border-bottom: 1px solid #141517;
        }
        /* [스크롤바 현대화] */
        QScrollBar:vertical {
            border: none;
            background: transparent;
            width: 6px; /* 더 얇게 */
            margin: 0px 2px 0px 2px;
        }
        QScrollBar::handle:vertical {
            background: #3C4043;
            min-height: 30px;
            border-radius: 3px; /* 둥근 캡슐 형태 */
        }
        QScrollBar::handle:vertical:hover {
            background: #4A4D50; /* 호버 시 약간 강조 */
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            border: none;
            background: none;
        }
        QTextEdit {
            background-color: transparent;
            border: none;
            color: #9AA0A6;
        }
        """
        self.setStyleSheet(css)

    def append_log(self, msg):
        import html
        msg_escaped = html.escape(msg)
        color = "#9AA0A6" # 기본 색상 (소프트 그레이)
        
        # 키워드별 파스텔 하이라이트 적용
        if "ERROR" in msg_escaped or "실패" in msg_escaped: 
            color = "#FFAB91" # 파스텔 레드 (오류)
        elif "WARNING" in msg_escaped or "경고" in msg_escaped: 
            color = "#FFE082" # 파스텔 옐로우 (경고)
        elif "매수" in msg_escaped or "체결" in msg_escaped or "진입" in msg_escaped: 
            color = "#8AB4F8" # 파스텔 블루 (매수/진입)
        elif "익절" in msg_escaped or "매도" in msg_escaped or "청산" in msg_escaped: 
            color = "#FF8A80" # 파스텔 핑크 (매도/수익)
        elif "시스템" in msg_escaped or "로그인" in msg_escaped or "완료" in msg_escaped: 
            color = "#A5D6A7" # 파스텔 그린 (시스템/성공)
        elif "스토틀링" in msg_escaped or "요청" in msg_escaped:
            color = "#80868B" # 어두운 그레이 (흐름 데이터)
        
        # 스타일 적용된 메시지 생성
        styled_msg = f"<span style='color: {color};'>{msg_escaped}</span>"
        self.log_text.append(styled_msg)
        
        # [성능 최적화] 로그 라인 수 제한 (500라인 이상일 경우 상단 비우기)
        if self.log_text.document().blockCount() > 500:
            cursor = self.log_text.textCursor()
            cursor.movePosition(cursor.Start)
            cursor.select(cursor.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar() # 줄바꿈 제거

        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    def on_target_changed(self, value):
        self.execution_manager.TARGET_PROFIT = value / 100.0
        env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
        dotenv.set_key(env_file, "TRADE_TARGET_PROFIT", str(round(value / 100.0, 3)))
        if hasattr(self.execution_manager.logger, 'info'):
            self.execution_manager.logger.info(f"✅ 환경 설정 갱신: 목표 익절가가 {value:.2f}%로 영구 저장되었습니다.")

    def on_trail_changed(self, value):
        self.execution_manager.TRAILING_STOP_ACTIVATION = value / 100.0
        env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
        dotenv.set_key(env_file, "TRAILING_STOP_ACTIVATION", str(round(value / 100.0, 3)))
        if hasattr(self.execution_manager.logger, 'info'):
            self.execution_manager.logger.info(f"✅ 환경 설정 갱신: 트레일링 스탑 발동선이 {value:.2f}%로 영구 저장되었습니다.")

    def on_stop_changed(self, value):
        self.execution_manager.STOP_LOSS = value / 100.0
        env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
        dotenv.set_key(env_file, "TRADE_STOP_LOSS", str(round(value / 100.0, 3)))
        if hasattr(self.execution_manager.logger, 'info'):
            self.execution_manager.logger.info(f"✅ 환경 설정 갱신: 손절 커트라인이 {value:.2f}%로 영구 저장되었습니다.")

    def on_ai_changed(self, value):
        os.environ["AI_THRESHOLD"] = str(round(value, 2))
        env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
        dotenv.set_key(env_file, "AI_THRESHOLD", str(round(value, 2)))
        if hasattr(self.execution_manager.logger, 'info'):
            self.execution_manager.logger.info(f"✅ 환경 설정 갱신: AI 저격 임계치가 {value:.2f}로 영구 저장되었습니다.")

    def toggle_panic(self):
        if self.execution_manager.is_risk_halt:
            # 상태 2 -> 1 (매수 재개)
            reply = QMessageBox.question(self, '시스템 재가동', '정말로 매수 중단(Lock)을 풀고 정상 사냥 모드로 복귀하시겠습니까?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.execution_manager.is_risk_halt = False
                self.btn_panic.setText("🚨 긴급 투매 & 매수 중단")
                self.btn_panic.setStyleSheet("background-color: #D93025; color: white; border-radius: 8px; font-weight: bold; font-size: 13px;")
                
                # 시간 체크 알림 (토스트 경고)
                buy_end_time = os.getenv("STRATEGY_BUY_END_TIME", "15:20")
                try:
                    from datetime import datetime
                    end_hour, end_minute = map(int, buy_end_time.split(":"))
                    now = datetime.now()
                    end_dt = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
                    if now >= end_dt:
                        QMessageBox.warning(self, "장 마감 알림", f"봇 설정({buy_end_time} 마감) 상 거래가능 시간이 이미 지났습니다!\n\n잠금(Lock)은 해제되지만, 실질적인 매수는 내일 아침 장이 열려야 재개됩니다.")
                except Exception as e:
                    pass

                self.execution_manager.slack.send_message("▶️ *[시스템 재가동]* 사용자가 매수 정지 락(Lock)을 해제하여 신규 진입을 재개합니다.")
        else:
            # 상태 1 -> 2 (긴급 투매)
            reply = QMessageBox.question(self, '비상 투매 최종 경고', '보유 중인 모든 종목을 [시장가]로 전량 투척합니까?\n확인 시 신규 매수도 영구 정지됩니다.', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.execution_manager.emergency_liquidate_all(self.pipeline)
                self.btn_panic.setText("▶️ 시스템 재가동 (매수 재개)")
                self.btn_panic.setStyleSheet("background-color: #1E8E3E; color: white; border-radius: 8px; font-weight: bold; font-size: 13px;")
    def start_daemon_worker(self):
        import subprocess, sys
        CREATE_NO_WINDOW = 0x08000000
        try:
            # 보이지 않는 창으로 데몬 워커 백그라운드 가동
            subprocess.Popen([sys.executable, "tools/daemon_worker.py"], creationflags=CREATE_NO_WINDOW)
            
            # 즉각적인 UI 반영을 위한 상태 임시 업데이트
            self.lbl_daemon_status.setText("상태: 🟢 ON (시작됨)")
            self.lbl_daemon_status.setStyleSheet("color: #A5D6A7; font-size: 12px; font-weight: bold;")
            self.btn_daemon.setText("가동 중")
            self.btn_daemon.setEnabled(False)
            self.btn_daemon.setStyleSheet("QPushButton { background-color: #3C4043; color: #80868B; border-radius: 6px; font-weight: bold; font-size: 13px; }")
            
            if hasattr(self.execution_manager, 'slack'):
                self.execution_manager.slack.send_message("▶️ *[데몬 구동]* 사용자가 대시보드에서 백그라운드 워커를 무소음 모드로 가동했습니다.")
        except Exception as e:
            QMessageBox.warning(self, "실행 오류", f"데몬 워커 실행에 실패했습니다:\n{e}")

    def _update_auto_tune_btn_style(self, checked):
        if checked:
            self.btn_auto_tune.setText("🤖 AUTO 세팅: ON")
            self.btn_auto_tune.setStyleSheet("QPushButton { background-color: #1a73e8; color: white; border-radius: 6px; font-weight: bold; font-size: 13px; } QPushButton:hover { background-color: #1557b0; }")
        else:
            self.btn_auto_tune.setText("🛠️ MANUAL: OFF")
            self.btn_auto_tune.setStyleSheet("QPushButton { background-color: #5f6368; color: white; border-radius: 6px; font-weight: bold; font-size: 13px; } QPushButton:hover { background-color: #3c4043; }")

    def toggle_auto_tune(self, checked):
        self._update_auto_tune_btn_style(checked)
        import dotenv
        dotenv.set_key(self.env_file, "AUTO_OPTIMIZE_MODE", str(checked))
        
        # 로그에 출력
        print(f"[{'AUTO' if checked else 'MANUAL'} 모드 작동] 야간 파라미터 자동 적용 기능이 {'활성화' if checked else '비활성화'}되었습니다.")
        if hasattr(self.execution_manager, 'slack'):
            self.execution_manager.slack.send_message(f"⚙️ *[시스템 설정]* 야간 데몬 파라미터 자동 최적화 기능이 *{'[ON]' if checked else '[OFF]'}* 되었습니다.")

    def hot_reload_env(self):
        import dotenv
        if not hasattr(self, 'env_file') or not os.path.exists(self.env_file): return
        dotenv.load_dotenv(self.env_file, override=True)
        
        ex = self.execution_manager
        
        new_target = float(os.getenv("TRADE_TARGET_PROFIT", str(ex.TARGET_PROFIT)))
        new_stop = float(os.getenv("TRADE_STOP_LOSS", str(ex.STOP_LOSS)))
        new_trail = float(os.getenv("TRAILING_STOP_ACTIVATION", str(ex.TRAILING_STOP_ACTIVATION)))
        new_ai = float(os.getenv("AI_THRESHOLD", "0.38"))
        new_hfs = float(os.getenv("HFS_GOLDEN_RATIO", "0.618"))
        
        # UI에서 사용자가 조작하여 변경한 경우 현재 메모리 값과 .env 값이 동일하므로 무시
        changed = False
        if abs(ex.TARGET_PROFIT - new_target) > 0.0001: changed = True
        if abs(ex.STOP_LOSS - new_stop) > 0.0001: changed = True
        if abs(ex.TRAILING_STOP_ACTIVATION - new_trail) > 0.0001: changed = True
        if abs(float(self.spin_ai.value()) - new_ai) > 0.0001: changed = True
        if abs(self.strategy.HFS_GOLDEN_RATIO - new_hfs) > 0.0001: changed = True

        if not changed:
            return

        ex.TARGET_PROFIT = new_target
        ex.STOP_LOSS = new_stop
        ex.TRAILING_STOP_ACTIVATION = new_trail
        self.strategy.HFS_GOLDEN_RATIO = new_hfs
        
        # UI 스핀박스 업데이트 (signal 무한루프 방지)
        self.spin_target.blockSignals(True)
        self.spin_target.setValue(new_target * 100.0)
        self.spin_target.blockSignals(False)
        
        self.spin_stop.blockSignals(True)
        self.spin_stop.setValue(new_stop * 100.0)
        self.spin_stop.blockSignals(False)
        
        self.spin_trail.blockSignals(True)
        self.spin_trail.setValue(new_trail * 100.0)
        self.spin_trail.blockSignals(False)
        
        self.spin_ai.blockSignals(True)
        self.spin_ai.setValue(new_ai)
        self.spin_ai.blockSignals(False)
        
        if hasattr(ex, 'logger'):
            ex.logger.info("♻️ [.env 핫 리로드] 백그라운드 워커에 의한 설정값 변경이 무중단으로 반영되었습니다.")
        if hasattr(ex, 'slack'):
            ex.slack.send_message("♻️ *[자동 환경설정 동기화 (Hot-Reload)]*\n야간 최적화 데몬이 발굴한 최신 파라미터가 시스템 재부팅 없이 라이브 엔진에 100% 동기화 및 반영되었습니다.")

    def update_dashboard(self):
        # [최적화] 대량 UI 업데이트 시 비동기 렌더링 억제로 스크롤 버벅임 방지
        self.setUpdatesEnabled(False)
        try:
            # [0] 핫 리로드 감지기 (Hot-Reload Watcher)
            if hasattr(self, 'env_file') and os.path.exists(self.env_file):
                current_mtime = os.path.getmtime(self.env_file)
                if current_mtime > self.last_env_mtime:
                    self.last_env_mtime = current_mtime
                    self.hot_reload_env()

            # [1] 대시보드 모드 정보 업데이트
            mode_str = "모의투자 시뮬레이션" if self.execution_manager.is_dry_run else "실거래 운영 모드"

            # [2] 리스크 가드 및 실현 손익 정보 업데이트
            ex = self.execution_manager
            if ex.FIXED_LOSS_LIMIT > 0:
                self.risk_limit_label.setText(f"손실 제한 한도: -{int(ex.FIXED_LOSS_LIMIT):,}원 (매매 차단선)")
                self.risk_limit_label.setStyleSheet("color: #F28B82; font-size: 12px; font-weight: bold; background-color: transparent;")
            else:
                self.risk_limit_label.setText("손실 제한 한도: 미설정 (락 해제)")
                self.risk_limit_label.setStyleSheet("color: #ADB5BD; font-size: 12px; background-color: transparent;")

            daily_pnl = ex.daily_pnl if ex.daily_pnl is not None else 0
            try:
                self.daily_pnl_label.setText(f"금일 실현 손익: {int(daily_pnl):+,} 원")
                # 파스텔 수익/손실 색상 적용
                pnl_color = '#FF8A80' if daily_pnl > 0 else '#92B9F9' if daily_pnl < 0 else '#80868B'
                self.daily_pnl_label.setStyleSheet(f"font-size: 19px; font-weight: bold; color: {pnl_color}; background-color: transparent;")
            except:
                pass

            # [2.5] 미실현 보유 손익(Unrealized PnL) 계산 및 패닉 뷰어 갱신
            total_unrealized_pnl = 0
            total_unrealized_cost = 0
            for code, pos in ex.positions.items():
                # [상장폐지 필터] 상장폐지/정리매매 종목은 손익 계산에서 제외
                if self._is_delisted(code):
                    continue
                buy_p = pos.get('buy_price', 0)
                qty = pos.get('qty', 0)
                if buy_p > 0 and qty > 0:
                    df_1m, _ = self.pipeline.get_data(code)
                    if df_1m is not None and not df_1m.empty:
                        cur_p = df_1m['close'].iloc[-1]
                    else:
                        cur_p = buy_p
                    
                    # 수수료 차감 후 순 실시간 손익
                    friction_pct = ex.TRADING_FRICTION * 100.0
                    net_rate = (((cur_p - buy_p) / buy_p) * 100.0) - friction_pct
                    
                    # [v11.5 교차검증] API 수익률과 자체계산의 괴리가 5%p 이상이면 자체계산 우선
                    if 'api_profit_rate' in pos and not getattr(self.kiwoom, 'is_mock', False):
                        api_rate = pos['api_profit_rate']
                        if api_rate is not None and abs(api_rate - net_rate) <= 5.0:
                            net_rate = api_rate
                        
                    unrealized_net_pnl = (buy_p * qty) * (net_rate / 100.0)
                    total_unrealized_pnl += unrealized_net_pnl
                    total_unrealized_cost += (buy_p * qty)
            
            total_rate = (total_unrealized_pnl / total_unrealized_cost * 100.0) if total_unrealized_cost > 0 else 0.0
            pnl_color = '#F28B82' if total_unrealized_pnl < 0 else '#8AB4F8' if total_unrealized_pnl > 0 else '#ADB5BD'
            self.lbl_panic_pnl.setText(f"보유 종목 총 손익: {int(total_unrealized_pnl):+,}원 ({total_rate:+.2f}%)")
            self.lbl_panic_pnl.setStyleSheet(f"color: {pnl_color}; font-size: 11px; font-weight: bold;")

            # [3] 운영 설정 (Config Panel)
            self.lbl_trade_mode.setText(f"💎 모드: {mode_str}")
            self.lbl_risk_rate.setText(f"💰 비중: 종목당 {ex.INVEST_RATE_PER_STOCK*100:.1f}%")
            rsi_p = os.getenv("INDICATOR_RSI_PERIOD", "14")
            self.lbl_indicators.setText(f"📊 보조: RSI({rsi_p}) | BB(20, 2.0)")
            
            max_stocks = os.getenv("MAX_MONITORED_STOCKS", "80")
            cooldown = os.getenv("STRATEGY_SIGNAL_COOLDOWN", "300")
            self.lbl_master_settings.setText(f"⚡ 마스터: {max_stocks}종목 관제 | 진입 쿨타임 {cooldown}초")

            # [3-1] 데몬 워커 상태 감지 (WMI 간헐적 조회)
            self.daemon_tick = getattr(self, 'daemon_tick', 0) + 1
            if self.daemon_tick % 5 == 1: # 약 10초 주기 (timer=2sec)
                import subprocess
                try:
                    out = subprocess.check_output('wmic process where "name=\'python.exe\' or name=\'pythonw.exe\'" get commandline', shell=True, creationflags=0x08000000).decode('cp949', errors='ignore')
                    if "daemon_worker.py" in out:
                        self.lbl_daemon_status.setText("상태: 🟢 ON (가동 중)")
                        self.lbl_daemon_status.setStyleSheet("color: #A5D6A7; font-size: 12px; font-weight: bold;")
                        self.btn_daemon.setText("가동 중")
                        self.btn_daemon.setEnabled(False)
                        self.btn_daemon.setStyleSheet("QPushButton { background-color: #3C4043; color: #80868B; border-radius: 6px; font-weight: bold; font-size: 13px; }")
                    else:
                        self.lbl_daemon_status.setText("상태: 🔴 OFF (중지됨)")
                        self.lbl_daemon_status.setStyleSheet("color: #F28B82; font-size: 12px; font-weight: bold;")
                        self.btn_daemon.setText("▶ 데몬 런처 가동")
                        self.btn_daemon.setEnabled(True)
                        self.btn_daemon.setStyleSheet("QPushButton { background-color: #1E8E3E; color: white; border-radius: 6px; font-weight: bold; font-size: 13px; } QPushButton:hover { background-color: #188038; }")
                except:
                    pass

            # [4] 감시 종목 테이블 업데이트 (Batch 모드 활용)
            def get_sort_key(c):
                if c in self.execution_manager.positions:
                    return 0 # 1순위: 보유중
                elif self.strategy.macro_states.get(c, False):
                    return 1 # 2순위: 진입대기(매크로 통과)
                else:
                    return 2 # 3순위: 스캔중(바이패스 관망)

            monitored_codes = sorted(
                list(self.kiwoom.monitored_codes.keys()),
                key=lambda c: (get_sort_key(c), c)
            )
            
            # 일괄 데이터 획득으로 Lock 경합 최소화
            all_data = self.pipeline.batch_get_data(monitored_codes)
            
            self.watch_table.setRowCount(len(monitored_codes))
            
            for row, code in enumerate(monitored_codes):
                data_tuple = all_data.get(code, (None, None))
                df_1m = data_tuple[0]
                has_data = df_1m is not None and not df_1m.empty
                
                if has_data:
                    current_price = df_1m['close'].iloc[-1]
                    rsi_val = df_1m['RSI'].iloc[-1] if 'RSI' in df_1m.columns else 0
                    bb_low = df_1m['BB_Lower'].iloc[-1] if 'BB_Lower' in df_1m.columns else 0
                    bb_up = df_1m['BB_Upper'].iloc[-1] if 'BB_Upper' in df_1m.columns else 0
                    open_p = df_1m['open'].iloc[0]
                    
                    # [추가] 상한가 종목 즉시 필터링 및 블랙리스트 등록
                    ref_p = self.pipeline.reference_prices.get(code, open_p)
                    change_rate = ((current_price - ref_p) / ref_p * 100) if ref_p > 0 else 0
                    
                    if change_rate >= 29.8 or code in self.kiwoom.blacklisted_codes:
                        if code not in self.kiwoom.blacklisted_codes:
                            self.logger.warning(f"🚫 [상한가 감지] {code} 종목이 상한가({change_rate:.2f}%)에 도달하여 대시보드에서 제외합니다.")
                            self.kiwoom.blacklisted_codes.add(code)
                            # 감시 목록에서도 제거 (다음 루프부터 monitored_codes에서 빠짐)
                            if code in self.kiwoom.monitored_codes:
                                del self.kiwoom.monitored_codes[code]
                                self.kiwoom.set_real_remove("1000", code)
                                self.pipeline.remove_code(code)
                        continue
                else:
                    current_price, rsi_val, bb_low, bb_up, open_p = 0, 0, 0, 0, 0
                    if code in self.kiwoom.blacklisted_codes:
                        continue

                ref_p = self.pipeline.reference_prices.get(code, open_p)
                change_rate = ((current_price - ref_p) / ref_p * 100) if ref_p > 0 else 0
                stats = self.pipeline.day_stats.get(code, {'high': current_price, 'low': current_price, 'volume': 0})
                trading_value_billion = (current_price * stats['volume']) / 100000000
                bb_pct = ((current_price - bb_low) / (bb_up - bb_low) * 100) if (bb_up - bb_low) > 0 else 0
                day_range = (stats['high'] - stats['low'])
                day_pos_pct = ((current_price - stats['low']) / day_range * 100) if day_range > 0 else 0
                
                if not has_data: day_pos_str = "대기 중..."
                elif day_pos_pct <= 20: day_pos_str = f"바닥권({day_pos_pct:.0f}%)"
                elif day_pos_pct <= 40: day_pos_str = f"저점부({day_pos_pct:.0f}%)"
                elif day_pos_pct <= 60: day_pos_str = f"중위권({day_pos_pct:.0f}%)"
                elif day_pos_pct <= 80: day_pos_str = f"고점부({day_pos_pct:.0f}%)"
                else: day_pos_str = f"상단권({day_pos_pct:.0f}%)"

                # [성능 최적화] 중앙 집중식 종목명 캐시 사용 (KiwoomCore에서 관리)
                display_name = self.kiwoom.get_master_code_name(code)
                name_text = f"{display_name} ({code})" if display_name else f"조회중... ({code})"
                
                # [최적화] 아이템 존재 여부 확인 후 업데이트 (새로 생성하지 않음)
                def update_cell(r, c, text, color=None):
                    it = self.watch_table.item(r, c)
                    if not it:
                        it = QTableWidgetItem(text)
                        self.watch_table.setItem(r, c, it)
                    else:
                        it.setText(text)
                    if color: it.setForeground(QColor(color))
                    return it

                update_cell(row, 0, name_text)
                
                p_text = f"{int(current_price):,} ({change_rate:+.2f}%)" if has_data else "로딩 중..."
                p_color = "#FF8A80" if change_rate > 0 else "#92B9F9" if change_rate < 0 else "#BDC1C6"
                update_cell(row, 1, p_text, p_color)
                
                rsi_sig = "⏳ 데이터 대기" if not has_data else (f"⬇️ 과매도 ({rsi_val:.1f})" if rsi_val <= 30 else f"⬆️ 과매수 ({rsi_val:.1f})" if rsi_val >= 70 else f"↔️ 안정 ({rsi_val:.1f})")
                rsi_color = "#8AB4F8" if rsi_val <= 30 and has_data else "#FF8A80" if rsi_val >= 70 and has_data else "#BDC1C6"
                update_cell(row, 2, rsi_sig, rsi_color)
                
                update_cell(row, 3, f"{trading_value_billion:.1f}억" if has_data else "-")
                
                bb_text = f"{bb_pct:.1f}%" if has_data else "-"
                bb_color = "#FFAB91" if has_data and bb_pct <= 0 else "#BDC1C6"
                update_cell(row, 4, bb_text, bb_color)
                
                dp_color = "#A5D6A7" if has_data and day_pos_pct <= 30 else "#BDC1C6"
                update_cell(row, 5, day_pos_str, dp_color)
                
                is_macro = self.strategy.macro_states.get(code, False)
                bypass_on = self.strategy.bypass_macro
                is_owned = code in self.execution_manager.positions
                
                if is_owned:
                    status_text, status_color = "💎 보유중", "#FFD700" # 골드색상
                elif is_macro:
                    status_text, status_color = "🟢 진입대기", "#8AB4F8"
                elif bypass_on:
                    status_text, status_color = "🔵 스캔중", "#92B9F9"
                else:
                    status_text, status_color = "🌑 관망", "#5F6368"
                    
                update_cell(row, 6, status_text, status_color)


            # [5] 포지션 테이블 업데이트 (수익률 기준 내림차순 정렬)
            # [스레드 안전] 백그라운드에서 수정 중일 수 있으므로 복사본 생성하여 순회
            with self.execution_manager.lock:
                positions = dict(self.execution_manager.positions)
            
            # [상장폐지 필터] 상장폐지/정리매매 종목을 보유 종목 테이블에서 숨김
            positions = {c: p for c, p in positions.items() if not self._is_delisted(c)}
            
            # [v11.6] 실시간 파이프라인에서 직접 현재가를 조회하는 함수 (monitored_codes 미포함 보유종목도 처리)
            def get_net_roi(c, p):
                # 파이프라인에서 직접 실시간 틱 데이터 조회 (가장 신뢰도 높은 현재가)
                df_1m_direct, _ = self.pipeline.get_data(c)
                if df_1m_direct is not None and not df_1m_direct.empty:
                    cp = df_1m_direct['close'].iloc[-1]
                else:
                    # 파이프라인 미등록 시 스냅샷 현재가 사용 (최후 수단)
                    cp = p.get('current_price', p['buy_price'])
                
                friction_pct = self.execution_manager.TRADING_FRICTION * 100.0
                simple_rate = ((cp - p['buy_price']) / p['buy_price'] * 100.0) - friction_pct
                
                # [v11.6 핵심 수정] api_profit_rate와 실시간 계산 중 더 나쁜(보수적) 값을 표시
                # → 대시보드 수익률이 손절 엔진과 동일한 기준으로 표시됨
                if 'api_profit_rate' in p and p['api_profit_rate'] is not None:
                    api_rate = p['api_profit_rate']
                    if abs(api_rate - simple_rate) <= 10.0:
                        return min(api_rate, simple_rate)  # 더 나쁜 값 표시 (보수적)
                
                return simple_rate

            sorted_positions = sorted(
                positions.items(),
                key=lambda x: get_net_roi(x[0], x[1]),
                reverse=True
            )
            
            self.pos_table.setRowCount(len(sorted_positions))
            
            # 자산 요약 업데이트
            total_account_value = self.kiwoom.initial_total_assets
            self.cash_value_label.setText(f"{int(total_account_value):,} 원" if total_account_value > 0 else "- 원")

            # [최적화] 포지션 테이블 셀 업데이트 레이어
            def update_pos_cell(r, c, text, color=None, font=None):
                it = self.pos_table.item(r, c)
                if not it:
                    it = QTableWidgetItem(text)
                    self.pos_table.setItem(r, c, it)
                else:
                    it.setText(text)
                if color: it.setForeground(QColor(color))
                if font: it.setFont(font)
                return it

            for row, (code, data) in enumerate(sorted_positions):
                d_name = self.kiwoom.get_master_code_name(code) or self.code_names.get(code, code)
                # 감시 테이블과 동일한 형식(종목명 + 코드)으로 표출
                update_pos_cell(row, 0, f"{d_name} ({code})")
                update_pos_cell(row, 1, f"{int(data['buy_price']):,}")
                
                roi = get_net_roi(code, data)
                roi_color = "#FF8A80" if roi > 0 else "#92B9F9" if roi < 0 else "#80868B"
                roi_font = QFont("Verdana", 9, QFont.Bold)
                update_pos_cell(row, 2, f"{roi:+.2f}%", roi_color, roi_font)
                
                # 투자금액 및 수익금 계산
                qty = data.get('qty', 0)
                invest_amt = data['buy_price'] * qty
                pnl_amt = invest_amt * (roi / 100.0)
                valuation_amt = invest_amt + pnl_amt
                
                update_pos_cell(row, 3, f"{int(pnl_amt):+,}", roi_color)
                update_pos_cell(row, 4, f"{int(invest_amt):,}")
                
                # 현재가 정보
                cur_p = data.get('current_price', data['buy_price'])
                d_tuple = all_data.get(code)
                if d_tuple and d_tuple[0] is not None and not d_tuple[0].empty:
                    cur_p = d_tuple[0]['close'].iloc[-1]
                
                update_pos_cell(row, 5, f"{int(cur_p):,}")
                
                # 당일 등락 정보
                ref_p = self.pipeline.reference_prices.get(code, cur_p)
                day_change = ((cur_p - ref_p) / ref_p * 100) if ref_p > 0 else 0
                dc_color = "#FF8A80" if day_change > 0 else "#92B9F9" if day_change < 0 else "#80868B"
                update_pos_cell(row, 6, f"{day_change:+.2f}%", dc_color)
                
                # 진입 전략명 UI 표출
                s_type = data.get('signal_type', '기본(스캘핑)')
                if s_type == 'breakout': s_type_str = "🚀 돌파"
                elif s_type == 'pullback': s_type_str = "🌊 눌림목"
                elif s_type == 'ai_sniper': s_type_str = "🤖 AI저격"
                elif s_type == 'patience': s_type_str = "💤 인내결실"
                elif s_type == 'macro': s_type_str = "🌍 거시추세"
                else: s_type_str = f"⚡ {s_type}"
                
                update_pos_cell(row, 7, s_type_str, "#FADB14", QFont("Apple SD Gothic Neo", 10, QFont.Bold))
        except Exception as e:
            # UI 스레드 예외를 로거에 기록하여 추적 가능하게 함
            import logging
            logging.getLogger("DopamingBot.Dashboard").error(f"❌ 대시보드 업데이트 중 오류 발생: {e}")
        finally:
            self.setUpdatesEnabled(True)

    def _is_delisted(self, code):
        """상장폐지/정리매매 종목 여부를 캐시된 결과로 빠르게 판별합니다."""
        if code in self._delisted_cache:
            return self._delisted_cache[code]
        try:
            # 상태값을 호출하여 빈 값이면 아예 키움에서 삭제된 종목(예: 이트론)으로 간주
            market_state = self.kiwoom.dynamicCall("GetMasterStockState(QString)", code)
            
            if not market_state or market_state.strip() == "":
                self._delisted_cache[code] = True
                import logging
                logging.getLogger("DopamingBot.Dashboard").info(f"🗑️ [상장폐지 필터] {code} 완전 삭제종목 (상태값 없음) - 숨김 처리")
                return True
                
            # 2차 검증: 이름은 있으나 정리매매/상장폐지 상태 코드인 경우
            # '거래정지'는 VI 발동 등 일시 정지일 때도 표시되므로 대시보드에서 아예 숨기면 안됨 (SK증권 증발 방지)
            bad_keywords = ["상장폐지", "정리매매"]
            
            states = market_state.split("|")
            # 토큰 단위로 정확히 검사하여 오탐 방지
            is_bad = any(k.strip() in states for k in bad_keywords)
                
            self._delisted_cache[code] = is_bad
            if is_bad:
                import logging
                logging.getLogger("DopamingBot.Dashboard").info(f"🗑️ [상장폐지 필터] {code} 종목을 대시보드에서 숨김 처리합니다. (상태: {market_state})")
            return is_bad
        except Exception:
            return False
