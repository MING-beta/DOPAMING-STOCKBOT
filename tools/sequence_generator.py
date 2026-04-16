import pandas as pd
import numpy as np
import os
import sys

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_dir)

class SequenceGenerator:
    """
    LSTM 딥러닝 모델 학습 및 추론을 위한 시계열(Time-series) 데이트를
    3차원 텐서 (N, Window_Size, Features) 형태로 변환하는 전처리 생성기입니다.
    """
    def __init__(self, window_size=120):
        self.window_size = window_size
        
    def create_sliding_windows(self, df: pd.DataFrame) -> np.ndarray:
        """
        단일 종목의 DataFrame을 받아 (N, window_size, features) 형식의 3D 배열(NumPy)로 생성합니다.
        추후 ONNX Runtime 혹은 C++ 확장 추론 엔진을 통과할 때 입력값 Shape 로 사용됩니다.
        """
        # 신경망이 패턴을 학습할 때 사용할 필수 핵심 피처들 (정규화 전 단계)
        feature_cols = ['current_price', 'volume', 'volume_ratio'] 
        
        # DataFrame 에 실제 존재하는 컬럼만 필터링
        actual_cols = [col for col in feature_cols if col in df.columns]
        if not actual_cols:
            return np.array([])
            
        # 결측치를 처리하고 스칼라 배열로 변환
        data_matrix = df[actual_cols].fillna(method='ffill').fillna(0).values
        n_samples = len(data_matrix) - self.window_size
        
        if n_samples <= 0:
            return np.array([]) # 윈도우 사이즈보다 짧은 상장 초기나 장애 데이터
            
        # 슬라이딩 윈도우 방식을 활용하여 1틱씩 밀려가며 시계열 덩어리(블록) 생성
        X = []
        for i in range(n_samples):
            window = data_matrix[i : i + self.window_size]
            X.append(window)
            
        return np.array(X)

if __name__ == "__main__":
    print("==============================================")
    print(" 🧠 LSTM Sequence Generator (3D Tensor) 🧠")
    print("==============================================")
    print(f"- Window Size 셋팅값: {SequenceGenerator().window_size} 개 캔들")
    print("- 추후 PyTorch Train 및 32-bit ONNX 추론 라우터에 물리게 될 코어 전처리 모듈입니다.")
