"""
datos_externos.py
UDIs e INPC desde Banxico SIE API — con fallback a CSV local.

El token de Banxico puede pasarse de tres formas (en orden de prioridad):
  1. Variable de entorno: BANXICO_TOKEN=...
  2. Archivo  ~/.banxico_token  (una sola línea con el token)
  3. Pasado en tiempo de ejecución con set_token(token)

Obtén tu token gratuito en: https://www.banxico.org.mx/SieAPIRest/service/v1/token
"""
from __future__ import annotations
import datetime
import os
import pandas as pd
import requests

_BASE     = os.path.join(os.path.dirname(__file__), "..", "data")
_UDI_CSV  = os.path.join(_BASE, "udis.csv")
_INPC_CSV = os.path.join(_BASE, "inpc.csv")
_PMG_CSV  = os.path.join(_BASE, "pmg_actualizado.csv")

_SIE = "https://www.banxico.org.mx/SieAPIRest/service/v1/series"

# ─── Gestión del token ────────────────────────────────────────────────────────
_runtime_token: str = ""

def set_token(token: str) -> None:
    """Configura el token en tiempo de ejecución (llamado desde la UI)."""
    global _runtime_token, _inpc_cache, _udi_cache
    _runtime_token = token.strip()
    # Limpiar caché para forzar re-fetch con el nuevo token
    _inpc_cache = None
    _udi_cache  = None

def get_token() -> str:
    """Devuelve el token activo (runtime > env > archivo)."""
    if _runtime_token:
        return _runtime_token
    env = os.environ.get("BANXICO_TOKEN", "").strip()
    if env:
        return env
    try:
        path = os.path.expanduser("~/.banxico_token")
        if os.path.exists(path):
            return open(path).read().strip()
    except Exception:
        pass
    return ""

def token_activo() -> bool:
    return bool(get_token())

# ─── Banxico helper ───────────────────────────────────────────────────────────
def _banxico_get(serie: str, inicio: str, fin: str) -> pd.DataFrame | None:
    token = get_token()
    if not token:
        return None
    url = f"{_SIE}/{serie}/datos/{inicio}/{fin}"
    try:
        r = requests.get(url, headers={"Bmx-Token": token}, timeout=15)
        r.raise_for_status()
        datos = r.json()["bmx"]["series"][0]["datos"]
        df = pd.DataFrame(datos)
        df["fecha"] = pd.to_datetime(df["fecha"], format="%d/%m/%Y")
        df["dato"]  = pd.to_numeric(df["dato"], errors="coerce")
        return df.dropna(subset=["dato"])
    except Exception:
        return None

def probar_token(token: str) -> tuple[bool, str]:
    """
    Verifica si el token es válido consultando la UDI de hoy.
    Devuelve (éxito, mensaje).
    """
    set_token(token)
    hoy = datetime.date.today().isoformat()
    df = _banxico_get("SP68257", hoy, hoy)
    if df is not None and not df.empty:
        return True, f"Token válido ✓  (UDI hoy: {float(df.iloc[-1]['dato']):.6f})"
    # Si no hay dato hoy (fin de semana), intentar los últimos 5 días
    hace5 = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
    df2 = _banxico_get("SP68257", hace5, hoy)
    if df2 is not None and not df2.empty:
        return True, f"Token válido ✓  (UDI más reciente: {float(df2.iloc[-1]['dato']):.6f})"
    return False, "Token inválido o sin conexión con Banxico"

# ─── INPC ─────────────────────────────────────────────────────────────────────
_inpc_cache: pd.DataFrame | None = None
_inpc_source: str = ""

def cargar_inpc(forzar: bool = False) -> pd.DataFrame:
    """DataFrame [anio, mes, quincena, inpc, fecha]. Fuente: API o CSV."""
    global _inpc_cache, _inpc_source
    if _inpc_cache is not None and not forzar:
        return _inpc_cache

    hoy = datetime.date.today()
    df  = _banxico_get("SP1", "1988-01-01", hoy.isoformat())
    if df is not None and not df.empty:
        df["anio"]     = df["fecha"].dt.year
        df["mes"]      = df["fecha"].dt.month
        df["quincena"] = df["fecha"].dt.day.apply(lambda d: 1 if d <= 15 else 2)
        df = df.rename(columns={"dato": "inpc"})[["anio","mes","quincena","inpc","fecha"]]
        _inpc_cache  = df.reset_index(drop=True)
        _inpc_source = "Banxico API"
        return _inpc_cache

    # Fallback CSV
    df = pd.read_csv(_INPC_CSV, header=0, names=["periodo","inpc"])
    df = df.dropna(subset=["inpc"])
    partes = df["periodo"].str.split("/", expand=True).astype(int)
    df["anio"]     = partes[0]
    df["mes"]      = partes[1]
    df["quincena"] = partes[2]
    df["fecha"]    = pd.to_datetime(
        df["anio"].astype(str) + "-" + df["mes"].astype(str).str.zfill(2) + "-" +
        df["quincena"].map({1:"01", 2:"16"})
    )
    _inpc_cache  = df[["anio","mes","quincena","inpc","fecha"]].reset_index(drop=True)
    _inpc_source = "CSV local"
    return _inpc_cache

