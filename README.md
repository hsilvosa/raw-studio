# Revelado local

Aplicación local para comparar perfiles y revelar fotografías con darktable. El modelo visual solo propone parámetros numéricos de darktable. No hay generación de imágenes, reconstrucción de contenido ni herramientas de inpainting.

## Arranque

Desde PowerShell, en esta carpeta:

```powershell
./run.ps1
```

Abre http://127.0.0.1:8765. El lanzador utiliza el entorno `wuxia` si existe; en otro equipo, instala `requirements.txt` en un entorno Python y ejecuta `python -m uvicorn studio.app:app --host 127.0.0.1 --port 8765`.

Darktable debe incluir el ejecutable experimental `darktable-mcp.exe`. La instalación estable habitual no lo incluye. Puedes indicar su ruta mediante `DARKTABLE_MCP`. El puente usa JSON-RPC por stdio, una biblioteca en memoria y `write_sidecar_files=never`.

## Uso

1. Importa fotos desde el explorador local. La raíz predeterminada es este repositorio; `PHOTO_ROOT` permite cambiarla antes de iniciar.
2. Selecciona una imagen, perfil, intensidad y compensación de exposición.
3. Revela un perfil o compara los ocho. Selecciona una miniatura para examinar la receta y comparar con el revelado base.
4. Exporta la selección a PNG de hasta 6000 píxeles en `PROCCESED/PERFILES/<perfil>/studio_<imagen>/`. Cada exportación tiene un nombre único y su receta JSON. No se sobrescriben pruebas anteriores ni se crean carpetas por iteración.

El antes es el revelado base de darktable, no el RAW sin interpretar. El zoom 100 % corresponde a la previsualización de 1600 píxeles, no al RAW a resolución completa.

## Modelo local

Se utiliza Qwen3-VL-4B-Instruct Q4_K_M con llama.cpp. Descarga los pesos una vez y arranca el servidor en otra terminal:

```powershell
./scripts/download-model.ps1
./scripts/run-model.ps1
```

Activa «Adaptar con modelo local» cuando aparezca conectado. `MODEL_URL` permite utilizar otro servidor compatible, siempre en localhost. Las imágenes no salen del equipo. La descarga de los pesos sí requiere Internet.

El modelo recibe una previsualización, mediciones y la receta actual; devuelve JSON. La aplicación valida módulos, campos, tipos y límites antes de llamar a darktable. Una propuesta inválida falla de forma visible y nunca se ejecuta. No se permite al modelo elegir rutas, ejecutar comandos, suministrar parámetros binarios o escribir píxeles.

Fuentes: [pesos oficiales de Qwen](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF), [llama.cpp](https://github.com/ggml-org/llama.cpp).

## Estado y límites

La primera versión incluye biblioteca persistente, copias verificadas, ocho perfiles, adaptación tonal acotada, comparación, cola de trabajo, conector visual local y exportación. Los perfiles se han separado de los recortes y el desenfoque de las pruebas antiguas: no se transfieren geometrías de una escena a otra.

La adaptación tonal inicial es una heurística de luminancia, no una evaluación estética. El modelo propone una única revisión; aún no hay segmentación, máscaras locales, balance de blancos adaptativo, reducción de ruido por ISO, preferencias aprendidas ni historial de comparaciones en la interfaz tras reiniciar. Los resultados y recetas permanecen en disco. La calidad artística del modelo requiere evaluación con más escenas.

Los RAW y sus XMP originales no se editan. `.studio` contiene copias, biblioteca, modelos, previsualizaciones y registros; está fuera de Git, igual que las fotografías y exportaciones. Solo el código y las recetas de perfil forman parte del repositorio.

## Pruebas

```powershell
python -m pytest tests -q
```

Las pruebas cubren el contrato del modelo, preservación de originales, límites de rutas y acceso local. Las pruebas de integración requieren darktable y fotografías reales.
