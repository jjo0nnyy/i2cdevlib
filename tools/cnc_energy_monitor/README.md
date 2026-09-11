# Reporte de energía HAAS VF-9

Script de monitoreo de consumo eléctrico que consulta InfluxDB,
calcula costos por tarifa CFE (Grafana + caché local de respaldo)
y envía un reporte a Telegram.

## Configuración

Las credenciales se pegan directamente en las constantes al inicio del
script (`TELEGRAM_TOKEN`, `CHAT_ID`, `INFLUX_TOKEN`, `GRAFANA_TOKEN`,
`MYSQL_PASSWORD`, etc.), igual que antes — reemplaza los placeholders
`PON_AQUI_TU_...` por tus valores reales.

`pip install -r requirements.txt` instala las dependencias
(`requests`, `influxdb-client`, `mysql-connector-python`, `matplotlib`).

## Resiliencia de la infraestructura (PC + Docker)

Este script depende de que el PC esté encendido y de que InfluxDB/Grafana/
MySQL (corriendo en Docker) estén disponibles. Dos configuraciones
recomendadas para que se recupere solo:

**Encendido automático tras un apagón** — se configura en el BIOS/UEFI
del PC (no en Windows): busca `Restore on AC Power Loss` / `AC Back
Function` / `After Power Failure` (el nombre varía según la marca de la
tarjeta madre) y ponlo en `Power On`.

**Reinicio diario de Docker Desktop** — `reiniciar_docker.ps1` (junto a
este script) cierra y vuelve a levantar Docker Desktop completo. Se
programa con el Programador de Tareas de Windows:

1. Abre `taskschd.msc` → **Crear tarea** (no "tarea básica", para tener
   más opciones).
2. **General:** nómbrala, marca "Ejecutar con los privilegios más
   altos". Si quieres que corra sin que haya una sesión iniciada, marca
   "Ejecutar tanto si el usuario inició sesión como si no" (ver nota
   abajo).
3. **Desencadenadores:** Nuevo → Diario → hora sugerida **03:00 a. m.**
   (para no chocar con los turnos del reporte a las 6:00/18:00/22:00 y
   darle tiempo a los contenedores de estar listos antes del reporte de
   las 6 AM).
4. **Acciones:** Nuevo → "Iniciar un programa":
   - Programa/script: `powershell.exe`
   - Argumentos: `-ExecutionPolicy Bypass -File "C:\Monitor_Energia_Haas\Python alerts\reiniciar_docker.ps1"`
     (ajusta la ruta a donde copies el `.ps1`)
5. **Condiciones:** desmarca "Iniciar la tarea solo si el equipo está
   conectado a la corriente alterna" si es una laptop.
6. **Configuración:** marca "Ejecutar la tarea tan pronto como sea
   posible después de omitir un inicio programado" (por si el PC estaba
   apagado a las 3 AM).

**Nota:** si eliges "Ejecutar tanto si el usuario inició sesión como si
no", algunas versiones de Docker Desktop pueden fallar en abrir su
interfaz sin una sesión de escritorio activa. Si el reinicio no levanta
Docker, la alternativa es "Ejecutar solo si el usuario inició sesión" +
inicio de sesión automático de Windows configurado (igual que
necesitarías para que este mismo script de Python corra solo tras un
apagón, en vez de abrirlo a mano en IDLE).

**Importante:** para que los contenedores (InfluxDB, Grafana, MySQL)
vuelvan a levantarse solos después del reinicio, cada uno debe tener
configurada una política de reinicio `unless-stopped` o `always` (por
ejemplo `docker run --restart unless-stopped ...` o el equivalente
`restart: unless-stopped` en su `docker-compose.yml`). Revísalo con
`docker inspect <contenedor> --format='{{.HostConfig.RestartPolicy.Name}}'`
— si dice `no` o está vacío, el contenedor se va a quedar apagado tras
el reinicio hasta que alguien lo levante a mano.

## Tarifas CFE

Gerencia sigue editando el precio en las variables `tarifa_base`,
`tarifa_intermedia` y `tarifa_punta` del dashboard de Grafana — es la
única forma que tienen de cambiarlo sin tocar código. Pero esa API falla
con frecuencia, así que el script ya no depende de que responda en cada
corrida:

