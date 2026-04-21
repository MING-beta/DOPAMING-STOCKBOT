import sys, os, logging, time, json, importlib
base_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(base_dir)

import backtest.engine as eng_mod
import strategy.stefano_strategy as strat_mod
import core.ai_engine as ai_mod
from backtest.virtual_broker import VirtualBroker
from backtest.historical_data_manager import HistoricalDataManager

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("DopamingBot")

# Force setup
os.environ["STRATEGY_MODE"] = "BREAKOUT"
os.environ["STRATEGY_BYPASS_MACRO"] = "True"
os.environ["AI_THRESHOLD"] = "0.0"

manager = HistoricalDataManager()
data_dir = os.path.join(base_dir, "data", "historical")
codes = sorted([f.replace("_1m.csv", "") for f in os.listdir(data_dir) if f.endswith("_1m.csv")])

for code in codes[:1]: # test single stock
    df = manager.load_code_data(code)
    broker = VirtualBroker(initial_balance=5000000)
    strategy = strat_mod.StefanoStrategy()
    ai_engine = ai_mod.AIEngine()
    strategy.set_ai_modules(ai_engine, None)
    engine = eng_mod.BacktestEngine(broker, strategy)
    engine.run(code, df)
    
print("DONE")
