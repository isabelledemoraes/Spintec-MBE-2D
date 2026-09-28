import sys
from PyQt6.QtWidgets import QApplication
from .GUIv5 import MainWindow   # note the relative import

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()