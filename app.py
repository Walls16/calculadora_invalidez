"""
app.py  –  Calculadora de Montos Constitutivos de Invalidez
           Seguridad Social y Pensiones · LSS · Nota Técnica CNSF

Cómo ejecutar:
    streamlit run app.py

Token Banxico (opcional, para datos en tiempo real):
    Obtén el tuyo gratis en https://www.banxico.org.mx/SieAPIRest/service/v1/token
    y pégalo en el panel lateral, o bien guárdalo en ~/.banxico_token
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
import pandas as pd
from datetime import date

# ─── Importaciones del core ───────────────────────────────────────────────────
from core.calculadora import calcular_montos, ParametrosInvalido
from core.datos_externos import (
    ultima_udi, inpc_ultima_quincena, pmg_vigente,
    set_token, probar_token, token_activo,
    fuente_udi, fuente_inpc, cargar_udis, cargar_inpc,
)
from core.pension import evaluar_elegibilidad

# ─── Configuración ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="MC Invalidez – LSS",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.stMetric          { background:#f0f4f8; border-radius:8px; padding:10px 14px; }
.warn-box          { background:#fff3cd; border-left:4px solid #ffc107;
                     padding:12px 16px; border-radius:6px; margin:8px 0; }
.error-box         { background:#f8d7da; border-left:4px solid #dc3545;
                     padding:12px 16px; border-radius:6px; margin:8px 0; }
.ok-box            { background:#d1e7dd; border-left:4px solid #198754;
                     padding:12px 16px; border-radius:6px; margin:8px 0; }
.section-hdr       { font-size:1rem; font-weight:700; color:#1f4e79;
                     border-bottom:2px solid #1f4e79; padding-bottom:3px; margin:14px 0 8px; }
.fuente-tag        { font-size:0.72rem; color:#888; font-style:italic; }
</style>
""", unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ═════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.title("Parámetros")

    # ── Token Banxico ─────────────────────────────────────────────────────────
    with st.expander("Token Banxico (tiempo real)", expanded=not token_activo()):
        st.caption(
            "Con token los datos se actualizan desde la API de Banxico. "
            "Sin token se usan los CSVs locales (hasta feb 2026). "
            "[Obtener token gratuito →](https://www.banxico.org.mx/SieAPIRest/service/v1/token)"
        )
        token_input = st.text_input(
            "Token", type="password",
            placeholder="Pega aquí tu token de Banxico",
            value=""
        )
        col_t1, col_t2 = st.columns(2)
        if col_t1.button("Verificar", use_container_width=True):
            if token_input.strip():
                ok, msg = probar_token(token_input.strip())
                if ok:
                    st.success(msg)
                    st.session_state["banxico_token_ok"] = True
                else:
                    st.error(msg)
                    st.session_state["banxico_token_ok"] = False
            else:
                st.warning("Ingresa un token primero.")
        if col_t2.button("Limpiar", use_container_width=True):
            set_token("")
            st.session_state["banxico_token_ok"] = False
            st.rerun()

        if token_input.strip() and not st.session_state.get("banxico_token_ok"):
            set_token(token_input.strip())

    # ── Datos en vivo ─────────────────────────────────────────────────────────
    with st.expander("Datos actuales", expanded=True):
        try:
            fecha_udi, val_udi = ultima_udi()
            f_udi = fuente_udi()
            st.metric("UDI", f"{val_udi:.6f}", f"al {fecha_udi}")
            st.markdown(f'<span class="fuente-tag">Fuente: {f_udi}</span>',
                        unsafe_allow_html=True)
        except Exception as e:
            st.warning(f"UDI no disponible: {e}")

        try:
            ay, am, aq, ainpc = inpc_ultima_quincena()
            f_inpc = fuente_inpc()
            st.metric("INPC", f"{ainpc:.3f}", f"{ay}/{am:02d} Q{aq}")
            st.markdown(f'<span class="fuente-tag">Fuente: {f_inpc}</span>',
                        unsafe_allow_html=True)
        except Exception as e:
            st.warning(f"INPC no disponible: {e}")

        try:
            pmg_val = pmg_vigente()
            st.metric("PMG vigente", f"${pmg_val:,.2f}")
        except Exception as e:
            st.warning(f"PMG no disponible: {e}")

    st.divider()

    # ── Inválido ──────────────────────────────────────────────────────────────
    st.markdown('<div class="section-hdr">Inválido</div>', unsafe_allow_html=True)
    edad_x  = st.number_input("Edad (años cumplidos)", 25, 64, 49, 1)
    sexo_x  = st.radio("Sexo", ["H", "M"], horizontal=True)
    semanas = st.number_input("Semanas cotizadas al IMSS", 0, 2500, 557, 1)
    riesgo_trabajo = st.checkbox(
        "Invalidez por riesgo de trabajo",
        help="Si la invalidez deriva de un accidente o enfermedad de trabajo, "
             "el requisito de semanas mínimas no aplica (Art. 122 LSS)."
    )

    # Grado de invalidez
    st.markdown('<div class="section-hdr">Grado de invalidez</div>', unsafe_allow_html=True)
    grado_pct = st.slider(
        "Porcentaje de invalidez dictaminado",
        min_value=0, max_value=100, value=75, step=1,
        help="Porcentaje dictaminado por los médicos del IMSS. "
             "Mínimo 50% para pensión definitiva (Art. 119 LSS)."
    )
    grado_invalidez = grado_pct / 100.0

    # Indicador visual del grado
    if grado_pct < 25:
        st.error(f"{grado_pct}% — Sin prestación (< 25%)")
    elif grado_pct < 50:
        st.warning(f"{grado_pct}% — Subsidio temporal (25–49%)")
    elif grado_pct < 75:
        st.info(f"{grado_pct}% — Pensión definitiva (50–74%)")
    else:
        st.success(f"✓ {grado_pct}% — Invalidez total (≥ 75%)")

    st.divider()

    # ── Fecha de pensión ──────────────────────────────────────────────────────
    st.markdown('<div class="section-hdr">Fecha de pensión</div>', unsafe_allow_html=True)
    hoy   = date.today()
    mes_p = st.selectbox("Mes", list(range(1,13)), index=1,
                         format_func=lambda m: ["Ene","Feb","Mar","Abr","May","Jun",
                                                 "Jul","Ago","Sep","Oct","Nov","Dic"][m-1])
    anio_p = st.number_input("Año", 2020, 2040, hoy.year, 1)

    st.divider()

    # ── Composición familiar ──────────────────────────────────────────────────
    st.markdown('<div class="section-hdr">Composición familiar</div>', unsafe_allow_html=True)
    caso_sel = st.selectbox("Tipo de caso", [
        "MC1 – Inválido solo",
        "MC4 – Con cónyuge, sin hijos",
        "MC10 – Con cónyuge e hijos",
        "MC16 – Con ascendientes",
    ])

    aa_pct = st.slider("Ayuda asistencial (%)", 0.0, 20.0, 16.0, 0.5,
                       help="Porcentaje de ayuda asistencial (máx. 20%)") / 100

    conyugue_cfg = {}
    hijos_cfg    = []
    ascend_cfg   = []

    if "MC4" in caso_sel or "MC10" in caso_sel:
        with st.expander("Datos del cónyuge", expanded=True):
            edad_y = st.number_input("Edad cónyuge", 18, 80, 47, 1)
            sexo_y = st.radio("Sexo cónyuge", ["M","H"], horizontal=True)
            conyugue_cfg = {"edad": edad_y, "sexo": sexo_y}

    if "MC10" in caso_sel:
        with st.expander("Hijos con derecho a pensión (≤ 25 años)", expanded=True):
            n_hijos = st.number_input("Número de hijos", 1, 10, 3, 1)
            defaults_e = [13, 19, 21, 5, 8, 3, 16, 22, 7, 11]
            for i in range(n_hijos):
                c1, c2 = st.columns(2)
                e_h = c1.number_input(f"Edad hijo {i+1}", 0, 24,
                                       defaults_e[i] if i < len(defaults_e) else 5,
                                       1, key=f"eh{i}")
                s_h = c2.radio(f"Sexo {i+1}", ["M","H"], horizontal=True, key=f"sh{i}")
                hijos_cfg.append({"edad": int(e_h), "sexo": s_h})

    if "MC16" in caso_sel:
        with st.expander("Ascendientes", expanded=True):
            n_asc = st.number_input("Número de ascendientes", 1, 2, 1, 1)
            for i in range(n_asc):
                c1, c2 = st.columns(2)
                e_a = c1.number_input(f"Edad asc. {i+1}", 40, 95,
                                       [69, 72][i] if i < 2 else 70, 1, key=f"ea{i}")
                s_a = c2.radio(f"Sexo asc. {i+1}", ["M","H"], horizontal=True, key=f"sa{i}")
                ascend_cfg.append({"edad": int(e_a), "sexo": s_a})

    st.divider()

    # ── Historia salarial ─────────────────────────────────────────────────────
    st.markdown('<div class="section-hdr">Historia salarial</div>', unsafe_allow_html=True)

    modo_salario = st.radio(
        "Modo de captura",
        ["Promedio directo", "Detallado por año"],
        horizontal=True,
        help=(
            "**Promedio directo**: ingresa el salario diario integrado promedio "
            "de las últimas ~500 semanas ya calculado externamente. "
            "No se aplicarán factores INPC adicionales.\n\n"
            "**Detallado por año**: captura el salario por cada año para que la "
            "calculadora aplique los factores INPC automáticamente."
        ),
    )

    default_hist = {2024:2369, 2023:2300, 2022:2172, 2021:2031, 2020:1014,
                    2019:472,  2018:413,  2016:200,  2015:1619, 2014:1558}

    if modo_salario == "Promedio directo":
        st.caption(
            "Ingresa el salario diario integrado promedio de las últimas ~500 semanas "
            "(ya actualizado a pesos de hoy). "
            "**No se aplicarán factores INPC adicionales.**"
        )
        sal_promedio_directo = st.number_input(
            "Salario diario promedio ($)",
            min_value=0.01,
            value=1_500.00,
            step=0.01,
            format="%.2f",
        )
        # Synthetic single-row DataFrame so downstream code is unchanged
        hist_edit = pd.DataFrame(
            [(hoy.year, sal_promedio_directo)],
            columns=["Año", "Salario diario ($)"],
        )
        modo_directo = True
        st.info(f"Salario promedio ingresado: **${sal_promedio_directo:,.2f}** / día")
    else:
        st.caption("Salario diario promedio por año (últimas ≈500 semanas / 10 años)")
        hist_df = pd.DataFrame(
            [(a, s) for a, s in sorted(default_hist.items(), reverse=True)],
            columns=["Año", "Salario diario ($)"]
        )
        hist_edit = st.data_editor(
            hist_df, num_rows="dynamic",
            column_config={
                "Año": st.column_config.NumberColumn("Año", min_value=1990,
                                                      max_value=hoy.year, step=1),
                "Salario diario ($)": st.column_config.NumberColumn(
                    "Salario diario ($)", min_value=0.0, format="$%.2f"),
            },
            hide_index=True, use_container_width=True,
        )
        modo_directo = False
        sal_promedio_directo = None

    calcular_btn = st.button("Calcular", type="primary", use_container_width=True)


