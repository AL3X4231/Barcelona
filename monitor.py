import asyncio
import json
import time
from pathlib import Path
from camoufox.async_api import AsyncCamoufox

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
SESSION_DIR = BASE_DIR / "sessions"
RESULTS_DIR = BASE_DIR / "results"


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_sessions():
    return sorted(SESSION_DIR.glob("session_*.json"))


def find_pair(seats, gap=2):
    """
    Cherche deux sièges disponibles sur le même rang avec l'écart spécifié.
    Valide également posSeat pour s'assurer qu'aucun couloir/escalier ne les sépare.
    """
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
            # Vérifie les deux types de tribunes : écart pair/impair (gap=2) ou consécutif (gap=1)
            candidate_gaps = [gap, 1] if gap != 1 else [1]
            for candidate_gap in candidate_gaps:
                s2 = lookup.get(num + candidate_gap)
                if s2:
                    # Validation physique anti-allée/escalier si posSeat est présent
                    pos1 = s1.get("posSeat")
                    pos2 = s2.get("posSeat")
                    if pos1 is not None and pos2 is not None:
                        # Dans le stade, deux sièges contigus ont un posSeat consécutif (différence = 1)
                        if abs(pos1 - pos2) > 1:
                            continue  # Sièges séparés par un couloir

                    return {"row": row, "seat1": s1, "seat2": s2}

    return None


async def scan_session(session_file, config):
    event_id = config["event_id"]
    poll_interval = config.get("poll_interval", 1.5)
    pair_gap = config.get("pair_gap", 2)
    auto_book = config.get("auto_book", False)

    with open(session_file, "r", encoding="utf-8") as f:
        session = json.load(f)

    session_name = session["name"]
    proxy = session["proxy"]
    token = session.get("token")

    print("\n" + "=" * 65)
    print(f"[{session_name}] Surveillance active via proxy {proxy['host']}:{proxy['port']}")
    print("=" * 65)

    proxy_cfg = {
        "server": f"http://{proxy['host']}:{proxy['port']}",
        "username": proxy["username"],
        "password": proxy["password"]
    }

    async with AsyncCamoufox(
        headless=config.get("headless", True),
        proxy=proxy_cfg,
        geoip=True
    ) as browser:
        context = await browser.new_context(storage_state=session["storage_state"])
        page = await context.new_page()

        # Capture dynamique du token a360session si manquant
        def on_req(r):
            nonlocal token
            auth = r.headers.get("a360session")
            if auth and "Bearer " in auth:
                token = auth.replace("Bearer ", "").strip()

        page.on("request", on_req)

        await page.goto(
            f"https://entradas.sevillafc.es/asientos?evento={event_id}",
            wait_until="domcontentloaded",
            timeout=45000
        )
        await page.wait_for_timeout(3000)

        iteration = 0
        error_count = 0

        while True:
            iteration += 1
            t0 = time.monotonic()

            try:
                # Requête parallèle de toutes les zones en un seul appel navigateur
                scan_data = await page.evaluate(
                    """
                    async ({eventId, token}) => {
                        const headers = { "accept": "application/json, text/plain, */*" };
                        if (token) headers["a360session"] = "Bearer " + token;

                        // 1. Récupération des zones
                        const resAreas = await fetch(`/api/events/${eventId}/areas`, { headers });
                        if (!resAreas.ok) return { ok: false, status: resAreas.status };
                        const areas = await resAreas.json();

                        const availableAreas = areas.filter(a => (a.available || 0) >= 2);
                        if (availableAreas.length === 0) return { ok: true, results: [] };

                        // 2. Récupération simultanée des sièges de toutes les zones ouvertes
                        const results = await Promise.all(availableAreas.map(async (area) => {
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
                    {"eventId": event_id, "token": token}
                )

                if not scan_data.get("ok"):
                    status = scan_data.get("status")
                    print(f"[{session_name}] HTTP {status} sur areas")
                    error_count += 1
                    if error_count >= 5:
                        print(f"[{session_name}] Trop d'erreurs consécutives ({error_count}). Rotation de session.")
                        return False
                    await asyncio.sleep(poll_interval)
                    continue

                error_count = 0
                results = scan_data.get("results", [])
                duration_ms = int((time.monotonic() - t0) * 1000)

                total_free = sum(len([s for s in item.get("seats", []) if s.get("available") is True]) for item in results)
                print(f"[{session_name}] #{iteration:04d} | {len(results)} zones inspectées ({total_free} places libres au total, 0 côte à côte) | {duration_ms}ms")

                if iteration % 10 == 0 and results:
                    breakdown = [f"{item['area']['name']}: {len([s for s in item.get('seats', []) if s.get('available') is True])} pl." for item in results[:3]]
                    print(f"    [Détail en direct] Exemples de zones : {', '.join(breakdown)} (places isolées, en veille...)")

                for item in results:
                    area = item["area"]
                    seats = item["seats"]
                    pair = find_pair(seats, gap=pair_gap)

                    if pair:
                        print("\n" + "!" * 70)
                        print(f"[{session_name}] PAIRE CÔTE À CÔTE DÉTECTÉE !")
                        print(f"Zone : {area.get('name')} (ID {area.get('id')})")
                        print(f"Rang : {pair['row']}")
                        print(f"Siège 1 : {pair['seat1']['seat']} (pos {pair['seat1'].get('posSeat')})")
                        print(f"Siège 2 : {pair['seat2']['seat']} (pos {pair['seat2'].get('posSeat')})")
                        print("!" * 70 + "\n")

                        # Réservation automatique dans le panier si configuré
                        book_result = None
                        if auto_book and token:
                            prices = area.get("prices", [])
                            valid_prices = [p for p in prices if not p.get("junior")]
                            price_id = valid_prices[0]["id"] if valid_prices else (prices[0]["id"] if prices else 1)
                            unit_price = valid_prices[0].get("price", "?") if valid_prices else "?"

                            print(f"[{session_name}] Verrouillage immédiat des 2 places via /api/tickets/book (tarif: {unit_price}€)...")
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
                                        return { ok: res.ok, status: res.status, data: await res.json() };
                                    } catch (err) {
                                        return { ok: false, error: err.message };
                                    }
                                }
                                """,
                                {"eventId": event_id, "areaId": area["id"], "priceId": price_id, "token": token}
                            )

                            if book_result.get("ok"):
                                cdata = book_result.get("data", {})
                                exp_sec = cdata.get("expirateIn", 540)
                                print(f"[{session_name}] ✓ RÉSERVATION CONFIRMÉE PAR LE SERVEUR (HTTP 201) !")
                                print(f"    ID Panier  : {cdata.get('id')}")
                                print(f"    Prix Total : {cdata.get('totalPrice')} €")
                                print(f"    Timer      : {exp_sec // 60}m {exp_sec % 60:02d}s")
                            else:
                                print(f"[{session_name}] ✗ Échec réservation :", book_result)

                        # Capture des cookies frais et du sessionStorage / localStorage après le panier
                        try:
                            fresh_storage = await context.storage_state()
                            local_storage = await page.evaluate("() => Object.assign({}, localStorage)")
                            session_storage = await page.evaluate("() => Object.assign({}, sessionStorage)")
                        except Exception as err:
                            print(f"[{session_name}] Warning capture storage : {err}")
                            fresh_storage = session.get("storage_state")
                            local_storage = {}
                            session_storage = {}

                        if token:
                            session_storage["a360_se_cart_token"] = token
                            session_storage["a360session"] = token

                        out_path = await save_result(session, session_file, area, pair, book_result, fresh_storage, local_storage, session_storage)

                        print("\n" + "=" * 70)
                        print("🎉 PAIRE SÉCURISÉE DANS LE PANIER !")
                        print(f"Zone   : {area.get('name')} (ID {area.get('id')})")
                        print(f"Places : Rang {pair['row']} | Sièges {pair['seat1']['seat']} & {pair['seat2']['seat']}")
                        print(f"\n👉 LANCEZ CETTE COMMANDE DANS UN NOUVEAU TERMINAL POUR PAYER :")
                        print(f"   python cart.py results/{out_path.name}")
                        print("=" * 70 + "\n")
                        return True

                elapsed = time.monotonic() - t0
                sleep_time = max(0.1, poll_interval - elapsed)
                await asyncio.sleep(sleep_time)

            except Exception as e:
                print(f"[{session_name}] Exception pendant le scan : {e}")
                await asyncio.sleep(poll_interval)


