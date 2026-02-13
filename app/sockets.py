from datetime import datetime
from flask import session
from flask_socketio import join_room, leave_room, emit
from sqlalchemy.orm import selectinload
from .extensions import db
from .models import (
    Message,
    Channel,
    User,
    KCLog,
    Notification,
    Emoji,
    UserAccessoryPermission,
    UserChannelRead,
)
from .utils import (
    adjust_kc,
    to_kst,
    resolve_channel_permissions,
    media_url,
    render_chat_content,
)


online_users = set()
channel_typing_users = {}


def _build_emoji_map_for_user(user):
    emoji_map = {emoji.name: emoji.image_url for emoji in Emoji.query.filter_by(is_public=True).all()}
    if user:
        emoji_map.update(
            {
                permission.emoji.name: permission.emoji.image_url
                for permission in user.emoji_permissions
            }
        )
    return emoji_map


def _active_accessory_map(user_ids):
    if not user_ids:
        return {}
    rows = (
        UserAccessoryPermission.query.options(selectinload(UserAccessoryPermission.accessory))
        .filter(
            UserAccessoryPermission.user_id.in_(user_ids),
            UserAccessoryPermission.is_active.is_(True),
        )
        .order_by(
            UserAccessoryPermission.user_id.asc(),
            UserAccessoryPermission.created_at.desc(),
        )
        .all()
    )
    result = {}
    for row in rows:
        result.setdefault(row.user_id, row)
    return result


def _current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return User.query.get(user_id)




def _mark_channel_read(user_id, channel_id, message_id):
    if not user_id or not channel_id or not message_id:
        return
    state = UserChannelRead.query.filter_by(user_id=user_id, channel_id=channel_id).first()
    if not state:
        db.session.add(
            UserChannelRead(
                user_id=user_id, channel_id=channel_id, last_read_message_id=message_id
            )
        )
        return
    if state.last_read_message_id < message_id:
        state.last_read_message_id = message_id


def _emit_typing_update(channel_slug):
    user_ids = list(channel_typing_users.get(channel_slug, set()))
    users = User.query.filter(User.id.in_(user_ids)).all() if user_ids else []
    emit(
        "typing_update",
        {
            "channel": channel_slug,
            "users": [{"id": user.id, "name": user.name} for user in users],
        },
        room=channel_slug,
    )

def register_socket_handlers(socketio):
    @socketio.on("connect")
    def handle_connect():
        user = _current_user()
        if not user:
            return False
        online_users.add(user.id)
        emit("online_update", _online_payload(), broadcast=True)

    @socketio.on("disconnect")
    def handle_disconnect():
        user = _current_user()
        if user and user.id in online_users:
            online_users.discard(user.id)
            emit("online_update", _online_payload(), broadcast=True)
        if user:
            for channel_slug in list(channel_typing_users.keys()):
                typers = channel_typing_users.get(channel_slug, set())
                if user.id in typers:
                    typers.discard(user.id)
                    if not typers:
                        channel_typing_users.pop(channel_slug, None)
                    _emit_typing_update(channel_slug)

    @socketio.on("join")
    def handle_join(data):
        user = _current_user()
        if not user:
            return
        channel_slug = data.get("channel")
        if not channel_slug:
            return
        channel = Channel.query.filter_by(slug=channel_slug).first()
        if not channel:
            return
        if not resolve_channel_permissions(user, channel)["can_view"]:
            return
        join_room(channel_slug)

    @socketio.on("leave")
    def handle_leave(data):
        user = _current_user()
        channel_slug = data.get("channel")
        if not channel_slug:
            return
        leave_room(channel_slug)
        if user:
            typers = channel_typing_users.get(channel_slug, set())
            if user.id in typers:
                typers.discard(user.id)
                if not typers:
                    channel_typing_users.pop(channel_slug, None)
                _emit_typing_update(channel_slug)

    @socketio.on("send_message")
    def handle_send_message(data):
        user = _current_user()
        if not user:
            return
        channel_slug = data.get("channel")
        content = (data.get("content") or "").strip()
        reply_to_id = data.get("reply_to")
        if not channel_slug or not content:
            return {"ok": False, "error": "메시지 내용을 입력해주세요."}
        channel = Channel.query.filter_by(slug=channel_slug).first()
        if not channel:
            return {"ok": False, "error": "채널을 찾을 수 없습니다."}
        if not resolve_channel_permissions(user, channel)["can_send"]:
            return {"ok": False, "error": "메시지 전송 권한이 없습니다."}
        message = Message(
            channel_id=channel.id,
            user_id=user.id,
            content=content,
            reply_to_id=reply_to_id,
        )
        db.session.add(message)
        adjust_kc(user, 1, "채팅 보상", db, KCLog, Notification)
        db.session.commit()
        _mark_channel_read(user.id, channel.id, message.id)
        db.session.commit()
        payload = serialize_message(message, emoji_map=_build_emoji_map_for_user(user))
        emit("new_message", payload, room=channel_slug)
        return {"ok": True, "message": payload}

    @socketio.on("typing")
    def handle_typing(data):
        user = _current_user()
        if not user:
            return
        channel_slug = data.get("channel")
        is_typing = bool(data.get("is_typing"))
        if not channel_slug:
            return
        channel = Channel.query.filter_by(slug=channel_slug).first()
        if not channel:
            return
        if not resolve_channel_permissions(user, channel)["can_view"]:
            return
        typers = channel_typing_users.setdefault(channel_slug, set())
        if is_typing:
            typers.add(user.id)
        else:
            typers.discard(user.id)
        if not typers:
            channel_typing_users.pop(channel_slug, None)
        _emit_typing_update(channel_slug)

    @socketio.on("edit_message")
    def handle_edit_message(data):
        user = _current_user()
        if not user:
            return
        message_id = data.get("message_id")
        content = (data.get("content") or "").strip()
        if not message_id or not content:
            return
        message = Message.query.get(message_id)
        if not message or message.is_deleted:
            return
        if message.user_id != user.id:
            return
        message.content = content
        message.updated_at = datetime.utcnow()
        db.session.commit()
        emit("message_updated", serialize_message(message), room=_channel_slug(message))

    @socketio.on("delete_message")
    def handle_delete_message(data):
        user = _current_user()
        if not user:
            return
        message_id = data.get("message_id")
        message = Message.query.get(message_id)
        if not message:
            return
        if message.user_id != user.id and not user.is_admin:
            return
        message.is_deleted = True
        message.content = "[삭제됨]"
        db.session.commit()
        emit("message_deleted", {"message_id": message.id}, room=_channel_slug(message))


