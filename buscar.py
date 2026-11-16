#!/usr/bin/env python3
"""
Cazacupones: busca términos en X cada N minutos y avisa al celular.

Pensado para correr en GitHub Actions (ver .github/workflows/buscar.yml),
pero funciona en cualquier PC con Python 3.10+:

    pip install -r requirements.txt
    X_COOKIES="auth_token=...; ct0=..." NTFY_TOPIC="mi-tema" python buscar.py

Opciones:
    --prueba    manda una notificación de prueba y termina.
"""
from __future__ import annotations

import os

os.environ.setdefault("TWS_TELEMETRY", "0")  # twscrape: sin telemetría

import asyncio
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
ARCHIVO_CONFIG = RAIZ / "config.json"
ARCHIVO_ESTADO = RAIZ / "data" / "estado.json"
ARCHIVO_RESULTADOS = RAIZ / "docs" / "resultados.json"
ARCHIVO_DB_TWSCRAPE = RAIZ / ".twscrape.db"

MAX_VISTOS = 3000
MAX_RESULTADOS = 300
HORAS_ENTRE_AVISOS_DE_ERROR = 6
TIMEOUT_BUSQUEDA_SEG = 90
EPOCH_X_MS = 1288834974657  # para sacar la fecha de un ID de X

CONFIG_POR_DEFECTO = {
    "fuente": "cookies",
    "busquedas": [],
    "solo_espanol": True,
    "excluir_retweets": True,
    "excluir_respuestas": False,
    "antiguedad_maxima_minutos": 120,
    "max_resultados_por_busqueda": 25,
    "max_notificaciones_por_ejecucion": 8,
    "prioridad_notificacion": 4,
}


# ----------------------------------------------------------------- utilidades


@dataclass
class Post:
    id: str
    texto: str
    fecha: datetime
    url: str
    usuario: str | None = None
    es_retweet: bool = False
    es_respuesta: bool = False


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def a_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def desde_iso(texto: str) -> datetime:
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


def fecha_de_id(id_post: str) -> datetime:
    ms = (int(id_post) >> 22) + EPOCH_X_MS
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def leer_json(ruta: Path, por_defecto):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return por_defecto


def escribir_json(ruta: Path, datos) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(ruta)


def normalizar(texto: str) -> str:
    """minúsculas y sin acentos, para comparar 'Cupón' con 'cupon'."""
    t = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def contiene(texto_normalizado: str, palabra: str) -> bool:
    p = normalizar(palabra).strip()
    if not p:
        return False
    return re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", texto_normalizado) is not None


def recortar(texto: str, largo: int) -> str:
    texto = re.sub(r"\s+\n", "\n", texto).strip()
    return texto if len(texto) <= largo else texto[: largo - 1].rstrip() + "…"


