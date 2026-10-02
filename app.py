"""Tablero agro para jitomate en la zona de riego del oriente de Morelos.

Dos modos en la barra lateral:
  - Agricultor: ve la recomendación de hoy de su zona solo cuando el especialista ya la validó.
  - Especialista: ajusta los parámetros, valida o cambia cada recomendación y ve las pestañas de análisis.

Las pestañas del especialista comparten un solo flujo de datos:
  0. Validar recomendaciones: riego y aviso de tizón de hoy, por zona.
  1. Riesgo de tizón tardío a partir de humedad y temperatura.
  2. Riego recomendado según la humedad del suelo.
  3. Ciclo del cultivo: balance de agua diario (FAO-56) e índices de plagas para todo el ciclo.
  4. Rutas de recolección compartida (cálculo adaptado del demo "Ruta fresca").

Ejecutar con:  py -m streamlit run app.py
"""

import io
import math
import re
import unicodedata
from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Tablero agro jitomate", layout="wide")

ZONAS = ["Cuautla", "Ayala", "Tepalcingo"]

# ---------------------------------------------------------------------------
# 1. PARÁMETROS (barra lateral): todo lo ajustable vive aquí y solo lo ve el especialista
# ---------------------------------------------------------------------------
modo = st.sidebar.radio("¿Quién usa el tablero?", ["Agricultor", "Especialista"], horizontal=True, key="modo")
especialista = modo == "Especialista"
PARAMETROS = st.session_state.setdefault("parametros", {})


def ajuste(clave: str, por_defecto, control):
    """El especialista ve y mueve el control; el agricultor usa el último valor que dejó el especialista."""
    PARAMETROS.setdefault(clave, por_defecto)
    if especialista:
        llave = f"par_{clave}"
        if llave not in st.session_state:  # Streamlit borra el control al pasar por la vista del agricultor
            st.session_state[llave] = PARAMETROS[clave]
        PARAMETROS[clave] = control(llave)
    return PARAMETROS[clave]


if especialista:
    st.sidebar.header("Parámetros del especialista")
    st.sidebar.subheader("Riesgo de tizón tardío")
umbral_humedad = ajuste("umbral_humedad", 80, lambda k: st.sidebar.slider("Humedad relativa de riesgo (%)", 50, 95, key=k))
temp_min_riesgo, temp_max_riesgo = ajuste(
    "rango_temp", (10, 17), lambda k: st.sidebar.slider("Rango de temperatura de riesgo (°C)", 0, 30, key=k)
)
ventana_horas = ajuste(
    "ventana_horas", 168,
    lambda k: st.sidebar.radio("Ventana de cálculo", [48, 168], format_func=lambda h: f"{h} horas", key=k),
)

if especialista:
    st.sidebar.subheader("Riego")
humedad_objetivo = ajuste("humedad_objetivo", 60, lambda k: st.sidebar.slider("Humedad de suelo objetivo (%)", 30, 80, key=k))
litros_por_punto = ajuste(
    "litros_por_punto", 50, lambda k: st.sidebar.number_input("Litros por punto de humedad faltante", 1, 500, key=k)
)
riego_fijo = ajuste(
    "riego_fijo", 1200, lambda k: st.sidebar.number_input("Riego fijo actual (litros por zona al día)", 0, 5000, key=k)
)

SUELOS = {"Arenoso": 50, "Franco": 80, "Arcilloso": 110}  # agua disponible total en la zona de raíces (mm)
SISTEMAS_RIEGO = {"Goteo": 85, "Aspersión": 75, "Surco o rodado": 60}  # eficiencia de aplicación (%)
ESCENARIOS_CLIMA = {  # (grados de más, factor de lluvia, semilla)
    "Normal": (0, 1.0, 11),
    "Seco y cálido": (2, 0.35, 23),
    "Lluvioso y fresco": (-1, 1.5, 37),
}

panel = st.sidebar.expander("Ciclo del cultivo: lote y supuestos") if especialista else None
fecha_trasplante = ajuste("fecha_trasplante", date(2026, 10, 15), lambda k: panel.date_input("Fecha de trasplante", key=k))
invernadero = ajuste(
    "produccion", "Campo abierto",
    lambda k: panel.radio("Producción", ["Campo abierto", "Invernadero"], horizontal=True, key=k),
) == "Invernadero"
suelo = ajuste("suelo", "Franco", lambda k: panel.selectbox("Suelo", list(SUELOS), key=k))
sistema_riego = ajuste("sistema_riego", "Goteo", lambda k: panel.selectbox("Sistema de riego", list(SISTEMAS_RIEGO), key=k))
clima_elegido = ajuste(
    "clima", "Normal", lambda k: panel.selectbox("Clima del ciclo", [*ESCENARIOS_CLIMA, "Archivo CSV"], key=k)
)
if especialista and clima_elegido == "Archivo CSV":
    # El archivo se guarda en bytes para que el cálculo siga igual en la vista del agricultor
    subido = panel.file_uploader("CSV diario: fecha, tmax, tmin, lluvia (mm) y, opcional, hr (%)", type="csv")
    if subido is not None:
        PARAMETROS["archivo_clima"] = subido.getvalue()
    elif PARAMETROS.get("archivo_clima"):
        panel.caption("Se usa el último CSV que cargaste.")
archivo_clima = None
if clima_elegido == "Archivo CSV" and PARAMETROS.get("archivo_clima"):
    archivo_clima = io.BytesIO(PARAMETROS["archivo_clima"])
dias_entre_riegos = ajuste(
    "dias_entre_riegos", 3, lambda k: panel.number_input("Riego actual: días entre riegos", 1, 15, key=k)
)
lamina_calendario = ajuste(
    "lamina_calendario", 14, lambda k: panel.number_input("Riego actual: lámina por riego (mm)", 1, 60, key=k)
)
costo_agua = ajuste(
    "costo_agua", 0.0,
    lambda k: panel.number_input("Costo del agua (MXN por m³; 0 si no se sabe)", 0.0, 50.0, step=0.5, key=k),
)
umbral_medio_plagas, umbral_alto_plagas = ajuste(
    "umbrales_plagas", (35, 65), lambda k: panel.slider("Índice de plagas: riesgo medio y alto", 0, 100, key=k)
)