# ═════════════════════════════════════════════════════════════════════════════
#  ÁREA PRINCIPAL
# ═════════════════════════════════════════════════════════════════════════════
st.title("Calculadora de Montos Constitutivos · Invalidez")
st.caption("Seguro de Invalidez (MCSI) y Sobrevivencia (MCSS) · LSS · Nota Técnica CNSF")

if not calcular_btn:
    st.info("Configure los parámetros en el panel izquierdo y presione **Calcular**.")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("""
**Casos cubiertos**
- **MC1** – Inválido solo (sin dependientes)
- **MC4** – Inválido con cónyuge
- **MC10** – Inválido con cónyuge e hijos
- **MC16** – Inválido con ascendientes

**Requisitos LSS (Art. 119-122)**
- Grado de invalidez ≥ 50% para pensión definitiva
- Grado 25–49%: subsidio temporal (no aplica MC)
- Mínimo **150 semanas** cotizadas (salvo riesgo de trabajo)
        """)
    with col2:
        st.markdown("""
**Hipótesis actuariales**
- Tasa técnica: **3.5%** anual
- Tablas mortalidad: EMSSAH/EMSSAM-15 (CNSF)
- Invalidez: Val Act 2020 (CNSF)
- Deserción escolar: DOF 19-dic-2009 · S-22.2

**Datos en tiempo real** *(con token Banxico)*
- UDIs — serie SP68257
- INPC quincenal — serie SP1
- *Sin token: CSV local hasta feb 2026*
        """)
    st.stop()


