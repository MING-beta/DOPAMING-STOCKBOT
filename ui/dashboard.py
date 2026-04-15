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

        self.pos_table = QTableWidget(0, 9)
        self.pos_table.setColumnCount(9)
        self.pos_table.setHorizontalHeaderLabels([
            "종목명", "매입가", "수익률", "평가손익", "매입금액", 
            "현재가", "보유수량", "등락률", "보유비중"
        ])
        self.pos_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.pos_table.setColumnWidth(0, 180) # 종목명 컬럼 너비 확장
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
        
        sum_title = QLabel("총 평가 자산 현황")
        sum_title.setStyleSheet("color: #ADB5BD; font-size: 11px; font-weight: bold; text-transform: uppercase;")
        
        self.cash_value_label = QLabel("로딩 중...")
        self.cash_value_label.setStyleSheet("font-size: 28px; font-weight: bold; color: #FFFFFF;")
        
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: rgba(255, 255, 255, 0.1); border: none; height: 1px;")

        self.daily_pnl_label = QLabel("당일 실현 손익: 0원")
        self.daily_pnl_label.setFont(QFont("Apple SD Gothic Neo", 15, QFont.Bold))
        
        self.risk_limit_label = QLabel("손실 제한 한도: -")
        self.risk_limit_label.setStyleSheet("color: #F28B82; font-size: 11px;")
        
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
        
        # [성능 최적화] 로그 라인 수 제한 (500라인 이상일 경우 상단 비우기)
        if self.log_text.document().blockCount() > 500:
            cursor = self.log_text.textCursor()
            cursor.movePosition(cursor.Start)
            cursor.select(cursor.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar() # 줄바꿈 제거

        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    def update_dashboard(self):
        # [최적화] 대량 UI 업데이트 시 비동기 렌더링 억제로 스크롤 버벅임 방지
        self.setUpdatesEnabled(False)
        try:
            # [1] 대시보드 모드 정보 업데이트
            mode_str = "모의투자 시뮬레이션" if self.execution_manager.is_dry_run else "실거래 운영 모드"

            # [2] 리스크 가드 및 실현 손익 정보 업데이트
            ex = self.execution_manager
            if ex.FIXED_LOSS_LIMIT > 0:
                self.risk_limit_label.setText(f"손실 제한 한도: -{int(ex.FIXED_LOSS_LIMIT):,}원 (실현 손실 기준)")
                self.risk_limit_label.setStyleSheet("color: #F28B82; font-size: 11px; font-weight: bold;")
            else:
                self.risk_limit_label.setText("손실 제한 한도: 미설정")

            daily_pnl = ex.daily_pnl if ex.daily_pnl is not None else 0
            try:
                self.daily_pnl_label.setText(f"당일 실현 손익: {int(daily_pnl):+,}원")
                # 파스텔 수익/손실 색상 적용
                pnl_color = '#FF8A80' if daily_pnl > 0 else '#92B9F9' if daily_pnl < 0 else '#80868B'
                self.daily_pnl_label.setStyleSheet(f"font-size: 19px; font-weight: bold; color: {pnl_color}; background: transparent;")
            except:
                pass

            # [3] 운영 설정 (Config Panel)
            self.lbl_trade_mode.setText(f"💎 매매 모드: {mode_str}")
            self.lbl_risk_rate.setText(f"💰 투자 비중: 종목당 {ex.INVEST_RATE_PER_STOCK*100:.1f}%")
            self.lbl_profit_target.setText(f"🎯 목표 수익/손절: +{ex.TARGET_PROFIT*100:.1f}% / {ex.STOP_LOSS*100:.1f}%")
            self.lbl_trailing.setText(f"📈 트레일링 스톱: {ex.TRAILING_STOP_ACTIVATION*100:.1f}% 발동 / {ex.TRAILING_STOP_CALLBACK*100:.1f}% 낙폭")
            
            rsi_p = os.getenv("INDICATOR_RSI_PERIOD", "14")
            self.lbl_indicators.setText(f"📊 보조지표 설정: RSI({rsi_p}) | 볼린저밴드(20, 2.0)")

            # [4] 감시 종목 테이블 업데이트 (Batch 모드 활용)
            monitored_codes = sorted(
                list(self.kiwoom.monitored_codes.keys()),
                key=lambda c: (
                    0 if self.strategy.macro_states.get(c, False) else 1,
                    -self.kiwoom.monitored_codes.get(c, 0),
                    c
                )
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
            
            # [최적화] 모든 계산에 일관된 '실질 수익률(Net ROI)'을 사용하기 위해 내부 함수 정의
            def get_net_roi(c, p):
                if 'api_profit_rate' in p:
                    # 키움 공식 수익률(제비용 포함) 사용
                    return p['api_profit_rate']
                
                # API 데이터가 없으면 현재가 기반으로 계산 (설정된 마찰 비용 차감)
                cp = p.get('current_price', p['buy_price'])
                d_tuple = all_data.get(c) 
                if d_tuple and d_tuple[0] is not None and not d_tuple[0].empty:
                    cp = d_tuple[0]['close'].iloc[-1]
                
                friction_pct = self.execution_manager.TRADING_FRICTION * 100.0
                return ((cp - p['buy_price']) / p['buy_price'] * 100.0) - friction_pct

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
                update_pos_cell(row, 0, d_name)
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
                
                # 현재가 정보 및 수량
                cur_p = data.get('current_price', data['buy_price'])
                d_tuple = all_data.get(code)
                if d_tuple and d_tuple[0] is not None and not d_tuple[0].empty:
                    cur_p = d_tuple[0]['close'].iloc[-1]
                
                update_pos_cell(row, 5, f"{int(cur_p):,}")
                update_pos_cell(row, 6, f"{int(qty):,}")
                
                # 당일 등락 정보
                ref_p = self.pipeline.reference_prices.get(code, cur_p)
                day_change = ((cur_p - ref_p) / ref_p * 100) if ref_p > 0 else 0
                dc_color = "#FF8A80" if day_change > 0 else "#92B9F9" if day_change < 0 else "#80868B"
                update_pos_cell(row, 7, f"{day_change:+.2f}%", dc_color)
                
                # 잔고 비중
                pos_weight = (valuation_amt / total_account_value * 100) if total_account_value > 0 else 0

        except Exception as e:
            # UI 스레드 예외를 로거에 기록하여 추적 가능하게 함
            import logging
            logging.getLogger("DopamingBot.Dashboard").error(f"❌ 대시보드 업데이트 중 오류 발생: {e}")
        finally:
            self.setUpdatesEnabled(True)