def serialize_message(message, emoji_map=None, active_accessory=None):
    created_at = to_kst(message.created_at)
    updated_at = to_kst(message.updated_at) if message.updated_at else None
    if emoji_map is None:
        emoji_map = _build_emoji_map_for_user(message.user)
    if active_accessory is None:
        active_accessory = _active_accessory_map([message.user_id]).get(message.user_id)
    return {
        "id": message.id,
        "channel_id": message.channel_id,
        "user_id": message.user_id,
        "user_name": message.user.name,
        "user_prefix": message.user.email_prefix,
        "avatar": media_url(message.user.avatar_url),
        "content": message.content,
        "rendered_content": str(render_chat_content(message.content, emoji_map)),
        "reply_to": message.reply_to.content if message.reply_to else None,
        "is_deleted": message.is_deleted,
        "name_color": (
            active_accessory.accessory.text_color
            if active_accessory and active_accessory.accessory
            else None
        ),
        "accessory_image": (
            media_url(active_accessory.accessory.image_url)
            if active_accessory and active_accessory.accessory
            else None
        ),
        "created_at": created_at.strftime("%Y-%m-%d %H:%M"),
        "updated_at": updated_at.strftime("%Y-%m-%d %H:%M") if updated_at else None,
    }



def serialize_messages(messages):
    if not messages:
        return []
    user_ids = sorted({message.user_id for message in messages})
    accessory_map = _active_accessory_map(user_ids)
    emoji_map_cache = {}
    serialized = []
    for message in messages:
        emoji_map = emoji_map_cache.get(message.user_id)
        if emoji_map is None:
            emoji_map = _build_emoji_map_for_user(message.user)
            emoji_map_cache[message.user_id] = emoji_map
        serialized.append(
            serialize_message(
                message,
                emoji_map=emoji_map,
                active_accessory=accessory_map.get(message.user_id),
            )
        )
    return serialized

def _online_payload():
    users = User.query.filter(User.id.in_(online_users)).all() if online_users else []
    accessory_map = _active_accessory_map([user.id for user in users])
    payload = []
    for user in users:
        active_accessory = accessory_map.get(user.id)
        payload.append(
            {
                "id": user.id,
                "name": user.name,
                "email_prefix": user.email_prefix,
                "avatar": media_url(user.avatar_url),
                "name_color": (
                    active_accessory.accessory.text_color
                    if active_accessory and active_accessory.accessory
                    else None
                ),
                "accessory_image": (
                    media_url(active_accessory.accessory.image_url)
                    if active_accessory and active_accessory.accessory
                    else None
                ),
            }
        )
    return payload


def _channel_slug(message):
    channel = Channel.query.get(message.channel_id)
    return channel.slug if channel else "general"
