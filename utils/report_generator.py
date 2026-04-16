"""
report_generator.py
-------------------
DatabaseManager로부터 전달받은 일일 매매 요약 데이터를 분석하여
슬랙(Slack) 메시지 형식에 맞게 가독성 높은 리포터 문장을 생성합니다.
"""

from datetime import datetime

class ReportGenerator:
    @staticmethod
    def generate_markdown_report(summary, initial_assets, code_to_name_map=None):
        """
        일일 요약 데이터를 바탕으로 마크다운 형식의 슬랙 리포트 생성
        """
        if code_to_name_map is None:
            code_to_name_map = {}

        now = datetime.now().strftime("%Y-%m-%d")
        pnl = summary.get('realized_pnl', 0)
        pnl_rate = (pnl / initial_assets * 100) if initial_assets > 0 else 0
        
        # 승률 및 디테일 파악
        win_count = 0
        trade_count = summary.get('trade_count', 0)
        
        for code, detail in summary.get('stock_details', {}).items():
            if detail['pnl'] > 0: win_count += 1
            
        total_stocks = len(summary.get('stock_details', {}))
        # 만약 trade_count 정보가 없다면 거래 종목 수로 대체
        actual_trades = trade_count if trade_count > 0 else total_stocks
        win_rate = (win_count / total_stocks * 100) if total_stocks > 0 else 0
        
        # 성적 기반 다이내믹 이모지
        if pnl_rate >= 1.0: header_emoji = "🚀 (초대박)"
        elif pnl_rate > 0:  header_emoji = "🔥 (수익달성)"
        elif pnl_rate == 0: header_emoji = "💤 (보합휴식)"
        elif pnl_rate > -1.0: header_emoji = "☔ (방어성공)"
        else:               header_emoji = "💀 (심각위험)"
        
        # 리포트 구성 시작
        report = []
        report.append(f"🤖 *DOPAMING BOT | DAILY REPORT* 🤖")
        report.append("════════════════════════════")
        report.append(f"> 🗓️ *Date:* {now}")
        report.append(f"> 💸 *Net Profit:* *{int(pnl):+,} 원* ({pnl_rate:+.2f}%) {header_emoji}")
        report.append(f"> 🎯 *Win Rate:* *{win_rate:.1f}%* ({win_count} Wins / {total_stocks} Stocks)")
        report.append(f"> ⚡ *Trades:* {actual_trades} 회 진입")
        report.append(f"> 💳 *Volume:* 매수 {int(summary.get('buy_total_amt',0)):,} 원 | 매도 {int(summary.get('sell_total_amt',0)):,} 원")
        report.append("════════════════════════════")
        report.append("")
        
        # 종목별 상세 랭킹 산출
        details = []
        for code, data in summary.get('stock_details', {}).items():
            name = code_to_name_map.get(code, code)
            details.append({'name': name, 'pnl': data['pnl']})
            
        details.sort(key=lambda x: x['pnl'], reverse=True)
        
        # Best Top 3
        best = [d for d in details if d['pnl'] > 0][:3]
        if best:
            report.append("🏆 *TOP 3 BEST (효자 등극)*")
            for i, d in enumerate(best):
                report.append(f"> *{i+1}. {d['name']}* : `+{int(d['pnl']):,} 원`")
            report.append("")

        # Worst Top 3
        worst = [d for d in details if d['pnl'] < 0][-3:]
        worst.reverse()
        if worst:
            report.append("⚠️ *TOP 3 WORST (원흉 분쇄)*")
            for i, d in enumerate(worst):
                report.append(f"> *{i+1}. {d['name']}* : `-{abs(int(d['pnl'])):,} 원`")
            report.append("")
            
        report.append("_Data Driven Scalping by DOPAMING_")
        
        return "\n".join(report)