panel = st.sidebar.expander("Logística: supuestos del cálculo") if especialista else None
factor_camino = ajuste(
    "factor_camino", 1.35, lambda k: panel.number_input("Factor de camino sobre línea recta", 1.0, 3.0, step=0.05, key=k)
)
vel_local = ajuste("vel_local", 30, lambda k: panel.number_input("Velocidad entre parcelas (km/h)", 5, 90, key=k))
vel_carretera = ajuste("vel_carretera", 55, lambda k: panel.number_input("Velocidad a CDMX (km/h)", 5, 120, key=k))
min_carga = ajuste("min_carga", 10, lambda k: panel.number_input("Minutos de carga por parada", 0, 60, key=k))
hora_salida = ajuste("hora_salida", "06:00", lambda k: panel.text_input("Salida de camiones (HH:MM)", key=k))
if especialista and not re.fullmatch(r"\d{1,2}:\d{2}", hora_salida.strip()):
    panel.warning("Hora de salida no válida; se usa 06:00.")
cap_c1 = ajuste("cap_c1", 3500, lambda k: panel.number_input("Capacidad C1 (kg)", 100, 20000, step=100, key=k))
cap_c2 = ajuste("cap_c2", 1500, lambda k: panel.number_input("Capacidad C2 (kg)", 100, 20000, step=100, key=k))
costo_c1 = ajuste("costo_c1", 18, lambda k: panel.number_input("Costo C1 por km (MXN)", 1, 100, key=k))
costo_c2 = ajuste("costo_c2", 12, lambda k: panel.number_input("Costo C2 por km (MXN)", 1, 100, key=k))
cap_camioneta = ajuste(
    "cap_camioneta", 1000,
    lambda k: panel.number_input("Capacidad de camioneta individual (kg)", 50, 5000, step=50, key=k),
)
costo_camioneta = ajuste("costo_camioneta", 9, lambda k: panel.number_input("Costo de camioneta por km (MXN)", 1, 100, key=k))
horas_espera = ajuste(
    "horas_espera", 3.0,
    lambda k: panel.number_input("Horas de espera del flete individual", 0.0, 12.0, step=0.5, key=k),
)

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
    extra_humedad = {"Cuautla": -14, "Ayala": -2, "Tepalcingo": 10}
    suelo_inicial = {"Cuautla": 52, "Ayala": 60, "Tepalcingo": 74}
    filas = []
    for zona in ZONAS:
        temperatura = 20 + 7 * ciclo_dia + rng.normal(0, 1, horas)  # noches frescas, como en otoño-invierno
        humedad_aire = 70 - 16 * ciclo_dia + rng.normal(0, 4, horas) + extra_humedad[zona]
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

# Ciclo del cultivo. Duraciones y coeficientes de referencia tipo FAO-56 para jitomate; confirmar con INIFAP
ETAPAS = [("Recién trasplantado", 30), ("Creciendo", 40), ("Floración y frutos", 45), ("Maduración y cosecha", 30)]
DIAS_CICLO = sum(dias for _, dias in ETAPAS)
KC_INICIAL, KC_MEDIO, KC_FINAL = 0.6, 1.15, 0.8
AGOTAMIENTO_PERMISIBLE = 0.4  # fracción del agua del suelo que se puede gastar sin estrés
UMBRAL_DEFICITARIO = 0.5  # el riego deficitario espera a que se gaste esta fracción
KY = 1.05  # sensibilidad del rendimiento al estrés hídrico (Doorenbos y Kassam)
LLUVIA_EFECTIVA, LLUVIA_MINIMA = 0.8, 5  # fracción que llega a la raíz; debajo de 5 mm se ignora
FACTOR_INVERNADERO = 0.8  # menos radiación bajo plástico; ejemplo por medir
LATITUD = 18.8
ESTRATEGIAS = ["Riego actual", "Balance de agua", "Riego deficitario"]

# Clima mensual de referencia del oriente de Morelos (aproximado): máxima, mínima y probabilidad de lluvia diaria
TMAX_MES = [29, 31, 33, 34, 34, 31, 30, 30, 29, 29, 29, 28]
TMIN_MES = [11, 12, 14, 16, 17, 18, 17, 17, 17, 15, 12, 11]
PROB_LLUVIA_MES = [0.04, 0.04, 0.04, 0.08, 0.2, 0.5, 0.55, 0.55, 0.6, 0.3, 0.08, 0.04]

# Umbrales de ejemplo para los índices de plagas; validar con un fitopatólogo
PLAGAS = {
    "Tizón tardío": {"rango": (12, 22), "revisar": "Manchas oscuras o aguadas en hojas y tallos, moho blanco por debajo."},
    "Mosquita blanca": {"rango": (26, 32), "revisar": "Moscas blancas que vuelan al mover las hojas; hojas amarillas."},
    "Palomilla del tomate": {"rango": None, "revisar": "Minas transparentes en las hojas y frutos con agujeritos."},
}
TUTA_BASE, TUTA_RITMO = 8, 22  # temperatura base y grados-día diarios del ritmo máximo
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


@st.cache_data
def clima_sintetico(inicio: date, dias: int, escenario: str) -> pd.DataFrame:
    """Clima diario inventado a partir del clima mensual de referencia, con rachas de calor y días de lluvia."""
    grados_extra, factor_lluvia, semilla = ESCENARIOS_CLIMA[escenario]
    rng = np.random.default_rng(semilla + inicio.toordinal())
    anomalia, filas = 0.0, []
    for i in range(dias):
        dia = inicio + timedelta(days=i)
        m = dia.month - 1
        anomalia = 0.7 * anomalia + (rng.random() - 0.5) * 3
        lluvia = 0.0
        if rng.random() < min(0.9, PROB_LLUVIA_MES[m] * factor_lluvia):
            lluvia = min(rng.exponential(12 if 5 <= m <= 9 else 6), 70)  # aguaceros más fuertes en temporal
        mojado = lluvia > 0
        filas.append(
            {
                "fecha": pd.Timestamp(dia),
                "tmax": TMAX_MES[m] + grados_extra + anomalia - (3 if mojado else 0),
                "tmin": TMIN_MES[m] + grados_extra * 0.5 + anomalia * 0.4 + (1 if mojado else 0),
                "lluvia": round(lluvia, 1),
                "hr": np.nan,
            }
        )
    return pd.DataFrame(filas)


