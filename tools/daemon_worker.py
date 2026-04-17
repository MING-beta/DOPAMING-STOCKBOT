import sys
import os
import time
import schedule
from dotenv import load_dotenv

# 프로젝트 루트 참조
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_dir)

from utils.notifier import SystemNotifier
from tools.nightly_optimizer import NightlyOptimizer

def run_nightly_optimization():
    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Nightly Strategy Optimization 시작")
    opt = NightlyOptimizer()
    # 야간이므로 시뮬레이션을 충분히 부여 (trials 15)
    opt.run_optimization(trials=15)
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Nightly 최적화 종료 (.env 업데이트 완료)")

def send_morning_briefing():
    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 08:50 모닝 브리핑 수집 시작")
    # 최신화된 .env 강제 리로드
    env_path = os.path.join(base_dir, ".env")
    load_dotenv(dotenv_path=env_path, override=True)
    
    target_profit = float(os.getenv("TRADE_TARGET_PROFIT", "0.021")) * 100
    stop_loss = float(os.getenv("TRADE_STOP_LOSS", "-0.020")) * 100
    ai_thresh = float(os.getenv("AI_THRESHOLD", "0.38"))
    
    msg = f"""🌞 **[DOPAMING-STOCK-BOT] 장 시작 모닝 브리핑** 🌞
오늘의 투자를 위해 야간에 탐색된 최적의 파라미터가 시스템에 장착되었습니다.

📈 **목표 익절가**: +{target_profit:.1f}%
📉 **안전 손절가**: {stop_loss:.1f}%
🤖 **AI 예측 임계치**: {ai_thresh:.2f}

잠시 후 09:00분, 스캘핑 엔진이 활성화됩니다.
건승을 기원합니다! 🚀"""

    print("슬랙 및 텔레그램 발송을 시도합니다...")
    notifier = SystemNotifier()
    notifier.send_message_sync(msg)
    print("모닝 브리핑 발송 완료!")

def main():
    print("=========================================")
    print(" 🚀 DOPAMING 백그라운드 자동화 워커 기동 🚀")
    print("=========================================")
    print("등록된 스케줄:")
    print("- 매일 16:00 : Nightly Strategy Auto-Optimization")
    print("- 매일 08:50 : Slack Parameter Morning Briefing")
    print("=========================================")
    
    schedule.every().day.at("16:00").do(run_nightly_optimization)
    schedule.every().day.at("08:50").do(send_morning_briefing)
    
    print("\n[Running...] 백그라운드 대기열 진입 완료")
    
    try:
        while True:
            schedule.run_pending()
            time.sleep(30) # CPU 낭비 방지를 위해 30초 단위 체크
    except KeyboardInterrupt:
        print("\n백그라운드 워커를 종료합니다.")

if __name__ == "__main__":
    main()