# ─── Validación de elegibilidad ───────────────────────────────────────────────
elegib = evaluar_elegibilidad(
    semanas=int(semanas),
    grado_invalidez=grado_invalidez,
    por_riesgo_trabajo=riesgo_trabajo,
)

# Mostrar advertencias antes que cualquier otra cosa
if elegib["advertencias"]:
    for adv in elegib["advertencias"]:
        if adv.startswith("⛔"):
            st.markdown(f'<div class="error-box">{adv}</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="warn-box">{adv}</div>', unsafe_allow_html=True)

if elegib["bloquear_calculo"]:
    st.error("**El cálculo del Monto Constitutivo no procede** con los datos ingresados. "
             "Revisa las condiciones indicadas arriba.")
    st.stop()

# ─── Preparar historia salarial ───────────────────────────────────────────────
if modo_directo:
    # Use the direct average as-is; no INPC re-actualisation
    if sal_promedio_directo <= 0:
        st.error("El salario promedio debe ser mayor a cero.")
        st.stop()
    historia_sal = {hoy.year: sal_promedio_directo}
    _override_sal_prom = sal_promedio_directo
else:
    historia_sal = {
        int(row["Año"]): float(row["Salario diario ($)"])
        for _, row in hist_edit.iterrows()
        if pd.notna(row["Año"]) and pd.notna(row["Salario diario ($)"])
           and float(row["Salario diario ($)"]) > 0
    }
    if not historia_sal:
        st.error("La historia salarial está vacía. Ingresa al menos un año.")
        st.stop()
    _override_sal_prom = None

