import requests
import logging
from influxdb_client import InfluxDBClient
import time
from datetime import datetime, timedelta
import sys
import mysql.connector
import csv
import json
import os
import shutil

# --- LIBRERÍAS NUEVAS PARA DIBUJAR LA GRÁFICA ---
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as ticker
# -----------------------------------------------

# ==========================================
# 0. CONFIGURACIÓN DE LA CAJA NEGRA (LOGGING)
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("historial_motor.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout) # reproducir proceso en consola
    ]
)

logging.info("==================================================")
logging.info("⚙️ INICIANDO MOTOR DE REPORTES INDUSTRIALES HAAS VF-9")
logging.info("==================================================")
tiempo_inicio_total = time.time()

# ==========================================
# 1. CREDENCIALES Y RUTAS
# ==========================================
# Las credenciales NUNCA deben quedar escritas en el código fuente:
# se leen de variables de entorno y el proceso falla rápido si faltan.
def _requerido(nombre_env):
    valor = os.environ.get(nombre_env)
    if not valor:
        logging.error(f"Falta la variable de entorno obligatoria: {nombre_env}")
        sys.exit(1)
    return valor

TELEGRAM_TOKEN = _requerido("TELEGRAM_TOKEN")
CHAT_ID = _requerido("TELEGRAM_CHAT_ID")

INFLUX_URL = os.environ.get("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = _requerido("INFLUX_TOKEN")
INFLUX_ORG = os.environ.get("INFLUX_ORG", "noramex")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "haas_vf9_energy")

GRAFANA_TOKEN = _requerido("GRAFANA_TOKEN")
GRAFANA_DASHBOARD_URL = os.environ.get(
    "GRAFANA_DASHBOARD_URL", "http://localhost:3000/api/dashboards/uid/adv9hsz"
)

MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = _requerido("MYSQL_PASSWORD")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "ems_noramex")

RUTA_TARIFAS_CFE = os.environ.get(
    "RUTA_TARIFAS_CFE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "tarifas_cfe.json")
)

# ==========================================
# 1b. UMBRALES DE OPERACIÓN
# ==========================================
# Por debajo de estos valores se considera que la máquina NO trabajó
# en el bloque de tiempo (y no un valor real de bajo consumo).
UMBRAL_KWH_MINIMO = 0.05        # kWh mínimos para considerar actividad real
UMBRAL_AMPERAJE_MINIMO = 1.0    # Pico de amperaje mínimo para considerar actividad real

# ==========================================
# 2. LÓGICA DE TURNOS, TIEMPOS Y COMPORTAMIENTO
# ==========================================
logging.info("[PASO 1/7] Calculando ventanas de tiempo y horarios locales...")
ahora = datetime.now()
hora_actual = ahora.hour
logging.info(f"Hora del servidor detectada: {ahora.strftime('%Y-%m-%d %H:%M:%S')}")

# Banderas de control maestro
guardar_en_db = True
enviar_telegram = True

if 18 <= hora_actual <= 19:
    # CORTE NORMAL DIA (6am - 6pm)
    inicio = ahora.replace(hour=6, minute=0, second=0, microsecond=0)
    fin = ahora.replace(hour=18, minute=0, second=0, microsecond=0)
    nombre_turno = "Turno de DIA ☀️"

elif 22 <= hora_actual <= 23:
    # REPORTE FLASH GERENCIAL (6pm - 10pm)
    inicio = ahora.replace(hour=18, minute=0, second=0, microsecond=0)
    fin = ahora.replace(hour=22, minute=0, second=0, microsecond=0)
    nombre_turno = "Corte Parcial (6PM - 10PM) ⏱️"
    guardar_en_db = False # Protegemos la base de datos de cortes incompletos

