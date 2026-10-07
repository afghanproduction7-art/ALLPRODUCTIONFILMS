const tg = window.Telegram?.WebApp;

if (tg) {
    tg.ready();
    tg.expand();

    try {
        tg.setHeaderColor("#0b0f19");
        tg.setBackgroundColor("#0b0f19");
    } catch {}
}

const initData = tg?.initData || "";

const state = {
    user: null,
    referral: null,
    films: [],
    currentFilm: null,
    currentView: "home",
    searchTimer: null,
};


// =========================================================
// DOM
// =========================================================

const $ = (id) =>
    document.getElementById(id);

const accessScreen =
    $("accessScreen");

const mainApp =
    $("mainApp");

const filmsGrid =
    $("filmsGrid");

const searchInput =
    $("searchInput");

const filmModal =
    $("filmModal");

const leadersModal =
    $("leadersModal");

const toastBox =
    $("toast");


// =========================================================
// API
// =========================================================

async function api(
    url,
    options = {}
) {
    const headers = {
        ...(options.headers || {}),
        "X-Telegram-Init-Data": initData,
    };

    const response = await fetch(
        url,
        {
            ...options,
            headers,
        }
    );

    let data = null;

    try {
        data = await response.json();
    } catch {
        data = {};
    }

    if (!response.ok) {
        const error = new Error(
            data?.detail?.code ||
            data?.detail?.message ||
            data?.detail ||
            "Request failed"
        );

        error.status =
            response.status;

        error.data = data;

        throw error;
    }

    return data;
}


// =========================================================
// TOAST
// =========================================================

function showToast(
    message,
    type = "normal"
) {
    if (!toastBox) return;

    toastBox.textContent =
        message;

    toastBox.className =
        `toast show ${type}`;

    clearTimeout(
        showToast.timer
    );

    showToast.timer =
        setTimeout(
            () => {
                toastBox.className =
                    "toast";
            },
            3000
        );
}


// =========================================================
// TELEGRAM
// =========================================================

function haptic(
    type = "light"
) {
    try {
        tg?.HapticFeedback?.impactOccurred(
            type
        );
    } catch {}
}


function openTelegramLink(
    url
) {
    if (!url) return;

    try {
        if (tg?.openTelegramLink) {
            tg.openTelegramLink(
                url
            );
            return;
        }
    } catch {}

    window.open(
        url,
        "_blank"
    );
}


// =========================================================
// ACCESS SCREEN
// =========================================================

function showAccessScreen(
    channel
) {
    if (accessScreen) {
        accessScreen.classList.remove(
            "hidden"
        );
    }

    if (mainApp) {
        mainApp.classList.add(
            "hidden"
        );
    }

    const joinButton =
        $("joinChannelBtn");

    if (!joinButton) return;

    const username =
        String(
            channel || ""
        )
            .replace("@", "")
            .trim();

    if (!username) return;

    joinButton.onclick = () => {
        openTelegramLink(
            `https://t.me/${username}`
        );
    };
}


function hideAccessScreen() {
    if (accessScreen) {
        accessScreen.classList.add(
            "hidden"
        );
    }

    if (mainApp) {
        mainApp.classList.remove(
            "hidden"
        );
    }
}


// =========================================================
// LOAD USER
// =========================================================

async function loadMe() {
    try {
        const data =
            await api(
                "/api/me"
            );

        state.user =
            data.user || null;

        state.referral =
            data.referral || null;

        renderUser();
        renderReferral();

        hideAccessScreen();

        return data;

    } catch (error) {

        if (
            error.status === 403 &&
            error.data?.detail?.code ===
                "ACCESS_REQUIRED"
        ) {
            showAccessScreen(
                error.data.detail.channel
            );

            return null;
        }

        if (
            error.status === 401
        ) {
            showToast(
                "Telegram معلومات معتبرې نه دي",
                "error"
            );

            return null;
        }

        if (
            error.status === 403 &&
            error.data?.detail?.code ===
                "USER_BLOCKED"
        ) {
            showToast(
                "ستاسو حساب بند شوی دی",
                "error"
            );

            return null;
        }

        console.error(
            error
        );

        showToast(
            "د معلوماتو ترلاسه کول ناکام شول",
            "error"
        );

        return null;
    }
}


