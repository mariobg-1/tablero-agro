"""Tablero agro para jitomate en la zona de riego del oriente de Morelos.

Tres pestañas, un solo flujo de datos:
  1. Riesgo de tizón tardío a partir de humedad y temperatura.
  2. Riego recomendado según la humedad del suelo.
  3. Rutas de recolección compartida (cálculo adaptado del demo "Ruta fresca").

Ejecutar con:  py -m streamlit run app.py
"""

import math
import re
from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Tablero agro jitomate", layout="wide")
st.title("Tablero agro: tizón tardío, agua y logística del jitomate")
st.caption(
    "Prototipo de hackathon para Cuautla, Ayala y Tepalcingo, Morelos. Las lecturas de sensores y las "
    "cargas son SIMULADAS; costos, velocidades, vida útil y litros son supuestos por validar."
)

ZONAS = ["Cuautla", "Ayala", "Tepalcingo"]

# ---------------------------------------------------------------------------
# 1. PARÁMETROS (barra lateral): todo lo ajustable vive aquí
# ---------------------------------------------------------------------------
st.sidebar.header("Parámetros")

st.sidebar.subheader("Riesgo de tizón tardío")
umbral_humedad = st.sidebar.slider("Humedad relativa de riesgo (%)", 50, 95, 70)
temp_max_riesgo = st.sidebar.slider("Temperatura máxima de riesgo (°C)", 10, 30, 22)
ventana_horas = st.sidebar.radio("Ventana de cálculo", [48, 168], format_func=lambda h: f"{h} horas", index=1)

st.sidebar.subheader("Riego")
humedad_objetivo = st.sidebar.slider("Humedad de suelo objetivo (%)", 30, 80, 60)
litros_por_punto = st.sidebar.number_input("Litros por punto de humedad faltante", 1, 500, 50)
riego_fijo = st.sidebar.number_input("Riego fijo actual (litros por zona al día)", 0, 5000, 1200)

with st.sidebar.expander("Logística: supuestos del cálculo"):
    factor_camino = st.number_input("Factor de camino sobre línea recta", 1.0, 3.0, 1.35, 0.05)
    vel_local = st.number_input("Velocidad entre parcelas (km/h)", 5, 90, 30)
    vel_carretera = st.number_input("Velocidad a CDMX (km/h)", 5, 120, 55)
    min_carga = st.number_input("Minutos de carga por parada", 0, 60, 10)
    hora_salida = st.text_input("Salida de camiones (HH:MM)", "06:00")
    if not re.fullmatch(r"\d{1,2}:\d{2}", hora_salida.strip()):
        st.warning("Hora de salida no válida; se usa 06:00.")
    cap_c1 = st.number_input("Capacidad C1 (kg)", 100, 20000, 3500, 100)
    cap_c2 = st.number_input("Capacidad C2 (kg)", 100, 20000, 1500, 100)
    costo_c1 = st.number_input("Costo C1 por km (MXN)", 1, 100, 18)
    costo_c2 = st.number_input("Costo C2 por km (MXN)", 1, 100, 12)
    cap_camioneta = st.number_input("Capacidad de camioneta individual (kg)", 50, 5000, 1000, 50)
    costo_camioneta = st.number_input("Costo de camioneta por km (MXN)", 1, 100, 9)
    horas_espera = st.number_input("Horas de espera del flete individual", 0.0, 12.0, 3.0, 0.5)

# ---------------------------------------------------------------------------
# 2. DATOS: sensores simulados y cargas de ejemplo (sustituir por datos reales)
# ---------------------------------------------------------------------------
SALIDA = {"nombre": "Salida Cuautla", "lat": 18.8106, "lng": -98.9544}
CENTRAL = {"nombre": "Central de Abasto CDMX", "lat": 19.3745, "lng": -99.093, "hora_limite": "14:00"}

