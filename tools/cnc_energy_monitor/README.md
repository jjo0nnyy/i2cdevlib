# Reporte de energía HAAS VF-9

Script de monitoreo de consumo eléctrico que consulta InfluxDB,
calcula costos por tarifa CFE (Grafana + caché local de respaldo)
y envía un reporte a Telegram.

## Variables de entorno requeridas

El script ya no acepta credenciales dentro del código. Son obligatorias:

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `INFLUX_TOKEN`
- `GRAFANA_TOKEN`
- `MYSQL_PASSWORD`

Opcionales (tienen valor por defecto):

- `INFLUX_URL` (default `http://localhost:8086`)
- `INFLUX_ORG` (default `noramex`)
- `INFLUX_BUCKET` (default `haas_vf9_energy`)
- `GRAFANA_DASHBOARD_URL` (default `http://localhost:3000/api/dashboards/uid/adv9hsz`)
- `MYSQL_HOST` (default `localhost`)
- `MYSQL_USER` (default `root`)
- `MYSQL_DATABASE` (default `ems_noramex`)
- `RUTA_TARIFAS_CFE` (default: `tarifas_cfe.json` junto al script)

### Cómo definirlas — opción recomendada para pruebas en Windows/IDLE

Configurar variables de entorno del sistema en Windows es incómodo (hay
que abrir "Variables de entorno", reiniciar IDLE, etc.) y es fácil
equivocarse: la variable necesita un **nombre** (p. ej. `TELEGRAM_TOKEN`)
y un **valor** (el token en sí) — si se invierten, el script no
encuentra la variable y falla con "Falta la variable de entorno
obligatoria: <lo que hayas puesto como nombre>".

Es más simple usar un archivo `.env`, que el script carga solo:

1. `pip install -r requirements.txt` (o al menos `pip install python-dotenv`)
2. Copia `.env.example` a `.env` (misma carpeta que el script).
3. Edita `.env` y llena cada valor **a la derecha del `=`**, por ejemplo:
   ```
   TELEGRAM_TOKEN=8553446968:AAHhW0ZSx6uVKwUxSqr12OdA8fvs9BXtzzw
   ```
4. Corre el script normal (F5 en IDLE, o `python reporte_energia_haas.py`).

`.env` está en `.gitignore`: nunca se sube al repositorio. Si prefieres
variables de entorno reales del sistema (por ejemplo para correrlo como
tarea programada/servicio en un servidor), también funcionan — el
`.env` es solo una comodidad adicional, no reemplaza nada.

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

## Alerta por consumo mínimo

Si en el bloque de tiempo el consumo (`kwh_total`) y el pico de corriente
(`pico_amperaje`) quedan por debajo de `UMBRAL_KWH_MINIMO` /
`UMBRAL_AMPERAJE_MINIMO`, el script no arma el reporte completo de
parámetros. En su lugar envía un mensaje corto a Telegram indicando que no
se detectó actividad de la máquina (o que no llegaron datos del sensor), y
omite la generación del gráfico. El guardado en base de datos histórica no
se ve afectado por esta condición.
