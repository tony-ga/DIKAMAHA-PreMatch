OBJETIVO DEL PUNTO 0.5  
Establecer las bases de control de versiones, gestión de dependencias y estructura de carpetas que garanticen:

Reproducibilidad: Que cualquier miembro del equipo (o tú mismo en 6 meses) pueda ejecutar el proyecto sin errores.

Trazabilidad: Poder volver a cualquier versión funcional del sistema si una actualización rompe algo.

Escalabilidad: Que añadir nuevas ligas, modelos o fuentes de datos no convierta el repositorio en un caos.

Seguridad: Mantener las API Keys, contraseñas de bases de datos y secretos fuera del código fuente.

2\. ESTRATEGIA DE VERSIONADO (GIT)  
2.1. Repositorio y Branches  
Se utilizará Git con un flujo de trabajo basado en Git Flow simplificado:

Rama	Nombre	Propósito	Reglas de Uso  
Producción	main	Código desplegado en el servidor (API en producción).	Solo se actualiza mediante Pull Request (PR) desde develop después de pruebas exitosas.  
Desarrollo	develop	Integración de todas las características nuevas.	Rama base para trabajar. Nunca se escribe código directamente aquí.  
Característica	feature/nombre-feature	Para desarrollar una funcionalidad concreta (ej. feature/parseador-espn).	Se crea desde develop. Al finalizar, se fusiona (merge) a develop mediante PR.  
Hotfix	hotfix/descripcion	Para arreglos urgentes en producción (ej. hotfix/error-api).	Se crea desde main. Al finalizar, se fusiona a main y a develop.

2.2. Convención para Nombres de Branches  
text  
feature/breve-descripcion-en-minusculas-con-guiones  
hotfix/breve-descripcion-en-minusculas-con-guiones  
release/vX.Y.Z  
Ejemplos:

feature/modelo-poisson-bivariante

feature/integracion-telegram-bot

hotfix/parseo-playbyplay-null

release/v1.2.0

2.3. Mensajes de Commit (Convención Estándar)  
Para mantener un historial limpio y comprensible, se seguirá el formato:

text  
\<tipo\>(\<alcance\>): \<mensaje en presente\>

\[Descripción opcional más detallada\]  
Tipos permitidos:

Tipo	Uso  
feat	Nueva funcionalidad o característica.  
fix	Corrección de un error.  
docs	Cambios en la documentación.  
style	Cambios de formato, espacios, puntos y comas (no afectan lógica).  
refactor	Refactorización del código sin cambiar funcionalidad.  
test	Añadir o corregir pruebas.  
chore	Cambios en tareas de mantenimiento (actualizar dependencias, configuraciones, etc.).  
Ejemplos de mensajes de commit:

text  
feat(api): añadir endpoint /predict/prematch para predicciones pre-partido

Implementa la lógica de carga del modelo Dixon-Coles y devuelve  
las probabilidades 1X2 en formato JSON.  
text  
fix(parser): corregir error al parsear goles en el minuto 90+ añadido

Ahora el parseador reconoce correctamente los tiempos de descuento  
como "45+2" y "90+5".  
text  
docs(readme): actualizar instrucciones de instalación del entorno virtual  
2.4. Versionado Semántico (Tags)  
Cada lanzamiento a producción se marcará con un tag en el repositorio siguiendo el formato Semantic Versioning (SemVer):

text  
vMAYOR.MENOR.PARCHE  
MAYOR: Cambios incompatibles con versiones anteriores (ej. cambiar de Poisson a otro modelo base).

MENOR: Nueva funcionalidad compatible hacia atrás (ej. añadir predicción de corners).

PARCHE: Corrección de errores compatible hacia atrás.

Ejemplo de tags:

v1.0.0 → Primer lanzamiento estable.

v1.1.0 → Se añade el modelo de Hawkes.

v1.1.1 → Corrección de un bug en la API.

v2.0.0 → Cambio de la fuente de datos ESPN a Opta (incompatible).

