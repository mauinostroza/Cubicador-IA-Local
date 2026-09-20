# Ensamblado cerrado del payload OCR

`scripts/assemble_ocr_payload.py` es una operación **offline** de CI. Solo
acepta archivos ya presentes cuyo tamaño y SHA-256 estén fijados en
`ocr-release.lock.json`. No descarga, instala, importa ni ejecuta activos.

El payload se publica de forma atómica solo cuando el cierre schema 2 incluye:

- runner Windows x64 mínimo;
- runtime Paddle CPU;
- ocho archivos directos de detección y reconocimiento PP-OCRv5 mobile.

El diccionario de caracteres no es un activo separado: está embebido por el
modelo oficial de reconocimiento en `config.json`/`inference.yml`. Duplicarlo
crearía dos fuentes de verdad y queda prohibido.

El wheel fijado de PaddlePaddle se trata solo como contenedor ZIP: se valida y
se extrae con cuotas y controles de rutas, enlaces, duplicados y compresión.
Cada archivo resultante se vuelve a hashear y se incluye individualmente en el
manifest; el `.whl` y los tarball nunca forman parte del payload final.

Si falta un pin, un archivo no coincide o un activo sigue bloqueado, no se crea
ningún payload. Esta etapa no cambia `release_enabled` ni los feature gates.

## Dependencias prohibidas

El runner de release no puede incorporar `paddleocr`, `paddlex`, `requests`,
`aiohttp`, gestores de modelos, actualizadores, navegadores, shell, plugins ni
clientes HTTP. Tampoco puede buscar herramientas en `PATH`, descargar modelos
o escribir fuera del workspace autorizado. El wheel de PaddlePaddle es una
entrada fijada, pero no se ejecuta durante el ensamblado.

El ejecutable del worker solo podrá compilarse en un job de CI separado cuando
el compilador/empaquetador y todo su cierre también estén fijados. Su ejecutable
resultante deberá volver al lock como artefacto con procedencia, tamaño y hash;
este checkpoint no fabrica un binario no verificable.

## Red en CI

El job de comprobación puede usar la red administrada por GitHub únicamente
para obtener las acciones fijadas y preparar Python. El ensamblador no contiene
cliente HTTP, no descarga activos y recibe exclusivamente archivos locales ya
fijados. La futura adquisición de activos será otro job de staging, separado
del ensamblado y de la ejecución local.
