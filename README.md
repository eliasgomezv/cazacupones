# Cazacupones

Revisa cada 10 minutos los perfiles de X que elijas y te manda una notificación al celular cuando alguno publica algo con la palabra cupón (o cupon, o cupones). Además tiene una página web con el historial y un botón para copiar el código del cupón.

Todo corre gratis en GitHub (no necesitás servidor ni PC prendida):

- **GitHub Actions** ejecuta la búsqueda cada 10 minutos.
- **GitHub Pages** publica la web con los hallazgos. Agregada a la pantalla de inicio del iPhone funciona como app y recibe las notificaciones (no hay que instalar nada).
- **Telegram** es opcional, por si también querés los avisos ahí.

---

## Antes de empezar: de dónde salen los datos

X ya no tiene acceso gratuito a su API de búsqueda. Hay dos opciones y se elige en `config.json` (`"fuente"`):

| | `"cookies"` (por defecto) | `"api"` |
|---|---|---|
| Costo | Gratis | Pago por uso (aprox. US$0,005 por post leído) |
| Cómo funciona | Usa la sesión de una cuenta de X, como si buscaras desde el navegador | API oficial de X |
| Riesgo | Va contra los términos de X: **usá una cuenta secundaria**, nunca la personal. Puede dejar de andar si X cambia algo | Ninguno |

---

## Instalación (una sola vez, ~15 minutos, mejor desde la PC)

### 1. Subir el proyecto a GitHub
1. Creá una cuenta en github.com si no tenés.
2. **New repository** → nombre `cazacupones` → **Public** → Create.
   (Público porque así Actions y Pages son gratis sin límite. Tus claves van en *Secrets* y no se ven.)
3. En el repo vacío: **uploading an existing file** → arrastrá todo el contenido de la carpeta (incluida la carpeta `.github`) → **Commit changes**.
4. Verificá que exista `.github/workflows/buscar.yml`. Si no se subió, hacé **Add file → Create new file**, escribí ese nombre exacto y pegá el contenido del archivo.

### 2. Sacar las cookies de la cuenta secundaria de X
1. En Chrome de la PC abrí una **ventana de incógnito** e iniciá sesión en x.com con la cuenta secundaria.
2. Apretá **F12** → pestaña **Application** → **Cookies** → `https://x.com`.
3. Copiá el valor de `auth_token` y el de `ct0`.
4. **Cerrá la ventana de incógnito sin cerrar sesión** (si cerrás sesión, las cookies dejan de servir).

### 3. Cargar los secretos de X en GitHub
En el repo: **Settings → Secrets and variables → Actions → New repository secret**. Creá:

| Nombre | Valor |
|---|---|
| `X_COOKIES` | `auth_token=VALOR; ct0=VALOR` |
| `X_USUARIO` | el usuario de la cuenta secundaria (sin @) |

### 4. Activar la web
**Settings → Pages** → Source: **Deploy from a branch** → Branch: `main`, carpeta `/docs` → **Save**.
En un par de minutos queda en `https://TU-USUARIO.github.io/cazacupones/`.

### 5. Activar las notificaciones en el iPhone (iOS 16.4 o más nuevo)
1. Abrí la web en **Safari** → **Compartir** → **Agregar a inicio**.
2. Abrí **Cupones** desde ese ícono nuevo (desde Safari no funciona).
3. Tocá **Activar notificaciones** y aceptá el permiso.
4. La web te muestra un texto: tocá **Copiar texto**.
5. En GitHub: **Settings → Secrets and variables → Actions → New repository secret**, nombre `WEBPUSH`, pegá el texto.

Ese texto incluye una clave que se generó en tu celular: no lo compartas. Para recibir avisos en varios celulares, repetí en cada uno y guardá los textos juntos en `WEBPUSH` como lista: `[ {...texto 1...}, {...texto 2...} ]`.

### 6. Probar
1. Pestaña **Actions** → si pide habilitar workflows, aceptá.
2. Elegí **Buscar cupones** → **Run workflow** → marcá *Solo mandar una notificación de prueba* → **Run**. Te tiene que llegar un aviso al iPhone.
3. Corrélo otra vez sin marcar la casilla: hace una búsqueda real. Si algo falla, abrí la ejecución y mirá el mensaje en rojo.

Listo: desde ahora corre solo cada 10 minutos.

---

## Elegir los perfiles y qué se busca

Editá `config.json` (desde la web de GitHub: abrir el archivo → lápiz → Commit). Lo principal es la lista `perfiles`:

```json
"perfiles": ["cuenta1", "@cuenta2", "https://x.com/cuenta3"],
"consulta": "(cupon OR cupón OR cupones)",
```

Podés poner el usuario con o sin @, o pegar el link del perfil. No hay límite práctico: si la lista es larga, la app la divide en varias búsquedas sola. **Mientras la lista esté vacía no busca nada.**

Cada búsqueda tiene:

| Campo | Para qué sirve |
|---|---|
| `nombre` | Lo que ves en la notificación y en la web |
| `activa` | `true` / `false` para pausarla sin borrarla |
| `perfiles` | Cuentas de X a revisar. Si sacás este campo, busca en todo X |
| `consulta` | Las palabras a buscar, con la sintaxis del buscador de X. Si la dejás vacía (`""`), avisa de todos los posts de esos perfiles |
| `debe_contener_alguna` | Opcional. Solo avisa si el post tiene alguna de estas palabras |
| `excluir` | Opcional. Descarta posts con estas palabras (ej. `"sorteo"`) |

