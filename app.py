"""Tablero agro: riesgo de plagas, riego y logística con un solo flujo de datos.

Ejecutar con:  py -m streamlit run app.py
"""

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Tablero agro", layout="wide")
st.title("Tablero agro: plagas, agua y logística")
st.caption(
    "Prototipo de hackathon. Las lecturas de sensores son SIMULADAS; "
    "los umbrales son de ejemplo y deben ajustarse con una fuente para el cultivo."
)

# ---------------------------------------------------------------------------
# 1. PARÁMETROS (barra lateral): todo lo ajustable vive aquí
# ---------------------------------------------------------------------------
st.sidebar.header("Parámetros")
num_parcelas = st.sidebar.slider("Número de parcelas", 2, 8, 4)

st.sidebar.subheader("Riesgo de plaga o enfermedad")
umbral_humedad = st.sidebar.slider("Humedad del aire de riesgo (%)", 60, 95, 85)
temp_min, temp_max = st.sidebar.slider("Rango de temperatura de riesgo (°C)", 5, 40, (18, 28))

st.sidebar.subheader("Riego")
humedad_objetivo = st.sidebar.slider("Humedad de suelo objetivo (%)", 30, 80, 60)
litros_por_punto = st.sidebar.number_input("Litros por punto de humedad faltante", 1, 500, 50)
riego_fijo = st.sidebar.number_input("Riego fijo actual (litros por parcela al día)", 0, 5000, 1200)


# ---------------------------------------------------------------------------
# 2. DATOS: simulador de sensores (sustituir por datos reales cuando existan)
# ---------------------------------------------------------------------------
@st.cache_data
def generar_lecturas(parcelas: int, horas: int = 72, semilla: int = 7) -> pd.DataFrame:
    """Crea una lectura por hora y por parcela: temperatura, humedad del aire y del suelo."""
    rng = np.random.default_rng(semilla)
    momentos = pd.date_range(end=pd.Timestamp.now().floor("h"), periods=horas, freq="h")
    filas = []
    for p in range(1, parcelas + 1):
        ciclo_dia = np.sin((momentos.hour.to_numpy() - 6) / 24 * 2 * np.pi)  # calor de día, fresco de noche
        temperatura = 24 + 5 * ciclo_dia + rng.normal(0, 1, horas)
        humedad_aire = 64 - 15 * ciclo_dia + rng.normal(0, 4, horas) + p * 6
        humedad_suelo = 65 - p * 5 + rng.normal(0, 2, horas) - np.linspace(0, 8, horas)
        filas.append(
            pd.DataFrame(
                {
                    "momento": momentos,
                    "parcela": f"Parcela {p}",
                    "temperatura": temperatura.round(1),
                    "humedad_aire": humedad_aire.clip(0, 100).round(1),
                    "humedad_suelo": humedad_suelo.clip(0, 100).round(1),
                }
            )
        )
    return pd.concat(filas, ignore_index=True)


lecturas = generar_lecturas(num_parcelas)


# ---------------------------------------------------------------------------
# 3. LÓGICA: tres cálculos sobre los mismos datos
# ---------------------------------------------------------------------------
def calcular_riesgo(df: pd.DataFrame) -> pd.DataFrame:
    """Riesgo = % de las últimas 48 horas con humedad alta y temperatura favorable."""
    recientes = df[df["momento"] >= df["momento"].max() - pd.Timedelta(hours=48)].copy()
    recientes["condicion"] = (recientes["humedad_aire"] >= umbral_humedad) & recientes[
        "temperatura"
    ].between(temp_min, temp_max)
    riesgo = (recientes.groupby("parcela")["condicion"].mean() * 100).round(0)
    tabla = riesgo.rename("riesgo_pct").reset_index()
    tabla["nivel"] = pd.cut(
        tabla["riesgo_pct"], bins=[-1, 20, 50, 100], labels=["Bajo", "Medio", "Alto"]
    ).astype(str)
    return tabla


def calcular_riego(df: pd.DataFrame) -> pd.DataFrame:
    """Litros recomendados = lo que falta para llegar a la humedad objetivo."""
    ultima = df.sort_values("momento").groupby("parcela").tail(1)[["parcela", "humedad_suelo"]]
    ultima = ultima.sort_values("parcela")
    ultima["litros_recomendados"] = (
        (humedad_objetivo - ultima["humedad_suelo"]).clip(lower=0) * litros_por_punto
    ).round(0)
    ultima["ahorro_litros"] = riego_fijo - ultima["litros_recomendados"]
    return ultima.reset_index(drop=True)


def calcular_logistica(lotes: pd.DataFrame, riesgo: pd.DataFrame) -> pd.DataFrame:
    """Margen = días de vida útil que le quedan al fruto al llegar a su destino."""
    tabla = lotes.merge(riesgo[["parcela", "nivel"]], on="parcela", how="left")
    tabla["margen_dias"] = tabla["vida_util_dias"] - tabla["traslado_dias"]
    return tabla.sort_values("margen_dias").reset_index(drop=True)


riesgo = calcular_riesgo(lecturas)
riego = calcular_riego(lecturas)

# ---------------------------------------------------------------------------
# 4. INTERFAZ: una pestaña por problemática del reto
# ---------------------------------------------------------------------------
tab_riesgo, tab_riego, tab_logistica = st.tabs(["Plagas y enfermedades", "Agua", "Logística"])

with tab_riesgo:
    st.subheader("Riesgo en las últimas 48 horas")
    columnas = st.columns(len(riesgo))
    for columna, fila in zip(columnas, riesgo.itertuples()):
        columna.metric(fila.parcela, f"{fila.riesgo_pct:.0f} %", fila.nivel, delta_color="off")
    for fila in riesgo[riesgo["nivel"] == "Alto"].itertuples():
        st.warning(f"{fila.parcela}: riesgo alto. Revisar la parcela hoy y aplicar medidas preventivas.")
    st.line_chart(lecturas.pivot(index="momento", columns="parcela", values="humedad_aire"))
    st.caption("Humedad del aire por parcela (%).")

with tab_riego:
    st.subheader("Riego recomendado para hoy")
    st.dataframe(riego, hide_index=True, use_container_width=True)
    ahorro_total = riego["ahorro_litros"].sum()
    st.metric("Ahorro frente al riego fijo (litros hoy)", f"{ahorro_total:,.0f}")
    st.caption("Cálculo: (humedad objetivo − humedad actual) × litros por punto.")

with tab_logistica:
    st.subheader("Orden de salida de los lotes")
    lotes_ejemplo = pd.DataFrame(
        {
            "lote": ["L-01", "L-02", "L-03", "L-04"],
            "parcela": ["Parcela 1", "Parcela 2", "Parcela 1", "Parcela 2"],
            "kg": [800, 500, 650, 900],
            "vida_util_dias": [5, 3, 7, 2],
            "destino": ["Mercado local", "Central de abasto", "Supermercado", "Central de abasto"],
            "traslado_dias": [1, 2, 1, 2],
        }
    )
    st.caption("Lotes de ejemplo. Edita la tabla para probar otros casos.")
    lotes = st.data_editor(lotes_ejemplo, num_rows="dynamic", hide_index=True)
    plan = calcular_logistica(lotes, riesgo)
    st.dataframe(plan, hide_index=True, use_container_width=True)
    for fila in plan[plan["margen_dias"] <= 0].itertuples():
        st.error(f"{fila.lote}: no llega en buen estado a {fila.destino}. Cambiar destino o adelantar salida.")
