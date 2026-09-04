# Reporte de energía HAAS VF-9

Script de monitoreo de consumo eléctrico que consulta InfluxDB,
calcula costos por tarifa CFE (desde un archivo de configuración local)
y envía un reporte a Telegram.

## Variables de entorno requeridas

El script ya no acepta credenciales dentro del código. Defínelas antes de
ejecutarlo (por ejemplo en un archivo `.env` fuera del control de versiones,
o como variables del sistema/servicio):

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `INFLUX_TOKEN`
- `MYSQL_PASSWORD`

Opcionales (tienen valor por defecto):

- `INFLUX_URL` (default `http://localhost:8086`)
- `INFLUX_ORG` (default `noramex`)
- `INFLUX_BUCKET` (default `haas_vf9_energy`)
- `MYSQL_HOST` (default `localhost`)
- `MYSQL_USER` (default `root`)
- `MYSQL_DATABASE` (default `ems_noramex`)
- `RUTA_TARIFAS_CFE` (default: `tarifas_cfe.json` junto al script)

## Tarifas CFE

Ya no se consultan desde la API de Grafana (fallaba con frecuencia y el
script terminaba usando los precios default sin que nadie se enterara).
Ahora se leen de `tarifas_cfe.json`, junto al script:

```json
{
  "Base": 1.15,
  "Intermedia": 2.00,
  "Punta": 5.00
}
```

Edita ese archivo cuando CFE actualice la tarifa GDMTH. Si el archivo no
existe o está mal formado, el script lo registra en el log (WARNING/ERROR)
y sigue con los precios default — ya no falla en silencio.

## Alerta por consumo mínimo

Si en el bloque de tiempo el consumo (`kwh_total`) y el pico de corriente
(`pico_amperaje`) quedan por debajo de `UMBRAL_KWH_MINIMO` /
`UMBRAL_AMPERAJE_MINIMO`, el script no arma el reporte completo de
parámetros. En su lugar envía un mensaje corto a Telegram indicando que no
se detectó actividad de la máquina (o que no llegaron datos del sensor), y
omite la generación del gráfico. El guardado en base de datos histórica no
se ve afectado por esta condición.
