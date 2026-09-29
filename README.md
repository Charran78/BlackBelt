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
| `run graphics` | ✅ | Snapshot y monitor en vivo (CPU, RAM, disco, red) |
| `run exec` | ✅ | Ejecución de comandos shell con Gatekeeper |
| `run audit` | ✅ | Visor del log de auditoría |
| `run obsidian` | ✅ | Lectura y escritura controlada de la bóveda |
| `run chat` | ✅ | Chat interactivo con Ollama local |
| `run email` | 🔜 | Clasificador de correo local |
| `run feeds` | 🔜 | Lector de feeds RSS |
| `run search` | 🔜 | Búsqueda semántica con Qdrant |

---

## Instalación

**Requisitos:**
- Python 3.11+
- Linux, macOS o Windows (probado en Mabox Linux)
- Opcional: [Ollama](https://ollama.com/) para los módulos de IA

```bash
git clone <repo> blackbelt
cd blackbelt
python -m venv .venv
source .venv/bin/activate
pip install -e .
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
OBSIDIAN_VAULT="/home/pedro/VaultGdrive/DriveSyncFiles/Mi bóveda Cloud"
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
│   └── tools/
│       ├── linux_mentor.py
│       ├── graphics.py
│       ├── obsidian.py
│       ├── exec.py
│       ├── audit.py
│       ├── chat.py
│       ├── email.py           # (stub)
│       ├── feeds.py           # (stub)
│       └── search.py          # (stub)
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
- `run_command(cmd)` — Ejecuta un comando shell pasándolo antes por analyze y confirm. Usa bash como intérprete para soportar `~`, `$VAR`, pipes y globs.

### Filosofía
- **La IA propone, tú dispones.** El Gatekeeper muestra qué se va a hacer antes de hacerlo. Tú decides.
- **Todo deja rastro.** Cada acción permitida o denegada se registra con timestamp, riesgo y detalles.
- **Fallo seguro.** Si algo no se clasifica claramente, sube de nivel. Mejor preguntar de más que de menos.
- **Sin write en Obsidian.** Sobrescribir requiere un comando dedicado que todavía no existe. `new` no sobrescribe, `append` solo añade.

---

## Roadmap

### Completado
- [x] CLI con Typer + Rich
- [x] Detección de entorno
- [x] Registro modular de herramientas
- [x] Gatekeeper con 3 niveles de riesgo
- [x] Log de auditoría
- [x] Ejecución shell con validación
- [x] Obsidian lectura (list, find, grep, read, info, recent)
- [x] Obsidian escritura (append, new) con Gatekeeper
- [x] Chat con Ollama + config por env
- [x] Gráficas de sistema (snapshot + monitor)
- [x] Linux Mentor (info, explain, diagnose)

### Próximo
- [ ] Flag global `--yes` para automatización
- [ ] Cache de explicaciones (respuestas instantáneas en linux_mentor explain)
- [ ] Clasificador de correo (email)
- [ ] Lector de feeds RSS (feeds)
- [ ] Búsqueda semántica con Qdrant (search)

### Futuro
- [ ] Integración MCP (Model Context Protocol)
- [ ] Snapshot previo a escrituras masivas en Obsidian
- [ ] TUI completa con paneles
- [ ] Soporte Windows nativo
- [ ] Documentación de aprendizaje Linux/Python asistida por IA

---

## Licencia

MIT
# prueba
