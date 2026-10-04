# 🥋 BlackBelt

**El cinturón de herramientas CLI local-first.**


![Carátula](images/BlackBelt.jpg)

> *"El cinturón es más útil que el cuchillo. El cuchillo corta. El cinturón transporta."*

BlackBelt no es un producto. Es un cinturón. Un taller de herramientas que vive en tu terminal, unifica tus scripts dispersos bajo un único punto de entrada, y actúa como mentor mientras aprendes Linux y Python.

Sin nube. Sin telemetría. Sin PowerPoints. Solo tú, una terminal, y un cinturón que gana bolsillos a medida que tú ganas conocimiento.

---

## Índice

- [Filosofía](#filosofía)
- [Estado actual](#estado-actual)
- [Instalación](#instalación)
- [Configuración](#configuración)
- [Uso](#uso)
  - [Comandos base](#comandos-base)
  - [Gatekeeper: ejecución segura](#gatekeeper-ejecución-segura)
  - [Obsidian: lectura y escritura](#obsidian-lectura-y-escritura)
  - [Linux Mentor](#linux-mentor)
  - [Windows Mentor](#windows-mentor)
  - [Predictor de comandos](#predictor-de-comandos)
  - [Aplicaciones locales](#aplicaciones-locales)
  - [Gráficas de sistema](#gráficas-de-sistema)
  - [Chat con Ollama](#chat-con-ollama)
  - [Auditoría](#auditoría)
- [Arquitectura](#arquitectura)
- [Diseño del Gatekeeper](#diseño-del-gatekeeper)
- [Roadmap](#roadmap)
- [Licencia](#licencia)

---

## Filosofía

BlackBelt parte de tres principios:

1. **Local-first.** Todo corre en tu máquina. Las únicas APIs externas son las que tú decidas. Los modelos de lenguaje son SLMs locales vía Ollama.
2. **Modular.** El núcleo es mínimo. Las herramientas son plugins que se registran, se invocan y se ejecutan. Añadir un bolsillo nuevo no toca el cinturón.
3. **Seguro por defecto.** Las acciones sensibles pasan por un Gatekeeper que te muestra qué se va a hacer antes de hacerlo, y deja rastro en un log de auditoría.

---

## Estado actual

| Módulo | Estado | Descripción |
| :--- | :---: | :--- |
| `env` | ✅ | Detección de OS, distro, shell, Python, Ollama |
| `tools` | ✅ | Listado de herramientas registradas |
| `run linux` | ✅ | Mentor Linux: info, explain, diagnose |
| `run windows` | ✅ | Mentor Windows y explicacion de PowerShell |
| `run suggest` | ✅ | Recetas locales que proponen comandos para el sistema actual |
| `run graphics` | ✅ | Snapshot y monitor en vivo (CPU, RAM, disco, red) |
| `run exec` | ✅ | Ejecución de comandos shell con Gatekeeper |
| `run audit` | ✅ | Visor del log de auditoría |
| `run obsidian` | ✅ | Lectura y escritura controlada de la bóveda |
| `run chat` | ✅ | Chat interactivo con Ollama local |
| `run email` | ✅ | Triaje local de Gmail en modo solo lectura |
| `run apps` | ✅ | SOMA y Ghost Writer en servidor local de loopback |
| `run feeds` | 🔜 | Lector de feeds RSS |
| `run search` | 🔜 | Búsqueda semántica con Qdrant |

---

## Instalación

**Requisitos:**
- Python 3.11+
- Linux/macOS con Bash o Windows 10+ con PowerShell
- Opcional: [Ollama](https://ollama.com/) para los módulos de IA

```bash
git clone <repo> blackbelt
cd blackbelt
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

En Windows PowerShell:
```powershell
git clone https://github.com/Charran78/blackbelt.git
Set-Location blackbelt
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

Comprueba que funciona:
```bash
blackbelt version
blackbelt env
blackbelt tools
```

---

## Configuración

BlackBelt lee su configuración de dos fuentes:

### 1. Variables de entorno (`.env` en la raíz)
```bash
# Modelo de Ollama
OLLAMA_MODEL=qwen2.5-coder:0.5b
OLLAMA_KEEP_ALIVE=2h
OLLAMA_NUM_CTX=512
OLLAMA_NUM_PREDICT=180
OLLAMA_NUM_THREAD=2
OLLAMA_TIMEOUT=180

# Bóveda Obsidian
# OBSIDIAN_VAULT="/ruta/a/tu/boveda"
```

### 2. Archivo YAML (`~/.config/blackbelt/config.yaml`)
Reservado para configuración persistente que no encaje en envs. Se gestiona con `blackbelt.core.config.load()` y `save()`.

### 3. Log de auditoría (`~/.config/blackbelt/audit.log`)
Se llena automáticamente con cada acción que pasa por el Gatekeeper. Formato:
```text
2026-09-30T00:10:46 | WARN     | ALLOWED | Crear nota 999 - DIARIO/nota.md | Ruta: ...
2026-09-30T00:14:40 | CRITICAL | ALLOWED | Ejecutar: rm /tmp/viejo.txt
```

---

## Uso

### Comandos base
```bash
blackbelt version              # Versión
blackbelt env                  # Entorno detectado
blackbelt tools                # Herramientas registradas
blackbelt run <tool> [args]    # Ejecutar una herramienta
```

### Gatekeeper: ejecución segura
El comando `exec` ejecuta comandos de shell pasándolos por el Gatekeeper, que evalúa el riesgo y pide confirmación según el nivel.
Usa Bash en Linux/macOS y PowerShell en Windows; escribe los comandos con la sintaxis del sistema actual. Los ejemplos siguientes son para Bash.

```bash
blackbelt run exec "ls -la ~/"
blackbelt run exec "cat ~/.bashrc | head -5"
blackbelt run exec "echo hola > /tmp/test.txt && cat /tmp/test.txt"
blackbelt run exec "rm /tmp/viejo.txt"        # Pide escribir "yes"
```

**Niveles de riesgo:**

| Nivel | Color | Comportamiento |
|---|---|---|
| **SAFE** | verde | Ejecuta sin preguntar |
| **WARN** | amarillo | Pregunta sí/no |
| **CRITICAL** | rojo | Exige escribir `yes` literal |

Comandos como `rm`, `dd`, `mkfs`, `sudo`, `chown` en raíz, etc. se clasifican como **CRITICAL** automáticamente. Redirecciones, `mv`, `cp`, `mkdir`, `chmod`, etc. como **WARN**.

### Obsidian: lectura y escritura
```bash
# Lectura
blackbelt run obsidian list                    # Carpetas y notas en la raíz
blackbelt run obsidian list "999 - DIARIO"     # Dentro de una carpeta
blackbelt run obsidian find "proyecto"         # Buscar por nombre
blackbelt run obsidian grep "blackbelt"        # Buscar en contenido
blackbelt run obsidian read "999 - DIARIO/2026-08-31.md"
blackbelt run obsidian info "999 - DIARIO/2026-08-31.md"
blackbelt run obsidian recent 15               # Últimas 15 modificadas

# Escritura (pasa por Gatekeeper WARN)
blackbelt run obsidian new "999 - DIARIO/2026-09-30.md" "# Nota\n\nContenido"
blackbelt run obsidian append "999 - DIARIO/2026-09-30.md" "Línea añadida"
```
- `new` no sobrescribe. Si la nota existe, cancela.
- `append` añade al final respetando saltos de línea.
- Se ignoran carpetas ocultas (`.obsidian`, `.trash`) en búsquedas.
- Los `\n` en los argumentos se interpretan como saltos reales.

### Linux Mentor
```bash
blackbelt run linux info                       # Info del entorno
blackbelt run linux explain "tar -xzvf archivo.tar.gz"
blackbelt run linux diagnose                   # Diagnóstico básico
```
`explain` consulta al SLM local (Ollama) para desglosar el comando: qué hace, opciones, ejemplo.

### Windows Mentor
```powershell
blackbelt run windows info
blackbelt run windows explain "Get-ChildItem -Force"
blackbelt run windows diagnose
```
Consulta la ayuda local de PowerShell y pide al SLM un resumen en español del texto oficial y solo de los parámetros usados. Muestra la fuente para que puedas contrastar el resumen; el modelo puede equivocarse y no debe tratarse como autoridad. La consulta no ejecuta el comando que se está explicando. Si la ayuda local está incompleta, lo avisa; puedes instalarla con `Update-Help`. También comprueba herramientas habituales de Windows. `blackbelt run windows` muestra la información del entorno.

### Predictor de comandos
```powershell
blackbelt run suggest "encontrar archivos grandes"
blackbelt run suggest "buscar texto dentro de archivos"
blackbelt run suggest
```
`suggest` busca tareas en un catálogo local de recetas, hace preguntas para completar los parámetros y muestra el comando para el sistema detectado. No usa Ollama ni genera comandos con IA. Para recetas de frameworks, busca nombres de archivos conocidos en la carpeta actual y hasta dos niveles de subcarpetas; no lee su contenido y omite directorios generados como `.git`, `venv`, `node_modules` y `target`. Si encuentra un candidato, Enter lo acepta; si encuentra varios, hay que elegir uno. Si no encuentra ninguno, no inventa una ruta y pide que se indique manualmente.

Las recetas ejecutables piden confirmación y pasan por el Gatekeeper. Los servidores, Docker Compose y comandos de Rust se ejecutan en primer plano con salida en vivo; `Ctrl+C` los interrumpe. El Gatekeeper puede pedir una segunda confirmación según el riesgo. La activación del entorno virtual sigue siendo manual: muestra el comando para pegar en la terminal actual, porque un proceso hijo no puede activar la sesión padre. En Windows, escribir solo `H:` se interpreta como la raíz `H:\`; por ser una búsqueda recursiva amplia, pide confirmación adicional. Las operaciones de borrado y `git reset` se clasifican como críticas. Las recetas están en `blackbelt/data/recipes.yaml`.

### Aplicaciones locales

SOMA y Ghost Writer se sirven desde un servidor enlazado exclusivamente a `127.0.0.1`. El servidor permanece en primer plano; `Ctrl+C` lo detiene.

```powershell
blackbelt run apps soma
blackbelt run apps ghostwriter
```

Cada aplicación tiene un manifiesto, un icono y un Service Worker con ámbito independiente, por lo que Edge o Chrome puede instalar SOMA y Ghost Writer como aplicaciones separadas desde el menú del navegador. Deben abrirse desde estos comandos en `localhost`; abrir directamente los HTML con doble clic no habilita el Service Worker ni la instalación PWA.

Ghost Writer permite elegir explícitamente el motor en **Studio de Escritura**. `Local` es la opción predeterminada: usa `GHOSTWRITER_OLLAMA_MODEL`, después `OLLAMA_MODEL` y, si no se configura ninguno, `qwen2.5:0.5b`. Puedes cambiarlo, por ejemplo, a `qwen2.5:1.5b` con `GHOSTWRITER_OLLAMA_MODEL=qwen2.5:1.5b`. `Ollama Cloud` usa `GHOSTWRITER_OLLAMA_CLOUD_MODEL` o, por defecto, `gpt-oss:120b-cloud`; requiere iniciar sesión con `ollama signin`. La selección Cloud pide confirmación antes de cada generación: la fuente y las instrucciones se envían a Ollama Cloud. No hay fallback automático entre motores. La disponibilidad, condiciones y cuotas gratuitas dependen de Ollama y de la cuenta, y pueden cambiar.

Los límites son independientes para no elevar el consumo local: el modo local conserva contexto de 4096 y salida de 2048 tokens (`GHOSTWRITER_NUM_CTX`, `GHOSTWRITER_NUM_PREDICT`). Cloud usa contexto de 32768 y salida de 8192 tokens de forma predeterminada, configurable con `GHOSTWRITER_CLOUD_NUM_CTX` y `GHOSTWRITER_CLOUD_NUM_PREDICT` (salida máxima admitida: 16384 tokens). BlackBelt acepta respuestas de hasta 100000 caracteres para que el borrador largo no se rechace al volver del servicio. Ollama puede aplicar límites propios del modelo o del servicio, así que una cuota o límite remoto aún puede interrumpir la generación. Ambos modos aplican penalización de repetición (`repeat_penalty=1.18`, `repeat_last_n=128`). `OLLAMA_KEEP_ALIVE` conserva `0` por defecto. El timeout e hilos se pueden ajustar con `GHOSTWRITER_TIMEOUT` y `OLLAMA_NUM_THREAD`.

El botón **Guardar en bóveda** crea la nota directamente en `OBSIDIAN_VAULT/013 - PUBLICACIONES`; el servidor devuelve solo la ruta relativa, no la ruta local completa. El nombre combina fecha, plataforma y el título del frontmatter. No sobrescribe notas: si ya existe una nota con el mismo nombre, crea otra con un sufijo numérico. La carpeta debe existir y estar disponible para escritura. `GHOSTWRITER_OBSIDIAN_SUBDIR` permite cambiar el destino por otra ruta relativa dentro de la bóveda. Si la bóveda está en una carpeta sincronizada, BlackBelt escribe en la carpeta local y el cliente de sincronización se encarga de subir los cambios; no se conecta a la API del proveedor. **Descargar .MD** permanece disponible como alternativa manual.

El Tech Radar consulta feeds RSS y puede recurrir a proxies públicos cuando el navegador bloquea el acceso directo. Tailwind, iconos, fuentes y el renderizador Markdown también se cargan desde CDN, así que la app no es totalmente offline. La IA solo permanece local cuando se elige `Local`; en modo Cloud, la fuente y las instrucciones se procesan remotamente. La generación de imágenes no está integrada: Ghost Writer prepara y copia prompts. Los modelos de texto o visión de Ollama Cloud no se consideran generadores de imágenes sin soporte explícito de salida de imagen. Los datos de SOMA permanecen en el almacenamiento local del navegador; el servidor no los recibe ni los guarda.

### Clasificador de correo
Requiere IMAP habilitado en Gmail, una contraseña de aplicación de Google y Ollama local con el modelo elegido descargado. Configura la cuenta de forma interactiva; la contraseña se guarda en el almacén seguro del sistema, no en el repositorio:

```powershell
blackbelt run email configure
blackbelt run email scan --hours 48 --limit 100
blackbelt run email scan --hours 48 --limit 100 --refresh
```

El escaneo es solo de lectura: abre INBOX en modo `readonly`, solicita mensajes con `BODY.PEEK[]` para no marcarlos como leídos y nunca mueve, borra ni modifica correos. Procesa como máximo 100 mensajes por defecto (límite configurable hasta 500), limita cada descarga a 1 MB, no analiza adjuntos y envía al Ollama local el remitente, asunto y hasta 3000 caracteres del cuerpo. La salida separa avisos de prioridad de la revisión manual. Baja confianza, categorías Spam/Phishing, riesgo medio/alto o acciones inesperadas para newsletters/promociones requieren revisión y no generan recomendaciones de acción. Una importancia muy baja con urgencia alta/media se muestra como aviso, no bloquea por sí sola. `--refresh` ignora la caché durante la lectura y vuelve a clasificar; los cambios de prompt/esquema invalidan la caché automáticamente. La evaluación de phishing es orientativa, no una garantía de seguridad. La caché local guarda los campos de clasificación, no el correo original. Se puede configurar con `EMAIL_USER`, `EMAIL_APP_PASSWORD`, `EMAIL_HOURS`, `EMAIL_LIMIT`, `EMAIL_MAX_MESSAGE_BYTES`, `EMAIL_MIN_CONFIDENCE`, `EMAIL_OLLAMA_MODEL` y `EMAIL_CACHE_PATH`; para automatización, las credenciales también pueden venir del entorno.

### Gráficas de sistema
```bash
blackbelt run graphics                # Snapshot estático
blackbelt run graphics monitor        # Monitor en vivo (Ctrl+C para salir)
```
El snapshot muestra CPU, RAM, swap, discos, red y temperaturas. El monitor dibuja gráficas de CPU y RAM en tiempo real usando plotext.

### Chat con Ollama
```bash
blackbelt run chat
blackbelt run chat qwen2.5-coder:0.5b      # Especificar modelo
```
Chat interactivo con el SLM local. El modelo se mantiene cargado en RAM entre consultas (`keep_alive`), lo cual es crítico en hardware modesto.
Comandos dentro del chat: `salir`, `exit`, `quit`.

### Auditoría
```bash
blackbelt run audit          # Últimas 30 entradas
blackbelt run audit 100      # Últimas 100
```
Muestra el log del Gatekeeper con colores por nivel de riesgo.

---

## Arquitectura

```text
blackbelt/
├── blackbelt/
│   ├── __init__.py
│   ├── __main__.py            # Entry point
│   ├── main.py                # Comandos CLI (Typer)
│   ├── core/
│   │   ├── environment.py     # Detección OS/distro/shell/Python
│   │   ├── registry.py        # Registro de herramientas
│   │   ├── executor.py        # Carga y ejecuta tools
│   │   ├── config.py          # YAML + env + Ollama
│   │   └── gatekeeper.py      # Seguridad y auditoría
│   ├── tools/
│       ├── linux_mentor.py
│       ├── windows_mentor.py
│       ├── suggest.py
│       ├── apps.py             # SOMA y Ghost Writer en localhost
│       ├── graphics.py
│       ├── obsidian.py
│       ├── exec.py
│       ├── audit.py
│       ├── chat.py
│       ├── email.py           # (stub)
│       ├── feeds.py           # (stub)
│       └── search.py          # (stub)
│   ├── webapps.py              # Servidor FastAPI y generación Ollama local
│   └── data/
│       └── recipes.yaml       # Catálogo local de suggest
│       └── webapps/soma-sw.js # Caché PWA acotada al ámbito de SOMA
├── pyproject.toml
├── README.md
└── .env                       # No versionado
```
Núcleo mínimo. Herramientas como plugins.
Cada herramienta es un módulo en `tools/` que expone una función `run(args)`. Se registra en `core/registry.py` y se invoca con `blackbelt run <nombre> [args]`.

---

## Diseño del Gatekeeper

El Gatekeeper es la capa de seguridad de BlackBelt. Intercepta acciones sensibles, evalúa su riesgo y pide confirmación humana cuando toca.

### Componentes
- `analyze_command(cmd)` — Analiza un comando de shell y devuelve su nivel de riesgo (SAFE/WARN/CRITICAL) mediante regex.
- `confirm(risk, action, detail)` — Pide confirmación interactiva según el nivel. Registra la decisión en el log.
- `run_command(cmd)` — Ejecuta el comando tras analizarlo y pedir confirmación cuando corresponde. Usa PowerShell en Windows y Bash en Linux/macOS.

### Filosofía
- **La IA propone, tú dispones.** El Gatekeeper muestra qué se va a hacer antes de hacerlo. Tú decides.
- **Todo deja rastro.** Cada acción permitida o denegada se registra con timestamp, riesgo y detalles.
- **Análisis heurístico.** Las reglas regex priorizan operaciones destructivas conocidas, pero no son un sandbox y no pueden identificar con certeza lo que hace cualquier script.
- **Sin write en Obsidian.** Sobrescribir requiere un comando dedicado que todavía no existe. `new` no sobrescribe, `append` solo añade.

---

## Roadmap

### Completado
- [x] CLI con Typer + Rich
- [x] Detección de entorno
- [x] Registro modular de herramientas
- [x] Gatekeeper con 3 niveles de riesgo
- [x] Ejecución nativa en Windows PowerShell y Linux/macOS Bash
- [x] Log de auditoría
- [x] Ejecución shell con validación
- [x] Obsidian lectura (list, find, grep, read, info, recent)
- [x] Obsidian escritura (append, new) con Gatekeeper
- [x] Chat con Ollama + config por env
- [x] Gráficas de sistema (snapshot + monitor)
- [x] Linux Mentor (info, explain, diagnose)
- [x] Predictor de comandos con recetas locales y Gatekeeper
- [x] Clasificador de Gmail en modo IMAP de solo lectura

### Próximo
- [ ] Flag global `--yes` para automatización
- [ ] Cache de explicaciones (respuestas instantáneas en linux_mentor explain)
- [ ] Lector de feeds RSS (feeds)
- [ ] Búsqueda semántica con Qdrant (search)

### Futuro
- [ ] Integración MCP (Model Context Protocol)
- [ ] Snapshot previo a escrituras masivas en Obsidian
- [ ] TUI completa con paneles
- [ ] Documentación de aprendizaje Linux/Python asistida por IA

---

## Licencia

MIT