def fuente_inpc() -> str:
    cargar_inpc()
    return _inpc_source

def inpc_quincena(anio: int, mes: int, quincena: int) -> float:
    df  = cargar_inpc()
    sub = df[(df["anio"]==anio) & (df["mes"]==mes) & (df["quincena"]==quincena)]
    if sub.empty:
        sub = df[(df["anio"]==anio) & (df["mes"]==mes)]
    if sub.empty:
        raise ValueError(f"Sin INPC {anio}/{mes} Q{quincena}")
    return float(sub.iloc[-1]["inpc"])

def inpc_ultima_quincena() -> tuple[int, int, int, float]:
    df  = cargar_inpc()
    row = df.iloc[-1]
    return int(row["anio"]), int(row["mes"]), int(row["quincena"]), float(row["inpc"])

def factor_inpc_excel(anio_sal: int, inpc_ref: float) -> float:
    """factor = INPC_ref / INPC_2Q_jun_{anio_sal}  (lógica exacta del Excel)."""
    return inpc_ref / inpc_quincena(anio_sal, 6, 2)

# ─── UDIs ─────────────────────────────────────────────────────────────────────
_udi_cache: pd.DataFrame | None = None
_udi_source: str = ""

def cargar_udis(forzar: bool = False) -> pd.DataFrame:
    global _udi_cache, _udi_source
    if _udi_cache is not None and not forzar:
        return _udi_cache

    hoy = datetime.date.today()
    df  = _banxico_get("SP68257", "1995-04-04", hoy.isoformat())
    if df is not None and not df.empty:
        df = df.rename(columns={"dato":"udi"})[["fecha","udi"]]
        df["fecha"]  = df["fecha"].dt.date
        _udi_cache   = df.reset_index(drop=True)
        _udi_source  = "Banxico API"
        return _udi_cache

    df = pd.read_csv(_UDI_CSV, parse_dates=["fecha"])
    df["fecha"]  = df["fecha"].dt.date
    _udi_cache   = df.reset_index(drop=True)
    _udi_source  = "CSV local"
    return _udi_cache

def fuente_udi() -> str:
    cargar_udis()
    return _udi_source

def udi_en_fecha(anio: int, mes: int, dia: int = 1) -> float:
    df     = cargar_udis()
    target = datetime.date(anio, mes, dia)
    sub    = df[df["fecha"] <= target]
    if sub.empty:
        raise ValueError(f"Sin UDI antes de {target}")
    return float(sub.iloc[-1]["udi"])

def ultima_udi() -> tuple[datetime.date, float]:
    df  = cargar_udis()
    row = df.iloc[-1]
    return row["fecha"], float(row["udi"])

# ─── FACBI ────────────────────────────────────────────────────────────────────
def calcular_facbi(mes_pension: int, anio_pension: int) -> float:
    """
    FACBI = UDI(último día mes anterior) / UDI(último día dos meses antes)
    Para feb-2026: UDI(31-ene-2026) / UDI(31-dic-2025)
    """
    import calendar
    anio_ant = anio_pension if mes_pension > 1 else anio_pension - 1
    mes_ant  = mes_pension - 1 if mes_pension > 1 else 12
    udi_num  = udi_en_fecha(anio_ant, mes_ant, calendar.monthrange(anio_ant, mes_ant)[1])
    anio_ant2 = anio_ant if mes_ant > 1 else anio_ant - 1
    mes_ant2  = mes_ant - 1 if mes_ant > 1 else 12
    udi_den   = udi_en_fecha(anio_ant2, mes_ant2, calendar.monthrange(anio_ant2, mes_ant2)[1])
    return udi_num / udi_den

# ─── PMG ──────────────────────────────────────────────────────────────────────
_pmg_cache: pd.DataFrame | None = None

def pmg_vigente(anio: int | None = None) -> float:
    global _pmg_cache
    if _pmg_cache is None:
        _pmg_cache = pd.read_csv(_PMG_CSV)
    df = _pmg_cache
    if anio is not None:
        row = df[df["anio"] == anio]
        if not row.empty:
            return float(row.iloc[0]["pmg_actualizado"])
    return float(df.iloc[-1]["pmg_actualizado"])
