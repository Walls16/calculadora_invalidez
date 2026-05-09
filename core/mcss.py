"""
mcss.py  –  Monto Constitutivo del Seguro de Sobrevivencia (MCSS)

Fórmulas exactas del Excel (Tarea_Larga_II.xlsm):

MC7 (con cónyuge, sin hijos):
    PBSS = b2_viuda × 13 × Σ W[k]
    W[k] = (1-kpx_inv) × kpy_t2 × V^k   (col W del Excel)

MC13 (con cónyuge e hijos):
    CE[k] = (1-kpx_inv) × V^k × (kpy × E[b1_ss(j)] + (1-kpy) × E[b2_ss(j)])
    PBSS = (13/12) × alpha × Σ CE[k]     (k=0..97)
    donde:
      b1_ss(j=0) = b2_viuda
      b1_ss(j>=1) = max(CBIV_men, b2_viuda)
      b2_ss(j) = min(j × b2_viuda/3, CBIV_men)
      alpha = (1-(1+i)^-1)/(1-(1+i)^(-1/12))

MC16 (con ascendientes):
    Igual que MC7 pero con pensión de ascendientes
"""
from __future__ import annotations
import numpy as np
from .tablas_actuariales import (
    V, _qx_inv, _qx_activo, kpx_activo,
    distribucion_hijos_vivos, prima_finiquito_hijo, suma_w_mcss,
)
from .pension import INC, CBIV_PCT

I      = 0.035
ALPHA  = (1 - (1 + I)**-1) / (1 - (1 + I)**(-1/12))
A_ADQ  = 0.02


def _b_ss(j: int, b2_viuda: float, cbiv_men: float) -> tuple[float, float]:
    """
    b1_ss(j) y b2_ss(j) para el seguro de sobrevivencia con hijos.
    b1_ss: pensión mientras cónyuge vive (o tras su muerte con j hijos)
    b2_ss: pensión de orfandad (solo hijos, cónyuge muerto)
    """
    b1_ss = b2_viuda if j == 0 else min(max(cbiv_men, b2_viuda), cbiv_men)
    b2_ss = min(j * b2_viuda / 3, cbiv_men)
    return b1_ss, b2_ss


# ─────────────────────────────────────────────────────────────────────────────
#  MC7: Con cónyuge sin hijos  →  PBSS = b2 × 13 × Σ W[k]
# ─────────────────────────────────────────────────────────────────────────────
def mcss_xy(edad_x: int, edad_y: int, sexo_y: str,
            b2_mensual: float, facbi: float) -> dict:
    sw   = suma_w_mcss(edad_x, edad_y, sexo_y)
    pbss = b2_mensual * 13 * sw
    pnss = pbss * facbi
    mcss = pnss * (1 + A_ADQ)
    return {"suma_W": sw, "PBSS": pbss, "PNSS": pnss, "MCSS": mcss}


# ─────────────────────────────────────────────────────────────────────────────
#  MC13: Con cónyuge e hijos
#  PBSS = (13/12) × alpha × Σ_{k=0}^{97} CE[k]
#  CE[k] = (1-kpx_inv) × V^k × (kpy × E[b1_ss(j)] + (1-kpy) × E[b2_ss(j)])
# ─────────────────────────────────────────────────────────────────────────────
def pbss_hijos_xy(edad_x: int, edad_y: int, sexo_y: str,
                  hijos: list[dict],
                  sal_prom: float, b2_viuda: float,
                  pmg: float, facbi: float) -> dict:
    n        = len(hijos)
    cbiv_men = CBIV_PCT * sal_prom * 365 / 12
    suma_ce  = 0.0
    kpx      = 1.0
    kpy      = 1.0

    for k in range(98):        # CE18:CE115 = 98 términos
        prob_muerto_x = 1.0 - kpx   # (1 - kpx_inv)
        dist_j = distribucion_hijos_vivos(hijos, k)

        e_b1ss = sum(dist_j[j] * _b_ss(j, b2_viuda, cbiv_men)[0] for j in range(n+1))
        e_b2ss = sum(dist_j[j] * _b_ss(j, b2_viuda, cbiv_men)[1] for j in range(n+1))

        ce_k = prob_muerto_x * (V**k) * (kpy * e_b1ss + (1.0 - kpy) * e_b2ss)
        suma_ce += ce_k

        kpx *= 1.0 - _qx_inv(edad_x + k)
        kpy *= 1.0 - _qx_activo(edad_y + k, sexo_y, tabla=2)

    pbss = (13 / 12) * ALPHA * suma_ce
    pnss = pbss * facbi
    mcss = pnss * (1 + A_ADQ)
    return {"suma_CE": suma_ce, "PBSS": pbss, "PNSS": pnss, "MCSS": mcss}


# ─────────────────────────────────────────────────────────────────────────────
#  MC16: Con ascendientes
# ─────────────────────────────────────────────────────────────────────────────
def mcss_ascendientes(edad_x: int, ascendientes: list[dict],
                      b2_asc_mensual: float, facbi: float) -> dict:
    pbss_total = 0.0
    for asc in ascendientes:
        sw_i = suma_w_mcss(edad_x, asc["edad"], asc["sexo"])
        pbss_total += b2_asc_mensual * 13 * sw_i
    pbss_total *= (1 + INC)
    pnss = pbss_total * facbi
    mcss = pnss * (1 + A_ADQ)
    return {"PBSS": pbss_total, "PNSS": pnss, "MCSS": mcss}
