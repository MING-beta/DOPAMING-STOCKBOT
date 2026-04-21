import sys
from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget

app = QApplication(sys.argv)
kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
def on_connect(*args):
    for code in ['001510', '096040']:
        name = kiwoom.dynamicCall("GetMasterCodeName(QString)", code)
        state = kiwoom.dynamicCall("GetMasterStockState(QString)", code)
        print(f"[{code}] {name}: {state}")
    sys.exit()

kiwoom.OnEventConnect.connect(on_connect)
kiwoom.dynamicCall("CommConnect()")
app.exec_()
