import logging

class VirtualBroker:
    """
    백테스팅용 가상 브로커. 
    현금 잔고, 보유 종목, 수수료 및 세금 시뮬레이션을 담당합니다.
    """
    def __init__(self, initial_balance=10000000, fee_rate=0.00015, tax_rate=0.002, slippage=0.00015):
        """
        Args:
            initial_balance (int): 초기 자본금
            fee_rate (float): 매수/매도 각각의 수수료율 (기본 0.015% -> 왕복 0.03%)
            tax_rate (float): 매도 시 세금 (기본 0.2%)
            slippage (float): 슬리피지 비율 (기본 0.015% -> 왕복 0.03%)
            # 총 제비용(Friction)은 .env의 TRADING_FRICTION 설정에 따라 run_backtest.py에서 조정됨
        """
        self.logger = logging.getLogger("DopamingBot.Backtest.VirtualBroker")
        self.balance = initial_balance
        self.initial_balance = initial_balance
        self.positions = {} # {code: {'buy_price': 0, 'qty': 0}}
        
        self.fee_rate = fee_rate
        self.tax_rate = tax_rate
        self.slippage = slippage
        
        self.total_fees = 0
        self.total_taxes = 0
        self.order_history = []
        
        # 상세 통계용 필드
        self.win_count = 0
        self.loss_count = 0
        self.total_profit = 0  # 이익 합계
        self.total_loss = 0    # 손실 합계
        self.peak_balance = initial_balance # MDD 계산용 고점
        self.max_drawdown = 0

    def buy(self, code, price, qty, dt, signal_type="상향돌파"):
        """매수 실행 (슬리피지 가산)"""
        # 슬리피지 반영 (매수는 좀 더 비싸게 산다고 가정)
        execution_price = price * (1.0 + self.slippage)
        total_cost = execution_price * qty
        fee = total_cost * self.fee_rate
        
        if self.balance < (total_cost + fee):
            # 잔고 부족 시 살 수 있는 최대 수량으로 조정 (간소화)
            qty = int(self.balance / (execution_price * (1.0 + self.fee_rate)))
            if qty <= 0: return False
            total_cost = execution_price * qty
            fee = total_cost * self.fee_rate

        self.balance -= (total_cost + fee)
        self.total_fees += fee
        
        if code in self.positions:
            old_qty = self.positions[code]['qty']
            old_price = self.positions[code]['buy_price']
            new_qty = old_qty + qty
            self.positions[code]['buy_price'] = ((old_price * old_qty) + (execution_price * qty)) / new_qty
            self.positions[code]['qty'] = new_qty
            # 추가 매수 시에도 최초 진입 시간을 기준으로 타임컷을 계산하기 위해 buy_time은 업데이트하지 않음
        else:
            self.positions[code] = {'buy_price': execution_price, 'qty': qty, 'buy_time': dt, 'signal_type': signal_type}
            
        self.order_history.append({
            'time': dt, 'code': code, 'type': 'BUY', 
            'price': price, 'exec_price': execution_price, 
            'qty': qty, 'fee': fee, 'tax': 0
        })
        
        # [v3.4] 매수 로그 기록
        self._log_trade_to_file(dt, code, 'BUY', execution_price, qty)
        
        return True

    def sell(self, code, price, qty, dt):
        """매도 실행 (슬리피지 차감 + 세금)"""
        if code not in self.positions or self.positions[code]['qty'] < qty:
            return False
            
        # 슬리피지 반영 (매도는 좀 더 싸게 판다고 가정)
        execution_price = price * (1.0 - self.slippage)
        total_proceeds = execution_price * qty
        fee = total_proceeds * self.fee_rate
        tax = total_proceeds * self.tax_rate
        
        self.balance += (total_proceeds - fee - tax)
        self.total_fees += fee
        self.total_taxes += tax
        
        # 손익 계산 (상세 통계용)
        buy_price = self.positions[code]['buy_price']
        pnl = (execution_price - buy_price) * qty - fee - tax
        if pnl > 0:
            self.win_count += 1
            self.total_profit += pnl
        else:
            self.loss_count += 1
            self.total_loss += abs(pnl)

        self.positions[code]['qty'] -= qty
        if self.positions[code]['qty'] <= 0:
            del self.positions[code]
            
        self.order_history.append({
            'time': dt, 'code': code, 'type': 'SELL', 
            'price': price, 'exec_price': execution_price, 
            'qty': qty, 'fee': fee, 'tax': tax, 'pnl': pnl
        })
        
        # [v3.4] 매도 로그 기록 (수익률 포함)
        self._log_trade_to_file(dt, code, 'SELL', execution_price, qty, pnl)
        
        return True

    def _log_trade_to_file(self, dt, code, type, price, qty, pnl=0):
        """매매 내역을 파일로 저장하여 정밀 분석 지원"""
        import os
        filename = "backtest_trades.csv"
        file_exists = os.path.isfile(filename)
        
        try:
            with open(filename, 'a', encoding='utf-8-sig') as f:
                if not file_exists:
                    f.write("Time,Code,Type,Price,Qty,PnL,ProfitRate(%)\n")
                
                pnl_rate = 0
                if type == 'SELL':
                    # 매도 가격에서 주당 수익금을 빼서 매수 단가 역산 (수익률 추정용)
                    buy_price = price - (pnl / qty) if qty > 0 else 0
                    pnl_rate = (pnl / (buy_price * qty)) * 100 if buy_price > 0 else 0
                
                f.write(f"{dt},{code},{type},{price:.1f},{qty},{pnl:.1f},{pnl_rate:.2f}\n")
        except Exception as e:
            self.logger.error(f"Failed to log trade to file: {e}")

    def get_total_asset_value(self, current_prices):
        """현재 가치 기준 총 자산 산출 (현금 + 주식 평가액)"""
        stock_value = 0
        for code, pos in self.positions.items():
            price = current_prices.get(code, pos['buy_price'])
            stock_value += price * pos['qty']
        
        total = self.balance + stock_value
        
        # MDD 갱신
        if total > self.peak_balance:
            self.peak_balance = total
        
        drawdown = (self.peak_balance - total) / self.peak_balance
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown
            
        return total

    def get_summary(self):
        # Profit Factor 계산
        profit_factor = self.total_profit / self.total_loss if self.total_loss > 0 else (999.0 if self.total_profit > 0 else 0)
        completed_trades = self.win_count + self.loss_count
        win_rate = (self.win_count / completed_trades * 100) if completed_trades > 0 else 0

        return {
            "initial_balance": self.initial_balance,
            "final_balance": self.balance,
            "total_fees": self.total_fees,
            "total_taxes": self.total_taxes,
            "order_count": len(self.order_history),
            "completed_trades": completed_trades,
            "win_rate": win_rate,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "max_drawdown": self.max_drawdown * 100,
            "profit_factor": profit_factor,
            "total_profit_sum": self.total_profit,
            "total_loss_sum": self.total_loss
        }
