import numpy as np
from scipy.signal import find_peaks
from scipy.optimize import curve_fit
from typing import List, Dict, Optional

class CalibrationCore:
    """スペクトル較正処理を行うコアロジック"""
    
    # Constants used for peak detection
    PEAK_WINDOW = 10  # Number of pixels around each peak to extract for fitting
    SIGMA_GUESS = 2.0  # Initial sigma guess for the Gaussian fit
    SIGMA_MAX = 20  # Upper bound on sigma
    NOISE_THRESHOLD = 1.0  # Floor value used for the estimated noise level

    @staticmethod
    def nm_to_raman(wl_nm: float, laser_wl: float) -> float:
        """Wavelength (nm) to Raman shift (cm^-1) for a given excitation wavelength."""
        if wl_nm == 0:
            return 0.0
        return (1e7 / laser_wl) - (1e7 / wl_nm)

    @staticmethod
    def raman_to_nm(shift_cm1: float, laser_wl: float) -> float:
        """Raman shift (cm^-1) to wavelength (nm) for a given excitation wavelength."""
        denominator = (1e7 / laser_wl) - shift_cm1
        return 1e7 / denominator if denominator != 0 else float("nan")

    def gaussian(self, x: np.ndarray, a: float, x0: float, sigma: float, offset: float) -> np.ndarray:
        return a * np.exp(-(x - x0)**2 / (2 * sigma**2)) + offset

    def find_and_fit_peaks(self, y_data: np.ndarray, prominence_multiplier: float = 3.5) -> List[Dict]:
        """スペクトルからピークを検索し、それぞれをガウシアンでフィットする
        
        Args:
            y_data: スペクトルデータ
            prominence_multiplier: プロミネンス閾値の倍率
            
        Returns:
            フィット済みピークの情報リスト
        """
        x_data = np.arange(len(y_data))
        
        # Estimate the background noise level
        median_y = np.median(y_data)
        baseline_data = y_data[y_data <= median_y]
        noise = np.std(baseline_data) if len(baseline_data) > 0 else self.NOISE_THRESHOLD
        
        # Fall back to the floor value if the estimated noise is too low
        if noise < self.NOISE_THRESHOLD: 
            noise = self.NOISE_THRESHOLD
        
        prominence_threshold = noise * prominence_multiplier
        height_threshold = median_y + noise * max(1.0, prominence_multiplier)
        print(f"Peak find, prominence_multiplier: {prominence_multiplier}, noise: {noise:.2f}, prominence_thresh: {prominence_threshold:.2f}, height_thresh: {height_threshold:.2f}")

        peaks, properties = find_peaks(y_data, prominence=prominence_threshold, height=height_threshold)

        fitted_peaks = []
        for p in peaks:
            # Extract a window around the peak and fit it
            start = max(0, p - self.PEAK_WINDOW)
            end = min(len(y_data), p + self.PEAK_WINDOW + 1)
            
            x_fit = x_data[start:end]
            y_fit = y_data[start:end]
            
            # Initial parameter guesses
            a_guess = y_data[p] - np.min(y_fit)
            offset_guess = np.min(y_fit)
            p0 = [a_guess, p, self.SIGMA_GUESS, offset_guess]
            bounds = ([0, min(x_fit), 0.1, -np.inf], [np.inf, max(x_fit), self.SIGMA_MAX, np.inf])
            
            try:
                popt, _ = curve_fit(self.gaussian, x_fit, y_fit, p0=p0, bounds=bounds)
                x_curve = np.linspace(x_fit[0], x_fit[-1], len(x_fit) * 10)
                y_curve = self.gaussian(x_curve, *popt)
                fitted_peaks.append({
                    "center": popt[1],
                    "x_fit": x_fit,
                    "y_data": y_fit,
                    "x_curve": x_curve,
                    "y_curve": y_curve
                })
            except (RuntimeError, ValueError) as e:
                # If the fit fails, fall back to returning the raw window data as-is
                print(f"Warning: Peak fitting failed for peak at {p}: {e}")
                fitted_peaks.append({
                    "center": float(p),
                    "x_fit": x_fit,
                    "y_data": y_fit,
                    "x_curve": x_fit,
                    "y_curve": y_fit
                })
                
        return fitted_peaks

    def calibrate(self, pixels: np.ndarray, ref_values: np.ndarray) -> Optional[Dict]:
        """ピクセルと基準値(nm または cm⁻¹)のリストを受け取り、多項式フィットを行う"""
        pixels = np.array(pixels)
        ref_values = np.array(ref_values)
        
        if len(pixels) < 2:
            return None
            
        if len(pixels) == 2:
            # With exactly 2 points, fit a linear function (y = c1*x + c0)
            coeffs = np.polyfit(pixels, ref_values, 1)
            return {
                "c0": coeffs[1],
                "c1": coeffs[0],
                "c2": 0.0
            }
        else:
            # With 3 or more points, fit a quadratic function (y = c2*x^2 + c1*x + c0)
            coeffs = np.polyfit(pixels, ref_values, 2)
            return {
                "c0": coeffs[2],
                "c1": coeffs[1],
                "c2": coeffs[0]
            }