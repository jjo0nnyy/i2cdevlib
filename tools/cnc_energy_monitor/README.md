# Reporte de energía HAAS VF-9

Script de monitoreo de consumo eléctrico que consulta InfluxDB/Grafana,
calcula costos por tarifa CFE y envía un reporte a Telegram.

## Variables de entorno requeridas

El script ya no acepta credenciales dentro del código. Defínelas antes de
ejecutarlo (por ejemplo en un archivo `.env` fuera del control de versiones,
o como variables del sistema/servicio):

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `INFLUX_TOKEN`
- `GRAFANA_TOKEN`
- `MYSQL_PASSWORD`

Opcionales (tienen valor por defecto):

- `INFLUX_URL` (default `http://localhost:8086`)
- `INFLUX_ORG` (default `noramex`)
- `INFLUX_BUCKET` (default `haas_vf9_energy`)
- `MYSQL_HOST` (default `localhost`)
- `MYSQL_USER` (default `root`)
- `MYSQL_DATABASE` (default `ems_noramex`)

## Alerta por consumo mínimo

Si en el bloque de tiempo el consumo (`kwh_total`) y el pico de corriente
(`pico_amperaje`) quedan por debajo de `UMBRAL_KWH_MINIMO` /
`UMBRAL_AMPERAJE_MINIMO`, el script no arma el reporte completo de
parámetros. En su lugar envía un mensaje corto a Telegram indicando que no
se detectó actividad de la máquina (o que no llegaron datos del sensor), y
omite la generación del gráfico. El guardado en base de datos histórica no
se ve afectado por esta condición.
