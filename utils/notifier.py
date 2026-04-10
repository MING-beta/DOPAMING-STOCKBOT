import requests
import logging
import threading
import os
from dotenv import load_dotenv

class SlackNotifier:
    def __init__(self):
        self.logger = logging.getLogger("DopamingBot.SlackNotifier")
        load_dotenv()
        self.webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")

    def send_message(self, message: str):
        """Slack Webhook을 통해 비동기 스레드로 메시지를 전송합니다."""
        if not self.webhook_url or "YOUR/WEBHOOK/URL" in self.webhook_url:
            # Windows(CP949) 터미널 출력 시 이모지에 의한 로거 크래시(UnicodeEncodeError) 방지
            safe_msg = message.encode('cp949', 'replace').decode('cp949')
            self.logger.warning(f"Slack 통지 (URL 미설정): {safe_msg}")
            return
            
        def _send():
            try:
                payload = {"text": message}
                response = requests.post(self.webhook_url, json=payload, timeout=5)
                if response.status_code != 200:
                    self.logger.error(f"Slack 통지 실패: HTTP {response.status_code}")
            except Exception as e:
                self.logger.error(f"Slack 통지 발송 중 에러 발생: {e}")
                
        # 메인 스레드 블로킹 방지를 위해 백그라운드로 보냄
        threading.Thread(target=_send, daemon=True).start()

    def send_message_sync(self, message: str):
        """Slack Webhook을 통해 동기식으로 메시지를 전송합니다 (프로그램 종료 직전용)."""
        if not self.webhook_url or "YOUR/WEBHOOK/URL" in self.webhook_url:
            safe_msg = message.encode('cp949', 'replace').decode('cp949')
            self.logger.warning(f"Slack 통지 (URL 미설정/동기): {safe_msg}")
            return
        try:
            payload = {"text": message}
            requests.post(self.webhook_url, json=payload, timeout=5)
        except Exception as e:
            self.logger.error(f"Slack 통지 발송 중 에러 발생: {e}")