3\. GESTIÓN DEL ENTORNO VIRTUAL Y DEPENDENCIAS  
3.1. Herramienta Elegida: Poetry  
Se utilizará Poetry como gestor de dependencias y entornos virtuales. Razones:

Gestiona de forma automática las dependencias directas y transitivas.

Genera archivos pyproject.toml y poetry.lock para garantizar reproducibilidad exacta.

Permite separar dependencias de producción (main) de las de desarrollo (dev).  
3.2. Estructura del Archivo pyproject.toml  
toml  
\[tool.poetry\]  
name \= "codex-predictor"  
version \= "1.0.0"  
description \= "Sistema predictivo de mercados de apuestas con modelos físicos y bayesianos"  
authors \= \["Tu Nombre \<tu.email@ejemplo.com\>"\]

\[tool.poetry.dependencies\]  
python \= "^3.10"  
pandas \= "^2.0.0"  
numpy \= "^1.24.0"  
scipy \= "^1.10.0"  
statsmodels \= "^0.14.0"  
pymc \= "^5.0.0"  
scikit-learn \= "^1.3.0"  
xgboost \= "^2.0.0"  
fastapi \= "^0.100.0"  
uvicorn \= "^0.23.0"  
python-telegram-bot \= "^20.0"  
psycopg2-binary \= "^2.9.0"  
requests \= "^2.31.0"  
apache-airflow \= "^2.7.0"  
feast \= "^0.32.0"

\[tool.poetry.group.dev.dependencies\]  
pytest \= "^7.4.0"  
black \= "^23.0.0"  
flake8 \= "^6.0.0"  
mypy \= "^1.0.0"  
pre-commit \= "^3.0.0"  
jupyter \= "^1.0.0"

\[build-system\]  
requires \= \["poetry-core"\]  
build-backend \= "poetry.core.masonry.api"  
3.3. Comandos de Uso Diario  
Acción	Comando  
Instalar todas las dependencias (crea entorno virtual automáticamente)	poetry install  
Instalar solo dependencias de producción	poetry install \--no-dev  
Añadir una nueva dependencia de producción	poetry add nombre-paquete  
Añadir una dependencia de desarrollo	poetry add \--group dev nombre-paquete  
Actualizar todas las dependencias a las últimas versiones permitidas	poetry update  
Activar el entorno virtual	poetry shell  
Ejecutar un script dentro del entorno	poetry run python script.py  
Exportar dependencias a requirements.txt (por si se necesita)	poetry export \-f requirements.txt \--output requirements.txt  
4\. ESTRUCTURA DE CARPETAS DEL PROYECTO (Escalable)  
Esta estructura está diseñada para crecer de forma ordenada, separando claramente las responsabilidades de cada componente.