elif 6 <= hora_actual <= 7:
    # CORTE NORMAL NOCHE (6pm - 6am)
    inicio = (ahora - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    fin = ahora.replace(hour=6, minute=0, second=0, microsecond=0)
    nombre_turno = "Turno de NOCHE 🌙"

else:
    # EJECUCIÓN MANUAL / FUERA DE HORARIO
    inicio = ahora.replace(hour=6, minute=0, second=0, microsecond=0)
    fin = ahora.replace(hour=18, minute=0, second=0, microsecond=0)
    nombre_turno = "Prueba Manual 🔧"
    guardar_en_db = False

ts_inicio = int(inicio.timestamp())
ts_fin = int(fin.timestamp())
logging.info(f"Rango de consulta: {inicio} a {fin} | Turno: {nombre_turno}")
logging.info(f"Configuración -> Guardar BD: {guardar_en_db} | Enviar Telegram: {enviar_telegram}")

# ==========================================
# 3. TARIFAS CFE (Grafana + caché local de respaldo)
# ==========================================
# Gerencia sigue editando el precio en las variables del dashboard de
# Grafana (es la única forma que tienen de cambiarlo sin tocar código).
# Pero esa API falla con frecuencia, así que:
#   1) Primero se carga la última tarifa guardada en tarifas_cfe.json.
#   2) Se intenta refrescarla contra Grafana; si responde bien, se
#      actualizan los precios en memoria Y se reescribe el JSON.
#   3) Si Grafana falla por cualquier motivo, se sigue usando lo que ya
#      había en el JSON (no se vuelve a los defaults "de fábrica").
logging.info("[PASO 2/7] Cargando tarifas CFE (caché local + Grafana)...")
precios_cfe = {"Base": 1.15, "Intermedia": 2.00, "Punta": 5.00}
tarifas_actualizado = None

try:
    with open(RUTA_TARIFAS_CFE, "r", encoding="utf-8") as f:
        cache_tarifas = json.load(f)
    for clave in ("Base", "Intermedia", "Punta"):
        if clave in cache_tarifas:
            precios_cfe[clave] = float(cache_tarifas[clave])
    tarifas_actualizado = cache_tarifas.get("actualizado")
    logging.info(f"Caché local cargada ({tarifas_actualizado or 'sin fecha registrada'}): {precios_cfe}")
except FileNotFoundError:
    logging.warning(f"No existe {RUTA_TARIFAS_CFE} todavía. Se parte de los precios default: {precios_cfe}")
except (json.JSONDecodeError, TypeError, ValueError) as e:
    logging.error(f"'{RUTA_TARIFAS_CFE}' está corrupto ({e}). Se parte de los precios default: {precios_cfe}")

try:
    resp_dashboard = requests.get(
        GRAFANA_DASHBOARD_URL, headers={"Authorization": GRAFANA_TOKEN}, timeout=15
    )
    resp_dashboard.raise_for_status()
    variables = resp_dashboard.json().get("dashboard", {}).get("templating", {}).get("list", [])

    mapa_variables = {"tarifa_base": "Base", "tarifa_intermedia": "Intermedia", "tarifa_punta": "Punta"}
    tarifas_grafana = {}
    for var in variables:
        clave = mapa_variables.get(var.get("name"))
        if clave is None:
            continue
        valor = var.get("current", {}).get("value")
        if isinstance(valor, list):
            valor = valor[0] if valor else ""
        tarifas_grafana[clave] = float(str(valor).strip().replace("$", "").replace(",", ""))

    faltantes = [c for c in ("Base", "Intermedia", "Punta") if c not in tarifas_grafana]
    if faltantes:
        raise ValueError(f"Grafana no devolvió las variables: {faltantes}")

    precios_cfe.update(tarifas_grafana)
    tarifas_actualizado = ahora.isoformat()

    # Escritura atómica: primero a un archivo temporal y luego rename,
    # para no dejar el JSON de respaldo a medio escribir si el proceso
    # se interrumpe (y el respaldo deja de ser confiable).
    tmp_path = RUTA_TARIFAS_CFE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({**precios_cfe, "actualizado": tarifas_actualizado}, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, RUTA_TARIFAS_CFE)

    logging.info(f"Tarifas actualizadas desde Grafana y guardadas en caché: {precios_cfe}")

except Exception as e:
    logging.warning(
        f"No se pudieron leer las tarifas desde Grafana ({e}). "
        f"Se usan los últimos valores en caché ({tarifas_actualizado or 'sin fecha registrada'}): {precios_cfe}"
    )

# ==========================================
# 4. EXTRACCIÓN DE DATOS INFLUXDB
# ==========================================
logging.info("[PASO 3/7] Extrayendo telemetría de InfluxDB...")
cliente_db = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG, timeout=60000)
query_api = cliente_db.query_api()

def obtener_valor(q):
    try:
        res = query_api.query(org=INFLUX_ORG, query=q)
        return round(res[0].records[0].get_value(), 2) if res and res[0].records else 0.0
    except Exception as e:
        logging.warning(f"Fallo consulta InfluxDB, se usa 0.0: {e}")
        return 0.0