// =========================================================
// USER UI
// =========================================================

function renderUser() {
    if (!state.user) return;

    const name =
        state.user.first_name ||
        state.user.username ||
        "کارن";

    const username =
        state.user.username
            ? `@${state.user.username}`
            : "";

    [
        $("userName"),
        $("welcomeName"),
    ].forEach(
        (element) => {
            if (element) {
                element.textContent =
                    name;
            }
        }
    );

    const usernameElement =
        $("userUsername");

    if (usernameElement) {
        usernameElement.textContent =
            username;
    }
}


// =========================================================
// REFERRAL UI
// =========================================================

function renderReferral() {
    if (!state.referral) return;

    const count =
        Number(
            state.referral.count ??
            state.referral.referral_count ??
            0
        );

    const target =
        Number(
            state.referral.target || 0
        );

    const remaining =
        Math.max(
            target - count,
            0
        );

    const percent =
        target > 0
            ? Math.min(
                (count / target) * 100,
                100
            )
            : 0;

    const countElement =
        $("referralCount");

    if (countElement) {
        countElement.textContent =
            count;
    }

    const targetElement =
        $("referralTarget");

    if (targetElement) {
        targetElement.textContent =
            target;
    }

    const remainingElement =
        $("referralRemaining");

    if (remainingElement) {
        remainingElement.textContent =
            remaining;
    }

    const progress =
        $("referralProgress");

    if (progress) {
        progress.style.width =
            `${percent}%`;
    }

    const status =
        $("publishStatus");

    if (status) {
        if (
            state.referral.can_publish
        ) {
            status.textContent =
                "✅ تاسو د فلم نشرولو اجازه لرئ";

            status.className =
                "publish-status success";
        } else {
            status.textContent =
                `🔒 د نشر لپاره ${remaining} ریفرل پاتې دي`;

            status.className =
                "publish-status";
        }
    }

    const publishButton =
        $("publishBtn");

    if (publishButton) {
        publishButton.disabled =
            !state.referral.can_publish;

        publishButton.classList.toggle(
            "disabled",
            !state.referral.can_publish
        );
    }
}


// =========================================================
// REFERRAL
// =========================================================

async function loadReferral() {
    try {
        const data =
            await api(
                "/api/referrals"
            );

        state.referral =
            data;

        renderReferral();

        return data;

    } catch (error) {
        console.error(
            error
        );

        return null;
    }
}


async function copyReferral() {
    let link =
        state.referral?.referral_link;

    if (!link) {
        const data =
            await loadReferral();

        link =
            data?.referral_link;
    }

    if (!link) {
        showToast(
            "Referral Link پیدا نه شو",
            "error"
        );

        return;
    }

    try {
        await navigator.clipboard.writeText(
            link
        );

        haptic("light");

        showToast(
            "Referral Link کاپي شو ✅",
            "success"
        );

    } catch {
        showToast(
            link
        );
    }
}


function shareReferral() {
    const link =
        state.referral?.referral_link;

    if (!link) {
        showToast(
            "لومړی Referral Link جوړېږي",
            "error"
        );

        loadReferral();

        return;
    }

    const text =
        "🎬 ALL PRODUCTION FILMS ته راشئ او د پښتو فلمونو نړۍ ومومئ!";

    const shareUrl =
        `https://t.me/share/url?url=${
            encodeURIComponent(link)
        }&text=${
            encodeURIComponent(text)
        }`;

    openTelegramLink(
        shareUrl
    );
}


// =========================================================
// FILM HELPERS
// =========================================================

function escapeHtml(
    value
) {
    if (
        value === null ||
        value === undefined
    ) {
        return "";
    }

    return String(value)
        .replaceAll(
            "&",
            "&amp;"
        )
        .replaceAll(
            "<",
            "&lt;"
        )
        .replaceAll(
            ">",
            "&gt;"
        )
        .replaceAll(
            '"',
            "&quot;"
        )
        .replaceAll(
            "'",
            "&#039;"
        );
}


