import logging
import os
from datetime import datetime
from PyQt5.QtCore import QObject, pyqtSignal

class LogSignal(QObject):
    """로깅 메시지를 GUI 스레드로 전달하기 위한 시그널 객체"""
    new_log = pyqtSignal(str)

class QTextEditLogger(logging.Handler):
    """
    logging.Handler를 상속받아, 파이썬 표준 로그를 PyQt 시그널로 내보내는 핸들러.
    직접 UI 객체를 건드리지 않고 시그널만 방출(Emit)합니다.
    """
    def __init__(self):
        super().__init__()
        self.signal = LogSignal()

    def emit(self, record):
        try:
            msg = self.format(record)
            self.signal.new_log.emit(msg)
        except Exception:
            self.handleError(record)

def setup_logger(name="KiwoomBot", log_dir="logs"):
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # 로거에 핸들러가 이미 있다면 중복 추가 방지
    if logger.hasHandlers():
        return logger

    # 포맷 설정
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # 1. 콘솔 핸들러: INFO 레벨 이상 콘솔 출력
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    
    # 2. 파일 핸들러: DEBUG 레벨 이상 모든 로그 파일 기록
    today = datetime.now().strftime("%Y%m%d")
    log_file = os.path.join(log_dir, f"system_{today}.log")
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # 핸들러 등록
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger

def add_gui_logger(logger_name="DopamingBot"):
    """
    기존에 설정된 로거에 GUI용 핸들러를 부착하여 반환합니다.
    """
    logger = logging.getLogger(logger_name)
    gui_handler = QTextEditLogger()
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s') # 화면용으론 간소화
    gui_handler.setFormatter(formatter)
    logger.addHandler(gui_handler)
    return gui_handler
