# Reinicia Docker Desktop por completo (cierra y vuelve a abrir la app),
# lo que reinicia el motor de Docker y, con el a los contenedores que
# tengan una politica de reinicio (--restart unless-stopped / always).
# Pensado para correr una vez al dia en la madrugada via el Programador
# de Tareas de Windows, para no interferir con los turnos del reporte
# (6:00, 18:00, 22:00).

$logFile = "C:\Monitor_Energia_Haas\reinicio_docker.log"
$dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"

function Log($mensaje) {
    $linea = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - $mensaje"
    Add-Content -Path $logFile -Value $linea
    Write-Output $linea
}

Log "Cerrando Docker Desktop..."
Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "com.docker.backend" -Force -ErrorAction SilentlyContinue

# Le da tiempo a Docker Desktop de apagar limpiamente la VM de WSL2
# antes de volver a levantarlo.
Start-Sleep -Seconds 20

if (Test-Path $dockerExe) {
    Log "Abriendo Docker Desktop..."
    Start-Process $dockerExe
    Log "Docker Desktop lanzado. Los contenedores con --restart unless-stopped/always deberian levantarse solos."
} else {
    Log "ERROR: no se encontro Docker Desktop en '$dockerExe'. Ajusta la ruta en este script."
}
