import os
import sys
import time
import logging
import requests
from datetime import datetime, timedelta, timezone

import spotipy
from spotipy.oauth2 import SpotifyOAuth

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["SPOTIFY_REFRESH_TOKEN"]
PLAYLIST_ID = os.environ.get("SPOTIFY_PLAYLIST_ID", "")

PLAYLIST_NAME = "アニソン Daily Mix"
PLAYLIST_DESCRIPTION = "毎日自動追加：新着アニソン"

MAX_ADD_PER_RUN = 20
DAYS_LOOKBACK = 14
MAX_RETRIES = 3

# Spotify Japan が管理するアニメ編集プレイリスト
EDITORIAL_PLAYLIST_IDS = [
    "37i9dQZF1E4suFKubfL1R8",  # Anime de Japan Radio
    "37i9dQZF1E4Cz38vKP0fPr",  # Anime Song Club! JAPAN Radio
]

# フランチャイズ・レーベル系キーワード（編集PL/AniListで拾えなかった分の補完）
PRIORITY_KEYWORDS = [
    "アイドルマスター", "THE IDOLM@STER",
    "ラブライブ", "Love Live",
    "バンドリ", "BanG Dream",
    "プロジェクトセカイ", "プロセカ",
    "ウマ娘", "プリコネ",
    "ガンダム", "プリキュア",
    "SACRA MUSIC", "ランティス",
]

# 一般キーワード（最終補完）
GENERAL_KEYWORDS = [
    "アニソン", "アニメ主題歌", "TVアニメ", "アニメED", "アニメOP",
    "声優", "アニソンアーティスト",
]

# トラック名・アルバム名・アーティスト名にこれらが含まれる曲を除外する
EXCLUDE_WORDS = [
    "オルゴール", "music box", "musicbox",
    "ピアノ", "piano",
    "アコースティック", "acoustic",
    "ギター", "guitar",
    "オーケストラ", "orchestra",
    "アレンジ", "arrange",
    "instrumental", "インストゥルメンタル", "インスト",
    "off vocal", "offvocal", "カラオケ", "karaoke",
    "backing track",
    "朗読", "ナレーション", "narration", "読み聞かせ",
    "bgm", "se ", "効果音",
    "子守唄", "lullaby", "睡眠", "relax", "リラックス",
    "bossa nova", "cover", "カバー",
    "8bit", "8ビット", "ヒーリング", "healing",
    "short ver", "short version",
]

# アーティスト名が完全一致する場合に除外（ノイズアーティスト）
EXCLUDE_ARTISTS = {
    "totodit",
}

ANILIST_API = "https://graphql.anilist.co"


# ── Spotify クライアント ──────────────────────────────────────────────

def get_spotify_client() -> spotipy.Spotify:
    auth_manager = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri="http://127.0.0.1:8888/callback",
        scope="playlist-modify-public playlist-modify-private playlist-read-private",
    )
    token_info = auth_manager.refresh_access_token(REFRESH_TOKEN)
    return spotipy.Spotify(auth=token_info["access_token"])


