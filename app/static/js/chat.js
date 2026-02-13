const socket = io({ transports: ['websocket', 'polling'] });
const chatMain = document.querySelector('.chat-main');
const channel = chatMain.dataset.channel;
const channelId = parseInt(chatMain.dataset.channelId, 10);
const canSend = chatMain.dataset.canSend === 'true';
const messageList = document.getElementById('chatMessages');
const input = document.getElementById('chatInput');
const sendButton = document.getElementById('sendButton');
const contextMenu = document.getElementById('contextMenu');
const replyBanner = document.getElementById('replyBanner');
const typingIndicator = document.getElementById('typingIndicator');
const onlineLists = document.querySelectorAll('[data-online-list]');
let replyToId = null;
let contextMessageId = null;
let contextUserId = null;
let typing = false;
let typingTimer = null;
let lastReadMessageId = 0;
let readSyncTimer = null;
let sending = false;
const queuedMessages = [];

const channelItems = Array.from(document.querySelectorAll('[data-channel-slug][data-channel-id]'));
const joinedChannelSlugs = new Set(channelItems.map((item) => item.dataset.channelSlug).filter(Boolean));

joinedChannelSlugs.forEach((slug) => {
  socket.emit('join', { channel: slug });
});

function setUnreadDot(targetChannelId, isUnread) {
  if (!targetChannelId) return;
  const channelLinks = document.querySelectorAll(`a[data-channel-id="${targetChannelId}"]`);
  channelLinks.forEach((link) => {
    const existingDot = link.querySelector('.unread-dot');
    if (isUnread && !existingDot) {
      const dot = document.createElement('span');
      dot.className = 'unread-dot';
      dot.setAttribute('aria-label', '읽지 않음');
      link.appendChild(dot);
      return;
    }
    if (!isUnread && existingDot) {
      existingDot.remove();
    }
  });
}

function flushReadSync() {
  if (!lastReadMessageId) return;
  const body = new URLSearchParams({ channel, message_id: lastReadMessageId.toString() });
  fetch('/chat/read', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  })
    .then(() => setUnreadDot(channelId, false))
    .catch(() => {});
}

function markChannelRead(messageId) {
  if (!messageId || messageId <= lastReadMessageId) return;
  lastReadMessageId = messageId;
  if (readSyncTimer) clearTimeout(readSyncTimer);
  readSyncTimer = setTimeout(flushReadSync, 400);
}

function updateTypingState(nextState) {
  if (typing === nextState) return;
  typing = nextState;
  socket.emit('typing', { channel, is_typing: typing });
}

function renderMessage(message) {
  const wrapper = document.createElement('div');
  wrapper.className = 'message';
  wrapper.dataset.messageId = message.id;
  wrapper.dataset.userId = message.user_id;

  wrapper.innerHTML = `
    <a href="/profile?usr=${message.user_prefix}" class="avatar-link">
      <img src="${message.avatar}" alt="avatar">
    </a>
    <div class="message-body">
      <div class="message-meta">
        <a href="/profile?usr=${message.user_prefix}" ${message.name_color ? `style="color:${message.name_color};"` : ''}>${message.user_name}</a>
        ${message.accessory_image ? `<img src="${message.accessory_image}" class="name-accessory" alt="accessory">` : ''}
        <span>${message.created_at}</span>
        ${message.updated_at && message.updated_at !== message.created_at ? '<span class="edited">수정됨</span>' : ''}
      </div>
      ${message.reply_to ? `<div class="reply-preview">↳ ${message.reply_to}</div>` : ''}
      <div class="message-content">${message.rendered_content || message.content}</div>
    </div>
  `;
  return wrapper;
}

function appendMessage(message) {
  const element = renderMessage(message);
  messageList.appendChild(element);
  messageList.scrollTop = messageList.scrollHeight;
  setUnreadDot(channelId, false);
  markChannelRead(message.id);
}

function updateOnlineList(users) {
  onlineLists.forEach((list) => {
    list.innerHTML = '';
    users.forEach((user) => {
      const li = document.createElement('li');
      li.className = 'online-item';
      li.innerHTML = `
        <a href="/profile?usr=${user.email_prefix}">
          <img src="${user.avatar}" alt="avatar">
        </a>
        <a href="/profile?usr=${user.email_prefix}">${user.name}</a>
        ${user.accessory_image ? `<img src="${user.accessory_image}" class="name-accessory" alt="accessory">` : ''}
      `;
      const nameLink = li.querySelectorAll('a')[1];
      if (nameLink && user.name_color) {
        nameLink.style.color = user.name_color;
      }
      list.appendChild(li);
    });
  });
}

function setSendDisabled(disabled) {
  if (!sendButton) return;
  sendButton.disabled = disabled;
}

function enqueueMessage(payload) {
  queuedMessages.push(payload);
}

