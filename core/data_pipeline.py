"""
DataPipeline 모듈
--------------------
실시간 주식 체결 틱(Tick) 데이터를 큐(Queue)로 비동기 수신하고,
내부 백그라운드 워커 스레드를 통해 1분봉 및 5분봉 등 다중 타임프레임(DataFrame)으로 리샘플링하여
메모리에 보관/제공하는 역할을 수행합니다.
"""

import threading
import queue
import time
import pandas as pd
import logging
from datetime import datetime

class DataPipeline:
    """
    고속으로 들어오는 틱 데이터를 처리하고, 스레드 안전(Thread-Safe)하게
    전략 엔진 모듈이 데이터를 소비할 수 있도록 중계하는 파이프라인.
    """
    def __init__(self):
        self.logger = logging.getLogger("DopamingBot.DataPipeline")
        
        # 틱 데이터를 받을 큐
        self.tick_queue = queue.Queue()
        
        # 종목코드별 데이터 프레임을 저장할 딕셔너리
        self.data_1m = {}
        self.data_5m = {}
        self.reference_prices = {}
        self.day_stats = {} # {code: {'high': 0, 'low': 0, 'volume': 0}}
        
        # 데이터 접근 시 Thread-Safety 보장을 위한 Lock
        self.lock = threading.Lock()
        
        # 파이프라인 워커 스레드 제어 플래그
        self.is_running = False
        self.worker_thread = None

    def start_pipeline(self):
        """파이프라인 백그라운드 스레드 시작"""
        if self.is_running:
            return
            
        self.is_running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        self.logger.info("백그라운드 데이터 파이프라인 시작됨")

    def stop_pipeline(self):
        """파이프라인 스레드 정지"""
        self.is_running = False
        if self.worker_thread:
            self.worker_thread.join()
        self.logger.info("데이터 파이프라인 정지됨")

    def add_tick_data(self, code, dt_time, price, volume):
        """메인 스레드(Kiwoom)에서 틱이 수신되면 큐에 삽입 (Non-blocking)"""
        # 체결시간 포맷 처리 (키움은 HHMMSS 또는 YYYYMMDDHHMMSS 로 옴)
        self.tick_queue.put({
            'code': code,
            'time': dt_time,
            'price': float(abs(price)),
            'volume': float(abs(volume))
        })

    def get_data(self, code):
        """
        전략 로직(StefanoStrategy 등)이나 외부 모듈에서 1분/5분봉 데이터를 안전하게 읽기 위해 사용합니다.
        스레드 락(Lock)을 걸어 데이터 복사본(copy)을 반환하므로 읽기 도중 수정되는 것을 방지합니다.
        
        Args:
            code (str): 조회할 종목코드
            
        Returns:
            tuple(pd.DataFrame, pd.DataFrame): 1분봉 데이터프레임, 5분봉 데이터프레임의 깊은 복사본
        """
        with self.lock:
            df_1m = self.data_1m.get(code, pd.DataFrame()).copy()
            df_5m = self.data_5m.get(code, pd.DataFrame()).copy()
        return df_1m, df_5m

    def _worker_loop(self):
        """백그라운드에서 주기적으로 큐를 비우며 DataFrame을 업데이트/리샘플링"""
        while self.is_running:
            try:
                # 큐에 데이터가 있을 때까지 대기 (최대 0.1초)
                tick = self.tick_queue.get(timeout=0.1)
                code = tick['code']
                
                # datetime 형태 변환 테스트 (임시 로직: 들어오는 시간 포맷에 따라 수정 필요)
                # 우선 현재 시간으로 강제 파싱 (테스트 목적) 또는 들어온 포맷에 맞춰 파싱
                dt = tick['time']
                if isinstance(dt, str):
                    if len(dt) == 6: # HHMMSS
                        now = datetime.now()
                        dt = datetime(now.year, now.month, now.day, int(dt[:2]), int(dt[2:4]), int(dt[4:6]))
                
                new_row = pd.DataFrame([{
                    'price': tick['price'],
                    'volume': tick['volume']
                }], index=[dt])

                with self.lock:
                    if code not in self.data_1m:
                        # 초기 생성: 시간 인덱스의 빈 DataFrame
                        self.data_1m[code] = pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
                    
                    # 1. 원본 틱 데이터를 1분봉 데이터 프레임의 현재 분(minute) 위치에 병합/업데이트
                    # 실무에서는 tick dataframe을 별도 유지하고 resample 하는 것이 정확하지만,
                    # 메모리 관리를 위해 1분 단위로 바로 처리
                    
                    # 현재 틱의 분(Minute) 단위 Floor 처리
                    minute_index = dt.replace(second=0, microsecond=0)
                    
                    df = self.data_1m[code]
                    if minute_index in df.index:
                        # 기존 캔들 업데이트
                        df.at[minute_index, 'high'] = max(df.at[minute_index, 'high'], tick['price'])
                        df.at[minute_index, 'low'] = min(df.at[minute_index, 'low'], tick['price'])
                        df.at[minute_index, 'close'] = tick['price']
                        df.at[minute_index, 'volume'] += tick['volume']
                    else:
                        # 새로운 1분 캔들 생성
                        df.loc[minute_index] = {
                            'open': tick['price'],
                            'high': tick['price'],
                            'low': tick['price'],
                            'close': tick['price'],
                            'volume': tick['volume']
                        }
                    
                    # 2. 1분봉 기반으로 5분봉 resample 연산
                    # [최적화] 매 틱마다 전체 리샘플링을 하지 않고, 1분 단위 인덱스가 새로 생성되었을 때만 수행
                    if code not in self.data_5m or self.data_5m[code].index[-1] < minute_index:
                        self.data_5m[code] = self.data_1m[code].resample('5T').agg({
                            'open': 'first',
                            'high': 'max',
                            'low': 'min',
                            'close': 'last',
                            'volume': 'sum'
                        }).dropna()

                    # 3. 당일 통계 정보 업데이트 (고가, 저가, 누적 거래량)
                    if code not in self.day_stats:
                        self.day_stats[code] = {'high': tick['price'], 'low': tick['price'], 'volume': tick['volume']}
                    else:
                        stats = self.day_stats[code]
                        stats['high'] = max(stats['high'], tick['price'])
                        stats['low'] = min(stats['low'], tick['price'])
                        stats['volume'] += tick['volume']

                self.tick_queue.task_done()
                
            except queue.Empty:
                pass
            except Exception as e:
                self.logger.error(f"파이프라인 워커 스레드 에러: {e}")

    def add_historical_data(self, code, history_df_1m):
        """
        초기화 시 대량의 과거 1분봉 데이터를 한 번에 밀어넣습니다.
        실시간으로 이미 수신된 데이터와 중복될 수 있으므로 병합 후 정렬합니다.
        """
        if history_df_1m.empty:
            return
            
        with self.lock:
            if code not in self.data_1m:
                self.data_1m[code] = history_df_1m
            else:
                # 합치고, 인덱스 기준으로 중복 시 최신(마지막) 데이터 유지, 그리고 인덱스 정렬
                combined = pd.concat([self.data_1m[code], history_df_1m])
                combined = combined[~combined.index.duplicated(keep='last')]
                self.data_1m[code] = combined.sort_index()

            # 5분봉 재가공
            self.data_5m[code] = self.data_1m[code].resample('5T').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna()
            
        self.logger.info(f"[{code}] 과거 차트 데이터 적재 완료: 1분봉 {len(self.data_1m[code])}개")

    def remove_code(self, code):
        """
        조건검색 이탈 종목을 파이프라인 메모리에서 완전히 제거합니다.
        더 이상 실시간 틱을 받지 않으므로 메모리 낭비를 방지합니다.
        """
        with self.lock:
            removed = False
            if code in self.data_1m:
                del self.data_1m[code]
                removed = True
            if code in self.data_5m:
                del self.data_5m[code]
                removed = True
        if removed:
            self.logger.info(f"[{code}] 조건 이탈 → 파이프라인 데이터 제거 완료")

