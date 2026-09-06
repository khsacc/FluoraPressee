import sys
import os

sys.stdout.reconfigure(errors="replace")
sys.stderr.reconfigure(errors="replace")

os.environ["QT_OPENGL"] = "software"
import gc
gc.enable()

from PyQt6.QtWidgets import QApplication

# Import the required classes and functions from ui.py
from src.ui import SpectrometerGUI
from src.app_bootstrap import print_software_and_author_info, check_and_create_config
from src.ui.theme import apply_application_style

def main():

    print_software_and_author_info()
    check_and_create_config()
    

    debug_mode = "--debug" in sys.argv
    print("debug_mode ", debug_mode)
    
    # Reuse the existing QApplication instance if one is already running, otherwise create one
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    apply_application_style(app)

    # SpectrometerGUI builds its own GuiBridge (see its __init__).
    window = SpectrometerGUI(debug=debug_mode)
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
