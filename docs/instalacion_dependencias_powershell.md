# Instalación de dependencias en PowerShell

El archivo agregado `requirements-full.txt` instala el runtime del predictor,
PostgreSQL/staging, el servicio HTTP y las herramientas de pruebas. No incluye
credenciales ni modifica PostgreSQL.

Desde la raíz del proyecto:

```powershell
if (-not (Test-Path .\.venv\Scripts\python.exe)) {
    py -3.12 -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r .\requirements-full.txt
```

Para comprobar la instalación:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q
```

Si sólo se necesita el conector y staging, usar
`requirements.runtime.txt`; para una imagen Docker, usar
`requirements.docker.txt`.
