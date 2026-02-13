from datetime import datetime
from sqlalchemy.orm import selectinload
from flask import (
    Blueprint,
    abort,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    current_app,
    send_from_directory,
)
from ..extensions import db
from ..models import (
    User,
    Channel,
    ChannelPermission,
    Message,
    Notification,
    KCLog,
    ShopItem,
    ShopRequest,
    Follow,
    Emoji,
    UserEmojiPermission,
    Accessory,
    UserAccessoryPermission,
    UserChannelRead,
)
from ..utils import (
    login_required,
    admin_required,
    set_login,
    logout_user,
    get_current_user,
    notify,
    adjust_kc,
    to_kst,
    save_upload,
    resolve_channel_permissions,
    parse_int,
    get_visible_channels,
)
from ..sockets import online_users
from ..sockets import serialize_messages

bp = Blueprint("views", __name__)

def _compute_unread_channel_ids(user, channels):
    if not user:
        return set()
    channel_ids = [channel.id for channel in channels]
    if not channel_ids:
        return set()
    latest_rows = (
        db.session.query(Message.channel_id, db.func.max(Message.id))
        .filter(Message.channel_id.in_(channel_ids), Message.is_deleted.is_(False))
        .group_by(Message.channel_id)
        .all()
    )
    latest_map = {channel_id: max_id for channel_id, max_id in latest_rows if max_id}
    read_rows = UserChannelRead.query.filter(
        UserChannelRead.user_id == user.id,
        UserChannelRead.channel_id.in_(channel_ids),
    ).all()
    read_map = {row.channel_id: row.last_read_message_id for row in read_rows}
    return {
        channel_id
        for channel_id, max_id in latest_map.items()
        if (read_map.get(channel_id) or 0) < max_id
    }


def _mark_channel_read(user, channel_id, message_id):
    if not user or not channel_id or not message_id:
        return
    state = UserChannelRead.query.filter_by(user_id=user.id, channel_id=channel_id).first()
    if not state:
        state = UserChannelRead(
            user_id=user.id, channel_id=channel_id, last_read_message_id=message_id
        )
        db.session.add(state)
    elif state.last_read_message_id < message_id:
        state.last_read_message_id = message_id


@bp.before_app_request
def load_user():
    get_current_user()


@bp.route("/")
def index():
    if get_current_user():
        current = get_current_user()
        visible_channels = get_visible_channels(current)
        if visible_channels:
            return redirect(url_for("views.chat", id=visible_channels[0].slug))
    return render_template("index.html")


