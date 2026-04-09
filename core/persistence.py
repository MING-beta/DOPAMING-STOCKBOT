import sqlite3
import logging
import threading
import queue
from datetime import datetime
import os

class DatabaseManager:
    def __init__(self, db_path="executions.db"):
        self.logger = logging.getLogger("DopamingBot.DatabaseManager")
        self.db_path = db_path
        self.queue = queue.Queue()
        self.lock = threading.Lock()
        
        self.is_running = False
        self.worker_thread = None
        
        # 테이블 초기화 및 스레드 시작
        self._init_db()
        self.start()

    def _init_db(self):
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            # 체결 내역 테이블 생성
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS executions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_no TEXT NOT NULL,
                    code TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    qty INTEGER NOT NULL,
                    order_type TEXT NOT NULL, -- '매수', '익절', '손절', '기타매도'
                    dt_time TEXT NOT NULL,
                    date_key TEXT NOT NULL
                )
            ''')
            conn.commit()
            conn.close()

    def start(self):
        if self.is_running: return
        self.is_running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def stop(self):
        self.is_running = False
        if self.worker_thread:
            self.worker_thread.join()

    def insert_execution(self, order_no, code, price, qty, order_type, dt_time=None):
        """체결 발생 시 큐에 삽입 (메인 스레드 비-블로킹)"""
        if dt_time is None:
            dt_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
        date_key = datetime.now().strftime("%Y%m%d")
        
        self.queue.put({
            'order_no': order_no,
            'code': code,
            'price': price,
            'qty': qty,
            'order_type': order_type,
            'dt_time': dt_time,
            'date_key': date_key
        })

    def _worker_loop(self):
        """큐를 폴링하며 DB에 INSERT 연산 수행"""
        while self.is_running:
            try:
                data = self.queue.get(timeout=0.1)
                with self.lock:
                    conn = sqlite3.connect(self.db_path)
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO executions (order_no, code, price, qty, order_type, dt_time, date_key)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (data['order_no'], data['code'], data['price'], data['qty'], 
                          data['order_type'], data['dt_time'], data['date_key']))
                    conn.commit()
                    conn.close()
                self.queue.task_done()
            except queue.Empty:
                pass
            except Exception as e:
                self.logger.error(f"DB 저장 워커 스레드 에러: {e}")

    def load_todays_open_positions(self):
        """
        당일 매수한 종목 중 청산되지 않은(매도가 없는) 잔고 목록 반환
        반환 형태: { '코드': {'buy_price': 평균단가(또는 최초단가), 'qty': 잔여수량} }
        """
        today_key = datetime.now().strftime("%Y%m%d")
        positions = {}
        
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 당일 거래 내역 조회
            cursor.execute('SELECT code, price, qty, order_type FROM executions WHERE date_key = ? ORDER BY id ASC', (today_key,))
            rows = cursor.fetchall()
            conn.close()
            
        for row in rows:
            code, price, qty, order_type = row
            if order_type == '매수':
                if code not in positions:
                    positions[code] = {'buy_price': price, 'qty': qty}
                else:
                    # 간단한 물타기 평단가 계산 (복수 주문 처리)
                    old_qty = positions[code]['qty']
                    old_price = positions[code]['buy_price']
                    new_qty = old_qty + qty
                    new_price = ((old_price * old_qty) + (price * qty)) / new_qty
                    positions[code] = {'buy_price': new_price, 'qty': new_qty}
            elif order_type in ['익절', '손절', '기타매도']:
                if code in positions:
                    positions[code]['qty'] -= qty
                    # 전략에 따라 수량이 0 이하면 포지션 삭제
                    if positions[code]['qty'] <= 0:
                        del positions[code]
                        
        return positions

    def get_daily_summary(self):
        """당일 총 매수/매도액, 실현 손익, 승률 및 종목별 상세 내역 반환"""
        today_key = datetime.now().strftime("%Y%m%d")
        summary = {
            'buy_count': 0, 'sell_count': 0,
            'buy_total_amt': 0, 'sell_total_amt': 0,
            'realized_pnl': 0,
            'stock_details': {} # {code: {'pnl': 0, 'buy_amt': 0, 'sell_amt': 0, 'trades': 0}}
        }
        
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT code, price, qty, order_type FROM executions WHERE date_key = ?', (today_key,))
            rows = cursor.fetchall()
            conn.close()
            
        temp_pos = {} # {code: {avg_price, qty}}
        
        for row in rows:
            code, price, qty, order_type = row
            if code not in summary['stock_details']:
                summary['stock_details'][code] = {'pnl': 0, 'buy_amt': 0, 'sell_amt': 0, 'trades': 0}
            
            detail = summary['stock_details'][code]
            detail['trades'] += 1
            
            if order_type == '매수':
                summary['buy_count'] += 1
                amt = price * qty
                summary['buy_total_amt'] += amt
                detail['buy_amt'] += amt
                
                if code not in temp_pos:
                    temp_pos[code] = {'avg_price': price, 'qty': qty}
                else:
                    curr = temp_pos[code]
                    new_qty = curr['qty'] + qty
                    curr['avg_price'] = ((curr['avg_price'] * curr['qty']) + (price * qty)) / new_qty
                    curr['qty'] = new_qty
            else:
                summary['sell_count'] += 1
                amt = price * qty
                summary['sell_total_amt'] += amt
                detail['sell_amt'] += amt
                
                if code in temp_pos:
                    avg_buy_price = temp_pos[code]['avg_price']
                    pnl = (price - avg_buy_price) * qty
                    summary['realized_pnl'] += pnl
                    detail['pnl'] += pnl
                    temp_pos[code]['qty'] -= qty
        
        return summary
