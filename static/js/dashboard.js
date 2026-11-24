document.addEventListener('DOMContentLoaded', () => {
    wireIcons();
    wireSidebar();
    wirePopovers();
    wireModals();
    wireSessionTimeout();
    wireLoanQuote();
    wireMockForms();
    wireMessages();
    wirePasswordToggles();
    wireRealtimeNotifications();
});

// Fill every `<span class="icon" data-icon="name">` with its sprite symbol.
// width/height="1em" here are a defensive fallback: if dashboard.css fails
// to load for any reason, the icon still sizes to its surrounding text
// instead of falling back to the browser's ~300x150 default SVG size.
function wireIcons() {
    document.querySelectorAll('[data-icon]').forEach((el) => {
        const name = el.getAttribute('data-icon');
        el.innerHTML = `<svg width="1em" height="1em"><use href="#icon-${name}"></use></svg>`;
    });
}

function wireSidebar() {
    const toggle = document.getElementById('sidebarToggle');
    const sidebar = document.getElementById('sidebar');
    const scrim = document.getElementById('sidebarScrim');
    if (!toggle || !sidebar) return;

    const close = () => {
        sidebar.classList.remove('is-open');
        scrim.classList.remove('is-visible');
        toggle.setAttribute('aria-expanded', 'false');
    };
    toggle.addEventListener('click', () => {
        const open = sidebar.classList.toggle('is-open');
        scrim.classList.toggle('is-visible', open);
        toggle.setAttribute('aria-expanded', String(open));
    });
    scrim.addEventListener('click', close);
}

function wirePopovers() {
    const popovers = document.querySelectorAll('[data-popover]');

    document.addEventListener('click', (event) => {
        popovers.forEach((popover) => {
            const trigger = popover.querySelector('[data-popover-trigger]');
            const isClickInside = popover.contains(event.target);
            if (trigger === event.target || trigger?.contains(event.target)) {
                const willOpen = !popover.classList.contains('is-open');
                popovers.forEach((p) => p.classList.remove('is-open'));
                popover.classList.toggle('is-open', willOpen);
                trigger.setAttribute('aria-expanded', String(willOpen));
            } else if (!isClickInside) {
                popover.classList.remove('is-open');
            }
        });
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') popovers.forEach((p) => p.classList.remove('is-open'));
    });
}

function wireModals() {
    const openModal = (modal) => {
        modal.hidden = false;
        modal.querySelector('input, select, button')?.focus();
    };
    const closeModal = (modal) => {
        modal.hidden = true;
    };

    document.querySelectorAll('[data-modal-open]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const modal = document.getElementById(btn.getAttribute('data-modal-open'));
            if (!modal) return;
            // Beneficiary "send" buttons pre-fill the recipient.
            const recipient = modal.querySelector('[data-recipient-input]');
            if (recipient) recipient.value = btn.dataset.recipient || '';
            openModal(modal);
            if (btn.dataset.recipient) modal.querySelector('input[name="amount"]')?.focus();
        });
    });

    // The session-timeout dialog must not be dismissible without a decision.
    const dismissible = document.querySelectorAll('.modal:not([data-session-timeout])');
    dismissible.forEach((modal) => {
        modal.addEventListener('click', (event) => {
            if (event.target === modal) closeModal(modal);
        });
        modal.querySelectorAll('[data-modal-close]').forEach((btn) => {
            btn.addEventListener('click', () => closeModal(modal));
        });
    });

    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        dismissible.forEach((modal) => {
            modal.hidden = true;
        });
    });
}

// Keep the existing client-side interaction, but allow real form submissions to proceed normally.
function wireMockForms() {
    document.querySelectorAll('[data-mock-submit]').forEach((form) => {
        form.addEventListener('submit', (event) => {
            const modal = form.closest('.modal');
            if (modal) {
                modal.hidden = true;
            }
        });
    });
}

function wireMessages() {
    document.querySelectorAll('[data-message-close]').forEach((btn) => {
        btn.addEventListener('click', () => btn.closest('.message')?.classList.add('is-dismissed'));
    });
}

