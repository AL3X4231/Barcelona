import asyncio
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config_client.json"
LOCAL_CART_FILE = BASE_DIR / "latest_cart.json"


def load_client_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"tunnel_url": ""}


async def run_camoufox_cart(cart_data):
    from camoufox.async_api import AsyncCamoufox

    proxy = cart_data.get("proxy")
    token = cart_data.get("token")
    event_id = cart_data.get("event_id", 468)

    proxy_cfg = {
        "server": f"http://{proxy['host']}:{proxy['port']}",
        "username": proxy["username"],
        "password": proxy["password"]
    }

    async with AsyncCamoufox(headless=False, proxy=proxy_cfg, geoip=True) as browser:
        context = await browser.new_context(
            storage_state=cart_data.get("storage_state")
        )
        page = await context.new_page()

        if token:
            async def on_api(route):
                h = dict(route.request.headers)
                h["a360session"] = f"Bearer {token}"
                await route.continue_(headers=h)

            await page.route("**/api/**", on_api)

        s_storage = cart_data.get("session_storage", {})
        l_storage = cart_data.get("local_storage", {})

        storage_js = f"""
            try {{
                const ss = {json.dumps(s_storage)};
                for (const [k, v] of Object.entries(ss)) sessionStorage.setItem(k, v);
                const ls = {json.dumps(l_storage)};
                for (const [k, v] of Object.entries(ls)) localStorage.setItem(k, v);
                if ("{token}") {{
                    sessionStorage.setItem("a360_se_cart_token", "{token}");
                    sessionStorage.setItem("a360session", "{token}");
                    localStorage.setItem("a360_se_cart_token", "{token}");
                    localStorage.setItem("a360session", "{token}");
                }}
            }} catch(e) {{
                console.error("Storage injection error:", e);
            }}
        """
        await page.add_init_script(storage_js)

        await page.goto(
            f"https://entradas.sevillafc.es/asientos?evento={event_id}",
            wait_until="domcontentloaded",
            timeout=45000
        )

        # Maintient le navigateur ouvert tant que l'utilisateur l'utilise
        while True:
            await asyncio.sleep(1)


class SevillaCartApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Sevilla FC - Assistant Panier 1-Clic")
        self.geometry("560x520")
        self.resizable(False, False)
        self.configure(bg="#1a1a1a")

        self.config_data = load_client_config()
        self.current_cart = None
        self.browser_running = False

        self._setup_ui()
        self._start_polling()

    def _setup_ui(self):
        # Header
        header_frame = tk.Frame(self, bg="#c8102e", height=70)
        header_frame.pack(fill="x")

        tk.Label(
            header_frame,
            text="⚽ SEVILLA FC - FC BARCELONA",
            font=("Helvetica", 15, "bold"),
            fg="white",
            bg="#c8102e"
        ).pack(pady=(12, 2))

        tk.Label(
            header_frame,
            text="Assistant d'ouverture et de paiement 1-Clic",
            font=("Helvetica", 10),
            fg="#f8d7da",
            bg="#c8102e"
        ).pack()

        # Status badge container
        content_frame = tk.Frame(self, bg="#1a1a1a", padx=25, pady=20)
        content_frame.pack(fill="both", expand=True)

        self.status_badge = tk.Label(
            content_frame,
            text="⚪ En veille : En attente d'un panier réservé...",
            font=("Helvetica", 11, "bold"),
            fg="#cccccc",
            bg="#2d2d2d",
            padx=15,
            pady=10,
            relief="groove"
        )
        self.status_badge.pack(fill="x", pady=(0, 15))

        # Info Box
        info_frame = tk.LabelFrame(
            content_frame,
            text=" Détails de la réservation ",
            font=("Helvetica", 10, "bold"),
            fg="#e0e0e0",
            bg="#252525",
            padx=15,
            pady=12
        )
        info_frame.pack(fill="x", pady=5)

        self.lbl_zone = tk.Label(info_frame, text="Tribune   : --", font=("Helvetica", 10), fg="#ffffff", bg="#252525", anchor="w")
        self.lbl_zone.pack(fill="x", pady=2)

        self.lbl_places = tk.Label(info_frame, text="Places    : --", font=("Helvetica", 10, "bold"), fg="#ffffff", bg="#252525", anchor="w")
        self.lbl_places.pack(fill="x", pady=2)

        self.lbl_prix = tk.Label(info_frame, text="Montant   : --", font=("Helvetica", 10), fg="#4cd137", bg="#252525", anchor="w")
        self.lbl_prix.pack(fill="x", pady=2)

        self.lbl_timer = tk.Label(info_frame, text="Temps rest: --", font=("Helvetica", 11, "bold"), fg="#e84118", bg="#252525", anchor="w")
        self.lbl_timer.pack(fill="x", pady=2)

        # Action Button (1-Click)
        self.btn_open = tk.Button(
            content_frame,
            text="🎟️ OUVRIR LE PANIER ET PAYER",
            font=("Helvetica", 13, "bold"),
            bg="#444444",
            fg="#888888",
            activebackground="#4cd137",
            activeforeground="white",
            height=2,
            cursor="hand2",
            state="disabled",
            command=self._on_click_open
        )
        self.btn_open.pack(fill="x", pady=(20, 10))

        self.lbl_hint = tk.Label(
            content_frame,
            text="Le bouton deviendra vert dès qu'une paire sera réservée par le bot.",
            font=("Helvetica", 9, "italic"),
            fg="#888888",
            bg="#1a1a1a"
        )
        self.lbl_hint.pack()

        # Footer config
        tunnel_url = self.config_data.get("tunnel_url", "")
        short_tunnel = tunnel_url[:35] + "..." if len(tunnel_url) > 35 else (tunnel_url or "Mode local / fichier")
        footer_lbl = tk.Label(
            self,
            text=f"Connexion : {short_tunnel}",
            font=("Helvetica", 8),
            fg="#555555",
            bg="#1a1a1a"
        )
        footer_lbl.pack(side="bottom", pady=8)

    def _start_polling(self):
        thread = threading.Thread(target=self._poll_loop, daemon=True)
        thread.start()

    def _poll_loop(self):
        while True:
            cart = self._fetch_latest_cart()
            self.after(0, self._update_state, cart)
            time.sleep(2)

    def _fetch_latest_cart(self):
        # 1. Vérification par Relais Cloud direct ntfy.sh (Zéro configuration, 100% automatique mondial)
        relay_topic = self.config_data.get("relay_topic", "sevilla_barca_cart_al3x").strip()
        if relay_topic:
            try:
                poll_url = f"https://ntfy.sh/{relay_topic}/json?poll=1"
                req = urllib.request.Request(poll_url, headers={"User-Agent": "SevillaAssistant/1.0"})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    lines = resp.read().decode("utf-8").strip().split("\n")
                    for l in reversed(lines):
                        if l:
                            ev = json.loads(l)
                            if "attachment" in ev and ev["attachment"].get("url"):
                                att_url = ev["attachment"]["url"]
                                with urllib.request.urlopen(att_url, timeout=4) as f_resp:
                                    data = json.loads(f_resp.read().decode("utf-8"))
                                    if data and "token" in data:
                                        return data
            except Exception:
                pass

        # 2. Vérification par URL de tunnel Cloudflare si configurée
        tunnel_url = self.config_data.get("tunnel_url", "").strip()
        if tunnel_url:
            if not tunnel_url.startswith("http"):
                tunnel_url = "https://" + tunnel_url
            try:
                url = f"{tunnel_url.rstrip('/')}/cart"
                req = urllib.request.Request(url, headers={"User-Agent": "SevillaAssistant/1.0"})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if data and "token" in data:
                            return data
            except Exception:
                pass

        # 3. Vérification par fichier local (si glissé dans le dossier)
        if LOCAL_CART_FILE.exists():
            try:
                with open(LOCAL_CART_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

        return None

    def _update_state(self, cart):
        if not cart:
            self.status_badge.config(
                text="⚪ En veille : En attente d'un panier réservé...",
                bg="#2d2d2d",
                fg="#cccccc"
            )
            self.lbl_zone.config(text="Tribune   : --")
            self.lbl_places.config(text="Places    : --")
            self.lbl_prix.config(text="Montant   : --")
            self.lbl_timer.config(text="Temps rest: --")
            self.btn_open.config(state="disabled", bg="#444444", fg="#888888")
            self.lbl_hint.config(text="Le bouton s'activera automatiquement dès qu'un panier sera sécurisé.")
            self.current_cart = None
            return

        self.current_cart = cart

        area = cart.get("area", {}).get("name", "Tribune Inconnue")
        pair = cart.get("pair", {})
        row = pair.get("row", "?")
        seat1 = pair.get("seat1", {}).get("seat", "?")
        seat2 = pair.get("seat2", {}).get("seat", "?")

        b_data = cart.get("book_result", {}).get("data", {})
        price = b_data.get("totalPrice", 300)

        # Calcul du timer restant
        created_ts = cart.get("timestamp", int(time.time()))
        exp_in = b_data.get("expirateIn", 540)
        remaining = max(0, exp_in - (int(time.time()) - created_ts))

        min_rem = remaining // 60
        sec_rem = remaining % 60

        if remaining > 0:
            self.status_badge.config(
                text="🟢 PANIER ACTIF : 2 BILLETS CÔTE À CÔTE VERROUILLÉS !",
                bg="#1b4d3e",
                fg="#2ed573"
            )
            self.lbl_zone.config(text=f"Tribune   : {area}")
            self.lbl_places.config(text=f"Places    : Rang {row} | Sièges {seat1} & {seat2} (Côte à côte)")
            self.lbl_prix.config(text=f"Montant   : {price} € (Total pour 2 places)")
            self.lbl_timer.config(text=f"Temps rest: {min_rem:02d} min {sec_rem:02d} sec (Dépêchez-vous !)")

            if not self.browser_running:
                self.btn_open.config(state="normal", bg="#2ed573", fg="#ffffff")
                self.lbl_hint.config(text="👉 Cliquez sur le bouton ci-dessus pour ouvrir et finaliser l'achat !")
        else:
            self.status_badge.config(
                text="🔴 PANIER EXPIRÉ (Temps écoulé)",
                bg="#4d1b1b",
                fg="#ff4757"
            )
            self.lbl_timer.config(text="Temps rest: EXPIRÉ")
            self.btn_open.config(state="disabled", bg="#444444", fg="#888888")

    def _on_click_open(self):
        if not self.current_cart or self.browser_running:
            return

        self.browser_running = True
        self.btn_open.config(text="⏳ Lancement du navigateur...", state="disabled", bg="#e67e22")

        def run():
            try:
                import os
                import subprocess
                from camoufox.pkgman import launch_path

                exe = launch_path()
                if not exe or not os.path.exists(exe):
                    self.after(0, lambda: self.btn_open.config(text="⏳ Telechargement navigateur (1ere fois)...", bg="#e67e22"))
                    subprocess.run([sys.executable, "-m", "camoufox", "fetch"], check=True)

                self.after(0, lambda: self.btn_open.config(text="⏳ Lancement du navigateur...", bg="#2980b9"))
                asyncio.run(run_camoufox_cart(self.current_cart))
            except Exception as e:
                messagebox.showerror("Erreur", f"Erreur lors de l'ouverture : {e}")
            finally:
                self.browser_running = False
                self.after(0, lambda: self.btn_open.config(text="🎟️ OUVRIR LE PANIER ET PAYER", state="normal", bg="#2ed573"))

        t = threading.Thread(target=run, daemon=True)
        t.start()


if __name__ == "__main__":
    app = SevillaCartApp()
    app.mainloop()