function filmPoster(
    film
) {
    if (
        film &&
        film.poster_url
    ) {
        return film.poster_url;
    }

    return "";
}


function filmCard(
    film
) {
    const poster =
        filmPoster(film);

    const title =
        escapeHtml(
            film.title ||
            "بې نومه فلم"
        );

    const year =
        film.year
            ? escapeHtml(
                film.year
            )
            : "";

    const quality =
        film.quality
            ? escapeHtml(
                film.quality
            )
            : "";

    return `
        <div
            class="film-card"
            onclick="openFilm(${film.id})"
        >

            <div class="film-poster">

                ${
                    poster
                    ? `
                        <img
                            src="${escapeHtml(
                                poster
                            )}"
                            alt="${title}"
                            loading="lazy"
                        >
                    `
                    : `
                        <div class="poster-placeholder">
                            🎬
                        </div>
                    `
                }

                ${
                    quality
                    ? `
                        <span class="quality-badge">
                            ${quality}
                        </span>
                    `
                    : ""
                }

            </div>

            <div class="film-info">

                <h3>
                    ${title}
                </h3>

                <div class="film-meta">

                    ${
                        year
                        ? `<span>${year}</span>`
                        : ""
                    }

                    ${
                        film.language
                        ? `
                            <span>
                                ${escapeHtml(
                                    film.language
                                )}
                            </span>
                        `
                        : ""
                    }

                </div>

            </div>

        </div>
    `;
}


function renderFilms(
    films,
    emptyText =
        "فلمونه پیدا نه شول"
) {
    state.films =
        Array.isArray(films)
            ? films
            : [];

    if (!filmsGrid) return;

    if (!state.films.length) {
        filmsGrid.innerHTML = `
            <div class="empty-state">

                <div class="empty-icon">
                    🎬
                </div>

                <p>
                    ${escapeHtml(
                        emptyText
                    )}
                </p>

            </div>
        `;

        return;
    }

    filmsGrid.innerHTML =
        state.films
            .map(
                filmCard
            )
            .join("");
}


// =========================================================
// LOAD LATEST
// =========================================================

async function loadLatest() {
    try {
        setSectionTitle(
            "وروستي فلمونه"
        );

        const data =
            await api(
                "/api/films/latest?limit=40"
            );

        renderFilms(
            data.films
        );

    } catch (error) {
        console.error(
            error
        );

        showToast(
            "فلمونه نه شول راوستلای",
            "error"
        );
    }
}


// =========================================================
// SEARCH
// =========================================================

async function searchFilms(
    query
) {
    query =
        String(query || "")
            .trim();

    if (!query) {
        await loadLatest();
        return;
    }

    try {
        setSectionTitle(
            "د لټون پایلې"
        );

        const data =
            await api(
                `/api/films/search?q=${
                    encodeURIComponent(
                        query
                    )
                }&limit=40`
            );

        renderFilms(
            data.films,
            "ستاسې د لټون لپاره فلم پیدا نه شو"
        );

    } catch (error) {
        console.error(
            error
        );

        showToast(
            "لټون ناکام شو",
            "error"
        );
    }
}


function setupSearch() {
    if (!searchInput) return;

    searchInput.addEventListener(
        "input",
        () => {
            clearTimeout(
                state.searchTimer
            );

            state.searchTimer =
                setTimeout(
                    () => {
                        searchFilms(
                            searchInput.value
                        );
                    },
                    400
                );
        }
    );

    searchInput.addEventListener(
        "keydown",
        (event) => {
            if (
                event.key === "Enter"
            ) {
                event.preventDefault();

                searchFilms(
                    searchInput.value
                );
            }
        }
    );
}


// =========================================================
// IMAGE SEARCH
// =========================================================