function wireRealtimeNotifications() {
    const topbar = document.querySelector('[data-notification-socket]');
    if (!topbar || !('WebSocket' in window)) return;

    const socketPath = topbar.dataset.notificationSocket;
    const socketUrl = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${socketPath}`;
    let reconnectDelay = 1000;

    const connect = () => {
        const socket = new WebSocket(socketUrl);

        socket.addEventListener('open', () => {
            reconnectDelay = 1000;
        });
        socket.addEventListener('message', (event) => {
            let payload;
            try {
                payload = JSON.parse(event.data);
            } catch {
                return;
            }

            if (payload.type === 'notifications.snapshot') {
                Object.entries(payload.items || {}).forEach(([kind, items]) => {
                    const list = document.querySelector(`[data-notification-list="${kind}"]`);
                    if (!list) return;
                    list.replaceChildren();
                    items.forEach((notification) => list.append(createNotificationItem(notification)));
                    if (!items.length) list.append(createEmptyNotificationItem(kind));
                    updateNotificationCount(kind, payload.counts?.[kind] || 0);
                });
            } else if (payload.type === 'notifications.created' && payload.notification) {
                const notification = payload.notification;
                const list = document.querySelector(`[data-notification-list="${notification.kind}"]`);
                if (list && !list.querySelector(`[data-notification-id="${CSS.escape(notification.id)}"]`)) {
                    list.querySelector('.popover__empty')?.remove();
                    list.prepend(createNotificationItem(notification));
                    while (list.children.length > 5) list.lastElementChild.remove();
                }
                updateNotificationCount(notification.kind, payload.unread_count || 0);
            }
        });
        socket.addEventListener('close', () => {
            window.setTimeout(connect, reconnectDelay);
            reconnectDelay = Math.min(reconnectDelay * 2, 30000);
        });
        socket.addEventListener('error', () => socket.close());
    };

    connect();
}

function createNotificationItem(notification) {
    const item = document.createElement('li');
    const levels = ['info', 'success', 'warning', 'danger'];
    item.className = `popover__item${notification.kind === 'alert' && levels.includes(notification.level) ? ` popover__item--${notification.level}` : ''}`;
    item.dataset.notificationId = notification.id;

    if (notification.kind === 'alert') {
        const dot = document.createElement('span');
        dot.className = 'popover__dot';
        item.append(dot);
    } else {
        const avatar = document.createElement('span');
        avatar.className = 'popover__avatar';
        avatar.textContent = (notification.sender_name || 'K').slice(0, 1);
        item.append(avatar);
    }

    const content = document.createElement('div');
    const title = document.createElement('p');
    title.className = 'popover__title';
    title.textContent = notification.title;
    const meta = document.createElement('p');
    meta.className = 'popover__meta';
    meta.textContent = `${notification.body} · Just now`;
    content.append(title, meta);
    item.append(content);
    return item;
}

function createEmptyNotificationItem(kind) {
    const item = document.createElement('li');
    item.className = 'popover__empty';
    item.textContent = kind === 'alert' ? "You're all caught up." : 'No new messages.';
    return item;
}

function updateNotificationCount(kind, count) {
    const badge = document.querySelector(`[data-notification-count="${kind}"]`);
    if (!badge) return;
    badge.textContent = String(count);
    badge.hidden = count < 1;
}

// Toggles a password field between masked and plain text via its trailing button.
function wirePasswordToggles() {
    document.querySelectorAll('[data-password-toggle]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const input = document.getElementById(btn.getAttribute('data-password-toggle'));
            if (!input) return;
            const showing = input.type === 'text';
            input.type = showing ? 'password' : 'text';
            btn.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
        });
    });
}

function getCookie(name) {
    return document.cookie.split('; ').find((c) => c.startsWith(`${name}=`))?.split('=')[1] ?? '';
}

// Mirrors the server's sliding idle timeout: warns before it expires and lets the
// user extend it. Tabs share the last-activity time so one active tab keeps all alive.
function wireSessionTimeout() {
    const dialog = document.querySelector('[data-session-timeout]');
    if (!dialog) return;

    const idleMs = Number(dialog.dataset.idleSeconds) * 1000;
    const warningMs = Number(dialog.dataset.warningSeconds) * 1000;
    const countdown = dialog.querySelector('[data-session-countdown]');
    const storageKey = 'sessionLastActivity';
    let lastActivity = Date.now();
    let tick;

    const remember = (time) => {
        lastActivity = time;
        try { localStorage.setItem(storageKey, String(time)); } catch { /* private mode */ }
    };

    const signOut = () => {
        window.location.assign(`${dialog.dataset.signedOutUrl}&next=${encodeURIComponent(location.pathname)}`);
    };

    const update = () => {
        const remaining = lastActivity + idleMs - Date.now();
        if (remaining <= 0) {
            clearInterval(tick);
            signOut();
        } else if (remaining <= warningMs) {
            dialog.hidden = false;
            countdown.textContent = String(Math.ceil(remaining / 1000));
        } else {
            dialog.hidden = true;
        }
    };

    dialog.querySelector('[data-session-stay]').addEventListener('click', async () => {
        const response = await fetch(dialog.dataset.keepaliveUrl, {
            method: 'POST',
            headers: {'X-CSRFToken': getCookie('csrftoken')},
            credentials: 'same-origin',
        });
        if (response.ok) {
            remember(Date.now());
            update();
        } else {
            signOut();
        }
    });

    window.addEventListener('storage', (event) => {
        if (event.key === storageKey && event.newValue) {
            lastActivity = Number(event.newValue);
            update();
        }
    });

    remember(Date.now());
    tick = setInterval(update, 1000);
}

// Live monthly-instalment preview on the loan application form.
function wireLoanQuote() {
    const form = document.querySelector('[data-loan-quote]');
    if (!form) return;
    const output = form.querySelector('[data-loan-quote-output]');
    const monthlyRate = Number(form.dataset.annualRate) / 100 / 12;

    const render = () => {
        const principal = Number(form.elements.principal.value);
        const months = Number(form.elements.term_months.value);
        if (!principal || !months) {
            output.textContent = '';
            return;
        }
        const instalment = monthlyRate
            ? (principal * monthlyRate) / (1 - (1 + monthlyRate) ** -months)
            : principal / months;
        const format = (value) => value.toLocaleString('en-KE', {minimumFractionDigits: 2, maximumFractionDigits: 2});
        output.textContent = `About KES ${format(instalment)} a month, KES ${format(instalment * months)} in total.`;
    };

    form.addEventListener('input', render);
    render();
}
