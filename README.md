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
2. Explora la biblioteca con scroll independiente o pulsa **⛶ Expand** para abrir la vista en cuadrícula amplia de miniaturas.
3. Selecciona una imagen, perfil, intensidad y compensación de exposición.
4. Revela un perfil o compara todos los disponibles. Selecciona una miniatura para examinar la receta y comparar con el revelado base.
5. Utiliza los controles de zoom (`Fit`, `−`, `+`, `100%`, `Full Page`), la tecla `F` (o `Esc` para salir), la rueda del ratón centrada en cualquier punto o arrastra con el ratón para desplazarte de forma sincronizada entre el antes y el después. En modo Full Page, la barra inferior de perfiles permanece discretamente minimizada y se desliza con suavidad al mover el ratón a la parte inferior.
6. Exporta la selección a PNG de hasta 6000 píxeles en `PROCCESED/PERFILES/<perfil>/studio_<imagen>/`. Cada exportación tiene un nombre único y su receta JSON.

Los detalles de las versiones y cambios están documentados en [CHANGELOG.md](file:///d:/FOTOS/revelado-local/CHANGELOG.md).

## Modelo local y supervisión

Se utiliza Qwen3-VL-4B-Instruct Q4_K_M con llama.cpp sobre GPU local. La descarga y arranque pueden gestionarse con los scripts dedicados:

```powershell
./scripts/download-model.ps1
./scripts/run-model.ps1
```

La aplicación incluye un supervisor automático (`ensure_server()`) que verifica la disponibilidad del modelo en `http://127.0.0.1:8081/v1` y monitoriza su estado de salud en tiempo real.

El modelo recibe una previsualización de la imagen, telemetría fotográfica (luminancia, recorte de blancos, dominantes de color y presencia de tonos de piel) y la receta base; devuelve un objeto JSON estructurado con ajustes acotados y justificación estética en español. La aplicación valida estrictamente los módulos y rangos permitidos (`LIMITS` en `studio/recipes.py`) antes de invocar a Darktable por stdio JSON-RPC.

## Adaptación fotográfica avanzada

El motor de adaptación (`studio/adaptation.py`) analiza la fotografía antes de proponer y aplicar el revelado:

- **Balance de blancos por escena**: Detecta dominantes cromáticas en tonos medios neutros y compensa suavemente la temperatura (`temp_bias`) y el tinte (`tint_bias`) sin alterar intenciones artísticas marcadas.
- **Protección de tonos de piel**: Identifica regiones de piel humana mediante segmentación en espacios HSV y YCbCr. Si se detectan tonos de piel, acota aumentos agresivos de contraste y vibranza, aplicando curvas suaves para preservar la naturalidad de los rostros.
- **Protección de blancos y altas luces**: Monitoriza percentiles altos de luminosidad (P98 y P99.5). En escenas con riesgo de sobreexposición, ajusta la caída de altas luces en el módulo sigmoid (`sig_highlight_rolloff`) y atenúa la exposición.
- **Enfoque y ruido adaptativo**: Estima la varianza de ruido de alta frecuencia (`noise_sigma`). En escenas de alto ISO o ruido notable, incrementa el umbral de enfoque (`sharpen_threshold`) y eleva el perfil de reducción de ruido bilateral, evitando amplificar el grano.

## Evaluación del modelo y perfiles

Para verificar objetivamente las decisiones del modelo frente a perfiles fijos y adaptaciones algorítmicas, se incluye una suite de evaluación (`studio/evaluator.py` y `scripts/evaluate-model.py`):

```powershell
python ./scripts/evaluate-model.py --profile 09_PORTRA_WARM
```

El script compara 3 etapas en paralelo (Perfil Fijo, Adaptación Algorítmica y Modelo Qwen3-VL), midiendo:
- Porcentaje de píxeles quemados (recorte de blancos >99.5%).
- Porcentaje de sombras empastadas (recorte de negros <0.5%).
- Rango dinámico efectivo (EV).
- Puntuación de armonía de tonos de piel (0–100).

Genera automáticamente un informe interactivo con miniaturas comparativas e histogramas en `.studio/reports/evaluation_report.html` y `.studio/reports/evaluation_summary.json`.

## Estado y seguridad

- Los archivos RAW y XMP originales nunca se modifican ni se eliminan.
- Todo el procesamiento es estrictamente local; ninguna imagen ni telemetría sale del equipo.
- Las recetas y máscaras son reproducibles y no destructivas.

## Pruebas

```powershell
python -m pytest tests -q
```

Las pruebas cubren la supervisión del modelo local, el motor de adaptación fotográfica, la segmentación y ajuste de zonas, y el aislamiento de rutas.
