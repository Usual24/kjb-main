from functools import wraps
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import os
import uuid
import re
from markupsafe import Markup, escape
from werkzeug.utils import secure_filename
from flask import session, redirect, url_for, g, current_app
from .models import User, ChannelPermission


EMOJI_PATTERN = re.compile(r":([a-zA-Z0-9_\-]+):")
CODE_PATTERN = re.compile(r"`([^`\n]+)`")
BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")
ITALIC_PATTERN = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def init_session(app):
    app.permanent_session_lifetime = timedelta(days=30)


def get_current_user():
    if hasattr(g, "current_user"):
        return g.current_user
    user_id = session.get("user_id")
    if not user_id:
        g.current_user = None
        return None
    g.current_user = User.query.get(user_id)
    return g.current_user


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not get_current_user():
            return redirect(url_for("views.signin"))
        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user or not user.is_admin:
            return redirect(url_for("views.index"))
        return view(*args, **kwargs)

    return wrapper


def set_login(user, remember=False):
    session["user_id"] = user.id
    session.permanent = bool(remember)


def logout_user():
    session.clear()


def notify(user_id, title, body, db, Notification):
    notification = Notification(user_id=user_id, title=title, body=body)
    db.session.add(notification)


def adjust_kc(user, delta, reason, db, KCLog, Notification):
    user.kc_points += delta
    db.session.add(KCLog(user_id=user.id, delta=delta, reason=reason))
    notify(user.id, "KC 변동", f"{reason} ({delta:+d} KC)", db, Notification)


_KST_TZ = None


def _get_kst_tz():
    global _KST_TZ
    if _KST_TZ is None:
        try:
            _KST_TZ = ZoneInfo("Asia/Seoul")
        except ZoneInfoNotFoundError:
            _KST_TZ = timezone(timedelta(hours=9), name="KST")
    return _KST_TZ


def to_kst(value):
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_get_kst_tz())


def allowed_file(filename, allowed_extensions):
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in allowed_extensions


def save_upload(file_storage, upload_folder, allowed_extensions):
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    if not allowed_file(filename, allowed_extensions):
        return None
    ext = filename.rsplit(".", 1)[1].lower()
    new_name = f"{uuid.uuid4().hex}.{ext}"
    os.makedirs(upload_folder, exist_ok=True)
    file_storage.save(os.path.join(upload_folder, new_name))
    return new_name


def media_url(value):
    if not value:
        return ""
    if value.startswith(("http://", "https://", "/")):
        return value
    return f"/media/{value}"


def resolve_channel_permissions(user, channel):
    if not user:
        return {"can_view": False, "can_read": False, "can_send": False}
    if user.is_admin:
        return {"can_view": True, "can_read": True, "can_send": True}
    permission_cache = getattr(g, "channel_permission_cache", None)
    if permission_cache is None or permission_cache.get("user_id") != user.id:
        rows = ChannelPermission.query.filter_by(user_id=user.id).all()
        permission_cache = {
            "user_id": user.id,
            "overrides": {row.channel_id: row for row in rows},
        }
        g.channel_permission_cache = permission_cache

    override = permission_cache["overrides"].get(channel.id)
    if override:
        permissions = {
            "can_view": override.can_view,
            "can_read": override.can_read,
            "can_send": override.can_send,
        }
    else:
        permissions = {
            "can_view": channel.default_can_view,
            "can_read": channel.default_can_read,
            "can_send": channel.default_can_send,
        }
    if not permissions["can_view"]:
        permissions["can_read"] = False
        permissions["can_send"] = False
    return permissions



def get_visible_channels(user, channels=None):
    if not user:
        return []
    channels = channels or []
    if not channels:
        from .models import Channel

        channels = Channel.query.order_by(Channel.priority.desc(), Channel.name.asc()).all()
    return [channel for channel in channels if resolve_channel_permissions(user, channel)["can_view"]]

def parse_int(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def render_chat_content(content, emoji_map):
    if not content:
        return ""
    parts = []
    last_end = 0
    for match in EMOJI_PATTERN.finditer(content):
        parts.append(_render_markdown_segment(content[last_end : match.start()]))
        key = match.group(1)
        emoji_url = emoji_map.get(key)
        if emoji_url:
            parts.append(
                Markup(
                    f'<img class="inline-emoji" src="{escape(media_url(emoji_url))}" alt=":{escape(key)}:" title=":{escape(key)}:">'
                )
            )
        else:
            parts.append(_render_markdown_segment(match.group(0)))
        last_end = match.end()
    parts.append(_render_markdown_segment(content[last_end:]))
    return Markup("".join(str(part) for part in parts))


def _render_markdown_segment(segment):
    if not segment:
        return ""
    text = escape(segment)
    text = CODE_PATTERN.sub(r"<code>\1</code>", str(text))
    text = BOLD_PATTERN.sub(r"<strong>\1</strong>", text)
    text = ITALIC_PATTERN.sub(r"<em>\1</em>", text)
    text = LINK_PATTERN.sub(
        r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>', text
    )
    text = text.replace("\n", "<br>")
    return Markup(text)
