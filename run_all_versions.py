import os
import subprocess
import sys
import re

base_dir = os.path.dirname(os.path.abspath(__file__))
data_root = os.path.join(base_dir, "data")

# 폴더 목록 (디렉토리만 필터링)
directories = [d for d in os.listdir(data_root) if os.path.isdir(os.path.join(data_root, d))]
directories = sorted(directories)

print(f"============================================================")
print(f" [CROSS-VALIDATION] ALL HISTORICAL DATA TESTS")
print(f"============================================================")
print(f"발견된 디렉토리: {', '.join(directories)}")
print("")

results = []

for idx, folder in enumerate(directories):
    print(f"({idx+1}/{len(directories)}) [ {folder} ] 타겟 백테스트 진행 중...")
    
    # 1초마다 출력하기 보다는 끝나고 파싱해서 보여줌
    cmd = [sys.executable, "-u", "run_active_backtest.py", folder]
    result = subprocess.run(cmd, cwd=base_dir, capture_output=True, text=True)
    
    out = result.stdout
    
    # 정규식으로 필요한 지표 추출
    roi_match = re.search(r"실전 체감 수익률\(ROI\):\s([+-]?\d+\.\d+)%", out)
    net_match = re.search(r"순수 창출 수익금:\s([+-]?[\d,]+)\s원", out)
    win_match = re.search(r"평균 승률:\s([\d\.]+)%", out)
    pf_match = re.search(r"포트폴리오 PF:\s([\d\.]+)", out)
    trade_match = re.search(r"총 주문 횟수:\s(\d+)\s회", out)
    
    if roi_match:
        roi = float(roi_match.group(1))
        net = net_match.group(1) if net_match else "0"
        win = float(win_match.group(1)) if win_match else 0.0
        pf = float(pf_match.group(1)) if pf_match else 0.0
        trades = int(trade_match.group(1)) if trade_match else 0
        
        status = "[PASS]" if roi > 0 else "[FAIL]"
        
        row = f"[{folder[:22]:<22}] {status} | ROI: {roi:+.2f}% | NET: {net:>11s}원 | 승률: {win:5.1f}% | PF: {pf:.2f} | 타점: {trades}회"
        print("  ->", row)
        results.append(row)
    else:
        # 데이터가 없을 경우 등
        err_msg = f"[{folder[:22]:<22}] [SKIP]  | 데이터 없음 혹은 에러 발생"
        print("  ->", err_msg)
        results.append(err_msg)

print(f"\n============================================================")
print(f" [RESULT SUMMARY]")
print(f"============================================================")
for r in results:
    print(r)
print(f"============================================================")