rango = f"|> range(start: {ts_inicio}, stop: {ts_fin})"
pico_amperaje = obtener_valor(f'from(bucket: "{INFLUX_BUCKET}") {rango} |> filter(fn: (r) => r["_field"] == "corriente") |> max()')
voltaje_min = obtener_valor(f'from(bucket: "{INFLUX_BUCKET}") {rango} |> filter(fn: (r) => r["_field"] == "voltaje") |> min()')
voltaje_max = obtener_valor(f'from(bucket: "{INFLUX_BUCKET}") {rango} |> filter(fn: (r) => r["_field"] == "voltaje") |> max()')
puntos_totales = obtener_valor(f'from(bucket: "{INFLUX_BUCKET}") {rango} |> filter(fn: (r) => r["_field"] == "corriente") |> count()')
puntos_standby = obtener_valor(f'from(bucket: "{INFLUX_BUCKET}") {rango} |> filter(fn: (r) => r["_field"] == "corriente") |> filter(fn: (r) => r["_value"] < 2.0) |> count()')

costo_total = 0.0
kwh_total = 0.0
kwh_base, kwh_inter, kwh_punta = 0.0, 0.0, 0.0

logging.info("Calculando desglose energético GDMTH por horas locales...")
ahora_ts = int(datetime.now().timestamp())
limite_ts = min(ts_fin, ahora_ts)

for h in range(12):
    t_bloque_inicio = inicio + timedelta(hours=h)
    t_bloque_fin = t_bloque_inicio + timedelta(hours=1)

    r_ini = int(t_bloque_inicio.timestamp())
    r_fin = int(t_bloque_fin.timestamp())

    if r_ini >= limite_ts: continue
    r_fin = min(r_fin, limite_ts)

    hora_h = t_bloque_inicio.hour
    tarifa_actual = "Intermedia"

    if "DIA" in nombre_turno or "Corte" in nombre_turno:
        tarifa_actual = "Intermedia"
    else:
        if 0 <= hora_h < 6: tarifa_actual = "Base"
        elif 20 <= hora_h < 22: tarifa_actual = "Punta"
        else: tarifa_actual = "Intermedia"

    q_bloque = f'from(bucket: "{INFLUX_BUCKET}") |> range(start: {r_ini}, stop: {r_fin}) |> filter(fn: (r) => r["_field"] == "energia") |> spread()'
    k_bloque = obtener_valor(q_bloque) * 3.0

    kwh_total += k_bloque
    costo_total += (k_bloque * precios_cfe[tarifa_actual])

    if tarifa_actual == "Base": kwh_base += k_bloque
    elif tarifa_actual == "Intermedia": kwh_inter += k_bloque
    else: kwh_punta += k_bloque

minutos_totales = (limite_ts - ts_inicio) / 60
minutos_totales = max(0, minutos_totales)
horas_standby = round((puntos_standby / puntos_totales * minutos_totales / 60) if puntos_totales > 0 else 0, 1)
minutos_standby = int(horas_standby * 60)
oee_real = round(((minutos_totales - minutos_standby) / minutos_totales * 100), 2) if minutos_totales > 0 else 0

# --- LÓGICA DE MICROPAROS Y PAROS PROLONGADOS ---
RES_SEGS = 60
PUNTOS_MIN = int(60 / RES_SEGS)

q_serie_corriente = f'from(bucket: "{INFLUX_BUCKET}") {rango} |> filter(fn: (r) => r["_field"] == "corriente") |> aggregateWindow(every: {RES_SEGS}s, fn: max)'
serie_corriente = []
try:
    res_serie = query_api.query(org=INFLUX_ORG, query=q_serie_corriente)
    for table in res_serie:
        for record in table.records:
            serie_corriente.append(record.get_value())
except Exception as e:
    logging.warning(f"No se pudo obtener la serie de corriente para el gráfico/microparos: {e}")

paros_prolongados = 0
umbral_paro_largo = 20 * PUNTOS_MIN
contador_consecutivo = 0

for v in serie_corriente:
    if v is not None and v < 2.0:
        contador_consecutivo += 1
    else:
        if contador_consecutivo >= umbral_paro_largo:
            paros_prolongados += contador_consecutivo
        contador_consecutivo = 0

if contador_consecutivo >= umbral_paro_largo:
    paros_prolongados += contador_consecutivo

