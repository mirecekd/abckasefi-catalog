# abckasefi-catalog

ABCKASEFI - ABeCedni KAtalog SErialu a FIlmu: a small self-hosted movie and series catalog service

<div align="center">

[![\"Buy Me A Coffee\"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/mirecekdg) [![\"PayPal.me\"](https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif)](https://www.paypal.com/donate/?business=LJ5ZF7Q9KMTRW&no_recurring=0&currency_code=USD)

</div>

## What it is

abckasefi-catalog is a FastAPI + SQLite service that builds a browsable catalog of movies and series and serves it over a small HTTP API and a built-in web UI.

- The catalog is built from the public IMDb datasets (`title.basics`, `title.ratings`, `title.akas`). A title is kept when it has at least `MIN_VOTES` IMDb votes, or when it has a Czech or Slovak title and at least `LOCAL_MIN_VOTES` votes. This gives roughly 143k titles.
- Titles are browsable A-Z by their first letter, with all non-letters grouped under `0-9`.
- Search works without diacritics (SQLite FTS5 over a diacritics-free key).
- Every title can carry Czech, Slovak and English names (`cs`, `sk`, `en`); the `lang` query parameter picks which one is shown.
- TMDB lists (top rated, popular, now playing, trending, ...) are mapped to IMDb ids and served through the same item format.
- Posters, backdrops and overviews are fetched lazily from TMDB on first request and cached in `cache.db`.
- Series expose seasons and episodes. TMDB is used first; [Cinemeta](https://v3-cinemeta.strem.io/) is the fallback for series TMDB does not know.
- Title and episode detail carry `plugin://plugin.video.nokturno/...` play links.

TMDB features need a TMDB API key. Without one the catalog, search and web UI still work, but there are no posters, overviews, TMDB lists or seasons.

## Companion Kodi add-on

[plugin.video.abckasefi](https://github.com/mirecekd/plugin.video.abckasefi) is a Kodi add-on that lists titles from this service and hands playback over to the Nokturno add-on.

## Quick start

Requirements: Docker with the compose plugin.

```sh
sudo install -d -o 10001 -g 10001 /opt/abckasefi-catalog/data
cp .env.example .env
# edit .env: set TMDB_API_KEY and API_TOKEN
docker compose up -d --build
```

The data directory must be writable by uid 10001, the user the container runs as. To keep the data elsewhere, set `DATA_PATH` in `.env`.

The first start downloads the IMDb datasets and builds the catalog in the background. This takes about 2 minutes, and peak memory is about 540 MiB; the compose file limits the container to 900m. While the catalog is being built, data routes answer `503` and `/health` reports `"ready": false`.

The catalog is rebuilt automatically when it is older than `IMPORT_EVERY_HOURS`.

A published multi-arch image (`linux/amd64`, `linux/arm64`) is available as `ghcr.io/mirecekd/abckasefi-catalog:latest`. To use it instead of building locally, replace `build: .` in `docker-compose.yml` with that image reference.

## Configuration

Settings are environment variables, read from `.env` by docker compose.

| Variable | Default | Meaning |
| --- | --- | --- |
| `TMDB_API_KEY` | empty | TMDB v3 API key or v4 read access token (a token starting with `eyJ` is sent as a Bearer header, anything else as the `api_key` query parameter). Empty disables all TMDB features. |
| `API_TOKEN` | empty | Shared secret for the API and the web UI. Empty means no authentication - only do that on a trusted LAN. |
| `TMDB_LANGUAGE` | `cs-CZ` | Language sent to TMDB by default (overviews, list titles). Its first two letters are also the title language of list entries the catalog does not hold. |
| `MIN_VOTES` | `200` | Keep titles with at least this many IMDb votes. |
| `LOCAL_MIN_VOTES` | `5` | Also keep titles with a Czech or Slovak name and at least this many votes. |
| `IMPORT_EVERY_HOURS` | `168` | Rebuild the catalog from IMDb when it is at least this old (checked hourly). |
| `DATA_DIR` | `/data` | Directory inside the container for `catalog.db`, `cache.db` and the downloaded IMDb datasets. Set in the Dockerfile; you normally do not change it. |
| `DATA_PATH` | `/opt/abckasefi-catalog/data` | Used by `docker-compose.yml` only: the host directory mounted at `/data`. Must be owned by uid 10001. |

A non-numeric value for `MIN_VOTES`, `LOCAL_MIN_VOTES` or `IMPORT_EVERY_HOURS` falls back to its default.

## HTTP API

Authentication: when `API_TOKEN` is set, send `Authorization: Bearer <token>` or add `?token=<token>`. A wrong or missing token gives `401`. Image clients cannot send headers, so use `?token=` for `/v1/poster/...`.

Public routes (no token): `GET /health`, the web UI at `GET /`, `/static/...`, and the FastAPI-generated `/docs` and `/openapi.json`. Everything under `/v1/` needs the token.

| Route | Query parameters | Description |
| --- | --- | --- |
| `GET /health` | none | Public. Returns `ok`, `ready` (catalog built), `tmdb` (key configured), title counts and build metadata. |
| `GET /v1/ping` | none | Cheap authenticated check: a `200` proves both the URL and the token. Returns `ok`, `version`, `tmdb`, `ready`. |
| `GET /v1/letters` | `type` = `movie` (default) or `series` | Letters with title counts, `0-9` last. |
| `GET /v1/titles` | `type` (`movie`/`series`, default `movie`), `letter` (default `A`, or `0-9`), `sort` = `name` (default), `rating` or `year`, `lang` = `cs`, `sk`, `en` (or empty), `page` (>= 1, default 1), `per_page` (1-200, default 50) | One page of the A-Z listing. Returns `page`, `per_page`, `has_more` and `items`. |
| `GET /v1/search` | `q` (2-80 characters, required), `type` (`movie`, `series` or empty for both), `sort` = `popular` (default, by votes), `name`, `rating` or `year`, `lang`, `limit` (1-200, default 40) | Full-text search without diacritics. The order is applied to all matches before `limit`. `has_more` says whether more than `limit` titles match. |
| `GET /v1/lists` | none | Names of the available TMDB lists per type. |
| `GET /v1/list/{type}/{name}` | `page` (1-500, default 1), `sort` = `tmdb` (default), `rating`, `votes`, `year` or `name`, `lang`, `per_page` (1-100, default 20) | A TMDB list mapped to IMDb ids. Titles missing from the catalog are still returned, built from the TMDB data. Needs `TMDB_API_KEY`. |
| `GET /v1/title/{id}` | `lang` | Title detail (`id` is an IMDb id such as `tt0068646`): catalog data plus lazily fetched overview, TMDB rating, runtime, poster and backdrop URLs, and a Nokturno `play` link. |
| `GET /v1/title/{id}/seasons` | none | Seasons of a series (TMDB, Cinemeta fallback) with a Nokturno `play` link. Needs `TMDB_API_KEY`. |
| `GET /v1/title/{id}/season/{season}` | `lang` | Episodes of one season (0-200, 0 = specials), each with a Nokturno `play` link. Needs `TMDB_API_KEY`. |
| `GET /v1/poster/{id}` | `size` = `w92`, `w154`, `w185` (default), `w342` or `w500`; `token` | `302` redirect to the TMDB poster, resolved on first request and then cached. `404` if there is none. |

Notes:

- `type` in `/v1/list/{type}/{name}` is `movie` or `series`. Movie lists: `top_rated`, `popular`, `now_playing`, `upcoming`, `trending_day`, `trending_week`. Series lists: `top_rated`, `popular`, `airing_today`, `on_the_air`, `trending_day`, `trending_week`. `GET /v1/lists` returns the same names.
- Paging: `per_page` sets the page size and `has_more` tells whether a later page exists, so clients can page until it is `false`.
- TMDB serves lists in pages of 20 and refuses pages above 500, so a list is capped at 500 x 20 = 10000 items. A page past the end returns an empty `items` list instead of an error.
- List `sort` orders the items of the requested page only (`sorted_within_page` says so). Ratings based on very few votes are flagged with `rating_reliable: false` and ranked below better-supported ones.
- `lang` accepts `cs`, `sk` or `en`; anything else gives `400`. When a title has no name in the requested language, the display title falls back (cs to sk, sk to cs, then the English or original name) and `title_lang` shows the language actually used.
- An invalid `type`, `sort` or `lang` gives `400`. A malformed title id gives `404`. While the catalog is not built yet, data routes give `503`.

## Web UI

Open `http://host:8090/` in a browser. When `API_TOKEN` is set, add the token: `http://host:8090/?token=<API_TOKEN>`. The page uses it for its API calls and poster images.

## Security notes

- Set `API_TOKEN` unless the service is reachable only from a trusted LAN. With an empty token anyone who can reach the port can use the API.
- The token travels in clear over plain HTTP, and `?token=` URLs end up in browser history and proxy logs. For anything outside your LAN put the service behind a TLS reverse proxy such as Caddy or nginx.
- `TMDB_API_KEY` is only ever sent to `api.themoviedb.org` and is never logged. The httpx request logger is lowered to warning level because it would otherwise print the full URL, including a v3 key in the query string.
- The container runs as the non-root user with uid 10001.

## Data and licensing

- The catalog is built from the [IMDb non-commercial datasets](https://developer.imdb.com/non-commercial-datasets/). They may be used for personal, non-commercial purposes only. Do not publish or redistribute the built catalog (`catalog.db`).
- Posters, overviews and TMDB lists come from TMDB. This product uses the TMDB API but is not endorsed or certified by TMDB.
- Cinemeta is used as a fallback source of seasons and episodes for series TMDB does not know.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest pyright
.venv/bin/python -m pytest -q
.venv/bin/pyright app tests
```

To run the service without Docker: `DATA_DIR=./data .venv/bin/uvicorn app.main:app --port 8090`.

## License

MIT - see [LICENSE](LICENSE).

## Support

If you find this useful, you can support the development:

<div align="center">

[![\"Buy Me A Coffee\"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/mirecekdg) [![\"PayPal.me\"](https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif)](https://www.paypal.com/donate/?business=LJ5ZF7Q9KMTRW&no_recurring=0&currency_code=USD)

</div>
