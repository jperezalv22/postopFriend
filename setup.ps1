# postopFriend — preparación en un comando (Windows / PowerShell)
#
#   .\setup.ps1
#
# Crea el entorno, instala dependencias, deja el modelo de embeddings descargado y
# diagnostica. Todo lo que pueda fallar, falla aquí y con un mensaje claro, en vez
# de a mitad de la sesión de evaluación.

$ErrorActionPreference = "Stop"
$inicio = Get-Date

Write-Host ""
Write-Host "postopFriend - preparacion del entorno" -ForegroundColor Cyan
Write-Host ("-" * 60)

# ─── 1. Python ────────────────────────────────────────────────────────────────
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { throw "No se encontro Python en el PATH. Instale Python 3.11 o superior." }
$version = & python -c "import sys; print('.'.join(map(str, sys.version_info[:2])))"
Write-Host "[1/5] Python $version"

# ─── 2. Entorno virtual ───────────────────────────────────────────────────────
if (-not (Test-Path ".venv")) {
    Write-Host "[2/5] Creando entorno virtual..."
    & python -m venv .venv
} else {
    Write-Host "[2/5] Entorno virtual ya existe"
}
$py = ".\.venv\Scripts\python.exe"

# ─── 3. Dependencias ──────────────────────────────────────────────────────────
Write-Host "[3/5] Instalando dependencias (~180 MB, 1-3 min)..."
& $py -m pip install --upgrade pip --quiet --disable-pip-version-check
& $py -m pip install -r requirements.txt --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "Fallo la instalacion de dependencias" }

# ─── 4. Credenciales ──────────────────────────────────────────────────────────
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "[4/5] Creado .env - PEGUE SU GROQ_API_KEY ahi" -ForegroundColor Yellow
    Write-Host "      Consigala gratis en https://console.groq.com/keys" -ForegroundColor Yellow
} else {
    Write-Host "[4/5] .env ya existe"
}

# ─── 5. Modelo de embeddings ──────────────────────────────────────────────────
# Se descarga aqui a proposito. Si se dejara para el primer turno de la llamada,
# el jurado veria 17 segundos de silencio sin explicacion en mitad de la demo.
Write-Host "[5/5] Descargando el modelo de embeddings (~220 MB, solo la primera vez)..."
& $py scripts/precalentar.py

Write-Host ("-" * 60)
$segundos = [int]((Get-Date) - $inicio).TotalSeconds
Write-Host "Listo en $segundos s" -ForegroundColor Green
Write-Host ""
& $py scripts/doctor.py
Write-Host ""
Write-Host "Para arrancar:  .\.venv\Scripts\uvicorn.exe app.main:app" -ForegroundColor Cyan
Write-Host "Luego abra:     http://127.0.0.1:8000" -ForegroundColor Cyan
Write-Host ""