text  
codex-predictor/                            \# Raíz del proyecto  
│  
├── .env                                    \# Variables de entorno (NUNCA se sube a Git)  
├── .gitignore                              \# Archivos a ignorar por Git  
├── .pre-commit-config.yaml                 \# Ganchos de pre-commit (formateo automático)  
├── pyproject.toml                          \# Configuración de Poetry  
├── poetry.lock                             \# Bloqueo exacto de versiones (generado automáticamente)  
├── README.md                               \# Documentación principal del proyecto  
├── LICENSE                                 \# Licencia (MIT, Apache, etc.)  
│  
├── data/                                   \# Datos locales (NUNCA se sube a Git)  
│   ├── raw/                                \# Datos crudos descargados de ESPN  
│   ├── processed/                          \# Datos transformados y listos para modelar  
│   └── models/                             \# Modelos entrenados guardados (.pkl, .joblib)  
│  
├── notebooks/                              \# Notebooks de Jupyter para exploración  
│   ├── 01\_EDA\_exploracion\_datos.ipynb  
│   ├── 02\_poisson\_dixon\_coles.ipynb  
│   └── 03\_hawkes\_kalman\_simulation.ipynb  
│  
├── src/                                    \# Código fuente del sistema  
│   ├── \_\_init\_\_.py                         \# Convierte src en un paquete Python  
│   │  
│   ├── data/                               \# Módulo de datos  
│   │   ├── \_\_init\_\_.py  
│   │   ├── connectors/                     \# Conectores a APIs  
│   │   │   ├── \_\_init\_\_.py  
│   │   │   ├── base.py                     \# Clase abstracta APIClient  
│   │   │   ├── espn\_client.py              \# Cliente específico para ESPN  
│   │   │   └── opta\_client.py              \# (Futuro) Cliente para Opta  
│   │   ├── parsers/                        \# Parsers de play-by-play  
│   │   │   ├── \_\_init\_\_.py  
│   │   │   ├── base\_parser.py              \# Interfaz abstracta EventParser  
│   │   │   └── espn\_parser.py              \# Parseador de texto de ESPN  
│   │   └── pipelines/                      \# Pipelines de procesamiento (Airflow DAGs)  
│   │       ├── \_\_init\_\_.py  
│   │       ├── extract\_dag.py  
│   │       ├── transform\_dag.py  
│   │       └── load\_dag.py  
│   │  
│   ├── features/                           \# Ingeniería de características  
│   │   ├── \_\_init\_\_.py  
│   │   ├── builders.py                     \# Funciones para construir features  
│   │   ├── markov\_matrix.py                \# Cálculo de matrices de transición  
│   │   └── hawkes\_kernel.py                \# Estimación de alfa y beta  
│   │  
│   ├── models/                             \# Modelos predictivos  
│   │   ├── \_\_init\_\_.py  
│   │   ├── base\_model.py                   \# Clase base para todos los modelos  
│   │   ├── dixon\_coles.py                  \# Implementación de Dixon-Coles  
│   │   ├── kalman\_filter.py                \# Filtro de Kalman  
│   │   ├── markov\_regime.py                \# Cadena de Markov de 3 estados  
│   │   ├── hawkes\_process.py               \# Proceso de Hawkes  
│   │   └── ensemble.py                     \# Ensamblaje de las 4 capas  
│   │  
│   ├── api/                                \# Microservicio FastAPI  
│   │   ├── \_\_init\_\_.py  
│   │   ├── main.py                         \# Punto de entrada de la API  
│   │   ├── routes.py                       \# Definición de endpoints  
│   │   ├── schemas.py                      \# Modelos Pydantic (validación)  
│   │   └── dependencies.py                 \# Inyección de dependencias (modelos cargados)  
│   │  
│   ├── telegram\_bot/                       \# Bot de Telegram  
│   │   ├── \_\_init\_\_.py  
│   │   ├── bot.py                          \# Lógica principal del bot  
│   │   ├── handlers.py                     \# Manejadores de comandos  
│   │   └── keyboards.py                    \# Teclados personalizados  
│   │  
│   ├── utils/                              \# Utilidades transversales  
│   │   ├── \_\_init\_\_.py  
│   │   ├── logger.py                       \# Configuración de logs  
│   │   ├── secrets.py                      \# Carga de variables de entorno  
│   │   ├── db\_connector.py                 \# Conexión a PostgreSQL  
│   │   └── metrics.py                      \# Métricas de evaluación (Log-Loss, ROI)  
│   │  
│   └── config/                             \# Configuraciones  
│       ├── \_\_init\_\_.py  
│       ├── settings.py                     \# Configuración global (paths, parámetros)  
│       └── logging\_config.py               \# Configuración de logs  
│  
├── tests/                                  \# Pruebas unitarias y de integración  
│   ├── \_\_init\_\_.py  
│   ├── test\_data/  
│   │   ├── test\_espn\_client.py  
│   │   └── test\_espn\_parser.py  
│   ├── test\_models/  
│   │   ├── test\_dixon\_coles.py  
│   │   └── test\_hawkes.py  
│   └── test\_api/  
│       └── test\_routes.py  
│  
├── scripts/                                \# Scripts auxiliares  
│   ├── init\_database.sql                   \# Esquema SQL para PostgreSQL  
│   ├── seed\_database.py                    \# Poblar la base de datos con datos iniciales  
│   └── deploy.sh                           \# Script de despliegue en la nube  
│  
├── docker/                                 \# Archivos relacionados con Docker  
│   ├── Dockerfile.api                      \# Imagen para la API  
│   ├── Dockerfile.airflow                  \# Imagen para Airflow  
│   └── docker-compose.yml                  \# Orquestación de contenedores  
│  
└── docs/                                   \# Documentación detallada  
    ├── arquitectura.md  
    ├── modelos\_matematicos.md  
    └── manual\_usuario\_telegram.md  