# Cargas ficticias: (id, nombre, municipio, lat, lng, kg, hora en que la carga está lista, vida útil en horas)
# Coordenadas aproximadas alrededor de las cabeceras municipales.
CARGAS_BASE = [
    ("P01", "Productor A", "Cuautla", 18.8231, -98.9612, 400, "06:00", 72),
    ("P02", "Productor B", "Cuautla", 18.8010, -98.9401, 350, "06:00", 120),
    ("P03", "Productor C", "Cuautla", 18.8302, -98.9455, 300, "06:30", 72),
    ("P04", "Productor D", "Cuautla", 18.7955, -98.9680, 500, "07:00", 72),
    ("P05", "Productor E", "Cuautla", 18.8150, -98.9320, 350, "06:00", 168),
    ("P06", "Productor F", "Ayala", 18.7702, -98.9790, 200, "06:00", 120),
    ("P07", "Productor G", "Ayala", 18.7580, -98.9915, 200, "07:00", 72),
    ("P08", "Productor H", "Ayala", 18.7455, -98.9702, 400, "06:30", 72),
    ("P09", "Productor I", "Ayala", 18.7790, -99.0010, 250, "06:00", 168),
    ("P10", "Productor J", "Tepalcingo", 18.6010, -98.8502, 350, "06:30", 120),
    ("P11", "Productor K", "Tepalcingo", 18.5890, -98.8380, 250, "07:00", 168),
    ("P12", "Productor L", "Tepalcingo", 18.6105, -98.8610, 200, "06:30", 120),
]
CARGAS_EXTRA = [  # se suman en el escenario optimista
    ("P13", "Productor M", "Tepalcingo", 18.5802, -98.8521, 400, "06:30", 120),
    ("P14", "Productor N", "Tepalcingo", 18.6190, -98.8405, 350, "07:00", 72),
    ("P15", "Productor O", "Ayala", 18.7350, -98.9850, 300, "07:00", 168),
    ("P16", "Productor P", "Ayala", 18.7620, -98.9580, 450, "06:00", 120),
]
IDS_PESIMISTA = ["P01", "P03", "P05", "P08", "P10", "P12"]
COLUMNAS = ["id", "nombre", "municipio", "lat", "lng", "kg", "hora_corte", "vida_util_horas"]
NOTAS_ESCENARIO = {
    "Pesimista": "Solo 6 productores se suman y llueve: los caminos van 30 % más lento.",
    "Base": "Una organización con 12 productores y dos camiones.",
    "Optimista": "Se suman 4 productores más de Tepalcingo y Ayala.",
}


@st.cache_data
def generar_lecturas(horas: int = 168, semilla: int = 7) -> pd.DataFrame:
    """Crea una lectura por hora y por zona: temperatura, humedad del aire y del suelo."""
    rng = np.random.default_rng(semilla)
    momentos = pd.date_range(end=pd.Timestamp.now().floor("h"), periods=horas, freq="h")
    ciclo_dia = np.sin((momentos.hour.to_numpy() - 6) / 24 * 2 * np.pi)  # calor de día, fresco de noche
    # Diferencias inventadas entre zonas para que el demo muestre los tres niveles de riesgo
    extra_humedad = {"Cuautla": -14, "Ayala": -2, "Tepalcingo": 8}
    suelo_inicial = {"Cuautla": 52, "Ayala": 60, "Tepalcingo": 74}
    filas = []
    for zona in ZONAS:
        temperatura = 23 + 7 * ciclo_dia + rng.normal(0, 1, horas)
        humedad_aire = 66 - 16 * ciclo_dia + rng.normal(0, 4, horas) + extra_humedad[zona]
        humedad_suelo = suelo_inicial[zona] + rng.normal(0, 2, horas) - np.linspace(0, 8, horas)
        filas.append(
            pd.DataFrame(
                {
                    "momento": momentos,
                    "zona": zona,
                    "temperatura": temperatura.round(1),
                    "humedad_aire": humedad_aire.clip(0, 100).round(1),
                    "humedad_suelo": humedad_suelo.clip(0, 100).round(1),
                }
            )
        )
    return pd.concat(filas, ignore_index=True)


def cargas_del_escenario(escenario: str) -> pd.DataFrame:
    """Devuelve la tabla de cargas según el escenario elegido."""
    filas = list(CARGAS_BASE)
    if escenario == "Pesimista":
        filas = [f for f in filas if f[0] in IDS_PESIMISTA]
    if escenario == "Optimista":
        filas = filas + CARGAS_EXTRA
    return pd.DataFrame(filas, columns=COLUMNAS)


lecturas = generar_lecturas()


