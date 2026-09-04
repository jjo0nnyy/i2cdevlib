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
