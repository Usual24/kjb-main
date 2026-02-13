from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from .extensions import db


class Follow(db.Model):
    __tablename__ = "follows"
    follower_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    followed_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    email_prefix = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    kc_points = db.Column(db.Integer, default=0)
    bio = db.Column(db.String(280), default="")
    avatar_url = db.Column(db.String(255), default="/static/images/default-avatar.svg")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    emoji_permissions = db.relationship(
        "UserEmojiPermission", back_populates="user", cascade="all, delete-orphan"
    )
    accessory_permissions = db.relationship(
        "UserAccessoryPermission",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    followers = db.relationship(
        "User",
        secondary="follows",
        primaryjoin=id == Follow.followed_id,
        secondaryjoin=id == Follow.follower_id,
        backref="following",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Channel(db.Model):
    __tablename__ = "channels"
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(80), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255), default="")
    priority = db.Column(db.Integer, default=0)
    default_can_view = db.Column(db.Boolean, default=True)
    default_can_read = db.Column(db.Boolean, default=True)
    default_can_send = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ChannelPermission(db.Model):
    __tablename__ = "channel_permissions"
    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.Integer, db.ForeignKey("channels.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    can_view = db.Column(db.Boolean, default=True)
    can_read = db.Column(db.Boolean, default=True)
    can_send = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    channel = db.relationship("Channel")
    user = db.relationship("User")


class Message(db.Model):
    __tablename__ = "messages"
    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.Integer, db.ForeignKey("channels.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    reply_to_id = db.Column(db.Integer, db.ForeignKey("messages.id"))
    is_deleted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=True, onupdate=datetime.utcnow)

    user = db.relationship("User", backref="messages")
    reply_to = db.relationship("Message", remote_side=[id])


class UserChannelRead(db.Model):
    __tablename__ = "user_channel_reads"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    channel_id = db.Column(db.Integer, db.ForeignKey("channels.id"), nullable=False)
    last_read_message_id = db.Column(db.Integer, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "channel_id", name="uq_user_channel_read"),
    )


class Emoji(db.Model):
    __tablename__ = "emojis"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    image_url = db.Column(db.String(255), nullable=False)
    is_public = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    permissions = db.relationship(
        "UserEmojiPermission", back_populates="emoji", cascade="all, delete-orphan"
    )


class UserEmojiPermission(db.Model):
    __tablename__ = "user_emoji_permissions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    emoji_id = db.Column(db.Integer, db.ForeignKey("emojis.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("user_id", "emoji_id", name="uq_user_emoji"),)

    user = db.relationship("User", back_populates="emoji_permissions")
    emoji = db.relationship("Emoji", back_populates="permissions")


class Accessory(db.Model):
    __tablename__ = "accessories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    image_url = db.Column(db.String(255), nullable=False)
    text_color = db.Column(db.String(20), default="#f7f9ff")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    permissions = db.relationship(
        "UserAccessoryPermission",
        back_populates="accessory",
        cascade="all, delete-orphan",
    )


class UserAccessoryPermission(db.Model):
    __tablename__ = "user_accessory_permissions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    accessory_id = db.Column(db.Integer, db.ForeignKey("accessories.id"), nullable=False)
    is_active = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "accessory_id", name="uq_user_accessory"),
    )

    user = db.relationship("User", back_populates="accessory_permissions")
    accessory = db.relationship("Accessory", back_populates="permissions")


class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    body = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)


class KCLog(db.Model):
    __tablename__ = "kc_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    delta = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ShopItem(db.Model):
    __tablename__ = "shop_items"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255), default="")
    kc_cost = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer)
    priority = db.Column(db.Integer, default=0)
    image_url = db.Column(db.String(255), default="/static/images/shop-default.svg")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ShopRequest(db.Model):
    __tablename__ = "shop_requests"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("shop_items.id"), nullable=False)
    status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    processed_at = db.Column(db.DateTime)

    user = db.relationship("User")
    item = db.relationship("ShopItem")
