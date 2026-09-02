import asyncio
import json
from pathlib import Path
from camoufox.async_api import AsyncCamoufox

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
PROXY_FILE = BASE_DIR / "proxy.txt"
SESSION_DIR = BASE_DIR / "sessions"


def load_config():
    default_cfg = {
        "event_id": 468,
        "max_sessions_to_create": 5
    }
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            default_cfg.update(json.load(f))
    return default_cfg


def load_proxies():
    proxies = []
    if not PROXY_FILE.exists():
        print(f"[ERREUR] Fichier de proxys introuvable : {PROXY_FILE}")
        return proxies

    with open(PROXY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) == 4:
                proxies.append({
                    "host": parts[0],
                    "port": parts[1],
                    "username": parts[2],
                    "password": parts[3]
                })
            else:
                print(f"[WARN] Ligne proxy ignorée (format incorrect) : {line}")
    return proxies


def proxy_config(p):
    return {
        "server": f"http://{p['host']}:{p['port']}",
        "username": p["username"],
        "password": p["password"]
    }


async def create_session(index, proxy, event_id):
    session_name = f"session_{index:03d}"
    session_path = SESSION_DIR / f"{session_name}.json"
    url = f"https://entradas.sevillafc.es/asientos?evento={event_id}"

    print(f"\n[{session_name}] Création via proxy {proxy['host']}:{proxy['port']}...")

    captured_token = None

    async with AsyncCamoufox(
        headless=False,
        proxy=proxy_config(proxy),
        geoip=True
    ) as browser:
        context = await browser.new_context()
        page = await context.new_page()

        # Interception automatique du token d'authentification a360session
        def on_request(request):
            nonlocal captured_token
            auth = request.headers.get("a360session")
            if auth and "Bearer " in auth:
                captured_token = auth.replace("Bearer ", "").strip()

        page.on("request", on_request)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(5000)

            storage = await context.storage_state()

            data = {
                "name": session_name,
                "event_id": event_id,
                "proxy": proxy,
                "token": captured_token,
                "storage_state": storage
            }

            with open(session_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            status_str = f"Bearer {captured_token[:10]}..." if captured_token else "Non capturé (sera détecté au monitoring)"
            print(f"[{session_name}] Session enregistrée -> {session_path.name}")
            print(f"[{session_name}] Token a360session : {status_str}")

        except Exception as e:
            print(f"[{session_name}] Erreur lors de la création : {e}")

        finally:
            await context.close()


async def main():
    SESSION_DIR.mkdir(exist_ok=True)
    config = load_config()
    event_id = config.get("event_id", 468)
    max_sessions = config.get("max_sessions_to_create", 5)

    proxies = load_proxies()
    if not proxies:
        print("Aucun proxy disponible dans proxy.txt.")
        return

    to_process = proxies[:max_sessions]
    print(f"Création de {len(to_process)} session(s) sur {len(proxies)} proxys disponibles...")

    for idx, p in enumerate(to_process, start=1):
        await create_session(idx, p, event_id)

    print("\nToutes les sessions demandées ont été générées dans le dossier 'sessions/'.")


if __name__ == "__main__":
    asyncio.run(main())
