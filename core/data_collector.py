"""
DataCollector 모듈 (AI 학습 데이터 수집기)
-------------------------------------------
스태파노 전략에서 거시 신호(5분봉 다이버전스)가 발생하는 순간의
시장 상태(Feature)를 캡쳐하여 'ai_features.db'에 저장하고,
일정 시간 후 결과(Label: 1=성공/0=실패)를 자동으로 업데이트합니다.

이 데이터는 ai_engine.py 가 매일 모델을 재학습하는 데 사용됩니다.

[최적화] update_labels()는 매 틱이 아닌 30초에 1번만 DB I/O 발생
"""

import sqlite3
import threading
import queue
import logging
import os
import time
from datetime import datetime

DB_PATH = os.getenv("AI_FEATURES_DB", "ai_features.db")

# 신호 발생 후 몇 분 뒤 가격을 확인하여 성공/실패를 판단할지 (분 단위)
LABEL_DELAY_MINUTES = int(os.getenv("AI_LABEL_DELAY_MIN", "30"))

# 성공 기준 수익률 (예: 0.03 = 3%)
LABEL_WIN_THRESHOLD  = float(os.getenv("TRADE_TARGET_PROFIT", "0.04"))
# 실패 기준 손실률 (예: -0.02 = -2%)
LABEL_LOSE_THRESHOLD = float(os.getenv("TRADE_STOP_LOSS", "-0.02"))

# [최적화] update_labels()의 DB I/O 최소화: 종목당 최소 이 시간(초)에 한 번만 실제 쿼리 실행
LABEL_UPDATE_INTERVAL = 30.0