def leer_clima_csv(archivo) -> pd.DataFrame:
    """Lee un CSV diario. Acepta encabezados como fecha/date, tmax, tmin, lluvia/precip y hr/humedad."""
    tabla = pd.read_csv(archivo, sep=None, engine="python")
    tabla.columns = [
        unicodedata.normalize("NFD", str(c).strip().lower()).encode("ascii", "ignore").decode() for c in tabla.columns
    ]

    def buscar(*prefijos):
        return next((c for c in tabla.columns if c.startswith(prefijos)), None)

    columnas = {
        "fecha": buscar("fecha", "date"),
        "tmax": buscar("tmax", "temp_max"),
        "tmin": buscar("tmin", "temp_min"),
        "lluvia": buscar("lluvia", "precip", "rain", "pp"),
        "hr": buscar("hr", "rh", "humedad"),
    }
    faltan = [nombre for nombre, columna in columnas.items() if columna is None and nombre != "hr"]
    if faltan:
        raise ValueError(f"Al CSV le faltan columnas: {', '.join(faltan)}.")
    clima = pd.DataFrame({nombre: tabla[c] if c else np.nan for nombre, c in columnas.items()})
    fechas = pd.to_datetime(clima["fecha"], format="ISO8601", errors="coerce")
    clima["fecha"] = fechas.fillna(pd.to_datetime(clima["fecha"], format="%d/%m/%Y", errors="coerce"))
    for nombre in ["tmax", "tmin", "lluvia", "hr"]:
        clima[nombre] = pd.to_numeric(clima[nombre], errors="coerce")
    clima["lluvia"] = clima["lluvia"].fillna(0).clip(lower=0)
    return clima.dropna(subset=["fecha", "tmax", "tmin"]).sort_values("fecha").reset_index(drop=True)


origen_clima = clima_elegido
clima_ciclo = None
if archivo_clima is not None:
    try:
        desde_csv = leer_clima_csv(archivo_clima)
        desde_csv = desde_csv[desde_csv["fecha"] >= pd.Timestamp(fecha_trasplante)].head(DIAS_CICLO)
        if len(desde_csv) >= 7:
            clima_ciclo = desde_csv.reset_index(drop=True)
        else:
            st.sidebar.warning("El CSV no tiene 7 días válidos desde el trasplante; se usa el clima normal.")
    except ValueError as error:
        st.sidebar.warning(f"No se pudo leer el CSV ({error}); se usa el clima normal.")
if clima_ciclo is None:
    if clima_elegido not in ESCENARIOS_CLIMA:
        origen_clima = "Normal (falta un CSV válido)"
    clima_ciclo = clima_sintetico(fecha_trasplante, DIAS_CICLO, clima_elegido if clima_elegido in ESCENARIOS_CLIMA else "Normal")


# ---------------------------------------------------------------------------
# 3. LÓGICA: riesgo, riego y rutas
# ---------------------------------------------------------------------------
def calcular_riesgo(df: pd.DataFrame) -> pd.DataFrame:
    """Riesgo = horas diarias con humedad alta y temperatura fresca, favorables al tizón tardío."""
    recientes = df[df["momento"] > df["momento"].max() - pd.Timedelta(hours=ventana_horas)].copy()
    recientes["favorable"] = (recientes["humedad_aire"] >= umbral_humedad) & recientes["temperatura"].between(
        temp_min_riesgo, temp_max_riesgo
    )
    tabla = recientes.groupby("zona")["favorable"].sum().rename("horas_favorables").reindex(ZONAS).reset_index()
    tabla["horas_por_dia"] = (tabla["horas_favorables"] / (ventana_horas / 24)).round(1)
    # Alto desde 8 horas diarias: es la duración mínima de un ciclo de infección en el estudio de referencia
    tabla["nivel"] = pd.cut(
        tabla["horas_por_dia"], bins=[0, 4, 8, 25], right=False, labels=["Bajo", "Medio", "Alto"]
    ).astype(str)
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


def mostrar_grafica_fija(figura: go.Figure) -> None:
    """Muestra una gráfica sin zoom ni arrastre, para que siempre se vea completa."""
    figura.update_xaxes(fixedrange=True)
    figura.update_yaxes(fixedrange=True)
    st.plotly_chart(figura, config={"displayModeBar": False, "scrollZoom": False})


def a_fecha(momento) -> str:
    """Fecha corta en español, como '15 oct'."""
    return f"{momento.day} {MESES[momento.month - 1]}"


def etapa_del_dia(i: int) -> str:
    """Nombre de la etapa del cultivo en el día i del ciclo (0 = trasplante)."""
    acumulado = 0
    for nombre, dias in ETAPAS:
        acumulado += dias
        if i < acumulado:
            return nombre
    return ETAPAS[-1][0]


def kc_del_dia(i: int) -> float:
    """Coeficiente de cultivo: fijo en etapas inicial y media, en línea recta durante el desarrollo y el final."""
    ini, des, med, fin = (dias for _, dias in ETAPAS)
    if i < ini:
        return KC_INICIAL
    if i < ini + des:
        return KC_INICIAL + (KC_MEDIO - KC_INICIAL) * (i - ini) / des
    if i < ini + des + med:
        return KC_MEDIO
    return KC_MEDIO + (KC_FINAL - KC_MEDIO) * min(1, (i - ini - des - med) / fin)


def radiacion_extraterrestre(dia_anio: np.ndarray, latitud: float) -> np.ndarray:
    """Radiación extraterrestre en mm de agua evaporable al día (ecuación 21 de FAO-56)."""
    phi = np.radians(latitud)
    dr = 1 + 0.033 * np.cos(2 * np.pi / 365 * dia_anio)
    delta = 0.409 * np.sin(2 * np.pi / 365 * dia_anio - 1.39)
    ws = np.arccos(np.clip(-np.tan(phi) * np.tan(delta), -1, 1))
    ra = 24 * 60 / np.pi * 0.082 * dr * (ws * np.sin(phi) * np.sin(delta) + np.cos(phi) * np.cos(delta) * np.sin(ws))
    return ra / 2.45


def preparar_ciclo(clima: pd.DataFrame) -> pd.DataFrame:
    """Agrega evapotranspiración de referencia (Hargreaves-Samani), del cultivo y lluvia efectiva."""
    ciclo = clima.copy()
    ciclo["tmedia"] = (ciclo["tmax"] + ciclo["tmin"]) / 2
    ra = radiacion_extraterrestre(ciclo["fecha"].dt.dayofyear.to_numpy(), LATITUD)
    ciclo["eto"] = (
        0.0023 * (ciclo["tmedia"] + 17.8) * np.sqrt((ciclo["tmax"] - ciclo["tmin"]).clip(lower=0)) * ra
    ).clip(lower=0) * (FACTOR_INVERNADERO if invernadero else 1)
    ciclo["kc"] = [kc_del_dia(i) for i in range(len(ciclo))]
    ciclo["etc"] = ciclo["kc"] * ciclo["eto"]
    util = (ciclo["lluvia"] >= LLUVIA_MINIMA) & (not invernadero)
    ciclo["lluvia_efectiva"] = np.where(util, ciclo["lluvia"] * LLUVIA_EFECTIVA, 0.0)
    return ciclo


