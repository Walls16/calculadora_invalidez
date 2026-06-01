"""
calculadora.py  –  Orquestador principal
Recibe ParametrosInvalido y devuelve el desglose completo MCSI + MCSS.
"""
from __future__ import annotations
import datetime
from dataclasses import dataclass, field
from typing import Optional

from .datos_externos import calcular_facbi, inpc_ultima_quincena, factor_inpc_excel, pmg_vigente
from .pension import (
    ComposicionFamiliar, salario_promedio_500,
    calcular_b1, calcular_b2,
    b_ascendientes, AF_SOLEDAD,
)
from .mcsi import (
    mcsi_solo, mcsi_con_conyugue, mcsi_con_hijos, mcsi_con_ascendientes,
)
from .mcss import (
    mcss_xy, pbss_hijos_xy, mcss_ascendientes,
)


@dataclass
class ParametrosInvalido:
    # ── Datos del inválido
    edad_x:            int
    sexo_x:            str                   # 'H' o 'M'
    semanas_cotizadas: int
    historia_salarial: dict[int, float]      # {año: salario_diario_promedio}

    # ── Composición familiar
    tiene_conyugue:    bool = False
    edad_y:            Optional[int]  = None
    sexo_y:            Optional[str]  = None

    hijos:             list[dict] = field(default_factory=list)
    # [{'edad': int, 'sexo': 'H'|'M'}, ...]

    ascendientes:      list[dict] = field(default_factory=list)
    # [{'edad': int, 'sexo': 'H'|'M'}, ...]

    # ── Parámetros actuariales
    aa_pct:            float = 0.0          # ayuda asistencial 0.0–0.20

    # ── Fecha de pensión
    mes_pension:       int = 2
    anio_pension:      int = field(
        default_factory=lambda: datetime.date.today().year)

    # ── Override de salario promedio (modo "Promedio directo" en la UI)
    # Si se provee, se usa directamente como sal_prom_500 y se omite el
    # cálculo por INPC sobre la historia salarial.
    sal_prom_override: Optional[float] = None


