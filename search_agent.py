"""
Agent de veille d'annonces (version sans IA / sans coût API)
==============================================================

Cherche des annonces correspondant à une recherche définie dans config.json
sur plusieurs sites français, filtre par règles simples (prix, mots-clés,
état), puis génère une page HTML statique (docs/index.html) consultable
depuis n'importe quel appareil une fois publiée sur GitHub Pages.

À LIRE AVANT DE MODIFIER
-------------------------
- Leboncoin et Vinted utilisent des protections anti-robot (Datadome).
  Il est normal que ces scrapers échouent de temps en temps (erreur 403).
  Regarde la section "État des sites" en bas de la page HTML générée :
  elle indique clairement quel site a répondu et lequel a été bloqué.
- Les autres sites (eBay, Rakuten, Cdiscount) sont scrapés via un
  navigateur headless (Playwright) pour gérer le JavaScript et réduire
  les blocages anti-robot.
- Aucune clé API, aucun compte payant n'est nécessaire pour cette version.
"""

from __future__ import annotations

import json
import re
import time
import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup
from jinja2 import Template
from playwright.sync_api import sync_playwright

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}
TIMEOUT = 15


def fetch_rendered_html(url: str, wait_selector: str | None = None, timeout_ms: int = 20000) -> str:
    """
    Charge une page avec un vrai navigateur headless (Chromium) et attend
    l'exécution du JavaScript, contrairement à requests.get() qui ne voit
    que le HTML brut envoyé avant que la page ne se construise.
    Nécessaire pour les sites modernes (Cdiscount, Fnac...) qui affichent
    leurs résultats de recherche via JavaScript.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="fr-FR",
        )
        page = context.new_page()
        try:
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=8000)
                except Exception:
                    pass
            else:
                page.wait_for_timeout(2500)
            html = page.content()
        finally:
            browser.close()
    return html


PRICE_RE = re.compile(r"(\d{1,4}(?:[.,]\d{2})?)\s?€")


def extract_generic_listings(html: str, base_url: str, site: str, limit: int = 30) -> list["Listing"]:
    """
    Extraction 'de secours' quand on ne connaît pas les sélecteurs CSS exacts
    d'un site : cherche tout lien qui contient à la fois une image et un prix
    à proximité. Moins précis qu'un sélecteur dédié, mais résiste beaucoup
    mieux aux changements de mise en page que des classes CSS figées.
    """
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen_urls = set()
    for a in soup.find_all("a", href=True):
        if len(results) >= limit:
            break
        href = a.get("href", "")
        if not href or href.startswith("#") or href.startswith("javascript"):
            continue
        block_text = a.get_text(" ", strip=True)
        price_match = PRICE_RE.search(block_text)
        if not price_match:
            parent = a.find_parent()
            if parent:
                price_match = PRICE_RE.search(parent.get_text(" ", strip=True))
        if not price_match:
            continue
        img = a.find("img")
        title = (img.get("alt") if img and img.get("alt") else "").strip() or block_text[:120]
        if not title:
            continue
        url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        price = None
        try:
            price = float(price_match.group(1).replace(",", "."))
        except ValueError:
            pass
        thumb = ""
        if img:
            thumb = img.get("src") or img.get("data-src") or ""
        results.append(Listing(site=site, title=title, price=price, location="", url=url, thumbnail=thumb))
    return results


@dataclass
class Listing:
    site: str
    title: str
    price: Optional[float]
    location: str
    url: str
    thumbnail: str
    description: str = ""
    listing_id: str = ""

    def __post_init__(self):
        if not self.listing_id:
            self.listing_id = hashlib.md5(self.url.encode("utf-8")).hexdigest()[:12]


def normalize(text: str) -> str:
    text = text or ""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return text.lower()


def matches_criteria(listing: Listing, config: dict) -> bool:
    text = normalize(f"{listing.title} {listing.description}")

    include = [normalize(k) for k in config.get("include_keywords", [])]
    if include and not any(k in text for k in include):
        return False

    exclude = [normalize(k) for k in config.get("exclude_keywords", [])]
    if any(k in text for k in exclude):
        return False

    if listing.price is not None:
        pmin = config.get("price_min")
        pmax = config.get("price_max")
        if pmin is not None and listing.price < pmin:
            return False
        if pmax is not None and listing.price > pmax:
            return False

    return True


# ---------------------------------------------------------------------------
# SCRAPERS PAR SITE
# Chaque fonction reçoit la requête de recherche et un dict "status" à
# remplir (utilisé pour afficher l'état de chaque site dans la page HTML).
# Elle doit toujours retourner une liste (vide en cas d'échec) et ne jamais
# lever d'exception non gérée, pour ne pas bloquer les autres sites.
# ---------------------------------------------------------------------------

def scrape_vinted(query: str, status: dict) -> list[Listing]:
    """Vinted expose une API JSON publique utilisée par leur propre site web."""
    listings: list[Listing] = []
    try:
        url = "https://www.vinted.fr/api/v2/catalog/items"
        params = {"search_text": query, "per_page": 30, "order": "newest_first"}
        r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        for item in data.get("items", []):
            price_info = item.get("price") or {}
            listings.append(Listing(
                site="Vinted",
                title=item.get("title", ""),
                price=float(price_info["amount"]) if price_info.get("amount") else None,
                location=item.get("city", "") or "",
                url=item.get("url", ""),
                thumbnail=(item.get("photo") or {}).get("url", "") or "",
                description=item.get("title", ""),
            ))
        status["Vinted"] = f"OK — {len(listings)} annonce(s)"
    except Exception as e:
        status["Vinted"] = f"Échec ({e})"
    return listings


def scrape_leboncoin(query: str, status: dict) -> list[Listing]:
    """
    Utilise l'API 'finder' publique consommée par le site web de Leboncoin.
    Protégée par Datadome : des échecs intermittents (403) sont normaux.
    """
    listings: list[Listing] = []
    try:
        url = "https://api.leboncoin.fr/finder/search"
        payload = {"filters": {"keywords": {"text": query}}, "limit": 30, "limit_alu": 3}
        r = requests.post(
            url, json=payload,
            headers={**HEADERS, "Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        for ad in data.get("ads", []):
            price_list = ad.get("price") or []
            thumb = (ad.get("images") or {}).get("thumb_url", "") or ""
            listings.append(Listing(
                site="Leboncoin",
                title=ad.get("subject", ""),
                price=float(price_list[0]) if price_list else None,
                location=(ad.get("location") or {}).get("city", "") or "",
                url=ad.get("url", ""),
                thumbnail=thumb,
                description=ad.get("body", "") or "",
            ))
        status["Leboncoin"] = f"OK — {len(listings)} annonce(s)"
    except Exception as e:
        status["Leboncoin"] = f"Échec, probablement bloqué par la protection anti-robot ({e})"
    return listings


def scrape_ebay(query: str, status: dict) -> list[Listing]:
    """
    Utilise un navigateur headless plutôt que requests : eBay bloquait les
    requêtes simples (403), probablement à cause de leur système anti-robot
    qui détecte les clients qui ne se comportent pas comme un vrai navigateur.
    """
    listings: list[Listing] = []
    try:
        from urllib.parse import quote
        url = f"https://www.ebay.fr/sch/i.html?_nkw={quote(query)}&_sacat=0"
        html = fetch_rendered_html(url, wait_selector="li.s-item")
        soup = BeautifulSoup(html, "lxml")
        for card in soup.select("li.s-item")[:30]:
            title_el = card.select_one(".s-item__title")
            price_el = card.select_one(".s-item__price")
            link_el = card.select_one("a.s-item__link")
            img_el = card.select_one("img.s-item__image-img, img.s-item__image")
            if not title_el or not link_el:
                continue
            price = None
            if price_el:
                m = re.search(r"[\d,.]+", price_el.get_text().replace("\xa0", ""))
                if m:
                    price = float(m.group().replace(",", "."))
            listings.append(Listing(
                site="eBay",
                title=title_el.get_text(strip=True),
                price=price,
                location="",
                url=link_el.get("href", "").split("?")[0],
                thumbnail=img_el.get("src", "") if img_el else "",
            ))
        status["eBay"] = f"OK — {len(listings)} annonce(s)"
    except Exception as e:
        status["eBay"] = f"Échec ({e})"
    return listings


def scrape_rakuten(query: str, status: dict) -> list[Listing]:
    """Navigateur headless pour la même raison que pour eBay (blocage 403 en requête simple)."""
    listings: list[Listing] = []
    try:
        from urllib.parse import quote
        url = f"https://fr.shopping.rakuten.com/search?q={quote(query)}"
        html = fetch_rendered_html(url)
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select("[data-testid='product-card'], .product-card")[:30]
        for card in cards:
            title_el = card.select_one("h3, [data-testid='product-title']")
            price_el = card.select_one("[data-testid='price'], .price")
            link_el = card.select_one("a")
            img_el = card.select_one("img")
            if not title_el or not link_el:
                continue
            price = None
            if price_el:
                m = re.search(r"[\d,.]+", price_el.get_text().replace("\xa0", ""))
                if m:
                    price = float(m.group().replace(",", "."))
            href = link_el.get("href", "")
            listings.append(Listing(
                site="Rakuten",
                title=title_el.get_text(strip=True),
                price=price,
                location="",
                url=href if href.startswith("http") else f"https://fr.shopping.rakuten.com{href}",
                thumbnail=img_el.get("src", "") if img_el else "",
            ))
        status["Rakuten"] = f"OK — {len(listings)} annonce(s)"
    except Exception as e:
        status["Rakuten"] = f"Échec ({e})"
    return listings


def scrape_cdiscount(query: str, status: dict) -> list[Listing]:
    """
    Cdiscount charge ses résultats de recherche en JavaScript après le
    chargement initial de la page : un simple requests.get() ne voit qu'une
    page vide. On utilise donc un navigateur headless (fetch_rendered_html),
    puis une extraction générique par prix/image plutôt que des sélecteurs
    CSS figés, dont on ne connaît pas la version exacte actuellement utilisée
    par le site.
    """
    listings: list[Listing] = []
    try:
        slug = query.replace(" ", "+")
        url = f"https://www.cdiscount.com/search/10/{slug}.html"
        html = fetch_rendered_html(url, wait_selector="a[href]")
        listings = extract_generic_listings(html, "https://www.cdiscount.com", "Cdiscount")
        status["Cdiscount"] = f"OK — {len(listings)} annonce(s)"
    except Exception as e:
        status["Cdiscount"] = f"Échec ({e})"
    return listings


SCRAPERS = {
    "Vinted": scrape_vinted,
    "Leboncoin": scrape_leboncoin,
    "eBay": scrape_ebay,
    "Rakuten": scrape_rakuten,
    "Cdiscount": scrape_cdiscount,
}


def load_config(path: str = "config.json") -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_seen(path: str = "data/seen_listings.json") -> set:
    p = Path(path)
    if p.exists():
        try:
            return set(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_seen(seen: set, path: str = "data/seen_listings.json") -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted(seen)), encoding="utf-8")


HTML_TEMPLATE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Veille annonces — {{ config.query }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0D1321;
    --card: #16213E;
    --card-border: #223159;
    --accent: #E85D04;
    --accent-soft: #F48C42;
    --visor: #4CC9F0;
    --text: #F1F1F1;
    --muted: #94A0B8;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: Inter, system-ui, sans-serif;
    line-height: 1.5;
  }
  header {
    padding: 2.5rem 1.5rem 1.5rem;
    max-width: 1100px;
    margin: 0 auto;
  }
  .eyebrow {
    font-family: "Press Start 2P", monospace;
    font-size: 0.6rem;
    color: var(--visor);
    letter-spacing: 0.05em;
  }
  h1 {
    font-size: 1.9rem;
    margin: 0.6rem 0 0.3rem;
    font-weight: 700;
  }
  .meta {
    color: var(--muted);
    font-size: 0.9rem;
  }
  .criteria {
    max-width: 1100px;
    margin: 0 auto 1.5rem;
    padding: 0 1.5rem;
    color: var(--muted);
    font-size: 0.9rem;
  }
  .criteria b { color: var(--text); }
  main {
    max-width: 1100px;
    margin: 0 auto;
    padding: 0 1.5rem 3rem;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 1.1rem;
  }
  .card {
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: 10px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    position: relative;
  }
  .card .thumb {
    width: 100%;
    aspect-ratio: 4 / 3;
    object-fit: cover;
    background: #0A0F1C;
    display: block;
  }
  .card .thumb.placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--muted);
    font-size: 0.8rem;
  }
  .card .body {
    padding: 0.9rem 1rem 1.1rem;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    flex: 1;
  }
  .badge-row {
    display: flex;
    gap: 0.4rem;
    align-items: center;
  }
  .site-badge {
    font-size: 0.7rem;
    color: var(--visor);
    border: 1px solid var(--visor);
    border-radius: 999px;
    padding: 0.1rem 0.55rem;
  }
  .new-badge {
    font-size: 0.7rem;
    color: var(--bg);
    background: var(--accent);
    border-radius: 999px;
    padding: 0.1rem 0.55rem;
    font-weight: 600;
  }
  .card h3 {
    font-size: 1rem;
    margin: 0.1rem 0;
    font-weight: 600;
  }
  .price {
    font-size: 1.2rem;
    font-weight: 700;
    color: var(--accent-soft);
  }
  .location {
    color: var(--muted);
    font-size: 0.82rem;
  }
  .card a.cta {
    margin-top: auto;
    text-align: center;
    text-decoration: none;
    background: var(--visor);
    color: #06222E;
    font-weight: 600;
    padding: 0.55rem;
    border-radius: 7px;
    font-size: 0.9rem;
  }
  .empty {
    color: var(--muted);
    padding: 3rem 0;
    text-align: center;
  }
  footer {
    max-width: 1100px;
    margin: 0 auto;
    padding: 1.5rem 1.5rem 3rem;
    color: var(--muted);
    font-size: 0.82rem;
    border-top: 1px solid var(--card-border);
  }
  footer table { border-collapse: collapse; margin-top: 0.6rem; }
  footer td { padding: 0.15rem 0.8rem 0.15rem 0; vertical-align: top; }
</style>
</head>
<body>
<header>
  <div class="eyebrow">VEILLE AUTOMATIQUE</div>
  <h1>{{ config.query }}</h1>
  <div class="meta">Dernière mise à jour : {{ generated_at }} · {{ results|length }} annonce(s) correspondante(s)</div>
</header>

<div class="criteria">
  Filtres actifs :
  {% if config.price_min or config.price_max %}<b>prix {{ config.price_min or 0 }}–{{ config.price_max or '∞' }} €</b> · {% endif %}
  {% if config.include_keywords %}<b>doit contenir : {{ config.include_keywords|join(', ') }}</b> · {% endif %}
  {% if config.exclude_keywords %}<b>exclut : {{ config.exclude_keywords|join(', ') }}</b>{% endif %}
</div>

<main>
  {% if results %}
  <div class="grid">
    {% for l in results %}
    <div class="card">
      {% if l.thumbnail %}
      <img class="thumb" src="{{ l.thumbnail }}" alt="{{ l.title }}" loading="lazy">
      {% else %}
      <div class="thumb placeholder">Pas de photo</div>
      {% endif %}
      <div class="body">
        <div class="badge-row">
          <span class="site-badge">{{ l.site }}</span>
          {% if l.listing_id in new_ids %}<span class="new-badge">NOUVEAU</span>{% endif %}
        </div>
        <h3>{{ l.title }}</h3>
        <div class="price">{{ "%.0f"|format(l.price) if l.price is not none else "Prix non précisé" }}{{ " €" if l.price is not none else "" }}</div>
        {% if l.location %}<div class="location">📍 {{ l.location }}</div>{% endif %}
        <a class="cta" href="{{ l.url }}" target="_blank" rel="noopener">Voir l'annonce</a>
      </div>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="empty">Aucune annonce ne correspond aux critères pour l'instant. La page se met à jour automatiquement 2 fois par jour.</div>
  {% endif %}
</main>

<footer>
  État des sites lors de cette exécution :
  <table>
    {% for site, msg in status.items() %}
    <tr><td><b>{{ site }}</b></td><td>{{ msg }}</td></tr>
    {% endfor %}
  </table>
</footer>
</body>
</html>
"""