5\. GESTIÓN DE SECRETOS Y VARIABLES DE ENTORNO  
Nunca se deben subir a Git las claves de API, contraseñas de bases de datos ni tokens de acceso. Para ello se usará un archivo .env (en la raíz del proyecto) y la librería python-dotenv.

5.1. Archivo .env (NUNCA subir a Git, debe aparecer en .gitignore)  
text  
\# \======================  
\# CONFIGURACIÓN GENERAL  
\# \======================  
ENVIRONMENT=development  
LOG\_LEVEL=INFO

\# \======================  
\# BASE DE DATOS (PostgreSQL)  
\# \======================  
DB\_HOST=localhost  
DB\_PORT=5432  
DB\_NAME=codex\_db  
DB\_USER=codex\_user  
DB\_PASSWORD=contraseña\_segura\_123

\# \======================  
\# APIS EXTERNAS  
\# \======================  
ESPN\_API\_KEY=tu\_api\_key\_de\_espn  
ESPN\_BASE\_URL=https://site.api.espn.com/apis/site/v2/sports/soccer

\# \======================  
\# TELEGRAM BOT  
\# \======================  
TELEGRAM\_BOT\_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz

\# \======================  
\# ALMACENAMIENTO EN NUBE  
\# \======================  
AWS\_ACCESS\_KEY\_ID=AKIA...  
AWS\_SECRET\_ACCESS\_KEY=...  
S3\_BUCKET\_NAME=codex-models-bucket  
5.2. Carga de Variables en Python  
python  
\# src/utils/secrets.py  
import os  
from dotenv import load\_dotenv

\# Cargar variables desde .env  
load\_dotenv()

class Settings:  
    \# Variables de entorno con valores por defecto para desarrollo  
    DB\_HOST: str \= os.getenv("DB\_HOST", "localhost")  
    DB\_PORT: int \= int(os.getenv("DB\_PORT", "5432"))  
    DB\_NAME: str \= os.getenv("DB\_NAME", "codex\_db")  
    DB\_USER: str \= os.getenv("DB\_USER", "codex\_user")  
    DB\_PASSWORD: str \= os.getenv("DB\_PASSWORD", "")  
      
    ESPN\_API\_KEY: str \= os.getenv("ESPN\_API\_KEY", "")  
    ESPN\_BASE\_URL: str \= os.getenv("ESPN\_BASE\_URL", "")  
      
    TELEGRAM\_BOT\_TOKEN: str \= os.getenv("TELEGRAM\_BOT\_TOKEN", "")  
      
    AWS\_ACCESS\_KEY\_ID: str \= os.getenv("AWS\_ACCESS\_KEY\_ID", "")  
    AWS\_SECRET\_ACCESS\_KEY: str \= os.getenv("AWS\_SECRET\_ACCESS\_KEY", "")  
    S3\_BUCKET\_NAME: str \= os.getenv("S3\_BUCKET\_NAME", "codex-models")

settings \= Settings()  
5.3. Archivo .gitignore (Mínimo obligatorio)  
text  
\# Entornos virtuales  
.venv/  
venv/  
env/

\# Archivos de datos locales  
data/raw/  
data/processed/  
data/models/

\# Secretos  
.env  
.env.local

\# Archivos de Python generados  
\_\_pycache\_\_/  
\*.pyc  
\*.pyo  
\*.pyd

\# Notebooks con resultados (para no saturar el repo)  
.ipynb\_checkpoints/  
\*.ipynb