# ─────────────────────────────────────────────────────────────────────────────
def calcular_montos(p: ParametrosInvalido) -> dict:
    """
    Devuelve un diccionario con todos los componentes del cálculo.
    """
    r = {}

    # ── 1. INPC y salario promedio actualizado ────────────────────────────────
    # Reproduce lógica exacta del Excel: factor = INPC_ref / INPC_2Q_jun_{año_sal}
    _, _, _, inpc_ref_val = inpc_ultima_quincena()
    historia_act = {}
    for anio_sal, sal_diario in p.historia_salarial.items():
        if sal_diario and sal_diario > 0:
            try:
                f = factor_inpc_excel(int(anio_sal), inpc_ref_val)
                historia_act[int(anio_sal)] = sal_diario * f
            except ValueError:
                pass

    # ── Salario promedio 500 semanas ──────────────────────────────────────────
    if p.sal_prom_override is not None and p.sal_prom_override > 0:
        # Modo "Promedio directo": el usuario ya calculó el promedio externamente.
        sal_prom = float(p.sal_prom_override)
        # Poblamos historia_act con ese valor para que el expander lo muestre.
        if not historia_act:
            historia_act = {p.anio_pension: sal_prom}
    elif historia_act:
        sal_prom = salario_promedio_500(historia_act)
    else:
        # Fallback: ningún año tuvo factor INPC disponible; usar salarios sin actualizar.
        historia_act = {
            int(a): float(s)
            for a, s in p.historia_salarial.items()
            if s and float(s) > 0
        }
        if not historia_act:
            raise ValueError(
                "Historia salarial vacía: no se encontraron salarios válidos "
                "ni factores INPC para ningún año. Revisa los datos o usa el "
                "modo 'Promedio directo'."
            )
        sal_prom = salario_promedio_500(historia_act)

    r["historia_actualizada"] = historia_act
    r["sal_prom_500"]          = sal_prom

    # ── 2. PMG ────────────────────────────────────────────────────────────────
    pmg = pmg_vigente(p.anio_pension)
    r["PMG"] = pmg

    # ── 3. FACBI ──────────────────────────────────────────────────────────────
    facbi = calcular_facbi(p.mes_pension, p.anio_pension)
    r["FACBI"] = facbi

    # ── 4. b1 y b2 ───────────────────────────────────────────────────────────
    composicion = ComposicionFamiliar(
        tiene_conyugue   = p.tiene_conyugue,
        aa_pct           = p.aa_pct,
        num_hijos        = len(p.hijos),
        num_ascendientes = len(p.ascendientes),
    )
    b1 = calcular_b1(sal_prom, composicion, pmg, p.aa_pct)
    b2 = calcular_b2(sal_prom, pmg)         # pensión viuda = 90% base

    # b1 sin cónyuge (con soledad, sin AA) — para MC4 el b2 del inválido
    composicion_solo = ComposicionFamiliar(tiene_conyugue=False, aa_pct=0.0,
                                           num_hijos=0, num_ascendientes=0)
    b1_solo = calcular_b1(sal_prom, composicion_solo, pmg, 0.0)

    r["b1_mensual"]      = b1
    r["b2_viuda_mens"]   = b2
    r["b1_solo_mensual"] = b1_solo

    # ═════════════════════════════════════════════════════════════════════════
    if not p.tiene_conyugue and not p.hijos and not p.ascendientes:
        # MC1 ─ Solo
        mcsi = mcsi_solo(p.edad_x, b1, facbi)
        r.update(caso="MC1 – Inválido solo", MCSI=mcsi, MCSS=None,
                 MC_TOTAL=mcsi["MCSI"])

    elif p.tiene_conyugue and not p.hijos:
        # MC4 ─ Con cónyuge sin hijos
        mcsi = mcsi_con_conyugue(
            p.edad_x, p.edad_y, p.sexo_y, b1, b1_solo, facbi)
        mcss = mcss_xy(p.edad_x, p.edad_y, p.sexo_y, b2, facbi)
        r.update(caso="MC4 – Inválido con cónyuge", MCSI=mcsi, MCSS=mcss,
                 MC_TOTAL=mcsi["MCSI"] + mcss["MCSS"])

    elif p.tiene_conyugue and p.hijos:
        # MC10 ─ Con cónyuge e hijos
        mcsi = mcsi_con_hijos(
            p.edad_x, p.edad_y, p.sexo_y, p.hijos,
            sal_prom, pmg, p.aa_pct, facbi)
        mcss = pbss_hijos_xy(
            p.edad_x, p.edad_y, p.sexo_y, p.hijos,
            sal_prom, b2, pmg, facbi)
        r.update(caso="MC10 – Inválido con cónyuge e hijos",
                 MCSI=mcsi, MCSS=mcss,
                 MC_TOTAL=mcsi["MCSI"] + mcss["MCSS"])

    elif p.ascendientes:
        # MC16 ─ Con ascendientes
        b1_asc      = b_ascendientes(sal_prom, pmg, len(p.ascendientes))
        b1_asc_viuda = calcular_b2(sal_prom, pmg) * 0.10  # 10% pensión base
        mcsi = mcsi_con_ascendientes(
            p.edad_x, p.ascendientes, b1_asc, b1_solo, facbi)
        mcss = mcss_ascendientes(
            p.edad_x, p.ascendientes, b1_asc_viuda, facbi)
        r.update(caso="MC16 – Inválido con ascendientes",
                 MCSI=mcsi, MCSS=mcss,
                 MC_TOTAL=mcsi["MCSI"] + mcss["MCSS"])

    else:
        r.update(caso="Composición no reconocida",
                 MCSI={}, MCSS={}, MC_TOTAL=0.0)

    return r