async def save_result(session, session_file, area, pair, book_result=None, fresh_storage=None, local_storage=None, session_storage=None):
    RESULTS_DIR.mkdir(exist_ok=True)
    ts = int(time.time())
    data = {
        "timestamp": ts,
        "session": session["name"],
        "session_file": str(session_file),
        "proxy": session["proxy"],
        "token": session.get("token"),
        "storage_state": fresh_storage or session.get("storage_state"),
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
    out_file = RESULTS_DIR / f"{session['name']}_{ts}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Résultat sauvegardé -> {out_file.name}")
    return out_file


async def main():
    config = load_config()
    RESULTS_DIR.mkdir(exist_ok=True)
    sessions = load_sessions()

    if not sessions:
        print("Aucune session trouvée dans 'sessions/'.")
        print("Veuillez d'abord exécuter : python create_sessions.py")
        return

    print(f"{len(sessions)} session(s) chargée(s). Démarrage de la surveillance...")

    # Rotation sur les sessions
    while True:
        for s_file in sessions:
            try:
                found = await scan_session(s_file, config)
                if found:
                    print(f"\n[INFO] Paire sécurisée avec {s_file.name}. Rotation vers la session suivante...")
            except KeyboardInterrupt:
                print("\nArrêt demandé par l'utilisateur.")
                return
            except Exception as e:
                print(f"[ERREUR] Session {s_file.name} : {e}")


if __name__ == "__main__":
    asyncio.run(main())