microparos_minutos = max(0, minutos_standby - (paros_prolongados / PUNTOS_MIN))

kwh_total = round(kwh_total, 2)
costo_total = round(costo_total, 2)
logging.info(f"Resultados: {kwh_total} kWh | ${costo_total} MXN | OEE: {oee_real}%")

# ==========================================
# 5. CONDICIÓN DE INACTIVIDAD (INTELIGENTE)
# ==========================================
logging.info("[PASO 4/7] Verificando actividad de la máquina...")

sin_datos_sensor = puntos_totales == 0
maquina_inactiva = kwh_total <= UMBRAL_KWH_MINIMO and pico_amperaje < UMBRAL_AMPERAJE_MINIMO

if maquina_inactiva:
    if sin_datos_sensor:
        logging.warning("⚠️ No se recibieron datos del sensor/PLC en este bloque de tiempo.")
    else:
        logging.warning("⚠️ Máquina Inactiva detectada en este bloque de tiempo (consumo mínimo).")

    # Si es el reporte de las 6 AM y no trabajaron, silenciamos Telegram
    if 6 <= hora_actual <= 7:
        logging.info(" -> Turno nocturno inactivo. Se silencia Telegram para no generar spam.")
        enviar_telegram = False

    # Nota: Si es el reporte de las 10 PM, el gerente SÍ recibirá la alerta de inactividad
    # Se eliminó el sys.exit(0) para permitir que el script guarde la inactividad en BD al final

# ==========================================
# 6. ALERTAS Y RECOMENDACIONES PREDEFINIDAS
# ==========================================
logging.info("[PASO 5/7] Generando alertas y recomendaciones técnicas...")

generar_grafico = True

if maquina_inactiva:
    # Consumo mínimo: en vez del reporte completo de parámetros, se envía
    # una alerta corta para gerencia indicando que no hubo mediciones relevantes.
    generar_grafico = False

    if sin_datos_sensor:
        motivo = "no se recibieron mediciones del sistema de monitoreo (posible falla de sensor/conectividad)"
    else:
        motivo = f"el consumo registrado fue prácticamente nulo (pico de {pico_amperaje} A, {kwh_total} kWh)"

    reporte_texto = f"""
🔕 *ALERTA DE MONITOREO EMS — SIN ACTIVIDAD DETECTADA*

📅 *Fecha:* {ahora.strftime('%d/%m/%Y')}
🕒 *Turno:* {nombre_turno}
⚙️ *Máquina:* HAAS VF-9

No se generó el reporte de parámetros porque {motivo}.

Esto indica que la máquina permaneció *apagada o sin actividad productiva* durante este periodo.

💡 *RECOMENDACIÓN:*
Validar con el área de producción el motivo del paro (falta de programa, mantenimiento, sin operador asignado, falla de sensor, etc.).
"""
else:
    alertas = []
    recomendacion = "Operación estable. Mantener programa de producción actual."

    if oee_real < 50:
        alertas.append("🔴 BAJA EFICIENCIA OPERATIVA")
        recomendacion = f"Se detectaron {horas_standby} horas de inactividad (Standby). Se recomienda revisar disponibilidad de operadores o falta de material."
    if 0 < voltaje_min < 210:
        alertas.append("⚠️ BAJO VOLTAJE DETECTADO")
        recomendacion = f"Voltaje mínimo registrado de {voltaje_min}V. Riesgo de daño en variadores y servomotores."
    if pico_amperaje > 60:
        alertas.append("⚡ PICO DE CORRIENTE ELEVADO")
        recomendacion = f"Pico máximo de {pico_amperaje}A. Revisar desgaste en herramientas de corte."
    if kwh_punta > 0 and ("NOCHE" in nombre_turno or "Corte" in nombre_turno):
        alertas.append("💸 CONSUMO EN HORARIO PUNTA")
        recomendacion = f"Se detectó consumo en el bloque más caro de CFE. Cuidado con desbastes pesados."

    str_alertas = "\n".join(alertas) if alertas else "✅ Sin anomalías críticas detectadas."

    reporte_texto = f"""
🤖 *REPORTE DE SISTEMA DE MONITOREO EMS*

📅 *Fecha:* {ahora.strftime('%d/%m/%Y')}
🕒 *Turno:* {nombre_turno}
⚙️ *Máquina:* HAAS VF-9

⚡ *ENERGÍA Y COSTOS:*
• Consumo Total: {kwh_total} kWh
• Costo Estimado: ${costo_total} MXN
• Desglose CFE: Base: {round(kwh_base,1)} | Int: {round(kwh_inter,1)} | Punta: {round(kwh_punta,1)}

📊 *ESTADO OPERATIVO:*
• OEE Real: {oee_real}%
• Horas Standby: {horas_standby} hrs
• Microparos (<20m): {microparos_minutos} min
• Paros Largos (>20m): {paros_prolongados} min
• Pico Corriente: {pico_amperaje} A
• Voltaje (Min/Max): {voltaje_min}V / {voltaje_max}V

⚠️ *ALERTAS:*
{str_alertas}

💡 *RECOMENDACIÓN:*
{recomendacion}
"""