# ---------------------------------------------------------------------------
# 3. LÓGICA: riesgo, riego y rutas
# ---------------------------------------------------------------------------
def calcular_riesgo(df: pd.DataFrame) -> pd.DataFrame:
    """Riesgo = % de horas de la ventana con humedad alta y temperatura fresca (favorables al tizón tardío)."""
    recientes = df[df["momento"] > df["momento"].max() - pd.Timedelta(hours=ventana_horas)].copy()
    recientes["favorable"] = (recientes["humedad_aire"] >= umbral_humedad) & (
        recientes["temperatura"] <= temp_max_riesgo
    )
    resumen = recientes.groupby("zona")["favorable"].agg(horas_favorables="sum", riesgo_pct="mean")
    tabla = resumen.reindex(ZONAS).reset_index()
    tabla["riesgo_pct"] = (tabla["riesgo_pct"] * 100).round(0)
    tabla["nivel"] = pd.cut(tabla["riesgo_pct"], bins=[-1, 15, 35, 100], labels=["Bajo", "Medio", "Alto"]).astype(str)
    return tabla


def calcular_riego(df: pd.DataFrame) -> pd.DataFrame:
    """Litros recomendados = lo que falta para llegar a la humedad objetivo; nunca más que eso."""
    ultima = df.sort_values("momento").groupby("zona").tail(1)[["zona", "humedad_suelo"]]
    ultima["litros_recomendados"] = (
        (humedad_objetivo - ultima["humedad_suelo"]).clip(lower=0) * litros_por_punto
    ).round(0)
    ultima["ahorro_litros"] = riego_fijo - ultima["litros_recomendados"]
    return ultima.set_index("zona").reindex(ZONAS).reset_index()


def a_minutos(hora: str) -> int:
    """Convierte 'HH:MM' en minutos desde medianoche. Si la hora está mal escrita usa 06:00."""
    try:
        h, m = str(hora).strip().split(":")[:2]
        return int(h) * 60 + int(m)
    except ValueError:
        return 6 * 60


def a_hora(minutos: float) -> str:
    """Convierte minutos desde medianoche en 'HH:MM'."""
    minutos = round(minutos)
    return f"{(minutos // 60) % 24:02d}:{minutos % 60:02d}"


def distancia_km(a: dict, b: dict) -> float:
    """Distancia en línea recta (fórmula de haversine) por el factor de camino."""
    r = math.pi / 180
    d_lat, d_lng = (b["lat"] - a["lat"]) * r, (b["lng"] - a["lng"]) * r
    x = math.sin(d_lat / 2) ** 2 + math.cos(a["lat"] * r) * math.cos(b["lat"] * r) * math.sin(d_lng / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(x)) * factor_camino


def largo_de_ruta(paradas: list) -> float:
    """Kilómetros de salida -> paradas en orden -> Central."""
    total, actual = 0.0, SALIDA
    for p in paradas:
        total += distancia_km(actual, p)
        actual = p
    return total + distancia_km(actual, CENTRAL)


def ordenar_paradas(paradas: list) -> list:
    """Primero va siempre a la parada más cercana; luego prueba invertir tramos (2-opt) para acortar."""
    pendientes, actual, orden = list(paradas), SALIDA, []
    while pendientes:
        cercana = min(pendientes, key=lambda p: distancia_km(actual, p))
        pendientes.remove(cercana)
        orden.append(cercana)
        actual = cercana
    mejoro = True
    while mejoro:
        mejoro = False
        for i in range(len(orden) - 1):
            for j in range(i + 1, len(orden)):
                candidata = orden[:i] + orden[i : j + 1][::-1] + orden[j + 1 :]
                if largo_de_ruta(candidata) + 1e-9 < largo_de_ruta(orden):
                    orden, mejoro = candidata, True
    return orden


