import numpy as np
from scipy.integrate import quad
from dataclasses import dataclass
from typing import Tuple, Optional


@dataclass
class PressureCalculationResult:
    pressure: Optional[float]
    pressure_err: Optional[float]
    zero_peak_at_current_t: Optional[float]


class PressureCalculator:
    """高圧下の圧力計算を行うクラス。複数の圧力マーカーと圧力スケールに対応"""

    SENSORS = {
        "ruby": {
            "label": "Ruby",
            "kind": "fluorescence",
            "unit": "nm",
            "initial_value": 694.300,
        },
        "sm_srb4o7": {
            "label": "Sm2+:SrB4O7",
            "kind": "fluorescence",
            "unit": "nm",
            "initial_value": 685.410,
        },
        "sm_srfcl": {
            "label": "Sm2+:SrFCl",
            "kind": "fluorescence",
            "unit": "nm",
            "initial_value": 690.300,
        },
        "sm_yag_y1": {
            "label": "Sm3+:YAG (Y1)",
            "kind": "fluorescence",
            "unit": "nm",
            "initial_value": 617.800,
        },
        "diamond_13c_1st_order": {
            "label": "13C diamond 1st order",
            "kind": "raman",
            "unit": "cm-1",
            "initial_value": 1287.79,
        },
        "diamond_raman_edge": {
            "label": "Diamond Raman Edge",
            "kind": "raman_edge",
            "unit": "cm-1",
            "initial_value": 1334.0,
        },
        "cubic_bn_to": {
            "label": "Cubic BN TO",
            "kind": "raman",
            "unit": "cm-1",
            "initial_value": 1058.3,
        },
        "zircon_b1g": {
            "label": "Zircon B1g",
            "kind": "raman",
            "unit": "cm-1",
            "initial_value": 1008.6,
        },
        "quartz_464": {
            "label": "Quartz 464 cm-1",
            "kind": "raman",
            "unit": "cm-1",
            "initial_value": 464.4,
        },
        "quartz_128": {
            "label": "Quartz 128 cm-1",
            "kind": "raman",
            "unit": "cm-1",
            "initial_value": 127.9,
        },
    }

    PRESSURE_SCALES = {
        "ruby": {
            "ruby_shen_2020": {"label": "Shen et al. 2020", "temperature_mode": "none"},
            "ruby_kraus_2016": {"label": "Kraus et al. 2016", "temperature_mode": "none"},
            "ruby_sokolova_2013": {"label": "Sokolova et al. 2013", "temperature_mode": "none"},
            "ruby_jacobsen_2008": {"label": "Jacobsen et al. 2008", "temperature_mode": "none"},
            "ruby_dorogokupets_oganov_2007": {"label": "Dorogokupets and Oganov 2007", "temperature_mode": "none"},
            "ruby_holzapfel_2003": {"label": "Holzapfel 2003", "temperature_mode": "none"},
            "ruby_mao_1986": {"label": "Mao et al. 1986", "temperature_mode": "none"},
            "ruby_piermarini_1975": {"label": "Piermarini et al. 1975", "temperature_mode": "none"},
        },
        "sm_srb4o7": {
            "sm_srb4o7_datchi_1997_mxb1986": {"label": "0-0 line: Datchi et al. 1997 (MXB1986)", "temperature_mode": "none"},
            "sm_srb4o7_datchi_2007_do2007": {"label": "0-0 line: Datchi et al. 2007 (DO2007)", "temperature_mode": "none"},
            "sm_srb4o7_rashchenko_2015_lam11": {"label": "0-0 line (lam1): Rashchenko et al. 2015", "temperature_mode": "none"},
            # "sm_srb4o7_rashchenko_2015_lam12": {"label": "0-1 line (lam2): Rashchenko et al. 2015", "temperature_mode": "none"},
            # "sm_srb4o7_rashchenko_2015_lam13": {"label": "0-1 line (lam3): Rashchenko et al. 2015", "temperature_mode": "none"},
            # "sm_srb4o7_rashchenko_2015_lam14": {"label": "0-1 line (lam4): Rashchenko et al. 2015", "temperature_mode": "none"},
            "sm_srb4o7_wei_2024": {"label": "0-0 line: Wei et al. 2024 (Ar PTM)", "temperature_mode": "none"},
        },
        "sm_srfcl": {
            "sm_srfcl_lorenz_1994": {"label": "Lorenz et al. 1994", "temperature_mode": "none"},
            "sm_srfcl_shen_2021": {"label": "Shen et al. 2021", "temperature_mode": "none"},
            "sm_srfcl_shen_1991": {"label": "Shen et al. 1991", "temperature_mode": "none"},
        },
        "sm_yag_y1": {
            "sm_yag_y1_wei_2024": {"label": "Y1 line: Wei et al. 2024 (Ar PTM)", "temperature_mode": "none"},
        },
        "diamond_13c_1st_order": {
            "diamond_13c_schiferl_1997": {
                "label": "Schiferl et al. 1997",
                "temperature_mode": "none",
            },
            "diamond_13c_mysen_yamashita_2010": {
                "label": "Mysen and Yamashita 2010",
                "temperature_mode": "embedded_pt",
                "fixed_t0": 298.15,
                "fixed_t0_note": "T0 is fixed at 25 C (298.15 K) for this scale.",
            },
        },
        "diamond_raman_edge": {
            "diamond_edge_hilberer_2026": {
                "label": "Hilberer et al. 2026",
                "temperature_mode": "none",
                "measurement_kind": "diamond_raman_edge",
                "formula": "k0_k0prime",
                "nu0": 1334.0,
                "k0": 575.0,
                "k0_err": 7.0,
                "k0_prime": 3.3,
                "k0_prime_err": 0.1,
            },
            "diamond_edge_eremets_2023": {
                "label": "Eremets et al. 2023",
                "temperature_mode": "none",
                "measurement_kind": "diamond_raman_edge",
                "formula": "quadratic",
                "nu0": 1332.5,
                "a": 517.0,
                "b": 764.0,
            },
            "diamond_edge_akahama_kawamura_2006": {
                "label": "Akahama and Kawamura 2006",
                "temperature_mode": "none",
                "measurement_kind": "diamond_raman_edge",
                "formula": "k0_k0prime",
                "nu0": 1334.0,
                "k0": 547.0,
                "k0_err": 11.0,
                "k0_prime": 3.75,
                "k0_prime_err": 0.20,
            },
            "diamond_edge_akahama_kawamura_2010": {
                "label": "Akahama and Kawamura 2010",
                "temperature_mode": "none",
                "measurement_kind": "diamond_raman_edge",
                "formula": "quadratic_absolute",
                "nu0": 1334.0,
                "c0": 3141.0,
                "c0_err": 3.0,
                "c1": -4.157,
                "c1_err": 0.020,
                "c2": 1.429e-3,
                "c2_err": 1.2e-5,
            },
        },
        "cubic_bn_to": {
            "cubic_bn_kawamoto_2004": {"label": "Kawamoto et al. 2004", "temperature_mode": "none"},
            "cubic_bn_datchi_2004": {
                "label": "Datchi et al. 2004",
                "temperature_mode": "embedded_pt",
                "reports_zero_peak_at_current_t": True,
                "valid_temp_range": (300, 723),
            },
        },
        "zircon_b1g": {
            "zircon_schmidt_2013": {"label": "Schmidt et al. 2013", "temperature_mode": "none"},
            "zircon_takahashi_2024": {"label": "Takahashi et al. 2024", "temperature_mode": "none"},
        },
        "quartz_464": {
            "quartz_schmidt_ziemann_2000": {
                "label": "Schmidt and Ziemann 2000 (quadratic, ~23 C)",
                "temperature_mode": "none",
            },
            "quartz_schmidt_ziemann_2000_linear": {
                "label": "Schmidt and Ziemann 2000 (linear, 9 cm-1/GPa)",
                "temperature_mode": "none",
            },
        },
        "quartz_128": {
            "quartz_li_2025": {
                "label": "Li et al. 2025",
                "temperature_mode": "embedded_pt",
                "valid_temp_range": (296.15, 973.15),
                "fixed_t0": 296.15,
                "fixed_t0_note": "T0 is fixed at 23 C (296.15 K) for this scale.",
            },
        },
    }

    TEMPERATURE_SCALES = {
        "ruby": {
            "ruby_kobayashi_unpublished": {"label": "Kobayashi et al. unpublished", "valid_range": (0, 300)},
            "ruby_yen_nicol_1992": {"label": "Yen and Nicol 1992", "valid_range": (0, 600)},
            "ruby_ragan_1992": {"label": "Ragan et al. 1992", "valid_range": (0.0, 600.0)},
            "ruby_datchi_2007_ht": {"label": "Datchi et al. 2007 HT", "valid_range": (296, 900)},
            "ruby_datchi_2007_lt": {"label": "Datchi et al. 2007 LT", "valid_range": (0, 296)},
            "ruby_wei_2024": {"label": "Wei et al. 2024 (R1)", "valid_range": (296, 773)},
        },
        "sm_srb4o7": {
            "sm_srb4o7_datchi_2007": {"label": "Datchi et al. 2007", "valid_range": (296, 900.0)},
            "sm_srb4o7_wei_2024": {"label": "Wei et al. 2024 (0-0 line)", "valid_range": (296, 923)},
        },
        "sm_srfcl": {
            "sm_srfcl_lorenz_1994": {"label": "Lorenz et al. 1994", "valid_range": (20, 650)},
        },
        "sm_yag_y1": {
            "sm_yag_y1_wei_2024": {"label": "Wei et al. 2024 (Y1)", "valid_range": (296, 873)},
        },
        "cubic_bn_to": {
            "cubic_bn_kawamoto_2004": {"label": "Kawamoto et al. 2004", "valid_range": (300, 1000)},
        },
        "zircon_b1g": {
            "zircon_schmidt_2013": {"label": "Schmidt et al. 2013", "valid_range": (296, 1223)},
            "zircon_takahashi_2024": {"label": "Takahashi et al. 2024", "valid_range": (294, 1078)},
        },
        "quartz_464": {
            "quartz_schmidt_ziemann_2000": {"label": "Schmidt and Ziemann 2000", "valid_range": (77.15, 833.15)},
        },
    }

    @staticmethod
    def get_sensors_for_unit(unit: str):
        return [key for key, meta in PressureCalculator.SENSORS.items() if meta["unit"] == unit]

    @staticmethod
    def get_pressure_scale_label(sensor: str, p_scale: str) -> str:
        return PressureCalculator.PRESSURE_SCALES.get(sensor, {}).get(p_scale, {}).get("label", p_scale)

    @staticmethod
    def is_diamond_edge_scale(*, sensor: str, p_scale: str) -> bool:
        scale = PressureCalculator.PRESSURE_SCALES.get(sensor, {}).get(p_scale, {})
        return scale.get("measurement_kind") == "diamond_raman_edge"

    @staticmethod
    def validate_fit_pressure_pair(*, fit_function: str, sensor: str, p_scale: str) -> None:
        """Reject configurations that mix edge extraction and peak-based scales."""
        edge_fit = fit_function == "Diamond Raman Edge"
        edge_scale = PressureCalculator.is_diamond_edge_scale(sensor=sensor, p_scale=p_scale)
        if edge_fit and not edge_scale:
            raise ValueError(
                "Diamond Raman Edge fitting requires a Diamond Raman Edge pressure scale."
            )
        if edge_scale and not edge_fit:
            raise ValueError(
                "A Diamond Raman Edge pressure scale requires Diamond Raman Edge fitting."
            )

    @staticmethod
    def get_scale_zero_peak(*, sensor: str, p_scale: str) -> Optional[float]:
        return PressureCalculator.PRESSURE_SCALES.get(sensor, {}).get(p_scale, {}).get("nu0")

    @staticmethod
    def get_temperature_scale_label(sensor: str, t_scale: str) -> str:
        return PressureCalculator.TEMPERATURE_SCALES.get(sensor, {}).get(t_scale, {}).get("label", t_scale)

    @staticmethod
    def pressure_scale_requires_temperature(*, sensor: str, p_scale: str) -> bool:
        return PressureCalculator.get_pressure_temperature_mode(sensor=sensor, p_scale=p_scale) == "embedded_pt"

    @staticmethod
    def get_pressure_temperature_mode(*, sensor: str, p_scale: str) -> str:
        return PressureCalculator.PRESSURE_SCALES.get(sensor, {}).get(p_scale, {}).get("temperature_mode", "none")

    @staticmethod
    def get_fixed_t0(*, sensor: str, p_scale: str) -> Optional[float]:
        return PressureCalculator.PRESSURE_SCALES.get(sensor, {}).get(p_scale, {}).get("fixed_t0")

    @staticmethod
    def get_fixed_t0_note(*, sensor: str, p_scale: str) -> str:
        return PressureCalculator.PRESSURE_SCALES.get(sensor, {}).get(p_scale, {}).get("fixed_t0_note", "")

    @staticmethod
    def resolve_t0(*, sensor: str, p_scale: str, t0: float) -> float:
        fixed_t0 = PressureCalculator.get_fixed_t0(sensor=sensor, p_scale=p_scale)
        return fixed_t0 if fixed_t0 is not None else t0

    @staticmethod
    def get_temp_valid_range(*, sensor: str, t_scale: Optional[str] = None,
                             p_scale: Optional[str] = None) -> Tuple[Optional[float], Optional[float]]:
        if p_scale is not None:
            p_meta = PressureCalculator.PRESSURE_SCALES.get(sensor, {}).get(p_scale, {})
            if p_meta.get("temperature_mode") == "embedded_pt":
                return p_meta.get("valid_temp_range", (None, None))

        if t_scale is not None:
            t_meta = PressureCalculator.TEMPERATURE_SCALES.get(sensor, {}).get(t_scale, {})
            return t_meta.get("valid_range", (None, None))

        return (None, None)

    @staticmethod
    def is_temp_in_range(*, sensor: str, temp: float, t_scale: Optional[str] = None,
                         p_scale: Optional[str] = None) -> Tuple[bool, Tuple]:
        """温度が圧力マーカーの有効範囲内にあるか確認

        全引数キーワード専用・すべて必須。

        Args:
            sensor: 圧力マーカー名
            temp: 温度値
            t_scale: 温度補正スケール
            p_scale: 温度入力が必須の圧力スケール

        Returns:
            (有効性, (最小値, 最大値)) のタプル
        """
        rng = PressureCalculator.get_temp_valid_range(sensor=sensor, t_scale=t_scale, p_scale=p_scale)
        if rng[0] is None or rng[1] is None:
            return True, (None, None)

        is_valid = rng[0] <= temp <= rng[1]
        return is_valid, rng


    @staticmethod
    def calculate(*, sensor: str, p_scale: str, peak: float, zero_peak: float,
                  zero_peak_at_t0: Optional[float] = None, peak_err: float = 0.0,
                  temperature_correction_enabled: bool = False,
                  t_scale: Optional[str] = None,
                  current_t: float = 298.15, t0: float = 298.15) -> PressureCalculationResult:
        """圧力を計算し、明示できる場合のみ現在温度でのゼロ圧ピークも返す。"""
        t0 = PressureCalculator.resolve_t0(sensor=sensor, p_scale=p_scale, t0=t0)
        if zero_peak_at_t0 is None:
            zero_peak_at_t0 = zero_peak

        try:
            kind = PressureCalculator.SENSORS[sensor]["kind"]
            temperature_mode = PressureCalculator.get_pressure_temperature_mode(
                sensor=sensor, p_scale=p_scale
            )

            zero_peak_for_calc = zero_peak
            zero_peak_at_current_t = zero_peak
            if temperature_mode == "embedded_pt":
                zero_peak_for_calc = zero_peak_at_t0
                zero_peak_at_current_t = None
            elif temperature_correction_enabled:
                zero_peak_at_current_t = PressureCalculator.get_corrected_zero_peak(
                    sensor=sensor, t_scale=t_scale, current_t=current_t,
                    t0=t0, zero_peak_at_t0=zero_peak_at_t0
                )
                zero_peak_for_calc = zero_peak_at_current_t

            if kind == "fluorescence":
                pressure, pressure_err, zero_peak_override = PressureCalculator._calculate_fluorescence(
                    sensor=sensor, p_scale=p_scale,
                    wavelength=peak, wavelength0=zero_peak_for_calc,
                    wavelength_err=peak_err,
                )
            elif kind == "raman":
                pressure, pressure_err, zero_peak_override = PressureCalculator._calculate_raman(
                    sensor=sensor, p_scale=p_scale,
                    wavenumber=peak, wavenumber0=zero_peak_for_calc,
                    wavenumber0_at_t0=zero_peak_at_t0,
                    wavenumber_err=peak_err,
                    current_t=current_t, t0=t0,
                )
            elif kind == "raman_edge":
                pressure, pressure_err, zero_peak_override = PressureCalculator._calculate_diamond_edge(
                    sensor=sensor, p_scale=p_scale, edge=peak, edge_err=peak_err
                )
            else:
                return PressureCalculationResult(None, None, None)

            if pressure is None:
                return PressureCalculationResult(None, None, None)

            if zero_peak_override is not None:
                zero_peak_at_current_t = zero_peak_override

            return PressureCalculationResult(pressure, pressure_err, zero_peak_at_current_t)
        except ZeroDivisionError as e:
            print(f"ZeroDivisionError in pressure calculation ({sensor}, {p_scale}): {e}")
            return PressureCalculationResult(None, None, None)
        except ValueError as e:
            print(f"ValueError in pressure calculation ({sensor}, {p_scale}): {e}")
            return PressureCalculationResult(None, None, None)
        except KeyError as e:
            print(f"KeyError in pressure calculation - missing parameter: {e}")
            return PressureCalculationResult(None, None, None)
        except Exception as e:
            print(f"Unexpected error in pressure calculation ({sensor}, {p_scale}): {e}")
            return PressureCalculationResult(None, None, None)

    @staticmethod
    def _calculate_diamond_edge(*, sensor: str, p_scale: str,
                                edge: float, edge_err: float):
        meta = PressureCalculator.PRESSURE_SCALES.get(sensor, {}).get(p_scale, {})
        if meta.get("measurement_kind") != "diamond_raman_edge":
            return None, None, None

        nu0 = float(meta["nu0"])
        x = edge / nu0 - 1.0
        dx = edge_err / nu0
        if meta["formula"] == "quadratic":
            a = float(meta["a"])
            b = float(meta["b"])
            pressure = a * x + b * x**2
            variance = ((a + 2.0 * b * x) * dx) ** 2
            if "a_err" in meta:
                variance += (x * float(meta["a_err"])) ** 2
            if "b_err" in meta:
                variance += (x**2 * float(meta["b_err"])) ** 2
        elif meta["formula"] == "k0_k0prime":
            k0 = float(meta["k0"])
            k0_prime = float(meta["k0_prime"])
            factor = 1.0 + 0.5 * (k0_prime - 1.0) * x
            pressure = k0 * x * factor
            variance = (k0 * (1.0 + (k0_prime - 1.0) * x) * dx) ** 2
            variance += (x * factor * float(meta.get("k0_err", 0.0))) ** 2
            variance += (0.5 * k0 * x**2 * float(meta.get("k0_prime_err", 0.0))) ** 2
        elif meta["formula"] == "quadratic_absolute":
            c0 = float(meta["c0"])
            c1 = float(meta["c1"])
            c2 = float(meta["c2"])
            pressure = c0 + c1 * edge + c2 * edge**2
            variance = ((c1 + 2.0 * c2 * edge) * edge_err) ** 2
            if "c0_err" in meta:
                variance += float(meta["c0_err"]) ** 2
            if "c1_err" in meta:
                variance += (edge * float(meta["c1_err"])) ** 2
            if "c2_err" in meta:
                variance += (edge**2 * float(meta["c2_err"])) ** 2
        else:
            return None, None, None

        return pressure, float(np.sqrt(variance)), nu0

    @staticmethod
    def _calc_mao_type(peak, zero_peak, peak_err, A, B, A_err, B_err):
        r = peak / zero_peak

        p = (A / B) * (r**B - 1.0)

        dp_peak = (A / zero_peak) * r**(B - 1.0) * peak_err
        dp_A = ((r**B - 1.0) / B) * A_err
        dp_B = (
            (A / B**2)
            * (B * r**B * np.log(r) - (r**B - 1.0))
            * B_err
        )

        dp = np.sqrt(dp_peak**2 + dp_A**2 + dp_B**2)
        return p, dp
    
    @staticmethod
    def _calc_kunk_type(peak, zero_peak, peak_err, A, B, A_err, B_err):
        # Kunc et al. (2003) PRB 10.1103/PhysRevB.68.094107.
        ratio = (peak - zero_peak) / zero_peak
        p = A * ratio * (1.0 + B * ratio)
        dp = (
            (ratio * (1 + B * ratio) * A_err)**2
            + (A * ratio**2 * B_err)**2
            + (A * (1 + 2 * B * ratio) / zero_peak * peak_err)**2
        )**0.5
        return p, dp

    @staticmethod
    def _calculate_fluorescence(*, sensor: str, p_scale: str, wavelength: float,
                                wavelength0: float, wavelength_err: float):
        if sensor == "ruby":
            if p_scale == "ruby_piermarini_1975":
                p = 2.746 * (wavelength - wavelength0)
                return p, 2.746 * wavelength_err, None

            if p_scale == "ruby_mao_1986":
                p, dp = PressureCalculator._calc_mao_type(
                    wavelength, wavelength0, wavelength_err, 1904.0, 7.665, 0, 0
                )
                return p, dp, None

            if p_scale == "ruby_holzapfel_2003":
                A = 1820
                B = 14
                C = 7.3
                A_err = 30
                B_err = 2
                C_err = 0
                r = wavelength / wavelength0

                p = (A / (B+C)) * (np.exp(((B+C)/C) * (1-r**(-C))) - 1)

                X = (B + C)/C * (1 - r**(-C))
                expX = np.exp(X)

                dp_dA = (expX - 1)/(B + C)
                dX_dB = (1/C)*(1 - r**(-C))
                dp_dB = -A/(B + C)**2 * (expX - 1) + (A/(B + C))*expX*dX_dB
                dX_dC = (
                    -B/C**2 * (1 - r**(-C))
                    + (B + C)/C * r**(-C) * np.log(r)
                )
                dp_dC = -A/(B + C)**2 * (expX - 1) + (A/(B + C))*expX*dX_dC
                dX_dwavelength = (B + C)/wavelength * r**(-C)
                dp_dwavelength = (A/(B + C)) * expX * dX_dwavelength

                dp = np.sqrt(
                    (dp_dA * A_err)**2 +
                    (dp_dB * B_err)**2 +
                    (dp_dC * C_err)**2 +
                    (dp_dwavelength * wavelength_err)**2
                )
                return p, dp, None

            if p_scale == "ruby_dorogokupets_oganov_2007":
                A = 1884
                m = 5.5
                d_wavelength = wavelength - wavelength0
                p = A * (d_wavelength / wavelength0) * (1 + m * d_wavelength / wavelength0)
                x = d_wavelength / wavelength0
                dp = abs(A / wavelength0 * (1 + 2*m*x)) * wavelength_err
                return p, dp, None

            if p_scale == "ruby_shen_2020":
                p, dp = PressureCalculator._calc_kunk_type(
                    wavelength, wavelength0, wavelength_err, 1870.0, 5.63, 10, 0.03
                )
                return p, dp, None

            if p_scale == "ruby_kraus_2016":
                p, dp = PressureCalculator._calc_mao_type(
                    wavelength, wavelength0, wavelength_err, 1915.1, 10.603, 0, 0
                )
                return p, dp, None

            if p_scale == "ruby_jacobsen_2008":
                p, dp = PressureCalculator._calc_mao_type(
                    wavelength, wavelength0, wavelength_err, 1904.0, 10.32, 0, 0.07
                )
                return p, dp, None

            if p_scale == "ruby_sokolova_2013":
                p, dp = PressureCalculator._calc_kunk_type(
                    wavelength, wavelength0, wavelength_err, 1870.0, 6.0, 0, 0
                )
                return p, dp, None

        if sensor == "sm_srb4o7":
            def datchi_borate_calc(C, a, b, d_wavelength, C_err, a_err, b_err):
                p = C * d_wavelength * (1 + a * d_wavelength) / (1 + b * d_wavelength)

                dp_dC = d_wavelength * (1 + a*d_wavelength) / (1 + b*d_wavelength)
                dp_da = C * d_wavelength**2 / (1 + b*d_wavelength)
                dp_db = - C * d_wavelength**2 * (1 + a*d_wavelength) / (1 + b*d_wavelength)**2
                dp_dwavelength = C * (
                    (1 + 2*a*d_wavelength)/(1 + b*d_wavelength)
                    - (b*d_wavelength*(1 + a*d_wavelength))/(1 + b*d_wavelength)**2
                )

                dp = np.sqrt(
                    (dp_dC * C_err)**2 +
                    (dp_da * a_err)**2 +
                    (dp_db * b_err)**2 +
                    (dp_dwavelength * wavelength_err)**2
                )
                return p, dp

            if p_scale == "sm_srb4o7_datchi_1997_mxb1986":
                p, dp = datchi_borate_calc(4.032, 9.29e-3, 2.32e-2, wavelength - wavelength0, 0, 0, 0)
                return p, dp, None
            if p_scale == "sm_srb4o7_datchi_2007_do2007":
                p, dp = datchi_borate_calc(3.989, 0.006915, 0.0166, wavelength-wavelength0, 0.006, 0.000074, 0.001)
                return p, dp, None
            if p_scale == "sm_srb4o7_rashchenko_2015_lam11":
                p, dp = PressureCalculator._calc_mao_type(wavelength, wavelength0, wavelength_err, 2836, 14.3, 21, 0.9)
                return p, dp, None
            if p_scale == "sm_srb4o7_rashchenko_2015_lam12":
                p, dp = PressureCalculator._calc_mao_type(wavelength, wavelength0, wavelength_err, 3259, -19.6, 20, 1.2)
                return p, dp, None
            if p_scale == "sm_srb4o7_rashchenko_2015_lam13":
                p, dp = PressureCalculator._calc_mao_type(wavelength, wavelength0, wavelength_err, 2389, -0.9, 15, 0.7)
                return p, dp, None
            if p_scale == "sm_srb4o7_rashchenko_2015_lam14":
                p, dp = PressureCalculator._calc_mao_type(wavelength, wavelength0, wavelength_err, 2988, 35.7, 36, 1.5)
                return p, dp, None
            if p_scale == "sm_srb4o7_wei_2024":
                p, dp = PressureCalculator._calc_mao_type(
                    wavelength, wavelength0, wavelength_err, 2761.0, -9.88, 20.02, 0.82
                )
                return p, dp, None

        if sensor == "sm_yag_y1":
            if p_scale == "sm_yag_y1_wei_2024":
                p, dp = PressureCalculator._calc_mao_type(
                    wavelength, wavelength0, wavelength_err, 2019.2, -1.33, 22.56, 0.97
                )
                return p, dp, None

        if sensor == "sm_srfcl":
            if p_scale == "sm_srfcl_lorenz_1994":
                # P = (A * lam0 / B) * ((lam / lam0)^B - 1), split at the 10 GPa boundary
                # between the two literature B values; select branch from the low-pressure
                # result since P(lam) is monotonic in this range.
                A = 0.904
                A_err = 0.004
                A_eff = A * wavelength0
                A_eff_err = A_err * wavelength0
                p, dp = PressureCalculator._calc_mao_type(
                    wavelength, wavelength0, wavelength_err, A_eff, -11.6, A_eff_err, 0.7
                )
                if p > 10.0:
                    p, dp = PressureCalculator._calc_mao_type(
                        wavelength, wavelength0, wavelength_err, A_eff, -13.6, A_eff_err, 0.6
                    )
                return p, dp, None

            if p_scale == "sm_srfcl_shen_2021":
                C = 1.123
                C_err = 0.002
                d_wavelength = wavelength - wavelength0
                p = d_wavelength / C
                dp = np.sqrt((wavelength_err / C) ** 2 + (d_wavelength / C**2 * C_err) ** 2)
                return p, dp, None

            if p_scale == "sm_srfcl_shen_1991":
                C = 1.10
                d_wavelength = wavelength - wavelength0
                p = d_wavelength / C
                dp = wavelength_err / C
                return p, dp, None

        return None, None, None

    @staticmethod
    def _calculate_raman(*, sensor: str, p_scale: str, wavenumber: float,
                         wavenumber0: float, wavenumber0_at_t0: float,
                         wavenumber_err: float, current_t: float, t0: float):
        if sensor == "diamond_13c_1st_order":
            if p_scale == "diamond_13c_schiferl_1997":
                return (wavenumber - wavenumber0) / 2.83, wavenumber_err / 2.83, None

            if p_scale == "diamond_13c_mysen_yamashita_2010":
                a = 1.65e-2
                a_err = 0.044e-2
                b = 1.769e-5
                b_err = 0.0046e-5
                c = 0.002707 * 1000

                p = ((wavenumber - wavenumber0) + a * current_t + b * current_t**2) / c
                p_err = np.sqrt(
                    wavenumber_err**2 +
                    (current_t * a_err)**2 +
                    (current_t**2 * b_err)**2
                ) / c
                return p, p_err, None

        if sensor == "cubic_bn_to":
            if p_scale == "cubic_bn_datchi_2004":
                a = -9.3*10**-3
                b = -1.54*10**-5
                c0 = 3.07
                c1 = 1.25*10**-3
                c2 = -1.03*10**-6
                d = -0.0103
                A = c0 + c1 * current_t + c2 * current_t**2
                B = wavenumber0_at_t0 + a*current_t + b*current_t**2
                p = -1/(2*d) * (A + np.sqrt(A**2 + 4*d*(wavenumber - B)))

                X = A**2 + 4*d*(wavenumber - B)
                dp = wavenumber_err / np.sqrt(X)
                zero_peak_at_current_t = (
                    B
                    if PressureCalculator.PRESSURE_SCALES[sensor][p_scale].get("reports_zero_peak_at_current_t")
                    else None
                )
                return p, dp, zero_peak_at_current_t

            if p_scale == "cubic_bn_kawamoto_2004":
                a = 3.45
                a_err = 0.03
                p = (wavenumber - wavenumber0) / a
                dp = np.sqrt((wavenumber_err / a)**2 + ((wavenumber - wavenumber0) / a**2 * a_err)**2)
                return p, dp, None

        if sensor == "zircon_b1g":
            if p_scale == "zircon_schmidt_2013":
                return (wavenumber-wavenumber0)/5.69, wavenumber_err / 5.69, None

            if p_scale == "zircon_takahashi_2024":
                return (wavenumber-wavenumber0)/5.48, wavenumber_err / 5.48, None

        if sensor == "quartz_464":
            if p_scale == "quartz_schmidt_ziemann_2000":
                # Schmidt and Ziemann (2000) Eq. 2: P (MPa) = 0.36079*x^2 + 110.86*x,
                # x = pressure-induced shift of the 464 cm-1 line relative to 0.1 MPa
                # at ~23 degC (after any temperature correction has been applied to
                # wavenumber0). Valid for 0 < x <= 20 cm-1 (up to ~2.1 GPa).
                a = 0.36079
                b = 110.86
                x = wavenumber - wavenumber0
                p = (a * x**2 + b * x) / 1000.0
                dp = abs(2.0 * a * x + b) / 1000.0 * wavenumber_err
                return p, dp, None

            if p_scale == "quartz_schmidt_ziemann_2000_linear":
                # Global isotherm slope (d(nu464)/dP)_T = 9 +/- 0.5 cm-1/GPa,
                # reported as constant between 100 and 560 degC.
                a = 9.0
                a_err = 0.5
                x = wavenumber - wavenumber0
                p = x / a
                dp = np.sqrt((wavenumber_err / a) ** 2 + (x / a**2 * a_err) ** 2)
                return p, dp, None

        if sensor == "quartz_128":
            if p_scale == "quartz_li_2025":
                # Li et al. (2025) Eq. 3: domega128(Tc, P) = A*Tc^4 + B*Tc^3 + C*Tc^2
                # + D*Tc + (E + F*Tc)*P + G, with Tc in degC (T0 fixed at 23 degC) and
                # P in MPa. Linear in P, so inverted algebraically for P(domega128, Tc).
                A = 1.20176e-10
                B = -1.64508e-7
                C = 2.0665e-5
                D = -0.02134
                E = 0.00599
                F = 1.60394e-5
                G = 0.48515
                t_c = current_t - 273.15
                baseline = A * t_c**4 + B * t_c**3 + C * t_c**2 + D * t_c + G
                slope = E + F * t_c
                domega = wavenumber - wavenumber0
                p_mpa = (domega - baseline) / slope
                p = p_mpa / 1000.0
                dp = abs(wavenumber_err / slope) / 1000.0
                return p, dp, None

        return None, None, None

    @staticmethod
    def _calc_debye_shift(alpha: float, theta: float, temperature: float) -> float:
        """Debye-model line-shift term: alpha * (T/theta)^4 * integral_0^(theta/T) x^3/(e^x-1) dx."""
        def integrand(x):
            if x == 0:
                return 0.0
            if x > 700:  # Avoid overflow for large x
                return 0.0
            return x ** 3 / (np.exp(x) - 1)

        if temperature == 0:
            return 0.0
        integral, _ = quad(integrand, 0, theta / temperature)
        return alpha * (temperature / theta) ** 4 * integral

    @staticmethod
    def get_corrected_zero_peak(*, sensor: str, t_scale: str, current_t: float, t0: float,
                                zero_peak_at_t0: float) -> float:
        """温度補正されたゼロ圧力ピーク位置を計算する。

        全引数キーワード専用・すべて必須。

        Args:
            sensor: 圧力マーカー名
            t_scale: 温度スケール
            current_t: 現在の温度
            t0: 基準温度
            zero_peak_at_t0: 基準温度でのゼロ圧力ピーク位置

        Returns:
            補正されたゼロ圧力ピーク位置
        """
        if sensor == "ruby":

            def datchi_ruby_temp(temp, a1, a2, a3):
                    # Note: this function returns the absolute wavelength reported in the literature, not the shift from the input lam0_at_t0.
                    if temp < 50:
                        return -0.887
                    else:
                        wl_at_296K = 694.281
                        deltat = temp - 296
                        return wl_at_296K + a1 * deltat + a2 * deltat**2 + a3 * deltat ** 3

            if t_scale == "ruby_ragan_1992":
                def ragan_ruby_temp(temp):
                    wn = 14423 + 4.49 * 10 **-2 * temp -4.81 * 10**-4 * temp ** 2 +3.71 * 10**-7 * temp ** 3
                    return 10 ** 7 / wn
                offset = ragan_ruby_temp(t0) - zero_peak_at_t0
                return ragan_ruby_temp(current_t) - offset
            
            elif t_scale == "ruby_datchi_2007_ht":
                a1, a2, a3 = 0.00746, -3.01e-6, 8.76e-9
                offset = datchi_ruby_temp(t0, a1, a2, a3) - zero_peak_at_t0
                return datchi_ruby_temp(current_t, a1, a2, a3) - offset
            
            elif t_scale == "ruby_datchi_2007_lt":
                a1, a2, a3 = 0.00664, 6.76e-6, -2.33e-8
                offset = datchi_ruby_temp(t0, a1, a2, a3) - zero_peak_at_t0
                return datchi_ruby_temp(current_t, a1, a2, a3) - offset

            elif t_scale == "ruby_kobayashi_unpublished":
                wn_at_t0 = 10 ** 7 / zero_peak_at_t0
                alpha = -458.9
                theta = 794.0
                wn_at_current_t = wn_at_t0 + PressureCalculator._calc_debye_shift(alpha, theta, current_t) - PressureCalculator._calc_debye_shift(alpha, theta, t0)
                return 10 ** 7 / wn_at_current_t

            elif t_scale == "ruby_yen_nicol_1992":
                wn_at_t0 = 10 ** 7 / zero_peak_at_t0
                alpha = -419
                theta = 760
                wn_at_current_t = wn_at_t0 + PressureCalculator._calc_debye_shift(alpha, theta, current_t) - PressureCalculator._calc_debye_shift(alpha, theta, t0)
                return 10 ** 7 / wn_at_current_t

            elif t_scale == "ruby_wei_2024":
                # Cubic fit to R1 wavelength shift at ambient pressure, Table I of
                # Wei et al. 2024, valid 296-773 K. Delta-lambda in nm, deltaT = T - 296 K.
                def wei_ruby_temp(temp):
                    deltat = temp - 296
                    b1, b2, b3 = 7.29e-3, -3.47e-6, 1.09e-8
                    return b1 * deltat + b2 * deltat**2 + b3 * deltat**3
                offset = wei_ruby_temp(t0) - zero_peak_at_t0
                return wei_ruby_temp(current_t) - offset


        if sensor == "sm_srb4o7":
            if t_scale == "sm_srb4o7_datchi_2007":
                def datchi_borate_temp(temp):
                    deltat = temp - 296
                    return -8.7 * 10**-5 * deltat + 4.62 * 10**-6 * deltat**2 -2.38 * 10**-9 * deltat**3
                offset = datchi_borate_temp(t0) - zero_peak_at_t0
                return datchi_borate_temp(current_t) - offset

            if t_scale == "sm_srb4o7_wei_2024":
                # Linear fit to the lambda1 (0-0 line) wavelength shift at ambient
                # pressure, Table II of Wei et al. 2024, valid 296-923 K.
                def wei_borate_temp(temp):
                    deltat = temp - 296
                    a1 = -0.70e-4
                    return a1 * deltat
                offset = wei_borate_temp(t0) - zero_peak_at_t0
                return wei_borate_temp(current_t) - offset

        if sensor == "sm_yag_y1":
            if t_scale == "sm_yag_y1_wei_2024":
                # Linear fit to the Y1 wavelength shift at ambient pressure, Table III
                # of Wei et al. 2024, valid 296-873 K.
                def wei_yag_y1_temp(temp):
                    deltat = temp - 296
                    c1 = -2.12e-4
                    return c1 * deltat
                offset = wei_yag_y1_temp(t0) - zero_peak_at_t0
                return wei_yag_y1_temp(current_t) - offset

        if sensor == "sm_srfcl":
            if t_scale == "sm_srfcl_lorenz_1994":
                # Electron-phonon line-shift model, Eqs. (4)+(5) of Lorenz et al. 1994,
                # jointly fitted: Theta_D = 538(81) K, alpha = 97(15) cm^-1, beta = 2.4(4) cm^-1,
                # T_e = 412 K (= dE(7F1-7F0)/k). Model is defined in wavenumber, not wavelength.
                theta_d = 538.0
                alpha = 97.0
                beta = 2.4
                t_e = 412.0

                def one_phonon_shift(temperature):
                    if temperature == 0:
                        return 0.0
                    c = t_e / theta_d

                    def integrand(x):
                        if x == 0:
                            return 0.0
                        return x**3 / (np.exp(x) - 1) / (x + c)
                    # Cauchy principal value integral (pole at x = T_e/Theta_D).
                    integral, _ = quad(integrand, 0, theta_d / temperature, weight="cauchy", wvar=c)
                    return beta * (temperature / t_e)**2 * integral

                def wavenumber_shift(temperature):
                    return PressureCalculator._calc_debye_shift(alpha, theta_d, temperature) + one_phonon_shift(temperature)

                wn_at_t0 = 10 ** 7 / zero_peak_at_t0
                wn_at_current_t = wn_at_t0 + wavenumber_shift(current_t) - wavenumber_shift(t0)
                return 10 ** 7 / wn_at_current_t
            
        if sensor == "zircon_b1g":
            if t_scale == "zircon_schmidt_2013":
                def schmidt_zircon_temp(temp): # in degC !!
                    return 7.53 * 10**-9 * temp**3 - 1.61 * 10**-5 * temp**2 - 2.89 * 10**-2 * temp + 1008.9
                calc_nu_at_t0 = schmidt_zircon_temp(t0-273.15)
                offset = calc_nu_at_t0 - zero_peak_at_t0
                return schmidt_zircon_temp(current_t-273.15) - offset
            
            if t_scale == "zircon_takahashi_2024":
                def takahashi_zircon_temp(temp): # in K
                    return 7.54 * 10**-9 * temp**3 - 2.23 * 10**-5 * temp**2 - 1.84 * 10**-2 * temp + 1015.44
                calc_nu_at_t0 = takahashi_zircon_temp(t0)
                offset = calc_nu_at_t0 - zero_peak_at_t0
                return takahashi_zircon_temp(current_t) - offset
        if sensor == "cubic_bn_to":
            if t_scale == "cubic_bn_kawamoto_2004":
                def kawamoto_BN_temp(temp):
                    a0 = 1060.6
                    a1 = -0.010
                    a2 = -1.42 * 10**-5
                    return a0 + a1*temp + a2*temp**2
                calc_nu_at_t0 = kawamoto_BN_temp(t0)
                offset = calc_nu_at_t0 - zero_peak_at_t0
                return kawamoto_BN_temp(current_t) - offset

        if sensor == "quartz_464":
            if t_scale == "quartz_schmidt_ziemann_2000":
                def schmidt_quartz464_temp(temp_c):  # in degC !!
                    return (
                        2.50136e-11 * temp_c**4
                        + 1.46454e-8 * temp_c**3
                        - 1.801e-5 * temp_c**2
                        - 0.01216 * temp_c
                        + 0.29
                    )
                calc_nu_at_t0 = schmidt_quartz464_temp(t0 - 273.15)
                offset = calc_nu_at_t0 - zero_peak_at_t0
                return schmidt_quartz464_temp(current_t - 273.15) - offset
        return zero_peak_at_t0

    @staticmethod
    def get_corrected_lam0(*, sensor: str, t_scale: str, current_t: float, t0: float,
                            lam0_at_t0: float) -> float:
        return PressureCalculator.get_corrected_zero_peak(
            sensor=sensor, t_scale=t_scale, current_t=current_t,
            t0=t0, zero_peak_at_t0=lam0_at_t0
        )