# ==========================================
# 7. GENERACIÓN DE GRÁFICO (MATPLOTLIB PRO)
# ==========================================
nombre_foto = "reporte_grafana.png"
foto_lista = False

if generar_grafico:
    logging.info("[PASO 6/7] Generando gráfico de alta resolución...")
    try:
        MULTIPLICADOR_AMPERAJE = 1.0
        UMBRAL_STANDBY = 2.0

        tiempo_x = [inicio + timedelta(seconds=i*RES_SEGS) for i in range(len(serie_corriente))]

        serie_limpia = []
        for valor in serie_corriente:
            try:
                if valor is None:
                    serie_limpia.append(0.0)
                else:
                    serie_limpia.append(float(valor) * MULTIPLICADOR_AMPERAJE)
            except (ValueError, TypeError):
                serie_limpia.append(0.0)

        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(12, 6))

        ax.plot(tiempo_x, serie_limpia, color='#00FFFF', linewidth=1.2, zorder=3)
        ax.fill_between(tiempo_x, serie_limpia, color='#00FFFF', alpha=0.2, zorder=3)

        ax.axhspan(ymin=0, ymax=UMBRAL_STANDBY, color='#FF4500', alpha=0.15, zorder=1)
        ax.axhline(y=UMBRAL_STANDBY, color='#FF4500', linestyle='--', linewidth=1.5, alpha=0.8, zorder=2)
        bbox_props = dict(boxstyle="round,pad=0.3", fc="#000000", ec="#FF4500", alpha=0.7)

        if len(tiempo_x) > 0:
            ax.text(tiempo_x[0], UMBRAL_STANDBY + 0.1, f'Límite de Standby ({UMBRAL_STANDBY} A)',
                    color='#FF4500', fontsize=10, fontweight='bold', bbox=bbox_props, zorder=4)

        max_amp = max(serie_limpia) if serie_limpia else 0
        limite_superior = max_amp * 1.2 if max_amp > 0 else 10

        ax.set_ylim(bottom=0, top=limite_superior)
        ax.yaxis.set_major_locator(ticker.MaxNLocator(10))

        ax.xaxis.set_major_locator(mdates.HourLocator(interval=1))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        ax.xaxis.set_minor_locator(mdates.MinuteLocator(byminute=[30]))

        ax.grid(True, which='major', color='#444444', linestyle='-', alpha=0.6, zorder=0)
        ax.grid(True, which='minor', color='#333333', linestyle=':', alpha=0.3, zorder=0)

        ax.set_title(f"Perfil de Consumo Eléctrico - HAAS VF-9\n{nombre_turno}", fontsize=16, fontweight='bold', pad=15)
        ax.set_ylabel("Corriente (Amperes)", fontsize=13, fontweight='bold')
        fig.autofmt_xdate(rotation=45)

        plt.tight_layout()
        plt.savefig(nombre_foto, dpi=300)
        plt.close()

        foto_lista = True
        logging.info(" -> ✅ Gráfico de Alta Resolución generado.")

    except Exception as e:
        logging.error(f" -> [!] Falló la generación del gráfico: {e}")
else:
    logging.info("[PASO 6/7] Sin actividad detectada: se omite el gráfico y se envía solo la alerta.")

# ==========================================
# ENVÍO A TELEGRAM
# ==========================================
if enviar_telegram:
    logging.info("Enviando datos a Telegram...")
    try:
        url_tel_foto = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        url_tel_texto = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

        if foto_lista:
            with open(nombre_foto, "rb") as f:
                requests.post(url_tel_foto, data={"chat_id": CHAT_ID, "message_thread_id": 42, "caption": "📊 Gráfico de Consumo Energético"}, files={"photo": f}, timeout=60)

        requests.post(url_tel_texto, data={"chat_id": CHAT_ID, "message_thread_id": 42, "text": reporte_texto, "parse_mode": "Markdown"}, timeout=60)
        logging.info("✅ Reporte entregado en Telegram.")

    except Exception as e:
        logging.error(f"❌ Error enviando a Telegram: {e}")