async function searchByImage(
    file
) {
    if (!file) return;

    if (
        !file.type.startsWith(
            "image/"
        )
    ) {
        showToast(
            "یوازې عکس انتخاب کړئ",
            "error"
        );

        return;
    }

    const formData =
        new FormData();

    formData.append(
        "image",
        file
    );

    showToast(
        "🔎 د عکس له لارې لټون روان دی..."
    );

    try {
        const data =
            await api(
                "/api/films/search-image",
                {
                    method: "POST",
                    body: formData,
                }
            );

        setSectionTitle(
            "د عکس لټون پایلې"
        );

        renderFilms(
            data.films,
            "د دې عکس سره ورته فلم پیدا نه شو"
        );

        haptic("light");

    } catch (error) {
        console.error(
            error
        );

        showToast(
            "د عکس لټون ناکام شو",
            "error"
        );
    }
}


function setupImageSearch() {
    const input =
        $("imageSearchInput");

    const button =
        $("imageSearchBtn");

    if (
        !button ||
        !input
    ) {
        return;
    }

    button.onclick = () => {
        input.click();
    };

    input.addEventListener(
        "change",
        () => {
            const file =
                input.files?.[0];

            if (file) {
                searchByImage(
                    file
                );
            }

            input.value = "";
        }
    );
}


// =========================================================
// CATEGORIES
// =========================================================

async function loadCategory(
    category
) {
    if (!category) return;

    try {
        setSectionTitle(
            category
        );

        const data =
            await api(
                `/api/films/category/${
                    encodeURIComponent(
                        category
                    )
                }?limit=40`
            );

        renderFilms(
            data.films,
            "په دې کټګورۍ کې فلم نشته"
        );

    } catch (error) {
        console.error(
            error
        );

        showToast(
            "کټګوري نه شوه خلاصولای",
            "error"
        );
    }
}


// =========================================================
// OFFICIAL
// =========================================================

async function loadOfficial() {
    try {
        setSectionTitle(
            "⭐ رسمي فلمونه"
        );

        const data =
            await api(
                "/api/films/official?limit=40"
            );

        renderFilms(
            data.films,
            "رسمي فلمونه نشته"
        );

    } catch (error) {
        console.error(
            error
        );

        showToast(
            "رسمي فلمونه نه شول راوستلای",
            "error"
        );
    }
}


// =========================================================
// FILM MODAL
// =========================================================

async function openFilm(
    filmId
) {
    try {
        const film =
            await api(
                `/api/films/${filmId}`
            );

        state.currentFilm =
            film;

        renderFilmModal(
            film
        );

        filmModal?.classList.add(
            "show"
        );

        haptic("light");

    } catch (error) {
        console.error(
            error
        );

        showToast(
            "د فلم معلومات نه شول راوستلای",
            "error"
        );
    }
}


function renderFilmModal(
    film
) {
    if (!filmModal) return;

    const poster =
        filmPoster(film);

    const title =
        escapeHtml(
            film.title || ""
        );

    filmModal.innerHTML = `
        <div
            class="modal-backdrop"
            onclick="closeFilmModal()"
        ></div>

        <div class="modal-content film-modal-content">

            <button
                class="modal-close"
                onclick="closeFilmModal()"
            >
                ✕
            </button>

            ${
                poster
                ? `
                    <img
                        class="modal-poster"
                        src="${escapeHtml(
                            poster
                        )}"
                        alt="${title}"
                    >
                `
                : `
                    <div class="modal-poster-placeholder">
                        🎬
                    </div>
                `
            }

            <div class="modal-body">

                <h2>
                    ${title}
                </h2>

                <div class="film-details">

                    ${
                        film.year
                        ? `
                            <div>
                                📅
                                <strong>
                                    کال:
                                </strong>
                                ${escapeHtml(
                                    film.year
                                )}
                            </div>
                        `
                        : ""
                    }

                    ${
                        film.quality
                        ? `
                            <div>
                                🎞
                                <strong>
                                    کیفیت:
                                </strong>
                                ${escapeHtml(
                                    film.quality
                                )}
                            </div>
                        `
                        : ""
                    }

                    ${
                        film.language
                        ? `
                            <div>
                                🔊
                                <strong>
                                    ژبه:
                                </strong>
                                ${escapeHtml(
                                    film.language
                                )}
                            </div>
                        `
                        : ""
                    }

                    ${
                        film.genre
                        ? `
                            <div>
                                🎭
                                <strong>
                                    ژانر:
                                </strong>
                                ${escapeHtml(
                                    film.genre
                                )}
                            </div>
                        `
                        : ""
                    }

                </div>

                ${
                    film.description
                    ? `
                        <div class="film-description">
                            ${escapeHtml(
                                film.description
                            )}
                        </div>
                    `
                    : ""
                }

                <button
                    class="download-btn"
                    onclick="downloadFilm(${film.id})"
                >
                    ⬇️ فلم ترلاسه کړئ
                </button>

            </div>

        </div>
    `;
}


