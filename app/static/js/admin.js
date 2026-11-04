/* Reception dashboard: week calendar (desktop), day list + bottom sheet (phone).
 *
 * Data: GET /api/admin/week?start=YYYY-MM-DD. Live updates: Server-Sent Events
 * from /api/admin/stream; while the stream is down it polls every 3 seconds.
 */
(function () {
  "use strict";

  const root = document.querySelector("[data-admin]");
  if (!root) return;

  const q = (selector) => root.querySelector(selector);
  const els = {
    live: q("[data-live]"),
    liveText: q("[data-live-text]"),
    weekLabel: q("[data-week-label]"),
    head: q("[data-cal-head]"),
    body: q("[data-cal-body]"),
    details: q("[data-details]"),
    dayTabs: q("[data-day-tabs]"),
    dayList: q("[data-day-list]"),
    sheet: q("[data-sheet]"),
    sheetPanel: q("[data-sheet-panel]"),
    toast: q("[data-toast]"),
    toastText: q("[data-toast-text]"),
  };

  const GRID_START = 9 * 60;
  const GRID_END = 19 * 60;
  const PX_PER_MIN = 80 / 60;

  const state = {
    data: null,
    selectedId: null,
    confirming: false,
    phoneDay: null,
    sheetId: null,
    sheetConfirm: false,
    sheetTrigger: null,
    lastSync: 0,
    live: false,
    poll: null,
    toastBooking: null,
    toastTimer: null,
    error: null,
    busy: false,
    flashId: null,
  };

  // --- helpers ---------------------------------------------------------------------------

  const SVG_NS = "http://www.w3.org/2000/svg";
  const ICONS = {
    phone: [["path", { d: "M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2" }]],
    trash: [["path", { d: "M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" }]],
    check: [["path", { d: "M5 12.5l4.5 4.5L19 7.5" }]],
    left: [["path", { d: "M15 6l-6 6 6 6" }]],
    right: [["path", { d: "M9 6l6 6-6 6" }]],
  };

  function icon(name, size, stroke) {
    const svg = document.createElementNS(SVG_NS, "svg");
    const attrs = {
      class: "icon", width: size || 18, height: size || 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
      "stroke-width": stroke || 2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false",
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
      else el.setAttribute(key, value === true ? "" : value);
    });
    (children || []).forEach((child) => {
      if (child == null || child === false) return;
      el.append(child.nodeType ? child : document.createTextNode(String(child)));
    });
    return el;
  }

  const pad = (n) => String(n).padStart(2, "0");
  const hhmm = (minutes) => `${pad(Math.floor(minutes / 60))}:${pad(Math.round(minutes % 60))}`;
  const toMinutes = (text) => {
    const [hours, minutes] = text.split(":").map(Number);
    return hours * 60 + minutes;
  };

  function nowMinutes() {
    if (!state.data) return 0;
    const now = new Date(state.data.now);
    const elapsed = (Date.now() - state.lastSync) / 60000;
    return now.getHours() * 60 + now.getMinutes() + now.getSeconds() / 60 + elapsed;
  }

  function ago(isoUtc) {
    const seconds = Math.max(0, Math.round((Date.now() - Date.parse(isoUtc)) / 1000));
    if (seconds < 60) return `acum ${seconds} s`;
    if (seconds < 3600) return `acum ${Math.floor(seconds / 60)} min`;
    return `acum ${Math.floor(seconds / 3600)} h`;
  }

  const bookingById = (id) => (state.data ? state.data.bookings.find((b) => b.id === id) : null);

  // Legend swatches take their colours from the clinic data (CSSOM, allowed by the CSP).
  root.querySelectorAll(".legend__swatch[data-color]").forEach((el) => {
    el.style.background = el.dataset.color;
    el.style.boxShadow = `inset 0 0 0 1.5px ${el.dataset.accent}`;
  });

  // --- loading ---------------------------------------------------------------------------

  function defaultSelection(data) {
    const live = data.bookings.filter((b) => b.status === "confirmed");
    const assistant = live.filter((b) => b.is_new).sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
    if (assistant.length) return assistant[0].id;
    const upcoming = live.find((b) => !b.is_past);
    return (upcoming || data.bookings[0] || {}).id ?? null;
  }

  function defaultPhoneDay(data) {
    const today = data.days.find((d) => d.is_today);
    return (today || data.days[0]).date;
  }

  async function load(weekStart, options) {
    const opts = options || {};
    const url = "/api/admin/week" + (weekStart ? `?start=${encodeURIComponent(weekStart)}` : "");
    let response;
    try {
      response = await fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin", cache: "no-store" });
    } catch (_) {
      setLive(false);
      return;
    }
    if (response.status === 401) {
      window.location.assign("/admin");
      return;
    }
    if (!response.ok) return;
    const data = await response.json();
    const weekChanged = !state.data || state.data.week_start !== data.week_start;
    state.data = data;
    state.lastSync = Date.now();

    if (opts.select) {
      state.selectedId = opts.select;
      state.confirming = false;
      const chosen = bookingById(opts.select);
      if (chosen) state.phoneDay = chosen.date;
    } else if (!bookingById(state.selectedId)) {
      state.selectedId = defaultSelection(data);
      state.confirming = false;
    }
    if (weekChanged && !opts.select) state.phoneDay = defaultPhoneDay(data);
    if (!data.days.some((d) => d.date === state.phoneDay)) state.phoneDay = defaultPhoneDay(data);
    render();
    tick();
  }

  const weekOf = (isoDate) => {
    const day = new Date(`${isoDate}T12:00:00`);
    const monday = new Date(day);
    monday.setDate(day.getDate() - ((day.getDay() + 6) % 7));
    return `${monday.getFullYear()}-${pad(monday.getMonth() + 1)}-${pad(monday.getDate())}`;
  };

  // --- desktop: week calendar --------------------------------------------------------------

  function render() {
    if (!state.data) return;
    renderToolbar();
    renderCalendar();
    renderDetails();
    renderPhone();
    if (state.sheetId) renderSheet();
  }

  function renderToolbar() {
    const data = state.data;
    els.weekLabel.textContent = data.label;
    q('[data-week-nav="this"]').setAttribute("aria-current", String(data.week_start === data.this_week));
    q('[data-stat="today"]').textContent = data.stats.today;
    q('[data-stat="tomorrow"]').textContent = data.stats.tomorrow;
    q('[data-stat="week"]').textContent = data.stats.week;
  }

  function weekdayLabel(day) {
    if (day.closes && day.closes !== "19:00") return `${day.weekday} · ${day.opens.slice(0, 2)}–${day.closes.slice(0, 2)}`;
    return day.weekday;
  }

  function renderCalendar() {
    const data = state.data;
    const head = [h("div")];
    data.days.forEach((day) => {
      const weekday = h("p", { class: "day-head__weekday" }, [weekdayLabel(day)]);
      if (day.is_today) weekday.append(h("span", { class: "today-pill" }, ["Azi"]));
      head.push(h("div", { class: `day-head${day.is_today ? " day-head--today" : day.is_past ? " day-head--past" : ""}` }, [
        weekday,
        h("p", { class: "day-head__date" }, [
          h("span", { class: "day-head__num" }, [day.day]),
          h("span", { class: "day-head__count" }, [day.count_label]),
        ]),
      ]));
    });
    els.head.replaceChildren(...head);

    const hours = h("div", { class: "hours-col", "aria-hidden": "true" });
    for (let i = 0; i < (GRID_END - GRID_START) / 60; i += 1) {
      const label = h("span", null, [`${pad(9 + i)}:00`]);
      label.style.top = `${4 + 80 * i}px`;
      hours.append(label);
    }
    const columns = [hours];
    const now = nowMinutes();
    data.days.forEach((day) => {
      const column = h("div", { class: `day-col${day.is_past ? " day-col--past" : ""}` });
      if (day.is_today) {
        const offset = Math.min(Math.max((now - GRID_START) * PX_PER_MIN, 0), 800);
        const past = h("div", { class: "day-col__past", "aria-hidden": "true" });
        past.style.height = `${Math.round(offset) + 1}px`;
        column.append(past);
        if (now >= GRID_START && now <= GRID_END) {
          const line = h("div", { class: "now-line", "aria-hidden": "true" }, [h("span", null, [hhmm(Math.floor(now))])]);
          line.style.top = `${Math.round(offset)}px`;
          column.append(line);
        }
      }
      data.bookings
        .filter((b) => b.date === day.date)
        .forEach((b) => column.append(blockButton(b)));
      if (day.closes && toMinutes(day.closes) < GRID_END) {
        const band = h("div", { class: "closed-band" }, [h("span", null, [`Închis de la ${day.closes}`])]);
        band.style.top = `${Math.round((toMinutes(day.closes) - GRID_START) * PX_PER_MIN) + 1}px`;
        column.append(band);
      }
      columns.push(column);
    });
    els.body.replaceChildren(...columns);
  }

  function blockButton(b) {
    const cancelled = b.status === "cancelled";
    const past = !cancelled && b.is_past;
    const classes = ["block"];
    if (b.is_new && !cancelled) classes.push("block--new");
    if (past) classes.push("block--past");
    if (cancelled) classes.push("block--cancelled");
    if (b.id === state.flashId) classes.push("block--flash");
    const tall = b.end_min - b.start_min >= 45;
    const button = h("button", {
      type: "button",
      class: classes.join(" "),
      "aria-pressed": String(b.id === state.selectedId),
      "aria-label": `${b.start}–${b.end}, ${b.service_short}, ${b.patient_name}${cancelled ? ", anulată" : past ? ", încheiată" : ""}`,
    }, [
      h("span", { class: "block__line block__line--main" }, [`${b.start} · ${b.service_short}`]),
      h("span", { class: "block__line block__line--name" }, [b.patient_name]),
      b.is_new && !cancelled && tall ? h("span", { class: "block__pill" }, ["Nouă · asistent"]) : null,
    ]);
    button.style.top = `${Math.round((b.start_min - GRID_START) * PX_PER_MIN) + 1}px`;
    button.style.height = `${Math.round((b.end_min - b.start_min) * PX_PER_MIN) - 3}px`;
    button.style.background = cancelled ? "#FFFFFF" : past ? "#EEF2F0" : b.color;
    button.addEventListener("click", () => select(b.id));
    return button;
  }

  function select(id) {
    state.selectedId = id;
    state.confirming = false;
    state.error = null;
    renderCalendar();
    renderDetails();
  }

  // --- details panel ---------------------------------------------------------------------------

  function rows(pairs) {
    const dl = h("dl", { class: "details__rows" });
    pairs.forEach(([label, value, numeric]) => {
      dl.append(h("dt", null, [label]));
      dl.append(h("dd", { class: numeric ? "num" : null }, [value]));
    });
    return dl;
  }

  function stateNote(kind, text, withIcon) {
    return h("p", { class: `state-note state-note--${kind}`, role: "status" }, [withIcon ? icon("check", 18, 2.6) : null, h("span", null, text)]);
  }

  function confirmBox(b, onKeep) {
    const yes = h("button", { type: "button", class: "btn btn--danger" }, ["Da, anulează"]);
    const no = h("button", { type: "button", class: "btn btn--outline" }, ["Renunță"]);
    yes.addEventListener("click", () => cancelBooking(b));
    no.addEventListener("click", onKeep);
    return h("div", { class: "confirm-box", role: "group", "aria-labelledby": "confirm-title" }, [
      h("p", { class: "confirm-box__title", id: "confirm-title" }, ["Anulezi programarea?"]),
      h("p", null, ["Locul devine imediat liber în chat. Pacientul nu e anunțat automat, așa că sună-l tu."]),
      h("div", { class: "confirm-box__buttons" }, [yes, no]),
    ]);
  }

  function renderDetails() {
    const b = bookingById(state.selectedId);
    if (!b) {
      els.details.replaceChildren(h("p", { class: "details__empty" }, ["Alege o programare din calendar ca să vezi detaliile."]));
      return;
    }
    const nodes = [];
    if (b.is_new && b.status === "confirmed") {
      nodes.push(h("p", { class: "new-pill", "data-ago": b.created_at }, [`Nouă · prin asistent · ${ago(b.created_at)}`]));
    }
    nodes.push(h("p", { class: "details__name" }, [b.patient_name]));
    nodes.push(rows([
      ["Serviciu", `${b.service_name} · ${b.duration_min} min`],
      ["Data", b.date_label],
      ["Ora", `${b.start}–${b.end}`, true],
      ["Telefon", h("a", { href: `tel:${b.tel}` }, [b.phone])],
      ["Preț", b.price_label],
      ["Sursă", b.source_label],
      ["Cod", b.code],
    ]));
    if (b.status === "cancelled") {
      nodes.push(stateNote("ok", [h("strong", null, ["Programare anulată."]), " Locul e din nou liber în chat."], true));
    } else if (!b.can_cancel) {
      nodes.push(stateNote("muted", ["Programare încheiată. Nu mai poate fi anulată."]));
    } else if (state.confirming) {
      nodes.push(confirmBox(b, () => {
        state.confirming = false;
        renderDetails();
      }));
    } else {
      const cancel = h("button", { type: "button", class: "btn btn--danger-line" }, [icon("trash", 18), "Anulează programarea"]);
      cancel.addEventListener("click", () => {
        state.confirming = true;
        state.error = null;
        renderDetails();
        els.details.querySelector(".confirm-box .btn--danger").focus();
      });
      nodes.push(h("div", { class: "details__actions" }, [
        h("a", { class: "btn btn--line", href: `tel:${b.tel}` }, [icon("phone", 18, 1.9), "Sună pacientul"]),
        cancel,
      ]));
    }
    if (state.error) nodes.push(stateNote("error", [state.error]));
    els.details.replaceChildren(...nodes);
  }

  async function cancelBooking(b) {
    if (state.busy) return;
    state.busy = true;
    try {
      const response = await fetch(`/api/admin/bookings/${b.id}/cancel`, {
        method: "POST", headers: { Accept: "application/json" }, credentials: "same-origin",
      });
      if (response.status === 401) {
        window.location.assign("/admin");
        return;
      }
      const data = await response.json().catch(() => ({}));
      state.error = response.ok ? null : data.message || data.detail || "Nu am putut anula programarea.";
    } catch (_) {
      state.error = "Nu am putut anula programarea. Verifică conexiunea.";
    } finally {
      state.busy = false;
      state.confirming = false;
      state.sheetConfirm = false;
    }
    await load(state.data.week_start);
  }

  // --- phone: day tabs, list and bottom sheet ----------------------------------------------------

  function weekButton(direction) {
    const button = h("button", {
      type: "button", class: "day-tabs__week",
      "aria-label": direction === "prev" ? "Săptămâna anterioară" : "Săptămâna următoare",
    }, [icon(direction === "prev" ? "left" : "right", 18, 2.2)]);
    button.addEventListener("click", () => load(direction === "prev" ? state.data.prev : state.data.next));
    return button;
  }

  function renderPhone() {
    const data = state.data;
    const tabs = [weekButton("prev")];
    data.days.forEach((day) => {
      const tab = h("button", {
        type: "button",
        class: `day-tab${day.is_today ? " day-tab--today" : day.is_past ? " day-tab--past" : ""}`,
        "aria-current": day.date === state.phoneDay ? "true" : null,
        "aria-label": `${day.label}, ${day.count_label}`,
      }, [h("span", null, [day.is_today ? "Azi" : day.weekday]), h("span", null, [day.day])]);
      tab.addEventListener("click", () => {
        state.phoneDay = day.date;
        renderPhone();
      });
      tabs.push(tab);
    });
    tabs.push(weekButton("next"));
    els.dayTabs.replaceChildren(...tabs);

    const day = data.days.find((d) => d.date === state.phoneDay) || data.days[0];
    const items = data.bookings.filter((b) => b.date === day.date).sort((a, b) => a.start_min - b.start_min);
    const active = items.filter((b) => b.status === "confirmed");
    const fresh = active.filter((b) => b.is_new).length;
    const summary = active.length
      ? `${day.count_label}${fresh ? ` · ${fresh} ${fresh === 1 ? "nouă" : "noi"} prin asistent` : ""}`
      : day.closes ? "Nicio programare încă" : "Închis";

    const list = h("ul", { class: "day-list__items" });
    if (day.opens) {
      const entries = [];
      let cursor = toMinutes(day.opens);
      active.forEach((b) => {
        if (b.start_min > cursor) entries.push({ gap: [cursor, b.start_min], at: cursor });
        cursor = Math.max(cursor, b.end_min);
      });
      if (cursor < toMinutes(day.closes)) entries.push({ gap: [cursor, toMinutes(day.closes)], at: cursor });
      items.forEach((b) => entries.push({ booking: b, at: b.start_min }));
      entries.sort((a, b) => a.at - b.at || (a.gap ? -1 : 1));
      entries.forEach((entry) => {
        if (entry.gap) {
          list.append(h("li", { class: "free-row" }, [`${hhmm(entry.gap[0])}–${hhmm(entry.gap[1])} · liber`]));
        } else {
          list.append(h("li", null, [rowButton(entry.booking)]));
        }
      });
    }

    els.dayList.replaceChildren(
      h("div", null, [
        h("h1", { class: "day-list__title" }, [day.label]),
        h("p", { class: "day-list__sub" }, [summary]),
      ]),
      list,
    );
  }

  function rowButton(b) {
    const cancelled = b.status === "cancelled";
    const past = !cancelled && b.is_past;
    const classes = ["row-button"];
    if (b.is_new && !cancelled) classes.push("row-button--new");
    if (cancelled) classes.push("row-button--cancelled");
    if (past) classes.push("row-button--past");
    const button = h("button", { type: "button", class: classes.join(" ") }, [
      h("span", { class: "row-button__time" }, [h("span", null, [b.start]), h("span", null, [b.end])]),
      h("span", { class: "row-button__text" }, [
        h("span", { class: "row-button__name" }, [b.patient_name]),
        h("span", { class: "row-button__meta" }, [`${b.service_label} · ${b.duration_min} min${cancelled ? " · anulată" : ""}`]),
      ]),
      b.is_new && !cancelled ? h("span", { class: "row-button__new" }, ["Nouă"]) : null,
      icon("right", 18, 2.2),
    ]);
    button.style.background = b.color;
    button.addEventListener("click", () => openSheet(b.id, button));
    return button;
  }

  function openSheet(id, trigger) {
    state.sheetId = id;
    state.sheetConfirm = false;
    state.sheetTrigger = trigger || null;
    state.error = null;
    renderSheet();
    els.sheet.hidden = false;
    document.documentElement.classList.add("chat-locked");
    els.sheetPanel.querySelector("button, a")?.focus();
  }

  function closeSheet() {
    els.sheet.hidden = true;
    state.sheetId = null;
    document.documentElement.classList.remove("chat-locked");
    if (state.sheetTrigger && state.sheetTrigger.isConnected) state.sheetTrigger.focus();
  }

  function renderSheet() {
    const b = bookingById(state.sheetId);
    if (!b) {
      closeSheet();
      return;
    }
    const info = h("div", { class: "sheet__info" }, [
      h("p", null, [b.patient_name]),
      h("p", null, [`${b.service_label} · ${b.date_short}, ${b.start}–${b.end}`]),
      h("p", null, [`${b.phone} · ${b.code}`]),
    ]);
    info.style.background = b.color;
    const nodes = [h("span", { class: "sheet__grabber", "aria-hidden": "true" })];
    const close = h("button", { type: "button", class: "btn btn--outline" }, ["Închide"]);
    close.addEventListener("click", closeSheet);

    if (state.sheetConfirm) {
      const yes = h("button", { type: "button", class: "btn btn--danger" }, ["Da, anulează"]);
      const no = h("button", { type: "button", class: "btn btn--outline" }, ["Renunță"]);
      yes.addEventListener("click", () => cancelBooking(b));
      no.addEventListener("click", () => {
        state.sheetConfirm = false;
        renderSheet();
      });
      nodes.push(
        h("h2", { class: "sheet__title", id: "sheet-title" }, ["Anulezi programarea?"]),
        info,
        h("p", { class: "sheet__text" }, ["Locul devine imediat liber în chat. Pacientul nu e anunțat automat, așa că sună-l tu."]),
        h("div", { class: "sheet__buttons" }, [yes, no]),
      );
    } else {
      nodes.push(h("h2", { class: "sheet__title", id: "sheet-title" }, ["Detalii programare"]), info);
      nodes.push(h("p", { class: "sheet__text" }, [`${b.source_label} · ${b.price_label}`]));
      const buttons = [];
      if (b.status === "cancelled") {
        nodes.push(stateNote("ok", [h("strong", null, ["Programare anulată."]), " Locul e din nou liber în chat."], true));
      } else if (!b.can_cancel) {
        nodes.push(stateNote("muted", ["Programare încheiată. Nu mai poate fi anulată."]));
      } else {
        const cancel = h("button", { type: "button", class: "btn btn--danger-line" }, [icon("trash", 18), "Anulează programarea"]);
        cancel.addEventListener("click", () => {
          state.sheetConfirm = true;
          renderSheet();
          els.sheetPanel.querySelector(".btn--danger")?.focus();
        });
        buttons.push(h("a", { class: "btn btn--line", href: `tel:${b.tel}` }, [icon("phone", 18, 1.9), "Sună pacientul"]), cancel);
      }
      buttons.push(close);
      nodes.push(h("div", { class: "sheet__buttons" }, buttons));
    }
    if (state.error) nodes.push(stateNote("error", [state.error]));
    els.sheetPanel.replaceChildren(...nodes);
  }

  els.sheet.addEventListener("click", (event) => {
    if (event.target.closest("[data-sheet-close]")) closeSheet();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !els.sheet.hidden) closeSheet();
  });

  // --- live updates -----------------------------------------------------------------------------

  function setLive(live) {
    state.live = live;
    tick();
  }

  function tick() {
    if (!state.data) return;
    const seconds = Math.max(0, Math.round((Date.now() - state.lastSync) / 1000));
    const age = seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min`;
    els.live.classList.toggle("is-offline", !state.live);
    els.liveText.replaceChildren(state.live ? "Live" : "Reconectare…", h("span", { class: "hide-sm" }, [` · actualizat acum ${age}`]));
    const pill = els.details.querySelector("[data-ago]");
    if (pill) pill.textContent = `Nouă · prin asistent · ${ago(pill.dataset.ago)}`;
  }

  function startPolling() {
    if (!state.poll) state.poll = setInterval(() => load(state.data && state.data.week_start), 3000);
  }

  function stopPolling() {
    clearInterval(state.poll);
    state.poll = null;
  }

  function showToast(booking) {
    state.toastBooking = booking;
    els.toastText.textContent = `${booking.patient_name} · ${booking.service_label} · ${booking.when_label}`;
    els.toast.hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => { els.toast.hidden = true; }, 15000);
  }

  q("[data-toast-close]").addEventListener("click", () => { els.toast.hidden = true; });
  q("[data-toast-open]").addEventListener("click", async () => {
    const b = state.toastBooking;
    els.toast.hidden = true;
    if (!b) return;
    state.flashId = b.id;
    await load(weekOf(b.start.slice(0, 10)), { select: b.id });
    const block = els.body.querySelector('.block[aria-pressed="true"]');
    if (block && block.offsetParent) block.focus();
    else openSheet(b.id);
    setTimeout(() => { state.flashId = null; }, 4000);
  });

  function connect() {
    if (!("EventSource" in window)) {
      startPolling();
      return;
    }
    const source = new EventSource("/api/admin/stream");
    source.addEventListener("open", () => {
      setLive(true);
      stopPolling();
      load(state.data && state.data.week_start);
    });
    source.addEventListener("booking.created", (event) => {
      const booking = JSON.parse(event.data).booking;
      load(state.data && state.data.week_start);
      if (booking.source !== "seed") showToast(booking);
    });
    source.addEventListener("booking.cancelled", () => load(state.data && state.data.week_start));
    source.addEventListener("reset", () => load(state.data && state.data.week_start));
    source.addEventListener("error", () => {
      setLive(false);
      startPolling();
    });
  }

  root.querySelectorAll("[data-week-nav]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!state.data) return;
      const target = { prev: state.data.prev, next: state.data.next, this: state.data.this_week }[button.dataset.weekNav];
      load(target);
    });
  });

  setInterval(tick, 1000);
  setInterval(() => load(state.data && state.data.week_start), 60000); // move the "now" line
  load(null).then(connect);
})();
