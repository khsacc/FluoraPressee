import sys
import os
import json
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from src.ui.theme import apply_application_style

def print_software_and_author_info():
    print(
        "\n================================================================================\n================================================================================\n"\
        "FluoRaPressée: Spectrometer Control & Analysis for high-pressure experiments\nHiroki Kobayashi (The University of Tokyo), 2026\n"\
        "https://github.com/khsacc/FluoraPressee\n"\
        "================================================================================\n================================================================================\n"
    )

def check_and_create_config():
    """Run ConfigWizard to create spectrometerConfig.json, but only on first launch.

    This runs **before SpectrometerGUI is constructed**, so the measurement-rights
    exclusion gate (SpectrometerGUI._acquisition_gate) does not exist yet. The API
    server is not running at this point either, so there is no competitor to
    exclude. ConfigWizard is therefore deliberately out of scope for the exclusion
    gate (work_API_standby.md 方針2 "対象外"). Do not "fix" this later by making it
    take the gate.
    """
    config_path = "spectrometerConfig.json"
    if os.path.exists(config_path):
        return

    app_temp = QApplication.instance()
    if not app_temp:
        app_temp = QApplication(sys.argv)
    apply_application_style(app_temp)

    from src.ui.config_wizard import ConfigWizard
    wizard = ConfigWizard()
    if wizard.exec() == QDialog.DialogCode.Accepted:
        config = wizard.result_config()
    else:
        QMessageBox.information(
            None,
            "Setup cancelled",
            "Setup wizard was cancelled.\n"
            "The application will now close without creating spectrometerConfig.json.",
        )
        sys.exit(0)

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        print(f"spectrometerConfig.json created: model={config.get('model')}")
    except Exception as e:
        QMessageBox.warning(None, "Warning", f"Failed to save config file:\n{e}")
