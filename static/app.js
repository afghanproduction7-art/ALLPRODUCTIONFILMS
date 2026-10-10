(() => {
  "use strict";

  if (window.__APF_APP_STARTED__) return;
  window.__APF_APP_STARTED__ = true;

  const tg = window.Telegram?.WebApp || null;
  const INIT_DATA = tg?.initData || "";
  const API_BASE = "";
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];

  let currentUser = null;
  let toastTimer = null;

  function showMessage(message, isError = false) {
    let box = $("#appMessage");

    if (!box) {
      box = document.createElement("div");
      box.id = "appMessage";
      box.setAttribute("role", "status");
      box.style.cssText =
        "display:block;margin:16px;padding:16px;border-radius:12px;" +
        "background:#202633;color:white;text-align:center;" +
        "direction:rtl;white-space:pre-wrap;overflow-wrap:anywhere;";
      document.body.prepend(box);
    }

    box.style.border = isError
      ? "1px solid #e05252"
      : "1px solid #53657a";

    box.textContent = message;
  }

  function clearMessage() {
    $("#appMessage")?.remove();
  }

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

  async function api(path, options = {}) {
    if (!INIT_DATA) {
      throw new Error(
        "د Telegram د اعتبار معلومات نشته. Mini App د بوټ له اصلي تڼۍ څخه خلاص کړه."
      );
    }

    const headers = new Headers(options.headers || {});
    headers.set("X-Telegram-Init-Data", INIT_DATA);

    if (options.body && !(options.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }

    const response = await fetch(API_BASE + path, {
      ...options,
      headers,
      cache: "no-store"
    });

    const contentType = response.headers.get("content-type") || "";
    let data;

    if (contentType.includes("application/json")) {
      data = await response.json();
    } else {
      const text = await response.text();
      data = { detail: text.slice(0, 250) };
    }

    if (!response.ok) {
      if (response.status === 401) {
        throw new Error(
          "د Telegram اعتبار تایید نه شو. Mini App بنده او بیا یې د بوټ له اصلي تڼۍ خلاص کړه."
        );
      }

      if (response.status === 403 && data?.detail === "JOIN_REQUIRED") {
        showJoinScreen(data.access_channel);
        throw new Error("لومړی اړین چینل کې ګډون وکړه.");
      }

      throw new Error(
        data?.detail || `د سرور تېروتنه: ${response.status}`
      );
    }

    return data;
  }

  function showJoinScreen(channel) {
    const access = $("#accessScreen");

    if (access) {
      access.hidden = false;
      access.style.display = "flex";
    }

    const main = $("#mainApp");

    if (main) {
      main.hidden = true;
      main.style.display = "none";
    }

    const message = $("#accessMessage");

    if (message) {
      message.textContent =
        "د فلمونو د کتلو لپاره لومړی اړین Telegram چینل کې ګډون وکړه، بیا Mini App بېرته خلاص کړه.";
    }

    const button = $("#joinChannel");

    if (button && channel) {
      const name = String(channel)
        .replace(/^@/, "")
        .replace(/^https?:\/\/t\.me\//, "")
        .replace(/\/$/, "");

      button.href = "https://t.me/" + name;
      button.hidden = false;
    }

    clearMessage();
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
    currentUser = user || {};

    setText(
      "#userName",
      currentUser.first_name ||
      currentUser.username ||
      "ګرانه کاروونکی"
    );

    setText("#referralCount", currentUser.referral_count ?? 0);

    const target = Number(currentUser.referral_target || 50);
    const count = Number(currentUser.referral_count || 0);

    setText("#referralProgressText", `${count} / ${target}`);

    const progress = $("#referralProgress");

    if (progress) {
      progress.style.width =
        `${target > 0 ? Math.min(100, count / target * 100) : 0}%`;
    }

    const publish = $("#publishFilm");

    if (publish) {
      publish.disabled = !currentUser.can_publish;
      publish.setAttribute(
        "aria-disabled",
        String(!currentUser.can_publish)
      );
    }
  }

  function renderFilms(films, selector = "#filmGrid") {
    const container = $(selector);
    if (!container) return;

    container.replaceChildren();

    if (!Array.isArray(films) || films.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty-message";
      empty.textContent = "اوس مهال فلمونه ونه موندل شول.";
      container.appendChild(empty);
      return;
    }

    films.forEach((film) => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "film-card";

      card.addEventListener("click", () => {
        openFilm(film.id).catch(handleError);
      });

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
        film.language
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
        const username = String(channel.username || "")
          .replace(/^@/, "");

        const link = document.createElement("a");
        link.className = "channel-item";
        link.href = channel.url || `https://t.me/${username}`;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent =
          channel.title || channel.username || "Telegram چینل";

        container.appendChild(link);
      });

      if (!container.children.length) {
        container.textContent = "تر اوسه چینلونه نه دي ثبت شوي.";
      }
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

      if (!container.children.length) {
        container.textContent = "تر اوسه د بلنو معلومات نشته.";
      }
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
    const film = await api(`/api/films/${encodeURIComponent(id)}`);

    setText("#filmModalTitle", film.title || "فلم");
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
        try {
          const result = await api(
            `/api/films/${encodeURIComponent(id)}/download`
          );

          const url = result.url || result.telegram_url;

          if (!url) {
            toast("د فلم د ډاونلوډ لینک نه دی موجود.");
            return;
          }

          if (tg?.openTelegramLink) {
            tg.openTelegramLink(url);
          } else {
            window.open(url, "_blank", "noopener,noreferrer");
          }
        } catch (error) {
          handleError(error);
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

  function handleError(error) {
    const message = error?.message || "ناڅرګنده تېروتنه رامنځته شوه.";
    console.error("ALL PRODUCTION FILMS:", error);
    showMessage(message, true);
  }

  function bindEvents() {
    $("#searchForm")?.addEventListener("submit", async (event) => {
      event.preventDefault();

      const query = $("#searchInput")?.value.trim();

      try {
        clearMessage();

        if (query) {
          await searchFilms(query);
        } else {
          await loadFilms();
          switchView("home");
        }
      } catch (error) {
        handleError(error);
      }
    });

    $$("[data-nav]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          clearMessage();
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
          handleError(error);
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
        } else if (input) {
          input.focus();
          input.select();

          if (!document.execCommand("copy")) {
            throw new Error("Copy failed");
          }
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
        toast("د فلم خپرولو لپاره د بلنو هدف بشپړ کړه.");
        return;
      }

      const url = "https://t.me/ALL_PRODUCTION_FILMBOT";

      if (tg?.openTelegramLink) {
        tg.openTelegramLink(url);
      } else {
        window.open(url, "_blank", "noopener,noreferrer");
      }
    });

    $("#imageSearchInput")?.addEventListener("change", async (event) => {
      const file = event.target.files?.[0];
      if (!file) return;

      const form = new FormData();
      form.append("image", file);

      try {
        clearMessage();

        const data = await api("/api/films/search-image", {
          method: "POST",
          body: form
        });

        renderFilms(data.films || []);
        switchView("home");
      } catch (error) {
        handleError(error);
      } finally {
        event.target.value = "";
      }
    });
  }

  async function start() {
    try {
      tg?.ready();
      tg?.expand();

      bindEvents();

      if (!INIT_DATA) {
        showMessage(
          "د Telegram اعتبار معلومات نشته.\n\n" +
          "Mini App د Telegram له بوټ څخه خلاص کړه، نه د عادي براوزر له لارې.",
          true
        );
        return;
      }

      showMessage("Mini App پرانیستل کېږي…");

      await loadMe();
      clearMessage();

      // که یوه برخه ناکامه شي، نورې برخې هم د کار کولو هڅه کوي.
      const results = await Promise.allSettled([
        loadFilms(),
        loadReferrals()
      ]);

      const failed = results.find((item) => item.status === "rejected");

      if (failed) {
        handleError(failed.reason);
      }

      switchView("home");
    } catch (error) {
      handleError(error);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
