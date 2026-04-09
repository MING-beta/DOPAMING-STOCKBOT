import sys
import os
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QTextEdit, QTableWidget, QTableWidgetItem, QLabel, QHeaderView,
    QGroupBox, QGridLayout, QFrame
)
from PyQt5.QtCore import QTimer, Qt, QSize
from PyQt5.QtGui import QColor, QFont, QPalette, QBrush

"""
Dashboard 2.0 Premium Edition
-------------------------------
Toss/Kakao 금융앱을 벤치마킹한 차분하고 고급스러운 하이엔드 거래 위주 레이아웃입니다.
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
        
        self.init_ui()
        self.apply_dark_theme()
        
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_dashboard)
        self.update_timer.start(1000)

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

        self.pos_table = QTableWidget(0, 9)
        self.pos_table.setHorizontalHeaderLabels([
            "종목명", "매입가", "수익률", "평가손익", "매입금액", 
            "현재가", "보유수량", "등락률", "보유비중"
        ])
        self.pos_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
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
        
        sum_title = QLabel("자산 운용 현황")
        sum_title.setStyleSheet("color: #ADB5BD; font-size: 11px; font-weight: bold; text-transform: uppercase;")
        
        self.cash_value_label = QLabel("로딩 중...")
        self.cash_value_label.setStyleSheet("font-size: 28px; font-weight: bold; color: #FFFFFF;")
        
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: rgba(255, 255, 255, 0.1); border: none; height: 1px;")

        self.daily_pnl_label = QLabel("당일 손익: 0원")
        self.daily_pnl_label.setFont(QFont("Apple SD Gothic Neo", 15, QFont.Bold))
        
        self.risk_limit_label = QLabel("리스크 한도: -")
        self.risk_limit_label.setStyleSheet("color: #FFA500; font-size: 11px;")
        
        summary_vbox.addWidget(sum_title)
        summary_vbox.addWidget(self.cash_value_label)
        summary_vbox.addSpacing(8)
        summary_vbox.addWidget(line)
        summary_vbox.addSpacing(16)
        summary_vbox.addWidget(self.daily_pnl_label)
        summary_vbox.addWidget(self.risk_limit_label)
        summary_vbox.addStretch()
        
        side_panel.addWidget(summary_card, stretch=2)

        # (D) 운영 설정 정보 카드
        config_card = QFrame()
        config_card.setObjectName("Card")
        config_vbox = QVBoxLayout(config_card)
        
        config_title = QLabel("⚙️ 운영 설정 센터")
        config_title.setFont(QFont("Apple SD Gothic Neo", 12, QFont.Bold))
        config_vbox.addWidget(config_title)
        
        config_grid = QGridLayout()
        self.lbl_trade_mode = QLabel("매매 모드: -")
        self.lbl_risk_rate = QLabel("투자 비중: -")
        self.lbl_profit_target = QLabel("목표 수익/손절: -")
        self.lbl_trailing = QLabel("트레일링 스톱: -")
        self.lbl_indicators = QLabel("보조지표 설정: -")
        
        for i, lbl in enumerate([self.lbl_trade_mode, self.lbl_risk_rate, self.lbl_profit_target, self.lbl_trailing, self.lbl_indicators]):
            lbl.setStyleSheet("color: #ADB5BD; font-size: 11px;")
            config_grid.addWidget(lbl, i // 1, i % 1) 
            # 한 줄씩 정렬로 변경하여 가독성 증대
            
        config_vbox.addLayout(config_grid)
        config_vbox.addStretch()
        side_panel.addWidget(config_card, stretch=3)

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
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    def update_dashboard(self):
        # [1] 자산 정보 업데이트 (방어 코드 포함)
        mode_str = "모의투자 시뮬레이션" if self.execution_manager.is_dry_run else "실거래 운영 모드"
        cash = self.kiwoom.available_cash if self.kiwoom.available_cash is not None else 0
        try:
            self.cash_value_label.setText(f"{int(cash):,} 원")
        except:
            self.cash_value_label.setText("- 원")

        initial_assets = self.kiwoom.initial_total_assets
        if initial_assets and initial_assets > 0:
            limit_rate = self.execution_manager.LOSS_LIMIT_RATE
            limit_amt = initial_assets * limit_rate
            self.risk_limit_label.setText(f"손실 제한 한도: -{limit_amt:,.0f}원 ({limit_rate*100:.0f}%)")
        else:
            self.risk_limit_label.setText("손실 제한 한도: 계산 중...")

        daily_pnl = self.execution_manager.daily_pnl if self.execution_manager.daily_pnl is not None else 0
        try:
            self.daily_pnl_label.setText(f"당일 손익: {daily_pnl:+, .0f}원")
            # 파스텔 수익/손실 색상 적용
            pnl_color = '#FF8A80' if daily_pnl > 0 else '#92B9F9' if daily_pnl < 0 else '#80868B'
            self.daily_pnl_label.setStyleSheet(f"font-size: 19px; font-weight: bold; color: {pnl_color}; background: transparent;")
        except:
            pass

        # [2] 운영 설정 (Config Panel)
        ex = self.execution_manager
        self.lbl_trade_mode.setText(f"💎 매매 모드: {mode_str}")
        self.lbl_risk_rate.setText(f"💰 투자 비중: 종목당 {ex.INVEST_RATE_PER_STOCK*100:.1f}%")
        self.lbl_profit_target.setText(f"🎯 목표 수익/손절: +{ex.TARGET_PROFIT*100:.1f}% / {ex.STOP_LOSS*100:.1f}%")
        self.lbl_trailing.setText(f"📈 트레일링 스톱: {ex.TRAILING_STOP_ACTIVATION*100:.1f}% 발동 / {ex.TRAILING_STOP_CALLBACK*100:.1f}% 낙폭")
        
        rsi_p = os.getenv("INDICATOR_RSI_PERIOD", "14")
        self.lbl_indicators.setText(f"📊 보조지표 설정: RSI({rsi_p}) | 볼린저밴드(20, 2.0)")

        # [3] 감시 종목 테이블 (Kiwoom 엔진의 monitored_codes 기준)
        # 정렬 우선순위: 1순위(진입대기 상태), 2순위(최근 발견된 기회순), 3순위(종목코드 순)
        monitored_codes = sorted(
            list(self.kiwoom.monitored_codes.keys()),
            key=lambda c: (
                0 if self.strategy.macro_states.get(c, False) else 1,    # 진입대기(0)가 관망(1)보다 우선
                -self.kiwoom.monitored_codes.get(c, 0),                 # 최신 발견 종목 우선
                c                                                       # 보조 정렬 (코드순)
            )
        )
        self.watch_table.setRowCount(len(monitored_codes))
        
        for row, code in enumerate(monitored_codes):
            df_1m = self.pipeline.data_1m.get(code)
            
            # 데이터 로딩 중인 경우(None/Empty)에 대한 예외 처리 및 기본값 설정
            has_data = df_1m is not None and not df_1m.empty
            
            if has_data:
                # 지표 계산 (전략 클래스의 메서드 활용)
                df_1m = self.strategy._calculate_indicators(df_1m)
                current_price = df_1m['close'].iloc[-1]
                rsi_val = df_1m['RSI'].iloc[-1] if 'RSI' in df_1m.columns else 0
                bb_low = df_1m['BB_Lower'].iloc[-1] if 'BB_Lower' in df_1m.columns else 0
                bb_up = df_1m['BB_Upper'].iloc[-1] if 'BB_Upper' in df_1m.columns else 0
                open_p = df_1m['open'].iloc[0]
            else:
                current_price = 0
                rsi_val = 0
                bb_low = 0
                bb_up = 0
                open_p = 0

            # 1. 등락률 (전일 종가 기준)
            ref_p = self.pipeline.reference_prices.get(code, open_p)
            change_rate = ((current_price - ref_p) / ref_p * 100) if ref_p > 0 else 0
            
            # 2. 수급(억)
            stats = self.pipeline.day_stats.get(code, {'high': current_price, 'low': current_price, 'volume': 0})
            trading_value_billion = (current_price * stats['volume']) / 100000000
            
            # 3. BB %B (위치)
            bb_pct = ((current_price - bb_low) / (bb_up - bb_low) * 100) if (bb_up - bb_low) > 0 else 0
            
            # 4. 당일 위치 문자열(수치)
            day_range = (stats['high'] - stats['low'])
            day_pos_pct = ((current_price - stats['low']) / day_range * 100) if day_range > 0 else 0
            
            if not has_data:
                day_pos_str = "대기 중..."
            elif day_pos_pct <= 20: day_pos_str = f"바닥권({day_pos_pct:.0f}%)"
            elif day_pos_pct <= 40: day_pos_str = f"저점부({day_pos_pct:.0f}%)"
            elif day_pos_pct <= 60: day_pos_str = f"중위권({day_pos_pct:.0f}%)"
            elif day_pos_pct <= 80: day_pos_str = f"고점부({day_pos_pct:.0f}%)"
            else: day_pos_str = f"상단권({day_pos_pct:.0f}%)"

            # 종목명
            if code not in self.code_names:
                name = self.kiwoom.dynamicCall("GetMasterCodeName(QString)", code)
                self.code_names[code] = name.strip() if getattr(name, 'strip', None) else ""
            
            self.watch_table.setItem(row, 0, QTableWidgetItem(f"{self.code_names[code]} ({code})"))
            
            # 현재가 (등락률)
            p_text = f"{int(current_price):,} ({change_rate:+.2f}%)" if has_data else "로딩 중..."
            price_item = QTableWidgetItem(p_text)
            price_item.setForeground(QColor("#FF8A80" if change_rate > 0 else "#92B9F9" if change_rate < 0 else "#BDC1C6"))
            self.watch_table.setItem(row, 1, price_item)
            
            # RSI 신호 (화살표 아이콘)
            if not has_data: rsi_signal = "⏳ 데이터 대기"
            elif rsi_val <= 30: rsi_signal = f"⬇️ 과매도 ({rsi_val:.1f})"
            elif rsi_val >= 70: rsi_signal = f"⬆️ 과매수 ({rsi_val:.1f})"
            else: rsi_signal = f"↔️ 안정 ({rsi_val:.1f})"
            
            rsi_item = QTableWidgetItem(rsi_signal)
            rsi_item.setForeground(QColor("#8AB4F8" if rsi_val <= 30 and has_data else "#FF8A80" if rsi_val >= 70 and has_data else "#BDC1C6"))
            self.watch_table.setItem(row, 2, rsi_item)
            
            # 수급(억)
            qv_text = f"{trading_value_billion:.1f}억" if has_data else "-"
            self.watch_table.setItem(row, 3, QTableWidgetItem(qv_text))
            
            # BB 위치
            bb_text = f"{bb_pct:.1f}%" if has_data else "-"
            bb_item = QTableWidgetItem(bb_text)
            if has_data and bb_pct <= 0: bb_item.setForeground(QColor("#FFAB91")) 
            self.watch_table.setItem(row, 4, bb_item)
            
            # 당일 위치
            day_pos_item = QTableWidgetItem(day_pos_str)
            day_pos_item.setForeground(QColor("#A5D6A7" if has_data and day_pos_pct <= 30 else "#BDC1C6"))
            self.watch_table.setItem(row, 5, day_pos_item)
            
            # 전략 상태
            is_macro = self.strategy.macro_states.get(code, False)
            status_text = "🟢 진입대기" if is_macro else "🌑 관망"
            item_status = QTableWidgetItem(status_text)
            item_status.setForeground(QColor("#8AB4F8" if is_macro else "#5F6368"))
            self.watch_table.setItem(row, 6, item_status)

        # [4] 포지션 테이블 업데이트
        positions = self.execution_manager.positions
        self.pos_table.setRowCount(len(positions))
        
        # 자산 비중 계산을 위한 총 자산 산출
        total_valuation = sum([p['qty'] * (self.pipeline.data_1m[c]['close'].iloc[-1] if c in self.pipeline.data_1m and not self.pipeline.data_1m[c].empty else p['buy_price']) for c, p in positions.items()])
        total_account_value = (self.kiwoom.available_cash if self.kiwoom.available_cash is not None else 0) + total_valuation

        for row, (code, data) in enumerate(positions.items()):
            cur_p = data['buy_price']
            open_p = cur_p
            with self.pipeline.lock:
                if code in self.pipeline.data_1m and not self.pipeline.data_1m[code].empty:
                    df = self.pipeline.data_1m[code]
                    cur_p = df['close'].iloc[-1]
                    open_p = df['open'].iloc[0]
            
            # 1. 기본 계산
            ref_p = self.pipeline.reference_prices.get(code, data['buy_price'])
            profit_rate = ((cur_p - data['buy_price']) / data['buy_price']) * 100.0 if data['buy_price'] > 0 else 0
            pnl_amt = (cur_p - data['buy_price']) * data['qty']
            invest_amt = data['buy_price'] * data['qty']
            valuation_amt = cur_p * data['qty']
            day_change_rate = ((cur_p - ref_p) / ref_p * 100) if ref_p > 0 else 0
            pos_weight = (valuation_amt / total_account_value * 100) if total_account_value > 0 else 0
            
            # 2. 아이템 매핑
            # 0: 종목명
            self.pos_table.setItem(row, 0, QTableWidgetItem(f"{self.code_names.get(code, code)}"))
            
            # 1: 매입가
            self.pos_table.setItem(row, 1, QTableWidgetItem(f"{int(data['buy_price']):,}"))
            
            # 2: 수익률 (색상 적용)
            p_item = QTableWidgetItem(f"{profit_rate:+.2f}%")
            p_color = "#FF8A80" if profit_rate > 0 else "#92B9F9" if profit_rate < 0 else "#80868B"
            p_item.setForeground(QColor(p_color))
            p_item.setFont(QFont("Verdana", 9, QFont.Bold))
            self.pos_table.setItem(row, 2, p_item)
            
            # 3: 평가손익 (색상 적용)
            pnl_item = QTableWidgetItem(f"{int(pnl_amt):+,}")
            pnl_item.setForeground(QColor(p_color))
            self.pos_table.setItem(row, 3, pnl_item)
            
            # 4: 매입금액
            self.pos_table.setItem(row, 4, QTableWidgetItem(f"{int(invest_amt):,}"))
            
            # 5: 현재가
            self.pos_table.setItem(row, 5, QTableWidgetItem(f"{int(cur_p):,}"))
            
            # 6: 보유수량
            self.pos_table.setItem(row, 6, QTableWidgetItem(f"{data['qty']}"))
            
            # 7: 등락률 (당일)
            dc_item = QTableWidgetItem(f"{day_change_rate:+.2f}%")
            dc_color = "#FF8A80" if day_change_rate > 0 else "#92B9F9" if day_change_rate < 0 else "#80868B"
            dc_item.setForeground(QColor(dc_color))
            self.pos_table.setItem(row, 7, dc_item)
            
            # 8: 보유비중
            w_item = QTableWidgetItem(f"{pos_weight:.1f}%")
            w_item.setForeground(QColor("#A5D6A7")) # 소프트 그린
            self.pos_table.setItem(row, 8, w_item)
