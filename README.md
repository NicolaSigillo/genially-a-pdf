# Genially a PDF (Microapp Local)

Convierte cualquier presentación **pública** de Genially (`https://view.genially.com/...`) en un archivo **PDF limpio, visible y descargable**, sin botones flotantes ni marcas de agua y con texto buscable (`Ctrl+F`).

Funciona 100% en tu propia computadora, sin subir tus clases a ningún servidor externo.

---

## Requisitos previos (solo revisar una vez)

1. **Google Chrome** o **Microsoft Edge** instalado (en Windows 10/11 Edge ya viene incluido).
2. **Python 3.10 o superior**:
   - Si no lo tienes, descárgalo en [python.org/downloads](https://www.python.org/downloads/).
   - **MUY IMPORTANTE en Windows:** al instalar Python, marca la casilla **"Add python.exe to PATH"** antes de dar clic en *Install Now*.

---

## Cómo abrir y usar la microapp (Windows — Paso a paso)

### Paso 1: Descargar la carpeta (si vienes desde GitHub)
- Opción A (sin usar comandos): arriba a la derecha haz clic en el botón verde **`<> Code`** → **`Download ZIP`**, y descomprime la carpeta en tu Escritorio o Documentos.
- Opción B (con Git):
  ```bash
  git clone https://github.com/NicolaSigillo/genially-a-pdf.git
  ```

### Paso 2: Abrir la aplicación
1. Abre la carpeta del proyecto.
2. Haz **doble clic** en el único archivo ejecutador de la carpeta principal:
   - **`Abrir_Genially_a_PDF.bat`** *(si Windows oculta las extensiones, se verá simplemente como **`Abrir_Genially_a_PDF`** con ícono de engranajes)*.
   - **Nota:** La **primera vez** que lo abras en una PC nueva tardará 1–2 minutos creando el entorno `.venv` e instalando las librerías. Las veces siguientes abrirá en 1–2 segundos.
   - Si Windows muestra *"Windows protegió su PC"*, haz clic en **"Más información"** → **"Ejecutar de todas formas"**.
3. Se abrirá una ventana negra de consola (déjala abierta mientras uses la app) y automáticamente se abrirá tu navegador en:
   **`http://127.0.0.1:8765`**

### Paso 3: Convertir una presentación de Genially a PDF
1. En la página web, pega el enlace de Genially en el cuadro de texto (por ejemplo `https://view.genially.com/67c88cee24ddcabc9eff7571`, o pulsa **"Usar link de clase de ejemplo"**).
2. Deja la calidad en **"Alta (2× nítida, recomendada)"** y marcada la casilla **"Texto buscable (Ctrl+F)"**.
3. Pulsa **"Convertir a PDF"** y espera unos segundos mientras captura todas las diapositivas (verás el porcentaje y la miniatura en vivo).
4. Cuando termine:
   - Puedes **ver el PDF completo** directamente dentro de la misma página.
   - Pulsa **"Descargar PDF"** para guardarlo donde quieras (además queda guardado automáticamente dentro de la carpeta `output/`).

### Paso 4: Cerrar cuando termines
- Solo cierra la ventana negra de `Abrir_Genially_a_PDF` (o presiona `Ctrl + C` en ella).

---

## Cómo abrirlo en macOS o Linux

1. Abre una terminal dentro de la carpeta del proyecto.
2. Ejecuta:
   ```bash
   chmod +x mac_linux/iniciar_mac_linux.sh
   ./mac_linux/iniciar_mac_linux.sh
   ```
3. Se abrirá `http://127.0.0.1:8765` en tu navegador.

---

## Preguntas frecuentes / Solución de problemas

- **No se abre mi navegador automáticamente:** entra manualmente a `http://127.0.0.1:8765` mientras la ventana negra esté abierta.
- **Error de que no se encontró Python:** instala Python 3.12 desde [python.org/downloads](https://www.python.org/downloads/) marcando *"Add python.exe to PATH"*, borra la carpeta `.venv` si se creó vacía y vuelve a abrir `Abrir_Genially_a_PDF.bat`.
- **Error de que el enlace es privado:** la microapp solo puede leer presentaciones públicas o accesibles sin contraseña desde tu navegador.
- **No veo dónde quedan guardados los PDFs:** todos se guardan en la carpeta `output/` junto al programa, y aparecen también en el historial inferior de la página.