def simular_riego(ciclo: pd.DataFrame, estrategia: str) -> dict:
    """Balance diario del agua que le falta a la zona de raíces (FAO-56) con una estrategia de riego."""
    taw = SUELOS[suelo]
    raw = AGOTAMIENTO_PERMISIBLE * taw
    eficiencia = SISTEMAS_RIEGO[sistema_riego] / 100
    deficit, filas = 0.0, []
    for i, dia in enumerate(ciclo.itertuples()):
        inicial = deficit
        if estrategia == "Riego actual":
            lamina = float(lamina_calendario) if i % dias_entre_riegos == 0 else 0.0
        elif estrategia == "Balance de agua":
            lamina = inicial if inicial >= raw else 0.0  # repone justo antes de que la planta pase sed
        else:
            lamina = inicial if inicial >= UMBRAL_DEFICITARIO * taw else 0.0
        tras_riego = max(0.0, inicial - lamina)  # el agua de más se pierde por debajo de la raíz
        ks = 1.0 if tras_riego <= raw else max(0.0, (taw - tras_riego) / (taw - raw))
        deficit = min(taw, max(0.0, tras_riego + ks * dia.etc - dia.lluvia_efectiva))
        filas.append({"deficit_inicial": inicial, "riego": lamina, "ks": ks, "et_real": ks * dia.etc})
    diario = pd.DataFrame(filas)
    rendimiento = 1 - KY * (1 - diario["et_real"].sum() / max(1e-9, ciclo["etc"].sum()))
    return {
        "diario": diario,
        "m3_ha": (diario["riego"] / eficiencia).sum() * 10,  # 1 mm sobre una hectárea son 10 m³
        "riegos": int((diario["riego"] > 0).sum()),
        "dias_estres": int((diario["ks"] < 1).sum()),
        "rendimiento": min(1.0, max(0.0, rendimiento)),
    }


def trapecio(x, a: float, b: float, c: float, d: float) -> np.ndarray:
    """Vale 1 dentro del rango favorable [b, c] y baja en línea recta hasta 0 en a y en d."""
    x = np.asarray(x, dtype=float)
    return np.clip(np.minimum((x - a) / (b - a), (d - x) / (d - c)), 0, 1)


def indices_plagas(ciclo: pd.DataFrame) -> pd.DataFrame:
    """Índice de 0 a 100 por plaga según la temperatura y la lluvia (o humedad) de los últimos días."""
    t7 = ciclo["tmedia"].rolling(7, min_periods=1).mean()
    lluvia7 = ciclo["lluvia"].rolling(7, min_periods=1).sum()
    if invernadero:
        humedad_respaldo = pd.Series(0.6, index=ciclo.index)
    else:  # sin sensor de humedad, cuenta los días con lluvia de los últimos 5
        humedad_respaldo = ((ciclo["lluvia"] >= 1).astype(int).rolling(5, min_periods=1).sum() / 3).clip(upper=1)
    humedad = ((ciclo["hr"].rolling(7, min_periods=1).mean() - 70) / 20).clip(0, 1).fillna(humedad_respaldo)
    grados_dia = ((np.minimum(ciclo["tmax"], 32) + np.maximum(ciclo["tmin"], TUTA_BASE)) / 2 - TUTA_BASE).clip(lower=0)
    tiz_min, tiz_max = PLAGAS["Tizón tardío"]["rango"]
    mos_min, mos_max = PLAGAS["Mosquita blanca"]["rango"]
    return pd.DataFrame(
        {
            "Tizón tardío": 100 * trapecio(t7, tiz_min - 4, tiz_min, tiz_max, tiz_max + 4) * humedad,
            "Mosquita blanca": 100
            * trapecio(t7, mos_min - 4, mos_min, mos_max, mos_max + 4)
            * (1 if invernadero else (1 - lluvia7 / 40).clip(0, 1)),
            "Palomilla del tomate": 100 * (grados_dia.rolling(14, min_periods=1).mean() / TUTA_RITMO).clip(0, 1),
        }
    )


def nivel_plaga(indice: float) -> str:
    """Bajo, Medio o Alto según los umbrales de la barra lateral."""
    if indice >= umbral_alto_plagas:
        return "Alto"
    return "Medio" if indice >= umbral_medio_plagas else "Bajo"


def periodos_de_alerta(indices: pd.DataFrame, fechas: pd.Series) -> pd.DataFrame:
    """Tramos de días seguidos en riesgo alto, por plaga, ordenados por fecha de inicio."""
    filas = []
    for plaga in indices.columns:
        alto = indices[plaga] >= umbral_alto_plagas
        tramos = (alto != alto.shift()).cumsum()
        for _, tramo in indices.loc[alto, plaga].groupby(tramos[alto]):
            filas.append(
                {
                    "inicio": tramo.index[0],
                    "Plaga": plaga,
                    "Desde": a_fecha(fechas[tramo.index[0]]),
                    "Hasta": a_fecha(fechas[tramo.index[-1]]),
                    "Índice máximo": f"{tramo.max():.0f}",
                }
            )
    if not filas:
        return pd.DataFrame()
    return pd.DataFrame(filas).sort_values("inicio").drop(columns="inicio").set_index("Plaga")


riesgo = calcular_riesgo(lecturas)
riego = calcular_riego(lecturas)
ciclo = preparar_ciclo(clima_ciclo)
simulaciones = {estrategia: simular_riego(ciclo, estrategia) for estrategia in ESTRATEGIAS}
indices = indices_plagas(ciclo)
zonas_en_riesgo_alto = list(riesgo.loc[riesgo["nivel"] == "Alto", "zona"])

AVISO_TIZON = {
    "Alto": "Riesgo alto de tizón tardío: revisar hoy hojas y tallos y ventilar el cultivo.",
    "Medio": "Riesgo medio de tizón tardío: revisar el cultivo como de costumbre.",
    "Bajo": "Riesgo bajo de tizón tardío: sin avisos.",
}
ESTADOS_VALIDACION = ["Pendiente", "Validada", "Cambiada"]
validaciones = st.session_state.setdefault("validaciones", {})


