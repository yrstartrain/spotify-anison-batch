import os
import sys
import time
import logging
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

# トラック名・アルバム名にこれらが含まれる曲を除外する
EXCLUDE_WORDS = [
    # オルゴール・アレンジ系
    "オルゴール", "music box", "musicbox",
    "ピアノ", "piano",
    "アコースティック", "acoustic",
    "ギター", "guitar",
    "オーケストラ", "orchestra",
    "アレンジ", "arrange",
    # ボーカルなし系
    "instrumental", "インストゥルメンタル", "インスト",
    "off vocal", "offvocal", "カラオケ", "karaoke",
    "backing track",
    # 朗読・ナレーション系
    "朗読", "ナレーション", "narration", "読み聞かせ",
    # 効果音・BGM系
    "bgm", "se ", "効果音",
    # 子守唄・リラックス系
    "子守唄", "lullaby", "睡眠", "relax", "リラックス",
    # カバー・アレンジバージョン系
    "bossa nova", "cover", "カバー",
    # 8bit・ヒーリング系（アーティスト名に含まれるケースも多い）
    "8bit", "8ビット", "ヒーリング", "healing",
    # その他
    "short ver", "short version",
]


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


def get_date_range() -> tuple[str, str]:
    jst = timezone(timedelta(hours=9))
    today = datetime.now(jst).date()
    since = today - timedelta(days=DAYS_LOOKBACK)
    return str(since), str(today)


def search_tracks(sp: spotipy.Spotify, query: str, date_from: str, date_to: str) -> list[dict]:
    """Search for tracks and return list of track objects."""
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


def is_excluded(track: dict) -> bool:
    track_name = track.get("name", "").lower()
    album_name = track["album"].get("name", "").lower()
    artist_names = " ".join(a["name"] for a in track.get("artists", [])).lower()
    for word in EXCLUDE_WORDS:
        if word in track_name or word in album_name or word in artist_names:
            return True
    return False


def get_playlist_track_ids(sp: spotipy.Spotify, playlist_id: str) -> set[str]:
    existing = set()
    offset = 0
    limit = 100
    while True:
        response = with_retry(sp.playlist_items, playlist_id, limit=limit, offset=offset, fields="items.track.id,next")
        for item in response["items"]:
            track = item.get("track")
            if track and track.get("id"):
                existing.add(track["id"])
        if not response.get("next"):
            break
        offset += limit
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


def main():
    logger.info("=== Spotify Anison Batch Start ===")

    sp = get_spotify_client()
    date_from, date_to = get_date_range()
    logger.info(f"Search range: {date_from} to {date_to}")

    playlist_id = ensure_playlist(sp)

    # Route A: genre:anime
    logger.info("Route A: genre:anime search")
    route_a = search_tracks(sp, "genre:anime", date_from, date_to)
    logger.info(f"Route A results: {len(route_a)} tracks")

    # Route B: keyword-based
    logger.info("Route B: keyword search")
    route_b = []
    keywords = [
        # 一般アニソン系
        "アニソン", "アニメ主題歌", "TVアニメ", "アニメED", "アニメOP",
        # 声優・アーティスト系
        "声優", "アニソンアーティスト",
        # 萌え・アイドルアニメ系フランチャイズ
        "アイドルマスター", "THE IDOLM@STER",
        "ラブライブ", "Love Live",
        "バンドリ", "BanG Dream",
        "プロジェクトセカイ", "プロセカ",
        "ウマ娘", "プリコネ",
        # その他人気フランチャイズ
        "ガンダム", "プリキュア",
        # 主要アニメ音楽レーベル
        "ランティス", "SACRA MUSIC",
    ]
    for keyword in keywords:
        found = search_tracks(sp, keyword, date_from, date_to)
        logger.info(f"  keyword '{keyword}': {len(found)} tracks")
        route_b.extend(found)

    # Merge and deduplicate
    seen_ids: set[str] = set()
    candidates: list[dict] = []
    for track in route_a + route_b:
        tid = track.get("id")
        if tid and tid not in seen_ids:
            seen_ids.add(tid)
            candidates.append(track)
    logger.info(f"Merged candidates: {len(candidates)} unique tracks")

    if not candidates:
        logger.info("No candidates found. Exiting normally.")
        return

    # Exclude unwanted tracks by keyword
    before = len(candidates)
    candidates = [t for t in candidates if not is_excluded(t)]
    logger.info(f"After keyword filter: {len(candidates)} tracks (excluded {before - len(candidates)})")

    if not candidates:
        logger.info("No candidates after keyword filter. Exiting normally.")
        return

    # Exclude already-in-playlist tracks
    existing_ids = get_playlist_track_ids(sp, playlist_id)
    new_tracks = [t for t in candidates if t["id"] not in existing_ids]
    logger.info(f"After duplicate exclusion: {len(new_tracks)} new tracks")

    if not new_tracks:
        logger.info("All candidates already in playlist. Exiting normally.")
        return

    # Add up to MAX_ADD_PER_RUN tracks
    to_add = new_tracks[:MAX_ADD_PER_RUN]
    uris = [f"spotify:track:{t['id']}" for t in to_add]
    with_retry(sp.playlist_add_items, playlist_id, uris)

    logger.info(f"Added {len(to_add)} tracks to playlist:")
    for track in to_add:
        artists = ", ".join(a["name"] for a in track["artists"])
        logger.info(f"  {track['name']} - {artists}")

    skipped = len(new_tracks) - len(to_add)
    logger.info(f"Skipped (over limit): {skipped}")
    logger.info(f"Already in playlist (skipped): {len(candidates) - len(new_tracks)}")
    logger.info("=== Spotify Anison Batch Complete ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
