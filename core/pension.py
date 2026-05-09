"""
pension.py
Calcula la cuantía de la pensión (b1 y b2) conforme a la LSS.

Lógica extraída directamente del Excel Tarea_Larga_II.xlsm.
"""
import os
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional

_BASE = os.path.join(os.path.dirname(__file__), "..", "data")

# ─── Constantes actuariales fijas (Nota Técnica CNSF) ────────────────────────
CBIV_PCT  = 0.35     # cuantía básica invalidez = 35% del salario promedio
AF_CONYUGUE = 0.15   # asignación familiar cónyuge
AF_HIJO     = 0.10   # asignación familiar por hijo
AF_SOLEDAD  = 0.15   # ayuda asistencial por soledad
AF_ASCEND   = 0.10   # asignación familiar por ascendiente
INC         = 0.11   # incremento (gastos de administración + utilidad)


# ─────────────────────────────────────────────────────────────────────────────
#  Historia salarial → Salario Promedio actualizado
# ─────────────────────────────────────────────────────────────────────────────
def actualizar_salarios(historia: dict[int, float],
                        inpc_anual: dict[int, float],
                        anio_objetivo: int) -> dict[int, float]:
    """
    Actualiza cada salario histórico con INPC promedio anual.
    historia       : {año: salario_diario_promedio}
    inpc_anual     : {año: inpc_promedio_anual}
    anio_objetivo  : año al que se actualiza (generalmente año de pensión)
    Devuelve       : {año: salario_diario_actualizado}
    """
    inpc_obj = inpc_anual.get(anio_objetivo)
    if inpc_obj is None:
        raise ValueError(f"No hay INPC para {anio_objetivo}")

    actualizados = {}
    for anio, sal in historia.items():
        inpc_base = inpc_anual.get(anio)
        if inpc_base is None or sal is None:
            continue
        factor = inpc_obj / inpc_base
        actualizados[anio] = sal * factor
    return actualizados


def salario_promedio_500(historia_actualizada: dict[int, float]) -> float:
    """
    Promedio de los salarios actualizados de las últimas 500 semanas ≈
    AVERAGE de todos los años con salario disponible (igual que el Excel).
    """
    vals = [v for v in historia_actualizada.values() if v is not None and v > 0]
    if not vals:
        raise ValueError("Historia salarial vacía")
    return float(np.mean(vals))


# ─────────────────────────────────────────────────────────────────────────────
#  PMG  (Pensión Mínima Garantizada  — Art. 170 LSS)
# ─────────────────────────────────────────────────────────────────────────────
_pmg_cache: Optional[pd.DataFrame] = None

def _cargar_pmg() -> pd.DataFrame:
    global _pmg_cache
    if _pmg_cache is None:
        _pmg_cache = pd.read_csv(os.path.join(_BASE, "pmg_actualizado.csv"))
    return _pmg_cache


def pmg_vigente(anio: Optional[int] = None) -> float:
    """
    Devuelve la PMG actualizada para el año indicado (default: último disponible).
    Equivale al valor de 'Art 170 (PMG)'!S14 del Excel.
    """
    df = _cargar_pmg()
    if anio is not None:
        row = df[df["anio"] == anio]
        if not row.empty:
            return float(row.iloc[0]["pmg_actualizado"])
    return float(df.iloc[-1]["pmg_actualizado"])


# ─────────────────────────────────────────────────────────────────────────────
#  Cuantía de pensión  b1  (inválido pensionado)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ComposicionFamiliar:
    """Describe la composición familiar del pensionado."""
    tiene_conyugue:  bool = False
    aa_pct:          float = 0.0          # % ayuda asistencial (0-0.20)
    num_hijos:       int = 0              # hijos ≤ 25 años con derecho
    num_ascendientes: int = 0             # 0, 1 ó 2

    @property
    def af_total(self) -> float:
        """Asignación familiar total (sin incluir AA)."""
        if self.tiene_conyugue:
            return AF_CONYUGUE + AF_HIJO * self.num_hijos
        elif self.num_hijos > 0:
            return AF_HIJO * self.num_hijos
        elif self.num_ascendientes > 0:
            return AF_ASCEND * self.num_ascendientes
        else:
            return AF_SOLEDAD   # solo

    @property
    def af_solo(self) -> float:
        """Porcentaje de ayuda asistencial por soledad (0 si tiene dependientes)."""
        if not self.tiene_conyugue and self.num_hijos == 0 and self.num_ascendientes == 0:
            return AF_SOLEDAD
        return 0.0


