import sys
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QTextEdit, QTableWidget, QTableWidgetItem, QLabel, QHeaderView
)
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QColor, QFont

"""
Dashboard 모듈
----------------
PyQt 앱의 메인 화면 디자인과 데이터 시각화를 전담합니다.
데이터 파이프라인에서 최신 상태를 폴링(Polling)하여 UI 테이블 등에 표시하며,
주소/키워드 색상 등에 Toss/Kakao 벤치마킹 디자인이 적용되어 있습니다.
"""

class Dashboard(QMainWindow):
    """실시간 로깅, 종목 감시 현황, 그리고 자산 데이터를 렌더링하는 GUI 위젯"""
    def __init__(self, kiwoom, pipeline, execution_manager, strategy):
        """
        초기화 메서드. 핵심 DI 객체들을 내부 참조로 연결하고 화면 UI를 세팅합니다.
        
        Args:
            kiwoom: Kiwoom API 코어
            pipeline: DataPipeline (데이터 엑세스 용도)
            execution_manager: ExecutionManager (포지션 엑세스 용도)
            strategy: StefanoStrategy (알고리즘 상태 엑세스 용도)
        """
        super().__init__()
        self.kiwoom = kiwoom
        self.pipeline = pipeline
        self.execution_manager = execution_manager
        self.strategy = strategy
        self.code_names = {} # 종목명 캐시 (API 부하 방지용)
        
        self.init_ui()
        self.apply_dark_theme()
        
        # 1초마다 백그라운드 상태를 가져와 UI 업데이트해주는 GUI 폴링 타이머
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_dashboard)
        self.update_timer.start(1000)

    def init_ui(self):
        self.setWindowTitle("Dopaming Stock Bot - Live Dashboard")
        self.setGeometry(100, 100, 1200, 800)

        # 메인 위젯 및 분할 레이아웃
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # 좌측 레이아웃 (로그 텍스트 에어리어)
        left_layout = QVBoxLayout()
        # 로그 라벨
        log_label = QLabel("시스템 실시간 로그")
        log_label.setFont(QFont("Apple SD Gothic Neo", 12, QFont.Bold))
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        # 로그 텍스트용 전용 폰트
        self.log_text.setFont(QFont("Consolas", 10))
        left_layout.addWidget(log_label)
        left_layout.addWidget(self.log_text)

        # 우측 레이아웃
        right_layout = QVBoxLayout()
        
        # 우측 상단: 파이프라인 감시 종목 & 다이버전스 상태 (macro_states)
        watch_label = QLabel("감시 중인 종목 리스트 (5분봉 거시 다이버전스 대기 상태)")
        watch_label.setFont(QFont("Apple SD Gothic Neo", 12, QFont.Bold))
        self.watch_table = QTableWidget(0, 4)
        self.watch_table.setHorizontalHeaderLabels(["종목명(코드)", "현재가", "1분봉 수", "5분봉 상태"])
        self.watch_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.watch_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.watch_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.watch_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        right_layout.addWidget(watch_label)
        right_layout.addWidget(self.watch_table, stretch=2)

        # 우측 중단: 현재 자산 현황 레이블 (토스 스타일 분리형 위젯)
        cash_widget = QWidget()
        cash_widget.setMinimumHeight(56)
        cash_widget.setStyleSheet("QWidget { background-color: #3182F6; border-radius: 12px; } QLabel { background-color: transparent; color: #FFFFFF; }")
        cash_layout = QHBoxLayout(cash_widget)
        cash_layout.setContentsMargins(16, 0, 16, 0)
        
        self.cash_title_label = QLabel("운영 주문 가능 예수금")
        self.cash_title_label.setFont(QFont("Apple SD Gothic Neo", 12, QFont.Bold))
        
        self.cash_value_label = QLabel("동기화 중...")
        self.cash_value_label.setFont(QFont("Apple SD Gothic Neo", 16, QFont.Bold))
        self.cash_value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        
        cash_layout.addWidget(self.cash_title_label)
        cash_layout.addWidget(self.cash_value_label)
        right_layout.addWidget(cash_widget)

        # 우측 하단: 보유 포지션 관리 테이블
        pos_label = QLabel("오픈 포지션 현황")
        pos_label.setFont(QFont("Apple SD Gothic Neo", 12, QFont.Bold))
        self.pos_table = QTableWidget(0, 4)
        self.pos_table.setHorizontalHeaderLabels(["종목명(코드)", "보유수량", "매입단가", "수익률(%)"])
        self.pos_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.pos_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.pos_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.pos_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        right_layout.addWidget(pos_label)
        right_layout.addWidget(self.pos_table, stretch=2)

        # 비율 지정 후 메인 레이아웃에 결합
        main_layout.addLayout(left_layout, stretch=4)
        main_layout.addLayout(right_layout, stretch=6)

    def apply_dark_theme(self):
        """토스/카카오 스타일의 모던 플랫 다크 모드 적용"""
        css = """
        QMainWindow, QWidget {
            background-color: #121212;
            color: #F8F9FA;
            font-family: 'Pretendard', 'Apple SD Gothic Neo', 'Malgun Gothic', 'sans-serif';
        }
        QLabel {
            color: #E9ECEF;
            font-weight: 600;
            padding: 2px 0px;
        }
        QTextEdit {
            background-color: #1E1E1E;
            color: #CED4DA;
            border: 1px solid #2C2C2E;
            border-radius: 12px;
            padding: 8px;
        }
        QTableWidget {
            background-color: #1E1E1E;
            color: #E9ECEF;
            gridline-color: transparent;
            border: 1px solid #2C2C2E;
            border-radius: 12px;
            padding: 4px;
            outline: none;
            selection-background-color: rgba(49, 130, 246, 0.2);
            selection-color: #F8F9FA;
        }
        QTableWidget::item {
            border-bottom: 1px solid #2C2C2E;
            padding: 4px;
        }
        QHeaderView::section {
            background-color: #1E1E1E;
            color: #ADB5BD;
            font-weight: bold;
            padding: 10px 4px;
            border: none;
            border-bottom: 1px solid #3E3E42;
        }
        QScrollBar:vertical {
            border: none;
            background: #121212;
            width: 10px;
            margin: 0px 0px 0px 0px;
        }
        QScrollBar::handle:vertical {
            background: #3E3E42;
            min-height: 20px;
            border-radius: 5px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            border: none;
            background: none;
        }
        """
        self.setStyleSheet(css)

    def append_log(self, msg):
        """로거로부터 받은 메시지를 파싱하여 직관적인 색상과 가이드를 적용해 추가"""
        import html
        msg_escaped = html.escape(msg)
        
        # 기본 색상
        color = "#D4D4D4"
        suffix_guide = ""
        
        if " - ERROR - " in msg_escaped:
            color = "#FF4D4D" # 눈에 띄는 빨간색
            if "-202" in msg_escaped:
                suffix_guide = "<br>&nbsp;&nbsp;↳ 💡 <b>[필수 확인 가이드]</b> 화면 우측 하단 키움 API 트레이 우클릭 -> [계좌비밀번호 저장] 작업이 누락되었습니다."
            elif "64비트" in msg_escaped or "활성화 실패" in msg_escaped:
                suffix_guide = "<br>&nbsp;&nbsp;↳ 💡 <b>[필수 확인 가이드]</b> 잘못된 환경입니다! 프로그램을 끄고 폴더 내 <u>start_bot.bat</u> 파일로 다시 실행하세요."
        elif " - WARNING - " in msg_escaped:
            color = "#FFA500" # 주황색
        elif " - DEBUG - " in msg_escaped:
            color = "#666666" # 어두운 회색 (눈에 덜 띄게)
        elif " - INFO - " in msg_escaped:
            if any(k in msg_escaped for k in ["매수", "주문", "체결", "진입", "ExecutionManager", "수익", "익절", "손절", "시그널"]):
                color = "#4CAF50" # 거래/포지션 관련 (초록색)
            elif any(k in msg_escaped for k in ["동기화", "데이터 요청", "로드", "조건검색", "QApplication", "CommConnect", "로그인"]):
                color = "#00BCD4" # 시스템 이벤트 관련 (청록색)
            elif "시스템 시작" in msg_escaped or "=====" in msg_escaped:
                color = "#FFEB3B" # 봇 시작 등 강조 포인트 (노란색)
                
        # 최종 HTML 포맷 조합
        styled_msg = f"<span style='color: {color};'>{msg_escaped}{suffix_guide}</span>"
        self.log_text.append(styled_msg)
        
        # 자동 스크롤
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def update_dashboard(self):
        """타이머에 의해 주기적으로 호출되어 백엔드 데이터를 끌어옵니다(Pull)."""
        # 1. 예수금 라벨 업데이트
        mode_str = "[시뮬레이션 (Virtual)]" if self.execution_manager.is_dry_run else "[운영 (Production)]"
        cash = self.kiwoom.available_cash
        self.cash_title_label.setText(f"{mode_str} 주문 가능 예수금")
        self.cash_value_label.setText(f"{cash:,} 원")

        # 2. 감시 종목 테이블 업데이트
        with self.pipeline.lock:
            monitored_codes = list(self.pipeline.data_1m.keys())
            
        # 우선순위 정렬: 5분봉 진입 대기(True)인 종목을 위로 올림
        monitored_codes.sort(key=lambda x: self.strategy.macro_states.get(x, False), reverse=True)
            
        self.watch_table.setRowCount(len(monitored_codes))
        for row, code in enumerate(monitored_codes):
            # 행 번호 및 상태 긁어오기
            try:
                df_1m = self.pipeline.data_1m.get(code)
                candle_cnt = len(df_1m) if df_1m is not None else 0
                current_price = df_1m['close'].iloc[-1] if df_1m is not None and not df_1m.empty else 0
            except:
                candle_cnt = 0
                current_price = 0
            
            # 종목명 가져오기 (캐시 처리)
            if code not in self.code_names:
                name = self.kiwoom.dynamicCall("GetMasterCodeName(QString)", code)
                self.code_names[code] = name.strip() if getattr(name, 'strip', None) else ""
            display_name = f"{self.code_names[code]} ({code})" if self.code_names[code] else code

            # 스테파노 전략 거시적 다이버전스(5m) 상태
            is_macro = self.strategy.macro_states.get(code, False)

            item_code = QTableWidgetItem(display_name)
            item_price = QTableWidgetItem(f"{int(current_price):,}" if current_price > 0 else "-")
            item_cnt = QTableWidgetItem(f"{candle_cnt}봉")
            
            # 이모지 부활 및 텍스트 적용
            state_text = "🟢 진입 대기" if is_macro else "🔴 관망"
            item_state = QTableWidgetItem(state_text)
            
            # 상태 변경 시 텍스트 색상 우선순위
            if is_macro:
                item_state.setForeground(QColor("#3182F6"))
                item_state.setFont(QFont("Apple SD Gothic Neo", 10, QFont.Bold))
            else:
                item_state.setForeground(QColor("#666666"))

            item_code.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            item_price.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item_cnt.setTextAlignment(Qt.AlignCenter)
            item_state.setTextAlignment(Qt.AlignCenter)

            self.watch_table.setItem(row, 0, item_code)
            self.watch_table.setItem(row, 1, item_price)
            self.watch_table.setItem(row, 2, item_cnt)
            self.watch_table.setItem(row, 3, item_state)

            # 토스 스타일의 은은한 하이라이트 배경
            if is_macro:
                bg_color = QColor(49, 130, 246, 20) # Toss Blue 얇은 배경
                item_code.setBackground(bg_color)
                item_price.setBackground(bg_color)
                item_cnt.setBackground(bg_color)
                item_state.setBackground(bg_color)

        # 3. 오픈 포지션 테이블 업데이트
        positions = self.execution_manager.positions
        self.pos_table.setRowCount(len(positions))
        
        row = 0
        for code, data in positions.items():
            qty = data['qty']
            buy_price = data['buy_price']
            
            # 현재 1분봉의 가장 마지막 행 종가를 현재가로 취급
            current_price = buy_price # 초기값 방어
            with self.pipeline.lock:
                if code in self.pipeline.data_1m and not self.pipeline.data_1m[code].empty:
                    current_price = self.pipeline.data_1m[code]['close'].iloc[-1]
            
            if buy_price > 0:
                profit_rate = ((current_price - buy_price) / buy_price) * 100.0
            else:
                profit_rate = 0.0
                
            # 종목명 가져오기 (캐시 처리)
            if code not in self.code_names:
                name = self.kiwoom.dynamicCall("GetMasterCodeName(QString)", code)
                self.code_names[code] = name.strip() if getattr(name, 'strip', None) else ""
            display_name = f"{self.code_names[code]} ({code})" if self.code_names[code] else code

            item_code = QTableWidgetItem(display_name)
            item_qty = QTableWidgetItem(str(qty))
            item_price = QTableWidgetItem(f"{int(buy_price):,}")
            item_profit = QTableWidgetItem(f"{profit_rate:.2f}%")

            item_code.setTextAlignment(Qt.AlignCenter)
            item_qty.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item_price.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item_profit.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            # 수익률 색상 표현 (토스 주식 스타일: 빨강 핑크톤, 파랑 하늘톤)
            item_profit.setFont(QFont("Apple SD Gothic Neo", 10, QFont.Bold))
            if profit_rate > 0:
                item_profit.setForeground(QColor("#F04452")) # 토스 레드
            elif profit_rate < 0:
                item_profit.setForeground(QColor("#3182F6")) # 토스 블루

            self.pos_table.setItem(row, 0, item_code)
            self.pos_table.setItem(row, 1, item_qty)
            self.pos_table.setItem(row, 2, item_price)
            self.pos_table.setItem(row, 3, item_profit)
            row += 1