@bp.route("/signin", methods=["GET", "POST"])
def signin():
    if request.method == "POST":
        email = request.form.get("email", "").lower().strip()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"
        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            flash("이메일 또는 비밀번호가 올바르지 않습니다.")
            return redirect(url_for("views.signin"))
        set_login(user, remember)
        return redirect(url_for("views.chat"))
    return render_template("signin.html")


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form.get("email", "").lower().strip()
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        if not all([email, name, username, password]):
            flash("모든 필드를 입력해주세요.")
            return redirect(url_for("views.signup"))
        if password != password_confirm:
            flash("비밀번호가 일치하지 않습니다.")
            return redirect(url_for("views.signup"))
        email_prefix = email.split("@")[0]
        if User.query.filter(
            (User.email == email)
            | (User.username == username)
            | (User.email_prefix == email_prefix)
        ).first():
            flash("이미 등록된 계정 정보입니다.")
            return redirect(url_for("views.signup"))
        is_first = User.query.count() == 0
        user = User(
            email=email,
            email_prefix=email_prefix,
            name=name,
            username=username,
            is_admin=is_first,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        set_login(user, True)
        return redirect(url_for("views.chat"))
    return render_template("signup.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("views.index"))


@bp.route("/chat")
@login_required
def chat():
    channel_slug = request.args.get("id")
    current = get_current_user()
    if not channel_slug:
        first_channel = None
        visible_channels = get_visible_channels(current)
        if visible_channels:
            first_channel = visible_channels[0]
        if first_channel:
            return redirect(url_for("views.chat", id=first_channel.slug))
    channel = Channel.query.filter_by(slug=channel_slug).first()
    if not channel:
        flash("채널을 찾을 수 없습니다.")
        return redirect(url_for("views.index"))
    permissions = resolve_channel_permissions(current, channel)
    if not permissions["can_view"]:
        allowed = get_visible_channels(current)
        if allowed:
            return redirect(url_for("views.chat", id=allowed[0].slug))
        flash("접근 가능한 채널이 없습니다.")
        return redirect(url_for("views.index"))
    messages = []
    serialized_messages = []
    if permissions["can_read"]:
        messages = (
            Message.query.filter_by(channel_id=channel.id)
            .options(
                selectinload(Message.user).selectinload(User.emoji_permissions).selectinload(UserEmojiPermission.emoji),
                selectinload(Message.reply_to),
            )
            .order_by(Message.created_at.desc())
            .limit(200)
            .all()
        )
        messages.reverse()
        serialized_messages = serialize_messages(messages)
        if messages:
            _mark_channel_read(current, channel.id, messages[-1].id)
            db.session.commit()
    visible_channels = get_visible_channels(current)
    unread_channel_ids = _compute_unread_channel_ids(current, visible_channels)
    return render_template(
        "chat.html",
        channel=channel,
        messages=serialized_messages,
        can_send=permissions["can_send"],
        can_read=permissions["can_read"],
        unread_channel_ids=unread_channel_ids,
    )


@bp.route("/chat/read", methods=["POST"])
@login_required
def mark_chat_read():
    current = get_current_user()
    channel_slug = request.form.get("channel", "")
    message_id = parse_int(request.form.get("message_id"))
    if not channel_slug or not message_id:
        return ("", 204)
    channel = Channel.query.filter_by(slug=channel_slug).first()
    if not channel:
        return ("", 204)
    if not resolve_channel_permissions(current, channel)["can_read"]:
        return ("", 204)
    _mark_channel_read(current, channel.id, message_id)
    db.session.commit()
    return ("", 204)


@bp.route("/profile")
@login_required
def profile():
    prefix = request.args.get("usr", "")
    user = User.query.filter_by(email_prefix=prefix).first()
    if not user:
        flash("사용자를 찾을 수 없습니다.")
        return redirect(url_for("views.index"))
    current = get_current_user()
    is_following = False
    if current:
        is_following = (
            Follow.query.filter_by(follower_id=current.id, followed_id=user.id).first()
            is not None
        )
    follower_count = Follow.query.filter_by(followed_id=user.id).count()
    following_count = Follow.query.filter_by(follower_id=user.id).count()
    return render_template(
        "profile.html",
        profile_user=user,
        is_following=is_following,
        follower_count=follower_count,
        following_count=following_count,
    )


@bp.route("/follow/<prefix>", methods=["POST"])
@login_required
def follow(prefix):
    target = User.query.filter_by(email_prefix=prefix).first_or_404()
    current = get_current_user()
    if current.id == target.id:
        return redirect(url_for("views.profile", usr=prefix))
    existing = Follow.query.filter_by(
        follower_id=current.id, followed_id=target.id
    ).first()
    if existing:
        db.session.delete(existing)
        adjust_kc(target, -50, "팔로워 감소", db, KCLog, Notification)
        notify(target.id, "팔로우", f"{current.name}님이 언팔로우했습니다.", db, Notification)
    else:
        db.session.add(Follow(follower_id=current.id, followed_id=target.id))
        adjust_kc(target, 50, "팔로워 증가", db, KCLog, Notification)
        notify(target.id, "팔로우", f"{current.name}님이 팔로우했습니다.", db, Notification)
    db.session.commit()
    return redirect(url_for("views.profile", usr=prefix))


@bp.route("/mypage", methods=["GET", "POST"])
@login_required
def mypage():
    current = get_current_user()
    if request.method == "POST":
        current.name = request.form.get("name", current.name).strip()
        current.bio = request.form.get("bio", current.bio).strip()
        avatar_file = request.files.get("avatar_file")
        if avatar_file and avatar_file.filename:
            upload_name = save_upload(
                avatar_file,
                current_app.config["UPLOAD_FOLDER"],
                current_app.config["ALLOWED_EXTENSIONS"],
            )
            if not upload_name:
                flash("지원하지 않는 파일 형식입니다.")
                return redirect(url_for("views.mypage"))
            current.avatar_url = upload_name
        db.session.commit()
        flash("프로필이 업데이트되었습니다.")
        return redirect(url_for("views.mypage"))
    return render_template("mypage.html", profile_user=current)


@bp.route("/shop", methods=["GET", "POST"])
@login_required
def shop():
    current = get_current_user()
    items = ShopItem.query.order_by(ShopItem.priority.desc(), ShopItem.name.asc()).all()
    if request.method == "POST":
        item_id = request.form.get("item_id")
        item = ShopItem.query.get(item_id)
        if not item:
            flash("상품을 찾을 수 없습니다.")
            return redirect(url_for("views.shop"))
        if item.quantity is not None and item.quantity <= 0:
            flash("품절된 상품입니다.")
            return redirect(url_for("views.shop"))
        request_entry = ShopRequest(user_id=current.id, item_id=item.id)
        db.session.add(request_entry)
        notify(current.id, "상점", f"{item.name} 구매 요청을 접수했습니다.", db, Notification)
        db.session.commit()
        flash("구매 요청이 접수되었습니다.")
        return redirect(url_for("views.shop"))
    return render_template("shop.html", items=items)


@bp.route("/sendkc", methods=["GET", "POST"])
@login_required
def sendkc():
    current = get_current_user()
    if request.method == "POST":
        recipient_prefix = request.form.get("recipient", "").strip()
        amount = parse_int(request.form.get("amount"))
        if amount is None:
            flash("올바른 KC를 입력해주세요.")
            return redirect(url_for("views.sendkc"))
        if amount <= 0:
            flash("올바른 KC를 입력해주세요.")
            return redirect(url_for("views.sendkc"))
        if current.kc_points < amount:
            flash("KC가 부족합니다.")
            return redirect(url_for("views.sendkc"))
        recipient = User.query.filter_by(email_prefix=recipient_prefix).first()
        if not recipient:
            notify(current.id, "송금", "수신자를 찾지 못해 송금이 취소되었습니다.", db, Notification)
            flash("수신자를 찾을 수 없습니다. 송금이 취소됩니다.")
            db.session.commit()
            return redirect(url_for("views.sendkc"))
        adjust_kc(current, -amount, "KC 송금", db, KCLog, Notification)
        adjust_kc(recipient, amount, "KC 수신", db, KCLog, Notification)
        notify(recipient.id, "송금", f"{current.name}님에게서 {amount} KC를 받았습니다.", db, Notification)
        db.session.commit()
        flash("송금이 완료되었습니다.")
        return redirect(url_for("views.sendkc"))
    return render_template("sendkc.html")


@bp.route("/mailbox")
@login_required
def mailbox():
    current = get_current_user()
    notifications = (
        Notification.query.filter_by(user_id=current.id)
        .order_by(Notification.created_at.desc())
        .all()
    )
    return render_template("mailbox.html", notifications=notifications)


@bp.route("/mailbox/clear", methods=["POST"])
@login_required
def clear_mailbox():
    current = get_current_user()
    Notification.query.filter_by(user_id=current.id).delete()
    db.session.commit()
    flash("알림이 모두 삭제되었습니다.")
    return redirect(url_for("views.mailbox"))


@bp.route("/media/<path:filename>")
def media(filename):
    import flask

    upload_folder = flask.current_app.config.get("UPLOAD_FOLDER")
    if not upload_folder:
        abort(404)
    return send_from_directory(upload_folder, filename)


@bp.route("/admin", methods=["GET", "POST"])
@admin_required
def admin():
    current = get_current_user()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "kc_adjust":
            target_prefix = request.form.get("target")
            delta = parse_int(request.form.get("delta"))
            if delta is None:
                flash("올바른 KC 값을 입력해주세요.")
                return redirect(url_for("views.admin"))
            target = User.query.filter_by(email_prefix=target_prefix).first()
            if target and delta != 0:
                adjust_kc(target, delta, "관리자 조정", db, KCLog, Notification)
                db.session.commit()
                flash("KC가 조정되었습니다.")
        elif action == "shop_decision":
            request_id = request.form.get("request_id")
            decision = request.form.get("decision")
            shop_request = ShopRequest.query.get(request_id)
            if shop_request and shop_request.status == "pending":
                if decision == "approve":
                    item = shop_request.item
                    if shop_request.user.kc_points >= item.kc_cost:
                        adjust_kc(shop_request.user, -item.kc_cost, "상점 구매", db, KCLog, Notification)
                        shop_request.status = "approved"
                        shop_request.processed_at = datetime.utcnow()
                        if item.quantity is not None:
                            item.quantity = max(0, item.quantity - 1)
                        notify(
                            shop_request.user.id,
                            "상점",
                            f"{item.name} 구매가 승인되었습니다.",
                            db,
                            Notification,
                        )
                    else:
                        shop_request.status = "denied"
                        notify(
                            shop_request.user.id,
                            "상점",
                            f"KC 부족으로 {item.name} 구매가 거절되었습니다.",
                            db,
                            Notification,
                        )
                else:
                    shop_request.status = "denied"
                    notify(
                        shop_request.user.id,
                        "상점",
                        f"{shop_request.item.name} 구매가 거절되었습니다.",
                        db,
                        Notification,
                    )
                db.session.commit()
        elif action == "channel_create":
            slug = request.form.get("slug", "").strip()
            name = request.form.get("name", "").strip()
            description = request.form.get("description", "").strip()
            priority = parse_int(request.form.get("priority")) or 0
            default_can_view = request.form.get("default_can_view") == "on"
            default_can_read = request.form.get("default_can_read") == "on"
            default_can_send = request.form.get("default_can_send") == "on"
            if slug and name and not Channel.query.filter_by(slug=slug).first():
                db.session.add(
                    Channel(
                        slug=slug,
                        name=name,
                        description=description,
                        priority=priority,
                        default_can_view=default_can_view,
                        default_can_read=default_can_read,
                        default_can_send=default_can_send,
                    )
                )
                db.session.commit()
        elif action == "channel_update":
            channel_id = request.form.get("channel_id")
            channel = Channel.query.get(channel_id)
            if channel:
                new_slug = request.form.get("slug", channel.slug).strip()
                if new_slug and new_slug != channel.slug:
                    if Channel.query.filter_by(slug=new_slug).first():
                        flash("이미 사용 중인 채널 ID입니다.")
                        return redirect(url_for("views.admin"))
                    channel.slug = new_slug
                channel.name = request.form.get("name", channel.name).strip()
                channel.description = request.form.get(
                    "description", channel.description
                ).strip()
                channel.priority = parse_int(request.form.get("priority")) or channel.priority
                channel.default_can_view = (
                    request.form.get("default_can_view") == "on"
                )
                channel.default_can_read = (
                    request.form.get("default_can_read") == "on"
                )
                channel.default_can_send = (
                    request.form.get("default_can_send") == "on"
                )
                db.session.commit()
        elif action == "channel_delete":
            channel_id = request.form.get("channel_id")
            channel = Channel.query.get(channel_id)
            if channel:
                Message.query.filter_by(channel_id=channel.id).delete()
                UserChannelRead.query.filter_by(channel_id=channel.id).delete()
                ChannelPermission.query.filter_by(channel_id=channel.id).delete()
                db.session.delete(channel)
                db.session.commit()
        elif action == "shop_item_create":
            name = request.form.get("name", "").strip()
            kc_cost = parse_int(request.form.get("kc_cost"))
            if kc_cost is None:
                flash("올바른 KC 값을 입력해주세요.")
                return redirect(url_for("views.admin"))
            description = request.form.get("description", "").strip()
            image_file = request.files.get("image_file")
            upload_name = None
            if image_file and image_file.filename:
                upload_name = save_upload(
                    image_file,
                    current_app.config["UPLOAD_FOLDER"],
                    current_app.config["ALLOWED_EXTENSIONS"],
                )
                if not upload_name:
                    flash("지원하지 않는 이미지 형식입니다.")
                    return redirect(url_for("views.admin"))
            image_url = upload_name or "/static/images/shop-default.svg"
            quantity = request.form.get("quantity")
            priority = parse_int(request.form.get("priority")) or 0
            quantity_value = parse_int(quantity) if quantity else None
            if name and kc_cost > 0:
                db.session.add(
                    ShopItem(
                        name=name,
                        description=description,
                        kc_cost=kc_cost,
                        quantity=quantity_value,
                        priority=priority,
                        image_url=image_url,
                    )
                )
                db.session.commit()
        elif action == "shop_item_delete":
            item_id = request.form.get("item_id")
            item = ShopItem.query.get(item_id)
            if item:
                ShopRequest.query.filter_by(item_id=item.id).delete()
                db.session.delete(item)
                db.session.commit()
        elif action == "channel_permission_upsert":
            channel_id = request.form.get("channel_id")
            user_id = request.form.get("user_id")
            channel = Channel.query.get(channel_id)
            user = User.query.get(user_id)
            if channel and user:
                permission = ChannelPermission.query.filter_by(
                    channel_id=channel.id, user_id=user.id
                ).first()
                if not permission:
                    permission = ChannelPermission(
                        channel_id=channel.id,
                        user_id=user.id,
                    )
                    db.session.add(permission)
                permission.can_view = request.form.get("can_view") == "on"
                permission.can_read = request.form.get("can_read") == "on"
                permission.can_send = request.form.get("can_send") == "on"
                db.session.commit()
        elif action == "channel_permission_delete":
            perm_id = request.form.get("permission_id")
            permission = ChannelPermission.query.get(perm_id)
            if permission:
                db.session.delete(permission)
                db.session.commit()
        elif action == "user_delete":
            prefix = request.form.get("target")
            target = User.query.filter_by(email_prefix=prefix).first()
            if target and target.id != current.id:
                Message.query.filter_by(user_id=target.id).delete()
                UserChannelRead.query.filter_by(user_id=target.id).delete()
                ShopRequest.query.filter_by(user_id=target.id).delete()
                Follow.query.filter_by(follower_id=target.id).delete()
                Follow.query.filter_by(followed_id=target.id).delete()
                ChannelPermission.query.filter_by(user_id=target.id).delete()
                UserEmojiPermission.query.filter_by(user_id=target.id).delete()
                UserAccessoryPermission.query.filter_by(user_id=target.id).delete()
                Notification.query.filter_by(user_id=target.id).delete()
                KCLog.query.filter_by(user_id=target.id).delete()
                db.session.delete(target)
                db.session.commit()
        elif action == "emoji_create":
            name = request.form.get("name", "").strip().lower()
            image_file = request.files.get("image_file")
            if not name:
                flash("이모지 이름을 입력해주세요.")
                return redirect(url_for("views.admin"))
            if Emoji.query.filter_by(name=name).first():
                flash("이미 존재하는 이모지 이름입니다.")
                return redirect(url_for("views.admin"))
            if not image_file or not image_file.filename:
                flash("이모지 이미지를 업로드해주세요.")
                return redirect(url_for("views.admin"))
            upload_name = save_upload(
                image_file,
                current_app.config["UPLOAD_FOLDER"],
                current_app.config["ALLOWED_EXTENSIONS"],
            )
            if not upload_name:
                flash("지원하지 않는 이미지 형식입니다.")
                return redirect(url_for("views.admin"))
            is_public = request.form.get("is_public") == "on"
            db.session.add(Emoji(name=name, image_url=upload_name, is_public=is_public))
            db.session.commit()
        elif action == "emoji_delete":
            emoji_id = request.form.get("emoji_id")
            emoji = Emoji.query.get(emoji_id)
            if emoji:
                db.session.delete(emoji)
                db.session.commit()
        elif action == "emoji_toggle_public":
            emoji_id = request.form.get("emoji_id")
            emoji = Emoji.query.get(emoji_id)
            if emoji:
                emoji.is_public = not emoji.is_public
                db.session.commit()
        elif action == "emoji_permission_upsert":
            user_id = request.form.get("user_id")
            emoji_id = request.form.get("emoji_id")
            user = User.query.get(user_id)
            emoji = Emoji.query.get(emoji_id)
            if user and emoji:
                existing = UserEmojiPermission.query.filter_by(
                    user_id=user.id, emoji_id=emoji.id
                ).first()
                if not existing:
                    db.session.add(UserEmojiPermission(user_id=user.id, emoji_id=emoji.id))
                    db.session.commit()
        elif action == "emoji_permission_delete":
            permission_id = request.form.get("permission_id")
            permission = UserEmojiPermission.query.get(permission_id)
            if permission:
                db.session.delete(permission)
                db.session.commit()
        elif action == "accessory_create":
            name = request.form.get("name", "").strip()
            text_color = request.form.get("text_color", "#f7f9ff").strip() or "#f7f9ff"
            image_file = request.files.get("image_file")
            if not name:
                flash("엑세서리 이름을 입력해주세요.")
                return redirect(url_for("views.admin"))
            if Accessory.query.filter_by(name=name).first():
                flash("이미 존재하는 엑세서리 이름입니다.")
                return redirect(url_for("views.admin"))
            if not image_file or not image_file.filename:
                flash("엑세서리 이미지를 업로드해주세요.")
                return redirect(url_for("views.admin"))
            upload_name = save_upload(
                image_file,
                current_app.config["UPLOAD_FOLDER"],
                current_app.config["ALLOWED_EXTENSIONS"],
            )
            if not upload_name:
                flash("지원하지 않는 이미지 형식입니다.")
                return redirect(url_for("views.admin"))
            db.session.add(Accessory(name=name, image_url=upload_name, text_color=text_color))
            db.session.commit()
        elif action == "accessory_delete":
            accessory_id = request.form.get("accessory_id")
            accessory = Accessory.query.get(accessory_id)
            if accessory:
                db.session.delete(accessory)
                db.session.commit()
        elif action == "accessory_permission_upsert":
            user_id = request.form.get("user_id")
            accessory_id = request.form.get("accessory_id")
            set_active = request.form.get("set_active") == "on"
            user = User.query.get(user_id)
            accessory = Accessory.query.get(accessory_id)
            if user and accessory:
                permission = UserAccessoryPermission.query.filter_by(
                    user_id=user.id, accessory_id=accessory.id
                ).first()
                if not permission:
                    permission = UserAccessoryPermission(
                        user_id=user.id,
                        accessory_id=accessory.id,
                    )
                    db.session.add(permission)
                if set_active:
                    UserAccessoryPermission.query.filter_by(user_id=user.id).update(
                        {"is_active": False}
                    )
                    permission.is_active = True
                db.session.commit()
        elif action == "accessory_permission_activate":
            permission_id = request.form.get("permission_id")
            permission = UserAccessoryPermission.query.get(permission_id)
            if permission:
                UserAccessoryPermission.query.filter_by(user_id=permission.user_id).update(
                    {"is_active": False}
                )
                permission.is_active = True
                db.session.commit()
        elif action == "accessory_permission_delete":
            permission_id = request.form.get("permission_id")
            permission = UserAccessoryPermission.query.get(permission_id)
            if permission:
                db.session.delete(permission)
                db.session.commit()
    stats = {
        "user_count": User.query.count(),
        "channel_count": Channel.query.count(),
        "online_count": len(online_users),
    }
    shop_requests = (
        ShopRequest.query.filter_by(status="pending")
        .order_by(ShopRequest.created_at.desc())
        .all()
    )
    items = ShopItem.query.order_by(ShopItem.priority.desc(), ShopItem.name.asc()).all()
    channels = Channel.query.order_by(Channel.priority.desc(), Channel.name.asc()).all()
    users = User.query.order_by(User.created_at.desc()).all()
    channel_permissions = ChannelPermission.query.order_by(
        ChannelPermission.created_at.desc()
    ).all()
    emojis = Emoji.query.order_by(Emoji.name.asc()).all()
    emoji_permissions = UserEmojiPermission.query.order_by(
        UserEmojiPermission.created_at.desc()
    ).all()
    accessories = Accessory.query.order_by(Accessory.created_at.desc()).all()
    accessory_permissions = UserAccessoryPermission.query.order_by(
        UserAccessoryPermission.created_at.desc()
    ).all()
    return render_template(
        "admin.html",
        stats=stats,
        shop_requests=shop_requests,
        items=items,
        channels=channels,
        channel_permissions=channel_permissions,
        emojis=emojis,
        emoji_permissions=emoji_permissions,
        accessories=accessories,
        accessory_permissions=accessory_permissions,
        users=users,
    )


@bp.app_template_filter("datetime")
def format_datetime(value):
    if not value:
        return ""
    return to_kst(value).strftime("%Y-%m-%d %H:%M")