def calcular_b1(sal_prom_diario: float,
                composicion: ComposicionFamiliar,
                pmg: float,
                aa_pct: float = 0.0) -> float:
    """
    Calcula b1 (mensual) = cuantía básica + asignaciones + aguinaldo.

    Fórmula LSS Art. 141 + Nota Técnica:
        CBIV = 0.35 × sal_prom_500
        Pensión_diaria = CBIV × (1 + AF + AA)
        Pensión_mensual = Pensión_diaria × 365/12
        Restricciones:
          - No menor a PMG
          - No mayor a sal_prom_500 × 365/12
        Aguinaldo = (1/12) × max(Pensión_diaria × 365/12, PMG)
        b1 = Pensión_mensual_restringida + Aguinaldo
    """
    af  = composicion.af_total
    aa  = aa_pct

    cbiv_diario  = CBIV_PCT * sal_prom_diario
    pension_dia  = cbiv_diario * (1 + af + aa)
    pension_mes  = pension_dia * 365 / 12

    # Restricciones
    pension_mes = max(pension_mes, pmg)
    pension_mes = min(pension_mes, sal_prom_diario * 365 / 12)

    aguinaldo = (1 / 12) * max(cbiv_diario * 365 / 12, pmg)
    b1 = pension_mes + aguinaldo
    return b1


def calcular_b2(sal_prom_diario: float, pmg: float) -> float:
    """
    b2 = pensión de viudez = 90% de la pensión básica del inválido (sin AA).
    Se calcula con la pensión del inválido SIN asignaciones familiares ni AA
    (solo cónyuge AF=0.15, que ya estaba), restringida por PMG.

    El Excel usa:
        CBIV = 0.35 × sal_prom
        R0vda = 0.90 × CBIV
        Pensión_mensual_vda = R0vda × 365/12
        b2 = max(PMG × 0.9, pensión_mensual_vda) + aguinaldo
    """
    cbiv_diario  = CBIV_PCT * sal_prom_diario
    r0vda_diario = 0.90 * cbiv_diario
    pension_mes  = r0vda_diario * 365 / 12

    pmg_vda = 0.90 * pmg
    b2 = max(pmg_vda, pension_mes)
    return b2


def calcular_b1j_hijos(sal_prom_diario: float,
                        pmg: float,
                        aa_pct: float,
                        tiene_conyugue: bool,
                        j_hijos_vivos: int) -> float:
    """
    b1(j) con cónyuge vivo: cuantía cuando hay j hijos con derecho a pensión.
    Fórmula EXACTA del Excel (MCSIx,y,x1,x2,x3 C22-C26):
        CBIV_men = CBIV_diario × 365/12
        b1(j) = MAX(CBIV_men × (1 + AA + 0.15 + 0.1×j), PMG) + (1/12)×CBIV_men
    Nota: el aguinaldo usa CBIV_mensual, no PMG.
    """
    cbiv_men = CBIV_PCT * sal_prom_diario * 365 / 12
    af_conyugue = 0.15 if tiene_conyugue else 0.0
    pension = max(cbiv_men * (1 + aa_pct + AF_HIJO * j_hijos_vivos + af_conyugue), pmg)
    return pension + cbiv_men / 12


def calcular_b2j_hijos(sal_prom_diario: float,
                        pmg: float,
                        aa_pct: float,
                        tiene_conyugue: bool,
                        j_hijos_vivos: int) -> float:
    """
    b2(j) cónyuge no vivo: pensión de orfandad con j hijos.
    Fórmula EXACTA del Excel (MCSIx,y,x1,x2,x3 F22-F26):
        CBIV_men = CBIV_diario × 365/12
        b2(j) = MAX(CBIV_men × (1 + 0.16 + 0.1×j), PMG) + (1/12)×CBIV_men
    Sin AF de cónyuge y aguinaldo = CBIV_men/12.
    """
    cbiv_men = CBIV_PCT * sal_prom_diario * 365 / 12
    pension = max(cbiv_men * (1 + aa_pct + AF_HIJO * j_hijos_vivos), pmg)
    return pension + cbiv_men / 12


def b_ss_hijos(pmg: float, j: int, tiene_conyugue: bool) -> tuple[float, float]:
    """
    Pensiones de sobrevivencia para el seguro de sobrevivencia con hijos.
    b1_ss(j) = min(0.9 + j×0.2, 1.0) × PMG   (cónyuge vivo)
    b2_ss(j) = min(j×0.3, 1.0) × PMG          (cónyuge no vivo)
    """
    b1 = min(0.9 + j * 0.20, 1.0) * pmg
    b2 = min(j * 0.30, 1.0) * pmg
    return b1, b2