# ─── Preparar parámetros ──────────────────────────────────────────────────────
tiene_conyugue = bool(conyugue_cfg)
params = ParametrosInvalido(
    edad_x=int(edad_x), sexo_x=sexo_x,
    semanas_cotizadas=int(semanas),
    historia_salarial=historia_sal,
    tiene_conyugue=tiene_conyugue,
    edad_y=conyugue_cfg.get("edad"),
    sexo_y=conyugue_cfg.get("sexo"),
    hijos=hijos_cfg,
    ascendientes=ascend_cfg,
    aa_pct=aa_pct,
    mes_pension=int(mes_p),
    anio_pension=int(anio_p),
)

# Inject pre-computed average if available (requires sal_prom_override field in core)
if _override_sal_prom is not None and hasattr(params, "sal_prom_override"):
    params.sal_prom_override = _override_sal_prom

# ─── Cálculo ──────────────────────────────────────────────────────────────────
try:
    with st.spinner("Calculando montos constitutivos..."):
        resultado = calcular_montos(params)
except Exception as e:
    st.error(f"Error en el cálculo: {e}")
    import traceback
    st.code(traceback.format_exc())
    st.stop()

# ─── Indicador de OK ──────────────────────────────────────────────────────────
st.markdown(
    f'<div class="ok-box">Cálculo completado · {elegib["tipo_prestacion"].upper()} · '
    f'Invalidez {grado_pct}% · {int(semanas)} semanas cotizadas</div>',
    unsafe_allow_html=True
)

# ─── KPIs principales ─────────────────────────────────────────────────────────
st.subheader(f"Resultados — {resultado.get('caso', '')}")

mcsi_val = (resultado.get("MCSI") or {}).get("MCSI", 0)
mcss_val = (resultado.get("MCSS") or {}).get("MCSS", 0)
total    =  resultado.get("MC_TOTAL", 0)

c1, c2, c3, c4 = st.columns(4)
c1.metric("MCSI",      f"${mcsi_val:>14,.2f}")
c2.metric("MCSS",      f"${mcss_val:>14,.2f}" if mcss_val else "—")
c3.metric("MC Total", f"${total:>14,.2f}")
c4.metric("Tipo",       elegib["tipo_prestacion"].title())

st.divider()

# ─── Desglose lado a lado ─────────────────────────────────────────────────────
col_a, col_b = st.columns(2)

with col_a:
    st.markdown("#### Parámetros clave")
    rows = [
        ("Salario prom. 500 semanas", f"${resultado.get('sal_prom_500',0):,.4f}"),
        ("PMG vigente",               f"${resultado.get('PMG',0):,.2f}"),
        ("FACBI",                     f"{resultado.get('FACBI',0):.8f}"),
        ("b₁ mensual (inválido)",     f"${resultado.get('b1_mensual',0):,.2f}"),
        ("b₂ mensual (viuda/asc.)",   f"${resultado.get('b2_viuda_mens',0):,.2f}"),
        ("Modo salarial",             "Promedio directo" if modo_directo else "Detallado por año"),
        ("Fuente UDI",                fuente_udi()),
        ("Fuente INPC",               fuente_inpc()),
    ]
    for label, val in rows:
        r1, r2 = st.columns([3, 2])
        r1.markdown(f"<small>{label}</small>", unsafe_allow_html=True)
        r2.markdown(f"<small><b>{val}</b></small>", unsafe_allow_html=True)