def recomendacion_del_dia(zona: str) -> dict:
    """Lo que el modelo propone hoy para una zona: riego y aviso de tizón tardío."""
    fila_riego = riego.set_index("zona").loc[zona]
    nivel = riesgo.set_index("zona").at[zona, "nivel"]
    litros = int(fila_riego["litros_recomendados"])
    texto_riego = f"Regar {litros:,} litros hoy." if litros > 0 else "No regar hoy: el suelo ya tiene la humedad objetivo."
    return {
        "litros": litros,
        "nivel": nivel,
        "humedad_suelo": fila_riego["humedad_suelo"],
        "riego": texto_riego,
        "tizon": AVISO_TIZON[nivel],
        "texto": f"{texto_riego} {AVISO_TIZON[nivel]}",
    }


def validacion_vigente(zona: str, recomendacion: dict):
    """La decisión del especialista, solo si sigue siendo sobre la misma recomendación que da el modelo ahora."""
    guardada = validaciones.get(zona)
    if guardada and guardada["texto"] == recomendacion["texto"]:
        return guardada
    return None


recomendaciones = {zona: recomendacion_del_dia(zona) for zona in ZONAS}
zonas_validadas = {  # alertas de tizón confirmadas por el especialista; la logística les da prioridad
    zona for zona in zonas_en_riesgo_alto
    if (v := validacion_vigente(zona, recomendaciones[zona])) and v["estado"] == "Validada"
}


def guardar_revision(zona: str, con_sintomas: str, nota: str, quien: str) -> None:
    """Agrega una revisión en parcela al registro que comparten el agricultor y el especialista."""
    registros = st.session_state.setdefault("registros", [])
    st.session_state.setdefault("siguiente_id", 1)
    registros.append(
        {
            "id": st.session_state["siguiente_id"],
            "fecha": date.today().isoformat(),
            "zona": zona,
            "síntomas": con_sintomas,
            "nota": nota,
            "registró": quien,
        }
    )
    st.session_state["siguiente_id"] += 1


# ---------------------------------------------------------------------------
# 4A. VISTA DEL AGRICULTOR: solo ve lo que el especialista ya validó
# ---------------------------------------------------------------------------
if not especialista:
    st.title("Mi parcela de jitomate")
    st.caption("Recomendaciones del día revisadas por tu técnico. Prototipo: las lecturas de sensores son simuladas.")
    if "mi_zona" not in st.session_state:  # Streamlit borra el control al pasar por la vista del especialista
        st.session_state["mi_zona"] = st.session_state.get("zona_guardada", ZONAS[0])
    zona = st.selectbox("¿Dónde está tu parcela?", ZONAS, key="mi_zona")
    st.session_state["zona_guardada"] = zona
    recomendacion = recomendaciones[zona]
    decision = validacion_vigente(zona, recomendacion)

    if decision is None:
        st.info(
            "Tu técnico todavía no revisa la recomendación de hoy. Cuando la confirme, aparecerá aquí. "
            "Mientras tanto, riega como de costumbre."
        )
    elif decision["estado"] == "Cambiada":
        st.warning(f"Tu técnico revisó la recomendación de hoy y te indica: {decision['nota']}")
    else:
        if recomendacion["litros"] > 0:
            st.header("Hoy toca regar")
            st.metric("Agua para hoy", f"{recomendacion['litros']:,} litros")
        else:
            st.header("Hoy no riegues")
            st.write("Tu suelo todavía tiene la humedad que necesita el cultivo.")
        mostrar_aviso = {"Alto": st.error, "Medio": st.warning, "Bajo": st.success}[recomendacion["nivel"]]
        mostrar_aviso(recomendacion["tizon"])
        if decision["nota"]:
            st.info(f"Nota de tu técnico: {decision['nota']}")
        st.caption(f"Confirmado por tu técnico a las {decision['hora']}. Esta herramienta nunca receta productos.")

    st.subheader("Cuéntale a tu técnico qué viste hoy")
    with st.form("revision_agricultor", clear_on_submit=True):
        con_sintomas = st.radio(
            "¿Viste manchas oscuras en hojas o tallos, o moho blanco debajo de las hojas?", ["No", "Sí"], horizontal=True
        )
        nota = st.text_input("Algo más que quieras contarle (opcional)")
        if st.form_submit_button("Enviar a mi técnico"):
            guardar_revision(zona, con_sintomas, nota, "Agricultor")
            st.success("Listo, tu técnico lo verá en su tablero.")
    st.stop()

# ---------------------------------------------------------------------------
# 4B. VISTA DEL ESPECIALISTA: una pestaña por problemática del reto
# ---------------------------------------------------------------------------
st.title("Tablero agro: tizón tardío, agua y logística del jitomate")
st.caption(
    "Prototipo de hackathon para Cuautla, Ayala y Tepalcingo, Morelos. Las lecturas de sensores y las "
    "cargas son SIMULADAS; costos, velocidades, vida útil y litros son supuestos por validar."
)
tab_validar, tab_riesgo, tab_riego, tab_ciclo, tab_logistica, tab_fuentes = st.tabs(
    ["Validar recomendaciones", "Tizón tardío", "Agua", "Ciclo del cultivo", "Logística", "Fuentes y supuestos"]
)

