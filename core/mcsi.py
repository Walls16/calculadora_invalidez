"""
mcsi.py  –  Monto Constitutivo del Seguro de Invalidez (MCSI)

Dos fórmulas según el Excel, que son equivalentes actuarialmente pero difieren en notación:

MC1 (solo):
    PBSI = b1 × 12 × ä^(12)_x × (1 + INC)
    PNSI = PBSI × FACBI
    MCSI = PNSI × (1 + a + b)

MC4/MC10/MC16 (con dependientes):
    U[k] = kpx_inv[k] × V^k × (kpy × b1_cy + (1−kpy) × b1_solo)
    PBSI = alpha × Σ U[k]     donde alpha = (1−(1+i)^−1)/(1−(1+i)^(−1/12))
    PNSI = PBSI × FACBI
    MCSI = PNSI × (1 + a + b)
    [Nota: en MC4 el INC se absorbe implícitamente en alpha y en las b1]
"""
from __future__ import annotations
import numpy as np
from .tablas_actuariales import (
    V, ax_invalido_12, _qx_inv, _qx_activo,
    kpx_activo, distribucion_hijos_vivos,
)
from .pension import INC, calcular_b1j_hijos, calcular_b2j_hijos

# ── Constantes ────────────────────────────────────────────────────────────────
I      = 0.035
ALPHA  = (1 - (1 + I)**-1) / (1 - (1 + I)**(-1/12))   # ≈ 11.81285443
A_ADQ  = 0.02
B_ADMIN = 0.01
N      = 64     # número de términos (filas 52:115 del Excel)


def _final(pbsi: float, facbi: float) -> dict:
    pnsi = pbsi * facbi
    mcsi = pnsi * (1 + A_ADQ + B_ADMIN)
    return {"PBSI": pbsi, "PNSI": pnsi, "MCSI": mcsi}


# ─────────────────────────────────────────────────────────────────────────────
#  MC1: Inválido solo  →  b1 × 12 × ä^(12)_x × (1+INC)
# ─────────────────────────────────────────────────────────────────────────────
def mcsi_solo(edad_x: int, b1_mensual: float, facbi: float) -> dict:
    ax12  = ax_invalido_12(edad_x)
    pbsi  = b1_mensual * 12 * ax12 * (1 + INC)
    d = _final(pbsi, facbi)
    d["ax12"] = ax12
    return d


# ─────────────────────────────────────────────────────────────────────────────
#  MC4: Con cónyuge (sin hijos)  →  alpha × Σ U[k]
#  U[k] = kpx_inv × V^k × (kpy × b1_cy + (1−kpy) × b1_solo)
# ─────────────────────────────────────────────────────────────────────────────
def mcsi_con_conyugue(edad_x: int, edad_y: int, sexo_y: str,
                      b1_cy: float, b1_solo: float, facbi: float) -> dict:
    suma_u = 0.0
    kpx = 1.0; kpy = 1.0
    for k in range(N):
        suma_u += kpx * (V**k) * (kpy * b1_cy + (1.0 - kpy) * b1_solo)
        kpx *= 1.0 - _qx_inv(edad_x + k)
        kpy *= 1.0 - _qx_activo(edad_y + k, sexo_y, tabla=2)
    pbsi = ALPHA * suma_u
    d = _final(pbsi, facbi)
    d["suma_U"] = suma_u
    return d


# ─────────────────────────────────────────────────────────────────────────────
#  MC10: Con cónyuge e hijos  →  alpha × Σ U[k]
#  U[k] = kpx_inv × V^k × [kpy × Σ_j P(J=j)·b1(j)  +  (1−kpy) × Σ_j P(J=j)·b2(j)]
# ─────────────────────────────────────────────────────────────────────────────
def mcsi_con_hijos(edad_x: int, edad_y: int, sexo_y: str,
                   hijos: list[dict],
                   sal_prom: float, pmg: float, aa_pct: float,
                   facbi: float) -> dict:
    n = len(hijos)
    suma_u = 0.0
    kpx = 1.0; kpy = 1.0

    for k in range(N):
        dist_j = distribucion_hijos_vivos(hijos, k)
        sub_b1 = sum(dist_j[j] * calcular_b1j_hijos(sal_prom, pmg, aa_pct, True,  j) for j in range(n+1))
        sub_b2 = sum(dist_j[j] * calcular_b2j_hijos(sal_prom, pmg, aa_pct, False, j) for j in range(n+1))
        suma_u += kpx * (V**k) * (kpy * sub_b1 + (1.0 - kpy) * sub_b2)
        kpx *= 1.0 - _qx_inv(edad_x + k)
        kpy *= 1.0 - _qx_activo(edad_y + k, sexo_y, tabla=2)

    pbsi = ALPHA * suma_u
    d = _final(pbsi, facbi)
    d["suma_U"] = suma_u
    return d


# ─────────────────────────────────────────────────────────────────────────────
#  MC16: Con ascendientes  →  alpha × Σ U[k]
#  igual lógica que MC4 pero con tabla de ascendientes
# ─────────────────────────────────────────────────────────────────────────────
def mcsi_con_ascendientes(edad_x: int, ascendientes: list[dict],
                          b1_asc: float, b1_solo: float,
                          facbi: float) -> dict:
    n = len(ascendientes)
    suma_u = 0.0
    kpx = 1.0

    for k in range(N):
        # distribución de cuántos ascendientes siguen vivos en año k
        dist_z = np.zeros(n + 1); dist_z[0] = 1.0
        for asc in ascendientes:
            pz = kpx_activo(asc["edad"], k, asc["sexo"], tabla=2)
            nueva = np.zeros(n + 1)
            for j in range(n + 1):
                if j + 1 <= n: nueva[j+1] += dist_z[j] * pz
                nueva[j] += dist_z[j] * (1.0 - pz)
            dist_z = nueva

        # b si algún ascendiente vive, b_solo si todos murieron
        esp_b = dist_z[0] * b1_solo + sum(dist_z[j] * b1_asc for j in range(1, n+1))
        suma_u += kpx * (V**k) * esp_b
        kpx *= 1.0 - _qx_inv(edad_x + k)

    pbsi = ALPHA * suma_u
    d = _final(pbsi, facbi)
    d["suma_U"] = suma_u
    return d
