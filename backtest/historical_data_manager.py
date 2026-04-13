import pandas as pd
import os

class HistoricalDataManager:
    """백테스트용 데이터 관리자. CSV 데이터를 로드하고 공급합니다."""
    def __init__(self, data_dir="data/historical"):
        self.data_dir = data_dir
        self.data_cache = {}

    def load_code_data(self, code):
        file_path = os.path.join(self.data_dir, f"{code}_1m.csv")
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"데이터 파일 없음: {file_path}")
            
        df = pd.read_csv(file_path, index_col=0, parse_dates=True)
        # 컬럼명 소문자로 통일 (open, high, low, close, volume)
        if 'date_idx' in df.columns:
            df = df.set_index('date_idx')
            
        self.data_cache[code] = df
        return df

    def get_time_range(self):
        """수집된 데이터들의 공통 시간 범위를 반환"""
        all_indices = []
        for df in self.data_cache.values():
            all_indices.append(df.index)
        
        if not all_indices: return None
        
        # 교집합 시간대 찾기 (단일 종목이면 그냥 그 범위)
        common_idx = all_indices[0]
        for idx in all_indices[1:]:
            common_idx = common_idx.intersection(idx)
            
        return common_idx.sort_values()

    @staticmethod
    def resample_to_5m(df_1m):
        """1분봉을 5분봉으로 리샘플링"""
        return df_1m.resample('5T').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
