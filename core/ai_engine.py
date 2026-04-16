"""
AIEngine 모듈 (신호 검증 AI)
------------------------------
DataCollector가 쌓은 'ai_features.db' 데이터를 학습하여
스태파노 전략의 매수 신호가 실제로 성공할 확률을 예측합니다.

- 모델: LightGBM (32bit 호환, 속도 최우선)
- 학습: 장 종료 후 자동 재학습 (매일 업데이트)
- 추론: 신호 발생 시 ~1ms 내 결과 반환 (실시간 트레이딩 가능)
- Shadow Mode: 데이터가 부족할 經우 예측만 하고 매수는 전략에 맡김
"""

import os
import sqlite3
import logging
import threading
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np

# ── Feature 컬럼 순서 (학습 및 추론 시 항상 동일해야 함) ──────────────
FEATURE_COLS = [
    "rsi_1m", "rsi_5m",
    "bb_pct_1m", "bb_pct_5m",
    "vol_ratio",
    "price_chg_5", "price_chg_20",
    "macro_div", "micro_div",
    "is_aggressive",
]

DB_PATH          = os.getenv("AI_FEATURES_DB", "ai_features.db")
MODEL_PATH       = os.getenv("AI_MODEL_PATH",  "ai_model.pkl")

# 모델을 활성화(실제 매수 제어)하기 위해 필요한 최소 학습 샘플 수
MIN_TRAIN_SAMPLES = int(os.getenv("AI_MIN_SAMPLES", "200"))

# 이 확률 이상일 때만 AI가 매수를 승인
AI_THRESHOLD = float(os.getenv("AI_THRESHOLD", "0.72"))

# Shadow Mode: True이면 AI가 로그만 남기고 매수 결정에 간섭하지 않음
SHADOW_MODE = os.getenv("AI_SHADOW_MODE", "True").lower() == "true"

class NumpyLogisticRegression:
    def __init__(self, lr=0.01, iters=1000):
        self.lr = lr
        self.iters = iters
    def fit(self, X, y):
        self.mean = np.mean(X, axis=0)
        self.std = np.std(X, axis=0) + 1e-8
        X_norm = (X - self.mean) / self.std
        n_samples, n_features = X_norm.shape
        self.weights = np.zeros(n_features)
        self.bias = 0
        for _ in range(self.iters):
            linear_model = np.dot(X_norm, self.weights) + self.bias
            y_pred = 1 / (1 + np.exp(-np.clip(linear_model, -250, 250)))
            dw = (1 / n_samples) * np.dot(X_norm.T, (y_pred - y))
            db = (1 / n_samples) * np.sum(y_pred - y)
            self.weights -= self.lr * dw
            self.bias -= self.lr * db
    def predict_proba(self, X):
        X_norm = (X - self.mean) / self.std
        linear_model = np.dot(X_norm, self.weights) + self.bias
        y_pred = 1 / (1 + np.exp(-np.clip(linear_model, -250, 250)))
        return np.column_stack((1 - y_pred, y_pred))
    def predict(self, X):
        probs = self.predict_proba(X)
        return (probs[:, 1] >= 0.5).astype(int)