with col_b:
    st.markdown("#### Desglose MCSI")
    mcsi_d = resultado.get("MCSI") or {}
    lbl_map = {
        "ax12"  : "ä⁽¹²⁾_x",
        "suma_U": "Σ U[k]",
        "PBSI"  : "PBSI",
        "PNSI"  : "PNSI",
        "MCSI"  : "**MCSI**",
    }
    for k, v in mcsi_d.items():
        if isinstance(v, float):
            label = lbl_map.get(k, k)
            r1, r2 = st.columns([2, 2])
            r1.markdown(f"<small>{label}</small>", unsafe_allow_html=True)
            r2.markdown(f"<small><b>${v:,.4f}</b></small>", unsafe_allow_html=True)

if resultado.get("MCSS"):
    st.markdown("#### Desglose MCSS")
    mcss_d = resultado["MCSS"]
    lbl_map_s = {"suma_W":"Σ W[k]","suma_CE":"Σ CE[k]","PBSS":"PBSS","PNSS":"PNSS","MCSS":"**MCSS**","PFH":"PFH (finiquito)"}
    cols_m = st.columns(min(len([v for v in mcss_d.values() if isinstance(v, float)]), 5))
    i = 0
    for k, v in mcss_d.items():
        if isinstance(v, float) and i < len(cols_m):
            cols_m[i].metric(lbl_map_s.get(k, k), f"${v:,.2f}")
            i += 1

st.divider()

# ─── Historia salarial actualizada ────────────────────────────────────────────
with st.expander("Historia salarial con factores INPC"):
    if modo_directo:
        st.info(
            f"Modo **Promedio directo** — salario ingresado: "
            f"**${sal_promedio_directo:,.2f} / día**. "
            "No se aplicaron factores INPC."
        )
    else:
        hist_act = resultado.get("historia_actualizada", {})
        if hist_act:
            rows_h = []
            for a in sorted(hist_act.keys(), reverse=True):
                sal_orig = historia_sal.get(a, 0)
                sal_act  = hist_act[a]
                factor   = sal_act / sal_orig if sal_orig > 0 else 0
                rows_h.append({"Año": a, "Salario original ($)": sal_orig,
                               "Factor INPC": factor, "Salario actualizado ($)": sal_act})
            df_h = pd.DataFrame(rows_h)
            st.dataframe(
                df_h.style.format({
                    "Salario original ($)":    "${:,.2f}",
                    "Factor INPC":             "{:.6f}",
                    "Salario actualizado ($)": "${:,.2f}",
                }),
                use_container_width=True, hide_index=True
            )

# ─── Nota metodológica ────────────────────────────────────────────────────────
with st.expander("Nota metodológica"):
    st.markdown(f"""
**Fuente de datos actuales:**
- UDIs: {fuente_udi()} — serie SP68257
- INPC: {fuente_inpc()} — serie SP1

**Modo salarial utilizado:** {"Promedio directo (sin re-actualización INPC)" if modo_directo else "Detallado por año (con factores INPC)"}

**Fórmulas aplicadas:**
- Factor INPC: `INPC_última_quincena / INPC_2Q_jun_(año_salario)`
- FACBI: `UDI(último día mes anterior) / UDI(último día dos meses antes)`
- MC1: `b₁ × 12 × ä⁽¹²⁾_x × (1+INC) × FACBI × (1+a+b)`
- MC4/MC10/MC16: `α × Σ U[k] × FACBI × (1+a+b)` donde `α = (1-(1+i)⁻¹)/(1-(1+i)⁻¹ᐟ¹²)`
- MCSS: `b₂ × 13 × Σ W[k] × FACBI × (1+a)` ó `(13/12) × α × Σ CE[k] × FACBI × (1+a)`

**Parámetros fijos (Nota Técnica CNSF · S-22.2):**
- Tasa técnica: i = 3.5%  |  INC = 11%  |  a = 2%  |  b = 1%
- CBIV = 35% del salario promedio
- AF cónyuge = 15%  |  AF hijo = 10%  |  AF ascendiente = 10%
- Mínimo pensión: PMG (Art. 170 LSS), actualizada por INPC cada año

**Requisitos de elegibilidad (LSS):**
- Art. 119: Grado ≥ 50% para pensión definitiva; 25–49% subsidio temporal
- Art. 122: Mínimo 150 semanas cotizadas (salvo riesgo de trabajo)
    """)

st.caption("Cálculos con fines académicos — Seguridad Social y Pensiones, UDLAP")