\# Logs  
logs/  
\*.log

\# Archivos de base de datos locales  
\*.db  
\*.sqlite

\# Archivos de IDE  
.vscode/  
.idea/  
\*.swp

\# Archivos de sistema  
.DS\_Store  
Thumbs.db

\# Docker  
\*.pid  
6\. PRE-COMMIT HOOKS (Calidad del Código)  
Para garantizar que el código que se sube al repositorio sigue unos estándares mínimos de calidad, se configurarán hooks de pre-commit. Estos se ejecutan automáticamente antes de cada commit.

6.1. Archivo .pre-commit-config.yaml  
yaml  
repos:  
  \- repo: https://github.com/pre-commit/pre-commit-hooks  
    rev: v4.4.0  
    hooks:  
      \- id: trailing-whitespace        \# Elimina espacios al final de las líneas  
      \- id: end-of-file-fixer          \# Asegura que los archivos terminen con salto de línea  
      \- id: check-yaml                 \# Valida archivos YAML  
      \- id: check-json                 \# Valida archivos JSON  
      \- id: check-added-large-files    \# Evita subir archivos \> 500 KB

  \- repo: https://github.com/psf/black  
    rev: 23.7.0  
    hooks:  
      \- id: black                      \# Formateador automático de código

  \- repo: https://github.com/pycqa/flake8  
    rev: 6.1.0  
    hooks:  
      \- id: flake8                     \# Linter para errores de estilo  
        args: \[--max-line-length=100\]

  \- repo: https://github.com/pre-commit/mirrors-mypy  
    rev: v1.5.0  
    hooks:  
      \- id: mypy                       \# Verificador de tipos estáticos  
        additional\_dependencies: \[types-requests, types-PyYAML\]  
6.2. Instalación de Pre-commit  
bash  
\# Instalar pre-commit  
poetry add \--group dev pre-commit

\# Instalar los hooks en el repositorio  
poetry run pre-commit install

\# Ejecutar manualmente sobre todos los archivos  
poetry run pre-commit run \--all-files  
7\. CONFIGURACIÓN DE LOGGING  
Un sistema robusto necesita registros (logs) estructurados para depurar errores y monitorizar el rendimiento.

7.1. Archivo src/config/logging\_config.py  
python  
import logging  
import sys  
from pathlib import Path

\# Crear directorio de logs si no existe  
LOG\_DIR \= Path("logs")  
LOG\_DIR.mkdir(exist\_ok=True)

def setup\_logging(level=logging.INFO):  
    """Configura el sistema de logging con formato estructurado."""  
      
    \# Formato: \[NIVEL\] Fecha \- Módulo \- Mensaje  
    formatter \= logging.Formatter(  
        "\[%(levelname)s\] %(asctime)s \- %(name)s \- %(message)s",  
        datefmt="%Y-%m-%d %H:%M:%S"  
    )

    \# Handler para consola (siempre activo)  
    console\_handler \= logging.StreamHandler(sys.stdout)  
    console\_handler.setFormatter(formatter)

    \# Handler para archivo (rotación diaria o por tamaño)  
    file\_handler \= logging.FileHandler(LOG\_DIR / "codex.log", encoding="utf-8")  
    file\_handler.setFormatter(formatter)

    \# Configurar el logger raíz  
    root\_logger \= logging.getLogger()  
    root\_logger.setLevel(level)  
    root\_logger.addHandler(console\_handler)  
    root\_logger.addHandler(file\_handler)

    \# Silenciar logs de librerías externas demasiado ruidosas  
    logging.getLogger("urllib3").setLevel(logging.WARNING)  
    logging.getLogger("requests").setLevel(logging.WARNING)  
      
    return root\_logger  
7.2. Uso en el código  
python  
\# En cualquier módulo  
import logging  
logger \= logging.getLogger(\_\_name\_\_)

logger.info("Iniciando parseador de play-by-play")  
logger.warning("Evento no reconocido en el minuto %s", minuto)  
logger.error("Error de conexión a la API: %s", str(e))  
8\. PRUEBAS UNITARIAS (Tests)  
Se utilizará pytest para escribir pruebas que aseguren que cada componente funciona correctamente.