function trySend(payload) {
  if (!payload || sending) return;
  sending = true;
  setSendDisabled(true);
  socket.timeout(5000).emit('send_message', payload, (error, response) => {
    sending = false;
    setSendDisabled(!canSend);
    if (error || !response) {
      enqueueMessage(payload);
      return;
    }
    if (!response.ok) {
      return;
    }
    if (response.message && response.message.channel_id === channelId) {
      markChannelRead(response.message.id);
    }
  });
}

function flushQueue() {
  if (sending || !queuedMessages.length) return;
  const next = queuedMessages.shift();
  trySend(next);
}

socket.on('connect', () => {
  joinedChannelSlugs.forEach((slug) => socket.emit('join', { channel: slug }));
  flushQueue();
});

socket.on('online_update', (users) => {
  updateOnlineList(users);
});

socket.on('typing_update', (payload) => {
  if (!payload || payload.channel !== channel) return;
  const others = (payload.users || []).filter((user) => user.id !== window.KJB_CURRENT_USER_ID);
  if (!others.length) {
    typingIndicator.classList.add('hidden');
    typingIndicator.textContent = '';
    return;
  }
  const names = others.map((user) => user.name);
  typingIndicator.textContent = names.length === 1 ? `${names[0]} 입력 중...` : `${names[0]} 외 ${names.length - 1}명 입력 중...`;
  typingIndicator.classList.remove('hidden');
});

socket.on('new_message', (message) => {
  if (message.channel_id !== channelId) {
    setUnreadDot(message.channel_id, true);
    return;
  }
  appendMessage(message);
});

socket.on('message_updated', (message) => {
  const element = messageList.querySelector(`[data-message-id="${message.id}"]`);
  if (!element) return;
  element.querySelector('.message-content').textContent = message.content;
  if (message.rendered_content) {
    element.querySelector('.message-content').innerHTML = message.rendered_content;
  }
  const meta = element.querySelector('.message-meta');
  if (!meta.querySelector('.edited')) {
    const edited = document.createElement('span');
    edited.className = 'edited';
    edited.textContent = '수정됨';
    meta.appendChild(edited);
  }
});

socket.on('message_deleted', (payload) => {
  const element = messageList.querySelector(`[data-message-id="${payload.message_id}"]`);
  if (!element) return;
  element.querySelector('.message-content').textContent = '[삭제됨]';
});

sendButton.addEventListener('click', () => {
  if (!canSend || sending) return;
  const content = input.value.trim();
  if (!content) return;
  const payload = { channel, content, reply_to: replyToId };
  trySend(payload);
  input.value = '';
  replyToId = null;
  replyBanner.classList.add('hidden');
  updateTypingState(false);
});

input.addEventListener('input', () => {
  if (!canSend) return;
  const hasValue = input.value.trim().length > 0;
  updateTypingState(hasValue);
  if (typingTimer) {
    clearTimeout(typingTimer);
  }
  typingTimer = setTimeout(() => updateTypingState(false), 1500);
});

input.addEventListener('blur', () => {
  updateTypingState(false);
});

input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendButton.click();
  }
});

messageList.addEventListener('contextmenu', (event) => {
  const messageElement = event.target.closest('.message');
  if (!messageElement) return;
  event.preventDefault();
  contextMessageId = messageElement.dataset.messageId;
  contextUserId = parseInt(messageElement.dataset.userId, 10);
  const isOwner = contextUserId === window.KJB_CURRENT_USER_ID;
  contextMenu.querySelector('[data-action="edit"]').style.display = isOwner ? 'block' : 'none';
  contextMenu.querySelector('[data-action="delete"]').style.display = (isOwner || window.KJB_IS_ADMIN) ? 'block' : 'none';
  contextMenu.style.top = `${event.clientY}px`;
  contextMenu.style.left = `${event.clientX}px`;
  contextMenu.classList.remove('hidden');
});

window.addEventListener('click', () => {
  contextMenu.classList.add('hidden');
});

window.addEventListener('beforeunload', () => {
  socket.emit('typing', { channel, is_typing: false });
  socket.emit('leave', { channel });
  flushReadSync();
});

contextMenu.addEventListener('click', (event) => {
  const action = event.target.dataset.action;
  if (!action) return;
  if (action === 'reply') {
    replyToId = contextMessageId;
    const messageElement = messageList.querySelector(`[data-message-id="${contextMessageId}"]`);
    const content = messageElement ? messageElement.querySelector('.message-content').textContent : '';
    replyBanner.textContent = `답장: ${content}`;
    replyBanner.classList.remove('hidden');
  }
  if (action === 'edit') {
    const newContent = prompt('수정할 내용을 입력하세요');
    if (newContent) {
      socket.emit('edit_message', { message_id: contextMessageId, content: newContent });
    }
  }
  if (action === 'delete') {
    if (confirm('메시지를 삭제할까요?')) {
      socket.emit('delete_message', { message_id: contextMessageId });
    }
  }
  contextMenu.classList.add('hidden');
});

const lastMessage = messageList.querySelector('.message:last-of-type');
if (lastMessage) {
  markChannelRead(parseInt(lastMessage.dataset.messageId, 10));
}
setUnreadDot(channelId, false);
setSendDisabled(!canSend);
