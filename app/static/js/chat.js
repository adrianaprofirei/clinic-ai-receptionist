/* Chat widget for Asistent Zâmbet.
 *
 * Talks to POST /api/chat/session (history + mode) and POST /api/chat, which
 * answers with NDJSON events. Every card is built on the server; this file only
 * renders them (DOM APIs, never innerHTML with data) and posts the `send`
 * payload of the button the visitor presses.
 */
(function () {
  "use strict";

  const root = document.querySelector("[data-chat]");
  if (!root) return;

  const q = (selector) => root.querySelector(selector);
  const panel = q(".chat-panel");
  const log = q("[data-chat-log]");
  const form = q("[data-chat-form]");
  const input = q("[data-chat-input]");
  const sendButton = q("[data-chat-send]");
  const launcher = q("[data-chat-launcher]");
  const closeButton = q("[data-chat-close]");
  const greeting = q("[data-chat-greeting]");
  const statusText = q("[data-chat-status]");
  const statusDot = q("[data-chat-dot]");
  const banner = q("[data-chat-banner]");
  const footnote = q("[data-chat-footnote]");
  const limit = q("[data-chat-limit]");
  const limitText = q("[data-chat-limit-text]");
  const limitCount = q("[data-chat-count]");

  const phoneMedia = window.matchMedia("(max-width: 560px)");
  const GREETING_KEY = "zb-greeting-dismissed";

  const state = {
    open: false,
    loading: null,
    busy: false,
    mode: "ai",
    step: 0,
    maxChars: 500,
    lastAt: null,
    typing: null,
  };

  // --- tiny DOM helpers -------------------------------------------------------------------

  const SVG_NS = "http://www.w3.org/2000/svg";
  const ICONS = {
    phone: [["path", { d: "M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2" }]],
    check: [["path", { d: "M5 12.5l4.5 4.5L19 7.5" }]],
    clock: [["circle", { cx: 12, cy: 12, r: 8.5 }], ["path", { d: "M12 7.5V12l3 2" }]],
    info: [["circle", { cx: 12, cy: 12, r: 9 }], ["path", { d: "M12 11v5.5M12 7.6v.2" }]],
    alert: [["circle", { cx: 12, cy: 12, r: 9 }], ["path", { d: "M12 7.5v5.5M12 16.4v.2" }]],
    close: [["path", { d: "M6 6l12 12M18 6L6 18" }]],
    arrow: [["path", { d: "M5 12h14M13 6l6 6-6 6" }]],
    lock: [["rect", { x: 5, y: 10.5, width: 14, height: 10, rx: 2.5 }], ["path", { d: "M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" }]],
    calendar: [["rect", { x: 4, y: 5, width: 16, height: 15, rx: 2.5 }], ["path", { d: "M4 10h16M9 3v4M15 3v4" }]],
  };

  function icon(name, size, stroke) {
    const svg = document.createElementNS(SVG_NS, "svg");
    const attrs = {
      class: "icon", width: size || 18, height: size || 18, viewBox: "0 0 24 24", fill: "none",
      stroke: "currentColor", "stroke-width": stroke || 2, "stroke-linecap": "round",
      "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false",
    };
    Object.keys(attrs).forEach((k) => svg.setAttribute(k, attrs[k]));
    (ICONS[name] || []).forEach(([tag, shape]) => {
      const node = document.createElementNS(SVG_NS, tag);
      Object.keys(shape).forEach((k) => node.setAttribute(k, shape[k]));
      svg.append(node);
    });
    return svg;
  }

  function h(tag, attrs, children) {
    const el = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value == null || value === false) return;
      if (key === "class") el.className = value;
      else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, value === true ? "" : value);
    });
    (children || []).forEach((child) => {
      if (child == null || child === false) return;
      el.append(child.nodeType ? child : document.createTextNode(String(child)));
    });
    return el;
  }

  // Turn Romanian phone numbers in plain text into tel: links.
  const PHONE_RE = /\b0\d{3} \d{3} \d{3}\b/g;
  function richText(text) {
    const fragment = document.createDocumentFragment();
    let last = 0;
    String(text).replace(PHONE_RE, (match, offset) => {
      fragment.append(text.slice(last, offset));
      fragment.append(h("a", { href: "tel:+4" + match.replace(/ /g, "") }, [match]));
      last = offset + match.length;
      return match;
    });
    fragment.append(text.slice(last));
    return fragment;
  }

  // --- header, banner and composer follow the session mode ------------------------------------

  function applySession(session) {
    if (!session) return;
    state.mode = session.mode;
    state.step = session.demo_step || 0;
    state.maxChars = session.max_chars || 500;
    const demo = state.mode === "demo";
    statusDot.classList.toggle("status-dot--demo", demo);
    statusText.textContent = demo
      ? (state.step ? `Mod demo · pasul ${state.step} din 4` : "Mod demo · programare ghidată")
      : "Online · răspunde imediat";
    banner.textContent = demo ? "Mod demo, fără AI · clinică fictivă" : "Demo – clinică fictivă · datele se șterg zilnic";
    footnote.textContent = demo ? "Programare ghidată · nu oferă sfaturi medicale" : "Asistent automat · nu oferă sfaturi medicale";
    input.disabled = demo;
    input.placeholder = demo ? "În modul demo alegi din butoane" : "Scrie un mesaj…";
    input.maxLength = state.maxChars;
    updateComposer();
  }

  function updateComposer() {
    const length = input.value.length;
    const max = state.maxChars;
    const atLimit = length >= max;
    sendButton.disabled = input.disabled || state.busy || !input.value.trim();
    if (length >= Math.floor(max * 0.8) && !input.disabled) {
      limit.hidden = false;
      limit.classList.toggle("is-error", atLimit);
      limitText.textContent = atLimit ? `Ai atins limita de ${max} de caractere.` : "";
      limitCount.textContent = `${length}/${max}`;
      if (atLimit) input.setAttribute("aria-invalid", "true");
      else input.removeAttribute("aria-invalid");
    } else {
      limit.hidden = true;
      input.removeAttribute("aria-invalid");
    }
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight + 3, 132) + "px";
  }

  function setBusy(busy) {
    state.busy = busy;
    panel.setAttribute("aria-busy", String(busy));
    updateComposer();
  }

  // --- conversation rendering ------------------------------------------------------------------

  function scrollToEnd() {
    log.scrollTop = log.scrollHeight;
  }

  function append(node) {
    if (state.typing && state.typing.isConnected) log.insertBefore(node, state.typing);
    else log.append(node);
  }

  function timeLabel(iso) {
    const at = new Date(iso);
    const now = new Date();
    const hhmm = at.toLocaleTimeString("ro-RO", { hour: "2-digit", minute: "2-digit" });
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    if (at.toDateString() === now.toDateString()) return `Azi, ${hhmm}`;
    if (at.toDateString() === yesterday.toDateString()) return `Ieri, ${hhmm}`;
    return `${at.toLocaleDateString("ro-RO", { day: "numeric", month: "long" })}, ${hhmm}`;
  }

  function maybeTime(iso) {
    if (!iso) return;
    const at = new Date(iso).getTime();
    if (state.lastAt === null || at - state.lastAt > 60 * 60 * 1000) {
      append(h("p", { class: "chat-time" }, [timeLabel(iso)]));
    }
    state.lastAt = at;
  }

  function messageGroup(msg) {
    const group = h("div", { class: `msg msg--${msg.role}`, "data-id": msg.id });
    if (msg.text) {
      const bubble = h("p", {
        class: msg.role === "user" ? "bubble bubble--user" : "bubble",
        lang: msg.lang && msg.lang !== "ro" ? msg.lang : null,
      });
      bubble.append(msg.role === "user" ? msg.text : richText(msg.text));
      group.append(bubble);
    }
    (msg.ui || []).forEach((card) => {
      const render = CARDS[card.type];
      const node = render ? render(card, msg) : null;
      if (node) group.append(node);
    });
    return group;
  }

  function renderMessage(msg) {
    maybeTime(msg.at);
    const group = messageGroup(msg);
    append(group);
    return group;
  }

  function updateMessage(msg) {
    const old = log.querySelector(`.msg[data-id="${msg.id}"]`);
    if (!old) {
      renderMessage(msg);
      return;
    }
    const fresh = messageGroup(msg);
    old.replaceWith(fresh);
    const invalid = fresh.querySelector('[aria-invalid="true"]');
    if (invalid) invalid.focus();
  }

  // Optimistic bubble: time separators follow the server's clock, set when it answers.
  function renderUserBubble(text) {
    const group = h("div", { class: "msg msg--user is-pending" }, [h("p", { class: "bubble bubble--user" }, [text])]);
    append(group);
    scrollToEnd();
    return group;
  }

  function showTyping(text) {
    if (!state.typing) {
      state.typing = h("p", { class: "chat-typing", role: "status" }, [
        h("span", { class: "dots", "aria-hidden": "true" }, [h("span"), h("span"), h("span")]),
        h("span", { class: "chat-typing__text" }),
      ]);
    }
    const label = state.typing.querySelector(".chat-typing__text");
    label.textContent = text || "";
    state.typing.classList.toggle("has-text", Boolean(text));
    if (!state.typing.isConnected) log.append(state.typing);
    scrollToEnd();
  }

  function hideTyping() {
    if (state.typing) state.typing.remove();
  }

  // Quick replies of earlier messages are spent once the conversation moves on.
  function spendQuickReplies() {
    log.querySelectorAll(".chips:not(.is-spent)").forEach(spend);
  }

  function spend(group) {
    group.classList.add("is-spent");
    group.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  }

  // --- buttons and cards ---------------------------------------------------------------------

  function choice(option, className, children) {
    const button = h("button", {
      type: "button", class: className, "aria-pressed": "false", "aria-label": option.aria || null,
    }, children || [option.label]);
    button.addEventListener("click", () => {
      if (state.busy) return;
      const group = button.closest(".chips, .slot-grid, .day-grid, .service-choices");
      if (group) {
        group.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
        spend(group);
      }
      send(option.send);
    });
    return button;
  }

  function actionButton(option, className, children) {
    const button = h("button", { type: "button", class: className }, children || [option.label]);
    button.addEventListener("click", () => {
      if (state.busy) return;
      button.closest(".card, .notice-block, .cancelled-block")?.querySelectorAll("button")
        .forEach((b) => { b.disabled = true; });
      send(option.send);
    });
    return button;
  }

  function rows(list) {
    const dl = h("dl", { class: "card__rows" });
    list.forEach((row) => {
      dl.append(h("dt", null, [row.label]));
      dl.append(h("dd", { class: [row.numeric ? "num" : "", row.code ? "booked__code" : ""].join(" ").trim() || null }, [row.value]));
    });
    return dl;
  }

  function downloadIcs(calendar) {
    const blob = new Blob([calendar.ics], { type: "text/calendar;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = h("a", { href: url, download: calendar.filename });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  function slotButtons(card, grid, options) {
    grid.replaceChildren(...options.map((o) => choice(o, "choice choice--time")));
  }

  const CARDS = {
    quick_replies(card) {
      const group = h("div", { class: "chips", role: "group" });
      card.options.forEach((o) => group.append(choice(o, o.tone === "danger" ? "choice choice--danger" : "choice")));
      return group;
    },

    services(card) {
      const list = h("div", { class: "service-choices", role: "group", "aria-label": card.label });
      card.options.forEach((o) => list.append(choice(o, "choice service-choice", [
        h("span", null, [o.label]), h("span", { class: "service-choice__meta" }, [o.meta]),
      ])));
      return list;
    },

    days(card) {
      const grid = h("div", { class: "day-grid", role: "group", "aria-label": card.label });
      card.options.forEach((o) => grid.append(choice({ ...o, aria: o.label }, "choice day-choice", [
        h("span", null, [o.top]), h("span", null, [o.bottom]),
      ])));
      return grid;
    },

    slots(card) {
      const grid = h("div", {
        class: card.layout === "grid" ? "slot-grid slot-grid--wide" : "slot-grid",
        role: "group", "aria-label": card.group_label,
      });
      slotButtons(card, grid, card.options);
      const actions = h("div", { class: "slot-actions" });
      if (card.more && card.more.length) {
        const more = h("button", { type: "button", class: "text-action" }, [card.more_label]);
        more.addEventListener("click", () => {
          const all = card.options.concat(card.more).sort((a, b) => (a.value < b.value ? -1 : 1));
          slotButtons(card, grid, all);
          more.remove();
          grid.querySelector("button")?.focus();
        });
        actions.append(more);
      }
      if (card.other_day) {
        const other = h("button", { type: "button", class: "text-action" }, [card.other_day.label, icon("arrow", 18, 2.2)]);
        other.addEventListener("click", () => {
          if (state.busy) return;
          spend(grid);
          other.disabled = true;
          send(card.other_day.send);
        });
        actions.append(other);
      }
      if (card.layout === "card") {
        return h("div", { class: "slot-card" }, [
          h("div", { class: "slot-card__head" }, [
            h("p", { class: "slot-card__date" }, [icon("calendar", 18), card.date_label]),
            h("p", { class: "slot-card__meta" }, [card.meta]),
          ]),
          grid,
          actions,
        ]);
      }
      return h("div", { class: "msg-block" }, [grid, actions]);
    },

    consent(card) {
      return h("div", { class: "consent" }, [
        h("span", { class: "consent__icon" }, [icon("lock", 15, 2.2)]),
        h("p", null, [card.text, " ", h("a", { href: card.link.href, target: "_blank", rel: "noopener" }, [card.link.label])]),
      ]);
    },

    details_form(card, msg) {
      if (card.state === "done") {
        return h("p", { class: "details-done" }, [icon("check", 16, 2.6), card.done_label]);
      }
      const formEl = h("form", { class: "card details-form", novalidate: true });
      formEl.append(h("p", { class: "details-form__consent" }, [
        icon("lock", 16, 2.2),
        h("span", null, [card.consent, " ", h("a", { href: card.link.href, target: "_blank", rel: "noopener" }, [card.link.label])]),
      ]));
      card.fields.forEach((field) => {
        const id = `chat-${msg.id}-${field.name}`;
        const error = card.errors && card.errors[field.name];
        formEl.append(h("div", { class: "field" }, [
          h("label", { class: "field__label", for: id }, [field.label]),
          h("input", {
            class: "field__input", id, name: field.name, type: field.type, autocomplete: field.autocomplete,
            inputmode: field.type === "tel" ? "tel" : null, placeholder: field.placeholder,
            value: (card.values && card.values[field.name]) || "", required: true,
            "aria-invalid": error ? "true" : null, "aria-describedby": error ? `${id}-error` : null,
          }),
          error ? h("p", { class: "field__error", id: `${id}-error` }, [error]) : null,
        ]));
      });
      formEl.append(h("button", { class: "btn btn--primary btn--block", type: "submit" }, [card.submit]));
      formEl.addEventListener("submit", (event) => {
        event.preventDefault();
        if (state.busy) return;
        send({
          action: {
            type: "details", name: formEl.elements.name.value, phone: formEl.elements.phone.value, message_id: msg.id,
          },
        });
      });
      return formEl;
    },

    summary(card) {
      if (card.state !== "pending") {
        const confirmed = card.state === "confirmed";
        return h("div", { class: "card summary--compact" }, [
          h("p", { class: "summary__title" }, [card.title]),
          h("p", { class: "summary__line num" }, [card.compact]),
          h("p", { class: confirmed ? "summary__state" : "summary__state summary__state--off" }, [
            confirmed ? icon("check", 16, 2.6) : null, card.state_labels[card.state] || "",
          ]),
        ]);
      }
      return h("div", { class: "card" }, [
        h("p", { class: "card__title" }, [card.title]),
        rows(card.rows),
        h("div", { class: "card__buttons" }, [
          actionButton(card.confirm, "btn btn--primary btn--block"),
          actionButton(card.modify, "btn btn--line btn--block"),
        ]),
      ]);
    },

    booking(card) {
      const calendar = h("button", { type: "button", class: "btn btn--line btn--block" }, [icon("calendar", 18), card.calendar.label]);
      calendar.addEventListener("click", () => downloadIcs(card.calendar));
      return h("div", { class: "booked", role: "status" }, [
        h("div", { class: "booked__head" }, [h("span", { class: "booked__check" }, [icon("check", 18, 2.8)]), h("p", null, [card.title])]),
        h("p", { class: "booked__when" }, [card.when[0], h("br"), card.when[1]]),
        rows(card.rows),
        calendar,
      ]);
    },

    emergency(card) {
      return h("div", { class: "emergency" }, [
        h("a", { class: "btn btn--danger btn--block", href: `tel:${card.call.tel}` }, [icon("phone", 20, 1.9), card.call.label]),
        h("p", null, [card.hours]),
        h("p", { class: "emergency__warning" }, [card.warning]),
      ]);
    },

    notice(card) {
      const box = h("div", { class: card.tone === "warning" ? "notice notice--warning" : "notice", role: "status" }, [
        icon(card.icon === "clock" ? "clock" : "info", 20), h("p", null, [richText(card.text)]),
      ]);
      if (!card.action) return box;
      return h("div", { class: "notice-block" }, [box, actionButton(card.action, "btn btn--primary")]);
    },

    cancelled(card) {
      return h("div", { class: "cancelled-block" }, [
        h("div", { class: "cancelled", role: "status" }, [
          h("span", { class: "cancelled__icon" }, [icon("close", 14, 2.8)]),
          h("p", null, [h("strong", null, [card.title]), " ", card.text]),
        ]),
        actionButton(card.action, "choice"),
      ]);
    },

    price_list(card) {
      return h("div", { class: "card price-list" }, [
        h("ul", null, card.rows.map((row) => h("li", null, [
          h("span", null, [row.label, h("span", { class: "price-list__meta" }, [row.meta])]),
          h("span", { class: "price-list__value" }, [row.value]),
        ]))),
        h("p", { class: "price-list__note" }, [card.note]),
      ]);
    },
  };

  // --- talking to the server ---------------------------------------------------------------------

  function failed(group, payload, text) {
    if (!group) {
      append(CARDS.notice({ tone: "warning", icon: "info", text: text || "Ceva n-a mers. Încearcă din nou." }));
      return;
    }
    group.classList.remove("is-pending");
    group.classList.add("is-failed");
    const retry = h("button", { type: "button", class: "link-button" }, ["Încearcă din nou"]);
    retry.addEventListener("click", () => {
      if (state.busy) return;
      group.remove();
      send(payload);
    });
    group.append(h("div", { class: "msg-error", role: "alert" }, [icon("alert", 16, 2.2), h("span", null, [text || "Nu s-a trimis."]), retry]));
  }

  async function handleHttpError(response, group, payload) {
    let data = {};
    try { data = await response.json(); } catch (_) { /* not JSON */ }
    if (response.status === 429) {
      if (group) group.classList.remove("is-pending");
      append(CARDS.notice({ tone: "warning", icon: "clock", text: data.message || "Prea multe mesaje. Încearcă din nou în câteva minute." }));
    } else if (response.status === 413) {
      group?.remove();
      input.value = (payload && payload.text) || input.value;
      updateComposer();
    } else {
      failed(group, payload, data.message);
    }
  }

  async function readStream(response, group) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const handle = (event) => {
      switch (event.type) {
        case "status":
          showTyping(event.text);
          break;
        case "user":
          if (group && !group.dataset.id) {
            group.dataset.id = event.message.id;
            group.classList.remove("is-pending");
            state.lastAt = new Date(event.message.at).getTime();
          } else {
            hideTyping();
            renderMessage(event.message);
          }
          break;
        case "message":
          hideTyping();
          renderMessage(event.message);
          break;
        case "update":
          updateMessage(event.message);
          break;
        case "session":
          applySession(event.session);
          break;
        case "error":
          hideTyping();
          append(CARDS.notice({ tone: "warning", icon: "info", text: event.message }));
          break;
        default:
          break;
      }
      scrollToEnd();
    };
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (line) handle(JSON.parse(line));
      }
    }
    if (group) group.classList.remove("is-pending");
  }

  async function send(payload) {
    if (!payload || state.busy) return;
    spendQuickReplies();
    const echo = payload.text != null ? payload.text : payload.echo;
    const group = echo ? renderUserBubble(echo) : null;
    const body = payload.text != null ? { message: payload.text } : { action: payload.action };
    setBusy(true);
    showTyping();
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/x-ndjson" },
        credentials: "same-origin",
        body: JSON.stringify(body),
      });
      hideTyping();
      if (!response.ok) {
        await handleHttpError(response, group, payload);
      } else {
        showTyping();
        await readStream(response, group);
      }
    } catch (_) {
      failed(group, payload);
    } finally {
      hideTyping();
      setBusy(false);
      scrollToEnd();
    }
  }

  function loadSession() {
    if (state.loading) return state.loading;
    showTyping();
    state.loading = fetch("/api/chat/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: "{}",
    })
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then((data) => {
        hideTyping();
        log.replaceChildren();
        state.lastAt = null;
        applySession(data.session);
        data.messages.forEach(renderMessage);
        scrollToEnd();
      })
      .catch(() => {
        hideTyping();
        state.loading = null;
        const retry = h("button", { type: "button", class: "btn btn--primary" }, ["Încearcă din nou"]);
        retry.addEventListener("click", () => { block.remove(); loadSession(); });
        const block = h("div", { class: "notice-block" }, [
          CARDS.notice({ tone: "warning", icon: "info", text: "Nu m-am putut conecta. Verifică internetul sau sună-ne la 0232 000 000." }),
          retry,
        ]);
        log.append(block);
        throw new Error("session");
      });
    return state.loading;
  }

  // --- open / close -------------------------------------------------------------------------------

  function hideGreeting(remember) {
    greeting.hidden = true;
    if (remember) {
      try { sessionStorage.setItem(GREETING_KEY, "1"); } catch (_) { /* private mode */ }
    }
  }

  function openChat(then) {
    if (!state.open) {
      state.open = true;
      panel.hidden = false;
      root.classList.add("is-open");
      launcher.setAttribute("aria-expanded", "true");
      launcher.setAttribute("aria-label", "Închide chatul");
      hideGreeting(true);
      if (phoneMedia.matches) document.documentElement.classList.add("chat-locked");
    }
    loadSession()
      .then(() => {
        if (then) then();
        scrollToEnd();
        if (!phoneMedia.matches && !input.disabled) input.focus();
        else panel.focus();
      })
      .catch(() => {});
  }

  function closeChat() {
    if (!state.open) return;
    state.open = false;
    panel.hidden = true;
    root.classList.remove("is-open");
    launcher.setAttribute("aria-expanded", "false");
    launcher.setAttribute("aria-label", "Întreabă asistentul");
    document.documentElement.classList.remove("chat-locked");
    launcher.focus();
  }

  panel.setAttribute("tabindex", "-1");
  launcher.addEventListener("click", () => (state.open ? closeChat() : openChat()));
  closeButton.addEventListener("click", closeChat);
  panel.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      closeChat();
    }
  });

  // Any [data-open-chat] link on the page opens the assistant (optionally for a service).
  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-open-chat]");
    if (!trigger) return;
    event.preventDefault();
    const service = trigger.dataset.service;
    const serviceName = trigger.dataset.serviceName;
    openChat(service ? () => {
      if (state.mode === "demo") send({ action: { type: "service", value: service }, echo: serviceName });
      else send({ text: `Aș vrea o programare: ${serviceName}` });
    } : null);
  });

  // Greeting bubble next to the launcher, once per visit.
  let dismissed = false;
  try { dismissed = sessionStorage.getItem(GREETING_KEY) === "1"; } catch (_) { /* private mode */ }
  if (!dismissed) {
    setTimeout(() => { if (!state.open) greeting.hidden = false; }, 3500);
  }
  q("[data-chat-greeting-open]").addEventListener("click", () => openChat());
  q("[data-chat-greeting-close]").addEventListener("click", () => hideGreeting(true));

  // --- composer --------------------------------------------------------------------------------------

  input.addEventListener("input", updateComposer);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || state.busy || input.disabled) return;
    if (text.length > state.maxChars) {
      updateComposer();
      return;
    }
    input.value = "";
    updateComposer();
    send({ text });
  });
  updateComposer();
})();