function closeFilmModal() {
    filmModal?.classList.remove(
        "show"
    );
}


// =========================================================
// DOWNLOAD
// =========================================================

async function downloadFilm(
    filmId
) {
    try {
        const data =
            await api(
                `/api/films/${filmId}/download`
            );

        if (
            data.deep_link
        ) {
            closeFilmModal();

            openTelegramLink(
                data.deep_link
            );

        } else {
            showToast(
                "د فلم لینک پیدا نه شو",
                "error"
            );
        }

    } catch (error) {
        console.error(
            error
        );

        showToast(
            "د فلم ترلاسه کول ناکام شول",
            "error"
        );
    }
}


// =========================================================
// LEADERS
// =========================================================

async function openLeaders() {
    try {
        const data =
            await api(
                "/api/referrals/leaders"
            );

        renderLeaders(
            data.leaders || []
        );

        leadersModal?.classList.add(
            "show"
        );

    } catch (error) {
        console.error(
            error
        );

        showToast(
            "Leaderboard نه شو خلاصولای",
            "error"
        );
    }
}


function renderLeaders(
    leaders
) {
    if (!leadersModal) return;

    const rows =
        leaders.map(
            (leader) => {
                const name =
                    leader.first_name ||
                    leader.username ||
                    "کارن";

                return `
                    <div class="leader-row">

                        <div class="leader-rank">
                            #${Number(
                                leader.rank || 0
                            )}
                        </div>

                        <div class="leader-name">
                            ${escapeHtml(
                                name
                            )}
                        </div>

                        <div class="leader-count">
                            ${
                                Number(
                                    leader.referral_count || 0
                                )
                            }
                            👥
                        </div>

                    </div>
                `;
            }
        ).join("");

    leadersModal.innerHTML = `
        <div
            class="modal-backdrop"
            onclick="closeLeaders()"
        ></div>

        <div class="modal-content">

            <button
                class="modal-close"
                onclick="closeLeaders()"
            >
                ✕
            </button>

            <h2>
                🏆 د Referral مشران
            </h2>

            <div class="leaders-list">

                ${
                    rows ||
                    `
                        <div class="empty-state">
                            تر اوسه معلومات نشته
                        </div>
                    `
                }

            </div>

        </div>
    `;
}


function closeLeaders() {
    leadersModal?.classList.remove(
        "show"
    );
}


// =========================================================
// CHANNELS
// =========================================================

async function loadChannels() {
    const container =
        $("channelsList");

    if (!container) return;

    try {
        const data =
            await api(
                "/api/channels"
            );

        const channels =
            data.channels || [];

        if (!channels.length) {
            container.innerHTML = `
                <div class="empty-state">
                    چینلونه نشته
                </div>
            `;

            return;
        }

        container.innerHTML =
            channels.map(
                (channel) => {
                    const username =
                        String(
                            channel.username ||
                            ""
                        )
                            .replace(
                                "@",
                                ""
                            )
                            .trim();

                    const title =
                        escapeHtml(
                            channel.title ||
                            channel.username ||
                            "چینل"
                        );

                    return `
                        <button
                            class="channel-item"
                            onclick="openTelegramLink('https://t.me/${encodeURIComponent(
                                username
                            )}')"
                        >

                            <span>
                                📢
                            </span>

                            <span>
                                ${title}
                            </span>

                            <span>
                                →
                            </span>

                        </button>
                    `;
                }
            ).join("");

    } catch (error) {
        console.error(
            error
        );

        showToast(
            "چینلونه نه شول راوستلای",
            "error"
        );
    }
}


