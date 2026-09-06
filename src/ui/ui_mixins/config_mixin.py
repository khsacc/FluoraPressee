import json

from PyQt6.QtWidgets import QMessageBox

from src.ui.menu.hardware_config_dialog import HardwareConfigDialog
from src.ui.menu.instrument_status_dialog import InstrumentStatusDialog
from src.ui.menu.configuration_manager_dialog import ConfigurationManagerDialog
from src.core.api_clients import load_clients, remove_legacy_key_file, save_clients
from src.ui.local_cache import load_local_cache, save_local_cache
from src.ui.ui_mixins.acquisition_mixin import GateBusyError

class ConfigMixin:
    def on_open_hardware_config_clicked(self):
        temperature_control_available = (
            self._temp_control_available
            if getattr(self, "_temp_capability_known", False)
            else None
        )
        dialog = HardwareConfigDialog(
            self.config,
            temperature_control_available=temperature_control_available,
            parent=self,
        )
        dialog.applied.connect(self._on_hardware_config_applied)
        # This dialog exists to change settings, so it is correct to hold the
        # exclusion gate for as long as it is open (work_API_standby.md 表#9).
        try:
            with self.acquisition_gate():
                dialog.exec()
        except GateBusyError:
            QMessageBox.warning(
                self, "Busy",
                "A remote operation is in progress, so the hardware configuration "
                "dialog cannot be opened right now."
            )

    def on_open_camera_status_clicked(self):
        if self.instrument_status_window is None:
            self.instrument_status_window = InstrumentStatusDialog(
                self.thread,
                self.spec_ctrl,
                busy_check=self._instrument_status_busy,
                # 表#10: this dialog performs live hardware queries
                # (camera_thread.request_status() and spec_ctrl.get_status_snapshot()).
                # It is modeless, though, so the gate is held only for the duration of
                # one refresh cycle, not for as long as the dialog itself is open.
                gate_acquire=self._try_acquire_gate,
                gate_release=self._release_acquisition_gate,
                parent=self,
            )
        self.instrument_status_window.show()
        self.instrument_status_window.raise_()
        self.instrument_status_window.activateWindow()

    def on_open_configuration_manager_clicked(self):
        dialog = ConfigurationManagerDialog(
            self.configuration_catalog,
            active_configuration_id=self.active_configuration_id,
            positioned_configuration_id=self.positioned_configuration_id,
            ui_lock_check=lambda: self._ui_is_locked(),
            parent=self,
        )
        dialog.exec()
        if dialog.active_configuration_was_deleted:
            self.clear_active_configuration()

    def on_open_analysis_mode_clicked(self):
        # Lazy import: Analysis Mode's widgets aren't needed until this menu action is
        # used, and keeping the import out of module load time keeps src/ui.py's own
        # startup path unaffected by src/analysis_ui.py.
        from src.ui.analysis_ui import AnalysisWindow

        if self.analysis_window is None:
            self.analysis_window = AnalysisWindow()
        self.analysis_window.show()
        self.analysis_window.raise_()
        self.analysis_window.activateWindow()

    def _instrument_status_busy(self):
        camera_busy = bool(getattr(self.thread, "is_measuring", False))
        move_thread = getattr(self, "spec_move_thread", None)
        spectrograph_busy = move_thread is not None and move_thread.isRunning()
        return camera_busy or spectrograph_busy

    def _on_hardware_config_applied(self, new_config, changed_tabs):
        self.config = new_config
        self.save_config_to_file()

        if "grating" in changed_tabs:
            self._refresh_grating_combo()

        if "display" in changed_tabs:
            self.chk_flip_x.setChecked(self.config.get("flip_x", False))

        if "hardware" in changed_tabs:
            QMessageBox.information(
                self, "Restart required",
                "Hardware / connection settings have been saved to spectrometerConfig.json.\n"
                "Please restart the application for these changes to take effect."
            )

    def _refresh_grating_combo(self):
        self.grating_list = [str(g.get("grooves")) for g in self.config.get("grating", [])]
        current = self.combo_grating.currentText()
        self.combo_grating.blockSignals(True)
        self.combo_grating.clear()
        self.combo_grating.addItems(self.grating_list)
        if current in self.grating_list:
            self.combo_grating.setCurrentText(current)
        self.combo_grating.blockSignals(False)
        self.check_spectrometer_changes()

    def get_roi_for_grating(self, grating_str):
        for g in self.config.get("grating", []):
            if str(g.get("grooves")) == str(grating_str):
                r = g.get("defaultROI", {})
                return r.get("from", 100), r.get("to", 140)
        return 100, 140

    def save_config_to_file(self):
        try:
            with open("spectrometerConfig.json", "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            print(f"Failed to save config: {e}")

    def check_and_record_hardware_identity(self, category, model, serial_number):
        """Cross-check a detected hardware model/serial number against the one recorded
        in spectrometerConfig.json's "hardware_identity" (category is "spectrometer" or
        "camera"), so a config left over from a different instrument can be spotted.

        If nothing could be detected (both fields empty), do nothing. On the first
        successful connection (nothing recorded yet) just record what was detected,
        silently. On a mismatch, warn -- like the grating-mismatch check in ui.py, this
        never auto-edits the config; it only offers to update the recorded identity if
        the user confirms the new hardware is intentional.
        """
        if not model and not serial_number:
            return

        recorded = self.config.setdefault("hardware_identity", {}).setdefault(category, {})
        recorded_model = recorded.get("model")
        recorded_serial = recorded.get("serial_number")

        if not recorded_model and not recorded_serial:
            recorded["model"] = model or None
            recorded["serial_number"] = serial_number or None
            self.save_config_to_file()
            print(f"Recorded {category} identity in spectrometerConfig.json: "
                  f"model={model!r}, serial_number={serial_number!r}")
            return

        mismatches = []
        if recorded_serial and serial_number and recorded_serial != serial_number:
            mismatches.append(f"Serial number: config has '{recorded_serial}', detected '{serial_number}'")
        if recorded_model and model and recorded_model != model:
            mismatches.append(f"Model: config has '{recorded_model}', detected '{model}'")

        if not mismatches:
            return

        reply = QMessageBox.warning(
            self, f"{category.capitalize()} identity mismatch",
            f"spectrometerConfig.json's recorded {category} identity does not match the "
            "currently connected hardware:\n\n" + "\n".join(mismatches) +
            f"\n\nThis config may have been created for a different {category}, so its "
            "calibration/grating/ROI settings may not apply here.\n\n"
            "If this hardware was intentionally connected (e.g. a permanent replacement), "
            "update the recorded identity to match it now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            recorded["model"] = model or recorded_model
            recorded["serial_number"] = serial_number or recorded_serial
            self.save_config_to_file()

    def _load_local_cache(self):
        return load_local_cache()

    def _save_local_cache(self, key, value):
        save_local_cache(key, value)

    def load_api_clients(self):
        """Return the authorised API clients, migrating the old single-key file.

        The legacy plaintext key at the repository root is read once, carried over as
        a client named "default" with no IP restriction (so an already-paired client
        keeps working with no reconfiguration), then deleted.
        """
        clients, needs_save, migrated_legacy = load_clients()
        persisted = not needs_save
        if needs_save:
            try:
                self.save_api_clients(clients)
                persisted = True
            except Exception as exc:
                # Keep the in-memory key usable for this run, but never delete the
                # legacy source unless its replacement was durably written.
                print(f"Failed to persist API clients: {exc}")
        if migrated_legacy and persisted:
            remove_legacy_key_file()
        return clients

    def save_api_clients(self, clients):
        # Let the caller decide whether an in-memory replacement or legacy-file
        # deletion is safe. Swallowing this exception can resurrect revoked keys after
        # restart and can destroy the only copy during migration.
        save_clients(clients)

    def load_spectrometer_config(self):
        config_path = "spectrometerConfig.json"
        default_config = {
            "model": "Andor",
            "com_port": "COM3",
            "grating": [
                {
                    "index": 1,
                    "grooves": 600,
                    "defaultROI": {"from": 100, "to": 140}
                },
                {
                    "index": 2,
                    "grooves": 1200,
                    "defaultROI": {"from": 100, "to": 140}
                },
                {
                    "index": 3,
                    "grooves": 1800,
                    "defaultROI": {"from": 100, "to": 140}
                }
            ],
            "flip_x": False,
            "default_temperature": -65,
            "hardware_identity": {
                "spectrometer": {"model": None, "serial_number": None},
                "camera": {"model": None, "serial_number": None},
            },
        }
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                print("spectrometerConfig.json read:", json.dumps(data, indent=2))

                if "grating" in data and len(data["grating"]) > 0 and isinstance(data["grating"][0], (int, float)):
                    new_grating = []
                    for i, g in enumerate(data["grating"]):
                        new_grating.append({
                            "index": i + 1,
                            "grooves": int(g),
                            "defaultROI": data.get("defaultROI", {"from": 100, "to": 140})
                        })
                    data["grating"] = new_grating

                    try:
                        with open(config_path, "w", encoding="utf-8") as fw:
                            json.dump(data, fw, indent=4)
                    except:
                        pass

                for key, val in default_config.items():
                    if key not in data:
                        data[key] = val
                return data
        except:
            return default_config
