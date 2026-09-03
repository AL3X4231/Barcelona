import asyncio
import json
import sys
from pathlib import Path
from camoufox.async_api import AsyncCamoufox

BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"


async def main():
    if len(sys.argv) == 2:
        target_path = Path(sys.argv[1])
        if not target_path.is_absolute():
            target_path = BASE_DIR / target_path
    else:
        target_path = RESULTS_DIR / "latest_cart.json"

    if not target_path.exists():
        print(f"[ERREUR] Fichier introuvable : {target_path}")
        print("Usage :")
        print("  python cart.py")
        print("  python cart.py results/cart_XXXXX.json")
        return

    with open(target_path, "r", encoding="utf-8") as f:
        file_data = json.load(f)

    # Récupération de la session / configuration
    if "session_file" in file_data and Path(file_data["session_file"]).exists():
        with open(file_data["session_file"], "r", encoding="utf-8") as sf:
            session = json.load(sf)
    else:
        session = file_data

    proxy = file_data.get("proxy") or session.get("proxy")
    event_id = file_data.get("event_id") or session.get("event_id", 468)
    token = file_data.get("token") or session.get("token")

    proxy_cfg = {
        "server": f"http://{proxy['host']}:{proxy['port']}",
        "username": proxy["username"],
        "password": proxy["password"]
    }

    print("=" * 65)
    print("OUVERTURE DU PANIER DANS LE NAVIGATEUR")
    print(f"Fichier   : {target_path.name}")
    print(f"Proxy     : {proxy['host']}:{proxy['port']}")
    print(f"Token     : Bearer {token}")
    if "pair" in file_data:
        p = file_data["pair"]
        print(f"Places    : Rang {p['row']} | Sièges {p['seat1']['seat']} & {p['seat2']['seat']}")
    print("=" * 65)

    async with AsyncCamoufox(
        headless=False,
        proxy=proxy_cfg,
        geoip=True
    ) as browser:
        context = await browser.new_context(
            storage_state=file_data.get("storage_state") or session.get("storage_state")
        )
        page = await context.new_page()

        # Routage API propre sans toucher aux scripts tiers
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
                console.log("[INJECTION OK] Token et stockage panier restaurés.");
            }} catch (e) {{
                console.error("Erreur injection storage:", e);
            }}
        """
        await page.add_init_script(storage_js)

        print(f"\nChargement de la page de billetterie Sevilla FC...")
        await page.goto(
            f"https://entradas.sevillafc.es/asientos?evento={event_id}",
            wait_until="domcontentloaded",
            timeout=45000
        )

        await page.wait_for_timeout(3500)

        # Inspection de l'état du panier sur le serveur (avec parsing robuste)
        cart_status = await page.evaluate(
            """
            async ({token}) => {
                try {
                    const res = await fetch('/api/tickets', {
                        headers: { 'a360session': 'Bearer ' + token }
                    });
                    const text = await res.text();
                    let parsed = null;
                    try { parsed = JSON.parse(text); } catch(e) {}

                    if (!res.ok) {
                        return { ok: false, status: res.status, raw: text };
                    }
                    if (!parsed) {
                        return { ok: false, status: res.status, raw: text, message: 'Réponse vide' };
                    }
                    return {
                        ok: true,
                        status: res.status,
                        cartId: parsed.id,
                        seats: parsed.seats ? parsed.seats.map(s => `R${s.row}-S${s.seat}`) : [],
                        expirateIn: parsed.expirateIn,
                        totalPrice: parsed.totalPrice,
                        raw: parsed
                    };
                } catch (err) {
                    return { ok: false, error: err.message };
                }
            }
            """,
            {"token": token}
        )

        print("\n" + "=" * 60)
        print("ÉTAT DU PANIER SUR LE SERVEUR :")
        if cart_status.get("ok"):
            exp = cart_status.get("expirateIn", 0)
            print(f"  ✓ Panier actif    : {cart_status.get('cartId')}")
            print(f"  ✓ Sièges réservés : {', '.join(cart_status.get('seats', []))}")
            print(f"  ✓ Montant total   : {cart_status.get('totalPrice')} €")
            print(f"  ⏱ Temps restant   : {exp // 60}m {exp % 60:02d}s")
        else:
            print(f"  ⚠ Statut serveur  : HTTP {cart_status.get('status')}")
            print(f"  ⚠ Détail          : {cart_status.get('raw') or cart_status.get('error')}")
        print("=" * 60)

        print("\n[OK] Navigateur ouvert et connecté.")
        print("-> Vos places sont visibles dans le bandeau de panier en haut.")
        print("-> Cliquez sur 'Finalizar compra' ou 'Continuar' pour payer.")
        print("-> Appuyez sur CTRL+C dans ce terminal lorsque vous aurez terminé.\n")

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\nFermeture de la session...")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