def planear_rutas(cargas: list, zonas_prioritarias: set, lento: float) -> dict:
    """Reparte las cargas en los camiones, ordena las paradas y calcula horarios y costos."""
    v_local, v_carretera = vel_local * lento, vel_carretera * lento
    camiones = [
        {"id": "C1", "cap": cap_c1, "costo": costo_c1, "cargas": [], "kg": 0},
        {"id": "C2", "cap": cap_c2, "costo": costo_c2, "cargas": [], "kg": 0},
    ]
    # Prioridad: zonas con alerta validada, luego menor vida útil, luego carga lista más temprano
    ordenadas = sorted(
        cargas,
        key=lambda c: (c["municipio"] not in zonas_prioritarias, c["vida_util_horas"], a_minutos(c["hora_corte"])),
    )
    sin_lugar = []
    for carga in ordenadas:
        camion = next((c for c in camiones if c["kg"] + carga["kg"] <= c["cap"]), None)
        if camion:
            camion["cargas"].append(carga)
            camion["kg"] += carga["kg"]
        else:
            sin_lugar.append(carga)

    paradas_totales = []
    for camion in camiones:
        camion["paradas"], camion["km"], camion["llegada"] = [], 0.0, None
        reloj, actual = a_minutos(hora_salida), SALIDA
        for parada in ordenar_paradas(camion["cargas"]):
            tramo = distancia_km(actual, parada)
            camion["km"] += tramo
            reloj = max(reloj + tramo / v_local * 60, a_minutos(parada["hora_corte"]))  # espera a la carga
            camion["paradas"].append({**parada, "recoleccion": reloj})
            reloj += min_carga
            actual = parada
        if camion["paradas"]:
            tramo = distancia_km(actual, CENTRAL)
            camion["llegada"] = reloj + tramo / v_carretera * 60
            camion["km"] += tramo + distancia_km(CENTRAL, SALIDA)  # incluye el regreso
            for p in camion["paradas"]:
                p["horas_transito"] = (camion["llegada"] - a_minutos(p["hora_corte"])) / 60
                p["vida_restante"] = p["vida_util_horas"] - p["horas_transito"]
                p["camion"] = camion["id"]
            paradas_totales += camion["paradas"]
        camion["costo_total"] = camion["km"] * camion["costo"]

    usados = [c for c in camiones if c["paradas"]]
    n = max(1, len(paradas_totales))
    kg = sum(p["kg"] for p in paradas_totales)
    compartida = {
        "viajes": len(usados),
        "km": sum(c["km"] for c in usados),
        "costo": sum(c["costo_total"] for c in usados),
        "ocupacion": kg / max(1, sum(c["cap"] for c in usados)),
        "horas": sum(p["horas_transito"] for p in paradas_totales) / n,
        "kg_riesgo": sum(p["kg"] for p in paradas_totales if p["vida_restante"] < p["vida_util_horas"] * 0.25),
    }
    # Comparación: cada productor contrata su propia camioneta, ida y vuelta
    km_ind, horas_ind, riesgo_ind = 0.0, 0.0, 0
    for p in paradas_totales:
        d = distancia_km(p, CENTRAL)
        horas = horas_espera + d / v_carretera
        km_ind += 2 * d
        horas_ind += horas
        if p["vida_util_horas"] - horas < p["vida_util_horas"] * 0.25:
            riesgo_ind += p["kg"]
    individual = {
        "viajes": len(paradas_totales),
        "km": km_ind,
        "costo": km_ind * costo_camioneta,
        "ocupacion": kg / max(1, len(paradas_totales) * cap_camioneta),
        "horas": horas_ind / n,
        "kg_riesgo": riesgo_ind,
    }
    return {"camiones": camiones, "sin_lugar": sin_lugar, "compartida": compartida, "individual": individual, "kg": kg}


def estado_frescura(parada: dict) -> str:
    """Clasifica la carga según la vida útil que le queda al llegar a la Central."""
    proporcion = parada["vida_restante"] / parada["vida_util_horas"]
    if proporcion >= 0.5:
        return "Llega fresco"
    if proporcion >= 0.25:
        return "Vender pronto"
    return "En riesgo"


riesgo = calcular_riesgo(lecturas)
riego = calcular_riego(lecturas)
zonas_en_riesgo_alto = list(riesgo.loc[riesgo["nivel"] == "Alto", "zona"])

# ---------------------------------------------------------------------------
# 4. INTERFAZ: una pestaña por problemática del reto
# ---------------------------------------------------------------------------
tab_riesgo, tab_riego, tab_logistica, tab_fuentes = st.tabs(
    ["Tizón tardío", "Agua", "Logística", "Fuentes y supuestos"]
)