class DataCollector:
    """
    AI 학습용 피처(Feature) 수집 및 자동 라벨링(Labeling) 클래스.

    사용법:
        collector = DataCollector()
        # 신호 발생 시
        collector.capture_signal(code, features_dict, entry_price)
        # 매 틱(Tick)마다 미결 라벨을 업데이트
        collector.update_labels(code, current_price)
    """

    def __init__(self):
        self.logger = logging.getLogger("DopamingBot.DataCollector")
        self._write_queue = queue.Queue()
        self._lock = threading.Lock()
        # [최적화] 종목별 마지막 DB I/O 시각 기록 (30초 배치 방어)
        self._last_label_time: dict[str, float] = {}
        self._init_db()

        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._running = True
        self._worker.start()
        self.logger.info("AI DataCollector 시작 완료 (DB: %s) | 라벨 배치 간격: %.0fs",
                         DB_PATH, LABEL_UPDATE_INTERVAL)

    # ------------------------------------------------------------------
    # DB 초기화
    # ------------------------------------------------------------------
    def _init_db(self):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_signals (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at   TEXT    NOT NULL,      -- 신호 캡쳐 시각 (ISO 8601)
                    date_key      TEXT    NOT NULL,      -- 'YYYYMMDD' 빠른 검색용
                    code          TEXT    NOT NULL,      -- 종목 코드
                    entry_price   REAL    NOT NULL,      -- 신호 발생 시 현재가

                    -- === Feature 컬럼 (전략 지표 스냅샷) ===
                    rsi_1m        REAL,                  -- 1분봉 RSI
                    rsi_5m        REAL,                  -- 5분봉 RSI
                    bb_pct_1m     REAL,                  -- BB 하단 이격도 (%)  [(현재가-하단)/하단]
                    bb_pct_5m     REAL,                  -- 5분봉 BB 하단 이격도
                    vol_ratio     REAL,                  -- 현재 거래량 / 20봉 평균 거래량 비율
                    price_chg_5   REAL,                  -- 최근 5봉 가격 변화율
                    price_chg_20  REAL,                  -- 최근 20봉 가격 변화율
                    macro_div     INTEGER,               -- 5분봉 다이버전스 여부 (1/0)
                    micro_div     INTEGER,               -- 1분봉 다이버전스 여부 (1/0)
                    is_aggressive INTEGER,               -- AGGRESSIVE_MODE 설정값 (1/0)

                    -- === Label 컬럼 ===
                    label         INTEGER DEFAULT NULL,  -- 1=성공(익절), 0=실패(손절), NULL=미결
                    labeled_at    TEXT    DEFAULT NULL,  -- 라벨 확정 시각
                    label_price   REAL    DEFAULT NULL,  -- 라벨 확정 시 가격
                    result_pct    REAL    DEFAULT NULL   -- 실제 수익률 (%)
                )
            """)
            conn.commit()

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------
    def capture_signal(self, code: str, features: dict, entry_price: float):
        """
        신호 발생 순간의 Feature를 DB에 저장합니다.

        Args:
            code (str): 종목 코드
            features (dict): StefanoStrategy._extract_ai_features() 반환값
            entry_price (float): 신호 발생 시 현재가
        """
        now = datetime.now()
        record = {
            "captured_at":   now.isoformat(timespec="seconds"),
            "date_key":      now.strftime("%Y%m%d"),
            "code":          code,
            "entry_price":   entry_price,
            "rsi_1m":        features.get("rsi_1m"),
            "rsi_5m":        features.get("rsi_5m"),
            "bb_pct_1m":     features.get("bb_pct_1m"),
            "bb_pct_5m":     features.get("bb_pct_5m"),
            "vol_ratio":     features.get("vol_ratio"),
            "price_chg_5":   features.get("price_chg_5"),
            "price_chg_20":  features.get("price_chg_20"),
            "macro_div":     int(bool(features.get("macro_div", False))),
            "micro_div":     int(bool(features.get("micro_div", False))),
            "is_aggressive": int(bool(features.get("is_aggressive", False))),
        }
        self._write_queue.put(("INSERT", record))
        self.logger.debug("[%s] AI 신호 스냅샷 캡쳐 완료 (진입가: %s)", code, entry_price)

    def update_labels(self, code: str, current_price: float):
        """
        매 실시간 틱마다 호출되지만, 실제 DB I/O는 LABEL_UPDATE_INTERVAL(30초)에
        한 번만 발생하도록 시간 기반 배치 처리합니다.

        Args:
            code (str): 종목 코드
            current_price (float): 현재 체결가
        """
        now = time.monotonic()
        last = self._last_label_time.get(code, 0.0)

        # [핵심 최적화] 마지막 DB I/O 이후 30초가 지나지 않았으면 큐에 넣지 않음
        if now - last < LABEL_UPDATE_INTERVAL:
            return

        self._last_label_time[code] = now
        self._write_queue.put(("LABEL", {"code": code, "current_price": current_price}))

    def stop(self):
        """워커 스레드 정지"""
        self._running = False

    # ------------------------------------------------------------------
    # 내부 구현
    # ------------------------------------------------------------------
    def _worker_loop(self):
        while self._running:
            try:
                task_type, payload = self._write_queue.get(timeout=0.2)
                if task_type == "INSERT":
                    self._do_insert(payload)
                elif task_type == "LABEL":
                    self._do_label(payload["code"], payload["current_price"])
                self._write_queue.task_done()
            except queue.Empty:
                pass
            except Exception as e:
                self.logger.error("DataCollector 워커 에러: %s", e)

    def _do_insert(self, record: dict):
        """미결 신호 레코드 INSERT"""
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT INTO ai_signals
                    (captured_at, date_key, code, entry_price,
                     rsi_1m, rsi_5m, bb_pct_1m, bb_pct_5m,
                     vol_ratio, price_chg_5, price_chg_20,
                     macro_div, micro_div, is_aggressive)
                VALUES
                    (:captured_at, :date_key, :code, :entry_price,
                     :rsi_1m, :rsi_5m, :bb_pct_1m, :bb_pct_5m,
                     :vol_ratio, :price_chg_5, :price_chg_20,
                     :macro_div, :micro_div, :is_aggressive)
            """, record)
            conn.commit()

    def _do_label(self, code: str, current_price: float):
        """미결 상태인 해당 종목 레코드를 순서대로 확인하고 라벨을 확정"""
        now_iso = datetime.now().isoformat(timespec="seconds")
        with sqlite3.connect(DB_PATH) as conn:
            # 해당 종목의 미결 레코드 조회 (오래된 것부터)
            rows = conn.execute("""
                SELECT id, entry_price, captured_at
                FROM ai_signals
                WHERE code = ? AND label IS NULL
                ORDER BY id ASC
            """, (code,)).fetchall()

            updates = []
            for row_id, entry_price, captured_at in rows:
                if entry_price <= 0:
                    continue

                pct = (current_price - entry_price) / entry_price

                # 익절 조건 달성 → 성공(1)
                if pct >= LABEL_WIN_THRESHOLD:
                    updates.append((1, now_iso, current_price, round(pct * 100, 2), row_id))
                # 손절 조건 달성 → 실패(0)
                elif pct <= LABEL_LOSE_THRESHOLD:
                    updates.append((0, now_iso, current_price, round(pct * 100, 2), row_id))

            if updates:
                conn.executemany("""
                    UPDATE ai_signals
                    SET label = ?, labeled_at = ?, label_price = ?, result_pct = ?
                    WHERE id = ?
                """, updates)
                conn.commit()
                self.logger.debug("[%s] %d건 라벨 확정 완료", code, len(updates))