with tab_validar:
    st.subheader("Recomendaciones de hoy por zona")
    st.caption(
        "Nada llega al agricultor hasta que lo valides. Si la recomendación cambia porque moviste un parámetro, "
        "vuelve a quedar pendiente."
    )
    for zona, recomendacion in recomendaciones.items():
        with st.container(border=True):
            st.markdown(f"**{zona}**")
            st.write(recomendacion["texto"])
            fila_riesgo = riesgo.set_index("zona").loc[zona]
            st.caption(
                f"Humedad del suelo {recomendacion['humedad_suelo']:.1f} % (objetivo {humedad_objetivo} %). "
                f"{fila_riesgo['horas_por_dia']:.1f} horas al día favorables al tizón."
            )
            vigente = validacion_vigente(zona, recomendacion) or {}
            sufijo = f"{zona}_{recomendacion['litros']}_{recomendacion['nivel']}"  # se reinicia si cambia la recomendación
            estado = st.radio(
                "Decisión", ESTADOS_VALIDACION, index=ESTADOS_VALIDACION.index(vigente.get("estado", "Pendiente")),
                format_func={"Pendiente": "Pendiente", "Validada": "Validar", "Cambiada": "Cambiar"}.get,
                horizontal=True, key=f"decision_{sufijo}",
            )
            nota = st.text_input(
                "Indicación para el agricultor" if estado == "Cambiada" else "Nota para el agricultor (opcional)",
                vigente.get("nota", ""), key=f"nota_{sufijo}",
            )
            if estado == "Cambiada" and not nota.strip():
                st.warning("Escribe qué debe hacer el agricultor; sin indicación no se le envía nada.")
                validaciones.pop(zona, None)
            elif estado == "Pendiente":
                validaciones.pop(zona, None)
                st.caption("Retenida: el agricultor ve que su técnico todavía no la revisa.")
            else:
                if vigente.get("estado") != estado or vigente.get("nota") != nota:
                    vigente = {"texto": recomendacion["texto"], "estado": estado, "nota": nota,
                               "hora": pd.Timestamp.now().strftime("%H:%M")}
                validaciones[zona] = vigente
                st.success(f"{'Validada' if estado == 'Validada' else 'Cambiada'} a las {vigente['hora']}: ya la ve el agricultor.")
    zonas_validadas = {
        zona for zona in zonas_en_riesgo_alto
        if (v := validacion_vigente(zona, recomendaciones[zona])) and v["estado"] == "Validada"
    }

with tab_riesgo:
    st.subheader(f"Horas favorables al tizón tardío en las últimas {ventana_horas} horas, por zona")
    columnas = st.columns(len(riesgo))
    for columna, fila in zip(columnas, riesgo.itertuples()):
        columna.metric(
            fila.zona, f"{fila.horas_por_dia:.1f} h al día", f"Riesgo {fila.nivel.lower()}", delta_color="off"
        )
    st.caption(
        f"Hora favorable: humedad relativa de {umbral_humedad} % o más y temperatura entre {temp_min_riesgo} y "
        f"{temp_max_riesgo} °C. Riesgo alto desde 8 horas favorables al día, la duración mínima de un ciclo de "
        "infección en el estudio de referencia; medio de 4 a 8; bajo con menos de 4."
    )

    st.markdown("**Revisión humana antes de avisar**")
    if not zonas_en_riesgo_alto:
        st.info("Ninguna zona está en riesgo alto con los umbrales actuales.")
    for zona in zonas_en_riesgo_alto:
        if zona in zonas_validadas:
            st.success(f"{zona}: riesgo alto. Aviso validado y enviado al agricultor.")
        else:
            st.warning(f"{zona}: riesgo alto. Aviso retenido hasta que lo valides en la pestaña Validar recomendaciones.")

    grafica = go.Figure()
    for zona in ZONAS:
        datos_zona = lecturas[lecturas["zona"] == zona]
        grafica.add_trace(go.Scatter(x=datos_zona["momento"], y=datos_zona["humedad_aire"], mode="lines", name=zona))
    grafica.add_hline(y=umbral_humedad, line_dash="dash", annotation_text=f"Umbral de riesgo: {umbral_humedad} %")
    grafica.update_layout(
        title="Humedad del aire por zona (%)", height=380, margin={"l": 10, "r": 10, "t": 50, "b": 10},
        yaxis_range=[0, 100],
    )
    mostrar_grafica_fija(grafica)

    st.markdown("**Registro de revisión en parcela**")
    registros = st.session_state.setdefault("registros", [])
    with st.form("registro_revision", clear_on_submit=True):
        zona_registro = st.selectbox("Zona", ZONAS)
        con_sintomas = st.radio("¿Se encontraron síntomas?", ["No", "Sí"], horizontal=True)
        nota = st.text_input("Nota del productor o técnico")
        if st.form_submit_button("Guardar revisión"):
            guardar_revision(zona_registro, con_sintomas, nota, "Especialista")
    if registros:
        tabla_registros = pd.DataFrame(registros).drop(columns="id")
        tabla_registros.index = pd.RangeIndex(1, len(registros) + 1, name="Núm.")
        st.table(tabla_registros)
        with st.expander("Corregir o borrar un registro"):
            numero = st.selectbox("Número de registro", range(1, len(registros) + 1))
            actual = registros[numero - 1]
            clave = actual["id"]  # cada registro tiene su propio id para no mezclar los campos al borrar
            zona_nueva = st.selectbox("Zona", ZONAS, index=ZONAS.index(actual["zona"]), key=f"zona_{clave}")
            sintomas_nuevos = st.radio(
                "¿Se encontraron síntomas?", ["No", "Sí"], index=["No", "Sí"].index(actual["síntomas"]),
                horizontal=True, key=f"sintomas_{clave}",
            )
            nota_nueva = st.text_input("Nota", actual["nota"], key=f"nota_{clave}")
            col_guardar, col_borrar = st.columns(2)
            if col_guardar.button("Guardar cambios", key=f"guardar_{clave}"):
                actual.update({"zona": zona_nueva, "síntomas": sintomas_nuevos, "nota": nota_nueva})
                st.rerun()
            if col_borrar.button("Borrar registro", key=f"borrar_{clave}"):
                registros.pop(numero - 1)
                st.rerun()
    st.caption("Estos registros sirven para calibrar los umbrales con lo que pasa en campo.")

with tab_riego:
    st.subheader("Riego recomendado para hoy")
    tabla_riego = riego.rename(
        columns={
            "zona": "Zona", "humedad_suelo": "Humedad del suelo (%)",
            "litros_recomendados": "Litros recomendados", "ahorro_litros": "Ahorro (litros)",
        }
    ).set_index("Zona")
    tabla_riego[["Litros recomendados", "Ahorro (litros)"]] = tabla_riego[
        ["Litros recomendados", "Ahorro (litros)"]
    ].astype(int)
    tabla_riego["Humedad del suelo (%)"] = tabla_riego["Humedad del suelo (%)"].map("{:.1f}".format)
    st.table(tabla_riego)
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