zonas_validadas = set()
with tab_riesgo:
    st.subheader(f"Riesgo de tizón tardío en las últimas {ventana_horas} horas, por zona")
    columnas = st.columns(len(riesgo))
    for columna, fila in zip(columnas, riesgo.itertuples()):
        columna.metric(
            fila.zona, f"{fila.riesgo_pct:.0f} %", f"{fila.nivel} · {fila.horas_favorables} h favorables",
            delta_color="off",
        )
    st.caption(
        f"Hora favorable: humedad relativa de {umbral_humedad} % o más y temperatura de {temp_max_riesgo} °C o menos."
    )

    st.markdown("**Revisión humana antes de avisar**")
    if not zonas_en_riesgo_alto:
        st.info("Ninguna zona está en riesgo alto con los umbrales actuales.")
    for zona in zonas_en_riesgo_alto:
        st.warning(f"{zona}: riesgo alto. Requiere validación del técnico de sanidad vegetal.")
        if st.checkbox(f"El técnico validó la alerta de {zona} en campo", key=f"valida_{zona}"):
            zonas_validadas.add(zona)
            st.success(
                f"Aviso liberado para productores de {zona}: revisar hojas y tallos hoy, ventilar el cultivo "
                "y consultar al técnico antes de cualquier aplicación."
            )
        else:
            st.caption(f"Aviso de {zona} retenido: no se envía hasta que el técnico lo valide.")

    st.line_chart(lecturas.pivot(index="momento", columns="zona", values="humedad_aire"))
    st.caption("Humedad del aire por zona (%).")

    st.markdown("**Registro de revisión en parcela**")
    if "registros" not in st.session_state:
        st.session_state["registros"] = []
    with st.form("registro_revision", clear_on_submit=True):
        zona_registro = st.selectbox("Zona", ZONAS)
        con_sintomas = st.radio("¿Se encontraron síntomas?", ["No", "Sí"], horizontal=True)
        nota = st.text_input("Nota del productor o técnico")
        if st.form_submit_button("Guardar revisión"):
            st.session_state["registros"].append(
                {"fecha": date.today().isoformat(), "zona": zona_registro, "síntomas": con_sintomas, "nota": nota}
            )
    if st.session_state["registros"]:
        st.dataframe(pd.DataFrame(st.session_state["registros"]), hide_index=True)
    st.caption("Estos registros sirven para calibrar los umbrales con lo que pasa en campo.")

with tab_riego:
    st.subheader("Riego recomendado para hoy")
    st.dataframe(riego, hide_index=True)
    st.metric("Ahorro frente al riego fijo (litros hoy)", f"{riego['ahorro_litros'].sum():,.0f}")
    for fila in riego.itertuples():
        if fila.litros_recomendados == 0 and fila.zona in zonas_en_riesgo_alto:
            st.warning(
                f"{fila.zona}: no regar hoy. El suelo ya está en la humedad objetivo y la zona está en "
                "riesgo alto de tizón tardío."
            )
        elif fila.litros_recomendados == 0:
            st.info(f"{fila.zona}: no hace falta regar hoy; el suelo ya está en la humedad objetivo.")
    st.caption(
        "Cálculo: (humedad objetivo − humedad actual) × litros por punto. Regar solo lo que falta ahorra agua "
        "y evita el exceso de humedad que favorece al tizón tardío."
    )

