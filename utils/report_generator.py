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
        pnl = summary['realized_pnl']
        pnl_rate = (pnl / initial_assets * 100) if initial_assets > 0 else 0
        
        # 승률 계산
        win_count = 0
        for code, detail in summary['stock_details'].items():
            if detail['pnl'] > 0:
                win_count += 1
        
        total_stocks = len(summary['stock_details'])
        win_rate = (win_count / total_stocks * 100) if total_stocks > 0 else 0
        
        # 이모지 결정
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        
        # 리포트 구성 시작
        report = []
        report.append(f"🗓️ *[{now}] 일일 매매 성적표*")
        report.append("=" * 30)
        report.append(f"{pnl_emoji} *당일 실현 손익: {int(pnl):+,}원 ({pnl_rate:+.2f}%)*")
        report.append(f"🎯 *당일 매매 승률: {win_rate:.1f}%* ({win_count}/{total_stocks} 종목 수익)")
        report.append(f"💳 *거래 규모: 매수 {int(summary['buy_total_amt']):,}원 | 매도 {int(summary['sell_total_amt']):,}원*")
        report.append("")
        
        # 종목별 상세 랭킹 산출
        details = []
        for code, data in summary['stock_details'].items():
            name = code_to_name_map.get(code, code)
            details.append({'name': name, 'pnl': data['pnl']})
            
        # 수익순 정렬
        details.sort(key=lambda x: x['pnl'], reverse=True)
        
        # Best Top 3
        best = [d for d in details if d['pnl'] > 0][:3]
        if best:
            report.append("🏆 *오늘의 효자 종목 (TOP 3)*")
            for i, d in enumerate(best):
                report.append(f"{i+1}. {d['name']}: {int(d['pnl']):+,}원")
            report.append("")

        # Worst Top 3
        worst = [d for d in details if d['pnl'] < 0][-3:]
        worst.reverse()
        if worst:
            report.append("⚠️ *아쉬운 종목 (Worst 3)*")
            for i, d in enumerate(worst):
                report.append(f"{i+1}. {d['name']}: {int(d['pnl']):+,}원")
            report.append("")
            
        report.append("=" * 30)
        report.append("_오늘도 수고하셨습니다. 내일도 성투하세요!_")
        
        return "\n".join(report)