def with_retry(fn, *args, **kwargs):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 429:
                retry_after = int(e.headers.get("Retry-After", 2 ** attempt))
                logger.warning(f"Rate limited. Retrying in {retry_after}s (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(retry_after)
            else:
                raise
    raise RuntimeError(f"Failed after {MAX_RETRIES} retries")


# ── 日付ユーティリティ ────────────────────────────────────────────────

def get_date_range() -> tuple[str, str]:
    jst = timezone(timedelta(hours=9))
    today = datetime.now(jst).date()
    since = today - timedelta(days=DAYS_LOOKBACK)
    return str(since), str(today)


def get_current_season() -> tuple[str, int]:
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    month, year = now.month, now.year
    if month in [1, 2, 3]:
        return "WINTER", year
    elif month in [4, 5, 6]:
        return "SPRING", year
    elif month in [7, 8, 9]:
        return "SUMMER", year
    else:
        return "FALL", year


# ── AniList: 今期アニメタイトル取得 ──────────────────────────────────

def get_current_season_titles(limit: int = 25) -> list[str]:
    season, year = get_current_season()
    query = """
    query ($season: MediaSeason, $year: Int, $limit: Int) {
      Page(page: 1, perPage: $limit) {
        media(
          season: $season, seasonYear: $year,
          type: ANIME, status: RELEASING,
          sort: POPULARITY_DESC
        ) {
          title { native romaji }
        }
      }
    }
    """
    try:
        resp = requests.post(
            ANILIST_API,
            json={"query": query, "variables": {"season": season, "year": year, "limit": limit}},
            timeout=10,
        )
        resp.raise_for_status()
        media_list = resp.json()["data"]["Page"]["media"]
        titles = [m["title"]["native"] for m in media_list if m["title"]["native"]]
        logger.info(f"AniList: {season} {year} — {len(titles)} airing anime titles")
        return titles
    except Exception as e:
        logger.warning(f"AniList fetch failed: {e}")
        return []


# ── Spotify 検索 ──────────────────────────────────────────────────────

def search_tracks(sp: spotipy.Spotify, query: str, date_from: str, date_to: str) -> list[dict]:
    results = []
    full_query = f"{query} year:{date_from[:4]}"
    offset = 0
    limit = 50
    while offset < 200:
        response = with_retry(
            sp.search,
            q=full_query,
            type="track",
            limit=limit,
            offset=offset,
            market="JP",
        )
        items = response["tracks"]["items"]
        if not items:
            break
        for track in items:
            release_date = track["album"].get("release_date", "")
            if release_date >= date_from:
                results.append(track)
        if len(items) < limit:
            break
        offset += limit
    return results


# ── Spotify 編集プレイリストから取得 ─────────────────────────────────

def get_editorial_tracks(sp: spotipy.Spotify, playlist_ids: list[str], date_from: str) -> list[dict]:
    tracks = []
    seen: set[str] = set()
    for pid in playlist_ids:
        try:
            offset = 0
            count = 0
            while True:
                resp = with_retry(
                    sp.playlist_items,
                    pid,
                    limit=100,
                    offset=offset,
                    fields="items(track(id,name,artists,album(name,release_date))),next",
                )
                for item in resp.get("items", []):
                    track = item.get("track")
                    if not track or not track.get("id"):
                        continue
                    release_date = track["album"].get("release_date", "")
                    if release_date >= date_from and track["id"] not in seen:
                        seen.add(track["id"])
                        tracks.append(track)
                        count += 1
                if not resp.get("next"):
                    break
                offset += 100
            logger.info(f"Editorial playlist {pid}: {count} tracks in date range")
        except Exception as e:
            logger.warning(f"Editorial playlist {pid} failed: {e}")
    return tracks


# ── フィルタ ──────────────────────────────────────────────────────────

def is_excluded(track: dict) -> bool:
    track_name = track.get("name", "").lower()
    album_name = track["album"].get("name", "").lower()
    artists = track.get("artists", [])
    artist_names_combined = " ".join(a["name"] for a in artists).lower()

    for a in artists:
        if a["name"].lower() in EXCLUDE_ARTISTS:
            return True

    for word in EXCLUDE_WORDS:
        if word in track_name or word in album_name or word in artist_names_combined:
            return True

    return False


# ── プレイリスト管理 ──────────────────────────────────────────────────

def get_playlist_track_ids(sp: spotipy.Spotify, playlist_id: str) -> set[str]:
    existing = set()
    offset = 0
    while True:
        response = with_retry(
            sp.playlist_items,
            playlist_id,
            limit=100,
            offset=offset,
            fields="items.track.id,next",
        )
        for item in response["items"]:
            track = item.get("track")
            if track and track.get("id"):
                existing.add(track["id"])
        if not response.get("next"):
            break
        offset += 100
    return existing


def ensure_playlist(sp: spotipy.Spotify) -> str:
    global PLAYLIST_ID
    if PLAYLIST_ID:
        try:
            with_retry(sp.playlist, PLAYLIST_ID)
            logger.info(f"Playlist found: {PLAYLIST_ID}")
            return PLAYLIST_ID
        except spotipy.exceptions.SpotifyException:
            logger.warning("SPOTIFY_PLAYLIST_ID is set but playlist not found. Creating new one.")

    user_id = with_retry(sp.current_user)["id"]
    playlist = with_retry(
        sp.user_playlist_create,
        user=user_id,
        name=PLAYLIST_NAME,
        public=True,
        description=PLAYLIST_DESCRIPTION,
    )
    new_id = playlist["id"]
    logger.info(f"Created new playlist: {new_id}")
    logger.info(f"ACTION REQUIRED: Set SPOTIFY_PLAYLIST_ID={new_id} in GitHub Secrets")
    return new_id


# ── メイン ────────────────────────────────────────────────────────────

def main():
    logger.info("=== Spotify Anison Batch Start ===")

    sp = get_spotify_client()
    date_from, date_to = get_date_range()
    logger.info(f"Search range: {date_from} to {date_to}")

    playlist_id = ensure_playlist(sp)

    # ── Source 1: Spotify 編集プレイリスト（最高品質）
    logger.info("--- Source 1: Spotify editorial playlists ---")
    editorial_tracks = get_editorial_tracks(sp, EDITORIAL_PLAYLIST_IDS, date_from)

    # ── Source 2: AniList 今期アニメ OP/ED
    logger.info("--- Source 2: Current season OP/ED (AniList) ---")
    season_titles = get_current_season_titles(limit=25)
    season_tracks = []
    for title in season_titles:
        found = search_tracks(sp, f'"{title}"', date_from, date_to)
        if found:
            logger.info(f"  '{title}': {len(found)} tracks")
        season_tracks.extend(found)
    logger.info(f"Season OP/ED total: {len(season_tracks)} tracks")

    # ── Source 3: フランチャイズ・レーベル系キーワード（補完）
    logger.info("--- Source 3: Franchise/label keywords ---")
    priority_tracks = []
    for keyword in PRIORITY_KEYWORDS:
        found = search_tracks(sp, keyword, date_from, date_to)
        if found:
            logger.info(f"  '{keyword}': {len(found)} tracks")
        priority_tracks.extend(found)

    # ── Source 4: 一般キーワード（最終補完）
    logger.info("--- Source 4: General keywords ---")
    general_tracks = []
    for keyword in GENERAL_KEYWORDS:
        found = search_tracks(sp, keyword, date_from, date_to)
        if found:
            logger.info(f"  '{keyword}': {len(found)} tracks")
        general_tracks.extend(found)

    # ── マージ（優先順: 編集PL → 今期OP/ED → フランチャイズ → 一般）
    seen_ids: set[str] = set()
    candidates: list[dict] = []
    for track in editorial_tracks + season_tracks + priority_tracks + general_tracks:
        tid = track.get("id")
        if tid and tid not in seen_ids:
            seen_ids.add(tid)
            candidates.append(track)
    logger.info(f"Merged candidates: {len(candidates)} unique tracks")

    if not candidates:
        logger.info("No candidates found. Exiting normally.")
        return

    # ── 除外フィルタ
    before = len(candidates)
    candidates = [t for t in candidates if not is_excluded(t)]
    logger.info(f"After exclusion filter: {len(candidates)} tracks (excluded {before - len(candidates)})")

    if not candidates:
        logger.info("No candidates after filter. Exiting normally.")
        return

    # ── 既存プレイリストとの重複除去
    existing_ids = get_playlist_track_ids(sp, playlist_id)
    new_tracks = [t for t in candidates if t["id"] not in existing_ids]
    logger.info(f"After duplicate exclusion: {len(new_tracks)} new tracks")

    if not new_tracks:
        logger.info("All candidates already in playlist. Exiting normally.")
        return

    # ── プレイリストに追加
    to_add = new_tracks[:MAX_ADD_PER_RUN]
    uris = [f"spotify:track:{t['id']}" for t in to_add]
    with_retry(sp.playlist_add_items, playlist_id, uris)

    logger.info(f"Added {len(to_add)} tracks to playlist:")
    for track in to_add:
        artists = ", ".join(a["name"] for a in track["artists"])
        logger.info(f"  {track['name']} - {artists}")

    logger.info(f"Skipped (over limit): {len(new_tracks) - len(to_add)}")
    logger.info(f"Already in playlist: {len(candidates) - len(new_tracks)}")
    logger.info("=== Spotify Anison Batch Complete ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
