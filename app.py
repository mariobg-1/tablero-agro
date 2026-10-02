"""Tablero agro para nopal en los Altos de Morelos: plagas, agua y logística.

Las tres pestañas usan las mismas zonas (municipios). La pestaña de logística
adapta a Python el cálculo de rutas del demo "Ruta fresca" del equipo.

Ejecutar con:  py -m streamlit run app.py
"""

import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Tablero agro", layout="wide")
st.title("Tablero agro: plagas, agua y logística del nopal")
st.caption(
    "Prototipo de hackathon. Las lecturas de sensores y las cargas son SIMULADAS; "
    "umbrales, costos y velocidades son supuestos que deben validarse con productores."
)

ZONAS = ["Tlalnepantla", "Totolapan", "Tlayacapan"]

# ---------------------------------------------------------------------------
# 1. PARÁMETROS (barra lateral): todo lo ajustable vive aquí
# ---------------------------------------------------------------------------
st.sidebar.header("Parámetros")

st.sidebar.subheader("Riesgo de plaga o enfermedad")
umbral_humedad = st.sidebar.slider("Humedad del aire de riesgo (%)", 60, 95, 85)
temp_min, temp_max = st.sidebar.slider("Rango de temperatura de riesgo (°C)", 5, 40, (18, 28))

st.sidebar.subheader("Riego")
humedad_objetivo = st.sidebar.slider("Humedad de suelo objetivo (%)", 30, 80, 60)
litros_por_punto = st.sidebar.number_input("Litros por punto de humedad faltante", 1, 500, 50)
riego_fijo = st.sidebar.number_input("Riego fijo actual (litros por zona al día)", 0, 5000, 1200)

with st.sidebar.expander("Logística: supuestos del cálculo"):
    factor_camino = st.number_input("Factor de camino sobre línea recta", 1.0, 3.0, 1.35, 0.05)
    vel_sierra = st.number_input("Velocidad entre parcelas (km/h)", 5, 90, 30)
    vel_carretera = st.number_input("Velocidad a CDMX (km/h)", 5, 120, 55)
    min_carga = st.number_input("Minutos de carga por parada", 0, 60, 10)
    hora_salida = st.text_input("Salida de camiones (HH:MM)", "05:00")
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
SALIDA = {"nombre": "Salida Tlalnepantla", "lat": 19.0083, "lng": -98.9978}
CENTRAL = {"nombre": "Central de Abasto CDMX", "lat": 19.3745, "lng": -99.093, "hora_limite": "10:00"}

# Cargas ficticias del demo Ruta fresca: (id, nombre, municipio, lat, lng, kg, hora de corte, vida útil en horas)
CARGAS_BASE = [
    ("P01", "Productor A", "Tlalnepantla", 19.00125, -99.01177, 400, "04:00", 12),
    ("P02", "Productor B", "Tlalnepantla", 19.02115, -99.01403, 350, "04:00", 24),
    ("P03", "Productor C", "Tlalnepantla", 18.99689, -99.01436, 300, "04:00", 12),
    ("P04", "Productor D", "Tlalnepantla", 18.99193, -99.00082, 500, "06:00", 12),
    ("P05", "Productor E", "Tlalnepantla", 19.0262, -98.99257, 350, "04:00", 24),
    ("P06", "Productor F", "Tlalnepantla", 19.01172, -99.01582, 200, "04:00", 24),
    ("P07", "Productor G", "Tlalnepantla", 19.02264, -99.00622, 200, "06:00", 12),
    ("P08", "Productor H", "Totolapan", 18.99064, -98.91789, 400, "04:30", 12),
    ("P09", "Productor I", "Totolapan", 18.99106, -98.91474, 250, "04:00", 24),
    ("P10", "Productor J", "Totolapan", 18.99628, -98.91773, 350, "04:30", 18),
    ("P11", "Productor K", "Totolapan", 18.99502, -98.9232, 250, "05:30", 24),
    ("P12", "Productor L", "Totolapan", 19.00474, -98.92584, 200, "04:30", 24),
]
CARGAS_EXTRA = [  # se suman en el escenario optimista
    ("P13", "Productor M", "Tlayacapan", 18.9561, -98.9822, 400, "04:30", 18),
    ("P14", "Productor N", "Tlayacapan", 18.9478, -98.9705, 350, "05:00", 12),
    ("P15", "Productor O", "Totolapan", 18.9712, -98.9311, 300, "05:30", 24),
    ("P16", "Productor P", "Totolapan", 19.0005, -98.9049, 450, "04:00", 18),
]
IDS_PESIMISTA = ["P01", "P03", "P05", "P08", "P10", "P12"]
COLUMNAS = ["id", "nombre", "municipio", "lat", "lng", "kg", "hora_corte", "vida_util_horas"]
NOTAS_ESCENARIO = {
    "Pesimista": "Solo 6 productores se suman y llueve: los caminos van 30 % más lento.",
    "Base": "Una cooperativa con 12 productores y dos camiones.",
    "Optimista": "Se suman 4 productores más de Tlayacapan y Totolapan.",
}