else:
    logging.info("⏭️ OMITIDO: Envío a Telegram cancelado (Inactividad nocturna).")

if os.path.exists(nombre_foto):
    os.remove(nombre_foto)

# ==========================================
# 8. GUARDADO EN BASES DE DATOS (SQL + CSV)
# ==========================================
if guardar_en_db:
    logging.info("[PASO 7/7] Sincronizando registros históricos en Base de Datos...")

    fecha_sql = ahora.strftime('%Y-%m-%d')
    turno_limpio = nombre_turno.replace("☀️", "").replace("🌙", "").replace("⏱️", "").strip()
    valores = ("HAAS VF-9", fecha_sql, turno_limpio, kwh_total, costo_total, pico_amperaje, horas_standby, microparos_minutos, paros_prolongados, oee_real)

    try:
        logging.info(" -> Intentando guardar en Base de Datos Local (MySQL)...")
        conexion_local = mysql.connector.connect(host=MYSQL_HOST, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DATABASE)
        cursor_local = conexion_local.cursor()

        cursor_local.execute("""
        CREATE TABLE IF NOT EXISTS historial_turnos (
            id INT AUTO_INCREMENT PRIMARY KEY, maquina VARCHAR(50), fecha DATE, turno VARCHAR(50),
            kwh_total FLOAT, costo_mxn FLOAT, pico_amperaje FLOAT, standby_horas FLOAT,
            microparos_minutos INT, paros_prolongados_minutos INT, oee_porcentaje FLOAT
        )""")

        sql_insert = """INSERT INTO historial_turnos (maquina, fecha, turno, kwh_total, costo_mxn, pico_amperaje, standby_horas, microparos_minutos, paros_prolongados_minutos, oee_porcentaje) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""

        cursor_local.execute(sql_insert, valores)
        conexion_local.commit()
        logging.info("    ✅ ÉXITO: Registro guardado en MySQL Local.")

    except Exception as e:
        logging.error(f"    ❌ ERROR: No se pudo guardar en MySQL: {e}")
    finally:
        if 'conexion_local' in locals() and conexion_local.is_connected():
            cursor_local.close()
            conexion_local.close()

    try:
        logging.info(" -> Extrayendo historial completo para archivo CSV maestro...")
        ruta_local = "historial_maestro_noramex.csv"
        ruta_red = r"\\192.168.1.1\sgc\12-MANTENIMIENTO\HISTORIAL DE ESTADO OPERATIVO EMS\historial_energia_noramex.csv"

        conexion_export = mysql.connector.connect(host=MYSQL_HOST, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DATABASE)
        cursor_export = conexion_export.cursor()
        cursor_export.execute("SELECT * FROM historial_turnos")
        registros = cursor_export.fetchall()
        nombres_columnas = [i[0] for i in cursor_export.description]

        with open(ruta_local, mode='w', newline='', encoding='utf-8-sig') as archivo_csv:
            escritor_csv = csv.writer(archivo_csv)
            escritor_csv.writerow(nombres_columnas)
            escritor_csv.writerows(registros)

        logging.info("    ✅ ÉXITO: CSV local recreado como un espejo exacto de MySQL.")
        cursor_export.close()
        conexion_export.close()

        shutil.copy2(ruta_local, ruta_red)
        logging.info(f"    ✅ ÉXITO: Archivo sincronizado en red corporativa: {ruta_red}")

    except IOError as e:
        logging.warning(f"    ⚠️ ADVERTENCIA: CSV extraído, pero falló sincronización en red. Detalle: {e}")
    except Exception as e:
        logging.error(f"    ❌ ERROR DESCONOCIDO en extracción a CSV: {e}")
else:
    logging.info("⏭️ OMITIDO: Guardado en BD saltado (Reporte Parcial de 10 PM o Prueba Manual).")

tiempo_final = round(time.time() - tiempo_inicio_total, 2)
logging.info("==================================================")
logging.info(f"🏁 PROCESO TERMINADO EN {tiempo_final} SEGUNDOS.")
logging.info("==================================================")