with tab_logistica:
    st.subheader("Ruta fresca: recolección compartida a la Central de Abasto")
    escenario = st.radio("Escenario", ["Pesimista", "Base", "Optimista"], index=1, horizontal=True)
    st.caption(NOTAS_ESCENARIO[escenario])
    priorizar = st.checkbox("Dar prioridad a las cargas de zonas con alerta validada", value=True)
    if priorizar and zonas_validadas:
        st.caption("Zonas con prioridad: " + ", ".join(sorted(zonas_validadas)) + ".")
    elif priorizar:
        st.caption("Aún no hay alertas validadas; el reparto usa solo vida útil y hora de carga.")

    with st.expander("Cargas registradas (editable)"):
        tabla_cargas = st.data_editor(
            cargas_del_escenario(escenario), num_rows="dynamic", hide_index=True, key=f"cargas_{escenario}"
        )
    cargas = tabla_cargas.dropna().to_dict("records")
    plan = planear_rutas(
        cargas,
        zonas_prioritarias=zonas_validadas if priorizar else set(),
        lento=0.7 if escenario == "Pesimista" else 1.0,
    )

    st.markdown("**Cada quien por su lado contra Ruta fresca**")
    ind, com = plan["individual"], plan["compartida"]
    kg_total = max(1, plan["kg"])
    st.dataframe(
        pd.DataFrame(
            {
                "Indicador": [
                    "Viajes a la Central",
                    "Kilómetros, ida y vuelta",
                    "Costo de flete por kilo (MXN)",
                    "Ocupación de vehículos (%)",
                    "Horas de la carga a la Central",
                    "Kilos en riesgo al llegar",
                ],
                "Por su lado": [
                    ind["viajes"], round(ind["km"]), round(ind["costo"] / kg_total, 2),
                    round(ind["ocupacion"] * 100), round(ind["horas"], 1), ind["kg_riesgo"],
                ],
                "Ruta fresca": [
                    com["viajes"], round(com["km"]), round(com["costo"] / kg_total, 2),
                    round(com["ocupacion"] * 100), round(com["horas"], 1), com["kg_riesgo"],
                ],
            }
        ),
        hide_index=True,
    )

    mapa = go.Figure()
    for camion in plan["camiones"]:
        if not camion["paradas"]:
            st.info(f"Camión {camion['id']}: sin carga asignada en este escenario.")
            continue
        st.markdown(
            f"**Camión {camion['id']}**: {camion['kg']:,.0f} de {camion['cap']:,.0f} kg, "
            f"{camion['km']:,.0f} km, ${camion['costo_total']:,.0f} de flete. "
            f"Llega a la Central a las {a_hora(camion['llegada'])}."
        )
        if camion["llegada"] > a_minutos(CENTRAL["hora_limite"]):
            st.error(f"Camión {camion['id']}: llega después del límite de {CENTRAL['hora_limite']}.")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Orden": i,
                        "Recolección": a_hora(p["recoleccion"]),
                        "Productor": p["nombre"],
                        "Municipio": p["municipio"],
                        "Kg": p["kg"],
                        "Carga lista": p["hora_corte"],
                        "Vida restante (h)": round(max(0, p["vida_restante"]), 1),
                        "Estado": estado_frescura(p),
                    }
                    for i, p in enumerate(camion["paradas"], start=1)
                ]
            ),
            hide_index=True,
        )
        puntos = [SALIDA] + camion["paradas"]
        mapa.add_trace(
            go.Scatter(
                x=[p["lng"] for p in puntos],
                y=[p["lat"] for p in puntos],
                mode="lines+markers+text",
                text=["Salida"] + [str(i) for i in range(1, len(puntos))],
                textposition="top center",
                marker={"size": 12},
                name=f"Camión {camion['id']}",
            )
        )

    if plan["sin_lugar"]:
        kg_fuera = sum(c["kg"] for c in plan["sin_lugar"])
        st.warning(
            f"{len(plan['sin_lugar'])} productor(es) con {kg_fuera:,.0f} kg no caben hoy. "
            "Se les reserva lugar para mañana; la comparación usa solo la carga que sí sale."
        )
        mapa.add_trace(
            go.Scatter(
                x=[c["lng"] for c in plan["sin_lugar"]],
                y=[c["lat"] for c in plan["sin_lugar"]],
                mode="markers",
                marker={"size": 12, "symbol": "circle-open", "color": "red"},
                name="Sin lugar hoy",
            )
        )
    else:
        st.caption(f"Toda la carga registrada ({plan['kg']:,.0f} kg) sale hoy.")

    mapa.update_layout(
        title="Mapa esquemático de recolección (la Central queda al norte, fuera del mapa)",
        xaxis_title="Longitud",
        yaxis_title="Latitud",
        height=450,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
    )
    mapa.update_yaxes(scaleanchor="x", scaleratio=1)
    st.plotly_chart(mapa)

    with st.expander("Avisos a productores (vista previa, no se envían)"):
        for camion in plan["camiones"]:
            for p in camion["paradas"]:
                st.write(
                    f"Para {p['nombre']}: el camión {camion['id']} pasa por tus {p['kg']:,.0f} kg "
                    f"a las {a_hora(p['recoleccion'])}."
                )
        for c in plan["sin_lugar"]:
            st.write(f"Para {c['nombre']}: hoy los camiones van llenos. Te apartamos lugar para mañana.")

    st.caption("Distancias en línea recta por un factor de camino; no sustituyen un cálculo de rutas reales.")

with tab_fuentes:
    st.subheader("De dónde sale cada cosa")
    st.markdown(
        """
- **Umbrales de tizón tardío:** humedad relativa mayor a 70 % y temperatura menor a 22 °C, según el manual
  *Producción sustentable de jitomate en agricultura protegida* (ICAMEX, Gobierno del Estado de México, 2024).
  No es una fuente de Morelos: hay que calibrarlos en campo.
- **Producción de jitomate en Morelos:** 201,721 t y quinto lugar nacional en el cierre agrícola 2023 (SIAP).
  Pendiente de abrir la fuente original.
- **Municipios de riego:** Cuautla, Ayala y Tepalcingo aparecen como zonas de riego en una nota de prensa de 2019.
  Pendiente de confirmar con SIAP o Sedagro.
- **Lecturas de sensores:** simuladas. Las diferencias entre zonas son inventadas para la demostración.
- **Cargas, vida útil, costos y velocidades:** supuestos del equipo, editables en pantalla.
- **Cálculo de rutas:** adaptado del demo Ruta fresca del equipo; usa línea recta por un factor de camino.
"""
    )