def hace(dt: datetime) -> str:
    minutos = int((ahora() - dt).total_seconds() // 60)
    if minutos < 1:
        return "recién"
    if minutos < 60:
        return f"hace {minutos} min"
    return f"hace {minutos // 60} h {minutos % 60} min"


def aviso_actions(nivel: str, mensaje: str) -> None:
    """Muestra el mensaje en el resumen de GitHub Actions (y en consola)."""
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::{nivel}::{mensaje.replace(chr(10), ' ')}")
    else:
        print(f"[{nivel.upper()}] {mensaje}")


def url_pagina() -> str | None:
    if os.environ.get("PAGINA_URL"):
        return os.environ["PAGINA_URL"]
    repo = os.environ.get("GITHUB_REPOSITORY")  # "usuario/repo"
    if repo and "/" in repo:
        usuario, nombre = repo.split("/", 1)
        return f"https://{usuario.lower()}.github.io/{nombre}/"
    return None


# ------------------------------------------------------------ posibles códigos

_NO_CODIGO = re.compile(r"^(\d+(OFF|ARS|USD|K|MIL|HS|GB|TB|MP)|(OFF|ARS|USD|MP)\d+)$")
_CODIGO_MIXTO = re.compile(r"(?<![\w@#/.$])([A-Z0-9]{5,20})(?![\w/.])")
_CODIGO_TRAS_PALABRA = re.compile(
    r"(?:c[oó]digo|cup[oó]n|code|cod)\s*[:=\-–]\s*([A-Za-z0-9]{4,20})", re.IGNORECASE
)


def posibles_codigos(texto: str) -> list[str]:
    """Heurística: palabras tipo MELI5000 o lo que sigue a 'código:'."""
    candidatos = [m.group(1) for m in _CODIGO_TRAS_PALABRA.finditer(texto)]
    candidatos += [m.group(1) for m in _CODIGO_MIXTO.finditer(texto)]
    codigos: list[str] = []
    for c in candidatos:
        if not re.search(r"[A-Za-z]", c) or _NO_CODIGO.match(c.upper()):
            continue
        es_mixto = re.search(r"\d", c) and re.search(r"[A-Za-z]", c)
        if not es_mixto and not c.isupper():
            continue
        if c.upper() in {"CUPON", "CUPONES", "CODIGO", "LINK", "GRATIS", "ENVIO", "HTTPS"}:
            continue
        if c not in codigos:
            codigos.append(c)
    return codigos[:3]


# --------------------------------------------------------------- consultas


LARGO_MAX_CONSULTA = 450  # X limita el largo de la búsqueda; si hay muchos perfiles se parte


def limpiar_perfil(texto: str) -> str:
    """Acepta 'usuario', '@usuario' o 'https://x.com/usuario'. Devuelve '' si no es válido."""
    p = re.sub(r"^https?://(www\.|mobile\.)?(x|twitter)\.com/", "", str(texto).strip(), flags=re.I)
    p = p.lstrip("@").split("/")[0].split("?")[0]
    return p if re.fullmatch(r"\w{1,15}", p) else ""


def perfiles_de(busqueda: dict) -> list[str]:
    perfiles: list[str] = []
    for crudo in busqueda.get("perfiles") or []:
        p = limpiar_perfil(crudo)
        if not p:
            aviso_actions("warning", f"«{busqueda.get('nombre')}»: perfil inválido ignorado: {crudo!r}")
        elif p.lower() not in {x.lower() for x in perfiles}:
            perfiles.append(p)
    return perfiles


def armar_consultas(busqueda: dict, cfg: dict, fuente: str) -> list[str]:
    """Devuelve una o más consultas (más de una si la lista de perfiles es larga)."""
    base = (busqueda.get("consulta") or "").strip()
    extras = []
    if cfg["solo_espanol"]:
        extras.append("lang:es")
    if cfg["excluir_retweets"]:
        extras.append("-is:retweet" if fuente == "api" else "-filter:retweets")
    if cfg["excluir_respuestas"]:
        extras.append("-is:reply" if fuente == "api" else "-filter:replies")
    resto = " ".join([base, *[e for e in extras if e not in base]]).strip()

    if "perfiles" not in busqueda:
        return [resto]
    perfiles = perfiles_de(busqueda)
    if not perfiles:
        return []  # tiene la lista de perfiles pero vacía: no buscar en todo X por error

    def armar(grupo: list[str]) -> str:
        desde = " OR ".join(f"from:{p}" for p in grupo)
        return f"({desde}) {resto}".strip() if len(grupo) > 1 else f"{desde} {resto}".strip()

    consultas, grupo = [], []
    for p in perfiles:
        if grupo and len(armar(grupo + [p])) > LARGO_MAX_CONSULTA:
            consultas.append(armar(grupo))
            grupo = []
        grupo.append(p)
    consultas.append(armar(grupo))
    return consultas


async def buscar_con_cookies(busquedas: list[dict], cfg: dict) -> tuple[dict, list[str]]:
    """Fuente gratuita: usa la sesión de una cuenta de X (cookies auth_token y ct0)."""
    from twscrape import API, NoAccountError

    cookies = os.environ.get("X_COOKIES", "").strip()
    if not cookies:
        raise RuntimeError(
            "Falta el secreto X_COOKIES (formato: auth_token=...; ct0=...). Ver README."
        )
    usuario = os.environ.get("X_USUARIO", "").strip().lstrip("@") or "cuenta"

    for f in RAIZ.glob(".twscrape.db*"):
        f.unlink()
    api = API(str(ARCHIVO_DB_TWSCRAPE), raise_when_no_account=True)
    await api.pool.add_account_cookies(usuario, cookies)

    limite = max(1, int(cfg["max_resultados_por_busqueda"]))
    resultados: dict[str, list[Post]] = {}
    errores: list[str] = []

    async def juntar(consulta: str) -> list[Post]:
        posts = []
        async for tw in api.search(consulta, limit=limite):
            posts.append(
                Post(
                    id=tw.id_str,
                    texto=tw.rawContent or "",
                    fecha=tw.date if tw.date.tzinfo else tw.date.replace(tzinfo=timezone.utc),
                    url=tw.url,
                    usuario=tw.user.username if tw.user else None,
                    es_retweet=tw.retweetedTweet is not None,
                    es_respuesta=tw.inReplyToTweetId is not None,
                )
            )
        return posts

    for b in busquedas:
        nombre = b["nombre"]
        for consulta in armar_consultas(b, cfg, "cookies"):
            try:
                posts = await asyncio.wait_for(juntar(consulta), TIMEOUT_BUSQUEDA_SEG)
                resultados.setdefault(nombre, []).extend(posts)
                print(f"«{nombre}»: {len(posts)} posts traídos ({consulta})")
            except NoAccountError:
                errores.append(
                    "X no aceptó la sesión o no respondió (cookies vencidas, cuenta bloqueada, "
                    "límite de uso o corte de conexión). Si sigue pasando, volvé a copiar "
                    "auth_token y ct0 y actualizá el secreto X_COOKIES."
                )
                return resultados, errores
            except asyncio.TimeoutError:
                errores.append(f"«{nombre}»: X tardó demasiado en responder.")
            except Exception as e:  # noqa: BLE001
                errores.append(f"«{nombre}»: {type(e).__name__}: {e}")

    return resultados, errores


def buscar_con_api(busquedas: list[dict], cfg: dict, estado: dict) -> tuple[dict, list[str]]:
    """Fuente oficial (pago por uso): API v2 de X, búsqueda reciente."""
    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Falta el secreto X_BEARER_TOKEN. Ver README.")

    max_res = min(100, max(10, int(cfg["max_resultados_por_busqueda"])))
    resultados: dict[str, list[Post]] = {}
    errores: list[str] = []
    autores: dict[str, str] = estado.setdefault("autores", {})  # id de usuario -> @usuario
    autor_de_post: dict[str, str] = {}

    def pedir(ruta: str, params: dict) -> dict:
        url = f"https://api.x.com/2/{ruta}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}", "User-Agent": "cazacupones/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    for b in busquedas:
        nombre = b["nombre"]
        consultas = armar_consultas(b, cfg, "api")
        for i, consulta in enumerate(consultas):
            clave = nombre if len(consultas) == 1 else f"{nombre}#{i + 1}"
            params = {
                "query": consulta,
                "max_results": str(max_res),
                "tweet.fields": "created_at,author_id",
            }
            since = estado["since_id"].get(clave)
            # La búsqueda reciente solo cubre 7 días: si el since_id es más viejo, X da error.
            if since and fecha_de_id(since) > ahora() - timedelta(days=6, hours=20):
                params["since_id"] = since
            else:
                minutos = int(cfg["antiguedad_maxima_minutos"])
                params["start_time"] = a_iso(ahora() - timedelta(minutes=minutos))

            try:
                datos = pedir("tweets/search/recent", params)
            except urllib.error.HTTPError as e:
                cuerpo = e.read().decode("utf-8", "replace")[:300]
                pista = {
                    401: "token inválido",
                    402: "sin créditos en la cuenta de desarrollador",
                    403: "la app no tiene permiso para búsquedas",
                    429: "límite de uso alcanzado",
                }.get(e.code, "")
                errores.append(f"«{nombre}»: API de X respondió {e.code} {pista}. {cuerpo}")
                continue
            except Exception as e:  # noqa: BLE001
                errores.append(f"«{nombre}»: {type(e).__name__}: {e}")
                continue

            posts = []
            for d in datos.get("data", []):
                posts.append(
                    Post(
                        id=d["id"],
                        texto=d.get("text", ""),
                        fecha=desde_iso(d["created_at"]) if d.get("created_at") else fecha_de_id(d["id"]),
                        url=f"https://x.com/i/web/status/{d['id']}",
                    )
                )
                if d.get("author_id"):
                    autor_de_post[d["id"]] = d["author_id"]
            nuevo_since = datos.get("meta", {}).get("newest_id")
            if nuevo_since:
                estado["since_id"][clave] = nuevo_since
            resultados.setdefault(nombre, []).extend(posts)
            print(f"«{nombre}»: {len(posts)} posts traídos ({consulta})")

    # Nombres de usuario: se piden una sola vez por perfil y quedan guardados.
    faltan = sorted({a for a in autor_de_post.values() if a not in autores})[:100]
    if faltan:
        try:
            for u in pedir("users", {"ids": ",".join(faltan)}).get("data", []):
                autores[u["id"]] = u["username"]
        except Exception as e:  # noqa: BLE001
            aviso_actions("warning", f"No se pudieron obtener los nombres de usuario: {e}")
    for posts in resultados.values():
        for p in posts:
            usuario = autores.get(autor_de_post.get(p.id, ""))
            if usuario:
                p.usuario = usuario
                p.url = f"https://x.com/{usuario}/status/{p.id}"

    return resultados, errores


# ---------------------------------------------------------------- filtrado


def filtrar(posts: list[Post], busqueda: dict, cfg: dict, vistos: set[str]) -> list[Post]:
    limite = ahora() - timedelta(minutes=int(cfg["antiguedad_maxima_minutos"]))
    requeridas = busqueda.get("debe_contener_alguna") or []
    excluidas = busqueda.get("excluir") or []
    aceptados = []
    for p in posts:
        if p.id in vistos or p.fecha < limite:
            continue
        if cfg["excluir_retweets"] and (p.es_retweet or p.texto.startswith("RT @")):
            continue
        if cfg["excluir_respuestas"] and p.es_respuesta:
            continue
        tn = normalizar(p.texto)
        if requeridas and not any(contiene(tn, w) for w in requeridas):
            continue
        if any(contiene(tn, w) for w in excluidas):
            continue
        vistos.add(p.id)  # evita avisar dos veces si coincide con otra búsqueda
        aceptados.append(p)
    return sorted(aceptados, key=lambda p: p.fecha)


# ---------------------------------------------------------- notificaciones


def _post_json(url: str, datos: dict, headers: dict | None = None) -> None:
    req = urllib.request.Request(
        url,
        data=json.dumps(datos).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def canales_configurados() -> list[str]:
    canales = []
    if os.environ.get("NTFY_TOPIC", "").strip():
        canales.append("ntfy")
    if os.environ.get("TELEGRAM_TOKEN", "").strip() and os.environ.get("TELEGRAM_CHAT_ID", "").strip():
        canales.append("telegram")
    return canales


def notificar(titulo: str, mensaje: str, url: str | None, prioridad: int, etiquetas: list[str]) -> bool:
    """Manda a todos los canales configurados. Devuelve True si al menos uno funcionó."""
    algun_ok = False
    tema = os.environ.get("NTFY_TOPIC", "").strip()
    if tema:
        servidor = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").strip().rstrip("/")
        cuerpo = {
            "topic": tema,
            "title": titulo,
            "message": mensaje,
            "priority": prioridad,
            "tags": etiquetas,
        }
        acciones = []
        if url:
            cuerpo["click"] = url
            acciones.append({"action": "view", "label": "Abrir en X", "url": url})
        pagina = url_pagina()
        if pagina:
            acciones.append({"action": "view", "label": "Ver todos", "url": pagina})
        if acciones:
            cuerpo["actions"] = acciones
        headers = {}
        if os.environ.get("NTFY_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['NTFY_TOKEN']}"
        try:
            _post_json(servidor, cuerpo, headers)
            algun_ok = True
        except Exception as e:  # noqa: BLE001
            aviso_actions("warning", f"No se pudo enviar a ntfy: {e}")

    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if token and chat:
        texto = f"{titulo}\n\n{mensaje}" + (f"\n\n{url}" if url else "")
        try:
            _post_json(
                f"https://api.telegram.org/bot{token}/sendMessage",
                {"chat_id": chat, "text": texto[:4000], "disable_web_page_preview": False},
            )
            algun_ok = True
        except Exception as e:  # noqa: BLE001
            aviso_actions("warning", f"No se pudo enviar a Telegram: {e}")

    return algun_ok


def notificar_post(p: Post, busqueda: str, codigos: list[str], prioridad: int) -> bool:
    titulo = f"{busqueda} · @{p.usuario}" if p.usuario else f"{busqueda} · nuevo post"
    lineas = []
    if codigos:
        lineas.append("Posible código: " + ", ".join(codigos))
    lineas.append(recortar(p.texto, 350))
    lineas.append(hace(p.fecha))
    return notificar(titulo, "\n".join(lineas), p.url, prioridad, ["ticket"])


def avisar_error(errores: list[str], estado: dict) -> None:
    for e in errores:
        aviso_actions("error", e)
    # Un corte suelto no amerita aviso: avisamos recién a la segunda falla seguida.
    estado["fallas_seguidas"] = min(2, int(estado.get("fallas_seguidas") or 0) + 1)
    if estado["fallas_seguidas"] < 2:
        return
    ultimo = estado.get("ultimo_aviso_error")
    if ultimo and ahora() - desde_iso(ultimo) < timedelta(hours=HORAS_ENTRE_AVISOS_DE_ERROR):
        return  # ya avisamos hace poco, no molestar cada 10 minutos
    if notificar("Cazacupones: hay un problema", "\n".join(errores)[:1000], None, 3, ["warning"]):
        estado["ultimo_aviso_error"] = a_iso(ahora())


# -------------------------------------------------------------------- main


def main() -> int:
    cfg = {**CONFIG_POR_DEFECTO, **leer_json(ARCHIVO_CONFIG, {})}
    estado = {"since_id": {}, "vistos": [], "fallas_seguidas": 0, "ultimo_aviso_error": None}
    estado.update(leer_json(ARCHIVO_ESTADO, {}))
    resultados = {"actualizado": None, "items": []}
    resultados.update(leer_json(ARCHIVO_RESULTADOS, {}))

    if not canales_configurados():
        aviso_actions(
            "warning",
            "No hay canal de notificación: cargá NTFY_TOPIC (o TELEGRAM_TOKEN y TELEGRAM_CHAT_ID).",
        )

    if "--prueba" in sys.argv:
        ok = notificar(
            "Cazacupones funcionando",
            "Esta es una notificación de prueba. Si la ves, los avisos van a llegar bien.",
            url_pagina(),
            int(cfg["prioridad_notificacion"]),
            ["white_check_mark"],
        )
        print("Notificación de prueba enviada." if ok else "No se pudo enviar la notificación.")
        return 0 if ok else 1

    busquedas = []
    for b in cfg["busquedas"]:
        if not b.get("activa", True):
            continue
        if "perfiles" in b and not any(limpiar_perfil(p) for p in b.get("perfiles") or []):
            aviso_actions("warning", f"«{b.get('nombre')}»: la lista de perfiles está vacía, no se busca.")
            continue
        if b.get("consulta") or b.get("perfiles"):
            busquedas.append(b)
    if not busquedas:
        aviso_actions("warning", "No hay búsquedas activas en config.json.")
        return 0

    fuente = cfg["fuente"].strip().lower()
    try:
        if fuente == "api":
            crudos, errores = buscar_con_api(busquedas, cfg, estado)
        elif fuente == "cookies":
            crudos, errores = asyncio.run(buscar_con_cookies(busquedas, cfg))
        else:
            raise RuntimeError(f'"fuente" debe ser "cookies" o "api", no "{cfg["fuente"]}".')
    except Exception as e:  # noqa: BLE001
        crudos, errores = {}, [str(e)]

    vistos = set(estado["vistos"])
    nuevos: list[tuple[str, Post]] = []
    for b in busquedas:
        for p in filtrar(crudos.get(b["nombre"], []), b, cfg, vistos):
            nuevos.append((b["nombre"], p))
    nuevos.sort(key=lambda par: par[1].fecha)  # el más nuevo queda arriba en el celu
    print(f"Nuevos para avisar: {len(nuevos)}")

    tope = max(1, int(cfg["max_notificaciones_por_ejecucion"]))
    prioridad = int(cfg["prioridad_notificacion"])
    sobrantes = nuevos[:-tope] if len(nuevos) > tope else []
    for nombre, p in nuevos[-tope:]:
        notificar_post(p, nombre, posibles_codigos(p.texto), prioridad)
    if sobrantes:
        notificar(
            f"Cazacupones: {len(sobrantes)} posts más",
            "Hubo más resultados de los que conviene notificar de a uno. Miralos en la web.",
            None,
            3,
            ["ticket"],
        )

    if errores:
        avisar_error(errores, estado)
    else:
        # se resolvió; si vuelve a fallar, avisamos de nuevo
        estado["fallas_seguidas"] = 0
        estado["ultimo_aviso_error"] = None

    if nuevos:
        encontrado = a_iso(ahora())
        items_nuevos = [
            {
                "id": p.id,
                "url": p.url,
                "usuario": p.usuario,
                "texto": p.texto,
                "fecha": a_iso(p.fecha),
                "encontrado": encontrado,
                "busqueda": nombre,
                "codigos": posibles_codigos(p.texto),
            }
            for nombre, p in reversed(nuevos)
        ]
        resultados["items"] = (items_nuevos + resultados["items"])[:MAX_RESULTADOS]
        resultados["actualizado"] = encontrado
        escribir_json(ARCHIVO_RESULTADOS, resultados)

    # conservamos los IDs más recientes (los IDs de X crecen con el tiempo)
    estado["vistos"] = sorted(vistos, key=int)[-MAX_VISTOS:]
    escribir_json(ARCHIVO_ESTADO, estado)
    return 0


if __name__ == "__main__":
    sys.exit(main())