def b_ascendientes(sal_prom_diario: float,
                   pmg: float,
                   num_ascendientes: int) -> float:
    """
    b1 para inválido con ascendientes (sin cónyuge ni hijos).
    AF = 10% × num_ascendientes (máx 2) + ayuda asistencial 10% si solo 1.
    """
    aa = AF_ASCEND if num_ascendientes == 1 else 0.0
    af = AF_ASCEND * num_ascendientes
    cbiv = CBIV_PCT * sal_prom_diario
    pension_dia = cbiv * (1 + af + aa)
    pension_mes = max(pension_dia * 365 / 12, pmg)
    pension_mes = min(pension_mes, sal_prom_diario * 365 / 12)
    aguinaldo = (1 / 12) * max(cbiv * 365 / 12, pmg)
    return pension_mes + aguinaldo


# ─────────────────────────────────────────────────────────────────────────────
#  Validación de requisitos de elegibilidad (LSS Art. 119-122)
# ─────────────────────────────────────────────────────────────────────────────

# Grado mínimo de invalidez para pensión permanente (Art. 119 LSS)
GRADO_MIN_PENSION     = 0.50   # 50% para pensión definitiva
GRADO_MIN_SUBSIDIO    = 0.25   # 25–49% para subsidio temporal
SEMANAS_MIN_INVALIDES = 150    # Art. 122 LSS: mínimo 150 semanas cotizadas
                               # salvo que invalidez sea por riesgo de trabajo


def evaluar_elegibilidad(semanas: int, grado_invalidez: float,
                         por_riesgo_trabajo: bool = False) -> dict:
    """
    Evalúa si el trabajador cumple los requisitos para pensión de invalidez.

    Parámetros
    ----------
    semanas           : semanas cotizadas al IMSS
    grado_invalidez   : porcentaje de invalidez (0.0 – 1.0)
    por_riesgo_trabajo: True si la invalidez deriva de riesgo de trabajo
                        (exime del requisito de semanas, Art. 122 LSS)

    Devuelve
    --------
    dict con claves:
      eligible         : bool  – puede tramitar pensión
      tipo_prestacion  : str   – "pensión definitiva" | "subsidio temporal" | "no elegible"
      advertencias     : list  – mensajes de advertencia
      bloquear_calculo : bool  – True si no tiene sentido calcular MC
    """
    advertencias = []
    tipo = "no elegible"
    eligible = False
    bloquear = False

    # ── 1. Grado de invalidez ────────────────────────────────────────────────
    if grado_invalidez < GRADO_MIN_SUBSIDIO:
        advertencias.append(
            f"⛔ Grado de invalidez {grado_invalidez*100:.1f}% es menor al mínimo del 25% "
            f"(Art. 119 LSS). No procede ninguna prestación en dinero."
        )
        bloquear = True

    elif grado_invalidez < GRADO_MIN_PENSION:
        advertencias.append(
            f"⚠️ Grado de invalidez {grado_invalidez*100:.1f}% corresponde a invalidez "
            f"parcial (25–49%). Se otorga subsidio temporal, no pensión definitiva. "
            f"El Monto Constitutivo aplica solo para pensión definitiva (≥50%)."
        )
        tipo = "subsidio temporal"
        bloquear = True   # MC solo aplica a pensión definitiva

    else:
        tipo = "pensión definitiva"

    # ── 2. Semanas cotizadas ─────────────────────────────────────────────────
    if not por_riesgo_trabajo:
        if semanas < SEMANAS_MIN_INVALIDES:
            advertencias.append(
                f"⛔ Solo {semanas} semanas cotizadas. Se requieren mínimo "
                f"{SEMANAS_MIN_INVALIDES} semanas para acceder a pensión de invalidez "
                f"(Art. 122 LSS). Salvo que la invalidez derive de un riesgo de trabajo."
            )
            bloquear = True
        elif semanas < 250:
            advertencias.append(
                f"⚠️ {semanas} semanas cotizadas — cumple el mínimo legal ({SEMANAS_MIN_INVALIDES}), "
                f"pero el salario promedio se calculará sobre un historial corto, "
                f"lo que puede resultar en pensión cercana a la PMG."
            )

    if not bloquear and not advertencias:
        eligible = True
    elif not bloquear:
        eligible = True   # hay advertencias pero puede continuar

    return {
        "eligible"        : eligible and not bloquear,
        "tipo_prestacion" : tipo,
        "advertencias"    : advertencias,
        "bloquear_calculo": bloquear,
    }