@st.cache_data
def generar_lecturas(horas: int = 72, semilla: int = 7) -> pd.DataFrame:
    """Crea una lectura por hora y por zona: temperatura, humedad del aire y del suelo."""
    rng = np.random.default_rng(semilla)
    momentos = pd.date_range(end=pd.Timestamp.now().floor("h"), periods=horas, freq="h")
    ciclo_dia = np.sin((momentos.hour.to_numpy() - 6) / 24 * 2 * np.pi)  # calor de día, fresco de noche
    extra_humedad = {"Tlalnepantla": 6, "Totolapan": 24, "Tlayacapan": 18}  # diferencia inventada entre zonas
    filas = []
    for i, zona in enumerate(ZONAS, start=1):
        temperatura = 24 + 5 * ciclo_dia + rng.normal(0, 1, horas)
        humedad_aire = 64 - 15 * ciclo_dia + rng.normal(0, 4, horas) + extra_humedad[zona]
        humedad_suelo = 65 - i * 5 + rng.normal(0, 2, horas) - np.linspace(0, 8, horas)
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
    """Riesgo = % de las últimas 48 horas con humedad alta y temperatura favorable."""
    recientes = df[df["momento"] >= df["momento"].max() - pd.Timedelta(hours=48)].copy()
    recientes["condicion"] = (recientes["humedad_aire"] >= umbral_humedad) & recientes[
        "temperatura"
    ].between(temp_min, temp_max)
    riesgo = (recientes.groupby("zona")["condicion"].mean() * 100).round(0)
    tabla = riesgo.reindex(ZONAS).rename("riesgo_pct").reset_index()
    tabla["nivel"] = pd.cut(
        tabla["riesgo_pct"], bins=[-1, 20, 50, 100], labels=["Bajo", "Medio", "Alto"]
    ).astype(str)
    return tabla


def calcular_riego(df: pd.DataFrame) -> pd.DataFrame:
    """Litros recomendados = lo que falta para llegar a la humedad objetivo."""
    ultima = df.sort_values("momento").groupby("zona").tail(1)[["zona", "humedad_suelo"]]
    ultima["litros_recomendados"] = (
        (humedad_objetivo - ultima["humedad_suelo"]).clip(lower=0) * litros_por_punto
    ).round(0)
    ultima["ahorro_litros"] = riego_fijo - ultima["litros_recomendados"]
    return ultima.set_index("zona").reindex(ZONAS).reset_index()


def a_minutos(hora: str) -> int:
    """Convierte 'HH:MM' en minutos desde medianoche."""
    h, m = str(hora).split(":")[:2]
    return int(h) * 60 + int(m)


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


def planear_rutas(cargas: list, zonas_alto: set, lento: float) -> dict:
    """Reparte las cargas en los camiones, ordena las paradas y calcula horarios y costos."""
    v_sierra, v_carretera = vel_sierra * lento, vel_carretera * lento
    camiones = [
        {"id": "C1", "cap": cap_c1, "costo": costo_c1, "cargas": [], "kg": 0},
        {"id": "C2", "cap": cap_c2, "costo": costo_c2, "cargas": [], "kg": 0},
    ]
    # Prioridad: zonas en riesgo alto, luego menor vida útil, luego corte más temprano
    ordenadas = sorted(
        cargas,
        key=lambda c: (c["municipio"] not in zonas_alto, c["vida_util_horas"], a_minutos(c["hora_corte"])),
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
            reloj = max(reloj + tramo / v_sierra * 60, a_minutos(parada["hora_corte"]))  # espera al corte
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
zonas_en_riesgo_alto = set(riesgo.loc[riesgo["nivel"] == "Alto", "zona"])

# ---------------------------------------------------------------------------
# 4. INTERFAZ: una pestaña por problemática del reto
# ---------------------------------------------------------------------------
tab_riesgo, tab_riego, tab_logistica = st.tabs(["Plagas y enfermedades", "Agua", "Logística"])

with tab_riesgo:
    st.subheader("Riesgo en las últimas 48 horas, por zona")
    columnas = st.columns(len(riesgo))
    for columna, fila in zip(columnas, riesgo.itertuples()):
        columna.metric(fila.zona, f"{fila.riesgo_pct:.0f} %", fila.nivel, delta_color="off")
    for zona in sorted(zonas_en_riesgo_alto):
        st.warning(f"{zona}: riesgo alto. Revisar parcelas hoy; sus cargas tienen prioridad en la ruta.")
    st.line_chart(lecturas.pivot(index="momento", columns="zona", values="humedad_aire"))
    st.caption("Humedad del aire por zona (%).")

with tab_riego:
    st.subheader("Riego recomendado para hoy")
    st.dataframe(riego, hide_index=True)
    st.metric("Ahorro frente al riego fijo (litros hoy)", f"{riego['ahorro_litros'].sum():,.0f}")
    st.caption("Cálculo: (humedad objetivo − humedad actual) × litros por punto.")

with tab_logistica:
    st.subheader("Ruta fresca: recolección compartida a la Central de Abasto")
    escenario = st.radio("Escenario", ["Pesimista", "Base", "Optimista"], index=1, horizontal=True)
    st.caption(NOTAS_ESCENARIO[escenario])
    priorizar = st.checkbox("Dar prioridad a las cargas de zonas en riesgo alto", value=True)

    with st.expander("Cargas registradas (editable)"):
        tabla_cargas = st.data_editor(
            cargas_del_escenario(escenario), num_rows="dynamic", hide_index=True, key=f"cargas_{escenario}"
        )
    cargas = tabla_cargas.dropna().to_dict("records")
    plan = planear_rutas(
        cargas,
        zonas_alto=zonas_en_riesgo_alto if priorizar else set(),
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
                    "Horas del corte a la Central",
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
                        "Corte": p["hora_corte"],
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

    st.caption(
        "Distancias en línea recta por un factor de camino; no sustituyen un cálculo de rutas reales."
    )
