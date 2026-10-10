(() => {
  "use strict";

  const tg = window.Telegram?.WebApp;
  const INIT_DATA = tg?.initData || "";
  const API_BASE = "";
  const BOT_URL = "https://t.me/ALL_PRODUCTION_FILMBOT";

  let currentUser = null;
  let toastTimer = null;

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) =>
    Array.from(root.querySelectorAll(selector));

  function toast(message) {
    const el = $("#toast");
    if (!el) return;

    el.textContent = message;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 3000);
  }

  function showError(message) {
    let el = $("#appError");

    if (!el) {
      el = document.createElement("div");
      el.id = "appError";
      el.setAttribute("role", "alert");
      el.style.cssText =
        "margin:16px;padding:16px;border-radius:12px;background:#202633;color:white;text-align:center;direction:rtl;line-height:1.8";
      document.body.prepend(el);
    }

    el.textContent = message;
  }

  async function api(path, options = {}) {
    if (!INIT_DATA) {
      throw new Error(
        "د Telegram د اعتبار معلومات نشته. Mini App د Telegram له اصلي تڼۍ څخه خلاص کړئ."
      );
    }

    const headers = new Headers(options.headers || {});
    headers.set("X-Telegram-Init-Data", INIT_DATA);

    if (options.body && !(options.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }

    const response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
      cache: "no-store"
    });

    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json")
      ? await response.json()
      : await response.text();

    if (!response.ok) {
      if (response.status === 403) {
        showJoinScreen(data?.access_channel);
        throw new Error("JOIN_REQUIRED");
      }

      if (response.status === 401) {
        throw new Error(
          "د Telegram اعتبار تایید نه شو. Mini App له Telegram څخه بیا خلاص کړئ."
        );
      }

      throw new Error(
        typeof data === "object"
          ? data.detail || `د سرور خطا: ${response.status}`
          : `د سرور خطا: ${response.status}`
      );
    }

    return data;
  }

  function showJoinScreen(channel) {
    const screen = $("#accessScreen");
    const main = $("#mainApp");

    if (screen) {
      screen.classList.remove("hidden");
      screen.hidden = false;
      screen.style.display = "flex";
    }

    if (main) {
      main.classList.add("hidden");
      main.hidden = true;
    }

    const button = $("#joinChannelBtn");
    if (button) {
      button.onclick = () => {
        const name = String(channel || "ALL_PASHTO")
          .replace(/^@/, "")
          .replace(/^https?:\/\/t\.me\//, "")
          .replace(/\/$/, "");

        const url = `https://t.me/${name}`;

        if (tg?.openTelegramLink) {
          tg.openTelegramLink(url);
        } else {
          window.open(url, "_blank", "noopener,noreferrer");
        }
      };
    }
  }

  function showMain() {
    const screen = $("#accessScreen");
    const main = $("#mainApp");

    if (screen) {
      screen.classList.add("hidden");
      screen.hidden = true;
      screen.style.display = "none";
    }

    if (main) {
      main.classList.remove("hidden");
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

    const name =
      currentUser.first_name ||
      currentUser.username ||
      "ګرانه کاروونکی";

    setText("#userName", name);
    setText("#welcomeName", name);
    setText(
      "#userUsername",
      currentUser.username ? `@${String(currentUser.username).replace(/^@/, "")}` : ""
    );

    const count = Number(currentUser.referral_count || 0);
    const target = Math.max(1, Number(currentUser.referral_target || 50));
    const remaining = Math.max(0, target - count);
    const percent = Math.min(100, (count / target) * 100);

    setText("#referralCount", count);
    setText("#referralTarget", target);
    setText("#referralRemaining", remaining);

    const progress = $("#referralProgress");
    if (progress) progress.style.width = `${percent}%`;

    const publish = $("#publishBtn");
    if (publish) {
      publish.disabled = !currentUser.can_publish;
      publish.setAttribute(
        "aria-disabled",
        String(!currentUser.can_publish)
      );
    }

    setText(
      "#publishStatus",
      currentUser.can_publish
        ? "✅ تاسو د فلم خپرولو اجازه لرئ."
        : `🔒 د نشر لپاره ${remaining} نورې بلنې پکار دي.`
    );
  }

  function switchView(view) {
    const grid = $("#filmsGrid");
    const channels = $(".channels-section");
    const title = $("#sectionTitle");

    if (grid) grid.style.display = view === "channels" ? "none" : "";
    if (channels) channels.style.display = view === "channels" ? "" : "none";

    const titles = {
      home: "وروستي فلمونه",
      official: "رسمي فلمونه",
      channels: "زموږ چینلونه"
    };

    if (title) title.textContent = titles[view] || "وروستي فلمونه";

    $$("[data-view]").forEach((button) => {
      button.classList.toggle("active", button.dataset.view === view);
    });
  }

  function renderFilms(films) {
    const container = $("#filmsGrid");
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

      if (film.poster_url) {
        const image = document.createElement("img");
        image.className = "film-poster";
        image.src = film.poster_url;
        image.alt = film.title || "فلم";
        image.loading = "lazy";
        image.onerror = () => image.remove();
        card.appendChild(image);
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

      card.addEventListener("click", () => openFilm(film.id));
      container.appendChild(card);
    });
  }

  async function loadLatest() {
    try {
      const data = await api("/api/films/latest");
      renderFilms(data.films || []);
      switchView("home");
    } catch (error) {
      toast(error.message || "د فلمونو په راوړلو کې ستونزه ده.");
    }
  }

  async function loadOfficial() {
    try {
      const data = await api("/api/films/official");
      renderFilms(data.films || []);
      switchView("official");
    } catch (error) {
      toast(error.message || "رسمي فلمونه نه پورته کېږي.");
    }
  }

  async function loadCategory(category) {
    try {
      const data = await api(
        `/api/films/category?category=${encodeURIComponent(category)}`
      );
      renderFilms(data.films || []);
      setText("#sectionTitle", category);
      switchView("home");
    } catch (error) {
      toast(error.message || "د کټګورۍ فلمونه نه پورته کېږي.");
    }
  }

  async function searchFilms(query) {
    try {
      const data = await api(
        `/api/films/search?q=${encodeURIComponent(query)}`
      );
      renderFilms(data.films || []);
      switchView("home");
    } catch (error) {
      toast(error.message || "د لټون پر مهال ستونزه رامنځته شوه.");
    }
  }

  async function loadChannels() {
    try {
      const data = await api("/api/channels");
      const container = $("#channelsList");

      if (container) {
        container.replaceChildren();

        (data.channels || []).forEach((channel) => {
          const link = document.createElement("a");
          link.className = "channel-item";
          link.href =
            channel.url ||
            `https://t.me/${String(channel.username || "").replace(/^@/, "")}`;
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
    } catch (error) {
      toast(error.message || "چینلونه نه پورته کېږي.");
    }
  }

  async function loadReferrals() {
    const data = await api("/api/referrals");

    const input = $("#referralLink");
    if (input && data.referral_link) input.value = data.referral_link;

    const count = Number(
      data.referral_count ?? data.count ?? currentUser?.referral_count ?? 0
    );
    setText("#referralCount", count);

    const target = Math.max(
      1,
      Number(data.referral_target || currentUser?.referral_target || 50)
    );
    setText("#referralTarget", target);
    setText("#referralRemaining", Math.max(0, target - count));

    const progress = $("#referralProgress");
    if (progress) {
      progress.style.width = `${Math.min(100, (count / target) * 100)}%`;
    }

    return data;
  }

  async function loadLeaders() {
    try {
      const data = await api("/api/leaders");
      let modal = $("#leadersModal");

      if (!modal) return;

      modal.replaceChildren();
      modal.classList.add("modal-open");
      modal.hidden = false;
      modal.style.display = "flex";

      const card = document.createElement("div");
      card.className = "modal-card";

      const heading = document.createElement("h2");
      heading.textContent = "🏆 د ریفرل مخکښان";
      card.appendChild(heading);

      const close = document.createElement("button");
      close.type = "button";
      close.textContent = "✖ بندول";
      close.addEventListener("click", () => {
        modal.hidden = true;
        modal.style.display = "none";
      });
      card.appendChild(close);

      const leaders = data.leaders || [];
      if (!leaders.length) {
        const empty = document.createElement("p");
        empty.textContent = "تر اوسه معلومات نشته.";
        card.appendChild(empty);
      }

      leaders.forEach((leader, index) => {
        const row = document.createElement("p");
        row.textContent =
          `${index + 1}. ${leader.first_name || leader.username || "کاروونکی"} — ${leader.referral_count || 0} بلنې`;
        card.appendChild(row);
      });

      modal.appendChild(card);
    } catch (error) {
      toast(error.message || "د مخکښانو معلومات نه پورته کېږي.");
    }
  }

  async function openFilm(id) {
    try {
      const film = await api(`/api/films/${id}`);
      let modal = $("#filmModal");
      if (!modal) return;

      modal.replaceChildren();
      modal.hidden = false;
      modal.style.display = "flex";
      modal.classList.add("modal-open");

      const card = document.createElement("div");
      card.className = "modal-card";

      const heading = document.createElement("h2");
      heading.textContent = film.title || "فلم";
      card.appendChild(heading);

      const info = document.createElement("p");
      info.textContent = [
        film.year,
        film.quality,
        film.genre,
        film.language
      ].filter(Boolean).join(" • ");
      card.appendChild(info);

      const description = document.createElement("p");
      description.textContent = film.description || "";
      card.appendChild(description);

      const download = document.createElement("button");
      download.type = "button";
      download.className = "primary-btn";
      download.textContent = "▶ فلم ترلاسه کړئ";
      download.addEventListener("click", async () => {
        try {
          const result = await api(`/api/films/${id}/download`);
          const url = result.url || result.telegram_url;

          if (!url) {
            toast("د فلم لینک نشته.");
            return;
          }

          if (tg?.openTelegramLink) tg.openTelegramLink(url);
          else window.open(url, "_blank", "noopener,noreferrer");
        } catch (error) {
          toast(error.message || "فلم نه ترلاسه کېږي.");
        }
      });
      card.appendChild(download);

      const close = document.createElement("button");
      close.type = "button";
      close.className = "secondary-btn";
      close.textContent = "بندول";
      close.addEventListener("click", () => {
        modal.hidden = true;
        modal.style.display = "none";
      });
      card.appendChild(close);

      modal.appendChild(card);
    } catch (error) {
      toast(error.message || "فلم نه پرانیستل کېږي.");
    }
  }

  function openTelegram(url) {
    if (tg?.openTelegramLink) tg.openTelegramLink(url);
    else window.open(url, "_blank", "noopener,noreferrer");
  }

  function bindEvents() {
    $("#searchInput")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        const query = event.target.value.trim();
        if (query) searchFilms(query);
        else loadLatest();
      }
    });

    $("#imageSearchBtn")?.addEventListener("click", () => {
      $("#imageSearchInput")?.click();
    });

    $("#imageSearchInput")?.addEventListener("change", async (event) => {
      const file = event.target.files?.[0];
      if (!file) return;

      const form = new FormData();
      form.append("image", file);

      try {
        const data = await api("/api/films/search-image", {
          method: "POST",
          body: form
        });
        renderFilms(data.films || []);
        switchView("home");
      } catch (error) {
        toast(error.message || "د عکس له لارې لټون ناکام شو.");
      } finally {
        event.target.value = "";
      }
    });

    $("#copyReferralBtn")?.addEventListener("click", async () => {
      try {
        const data = await loadReferrals();
        const link = data.referral_link;

        if (!link) {
          toast("د بلنې لینک لا نه دی جوړ شوی.");
          return;
        }

        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(link);
        } else {
          const input = document.createElement("textarea");
          input.value = link;
          document.body.appendChild(input);
          input.select();
          document.execCommand("copy");
          input.remove();
        }

        toast("د بلنې لینک کاپي شو.");
      } catch (error) {
        toast(error.message || "د لینک کاپي ممکن نه شوه.");
      }
    });

    $("#shareReferralBtn")?.addEventListener("click", async () => {
      try {
        const data = await loadReferrals();
        if (!data.referral_link) {
          toast("د بلنې لینک لا نه دی جوړ شوی.");
          return;
        }

        const shareUrl =
          "https://t.me/share/url?url=" +
          encodeURIComponent(data.referral_link) +
          "&text=" +
          encodeURIComponent("زموږ د فلمونو Mini App وکاروئ 🎬");

        openTelegram(shareUrl);
      } catch (error) {
        toast(error.message || "د شریکولو لینک نه جوړېږي.");
      }
    });

    $("#publishBtn")?.addEventListener("click", () => {
      if (!currentUser?.can_publish) {
        toast("د فلم خپرولو لپاره د ریفرل هدف بشپړ کړئ.");
        return;
      }
      openTelegram(BOT_URL);
    });

    $$("[data-view]").forEach((button) => {
      button.addEventListener("click", async () => {
        const view = button.dataset.view;

        if (view === "official") await loadOfficial();
        else if (view === "leaders") await loadLeaders();
        else if (view === "channels") await loadChannels();
        else await loadLatest();
      });
    });
  }

  async function start() {
    try {
      tg?.ready();
      tg?.expand();
      bindEvents();

      if (!INIT_DATA) {
        showError(
          "Mini App د Telegram له اصلي تڼۍ څخه خلاص کړئ؛ د Telegram اعتبار معلومات نشته."
        );
        return;
      }

      const user = await api("/api/me");
      setUser(user);
      showMain();

      await Promise.allSettled([loadLatest(), loadReferrals(), loadChannels()]);
      switchView("home");
    } catch (error) {
      console.error("Mini App startup error:", error);
      showError(error.message || "Mini App نه پرانیستل کېږي. بیا هڅه وکړئ.");
    }
  }

  // د HTML د موجودو تڼیو لپاره
  window.loadLatest = loadLatest;
  window.loadOfficial = loadOfficial;
  window.loadCategory = loadCategory;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