// =========================================================
// PUBLISH
// =========================================================

function showPublishInfo() {
    if (
        !state.referral?.can_publish
    ) {
        showToast(
            `🔒 د فلم نشرولو لپاره ${
                state.referral?.remaining || 0
            } ریفرل پاتې دي`
        );

        return;
    }

    showToast(
        "🎬 فلم د Telegram Bot له لارې واستوئ"
    );

    if (tg?.close) {
        tg.close();
    }
}


// =========================================================
// NAVIGATION
// =========================================================

function setSectionTitle(
    title
) {
    const element =
        $("sectionTitle");

    if (element) {
        element.textContent =
            title;
    }
}


function switchView(
    view
) {
    state.currentView =
        view;

    document
        .querySelectorAll(
            ".bottom-nav button"
        )
        .forEach(
            (button) => {
                button.classList.toggle(
                    "active",
                    button.dataset.view ===
                        view
                );
            }
        );

    if (view === "home") {
        loadLatest();
    }

    if (view === "official") {
        loadOfficial();
    }

    if (view === "leaders") {
        openLeaders();
    }

    haptic("light");
}


// =========================================================
// GLOBAL BUTTONS
// =========================================================

function setupButtons() {

    const copyButton =
        $("copyReferralBtn");

    if (copyButton) {
        copyButton.onclick =
            copyReferral;
    }

    const shareButton =
        $("shareReferralBtn");

    if (shareButton) {
        shareButton.onclick =
            shareReferral;
    }

    const leadersButton =
        $("leadersBtn");

    if (leadersButton) {
        leadersButton.onclick =
            openLeaders;
    }

    const publishButton =
        $("publishBtn");

    if (publishButton) {
        publishButton.onclick =
            showPublishInfo;
    }

    document
        .querySelectorAll(
            "[data-category]"
        )
        .forEach(
            (button) => {
                button.addEventListener(
                    "click",
                    () => {
                        loadCategory(
                            button.dataset.category
                        );

                        haptic(
                            "light"
                        );
                    }
                );
            }
        );

    document
        .querySelectorAll(
            ".bottom-nav button"
        )
        .forEach(
            (button) => {
                button.addEventListener(
                    "click",
                    () => {
                        const view =
                            button.dataset.view;

                        if (
                            view === "channels"
                        ) {
                            loadChannels();
                            return;
                        }

                        switchView(
                            view
                        );
                    }
                );
            }
        );
}


// =========================================================
// INIT
// =========================================================

async function initApp() {

    if (!tg) {
        showToast(
            "دا Mini App باید د Telegram دننه خلاص شي",
            "error"
        );

        return;
    }

    setupSearch();
    setupImageSearch();
    setupButtons();

    const result =
        await loadMe();

    if (!result) {
        return;
    }

    await Promise.all([
        loadLatest(),
        loadChannels(),
        loadReferral(),
    ]);
}


// =========================================================
// GLOBAL FUNCTIONS
// =========================================================

window.openFilm =
    openFilm;

window.closeFilmModal =
    closeFilmModal;

window.downloadFilm =
    downloadFilm;

window.openLeaders =
    openLeaders;

window.closeLeaders =
    closeLeaders;

window.copyReferral =
    copyReferral;

window.shareReferral =
    shareReferral;

window.searchFilms =
    searchFilms;

window.searchByImage =
    searchByImage;

window.loadCategory =
    loadCategory;

window.loadOfficial =
    loadOfficial;

window.loadLatest =
    loadLatest;

window.loadChannels =
    loadChannels;

window.showPublishInfo =
    showPublishInfo;

window.openTelegramLink =
    openTelegramLink;


// =========================================================
// START
// =========================================================

document.addEventListener(
    "DOMContentLoaded",
    initApp
);