def render_html(results: list[Listing], status: dict, config: dict, new_ids: set,
                 out_path: str = "docs/index.html") -> None:
    template = Template(HTML_TEMPLATE)
    html = template.render(
        results=sorted(results, key=lambda x: (x.price is None, x.price or 0)),
        status=status,
        config=config,
        new_ids=new_ids,
        generated_at=datetime.now().strftime("%d/%m/%Y à %H:%M"),
    )
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")


def main() -> None:
    config = load_config()
    query = config["query"]
    status: dict[str, str] = {}
    all_listings: list[Listing] = []

    sites = config.get("sites") or list(SCRAPERS.keys())
    for site in sites:
        scraper = SCRAPERS.get(site)
        if not scraper:
            status[site] = "Pas de scraper écrit pour ce site"
            continue
        all_listings.extend(scraper(query, status))
        time.sleep(1)  # petite pause polie entre deux sites

    filtered = [l for l in all_listings if matches_criteria(l, config)]

    seen = load_seen()
    new_ids = {l.listing_id for l in filtered if l.listing_id not in seen}
    seen |= {l.listing_id for l in filtered}
    save_seen(seen)

    render_html(filtered, status, config, new_ids)
    print(f"{len(filtered)} annonce(s) trouvée(s), {len(new_ids)} nouvelle(s).")
    for site, msg in status.items():
        print(f"  - {site}: {msg}")


if __name__ == "__main__":
    main()