1. Al arrancar, carga la última tarifa guardada en `tarifas_cfe.json`
   (junto al script).
2. Intenta refrescarla contra Grafana. Si responde bien, actualiza los
   precios en memoria **y reescribe** `tarifas_cfe.json` (incluyendo la
   fecha de actualización), para que ese sea el nuevo respaldo.
3. Si Grafana falla por cualquier motivo (caída, timeout, variable
   faltante, etc.), se queda con lo que ya había en el JSON — nunca
   vuelve en silencio a los precios "de fábrica" salvo que sea la
   primera corrida y el JSON todavía no exista.

Ejemplo de `tarifas_cfe.json`:

```json
{
  "Base": 1.15,
  "Intermedia": 2.00,
  "Punta": 5.00,
  "actualizado": "2026-09-04T18:00:03.123456"
}
```

No hace falta editarlo a mano en operación normal; el propio script lo
mantiene al día cada vez que Grafana responde. Si quieres forzar un valor
manualmente (por ejemplo si Grafana estará caído varios días), edítalo y
el script lo respetará hasta que Grafana vuelva a responder.

## Blindaje contra lecturas erróneas del sensor

Al desconectar el equipo (o ante cualquier glitch eléctrico), el sensor
puede reportar por una fracción de segundo un valor absurdo de corriente
o voltaje. Un solo dato así arruina el `max()`/`min()`/`spread()` de toda
la ventana de tiempo (por ejemplo, un consumo de "millones de A" que
infla el kWh y el costo del reporte a cifras imposibles).

El script ahora descarta, antes de agregarlos, cualquier valor de
corriente fuera de `[0, CORRIENTE_MAX_VALIDA]` A o de voltaje fuera de
`[0, VOLTAJE_MAX_VALIDO]` V (ajustados a tu instalación: 220V
trifásicos, hasta 150 A por fase). Para la energía por bloque, se
calcula la potencia máxima físicamente posible con esos límites
(`√3 · V · I`) y se descarta cualquier lectura de `spread()` que la
supere, registrando un `WARNING` en el log con el bloque afectado.

Ajusta `VOLTAJE_NOMINAL`, `CORRIENTE_MAX_VALIDA` y `VOLTAJE_MAX_VALIDO`
en la sección `1c` del script si cambia la instalación eléctrica o la
máquina.

## Alerta por consumo mínimo

Si en el bloque de tiempo el consumo (`kwh_total`) y el pico de corriente
(`pico_amperaje`) quedan por debajo de `UMBRAL_KWH_MINIMO` /
`UMBRAL_AMPERAJE_MINIMO`, el script no arma el reporte completo de
parámetros. En su lugar envía un mensaje corto a Telegram indicando que no
se detectó actividad de la máquina (o que no llegaron datos del sensor), y
omite la generación del gráfico. El guardado en base de datos histórica no
se ve afectado por esta condición.

## Escala del gráfico (picos vs. rango continuo)

Antes, el eje Y del gráfico se ajustaba al máximo absoluto del día
(`max × 1.2`), así que un solo pico de corriente estiraba toda la escala
y el rango donde la máquina realmente opera la mayor parte del tiempo
quedaba aplastado abajo, casi ilegible.

Ahora el techo del eje Y se calcula con un **percentil** de las lecturas
del día (`PERCENTIL_ESCALA = 75`, con un margen `MARGEN_ESCALA = 1.3`),
en vez del máximo absoluto. Así, el rango "continuo" de maquinado ocupa
casi toda la altura del gráfico, y los picos que superan ese techo
simplemente se recortan visualmente arriba (no se pierden del cálculo
de `pico_amperaje` en el texto del reporte, solo del dibujo). Cuando el
pico real queda fuera de la escala dibujada, se agrega una anotación en
la esquina superior del gráfico con el valor real (ej. "⚠ Pico real:
59.8 A (fuera de escala)").

Si el gráfico sigue viéndose muy comprimido o muy estirado según cómo
trabaje tu máquina, ajusta `PERCENTIL_ESCALA` (más bajo = techo más
apretado, más picos recortados) y `MARGEN_ESCALA` (aire extra sobre ese
percentil) en la sección `[PASO 6/7]` del script.