class AIEngine:
    """
    LightGBM 기반 신호 검증 AI 엔진.

    사용법:
        engine = AIEngine()
        prob = engine.predict(features_dict)   # 0.0 ~ 1.0
        approved = engine.approve(features_dict)  # True / False
    """

    def __init__(self):
        self.logger = logging.getLogger("DopamingBot.AIEngine")
        self._model  = None       # 로드된 LightGBM 모델 (없으면 None)
        self._lock   = threading.Lock()
        self._shadow = SHADOW_MODE

        # 시작 시 저장된 모델 파일이 있으면 로드
        self._load_model()

        mode_str = "Shadow(관찰)" if self._shadow else "Active(제어)"
        self.logger.info(
            "AIEngine 초기화 완료 | 모드: %s | 임계값: %.0f%% | 최소 샘플: %d",
            mode_str, AI_THRESHOLD * 100, MIN_TRAIN_SAMPLES
        )

    # ==================================================================
    # 공개 API
    # ==================================================================

    def predict(self, features: dict) -> float:
        """
        Feature 딕셔너리를 받아 신호 성공 확률을 반환합니다.

        Returns:
            float: 성공 확률 (0.0 ~ 1.0). 모델 없으면 -1.0 반환.
        """
        with self._lock:
            if self._model is None:
                return -1.0   # 모델 없음 → 판단 불가
            try:
                X = self._dict_to_array(features)
                prob = float(self._model.predict_proba(X)[0][1])
                return prob
            except Exception as e:
                self.logger.error("AI 추론 오류: %s", e)
                return -1.0

    def approve(self, features: dict) -> tuple[bool, float]:
        """
        신호를 AI에게 제출하고 매수 승인 여부를 반환합니다.

        Returns:
            (approved: bool, prob: float)
            - Shadow Mode: 항상 approved=True (매수 결정에 간섭 안 함)
            - Active Mode: prob >= AI_THRESHOLD 일 때만 True
        """
        prob = self.predict(features)

        # 모델이 없거나 데이터 부족 → 통과 (전략 로직에 맡김)
        if prob < 0:
            self.logger.debug("AI 미개입 (모델 없음 또는 데이터 부족)")
            return True, prob

        if self._shadow:
            # Shadow Mode: 로그만 남기고 무조건 통과
            self.logger.info(
                "🤖 [Shadow] AI 예측 확률: %.1f%% (매수 결정 불간섭)", prob * 100
            )
            return True, prob

        # Active Mode: 임계값 기준 승인/기각
        approved = prob >= AI_THRESHOLD
        verdict  = "✅ 승인" if approved else "🛑 기각"
        self.logger.info(
            "🤖 [Active] AI %s | 확률: %.1f%% (임계값: %.0f%%)",
            verdict, prob * 100, AI_THRESHOLD * 100
        )
        return approved, prob

    def train_model(self, force: bool = False):
        """
        DB에 쌓인 라벨 확정 데이터로 모델을 재학습하고 저장합니다.
        장 종료 후 main.py 스케줄러에 의해 자동 호출됩니다.

        Args:
            force (bool): True이면 최소 샘플 조건 무시하고 강제 학습
        """
        threading.Thread(target=self._train_worker, args=(force,), daemon=True).start()

    def get_stats(self) -> dict:
        """현재 모델 상태 및 누적 데이터 통계 반환"""
        try:
            with sqlite3.connect(DB_PATH) as conn:
                total  = conn.execute("SELECT COUNT(*) FROM ai_signals").fetchone()[0]
                labeled = conn.execute(
                    "SELECT COUNT(*), SUM(label) FROM ai_signals WHERE label IS NOT NULL"
                ).fetchone()
                labeled_cnt = labeled[0] or 0
                win_cnt     = int(labeled[1] or 0)
        except Exception:
            total = labeled_cnt = win_cnt = 0

        win_rate = (win_cnt / labeled_cnt * 100) if labeled_cnt > 0 else 0
        return {
            "total_signals":  total,
            "labeled":        labeled_cnt,
            "win_count":      win_cnt,
            "win_rate_pct":   round(win_rate, 1),
            "model_loaded":   self._model is not None,
            "shadow_mode":    self._shadow,
            "threshold":      AI_THRESHOLD,
        }

    # ==================================================================
    # 내부 구현
    # ==================================================================

    def _dict_to_array(self, features: dict) -> np.ndarray:
        """Feature 딕셔너리 → 2D numpy array (모델 입력 형식)"""
        row = [float(features.get(col, 0) or 0) for col in FEATURE_COLS]
        return np.array([row])

    def _load_model(self):
        """저장된 .pkl 모델 파일 로드"""
        if Path(MODEL_PATH).exists():
            try:
                with open(MODEL_PATH, "rb") as f:
                    self._model = pickle.load(f)
                self.logger.info("AI 모델 로드 완료: %s", MODEL_PATH)

                # Shadow Mode 자동 해제: 모델이 있고 데이터도 충분하면 Active 전환
                if not SHADOW_MODE:
                    self._shadow = False
            except Exception as e:
                self.logger.warning("AI 모델 로드 실패 (Shadow Mode 유지): %s", e)
        else:
            self.logger.info("저장된 AI 모델 없음 → Shadow Mode 유지")

    def _save_model(self, model):
        """학습된 모델을 .pkl 파일로 저장"""
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(model, f)
        self.logger.info("AI 모델 저장 완료: %s", MODEL_PATH)

    def _fetch_train_data(self):
        """DB에서 라벨이 확정된 데이터를 X, y 형태로 로드"""
        if not Path(DB_PATH).exists():
            return None, None

        with sqlite3.connect(DB_PATH) as conn:
            cols_sql = ", ".join(FEATURE_COLS)
            rows = conn.execute(f"""
                SELECT {cols_sql}, label
                FROM ai_signals
                WHERE label IS NOT NULL
                ORDER BY id ASC
            """).fetchall()

        if len(rows) < 10:
            return None, None

        data = np.array(rows, dtype=float)
        X = data[:, :-1]
        y = data[:,  -1].astype(int)
        return X, y

    def _train_worker(self, force: bool):
        """백그라운드 학습 워커 (순수 Numpy 기반 Logistic Regression)"""
        self.logger.info("📚 AI 모델 재학습 시작... (Numpy LogReg)")
        try:
            Classifier = NumpyLogisticRegression
            clf_kwargs = {"lr": 0.05, "iters": 2000}

            X, y = self._fetch_train_data()
            if X is None:
                self.logger.warning("학습 데이터 없음 → 재학습 중단")
                return

            n_samples = len(y)
            if n_samples < MIN_TRAIN_SAMPLES and not force:
                self.logger.warning(
                    "학습 샘플 부족 (%d / %d) → Shadow Mode 유지. "
                    "데이터가 더 쌓이면 자동 전환됩니다.",
                    n_samples, MIN_TRAIN_SAMPLES
                )
                return

            # 클래스 불균형 처리를 위한 가중치 계산 (Numpy LogReg에는 미적용)
            win_ratio  = float(np.mean(y))
            
            # 학습 / 검증 분리 (간이 Numpy 구현)
            if n_samples >= 50:
                indices = np.random.permutation(n_samples)
                split_idx = int(n_samples * 0.8)
                train_idx, val_idx = indices[:split_idx], indices[split_idx:]
                X_tr, X_val = X[train_idx], X[val_idx]
                y_tr, y_val = y[train_idx], y[val_idx]
            else:
                X_tr, X_val, y_tr, y_val = X, X, y, y

            model = Classifier(**clf_kwargs)
            model.fit(X_tr, y_tr)

            # 검증 정확도 로그
            acc = float(np.mean(model.predict(X_val) == y_val)) * 100
            self.logger.info(
                "AI 재학습 완료 | 샘플: %d | 검증 정확도: %.1f%% | 승률(실측): %.1f%%",
                n_samples, acc, win_ratio * 100
            )

            # 모델 교체 (락 사용)
            with self._lock:
                self._model = model
                # 충분한 데이터가 있으면 Shadow → Active 자동 전환
                if n_samples >= MIN_TRAIN_SAMPLES and not SHADOW_MODE:
                    self._shadow = False
                    self.logger.info("✅ 데이터 충분 → AI가 Active 모드로 전환되었습니다!")

            self._save_model(model)

        except Exception as e:
            self.logger.error("AI 재학습 실패: %s", e, exc_info=True)
