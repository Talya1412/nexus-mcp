# nexus-mcp

MCP server (Python + FastMCP, stdio) để giao tiếp với **Nexus Mods REST API v1** + **GraphQL API v2**.

## Setup

```powershell
pip install -r requirements.txt
```

API key được đọc từ biến môi trường `NEXUS_API_KEY` (không hardcode vào code).
Key tạo tại: https://www.nexusmods.com/users/myaccount?tab=api%20access

## Đăng ký trong opencode

Thêm vào `.opencode/opencode.json` của project (hoặc `~/.config/opencode/opencode.json` để dùng toàn cục):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "nexus": {
      "type": "local",
      "command": ["python", "C:/Users/hoang/OneDrive/Documents/Default Project/nexus-mcp/server.py"],
      "enabled": true,
      "environment": {
        "NEXUS_API_KEY": "<key-của-bạn>"
      }
    }
  }
}
```

Đã đăng ký sẵn trong `.opencode/opencode.json` của workspace này — chỉ cần **khởi động lại opencode** là dùng được.

Sau khi sửa config, **khởi động lại opencode** (config chỉ load lúc khởi động).

## Tools

| Tool | Mô tả |
|---|---|
| `nexus_validate_key` | Kiểm tra API key + thông tin tài khoản (exempt khỏi rate limit) |
| `nexus_get_games` | Danh sách game (có filter substring để thu gọn output) |
| `nexus_get_game` | Chi tiết 1 game + categories |
| `nexus_get_mod` | Chi tiết mod (endorsements, downloads, description...) |
| `nexus_get_mod_changelogs` | Changelog theo version |
| `nexus_get_latest_added` / `nexus_get_latest_updated` / `nexus_get_trending` | 10 mod mới/cập nhật/hot của game |
| `nexus_get_updated_mods` | Mod có hoạt động trong `1d`/`1w`/`1m` |
| `nexus_get_mod_files` | Danh sách file của mod (filter category) |
| `nexus_get_file_info` | Chi tiết 1 file (MD5, size, version) |
| `nexus_get_download_link` | Link download ngắn hạn (non-premium cần `key`+`expires` từ link `.nxm`) |
| `nexus_download_mod_file` | Tải file trực tiếp về disk (stream, verify MD5+SHA256, cap `max_bytes`; non-premium vẫn cần `key`+`expires`) |
| `nexus_search_by_md5` | Tìm mod/file từ MD5 hash |
| `nexus_get_tracked_mods` / `nexus_track_mod` / `nexus_untrack_mod` | Quản lý tracked mods |
| `nexus_get_endorsements` / `nexus_endorse_mod` / `nexus_abstain_endorsement` | Quản lý endorsements |

### Tools v2 GraphQL (không tốn quota v1 REST)

| Tool | Mô tả |
|---|---|
| `nexus_search_mods` | Tìm kiếm mod free-text (wildcard) + filter game/endorsements/downloads/adult + sort + phân trang offset — **v1 không có search** |
| `nexus_get_mod_v2` | Chi tiết mod đầy đủ: description BBCode thô, tags, requirements, toàn bộ file list — những thứ v1 không trả về |
| `nexus_get_user_v2` | Profile công khai theo `member_id` hoặc `username` (kudos, modCount, joined...) |
| `nexus_search_collections` | Tìm collection (mod pack) free-text + sort — v1 không có |
| `nexus_graphql_query` | Chạy query GraphQL thô (escape hatch cho power user) |
| `nexus_graphql_introspect` | Introspect type bất kỳ trong schema v2 (tự khám phá API) |
| `nexus_search_users` | Tìm user theo tên (wildcard/exact) — không cần username chính xác |
| `nexus_search_games` | Tìm game free-text + sort (downloads/mods/name...) |
| `nexus_get_game_v2` | Chi tiết game: genre, forum, đếm mod/download/collection, Vortex |
| `nexus_get_files_v2` | File list của mod qua v2 (không tốn quota v1) |
| `nexus_get_mods_batch` | Lấy NHIỀU mod cùng lúc qua 1 query — `"domain:modId,domain:modId"` |
| `nexus_get_mod_endorsers` | Danh sách user endorse 1 mod (cursor pagination) |
| `nexus_get_news` | Tin tức Nexus (site/game news, interviews...) filter theo category/game |
| `nexus_get_categories` | Categories per-game hoặc global (collection categories) |
| `nexus_get_tags` | Tag taxonomy của game (id, name, parentId, blockable...) |
| `nexus_get_collection` | Chi tiết collection theo slug: description BBCode, ratings, tags |
| `nexus_get_collection_revision` | Chi tiết 1 revision: status, rating, số mod, tổng dung lượng |
| `nexus_search_comments` | Tìm comment (⚠️ endpoint `searchComments` của Nexus đang 500 server-side) |
| `nexus_get_comment_thread` | Đọc 1 comment thread đầy đủ: comments + replies (thay thế cho `searchComments` đang lỗi) |
| `nexus_get_comment` | Đọc 1 comment theo ID |
| `nexus_get_badges` | Danh sách badge mod có thể đạt ('Top pick', 'Easy install'...) |
| `nexus_get_user_monthly_summary` | Các tháng user có báo cáo hoạt động hàng tháng |
| `nexus_track_user` / `nexus_untrack_user` | Theo dõi/dừng theo dõi user (mutation v2) |
| `nexus_give_kudos` / `nexus_remove_kudos` | Tặng/gỡ kudos cho user (mutation v2) |
| `nexus_add_favourite_game` / `nexus_remove_favourite_game` | Thêm/bỏ game yêu thích (mutation v2) |
| `nexus_like_comment` / `nexus_remove_comment_like` | Thích/bỏ thích comment (mutation v2) |
| `nexus_create_comment` | Đăng comment vào thread — top-level hoặc **reply lồng vào 1 comment cụ thể** qua `reply_to_id` (mutation v2; đã live test E2E) |
| `nexus_edit_comment` | Sửa nội dung comment **của chính mình** (mutation v2 `updateComment`; đã live test) |
| `nexus_discard_comment` / `nexus_restore_comment` | Xóa mềm / hoàn tác comment (mutation v2; discard đã live test — **restore bị từ chối với API key** ngay cả với comment của mình, có thể cần OAuth) |
| `nexus_update_mod_direct_download` | Bật/tắt direct download cho mod **sở hữu** (mutation v2 — **chỉ chạy được với OAuth**) |
| `nexus_get_files_by_uid` | File theo uid (không cần domain/modId) — truyền `"uid1,uid2"` |
| `nexus_get_favourite_games` | Danh sách game yêu thích của viewer |
| `nexus_get_ignored_users` / `nexus_ignore_user` / `nexus_unignore_user` | Quản lý ignored users (mutation v2) |
| `nexus_get_blocked_tags` / `nexus_block_tag` / `nexus_unblock_tag` | Quản lý blocked tags (mutation v2) |
| `nexus_get_user_by_name` | Profile user theo username chính xác (nhẹ hơn `nexus_get_user_v2`) |
| `nexus_get_user_monthly_report` | Báo cáo hoạt động tháng cụ thể (⚠️ API có thể ẩn dữ liệu theo permissions) |
| `nexus_get_speedtest_urls` | Danh sách speedtest/CDN endpoint |

### Batch cuối — v2 reads + mutations collection/moderation (134 tools tổng)

| Tool | Mô tả |
|---|---|
| `nexus_get_age_verification_info` / `nexus_start_age_verification_flow` / `nexus_start_age_verification_appeal_flow` | Xem trạng thái xác minh tuổi + bắt đầu flow xác minh/kháng nghị (mutation) |
| `nexus_get_api_applications` | Các OAuth applications đã đăng ký |
| `nexus_get_category_by_id` | Collection category theo ID |
| `nexus_get_collection_games` | Games có collection |
| `nexus_get_current_warnings` | Warnings/moderation hiện tại của viewer |
| `nexus_get_external_video` | Video external theo ID |
| `nexus_get_file_hash` / `nexus_get_file_hashes` | MD5 hash file mod (1 file hoặc nhiều `gameId:fileId`) |
| `nexus_get_game_artwork` | Artwork của game |
| `nexus_get_legacy_mods` | Dữ liệu mod legacy theo `"gameId:modId"` |
| `nexus_get_tags_v2` / `nexus_get_tag_by_id` / `nexus_get_tag_categories` / `nexus_get_tag_category_by_id` | Tag taxonomy v2 (theo game / ID / categories) |
| `nexus_search_media` | Tìm media toàn site (ảnh/supporter/video) + sort random có seed (⚠️ endpoint Nexus thỉnh thoảng lỗi server-side "A name each was not found" — retry là được; filter `adultContent` của Nexus hỏng nên đã bỏ param) |
| `nexus_get_opted_in_mods` | Mods opted-in Donation Points của 1 account |
| `nexus_get_preferences` / `nexus_update_preferences` | Xem/cập nhật preferences (emails, auto-update, allow indexing...) |
| `nexus_get_private_message_url` | URL deep-link vào PM Nexus |
| `nexus_get_transactions` | Lịch sử DP transactions (⚠️ Nexus ẩn data với API key — cần OAuth) |
| `nexus_get_uploads` | Upload activity (mods/collections/media) theo khoảng thời gian |
| `nexus_get_user_donation_preferences` / `nexus_update_user_donation_preferences` | Xem/cập nhật donation prefs (DP, tip jar...) |
| `nexus_get_user_monthly_report_by_id` | Monthly report theo accountId |
| `nexus_request_media_upload_url` / `nexus_get_collection_revision_upload_url` | Lấy upload URL (media / revision zip) |
| `nexus_update_about_me` / `nexus_update_country` | Cập nhật profile |
| `nexus_create_message` | Gửi tin nhắn tới user (mutation) |
| `nexus_close_collection_bug_report` | Đóng bug report của collection |
| `nexus_submit_moderation_fix` | Nộp fix cho mod bị moderation |
| `nexus_create_collection` / `nexus_edit_collection` | Tạo/sửa collection (manifest: author, summary, domainName, description BBCode...) |
| `nexus_create_or_update_revision` / `nexus_update_revision` | Tạo/cập nhật revision (manifest + mod list) |
| `nexus_publish_revision` / `nexus_retract_revision` / `nexus_discard_revision` | Publish / rút / xóa revision |
| `nexus_discard_collection` / `nexus_list_collection` / `nexus_unlist_collection` | Quản lý vòng đời collection |
| `nexus_create_changelog` / `nexus_update_changelog` | Changelog revision |
| `nexus_create_tag` / `nexus_update_tag` / `nexus_discard_tag` | Quản lý tag của collection |
| `nexus_add_badge_to_collection` / `nexus_remove_badge_from_collection` | Badge cho collection (owner) |
| `nexus_reorder_item` | Đổi thứ tự mod trong collection |
| `nexus_hide_comment` / `nexus_lock_comment` / `nexus_lock_comment_thread` / `nexus_pin_comment` / `nexus_unpin_comment` / `nexus_reorder_pinned_comments` / `nexus_clear_comment_moderation_status` / `nexus_clear_comment_thread_moderation_status` | Moderation comments/threads (moderator/owner — chưa live test destructive) |
| `nexus_track_app_metric` | Báo metric cho app (downloads...) |
| `nexus_block_mods_from_earning_dp` / `nexus_unblock_mods_from_earning_dp` | Chặn/bỏ chặn mods kiếm DP |
| `nexus_upload_attachment` | Upload attachment (multipart) cho message |

## OAuth (tuỳ chọn)

Một số mutation user-context (vd `nexus_update_mod_direct_download`) bị từ chối với API key — cần **OAuth Bearer token**. Server hỗ trợ OAuth2 + PKCE (S256) song song với API key: ưu tiên Bearer nếu còn hạn, tự refresh, fallback về API key khi hết/revoked.

**Đăng ký OAuth app**: Nexus **không có trang web** để đăng ký — phải email `support@nexusmods.com` (kèm tên app, mô tả, logo, link source, callback URI). Sau đó set env vars:

```json
"environment": {
  "NEXUS_API_KEY": "<key>",
  "NEXUS_OAUTH_CLIENT_ID": "<client-id-nhận-từ-support>",
  "NEXUS_OAUTH_CLIENT_SECRET": "<nếu có - app private>",
  "NEXUS_OAUTH_REDIRECT_URI": "http://localhost/callback",
  "NEXUS_OAUTH_TOKEN_FILE": "~/.nexus-mcp/oauth-tokens.json"
}
```

**Flow 2 bước** (stdio an toàn, browser tự mở từ authorize URL):

1. `nexus_oauth_login` → trả `authorize_url` (kèm state + PKCE challenge), user mở URL, đăng nhập Nexus, nhận `code`
2. `nexus_oauth_exchange(code)` → đổi code lấy token, lưu vào `NEXUS_OAUTH_TOKEN_FILE`, validate danh tính qua `validate.json`

Tools đi kèm: `nexus_oauth_status` (đã login? token hết hạn?), `nexus_oauth_refresh` (refresh tay), `nexus_oauth_logout` (xóa token).

- Token ~6h TTL, tự refresh trước 60s; refresh 4xx (user revoke) → tự xóa token, fallback API key.
- Scope chỉ cần `public`; Bearer được chấp nhận trên cả v1 REST và v2 GraphQL.

## Lưu ý

- Mỗi response v1 có kèm `_rl` — snapshot rate limit (`X-RL-*` headers) của Nexus.
- `domain_name` là slug lowercase (vd `forzahorizon6`), **không phải** tên hiển thị.
- Link download ngắn hạn, không cache.
- **Bảo mật**: không commit API key; nếu key từng bị lộ trong chat/log thì rotate tại trang API access.

### Tiết kiệm quota

- **Cache TTL phía client** cho GET v1: games 1h, mod/file data 5 phút (Nexus tự cache 5 phút phía server), state cá nhân (`/user`) không bao giờ cache. Gọi lặp lại cùng tham số trong session không tốn quota. GraphQL POST cũng cache 60s.
- **v2 GraphQL** (`api.nexusmods.com/v2/graphql`) có pool rate-limit riêng, không trừ quota v1 (2000/giờ, 20000/ngày). Ưu tiên dùng tool v2 cho search/dữ liệu công khai.
- Scrape website nexusmods.com (tab posts/stats) bị Cloudflare chặn với httpx thường; chỉ qua được với `curl_cffi` (impersonate browser). Comment trên site render client-side nên không extract được — dùng `nexus_get_comment_thread` thay thế.
- Endpoint v1 `/v1/games/{domain}/categories.json` đã bị Nexus gỡ (404 trên mọi game) — categories chỉ còn qua v2.
- Mutation user-preferences (ignore/unignore user, block/unblock tag) có **eventual consistency**: mutation trả `success: true` ngay nhưng list đọc ngay sau đó có thể còn stale vài giây.
- Mutation sở hữu (vd `updateModDirectDownloadEnabled`) từ chối với API key dù đúng chủ mod ("not allowed") — cần OAuth user-context, xem mục [OAuth](#oauth-tuỳ-chọn) (`nexus_update_mod_direct_download` sẽ hoạt động sau khi login).
- Một số endpoint GraphQL cần OAuth (scopes mà server không có) sẽ trả lỗi sạch; hiện chỉ `nexus_search_comments` bị lỗi 500 từ phía server của Nexus.