Sintaxis útil para `consulta`:
- `cupon 5000` → posts con ambas palabras.
- `"cupon 5000"` → la frase exacta.
- `(cupon OR cupón)` → cualquiera de las dos.
- `-sorteo` → sin esa palabra.
- `from:usuario` → solo de esa cuenta.

Opciones generales:

| Campo | Valor inicial | Qué hace |
|---|---|---|
| `antiguedad_maxima_minutos` | 120 | Ignora posts más viejos que esto (así solo llegan cosas recientes) |
| `solo_espanol` | false | Agrega `lang:es`. Con perfiles conviene dejarlo apagado: X a veces no detecta el idioma de posts cortos como "CUPON MELI5000" |
| `excluir_retweets` | true | Descarta retuits |
| `excluir_respuestas` | false | Descarta respuestas a otros posts |
| `max_resultados_por_busqueda` | 25 | Cuántos posts trae por búsqueda en cada vuelta |
| `max_notificaciones_por_ejecucion` | 8 | Más que esto se agrupa en un solo aviso |
| `horario_descanso` | 02:00 a 07:00 | Franja en la que no busca, para no parecer un bot activo las 24 h. Lo publicado en ese rato se revisa en la primera búsqueda de la mañana. Para desactivarlo: `{}` |
| `demora_al_azar_segundos` | 240 | Cada búsqueda espera entre 0 y esto antes de arrancar, para no ser exacta |
| `zona_horaria` | America/Argentina/Buenos_Aires | Zona para interpretar el horario de descanso |
| `prioridad_notificacion` | 4 | 4 o 5 = urgente (el iPhone lo entrega enseguida aunque esté en ahorro de batería); 1 a 3 = normal |

**¿Está corriendo?** Debajo del título de la web aparece "Última revisión hace X min". Si dice *está demorada* o *falló*, mirá la pestaña Actions.

**Frecuencia:** está en `.github/workflows/buscar.yml`, línea `cron: "*/10 * * * *"`. Podés poner `*/5` (mínimo de GitHub) o `*/15`.

---

## Si algo deja de andar

- **Te llega "Cazacupones: hay un problema"**: casi siempre son las cookies vencidas. Repetí los pasos 3 y 4 (solo `X_COOKIES`). El aviso se repite como máximo cada 6 horas.
- **No llegan notificaciones**: corré la prueba del paso 6 y mirá el mensaje. Si dice que la suscripción venció (pasa si borrás el ícono de inicio o restaurás el iPhone), repetí el paso 5 y reemplazá el secreto `WEBPUSH`. Revisá también que las notificaciones de **Cupones** estén permitidas en Ajustes del iPhone.
- **De un perfil nunca llega nada**: X oculta algunas cuentas de su buscador (pasa con cuentas que publican mucho o tienen links). Probá buscar `from:esacuenta cupon` en x.com: si ahí tampoco aparece, la app no lo va a ver por búsqueda.
- **Demoras o no corre solo**: GitHub a veces atrasa o saltea las ejecuciones programadas, sobre todo en repos nuevos y en horas pico. Para que sea puntual, usá un disparador externo gratuito:
  1. GitHub → tu foto → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**. Repositorio: solo `cazacupones`. Permiso: **Actions → Read and write**. Copiá el token.
  2. En **cron-job.org** (gratis) → **Create cronjob** → URL `https://api.github.com/repos/TU-USUARIO/cazacupones/actions/workflows/buscar.yml/dispatches`, cada 10 minutos. En *Advanced*: método **POST**; headers `Authorization: Bearer TU_TOKEN`, `Accept: application/vnd.github+json` y `User-Agent: cazacupones`; body `{"ref":"main"}`.
  3. **Test run** tiene que dar 204 y aparecer una ejecución nueva en Actions.
- **Se pausó solo**: GitHub desactiva las tareas programadas de repos sin actividad durante 60 días. Cada hallazgo guarda un cambio en el repo, así que no debería pasar; si pasa, reactivalo en **Actions**.

---

## Opcional: avisos por Telegram

1. En Telegram hablale a **@BotFather** → `/newbot` → guardá el token.
2. Mandale cualquier mensaje a tu bot nuevo.
3. Abrí `https://api.telegram.org/botTU_TOKEN/getUpdates` y buscá `"chat":{"id": NUMERO`.
4. Cargá los secretos `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID`.

Podés usar las notificaciones de la web, Telegram o los dos.

## Opcional: API oficial de X (pago)

1. Creá una app en console.x.com, cargá créditos y **poné un límite de gasto**.
2. Copiá el *Bearer Token* y cargalo como secreto `X_BEARER_TOKEN`.
3. En `config.json` poné `"fuente": "api"`.

X cobra por cada post que devuelve la búsqueda (aunque después lo descarte el filtro `excluir`), así que conviene que la `consulta` ya sea precisa. Como referencia: 30 posts nuevos por día son unos 900 por mes, alrededor de US$4,50. Verificá los precios actuales en la consola de X.

## Correrlo en tu PC (opcional)

```bash
pip install -r requirements.txt
export X_COOKIES="auth_token=...; ct0=..."  X_USUARIO="tu_secundaria"  WEBPUSH='{...texto de la web...}'
python buscar.py --prueba   # notificación de prueba
python buscar.py            # una búsqueda
```
