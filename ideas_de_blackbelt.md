# 💡 Ideas para BlackBelt

Documento vivo. Nada aquí es compromiso. Son semillas.
Última revisión: 2026-10-01

---

## ✅ Sprint 1 — Cerrado (2026-09-30)
- CLI + registry + executor
- Gatekeeper (SAFE/WARN/CRITICAL) + audit log
- Obsidian read/write
- `exec` con bash completo
- Chat + Linux Mentor + Graphics
- Tool `git` con `sync`
- Multiplataforma: funciona en Linux (Mabox) y Windows 10
- Repo en GitHub con SSH configurado

---

## 🌱 Ideas en cola (Sprint 2+)

### 1. `blackbelt run journal`
Cada noche junta en una nota de Obsidian:
- Commits del día (git log)
- Notas modificadas hoy
- Comandos más usados (audit log)
- Una línea reflexiva generada por Ollama

Al día siguiente, te muestra lo de ayer. En un año, 365 entradas que no escribiste tú pero son tuyas.

### 2. Tutor pasivo (⭐ favorita)
BlackBelt observa sin bloquear lo que haces en el terminal. Cuando ejecutas un comando que no conoce, añade un comentario al final:

```text
$ tar -xzvf archivo.tar.gz
...output...
💡 tar descomprime. -x extrae, -z gzip, -v verbose, -f fichero.
```

Modo `--aprende`. Tú trabajas, el mentor te habla al oído. Es el objetivo original del briefing: un sysadmin en prácticas disponible 24/7.

### 3. `blackbelt run recipes`
Recetario personal de comandos con parámetros:

```bash
blackbelt run recipes save "buscar pdf escaneado" "find {dir} -name '*.pdf' | xargs pdfgrep -i {texto}"
blackbelt run recipes find pdf
```

Snippets vivos, versionados en git.

### 4. Snapshot de la bóveda (rclone)
Antes de escrituras masivas en Obsidian (>X ficheros o >Y KB), lanzar `rclone sync` a destino frío (otro Drive, disco externo, lo que haya). Cierra el agujero que dejamos abierto por pragmatismo.

### 5. Modo "¿estás cansado?"
Analiza la sesión: hora, comandos repetidos, errores, uso de `--help`. Si detecta tumbos, avisa:

```text
Has ejecutado 4 comandos seguidos que han fallado.
Son las 01:53. ¿Cerramos?
```

Higiene mental, no paternalismo.

### 6. MCP a lo práctico
No todo MCP: **un conector, no una arquitectura**. Por ejemplo, un MCP de n8n que lance tu workflow de tech news discovery desde BlackBelt. Enchufar cuando sirva, apagar cuando no.

### 7. `blackbelt run karma`
Mini-resumen de una línea al final del día:

```text
Hoy: 12 comandos, 2 commits, 3 notas tocadas, 1 cosa aprendida.
```

Archivito por día. Dashboard mensual de tu propio trabajo.

### 8. Cache de explicaciones
`linux_mentor explain` guarda en JSON las respuestas. La segunda vez que preguntas por `ls -la`, respuesta instantánea. Crítico en hardware modesto.

### 9. Flag global `--yes`
`blackbelt --yes run exec "..."` salta confirmaciones del Gatekeeper. Útil para scripts y automatización. Interactivo por defecto, `--yes` opcional.

---

## 💭 Idea 8 — Capa visual sobre BlackBelt

**Fecha:** 2026-09-30
**Estado:** concepto, sin explorar. **Opt-in, no reemplazo.**

### La idea original
"Como estoy en Mabox y he visto la ligereza de su UI, y conociendo proyectos como Kolibri OS o Gentle OS... crear una instancia visual dentro del SO principal. Quizás Streamlit o Gradio. Traducir comandos CLI a clicks de ratón."

### Reformulación
No es crear un SO dentro del SO. Es añadir **una segunda interfaz** al mismo núcleo. BlackBelt ya separa `core/` (lógica) de `tools/` (acciones). La CLI es una frontend. Una UI web sería otra frontend.

### Regla de oro
La CLI sigue siendo la única verdad. La interfaz gráfica es **opt-in**:

```bash
blackbelt run serve              # levanta la web en localhost
```

Si no lo invocas, no existe. Ni RAM, ni puertos, ni dependencias cargadas. BlackBelt nace CLI y muere CLI.

