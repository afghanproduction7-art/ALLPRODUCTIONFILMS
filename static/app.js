
(() => {
  "use strict";

  const tg = window.Telegram?.WebApp;
  const INIT_DATA = tg?.initData || "";
  const API_BASE = "";

  let currentUser = null;
  let toastTimer = null;

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) =>
    Array.from(root.querySelectorAll(selector));

  function toast(message) {
    let el = $("#toast");

    if (!el) {
      el = document.createElement("div");
      el.id = "toast";
      el.className = "toast";
      el.setAttribute("role", "status");
      document.body.appendChild(el);
    }

    el.textContent = message;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 3000);
  }

  function showError(message) {
    let el = $("#authError");

    if (!el) {
      el = document.createElement("div");
      el.id = "authError";
      el.setAttribute("role", "alert");
      el.style.cssText =
        "margin:16px;padding:16px;border-radius:12px;background:#202633;color:white;text-align:center;direction:rtl";
      document.body.prepend(el);
    }

    el.textContent = message;
  }

  async function api(path, options = {}) {
    if (!INIT_DATA) {
      showError(
        "د Telegram د اعتبار معلومات نشته. Mini App د Telegram له اصلي تڼۍ څخه خلاص کړئ."
      );
      throw new Error("TELEGRAM_INIT_DATA_MISSING");
    }

    const headers = new Headers(options.headers || {});
    headers.set("X-Telegram-Init-Data", INIT_DATA);

    if (options.body && !(options.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }

    const response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
      cache: "no-store",
    });

    const type = response.headers.get("content-type") || "";
    const data = type.includes("application/json")
      ? await response.json()
      : await response.text();

    if (!response.ok) {
      const detail =
        data && typeof data === "object" ? data.detail || "" : "";

      if (response.status === 401) {
        showError(
          "د Telegram اعتبار تایید نه شو. Mini App بنده او بیا یې د Telegram له اصلي تڼۍ خلاص کړئ."
        );
      } else if (response.status === 403 && detail === "JOIN_REQUIRED") {
        showJoinScreen(data.access_channel);
      } else {
        toast(detail || `د سرور خطا: ${response.status}`);
      }

      throw new Error(detail || `HTTP_${response.status}`);
    }

    return data;
  }

  function showJoinScreen(channel) {
    const screen = $("#accessScreen");
    if (screen) {
      screen.hidden = false;
      screen.style.display = "flex";
    }

    const message = $("#accessMessage");
    if (message) {
      message.textContent =
        "د فلمونو د کتلو لپاره لومړی اړین Telegram چینل کې ګډون وکړئ.";
    }

    const button = $("#joinChannel");
    if (button && channel) {
      const name = String(channel).replace(/^@/, "").replace(
        "https://t.me/",
        ""
      );
      button.href = `https://t.me/${name}`;
      button.hidden = false;
    }
  }

  function showMain() {
    const access = $("#accessScreen");
    if (access) {
      access.hidden = true;
      access.style.display = "none";
    }

    const main = $("#mainApp");
    if (main) {
      main.hidden = false;
      main.style.display = "";
    }
  }

  function setText(selector, value) {
    const el = $(selector);
    if (el) el.textContent = value ?? "";
  }

  function setUser(user) {
    currentUser = user;

    setText(
      "#userName",
      user.first_name || user.username || "ګرانه کاروونکی"
    );
    setText("#referralCount", user.referral_count ?? 0);

    const target = Number(user.referral_target || 50);
    const count = Number(user.referral_count || 0);
    const percent = target > 0
      ? Math.min(100, (count / target) * 100)
      : 0;

    setText("#referralProgressText", `${count} / ${target}`);

    const progress = $("#referralProgress");
    if (progress) progress.style.width = `${percent}%`;

    const publish = $("#publishFilm");
    if (publish) {
      publish.disabled = !user.can_publish;
      publish.setAttribute("aria-disabled", String(!user.can_publish));
    }
  }

  function renderFilms(films, selector = "#filmGrid") {
    const container = $(selector);
    if (!container) return;

    container.replaceChildren();

    if (!Array.isArray(films) || !films.length) {
      const empty = document.createElement("p");
      empty.className = "empty-message";
      empty.textContent = "فلمونه ونه موندل شول.";
      container.appendChild(empty);
      return;
    }

    films.forEach((film) => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "film-card";
      card.addEventListener("click", () => openFilm(film.id));

      if (film.poster_url) {
        const img = document.createElement("img");
        img.className = "film-poster";
        img.src = film.poster_url;
        img.alt = film.title || "فلم";
        img.loading = "lazy";
        img.onerror = () => img.remove();
        card.appendChild(img);
      }

      const title = document.createElement("div");
      title.className = "film-title";
      title.textContent = film.title || "بې نومه فلم";
      card.appendChild(title);

      const info = document.createElement("div");
      info.className = "film-info";
      info.textContent = [
        film.year,
        film.quality,
        film.language,
      ].filter(Boolean).join(" • ");
      card.appendChild(info);

      container.appendChild(card);
    });
  }

  async function loadMe() {
    const user = await api("/api/me");
    setUser(user);
    showMain();
    return user;
  }

  async function loadFilms() {
    const data = await api("/api/films/latest");
    renderFilms(data.films || []);
  }

  async function loadOfficial() {
    const data = await api("/api/films/official");
    renderFilms(data.films || []);
    switchView("official");
  }

  async function searchFilms(query) {
    const data = await api(
      `/api/films/search?q=${encodeURIComponent(query)}`
    );
    renderFilms(data.films || []);
    switchView("home");
  }

  async function loadChannels() {
    const data = await api("/api/channels");
    const container = $("#channelsList");

    if (container) {
      container.replaceChildren();

      (data.channels || []).forEach((channel) => {
        const link = document.createElement("a");
        link.className = "channel-item";
        link.href = channel.url ||
          `https://t.me/${String(channel.username || "").replace(/^@/, "")}`;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = channel.title || channel.username || "Telegram چینل";
        container.appendChild(link);
      });
    }

    switchView("channels");
  }

  async function loadLeaders() {
    const data = await api("/api/leaders");
    const container = $("#leadersList");

    if (container) {
      container.replaceChildren();

      (data.leaders || []).forEach((leader, index) => {
        const row = document.createElement("div");
        row.className = "leader-item";

        const name = document.createElement("span");
        name.textContent =
          `${index + 1}. ${leader.first_name || leader.username || "کاروونکی"}`;

        const count = document.createElement("span");
        count.textContent = `${leader.referral_count || 0} بلنې`;

        row.append(name, count);
        container.appendChild(row);
      });
    }

    const modal = $("#leadersModal");
    if (modal) modal.hidden = false;
  }

  async function loadReferrals() {
    const data = await api("/api/referrals");
    const link = $("#referralLink");

    if (link && data.referral_link) {
      link.value = data.referral_link;
    }

    setText(
      "#referralCount",
      data.referral_count ?? data.count ?? currentUser?.referral_count ?? 0
    );

    return data;
  }

  async function openFilm(id) {
    const film = await api(`/api/films/${id}`);

    setText("#filmModalTitle", film.title);
    setText("#filmModalDescription", film.description || "");
    setText(
      "#filmModalInfo",
      [film.year, film.quality, film.genre, film.language]
        .filter(Boolean).join(" • ")
    );

    const modal = $("#filmModal");
    if (modal) modal.hidden = false;

    const download = $("#downloadFilm");
    if (download) {
      download.onclick = async () => {
        const result = await api(`/api/films/${id}/download`);
        const url = result.url || result.telegram_url;

        if (!url) {
          toast("د ډاونلوډ لینک نشته.");
          return;
        }

        if (tg?.openTelegramLink) {
          tg.openTelegramLink(url);
        } else {
          window.open(url, "_blank", "noopener,noreferrer");
        }
      };
    }
  }

  function switchView(view) {
    $$(".view").forEach((el) => {
      el.hidden = el.dataset.view !== view;
    });

    $$("[data-nav]").forEach((el) => {
      el.classList.toggle("active", el.dataset.nav === view);
    });
  }

  function bindEvents() {
    const searchForm = $("#searchForm");

    searchForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const query = $("#searchInput")?.value.trim();

      try {
        if (query) {
          await searchFilms(query);
        } else {
          await loadFilms();
          switchView("home");
        }
      } catch (error) {
        console.error("Search failed:", error);
      }
    });

    $$("[data-nav]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          const view = button.dataset.nav;

          if (view === "official") {
            await loadOfficial();
          } else if (view === "channels") {
            await loadChannels();
          } else if (view === "leaders") {
            await loadLeaders();
          } else {
            await loadFilms();
            switchView("home");
          }
        } catch (error) {
          console.error("Navigation failed:", error);
        }
      });
    });

    $("#copyReferralLink")?.addEventListener("click", async () => {
      const input = $("#referralLink");
      const value = input?.value;

      if (!value) {
        toast("د بلنې لینک نشته.");
        return;
      }

      try {
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(value);
        } else {
          input.focus();
          input.select();
          if (!document.execCommand("copy")) throw new Error("Copy failed");
        }
        toast("د بلنې لینک کاپي شو.");
      } catch {
        toast("لینک انتخاب او په لاس یې کاپي کړه.");
      }
    });

    $("#closeFilmModal")?.addEventListener("click", () => {
      const modal = $("#filmModal");
      if (modal) modal.hidden = true;
    });

    $("#closeLeadersModal")?.addEventListener("click", () => {
      const modal = $("#leadersModal");
      if (modal) modal.hidden = true;
    });

    $("#publishFilm")?.addEventListener("click", () => {
      if (!currentUser?.can_publish) {
        toast("د فلم خپرولو لپاره د بلنې هدف بشپړ کړه.");
        return;
      }

      if (tg?.openTelegramLink) {
        tg.openTelegramLink("https://t.me/ALL_PRODUCTION_FILMBOT");
      } else {
        window.open(
          "https://t.me/ALL_PRODUCTION_FILMBOT",
          "_blank",
          "noopener,noreferrer"
        );
      }
    });

    $("#imageSearchInput")?.addEventListener("change", async (event) => {
      const file = event.target.files?.[0];
      if (!file) return;

      const form = new FormData();
      form.append("image", file);

      try {
        const data = await api("/api/films/search-image", {
          method: "POST",
          body: form,
        });
        renderFilms(data.films || []);
        switchView("home");
      } catch (error) {
        console.error("Image search failed:", error);
      } finally {
        event.target.value = "";
      }
    });
  }

  async function start() {
    tg?.ready();
    tg?.expand();

    bindEvents();

    if (!INIT_DATA) {
      showError(
        "Mini App د Telegram له اصلي تڼۍ څخه خلاص کړئ؛ د اعتبار معلومات نشته."
      );
      return;
    }

    try {
      await loadMe();
      await Promise.allSettled([loadFilms(), loadReferrals()]);
      switchView("home");
    } catch (error) {
      console.error("Mini App initialization failed:", error);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