8.1. Estructura de las Pruebas  
Cada módulo debe tener su correspondiente archivo de pruebas en la carpeta tests/:

text  
tests/  
├── test\_data/  
│   ├── test\_espn\_client.py  
│   └── test\_espn\_parser.py  
├── test\_models/  
│   ├── test\_dixon\_coles.py  
│   └── test\_hawkes.py  
└── test\_api/  
    └── test\_routes.py  
8.2. Ejemplo de Prueba  
python  
\# tests/test\_data/test\_espn\_parser.py  
import pytest  
from src.data.parsers.espn\_parser import ESPNEventParser

def test\_parse\_goal\_event():  
    parser \= ESPNEventParser()  
    text\_line \= "GOAL: L. Messi (Barcelona) left footed shot from the centre of the box to the bottom right corner. Assisted by J. Alba."  
      
    event \= parser.parse(text\_line, minute=35)  
      
    assert event\["type"\] \== "goal"  
    assert event\["team"\] \== "Barcelona"  
    assert event\["minute"\] \== 35

def test\_parse\_corner\_event():  
    parser \= ESPNEventParser()  
    text\_line \= "Corner, Real Madrid. Conceded by K. Navas."  
      
    event \= parser.parse(text\_line, minute=72)  
      
    assert event\["type"\] \== "corner"  
    assert event\["team"\] \== "Real Madrid"  
    assert event\["minute"\] \== 72  
8.3. Ejecución de Pruebas  
bash  
\# Ejecutar todas las pruebas  
poetry run pytest

\# Ejecutar pruebas con cobertura de código  
poetry run pytest \--cov=src

\# Ejecutar pruebas de un módulo específico  
poetry run pytest tests/test\_data/  
9\. FLUJO DE TRABAJO DIARIO (Resumen)  
Cuando un desarrollador (tú) se incorpora al proyecto por primera vez:

bash  
\# 1\. Clonar el repositorio  
git clone https://github.com/tu-usuario/codex-predictor.git  
cd codex-predictor

\# 2\. Crear archivo .env a partir de .env.example  
cp .env.example .env  
\# Editar .env con tus propias claves y contraseñas

\# 3\. Instalar dependencias (Poetry crea el entorno virtual automáticamente)  
poetry install

\# 4\. Activar el entorno virtual  
poetry shell

\# 5\. Instalar hooks de pre-commit  
pre-commit install

\# 6\. Verificar que todo funciona  
pytest  
Cuando se desarrolla una nueva funcionalidad:

bash  
\# 1\. Crear rama desde develop  
git checkout develop  
git pull origin develop  
git checkout \-b feature/nuevo-modelo

\# 2\. Desarrollar, hacer commits con mensajes semánticos  
git add .  
git commit \-m "feat(models): añadir proceso de Hawkes para inercia emocional"

\# 3\. Subir la rama al repositorio remoto  
git push origin feature/nuevo-modelo

\# 4\. Crear un Pull Request (PR) en GitHub/GitLab para fusionar a develop  
10\. CONCLUSIONES DEL PUNTO 0.5  
Con esta configuración, el proyecto cumple con los estándares de desarrollo profesional:

✅ Reproducibilidad: poetry install \+ .env garantiza que cualquiera pueda ejecutar el sistema.

✅ Trazabilidad: Git \+ mensajes semánticos \+ tags SemVer permiten saber exactamente qué cambió y cuándo.

✅ Escalabilidad: La estructura de carpetas permite añadir nuevos modelos, fuentes de datos o deportes sin modificar el núcleo.

✅ Seguridad: Las claves de API y contraseñas nunca se suben al repositorio.

✅ Calidad: Pre-commit hooks y pruebas unitarias detectan errores antes de que lleguen a producción.

Este documento debe ser entregado junto con el código fuente para garantizar la continuidad del proyecto.

Fin del Documento