with tab_ciclo:
    st.subheader("Ciclo del cultivo: riego por balance de agua y alertas de plagas")
    st.caption(
        f"{'Invernadero' if invernadero else 'Campo abierto'} · suelo {suelo.lower()} · {sistema_riego.lower()} · "
        f"trasplante el {a_fecha(ciclo['fecha'].iloc[0])} · clima: {origen_clima.lower()}. "
        "Con clima de ejemplo, el cálculo es una demostración y no un pronóstico."
    )
    n_dias = len(ciclo)
    dia = st.slider("Día del ciclo", 1, n_dias, min(60, n_dias)) - 1
    hoy = ciclo.iloc[dia]
    balance = simulaciones["Balance de agua"]["diario"]
    taw = SUELOS[suelo]
    raw = AGOTAMIENTO_PERMISIBLE * taw
    eficiencia = SISTEMAS_RIEGO[sistema_riego] / 100
    st.markdown(f"**{a_fecha(hoy['fecha'])}: día {dia + 1} de {n_dias}, {etapa_del_dia(dia).lower()}**")

    col_riego, col_suelo = st.columns(2)
    lamina_hoy = balance.at[dia, "riego"]
    if lamina_hoy > 0:
        m3_hoy = lamina_hoy / eficiencia * 10
        col_riego.metric("Hoy toca regar", f"{lamina_hoy:.1f} litros por m²")
        col_riego.caption(f"Con {sistema_riego.lower()} son unos {m3_hoy:,.0f} m³ por hectárea ({m3_hoy / 10:.1f} pipas de 10,000 litros).")
    else:
        col_riego.metric("Hoy no riegues", "0 litros por m²")
        if hoy["etc"] > 0:
            dias_faltan = max(1, math.ceil(max(0.0, raw - balance.at[dia, "deficit_inicial"]) / hoy["etc"]))
            col_riego.caption(
                f"El suelo todavía tiene agua. El próximo riego sería el "
                f"{a_fecha(hoy['fecha'] + pd.Timedelta(days=dias_faltan))}, en unos {dias_faltan} día(s)."
            )
    agua_suelo = min(100.0, max(0.0, 100 * (taw - balance.at[dia, "deficit_inicial"]) / taw))
    col_suelo.metric("Agua en el suelo", f"{agua_suelo:.0f} %")
    col_suelo.progress(agua_suelo / 100)
    col_suelo.caption(f"Toca regar cuando baja de {100 * (1 - AGOTAMIENTO_PERMISIBLE):.0f} %.")
    if hoy["lluvia_efectiva"] > 0:
        st.info(f"Hoy llovió {hoy['lluvia']:.0f} mm. Eso ya cuenta en el cálculo.")

    st.markdown("**Plagas y enfermedades**")
    plagas_altas = []
    for columna, plaga in zip(st.columns(len(PLAGAS)), PLAGAS):
        valor = indices.at[dia, plaga]
        nivel = nivel_plaga(valor)
        if nivel == "Alto":
            plagas_altas.append(plaga)
        columna.metric(plaga, f"{valor:.0f} de 100", f"Riesgo {nivel.lower()}", delta_color="off")
        columna.caption(f"Qué revisar: {PLAGAS[plaga]['revisar']}")
    if plagas_altas:
        st.warning(
            f"Riesgo alto de {' y '.join(p.lower() for p in plagas_altas)}. Revisar plantas y trampas hoy y "
            "consultar al técnico antes de aplicar cualquier producto."
        )
    st.caption("El índice avisa cuándo revisar; no confirma que haya plaga.")

    st.markdown("**Validación del técnico**")
    bitacora = st.session_state.setdefault("bitacora_ciclo", {})
    recomendacion = f"Regar {lamina_hoy:.1f} mm" if lamina_hoy > 0 else "No regar"
    aviso_plagas = "Monitorear" if plagas_altas else "Sin alerta"
    estados = ["Pendiente", "Validada", "Modificada"]
    guardado = bitacora.get(dia, {})
    estado = st.radio(
        f"Recomendación del {a_fecha(hoy['fecha'])}: {recomendacion.lower()}; plagas: {aviso_plagas.lower()}",
        estados, index=estados.index(guardado.get("Estado", "Pendiente")), horizontal=True, key=f"estado_ciclo_{dia}",
    )
    nota_ciclo = st.text_input("Nota del técnico", guardado.get("Nota", ""), key=f"nota_ciclo_{dia}")
    if estado != "Pendiente" or nota_ciclo:
        bitacora[dia] = {
            "Fecha": a_fecha(hoy["fecha"]), "Riego": recomendacion, "Plagas": aviso_plagas,
            "Estado": estado, "Nota": nota_ciclo,
        }
    else:
        bitacora.pop(dia, None)
    if bitacora:
        st.table(pd.DataFrame([bitacora[d] for d in sorted(bitacora)]).set_index("Fecha"))

    st.markdown("**Los próximos 7 días**")
    semana = range(dia, min(n_dias, dia + 7))
    st.table(
        pd.DataFrame(
            [
                {
                    "Fecha": a_fecha(ciclo.at[j, "fecha"]),
                    "Riego (mm)": f"{balance.at[j, 'riego']:.1f}" if balance.at[j, "riego"] > 0 else "—",
                    "Lluvia (mm)": f"{ciclo.at[j, 'lluvia']:.0f}" if ciclo.at[j, "lluvia"] > 0 else "—",
                    "Riesgo alto de plagas": ", ".join(p for p in PLAGAS if indices.at[j, p] >= umbral_alto_plagas) or "—",
                }
                for j in semana
            ]
        ).set_index("Fecha")
    )

    st.markdown("**¿Cuánta agua se ahorra en todo el ciclo?**")
    filas_estrategias = []
    for estrategia, sim in simulaciones.items():
        fila = {
            "Estrategia": estrategia,
            "Agua (m³ por ha)": f"{sim['m3_ha']:,.0f}",
            "Riegos": sim["riegos"],
            "Días con estrés": sim["dias_estres"],
            "Rendimiento relativo (%)": f"{sim['rendimiento'] * 100:.0f}",
        }
        if costo_agua > 0:
            fila["Costo del agua (MXN por ha)"] = f"{sim['m3_ha'] * costo_agua:,.0f}"
        filas_estrategias.append(fila)
    st.table(pd.DataFrame(filas_estrategias).set_index("Estrategia"))
    actual, recomendado = simulaciones["Riego actual"], simulaciones["Balance de agua"]
    diferencia = actual["m3_ha"] - recomendado["m3_ha"]
    cambio_cosecha = (recomendado["rendimiento"] - actual["rendimiento"]) * 100
    if diferencia >= 0.03 * actual["m3_ha"]:
        cosecha = "sin bajar la cosecha estimada" if abs(cambio_cosecha) <= 0.5 else (
            "y la cosecha estimada mejora" if cambio_cosecha > 0 else "con una pequeña baja en la cosecha estimada"
        )
        st.success(
            f"Con el balance de agua se usan {diferencia:,.0f} m³ menos por hectárea "
            f"({diferencia / actual['m3_ha'] * 100:.0f} % menos), {cosecha}."
        )
    elif diferencia <= -0.03 * actual["m3_ha"]:
        st.warning(
            f"En este ciclo el riego actual se queda corto: el balance de agua usa {-diferencia:,.0f} m³ más por "
            "hectárea, pero la planta no pasa sed."
        )
    else:
        st.info("El riego actual y el balance de agua gastan casi lo mismo.")
    st.caption(
        "Rendimiento relativo con la relación de Doorenbos y Kassam: 1 − Ky × (1 − ET real ÷ ET del cultivo). "
        "El riego deficitario espera a que el suelo se seque más y acepta algo de estrés."
    )

    grafica_suelo = go.Figure()
    for estrategia, sim in simulaciones.items():
        grafica_suelo.add_trace(
            go.Scatter(
                x=ciclo["fecha"], y=100 * (taw - sim["diario"]["deficit_inicial"]) / taw, mode="lines", name=estrategia
            )
        )
    grafica_suelo.add_hline(
        y=100 * (1 - AGOTAMIENTO_PERMISIBLE), line_dash="dash", annotation_text="Límite sin estrés"
    )
    grafica_suelo.add_vline(x=hoy["fecha"], line_dash="dot", line_color="gray")
    grafica_suelo.update_layout(
        title="Agua en el suelo durante el ciclo (%)", height=380, margin={"l": 10, "r": 10, "t": 50, "b": 10},
        yaxis_range=[0, 100],
    )
    mostrar_grafica_fija(grafica_suelo)

    grafica_plagas = go.Figure()
    for plaga in PLAGAS:
        grafica_plagas.add_trace(go.Scatter(x=ciclo["fecha"], y=indices[plaga], mode="lines", name=plaga))
    grafica_plagas.add_hline(y=umbral_medio_plagas, line_dash="dot", annotation_text="Riesgo medio")
    grafica_plagas.add_hline(y=umbral_alto_plagas, line_dash="dash", annotation_text="Riesgo alto")
    grafica_plagas.add_vline(x=hoy["fecha"], line_dash="dot", line_color="gray")
    grafica_plagas.update_layout(
        title="Índice de riesgo de plagas (0 a 100)", height=380, margin={"l": 10, "r": 10, "t": 50, "b": 10},
        yaxis_range=[0, 100],
    )
    mostrar_grafica_fija(grafica_plagas)

    alertas = periodos_de_alerta(indices, ciclo["fecha"])
    if alertas.empty:
        st.caption("Ninguna plaga llega a riesgo alto en este ciclo.")
    else:
        st.markdown("**Periodos con riesgo alto en el ciclo**")
        st.table(alertas)
    st.caption(
        "El modelo no considera la salinidad, los conteos de trampas, el pronóstico de lluvia ni la humedad "
        "medida en campo. Los valores no están calibrados para Morelos."
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
    filas_comparacion = [
        ("Viajes a la Central", f"{ind['viajes']}", f"{com['viajes']}"),
        ("Kilómetros, ida y vuelta", f"{ind['km']:,.0f}", f"{com['km']:,.0f}"),
        ("Costo de flete por kilo (MXN)", f"{ind['costo'] / kg_total:.2f}", f"{com['costo'] / kg_total:.2f}"),
        ("Ocupación de vehículos (%)", f"{ind['ocupacion'] * 100:.0f}", f"{com['ocupacion'] * 100:.0f}"),
        ("Horas de la carga a la Central", f"{ind['horas']:.1f}", f"{com['horas']:.1f}"),
        ("Kilos en riesgo al llegar", f"{ind['kg_riesgo']:,.0f}", f"{com['kg_riesgo']:,.0f}"),
    ]
    st.table(pd.DataFrame(filas_comparacion, columns=["Indicador", "Por su lado", "Ruta fresca"]).set_index("Indicador"))

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
        st.table(
            pd.DataFrame(
                [
                    {
                        "Orden": i,
                        "Recolección": a_hora(p["recoleccion"]),
                        "Productor": p["nombre"],
                        "Municipio": p["municipio"],
                        "Kg": f"{p['kg']:,.0f}",
                        "Carga lista": p["hora_corte"],
                        "Vida restante (h)": f"{max(0, p['vida_restante']):.1f}",
                        "Estado": estado_frescura(p),
                    }
                    for i, p in enumerate(camion["paradas"], start=1)
                ]
            ).set_index("Orden")
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
    mostrar_grafica_fija(mapa)

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
- **Umbrales de tizón tardío:** humedad relativa de 80 a 100 % y temperatura de 10 a 16.7 °C durante 8 a 13 horas
  de noche y madrugada, según Delesma-Morales y colaboradores, *Revista Mexicana de Fitopatología*, 2020
  (https://www.scielo.org.mx/scielo.php?script=sci_arttext&pid=S0185-33092020000100103).
  El estudio es de Chapingo, Estado de México, a 2,250 m de altitud; no es de Morelos y hay que calibrarlo en campo.
- **Producción de jitomate en Morelos:** 201,721 t y quinto lugar nacional en el cierre agrícola 2023 (SIAP).
  Pendiente de abrir la fuente original.
- **Municipios de riego:** Cuautla, Ayala y Tepalcingo aparecen como zonas de riego en una nota de prensa de 2019.
  Pendiente de confirmar con SIAP o Sedagro.
- **Lecturas de sensores:** simuladas. Las diferencias entre zonas son inventadas para la demostración.
- **Cargas, vida útil, costos y velocidades:** supuestos del equipo, editables en pantalla.
- **Cálculo de rutas:** adaptado del demo Ruta fresca del equipo; usa línea recta por un factor de camino.
- **Ciclo del cultivo:** evapotranspiración de referencia con Hargreaves-Samani y balance de agua en la zona de raíces
  según FAO-56 (Allen y colaboradores, 1998); rendimiento relativo con Doorenbos y Kassam (FAO-33, 1979).
  Duraciones de etapas, Kc, agotamiento permisible y Ky son valores de referencia escritos de memoria: confirmar con
  las tablas 11, 12 y 22 de FAO-56 o con INIFAP para Morelos.
- **Clima del ciclo:** clima mensual aproximado del oriente de Morelos con variación inventada. Para usar datos reales,
  subir un CSV diario (por ejemplo, de una estación del SMN o de la red agroclimática).
- **Índices de plagas del ciclo:** rangos de temperatura y umbrales de ejemplo; validar con un fitopatólogo.
"""
    )