### Arquitectura limpia
- `blackbelt/core/` → lógica pura, sin prints, devuelve datos
- `blackbelt/cli/` → frontend terminal (lo que hay ahora)
- `blackbelt/web/` → frontend navegador (nuevo)
- `blackbelt run serve` → lanza la web local

### Retos técnicos
- **Refactor de tools** — ahora imprimen con Rich. Para GUI, deben devolver estructuras. Cambio de contrato.
- **Peso en 4 GB** — Streamlit ~400MB, Gradio similar. Textual ~50MB. FastAPI + HTMX ~50MB.
- **Gatekeeper en web** — las confirmaciones WARN/CRITICAL necesitan diálogo en el navegador.
- **Venvs y subprocesos** — cada tool que corra un binario externo necesita gestión.

### Frameworks candidatos

| Framework | RAM | Pros | Cons |
| :--- | :--- | :--- | :--- |
| Streamlit | ~400MB | Rápido de prototipar | Pesado, poco control |
| Gradio | ~350MB | Bueno para ML | Pensado para demos |
| FastAPI + HTMX | ~50MB | Ligero, control total | Más código |
| Textual (TUI) | ~30MB | Todo en terminal | No es "visual" de ratón |
| Flask + templates | ~40MB | Simple, estable | Anticuado |

Para 4 GB, FastAPI + HTMX es la opción realista.

### MVP hipotético
- `blackbelt run serve` levanta FastAPI en `localhost:8765`
- Vista principal: estado del sistema (reutiliza graphics)
- Vista Obsidian: lista notas recientes, abrir, editar
- Vista Git: status, sync con botón
- Confirmaciones Gatekeeper como modales HTML
- Nada de WARN/CRITICAL sin pasar por humano, igual que en CLI

### Por qué importa
- Accesible desde móvil en la red local.
- Más cómodo para tareas repetitivas (sync, journal).
- No pierde nada: la CLI sigue siendo la interfaz principal.
- Aprendizaje: FastAPI, HTMX, arquitectura de dos frontends.

### Por qué esperar
- Refactor de tools primero (que devuelvan datos, no prints).
- Cache de explicaciones y `--yes` antes (20-30 min cada uno).
- Si no, arrastramos deuda y reescribimos.

### Notas
- Kolibri OS y Gentle OS son sistemas operativos completos y minimalistas. No es el modelo a seguir. La idea es capa, no SO.
- Cualquier cosa que se añada debe poder apagarse sin romper la CLI.

---

## 🚀 Distribución futura — PyPI

**Fecha:** 2026-09-30
**Estado:** idea registrada, no prioritaria

### Qué es
PyPI (Python Package Index) es el repositorio público donde viven los paquetes de Python. Cualquiera puede publicar su paquete y cualquiera puede instalarlo con `pip` o `pipx`. Es el equivalente a una "tienda de apps" para Python.

### Para qué sirve
- **Distribuir:** publicar BlackBelt para que otros lo instalen con un solo comando.
- **Instalar:** que tú mismo puedas instalarlo en cualquier máquina sin clonar el repo.

### Cómo se instala un paquete de PyPI en el PATH
La clave es que el comando `blackbelt` quede accesible desde cualquier directorio. Tres formas:
1. `pip install --user` — instala en `~/.local/` y añade `~/.local/bin` al PATH. Simple, pero puede romper dependencias.
2. `pipx install` — crea un entorno aislado por paquete y expone el ejecutable en `~/.local/bin` (que ya suele estar en el PATH). La forma limpia para herramientas CLI.
3. `pip install` dentro de un venv activado — solo disponible mientras el venv esté activo. No es "global".

### Recomendación para BlackBelt
Cuando el proyecto madure, la forma correcta de distribución será `pipx`:
- Cada usuario tiene su propio entorno aislado.
- `blackbelt` queda en el PATH sin tocar el Python del sistema.
- Un comando: `pipx install blackbelt`.

### Cuándo
No ahora. Cuando el MVP esté cerrado, estable y probado en uso real. Antes, no.

### Pasos cuando toque

```bash
# 1. Crear cuenta en pypi.org
# 2. Instalar twine: pip install twine
# 3. Construir: python -m build
# 4. Subir: twine upload dist/*
# 5. En cualquier máquina: pipx install blackbelt
```

---

## 📝 Notas sueltas
- Si una idea no se usa en una semana, se aparca. Sin culpa.
- Cualquier añadido debe poder apagarse sin romper la CLI.
- La CLI es la interfaz primaria. Todo lo demás es opcional.