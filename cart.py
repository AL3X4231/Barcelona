import asyncio
import json
import sys
from pathlib import Path
from camoufox.async_api import AsyncCamoufox

BASE_DIR = Path(__file__).resolve().parent


async def main():
    if len(sys.argv) != 2:
        print("Usage :")
        print("  python cart.py results/session_001_1788391422.json")
        return

    target_path = Path(sys.argv[1])
    if not target_path.is_absolute():
        target_path = BASE_DIR / target_path

    if not target_path.exists():
        print(f"[ERREUR] Fichier introuvable : {target_path}")
        return

    with open(target_path, "r", encoding="utf-8") as f:
        file_data = json.load(f)

    # Récupération de la session
    if "session_file" in file_data and Path(file_data["session_file"]).exists():
        with open(file_data["session_file"], "r", encoding="utf-8") as sf:
            session = json.load(sf)
    elif "storage_state" in file_data:
        session = file_data
    else:
        print("[ERREUR] Format de session non reconnu.")
        return

    proxy = session["proxy"]
    event_id = session.get("event_id", 468)
    token = file_data.get("token") or session.get("token")

    proxy_cfg = {
        "server": f"http://{proxy['host']}:{proxy['port']}",
        "username": proxy["username"],
        "password": proxy["password"]
    }

    print("=" * 65)
    print(f"Restauration de la session : {session['name']}")
    print(f"Proxy associé : {proxy['host']}:{proxy['port']}")
    print(f"Token panier  : Bearer {token}")
    if "pair" in file_data:
        p = file_data["pair"]
        print(f"Places : Rang {p['row']} | Sièges {p['seat1']['seat']} & {p['seat2']['seat']}")
    print("=" * 65)

    # ATTENTION : Ne JAMAIS mettre extra_http_headers sur new_context() !
    # Cela envoie le header a360session aux CDNs tiers (Stripe, OneTrust, Google),
    # ce qui déclenche des erreurs CORS et bloque le chargement (spinner rouge infini).
    async with AsyncCamoufox(
        headless=False,
        proxy=proxy_cfg,
        geoip=True
    ) as browser:
        context = await browser.new_context(
            storage_state=session["storage_state"]
        )
        page = await context.new_page()

        # On attache a360session UNIQUEMENT sur les appels internes /api/** du club
        if token:
            async def on_api_route(route):
                h = dict(route.request.headers)
                h["a360session"] = f"Bearer {token}"
                await route.continue_(headers=h)

            await page.route("**/api/**", on_api_route)

        # Injection complète du sessionStorage et localStorage
        saved_session_storage = file_data.get("session_storage", {})
        saved_local_storage = file_data.get("local_storage", {})

        storage_js = f"""
            try {{
                const sStorage = {json.dumps(saved_session_storage)};
                for (const [k, v] of Object.entries(sStorage)) {{
                    sessionStorage.setItem(k, v);
                }}
                const lStorage = {json.dumps(saved_local_storage)};
                for (const [k, v] of Object.entries(lStorage)) {{
                    localStorage.setItem(k, v);
                }}
                if ("{token}") {{
                    sessionStorage.setItem("a360_se_cart_token", "{token}");
                    sessionStorage.setItem("a360session", "{token}");
                    localStorage.setItem("a360_se_cart_token", "{token}");
                    localStorage.setItem("a360session", "{token}");
                }}
                console.log("[INJECTION OK] Token panier restauré dans sessionStorage.");
            }} catch (e) {{
                console.error("Erreur injection storage:", e);
            }}
        """
        await page.add_init_script(storage_js)

        # On ouvre la page du match pour initialiser Angular et le store avec le panier
        print(f"\nChargement de la page de billetterie...")
        await page.goto(
            f"https://entradas.sevillafc.es/asientos?evento={event_id}",
            wait_until="domcontentloaded",
            timeout=45000
        )

        await page.wait_for_timeout(3000)

        # Vérification si le panier est chargé dans Angular
        cart_status = await page.evaluate(
            """
            async ({token}) => {
                try {
                    const res = await fetch('/api/tickets', {
                        headers: { 'a360session': 'Bearer ' + token }
                    });
                    if (!res.ok) return { ok: false, status: res.status };
                    const cart = await res.json();
                    return {
                        ok: true,
                        cartId: cart.id,
                        seats: cart.seats ? cart.seats.map(s => `R${s.row}-S${s.seat}`) : [],
                        expirateIn: cart.expirateIn,
                        totalPrice: cart.totalPrice
                    };
                } catch (err) {
                    return { ok: false, error: err.message };
                }
            }
            """,
            {"token": token}
        )

        print(f"\nÉtat du panier sur le serveur :", cart_status)

        print("\n[OK] Navigateur ouvert et connecté.")
        print("Si vous êtes sur la page des sièges, le bandeau de votre panier actif apparaît en haut.")
        print("Cliquez sur 'Continuar' ou sur l'icône Panier pour finaliser.")
        print("Appuyez sur CTRL+C dans ce terminal pour quitter.\n")

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\nFermeture de la session...")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
