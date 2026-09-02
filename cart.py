import asyncio
import json
import sys
from pathlib import Path
from camoufox.async_api import AsyncCamoufox

BASE_DIR = Path(__file__).resolve().parent


async def main():
    if len(sys.argv) != 2:
        print("Usage :")
        print("  python cart.py results/session_001_1788390217.json")
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

    # Injection du token dans TOUS les headers HTTP envoyés par le navigateur
    headers = {
        "a360session": f"Bearer {token}"
    } if token else {}

    async with AsyncCamoufox(
        headless=False,
        proxy=proxy_cfg,
        geoip=True
    ) as browser:
        context = await browser.new_context(
            storage_state=session["storage_state"],
            extra_http_headers=headers
        )
        page = await context.new_page()

        # INJECTION COMPLÈTE : sessionStorage + localStorage + headers
        # Le framework de Séville lit son token dans sessionStorage.getItem("a360_se_cart_token").
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
                console.log("[INJECTION OK] Storage et token panier restaurés.");
            }} catch (e) {{
                console.error("Erreur injection storage:", e);
            }}
        """
        await page.add_init_script(storage_js)

        print(f"\nNavigation directe vers la page de commande (checkout)...")
        try:
            await page.goto(
                "https://entradas.sevillafc.es/checkout",
                wait_until="domcontentloaded",
                timeout=45000
            )
        except Exception as e:
            print(f"Navigation vers /checkout a levé : {e}, repli sur /asientos...")
            await page.goto(
                f"https://entradas.sevillafc.es/asientos?evento={event_id}",
                wait_until="domcontentloaded",
                timeout=45000
            )

        print("\n[OK] Navigateur ouvert avec la session active et le panier injecté.")
        print("Vérifiez l'écran : vos 2 billets doivent apparaître dans le récapitulatif / panier.")
        print("Appuyez sur CTRL+C dans ce terminal quand vous aurez terminé votre achat.\n")

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\nFermeture de la session...")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
