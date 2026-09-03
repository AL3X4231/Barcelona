import asyncio
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from camoufox.async_api import AsyncCamoufox

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
PROXY_FILE = BASE_DIR / "proxy.txt"
RESULTS_DIR = BASE_DIR / "results"


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_proxies():
    if not PROXY_FILE.exists():
        raise FileNotFoundError(f"{PROXY_FILE} introuvable.")
    proxies = []
    with open(PROXY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                p = line.split(":")
                if len(p) == 4:
                    proxies.append({
                        "host": p[0],
                        "port": p[1],
                        "username": p[2],
                        "password": p[3]
                    })
    if not proxies:
        raise ValueError("Aucun proxy valide trouvé dans proxy.txt.")
    return proxies


import http.server
import re
import subprocess
import threading
import uuid

tunnel_public_url = None


class CartServerHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        latest_file = RESULTS_DIR / "latest_cart.json"
        if self.path.startswith("/cart") or self.path == "/":
            if latest_file.exists():
                with open(latest_file, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"error": "Aucun panier disponible"}')
        elif self.path == "/status":
            if latest_file.exists():
                with open(latest_file, "r", encoding="utf-8") as f:
                    d = json.load(f)
                br = d.get("book_result", {}).get("data", {})
                resp = {
                    "active": True,
                    "timestamp": d.get("timestamp"),
                    "cartId": br.get("id"),
                    "totalPrice": br.get("totalPrice"),
                    "expirateIn": br.get("expirateIn"),
                    "area": d.get("area", {}).get("name"),
                    "seats": f"Rang {d.get('pair', {}).get('row')} | Sièges {d.get('pair', {}).get('seat1', {}).get('seat')} & {d.get('pair', {}).get('seat2', {}).get('seat')}"
                }
            else:
                resp = {"active": False}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_cart_server(port=8765):
    try:
        server = http.server.ThreadingHTTPServer(("0.0.0.0", port), CartServerHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server
    except Exception as e:
        print(f"[SERVEUR] Erreur démarrage serveur local sur port {port} : {e}")
        return None


def start_cloudflare_tunnel(port=8765):
    global tunnel_public_url
    cloudflared_path = BASE_DIR / "cloudflared.exe"
    if not cloudflared_path.exists():
        return None

    try:
        proc = subprocess.Popen(
            [str(cloudflared_path), "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore"
        )

        t0 = time.time()
        while time.time() - t0 < 15:
            line = proc.stdout.readline()
            if not line:
                break
            match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
            if match:
                tunnel_public_url = match.group(0)
                # Mise à jour de config_client.json pour PC 2
                client_cfg = BASE_DIR / "Client_Paiement" / "config_client.json"
                if client_cfg.parent.exists():
                    with open(client_cfg, "w", encoding="utf-8") as f:
                        json.dump({"tunnel_url": tunnel_public_url}, f, indent=2)
                return tunnel_public_url
            time.sleep(0.1)
    except Exception as e:
        print(f"[TUNNEL] Impossible de démarrer cloudflared : {e}")
    return None


def _publish_relay_sync(topic, file_path):
    if not topic or not file_path or not Path(file_path).exists():
        return None
    try:
        with open(file_path, "rb") as f:
            data = f.read()
        url = f"https://ntfy.sh/{topic}"
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Filename": "latest_cart.json",
                "Title": "Sevilla FC - Panier Reserve !"
            },
            method="PUT"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                print(f"[RELAIS CLOUD] ✓ Panier synchronisé pour le PC 2 via {url} !")
                return True
    except Exception as e:
        print(f"[RELAIS CLOUD] ⚠ Erreur synchronisation : {e}")
    return False


def _post_discord_multipart_sync(webhook_url, payload_json, file_path=None):
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    body = bytearray()

    # Part 1: payload_json
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(b'Content-Disposition: form-data; name="payload_json"\r\n\r\n')
    body.extend(json.dumps(payload_json).encode("utf-8"))
    body.extend(b"\r\n")

    # Part 2: file attachment
    if file_path and Path(file_path).exists():
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="files[0]"; filename="{Path(file_path).name}"\r\n'.encode("utf-8"))
        body.extend(b"Content-Type: application/json\r\n\r\n")
        body.extend(file_bytes)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    req = urllib.request.Request(
        webhook_url,
        data=bytes(body),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "SevillaFC-Monitor/1.0"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status


async def notify_discord(webhook_url, proxy_str, area, pair, book_result, out_path):
    if not webhook_url:
        return

    try:
        cdata = book_result.get("data", {}) if book_result else {}
        cart_id = cdata.get("id", "Non réservé")
        total_price = cdata.get("totalPrice", "N/A")
        exp_sec = cdata.get("expirateIn", 540)
        minutes = exp_sec // 60
        seconds = exp_sec % 60

        area_name = area.get("name", "Zone inconnue")
        area_id = area.get("id", "")
        row = pair["row"]
        seat1 = pair["seat1"]["seat"]
        seat2 = pair["seat2"]["seat"]

        cart_command = f"python cart.py results/{out_path.name}"

        fields = [
            {
                "name": "🏟️ Zone / Tribune",
                "value": f"**{area_name}** (ID {area_id})",
                "inline": True
            },
            {
                "name": "💺 Places côte à côte",
                "value": f"Rang **{row}** | Sièges **{seat1}** & **{seat2}**",
                "inline": True
            },
            {
                "name": "💶 Montant Total",
                "value": f"**{total_price} €**",
                "inline": True
            },
            {
                "name": "🛒 ID Panier",
                "value": f"`{cart_id}`",
                "inline": True
            },
            {
                "name": "⏱️ Temps restant",
                "value": f"**{minutes}m {seconds:02d}s**",
                "inline": True
            },
            {
                "name": "🌐 Proxy",
                "value": f"`{proxy_str}`",
                "inline": True
            }
        ]

        if tunnel_public_url:
            fields.append({
                "name": "🔗 Accès 1-Clic pour PC 2 (Tunnel Web)",
                "value": f"[Cliquez ici pour récupérer le panier]({tunnel_public_url}/cart)",
                "inline": False
            })

        fields.append({
            "name": "⚡ Commande à exécuter pour payer (PC local)",
            "value": f"```bash\n{cart_command}\n```",
            "inline": False
        })

        embed = {
            "title": "⚽ SEVILLA FC - FC BARCELONA",
            "description": "🚨 **Une paire de billets côte à côte a été verrouillée dans votre panier !**\n📎 *Le fichier de session est également attaché à ce message pour le PC 2.*",
            "color": 3066993,
            "fields": fields,
            "footer": {
                "text": "Sevilla FC Bot • Ouvrez rapidement le panier pour finaliser l'achat !"
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        payload = {
            "content": f"@everyone 🎟️ **BILLETS VERROUILLÉS DANS LE PANIER ! ({seat1} & {seat2})**",
            "embeds": [embed]
        }

        status = await asyncio.to_thread(_post_discord_multipart_sync, webhook_url, payload, out_path)
        print(f"[DISCORD] ✓ Alerte & fichier de session envoyés avec succès (HTTP {status}) !")
    except Exception as e:
        print(f"[DISCORD] ⚠ Erreur envoi : {e}")


def find_pair(seats, gap=2):
    available = [s for s in seats if s.get("available") is True]
    if len(available) < 2:
        return None

    rows = {}
    for s in available:
        row = str(s.get("row", ""))
        try:
            num = int(str(s.get("seat")))
            rows.setdefault(row, []).append((num, s))
        except (ValueError, TypeError):
            continue

    for row, row_seats in rows.items():
        row_seats.sort(key=lambda x: x[0])
        lookup = {num: s for num, s in row_seats}

        for num, s1 in row_seats:
            candidate_gaps = [gap, 1] if gap != 1 else [1]
            for candidate_gap in candidate_gaps:
                s2 = lookup.get(num + candidate_gap)
                if s2:
                    pos1 = s1.get("posSeat")
                    pos2 = s2.get("posSeat")
                    if pos1 is not None and pos2 is not None:
                        if abs(pos1 - pos2) > 1:
                            continue

                    return {"row": row, "seat1": s1, "seat2": s2}

    return None


async def save_cart_result(proxy, live_token, area, pair, book_result, storage_state, local_storage, session_storage):
    RESULTS_DIR.mkdir(exist_ok=True)
    ts = int(time.time())

    data = {
        "timestamp": ts,
        "proxy": proxy,
        "token": live_token,
        "storage_state": storage_state,
        "local_storage": local_storage or {},
        "session_storage": session_storage or {},
        "area": {
            "id": area.get("id"),
            "name": area.get("name"),
            "available": area.get("available"),
            "prices": area.get("prices")
        },
        "pair": {
            "row": pair["row"],
            "seat1": pair["seat1"],
            "seat2": pair["seat2"]
        },
        "book_result": book_result
    }

    # Sauvegarde horodatée
    out_file = RESULTS_DIR / f"cart_{ts}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Sauvegarde du dernier panier (raccourci pour cart.py)
    latest_file = RESULTS_DIR / "latest_cart.json"
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"[FICHIER] Panier enregistré -> {out_file.name} (et latest_cart.json)")
    return out_file


async def monitor_worker():
    config = load_config()
    proxies = load_proxies()
    RESULTS_DIR.mkdir(exist_ok=True)

    event_id = config.get("event_id", 468)
    poll_interval = config.get("poll_interval", 1.5)
    pair_gap = config.get("pair_gap", 2)
    auto_book = config.get("auto_book", True)
    refresh_interval = config.get("session_refresh_interval", 600)  # 10 minutes par défaut
    discord_webhook = config.get("discord_webhook")

    proxy_idx = 0
    print("=" * 70)
    print(f"SURVEILLANCE SEVILLA FC DYNAMIQUE (Refresh toutes les {refresh_interval // 60} min)")
    print(f"Événement : {event_id} | Proxys chargés : {len(proxies)}")

    # Démarrage du serveur local et du tunnel Cloudflare pour le PC 2
    start_cart_server(8765)
    tunnel_url = start_cloudflare_tunnel(8765)
    if tunnel_url:
        print(f"[RELAIS PC 2] ✓ Tunnel Cloudflare actif : {tunnel_url}")
        print(f"             Le PC 2 peut récupérer les paniers à distance en 1-Clic.")
    else:
        print("[RELAIS PC 2] Serveur local actif sur port 8765 (mode local / fichier).")

    print("=" * 70)

    while True:
        proxy = proxies[proxy_idx]
        proxy_str = f"{proxy['host']}:{proxy['port']}"
        proxy_cfg = {
            "server": f"http://{proxy['host']}:{proxy['port']}",
            "username": proxy["username"],
            "password": proxy["password"]
        }

        print(f"\n[SESSION ACTIVE] Connexion via proxy {proxy_str}...")

        try:
            async with AsyncCamoufox(
                headless=config.get("headless", True),
                proxy=proxy_cfg,
                geoip=True
            ) as browser:
                context = await browser.new_context()
                page = await context.new_page()

                live_token = None

                def on_req(r):
                    nonlocal live_token
                    auth = r.headers.get("a360session")
                    if auth and "Bearer " in auth:
                        live_token = auth.replace("Bearer ", "").strip()

                page.on("request", on_req)

                # Connexion initiale
                print(f"[{proxy_str}] Chargement de la billetterie en direct...")
                await page.goto(
                    f"https://entradas.sevillafc.es/asientos?evento={event_id}",
                    wait_until="domcontentloaded",
                    timeout=45000
                )
                await page.wait_for_timeout(3000)

                # Récupération immédiate du token généré à la volée
                if not live_token:
                    live_token = await page.evaluate("() => sessionStorage.getItem('a360_se_cart_token')")

                print(f"[{proxy_str}] ✓ Session active & token frais obtenu : Bearer {live_token}")

                iteration = 0
                error_count = 0
                session_start_time = time.monotonic()

                while True:
                    iteration += 1
                    t0 = time.monotonic()

                    # VÉRIFICATION DE LA FRAÎCHEUR : Actualisation toutes les 10 minutes
                    if (time.monotonic() - session_start_time) > refresh_interval:
                        print(f"\n[{proxy_str}] 🔄 10 minutes écoulées : Actualisation de la session pour renouveler les cookies et tokens...")
                        try:
                            await page.reload(wait_until="domcontentloaded", timeout=30000)
                            await page.wait_for_timeout(2500)
                            new_tok = await page.evaluate("() => sessionStorage.getItem('a360_se_cart_token')")
                            if new_tok:
                                live_token = new_tok
                            session_start_time = time.monotonic()
                            print(f"[{proxy_str}] ✓ Session actualisée avec succès ! Token : Bearer {live_token}\n")
                        except Exception as ref_err:
                            print(f"[{proxy_str}] ⚠ Erreur lors du reload : {ref_err}. Rotation de proxy...")
                            break

                    try:
                        scan_data = await page.evaluate(
                            """
                            async ({eventId, token}) => {
                                const headers = {};
                                if (token) headers['a360session'] = 'Bearer ' + token;

                                const res = await fetch(`/api/events/${eventId}/areas`, { headers });
                                if (!res.ok) return { ok: false, status: res.status };
                                const areas = await res.json();
                                const activeAreas = areas.filter(a => (a.available || 0) >= 2);

                                const results = await Promise.all(activeAreas.map(async (area) => {
                                    try {
                                        const r = await fetch(`/api/events/${eventId}/area/${area.id}/seats`, { headers });
                                        const seats = await r.json();
                                        return { area, seats };
                                    } catch {
                                        return { area, seats: [] };
                                    }
                                }));

                                return { ok: true, results };
                            }
                            """,
                            {"eventId": event_id, "token": live_token}
                        )

                        if not scan_data.get("ok"):
                            status = scan_data.get("status")
                            print(f"[{proxy_str}] HTTP {status} sur areas")
                            error_count += 1
                            if error_count >= 4:
                                print(f"[{proxy_str}] 4 erreurs consécutives. Rotation de proxy.")
                                break
                            await asyncio.sleep(poll_interval)
                            continue

                        error_count = 0
                        results = scan_data.get("results", [])
                        duration_ms = int((time.monotonic() - t0) * 1000)

                        total_free = sum(len([s for s in item.get("seats", []) if s.get("available") is True]) for item in results)
                        print(f"[{proxy_str}] #{iteration:04d} | {len(results)} zones inspectées ({total_free} places libres au total, 0 côte à côte) | {duration_ms}ms")

                        if iteration % 10 == 0 and results:
                            breakdown = [f"{item['area']['name']}: {len([s for s in item.get('seats', []) if s.get('available') is True])} pl." for item in results[:3]]
                            print(f"    [Détail] {', '.join(breakdown)} (places isolées, en veille...)")

                        for item in results:
                            area = item["area"]
                            seats = item["seats"]
                            pair = find_pair(seats, gap=pair_gap)

                            if pair:
                                print("\n" + "!" * 70)
                                print(f"[{proxy_str}] ⚡ PAIRE CÔTE À CÔTE DÉTECTÉE !")
                                print(f"Zone : {area.get('name')} (ID {area.get('id')})")
                                print(f"Rang : {pair['row']}")
                                print(f"Siège 1 : {pair['seat1']['seat']} (pos {pair['seat1'].get('posSeat')})")
                                print(f"Siège 2 : {pair['seat2']['seat']} (pos {pair['seat2'].get('posSeat')})")
                                print("!" * 70 + "\n")

                                book_result = None
                                if auto_book and live_token:
                                    prices = area.get("prices", [])
                                    valid_prices = [p for p in prices if not p.get("junior")]
                                    price_id = valid_prices[0]["id"] if valid_prices else (prices[0]["id"] if prices else 1)
                                    unit_price = valid_prices[0].get("price", "?") if valid_prices else "?"

                                    print(f"[{proxy_str}] Verrouillage immédiat des 2 places via /api/tickets/book (tarif: {unit_price}€)...")
                                    book_result = await page.evaluate(
                                        """
                                        async ({eventId, areaId, priceId, token}) => {
                                            try {
                                                const res = await fetch('/api/tickets/book', {
                                                    method: 'POST',
                                                    headers: {
                                                        'content-type': 'application/json',
                                                        'a360session': 'Bearer ' + token
                                                    },
                                                    body: JSON.stringify({
                                                        idEvent: eventId,
                                                        seats: [{ numSeats: 2, idArea: areaId, idPrice: priceId }],
                                                        additionalInfo: { premium: false }
                                                    })
                                                });
                                                const rawText = await res.text();
                                                let parsed = null;
                                                try { parsed = JSON.parse(rawText); } catch(e) {}
                                                return { ok: res.ok, status: res.status, data: parsed, raw: rawText };
                                            } catch (err) {
                                                return { ok: false, error: err.message };
                                            }
                                        }
                                        """,
                                        {"eventId": event_id, "areaId": area["id"], "priceId": price_id, "token": live_token}
                                    )

                                    print(f"[{proxy_str}] RÉPONSE SERVEUR /api/tickets/book (HTTP {book_result.get('status')}) :")
                                    if book_result.get("ok"):
                                        cdata = book_result.get("data", {})
                                        exp_sec = cdata.get("expirateIn", 540)
                                        print(f"    ✓ Panier ID : {cdata.get('id')}")
                                        print(f"    ✓ Prix Total: {cdata.get('totalPrice')} €")
                                        print(f"    ✓ Timer     : {exp_sec // 60}m {exp_sec % 60:02d}s")
                                    else:
                                        print(f"    ✗ Erreur : {book_result.get('data') or book_result.get('raw')}")

                                # Capture des données de session fraîches
                                fresh_storage = await context.storage_state()
                                local_storage = await page.evaluate("() => Object.assign({}, localStorage)")
                                session_storage = await page.evaluate("() => Object.assign({}, sessionStorage)")

                                # Garantie des clés maîtresses
                                session_storage["a360_se_cart_token"] = live_token
                                session_storage["a360session"] = live_token

                                out_file = await save_cart_result(
                                    proxy=proxy,
                                    live_token=live_token,
                                    area=area,
                                    pair=pair,
                                    book_result=book_result,
                                    storage_state=fresh_storage,
                                    local_storage=local_storage,
                                    session_storage=session_storage
                                )

                                # Publication instantanée sur le relais Cloud pour le PC 2
                                relay_topic = config.get("relay_topic", "sevilla_barca_cart_al3x")
                                if relay_topic:
                                    await asyncio.to_thread(_publish_relay_sync, relay_topic, out_file)

                                if discord_webhook:
                                    await notify_discord(discord_webhook, proxy_str, area, pair, book_result, out_file)

                                print("\n" + "=" * 70)
                                print("🎉 PAIRE SÉCURISÉE DANS VOTRE PANIER !")
                                print(f"Zone   : {area.get('name')} (ID {area.get('id')})")
                                print(f"Places : Rang {pair['row']} | Sièges {pair['seat1']['seat']} & {pair['seat2']['seat']}")
                                print(f"\n👉 LANCEZ CETTE COMMANDE DANS UN NOUVEAU TERMINAL POUR PAYER :")
                                print(f"   python cart.py")
                                print(f"   (ou python cart.py results/{out_file.name})")
                                print("=" * 70 + "\n")

                                # Rotation vers le proxy suivant pour la suite de la surveillance
                                proxy_idx = (proxy_idx + 1) % len(proxies)
                                break  # Sort de cette session pour lancer la suivante

                        elapsed = time.monotonic() - t0
                        sleep_time = max(0.1, poll_interval - elapsed)
                        await asyncio.sleep(sleep_time)

                    except Exception as loop_err:
                        print(f"[{proxy_str}] Erreur boucle : {loop_err}")
                        error_count += 1
                        if error_count >= 4:
                            break
                        await asyncio.sleep(poll_interval)

        except Exception as conn_err:
            print(f"[{proxy_str}] Erreur de connexion : {conn_err}")

        # Rotation vers le proxy suivant
        proxy_idx = (proxy_idx + 1) % len(proxies)
        print(f"[ROTATION] Bascule sur le proxy suivant (#{proxy_idx + 1}/{len(proxies)})...")
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(monitor_worker())
