# Veille annonces

Cherche automatiquement une annonce (ex: "coffret collector Metroid Switch") sur
plusieurs sites français, filtre par prix/mots-clés, et publie les résultats sur
une page web mise à jour 2 fois par jour. Aucun coût, aucune clé API nécessaire.

## Installation (une seule fois)

1. **Créer un dépôt GitHub**
   - Sur github.com, clique "New repository"
   - Nomme-le par exemple `veille-annonces`
   - Laisse-le en **Public** (nécessaire pour que GitHub Pages soit gratuit)
   - Ne coche aucune case d'initialisation (pas de README auto)

2. **Déposer les fichiers de ce dossier dans le dépôt**
   - Le plus simple : sur la page du dépôt vide, clique "uploading an existing file"
     et glisse-dépose tout le contenu de ce dossier (en gardant la structure des
     sous-dossiers `.github/`, `docs/`, `data/`)
   - Alternative en ligne de commande :
     ```
     git init
     git remote add origin https://github.com/TON-COMPTE/veille-annonces.git
     git add .
     git commit -m "Premier envoi"
     git push -u origin main
     ```

3. **Activer GitHub Pages**
   - Dans le dépôt : Settings → Pages
   - "Source" : Deploy from a branch
   - Branche : `main`, dossier : `/docs`
   - Enregistrer. GitHub te donne une URL du type
     `https://ton-compte.github.io/veille-annonces/` : c'est la page à consulter
     depuis n'importe quel appareil (tel, PC...)

4. **Vérifier que l'automatisation tourne**
   - Onglet "Actions" du dépôt : tu dois voir le workflow "Veille annonces"
   - Tu peux le lancer tout de suite manuellement avec le bouton "Run workflow"
     (au lieu d'attendre le prochain horaire programmé)
   - S'il échoue, l'onglet Actions montre le détail de l'erreur

C'est tout : ensuite ça tourne seul, 2 fois par jour, et la page se met à jour.

## Modifier la recherche

Tout se règle dans `config.json` :

```json
{
  "query": "coffret collector Metroid Switch",
  "price_min": 0,
  "price_max": 300,
  "include_keywords": ["metroid"],
  "exclude_keywords": ["recherche", "cherche", "echange contre"],
  "sites": ["Vinted", "Leboncoin", "eBay", "Rakuten", "Cdiscount"]
}
```

- `query` : les mots tapés dans la barre de recherche de chaque site
- `include_keywords` : au moins un de ces mots doit apparaître dans le titre/la
  description (laisse la liste vide `[]` pour ne rien exiger de plus)
- `exclude_keywords` : si un de ces mots apparaît, l'annonce est écartée (utile
  pour enlever les "je recherche..." ou les objets à l'unité)
- `price_min` / `price_max` : fourchette de prix en euros
- `sites` : la liste des sites à interroger (retire-en un s'il pose problème)

Après modification, il suffit de pousser le changement sur GitHub (ou de le
modifier directement dans l'éditeur en ligne de GitHub) : le prochain passage
planifié utilisera la nouvelle config.

## À savoir

- **Leboncoin et Vinted** ont des protections anti-robot. Il est normal que ça
  échoue de temps en temps : regarde la section "État des sites" en bas de la
  page générée pour voir si un site bloque systématiquement.
- **eBay, Rakuten, Cdiscount** sont scrapés en lisant leur page de résultats.
  Si un site change sa mise en page, son scraper peut s'arrêter de trouver des
  résultats ; il faudra alors ajuster les sélecteurs dans `search_agent.py`
  (chaque site a sa propre fonction `scrape_xxx`, bien séparée des autres).
- Les annonces marquées "NOUVEAU" sont celles apparues depuis la dernière
  exécution (mémorisées dans `data/seen_listings.json`).
- Usage personnel : ce script interroge les mêmes pages qu'un navigateur, à
  fréquence raisonnable (2x/jour). Il reste bon à savoir que la plupart des
  sites mentionnent le scraping dans leurs conditions d'utilisation.
