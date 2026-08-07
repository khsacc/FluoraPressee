# FluoRaPressée: Spectroscopic measurements and analysis platform designed for efficient high-pressure experiments

<img src="./logo/Large_logo.jpg" width="50%">

This program is designed to
- control spectrometers and cameras, and acquire spectroscopic data
- instantly fit the data
- calculate pressure by using an established fluorescence/Raman pressure sensors

The name FluoRaPressée combines **Flu**orescence, **Ra**man, and the French adjective **« pressée »** ('in a hurry'; literary 'pressed'), a playful nod to rapid spectroscopic measurements and analyses under pressure.

Author: Hiroki Kobayashi (Geochemical Research Center, The University of Tokyo). https://orcid.org/0000-0002-3682-7558 E-mail as of 2026: hiroki (at) eqchem.s.u-tokyo.ac.jp

> [!NOTE]
> This program is under active development, and README.md will be completed once the fundamental development is completed. [Japanese version available here](./README_ja.md), which is updated more frequently.


## Acknowledgements

The design of FluoRaPressée was inspired by [Rubycond](https://github.com/CelluleProjet/Rubycond), an open-source software for ruby fluorescence measurements and pressure determination, which I used every day during my 1-year stay at l'Institut de minéralogie, de physique des matériaux et de cosmochimie (IMPMC), Paris, France. The experience of using Rubycond motivated the development of this more general-purpose platform, which extends the scope towards the use of Raman sensors for high-temperature experiments and API-based remote operation. I gratefully acknowledge the Rubycond developers (Yiuri Garino and Silvia Boccato).


This software was developed in the Geochemical Research Center (GcRC), Graduate School of Science, the University of Tokyo, with the support from Profs. K. Komatsu and H. Kagi. I thank the members of the group who helped me with testing and improving the software, especially K. Komatsu and S. Koyano.

<!-- ## ✨ Features

* **Real-time Measurement & Control**
  * Real-time display of 1D spectra (Full Range / Custom ROI) and 2D images.
  * Control over exposure time, number of accumulations, and detector cooling temperature.
  * Supports single shots, continuous measurements, and sequential automated saving.
* **Spectrometer Control**
  * Control of grating and center wavelength.
  * Seamless switching between Wavelength (nm) and Raman shift (cm⁻¹) modes (supports excitation wavelength setting).
* **Background Correction & Calibration**
  * Acquire, save, and subtract background spectra in real-time.
  * X-axis wavelength calibration tool with a versioned, hardware-compatible configuration catalog.
* **Real-time Peak Fitting**
  * Single and double peak fitting using Gauss, Lorentz, and Pseudo-Voigt functions.
  * Automatic and manual fitting range configurations.
* **Pressure Calculation (for High-Pressure Experiments)**
  * Pressure calculation based on Ruby fluorescence shift (supports Piermarini, Mao, and Shen scales).
  * Temperature correction for the center wavelength (λ0) based on sample temperature.

## 🛠 Requirements

* **OS**: Windows 10 / 11 (Depends on Andor SDK compatibility)
* **Python**: Python 3.9 through 3.13
* **Hardware**:
  * Andor Camera (Detector)
  * Andor Spectrometer
* **Drivers/SDK**:
  * Andor SDK must be installed on the system.

### Dependencies
* PyQt6
* pyqtgraph
* numpy
* scipy

## 📥 Installation

1. Clone the repository. Andor and Princeton Instruments users should double-click `setup.bat`
   (or run it from Command Prompt / PowerShell). Ocean Optics users should instead run
   `setup_oceanoptics.bat` as Administrator; see below.
   This creates a `.venv` virtual environment in the project folder and installs all required
   packages (`PyQt6`, `pyqtgraph`, `numpy`, `scipy`, `pylablib`, `pyserial`) into it.
2. Ensure the Andor SDK is properly installed on the system.

### Ocean Optics support (optional)

FluoRaPressée can also drive Ocean Optics USB2000/USB4000 spectrometers via
[python-seabreeze](https://github.com/ap--/python-seabreeze). This is optional and not installed
by the base `setup.bat`/`setup.sh`.

1. On Windows, right-click `setup_oceanoptics.bat` and choose **Run as administrator**. Do not run
   `setup.bat` first. On macOS/Linux, run `./setup_oceanoptics.sh` instead of `./setup.sh`.
   The Ocean Optics setup script creates `.venv`, installs all standard dependencies and
   `seabreeze`, and runs `seabreeze_os_setup` for the required OS-level configuration.
2. Set `"model": "OceanOptics"` in `spectrometerConfig.json` (the setup wizard on first launch
   also offers this as a supplier choice).

Ocean Optics is a fixed spectrometer, not a movable grating + detector like Andor/Princeton
Instruments, so the following do not apply and are hidden in the GUI when connected:
* 2D image mode and custom vertical ROI (the detector is a single row).
* Grating selection and centre-wavelength "Apply" (the device has neither).

Before a FluoRaPressée neon calibration is applied, the X-axis shows the device's own
factory-calibrated wavelength (a warning banner above the plot makes this explicit) rather than a
plain pixel index; this is not the same as "uncalibrated".

## 🚀 Usage

### Launching the Application
Double-click `FluoraPressee_run.bat` (or run it from Command Prompt / PowerShell) to launch the app using the
virtual environment created by the applicable setup script.

*Note: If you want to test the UI without connecting to the hardware, use `FluoRaPressee_run_debug.bat` instead,
which launches the app in debug mode.*

On macOS/Linux (UI development only, no hardware support), use `./setup.sh` and `./run_debug.sh`
instead.

### Basic Measurement Workflow
1. **Cooling**: 
   Set the "Cooler target temp" in the "Measurement" panel and wait for the camera temperature to stabilize.
2. **Spectrometer Setup**: 
   In the "Spectrometer Configurations" section, enter the desired grating and center wavelength (or Raman shift), then click **"Apply"**.
3. **Background Acquisition (Optional)**: 
   Close the shutter, then click **"Acquire and save background"** in the "Background" section to collect and save background data.
4. **Measurement**:
   * **Take single spectrum**: Acquires a single spectrum based on the set exposure time and accumulations.
   * **Commence Measurement**: Continuously acquires and displays spectra in real-time. Click "Terminate Measurement" to stop.
5. **Saving Data**:
   * Click **"Save data"** to save the currently displayed spectrum.
   * For automated sequential saving, expand **"▶ Sequential measurements"**, select a directory, set the interval (Skip frames) and max number of images, then click **"Start Sequential"**.

### Fitting and Pressure Calculation
* Turn **ON** "Fitting Configurations" to perform real-time curve fitting on the displayed spectrum.
* When a double-peak fit is successful, turn **ON** the "Pressure Calculation" section to automatically calculate and display the pressure based on the Ruby R1 peak.

## 📁 File Structure

* ui.py: Main GUI application script.
* camera.py: Thread class for controlling the Andor camera, acquiring data, and reading temperature.
* spectrometer.py: Module for controlling the Andor spectrometer's gratings and wavelength.
* analysis.py: Handles peak fitting logic for spectrum data.
* calibration_ui.py: Dedicated GUI dialogue for pixel-to-wavelength calibration.
* pressureCalc.py: Module for pressure calculation logic from Ruby fluorescence.
* spectrometerConfig.json: Startup hardware/configuration file (generated automatically on first run).
* Per-measurement spectrometer configurations: immutable JSON records indexed by SQLite below the
  user's `FluoraPressee/configurations` application-data directory. Each active slot is distinguished
  by hardware, grating, centre position, and ROI; exposure and sample/material are not part of it.
  Hardware compatibility uses an exact serial number when available and an exact model fallback
  otherwise. Legacy free-form calibration JSON files are not imported into this catalog.

# Acknowledgement

This program was developed from scratch, but many of my ideas about its design and functions come from [Rubycond](https://github.com/CelluleProjet/Rubycond) software developed by Yiuri Garino (yiuri.garino (at) cnrs.fr), which I heavily used during my stay at IMPMC, Sorbonne Universite, CNRS UMR 7590, Paris, France, where I worked with Dr Stefan Klotz.
This program was developed using spectrometers at Geochemistry laboratory lead by Prof Hiroyuki Kagi and Prof Kazuki Komatsu, at the Geochemical Research Center, Graduate School of Science, The University of Tokyo. I used Gemini Pro for coding, without which this program would never be completed. -->
