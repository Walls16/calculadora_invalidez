"""
tablas_actuariales.py
Tablas de mortalidad y anualidades para MCSI/MCSS.

Tablas:
  qx_h_t1 / qx_m_t1 : EMSSAH/EMSSAM-15 tabla 1 (para asegurado activo)
  qx_h_t2 / qx_m_t2 : EMSSAH/EMSSAM-15 tabla 2 (para cónyuge/hijos, MCSS)
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd
from functools import lru_cache

_BASE = os.path.join(os.path.dirname(__file__), "..", "data")

I_TECNICA = 0.035
V = 1 / (1 + I_TECNICA)

@lru_cache(maxsize=1)
def _df_invalidos() -> pd.DataFrame:
    return pd.read_csv(os.path.join(_BASE, "qx_invalidos.csv")).sort_values("edad").set_index("edad")

@lru_cache(maxsize=1)
def _df_activos() -> pd.DataFrame:
    return pd.read_csv(os.path.join(_BASE, "qx_activos.csv")).sort_values("edad").set_index("edad")

@lru_cache(maxsize=1)
def _df_desercion() -> pd.DataFrame:
    return pd.read_csv(os.path.join(_BASE, "desercion.csv")).sort_values("edad").set_index("edad")

def _qx_inv(edad: int) -> float:
    df = _df_invalidos()
    return float(df.loc[edad, "qx_inv"]) if edad in df.index else 1.0

def _qx_activo(edad: int, sexo: str, tabla: int = 2) -> float:
    df  = _df_activos()
    col = f"qx_{'h' if sexo.upper().startswith('H') else 'm'}_t{tabla}"
    return float(df.loc[edad, col]) if edad in df.index else 1.0

def _qx_desercion(edad: int) -> float:
    df = _df_desercion()
    return float(df.loc[edad, "qx_d"]) if edad in df.index else (1.0 if edad >= 25 else 0.0)

def kpx_invalido(edad_x: int, k: int) -> float:
    p = 1.0
    for j in range(k):
        p *= 1.0 - _qx_inv(edad_x + j)
        if p < 1e-12: return 0.0
    return p

def kpx_activo(edad: int, k: int, sexo: str, tabla: int = 2) -> float:
    p = 1.0
    for j in range(k):
        p *= 1.0 - _qx_activo(edad + j, sexo, tabla)
        if p < 1e-12: return 0.0
    return p

def ax_invalido_12(edad_x: int, edad_max: int = 110) -> float:
    """ä^(12)_x = ä_x_anual − 11/24 (Woolhouse primer orden)."""
    total = 0.0; kp = 1.0
    for k in range(edad_max - edad_x + 1):
        total += (V ** k) * kp
        if edad_x + k >= edad_max: break
        kp *= 1.0 - _qx_inv(edad_x + k)
    return total - 11 / 24

def suma_w_mcss(edad_x: int, edad_y: int, sexo_y: str, n_anios: int = 64) -> float:
    """
    Σ_{k=0}^{n-1} V^k × (1 − kpx_inv) × kpy_t2
    Reproduce col W del Excel (SUM W52:W115 = 64 términos, tabla 2 para cónyuge).
    """
    total = 0.0; kpx = 1.0; kpy = 1.0
    for k in range(n_anios):
        total += (V ** k) * (1.0 - kpx) * kpy
        kpx *= 1.0 - _qx_inv(edad_x + k)
        kpy *= 1.0 - _qx_activo(edad_y + k, sexo_y, tabla=2)
    return total

def _px_hijo_pension(edad_hijo: int, k: int, sexo_hijo: str) -> float:
    if edad_hijo + k >= 25: return 0.0
    p_bio = kpx_activo(edad_hijo, k, sexo_hijo, tabla=2)
    p_esc = (1.0 - _qx_desercion(edad_hijo + k)) if edad_hijo + k >= 16 else 1.0
    return p_bio * p_esc

def distribucion_hijos_vivos(hijos: list[dict], k: int) -> np.ndarray:
    """P[j] = P(j hijos con derecho a pensión en año k). Convolución Poisson-Binomial."""
    n = len(hijos)
    p_i = np.array([_px_hijo_pension(h["edad"], k, h["sexo"]) for h in hijos])
    dist = np.zeros(n + 1); dist[0] = 1.0
    for p in p_i:
        nueva = np.zeros(n + 1)
        for j in range(n + 1):
            if dist[j] < 1e-15: continue
            if j + 1 <= n: nueva[j + 1] += dist[j] * p
            nueva[j] += dist[j] * (1.0 - p)
        dist = nueva
    return dist

def prima_finiquito_hijo(edad_hijo: int, sexo_hijo: str, pension_mensual_hijo: float) -> float:
    k_fin = max(0, 25 - edad_hijo)
    kp = kpx_activo(edad_hijo, k_fin, sexo_hijo, tabla=2)
    return pension_mensual_hijo * 3 * (V ** k_fin) * kp
